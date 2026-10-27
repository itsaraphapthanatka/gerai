"""หน้าแอดมิน CRM: กระดานดีล /admin/deals + ฟอร์มบนหน้าลูกค้าเป้าหมาย (มูลค่า · งานถัดไป · กิจกรรม · ผู้ติดต่อ · ผูกคำขอนัดคุย)
แยกจาก main.py (ไฟล์ใหญ่ที่หลายคนแก้) — main.py include router นี้ท้ายไฟล์ · ตัวช่วยร่วม (_is_admin, templates) อ่านจาก main ตอนเรียก"""
from __future__ import annotations
from urllib.parse import urlparse, quote as _q

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from . import crm, db, leads as L

router = APIRouter()


def _m():
    from . import main
    return main


def _admin(request: Request) -> bool:
    return bool(request.session.get("is_admin"))


def _who(request: Request) -> str:
    return request.session.get("email") or "owner"


def _wants_json(request: Request) -> bool:
    return request.headers.get("x-requested-with") == "fetch" or "application/json" in (request.headers.get("accept") or "")


def _back(request: Request, lead_id: int | None = None, notice: str = "", anchor: str = "") -> RedirectResponse:
    """กลับหน้าที่กดมา (กระดาน/หน้าลูกค้า) — รับเฉพาะ path ภายใต้ /admin/ ของเราเอง"""
    ref = urlparse(request.headers.get("referer") or "")
    path = ref.path if ref.path.startswith("/admin/") else ""
    if lead_id and f"/admin/leads/{lead_id}" in path:
        url = f"/admin/leads/{lead_id}"
    elif path.startswith("/admin/deals"):
        qs = "&".join(p for p in (ref.query or "").split("&") if p and not p.startswith("notice="))
        url = "/admin/deals" + (f"?{qs}" if qs else "")
    else:
        url = f"/admin/leads/{lead_id}" if lead_id else "/admin/deals"
    if notice:
        url += ("&" if "?" in url else "?") + "notice=" + _q(notice)
    return RedirectResponse(url + anchor, status_code=303)


def _deny(request: Request):
    if _wants_json(request):
        return JSONResponse({"error": "ต้องเข้าสู่ระบบแอดมิน"}, status_code=403)
    return RedirectResponse("/login", status_code=303)


@router.get("/admin/deals")
def admin_deals(request: Request, q: str = "", source: str = "", overdue: str = "", notice: str = ""):
    if not _admin(request):
        return RedirectResponse("/app" if request.session.get("tenant_id") else "/login", status_code=303)
    b = crm.board(q, source if source in L.SOURCES else "", overdue == "1")
    return _m().templates.TemplateResponse(request, "admin_deals.html", {
        **b, "requests": crm.requests_inbox(), "lead_options": crm.lead_options(), "sources": L.SOURCES,
        "statuses": [(k, crm.BOARD_LABEL.get(k, lb)) for k, lb in L.STATUSES], "board_stages": crm.BOARD, "presets": crm.PRESETS, "next_kinds": crm.NEXT_KINDS,
        "value_src": crm.VALUE_SRC, "notice": notice})


@router.post("/admin/deals/{lead_id}/stage")
def deal_stage(request: Request, lead_id: int, status: str = Form("")):
    if not _admin(request):
        return _deny(request)
    lead = db.get_lead(lead_id)
    if not lead or status not in L.STATUS_LABEL:
        return JSONResponse({"error": "ไม่พบดีลหรือขั้นไม่ถูกต้อง"}, status_code=400) if _wants_json(request) else _back(request, lead_id)
    crm.set_stage(lead, status, _who(request))
    if _wants_json(request):
        st = db.get_lead(lead_id)["status"]
        return JSONResponse({"ok": True, "status": st, "label": crm.BOARD_LABEL.get(st, L.STATUS_LABEL[st]), "column": crm.COLUMN_OF.get(st, st)})
    return _back(request, lead_id, anchor=f"#deal-{lead_id}")


@router.post("/admin/deals/{lead_id}/value")
def deal_value(request: Request, lead_id: int, value: str = Form("")):
    if not _admin(request):
        return _deny(request)
    if not db.get_lead(lead_id):
        return _back(request)
    crm.set_value(lead_id, value, _who(request))
    v = crm.deal_value(db.get_lead(lead_id))
    if _wants_json(request):
        return JSONResponse({"ok": True, "value": v["value"], "src": v["src"], "src_label": crm.VALUE_SRC[v["src"]]})
    return _back(request, lead_id, anchor=f"#deal-{lead_id}")


