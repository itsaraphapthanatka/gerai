"""GEO Platform — FastAPI app (MVP, monitoring-first)."""
from __future__ import annotations
import os
import json
from pathlib import Path
from dotenv import load_dotenv

# โหลด .env ก่อน import db (db อ่าน GEO_DB_PATH ตอน import)
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse, PlainTextResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
import bcrypt

from . import db, geo_worker, geo_content, wp_client, billing, ai_client, image_finder, promptpay, site_health, ai_visibility, gsc, pagespeed, ai_serp, report, analytics_chat, mailer, campaigns, autopilot

# ลิงก์ในอีเมล/งานเบื้องหลังที่ไม่มี request ให้อ่าน base_url
BASE_URL = os.getenv("GEO_BASE_URL", "https://geo.appreview.cloud").rstrip("/")


def hash_pw(password: str) -> str:
    # bcrypt อ่านได้สูงสุด 72 ไบต์ — ตัดให้พอดีกันมันโยน error
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def verify_pw(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], hashed.encode("utf-8"))
    except Exception:
        return False

BASE = Path(__file__).resolve().parent
app = FastAPI(title="GEO Platform")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET", "dev-secret-change-me"))
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))


@app.middleware("http")
async def _no_cache_html(request: Request, call_next):
    """หน้า HTML ที่เป็น dynamic (ไม่ได้ตั้ง Cache-Control เอง) → no-cache กัน browser โชว์ของเก่า
    (หน้า hosted/landing ที่ตั้ง max-age ไว้แล้วจะไม่โดน)"""
    resp = await call_next(request)
    ct = resp.headers.get("content-type", "")
    if ct.startswith("text/html") and "cache-control" not in resp.headers:
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
    return resp


@app.on_event("startup")
def _startup():
    db.init_db()
    _maybe_start_scheduler()
    # Jinja2 global: sidebar ดึงแบรนด์ของ tenant ที่ล็อกอินอยู่
    templates.env.globals["sidebar_brands"] = lambda req: (
        db.list_brands(req.session["tenant_id"]) if req.session.get("tenant_id") else []
    )
    templates.env.globals["unread_count"] = lambda req: (
        db.count_unread(req.session["tenant_id"]) if req.session.get("tenant_id") else 0
    )
    templates.env.globals["new_contacts_count"] = lambda req: (
        db.count_new_contacts() if req.session.get("is_admin") else 0
    )


