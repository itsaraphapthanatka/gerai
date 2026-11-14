"""ขอแผนจากรายงาน (stage 06-core, flag plan_request_v2) — ฟอร์มสั้น · บันทึกจริงก่อนบอก "ได้รับแล้ว" · ขอให้โทรกลับ (ไม่มีปฏิทินปลอม)
ความยินยอมแยกตามวัตถุประสงค์ (DATA_CONTRACTS §5): fulfil_request บังคับ · marketing ไม่บังคับ ไม่ติ๊กไว้ก่อน
ข้อความความยินยอมยังเป็นร่าง (NOTICE_VERSION ลงท้าย -draft) — ต้องให้คนตรวจด้านกฎหมายก่อนเปิด flag จริง
Sales Brief แยก ลูกค้ายืนยัน / ระบบเดา / ยังไม่ทราบ — ไม่เติมข้อมูลที่ไม่มี"""
from __future__ import annotations

import json
import re
import uuid

from . import db, leads as L

NOTICE_VERSION = "2026-11-v1-draft"
CHANNELS = {"line": "LINE", "phone": "โทรศัพท์", "email": "อีเมล"}
WINDOWS = {"": "ยังไม่แน่ใจ", "asap": "เร็วที่สุด", "1m": "ภายใน 1 เดือน", "3m": "ภายใน 3 เดือน", "explore": "แค่ศึกษาข้อมูล"}
READINESS = {"": "ยังไม่แน่ใจ", "own_team": "มีทีมเว็บของตัวเอง", "agency": "ใช้เอเจนซีอยู่", "none": "ยังไม่มีคนดูแลเว็บ"}
BUDGETS = {"": "ยังไม่แน่ใจ", "lt10k": "ต่ำกว่า 10,000 บาท/เดือน", "10-30k": "10,000–30,000 บาท/เดือน", "gt30k": "มากกว่า 30,000 บาท/เดือน"}
CONSENT_TEXT = {
    "fulfil_request": "ยินยอมให้เจอ.AI ติดต่อกลับทางช่องทางข้างบนเพื่อตอบคำขอนี้ และเก็บข้อมูลในฟอร์มไว้เพื่อการนี้",
    "marketing": "รับข่าวสารและบทความจากเจอ.AI ทางช่องทางข้างบน (ไม่บังคับ ยกเลิกได้ทุกเมื่อ)",
}
ALERT_GAP_MIN = 10                                    # คำขอซ้ำจากรายงานเดิมภายใน 10 นาที = ไม่แจ้งทีมซ้ำ

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE = re.compile(r"^\+?[0-9][0-9 \-]{7,15}$")
_LINE = re.compile(r"^@?[A-Za-z0-9._\-]{2,40}$")


def new_key() -> str:
    return uuid.uuid4().hex


def validate(form: dict) -> tuple[dict, dict]:
    """คืน (ค่าที่สะอาด, ข้อผิดพลาดต่อช่อง) · ข้อความผิดพลาดบอกวิธีแก้"""
    v = {k: (form.get(k) or "").strip() for k in ("name", "role", "contact_channel", "contact_value", "desired_window",
                                                  "web_readiness", "budget", "message", "idem_key")}
    err = {}
    v["name"], v["role"], v["message"] = v["name"][:120], v["role"][:80], v["message"][:1000]
    if not v["name"]:
        err["name"] = "กรอกชื่อที่ให้ทีมเรียก"
    if v["contact_channel"] not in CHANNELS:
        err["contact_channel"] = "เลือกช่องทางที่สะดวกให้ติดต่อกลับ"
    cv = v["contact_value"][:160]
    if v["contact_channel"] == "email":
        cv = cv.lower()
        if not _EMAIL.match(cv):
            err["contact_value"] = "อีเมลดูไม่ถูกต้อง เช่น name@company.co.th"
    elif v["contact_channel"] == "phone":
        if not _PHONE.match(cv):
            err["contact_value"] = "เบอร์โทรควรเป็นตัวเลข 9–10 หลัก เช่น 081 234 5678"
    elif v["contact_channel"] == "line":
        if not _LINE.match(cv):
            err["contact_value"] = "LINE ID ใช้ตัวอักษรอังกฤษ ตัวเลข . _ - เช่น @company หรือ somchai.k"
    if not cv and "contact_value" not in err:
        err["contact_value"] = "กรอกช่องทางติดต่อ"
    v["contact_value"] = cv
    for k, allowed in (("desired_window", WINDOWS), ("web_readiness", READINESS), ("budget", BUDGETS)):
        if v[k] not in allowed:
            v[k] = ""
    if not form.get("consent_fulfil"):
        err["consent_fulfil"] = "ติ๊กยินยอมให้ติดต่อกลับก่อน เราถึงจะตอบคำขอนี้ได้"
    if not re.fullmatch(r"[0-9a-f]{32}", v["idem_key"]):
        v["idem_key"] = new_key()                     # ฟอร์มเก่า/ถูกแก้ → ใช้ key ใหม่ (ยังกันส่งซ้ำด้วยการแจ้งเตือนห่าง 10 นาที)
    v["marketing"] = bool(form.get("consent_marketing"))
    v["consent_fulfil"] = bool(form.get("consent_fulfil"))
    return v, err


