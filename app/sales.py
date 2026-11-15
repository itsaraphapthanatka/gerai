"""ขาย/ติดตาม (stage 11) — ความยินยอมกลาง · ช่องทางที่ได้รับอนุญาต · ขั้นโอกาสขาย · คะแนนคัดกรองที่อธิบายได้

- `contact_block()` ใช้ทุกครั้งก่อนส่ง (ทำงานเสมอ ไม่ผ่าน flag — เข้มขึ้นอย่างเดียว)
- ที่เหลืออยู่หลัง flag `sales_v2`:
  · ส่งเฉพาะช่องทางที่มีที่มาของสิทธิ์ (ลูกค้าให้มาเอง/ทักเรามาเอง) · **ไม่ไล่ข้ามช่องทางเพราะลูกค้าเงียบหรือช่องแรกส่งไม่ได้**
  · อีเมลที่หาเจอบนเว็บสาธารณะ ≠ สิทธิ์ทางกฎหมาย → ใช้ได้เมื่อเจ้าของเปิด setting `allow_public_email_outreach` เท่านั้น
  · ขั้นโอกาสขายคำนวณจากข้อมูลเดิม (ไม่เขียนทับสถานะเดิม) + `opp_override` (nurture) + `lost_reason`
  · คะแนนคัดกรอง qual-v1: น้ำหนักเป็นสมมติฐาน ปรับได้ · ไม่รู้ = ไม่นับ (ไม่ใช่ 0) · การเปิดดูรายงาน/ราคาไม่นับเป็นความพร้อมซื้อ"""
from __future__ import annotations

import datetime as dt
import json
import re

from . import db, flags, leads as L


def enabled() -> bool:
    try:
        return flags.enabled("sales_v2")
    except Exception:
        return False


# ---------- ความยินยอมกลาง (เช็กตอนจะส่งทุกครั้ง) ----------
def contact_block(lead, channel: str | None = None, purpose: str = "sales") -> str:
    """'' = ติดต่อได้ · อื่น ๆ = เหตุผลที่ห้าม · purpose sales = ทัก/ติดตาม/ตอบเรื่องขาย"""
    if not lead:
        return "ไม่พบลีด"
    if lead["unsub_at"] or lead["status"] == "unsubscribed":
        return "ลูกค้าขอไม่รับการติดต่อ"
    with db.get_conn() as c:
        pr = c.execute(db.q("SELECT status FROM plan_requests WHERE lead_id=? ORDER BY id DESC LIMIT 1"), (lead["id"],)).fetchone()
        stopped = c.execute(db.q("SELECT 1 FROM line_plan_sessions WHERE lead_id=? AND step='stopped' LIMIT 1"), (lead["id"],)).fetchone()
    if pr and pr["status"] == "withdrawn":
        return "ลูกค้าขอให้หยุดติดต่อเรื่องคำขอแผน"
    if stopped and (channel in (None, "line") or purpose == "sales"):
        return "ลูกค้าขอให้หยุดติดต่อใน LINE"
    return ""


# ---------- ช่องทางที่ได้รับอนุญาต (sales_v2) ----------
CH_LABEL = {"email": "อีเมล", "line": "LINE", "sms": "SMS"}


def channel_basis(lead) -> dict:
    """{ช่องทาง: ที่มาของสิทธิ์} เฉพาะช่องทางที่มีที่มา · ไม่มีในนี้ = ห้ามส่งทางนั้น"""
    from . import mailer
    out = {}
    with db.get_conn() as c:
        pr = c.execute(db.q("SELECT contact_channel, contact_value FROM plan_requests WHERE lead_id=? AND status='stored' ORDER BY id DESC LIMIT 1"),
                       (lead["id"],)).fetchone()
        emailed_us = c.execute(db.q("SELECT 1 FROM inbox_messages WHERE lead_id=? AND category NOT IN ('auto','bounce','unmatched') LIMIT 1"),
                               (lead["id"],)).fetchone()
    em = lead["email"] or ""
    if em and mailer.is_deliverable(em):
        if lead["email_src"] == "form" or (pr and pr["contact_channel"] == "email"):
            out["email"] = "ลูกค้าให้อีเมลเองในฟอร์ม"
        elif emailed_us or (lead["source"] or "") == "email":
            out["email"] = "ลูกค้าส่งอีเมลมาหาเราเอง"
        elif (db.get_setting("allow_public_email_outreach") or "0") == "1":
            out["email"] = "อีเมลสาธารณะบนเว็บ (เจ้าของเปิดนโยบายให้ทักได้)"
    if lead["line_user_id"]:
        out["line"] = "ลูกค้าทักเรามาใน LINE"
    if pr and pr["contact_channel"] == "phone" and lead["consent"]:
        out["sms"] = "ลูกค้าให้เบอร์เองและยินยอมให้ติดต่อเรื่องคำขอ"
    return out


