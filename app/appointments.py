"""นัดคุยผ่าน LINE (stage 06A-2, flag line_appointments) — ดู docs/rebuild/STATUS.md stage 06A-2

หลักการ (PROMPT 06: "Clicking link ≠ booking; authoritative confirmation required"):
- ยังไม่มีปฏิทินภายนอกที่ยืนยันได้ → ลูกค้าเลือกช่วงเวลาที่ทีมเปิดไว้ = **คำขอนัดรอยืนยัน** (กันช่องเวลาไว้) ไม่ใช่นัดสำเร็จ
- **นัดสำเร็จ = ทีมกดยืนยันในหลังบ้านเท่านั้น** → ตั้งงานถัดไปใน CRM เดิม + นับ meeting_booked + ตั้งเตือนผ่านคิวงาน
- ข้อความที่ระบบส่งเอง (ยืนยัน/เตือน/ยกเลิกโดยทีม) ผ่าน outbox โหมดทดสอบของ stage 10 → **ยังไม่ส่งจริง** ทีมแจ้งลูกค้าเองใน LINE OA Manager
- การตอบกลับข้อความที่ลูกค้าพิมพ์/กดมา (reply token) ทำเหมือน 06A-1
- เวลาไทย UTC+07:00 (ไม่มีเวลาออมแสง) · ช่องเวลาตามที่ทีมตั้ง · เลือกได้เฉพาะช่องที่ระบบเสนอ ณ ตอนนั้น (ไม่เชื่อค่าจากปุ่ม)"""
from __future__ import annotations

import datetime as dt
import json
import re

from . import db, flags

TZ = dt.timezone(dt.timedelta(hours=7), "Asia/Bangkok")
ACTIVE = ("requested", "pending_confirmation", "confirmed")
STATUS_LABEL = {"requested": "ขอนัด (ยังไม่เลือกเวลา)", "pending_confirmation": "รอทีมยืนยันเวลา", "confirmed": "ยืนยันแล้ว",
                "completed": "คุยแล้ว", "cancelled": "ยกเลิก", "no_show": "ลูกค้าไม่มา", "needs_review": "ต้องตรวจ"}
FORMAT_LABEL = {"video": "วิดีโอคอล", "phone": "โทรศัพท์", "onsite": "พบที่ออฟฟิศ"}
WEEKDAY_TH = ("จ.", "อ.", "พ.", "พฤ.", "ศ.", "ส.", "อา.")
MONTH_TH = ("ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค.")
OFFER_N = 6
DEFAULT_CFG = {"days": [], "times": [], "duration_min": 30, "lead_hours": 4, "horizon_days": 7, "capacity": 1, "blackout": [], "format": "video"}


class SlotTaken(Exception):
    pass


class NotOffered(Exception):
    pass


def now() -> dt.datetime:
    return dt.datetime.now(TZ).replace(microsecond=0)


def _iso(t: dt.datetime) -> str:
    return t.astimezone(TZ).isoformat(timespec="seconds")


def parse(s: str) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    return t.replace(tzinfo=TZ) if t.tzinfo is None else t.astimezone(TZ)


# ---------- ตั้งค่าช่องเวลา (ทีมเป็นคนตั้ง) ----------
def config() -> dict:
    try:
        c = json.loads(db.get_setting("appt_slots") or "{}")
    except ValueError:
        c = {}
    return {**DEFAULT_CFG, **{k: v for k, v in c.items() if k in DEFAULT_CFG}}


def configured() -> bool:
    c = config()
    return bool(c["days"] and c["times"])


def ready(request=None) -> bool:
    """เปิดให้ลูกค้าเลือกเวลาได้: flag line_appointments + LINE พร้อม + ทีมตั้งช่องเวลาแล้ว"""
    from . import line_plan
    return line_plan.active(request) and configured()


