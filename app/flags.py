"""Feature flags กลาง — ทุกการเปลี่ยนที่เสี่ยงต้องอยู่หลัง flag ที่นี่ ค่าเริ่มต้น OFF (OFF = พฤติกรรมเดิม)

ลำดับการอ่าน:
1. env `GEO_FLAG_<NAME>` (เช่น GEO_FLAG_MARKETING_V2=1) — สำหรับ local/test/staging
2. settings ใน DB key `flag_<name>` ("1" = เปิด) — เจ้าของเปิด/ปิดได้โดยไม่ deploy
3. ไม่มีทั้งคู่ = ปิด

ดูตัวอย่างก่อนเปิดจริง (preview): แอดมินที่ล็อกอินอยู่ + คุกกี้ `jr_preview` มีชื่อ flag → เห็นของใหม่คนเดียว
หน้าที่แสดงจาก preview ต้องใส่ noindex (ใช้ `previewing()` ตัดสินใจ) · flag ไม่ใช่การให้สิทธิ์ ต้องตรวจสิทธิ์แยกเสมอ
"""
from __future__ import annotations

import os

FLAGS: dict[str, str] = {
    "marketing_v2": "หน้าเว็บสาธารณะชุดใหม่ (design system, หน้าแรก, หน้าบริการ) — stage 01–03",
    "snapshot_v2": "ตรวจเว็บแบบเร็วจำกัดหน้า + สถานะจริงทีละขั้น — stage 04A",
    "report_v2": "รายงาน 4 ช่องตัวชี้วัดแยกกัน — stage 05",
    "context_v2": "ลูกค้ายืนยัน/แก้ข้อมูลธุรกิจในรายงาน — stage 04B",
    "plan_request_v2": "ฟอร์มขอแผน + ความยินยอม + sales brief — stage 06",
    "line_appointments": "นัดหมายผ่าน LINE — stage 06A",
    "durable_jobs": "คิวงานถาวร + งบ + การอนุมัติ — stage 10 · เริ่มจากการทดสอบ AI ของรายงาน · ปิด = thread แบบเดิม",
    "customer360": "หน้า Today + Customer 360 + รายงานทดลองจับคู่ตัวตน ในหลังบ้าน — stage 09 · ปิด = 404 ไม่มีลิงก์",
    "sales_v2": "ขาย/ติดตาม: ส่งเฉพาะช่องทางที่ได้รับอนุญาต (ไม่ไล่ข้ามช่องทาง) · พักติดตามเมื่อไม่อยู่ · ขั้นโอกาสขาย · คะแนนคัดกรอง — stage 11",
    "agent_policy": "เอเจนต์ AI ทำงานใต้นโยบายในโค้ด: ระดับอำนาจ A–D · ติดต่อคนนอก/เผยแพร่ต้องอนุมัติตรงงานหรือกฎประจำที่เจ้าของเปิด · กันวนซ้ำ/ข้ามบัญชี · บันทึกทุกครั้ง — stage 14 · ปิด = แบบเดิม",
    "delivery_v2": "ส่งมอบตามขอบเขต + พื้นที่งานลูกค้า: จ่ายแล้วยังไม่เริ่มงานอัตโนมัติจนมีข้อมูล สิทธิ์ วัดผลตั้งต้น และแผนที่อนุมัติ — stage 15 · ปิด = แบบเดิม · ลูกค้าเดิมไม่กระทบ",
    "publish_v2": "เผยแพร่ผ่านประตูตรวจ + อนุมัติผูกเวอร์ชัน + ยืนยันจากปลายทาง + คืนเวอร์ชัน · ปิด auto-publish · รายงานผลรายเดือนแยกหมวด · ขยายบัญชีต้องมีเหตุผลที่เหมาะ — stage 16 · ปิด = แบบเดิม",
    "revenue_v2": "รายได้จากเอกสารบัญชีจริง (ครั้งเดียว/รายรอบ/MRR/คืนเงิน) · ที่มาหลายมุม · ต้นทุนแยกหาลูกค้า/ส่งมอบ — stage 17 · ปิด = ไม่มีหน้า ไม่บันทึกเพิ่ม",
    "analytics": "เก็บเหตุการณ์ funnel ภายใน (ไม่มี tracker ภายนอก ไม่มีคุกกี้) — stage 08B · ปิด = ไม่เก็บอะไรเลย",
}
PREVIEW_COOKIE = "jr_preview"
_ON = ("1", "true", "yes", "on")


def _check(name: str) -> None:
    if name not in FLAGS:
        raise KeyError(f"ไม่รู้จัก flag: {name} (เพิ่มใน app/flags.py FLAGS ก่อน)")


def enabled(name: str) -> bool:
    """เปิดให้ทุกคนหรือยัง"""
    _check(name)
    env = os.getenv(f"GEO_FLAG_{name.upper()}")
    if env is not None:
        return env.strip().lower() in _ON
    try:
        from . import db
        return (db.get_setting(f"flag_{name}") or "").strip().lower() in _ON
    except Exception:
        return False                                   # อ่าน DB ไม่ได้ = ปิด (ปลอดภัยกว่า)


def previewing(name: str, request) -> bool:
    """แอดมินกำลังดูตัวอย่าง flag นี้ (flag ยังปิดสำหรับคนอื่น)"""
    _check(name)
    if request is None:
        return False
    try:
        if not request.session.get("is_admin"):
            return False
    except (AssertionError, AttributeError):
        return False
    wanted = (request.cookies.get(PREVIEW_COOKIE) or "").split(",")
    return name in [w.strip() for w in wanted]


def active(name: str, request=None) -> bool:
    """ใช้ของใหม่กับ request นี้ไหม = เปิดจริง หรือแอดมินกำลัง preview"""
    return enabled(name) or previewing(name, request)


def set_enabled(name: str, on: bool) -> None:
    _check(name)
    from . import db
    db.set_setting(f"flag_{name}", "1" if on else "0")


def snapshot() -> dict[str, bool]:
    return {n: enabled(n) for n in FLAGS}