@router.post("/admin/deals/{lead_id}/next")
def deal_next(request: Request, lead_id: int, action: str = Form(""), preset: str = Form(""), at: str = Form(""),
              kind: str = Form("call"), clear: str = Form("")):
    if not _admin(request):
        return _deny(request)
    if not db.get_lead(lead_id):
        return _back(request)
    if clear or not action.strip():
        crm.set_next(lead_id, "", None, who=_who(request))
        return _back(request, lead_id, "ล้างงานถัดไปแล้ว", anchor=f"#deal-{lead_id}")
    when = crm.preset_at(preset, at) if (preset and preset != "custom") or at.strip() else None
    if preset == "custom" and at.strip() and not when:
        return _back(request, lead_id, "อ่านวันเวลาไม่ออก", anchor=f"#deal-{lead_id}")
    crm.set_next(lead_id, action, when, kind, _who(request))
    return _back(request, lead_id, f"ตั้งงานถัดไปแล้ว: {action.strip()[:60]}" + (f" · {crm.fmt_ts(when)}" if when else ""), anchor=f"#deal-{lead_id}")


@router.post("/admin/deals/{lead_id}/activity")
def deal_activity(request: Request, lead_id: int, kind: str = Form("note"), outcome: str = Form(""), reached: str = Form(""),
                  next_action: str = Form(""), preset: str = Form(""), at: str = Form(""), next_kind: str = Form("call"),
                  done_next: str = Form("")):
    if not _admin(request):
        return _deny(request)
    lead = db.get_lead(lead_id)
    if not lead:
        return _back(request)
    if kind not in crm.ACT_KINDS:
        return _back(request, lead_id, "ชนิดกิจกรรมไม่ถูกต้อง")
    if kind == "note" and not outcome.strip():
        return _back(request, lead_id, "พิมพ์บันทึกก่อน")
    msg = crm.log_activity(lead, kind, outcome, reached != "0", _who(request))
    if next_action.strip():
        when = crm.preset_at(preset, at) if (preset and preset != "custom") or at.strip() else None
        crm.set_next(lead_id, next_action, when, next_kind, _who(request))
        msg += f" · งานถัดไป: {next_action.strip()[:60]}" + (f" ({crm.fmt_ts(when)})" if when else "")
    elif done_next and lead["next_action"]:
        crm.set_next(lead_id, "", None, who=_who(request))
        msg += " · ปิดงานถัดไปเดิมแล้ว"
    return _back(request, lead_id, msg)


def _contact_fields(name, role, phone, email, line_id, note) -> dict:
    return {"name": name[:120], "role": role[:80], "phone": phone[:40], "email": email.strip().lower()[:160], "line_id": line_id[:120], "note": note[:500]}


@router.post("/admin/deals/{lead_id}/contacts")
def contact_add(request: Request, lead_id: int, name: str = Form(""), role: str = Form(""), phone: str = Form(""),
                email: str = Form(""), line_id: str = Form(""), note: str = Form("")):
    if not _admin(request):
        return _deny(request)
    if not db.get_lead(lead_id):
        return _back(request)
    f = _contact_fields(name, role, phone, email, line_id, note)
    if not any(v.strip() for v in f.values()):
        return _back(request, lead_id, "กรอกชื่อหรือช่องทางติดต่ออย่างน้อยหนึ่งช่อง")
    db.add_lead_contact(lead_id, **f)
    return _back(request, lead_id, f"เพิ่มผู้ติดต่อ {f['name'] or ''} แล้ว".replace("  ", " "))


@router.post("/admin/deals/{lead_id}/contacts/{cid}")
def contact_edit(request: Request, lead_id: int, cid: int, name: str = Form(""), role: str = Form(""), phone: str = Form(""),
                 email: str = Form(""), line_id: str = Form(""), note: str = Form("")):
    if not _admin(request):
        return _deny(request)
    c = db.get_lead_contact(cid)
    if not c or c["lead_id"] != lead_id:
        return _back(request, lead_id, "ไม่พบผู้ติดต่อ")
    db.update_lead_contact(cid, **_contact_fields(name, role, phone, email, line_id, note))
    return _back(request, lead_id, "บันทึกผู้ติดต่อแล้ว")


@router.post("/admin/deals/{lead_id}/contacts/{cid}/delete")
def contact_delete(request: Request, lead_id: int, cid: int):
    if not _admin(request):
        return _deny(request)
    c = db.get_lead_contact(cid)
    if c and c["lead_id"] == lead_id:
        db.delete_lead_contact(cid)
    return _back(request, lead_id, "ลบผู้ติดต่อแล้ว")


@router.post("/admin/deals/requests/{cid}/link")
def request_link(request: Request, cid: int, lead: str = Form(""), domain: str = Form("")):
    if not _admin(request):
        return _deny(request)
    lid, msg = crm.link_request(cid, lead, domain, _who(request))
    return RedirectResponse(f"/admin/deals?notice={_q(msg)}" + (f"#deal-{lid}" if lid else "#requests"), status_code=303)


@router.post("/admin/deals/requests/{cid}/dismiss")
def request_dismiss(request: Request, cid: int):
    if not _admin(request):
        return _deny(request)
    c = db.get_contact(cid)
    if c and c["status"] == "new":
        db.link_contact(cid, None, "handled")
    return RedirectResponse("/admin/deals?notice=" + _q("ซ่อนคำขอแล้ว (ยังดูได้ที่หน้าผู้ติดต่อ)") + "#requests", status_code=303)
