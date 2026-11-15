"""หน้าเว็บสาธารณะชุดใหม่ (stage 01+) — แยกจาก main.py · ทุกหน้าอยู่หลัง flag marketing_v2 (ค่าเริ่มต้นปิด)
flag ปิด + ไม่ใช่แอดมินที่ preview → 404 (เหมือนไม่มีหน้า) · preview ใส่ noindex ทั้ง header และ meta"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from . import flags
from .csrf import require_csrf

router = APIRouter()
NOINDEX = {"X-Robots-Tag": "noindex, nofollow"}


def _render(request: Request, name: str, ctx: dict | None = None, *, internal: bool = False):
    from .main import templates                         # อ่านตอนเรียก กัน import วน (แบบเดียวกับ crm_web)
    preview = internal or not flags.enabled("marketing_v2")
    resp = templates.TemplateResponse(request, name, {"preview": preview, **(ctx or {})})
    if preview:
        resp.headers.update(NOINDEX)
    return resp


def _home_ctx(request: Request) -> dict:
    """ข้อมูลจริงเท่านั้น: ราคาเริ่มต้นอ่านจาก billing.PLANS (ไม่พิมพ์ตัวเลขเอง) · src/ref ผ่านตัวกรองเดิมของ /audit
    SEO ต่อจากหน้าเดิม: canonical = /, hreflang ชี้หน้าภาษาอื่นเดิม, ลิงก์บทความล่าสุด"""
    from . import billing, landing_i18n as L
    from .main import _src, _ref, _home_articles
    pro = billing.PLANS.get("pro") or {}
    base = str(request.base_url).rstrip("/")
    return {"price_from": pro.get("price") or None,
            "src": _src(request.query_params.get("src", "")) or "home",
            "ref": _ref(request, request.query_params.get("ref", "")),
            "canonical": base + "/",
            "alternates": [{"hreflang": L.HTML_LANG[c], "href": base + L.PATHS[c]} for c in L.LANGS],
            # ตัวเลือกภาษาแบบหน้าแรกเดิม: ไปหน้าแรกของภาษานั้นตรง ๆ (ไม่ใช่ ?lang= ซึ่งจะยังได้หน้าไทย)
            "home_langs": [{"name": L.NAMES[c], "href": "/?lang=th" if c == "th" else L.PATHS[c],
                            "hreflang": L.HTML_LANG[c], "current": c == "th"} for c in L.LANGS],
            "articles": _home_articles(),
            "show_services": _services_visible(request)}


def _services_visible(request: Request) -> bool:
    from . import service_pages as SP
    return SP.SCOPE_STATUS == "confirmed" or flags.previewing("marketing_v2", request)


def home(request: Request):
    """หน้าแรกชุดใหม่สำหรับ / (เรียกจาก main.landing เมื่อ flag marketing_v2 active) · flag ยังไม่เปิดจริง = noindex"""
    return _render(request, "mk/home.html", _home_ctx(request))


@router.get("/preview/home")
def home_preview(request: Request):
    """หน้าแรกชุดใหม่ (stage 02) — รอเจ้าของอนุมัติก่อนต่อเข้ากับ / · ตอนนี้เห็นเฉพาะแอดมินที่ preview"""
    if not flags.previewing("marketing_v2", request):
        raise HTTPException(status_code=404)
    return _render(request, "mk/home.html", _home_ctx(request), internal=True)


@router.get("/preview/ui-kit")
def ui_kit(request: Request):
    """สมุดชิ้นส่วน — หน้าภายในเสมอ (noindex) ให้แอดมินตรวจหน้าตาก่อนเปิดใช้จริง"""
    if not flags.previewing("marketing_v2", request):
        raise HTTPException(status_code=404)
    return _render(request, "mk/ui_kit.html", internal=True)


# ---------- stage 03: วิธีทำงาน · บริการ · กลุ่มธุรกิจ ----------
def _sp_page(request: Request, name: str, *, needs_scope: bool = False):
    """หลัง flag marketing_v2 เหมือนหน้าแรก · หน้าที่มีขอบเขตบริการ (needs_scope) เปิดให้คนทั่วไปได้เมื่อเจ้าของยืนยันขอบเขตแล้วเท่านั้น
    (service_pages.SCOPE_STATUS = "confirmed") — ก่อนนั้นเห็นเฉพาะแอดมินที่ preview พร้อมแถบร่าง"""
    from . import service_pages as SP
    if not flags.active("marketing_v2", request):
        raise HTTPException(status_code=404)
    previewing = flags.previewing("marketing_v2", request)
    if needs_scope and SP.SCOPE_STATUS != "confirmed" and not previewing:
        raise HTTPException(status_code=404)
    from . import landing_i18n as L
    ctx = SP.ctx(request)
    ctx["home_langs"] = [{"name": L.NAMES[c], "href": "/?lang=th" if c == "th" else L.PATHS[c],     # หน้าเหล่านี้มีแต่ภาษาไทย → เมนูภาษาพาไปหน้าแรกของภาษานั้น
                          "hreflang": L.HTML_LANG[c], "current": c == "th"} for c in L.LANGS]
    resp = _render(request, name, {**ctx, "show_services": SP.SCOPE_STATUS == "confirmed" or previewing}, internal=previewing)
    return resp


@router.get("/how-it-works")
def how_it_works(request: Request):
    return _sp_page(request, "mk/how.html")


@router.get("/services")
def services(request: Request):
    return _sp_page(request, "mk/services.html", needs_scope=True)


@router.get("/services/foundation-sprint")
def foundation_sprint(request: Request):
    return _sp_page(request, "mk/foundation.html", needs_scope=True)


@router.get("/solutions/industrial-property")
def industrial_property(request: Request):
    return _sp_page(request, "mk/solution.html")


# ---------- นัดคุย 30 นาที (stage 06) — แทนปุ่มเดิมของหน้าแรกเก่า · ขอให้โทรกลับ ไม่ใช่การจองเวลา (ยังไม่มีปฏิทินจริง) ----------
def _callback_page(request: Request, form: dict | None = None, errors: dict | None = None, done: bool = False, status_code: int = 200):
    from . import plan_request
    resp = _render(request, "mk/callback.html", {"form": form or {"idem_key": plan_request.new_key()}, "errors": errors or {},
                                                  "done": done, "pr": plan_request})
    resp.status_code = status_code
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.get("/callback")
def callback_form(request: Request, done: int = 0):
    if not flags.active("marketing_v2", request):
        raise HTTPException(status_code=404)
    return _callback_page(request, done=bool(done))


@router.post("/callback", dependencies=[Depends(require_csrf)])
async def callback_submit(request: Request):
    """เก็บลงตาราง contacts เดิม (plan = "นัดคุย") → ขึ้นกล่องคำขอบนกระดานดีลแบบเดียวกับปุ่มเดิม · บันทึกก่อนแล้วจึงบอกว่าได้รับ"""
    from . import db, pipeline, plan_request, i18n, landing_i18n
    from .main import _redirect
    if not flags.active("marketing_v2", request):
        raise HTTPException(status_code=404)
    form = dict(await request.form())
    if (form.get("website") or "").strip():                   # honeypot
        return _redirect("/callback?done=1")
    v, err = plan_request.validate(form)
    if err:
        return _callback_page(request, v, err, status_code=422)
    if request.session.get("cb_idem") == v["idem_key"]:          # กดส่งซ้ำ/รีเฟรช = ไม่สร้างคำขอซ้ำ
        return _redirect("/callback?done=1")
    contact = f"{plan_request.CHANNELS[v['contact_channel']]}: {v['contact_value']}"
    note = " · ".join(x for x in [v["message"], "รับข่าวสาร: " + ("ยินยอม" if v["marketing"] else "ไม่ยินยอม"),
                                  f"ข้อความยินยอม {plan_request.NOTICE_VERSION}"] if x)
    try:
        db.add_contact(v["name"], contact, landing_i18n.CALL_PLAN, note[:1000], i18n.lang_of(request))
    except Exception:
        return _callback_page(request, v, {"_": "บันทึกคำขอไม่สำเร็จ ยังไม่ได้ส่งถึงทีม ลองกดส่งอีกครั้ง"}, status_code=500)
    request.session["cb_idem"] = v["idem_key"]
    pipeline.alert_admins(f"📩 คำขอนัดคุยใหม่: {v['name']} · สนใจ {landing_i18n.CALL_PLAN} — ผูกกับดีลที่หน้าดีล",
                          "/admin/deals#requests", "contact")
    return _redirect("/callback?done=1")
