"""หน้าแอดมิน ทีมงานและสิทธิ์ /admin/staff: เพิ่มบัญชีทีมงาน · บทบาท + สิทธิ์รายหมวด · ปิด/เปิดบัญชี · ตั้งรหัสใหม่ · ประวัติการทำงาน
สิทธิ์เข้าหน้านี้ = หมวด "staff" (main._staff_guard ตรวจก่อนถึง route) · กฎสิทธิ์ทั้งหมดอยู่ใน app/staff.py"""
from __future__ import annotations
import re
from urllib.parse import quote as _q

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from . import db, staff, staff_audit

router = APIRouter()
MIN_PW = 8


def _m():
    from . import main
    return main


def _back(notice: str = "", error: str = "") -> RedirectResponse:
    qs = ("?notice=" + _q(notice)) if notice else (("?error=" + _q(error)) if error else "")
    return RedirectResponse("/admin/staff" + qs, status_code=303)


def _owners_left(excluding: int) -> int:
    """เจ้าของที่ยังใช้งานได้ ถ้าไม่นับบัญชี excluding — กันล็อกตัวเองออกจนไม่เหลือใครจัดการทีมได้"""
    return sum(1 for t in db.list_staff() if t["id"] != excluding and staff.is_owner(t))


@router.get("/admin/staff")
def staff_page(request: Request):
    if not request.session.get("is_admin"):
        return RedirectResponse("/login", status_code=303)
    rows = db.list_staff()
    team = [{"t": t, "role": staff.role_key(t), "role_label": staff.role_label(t), "perms": staff.perms(t),
             "me": t["id"] == request.session.get("tenant_id")} for t in rows]
    return _m().templates.TemplateResponse(request, "admin_staff.html", {
        "team": team, "areas": staff.AREAS, "roles": staff.ROLES, "levels": staff.LEVEL_LABEL,
        "presets": staff.role_presets(), "log": [staff_audit.view(r) for r in db.list_staff_log(15)],
        "notice": request.query_params.get("notice"), "error": request.query_params.get("error"), "min_pw": MIN_PW})


@router.get("/admin/staff/history")
def staff_history(request: Request, who: int = 0, target: str = "", status: str = "", q: str = "", before: int = 0):
    """ประวัติการแก้ไขทั้งหมด · กรองตามคน / รายการ (ตาราง:id) / ผล / คำค้น · ทีละ 100 แถว"""
    if not request.session.get("is_admin"):
        return RedirectResponse("/login", status_code=303)
    per = 100
    rows = [staff_audit.view(r) for r in db.list_staff_log(per + 1, tenant_id=who or None, target=target or None,
                                                             status=status or None, q_text=q.strip() or None, before_id=before or None)]
    more = len(rows) > per
    rows = rows[:per]
    f = {"who": who, "target": target, "status": status, "q": q, "any": bool(who or target or status or q),
         "target_label": next((r["target_label"] for r in rows if r.get("target_label")), None)}
    nxt = ""
    if more:
        from urllib.parse import urlencode
        nxt = "/admin/staff/history?" + urlencode({k: v for k, v in (("who", who or ""), ("target", target), ("status", status), ("q", q),
                                                                     ("before", rows[-1]["id"])) if v})
    return _m().templates.TemplateResponse(request, "admin_staff_history.html", {
        "rows": rows, "team": db.list_staff(), "f": f, "next_before": more, "next_url": nxt})


@router.post("/admin/staff")
def staff_create(request: Request, email: str = Form(...), name: str = Form(""), password: str = Form(...), role: str = Form("viewer")):
    if not request.session.get("is_admin"):
        return RedirectResponse("/login", status_code=303)
    e = (email or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e):
        return _back(error="รูปแบบอีเมลไม่ถูกต้อง")
    if len(password) < MIN_PW:
        return _back(error=f"รหัสผ่านเริ่มต้นต้องยาวอย่างน้อย {MIN_PW} ตัว")
    if role not in staff.ROLES:
        return _back(error="ไม่รู้จักบทบาทนี้")
    if db.get_tenant_by_email(e):
        return _back(error=f"อีเมล {e} มีบัญชีอยู่แล้ว — ใช้อีเมลอื่นสำหรับทีมงาน (บัญชีลูกค้าเปลี่ยนเป็นทีมงานไม่ได้)")
    tid = db.create_tenant(e, _m().hash_pw(password), name.strip(), is_admin=True)
    db.set_staff(tid, role)
    db.set_plan(tid, "unlimited")
    return _back(notice=f"เพิ่ม {e} เป็น{staff.ROLES[role][0]}แล้ว — ส่งรหัสผ่านให้เขาทางช่องทางส่วนตัว และให้เปลี่ยนรหัสที่ /account/password")