_HM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def validate_config(form: dict) -> tuple[dict, dict]:
    err = {}
    days = sorted({int(d) for d in (form.get("days") or []) if str(d).isdigit() and 0 <= int(d) <= 6})
    times = sorted({t.strip() for t in re.split(r"[,\s]+", form.get("times") or "") if t.strip()})
    if any(not _HM.match(t) for t in times):
        err["times"] = "เวลาให้พิมพ์แบบ 10:00, 13:30 คั่นด้วยจุลภาค"
    if len(times) > 8:
        err["times"] = "ไม่เกิน 8 ช่วงต่อวัน"

    def num(k, lo, hi):
        try:
            v = int(form.get(k) or DEFAULT_CFG[k])
        except ValueError:
            err[k] = "ใส่เป็นตัวเลข"
            return DEFAULT_CFG[k]
        if not lo <= v <= hi:
            err[k] = f"ต้องอยู่ระหว่าง {lo}–{hi}"
        return v
    black = sorted({b.strip() for b in re.split(r"[,\s]+", form.get("blackout") or "") if b.strip()})
    for b in black:
        try:
            dt.date.fromisoformat(b)
        except ValueError:
            err["blackout"] = "วันหยุดให้พิมพ์แบบ 2026-12-05 คั่นด้วยจุลภาค"
    fmt = form.get("format") if form.get("format") in FORMAT_LABEL else "video"
    c = {"days": days, "times": [t for t in times if _HM.match(t)], "duration_min": num("duration_min", 15, 120), "lead_hours": num("lead_hours", 1, 72),
         "horizon_days": num("horizon_days", 1, 30), "capacity": num("capacity", 1, 5), "blackout": black[:60], "format": fmt}
    return c, err


def save_config(c: dict) -> None:
    db.set_setting("appt_slots", json.dumps(c, ensure_ascii=False))


# ---------- ช่องเวลา ----------
def _active_counts(c) -> dict:
    rows = c.execute(db.q(f"SELECT slot_start, lead_id FROM appointments WHERE status IN ({','.join('?' * len(ACTIVE))}) AND slot_start IS NOT NULL"),
                     ACTIVE).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["slot_start"], []).append(r["lead_id"])
    return out


def slots(at: dt.datetime | None = None, limit: int = OFFER_N, exclude_lead: int | None = None) -> list[str]:
    """ช่องเวลาว่างถัดไป (ISO +07:00) · ข้ามวันหยุดที่ตั้ง · ไม่เสนอเร็วกว่า lead_hours · ช่องที่เต็มไม่เสนอ · ช่องของลีดเองไม่เสนอซ้ำ"""
    cfg = config()
    if not (cfg["days"] and cfg["times"]):
        return []
    at = at or now()
    earliest = at + dt.timedelta(hours=cfg["lead_hours"])
    with db.get_conn() as c:
        used = _active_counts(c)
    out = []
    for d in range(cfg["horizon_days"] + 1):
        day = (at + dt.timedelta(days=d)).date()
        if day.weekday() not in cfg["days"] or day.isoformat() in cfg["blackout"]:
            continue
        for t in cfg["times"]:
            hh, mm = map(int, t.split(":"))
            s = dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=TZ)
            if s < earliest:
                continue
            key = _iso(s)
            holders = used.get(key, [])
            if len(holders) >= cfg["capacity"] or (exclude_lead and exclude_lead in holders):
                continue
            out.append(key)
            if len(out) >= limit:
                return out
    return out


def label(iso: str | None) -> str:
    t = parse(iso or "")
    if not t:
        return "—"
    return f"{WEEKDAY_TH[t.weekday()]} {t.day} {MONTH_TH[t.month - 1]} {t:%H:%M}"


def slot_code(iso: str) -> str:
    return parse(iso).strftime("%Y%m%d%H%M")


def from_code(code: str) -> str | None:
    if not re.fullmatch(r"\d{12}", code or ""):              # strptime ยอมเลขไม่ครบหลัก (เช่น 20261399) → บังคับ 12 หลัก
        return None
    try:
        return _iso(dt.datetime.strptime(code, "%Y%m%d%H%M").replace(tzinfo=TZ))
    except (TypeError, ValueError):
        return None


# ---------- นัด ----------
def get(appt_id: int):
    with db.get_conn() as c:
        return c.execute(db.q("SELECT * FROM appointments WHERE id=?"), (appt_id,)).fetchone()


def active_for_lead(lead_id: int):
    with db.get_conn() as c:
        return c.execute(db.q(f"SELECT * FROM appointments WHERE lead_id=? AND status IN ({','.join('?' * len(ACTIVE))}) ORDER BY id DESC LIMIT 1"),
                         (lead_id, *ACTIVE)).fetchone()


