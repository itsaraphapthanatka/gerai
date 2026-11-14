"""หน้าเว็บสาธารณะชุดใหม่ (stage 01+) — แยกจาก main.py · ทุกหน้าอยู่หลัง flag marketing_v2 (ค่าเริ่มต้นปิด)
flag ปิด + ไม่ใช่แอดมินที่ preview → 404 (เหมือนไม่มีหน้า) · preview ใส่ noindex ทั้ง header และ meta"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from . import flags

router = APIRouter()
NOINDEX = {"X-Robots-Tag": "noindex, nofollow"}


def _render(request: Request, name: str, ctx: dict | None = None, *, internal: bool = False):
    from .main import templates                         # อ่านตอนเรียก กัน import วน (แบบเดียวกับ crm_web)
    preview = internal or not flags.enabled("marketing_v2")
    resp = templates.TemplateResponse(request, name, {"preview": preview, **(ctx or {})})
    if preview:
        resp.headers.update(NOINDEX)
    return resp


@router.get("/preview/ui-kit")
def ui_kit(request: Request):
    """สมุดชิ้นส่วน — หน้าภายในเสมอ (noindex) ให้แอดมินตรวจหน้าตาก่อนเปิดใช้จริง"""
    if not flags.previewing("marketing_v2", request):
        raise HTTPException(status_code=404)
    return _render(request, "mk/ui_kit.html", internal=True)