def pick_channel(lead) -> str:
    """ช่องทางเดียวที่ใช้: ตามที่ลูกค้าเลือกในคำขอแผนก่อน แล้วอีเมล > LINE · ไม่มีสิทธิ์ = '' (ไม่ส่ง)"""
    basis = channel_basis(lead)
    with db.get_conn() as c:
        pr = c.execute(db.q("SELECT contact_channel FROM plan_requests WHERE lead_id=? AND status='stored' ORDER BY id DESC LIMIT 1"),
                       (lead["id"],)).fetchone()
    pref = {"email": "email", "line": "line", "phone": "sms"}.get(pr["contact_channel"]) if pr else None
    for ch in ([pref] if pref else []) + ["email", "line"]:
        if ch and ch in basis:
            return ch
    return ""


# ---------- ขั้นโอกาสขาย (แปลงจากสถานะเดิม ย้อนกลับได้ ไม่เขียนทับ) ----------
STAGES = [("new", "ใหม่"), ("needs_context", "รอข้อมูลธุรกิจ"), ("qualified", "ผ่านการคัดกรอง"), ("meeting_booked", "นัดคุยแล้ว"),
          ("meeting_held", "คุยแล้ว"), ("proposal_sent", "ส่งข้อเสนอแล้ว"), ("won", "ปิดดีล"), ("lost", "ไม่ได้ดีล"), ("nurture", "รอจังหวะ")]
STAGE_LABEL = dict(STAGES)
LOST_REASONS = {"not_now": "ยังไม่ใช่ตอนนี้", "no_budget": "งบไม่พอ", "has_vendor": "มีผู้ให้บริการอยู่แล้ว", "no_fit": "ไม่ตรงกับบริการ",
                "no_response": "ไม่ตอบกลับ", "price": "ราคา", "other": "อื่น ๆ"}
# สถานะเดิม → ขั้นตั้งต้น (ข้อยกเว้นอธิบายใน STATUS stage 11)
LEGACY = {"new": "new", "contacted": "new", "replied": "needs_context", "call": "qualified", "met": "meeting_held", "quoted": "proposal_sent",
          "won": "won", "lost": "lost", "unsubscribed": "lost"}


def opportunity(lead) -> dict:
    """ขั้นปัจจุบัน + เหตุผล · ความยินยอม (permission) แยกจากขั้น: ขอไม่รับ ≠ แพ้ดีล ในความหมาย แต่ห้ามติดต่อ"""
    from . import appointments as AP
    st = lead["status"] or "new"
    stage, why = LEGACY.get(st, "new"), f"จากสถานะเดิม \"{L.STATUS_LABEL.get(st, st)}\""
    override = lead["opp_override"] if "opp_override" in lead.keys() else None
    a = AP.latest_for_lead(lead["id"])
    if st not in ("won", "lost", "unsubscribed", "quoted", "met"):
        if a and a["status"] == "confirmed":
            stage, why = "meeting_booked", f"ทีมยืนยันนัด {AP.label(a['slot_start'])}"
        elif a and a["status"] in ("completed",):
            stage, why = "meeting_held", "บันทึกว่าคุยแล้ว"
        else:
            with db.get_conn() as c:
                pr = c.execute(db.q("SELECT 1 FROM plan_requests WHERE lead_id=? AND status='stored' LIMIT 1"), (lead["id"],)).fetchone()
                ctx = c.execute(db.q("SELECT 1 FROM business_contexts WHERE lead_id=? LIMIT 1"), (lead["id"],)).fetchone()
            if pr and stage in ("new", "needs_context"):
                stage, why = ("qualified", "ขอแผนและยืนยันข้อมูลธุรกิจแล้ว") if ctx else ("needs_context", "ขอแผนแล้ว แต่ข้อมูลธุรกิจยังเป็นที่ระบบเดา")
    if override == "nurture" and stage not in ("won",):
        stage, why = "nurture", "ทีมตั้งเป็นรอจังหวะ"
    perm = "blocked" if (lead["unsub_at"] or st == "unsubscribed") else "ok"
    lr = lead["lost_reason"] if "lost_reason" in lead.keys() else None
    return {"stage": stage, "label": STAGE_LABEL[stage], "why": why, "permission": perm, "lost_reason": lr,
            "lost_reason_label": LOST_REASONS.get(lr) if lr else None, "legacy_status": st}