def latest_for_lead(lead_id: int):
    with db.get_conn() as c:
        return c.execute(db.q("SELECT * FROM appointments WHERE lead_id=? ORDER BY id DESC LIMIT 1"), (lead_id,)).fetchone()


def list_upcoming(limit: int = 100):
    with db.get_conn() as c:
        return c.execute(db.q("SELECT a.*, l.domain FROM appointments a LEFT JOIN leads l ON l.id=a.lead_id "
                              "ORDER BY CASE a.status WHEN 'pending_confirmation' THEN 0 WHEN 'confirmed' THEN 1 WHEN 'requested' THEN 2 ELSE 3 END, "
                              "a.slot_start, a.id DESC LIMIT ?"), (limit,)).fetchall()


def history(a) -> list:
    try:
        return json.loads(a["history_json"] or "[]")
    except (TypeError, ValueError):
        return []


def _hist(a, to: str, by: str, reason: str = "", slot: str | None = None) -> str:
    h = history(a) if a else []
    h.append({"version": (a["version"] + 1) if a else 1, "from": a["status"] if a else None, "to": to, "slot": slot, "by": by,
              "reason": reason[:200], "at": _iso(now())})
    return json.dumps(h[-50:], ensure_ascii=False)


def request(lead_id: int, slot_iso: str, *, by: str = "customer", plan_id: int | None = None, line_user_id: str | None = None,
            at: dt.datetime | None = None) -> dict:
    """ลูกค้าเลือกช่องเวลา: ต้องเป็นช่องที่ระบบเสนอได้ ณ ตอนนี้ · มีนัดอยู่แล้ว = เลื่อน (version+1) · กันช่องแบบ atomic"""
    cfg = config()
    if slot_iso not in slots(at, limit=500, exclude_lead=lead_id):
        raise NotOffered(slot_iso)
    nowi = _iso(now())
    with db.get_conn() as c:
        if db.IS_PG:
            c.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"appt:{slot_iso}",))
        cur = c.execute(db.q(f"SELECT * FROM appointments WHERE lead_id=? AND status IN ({','.join('?' * len(ACTIVE))}) ORDER BY id DESC LIMIT 1"),
                        (lead_id, *ACTIVE)).fetchone()
        if cur:
            c.execute(db.q("UPDATE appointments SET slot_start=?, status='pending_confirmation', version=version+1, history_json=?, updated_at=?, "
                           "confirmed_by=NULL, confirmed_at=NULL WHERE id=?"),
                      (slot_iso, _hist(cur, "pending_confirmation", by, "เลื่อนเวลา" if cur["slot_start"] else "เลือกเวลา", slot_iso), nowi, cur["id"]))
            aid, kind = cur["id"], ("rescheduled" if cur["slot_start"] else "picked")
        else:
            aid = c.execute(db.q("INSERT INTO appointments(lead_id, plan_id, line_user_id, status, slot_start, duration_min, timezone, format, version, "
                                 "history_json, created_at, updated_at) VALUES(?,?,?,'pending_confirmation',?,?,'Asia/Bangkok',?,1,?,?,?) RETURNING id"),
                            (lead_id, plan_id, line_user_id, slot_iso, cfg["duration_min"], cfg["format"],
                             _hist(None, "pending_confirmation", by, "เลือกเวลา", slot_iso), nowi, nowi)).fetchone()["id"]
            kind = "picked"
        n = c.execute(db.q(f"SELECT COUNT(*) AS n FROM appointments WHERE slot_start=? AND status IN ({','.join('?' * len(ACTIVE))})"),
                      (slot_iso, *ACTIVE)).fetchone()["n"]
        if n > cfg["capacity"]:
            raise SlotTaken(slot_iso)                          # ยกเลิกทั้งธุรกรรม (ช่องเต็มพอดีระหว่างเลือก)
    db.add_lead_event(lead_id, "meeting_request", f"ขอนัดคุย {label(slot_iso)} ({'เลื่อนเวลา' if kind == 'rescheduled' else 'เลือกเวลา'} · รอทีมยืนยัน)", by)
    return {"id": aid, "kind": kind}