def _auto_content_tick(b, weekly: int, now_local):
    """สร้างร่างคอนเทนต์ปิด gap แบบมีจังหวะ (~weekly ชิ้น/สัปดาห์) — ร่างเท่านั้น ไม่ publish เอง"""
    import datetime as _dt
    bid = b["id"]
    # เพดานต่อสัปดาห์ (รวมทุกแหล่ง — manual+auto)
    week_ago = (now_local - _dt.timedelta(days=7)).isoformat(timespec="seconds")
    if db.count_content_since(bid, week_ago) >= weekly:
        return
    # เว้นระยะจากร่าง auto ครั้งก่อน เพื่อกระจายให้ทั่วสัปดาห์
    spacing_s = max(1, 7 // weekly) * 86400
    last = b["last_auto_content_at"]
    if last:
        try:
            if (now_local - _dt.datetime.fromisoformat(last)).total_seconds() < spacing_s:
                return
        except Exception:
            pass
    # หา gap ระดับ critical (ยังไม่มีคอนเทนต์) — ถ้าไม่มี = ปิดครบแล้ว หยุดสร้าง
    crit = [g for g in db.get_content_gaps(bid)["gaps"] if g["level"] == "critical"]
    if not crit:
        return
    ok, _msg = billing.check(db.get_tenant(b["tenant_id"]), "content")
    if not ok:
        return  # เกิน quota แพ็กเกจ
    g = crit[0]
    lang = g["lang"] or "th"
    b = _brand_grounded(b)
    # เลือกรูปแบบ AEO ตามลักษณะคำถาม (เทียบ → comparison, ลิสต์ → listicle)
    data = geo_content.generate_content(b, g["question"], lang, geo_content.pick_ctype(g["question"]))
    cid = db.create_content_item(
        bid, g["question_id"], lang, data["title"], data["meta_title"],
        data["meta_desc"], data["body_md"], data["schema_json"], "auto",  # มาร์คเป็น auto สำหรับ auto-publish
    )
    if b["auto_image"]:
        _attach_image(b, cid, g["question"])
    db.touch_auto_content(bid, now_local.isoformat(timespec="seconds"))


def _publish_item(item, conn, brand, status="publish"):
    """เผยแพร่คอนเทนต์ 1 ชิ้นเข้า WordPress (ใช้ร่วมกันระหว่าง route กับ auto-publish)"""
    if conn["mode"] == "connector" and conn["api_key"]:
        key = wp_client.decrypt(conn["api_key"])
        res = wp_client.connector_publish(
            conn["site_url"], key, item["title"], item["body_md"],
            schema_json=item["schema_json"], meta_title=item["meta_title"],
            meta_desc=item["meta_desc"], status=status, wp_post_id=item["wp_post_id"],
        )
        if res["ok"]:
            db.mark_content_published(item["id"], res["id"], res.get("link", ""))
            wp_client.connector_push_settings(
                conn["site_url"], key,
                llms_txt=geo_content.llms_txt(brand, db.list_content(brand["id"])),
            )
        return res
    res = wp_client.publish_post(
        conn["site_url"], conn["auth_user"], wp_client.decrypt(conn["auth_secret"]),
        item["title"], item["body_md"], status=status, wp_post_id=item["wp_post_id"],
    )
    if res["ok"]:
        db.mark_content_published(item["id"], res["id"], res.get("link", ""))
    return res


def _auto_publish_tick(b, days, now_local):
    """เผยแพร่ร่าง auto ที่ถึง review window (days) — มี WP → ดันเข้า WP, ไม่มี (เว็บ dev) → ขึ้นหน้า hosted"""
    import datetime as _dt
    cutoff = (now_local - _dt.timedelta(days=days)).isoformat(timespec="seconds")
    drafts = db.due_auto_drafts(b["id"], cutoff)
    if not drafts:
        return
    conn = db.get_wp_connection(b["id"])
    brand = db.get_brand(b["id"])
    site = geo_content._site_url(brand)
    for it in drafts:
        try:
            if conn:
                _publish_item(it, conn, brand, status="publish")
            else:
                db.mark_content_published(it["id"], None, f"{site}/geo/{it['id']}")  # hosted
        except Exception:
            pass


def _maybe_start_scheduler():
    """(ออปชัน) auto-run มอนิเตอร์ในแอป เมื่อ GEO_AUTORUN=1 — ดีฟอลต์ปิด (ใช้ run_monitors.py + cron แทนได้)."""
    if os.getenv("GEO_AUTORUN", "").strip().lower() not in ("1", "true", "yes"):
        return
    import threading
    import time
    import datetime

    default_days = int(os.getenv("GEO_RUN_INTERVAL_DAYS", "7"))

    def _parse_hm(s):
        try:
            hh, mm = (s or "08:00").split(":")
            return max(0, min(23, int(hh))), max(0, min(59, int(mm)))
        except Exception:
            return 8, 0

    def _due(ts, interval, time_str, now_local):
        if not interval or interval <= 0:  # 0/None = ปิด auto-run แบรนด์นี้
            return False
        if not ts:
            return True  # ไม่เคยรัน → รัน bootstrap เลย
        try:
            last = datetime.datetime.fromisoformat(ts)  # naive = เวลาไทย (server TZ = Asia/Bangkok)
        except Exception:
            return True
        hh, mm = _parse_hm(time_str)
        # รอบถัดไป = (วันรันล่าสุด + interval วัน) เวลา HH:MM
        next_dt = (last + datetime.timedelta(days=interval)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        return now_local >= next_dt

    def loop():
        while True:
            try:
                now_local = datetime.datetime.now()  # server TZ = Asia/Bangkok → เวลาไทย
                for b in db.list_all_brands():
                    try:
                        interval = b["auto_run_days"] if b["auto_run_days"] is not None else default_days
                        if _due(b["last_run_at"], interval, b["auto_run_time"], now_local):
                            geo_worker.run_for_brand(b["id"])
                        wk = b["auto_content_weekly"] or 0
                        if wk > 0:
                            _auto_content_tick(b, wk, now_local)
                        pub_days = b["auto_publish_days"]
                        if pub_days is not None and pub_days >= 0:
                            _auto_publish_tick(b, pub_days, now_local)
                    except Exception:
                        pass  # แบรนด์นี้พลาด ไม่ให้กระทบแบรนด์อื่น
            except Exception:
                pass
            time.sleep(60)  # เช็คทุก 60 วิ → ยิงตรงเวลาเป๊ะระดับนาที

    threading.Thread(target=loop, daemon=True, name="geo-autorun").start()


def _tid(request: Request):
    return request.session.get("tenant_id")


def _is_admin(request: Request) -> bool:
    return bool(request.session.get("is_admin"))


def _brand_for(request: Request, brand_id: int):
    """แบรนด์ที่ผู้ใช้มีสิทธิ์ดู: เจ้าของเห็นของตัวเอง, admin เห็นทุกอัน."""
    tid = _tid(request)
    if not tid:
        return None
    return db.get_brand(brand_id) if _is_admin(request) else db.get_brand(brand_id, tid)


def _dashboard_ctx(request: Request, error=None):
    tid = _tid(request)
    tenant = db.get_tenant(tid)
    brands = db.list_brands(tid)
    brand_gaps = {b["id"]: db.get_content_gaps(b["id"]) for b in brands}
    return {"brands": brands, "brand_gaps": brand_gaps, "name": request.session.get("name"),
            "plan": billing.plan_of(tenant), "usage": billing.usage(tid), "error": error,
            "is_admin": bool(tenant and tenant["is_admin"])}


def _brand_ctx(brand, error=None):
    bid = brand["id"]
    return {"brand": brand, "questions": db.list_questions(bid), "runs": db.list_runs(bid),
            "content": db.list_content(bid), "wp": db.get_wp_connection(bid), "error": error}


def _redirect(url: str):
    return RedirectResponse(url, status_code=303)


def _public_base() -> str:
    return os.getenv("PUBLIC_BASE_URL", "https://geo.appreview.cloud").rstrip("/")


def _attach_image(brand, content_id: int, topic: str) -> None:
    """หา/สร้างรูปประกอบ แล้วแปะหัวบทความ (เงียบ ถ้าไม่ได้ก็ข้าม)"""
    try:
        res = image_finder.image_for_article(brand, topic, geo_content._site_url(brand))
        if not res:
            return
        if res["mode"] == "generated":
            gen_dir = BASE / "static" / "gen"
            gen_dir.mkdir(parents=True, exist_ok=True)
            (gen_dir / f"c{content_id}.png").write_bytes(res["png"])
            url = f"{_public_base()}/static/gen/c{content_id}.png"
        else:
            url = res["url"]
        item = db.get_content(content_id)
        if not item:
            return
        alt = (res.get("alt") or topic).replace("\n", " ").replace("]", "").replace(")", "").strip()
        img = f"![{alt}]({url})"
        body = item["body_md"] or ""
        if body.lstrip().startswith(">"):
            # มี TL;DR (answer-first) อยู่บนสุด → แทรกรูปหลังบล็อก TL;DR ไม่ให้ answer-first ตก
            parts = body.split("\n\n", 1)
            body = (parts[0] + "\n\n" + img + "\n\n" + parts[1]) if len(parts) == 2 else (body + "\n\n" + img)
        else:
            body = f"{img}\n\n{body}"
        db.update_content_body(content_id, body)
    except Exception:
        pass


def _brand_grounded(brand):
    """คืน brand ที่มี site_context (ดึงเนื้อหาเว็บจริงมา cache ครั้งแรก) เพื่อใช้ ground การเขียน"""
    try:
        if not brand["site_context"]:
            txt = ai_client._fetch_text(geo_content._site_url(brand))
            if txt and len(txt) > 40:
                db.set_brand_site_context(brand["id"], txt[:4000])
                brand = db.get_brand(brand["id"])
    except Exception:
        pass
    return brand


# ---------- auth ----------
@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    t = db.get_tenant_by_email(email)
    if not t or not verify_pw(password, t["password_hash"]):
        return templates.TemplateResponse(request, "login.html", {"error": "อีเมลหรือรหัสผ่านไม่ถูกต้อง"})
    request.session.update({
        "tenant_id": t["id"], "email": t["email"],
        "name": t["name"] or t["email"], "is_admin": bool(t["is_admin"]),
    })
    return _redirect("/admin" if t["is_admin"] else "/app")


# ปิดรับสมัครเอง — บัญชีลูกค้าสร้างโดยผู้ดูแลผ่านหน้า /admin เท่านั้น
@app.get("/register")
def register_page(request: Request):
    return _redirect("/login")


@app.post("/register")
def register(request: Request):
    return _redirect("/login")


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return _redirect("/login")


# ---------- landing (public) ----------
@app.get("/")
def landing(request: Request):
    return templates.TemplateResponse(request, "landing.html",
                                      {"plans": billing.PLANS, "addons": billing.ADDONS, "ai_live": _ai_live()})


@app.post("/api/contact")
async def api_contact(request: Request):
    """ฟอร์มติดต่อจากหน้า landing (public) → เก็บ lead + แจ้ง admin"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if (body.get("company") or "").strip():        # honeypot: บอทกรอก → เงียบ
        return JSONResponse({"ok": True})
    name = (body.get("name") or "").strip()[:120]
    contact = (body.get("contact") or "").strip()[:160]
    plan = (body.get("plan") or "").strip()[:60]
    message = (body.get("message") or "").strip()[:1000]
    if not name or not contact:
        return JSONResponse({"error": "กรุณากรอกชื่อและช่องทางติดต่อ"}, status_code=400)
    db.add_contact(name, contact, plan, message)
    db.notify_admins(f"📩 ผู้ติดต่อใหม่: {name} ({contact})" + (f" · สนใจ {plan}" if plan else ""),
                     "/admin/contacts", "contact")
    return JSONResponse({"ok": True})


# ---------- PWA (manifest + service worker) ----------
@app.get("/manifest.webmanifest")
def pwa_manifest():
    return JSONResponse({
        "name": "เจอ.AI — GEO Platform",
        "short_name": "เจอ.AI",
        "description": "ให้แบรนด์คุณโผล่ในคำตอบ AI — วัด Share of Voice + สร้างคอนเทนต์ GEO อัตโนมัติ",
        "lang": "th",
        "start_url": "/app",
        "scope": "/",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#0a0a0a",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/static/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }, media_type="application/manifest+json")


_SW_JS = """
const CACHE = 'geo-v3';
const ASSETS = ['/static/icon-192.png', '/static/icon-512.png', '/manifest.webmanifest'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
// respondWith ต้องได้ Response เสมอ — เดิม catch แล้วคืน caches.match() ซึ่งเป็น undefined สำหรับหน้าเว็บ
// (เราแคชแค่ /static) พอเซิร์ฟเวอร์รีสตาร์ท/เน็ตสะดุด เบราว์เซอร์จึงฟ้อง "Failed to convert value to 'Response'"
// และ "network error response: the promise was rejected" ทุกครั้ง แทนที่จะเห็นหน้าออฟไลน์ธรรมดา
function offline(req) {
  if (req.mode === 'navigate') {
    return new Response('<!doctype html><html lang="th"><meta charset="utf-8"><title>เชื่อมต่อไม่ได้</title>'
      + '<p style="font-family:system-ui,sans-serif;padding:40px;line-height:1.7">เชื่อมต่อเซิร์ฟเวอร์ไม่ได้ชั่วคราว — ลองรีเฟรชอีกครั้ง</p>',
      {status: 503, headers: {'Content-Type': 'text/html; charset=utf-8'}});
  }
  return Response.error();
}
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;                 // ไม่แตะ POST
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;       // เฉพาะ same-origin
  if (url.pathname.startsWith('/static/')) {        // static: cache-first
    e.respondWith(caches.match(req).then(c => c || fetch(req).then(r => {
      if (r.ok) { const copy = r.clone(); caches.open(CACHE).then(c => c.put(req, copy)); }
      return r;
    })).catch(() => offline(req)));
    return;
  }
  e.respondWith(fetch(req).catch(() => caches.match(req).then(c => c || offline(req))));  // page: network-first
});
"""


@app.get("/sw.js")
def pwa_service_worker():
    return Response(_SW_JS, media_type="application/javascript",
                    headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})


# ---------- dashboard (ต้อง login) ----------
@app.get("/app")
def dashboard(request: Request):
    if not _tid(request):
        return _redirect("/login")
    return templates.TemplateResponse(request, "dashboard.html", _dashboard_ctx(request))


def _item_label(item: str) -> str:
    if item in billing.PLANS:
        return billing.PLANS[item]["label"]
    for a in billing.ADDONS:
        if a["key"] == item:
            return a["label"]
    return item


@app.get("/notifications")
def notifications_page(request: Request):
    tid = _tid(request)
    if not tid:
        return _redirect("/login")
    items = db.list_notifications(tid)
    db.mark_all_read(tid)  # เปิดดู = อ่านแล้ว
    return templates.TemplateResponse(request, "notifications.html", {"items": items})


def _upgrade_ctx(request, notice=None):
    tid = _tid(request)
    tenant = db.get_tenant(tid)
    return {"plans": billing.PLANS, "addons": billing.ADDONS, "current": billing.plan_key(tenant),
            "usage": billing.usage(tid), "requested": tenant["requested_plan"], "notice": notice,
            "is_admin": bool(tenant and tenant["is_admin"])}


@app.get("/upgrade")
def upgrade_page(request: Request):
    if not _tid(request):
        return _redirect("/login")
    return templates.TemplateResponse(request, "upgrade.html", _upgrade_ctx(request))


def _pay_item(item: str):
    """คืน dict {label, price, period, sub, is_plan} สำหรับ plan หรือ add-on — None ถ้าไม่พบ"""
    if item in billing.PLANS and item != "free" and not billing.PLANS[item].get("hidden"):
        p = billing.PLANS[item]
        return {"label": "แผน " + p["label"], "price": p["price"], "period": p["period"],
                "sub": p["tagline"], "is_plan": True}
    for a in billing.ADDONS:
        if a["key"] == item:
            return {"label": a["label"], "price": a["price"], "period": a["period"],
                    "sub": a["desc"], "is_plan": False}
    return None


def _promptpay_config():
    """บัญชีรับเงิน: ใช้ค่าจาก DB (ตั้งผ่านหน้า admin) ก่อน ถ้าไม่มี fallback ไป .env"""
    ppid = (db.get_setting("promptpay_id") or os.getenv("PROMPTPAY_ID", "")).strip()
    ppname = db.get_setting("promptpay_name") or os.getenv("PROMPTPAY_NAME", "เจอ.AI")
    return ppid, ppname


@app.get("/upgrade/pay/{item}")
def pay_page(request: Request, item: str):
    if not _tid(request):
        return _redirect("/login")
    it = _pay_item(item)
    if not it:
        return _redirect("/upgrade")
    ppid, ppname = _promptpay_config()
    return templates.TemplateResponse(request, "pay.html", {
        "item": item, "p": it, "ppid": ppid, "ppname": ppname,
        "qr": promptpay.qr_data_uri(ppid, it["price"]) if ppid else None,
        "notice": None,
    })


@app.post("/upgrade/pay/{item}")
async def pay_submit(request: Request, item: str, slip: UploadFile = File(None)):
    tid = _tid(request)
    if not tid:
        return _redirect("/login")
    it = _pay_item(item)
    if not it:
        return _redirect("/upgrade")
    slip_path = None
    if slip is not None and slip.filename:
        data = await slip.read()
        if data:
            import secrets as _sec
            ext = os.path.splitext(slip.filename)[1].lower()
            if ext not in (".jpg", ".jpeg", ".png", ".webp", ".pdf"):
                ext = ".jpg"
            up = BASE.parent / "uploads" / "slips"
            up.mkdir(parents=True, exist_ok=True)
            fname = f"{_sec.token_hex(8)}{ext}"
            (up / fname).write_bytes(data[:8_000_000])
            slip_path = f"slips/{fname}"
    db.create_payment(tid, item, it["price"], slip_path)
    if it["is_plan"]:
        db.set_requested_plan(tid, item)
    tn = db.get_tenant(tid)
    db.notify_admins(f"💳 {tn['email']} แจ้งชำระเงิน {it['label']} ฿{it['price']:,}", "/admin", "payment")
    msg = ("ได้รับแจ้งชำระเงินแล้ว ✓ ทีมงานจะตรวจสอบสลิปและเปิดใช้แผนให้"
           if it["is_plan"] else
           "ได้รับแจ้งชำระเงินแพ็กเกจเสริมแล้ว ✓ ทีมงานจะตรวจสอบและดำเนินการให้")
    _ppid, _ppname = _promptpay_config()
    return templates.TemplateResponse(request, "pay.html", {
        "item": item, "p": it, "ppid": _ppid, "ppname": _ppname, "qr": None, "notice": msg,
    })


@app.post("/upgrade")
def upgrade_request(request: Request, plan: str = Form(...)):
    if not _tid(request):
        return _redirect("/login")
    notice = None
    if plan in billing.PLANS:
        tid = _tid(request)
        db.set_requested_plan(tid, plan)
        tn = db.get_tenant(tid)
        db.notify_admins(f"⭐ {tn['email']} ขอแผน {billing.PLANS[plan]['label']}", "/admin", "request")
        notice = f"ส่งคำขอแผน {billing.PLANS[plan]['label']} แล้ว — ทีมงานจะติดต่อกลับ"
    return templates.TemplateResponse(request, "upgrade.html", _upgrade_ctx(request, notice=notice))


@app.post("/api/classify-market")
async def api_classify_market(request: Request):
    if not _tid(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    url = (body.get("url") or "").strip()
    name = (body.get("name") or "").strip()
    if not url:
        return JSONResponse({"error": "กรอกโดเมนเว็บไซต์ก่อน แล้วกดอีกครั้ง"}, status_code=400)
    try:
        market = ai_client.classify_market(url, name)
    except Exception:
        return JSONResponse({"error": "วิเคราะห์ไม่สำเร็จ ลองใหม่ หรือกรอกเอง"}, status_code=502)
    if not market:
        return JSONResponse({"error": "อ่านเว็บไซต์ไม่ได้ — กรอกเองได้เลย"}, status_code=200)
    return JSONResponse({"market": market})


@app.post("/brands")
def create_brand(request: Request, name: str = Form(...), domain: str = Form(...), market: str = Form("")):
    tid = _tid(request)
    if not tid:
        return _redirect("/login")
    ok, msg = billing.check(db.get_tenant(tid), "brands")
    if not ok:
        return templates.TemplateResponse(request, "dashboard.html", _dashboard_ctx(request, error=msg))
    bid = db.create_brand(tid, name, domain, market)
    return _redirect(f"/brands/{bid}")


@app.post("/seed-demo")
def seed_demo(request: Request, industry: str = Form("property")):
    tid = _tid(request)
    if not tid:
        return _redirect("/login")
    demos = {
        "property": {
            "name": "ธุรกิจอสังหาริมทรัพย์ (ตัวอย่าง)",
            "domain": "example-property.com",
            "market": "โกดัง/โรงงานให้เช่า กทม.+ปริมณฑล",
            "questions": [
                ("โกดังให้เช่า ราคาถูก สมุทรปราการ", "th"),
                ("โรงงานให้เช่า บางนา ลาดกระบัง", "th"),
                ("นายหน้าโกดัง โรงงาน ให้เช่า", "th"),
                ("warehouse for rent Bangkok Thailand", "en"),
            ],
        },
        "restaurant": {
            "name": "ร้านอาหาร (ตัวอย่าง)",
            "domain": "example-restaurant.com",
            "market": "ร้านอาหารไทย กรุงเทพ",
            "questions": [
                ("ร้านอาหารไทยอร่อย ใกล้ฉัน", "th"),
                ("ร้านอาหาร แนะนำ สีลม", "th"),
                ("Thai restaurant Bangkok", "en"),
            ],
        },
        "service": {
            "name": "ธุรกิจบริการ (ตัวอย่าง)",
            "domain": "example-service.com",
            "market": "บริการทำความสะอาด กรุงเทพ",
            "questions": [
                ("บริษัททำความสะอาด ราคาถูก", "th"),
                ("จ้างแม่บ้าน รายวัน กรุงเทพ", "th"),
                ("cleaning service Bangkok", "en"),
            ],
        },
    }
    d = demos.get(industry, demos["property"])
    bid = db.create_brand(tid, d["name"], d["domain"], d["market"])
    for q, lang in d["questions"]:
        db.add_question(bid, q, lang)
    return _redirect(f"/brands/{bid}")


@app.get("/brands/{brand_id}")
def brand_detail(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/app" if _tid(request) else "/login")
    runs = db.list_runs(brand_id)
    last_run = runs[0] if runs else None
    content_count = len(db.list_content(brand_id))
    q_count = len(db.list_questions(brand_id))
    base_url = str(request.base_url).rstrip("/")
    embed_key = brand["embed_key"] or ""
    return templates.TemplateResponse(request, "brand.html",
        {"brand": brand, "last_run": last_run, "content_count": content_count,
         "q_count": q_count, "error": None,
         "last_rank": db.last_rank_check(brand_id),
         "health": db.last_health_check(brand_id),
         "ai": db.last_ai_visibility(brand_id),
         "gsc": db.last_gsc(brand_id),
         "speed": db.last_pagespeed(brand_id),
         "aiserp": db.last_ai_serp(brand_id),
         "autopilot_last": db.last_autopilot_run(brand_id),
         "gaps": db.get_content_gaps(brand_id),
         "wp": db.get_wp_connection(brand_id),
         "embed_js_url": f"{base_url}/e/{embed_key}.js",
         "embed_head_url": f"{base_url}/e/{embed_key}/head.html",
         "embed_head_html": _head_html(brand),
         "embed_llms_url": f"{base_url}/e/{embed_key}/llms.txt",
         "embed_robots_url": f"{base_url}/e/{embed_key}/robots.txt",
         "embed_sitemap_url": f"{base_url}/e/{embed_key}/sitemap.xml",
         "embed_articles_url": f"{base_url}/e/{embed_key}/a/",
         "embed_content_json_url": f"{base_url}/e/{embed_key}/content.json",
         "embed_host": request.url.hostname or "geo.appreview.cloud",
         **_hosting_snippets(f"{base_url}/e/{embed_key}")})


def _hosting_snippets(ev: str) -> dict:
    """สร้าง config สำหรับ hosting ที่ไม่มี nginx (Vercel/Netlify/Next.js) — ev = {base}/e/{key}"""
    SITEMAP_PATH = geo_content.SITEMAP_PATH
    vercel = (
        '{\n'
        '  "rewrites": [\n'
        f'    {{ "source": "/geo",        "destination": "{ev}/a/" }},\n'
        f'    {{ "source": "/geo/:path*", "destination": "{ev}/a/:path*" }},\n'
        f'    {{ "source": "/llms.txt",   "destination": "{ev}/llms.txt" }},\n'
        f'    {{ "source": "{SITEMAP_PATH}", "destination": "{ev}/sitemap.xml" }}\n'
        '  ]\n'
        '}'
    )
    netlify = (
        f"/geo/*     {ev}/a/:splat     200\n"
        f"/llms.txt  {ev}/llms.txt      200\n"
        f"{SITEMAP_PATH}  {ev}/sitemap.xml  200"
    )
    nextjs = (
        "// next.config.js\n"
        "module.exports = {\n"
        "  async rewrites() {\n"
        "    return [\n"
        f"      {{ source: '/geo/:path*', destination: '{ev}/a/:path*' }},\n"
        f"      {{ source: '/llms.txt',   destination: '{ev}/llms.txt' }},\n"
        f"      {{ source: '{SITEMAP_PATH}', destination: '{ev}/sitemap.xml' }},\n"
        "    ]\n"
        "  },\n"
        "}"
    )
    return {"embed_vercel_json": vercel, "embed_netlify": netlify, "embed_next_config": nextjs}


@app.get("/brands/{brand_id}/questions")
def brand_questions(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/app" if _tid(request) else "/login")
    return templates.TemplateResponse(request, "brand_questions.html",
        {"brand": brand, "questions": db.list_questions(brand_id), "error": None})


@app.get("/brands/{brand_id}/monitor")
def brand_monitor(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/app" if _tid(request) else "/login")
    return templates.TemplateResponse(request, "brand_monitor.html",
        {"brand": brand, "runs": db.list_runs(brand_id), "schedule_choices": SCHEDULE_CHOICES})


# ค่าความถี่ auto-run ที่อนุญาต (วัน) — 0 = ปิด
SCHEDULE_CHOICES = [(0, "ปิด (รันเอง)"), (1, "ทุกวัน"), (3, "ทุก 3 วัน"),
                    (7, "ทุกสัปดาห์"), (14, "ทุก 2 สัปดาห์"), (30, "ทุกเดือน")]

# จำนวนร่างคอนเทนต์ auto ต่อสัปดาห์ — 0 = ปิด
AUTO_CONTENT_CHOICES = [(0, "ปิด (เขียนเอง)"), (2, "2 ชิ้น/สัปดาห์"), (3, "3 ชิ้น/สัปดาห์")]

# โหมดเผยแพร่ร่าง auto — -1 = ร่างเท่านั้น, 0 = ทันที, N = หลัง N วัน
AUTO_PUBLISH_CHOICES = [(-1, "ร่างเท่านั้น (review เอง)"), (3, "auto หลัง 3 วัน"),
                        (1, "auto หลัง 1 วัน"), (7, "auto หลัง 7 วัน"), (0, "เผยแพร่ทันที")]


@app.post("/brands/{brand_id}/auto-content")
def set_auto_content(request: Request, brand_id: int, weekly: int = Form(0)):
    if not _brand_for(request, brand_id):
        return _redirect("/login")
    allowed = {n for n, _ in AUTO_CONTENT_CHOICES}
    db.set_auto_content(brand_id, weekly if weekly in allowed else 0)
    return _redirect(f"/brands/{brand_id}/content")


@app.post("/brands/{brand_id}/auto-publish")
def set_auto_publish(request: Request, brand_id: int, days: int = Form(-1)):
    if not _brand_for(request, brand_id):
        return _redirect("/login")
    allowed = {n for n, _ in AUTO_PUBLISH_CHOICES}
    db.set_auto_publish(brand_id, days if days in allowed else -1)
    return _redirect(f"/brands/{brand_id}/content")


@app.post("/brands/{brand_id}/auto-image")
def set_auto_image(request: Request, brand_id: int, on: int = Form(0)):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/login")
    if on and not billing.feature(db.get_tenant(brand["tenant_id"]), "images"):
        return _redirect("/upgrade")  # แผนนี้ใช้รูปอัตโนมัติไม่ได้ → ชวนอัปเกรด
    db.set_auto_image(brand_id, on)
    return _redirect(f"/brands/{brand_id}/content")


@app.post("/brands/{brand_id}/schema-type")
def save_schema_type(request: Request, brand_id: int, schema_type: str = Form("")):
    """เลือก schema.org @type ขององค์กร — ว่าง = ให้ระบบเดาจากชื่อ/ตลาด"""
    if not _brand_for(request, brand_id):
        return _redirect("/login")
    t = (schema_type or "").strip()
    db.set_schema_type(brand_id, t if t in geo_content.SCHEMA_TYPE_VALUES else None)
    return _redirect(f"/brands/{brand_id}/assets?saved=1")


@app.post("/brands/{brand_id}/facts")
def save_brand_facts(request: Request, brand_id: int, facts: str = Form(""), refresh_site: str = Form("")):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/login")
    db.set_brand_facts(brand_id, facts.strip()[:4000])
    if refresh_site:   # ดึงเนื้อหาเว็บจริงใหม่ (grounding)
        try:
            txt = ai_client._fetch_text(geo_content._site_url(brand))
            db.set_brand_site_context(brand_id, (txt or "")[:4000])
        except Exception:
            pass
    return _redirect(f"/brands/{brand_id}/content?saved=1")


def _valid_hm(t: str) -> str:
    """ตรวจ 'HH:MM' → คืนแบบ zero-pad, ถ้าผิดคืน '08:00'"""
    try:
        hh, mm = (t or "").strip().split(":")
        hh, mm = int(hh), int(mm)
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{hh:02d}:{mm:02d}"
    except Exception:
        pass
    return "08:00"


@app.post("/brands/{brand_id}/schedule")
def set_schedule(request: Request, brand_id: int, auto_run_days: int = Form(...), auto_run_time: str = Form("08:00")):
    if not _brand_for(request, brand_id):
        return _redirect("/login")
    allowed = {d for d, _ in SCHEDULE_CHOICES}
    days = auto_run_days if auto_run_days in allowed else 7
    db.set_auto_schedule(brand_id, days, _valid_hm(auto_run_time))
    return _redirect(f"/brands/{brand_id}/monitor")


@app.get("/brands/{brand_id}/content")
def brand_content_list(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/app" if _tid(request) else "/login")
    import datetime as _dt
    questions = db.list_questions(brand_id)
    ok, limit_msg = billing.check(db.get_tenant(brand["tenant_id"]), "content")
    content = db.list_content(brand_id)
    # countdown: ร่าง auto เหลืออีกกี่วันจะเผยแพร่เอง
    pub_days = brand["auto_publish_days"]
    publish_eta = {}
    if pub_days is not None and pub_days >= 0:
        now = _dt.datetime.now()
        for ci in content:
            if ci["status"] == "draft" and ci["source"] == "auto" and ci["created_at"]:
                try:
                    left = pub_days - (now - _dt.datetime.fromisoformat(ci["created_at"])).total_seconds() / 86400
                    publish_eta[ci["id"]] = max(0.0, left)
                except Exception:
                    pass
    # คะแนน AEO ต่อชิ้น + สรุปรวม
    aeo_scores = {ci["id"]: geo_content.aeo_report_item(ci) for ci in content}
    aeo_summary = None
    if aeo_scores:
        vals = list(aeo_scores.values())
        aeo_summary = {
            "avg": round(sum(r["score"] for r in vals) / len(vals), 1),
            "max": vals[0]["max"],
            "full": sum(1 for r in vals if r["score"] == r["max"]),
            "low": sum(1 for r in vals if r["score"] < r["max"]),
        }
    return templates.TemplateResponse(request, "brand_content.html",
        {"brand": brand, "questions": questions, "content": content,
         "can_generate": ok, "limit_msg": limit_msg if not ok else None,
         "publish_eta": publish_eta, "aeo_scores": aeo_scores, "aeo_summary": aeo_summary,
         "can_images": billing.feature(db.get_tenant(brand["tenant_id"]), "images"),
         "auto_content_choices": AUTO_CONTENT_CHOICES, "auto_publish_choices": AUTO_PUBLISH_CHOICES})


@app.get("/brands/{brand_id}/wp")
def brand_wp(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/app" if _tid(request) else "/login")
    return templates.TemplateResponse(request, "brand_wp.html",
        {"brand": brand, "wp": db.get_wp_connection(brand_id), "notice": None})


@app.post("/brands/{brand_id}/delete")
def delete_brand(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/app" if _tid(request) else "/login")
    db.delete_brand(brand_id)
    return _redirect("/app")


@app.post("/brands/{brand_id}/questions")
def add_question(request: Request, brand_id: int, question: str = Form(...), lang: str = Form("th")):
    if not _brand_for(request, brand_id):
        return _redirect("/login")
    if question.strip():
        db.add_question(brand_id, question, lang)
    return _redirect(f"/brands/{brand_id}/questions")


@app.post("/brands/{brand_id}/questions/generate")
def generate_questions(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/login")
    try:
        items = ai_client.generate_questions(
            brand["name"], brand["domain"], brand["market"] or ""
        )
        for item in items:
            if item["question"]:
                db.add_question(brand_id, item["question"], item["lang"])
        added = len(items)
    except Exception as e:
        added = 0
    return _redirect(f"/brands/{brand_id}/questions")


@app.post("/questions/{qid}/delete")
def del_question(request: Request, qid: int, brand_id: int = Form(...)):
    if _brand_for(request, brand_id):
        db.delete_question(qid)
    return _redirect(f"/brands/{brand_id}/questions")


# sync def -> Starlette runs it in a threadpool (ddgs is blocking)
@app.post("/brands/{brand_id}/run")
def run_brand(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/login")
    ok, msg = billing.check(db.get_tenant(brand["tenant_id"]), "runs")
    if not ok:
        return _redirect(f"/brands/{brand_id}")
    summary = geo_worker.run_for_brand(brand_id)
    return _redirect(f"/runs/{summary['run_id']}")


@app.get("/runs/{run_id}")
def run_report(request: Request, run_id: int):
    if not _tid(request):
        return _redirect("/login")
    run = db.get_run(run_id)
    if not run:
        return _redirect("/app")
    brand = _brand_for(request, run["brand_id"])
    if not brand:
        return _redirect("/app")
    results = db.get_results(run_id)
    # aggregate competitors from stored top_domains
    bd = (brand["domain"] or "").lower()
    if bd.startswith("www."):
        bd = bd[4:]
    comp: dict[str, int] = {}
    parsed = []
    for r in results:
        doms = json.loads(r["top_domains"] or "[]")
        for d in doms:
            if d and bd not in d and d not in bd:
                comp[d] = comp.get(d, 0) + 1
        parsed.append({"row": r, "domains": doms})
    top_comp = sorted(comp.items(), key=lambda kv: -kv[1])[:8]
    return templates.TemplateResponse(
        request,
        "run.html",
        {"run": run, "brand": brand, "results": parsed, "top_comp": top_comp},
    )


@app.get("/brands/{brand_id}/progress")
def brand_progress(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/app" if _tid(request) else "/login")
    runs = db.list_runs(brand_id)  # ใหม่→เก่า

    def _pct(r):
        if r["share_of_voice"] is not None:
            return round(r["share_of_voice"] * 100)
        t = r["questions_total"] or 0
        return round((r["brand_hits"] or 0) / t * 100) if t else 0

    # กราฟแท่ง: เก่า→ใหม่, เอา 12 รันล่าสุด
    BW, GAP, left, base, maxh = 38, 16, 44, 150, 110
    bars = []
    for i, r in enumerate(list(reversed(runs))[-12:]):
        h = round(_pct(r) / 100 * maxh)
        bars.append({"x": left + i * (BW + GAP), "y": base - h, "h": h,
                     "pct": _pct(r), "date": (r["started_at"] or "")[:10]})
    chart_w = max(320, left + len(bars) * (BW + GAP) + 10)

    delta, compare = None, []
    if runs:
        baseline, latest = runs[-1], runs[0]
        base_res = {x["question"]: x for x in db.get_results(baseline["id"])}
        late_res = {x["question"]: x for x in db.get_results(latest["id"])}
        for q in db.list_questions(brand_id):
            qt = q["question"]
            bp = bool(base_res[qt]["brand_present"]) if qt in base_res else False
            lp = bool(late_res[qt]["brand_present"]) if qt in late_res else False
            change = "up" if (lp and not bp) else ("down" if (bp and not lp) else "same")
            compare.append({"q": qt, "base": bp, "late": lp, "change": change})
        delta = {
            "base_pct": _pct(baseline), "late_pct": _pct(latest), "diff": _pct(latest) - _pct(baseline),
            "base_label": f'{baseline["brand_hits"]}/{baseline["questions_total"]}',
            "late_label": f'{latest["brand_hits"]}/{latest["questions_total"]}',
            "same_run": baseline["id"] == latest["id"],
        }
    return templates.TemplateResponse(
        request, "progress.html",
        {"brand": brand, "bars": bars, "chart_w": chart_w, "base_y": base, "delta": delta, "compare": compare},
    )


# ---------- ตรวจสุขภาพเว็บ ----------
_checking_brands: set = set()


def _wp_probe(conn) -> dict:
    """ยิงถาม WordPress ของแบรนด์ว่ายังรับงานได้ไหม ด้วยข้อมูลล็อกอินที่เก็บไว้ — ทำที่นี่
    เพราะ site_health ไม่แตะ DB/ความลับ แล้วส่งผลให้ judge_wp ตัดสิน (ไม่เก็บ/ไม่พิมพ์คีย์)"""
    try:
        if conn["mode"] == "connector" and conn["api_key"]:
            r = wp_client.connector_ping(conn["site_url"], wp_client.decrypt(conn["api_key"]))
        else:
            r = wp_client.test_connection(conn["site_url"], conn["auth_user"],
                                          wp_client.decrypt(conn["auth_secret"]))
    except Exception as e:
        r = {"ok": False, "msg": f"ตรวจไม่ได้: {e}"}
    return {"mode": conn["mode"], "ok": bool(r.get("ok")), "msg": r.get("msg", "")}


def run_health_check(brand_id: int, notify_tenant: int | None = None) -> dict:
    """ตรวจเว็บ 1 แบรนด์ + เก็บผล — ใช้ได้ทั้งจาก route, cron และ worker เบื้องหลัง"""
    brand = db.get_brand(brand_id)
    published = [c for c in db.list_content(brand_id) if c["status"] == "published"]
    conn = db.get_wp_connection(brand_id)
    snap = db.last_gsc(brand_id)       # index จริงจาก Search Console ถ้าเคยซิงค์และเชื่อมได้ — แทนการเดาผ่าน Serper
    res = site_health.run_checks(
        geo_content._site_url(brand),
        last_published=db.last_published_at(brand_id),
        n_published=len(published),
        wp=_wp_probe(conn) if conn else None,
        gsc=gsc.health_summary(json.loads(snap["report"])) if snap and snap["report"] else None,
    )
    db.add_health_check(brand_id, res)
    if notify_tenant and not res["ok"]:
        bad = [c["label"] for c in res["checks"] if c["status"] == "fail"][:3]
        db.add_notification(
            notify_tenant,
            f"🩺 {brand['name']}: ตรวจเจอ {res['n_fail']} ปัญหา — " + ", ".join(bad),
            f"/brands/{brand_id}/health", "warn")
    return res


def _health_worker(brand_id: int, tenant_id: int):
    try:
        run_health_check(brand_id, notify_tenant=tenant_id)
    except Exception:
        pass
    finally:
        _checking_brands.discard(brand_id)


def run_ai_visibility(brand_id: int) -> dict:
    """ถาม AI ผู้ช่วยจริงว่าแบรนด์ถูกเอ่ยถึงไหม + เก็บผล

    ยังไม่แจ้งเตือนโดยตั้งใจ — ตอนนี้แทบทุกแบรนด์ยังไม่ถูกเอ่ยถึงเลย ถ้าแจ้งทุกรอบ
    จะกลายเป็นเสียงรบกวนที่ทุกคนเลิกอ่าน เหมือนที่กันไว้ในเช็ค uptime
    ค่อยเพิ่มเมื่อมีฐานให้เทียบว่า "ตกลงจากเดิม" ได้
    """
    brand = db.get_brand(brand_id)
    res = ai_visibility.check_brand(brand, db.list_questions(brand_id))
    db.add_ai_visibility(brand_id, res)
    return res


UPTIME_REMIND_DAYS = 7    # ล่มค้างนานเท่านี้ เตือนซ้ำหนึ่งครั้ง กันเรื่องหล่นหาย


def _uptime_state(brand_id: int) -> dict:
    try:
        return json.loads(db.get_setting(f"uptime:{brand_id}") or "{}")
    except Exception:
        return {}


def _days_since(iso) -> int:
    import datetime as _dt
    try:
        return (_dt.datetime.now() - _dt.datetime.fromisoformat(iso)).days
    except Exception:
        return 0


def _notify_outage(tenant_id, message: str, link: str, type_: str) -> None:
    """เว็บเข้าไม่ได้ทั้งเว็บเป็นเรื่องด่วน — แจ้งทั้งเจ้าของแบรนด์และแอดมิน
    (ถ้าเจ้าของเป็นแอดมินอยู่แล้วไม่ต้องส่งซ้ำ)"""
    owner = db.get_tenant(tenant_id) if tenant_id else None
    if tenant_id:
        db.add_notification(tenant_id, message, link, type_)
    if not (owner and owner["is_admin"]):
        db.notify_admins(message, link, type_)


def run_uptime_check(brand_id: int, notify_tenant: int | None = None) -> dict:
    """เช็คเร็ว ๆ ว่าเว็บยังเปิดได้ไหม — แจ้งเตือนเฉพาะตอนสถานะ "เปลี่ยน" ไม่ใช่ทุกวัน
    ไม่งั้นเว็บที่ล่มยาวจะยิงแจ้งเตือนซ้ำทุกวันจนคนเลิกอ่าน แล้วของจริงก็หลุด"""
    brand = db.get_brand(brand_id)
    res = site_health.probe_uptime(geo_content._site_url(brand))
    prev = _uptime_state(brand_id)
    was_up = prev.get("up", True)   # ครั้งแรกถือว่าเคยปกติ ถ้าล่มอยู่แล้วจะได้แจ้งทันที
    now = db.now()
    link = f"/brands/{brand_id}/health"

    changed = res["up"] != was_up
    since = now if changed else (prev.get("since") or now)
    state = {"up": res["up"], "since": since, "alerted_at": prev.get("alerted_at"),
             "reason": res["reason"], "code": res["code"], "checked_at": now}

    if res["up"]:
        if changed:
            d = _days_since(prev.get("since") or now)
            _notify_outage(notify_tenant,
                           f"🟢 {brand['name']}: เว็บกลับมาเข้าได้แล้ว"
                           + (f" — ล่มไป {d} วัน" if d else ""), link, "info")
    elif changed:
        _notify_outage(notify_tenant,
                       f"🔴 {brand['name']}: เข้าเว็บไม่ได้ — {res['reason']}", link, "warn")
        state["alerted_at"] = now
    elif _days_since(prev.get("alerted_at") or since) >= UPTIME_REMIND_DAYS:
        _notify_outage(notify_tenant,
                       f"🔴 {brand['name']}: ยังเข้าเว็บไม่ได้ ({_days_since(since)} วันแล้ว)"
                       f" — {res['reason']}", link, "warn")
        state["alerted_at"] = now

    db.set_setting(f"uptime:{brand_id}", json.dumps(state, ensure_ascii=False))
    res["was_up"] = was_up
    res["down_since"] = None if res["up"] else since
    return res


@app.post("/brands/{brand_id}/health/run")
def run_brand_health(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/login")
    if brand_id not in _checking_brands:      # ยิงเน็ตหลายสิบครั้ง — กันกดรัว
        _checking_brands.add(brand_id)
        import threading
        threading.Thread(target=_health_worker, args=(brand_id, brand["tenant_id"]), daemon=True).start()
    return _redirect(f"/brands/{brand_id}/health?running=1")


@app.get("/brands/{brand_id}/health")
def brand_health(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/app" if _tid(request) else "/login")
    row = db.last_health_check(brand_id)
    report = json.loads(row["report"]) if row and row["report"] else None
    up = _uptime_state(brand_id)
    # รายงานที่ดึงหน้าแรกไม่ได้ เชื่อไม่ได้ทั้งฉบับ — ข้ออื่นตกตามกันเพราะตัวตรวจเข้าเว็บไม่ได้
    # ไม่ใช่เพราะเว็บมีปัญหาหลายอย่าง ถ้าไม่บอกไว้คนจะไล่แก้ทีละข้อที่ไม่ได้เสีย
    # ตัดสินจากตัวรายงานเอง ไม่ใช่จากเวลาของ uptime state — เว็บอาจล่มมาก่อนที่จะเริ่มเช็ครายวัน
    stale = bool(report and any(c.get("key") == "home" and c.get("status") == "fail"
                                for c in report.get("checks", [])))
    return templates.TemplateResponse(request, "health.html", {
        "brand": brand, "report": report, "uptime": up, "report_stale": stale,
        "down_days": _days_since(up.get("since")) if up.get("up") is False else 0,
        "running": brand_id in _checking_brands or request.query_params.get("running"),
    })


# ---------- การมองเห็นบน AI (ถามผู้ช่วยจริง) ----------
_ai_running: set = set()


def _ai_worker(brand_id: int):
    try:
        run_ai_visibility(brand_id)
    except Exception:
        pass
    finally:
        _ai_running.discard(brand_id)


@app.post("/brands/{brand_id}/ai/run")
def run_brand_ai(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/login")
    if brand_id not in _ai_running:          # ทุกคำถามมีค่าใช้จ่าย — กันกดรัวซ้ำซ้อน
        _ai_running.add(brand_id)
        import threading
        threading.Thread(target=_ai_worker, args=(brand_id,), daemon=True).start()
    return _redirect(f"/brands/{brand_id}/ai?running=1")


@app.get("/brands/{brand_id}/ai")
def brand_ai(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/app" if _tid(request) else "/login")
    row = db.last_ai_visibility(brand_id)
    report = json.loads(row["report"]) if row and row["report"] else None
    matrix, rivals = [], []
    if report:
        from collections import Counter
        # แถว = คำถาม (คงลำดับ) · คอลัมน์ = เจ้า — เรียงใน Python ให้ template เรียบ
        order, cells, links = [], {}, {}
        for r in report["rows"]:
            if r["question"] not in cells:
                order.append(r["question"]); cells[r["question"]] = {}; links[r["question"]] = {}
            cells[r["question"]][r["engine"]] = r["status"]
            # รายงานเก่า (ก่อนเก็บ URL เต็ม) ไม่มี citations — ให้เป็นลิสต์ว่าง หน้าเว็บจะไม่พัง
            links[r["question"]][r["engine"]] = r.get("citations") or []
        matrix = [{"q": qq, "cells": cells[qq], "links": links[qq]} for qq in order]
        cnt = Counter(h for r in report["rows"] for h in (r.get("others") or []))
        rivals = cnt.most_common(8)
    available = ai_visibility.available_engines()
    # คอลัมน์/แถวของเจ้าที่ไม่มีคีย์และไม่เคยมีข้อมูลในรายงานนี้ เป็นแค่ช่อง "ข้าม" เต็มหน้า — เอาออก
    # แล้วบอกเป็นบรรทัดเดียวว่าเจ้าไหนยังไม่ได้ถาม (เจ้าที่เคยถามได้/เคยพังในรายงานยังโชว์ เพื่อให้เห็นประวัติ)
    by = (report or {}).get("by_engine") or {}
    shown = {e: spec for e, spec in ai_visibility.ENGINES.items()
             if e in available or (by.get(e) or {}).get("asked") or (by.get(e) or {}).get("errors")}
    return templates.TemplateResponse(request, "ai.html", {
        "brand": brand, "report": report, "matrix": matrix, "rivals": rivals,
        "engines": shown,
        "hidden": [spec["label"] for e, spec in ai_visibility.ENGINES.items() if e not in shown],
        "ai_live": _ai_live(fresh=True),
        "available": available,
        "routes": {e: ai_visibility.route(e) for e in ai_visibility.ENGINES},
        # เก็บเป็น USD (ค่าจริงจาก API) — บาทเป็นแค่การแสดงผล อัตราตั้งทับได้ด้วย GEO_FX_THB
        "cost_30d": db.ai_cost_30d(brand_id),
        "fx": float(os.getenv("GEO_FX_THB", "33.6")),
        "running": brand_id in _ai_running or request.query_params.get("running"),
    })


_AI_LIVE: dict = {"ts": 0.0, "val": None}


def _ai_live(fresh: bool = False) -> dict:
    """เจ้า AI ที่ถามได้จริงตอนนี้ (มีคีย์) — ข้อความโฆษณาและหัวหน้าใช้ชุดนี้ ไม่ใช่รายชื่อเต็ม 4 เจ้า
    เคยโฆษณา "ChatGPT, Claude, Perplexity" และ "4+ AI engines" ทั้งที่ไม่มีคีย์ Claude — ของที่วัดไม่ได้
    ต้องไม่อยู่ในหน้าขาย · landing เป็นสาธารณะ จึงแคช 5 นาที ไม่ให้ทุก hit ไปอ่าน settings"""
    import time
    if fresh or _AI_LIVE["val"] is None or time.time() - _AI_LIVE["ts"] > 300:
        keys = ai_visibility.available_engines()
        labels = [ai_visibility.ENGINES[e]["label"] for e in keys]
        _AI_LIVE.update(ts=time.time(), val={
            "keys": keys, "labels": labels, "n": len(labels),
            "text": ", ".join(labels) if labels else "ผู้ช่วย AI",     # "ChatGPT, Perplexity, Gemini"
            "short": "/".join(labels),                                  # "ChatGPT/Perplexity/Gemini"
            "missing": [spec["label"] for e, spec in ai_visibility.ENGINES.items() if e not in keys],
        })
    return _AI_LIVE["val"]


# ---------- Google Search Console ----------
def run_gsc_sync(brand_id: int) -> dict:
    """ซิงค์ Search Console ของแบรนด์ + เก็บผล — ส่ง sitemap ถ้ายังไม่ได้ส่ง ตรวจ index รายหน้า ดึงคลิก 28 วัน
    ยังไม่แจ้งเตือน (เหมือน AI) — ค่อยเพิ่มเมื่อมีฐานให้เทียบว่า "ตกจากเดิม" ได้"""
    brand = db.get_brand(brand_id)
    pages = [{"title": it["title"], "url": geo_content.content_url(brand, it)}
             for it in db.list_content(brand_id) if it["status"] == "published"]
    res = gsc.sync_brand(geo_content._site_url(brand), pages)
    db.add_gsc_snapshot(brand_id, res)
    return res


_gsc_running: set = set()


def _gsc_worker(brand_id: int):
    try:
        run_gsc_sync(brand_id)
    except Exception:
        pass
    finally:
        _gsc_running.discard(brand_id)


@app.post("/brands/{brand_id}/gsc/run")
def run_brand_gsc(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/login")
    if brand_id not in _gsc_running:         # ตรวจรายหน้าหลายสิบครั้งต่อรอบ — กันกดรัว
        _gsc_running.add(brand_id)
        import threading
        threading.Thread(target=_gsc_worker, args=(brand_id,), daemon=True).start()
    return _redirect(f"/brands/{brand_id}/gsc?running=1")


@app.get("/brands/{brand_id}/gsc")
def brand_gsc(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/app" if _tid(request) else "/login")
    row = db.last_gsc(brand_id)
    report = json.loads(row["report"]) if row and row["report"] else None
    return templates.TemplateResponse(request, "gsc.html", {
        "brand": brand, "report": report,
        "info": gsc.key_info(),                     # อีเมลบัญชีบริการที่ลูกค้าต้องเอาไปเพิ่ม — ไม่มีส่วนลับ
        "labels": gsc.GROUP_LABELS,
        "perm_label": gsc.PERM_LABELS.get(report.get("permission"), report.get("permission")) if report else "",
        "running": brand_id in _gsc_running or request.query_params.get("running"),
    })


# ---------- Page Speed (PageSpeed Insights) ----------
def run_pagespeed(brand_id: int) -> dict:
    """สแกนความเร็วหน้าแรก mobile + desktop แล้วเก็บผล — ใช้จาก route, cron (--speed) และ autopilot"""
    brand = db.get_brand(brand_id)
    res = pagespeed.scan(geo_content._site_url(brand))
    db.add_pagespeed(brand_id, res)
    return res


_speed_running: set = set()


def _speed_worker(brand_id: int):
    try:
        run_pagespeed(brand_id)
    except Exception:
        pass
    finally:
        _speed_running.discard(brand_id)


@app.post("/brands/{brand_id}/speed/run")
def run_brand_speed(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/login")
    if brand_id not in _speed_running:        # Lighthouse ใช้เวลาเป็นนาที — กันกดรัว
        _speed_running.add(brand_id)
        import threading
        threading.Thread(target=_speed_worker, args=(brand_id,), daemon=True).start()
    return _redirect(f"/brands/{brand_id}/speed?running=1")


@app.get("/brands/{brand_id}/speed")
def brand_speed(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/app" if _tid(request) else "/login")
    row = db.last_pagespeed(brand_id)
    report = json.loads(row["report"]) if row and row["report"] else None
    return templates.TemplateResponse(request, "speed.html", {
        "brand": brand, "report": report, "history": db.pagespeed_history(brand_id),
        "categories": pagespeed.CATEGORIES, "grade": pagespeed.grade,
        "running": brand_id in _speed_running or request.query_params.get("running"),
    })


# ---------- Google AI Overview / AI Mode (SerpApi) ----------
def run_ai_serp(brand_id: int) -> dict:
    """ถาม AI Overview + AI Mode ของ Google ด้วยคำถามเป้าหมาย แล้วเก็บผล — ไม่มีคีย์ SerpApi = skip"""
    brand = db.get_brand(brand_id)
    res = ai_serp.check_brand(brand, db.list_questions(brand_id))
    db.add_ai_serp(brand_id, res)
    return res


_aiserp_running: set = set()


def _aiserp_worker(brand_id: int):
    try:
        run_ai_serp(brand_id)
    except Exception:
        pass
    finally:
        _aiserp_running.discard(brand_id)


@app.post("/brands/{brand_id}/ai-serp/run")
def run_brand_ai_serp(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/login")
    if brand_id not in _aiserp_running:       # ทุกคำถามกินโควตา SerpApi — กันกดรัว
        _aiserp_running.add(brand_id)
        import threading
        threading.Thread(target=_aiserp_worker, args=(brand_id,), daemon=True).start()
    return _redirect(f"/brands/{brand_id}/ai-serp?running=1")


@app.get("/brands/{brand_id}/ai-serp")
def brand_ai_serp(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/app" if _tid(request) else "/login")
    row = db.last_ai_serp(brand_id)
    report = json.loads(row["report"]) if row and row["report"] else None
    matrix, rivals = [], []
    if report:
        from collections import Counter
        order, cells = [], {}
        for r in report["rows"]:
            if r["question"] not in cells:
                order.append(r["question"]); cells[r["question"]] = {}
            cells[r["question"]][r["kind"]] = r
        matrix = [{"q": qq, "cells": cells[qq]} for qq in order]
        rivals = Counter(h for r in report["rows"] for h in (r.get("others") or [])).most_common(8)
    return templates.TemplateResponse(request, "aiserp.html", {
        "brand": brand, "report": report, "matrix": matrix, "rivals": rivals,
        "kinds": ai_serp.KINDS, "available": ai_serp.available(),
        "running": brand_id in _aiserp_running or request.query_params.get("running"),
    })


# ---------- รายงานสรุป + ส่งออกไฟล์ ----------
@app.get("/brands/{brand_id}/report")
def brand_report(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/app" if _tid(request) else "/login")
    data = report.collect(brand_id)
    return templates.TemplateResponse(request, "report.html", {"d": data, "pdf": False, "exports": report.EXPORTS})


@app.get("/brands/{brand_id}/report.pdf")
def brand_report_pdf(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/app" if _tid(request) else "/login")
    data = report.collect(brand_id)
    try:
        pdf = report.to_pdf(report.render_html(data, pdf=True))
    except Exception as e:
        # บริการ PDF (container pdf) ล่ม — ให้คนยังเอารายงานไปได้ด้วย Print ของเบราว์เซอร์
        return templates.TemplateResponse(request, "report.html",
            {"d": data, "pdf": False, "exports": report.EXPORTS,
             "pdf_error": f"สร้าง PDF ไม่ได้ตอนนี้ ({type(e).__name__}) — ใช้ Print → Save as PDF ของเบราว์เซอร์แทนได้"}, status_code=503)
    fname = f"geo-report-{(brand['name'] or 'brand').replace(' ', '_')[:30]}-{data['period']['to']}.pdf"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{__import__('urllib.parse').parse.quote(fname)}"})


@app.get("/brands/{brand_id}/export/{kind}.csv")
def brand_export_csv(request: Request, brand_id: int, kind: str):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/app" if _tid(request) else "/login")
    ex = report.export(brand_id, kind)
    if not ex:
        return PlainTextResponse("ไม่รู้จักชนิดไฟล์ — ใช้ " + ", ".join(report.EXPORTS), status_code=404)
    fname, header, rows = ex
    return Response(report.csv_bytes(header, rows), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{__import__('urllib.parse').parse.quote(fname)}"})


# ---------- ถามข้อมูลแบรนด์ (analytics chat) ----------
@app.get("/brands/{brand_id}/chat")
def brand_chat(request: Request, brand_id: int, error: str = ""):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/app" if _tid(request) else "/login")
    return templates.TemplateResponse(request, "chat.html", {
        "brand": brand, "messages": db.list_chat_messages(brand_id), "suggestions": analytics_chat.SUGGESTIONS,
        "llm_ready": bool(ai_client.API_KEY), "error": error or None,
    })


@app.post("/brands/{brand_id}/chat")
def brand_chat_ask(request: Request, brand_id: int, message: str = Form(...)):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/login")
    msg = (message or "").strip()[:600]
    if not msg:
        return _redirect(f"/brands/{brand_id}/chat")
    history = [dict(m) for m in db.list_chat_messages(brand_id, limit=analytics_chat.HISTORY_TURNS)]
    try:
        reply = analytics_chat.answer(report.collect(brand_id), history, msg)
    except Exception as e:
        return _redirect(f"/brands/{brand_id}/chat?error=" + __import__("urllib.parse").parse.quote(f"ถามโมเดลไม่สำเร็จ: {type(e).__name__}: {str(e)[:120]}"))
    db.add_chat_message(brand_id, "user", msg)
    db.add_chat_message(brand_id, "assistant", reply or "(โมเดลไม่ตอบ)")
    return _redirect(f"/brands/{brand_id}/chat")


@app.post("/brands/{brand_id}/chat/clear")
def brand_chat_clear(request: Request, brand_id: int):
    if _brand_for(request, brand_id):
        db.clear_chat(brand_id)
    return _redirect(f"/brands/{brand_id}/chat")


# ---------- Autopilot ----------
def _autopilot_content(brand, n: int, mode: str) -> list:
    """เขียนคอนเทนต์ให้คำถามที่ยังไม่มีคอนเทนต์ (critical) สูงสุด n ชิ้น แล้วเผยแพร่ตามโหมด — เคารพโควตาแพ็กเกจ"""
    gaps = db.get_content_gaps(brand["id"])
    todo = [g for g in gaps["gaps"] if g["level"] == "critical"][:n]
    tenant = db.get_tenant(brand["tenant_id"])
    conn = db.get_wp_connection(brand["id"])
    site = geo_content._site_url(brand)
    b = _brand_grounded(brand)
    made = []
    for g in todo:
        ok, msg = billing.check(tenant, "content")
        if not ok:
            made.append({"question": g["question"], "status": "quota", "msg": msg})
            break
        lang = g["lang"] or "th"
        data = geo_content.generate_content(b, g["question"], lang, geo_content.pick_ctype(g["question"]))
        cid = db.create_content_item(brand["id"], g["question_id"], lang, data["title"], data["meta_title"],
                                     data["meta_desc"], data["body_md"], data["schema_json"], "autopilot")
        if b["auto_image"]:
            _attach_image(b, cid, g["question"])
        status = "draft"
        if mode == "publish" and billing.feature(tenant, "auto_publish"):
            item = db.get_content(cid)
            if conn:
                res = _publish_item(item, conn, brand, status="publish")
                status = "published" if res["ok"] else f"เผยแพร่ไม่สำเร็จ: {res['msg'][:80]}"
            else:
                db.mark_content_published(cid, None, f"{site}/geo/{cid}")
                status = "published"
        made.append({"question": g["question"], "id": cid, "title": data["title"], "status": status})
    return made


def _autopilot_report(brand, log) -> str:
    """รายงานสรุป → อีเมลเจ้าของแบรนด์พร้อม PDF ถ้ามี SMTP · ไม่มีก็บอกว่าดูในระบบ"""
    tenant = db.get_tenant(brand["tenant_id"])
    rep = report.collect(brand["id"])
    if not (mailer.config() and tenant and tenant["email"]):
        return "ไม่มี SMTP — ดูรายงานได้ที่หน้า รายงาน & ส่งออก"
    vals = campaigns.values_for(rep, tenant["name"] or "", BASE_URL)
    subject = campaigns.render_body("Autopilot รายสัปดาห์ — {brand}", vals)
    body_md = campaigns.render_body(campaigns.DEFAULT_BODY, vals)
    done = [s for s in log["steps"] if s["status"] == "ok" and s["step"] not in ("report", "notify")]
    body_md += "\n\n**Autopilot รอบนี้ทำ**\n" + "\n".join(f"- {s['label']}: {s['detail']}" for s in done)
    attachments = []
    try:
        attachments.append((f"geo-report-{brand['name'][:30].replace(' ', '_')}-{rep['period']['to']}.pdf",
                            report.to_pdf(report.render_html(rep, pdf=True)), "application/pdf"))
    except Exception:
        pass
    mailer.send(tenant["email"], subject, mailer.wrap_html(subject, wp_client.md_to_html(body_md), f"Autopilot ของ เจอ.AI · {BASE_URL}/brands/{brand['id']}/autopilot"),
                body_md, attachments)
    db.add_email_log(None, tenant["id"], tenant["email"], brand["id"], True, "")
    return f"ส่งอีเมลรายงานถึง {tenant['email']}" + (" พร้อม PDF" if attachments else "")


def _autopilot_notify(brand, log) -> bool:
    db.add_notification(brand["tenant_id"], autopilot.notify_text(brand, log), f"/brands/{brand['id']}/autopilot",
                        "warn" if log.get("errors") else "info")
    return True


def run_autopilot(brand_id: int) -> dict:
    """รอบ Autopilot ของแบรนด์ — ต่อสายทุกขั้นเข้ากับฟังก์ชันจริง แล้วเก็บ log"""
    brand = dict(db.get_brand(brand_id))
    last = {
        "sov": brand.get("last_run_at"),
        "rank": db.last_rank_check(brand_id),
        "gsc": (db.last_gsc(brand_id) or {}).get("synced_at") if db.last_gsc(brand_id) else None,
        "health": (db.last_health_check(brand_id) or {}).get("checked_at") if db.last_health_check(brand_id) else None,
        "speed": (db.last_pagespeed(brand_id) or {}).get("checked_at") if db.last_pagespeed(brand_id) else None,
        "ai": (db.last_ai_visibility(brand_id) or {}).get("checked_at") if db.last_ai_visibility(brand_id) else None,
        "aiserp": (db.last_ai_serp(brand_id) or {}).get("checked_at") if db.last_ai_serp(brand_id) else None,
    }
    actions = {
        "sov": geo_worker.run_for_brand, "rank": geo_worker.check_rank_for_brand, "gsc": run_gsc_sync,
        "health": lambda bid: run_health_check(bid, notify_tenant=None), "speed": run_pagespeed,
        "ai": run_ai_visibility, "aiserp": run_ai_serp,
        "content": _autopilot_content, "report": _autopilot_report, "notify": _autopilot_notify,
    }
    n = brand.get("autopilot_content") if brand.get("autopilot_content") is not None else 1
    log = autopilot.run(brand, actions, last, content_n=int(n), mode=brand.get("autopilot_mode") or "draft")
    db.add_autopilot_run(brand_id, log)
    return log


_autopilot_running: set = set()


def _autopilot_worker(brand_id: int):
    try:
        run_autopilot(brand_id)
    except Exception:
        pass
    finally:
        _autopilot_running.discard(brand_id)


@app.post("/brands/{brand_id}/autopilot/run")
def run_brand_autopilot(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/login")
    if brand_id not in _autopilot_running:
        _autopilot_running.add(brand_id)
        import threading
        threading.Thread(target=_autopilot_worker, args=(brand_id,), daemon=True).start()
    return _redirect(f"/brands/{brand_id}/autopilot?running=1")


@app.post("/brands/{brand_id}/autopilot/settings")
def save_autopilot(request: Request, brand_id: int, on: str = Form(""), mode: str = Form("draft"), content_n: int = Form(1)):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/login")
    db.set_autopilot(brand_id, bool(on), mode if mode in autopilot.MODES else "draft", max(0, min(3, content_n)))
    return _redirect(f"/brands/{brand_id}/autopilot?saved=1")


@app.get("/brands/{brand_id}/autopilot")
def brand_autopilot(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/app" if _tid(request) else "/login")
    runs = db.list_autopilot_runs(brand_id, 10)
    logs = {r["id"]: json.loads(r["report"]) for r in runs if r["report"]}
    return templates.TemplateResponse(request, "autopilot.html", {
        "brand": brand, "runs": runs, "logs": logs, "modes": autopilot.MODES,
        "can_publish": billing.feature(db.get_tenant(brand["tenant_id"]), "auto_publish"),
        "saved": request.query_params.get("saved"),
        "running": brand_id in _autopilot_running or request.query_params.get("running"),
    })


# ---------- Google rank tracking ----------
@app.post("/brands/{brand_id}/rank/run")
def run_rank(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/login")
    ok, _msg = billing.check(db.get_tenant(brand["tenant_id"]), "runs")
    if not ok:
        return _redirect(f"/brands/{brand_id}")
    geo_worker.check_rank_for_brand(brand_id)
    return _redirect(f"/brands/{brand_id}/rank")


@app.get("/brands/{brand_id}/rank")
def brand_rank(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/app" if _tid(request) else "/login")
    batches = db.rank_batches(brand_id)          # ใหม่→เก่า
    rows, delta = [], None
    if batches:
        latest = db.rank_results_at(brand_id, batches[0]["checked_at"])
        prev = {r["question"]: r["position"] for r in
                db.rank_results_at(brand_id, batches[1]["checked_at"])} if len(batches) > 1 else {}
        for r in latest:
            before, after = prev.get(r["question"]), r["position"]
            if before is None and after is None:
                change, diff = "same", None
            elif before is None:
                change, diff = "new", None           # เพิ่งติดอันดับครั้งแรก
            elif after is None:
                change, diff = "lost", None          # หลุดจาก top N
            else:
                diff = before - after                # +ve = อันดับดีขึ้น (เลขน้อยลง)
                change = "up" if diff > 0 else ("down" if diff < 0 else "same")
            rows.append({"q": r["question"], "pos": after, "url": r["url"],
                         "change": change, "diff": abs(diff) if diff else None})
        b0 = batches[0]
        delta = {"checked_at": b0["checked_at"], "engine": latest[0]["engine"] if latest else "",
                 "total": b0["total"], "ranked": b0["ranked"] or 0,
                 "avg_pos": round(b0["avg_pos"], 1) if b0["avg_pos"] else None}

    # แนวโน้ม: จำนวนคำถามที่ติด top N ต่อรอบ (เก่า→ใหม่)
    BW, GAP, left, base, maxh = 38, 16, 44, 150, 110
    bars = []
    for i, b in enumerate(list(reversed(batches))[-12:]):
        pct = round((b["ranked"] or 0) / b["total"] * 100) if b["total"] else 0
        h = round(pct / 100 * maxh)
        bars.append({"x": left + i * (BW + GAP), "y": base - h, "h": h,
                     "pct": pct, "date": (b["checked_at"] or "")[:10]})
    return templates.TemplateResponse(
        request, "rank.html",
        {"brand": brand, "rows": rows, "delta": delta, "bars": bars,
         "chart_w": max(320, left + len(bars) * (BW + GAP) + 10), "base_y": base,
         "rank_limit": geo_worker.RANK_LIMIT, "rank_pages": geo_worker.RANK_PAGES,
         "is_google": geo_worker.rank_backend() == "serper"},
    )


# ---------- content (execution layer — Phase A) ----------
@app.post("/brands/{brand_id}/content")
def gen_content(request: Request, brand_id: int, question_id: int = Form(...), lang: str = Form("th"),
                ctype: str = Form("qa")):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/login")
    ok, msg = billing.check(db.get_tenant(brand["tenant_id"]), "content")
    if not ok:
        return _redirect(f"/brands/{brand_id}/content")
    q = next((row for row in db.list_questions(brand_id) if row["id"] == question_id), None)
    if not q:
        return _redirect(f"/brands/{brand_id}/content")
    if ctype == "auto":  # ให้ระบบเลือกรูปแบบ AEO ตามลักษณะคำถาม
        ctype = geo_content.pick_ctype(q["question"])
    brand = _brand_grounded(brand)
    data = geo_content.generate_content(brand, q["question"], lang, ctype)
    cid = db.create_content_item(
        brand_id, question_id, lang, data["title"], data["meta_title"],
        data["meta_desc"], data["body_md"], data["schema_json"], data["source"],
    )
    if brand["auto_image"]:
        _attach_image(brand, cid, q["question"])
    return _redirect(f"/content/{cid}")


_fixing_brands = set()  # กันกดซ้ำระหว่างซ่อม batch อยู่


def _fix_all_worker(brand_id: int, tenant_id: int, publish_drafts: bool):
    """ซ่อม AEO ทุกชิ้นที่ยังไม่เต็ม (regenerate) — รันเบื้องหลัง แล้วแจ้งเตือนเมื่อเสร็จ"""
    fixed = published = 0
    try:
        brand = _brand_grounded(db.get_brand(brand_id))
        conn = db.get_wp_connection(brand_id)
        site = geo_content._site_url(brand)
        for it in db.list_content(brand_id):
            rep = geo_content.aeo_report_item(it)
            if rep["score"] >= rep["max"]:
                continue
            q = next((r for r in db.list_questions(brand_id) if r["id"] == it["question_id"]), None)
            question = q["question"] if q else (it["title"] or "")
            try:
                data = geo_content.generate_content(brand, question, it["lang"] or "th",
                                                    geo_content.pick_ctype(question))
                db.update_content(it["id"], data["title"], data["meta_title"], data["meta_desc"],
                                  data["body_md"], data["schema_json"], data["source"])
                if brand["auto_image"]:
                    _attach_image(brand, it["id"], question)
                fixed += 1
                do_pub = (it["status"] == "published") or publish_drafts
                if do_pub:
                    if conn:
                        _publish_item(db.get_content(it["id"]), conn, brand, status="publish")
                    else:
                        db.mark_content_published(it["id"], it["wp_post_id"],
                                                  it["wp_link"] or f"{site}/geo/{it['id']}")
                    published += 1
            except Exception:
                pass
        db.add_notification(
            tenant_id,
            f"🔧 ซ่อม AEO เสร็จแล้ว — ปรับปรุง {fixed} ชิ้น" + (f" · เผยแพร่ {published} ชิ้น" if published else ""),
            f"/brands/{brand_id}/content", "info")
    finally:
        _fixing_brands.discard(brand_id)


@app.post("/brands/{brand_id}/content/fix-all")
def fix_all_content(request: Request, brand_id: int, publish_drafts: str = Form("")):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/app" if _tid(request) else "/login")
    if brand_id not in _fixing_brands:
        _fixing_brands.add(brand_id)
        import threading
        threading.Thread(target=_fix_all_worker,
                         args=(brand_id, brand["tenant_id"], bool(publish_drafts)),
                         daemon=True).start()
    return _redirect(f"/brands/{brand_id}/content?fixing=1")


@app.post("/content/{content_id}/regenerate")
def regenerate_content(request: Request, content_id: int):
    item = db.get_content(content_id)
    if not item:
        return _redirect("/app")
    brand = _brand_for(request, item["brand_id"])
    if not brand:
        return _redirect("/app" if _tid(request) else "/login")
    q = next((r for r in db.list_questions(item["brand_id"]) if r["id"] == item["question_id"]), None)
    question = q["question"] if q else (item["title"] or "")
    lang = item["lang"] or "th"
    brand = _brand_grounded(brand)
    # เขียนทับด้วยฟอร์แมต AEO เต็ม (เลือก comparison/listicle ตามคำถามอัตโนมัติ) — ไม่คิดโควตา (แก้ของเดิม)
    data = geo_content.generate_content(brand, question, lang, geo_content.pick_ctype(question))
    db.update_content(content_id, data["title"], data["meta_title"], data["meta_desc"],
                      data["body_md"], data["schema_json"], data["source"])
    if brand["auto_image"]:
        _attach_image(brand, content_id, question)
    # ถ้าเคยเผยแพร่แล้ว → sync เวอร์ชัน public (WP อัปเดตโพสต์เดิม / hosted อัปเดตสดอยู่แล้ว)
    if item["status"] == "published":
        conn = db.get_wp_connection(item["brand_id"])
        if conn:
            try:
                _publish_item(db.get_content(content_id), conn, brand, status="publish")
            except Exception:
                pass
    return _redirect(f"/content/{content_id}")


@app.get("/content/{content_id}")
def view_content(request: Request, content_id: int):
    item = db.get_content(content_id)
    if not item:
        return _redirect("/app")
    brand = _brand_for(request, item["brand_id"])
    if not brand:
        return _redirect("/app" if _tid(request) else "/login")
    return templates.TemplateResponse(
        request, "content.html",
        {"item": item, "brand": brand, "wp": db.get_wp_connection(item["brand_id"]), "notice": None,
         "aeo": geo_content.aeo_report_item(item),
         "hosted_url": f"{geo_content._site_url(brand)}/geo/{item['id']}"},
    )


@app.post("/content/{content_id}/delete")
def del_content(request: Request, content_id: int):
    item = db.get_content(content_id)
    if item and _brand_for(request, item["brand_id"]):
        db.delete_content(content_id)
        return _redirect(f"/brands/{item['brand_id']}")
    return _redirect("/app")


@app.get("/brands/{brand_id}/assets")
def brand_assets(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/app" if _tid(request) else "/login")
    items = db.list_content(brand_id)
    return templates.TemplateResponse(
        request,
        "assets.html",
        {"brand": brand, "llms_txt": geo_content.llms_txt(brand, items),
         "robots": geo_content.robots_snippet(brand), "org_schema": geo_content.org_schema(brand),
         "sitemap": geo_content.sitemap_xml(brand, items),
         "sitemap_path": geo_content.SITEMAP_PATH,
         "sitemap_src": f"{str(request.base_url).rstrip('/')}/e/{brand['embed_key'] or ''}/sitemap.xml",
         "published_count": len([i for i in items if i["status"] == "published"]),
         "wp": db.get_wp_connection(brand_id),
         "schema_types": geo_content.SCHEMA_TYPES,
         "schema_type": geo_content.schema_type_of(brand),
         "schema_type_is_guess": not brand["schema_type"],
         "saved": request.query_params.get("saved")},
    )


# ---------- WordPress publish (execution layer — Phase B) ----------
@app.post("/brands/{brand_id}/wp")
def save_wp(request: Request, brand_id: int, site_url: str = Form(...), user: str = Form(""), app_password: str = Form(""), api_key: str = Form("")):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/login")
    api_key = (api_key or "").strip()
    if api_key:
        test = wp_client.connector_ping(site_url, api_key)
        mode = "connector"
        if test["ok"]:
            # auto: push org schema + llms.txt + เปิด AI bots เข้าปลั๊กอินทันที
            org = geo_content.org_schema(brand)
            llms = geo_content.llms_txt(brand, db.list_content(brand_id))
            push = wp_client.connector_push_settings(site_url, api_key, org_schema=org, llms_txt=llms, ai_bots=True)
            test["msg"] = test["msg"] + " · " + push["msg"]
    elif user.strip() and app_password.strip():
        test = wp_client.test_connection(site_url, user, app_password)
        mode = "rest"
    else:
        notice = {"ok": False, "msg": "กรอก Connector API Key หรือ (ชื่อผู้ใช้ + Application Password) อย่างใดอย่างหนึ่ง"}
        return templates.TemplateResponse(request, "brand_wp.html",
            {"brand": brand, "wp": db.get_wp_connection(brand_id), "notice": notice})
    db.upsert_wp_connection(
        brand_id, site_url.strip(), user.strip(), wp_client.encrypt(app_password),
        test["msg"], mode, wp_client.encrypt(api_key) if api_key else None,
    )
    return _redirect(f"/brands/{brand_id}/wp")


@app.post("/brands/{brand_id}/wp/delete")
def del_wp(request: Request, brand_id: int):
    if _brand_for(request, brand_id):
        db.delete_wp_connection(brand_id)
    return _redirect(f"/brands/{brand_id}/wp")


@app.post("/brands/{brand_id}/wp/sync")
def sync_wp(request: Request, brand_id: int):
    """ซิงค์ org schema + llms.txt เข้าปลั๊กอินอีกครั้ง + re-ping อัปเดตเวอร์ชัน โดยใช้ API key ที่เก็บไว้"""
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/login")
    conn = db.get_wp_connection(brand_id)
    if conn and conn["mode"] == "connector" and conn["api_key"]:
        key = wp_client.decrypt(conn["api_key"])
        ping = wp_client.connector_ping(conn["site_url"], key)
        if ping["ok"]:
            push = wp_client.connector_push_settings(
                conn["site_url"], key,
                org_schema=geo_content.org_schema(brand),
                llms_txt=geo_content.llms_txt(brand, db.list_content(brand_id)),
                ai_bots=True,
            )
            # อัปเดตสถานะที่เก็บไว้ให้สะท้อนเวอร์ชันล่าสุด + ผลซิงค์ (ค่า encrypted คงเดิม)
            db.upsert_wp_connection(
                brand_id, conn["site_url"], conn["auth_user"], conn["auth_secret"],
                ping["msg"] + " · " + push["msg"], "connector", conn["api_key"],
            )
            notice = {"ok": push["ok"], "msg": push["msg"]}
        else:
            notice = {"ok": False, "msg": ping["msg"]}
    else:
        notice = {"ok": False, "msg": "ต้องเชื่อมต่อแบบ Connector ก่อนจึงจะซิงค์ได้"}
    return templates.TemplateResponse(request, "brand_wp.html",
        {"brand": brand, "wp": db.get_wp_connection(brand_id), "notice": notice})


@app.post("/content/{content_id}/publish")
def publish_content(request: Request, content_id: int, status: str = Form("draft")):
    item = db.get_content(content_id)
    if not item:
        return _redirect("/app")
    brand = _brand_for(request, item["brand_id"])
    if not brand:
        return _redirect("/app" if _tid(request) else "/login")
    wp_status = "publish" if status == "publish" else "draft"  # validate
    conn = db.get_wp_connection(item["brand_id"])
    if not conn:
        # เว็บ dev (ไม่มี WP) → เผยแพร่ = ขึ้นหน้า hosted ของแพลตฟอร์ม
        if wp_status == "publish":
            site = geo_content._site_url(brand)
            db.mark_content_published(content_id, None, f"{site}/geo/{content_id}")
            item = db.get_content(content_id)
            notice = {"ok": True, "msg": f"เผยแพร่บนหน้า hosted แล้ว → {site}/geo/{content_id}"}
        else:
            notice = {"ok": False, "msg": "เว็บนี้ไม่ได้เชื่อม WordPress — กด \"เผยแพร่ (public)\" เพื่อขึ้นหน้า hosted"}
    else:
        res = _publish_item(item, conn, brand, status=wp_status)
        if res["ok"]:
            item = db.get_content(content_id)
        notice = {"ok": res["ok"], "msg": res["msg"]}
    return templates.TemplateResponse(
        request, "content.html",
        {"item": item, "brand": brand, "wp": conn, "notice": notice,
         "aeo": geo_content.aeo_report_item(item),
         "hosted_url": f"{geo_content._site_url(brand)}/geo/{item['id']}"},
    )


# ---------- Embed (public — ไม่ต้อง login) ----------
def _published_faqs(brand) -> list[dict]:
    """schema (FAQPage/HowTo) ของคอนเทนต์ที่เผยแพร่แล้ว — รองรับทั้ง dict เดิม และ array ใหม่"""
    out = []
    for item in db.list_content(brand["id"]):
        if item["status"] == "published" and item.get("schema_json"):
            try:
                s = json.loads(item["schema_json"])
                for o in (s if isinstance(s, list) else [s]):
                    if isinstance(o, dict) and o.get("@type") in ("FAQPage", "HowTo", "ItemList"):
                        out.append(o)
            except Exception:
                pass
    return out


def _head_html(brand) -> str:
    """บล็อก <script type=application/ld+json> สำเร็จรูปสำหรับวางใน <head> ฝั่งเซิร์ฟเวอร์"""
    blocks = []
    org = geo_content.org_schema(brand)  # indent=2 อยู่แล้ว
    if org:
        blocks.append(f'<script type="application/ld+json">\n{org}\n</script>')
    for s in _published_faqs(brand):
        pretty = json.dumps(s, ensure_ascii=False, indent=2)
        blocks.append(f'<script type="application/ld+json">\n{pretty}\n</script>')
    return "\n".join(blocks)


@app.api_route("/e/{key}.js", methods=["GET", "HEAD"])
def embed_js(key: str):
    brand = db.get_brand_by_embed_key(key)
    if not brand:
        return Response("/* brand not found */", media_type="application/javascript")
    org_schema = geo_content.org_schema(brand)
    faq_schemas = [json.dumps(s, ensure_ascii=False) for s in _published_faqs(brand)]
    org_json = json.dumps(json.loads(org_schema), ensure_ascii=False) if org_schema else "{}"
    faq_block = "\n".join(
        f'  _injectSchema({s});' for s in faq_schemas
    )
    js = f"""(function(){{
  function _injectSchema(data){{
    var s=document.createElement('script');
    s.type='application/ld+json';
    s.textContent=JSON.stringify(data);
    (document.head||document.documentElement).appendChild(s);
  }}
  _injectSchema({org_json});
{faq_block}
  /* เจอ.AI GEO Embed — {brand['name']} | อัปเดตอัตโนมัติ */
}})();"""
    return Response(js, media_type="application/javascript",
                    headers={"Cache-Control": "public, max-age=3600"})


# FastAPI ไม่เติม HEAD ให้ route GET อัตโนมัติ (ต่างจาก Starlette) — endpoint สาธารณะ
# ต้องระบุเอง ไม่งั้น crawler ที่ยิง HEAD ตรวจก่อน (รวม Search Console) เจอ 405
@app.api_route("/e/{key}/llms.txt", methods=["GET", "HEAD"])
def embed_llms(key: str, dl: int = 0):
    brand = db.get_brand_by_embed_key(key)
    if not brand:
        return PlainTextResponse("# not found", status_code=404)
    items = db.list_content(brand["id"])
    txt = geo_content.llms_txt(brand, items)
    headers = {"Cache-Control": "public, max-age=3600"}
    if dl:
        headers["Content-Disposition"] = 'attachment; filename="llms.txt"'
    return PlainTextResponse(txt, headers=headers)


@app.api_route("/e/{key}/robots.txt", methods=["GET", "HEAD"])
def embed_robots(key: str, dl: int = 0):
    brand = db.get_brand_by_embed_key(key)
    if not brand:
        return PlainTextResponse("", status_code=404)
    headers = {"Cache-Control": "public, max-age=86400"}
    if dl:
        headers["Content-Disposition"] = 'attachment; filename="robots.txt"'
    return PlainTextResponse(geo_content.robots_snippet(brand), headers=headers)


@app.api_route("/e/{key}/sitemap.xml", methods=["GET", "HEAD"])
def embed_sitemap(key: str, dl: int = 0):
    """XML sitemap — ต้อง rewrite มาที่ {domain}/geo-sitemap.xml ถึงจะใช้ได้จริง (Google ไม่รับข้ามโดเมน)"""
    brand = db.get_brand_by_embed_key(key)
    if not brand:
        return Response("", media_type="application/xml", status_code=404)
    xml = geo_content.sitemap_xml(brand, db.list_content(brand["id"]))
    headers = {"Cache-Control": "public, max-age=3600", "Access-Control-Allow-Origin": "*"}
    if dl:
        headers["Content-Disposition"] = 'attachment; filename="geo-sitemap.xml"'
    return Response(xml, media_type="application/xml; charset=utf-8", headers=headers)


@app.api_route("/e/{key}/head.html", methods=["GET", "HEAD"])
def embed_head(key: str):
    """JSON-LD ดิบสำหรับ dev วางใน <head> ฝั่งเซิร์ฟเวอร์ (หรือให้เซิร์ฟเวอร์ fetch มา inline)"""
    brand = db.get_brand_by_embed_key(key)
    if not brand:
        return Response("<!-- not found -->", media_type="text/html", status_code=404)
    return Response(_head_html(brand), media_type="text/html; charset=utf-8",
                    headers={"Cache-Control": "public, max-age=3600",
                             "Access-Control-Allow-Origin": "*"})


# ---------- Hosted article pages (สำหรับเว็บ dev — platform โฮสต์หน้าจริงให้) ----------
def _published_items(brand):
    return [c for c in db.list_content(brand["id"]) if c["status"] == "published"]


@app.api_route("/e/{key}/a/", methods=["GET", "HEAD"])
def hosted_index(request: Request, key: str):
    brand = db.get_brand_by_embed_key(key)
    if not brand:
        return Response("not found", status_code=404)
    site = geo_content._site_url(brand)
    articles = [{"title": it["title"], "url": f"{site}/geo/{it['id']}"} for it in _published_items(brand)]
    return templates.TemplateResponse(request, "hosted_index.html",
        {"brand_name": brand["name"], "site_url": site, "domain": brand["domain"], "articles": articles},
        headers={"Cache-Control": "public, max-age=600", "Access-Control-Allow-Origin": "*"})


@app.api_route("/e/{key}/a/{cid}", methods=["GET", "HEAD"])
def hosted_article(request: Request, key: str, cid: int):
    brand = db.get_brand_by_embed_key(key)
    if not brand:
        return Response("not found", status_code=404)
    it = db.get_content(cid)
    if not it or it["brand_id"] != brand["id"] or it["status"] != "published":
        return Response("not found", status_code=404)
    site = geo_content._site_url(brand)
    schemas = []
    org = geo_content.org_schema(brand)
    if org:
        schemas.append(org)
    if it["schema_json"]:
        schemas.append(it["schema_json"])
    return templates.TemplateResponse(request, "hosted_article.html",
        {"lang": it["lang"] or "th", "title": it["title"],
         "meta_title": (it["meta_title"] or it["title"]), "meta_desc": it["meta_desc"] or "",
         "body_html": wp_client.md_to_html(it["body_md"]), "schemas": schemas,
         "canonical": f"{site}/geo/{cid}", "brand_name": brand["name"],
         "site_url": site, "domain": brand["domain"]},
        headers={"Cache-Control": "public, max-age=600", "Access-Control-Allow-Origin": "*"})


@app.api_route("/e/{key}/content.json", methods=["GET", "HEAD"])
def content_feed(key: str):
    """ฟีดคอนเทนต์ที่เผยแพร่แล้ว — ให้เว็บ dev ดึงไป render เองในสแตกตัวเอง"""
    brand = db.get_brand_by_embed_key(key)
    if not brand:
        return JSONResponse([], status_code=404)
    site = geo_content._site_url(brand)
    out = []
    for it in _published_items(brand):
        out.append({
            "id": it["id"], "title": it["title"], "lang": it["lang"],
            "url": f"{site}/geo/{it['id']}",
            "meta_title": it["meta_title"], "meta_desc": it["meta_desc"],
            "body_markdown": it["body_md"], "body_html": wp_client.md_to_html(it["body_md"]),
            "schema_json": it["schema_json"], "published_at": it["published_at"],
        })
    return JSONResponse(out, headers={"Cache-Control": "public, max-age=600", "Access-Control-Allow-Origin": "*"})


@app.get("/plugin/jor-ai-connector.zip")
def download_plugin():
    """แพ็กปลั๊กอิน WordPress Connector เป็น .zip ให้ติดตั้งได้เลย"""
    import io, zipfile
    root = BASE.parent / "wordpress-plugin"
    pkg = root / "jor-ai-connector"
    if not pkg.exists():
        return Response("plugin not found", status_code=404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(pkg.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(root))  # เก็บโฟลเดอร์ jor-ai-connector/ ไว้ในซิป
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="jor-ai-connector.zip"'})


# ---------- M4: SoV impact ----------
@app.get("/brands/{brand_id}/impact")
def brand_impact(request: Request, brand_id: int):
    brand = _brand_for(request, brand_id)
    if brand is None:
        return _redirect("/app")
    data = db.get_sov_impact(brand_id)
    return templates.TemplateResponse(
        request, "impact.html",
        {"brand": brand, "runs": data["runs"], "publishes": data["publishes"],
         "question_results": json.dumps(data["question_results"], ensure_ascii=False)},
    )


# ---------- admin (operator god-view) ----------
def _admin_ctx(error=None):
    tenants = db.list_all_tenants()
    all_brands = db.list_all_brands()
    # สรุปบริการเสริม (add-on ที่จ่าย+ยืนยันแล้ว) ต่อ tenant
    from collections import Counter
    addon_labels = {a["key"]: a["label"] for a in billing.ADDONS}
    _raw = {}
    for pay in db.list_confirmed_payments():
        if pay["plan"] not in billing.PLANS:  # เป็น add-on ไม่ใช่แผน
            _raw.setdefault(pay["tenant_id"], []).append(pay["plan"])
    addon_summary = {}
    for tid, keys in _raw.items():
        addon_summary[tid] = [f"{addon_labels.get(k, k)}{(' ×' + str(n)) if n > 1 else ''}"
                              for k, n in Counter(keys).items()]
    return {
        "tenants": tenants, "all_brands": all_brands, "plans": billing.PLANS, "error": error,
        "pending_payments": db.list_pending_payments(),
        "addon_labels": addon_labels, "addon_summary": addon_summary,
        "stats": {
            "customers": sum(1 for t in tenants if not t["is_admin"]),
            "admins": sum(1 for t in tenants if t["is_admin"]),
            "brands": len(all_brands),
            "brands_run": sum(1 for b in all_brands if b["last_run_at"]),
        },
    }


@app.get("/admin/slip/{pid}")
def admin_slip(request: Request, pid: int):
    if not _is_admin(request):
        return _redirect("/login")
    pay = db.get_payment(pid)
    if not pay or not pay["slip_path"]:
        return Response("not found", status_code=404)
    f = BASE.parent / "uploads" / pay["slip_path"]
    if not f.exists():
        return Response("not found", status_code=404)
    ext = f.suffix.lower()
    ct = {".png": "image/png", ".webp": "image/webp", ".pdf": "application/pdf"}.get(ext, "image/jpeg")
    return Response(f.read_bytes(), media_type=ct)


@app.post("/admin/payments/{pid}/confirm")
def admin_confirm_payment(request: Request, pid: int):
    if not _is_admin(request):
        return _redirect("/login")
    pay = db.get_payment(pid)
    if pay and pay["status"] == "pending":
        if pay["plan"] in billing.PLANS:        # แผน → เปิดให้อัตโนมัติ
            db.set_plan(pay["tenant_id"], pay["plan"])
            db.add_notification(pay["tenant_id"], f"✅ เปิดใช้แผน {_item_label(pay['plan'])} แล้ว — ขอบคุณที่ใช้บริการ", "/upgrade", "plan")
        else:                                   # add-on → ยืนยัน (admin ทำให้เองตามแพ็กเกจ)
            db.add_notification(pay["tenant_id"], f"✅ ยืนยันแพ็กเกจ {_item_label(pay['plan'])} แล้ว ทีมงานจะดำเนินการให้", "/app", "addon")
        db.confirm_payment(pid)
    return _redirect("/admin")


@app.get("/admin")
def admin_home(request: Request):
    if not _is_admin(request):
        return _redirect("/app" if _tid(request) else "/login")
    return templates.TemplateResponse(request, "admin.html", _admin_ctx())


@app.post("/admin/tenants")
def admin_create_tenant(request: Request, email: str = Form(...), password: str = Form(...), name: str = Form("")):
    if not _is_admin(request):
        return _redirect("/login")
    if db.get_tenant_by_email(email):
        return templates.TemplateResponse(request, "admin.html", _admin_ctx(error=f"อีเมล {email} ถูกใช้แล้ว"))
    db.create_tenant(email, hash_pw(password), name, is_admin=False)
    return _redirect("/admin")


@app.post("/admin/tenants/{tid}/plan")
def admin_set_plan(request: Request, tid: int, plan: str = Form(...)):
    if not _is_admin(request):
        return _redirect("/login")
    if plan in billing.PLANS:
        db.set_plan(tid, plan)
        db.add_notification(tid, f"แผนของคุณถูกตั้งเป็น {billing.PLANS[plan]['label']}", "/upgrade", "plan")
    return _redirect("/admin")


def _settings_ctx(request: Request, saved=False, error=None):
    ppid, ppname = _promptpay_config()
    return {
        "ppid": ppid, "ppname": ppname,
        "qr": promptpay.qr_data_uri(ppid, 100) if ppid else None,
        "from_env": bool(not db.get_setting("promptpay_id") and os.getenv("PROMPTPAY_ID", "").strip()),
        # search backend (วัด SoV)
        "search_backend": (db.get_setting("search_backend") or os.getenv("GEO_SEARCH_BACKEND", "ddgs")).lower(),
        "serper_set": bool(db.get_setting("serper_key") or os.getenv("SERPER_API_KEY")),
        "brave_set": bool(db.get_setting("brave_key") or os.getenv("BRAVE_API_KEY")),
        "active_backend": geo_worker.active_backend(),
        # คีย์ AI ผู้ช่วย (วัดการมองเห็นจริง) — บอกแค่ว่าตั้งแล้วหรือยัง ไม่ส่งค่าออกไปหน้าเว็บ
        "ai_engines": ai_visibility.ENGINES,
        "ai_set": {e: bool(db.get_setting(spec["db_key"]) or os.getenv(spec["env"]))
                   for e, spec in ai_visibility.ENGINES.items()},
        "openrouter": ai_visibility.OPENROUTER,
        "or_set": bool(db.get_setting(ai_visibility.OPENROUTER["db_key"])
                       or os.getenv(ai_visibility.OPENROUTER["env"])),
        # รูปปิดกลางของคีย์ที่บันทึกไว้ (หัว 4 ท้าย 4) — ให้ยืนยันได้ว่าใส่ตัวไหน ไม่ส่งคีย์เต็มออกไป
        "ai_mask": {e: ai_visibility.stored_key_preview(spec) for e, spec in ai_visibility.ENGINES.items()},
        "or_mask": ai_visibility.stored_key_preview(ai_visibility.OPENROUTER),
        # Search Console — โชว์แค่อีเมล/โปรเจกต์ของบัญชีบริการ + ผลทดสอบตอนบันทึก ไม่ส่ง JSON กลับหน้าเว็บ
        "gsc_info": gsc.key_info(), "gsc_status": gsc.status(),
        # SerpApi (AI Overview / AI Mode) + PageSpeed key — โชว์แค่รูปปิดกลาง
        "serpapi": ai_serp.SPEC, "serpapi_set": ai_serp.available(), "serpapi_mask": ai_serp.key_preview(),
        "psi": pagespeed.SPEC, "psi_set": bool(pagespeed._key()), "psi_mask": pagespeed.key_preview(),
        "saved": saved, "error": error,
    }


@app.get("/admin/settings")
def admin_settings(request: Request):
    if not _is_admin(request):
        return _redirect("/app" if _tid(request) else "/login")
    return templates.TemplateResponse(request, "admin_settings.html", _settings_ctx(request))


@app.post("/admin/settings")
def admin_settings_save(request: Request, promptpay_id: str = Form(""), promptpay_name: str = Form("")):
    if not _is_admin(request):
        return _redirect("/login")
    raw = promptpay_id.strip().replace("-", "").replace(" ", "")
    # ตรวจรูปแบบ: เบอร์มือถือ 10 หลัก หรือเลขบัตรปชช./taxid 13 หลัก (ว่าง = ล้างค่า)
    if raw and not (raw.isdigit() and len(raw) in (10, 13)):
        return templates.TemplateResponse(request, "admin_settings.html",
            _settings_ctx(request, error="รูปแบบไม่ถูกต้อง — ใส่เบอร์พร้อมเพย์ 10 หลัก หรือเลขบัตรประชาชน/ภาษี 13 หลัก"))
    db.set_setting("promptpay_id", raw)
    db.set_setting("promptpay_name", promptpay_name.strip() or "เจอ.AI")
    return templates.TemplateResponse(request, "admin_settings.html", _settings_ctx(request, saved=True))


@app.post("/admin/settings/search")
def admin_settings_search(request: Request, search_backend: str = Form("ddgs"),
                          serper_key: str = Form(""), brave_key: str = Form("")):
    if not _is_admin(request):
        return _redirect("/login")
    if search_backend not in ("ddgs", "serper", "brave"):
        search_backend = "ddgs"
    db.set_setting("search_backend", search_backend)
    if serper_key.strip():           # เว้นว่าง = คงคีย์เดิม (ไม่ล้าง)
        db.set_setting("serper_key", serper_key.strip())
    if brave_key.strip():
        db.set_setting("brave_key", brave_key.strip())
    return templates.TemplateResponse(request, "admin_settings.html", _settings_ctx(request, saved=True))


@app.post("/admin/settings/ai")
def admin_settings_ai(request: Request, ai_key_openai: str = Form(""), ai_key_anthropic: str = Form(""),
                      ai_key_pplx: str = Form(""), ai_key_gemini: str = Form(""),
                      ai_key_openrouter: str = Form("")):
    if not _is_admin(request):
        return _redirect("/login")
    # เว้นว่าง = คงคีย์เดิม (ไม่ล้าง) — แบบเดียวกับ serper/brave
    for k, v in (("ai_key_openai", ai_key_openai), ("ai_key_anthropic", ai_key_anthropic),
                 ("ai_key_pplx", ai_key_pplx), ("ai_key_gemini", ai_key_gemini),
                 ("ai_key_openrouter", ai_key_openrouter)):
        if v.strip():
            db.set_setting(k, v.strip())
    _AI_LIVE["ts"] = 0.0          # ข้อความโฆษณาบน landing อ้างอิงเจ้าที่มีคีย์ — ให้สะท้อนคีย์ใหม่ทันที
    return templates.TemplateResponse(request, "admin_settings.html", _settings_ctx(request, saved=True))


@app.post("/admin/settings/gsc")
def admin_settings_gsc(request: Request, gsc_json: str = Form("")):
    if not _is_admin(request):
        return _redirect("/login")
    if not gsc_json.strip():
        return templates.TemplateResponse(request, "admin_settings.html",
            _settings_ctx(request, error="ยังไม่ได้วาง service account JSON"))
    # ทดสอบจริงก่อนเก็บ (ขอ token + ดึง property) — คีย์ที่ใช้ไม่ได้จะไม่ทับของเดิม
    r = gsc.save_key(gsc_json)
    if not r["ok"]:
        return templates.TemplateResponse(request, "admin_settings.html",
            _settings_ctx(request, error="Search Console: " + r["msg"]))
    return templates.TemplateResponse(request, "admin_settings.html", _settings_ctx(request, saved=True))


@app.post("/admin/settings/serp")
def admin_settings_serp(request: Request, serpapi_key: str = Form(""), pagespeed_key: str = Form("")):
    if not _is_admin(request):
        return _redirect("/login")
    for k, v in (("serpapi_key", serpapi_key), ("pagespeed_key", pagespeed_key)):
        if v.strip():                           # เว้นว่าง = คงคีย์เดิม
            db.set_setting(k, v.strip())
    return templates.TemplateResponse(request, "admin_settings.html", _settings_ctx(request, saved=True))


# ---------- อีเมล: SMTP + แคมเปญ ----------
def _email_ctx(request: Request, notice=None):
    import datetime as _dt
    tenants = [dict(t) for t in db.list_all_tenants()]
    brands = [dict(b) for b in db.list_all_brands()]
    camps = db.list_campaigns()
    me = db.get_tenant(_tid(request))
    return {
        "smtp": mailer.public_config(), "me_email": me["email"] if me else "",
        "campaigns": camps, "labels": {c["id"]: campaigns.audience_label(c["audience"], tenants, brands) for c in camps},
        "tenants": tenants, "brands": brands, "schedules": campaigns.SCHEDULES, "placeholders": campaigns.PLACEHOLDERS,
        "default_subject": campaigns.DEFAULT_SUBJECT, "default_body": campaigns.DEFAULT_BODY,
        "default_first_at": (_dt.datetime.now() + _dt.timedelta(days=1)).replace(hour=8, minute=0).strftime("%Y-%m-%dT%H:%M"),
        "log": db.list_email_log(30), "notice": notice,
    }


@app.get("/admin/email")
def admin_email(request: Request):
    if not _is_admin(request):
        return _redirect("/app" if _tid(request) else "/login")
    return templates.TemplateResponse(request, "admin_email.html", _email_ctx(request))


@app.post("/admin/email/smtp")
def admin_email_smtp(request: Request, host: str = Form(""), port: str = Form(""), user: str = Form(""), password: str = Form(""),
                     from_: str = Form(""), from_name: str = Form(""), tls: str = Form("starttls")):
    if not _is_admin(request):
        return _redirect("/login")
    mailer.save_config(host, port, user, password, from_, from_name, tls)
    return templates.TemplateResponse(request, "admin_email.html", _email_ctx(request, {"ok": True, "msg": "บันทึก SMTP แล้ว — กด \"ส่งอีเมลทดสอบ\" เพื่อยืนยันว่าส่งออกได้จริง"}))


@app.post("/admin/email/test")
def admin_email_test(request: Request, host: str = Form(""), port: str = Form(""), user: str = Form(""), password: str = Form(""),
                     from_: str = Form(""), from_name: str = Form(""), tls: str = Form("starttls")):
    """บันทึกค่าที่กรอกไว้ก่อน แล้วส่งทดสอบถึงอีเมลของแอดมินที่ล็อกอิน — จะได้รู้ทันทีว่า host/port/รหัสถูก"""
    if not _is_admin(request):
        return _redirect("/login")
    mailer.save_config(host, port, user, password, from_, from_name, tls)
    me = db.get_tenant(_tid(request))
    try:
        mailer.send(me["email"], "ทดสอบ SMTP จาก เจอ.AI",
                    mailer.wrap_html("SMTP ใช้งานได้", "<p>ถ้าคุณได้รับอีเมลนี้ แปลว่าการตั้งค่า SMTP ถูกต้อง แคมเปญและ Autopilot จะส่งรายงานผ่านช่องทางนี้</p>",
                                     f"ส่งจาก {BASE_URL}/admin/email"))
        notice = {"ok": True, "msg": f"ส่งอีเมลทดสอบถึง {me['email']} แล้ว — เช็คกล่องจดหมาย (และ Spam) "}
    except Exception as e:
        notice = {"ok": False, "msg": f"ส่งไม่สำเร็จ: {type(e).__name__}: {str(e)[:200]}"}
    return templates.TemplateResponse(request, "admin_email.html", _email_ctx(request, notice))


@app.post("/admin/email/campaigns")
def admin_email_create(request: Request, name: str = Form(...), subject: str = Form(...), body_md: str = Form(...),
                       audience: str = Form("all"), schedule: str = Form("monthly"), first_at: str = Form(""), attach_report: str = Form("")):
    if not _is_admin(request):
        return _redirect("/login")
    import datetime as _dt
    try:
        nxt = _dt.datetime.fromisoformat(first_at).isoformat(timespec="seconds")
    except Exception:
        nxt = (_dt.datetime.now() + _dt.timedelta(minutes=5)).isoformat(timespec="seconds")
    if schedule not in campaigns.SCHEDULES:
        schedule = "monthly"
    db.create_campaign(name.strip()[:120], subject.strip()[:200], body_md.strip()[:8000], audience.strip()[:40], schedule, bool(attach_report), nxt)
    return _redirect("/admin/email")


@app.post("/admin/email/campaigns/{cid}/send")
def admin_email_send_now(request: Request, cid: int):
    if not _is_admin(request):
        return _redirect("/login")
    camp = db.get_campaign(cid)
    if not camp:
        return _redirect("/admin/email")
    res = campaigns.send_campaign(dict(camp), BASE_URL)
    msg = f"ส่ง \"{res['campaign']}\" แล้ว {res['sent']}/{res['recipients']} ฉบับ" + (f" · ล้ม {res['failed']}: {'; '.join(res['errors'])}" if res["failed"] else "") \
          + (f" · รอบถัดไป {res['next_at'].replace('T', ' ')}" if res.get("next_at") else " · แคมเปญครั้งเดียว — จบแล้ว")
    return templates.TemplateResponse(request, "admin_email.html", _email_ctx(request, {"ok": res["failed"] == 0, "msg": msg}))


@app.post("/admin/email/campaigns/{cid}/pause")
def admin_email_pause(request: Request, cid: int):
    if _is_admin(request):
        db.set_campaign_status(cid, "paused")
    return _redirect("/admin/email")


@app.post("/admin/email/campaigns/{cid}/resume")
def admin_email_resume(request: Request, cid: int):
    if _is_admin(request):
        db.set_campaign_status(cid, "active")
    return _redirect("/admin/email")


@app.post("/admin/email/campaigns/{cid}/delete")
def admin_email_delete(request: Request, cid: int):
    if _is_admin(request):
        db.delete_campaign(cid)
    return _redirect("/admin/email")


@app.get("/admin/contacts")
def admin_contacts(request: Request):
    if not _is_admin(request):
        return _redirect("/app" if _tid(request) else "/login")
    return templates.TemplateResponse(request, "admin_contacts.html", {"contacts": db.list_contacts()})


@app.post("/admin/contacts/{cid}/handled")
def admin_contact_handled(request: Request, cid: int):
    if not _is_admin(request):
        return _redirect("/login")
    db.set_contact_status(cid, "handled")
    return _redirect("/admin/contacts")