@router.post("/admin/staff/{tid}/perms")
async def staff_perms(request: Request, tid: int):
    if not request.session.get("is_admin"):
        return RedirectResponse("/login", status_code=303)
    t = db.get_tenant(tid)
    if not t or not t["is_admin"]:
        return _back(error="ไม่พบบัญชีทีมงานนี้")
    form = await request.form()
    levels = {a: form.get("lv_" + a) for a in staff.AREAS if form.get("lv_" + a)}
    role, perms_json = staff.normalize(form.get("role") or "", levels)
    after = {**dict(t), "staff_role": role, "staff_perms": perms_json}
    if staff.is_owner(t) and not staff.is_owner(after) and _owners_left(tid) == 0:
        return _back(error="ต้องเหลือเจ้าของ (จัดการทีมงานได้) อย่างน้อย 1 คน — เพิ่มเจ้าของอีกคนก่อนลดสิทธิ์บัญชีนี้")
    db.set_staff(tid, role, perms_json)
    label = "กำหนดเอง" if role == staff.CUSTOM else staff.ROLES[role][0]
    return _back(notice=f"บันทึกสิทธิ์ของ {t['email']} แล้ว ({label}) — มีผลทันที")


@router.post("/admin/staff/{tid}/active")
def staff_active(request: Request, tid: int, on: str = Form("")):
    if not request.session.get("is_admin"):
        return RedirectResponse("/login", status_code=303)
    t = db.get_tenant(tid)
    if not t or not t["is_admin"]:
        return _back(error="ไม่พบบัญชีทีมงานนี้")
    if not on:
        if tid == request.session.get("tenant_id"):
            return _back(error="ปิดบัญชีของตัวเองไม่ได้")
        if staff.is_owner(t) and _owners_left(tid) == 0:
            return _back(error="ต้องเหลือเจ้าของที่ใช้งานได้อย่างน้อย 1 คน")
    db.set_disabled(tid, not on)
    return _back(notice=f"{'เปิด' if on else 'ปิด'}บัญชี {t['email']} แล้ว" + ("" if on else " — ออกจากระบบทันทีในคำขอถัดไป"))


@router.post("/admin/staff/{tid}/password")
def staff_password(request: Request, tid: int, password: str = Form(...)):
    if not request.session.get("is_admin"):
        return RedirectResponse("/login", status_code=303)
    t = db.get_tenant(tid)
    if not t or not t["is_admin"]:
        return _back(error="ไม่พบบัญชีทีมงานนี้")
    if len(password) < MIN_PW:
        return _back(error=f"รหัสผ่านใหม่ต้องยาวอย่างน้อย {MIN_PW} ตัว")
    db.set_password(tid, _m().hash_pw(password))
    return _back(notice=f"ตั้งรหัสผ่านใหม่ให้ {t['email']} แล้ว")


@router.post("/admin/staff/{tid}/email")
def staff_email(request: Request, tid: int, email: str = Form(...)):
    if not request.session.get("is_admin"):
        return RedirectResponse("/login", status_code=303)
    t = db.get_tenant(tid)
    if not t or not t["is_admin"]:
        return _back(error="ไม่พบบัญชีทีมงานนี้")
    e = (email or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e):
        return _back(error="รูปแบบอีเมลไม่ถูกต้อง")
    if not db.set_email(tid, e):
        return _back(error=f"อีเมล {e} ถูกใช้กับบัญชีอื่นแล้ว")
    if tid == request.session.get("tenant_id"):
        request.session["email"] = e
    return _back(notice=f"เปลี่ยนอีเมลเข้าระบบเป็น {e} แล้ว")