def _set_status(a, to: str, by: str, reason: str = "", extra: dict | None = None, expect: tuple = ACTIVE) -> bool:
    """เปลี่ยนสถานะแบบ compare-and-set ด้วย version (กดซ้ำ/กดพร้อมกัน = เปลี่ยนครั้งเดียว)"""
    f = {"status": to, "version": a["version"] + 1, "history_json": _hist(a, to, by, reason, a["slot_start"]), "updated_at": _iso(now()), **(extra or {})}
    with db.get_conn() as c:
        cur = c.execute(db.q(f"UPDATE appointments SET {', '.join(k + '=?' for k in f)} WHERE id=? AND version=? AND status IN ({','.join('?' * len(expect))})"),
                        (*f.values(), a["id"], a["version"], *expect))
        return (cur.rowcount or 0) == 1


def confirm(appt_id: int, staff: str, version: int) -> tuple[bool, str]:
    """ทีมยืนยัน = นัดสำเร็จจริง · ต้องเป็นเวอร์ชันที่ทีมเห็นอยู่ (ลูกค้าเลื่อนระหว่างนั้น = ไม่ยืนยันเวลาเก่า)"""
    a = get(appt_id)
    if not a or a["status"] != "pending_confirmation":
        return False, "สถานะไม่ใช่รอยืนยัน"
    if a["version"] != version:
        return False, "ลูกค้าเปลี่ยนเวลาแล้ว โหลดหน้าใหม่ก่อนยืนยัน"
    if (parse(a["slot_start"]) or now()) <= now():
        return False, "เวลานัดผ่านไปแล้ว"
    if not _set_status(a, "confirmed", staff, "ทีมยืนยัน", {"confirmed_by": staff[:80], "confirmed_at": _iso(now())}, ("pending_confirmation",)):
        return False, "มีคนเปลี่ยนนัดนี้พร้อมกัน ลองใหม่"
    a = get(appt_id)
    t = parse(a["slot_start"])
    db.update_lead(a["lead_id"], next_at=t.replace(tzinfo=None).isoformat(timespec="seconds"), next_kind="meeting",
                   next_action=f"นัดคุย {a['duration_min']} นาที ({FORMAT_LABEL.get(a['format'], a['format'])}) จาก LINE", next_alerted_at=None)
    db.add_lead_event(a["lead_id"], "meeting", f"ยืนยันนัดคุย {label(a['slot_start'])} โดย {staff}", "staff")
    from . import analytics
    analytics.emit("meeting_booked", a["lead_id"], {})
    _queue_messages(a, ("confirmed", "reminder_24h", "reminder_1h"))
    return True, "ok"


def cancel(appt_id: int, by: str, reason: str = "") -> bool:
    a = get(appt_id)
    if not a or a["status"] not in ACTIVE:
        return False
    was_confirmed = a["status"] == "confirmed"
    if not _set_status(a, "cancelled", by, reason or ("ลูกค้ายกเลิก" if by == "customer" else "ทีมยกเลิก")):
        return False
    lead = db.get_lead(a["lead_id"])
    if lead and lead["next_kind"] == "meeting" and was_confirmed:
        db.update_lead(a["lead_id"], next_at=None, next_kind=None, next_action=None)
    db.add_lead_event(a["lead_id"], "meeting", f"ยกเลิกนัด {label(a['slot_start'])} ({'ลูกค้า' if by == 'customer' else by})", by if by == "customer" else "staff")
    if by != "customer" and was_confirmed:
        _queue_messages(get(appt_id), ("cancelled",))
    return True


def mark(appt_id: int, to: str, staff: str) -> tuple[bool, str]:
    """หลังเวลานัด: คุยแล้ว / ลูกค้าไม่มา"""
    a = get(appt_id)
    if to not in ("completed", "no_show") or not a or a["status"] != "confirmed":
        return False, "บันทึกได้เฉพาะนัดที่ยืนยันแล้ว"
    if (parse(a["slot_start"]) or now()) > now():
        return False, "ยังไม่ถึงเวลานัด"
    if not _set_status(a, to, staff, STATUS_LABEL[to], expect=("confirmed",)):
        return False, "มีคนเปลี่ยนนัดนี้พร้อมกัน ลองใหม่"
    db.add_lead_event(a["lead_id"], "meeting", f"{STATUS_LABEL[to]} ({label(a['slot_start'])})", "staff")
    return True, "ok"