def set_opportunity(lead_id: int, action: str, reason: str | None, by: str, expected_status: str) -> tuple[bool, str]:
    """ทีมตั้ง: nurture / lost (ต้องมีเหตุผล) / clear (ล้างค่าที่ตั้งเอง) · compare-and-set กับสถานะเดิมที่ทีมเห็นอยู่"""
    if action == "lost" and reason not in LOST_REASONS:
        return False, "เลือกเหตุผลที่ไม่ได้ดีล"
    sets = {"nurture": ("opp_override='nurture', lost_reason=?", (reason if reason in LOST_REASONS else "not_now",)),
            "lost": ("status='lost', opp_override=NULL, lost_reason=?", (reason,)),
            "clear": ("opp_override=NULL", ())}
    if action not in sets:
        return False, "ไม่รู้จักคำสั่ง"
    frag, args = sets[action]
    with db.get_conn() as c:
        cur = c.execute(db.q(f"UPDATE leads SET {frag} WHERE id=? AND status=?"), (*args, lead_id, expected_status))
        ok = (cur.rowcount or 0) == 1
    if not ok:
        return False, "สถานะลีดเปลี่ยนไปแล้ว โหลดหน้าใหม่"
    note = {"nurture": "ตั้งเป็นรอจังหวะ", "lost": "ปิดเป็นไม่ได้ดีล", "clear": "ล้างขั้นที่ตั้งเอง"}[action]
    db.add_lead_event(lead_id, "status", note + (f" ({LOST_REASONS.get(reason, reason)})" if reason else "") + f" โดย {by}", "staff")
    if action in ("nurture", "lost"):
        from . import pipeline
        pipeline._cancel_pending(lead_id, {"outreach", "followup"}, "ทีมตั้งเป็น" + ("รอจังหวะ" if action == "nurture" else "ไม่ได้ดีล"))
    return True, "ok"


# ---------- คะแนนคัดกรอง (qual-v1) ----------
QUAL_VERSION = "qual-v1"
DEFAULT_WEIGHTS = {"fit": 1.0, "intent": 2.0, "readiness": 1.0, "problem": 1.0}
FACTOR_LABEL = {"fit": "ตรงกับธุรกิจที่เราช่วยได้", "intent": "สัญญาณว่าอยากคุย/อยากได้", "readiness": "ความพร้อมเริ่มงาน", "problem": "ปัญหาที่ตรวจเจอพร้อมหลักฐาน"}


def weights() -> dict:
    try:
        w = json.loads(db.get_setting("qual_weights") or "{}")
    except ValueError:
        w = {}
    return {k: float(w.get(k, v)) for k, v in DEFAULT_WEIGHTS.items()}


