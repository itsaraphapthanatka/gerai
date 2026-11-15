"""คิวงานถาวร + จุดหยุด + งบ + การอนุมัติ + outbox (stage 10, flag durable_jobs) — ดู docs/rebuild/JOBS_CONTRACT.md

การรับประกัน: **at-least-once** (งานอาจถูกทำซ้ำได้เมื่อ lease หมดอายุหรือเครื่องล่มกลางทาง) ไม่ใช่ exactly-once
→ ผลภายนอกทุกอย่างต้อง idempotent เอง: เขียนผ่าน outbox ด้วย idem_key / ตรวจสถานะก่อนทำ / ใช้การอนุมัติที่ผูกกับงานนี้
งานชนกัน (หลาย worker/หลาย scheduler) ปลอดภัยเพราะ claim เป็น compare-and-set ในฐานข้อมูล ไม่ได้พึ่ง lock ในหน่วยความจำ

stage นี้ย้ายงานตระกูลเดียว: การทดสอบ AI ของรายงาน (deep_ai) · งานส่งข้อความภายนอกยังปิด มีแค่ test_message ที่บันทึกลง outbox (ไม่ส่งจริง)"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import socket
import threading
import uuid

from . import db, flags

DEFAULT_LEASE_S = 15 * 60
BACKOFF_BASE_S = 30
BACKOFF_MAX_S = 3600
MAX_DEFER_HOURS = 72                  # เลื่อนได้นานสุด (รอชั้นแรก/รองบ/หยุดชั่วคราว) ก่อนถือว่าล้ม
RESERVATION_TTL_H = 2                 # จองงบค้างเกินนี้โดยงานไม่ได้รันอยู่ = คืนงบ
STATUS_LABEL = {"queued": "รอทำ", "running": "กำลังทำ", "succeeded": "สำเร็จ", "dead": "ล้ม (ต้องดู)", "cancelled": "ยกเลิก"}


class Defer(Exception):
    """handler ขอเลื่อนงาน (ไม่นับเป็นความพยายาม) เช่น รอข้อมูลก่อนหน้า หรือรองบวันถัดไป"""

    def __init__(self, seconds: int, note: str, until: str | None = None):
        super().__init__(note)
        self.seconds, self.note, self.until = seconds, note, until


class Permanent(Exception):
    """ล้มแบบลองใหม่ไม่ช่วย (เช่น การอนุมัติไม่ตรง) → dead ทันที"""


class Cancelled(Exception):
    """หยุดงานโดยไม่ทำผล (เช่น ผู้รับขอไม่รับแล้ว)"""


def enabled() -> bool:
    try:
        return flags.enabled("durable_jobs")
    except Exception:
        return False


def _now() -> dt.datetime:
    return dt.datetime.now().replace(microsecond=0)


def _iso(t: dt.datetime) -> str:
    return t.isoformat(timespec="seconds")


def _hash(v) -> str:
    return hashlib.sha256(json.dumps(v, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}:{uuid.uuid4().hex[:6]}"


# ---------- จุดหยุด ----------
PAUSE_SCOPES = ("global", "workflow", "channel", "account")


def set_pause(scope: str, key: str, on: bool, reason: str = "", by: str = "") -> None:
    assert scope in PAUSE_SCOPES
    key = "*" if scope == "global" else str(key)
    with db.get_conn() as c:
        c.execute(db.q("DELETE FROM job_pauses WHERE scope=? AND key=?"), (scope, key))
        if on:
            c.execute(db.q("INSERT INTO job_pauses(scope, key, reason, created_by, created_at) VALUES(?,?,?,?,?)"),
                      (scope, key, (reason or "")[:200], (by or "")[:80], db.now()))


def pauses() -> list:
    with db.get_conn() as c:
        return c.execute(db.q("SELECT * FROM job_pauses ORDER BY scope, key")).fetchall()


def paused(kind: str, channel: str | None = None, lead_id: int | None = None) -> str | None:
    """คืนเหตุผลถ้าหยุดอยู่ · global (kill switch) > workflow > channel > account"""
    want = [("global", "*"), ("workflow", kind)] + ([("channel", channel)] if channel else []) + ([("account", str(lead_id))] if lead_id else [])
    with db.get_conn() as c:
        for scope, key in want:
            r = c.execute(db.q("SELECT reason FROM job_pauses WHERE scope=? AND key=?"), (scope, key)).fetchone()
            if r:
                return f"{scope}:{key}" + (f" ({r['reason']})" if r["reason"] else "")
    return None


# ---------- คิว ----------
def enqueue(kind: str, payload: dict, *, idem_key: str, lead_id: int | None = None, channel: str | None = None,
            max_attempts: int = 5, delay_s: int = 0) -> tuple[int | None, str]:
    """คืน (job_id, "queued"|"exists"|"paused:<เหตุผล>") · idem_key ซ้ำ = คืนงานเดิม ไม่สร้างใหม่"""
    if kind not in HANDLERS:
        raise KeyError(f"ไม่รู้จักงาน {kind}")
    why = paused(kind, channel, lead_id)
    if why:
        return None, f"paused:{why}"
    now = db.now()
    avail = _iso(_now() + dt.timedelta(seconds=max(0, delay_s)))
    with db.get_conn() as c:
        row = c.execute(db.q("INSERT INTO jobs(kind, idem_key, payload_json, lead_id, channel, max_attempts, available_at, created_at, updated_at) "
                             "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT (idem_key) DO NOTHING RETURNING id"),
                        (kind, idem_key[:200], json.dumps(payload, ensure_ascii=False, default=str), lead_id, channel,
                         max(1, max_attempts), avail, now, now)).fetchone()
        if row:
            return row["id"], "queued"
        old = c.execute(db.q("SELECT id FROM jobs WHERE idem_key=?"), (idem_key[:200],)).fetchone()
    return (old["id"] if old else None), "exists"


def get(job_id: int):
    with db.get_conn() as c:
        return c.execute(db.q("SELECT * FROM jobs WHERE id=?"), (job_id,)).fetchone()


def list_jobs(status: str | None = None, limit: int = 50):
    sql, args = "SELECT * FROM jobs", []
    if status:
        sql += " WHERE status=?"
        args.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with db.get_conn() as c:
        return c.execute(db.q(sql), tuple(args)).fetchall()


def counts() -> dict:
    with db.get_conn() as c:
        return {r["status"]: r["n"] for r in c.execute(db.q("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")).fetchall()}


def recover(now: dt.datetime | None = None) -> int:
    """งานที่ lease หมดอายุ (worker ล่ม/ค้าง) → กลับเข้าคิว หรือ dead ถ้าครบจำนวนครั้ง · คืนงบที่จองค้าง"""
    now = now or _now()
    n = 0
    with db.get_conn() as c:
        for j in c.execute(db.q("SELECT id, attempts, max_attempts FROM jobs WHERE status='running' AND lease_until<?"), (_iso(now),)).fetchall():
            to = "dead" if j["attempts"] >= j["max_attempts"] else "queued"
            cur = c.execute(db.q("UPDATE jobs SET status=?, lease_owner=NULL, lease_until=NULL, updated_at=?, last_error=?, finished_at=? "
                                 "WHERE id=? AND status='running' AND lease_until<?"),
                            (to, _iso(now), "lease หมดอายุ (worker ล่มหรือค้าง)", _iso(now) if to == "dead" else None, j["id"], _iso(now)))
            n += cur.rowcount or 0
        old = _iso(now - dt.timedelta(hours=RESERVATION_TTL_H))
        c.execute(db.q("UPDATE budget_reservations SET status='released', updated_at=? WHERE status='reserved' AND created_at<? "
                       "AND (job_id IS NULL OR job_id NOT IN (SELECT id FROM jobs WHERE status='running'))"), (_iso(now), old))
    return n


def claim(worker: str, lease_s: int = DEFAULT_LEASE_S, kinds: tuple | None = None, now: dt.datetime | None = None):
    """จองงานถัดไปแบบ compare-and-set · สอง worker แย่งงานเดียวกัน ได้คนเดียว"""
    now = now or _now()
    sql = "SELECT id FROM jobs WHERE status='queued' AND available_at<=?"
    args = [_iso(now)]
    if kinds:
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        args += list(kinds)
    sql += " ORDER BY available_at, id LIMIT 10"
    with db.get_conn() as c:
        ids = [r["id"] for r in c.execute(db.q(sql), tuple(args)).fetchall()]
    for jid in ids:
        with db.get_conn() as c:
            cur = c.execute(db.q("UPDATE jobs SET status='running', lease_owner=?, lease_until=?, attempts=attempts+1, updated_at=? "
                                 "WHERE id=? AND status='queued' AND available_at<=?"),
                            (worker, _iso(now + dt.timedelta(seconds=lease_s)), _iso(now), jid, _iso(now)))
            got = (cur.rowcount or 0) == 1
        if got:
            return get(jid)
    return None


def heartbeat(job_id: int, worker: str, lease_s: int = DEFAULT_LEASE_S) -> bool:
    with db.get_conn() as c:
        cur = c.execute(db.q("UPDATE jobs SET lease_until=?, updated_at=? WHERE id=? AND status='running' AND lease_owner=?"),
                        (_iso(_now() + dt.timedelta(seconds=lease_s)), db.now(), job_id, worker))
        return (cur.rowcount or 0) == 1


def _finish(job_id: int, worker: str, status: str, **f) -> bool:
    """เปลี่ยนสถานะเฉพาะเมื่อ worker นี้ยังถือ lease อยู่ · lease หลุดไปแล้ว = ผลนี้ไม่ถูกบันทึก (อีกคนทำแทน)"""
    cols = {"status": status, "updated_at": db.now(), "lease_owner": None, "lease_until": None, **f}
    with db.get_conn() as c:
        cur = c.execute(db.q(f"UPDATE jobs SET {', '.join(k + '=?' for k in cols)} WHERE id=? AND status='running' AND lease_owner=?"),
                        (*cols.values(), job_id, worker))
        return (cur.rowcount or 0) == 1


def complete(job_id: int, worker: str, result: dict | None = None, note: str = "") -> bool:
    return _finish(job_id, worker, "succeeded", result_json=json.dumps(result or {}, ensure_ascii=False, default=str),
                   note=note[:300] or None, finished_at=db.now())


def backoff_s(attempts: int) -> int:
    return min(BACKOFF_MAX_S, BACKOFF_BASE_S * (2 ** max(0, attempts - 1)))


def fail(job, worker: str, error: str, permanent: bool = False) -> str:
    """ลองใหม่แบบเว้นระยะเพิ่มขึ้น · ครบจำนวนครั้งหรือ permanent = dead (ไปที่หน้าแอดมิน)"""
    dead = permanent or job["attempts"] >= job["max_attempts"]
    if dead:
        _finish(job["id"], worker, "dead", last_error=error[:500], finished_at=db.now())
        return "dead"
    _finish(job["id"], worker, "queued", last_error=error[:500], available_at=_iso(_now() + dt.timedelta(seconds=backoff_s(job["attempts"]))))
    return "retry"


def defer(job, worker: str, seconds: int, note: str, until: str | None = None) -> str:
    """เลื่อนโดยไม่นับเป็นความพยายาม · เลื่อนมานานเกิน MAX_DEFER_HOURS = dead"""
    try:
        age = _now() - dt.datetime.fromisoformat(job["created_at"])
    except ValueError:
        age = dt.timedelta(0)
    if age > dt.timedelta(hours=MAX_DEFER_HOURS):
        _finish(job["id"], worker, "dead", last_error=f"รอนานเกิน {MAX_DEFER_HOURS} ชม.: {note}"[:500], finished_at=db.now())
        return "dead"
    _finish(job["id"], worker, "queued", note=note[:300], attempts=max(0, job["attempts"] - 1),
            available_at=until or _iso(_now() + dt.timedelta(seconds=max(1, seconds))))
    return "deferred"


def cancel(job_id: int, by: str = "") -> str:
    """รอทำ/ล้ม → ยกเลิกทันที · กำลังทำ → ขอให้หยุด (handler เช็กก่อนทำผลภายนอก)"""
    with db.get_conn() as c:
        cur = c.execute(db.q("UPDATE jobs SET status='cancelled', updated_at=?, finished_at=?, note=? WHERE id=? AND status IN ('queued','dead')"),
                        (db.now(), db.now(), f"ยกเลิกโดย {by}"[:300], job_id))
        if (cur.rowcount or 0) == 1:
            return "cancelled"
        cur = c.execute(db.q("UPDATE jobs SET cancel_requested=1, updated_at=? WHERE id=? AND status='running'"), (db.now(), job_id))
        return "requested" if (cur.rowcount or 0) == 1 else "noop"


def retry(job_id: int) -> bool:
    """งานที่ dead → กลับเข้าคิว นับใหม่ (แอดมินกด)"""
    with db.get_conn() as c:
        cur = c.execute(db.q("UPDATE jobs SET status='queued', attempts=0, available_at=?, updated_at=?, finished_at=NULL WHERE id=? AND status='dead'"),
                        (db.now(), db.now(), job_id))
        return (cur.rowcount or 0) == 1


def stop_requested(job_id: int) -> bool:
    j = get(job_id)
    return bool(j and j["cancel_requested"])


# ---------- งบ ----------
AI_RUN_EST_USD = 1.20                 # วัดจริง ≈ $1.15 ต่อ 20 คำถาม (deep_audit.AI_DAILY_DEFAULT)


class BudgetExhausted(Exception):
    pass


def budget_limit(key: str) -> float:
    """เพดานต่อวัน · ตั้งได้ที่ setting budget_<key>_daily · ค่าเริ่มต้น ai_usd = เพดานจำนวนรายต่อวันเดิม × ค่าประมาณต่อครั้ง"""
    raw = db.get_setting(f"budget_{key}_daily")
    if raw not in (None, ""):
        try:
            return max(0.0, float(raw))
        except ValueError:
            return 0.0
    if key == "ai_usd":
        from . import deep_audit
        return deep_audit.ai_daily() * AI_RUN_EST_USD
    return 0.0


def _today() -> str:
    from . import office
    return office.today_bkk()


def budget_used(key: str, day: str | None = None) -> dict:
    day = day or _today()
    with db.get_conn() as c:
        r = c.execute(db.q("SELECT COALESCE(SUM(CASE WHEN status='reserved' THEN amount ELSE 0 END),0) AS reserved, "
                           "COALESCE(SUM(CASE WHEN status='committed' THEN COALESCE(actual,0) ELSE 0 END),0) AS committed "
                           "FROM budget_reservations WHERE budget_key=? AND day=?"), (key, day)).fetchone()
    return {"reserved": float(r["reserved"]), "committed": float(r["committed"]), "limit": budget_limit(key), "day": day}


def reserve(key: str, amount: float, job_id: int | None = None) -> int:
    """จองแบบ atomic: ใส่แถวก่อนแล้วรวมยอดในธุรกรรมเดียว เกินเพดาน = ยกเลิกทั้งธุรกรรม
    SQLite: INSERT ถือ write lock จนจบธุรกรรม (ผู้จองพร้อมกันต้องรอ) · Postgres: advisory lock ต่อ budget_key"""
    limit, day, now = budget_limit(key), _today(), db.now()
    with db.get_conn() as c:
        if db.IS_PG:
            c.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"budget:{key}",))
        rid = c.execute(db.q("INSERT INTO budget_reservations(budget_key, day, amount, status, job_id, created_at, updated_at) "
                             "VALUES(?,?,?,'reserved',?,?,?) RETURNING id"), (key, day, float(amount), job_id, now, now)).fetchone()["id"]
        tot = c.execute(db.q("SELECT COALESCE(SUM(CASE WHEN status='reserved' THEN amount WHEN status='committed' THEN COALESCE(actual,0) ELSE 0 END),0) AS t "
                             "FROM budget_reservations WHERE budget_key=? AND day=?"), (key, day)).fetchone()["t"]
        if float(tot) > limit + 1e-9:
            raise BudgetExhausted(f"งบ {key} วันนี้ไม่พอ (ใช้/จอง {float(tot) - amount:.2f} + {amount:.2f} > {limit:.2f})")
    return rid


def settle(res_id: int, actual: float) -> bool:
    """ปิดยอดด้วยค่าจริง (อาจมากหรือน้อยกว่าที่จอง)"""
    with db.get_conn() as c:
        cur = c.execute(db.q("UPDATE budget_reservations SET status='committed', actual=?, updated_at=? WHERE id=? AND status='reserved'"),
                        (float(actual), db.now(), res_id))
        return (cur.rowcount or 0) == 1


def release(res_id: int) -> bool:
    with db.get_conn() as c:
        cur = c.execute(db.q("UPDATE budget_reservations SET status='released', updated_at=? WHERE id=? AND status='reserved'"), (db.now(), res_id))
        return (cur.rowcount or 0) == 1


# ---------- การอนุมัติ ----------
def grant(action: str, *, payload: dict, policy: str, approver: str, lead_id: int | None = None, recipient: str | None = None,
          channel: str | None = None, evidence_ref: str | None = None, max_cost: float = 0.0, version: int = 1, ttl_hours: float = 24) -> int:
    with db.get_conn() as c:
        return c.execute(db.q("INSERT INTO approvals(action, lead_id, recipient_hash, channel, content_hash, content_version, evidence_ref, max_cost, "
                              "policy, approver, expires_at, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id"),
                         (action, lead_id, _hash(recipient) if recipient else None, channel, _hash(payload), version, evidence_ref,
                          float(max_cost), policy, approver, _iso(_now() + dt.timedelta(hours=ttl_hours)), db.now())).fetchone()["id"]


def consume(approval_id: int, *, action: str, payload: dict, job_id: int, lead_id: int | None = None, recipient: str | None = None,
            channel: str | None = None, cost: float = 0.0, version: int = 1) -> tuple[bool, str]:
    """ตรวจว่าตรงกับที่อนุมัติทุกช่อง แล้วใช้สิทธิ์แบบ atomic (ใช้ได้ครั้งเดียว) · งานเดิมทำซ้ำ = ผ่าน (idempotent)"""
    with db.get_conn() as c:
        a = c.execute(db.q("SELECT * FROM approvals WHERE id=?"), (approval_id,)).fetchone()
    if not a:
        return False, "not_found"
    if a["status"] == "consumed":
        return (True, "already_consumed_by_this_job") if a["consumed_job_id"] == job_id else (False, "already_used")
    if a["status"] != "granted":
        return False, a["status"]
    if a["expires_at"] < db.now():
        return False, "expired"
    checks = {"action": a["action"] == action, "lead": a["lead_id"] == lead_id, "channel": a["channel"] == channel,
              "recipient": a["recipient_hash"] == (_hash(recipient) if recipient else None), "content": a["content_hash"] == _hash(payload),
              "version": a["content_version"] == version, "cost": float(cost) <= float(a["max_cost"]) + 1e-9}
    bad = [k for k, ok in checks.items() if not ok]
    if bad:
        return False, "mismatch:" + ",".join(bad)
    with db.get_conn() as c:
        cur = c.execute(db.q("UPDATE approvals SET status='consumed', consumed_at=?, consumed_job_id=? WHERE id=? AND status='granted' AND expires_at>=?"),
                        (db.now(), job_id, approval_id, db.now()))
        if (cur.rowcount or 0) != 1:
            return False, "already_used"
    return True, "ok"


def revoke(approval_id: int) -> bool:
    with db.get_conn() as c:
        cur = c.execute(db.q("UPDATE approvals SET status='revoked' WHERE id=? AND status='granted'"), (approval_id,))
        return (cur.rowcount or 0) == 1


# ---------- outbox (โหมดทดสอบเท่านั้น) ----------
def outbox_put(idem_key: str, *, job_id: int, channel: str, content: str, lead_id: int | None = None, recipient: str | None = None) -> tuple[int, bool]:
    """บันทึกผลภายนอกที่ "จะ" ส่ง · idem_key เดิม = ไม่สร้างซ้ำ · stage 10 ไม่มีตัวส่งจริง (mode='test')"""
    with db.get_conn() as c:
        row = c.execute(db.q("INSERT INTO outbox(idem_key, job_id, lead_id, channel, recipient_hash, content_hash, preview, mode, created_at) "
                             "VALUES(?,?,?,?,?,?,?,'test',?) ON CONFLICT (idem_key) DO NOTHING RETURNING id"),
                        (idem_key, job_id, lead_id, channel, _hash(recipient) if recipient else None, _hash(content), content[:200], db.now())).fetchone()
        if row:
            return row["id"], True
        return c.execute(db.q("SELECT id FROM outbox WHERE idem_key=?"), (idem_key,)).fetchone()["id"], False


def outbox_list(limit: int = 50):
    with db.get_conn() as c:
        return c.execute(db.q("SELECT * FROM outbox ORDER BY id DESC LIMIT ?"), (limit,)).fetchall()


# ---------- handlers ----------
def handle_deep_ai(job, worker: str) -> dict:
    """ทดสอบ AI ของรายงาน (เดิม: thread ใน deep_audit.start) · จองงบก่อนเรียกบริการที่เสียเงิน · ปิดยอดด้วยค่าจริง"""
    from . import deep_audit
    p = json.loads(job["payload_json"] or "{}")
    lid = int(p["lead_id"])
    lead = db.get_lead(lid)
    if not lead:
        raise Permanent("ไม่พบลีด")
    t2 = deep_audit.tier2(lead)
    if t2.get("state") == "done" and (t2.get("at") or "") >= job["created_at"] and not p.get("force"):
        return {"skipped": "already_done"}                       # มีคนทำไปแล้วหลังเข้าคิว (idempotent)
    t1 = deep_audit.tier1(lead)
    if t1.get("state") != "done":
        if not deep_audit._running(t1):
            deep_audit.start(lid)                                # ชั้นแรก (ไต่เว็บ+อันดับ) ทำแบบเดิม แล้วค่อยกลับมา
        raise Defer(60, "รอรายงานชั้นแรกเสร็จ")
    try:
        rid = reserve("ai_usd", AI_RUN_EST_USD, job["id"])
    except BudgetExhausted as e:
        deep_audit._save(lid, "deep_ai_json", {"state": "queued", "at": db.now(), "reason": "ครบงบทดสอบ AI วันนี้ — ระบบจะวัดให้วันถัดไป"})
        tomorrow = (dt.datetime.combine(_now().date() + dt.timedelta(days=1), dt.time(0, 5))).isoformat(timespec="seconds")
        raise Defer(0, str(e), until=tomorrow)
    if stop_requested(job["id"]):
        release(rid)
        raise Cancelled("ยกเลิกก่อนเริ่มเรียก AI")
    try:
        d = deep_audit.run_ai(lid, force=bool(p.get("force")))
    except Exception:
        release(rid)
        raise
    if d.get("state") == "done":
        settle(rid, float(d.get("cost_usd") or 0))
        return {"state": "done", "cost_usd": d.get("cost_usd"), "asked": d.get("asked")}
    release(rid)
    if d.get("state") == "error":
        raise RuntimeError(d.get("error") or "ทดสอบ AI ไม่สำเร็จ")
    return {"state": d.get("state"), "reason": d.get("reason")}   # queued (เพดานจำนวนรายต่อวันเดิม) = ทำเหมือนเดิม


def handle_test_message(job, worker: str) -> dict:
    """งานตัวอย่างของ "ส่งข้อความภายนอก" ที่ไม่ส่งจริง: ต้องมีการอนุมัติที่ตรงเป๊ะ · เช็กการขอไม่รับตอนจะทำ · เขียน outbox แบบ idempotent"""
    p = json.loads(job["payload_json"] or "{}")
    lid = p.get("lead_id")
    lead = db.get_lead(lid) if lid else None
    if lead is not None and (lead["unsub_at"] or (lead["status"] or "") == "unsubscribed"):
        raise Cancelled("ผู้รับขอไม่รับข้อความแล้ว (หลังเข้าคิว)")
    if stop_requested(job["id"]):
        raise Cancelled("ยกเลิกระหว่างทำ")
    content = {"text": p.get("text") or "", "channel": p.get("channel"), "recipient": p.get("recipient")}
    ok, why = consume(int(p.get("approval_id") or 0), action="test_message", payload=content, job_id=job["id"], lead_id=lid,
                      recipient=p.get("recipient"), channel=p.get("channel"), cost=0.0)
    if not ok:
        raise Permanent(f"การอนุมัติใช้ไม่ได้: {why}")
    oid, created = outbox_put(f"test_message:{job['id']}", job_id=job["id"], channel=p.get("channel") or "", content=content["text"],
                              lead_id=lid, recipient=p.get("recipient"))
    return {"outbox_id": oid, "created": created}


def handle_appt_message(job, worker: str) -> dict:
    from . import appointments
    return appointments.handle_message(job, worker)


HANDLERS = {"deep_ai": handle_deep_ai, "test_message": handle_test_message, "appt_message": handle_appt_message}
KIND_LABEL = {"deep_ai": "ทดสอบ AI ของรายงาน", "test_message": "ข้อความทดสอบ (outbox ไม่ส่งจริง)",
              "appt_message": "ข้อความนัดคุย LINE (outbox ไม่ส่งจริง)"}


# ---------- worker ----------
def dispatch(job, worker: str) -> str:
    """เช็กจุดหยุดอีกครั้งตอนจะทำ · เรียก handler · คืนผล: succeeded/retry/dead/deferred/cancelled/paused/lost"""
    why = paused(job["kind"], job["channel"], job["lead_id"])
    if why:
        defer(job, worker, 300, f"หยุดชั่วคราว: {why}")
        return "paused"
    fn = HANDLERS.get(job["kind"])
    if not fn:
        return fail(job, worker, f"ไม่รู้จักงาน {job['kind']}", permanent=True)
    try:
        res = fn(job, worker)
    except Defer as d:
        return defer(job, worker, d.seconds, d.note, d.until)
    except Cancelled as c:
        return "cancelled" if _finish(job["id"], worker, "cancelled", note=str(c)[:300], finished_at=db.now()) else "lost"
    except Permanent as e:
        return fail(job, worker, str(e), permanent=True)
    except Exception as e:
        return fail(job, worker, f"{type(e).__name__}: {str(e)[:300]}")
    return "succeeded" if complete(job["id"], worker, res) else "lost"


def run_once(worker: str | None = None, limit: int = 5, kinds: tuple | None = None) -> list[tuple[int, str]]:
    """ทำงานที่ถึงเวลาไม่เกิน limit ชิ้น · flag ปิด = ไม่ทำอะไร (งานค้างในคิวรอไว้) · เรียกซ้อนกันได้ปลอดภัย"""
    if not enabled():
        return []
    worker = worker or worker_id()
    recover()
    out = []
    for _ in range(max(1, limit)):
        j = claim(worker, kinds=kinds)
        if not j:
            break
        out.append((j["id"], dispatch(j, worker)))
    return out


KICK_INLINE = False                    # เทสต์ตั้ง True = ทำทันทีใน thread เดียวกัน


def kick() -> None:
    """ปลุก worker หนึ่งรอบหลังเข้าคิว (ไม่ต้องรอ cron) · ซ้อนกับ cron/autorun ได้เพราะ claim เป็น CAS"""
    if not enabled():
        return
    if KICK_INLINE:
        run_once()
        return
    threading.Thread(target=lambda: run_once(), daemon=True, name="jobs-kick").start()
