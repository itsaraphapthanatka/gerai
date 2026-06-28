"""Billing/quota ต่อ tenant — แผน + นับการใช้ + เช็คโควตา.

ใช้ db helpers ล้วน → ทำงานทั้ง SQLite และ Postgres
โควตานับ "ต่อเดือนปฏิทิน" จาก started_at/created_at (ISO string, LIKE 'YYYY-MM%')
"""
from __future__ import annotations
from . import db

# ปรับราคา/โควตาให้คุ้มต้นทุน AI (ทุก content/run/image = ค่า API จริง) — ตัวเลขแก้ได้ตามต้นทุนจริง
PLANS = {
    "free": {
        "label": "Free", "price": 0, "period": "", "tagline": "ทดลองใช้",
        "brands": 1, "runs_month": 4, "content_month": 3,
        "images": False, "auto_publish": False,
        "perks": ["1 แบรนด์", "มอนิเตอร์ 4 ครั้ง/เดือน", "สร้างคอนเทนต์ 3 ชิ้น/เดือน", "Schema + llms.txt", "ติดตั้งได้ทุกเว็บ"],
    },
    "pro": {
        "label": "Pro", "price": 9900, "period": "/เดือน", "tagline": "ธุรกิจเดียว ครบทุกฟีเจอร์",
        "brands": 5, "runs_month": 30, "content_month": 30,
        "images": True, "auto_publish": True,
        "perks": ["5 แบรนด์", "มอนิเตอร์ 30 ครั้ง/เดือน", "สร้างคอนเทนต์ 30 ชิ้น/เดือน",
                  "🖼️ AI แนบรูปประกอบ", "🚀 auto สร้าง+เผยแพร่", "ตั้งเวลามอนิเตอร์เอง"],
    },
    "agency": {
        "label": "Agency", "price": 29000, "period": "/เดือน", "tagline": "เอเจนซี / หลายแบรนด์",
        "brands": 30, "runs_month": 150, "content_month": 150,
        "images": True, "auto_publish": True,
        "perks": ["30 แบรนด์", "มอนิเตอร์ 150 ครั้ง/เดือน", "สร้างคอนเทนต์ 150 ชิ้น/เดือน",
                  "ทุกฟีเจอร์ของ Pro", "เหมาะกับดูแลหลายลูกค้า", "ซัพพอร์ตลำดับต้น"],
    },
    # ทเทียร์ Enterprise — ไม่กดซื้อเอง (ติดต่อทีมงาน) admin เป็นคนเปิดให้
    "unlimited": {
        "label": "ไม่จำกัด", "price": 0, "period": "", "tagline": "องค์กร / เอเจนซีใหญ่", "hidden": True,
        "brands": 999999, "runs_month": 999999, "content_month": 999999,
        "images": True, "auto_publish": True,
        "perks": ["แบรนด์ไม่จำกัด", "มอนิเตอร์ไม่จำกัด", "คอนเทนต์ไม่จำกัด", "ทุกฟีเจอร์ AI", "ซัพพอร์ตเฉพาะทาง"],
    },
}

# แพ็กเกจเสริม (ขายแยก/ครั้งเดียว — admin จัดการให้หลังลูกค้าขอ)
ADDONS = [
    {"key": "setup", "label": "GEO Setup — เสริมบล็อกเก่า", "price": 9900, "period": "ครั้งเดียว",
     "desc": "เพิ่ม Schema + ใส่ llms.txt ให้คอนเทนต์เดิมทั้งเว็บ — ไม่ต้องเขียนใหม่ ของเก่าได้ GEO ทันที"},
    {"key": "content20", "label": "คอนเทนต์เพิ่ม 20 ชิ้น", "price": 3900, "period": "ครั้งเดียว",
     "desc": "เติมโควตาสร้างคอนเทนต์ + รูปประกอบ"},
    {"key": "brand", "label": "แบรนด์เพิ่ม", "price": 1500, "period": "/แบรนด์/เดือน",
     "desc": "เพิ่มจำนวนแบรนด์ที่ดูแลเกินแผน"},
]

_FIELD = {"brands": "brands", "runs": "runs_month", "content": "content_month"}
_NAME_TH = {"brands": "จำนวนแบรนด์", "runs": "การมอนิเตอร์เดือนนี้", "content": "การสร้างคอนเทนต์เดือนนี้"}


def plan_key(tenant) -> str:
    k = tenant["plan"] if (tenant and tenant["plan"]) else "free"
    return k if k in PLANS else "free"


def plan_of(tenant) -> dict:
    return PLANS[plan_key(tenant)]


def feature(tenant, key: str) -> bool:
    """ฟีเจอร์ที่ต้องมีในแผน เช่น 'images', 'auto_publish' — admin ใช้ได้หมด"""
    if tenant and tenant["is_admin"]:
        return True
    return bool(plan_of(tenant).get(key))


def usage(tenant_id: int) -> dict:
    ym = db.now()[:7]  # YYYY-MM
    return {
        "brands": db.count_brands(tenant_id),
        "runs": db.count_runs_month(tenant_id, ym),
        "content": db.count_content_month(tenant_id, ym),
    }


def check(tenant, kind: str):
    """คืน (ok, ข้อความ) สำหรับ kind = 'brands' | 'runs' | 'content'. admin ไม่จำกัดโควตา."""
    if tenant and tenant["is_admin"]:
        return True, ""
    plan = plan_of(tenant)
    used = usage(tenant["id"])[kind]
    cap = plan[_FIELD[kind]]
    if used >= cap:
        return False, f"เกินโควตาแผน {plan['label']} — {_NAME_TH[kind]} {used}/{cap}. อัปเกรดแผนเพื่อใช้เพิ่ม"
    return True, ""
