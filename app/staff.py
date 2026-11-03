"""ทีมงานหลังบ้าน + แยกสิทธิ์: บัญชี is_admin=1 ทุกบัญชีคือทีมงาน · สิทธิ์แยกตามหมวดงาน (ดู/แก้ไข)

- staff_role ว่าง (แอดมินเดิมก่อนมีระบบนี้) = เจ้าของ ทำได้ทุกอย่าง
- ตรวจสิทธิ์ที่จุดเดียว: main._staff_guard (dependency ระดับแอป) จับคู่ URL /admin/... กับหมวดใน RULES
  GET/HEAD = ต้องมีสิทธิ์ "ดู" · เมธอดอื่น = ต้องมีสิทธิ์ "แก้ไข" · URL ที่ไม่เข้าหมวดไหนเลย = เจ้าของเท่านั้น (ปิดไว้ก่อน)
- ไฟล์นี้ไม่แตะ DB — ฟังก์ชันรับแถว tenant (sqlite Row / dict) เข้ามา"""
import json
import re

# หมวดงาน: key → (ชื่อ, คำอธิบาย) — ลำดับนี้ใช้แสดงในหน้า /admin/staff
AREAS = {
    "sales":     ("ขายและดีล", "ดีล ลูกค้าเป้าหมาย ผู้ติดต่อ โทรปิดดีล อีเมลตอบกลับ แชตขาย ส่ง/ยกเลิกใบเสนอราคา"),
    "customers": ("ลูกค้าและผลงาน", "หน้าลูกค้า & MRR ผลลัพธ์ลูกค้า และเข้าแบรนด์ของลูกค้าทุกราย (แก้ไข = สั่งรัน/เขียนคอนเทนต์ให้)"),
    "marketing": ("การตลาด", "การตลาดของเราเอง แคมเปญอีเมล การส่งอีเมล"),
    "finance":   ("บัญชีและการเงิน", "บัญชี ยืนยันการชำระเงิน สลิป ยืนยันใบเสนอราคาที่จ่ายแล้ว"),
    "agents":    ("เอเจนต์ AI", "การตัดสินใจ ออฟฟิศ สั่งงาน CEO งานที่แจก ทักษะเอเจนต์"),
    "partners":  ("พันธมิตร", "โปรแกรมพันธมิตร"),
    "accounts":  ("บัญชีผู้ใช้ลูกค้า", "สร้างบัญชีลูกค้า เปลี่ยนแผน ต่อทดลองใช้ แก้อีเมล"),
    "settings":  ("ตั้งค่าระบบและคีย์", "ตั้งค่า คีย์ API SMTP LINE Stripe โหมดเอเจนซี แผน Free"),
    "staff":     ("ทีมงานและสิทธิ์", "เพิ่ม/ปิดบัญชีทีมงาน กำหนดสิทธิ์ ดูประวัติการทำงาน"),
}
LEVELS = {"none": 0, "view": 1, "edit": 2}
LEVEL_LABEL = {"none": "ไม่เห็น", "view": "ดูได้", "edit": "แก้ไขได้"}

# บทบาทสำเร็จรูป — เลือกแล้วค่อยปรับรายหมวดได้ (บันทึกเป็น custom ถ้าไม่ตรงชุด)
_ALL_EDIT = {a: "edit" for a in AREAS}
ROLES = {
    "owner":     ("เจ้าของ", _ALL_EDIT),
    "manager":   ("ผู้จัดการ", {**_ALL_EDIT, "settings": "view", "staff": "none"}),
    "sales":     ("ฝ่ายขาย", {"sales": "edit", "customers": "view", "partners": "view"}),
    "marketing": ("ฝ่ายการตลาด/คอนเทนต์", {"marketing": "edit", "customers": "edit", "agents": "view"}),
    "finance":   ("ฝ่ายบัญชี", {"finance": "edit", "customers": "view", "accounts": "view"}),
    "viewer":    ("ดูอย่างเดียว", {**{a: "view" for a in AREAS}, "settings": "none", "staff": "none"}),
}
CUSTOM = "custom"

