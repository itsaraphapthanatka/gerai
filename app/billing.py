"""Billing/quota ต่อ tenant — แผน + นับการใช้ + เช็คโควตา.

ใช้ db helpers ล้วน → ทำงานทั้ง SQLite และ Postgres
โควตานับ "ต่อเดือนปฏิทิน" จาก started_at/created_at (ISO string, LIKE 'YYYY-MM%')
"""
from __future__ import annotations
from . import db

PLANS = {
    "free":   {"label": "Free",   "brands": 1,   "runs_month": 4,    "content_month": 5,    "price": 0},
    "pro":    {"label": "Pro",    "brands": 10,  "runs_month": 100,  "content_month": 100,  "price": 12900},
    "agency": {"label": "Agency", "brands": 100, "runs_month": 1000, "content_month": 1000, "price": 39000},
}

_FIELD = {"brands": "brands", "runs": "runs_month", "content": "content_month"}
_NAME_TH = {"brands": "จำนวนแบรนด์", "runs": "การมอนิเตอร์เดือนนี้", "content": "การสร้างคอนเทนต์เดือนนี้"}


def plan_key(tenant) -> str:
    k = tenant["plan"] if (tenant and tenant["plan"]) else "free"
    return k if k in PLANS else "free"


def plan_of(tenant) -> dict:
    return PLANS[plan_key(tenant)]


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
