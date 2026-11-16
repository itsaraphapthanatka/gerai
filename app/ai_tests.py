"""นโยบายการทดสอบ AI แบบเสียเงิน (stage 04C) — ใช้กับเส้นทางคิวงาน (flag durable_jobs) เท่านั้น · เส้นทาง thread เดิมไม่เปลี่ยน

- **สวิตช์ทดสอบจริง** `ai_live_tests` (ค่าเริ่มต้นปิด): ปิด = ไม่เรียก AI แบบเสียเงินเลย รายงานบอกว่า "ยังไม่เปิดการทดสอบ AI"
- **สิทธิ์ (eligibility) ตามเหตุผลที่สั่ง:** ลูกค้าขอแผน/ทิ้งช่องทางติดต่อ/ขอนัด = ทำได้อัตโนมัติ 1 ครั้งต่อลีดต่อ 30 วัน ·
  ทีมกดเอง = สูงสุด 3 ครั้งต่อลีดต่อ 30 วัน · ตอบกลับเฉย ๆ / เอเจนต์ AI สั่ง / กดติดต่อหรือดูราคา = ไม่ทำเอง (รอทีมกด)
- **ต้องมีข้อมูลพอ:** ไม่มีคำค้นและลูกค้ายังไม่ยืนยันบริการหลัก = needs_input (ไม่เดาคำถามเอง)
- **ลองใหม่ไม่จ่ายซ้ำ:** คำตอบที่ได้แล้วเก็บในตาราง ai_answer_cache ผูกกับงาน (idem_key) → งานเดิมที่ทำซ้ำหลังเครื่องล่มใช้คำตอบเดิม
- งบ: จองผ่าน jobs.reserve ก่อนเรียก (stage 10)"""
from __future__ import annotations

import datetime as dt
import hashlib
import json

from . import db

AUTO_REASONS = {"plan_requested": "ลูกค้าขอแผนจากรายงาน", "contact_form": "ลูกค้าทิ้งช่องทางติดต่อ", "meeting_request": "ลูกค้าขอนัดคุย"}
STAFF_REASONS = {"staff": "ทีมกดสั่งเอง"}
MANUAL_ONLY = {"replied": "ลูกค้าตอบกลับ (ยังไม่ได้ขอให้ทดสอบ)", "agent": "เอเจนต์ AI เสนอ", "unspecified": "ไม่ระบุเหตุผล"}
AUTO_PER_30D = 1
STAFF_PER_30D = 3


def live() -> bool:
    return (db.get_setting("ai_live_tests") or "0") == "1"


def set_live(on: bool) -> None:
    db.set_setting("ai_live_tests", "1" if on else "0")


def _recent(lead_id: int, days: int = 30) -> list[dict]:
    since = (dt.datetime.now() - dt.timedelta(days=days)).isoformat(timespec="seconds")
    with db.get_conn() as c:
        rows = c.execute(db.q("SELECT payload_json, status FROM jobs WHERE kind='deep_ai' AND lead_id=? AND created_at>=? "
                              "AND status IN ('queued','running','succeeded')"), (lead_id, since)).fetchall()
    out = []
    for r in rows:
        try:
            out.append(json.loads(r["payload_json"] or "{}"))
        except ValueError:
            continue
    return out


def eligibility(lead, reason: str) -> tuple[bool, str]:
    """(ทำได้ไหม, เหตุผลภาษาไทย) — ไม่รวมสวิตช์ทดสอบจริงและงบ (เช็กแยก)"""
    from . import deep_audit, business_context as BC
    if not lead:
        return False, "ไม่พบลีด"
    if lead["unsub_at"] or lead["status"] == "unsubscribed":
        return False, "ลูกค้าขอไม่รับการติดต่อ"
    if reason in MANUAL_ONLY:
        return False, f"{MANUAL_ONLY[reason]} — ทีมกดสั่งทดสอบ AI ได้ในหน้าลีด"
    if reason not in AUTO_REASONS and reason not in STAFF_REASONS:
        return False, "ไม่รู้จักเหตุผลที่สั่ง"
    t1 = deep_audit.tier1(lead)
    if t1.get("state") == "done":
        try:
            ctx = BC.for_ai(lead)
        except Exception:
            ctx = None
        if not (t1.get("keywords") or (ctx and (ctx.get("primary_service") or ctx.get("questions")))):
            return False, "needs_input"
    rec = _recent(lead["id"])
    if reason in AUTO_REASONS and sum(1 for p in rec if p.get("reason") in AUTO_REASONS) >= AUTO_PER_30D:
        return False, "ทดสอบอัตโนมัติไปแล้วในรอบ 30 วัน — ทีมกดสั่งใหม่ได้ถ้าจำเป็น"
    if reason in STAFF_REASONS and sum(1 for p in rec if p.get("reason") in STAFF_REASONS) >= STAFF_PER_30D:
        return False, f"ทีมสั่งครบ {STAFF_PER_30D} ครั้งในรอบ 30 วันแล้ว"
    return True, AUTO_REASONS.get(reason) or STAFF_REASONS.get(reason)


def estimate() -> dict:
    """ค่าใช้จ่ายโดยประมาณ (ค่าวัดจริงเดิม ≈ $1.15 ต่อ 20 คำถาม) · เพดานวันนี้จากระบบงบ stage 10"""
    from . import jobs, deep_audit
    lim = jobs.budget_limit("ai_usd")
    return {"prompts": deep_audit.N_PROMPTS, "est_per_run_usd": jobs.AI_RUN_EST_USD, "daily_budget_usd": lim,
            "max_runs_per_day": int(lim // jobs.AI_RUN_EST_USD) if jobs.AI_RUN_EST_USD else 0, "count_cap_per_day": deep_audit.ai_daily()}


# ---------- แคชคำตอบต่องาน (ลองใหม่ไม่จ่ายซ้ำ) ----------
def _key(job_key: str, engine: str, prompt: str) -> str:
    return hashlib.sha256(f"{job_key}\n{engine}\n{prompt}".encode()).hexdigest()


def cached_ask(job_key: str, engine: str, ask):
    """ห่อตัวถาม AI: ถามแล้วสำเร็จ = เก็บ · งานเดิมถามซ้ำ = ใช้ของเดิม (ไม่จ่ายซ้ำ) · ที่ล้มไม่เก็บ (ลองใหม่ได้)"""
    def run(prompt: str) -> dict:
        k = _key(job_key, engine or "", prompt)
        with db.get_conn() as c:
            row = c.execute(db.q("SELECT response_json FROM ai_answer_cache WHERE cache_key=?"), (k,)).fetchone()
        if row:
            r = json.loads(row["response_json"])
            return {**r, "cost_usd": 0, "cached": True}            # จ่ายไปแล้วในรอบก่อน ไม่นับซ้ำในยอดรอบนี้
        r = ask(prompt)
        if r.get("ok"):
            with db.get_conn() as c:
                c.execute(db.q("INSERT INTO ai_answer_cache(cache_key, job_key, response_json, cost_usd, created_at) VALUES(?,?,?,?,?) "
                               "ON CONFLICT (cache_key) DO NOTHING"),
                          (k, job_key[:200], json.dumps({x: r.get(x) for x in ("ok", "text", "citations")}, ensure_ascii=False),
                           float(r.get("cost_usd") or 0), db.now()))
        return r
    return run


def cached_cost(job_key: str) -> float:
    with db.get_conn() as c:
        r = c.execute(db.q("SELECT COALESCE(SUM(cost_usd),0) AS s FROM ai_answer_cache WHERE job_key=?"), (job_key[:200],)).fetchone()
    return float(r["s"])
