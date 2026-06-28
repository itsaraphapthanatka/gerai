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

from . import db, geo_worker, geo_content, wp_client, billing, ai_client, image_finder, promptpay


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
    data = geo_content.generate_content(b, g["question"], lang)
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
            "plan": billing.plan_of(tenant), "usage": billing.usage(tid), "error": error}


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
        db.update_content_body(content_id, f"![{alt}]({url})\n\n{item['body_md']}")
    except Exception:
        pass


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
    return templates.TemplateResponse(request, "landing.html", {})


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
const CACHE = 'geo-v2';
const ASSETS = ['/static/icon-192.png', '/static/icon-512.png', '/manifest.webmanifest'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;                 // ไม่แตะ POST
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;       // เฉพาะ same-origin
  if (url.pathname.startsWith('/static/')) {        // static: cache-first
    e.respondWith(caches.match(req).then(c => c || fetch(req).then(r => {
      const copy = r.clone(); caches.open(CACHE).then(c => c.put(req, copy)); return r;
    })));
    return;
  }
  e.respondWith(fetch(req).catch(() => caches.match(req)));  // page: network-first
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
         "gaps": db.get_content_gaps(brand_id),
         "wp": db.get_wp_connection(brand_id),
         "embed_js_url": f"{base_url}/e/{embed_key}.js",
         "embed_head_url": f"{base_url}/e/{embed_key}/head.html",
         "embed_head_html": _head_html(brand),
         "embed_llms_url": f"{base_url}/e/{embed_key}/llms.txt",
         "embed_robots_url": f"{base_url}/e/{embed_key}/robots.txt",
         "embed_articles_url": f"{base_url}/e/{embed_key}/a/",
         "embed_content_json_url": f"{base_url}/e/{embed_key}/content.json",
         "embed_host": request.url.hostname or "geo.appreview.cloud"})


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
    return templates.TemplateResponse(request, "brand_content.html",
        {"brand": brand, "questions": questions, "content": content,
         "can_generate": ok, "limit_msg": limit_msg if not ok else None,
         "publish_eta": publish_eta,
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


# ---------- content (execution layer — Phase A) ----------
@app.post("/brands/{brand_id}/content")
def gen_content(request: Request, brand_id: int, question_id: int = Form(...), lang: str = Form("th")):
    brand = _brand_for(request, brand_id)
    if not brand:
        return _redirect("/login")
    ok, msg = billing.check(db.get_tenant(brand["tenant_id"]), "content")
    if not ok:
        return _redirect(f"/brands/{brand_id}/content")
    q = next((row for row in db.list_questions(brand_id) if row["id"] == question_id), None)
    if not q:
        return _redirect(f"/brands/{brand_id}/content")
    data = geo_content.generate_content(brand, q["question"], lang)
    cid = db.create_content_item(
        brand_id, question_id, lang, data["title"], data["meta_title"],
        data["meta_desc"], data["body_md"], data["schema_json"], data["source"],
    )
    if brand["auto_image"]:
        _attach_image(brand, cid, q["question"])
    return _redirect(f"/content/{cid}")


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
         "robots": geo_content.robots_snippet(), "org_schema": geo_content.org_schema(brand)},
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
         "hosted_url": f"{geo_content._site_url(brand)}/geo/{item['id']}"},
    )


# ---------- Embed (public — ไม่ต้อง login) ----------
def _published_faqs(brand) -> list[dict]:
    """FAQPage schema ของคอนเทนต์ที่เผยแพร่แล้ว (เป็น dict)"""
    out = []
    for item in db.list_content(brand["id"]):
        if item["status"] == "published" and item.get("schema_json"):
            try:
                s = json.loads(item["schema_json"])
                if s.get("@type") == "FAQPage":
                    out.append(s)
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


@app.get("/e/{key}.js")
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


@app.get("/e/{key}/llms.txt")
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


@app.get("/e/{key}/robots.txt")
def embed_robots(key: str, dl: int = 0):
    brand = db.get_brand_by_embed_key(key)
    if not brand:
        return PlainTextResponse("", status_code=404)
    headers = {"Cache-Control": "public, max-age=86400"}
    if dl:
        headers["Content-Disposition"] = 'attachment; filename="robots.txt"'
    return PlainTextResponse(geo_content.robots_snippet(), headers=headers)


@app.get("/e/{key}/head.html")
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


@app.get("/e/{key}/a/")
def hosted_index(request: Request, key: str):
    brand = db.get_brand_by_embed_key(key)
    if not brand:
        return Response("not found", status_code=404)
    site = geo_content._site_url(brand)
    articles = [{"title": it["title"], "url": f"{site}/geo/{it['id']}"} for it in _published_items(brand)]
    return templates.TemplateResponse(request, "hosted_index.html",
        {"brand_name": brand["name"], "site_url": site, "domain": brand["domain"], "articles": articles},
        headers={"Cache-Control": "public, max-age=600", "Access-Control-Allow-Origin": "*"})


@app.get("/e/{key}/a/{cid}")
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


@app.get("/e/{key}/content.json")
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