# ---------- ข้อความที่ระบบส่งเอง → คิวงาน + outbox โหมดทดสอบ ----------
MSG_OFFSET = {"confirmed": None, "cancelled": None, "reminder_24h": dt.timedelta(hours=24), "reminder_1h": dt.timedelta(hours=1)}


def message_text(kind: str, a) -> str:
    w = label(a["slot_start"])
    fmt = FORMAT_LABEL.get(a["format"], a["format"])
    return {"confirmed": f"ยืนยันนัดคุยกับทีมเจอ.AI {w} ({a['duration_min']} นาที · {fmt}) ถ้าต้องการเลื่อน พิมพ์ \"เลื่อนนัด\" ในแชตนี้ได้เลยครับ",
            "reminder_24h": f"เตือนนัดคุยกับทีมเจอ.AI พรุ่งนี้ {w} ถ้าไม่สะดวก พิมพ์ \"เลื่อนนัด\" หรือ \"ยกเลิกนัด\" ได้ครับ",
            "reminder_1h": f"อีก 1 ชั่วโมงถึงนัดคุยกับทีมเจอ.AI ({w}) แล้วพบกันครับ",
            "cancelled": f"นัดคุย {w} ถูกยกเลิกแล้ว ถ้าต้องการนัดใหม่ พิมพ์ \"นัด\" ในแชตนี้ได้เลยครับ"}[kind]


def _queue_messages(a, kinds: tuple) -> list:
    """เข้าคิว appt_message ต่อชนิด (เตือน = ตั้งเวลาล่วงหน้า) · ผูกกับ version ตอนนี้: ลูกค้าเลื่อน/ยกเลิกทีหลัง = งานเก่าไม่ทำ
    คิวงานปิด = ไม่มีข้อความอัตโนมัติ (ทีมแจ้งเอง)"""
    from . import jobs
    if not jobs.enabled():
        return []
    out = []
    t = parse(a["slot_start"])
    for k in kinds:
        delay = 0
        if MSG_OFFSET[k]:
            when = t - MSG_OFFSET[k]
            if when <= now():
                continue                                       # นัดใกล้กว่าเวลาเตือน = ไม่เตือนย้อนหลัง
            delay = int((when - now()).total_seconds())
        jid, st = jobs.enqueue("appt_message", {"appt_id": a["id"], "version": a["version"], "kind": k},
                               idem_key=f"appt:{a['id']}:v{a['version']}:{k}", lead_id=a["lead_id"], channel="line", max_attempts=3, delay_s=delay)
        out.append((k, jid, st))
    jobs.kick()
    return out


def handle_message(job, worker: str) -> dict:
    """handler ของคิวงาน: ตรวจตอนจะทำว่านัดยังเป็นเวอร์ชันเดิมและสถานะเหมาะ · ลูกค้าขอหยุดแล้ว = ยกเลิก · เขียน outbox ทดสอบ (ไม่ส่งจริง)"""
    from . import jobs
    p = json.loads(job["payload_json"] or "{}")
    a = get(int(p.get("appt_id") or 0))
    kind = p.get("kind")
    if not a or kind not in MSG_OFFSET:
        raise jobs.Permanent("ไม่พบนัดหรือชนิดข้อความ")
    want = "cancelled" if kind == "cancelled" else "confirmed"
    if a["status"] != want or (kind != "cancelled" and a["version"] != p.get("version")):
        raise jobs.Cancelled("นัดเปลี่ยนไปแล้ว (เลื่อน/ยกเลิก) ข้อความนี้ไม่ใช้")
    lead = db.get_lead(a["lead_id"])
    if not lead or lead["unsub_at"]:
        raise jobs.Cancelled("ลูกค้าขอไม่รับข้อความ")
    s = db.get_line_session(a["line_user_id"]) if a["line_user_id"] else None
    if s and s["step"] == "stopped":
        raise jobs.Cancelled("ลูกค้าขอให้หยุดติดต่อใน LINE")
    if not a["line_user_id"]:
        raise jobs.Cancelled("นัดนี้ไม่ได้มาจาก LINE")
    oid, created = jobs.outbox_put(f"appt:{a['id']}:v{p.get('version')}:{kind}", job_id=job["id"], channel="line",
                                   content=message_text(kind, a), lead_id=a["lead_id"], recipient=a["line_user_id"])
    return {"outbox_id": oid, "created": created}