# URL → หมวด (ตัวแรกที่ตรงชนะ — ตัวเจาะจงอยู่บน) · None = หน้าแรก /admin ที่ทีมงานทุกคนเข้าได้ (ส่วนย่อยซ่อนตามสิทธิ์)
RULES = [
    (r"/admin/?", None),
    (r"/admin/staff(/.*)?", "staff"),
    (r"/admin/(settings|free-plan|agency-mode)(/.*)?", "settings"),
    (r"/admin/(email/smtp|inbox/settings|salesbot/settings|marketing/settings|marketing/fb-test|results/settings)", "settings"),
    (r"/admin/deliverability/.+", "settings"),                      # เพิ่มกล่องส่ง/รหัส SMTP/DKIM — หน้าดูอยู่หมวดการตลาด
    (r"/admin/(payments|slip|accounting|acct)(/.*|\.csv)?", "finance"),
    (r"/admin/quotes/[^/]+/(confirm|slip)", "finance"),
    (r"/admin/(deals|contacts|leads|calls|inbox|salesbot|prospect|quotes)(/.*)?", "sales"),
    (r"/admin/(customers|results)(/.*|\.csv)?", "customers"),
    (r"/admin/(marketing|email|deliverability)(/.*)?", "marketing"),
    (r"/admin/(decisions|office|ceo|tasks|skills|agents|digest)(/.*)?", "agents"),
    (r"/admin/partners(/.*)?", "partners"),
    (r"/admin/tenants(/.*)?", "accounts"),
]
_RULES = [(re.compile(p + r"\Z"), a) for p, a in RULES]
OWNER_ONLY = "staff"           # URL ที่ไม่เข้ากฎข้อไหน = ต้องมีสิทธิ์หมวดนี้ (เจ้าของ)

def area_for(path: str):
    """หมวดของ URL หลังบ้าน · "" = ไม่ใช่ URL หลังบ้าน · None = หน้าแรก /admin"""
    if not path.startswith("/admin"):
        return ""
    for rx, area in _RULES:
        if rx.match(path):
            return area
    return OWNER_ONLY


def _get(t, k):
    try:
        return t[k] if k in t.keys() else None
    except Exception:
        return None


def is_staff(t) -> bool:
    return bool(t and _get(t, "is_admin") and not _get(t, "disabled"))


def role_key(t) -> str:
    r = _get(t, "staff_role")
    return r if r in ROLES or r == CUSTOM else "owner"


def role_label(t) -> str:
    r = role_key(t)
    return "กำหนดเอง" if r == CUSTOM else ROLES[r][0]


def perms(t) -> dict:
    """{หมวด: none|view|edit} ครบทุกหมวด"""
    if not is_staff(t):
        return {a: "none" for a in AREAS}
    r = role_key(t)
    if r == CUSTOM:
        try:
            raw = json.loads(_get(t, "staff_perms") or "{}")
        except ValueError:
            raw = {}
    else:
        raw = ROLES[r][1]
    return {a: (raw.get(a) if raw.get(a) in LEVELS else "none") for a in AREAS}


def can(t, area, level: str = "view") -> bool:
    if not is_staff(t):
        return False
    if not area:                   # None = หน้าแรก /admin · "" = ไม่ใช่หน้าหลังบ้าน
        return True
    return LEVELS[perms(t).get(area, "none")] >= LEVELS[level]


def allowed(t, path: str, method: str) -> bool:
    return can(t, area_for(path), "view" if method in ("GET", "HEAD") else "edit")


def is_owner(t) -> bool:
    return can(t, "staff", "edit")


def receives(t, link) -> bool:
    """แจ้งเตือน/อีเมลถึงทีมงาน: ส่งเฉพาะคนที่เปิดลิงก์นั้นได้ (ไม่มีลิงก์หลังบ้าน = ทุกคน)"""
    if not is_staff(t):
        return False
    path = (link or "").split("?")[0].split("#")[0]
    if "://" in path:
        path = "/" + path.split("://", 1)[1].partition("/")[2]
    return can(t, area_for(path), "view")


def normalize(role: str, levels: dict) -> tuple[str, str | None]:
    """จากฟอร์ม → (staff_role, staff_perms) · ตรงกับบทบาทสำเร็จรูป = เก็บชื่อบทบาทอย่างเดียว"""
    if not levels and role in ROLES:
        return role, None
    clean = {a: (levels.get(a) if levels.get(a) in LEVELS else "none") for a in AREAS}
    for k, (_, preset) in ROLES.items():
        if clean == {a: preset.get(a, "none") for a in AREAS}:
            return k, None
    return CUSTOM, json.dumps({a: v for a, v in clean.items() if v != "none"})


def role_presets() -> dict:
    """สำหรับ JS ในหน้า /admin/staff: เลือกบทบาท → เติมระดับรายหมวด"""
    return {k: {a: p.get(a, "none") for a in AREAS} for k, (_, p) in ROLES.items()}