def qualify(lead) -> dict:
    """แต่ละปัจจัย: +1 (หนุน) / 0 (กลาง) / -1 (ขัด) / None (ยังไม่รู้ ไม่นับ) พร้อมเหตุผล · คะแนน = ถ่วงน้ำหนักเฉพาะปัจจัยที่รู้ (ต้องรู้ ≥ 2 ข้อ)"""
    from . import business_context as BC, report_view as RV
    f = {}
    with db.get_conn() as c:
        pr = c.execute(db.q("SELECT * FROM plan_requests WHERE lead_id=? AND status='stored' ORDER BY id DESC LIMIT 1"), (lead["id"],)).fetchone()
        evs = {r["kind"] for r in c.execute(db.q("SELECT DISTINCT kind FROM lead_events WHERE lead_id=?"), (lead["id"],)).fetchall()}
    try:
        cur = BC.current(lead)
    except Exception:
        cur = {"confirmed": False, "fields": {}}
    svc = (cur["fields"].get("primary_service") or {})
    if cur["confirmed"] and svc.get("value"):
        f["fit"] = (1, f"ลูกค้ายืนยันบริการหลัก: {svc['value']}")
    elif lead["status"] == "lost" and lead["lost_reason"] == "no_fit" if "lost_reason" in lead.keys() else False:
        f["fit"] = (-1, "ทีมบันทึกว่าไม่ตรงกับบริการ")
    else:
        f["fit"] = (None, "ยังไม่ได้ยืนยันว่าธุรกิจทำอะไร (ที่ระบบเดาไม่นับ)")
    if pr or "wants_call" in evs:
        f["intent"] = (1, "ขอแผน/ขอนัดคุยเอง")
    elif "replied" in evs:
        f["intent"] = (0, "ตอบกลับ แต่ยังไม่ได้ขอคุย")
    elif lead["status"] == "lost":
        f["intent"] = (-1, "ตอบว่าไม่สนใจ")
    else:
        f["intent"] = (None, "ยังไม่มีสัญญาณจากลูกค้า (การเปิดดูรายงาน/ราคาไม่นับ)")
    if pr and (pr["desired_window"] or pr["web_readiness"] or pr["budget"]):
        w, r, b = pr["desired_window"] or "", pr["web_readiness"] or "", pr["budget"] or ""
        pts = 1 if w in ("now", "1m") or r in ("own_team", "agency") else (0 if w in ("3m", "later") else 0)
        if w == "unsure" and not r and not b:
            f["readiness"] = (None, "ลูกค้ายังไม่แน่ใจเรื่องเวลา")
        else:
            from . import plan_request as PR
            f["readiness"] = (pts, " · ".join(x for x in (PR.WINDOWS.get(w, "") if w else "", PR.READINESS.get(r, "") if r else "",
                                                          PR.BUDGETS.get(b, "") if b else "") if x) or "ข้อมูลไม่พอ")
    else:
        f["readiness"] = (None, "ยังไม่รู้เวลา/คนดูแลเว็บ/งบ")
    try:
        vm = RV.build(lead, None, None)
        acts = [a for a in vm["actions"] if a.get("confidence") in ("high", "medium")]
        f["problem"] = ((1, f"ตรวจเจอ {len(acts)} ข้อพร้อมหลักฐาน") if acts else
                        ((0, "ตรวจแล้วไม่พบข้อที่ต้องแก้ด่วน") if vm["tested"] else (None, "ยังตรวจเว็บไม่สำเร็จ")))
    except Exception:
        f["problem"] = (None, "ยังไม่มีผลตรวจ")
    w = weights()
    known = {k: v for k, v in f.items() if v[0] is not None}
    score = None
    if len(known) >= 2:
        tot = sum(w[k] for k in known)
        score = round(100 * sum(w[k] * (v[0] + 1) / 2 for k, v in known.items()) / tot) if tot else None
    return {"version": QUAL_VERSION, "score": score, "known": len(known), "total": len(f), "weights": w,
            "factors": [{"key": k, "label": FACTOR_LABEL[k], "points": v[0], "reason": v[1], "weight": w[k]} for k, v in f.items()],
            "note": "น้ำหนักเป็นสมมติฐาน ปรับได้ · ปัจจัยที่ยังไม่รู้ไม่นับเป็นศูนย์"}


# ---------- ตอบกลับไม่อยู่ (OOO) ----------
OOO_RE = re.compile(r"(out of office|ไม่อยู่ที่สำนักงาน|ลาพักร้อน|on leave|vacation)", re.I)
_UNTIL = re.compile(r"(?:until|จนถึง|ถึงวันที่|กลับมา(?:วันที่)?)\s*(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?", re.I)
NOT_NOW_RE = re.compile(r"(ไม่ใช่ตอนนี้|ยังไม่พร้อม|ไว้ก่อน|ปีหน้า|not now|not right now|maybe later|next year)", re.I)


def ooo_until(text: str, now: dt.datetime | None = None) -> dt.datetime:
    """วันที่กลับมาจากข้อความ (วัน/เดือน[/ปี]) + 1 วัน · หาไม่เจอ/ผิด = 7 วัน · ไกลเกิน 60 วัน = 60 วัน"""
    now = now or dt.datetime.now()
    m = _UNTIL.search(text or "")
    t = None
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        yr = int(y) + (2000 if y and len(y) == 2 else 0) if y else now.year
        if yr > 2400:
            yr -= 543                                            # พ.ศ.
        try:
            t = dt.datetime(yr, mo, d) + dt.timedelta(days=1)
            if t < now - dt.timedelta(days=1) and not y:
                t = t.replace(year=t.year + 1)
        except ValueError:
            t = None
    if not t or t < now:
        t = now + dt.timedelta(days=7)
    return min(t, now + dt.timedelta(days=60))


def followup_paused_until(lead_id: int) -> str | None:
    """เวลาที่ติดตามผลถูกพักไว้ (จากอีเมลไม่อยู่ล่าสุด) · None = ไม่ได้พัก"""
    for e in db.list_lead_events(lead_id, 200):
        if e["kind"] == "ooo":
            m = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", e["note"] or "")
            return m.group(0) if m else None
    return None