def submit(lead, v: dict, ip: str = "") -> tuple[int, bool]:
    """บันทึกคำขอ + ความยินยอม + ไทม์ไลน์ลีด · คืน (id, ใหม่ไหม) · บันทึกไม่สำเร็จ = exception (หน้าไม่บอกว่าสำเร็จ)"""
    at = db.now()
    consents = [{"purpose": "fulfil_request", "granted": True, "notice_version": NOTICE_VERSION, "source": "plan_form", "at": at},
                {"purpose": "marketing", "granted": v["marketing"], "notice_version": NOTICE_VERSION, "source": "plan_form", "at": at}]
    pid, created = db.add_plan_request(lead["id"], v["idem_key"], name=v["name"], role=v["role"] or None,
                                       contact_channel=v["contact_channel"], contact_value=v["contact_value"],
                                       desired_window=v["desired_window"] or None, web_readiness=v["web_readiness"] or None,
                                       budget=v["budget"] or None, message=v["message"] or None,
                                       consents_json=json.dumps(consents, ensure_ascii=False), ip=ip[:64] or None)
    if not created:
        return pid, False
    fill = {"consent": 1}                             # consent ของลีด = ยินยอมให้ติดต่อเรื่องคำขอนี้ (ไม่ใช่การตลาด)
    if not lead["name"]:
        fill["name"] = v["name"]
    if v["contact_channel"] == "email" and not lead["email"]:
        fill.update(email=v["contact_value"], email_src="form")
    elif v["contact_channel"] != "email" and not lead["contact"]:
        fill["contact"] = f"{CHANNELS[v['contact_channel']]}: {v['contact_value']}"[:160]
    db.update_lead(lead["id"], **fill)
    note = f"ขอแผนจากรายงาน: {v['name']}" + (f" ({v['role']})" if v["role"] else "") + f" · ติดต่อทาง {CHANNELS[v['contact_channel']]}"
    if v["desired_window"]:
        note += f" · {WINDOWS[v['desired_window']]}"
    db.add_lead_event(lead["id"], "wants_call", note, "customer")       # ใช้ขั้น "อยากคุย" เดิม: ขึ้นบนสุดของรายการโทร
    return pid, True


def recent_alerted(lead_id: int, pid: int) -> bool:
    import datetime as dt
    since = (dt.datetime.now() - dt.timedelta(minutes=ALERT_GAP_MIN)).isoformat(timespec="seconds")
    return any(r["id"] != pid and (r["created_at"] or "") >= since for r in db.list_plan_requests(lead_id, 5))


def latest(lead_id: int) -> dict | None:
    rows = db.list_plan_requests(lead_id, 1)
    return dict(rows[0]) if rows else None


def brief(lead, report_vm: dict | None = None) -> dict | None:
    """สรุปให้ทีมขายก่อนโทร · confirmed = ลูกค้ากรอกเอง · inferred = ระบบเดาจากเว็บ · unknown = ยังไม่มีข้อมูล"""
    pr = latest(lead["id"])
    if not pr:
        return None
    try:
        consents = json.loads(pr["consents_json"] or "[]")
    except ValueError:
        consents = []
    mk = next((c for c in consents if c.get("purpose") == "marketing"), {})
    confirmed = [("ชื่อ", pr["name"]), ("ตำแหน่ง", pr["role"]),
                 ("ติดต่อทาง", f"{CHANNELS.get(pr['contact_channel'], pr['contact_channel'])}: {pr['contact_value']}"),
                 ("ช่วงเวลาที่อยากเริ่ม", WINDOWS.get(pr["desired_window"] or "") if pr["desired_window"] else None),
                 ("คนดูแลเว็บ", READINESS.get(pr["web_readiness"] or "") if pr["web_readiness"] else None),
                 ("งบประมาณ", BUDGETS.get(pr["budget"] or "") if pr["budget"] else None),
                 ("ข้อความ", pr["message"])]
    vm = report_vm or {}
    inferred = [("ชื่อแบรนด์", vm.get("brand")), ("คำค้นหลัก", ", ".join(vm.get("keywords") or []) or None),
                ("คู่แข่งบน Google", ", ".join(vm.get("competitors") or []) or None)]
    unknown = [k for k, val in confirmed + inferred if not val]
    return {"request": pr, "confirmed": [(k, val) for k, val in confirmed if val], "inferred": [(k, val) for k, val in inferred if val],
            "unknown": unknown, "marketing": bool(mk.get("granted")), "notice_version": NOTICE_VERSION,
            "findings": (vm.get("actions") or [])[:3], "lanes": vm.get("lanes") or [], "next": "โทรหรือทักกลับภายใน 1 วันทำการ ทางช่องทางที่ลูกค้าเลือก"}
