"""GEO Platform — FastAPI app (MVP, monitoring-first)."""
from __future__ import annotations
import os
import json
import secrets
from pathlib import Path
from urllib.parse import quote
from dotenv import load_dotenv

# โหลด .env ก่อน import db (db อ่าน GEO_DB_PATH ตอน import)
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse, PlainTextResponse, Response, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
import bcrypt

from . import db, geo_worker, geo_content, wp_client, billing, ai_client, image_finder, promptpay, site_health, ai_visibility, gsc, pagespeed, ai_serp, report, analytics_chat, mailer, campaigns, autopilot, auto_content, leads, execs, pipeline, quotes, line_oa, prospecting, stripe_pay, marketing, skills, office, deep_audit, sms

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


# Jinja2 globals ของ base.html — ลงทะเบียนตอน import ไม่ใช่ตอน startup: เป็น lambda ที่ค่อยอ่าน DB ตอน render
# จึงไม่ต้องรอ startup และ TestClient(app) ที่ไม่ได้เข้า lifespan (สเมอก์เทสต์ทุกตัว) render หน้าได้เหมือน production
templates.env.filters["urlpath"] = lambda u, n=60: deep_audit._path(u, n)   # รายงานเต็ม: path อ่านง่าย ไม่ล้นตาราง
templates.env.globals["sidebar_brands"] = lambda req: (
    db.list_brands(req.session["tenant_id"]) if req.session.get("tenant_id") else []   # sidebar ดึงแบรนด์ของ tenant ที่ล็อกอินอยู่
)
templates.env.globals["unread_count"] = lambda req: (
    db.count_unread(req.session["tenant_id"]) if req.session.get("tenant_id") else 0
)
templates.env.globals["trial_of"] = lambda req: (
    billing.trial(db.get_tenant(req.session["tenant_id"])) if req.session.get("tenant_id") else {"on": False}
)
templates.env.globals["trial_fn"] = billing.trial
templates.env.globals["q_payable"] = lambda q: quotes.baht(quotes.payable(q))
templates.env.globals["chat_on"] = lambda: (db.get_setting("salesbot_web") or "1") == "1"
templates.env.globals["new_contacts_count"] = lambda req: (
    db.count_new_contacts() if req.session.get("is_admin") else 0
)


@app.on_event("startup")
def _startup():
    db.init_db()
    _maybe_start_scheduler()


def _topup_questions(b, n: int) -> list[dict]:
    """ปิดครบทุกคำถามแล้ว → ให้ AI คิดคำถามใหม่ที่ไม่ซ้ำของเดิม เพิ่มเข้าแบรนด์ (source=auto) — คืนรายการที่เพิ่มจริง
    ขอเผื่อ 2 เท่าแล้วค่อยกรองซ้ำ เพราะโมเดลชอบวนมุมเดิม"""
    existing = [qq["question"] for qq in db.list_questions(b["id"])]
    cands = ai_client.generate_questions(b["name"], b["domain"], b["market"] or "", n=n * 2, existing=existing)
    picked = auto_content.pick_new(cands, existing, n)
    for p in picked:
        p["id"] = db.add_question(b["id"], p["question"], p["lang"], source="auto")
    return picked


def _auto_content_tick(b, weekly: int, now_local):
    """สร้างร่างคอนเทนต์ปิด gap แบบมีจังหวะ (~weekly ชิ้น/สัปดาห์) — ร่างเท่านั้น ไม่ publish เอง
    ปิดครบทุกข้อแล้วไม่หยุดเงียบ: คิดคำถามใหม่เพิ่ม รอมอนิเตอร์วัด แล้วค่อยเขียน (กติกาทั้งหมดอยู่ใน auto_content)"""
    import datetime as _dt
    bid = b["id"]
    # ด่านเวลาก่อน (ถูก) — เพดานต่อสัปดาห์รวมทุกแหล่ง + เว้นระยะจากรอบก่อน
    week_ago = (now_local - _dt.timedelta(days=7)).isoformat(timespec="seconds")
    if auto_content.time_gate(weekly, db.count_content_since(bid, week_ago), b["last_auto_content_at"], now_local):
        return
    ok, _msg = billing.check(db.get_tenant(b["tenant_id"]), "content")
    d = auto_content.queue_state(db.get_content_gaps(bid), ok)
    if d["action"] == "topup":
        _topup_questions(b, d["n"])
        # นับเป็นรอบหนึ่ง — ไม่งั้นลูป 60 วิจะเรียก AI ซ้ำทุกนาทีถ้าโมเดลให้แต่ของซ้ำ
        db.touch_auto_content(bid, now_local.isoformat(timespec="seconds"))
        return
    if d["action"] != "write":
        return  # รอวัด / โควตาเต็ม / ถึงเพดานคำถาม — หน้าคอนเทนต์บอกเหตุผลเดียวกันนี้
    g = d["gap"]
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


def _attach_image(brand, content_id: int, topic: str) -> str:
    """หา/สร้างรูปประกอบ แล้วแปะหัวบทความ — ได้หรือไม่ได้ก็บันทึกเหตุผลไว้ที่ชิ้นงาน (image_note) ไม่เงียบ · คืน note"""
    note = ""
    try:
        res = image_finder.image_for_article(brand, topic, geo_content._site_url(brand))
        note = res.get("note") or ""
        if res["mode"] == "none":
            db.set_image_note(content_id, "ไม่มีรูป — " + (note or "ไม่ทราบสาเหตุ"))
            return note
        if res["mode"] == "generated":
            gen_dir = BASE / "static" / "gen"
            gen_dir.mkdir(parents=True, exist_ok=True)
            (gen_dir / f"c{content_id}.png").write_bytes(res["png"])
            url = f"{_public_base()}/static/gen/c{content_id}.png"
        else:
            url = res["url"]
        item = db.get_content(content_id)
        if not item:
            return note
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
        db.set_image_note(content_id, note)
    except Exception as e:
        note = f"ไม่มีรูป — ผิดพลาด {type(e).__name__}: {str(e)[:80]}"
        try:
            db.set_image_note(content_id, note)
        except Exception:
            pass
    return note


def _brand_grounded(brand):
    """คืน brand ที่มี site_context (ดึงเนื้อหาเว็บจริงมา cache ครั้งแรก) เพื่อใช้ ground การเขียน"""
    try:
        if ai_client.context_stale(brand["site_context"]):        # ว่าง / หน้า placeholder ของเซิร์ฟเวอร์ / บางจนไม่มีข้อเท็จจริง → ดึงใหม่
            txt = ai_client.site_context(geo_content._site_url(brand))
            old = brand["site_context"] or ""
            if txt and (len(txt) > len(old) or ai_client.is_placeholder(old)):
                db.set_brand_site_context(brand["id"], txt)
                brand = db.get_brand(brand["id"])
            elif ai_client.is_placeholder(brand["site_context"]):
                db.set_brand_site_context(brand["id"], "")            # placeholder ห้ามใช้ ดีกว่าไม่มี
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
    from . import partners as _pt
    return templates.TemplateResponse(request, "landing.html",
                                      {"plans": billing.PLANS, "addons": billing.ADDONS, "ai_live": _ai_live(),
                                       "yearly_pct": quotes.yearly_discount(), "refund_days": execs.MANDATE["refund_days"],
                                       "partner_pct": _pt.pct(), "partner_months": _pt.months(),
                                       "stripe_on": stripe_pay.available()})


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


# ---------- ลูกค้าเป้าหมาย: ตรวจเว็บฟรี (public) + ผู้บริหารเอเจนต์โหมดเสนอ ----------
def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for") or ""
    return (fwd.split(",")[0].strip() or (request.client.host if request.client else "") or "")[:64]


def _run_lead_audit(lead_id: int, site: str, ip: str = "") -> str:
    return pipeline.run_lead_audit(lead_id, site, ip)


def _ceo_propose(lead) -> int | None:
    return pipeline.ceo_propose(lead)


def _src(v: str) -> str:
    import re as _re
    v = (v or "").strip().lower()[:24]
    return v if _re.fullmatch(r"[a-z0-9_-]{1,24}", v) else ""


def _ref(request: Request, ref: str = "") -> str:
    """รหัสพันธมิตรจากลิงก์ (?ref=) หรือคุกกี้ /p/<code>"""
    return _src(ref) or _src(request.cookies.get("jr_ref", ""))


@app.get("/p/{code}")
def partner_link(request: Request, code: str):
    """ลิงก์แนะนำของพันธมิตร → หน้าตรวจเว็บฟรี + จำรหัสไว้ 60 วัน"""
    from . import partners
    code = _src(code)
    p = db.find_partner(code=code) if code else None
    resp = _redirect(f"/audit?src=partner&ref={code}" if p and p["status"] == "active" else "/audit")
    if p and p["status"] == "active":
        resp.set_cookie("jr_ref", code, max_age=partners.COOKIE_DAYS * 86400, httponly=True, samesite="lax")
    return resp


@app.get("/audit")
def audit_page(request: Request, src: str = "", ref: str = ""):
    return templates.TemplateResponse(request, "audit.html", {"error": "", "url": "", "src": _src(src), "ref": _ref(request, ref)})


@app.post("/audit")
def audit_run(request: Request, url: str = Form(""), website: str = Form(""), src: str = Form(""), ref: str = Form("")):
    src = _src(src)
    ref = _ref(request, ref)
    if website.strip():                                   # honeypot: บอทกรอก → เงียบ
        return _redirect("/audit")
    site = leads.normalize_url(url)
    if not site:
        return templates.TemplateResponse(request, "audit.html", {"error": "กรอกที่อยู่เว็บให้ถูกต้อง เช่น example.co.th", "url": url, "src": src})
    import datetime as _dt
    ip = _client_ip(request)
    since = (_dt.datetime.now() - _dt.timedelta(hours=leads.REUSE_AUDIT_HOURS)).isoformat(timespec="seconds")
    if db.count_audits_since(ip, since) >= leads.AUDITS_PER_IP_PER_DAY:
        return templates.TemplateResponse(request, "audit.html", {"error": f"วันนี้ตรวจครบ {leads.AUDITS_PER_IP_PER_DAY} เว็บแล้ว พรุ่งนี้ตรวจต่อได้", "url": url, "src": src})
    dom = leads.domain_of(site)
    recent = db.find_lead_by_domain(dom)
    if recent and recent["audit_json"] and recent["token"] and (recent["audit_at"] or "") >= since:
        return _redirect(f"/audit/{recent['token']}")   # โดเมนเดิมภายใน 24 ชม. ใช้ผลเดิม
    lid = recent["id"] if recent else db.add_lead(site, dom, source="audit", ip=ip)
    if src and not (recent and recent["src"]):
        db.update_lead(lid, src=src)                      # ช่องทางที่ลีดมา (รหัสในลิงก์ที่ CMO โพสต์)
    if ref:
        from . import partners
        partners.attribute(lid, ref, "ลิงก์แนะนำ")              # มาจากพันธมิตร (ครั้งแรกเท่านั้น)
    try:
        token = _run_lead_audit(lid, site, ip)
    except Exception:
        return templates.TemplateResponse(request, "audit.html", {"error": "ตรวจไม่สำเร็จ ลองใหม่อีกครั้ง หรือเช็กว่าเว็บเปิดได้", "url": url, "src": src})
    deep_audit.start(lid)                                 # รายงานเต็มชั้น 1 ทำต่อเบื้องหลัง หน้าผลจะอัปเดตเอง
    return _redirect(f"/audit/{token}")


def _audit_result_ctx(lead, sent=False, error="", replied=False):
    report = json.loads(lead["audit_json"] or "{}") if lead["audit_json"] else {"checks": []}
    t1, t2 = deep_audit.tier1(lead), deep_audit.tier2(lead)
    return {"lead": lead, "report": report, "score": leads.score_audit(report), "summary": leads.public_summary(report),
            "sent": sent or bool(lead["consent"]), "error": error, "replied": replied,
            "deep": t1 if t1.get("state") == "done" else None, "ai": t2 if t2.get("state") == "done" else None, "ai_state": t2,
            "progress": deep_audit.progress(lead), "highlights": deep_audit.highlights(t1, t2), "benchmark": deep_audit.BENCHMARK}


@app.get("/audit/{token}")
def audit_result(request: Request, token: str, sent: int = 0, replied: int = 0, via: str = ""):
    lead = db.get_lead_by_token(token)
    if not lead:
        return _redirect("/audit")
    if via == "mail":
        pipeline.mark_opened(lead)
    if lead["audit_json"] and deep_audit.progress(lead)["tier1"] in ("none", "error"):
        deep_audit.start(lead["id"])                      # ลีดเก่า/หาเอง: เปิดหน้าครั้งแรกค่อยทำรายงานเต็ม
        lead = db.get_lead(lead["id"])
    return templates.TemplateResponse(request, "audit_result.html", _audit_result_ctx(lead, sent=bool(sent), replied=bool(replied)))


@app.get("/audit/{token}/progress")
def audit_progress(token: str):
    lead = db.get_lead_by_token(token)
    if not lead:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(deep_audit.progress(lead), headers={"Cache-Control": "no-store"})


@app.post("/audit/{token}/contact")
def audit_contact(request: Request, token: str, name: str = Form(""), email: str = Form(""), contact: str = Form(""),
                  consent: str = Form(""), website: str = Form("")):
    lead = db.get_lead_by_token(token)
    if not lead:
        return _redirect("/audit")
    if website.strip():
        return _redirect(f"/audit/{token}")
    if not (email.strip() or contact.strip()):
        return templates.TemplateResponse(request, "audit_result.html", _audit_result_ctx(lead, error="กรอกอีเมลหรือ LINE/เบอร์โทรอย่างน้อยหนึ่งอย่าง"))
    if not consent:
        return templates.TemplateResponse(request, "audit_result.html", _audit_result_ctx(lead, error="ติ๊กยินยอมให้ติดต่อกลับก่อน เราถึงจะส่งแผนให้ได้"))
    db.update_lead(lead["id"], name=name.strip()[:120] or None, email=email.strip().lower()[:160] or None,
                   contact=contact.strip()[:160] or None, consent=1)
    pipeline.alert_admins(f"🎯 ลูกค้าเป้าหมายใหม่จากตรวจเว็บฟรี: {lead['domain']}" + (f" · {name.strip()}" if name.strip() else ""),
                          f"/admin/leads/{lead['id']}", "lead", subject=f"🎯 ลูกค้าเป้าหมายใหม่: {lead['domain']}")
    try:
        _ceo_propose(db.get_lead(lead["id"]))
    except Exception:
        pass   # ข้อเสนอสร้างทีหลังได้จากหน้าแอดมิน ไม่ให้ลูกค้าเห็น error
    deep_audit.start(lead["id"], ai=True)                 # ทิ้งช่องทางติดต่อ = สนใจจริง → วัด AI 20 คำถาม (ชั้น 2)
    return _redirect(f"/audit/{token}?sent=1")


@app.get("/admin/leads")
def admin_leads(request: Request, status: str = "", notice: str = ""):
    if not _is_admin(request):
        return _redirect("/app" if _tid(request) else "/login")
    all_rows = db.list_leads(None, 5000)
    rows = []
    for l in all_rows:
        if status and l["status"] != status:
            continue
        d = dict(l)
        rep = json.loads(d["audit_json"]) if d.get("audit_json") else None
        d["sc"] = leads.score_audit(rep) if rep else None
        d["pending"] = db.count_decisions_for_lead(d["id"], "proposed")
        d["quote"] = next((dict(x) for x in db.list_quotes(d["id"], None, 5) if x["status"] in quotes.OPEN and x["status"] != "draft"), None)
        rows.append(d)
    return templates.TemplateResponse(request, "admin_leads.html",
        {"leads": rows, "statuses": leads.STATUSES, "status_label": leads.STATUS_LABEL, "pipeline": leads.PIPELINE,
         "funnel": leads.funnel(all_rows), "sources": leads.SOURCES, "qstatus": quotes.STATUS_LABEL, "filter": status, "notice": notice})


@app.post("/admin/leads/import")
def admin_leads_import(request: Request, text: str = Form("")):
    if not _is_admin(request):
        return _redirect("/login")
    added = skipped = 0
    for row in leads.parse_import(text):
        if db.find_lead_by_domain(row["domain"]):
            skipped += 1
            continue
        db.add_lead(row["url"], row["domain"], source="import", name=row["name"], email=row["email"])
        added += 1
    return _redirect(f"/admin/leads?notice=นำเข้า {added} ราย" + (f" ข้ามซ้ำ {skipped}" if skipped else ""))


@app.post("/admin/leads/{lead_id}/status")
def admin_lead_status(request: Request, lead_id: int, status: str = Form("new")):
    if not _is_admin(request):
        return _redirect("/login")
    lead = db.get_lead(lead_id)
    who = request.session.get("email") or "admin"
    if lead and status in leads.STATUS_LABEL and status != lead["status"]:
        if status == "unsubscribed":
            pipeline.unsubscribe(lead, who)
        else:
            db.update_lead(lead_id, status=status)
            db.add_lead_event(lead_id, "status", f"{leads.STATUS_LABEL[lead['status']]} → {leads.STATUS_LABEL[status]}", who)
    ref = request.headers.get("referer") or ""
    return _redirect(f"/admin/leads/{lead_id}" if f"/admin/leads/{lead_id}" in ref else "/admin/leads")


@app.post("/admin/leads/{lead_id}/audit")
def admin_lead_audit(request: Request, lead_id: int):
    if not _is_admin(request):
        return _redirect("/login")
    lead = db.get_lead(lead_id)
    if not lead:
        return _redirect("/admin/leads")
    try:
        _run_lead_audit(lead_id, lead["site_url"])
    except Exception as e:
        return _redirect(f"/admin/leads?notice=ตรวจ {lead['domain']} ไม่สำเร็จ: {type(e).__name__}")
    return _redirect(f"/admin/leads?notice=ตรวจ {lead['domain']} แล้ว")


@app.post("/admin/leads/{lead_id}/propose")
def admin_lead_propose(request: Request, lead_id: int):
    if not _is_admin(request):
        return _redirect("/login")
    lead = db.get_lead(lead_id)
    if not lead or not lead["audit_json"]:
        return _redirect("/admin/leads?notice=ต้องตรวจเว็บก่อน CEO ถึงจะเสนอได้")
    did = _ceo_propose(lead)
    return _redirect("/admin/decisions" if did else "/admin/leads?notice=ไม่มีผลตรวจให้เสนอ")


def _execute_decision(d, who: str) -> str:
    return pipeline.execute_decision(d, who)


def _decisions_ctx(request: Request, notice: str = ""):
    pending = []
    for d in db.list_decisions("proposed", 50):
        x = dict(d)
        try:
            x["pl"] = json.loads(x.get("payload_json") or "{}")
        except Exception:
            x["pl"] = {}
        if x["pl"].get("quote_id"):
            qq = db.get_quote(x["pl"]["quote_id"])
            x["quote"] = dict(qq) if qq else None
        pending.append(x)
    history = [dict(d) for d in db.list_decisions(None, 60) if d["status"] != "proposed"]
    kpi = execs.kpis()
    awaiting = []                                            # ใบเสนอราคาที่ลูกค้ายืนยันแล้ว รอเงินเข้า/รอคุณยืนยัน
    for st in ("accepted", "claimed"):
        for qq in db.list_quotes(None, st, 50):
            x = dict(qq)
            l = db.get_lead(qq["lead_id"])
            x["domain"] = l["domain"] if l else "?"
            x["status_label"] = quotes.STATUS_LABEL.get(qq["status"], qq["status"])
            awaiting.append(x)
    return {"pending": pending, "history": history, "stats": db.decision_stats(), "mandate": execs.MANDATE,
            "agents": execs.AGENTS, "kinds": execs.KINDS, "notice": notice, "awaiting": awaiting,
            "kpi": kpi, "by_src": kpi["values"].get("audits_by_src") or {},
            "gates": [execs.auto_gate(k) for k in execs.KINDS if k != "digest"],
            "paused": execs.paused(), "mode": execs.mode_label()}


@app.get("/admin/decisions")
def admin_decisions(request: Request, notice: str = ""):
    if not _is_admin(request):
        return _redirect("/app" if _tid(request) else "/login")
    return templates.TemplateResponse(request, "admin_decisions.html", _decisions_ctx(request, notice))


@app.post("/admin/decisions/{did}/approve")
def admin_decision_approve(request: Request, did: int):
    if not _is_admin(request):
        return _redirect("/login")
    d = db.get_decision(did)
    if not d or d["status"] != "proposed":
        return _redirect("/admin/decisions")
    who = request.session.get("email") or "admin"
    try:
        result = _execute_decision(d, who)
        db.set_decision(did, "executed", who, result)
    except sms.QuietHours:
        return _redirect("/admin/decisions?notice=ตอนนี้นอกเวลาส่ง SMS (08:00–20:00) — ข้อเสนอยังค้างอยู่ กดอนุมัติอีกครั้งหลัง 08:00 (ถ้าเรื่องนี้ตัดสินเอง ระบบส่งให้รอบ 08:00)")
    except Exception as e:
        db.set_decision(did, "approved", who, f"อนุมัติแล้วแต่ทำไม่สำเร็จ: {type(e).__name__}: {str(e)[:120]}")
        result = f"ทำไม่สำเร็จ: {type(e).__name__}"
    return _redirect(f"/admin/decisions?notice={result}")


@app.post("/admin/decisions/{did}/reject")
def admin_decision_reject(request: Request, did: int, reason: str = Form("")):
    if not _is_admin(request):
        return _redirect("/login")
    d = db.get_decision(did)
    if d and d["status"] == "proposed":
        db.set_decision(did, "rejected", request.session.get("email") or "admin", reason.strip()[:300] or None)
    return _redirect("/admin/decisions?notice=ปฏิเสธแล้ว — ระบบนับเป็นการตัดสินใจที่ไม่ตรงกัน")


@app.post("/admin/digest/run")
def admin_digest_run(request: Request):
    if not _is_admin(request):
        return _redirect("/login")
    res = execs.morning_digest(_public_base())
    note = "ทำสรุปเช้าแล้ว" + (f" · ส่งอีเมล {len(res['sent_to'])} ฉบับ" if res["sent_to"] else " · ไม่มีแอดมินที่อีเมลใช้ได้จริง เลยเก็บไว้ที่หน้านี้อย่างเดียว")
    return _redirect(f"/admin/decisions?notice={note}")


# ---------- เฟส 2: ตอบกลับ · ขอไม่รับการติดต่อ · ใบเสนอราคา · สัญญา · LINE · ด่าน/โหมดอัตโนมัติ ----------
def _company() -> dict:
    return {"name": db.get_setting("company_name") or "", "address": db.get_setting("company_address") or "",
            "tax_id": db.get_setting("company_tax_id") or "", "signer": db.get_setting("company_signer") or "",
            "phone": db.get_setting("company_phone") or "", "email": db.get_setting("company_email") or "",
            "branch": db.get_setting("company_branch") or "สำนักงานใหญ่", "vat": db.get_setting("company_vat") == "1",
            "contract_reviewed": db.get_setting("contract_reviewed") == "1"}


def _quote_ctx(q, lead, error: str = "", pdf: bool = False) -> dict:
    report = json.loads(lead["audit_json"] or "{}") if lead["audit_json"] else {"checks": []}
    ppid, ppname = _promptpay_config()
    return {"q": dict(q), "lead": dict(lead), "plan": billing.PLANS[q["package"]], "setup": quotes.setup_addon(), "terms": quotes.terms_for(q),
            "yearly": quotes.cycle_of(q) == "year", "period_total": quotes.period_total(q), "yearly_pct": quotes.yearly_discount(),
            "alt": quotes.alternative(q),
            "summary": leads.public_summary(report), "company": _company(),
            "status_label": quotes.STATUS_LABEL.get(q["status"], q["status"]), "expired": quotes.is_expired(q),
            "ppid": ppid, "ppname": ppname,
            "qr": promptpay.qr_data_uri(ppid, quotes.payable(q)) if (ppid and q["status"] == "accepted" and not pdf) else None,
            "vat": quotes.vat_pct(q), "payable": quotes.payable(q), "baht": quotes.baht,
            "stripe_ready": stripe_pay.available() and not pdf, "stripe_mode": stripe_pay.mode(),
            "error": error, "pdf": pdf, "base": _public_base()}


def _contract_ctx(q, lead, pdf: bool = False) -> dict:
    co = _company()
    return {"q": dict(q), "lead": dict(lead), "company": co, "pdf": pdf, "base": _public_base(),
            "sections": quotes.contract_sections(co, {"name": lead["name"], "domain": lead["domain"], "email": lead["email"]}, q)}


def _render_pdf(request: Request, template: str, ctx: dict, filename: str):
    html = templates.get_template(template).render({**ctx, "request": request})
    try:
        pdf = report.to_pdf(html)
    except Exception as e:
        return Response(f"สร้าง PDF ไม่ได้ตอนนี้ ({type(e).__name__}) — เปิดหน้าเว็บแทนได้", status_code=503, media_type="text/plain; charset=utf-8")
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{filename}"'})


@app.post("/audit/{token}/reply")
def audit_reply(request: Request, token: str, message: str = Form(""), website: str = Form("")):
    """ลูกค้ากด 'ขอใบเสนอราคา' บนหน้าผลตรวจ (ต้องให้ช่องทางติดต่อ+ยินยอมก่อน) → CEO เสนอใบเสนอราคา"""
    lead = db.get_lead_by_token(token)
    if not lead or website.strip():
        return _redirect("/audit")
    if not lead["consent"]:
        return _redirect(f"/audit/{token}")
    try:
        pipeline.lead_replied(lead, message, via="เว็บ")
    except Exception:
        pass
    return _redirect(f"/audit/{token}?replied=1")


@app.get("/u/{token}")
def unsubscribe_page(request: Request, token: str):
    lead = db.get_lead_by_token(token)
    if not lead:
        return _redirect("/")
    return templates.TemplateResponse(request, "unsubscribe.html", {"lead": dict(lead), "done": lead["status"] == "unsubscribed"})


@app.post("/u/{token}")
def unsubscribe_do(request: Request, token: str):
    lead = db.get_lead_by_token(token)
    if not lead:
        return _redirect("/")
    if lead["status"] != "unsubscribed":
        pipeline.unsubscribe(lead)
    return templates.TemplateResponse(request, "unsubscribe.html", {"lead": dict(db.get_lead(lead["id"])), "done": True})


@app.get("/q/{token}.pdf")
def quote_pdf(request: Request, token: str):
    q = db.get_quote_by_token(token)
    if not q:
        return _redirect("/")
    return _render_pdf(request, "quote.html", _quote_ctx(q, db.get_lead(q["lead_id"]), pdf=True), f"{q['number']}.pdf")


@app.get("/q/{token}/contract.pdf")
def contract_pdf(request: Request, token: str):
    q = db.get_quote_by_token(token)
    if not q:
        return _redirect("/")
    return _render_pdf(request, "contract.html", _contract_ctx(q, db.get_lead(q["lead_id"]), pdf=True), f"{q['number']}-contract.pdf")


@app.get("/q/{token}/contract")
def contract_page(request: Request, token: str):
    q = db.get_quote_by_token(token)
    if not q:
        return _redirect("/")
    return templates.TemplateResponse(request, "contract.html", _contract_ctx(q, db.get_lead(q["lead_id"])))


@app.get("/q/{token}")
def quote_page(request: Request, token: str, paid: int = 0):
    q = db.get_quote_by_token(token)
    if not q or (q["status"] == "draft" and not _is_admin(request)):
        return _redirect("/")
    pipeline.view_quote(q)
    if paid and q["status"] != "paid":                      # กลับจาก Stripe แต่ webhook ยังไม่มา → ถาม Stripe ตรง ๆ
        stripe_pay.sync_session(db.get_quote(q["id"]))
    q = db.get_quote(q["id"])
    ctx = _quote_ctx(q, db.get_lead(q["lead_id"]))
    ctx["just_paid"] = bool(paid) and q["status"] != "paid"
    return templates.TemplateResponse(request, "quote.html", ctx)


@app.post("/q/{token}/cycle")
def quote_cycle(request: Request, token: str, cycle: str = Form(""), website: str = Form("")):
    """ลูกค้าเลือกรายปี/รายเดือนเองก่อนยืนยัน"""
    q = db.get_quote_by_token(token)
    if not q or website.strip():
        return _redirect("/")
    err = pipeline.change_cycle(q, cycle, "customer")
    if err:
        return templates.TemplateResponse(request, "quote.html", _quote_ctx(q, db.get_lead(q["lead_id"]), error=err))
    return _redirect(f"/q/{token}")


@app.post("/q/{token}/stripe")
def quote_stripe(request: Request, token: str):
    """ลูกค้าเลือกจ่ายด้วยบัตร → สร้าง Checkout แล้วพาไป Stripe · กลับมาที่หน้านี้พร้อม ?paid=1"""
    q = db.get_quote_by_token(token)
    if not q:
        return _redirect("/")
    if q["status"] not in ("accepted", "claimed"):
        return _redirect(f"/q/{token}")
    lead = db.get_lead(q["lead_id"])
    try:
        url = stripe_pay.create_checkout(q, lead, _public_base(), _company().get("name") or "เจอ.AI")
    except Exception as e:
        return templates.TemplateResponse(request, "quote.html", _quote_ctx(q, lead, error=f"เปิดหน้าชำระเงินไม่ได้: {str(e)[:160]}"))
    pipeline.record(lead["id"], "note", f"ลูกค้าเปิดหน้าชำระ Stripe ({q['number']})", "customer")
    return _redirect(url)


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    """Stripe ยิง event มาที่นี่ — ตรวจลายเซ็นก่อนเสมอ · ตอบ 200 เสมอเมื่อลายเซ็นถูก (กัน Stripe ยิงซ้ำไม่รู้จบ) แต่บันทึกผลไว้"""
    body = await request.body()
    if not stripe_pay.verify(body, request.headers.get("stripe-signature", "")):
        return Response("bad signature", status_code=400)
    try:
        evt = json.loads(body or b"{}")
    except Exception:
        return Response("bad json", status_code=400)
    try:
        res = stripe_pay.handle_event(evt)
    except Exception as e:
        res = f"error:{type(e).__name__}: {str(e)[:120]}"
        db.notify_admins(f"⚠️ Stripe webhook {evt.get('type')} ทำไม่สำเร็จ: {res}", "/admin/decisions", "payment")
    return JSONResponse({"ok": True, "result": res})





@app.post("/q/{token}/accept")
def quote_accept(request: Request, token: str, name: str = Form(""), agree: str = Form(""), website: str = Form(""),
                 buyer_name: str = Form(""), buyer_tax_id: str = Form(""), buyer_branch: str = Form(""), buyer_address: str = Form("")):
    q = db.get_quote_by_token(token)
    if not q or website.strip():
        return _redirect("/")
    lead = db.get_lead(q["lead_id"])
    if not agree:
        return templates.TemplateResponse(request, "quote.html", _quote_ctx(q, lead, error="ติ๊กยอมรับเงื่อนไขและสัญญาก่อน"))
    if q["status"] in ("sent", "viewed") and (buyer_name or buyer_tax_id or buyer_address):
        from . import accounting
        err = accounting.save_buyer(q["id"], buyer_name, buyer_tax_id, buyer_branch, buyer_address)
        if err:
            return templates.TemplateResponse(request, "quote.html", _quote_ctx(q, lead, error=err))
    err = pipeline.accept_quote(q, name, _client_ip(request))
    if err:
        return templates.TemplateResponse(request, "quote.html", _quote_ctx(q, lead, error=err))
    return _redirect(f"/q/{token}")


@app.post("/q/{token}/pay")
async def quote_pay(request: Request, token: str, slip: UploadFile = File(None), website: str = Form("")):
    q = db.get_quote_by_token(token)
    if not q or website.strip():
        return _redirect("/")
    if q["status"] not in ("accepted", "claimed"):
        return _redirect(f"/q/{token}")
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
            fname = f"q{q['id']}-{_sec.token_hex(6)}{ext}"
            (up / fname).write_bytes(data[:8_000_000])
            slip_path = f"slips/{fname}"
    if q["status"] == "claimed":                       # แจ้งซ้ำ = อัปเดตสลิป ไม่สร้างข้อเสนอใหม่
        db.update_quote(q["id"], slip_path=slip_path or q["slip_path"], claimed_at=db.now())
    else:
        pipeline.claim_payment(q, slip_path)
    return _redirect(f"/q/{token}")


def _lead_ctx(lead, notice: str = "") -> dict:
    rep = json.loads(lead["audit_json"]) if lead["audit_json"] else None
    pending = [dict(d) for d in db.list_decisions("proposed", 300) if d["lead_id"] == lead["id"]]
    last_draft = None                                                   # ข้อความทักล่าสุดที่ CEO ร่าง (ให้คัดลอกไปส่งเองเมื่อมีแต่ LINE)
    for d in db.list_decisions(None, 300):
        if d["lead_id"] == lead["id"] and d["kind"] == "outreach":
            try:
                pl = json.loads(d["payload_json"] or "{}")
            except Exception:
                pl = {}
            if pl.get("body"):
                last_draft = {"subject": pl.get("subject", ""), "body": pl["body"], "when": d["created_at"]}
                break
    from . import followup as _fu
    _h = _fu.history(lead["id"])
    from . import success as _cs
    from . import accounting as _ac
    _qs = db.list_quotes(lead["id"])
    _bq = next((x for x in _qs if x["status"] in ("paid", "accepted", "claimed")), None)
    return {"acct_docs": [dict(x) for x in db.list_acct_docs(lead_id=lead["id"], limit=50)], "acct_title": _ac.title,
            "acct_status": _ac.STATUS_TH, "buyer_quote": dict(_bq) if _bq else None,
            "cs": _cs.status(lead) if lead["status"] == "won" else None,
            "fu": {"n": _h["n"], "next": _fu.next_step(lead), "why": _fu.eligible(lead), "replied": _h["replied"], "steps": len(_fu.STEPS)},
            "deep": deep_audit.tier1(lead), "deep_ai": deep_audit.tier2(lead), "deep_progress": deep_audit.progress(lead),
            "ai_today": deep_audit.ai_today(), "ai_daily": deep_audit.ai_daily(),
            "lead": dict(lead), "sc": leads.score_audit(rep) if rep else None, "summary": leads.public_summary(rep) if rep else [],
            "events": [dict(e) for e in db.list_lead_events(lead["id"])], "labels": leads.EVENT_LABEL, "last_draft": last_draft,
            "channel": pipeline.send_channel(lead),
            "quotes": [dict(x) for x in db.list_quotes(lead["id"])], "qstatus": quotes.STATUS_LABEL, "pending": pending,
            "statuses": leads.STATUSES, "sources": leads.SOURCES, "kinds": execs.KINDS, "packages": quotes.PACKAGES,
            "plans": billing.PLANS, "setup_price": quotes.setup_addon()["price"], "yearly_pct": "%g" % quotes.yearly_discount(),
            "needs_setup": quotes.needs_setup(rep) if rep else False, "mandate": execs.MANDATE, "notice": notice,
            "default_cycle": quotes.default_cycle()}


@app.get("/admin/leads/{lead_id}")
def admin_lead(request: Request, lead_id: int, notice: str = ""):
    if not _is_admin(request):
        return _redirect("/app" if _tid(request) else "/login")
    lead = db.get_lead(lead_id)
    if not lead:
        return _redirect("/admin/leads")
    return templates.TemplateResponse(request, "admin_lead.html", _lead_ctx(lead, notice))


@app.post("/admin/leads/{lead_id}/note")
def admin_lead_note(request: Request, lead_id: int, note: str = Form("")):
    if not _is_admin(request):
        return _redirect("/login")
    if note.strip() and db.get_lead(lead_id):
        db.add_lead_event(lead_id, "note", note.strip()[:500], request.session.get("email") or "admin")
    return _redirect(f"/admin/leads/{lead_id}")


@app.post("/admin/leads/{lead_id}/unsubscribe")
def admin_lead_unsubscribe(request: Request, lead_id: int):
    if not _is_admin(request):
        return _redirect("/login")
    lead = db.get_lead(lead_id)
    if lead and lead["status"] != "unsubscribed":
        pipeline.unsubscribe(lead, request.session.get("email") or "admin")
    return _redirect(f"/admin/leads/{lead_id}?notice=บันทึกว่าลูกค้าขอไม่รับการติดต่อแล้ว")


@app.post("/admin/leads/{lead_id}/find-email")
def admin_lead_find_email(request: Request, lead_id: int):
    """หาอีเมลซ้ำ (เว็บ + ผลค้นหา) — เจอแล้ว CEO เสนอ/ส่งต่อเอง"""
    if not _is_admin(request):
        return _redirect("/login")
    lead = db.get_lead(lead_id)
    if not lead:
        return _redirect("/admin/leads")
    try:
        r = prospecting.refind_contact(lead)
    except Exception as e:
        return _redirect(f"/admin/leads/{lead_id}?notice=หาไม่สำเร็จ: {type(e).__name__}")
    if r["email"]:
        note = f"พบอีเมล {r['email']} (จาก{r['source']})" + (" — CEO ดำเนินการต่อแล้ว" if r["proposed"] else "")
    else:
        note = "ยังไม่พบอีเมลธุรกิจทั้งบนเว็บและผลค้นหา — ส่งข้อความด้านล่างทาง LINE เองได้" + (f" · LINE: {r['line']}" if r["line"] else "")
    return _redirect(f"/admin/leads/{lead_id}?notice={note}")


@app.post("/admin/leads/{lead_id}/mark-contacted")
def admin_lead_mark_contacted(request: Request, lead_id: int, via: str = Form("LINE")):
    """เจ้าของส่งข้อความเองทางช่องทางที่ระบบส่งไม่ได้ (เช่น LINE ของลูกค้า) → บันทึกว่าทักแล้ว"""
    if not _is_admin(request):
        return _redirect("/login")
    lead = db.get_lead(lead_id)
    if lead and lead["status"] in ("new", "lost"):
        db.update_lead(lead_id, status="contacted", last_contacted_at=db.now())
        db.add_lead_event(lead_id, "contacted", f"ส่งเองทาง {via.strip()[:40] or 'LINE'}", request.session.get("email") or "admin")
    return _redirect(f"/admin/leads/{lead_id}?notice=บันทึกว่าทักแล้วทาง {via.strip()[:40] or 'LINE'}")


@app.post("/admin/leads/{lead_id}/quote")
def admin_lead_quote(request: Request, lead_id: int, package: str = Form("pro"), discount_pct: str = Form("0"),
                     setup: str = Form(""), send: str = Form(""), cycle: str = Form("")):
    """แอดมินสร้างใบเสนอราคาเอง = การตัดสินใจของเจ้าของ ส่งได้ทันที (CFO ยังบอกว่าอยู่ในกรอบไหม)"""
    if not _is_admin(request):
        return _redirect("/login")
    lead = db.get_lead(lead_id)
    if not lead:
        return _redirect("/admin/leads")
    if lead["status"] == "unsubscribed":
        return _redirect(f"/admin/leads/{lead_id}?notice=ลูกค้าขอไม่รับการติดต่อ — สร้างใบเสนอราคาไม่ได้")
    try:
        disc = float(discount_pct or 0)
    except ValueError:
        disc = 0.0
    who = request.session.get("email") or "admin"
    try:
        q = pipeline.create_quote(lead, package, disc, bool(setup), created_by=who, cycle=cycle if cycle in quotes.CYCLES else quotes.default_cycle())
    except ValueError as e:
        return _redirect(f"/admin/leads/{lead_id}?notice={e}")
    cfo = execs.cfo_check({"price": q["price"], "discount_pct": q["discount_pct"], "cycle": quotes.cycle_of(q)})
    note = (pipeline.send_quote(q, who) if send else f"สร้าง {q['number']} เป็นร่างแล้ว") + f" · CFO: {cfo['note']}"
    return _redirect(f"/admin/leads/{lead_id}?notice={note}")


@app.post("/admin/quotes/{qid}/send")
def admin_quote_send(request: Request, qid: int):
    if not _is_admin(request):
        return _redirect("/login")
    q = db.get_quote(qid)
    if not q:
        return _redirect("/admin/leads")
    note = pipeline.send_quote(q, request.session.get("email") or "admin")
    for d in db.list_decisions("proposed", 300):                     # ข้อเสนอ 'ใบเสนอราคา' ของใบนี้ถือว่าอนุมัติแล้ว
        if d["kind"] == "quote" and json.loads(d["payload_json"] or "{}").get("quote_id") == qid:
            db.set_decision(d["id"], "executed", request.session.get("email") or "admin", note)
    return _redirect(f"/admin/leads/{q['lead_id']}?notice={note}")


@app.post("/admin/quotes/{qid}/confirm")
def admin_quote_confirm(request: Request, qid: int):
    """เจ้าของเช็กบัญชีแล้วยืนยันยอดเข้า → เปิดบัญชี+แบรนด์ (ข้อเสนอ 'ยืนยันยอดเข้า' ของ CFO ถือว่าอนุมัติ)"""
    if not _is_admin(request):
        return _redirect("/login")
    q = db.get_quote(qid)
    if not q:
        return _redirect("/admin/leads")
    who = request.session.get("email") or "admin"
    if q["status"] != "paid" and not q["slip_path"] and stripe_pay.session_paid(q) is False:
        # ลูกค้าเปิดหน้าจ่าย Stripe แล้วแต่ยังไม่จ่าย — ห้ามยืนยันด้วยมือ (เคยเกิด: กดยืนยันแล้วระบบเปิดบัญชี + ส่งแผน 30 วันให้คนที่ยังไม่จ่าย)
        note = "ยืนยันไม่ได้ — Stripe ยังไม่ได้รับเงินใบนี้ (ลูกค้าเปิดหน้าจ่ายแต่ยังไม่จ่าย) ถ้าลูกค้าโอนทางอื่น ให้แนบสลิปก่อนแล้วค่อยยืนยัน"
        return _redirect(f"/admin/leads/{q['lead_id']}?notice={note}")
    try:
        note = pipeline.confirm_payment(q, who)
    except Exception as e:
        note = f"ยืนยันไม่สำเร็จ: {type(e).__name__}: {str(e)[:120]}"
    for d in db.list_decisions("proposed", 300):
        if d["kind"] == "payment" and json.loads(d["payload_json"] or "{}").get("quote_id") == qid:
            db.set_decision(d["id"], "executed", who, note)
    return _redirect(f"/admin/leads/{q['lead_id']}?notice={note}")


@app.post("/admin/quotes/{qid}/cancel")
def admin_quote_cancel(request: Request, qid: int):
    if not _is_admin(request):
        return _redirect("/login")
    q = db.get_quote(qid)
    if not q:
        return _redirect("/admin/leads")
    who = request.session.get("email") or "admin"
    db.update_quote(qid, status="cancelled")
    db.add_lead_event(q["lead_id"], "note", f"ยกเลิกใบเสนอราคา {q['number']}", who)
    for d in db.list_decisions("proposed", 300):
        if json.loads(d["payload_json"] or "{}").get("quote_id") == qid:
            db.set_decision(d["id"], "cancelled", who, "ยกเลิกใบเสนอราคา")
    return _redirect(f"/admin/leads/{q['lead_id']}?notice=ยกเลิก {q['number']} แล้ว")


@app.get("/admin/quotes/{qid}/slip")
def admin_quote_slip(request: Request, qid: int):
    if not _is_admin(request):
        return _redirect("/login")
    q = db.get_quote(qid)
    if not q or not q["slip_path"]:
        return Response("not found", status_code=404)
    f = BASE.parent / "uploads" / q["slip_path"]
    if not f.exists():
        return Response("not found", status_code=404)
    ct = {".png": "image/png", ".webp": "image/webp", ".pdf": "application/pdf"}.get(f.suffix.lower(), "image/jpeg")
    return Response(f.read_bytes(), media_type=ct)


@app.post("/admin/decisions/auto")
def admin_decisions_auto(request: Request, kind: str = Form(""), on: str = Form("0"), override: str = Form("")):
    """สลับเรื่องหนึ่งเป็น 'ตัดสินเองในกรอบ' — โค้ดตรวจด่าน (≥ 20 เรื่อง · ≥ 95%) · override = เจ้าของปลดล็อกเองโดยไม่รอด่าน"""
    if not _is_admin(request):
        return _redirect("/login")
    if kind not in execs.KINDS or kind == "digest":
        return _redirect("/admin/decisions?notice=ไม่รู้จักเรื่องนี้")
    ok, reason = execs.set_auto(kind, on == "1", override=bool(override))
    note = (f"{execs.KINDS[kind]}: {'ตัดสินเองในกรอบตั้งแต่ตอนนี้' + (' (ปลดล็อกโดยเจ้าของ ไม่รอด่าน)' if override else '') if on == '1' else 'กลับเป็นโหมดเสนอ'}" if ok else f"สลับไม่ได้ — {reason}")
    if ok and on == "1":
        done = pipeline.run_pending_auto(kind)                 # ข้อเสนอที่ค้างอยู่ก่อนสลับ → ทำให้เลย
        if done:
            note += f" · ทำข้อเสนอที่ค้าง {done} เรื่องให้แล้ว"
    return _redirect(f"/admin/decisions?notice={note}")


@app.post("/admin/agents/pause")
def admin_agents_pause(request: Request, on: str = Form("1")):
    if not _is_admin(request):
        return _redirect("/login")
    execs.set_paused(on == "1")
    return _redirect("/admin/decisions?notice=" + ("หยุดฉุกเฉินแล้ว — ทุกเรื่องรอคุณ" if on == "1" else "เริ่มทำงานต่อแล้ว"))


@app.post("/line/webhook")
async def line_webhook(request: Request):
    """LINE ส่ง event มาที่นี่ — ตรวจลายเซ็นก่อนเสมอ (ไม่ตรง = ไม่ใช่ LINE) · Verify ของ LINE ส่ง events ว่างต้องได้ 200"""
    body = await request.body()
    if not line_oa.verify(body, request.headers.get("x-line-signature", "")):
        return Response("bad signature", status_code=403)
    try:
        data = json.loads(body or b"{}")
    except Exception:
        return Response("bad json", status_code=400)
    try:
        handled = pipeline.handle_line_webhook(data)
    except Exception as e:
        handled = [f"error:{type(e).__name__}"]
    return JSONResponse({"ok": True, "handled": handled})


@app.get("/account/password")
def account_password_page(request: Request):
    tid = _tid(request)
    if not tid:
        return _redirect("/login")
    return templates.TemplateResponse(request, "account_password.html", {"email": db.get_tenant(tid)["email"], "notice": "", "ok": True})


@app.post("/account/password")
def account_password(request: Request, current: str = Form(""), new: str = Form(""), confirm: str = Form("")):
    tid = _tid(request)
    if not tid:
        return _redirect("/login")
    t = db.get_tenant(tid)
    if not verify_pw(current, t["password_hash"]):
        notice, ok = "รหัสผ่านปัจจุบันไม่ถูกต้อง", False
    elif len(new) < 8:
        notice, ok = "รหัสผ่านใหม่ต้องยาวอย่างน้อย 8 ตัวอักษร", False
    elif new != confirm:
        notice, ok = "รหัสผ่านใหม่สองช่องไม่ตรงกัน", False
    else:
        db.set_password(tid, hash_pw(new))
        notice, ok = "เปลี่ยนรหัสผ่านแล้ว", True
    return templates.TemplateResponse(request, "account_password.html", {"email": t["email"], "notice": notice, "ok": ok})


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
            "is_admin": bool(tenant and tenant["is_admin"]),
            "stripe_portal": bool(tenant and tenant["stripe_customer_id"] and stripe_pay.available()),
            "stripe_invoices": [dict(r) for r in db.list_stripe_invoices(tid)],
            "acct_docs": [dict(r) for r in db.list_acct_docs(tenant_id=tid, limit=50) if r["status"] != "void"]}


@app.get("/account/billing")
def account_billing(request: Request):
    """ลูกค้าที่จ่ายผ่าน Stripe → Customer Portal (เปลี่ยนบัตร ดูใบแจ้งหนี้ ยกเลิก) · ต้องเปิด portal ใน Stripe Dashboard ครั้งเดียว"""
    tid = _tid(request)
    if not tid:
        return _redirect("/login")
    t = db.get_tenant(tid)
    if not (t and t["stripe_customer_id"] and stripe_pay.available()):
        return _redirect("/upgrade")
    try:
        return _redirect(stripe_pay.portal_url(t["stripe_customer_id"], f"{_public_base()}/upgrade"))
    except Exception as e:
        return templates.TemplateResponse(request, "upgrade.html", _upgrade_ctx(request, notice=f"เปิดหน้าจัดการการสมัครไม่ได้: {str(e)[:160]}"))


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
            txt = ai_client.site_context(geo_content._site_url(brand))
            db.set_brand_site_context(brand_id, txt or "")
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
    # สถานะจริงของ auto สร้างร่าง — สัปดาห์นี้กี่ชิ้น, คิวถัดไปคืออะไร, ติดอะไร (กติกาเดียวกับตัวที่รันจริง)
    auto_status = None
    wk = brand["auto_content_weekly"] or 0
    if wk > 0:
        now = _dt.datetime.now()
        gaps = db.get_content_gaps(brand_id)
        n_week = db.count_content_since(brand_id, (now - _dt.timedelta(days=7)).isoformat(timespec="seconds"))
        run_days = brand["auto_run_days"] if brand["auto_run_days"] is not None else int(os.getenv("GEO_RUN_INTERVAL_DAYS", "7"))
        # last_run_at ไม่ใช่คอลัมน์ของ brands (list_all_brands คำนวณให้) — ใช้รอบล่าสุดจาก gaps แทน
        nxt = auto_content.next_monitor_at(gaps["last_run_at"], run_days, brand["auto_run_time"], now)
        last_item = max((ci["created_at"] for ci in content if ci["source"] in ("auto", "autopilot") and ci["created_at"]), default=None)
        auto_status = auto_content.status(wk, n_week, brand["last_auto_content_at"], gaps, ok, now, next_run_at=nxt, last_item_at=last_item)
    return templates.TemplateResponse(request, "brand_content.html",
        {"brand": brand, "questions": questions, "content": content,
         "can_generate": ok, "limit_msg": limit_msg if not ok else None,
         "publish_eta": publish_eta, "aeo_scores": aeo_scores, "aeo_summary": aeo_summary,
         "can_images": billing.feature(db.get_tenant(brand["tenant_id"]), "images"),
         "auto_status": auto_status, "auto_topup": (auto_content.TOPUP_N, auto_content.TOPUP_MAX_Q),
         "image_cap": image_finder.describe(image_finder.capabilities()) if brand["auto_image"] else None,
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
        existing = [qq["question"] for qq in db.list_questions(brand_id)]
        items = ai_client.generate_questions(
            brand["name"], brand["domain"], brand["market"] or "", existing=existing
        )
        picked = auto_content.pick_new(items, existing, len(items))  # ตัดที่ซ้ำของเดิม/ซ้ำกันเอง
        for item in picked:
            db.add_question(brand_id, item["question"], item["lang"])
        added = len(picked)
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
        "running": brand_id in _checking_brands,
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
        "running": brand_id in _ai_running,
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
        "running": brand_id in _gsc_running,
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
        "running": brand_id in _speed_running,
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


@app.post("/brands/{brand_id}/ai-serp/auto")
def set_brand_ai_serp_auto(request: Request, brand_id: int, on: str = Form("")):
    """เปิด/ปิดเช็ค Google AI อัตโนมัติรายแบรนด์ — ทุกคำถามกินโควตา SerpApi จึงไม่เปิดให้ทุกแบรนด์โดยอัตโนมัติ"""
    if not _brand_for(request, brand_id):
        return _redirect("/login")
    db.set_aiserp_auto(brand_id, bool(on))
    return _redirect(f"/brands/{brand_id}/ai-serp?saved=1")


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
    auto_brands = db.aiserp_auto_brands()
    return templates.TemplateResponse(request, "aiserp.html", {
        "brand": brand, "report": report, "matrix": matrix, "rivals": rivals,
        "kinds": ai_serp.KINDS, "available": ai_serp.available(),
        "running": brand_id in _aiserp_running, "saved": request.query_params.get("saved"),
        # โควตา: รอบนี้ของแบรนด์นี้ + รวมทุกแบรนด์ที่เปิด auto (×4 รอบ/เดือน) ให้ตัดสินใจเทียบแผน SerpApi ได้
        "est_round": ai_serp.searches_per_round(len(db.list_questions(brand_id))),
        "auto_brands": auto_brands,
        "auto_month": 4 * sum(ai_serp.searches_per_round(b["n_questions"]) for b in auto_brands),
        "max_q": ai_serp.MAX_Q,
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
    if not todo:
        # ไม่มีข้อที่ยังไม่มีคอนเทนต์ → กติกาเดียวกับ auto รายสัปดาห์: ปิดครบจริง = คิดคำถามใหม่เพิ่ม, ไม่งั้นบอกว่าติดอะไร
        qs = auto_content.queue_state(gaps, billing.check(tenant, "content")[0])
        if qs["action"] == "topup":
            added = _topup_questions(brand, qs["n"])
            return [{"status": "topup", "questions": [a["question"] for a in added]}]
        return [{"status": qs["action"], "msg": qs["text"]}]
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
    if not mailer.config():
        return "ไม่มี SMTP — ดูรายงานได้ที่หน้า รายงาน & ส่งออก"
    if not (tenant and mailer.is_deliverable(tenant["email"])):
        return f"ไม่ได้ส่งอีเมล — อีเมลเจ้าของแบรนด์ ({tenant['email'] if tenant else '-'}) ยังเป็นที่อยู่สมมติ แก้ที่แผงผู้ดูแล → ลูกค้า"
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


@office.track("delivery", lambda brand_id: f"รอบ Autopilot แบรนด์ #{brand_id}")
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
        "running": brand_id in _autopilot_running,
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
        if item["status"] == "published" and item["schema_json"]:  # แถว SQLite ไม่มี .get() — ใช้ [] ซึ่งทั้ง sqlite3.Row และ dict_row รองรับ
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


def _self_brand():
    """แบรนด์ของเจอ.AI เอง (settings self_brand_id) — ให้โดเมนแพลตฟอร์มมีหน้า GEO เหมือนลูกค้า"""
    bid = db.get_setting("self_brand_id")
    try:
        return db.get_brand(int(bid)) if bid else None
    except Exception:
        return None


@app.api_route("/geo", methods=["GET", "HEAD"])
def self_geo_index(request: Request):
    b = _self_brand()
    return hosted_index(request, b["embed_key"]) if b else Response("not found", status_code=404)


@app.api_route("/geo/{cid}", methods=["GET", "HEAD"])
def self_geo_article(request: Request, cid: int):
    b = _self_brand()
    return hosted_article(request, b["embed_key"], cid) if b else Response("not found", status_code=404)


@app.api_route("/llms.txt", methods=["GET", "HEAD"])
def self_llms():
    b = _self_brand()
    return embed_llms(b["embed_key"]) if b else PlainTextResponse("# not found", status_code=404)


@app.api_route(geo_content.SITEMAP_PATH, methods=["GET", "HEAD"])
def self_sitemap():
    b = _self_brand()
    return embed_sitemap(b["embed_key"]) if b else PlainTextResponse("not found", status_code=404)


@app.api_route("/robots.txt", methods=["GET", "HEAD"])
def self_robots():
    """robots.txt ของโดเมนแพลตฟอร์ม — เปิดให้บอท AI และชี้ sitemap เหมือนที่แนะนำลูกค้า (ก่อนหน้านี้ตอบ 404)"""
    b = _self_brand()
    if b:
        return embed_robots(b["embed_key"])
    return PlainTextResponse("User-agent: *\nAllow: /\n")


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
        "tenants": tenants, "all_brands": all_brands, "plans": billing.PLANS, "error": error, "trial_days": billing.TRIAL_DAYS,
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
    # error จาก redirect ของฟอร์มแก้อีเมล (รูปแบบผิด/ซ้ำ) — เทมเพลตมี {% if error %} รออยู่แล้ว
    return templates.TemplateResponse(request, "admin.html", {**_admin_ctx(), "error": request.query_params.get("error")})


@app.post("/admin/tenants")
def admin_create_tenant(request: Request, email: str = Form(...), password: str = Form(...), name: str = Form("")):
    if not _is_admin(request):
        return _redirect("/login")
    if db.get_tenant_by_email(email):
        return templates.TemplateResponse(request, "admin.html", _admin_ctx(error=f"อีเมล {email} ถูกใช้แล้ว"))
    db.create_tenant(email, hash_pw(password), name, is_admin=False)
    return _redirect("/admin")


@app.post("/admin/tenants/{tid}/email")
def admin_set_email(request: Request, tid: int, email: str = Form(...)):
    """แก้อีเมลบัญชีลูกค้า/แอดมิน — บัญชีที่สร้างตอนตั้งระบบใช้ @geo.local ฯลฯ ซึ่งแคมเปญ/Autopilot ส่งไปแล้วไม่มีใครได้รับ"""
    if not _is_admin(request):
        return _redirect("/login")
    import re as _re
    e = (email or "").strip().lower()
    if not _re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e):
        return _redirect("/admin?error=" + __import__("urllib.parse").parse.quote("รูปแบบอีเมลไม่ถูกต้อง"))
    if not db.set_email(tid, e):
        return _redirect("/admin?error=" + __import__("urllib.parse").parse.quote(f"อีเมล {e} ถูกใช้กับบัญชีอื่นแล้ว"))
    return _redirect("/admin")


@app.post("/admin/tenants/{tid}/trial")
def admin_extend_trial(request: Request, tid: int, days: int = Form(14)):
    """ต่อเวลาทดลองใช้แผนฟรี — นับต่อจากวันหมดเดิม (หรือจากวันนี้ถ้าหมดไปแล้ว)"""
    import datetime as _dt
    if not _is_admin(request):
        return _redirect("/login")
    t = db.get_tenant(tid)
    if not t:
        return _redirect("/admin")
    tr = billing.trial(t)
    base = max(_dt.date.today(), _dt.date.fromisoformat(tr["ends"])) if tr.get("ends") else _dt.date.today()
    new = base + _dt.timedelta(days=max(1, min(90, int(days))))
    db.set_trial_until(tid, new.isoformat())
    db.add_notification(tid, f"ต่อเวลาทดลองใช้ฟรีถึง {new.strftime('%d/%m/')}{new.year + 543} แล้ว", "/upgrade", "plan")
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
    from . import followup as _fu
    return {
        "followup_on": _fu.enabled(),
        "sms": sms.public_config(), "sms_balance": sms.balance() if sms.config() else "",
        "cs_on": (db.get_setting("cs_on") or "1") == "1",
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
        # โมเดลต่องาน — ตอนนี้มีแค่ตัวคิดคำถาม (local | gemini)
        "llm_tasks": ai_client.TASKS, "llm_choices": ai_client.MODEL_CHOICES,
        "llm_status": {t: ai_client.task_status(t) for t in ai_client.TASKS},
        # เฟส 2: LINE OA + ข้อมูลบริษัทบนใบเสนอราคา/สัญญา
        "line": line_oa.public_config(), "company": _company(), "public_base": _public_base(), "prospect": prospecting.public_config(),
        "stripe": stripe_pay.public_config(),
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


@app.post("/admin/settings/llm")
def admin_settings_llm(request: Request, task: str = Form(""), model: str = Form("local")):
    """เลือกโมเดลต่องาน — ค่าอยู่ใน settings llm_<task> มีผลทันทีกับทั้งปุ่มสร้างคำถามและการเติมคำถามอัตโนมัติ"""
    if not _is_admin(request):
        return _redirect("/login")
    if task in ai_client.TASKS and model in dict(ai_client.MODEL_CHOICES):
        db.set_setting(f"llm_{task}", model)
    return templates.TemplateResponse(request, "admin_settings.html", _settings_ctx(request, saved=True))


@app.post("/admin/settings/line")
def admin_settings_line(request: Request, token: str = Form(""), secret: str = Form(""), unlink: str = Form(""), bot_name: str = Form("")):
    if not _is_admin(request):
        return _redirect("/login")
    if unlink:
        db.set_setting("line_owner_user_id", "")
    else:
        line_oa.save_config(token, secret)
        db.set_setting("line_bot_name", bot_name.strip()[:60])
    return templates.TemplateResponse(request, "admin_settings.html", _settings_ctx(request, saved=True))


@app.post("/admin/settings/prospect")
def admin_settings_prospect(request: Request, segments: str = Form(""), daily: str = Form("10"), enabled: str = Form(""), followup_on: str = Form(""), cs_on: str = Form(""),
                            maps: str = Form("")):
    if not _is_admin(request):
        return _redirect("/login")
    prospecting.save_config(segments, daily, bool(enabled), maps=bool(maps) if prospecting.maps_available() else None)
    from . import followup as _fu
    _fu.set_enabled(bool(followup_on))
    from . import success as _cs
    _cs.set_enabled(bool(cs_on))
    return templates.TemplateResponse(request, "admin_settings.html", _settings_ctx(request, saved=True))


@app.post("/admin/prospect/run")
def admin_prospect_run(request: Request, limit: str = Form("")):
    """หาลูกค้าตอนนี้ — รันเบื้องหลัง (ตรวจเว็บละราวครึ่งนาที) แล้วดูผลที่หน้าลูกค้าเป้าหมาย"""
    if not _is_admin(request):
        return _redirect("/login")
    try:
        n = int(limit or 0) or None
    except ValueError:
        n = None
    pipeline._spawn(lambda: prospecting.run(n))
    return _redirect("/admin/leads?notice=เริ่มหาลูกค้าแล้ว ใช้เวลาราวครึ่งนาทีต่อเว็บ รีเฟรชหน้านี้สักครู่ · ข้อเสนอทักจะไปรอที่หน้าการตัดสินใจ")


@app.post("/admin/leads/{lead_id}/contact")
def admin_lead_contact(request: Request, lead_id: int, name: str = Form(""), email: str = Form(""), contact: str = Form("")):
    if not _is_admin(request):
        return _redirect("/login")
    lead = db.get_lead(lead_id)
    if not lead:
        return _redirect("/admin/leads")
    import re as _re
    e = email.strip().lower()
    if e and not _re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e):
        return _redirect(f"/admin/leads/{lead_id}?notice=รูปแบบอีเมลไม่ถูกต้อง")
    old = db.get_lead(lead_id)
    db.update_lead(lead_id, name=name.strip()[:120] or None, email=e[:160] or None, contact=contact.strip()[:160] or None,
                   **({"email_src": "manual" if e else None} if old and (e or None) != (old["email"] or None) else {}))   # คนกรอกเอง = ไม่ใช่อีเมลเดา
    db.add_lead_event(lead_id, "note", f"แก้ช่องทางติดต่อ: {e or '—'} · {contact.strip() or '—'}", request.session.get("email") or "admin")
    return _redirect(f"/admin/leads/{lead_id}?notice=บันทึกช่องทางติดต่อแล้ว")


def _marketing_ctx(request: Request, notice: str = "", error: str = ""):
    sb = marketing.self_brand()
    posts = []
    for x in db.list_posts(60):
        d = dict(x)
        d["channel_label"] = marketing.CHANNELS.get(d["channel"], d["channel"])
        d["kind_label"] = marketing.POST_KINDS.get(d["kind"], d["kind"])
        posts.append(d)
    brands = [dict(b) for b in db.list_all_brands() if not (sb and b["id"] == sb["id"])]
    drafts = [i for i in (db.list_content(sb["id"]) if sb else []) if i["status"] == "draft"]
    return {"cfg": marketing.public_config(), "posts": posts, "brands": brands, "self_brand": dict(sb) if sb else None,
            "self_drafts": len(drafts), "attr7": marketing.attribution(7), "attr28": marketing.attribution(28),
            "gate": execs.auto_gate("post"), "notice": notice, "error": error, "base": _public_base()}


@app.get("/admin/marketing")
def admin_marketing(request: Request, notice: str = ""):
    if not _is_admin(request):
        return _redirect("/app" if _tid(request) else "/login")
    return templates.TemplateResponse(request, "admin_marketing.html", _marketing_ctx(request, notice))


@app.post("/admin/marketing/settings")
def admin_marketing_settings(request: Request, enabled: str = Form(""), weekly: str = Form("3"), line: str = Form(""),
                             fb_page_id: str = Form(""), fb_token: str = Form(""), fb_app_id: str = Form(""), fb_app_secret: str = Form("")):
    if not _is_admin(request):
        return _redirect("/login")
    note = marketing.save_config(bool(enabled), weekly, bool(line), fb_page_id, fb_token, fb_app_id, fb_app_secret)
    return _redirect(f"/admin/marketing?notice={note}")


@app.post("/admin/marketing/run")
def admin_marketing_run(request: Request):
    """รันรอบ CMO ตอนนี้ (เบื้องหลัง: เผยแพร่ร่างที่ผ่าน QA · เสนอโพสต์ · เคสศึกษา)"""
    if not _is_admin(request):
        return _redirect("/login")
    pipeline._spawn(lambda: marketing.run())
    return _redirect("/admin/marketing?notice=เริ่มรอบการตลาดแล้ว รีเฟรชหน้านี้สักครู่ · โพสต์ที่เสนอจะไปรอที่หน้าการตัดสินใจ (หรือโพสต์เองถ้าเปิดโหมดตัดสินเอง)")


@app.post("/admin/marketing/fb-test")
def admin_marketing_fb_test(request: Request):
    if not _is_admin(request):
        return _redirect("/login")
    return _redirect(f"/admin/marketing?notice=Facebook: {marketing.fb_test_post()}")


@app.post("/admin/marketing/brand/{brand_id}/case-study")
def admin_marketing_case_study(request: Request, brand_id: int, ok: str = Form("0")):
    if not _is_admin(request):
        return _redirect("/login")
    db.set_brand_case_study(brand_id, ok == "1")
    return _redirect("/admin/marketing?notice=อัปเดตการอนุญาตใช้ชื่อในเคสศึกษาแล้ว")


def _skills_ctx(request: Request, notice: str = "", error: str = "", edit: str = ""):
    items = skills.summary()
    for it in items:
        it["text"] = skills.load(it["name"])
    return {"skills": items, "edit": edit, "mandate": execs.MANDATE.as_dict(), "mandate_default": execs.MANDATE_DEFAULT,
            "mandate_label": execs.MANDATE_LABEL, "task_skill": skills.TASK_SKILL, "tasks": ai_client.TASKS, "notice": notice, "error": error}


@app.get("/admin/skills")
def admin_skills(request: Request, notice: str = "", edit: str = ""):
    if not _is_admin(request):
        return _redirect("/app" if _tid(request) else "/login")
    return templates.TemplateResponse(request, "admin_skills.html", _skills_ctx(request, notice, edit=edit))


@app.post("/admin/skills/mandate")
async def admin_skills_mandate(request: Request):
    """แก้กรอบอำนาจทุกช่องที่มีในตาราง (ส่วนลด วงเงิน คืนเงิน เพดาน AI กำไรขั้นต่ำ ส่วนลดรายปี …) — มีผลทันที · บันทึกเป็นการตัดสินใจของบอร์ด"""
    if not _is_admin(request):
        return _redirect("/login")
    form = await request.form()
    changed = []
    for key in execs.MANDATE_DEFAULT:                    # อ่านทุกช่องของกรอบอำนาจ — ช่องที่เพิ่มทีหลังไม่ตกหล่น
        val = str(form.get(key) or "")
        if val.strip() == "":
            continue
        try:
            old_v = execs.MANDATE[key]
            execs.set_mandate(key, val)
            if execs.MANDATE[key] != old_v:
                changed.append(f"{execs.MANDATE_LABEL[key]}: {old_v} → {execs.MANDATE[key]}")
        except ValueError as e:
            return _redirect(f"/admin/skills?notice=ค่าไม่ถูกต้อง ({execs.MANDATE_LABEL.get(key, key)}): {e}")
    if changed:
        db.add_decision("ceo", "digest", "บอร์ดแก้กรอบอำนาจ", "\n".join(changed), {"mandate": execs.MANDATE.as_dict()}, status="auto")
    return _redirect("/admin/skills?notice=" + ("แก้กรอบอำนาจแล้ว: " + " · ".join(changed) if changed else "ไม่มีค่าที่เปลี่ยน"))


@app.post("/admin/skills/{name}/reset")
def admin_skill_reset(request: Request, name: str):
    if not _is_admin(request):
        return _redirect("/login")
    if name in skills.AGENTS:
        skills.reset(name)
    return _redirect(f"/admin/skills?notice=คืนค่าเริ่มต้นของทักษะ {skills.AGENTS.get(name, name)} แล้ว")


@app.post("/admin/skills/{name}")
def admin_skill_save(request: Request, name: str, text: str = Form("")):
    if not _is_admin(request):
        return _redirect("/login")
    try:
        skills.save(name, text)
    except ValueError as e:
        return _redirect(f"/admin/skills?notice={e}")
    return _redirect(f"/admin/skills?notice=บันทึกทักษะ {skills.AGENTS.get(name, name)} แล้ว มีผลกับงานถัดไปทันที")


@app.post("/admin/settings/stripe")
def admin_settings_stripe(request: Request, secret: str = Form(""), whsec: str = Form("")):
    """บันทึกคีย์ · ถ้าใส่ secret key แล้วยังไม่มี webhook secret → ตั้ง webhook ให้เองทันที"""
    if not _is_admin(request):
        return _redirect("/login")
    stripe_pay.save_config(secret, whsec)
    error = None
    if stripe_pay.available() and not stripe_pay.webhook_secret():
        try:
            stripe_pay.ensure_webhook(_public_base())
        except Exception as e:
            error = f"บันทึกคีย์แล้ว แต่ตั้ง webhook อัตโนมัติไม่สำเร็จ: {str(e)[:160]} — กด \"ตั้ง webhook ให้อัตโนมัติ\" อีกครั้ง หรือสร้างเองใน Dashboard"
    return templates.TemplateResponse(request, "admin_settings.html", _settings_ctx(request, saved=not error, error=error))


@app.post("/admin/settings/stripe/webhook")
def admin_settings_stripe_webhook(request: Request):
    if not _is_admin(request):
        return _redirect("/login")
    try:
        note = stripe_pay.ensure_webhook(_public_base())
        return templates.TemplateResponse(request, "admin_settings.html", _settings_ctx(request, saved=True) | {"stripe_note": note})
    except Exception as e:
        return templates.TemplateResponse(request, "admin_settings.html", _settings_ctx(request, error=f"ตั้ง webhook ไม่สำเร็จ: {str(e)[:160]}"))


@app.post("/admin/settings/company")
def admin_settings_company(request: Request, name: str = Form(""), address: str = Form(""), tax_id: str = Form(""),
                           signer: str = Form(""), contract_reviewed: str = Form(""), phone: str = Form(""), email: str = Form(""),
                           branch: str = Form(""), vat: str = Form("")):
    if not _is_admin(request):
        return _redirect("/login")
    from . import accounting
    tax_digits = "".join(ch for ch in tax_id if ch.isdigit())
    if tax_digits and not accounting.valid_tax_id(tax_digits):
        return templates.TemplateResponse(request, "admin_settings.html", _settings_ctx(request, error="เลขประจำตัวผู้เสียภาษีของบริษัทไม่ถูกต้อง (13 หลัก)"))
    if vat and not tax_digits:
        return templates.TemplateResponse(request, "admin_settings.html", _settings_ctx(request, error="เปิด VAT ต้องใส่เลขประจำตัวผู้เสียภาษี 13 หลักก่อน"))
    tax_id = tax_digits
    db.set_setting("company_branch", branch.strip()[:60] or "สำนักงานใหญ่")
    db.set_setting("company_vat", "1" if vat else "0")
    db.set_setting("company_name", name.strip()[:160])
    db.set_setting("company_address", address.strip()[:300])
    db.set_setting("company_tax_id", tax_id.strip()[:20])
    db.set_setting("company_signer", signer.strip()[:160])
    db.set_setting("company_phone", phone.strip()[:40])                 # ท้ายอีเมลถึงลูกค้า
    db.set_setting("company_email", email.strip()[:120])
    db.set_setting("contract_reviewed", "1" if contract_reviewed else "0")
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
                     from_: str = Form(""), from_name: str = Form(""), tls: str = Form("starttls"), test_to: str = Form("")):
    """บันทึกค่าที่กรอกไว้ก่อน แล้วส่งทดสอบถึงที่อยู่ที่กรอก (ดีฟอลต์ = อีเมลแอดมินที่ล็อกอิน) — รู้ทันทีว่า host/port/รหัส/โดเมนผู้ส่งถูก"""
    if not _is_admin(request):
        return _redirect("/login")
    mailer.save_config(host, port, user, password, from_, from_name, tls)
    me = db.get_tenant(_tid(request))
    to = (test_to or "").strip() or me["email"]
    try:
        mailer.send(to, "ทดสอบ SMTP จาก เจอ.AI",
                    mailer.wrap_html("SMTP ใช้งานได้", "<p>ถ้าคุณได้รับอีเมลนี้ แปลว่าการตั้งค่า SMTP ถูกต้อง แคมเปญและ Autopilot จะส่งรายงานผ่านช่องทางนี้</p>",
                                     f"ส่งจาก {BASE_URL}/admin/email"))
        notice = {"ok": True, "msg": f"ส่งอีเมลทดสอบถึง {to} แล้ว — เช็คกล่องจดหมาย (และ Spam)"}
        db.add_email_log(None, me["id"], to, None, True, "")
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


# ---------- ออฟฟิศเอเจนต์: ภาพ 3D ของทีมที่ขยับตามงานจริง (เจ้าของดู หรือเปิดค้างบนทีวีด้วย ?tv=1) ----------
@app.get("/admin/office")
def admin_office(request: Request):
    if not _is_admin(request):
        return _redirect("/app" if _tid(request) else "/login")
    return templates.TemplateResponse(request, "admin_office.html", {"actors": office.ACTORS})


@app.get("/admin/office/pending.json")
def admin_office_pending(request: Request):
    """ถาดเรื่องรออนุมัติในออฟฟิศ: เรื่องที่เสนอไว้ทั้งหมด พร้อมข้อเสนอให้อ่านและกดอนุมัติได้เลย"""
    if not _is_admin(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    out = []
    for d in db.list_decisions("proposed", 100):
        try:
            pl = json.loads(d["payload_json"] or "{}")
        except Exception:
            pl = {}
        lead = db.get_lead(d["lead_id"]) if d["lead_id"] else None
        out.append({"id": d["id"], "agent": execs.AGENTS.get(d["agent"], d["agent"]), "kind": d["kind"], "kind_th": execs.KINDS.get(d["kind"], d["kind"]),
                    "title": d["title"], "proposal": (d["proposal"] or "")[:1500], "at": d["created_at"], "escalate": bool(pl.get("escalate")),
                    "risky": bool(pl.get("risky_email")), "money": d["kind"] == "payment",
                    "lead_id": d["lead_id"], "domain": lead["domain"] if lead else ""})
    return JSONResponse({"items": out}, headers={"Cache-Control": "no-store"})


@app.get("/admin/office/state.json")
def admin_office_state(request: Request):
    if not _is_admin(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse(office.state(), headers={"Cache-Control": "no-store"})


@app.post("/admin/leads/{lead_id}/deep")
def admin_lead_deep(request: Request, lead_id: int, ai: str = Form("")):
    """แอดมินสั่งรายงานเต็ม: ชั้น 1 ใหม่ หรือวัด AI 20 คำถาม (ไม่นับเพดานต่อวัน เพราะคนตัดสินใจเอง)"""
    if not _is_admin(request):
        return _redirect("/login")
    lead = db.get_lead(lead_id)
    if not lead:
        return _redirect("/admin/leads")
    if ai:
        st = deep_audit.start(lead_id, ai=True, force_ai=True)
        note = "เริ่มถาม AI 20 คำถามแล้ว (ราว 3 นาที ≈ ฿40)" if st == "started" else "กำลังทำอยู่แล้ว"
    else:
        db.update_lead(lead_id, deep_json=None)
        st = deep_audit.start(lead_id)
        note = "เริ่มไต่เว็บใหม่แล้ว (ราว 2 นาที)" if st == "started" else "กำลังทำอยู่แล้ว"
    return _redirect(f"/admin/leads/{lead_id}?notice={note}")


# ---------- ฝ่ายพันธมิตร ----------
def _partner_public_ctx(**kw) -> dict:
    from . import partners
    return {"pct": partners.pct(), "months": partners.months(), "cookie_days": partners.COOKIE_DAYS, "error": "", "done": False,
            "form": {}, "plans": billing.PLANS, **kw}


@app.get("/partners")
def partners_page(request: Request, src: str = ""):
    return templates.TemplateResponse(request, "partners.html", _partner_public_ctx(src=_src(src)))


@app.post("/partners")
def partners_apply(request: Request, name: str = Form(""), company: str = Form(""), email: str = Form(""), phone: str = Form(""),
                   website: str = Form(""), clients: str = Form(""), agree: str = Form(""), src: str = Form(""), hp: str = Form("")):
    from . import partners
    form = {"name": name, "company": company, "email": email, "phone": phone, "website": website, "clients": clients}
    if hp.strip():
        return _redirect("/partners")
    if not agree:
        return templates.TemplateResponse(request, "partners.html", _partner_public_ctx(error="ติ๊กยอมรับกติกาพันธมิตรก่อน", form=form))
    err, _ = partners.apply(name, company, email, phone, website, clients, src=_src(src))
    if err:
        return templates.TemplateResponse(request, "partners.html", _partner_public_ctx(error=err, form=form))
    return templates.TemplateResponse(request, "partners.html", _partner_public_ctx(done=True))


def _partner_dash(request: Request, p, notice: str = "", error: str = ""):
    from . import partners
    st = partners.stats(p)
    return templates.TemplateResponse(request, "partner_dash.html", {"p": dict(p), "st": st, "ref_url": partners.ref_url(p) if p["code"] else "",
                                      "notice": notice, "error": error, "cst": partners.CSTATUS_TH, "lst": leads.STATUS_LABEL,
                                      "pct": partners.pct(), "months": partners.months()})


@app.get("/partner/{token}")
def partner_dashboard(request: Request, token: str, notice: str = ""):
    p = db.find_partner(token=token)
    if not p or p["status"] not in ("active", "paused"):
        return _redirect("/partners")
    return _partner_dash(request, p, notice)


@app.post("/partner/{token}/client")
def partner_client(request: Request, token: str, website: str = Form(""), email: str = Form(""), name: str = Form(""), consent: str = Form("")):
    from . import partners
    p = db.find_partner(token=token)
    if not p or p["status"] != "active":
        return _redirect("/partners")
    err = partners.submit_client(p, website, email, name, bool(consent))
    if err:
        return _partner_dash(request, p, error=err)
    return _redirect(f"/partner/{token}?notice=รับชื่อลูกค้าแล้ว — ตรวจเว็บและทีมขายจะติดต่อพร้อมบอกว่าคุณแนะนำมา")


@app.get("/partner/{token}/unsubscribe")
def partner_unsub_page(request: Request, token: str):
    p = db.find_partner(token=token)
    if not p:
        return _redirect("/")
    return HTMLResponse("<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>ไม่รับการติดต่อ</title>"
                        "<body style='font-family:system-ui,sans-serif;max-width:520px;margin:60px auto;padding:0 16px'>"
                        "<h2>ไม่รับการติดต่อเรื่องพันธมิตรจากเจอ.AI</h2>"
                        f"<form method='post'><button style='padding:10px 18px;font-size:15px'>ยืนยัน ไม่รับการติดต่ออีก</button></form></body>")


@app.post("/partner/{token}/unsubscribe")
def partner_unsub(request: Request, token: str):
    from . import partners
    p = db.find_partner(token=token)
    if p and p["status"] != "unsubscribed":
        partners.unsubscribe(p)
    return HTMLResponse("<!doctype html><meta charset='utf-8'><body style='font-family:system-ui,sans-serif;max-width:520px;margin:60px auto;padding:0 16px'>"
                        "<h2>เรียบร้อย — เราจะไม่ติดต่อเรื่องนี้อีก</h2></body>")


@app.post("/chat/ask")
async def chat_ask(request: Request):
    """กล่องแชตบนหน้าเว็บ → ตัวตอบแชตขาย · จำกัดต่อ IP ต่อวัน · token = ลีดที่กำลังดูรายงานของตัวเอง"""
    from . import salesbot
    if not salesbot.enabled("web"):
        return JSONResponse({"answer": "ตอนนี้ปิดแชตชั่วคราว ส่งคำถามมาที่อีเมลในหน้าเว็บได้เลยครับ", "escalate": None})
    try:
        data = await request.json()
    except Exception:
        data = {}
    q = str(data.get("q") or "").strip()[:500]
    if len(q) < 2:
        return JSONResponse({"answer": "พิมพ์คำถามมาได้เลยครับ", "escalate": None})
    tok = str(data.get("token") or "")[:64]
    lead = db.get_lead_by_token(tok) if tok else None
    ip = _client_ip(request)
    res = salesbot.handle("web", f"ip:{ip}", q, lead)
    return JSONResponse({"answer": res["answer"], "escalate": bool(res.get("escalate")), "id": res.get("id")})


@app.post("/chat/contact")
async def chat_contact(request: Request):
    from . import salesbot
    import re as _re
    try:
        data = await request.json()
    except Exception:
        data = {}
    c = str(data.get("contact") or "").strip()[:120]
    if not data.get("consent"):
        return JSONResponse({"ok": False, "message": "ติ๊กยินยอมก่อน เราถึงจะติดต่อกลับได้"})
    if not (_re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", c, _re.I) or _re.search(r"\d{9,}", _re.sub(r"\D", "", c)) or _re.fullmatch(r"@?[\w.\-]{3,40}", c)):
        return JSONResponse({"ok": False, "message": "ขออีเมล เบอร์โทร หรือ LINE ID ที่ติดต่อได้"})
    ok = salesbot.add_contact(int(data.get("id") or 0), c)
    return JSONResponse({"ok": ok, "message": "ได้รับแล้วครับ ผู้ดูแลจะติดต่อกลับภายในวันทำการนี้" if ok else "ส่งไม่สำเร็จ ลองใหม่อีกครั้ง"})


@app.get("/admin/salesbot")
def admin_salesbot(request: Request, notice: str = ""):
    from . import salesbot
    if not _is_admin(request):
        return _redirect("/app" if _tid(request) else "/login")
    return templates.TemplateResponse(request, "admin_salesbot.html", {"chats": salesbot.recent(150), "s7": salesbot.stats(7), "notice": notice,
                                      "on": {ch: salesbot.enabled(ch) for ch in ("line", "web", "email")}, "facts": salesbot.knowledge()})


@app.post("/admin/salesbot/settings")
def admin_salesbot_settings(request: Request, line: str = Form(""), web: str = Form(""), email: str = Form("")):
    from . import salesbot
    if not _is_admin(request):
        return _redirect("/login")
    for ch, v in (("line", line), ("web", web), ("email", email)):
        salesbot.set_enabled(ch, bool(v))
    return _redirect("/admin/salesbot?notice=" + quote("บันทึกแล้ว"))


@app.get("/admin/results")
def admin_results(request: Request, notice: str = ""):
    from . import results as RS
    if not _is_admin(request):
        return _redirect("/app" if _tid(request) else "/login")
    return templates.TemplateResponse(request, "admin_results.html", {
        "rows": RS.summary(), "on": RS.enabled(), "notice": notice, "every": RS.REVIEW_EVERY_DAYS, "nudge": RS.NUDGE_AFTER_DAYS,
        "state_th": RS.STATE_TH, "kind_th": RS.KIND_TH, "check_kinds": RS.CHECK_KINDS})


@app.post("/admin/results/run")
def admin_results_run(request: Request, bid: str = Form("")):
    from . import results as RS
    if not _is_admin(request):
        return _redirect("/login")
    if bid.isdigit() and db.get_brand(int(bid)):
        pipeline._spawn(lambda: RS.run_one(int(bid)))
    else:
        pipeline._spawn(lambda: RS.run(force=True))
    return _redirect("/admin/results?notice=" + quote("เริ่มรีวิวแล้ว ใช้เวลาราวครึ่งนาทีต่อแบรนด์ (ตรวจรูปและลิงก์ในบทความ) รีเฟรชหน้านี้สักครู่"))


@app.post("/admin/results/settings")
def admin_results_settings(request: Request, on: str = Form("")):
    if not _is_admin(request):
        return _redirect("/login")
    db.set_setting("results_on", "1" if on else "0")
    return _redirect("/admin/results?notice=" + quote("บันทึกแล้ว"))


@app.post("/admin/results/task")
def admin_results_task(request: Request, bid: str = Form(...), title: str = Form(...), detail: str = Form(""),
                       check_kind: str = Form("manual"), check_url: str = Form(""), check_text: str = Form("")):
    from . import results as RS
    if not _is_admin(request):
        return _redirect("/login")
    if not bid.isdigit() or not db.get_brand(int(bid)) or not title.strip():
        return _redirect("/admin/results?notice=" + quote("ข้อมูลไม่ครบ"))
    kind = check_kind if check_kind in RS.CHECK_KINDS else "manual"
    url = check_url.strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    if kind != "manual" and not url:
        kind = "manual"
    db.add_result_task(int(bid), title.strip()[:200], detail=detail.strip()[:2000] or None, check_kind=kind,
                       check_url=url[:500] or None, check_text=check_text.strip()[:200] or None)
    return _redirect("/admin/results?notice=" + quote("เพิ่มงานแล้ว — ระบบตรวจให้ทุกรอบรีวิว"))


@app.post("/admin/results/task/{tid}/{action}")
def admin_results_task_action(request: Request, tid: int, action: str):
    from . import results as RS
    if not _is_admin(request):
        return _redirect("/login")
    t = db.get_result_task(tid)
    if not t:
        return _redirect("/admin/results")
    note = ""
    if action == "done":
        db.update_result_task(tid, status="done", done_at=db.now(), last_check_note="เจ้าของกดเสร็จ")
        pipeline._spawn(lambda: RS._remeasure(t["brand_id"]))
        note = "บันทึกว่าเสร็จแล้ว และสั่งวัดผลซ้ำ"
    elif action == "reopen":
        db.update_result_task(tid, status="open", done_at=None)
        note = "เปิดงานใหม่แล้ว"
    elif action == "delete":
        db.delete_result_task(tid)
        note = "ลบงานแล้ว"
    elif action == "check":
        ok, n = RS.check_task(t)
        db.update_result_task(tid, last_check_at=db.now(), last_check_note=n)
        if ok:
            db.update_result_task(tid, status="done", done_at=db.now())
            pipeline._spawn(lambda: RS._remeasure(t["brand_id"]))
        note = ("ขึ้นแล้ว — ปิดงานและสั่งวัดผลซ้ำ" if ok else f"ยังไม่เสร็จ: {n}")
    return _redirect("/admin/results?notice=" + quote(note or "ไม่รู้จักคำสั่ง"))


@app.get("/admin/partners")
def admin_partners(request: Request, notice: str = ""):
    from . import partners
    if not _is_admin(request):
        return _redirect("/app" if _tid(request) else "/login")
    ps = [dict(p) for p in db.list_partners()]
    for p in ps:
        st = partners.stats(p)
        p.update(n_leads=st["leads"], n_won=st["won"], due=st["approved"], pending=st["pending"], paid=st["paid"])
    try:
        last = json.loads(db.get_setting("partner_last") or "{}")
    except Exception:
        last = {}
    return templates.TemplateResponse(request, "admin_partners.html", {"ps": ps, "s": partners.summary(), "sth": partners.STATUS_TH, "notice": notice,
                                      "pct": partners.pct(), "months": partners.months(), "on": partners.enabled(), "daily": partners.daily_limit(),
                                      "daily_max": partners.DAILY_MAX, "last": last, "base": _public_base(),
                                      "comms": [dict(c) for c in db.list_commissions(limit=100)], "cst": partners.CSTATUS_TH})


@app.post("/admin/partners/{action}")
def admin_partners_action(request: Request, action: str, pid: int = Form(0), name: str = Form(""), company: str = Form(""), email: str = Form(""),
                          phone: str = Form(""), website: str = Form(""), on: str = Form(""), daily: str = Form("")):
    from . import partners
    if not _is_admin(request):
        return _redirect("/login")
    who = request.session.get("email") or "admin"
    p = db.get_partner(pid) if pid else None
    if action == "run":
        r = partners.run(force=True)
        note = f"หาพันธมิตร: พบ {r['found']} เพิ่ม {r['added']} เสนอคำชวน {r['proposed']} ติดตาม {r['followups']}" + (f" · {', '.join(r['domains'])}" if r["domains"] else "")
    elif action == "add":
        err, did = partners.apply(name, company, email, phone, website, "", src="เพิ่มโดยผู้ดูแล")
        note = err or "เพิ่มแล้ว — อนุมัติได้ที่ตารางด้านล่างหรือหน้าการตัดสินใจ"
    elif action == "activate" and p:
        note = partners.activate(p, who)
        for d in db.list_decisions("proposed", 300):
            if d["kind"] == "partner_apply" and json.loads(d["payload_json"] or "{}").get("partner_id") == p["id"]:
                db.set_decision(d["id"], "executed", who, note)
    elif action == "pause" and p:
        db.update_partner(p["id"], status="paused" if p["status"] == "active" else "active")
        note = "พักพันธมิตรแล้ว (ลิงก์ไม่นับลีดใหม่)" if p["status"] == "active" else "เปิดใช้งานอีกครั้งแล้ว"
    elif action == "invite" and p:
        partners.propose_invite(p)
        note = "ร่างคำชวนแล้ว — อนุมัติที่หน้าการตัดสินใจ"
    elif action == "paid" and p:
        note = partners.mark_paid(p["id"], who)
    elif action == "settings":
        db.set_setting("partner_on", "1" if on else "0")
        if daily.strip():
            db.set_setting("partner_daily", str(max(1, min(partners.DAILY_MAX, int(float(daily))))))
        note = "บันทึกการตั้งค่าแล้ว"
    else:
        note = "ไม่รู้จักคำสั่ง"
    return _redirect(f"/admin/partners?notice={note}")


# ---------- ฝ่ายส่งอีเมล: โดเมนผู้ส่ง · ทดสอบกล่อง · กันสแปม · ทดลองข้อความ ----------
@app.post("/resend/webhook")
async def resend_webhook(request: Request):
    """Resend ยิง event (เด้ง · ร้องเรียนสแปม · ส่งถึง) — ตรวจลายเซ็น Svix ก่อนเสมอ"""
    from . import deliverability
    body = await request.body()
    if not deliverability.verify_svix(body, {k.lower(): v for k, v in request.headers.items()}):
        return Response("bad signature", status_code=400)
    try:
        res = deliverability.handle_resend(json.loads(body or b"{}"))
    except Exception as e:
        res = f"error:{type(e).__name__}"
    return JSONResponse({"ok": True, "result": res})


def _deliv_ctx(notice: str = "", error: str = "") -> dict:
    from . import deliverability as D, prospecting
    e = D.active_experiment()
    try:
        last = json.loads(db.get_setting("deliv_last") or "{}")
    except Exception:
        last = {}
    m = D.metrics()
    ok, why = D.healthy(m)
    return {"notice": notice, "error": error, "dns": D._json("deliv_dns", {}), "m": m, "healthy": ok, "healthy_why": why,
            "hold": D.hold_reason(), "seeds": D.public_seeds(), "tests": list(reversed(D._json("deliv_tests", [])))[:6], "place_th": D.PLACE_TH,
            "exp_on": D.exp_on(), "exp": e, "exp_stats": D.exp_stats(e["key"]) if e else None, "experiments": D.EXPERIMENTS,
            "adopted": [D.EXP[k] for k in D.adopted() if k in D.EXP], "results": list(reversed(D._json("deliv_exp_results", []))),
            "min_arm": D.MIN_ARM, "max_arm": D.MAX_ARM, "limits": D.LIMITS, "ramp_on": (db.get_setting("deliv_ramp") or "1") == "1",
            "daily": prospecting.daily_limit(), "daily_max": prospecting.DAILY_MAX, "webhook_set": bool(D.webhook_secret()),
            "webhook_url": f"{_public_base()}/resend/webhook", "last": last, "domain": D.sender_domain(),
            "sender": D.outreach_sender(), "signer": D.signer(), "llm_write": D.llm_writes_outreach(), "system_from": (mailer.config() or {}).get("smtp_from", "")}


@app.get("/admin/deliverability")
def admin_deliverability(request: Request, notice: str = ""):
    if not _is_admin(request):
        return _redirect("/app" if _tid(request) else "/login")
    return templates.TemplateResponse(request, "admin_deliverability.html", _deliv_ctx(notice))


@app.post("/admin/deliverability/{action}")
def admin_deliverability_action(request: Request, action: str, label: str = Form(""), host: str = Form(""), user: str = Form(""),
                                password: str = Form(""), sid: str = Form(""), secret: str = Form(""), on: str = Form(""), key: str = Form(""),
                                addr: str = Form(""), name: str = Form(""), signer: str = Form(None), llm_write: str = Form(None)):
    from . import deliverability as D
    if not _is_admin(request):
        return _redirect("/login")
    note, err = "", ""
    if action == "run":
        r = D.run()
        note = "ตรวจแล้ว" + (f" · หยุดส่ง: {r['hold']}" if r.get("hold") else "") + (f" · เพิ่มหาลูกค้า {r['ramp']}" if r.get("ramp") else "")
    elif action == "test":
        t = D.send_placement_test()
        note = t.get("error") or f"ส่งอีเมลทดสอบ {len(t['results'])} กล่องแล้ว — ระบบตรวจผลเองภายใน 10 นาที หรือกด \"ตรวจผลทดสอบ\""
    elif action == "check":
        note = f"ตรวจผลทดสอบแล้ว ({D.check_placement()} รายการเปลี่ยน)"
    elif action == "seed":
        err = D.save_seed(label, host, user, password)
        note = "" if err else f"บันทึกกล่องทดสอบ {user} แล้ว (รหัสผ่านเข้ารหัสเก็บ ไม่แสดงอีก)"
    elif action == "seed_remove":
        D.remove_seed(sid)
        note = "ลบกล่องทดสอบแล้ว"
    elif action == "webhook":
        err = D.save_webhook_secret(secret)
        note = "" if err else "บันทึกรหัสลับ webhook ของ Resend แล้ว"
    elif action == "sender":
        err = D.save_outreach_sender(addr, name, signer, (llm_write == "1") if signer is not None else None)
        if not err:
            s = D.outreach_sender()
            note = ("เลิกใช้ที่อยู่แยก — อีเมลทักกลับไปส่งจากที่อยู่ระบบ" if not s["addr"] else
                    f"บันทึก {s['addr']} แล้ว" + ("" if s["ok"] else " — ตั้ง Cloudflare ตามขั้นตอนแล้วกด \"ตรวจเส้นทางตอบกลับ\" ระบบจะเริ่มใช้เมื่อตรวจผ่าน"))
    elif action == "route":
        note = D.send_route_check()
    elif action == "release":
        D.release_hold()
        note = "ปลดการหยุดส่งแล้ว — ข้อความทัก/ติดตามผลกลับไปทำตามโหมดเดิม"
    elif action == "exp":
        db.set_setting("deliv_exp_on", "1" if on else "0")
        note = "เปิดการทดลองข้อความแล้ว" if on else "ปิดการทดลองข้อความแล้ว — ทุกอีเมลใช้แนวที่ชนะแล้ว"
    elif action == "ramp":
        db.set_setting("deliv_ramp", "1" if on else "0")
        note = "เปิดการเพิ่มจำนวนทักอัตโนมัติแล้ว" if on else "ปิดการเพิ่มจำนวนทักอัตโนมัติแล้ว"
    elif action == "unadopt":
        D.reset_adopted(key)
        note = "เลิกใช้แนวนี้แล้ว"
    else:
        err = "ไม่รู้จักคำสั่ง"
    if err:
        return templates.TemplateResponse(request, "admin_deliverability.html", _deliv_ctx(error=err))
    return _redirect(f"/admin/deliverability?notice={note}")


# ---------- ฝ่ายบัญชี: ใบเสร็จ/ใบกำกับภาษี · ใบแจ้งหนี้รอบถัดไป · ทวงชำระ ----------
def _acct_page(request: Request, doc, error: str = ""):
    from . import accounting
    extra = {"error": error, "qr": None, "ppname": ""}
    if doc["kind"] == "invoice" and doc["status"] == "open":
        ppid, ppname = _promptpay_config()
        extra.update(qr=promptpay.qr_data_uri(ppid, doc["total"]) if ppid else None, ppname=ppname if ppid else "")
    return HTMLResponse(accounting.render(doc, extra=extra))


def _acct_pdf(doc):
    from . import accounting
    pdf = accounting.pdf_bytes(doc)
    if not pdf:
        return Response("สร้าง PDF ไม่ได้ตอนนี้ — เปิดหน้าเว็บแล้วสั่งพิมพ์แทนได้", status_code=503, media_type="text/plain; charset=utf-8")
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{doc["number"]}.pdf"'})


@app.get("/doc/{token}.pdf")
def acct_doc_pdf(token: str):
    doc = db.get_acct_doc_by_token(token)
    return _acct_pdf(doc) if doc and doc["kind"] == "receipt" else _redirect("/")


@app.get("/doc/{token}")
def acct_doc_page(request: Request, token: str):
    doc = db.get_acct_doc_by_token(token)
    return _acct_page(request, doc) if doc and doc["kind"] == "receipt" else _redirect("/")


@app.get("/bill/{token}.pdf")
def acct_bill_pdf(token: str):
    doc = db.get_acct_doc_by_token(token)
    return _acct_pdf(doc) if doc and doc["kind"] == "invoice" else _redirect("/")


@app.get("/bill/{token}")
def acct_bill_page(request: Request, token: str):
    doc = db.get_acct_doc_by_token(token)
    return _acct_page(request, doc) if doc and doc["kind"] == "invoice" else _redirect("/")


@app.post("/bill/{token}/pay")
async def acct_bill_pay(request: Request, token: str, slip: UploadFile = File(None), website: str = Form("")):
    from . import accounting
    doc = db.get_acct_doc_by_token(token)
    if not doc or doc["kind"] != "invoice" or website.strip():
        return _redirect("/")
    if doc["status"] not in ("open", "claimed"):
        return _redirect(f"/bill/{token}")
    slip_path = None
    if slip is not None and slip.filename:
        data = await slip.read()
        if data:
            ext = os.path.splitext(slip.filename)[1].lower()
            ext = ext if ext in (".jpg", ".jpeg", ".png", ".webp", ".pdf") else ".jpg"
            up = BASE.parent / "uploads" / "slips"
            up.mkdir(parents=True, exist_ok=True)
            fname = f"inv{doc['id']}-{secrets.token_hex(6)}{ext}"
            (up / fname).write_bytes(data[:8_000_000])
            slip_path = f"slips/{fname}"
    if doc["status"] == "claimed":
        db.update_acct_doc(doc["id"], slip_path=slip_path or doc["slip_path"])
    else:
        accounting.claim(doc, slip_path)
    return _redirect(f"/bill/{token}")


def _accounting_ctx(month: str = "", notice: str = "") -> dict:
    import datetime as _dt
    from . import accounting
    month = month if (len(month) == 7 and month[4] == "-") else _dt.date.today().strftime("%Y-%m")
    docs = [dict(d) for d in db.list_acct_docs(limit=300)]
    doms = {}
    for d in docs:
        if d["lead_id"] not in doms:
            l = db.get_lead(d["lead_id"]) if d["lead_id"] else None
            doms[d["lead_id"]] = l["domain"] if l else "-"
    today = _dt.date.today().isoformat()
    try:
        last = json.loads(db.get_setting("acct_last") or "{}")
    except Exception:
        last = {}
    return {"s": accounting.summary(month), "month": month, "docs": docs, "doms": doms, "today": today, "notice": notice,
            "title": accounting.title, "status_th": accounting.STATUS_TH, "source_th": accounting.SOURCE_TH, "seller": accounting.seller(),
            "on": accounting.enabled(), "last": last, "customers": len(accounting.customers()), "wht_rate": accounting.WHT_RATE,
            "lead_days": accounting.INVOICE_LEAD}


@app.get("/admin/accounting")
def admin_accounting(request: Request, month: str = "", notice: str = ""):
    if not _is_admin(request):
        return _redirect("/app" if _tid(request) else "/login")
    return templates.TemplateResponse(request, "admin_accounting.html", _accounting_ctx(month, notice))


@app.get("/admin/accounting.csv")
def admin_accounting_csv(request: Request, month: str = ""):
    import datetime as _dt
    from . import accounting
    if not _is_admin(request):
        return _redirect("/login")
    month = month if (len(month) == 7 and month[4] == "-") else _dt.date.today().strftime("%Y-%m")
    return Response(accounting.csv_month(month), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="jerai-accounting-{month}.csv"'})


@app.post("/admin/accounting/run")
def admin_accounting_run(request: Request):
    from . import accounting
    if not _is_admin(request):
        return _redirect("/login")
    res = accounting.run()
    note = res.get("skipped") or f"ออกใบแจ้งหนี้ {len(res['invoiced'])} ใบ · เตือน/แจ้งค้าง {len(res['actions'])} รายการ"
    return _redirect(f"/admin/accounting?notice={note}")


@app.post("/admin/accounting/toggle")
def admin_accounting_toggle(request: Request, on: str = Form("")):
    from . import accounting
    if not _is_admin(request):
        return _redirect("/login")
    accounting.set_enabled(bool(on))
    return _redirect("/admin/accounting?notice=" + ("เปิดฝ่ายบัญชีแล้ว" if on else "ปิดฝ่ายบัญชีแล้ว — ไม่ออกเอกสาร ไม่ทวง"))


@app.post("/admin/acct/{did}/{action}")
def admin_acct_action(request: Request, did: int, action: str, wht: str = Form(""), reason: str = Form("")):
    from . import accounting
    if not _is_admin(request):
        return _redirect("/login")
    doc = db.get_acct_doc(did)
    if not doc:
        return _redirect("/admin/accounting?notice=ไม่พบเอกสาร")
    who = request.session.get("email") or "admin"
    if action == "paid":
        note = accounting.mark_paid(doc, who, wht=bool(wht))
    elif action == "send":
        note = accounting.send(doc)
    elif action == "void":
        note = accounting.void(doc, reason, who)
    elif action == "reissue":
        note = accounting.reissue(doc, who)
    elif action == "suspend":
        note = accounting.suspend(db.get_quote(doc["quote_id"]), who)
    else:
        note = "ไม่รู้จักคำสั่ง"
    return _redirect(f"/admin/accounting?notice={note}")


@app.post("/admin/leads/{lead_id}/buyer")
def admin_lead_buyer(request: Request, lead_id: int, quote_id: int = Form(0), name: str = Form(""), tax_id: str = Form(""),
                     branch: str = Form(""), address: str = Form("")):
    from . import accounting
    if not _is_admin(request):
        return _redirect("/login")
    q = db.get_quote(quote_id) if quote_id else None
    if not q or q["lead_id"] != lead_id:
        return _redirect(f"/admin/leads/{lead_id}?notice=ไม่พบใบเสนอราคา")
    err = accounting.save_buyer(q["id"], name, tax_id, branch, address)
    return _redirect(f"/admin/leads/{lead_id}?notice=" + (err or "บันทึกข้อมูลออกเอกสารแล้ว — ใบเสร็จที่ออกแล้วกด \"ออกใหม่\" ที่หน้าบัญชีถ้าต้องการแก้"))


# ---------- บทโทรปิดดีล: เอเจนต์คัดลีดที่ควรโทรและเตรียมบท คุณโทรเองแล้วจดผล ----------
@app.get("/admin/calls")
def admin_calls(request: Request, notice: str = ""):
    if not _is_admin(request):
        return _redirect("/app" if _tid(request) else "/login")
    from . import closer
    return templates.TemplateResponse(request, "admin_calls.html", {"items": closer.call_list(limit=30), "notice": notice, "fmt_phone": closer.fmt_phone})


@app.get("/admin/calls/{lead_id}")
def admin_call(request: Request, lead_id: int, notice: str = ""):
    if not _is_admin(request):
        return _redirect("/app" if _tid(request) else "/login")
    from . import closer
    lead = db.get_lead(lead_id)
    if not lead:
        return _redirect("/admin/calls")
    d = closer.prepare(lead)
    return templates.TemplateResponse(request, "admin_call.html", {"lead": lead, "s": closer.script(db.get_lead(lead_id)), "phone": d.get("phone") or "",
                                                                   "calls": d.get("calls") or [], "outcomes": closer.OUTCOMES, "notice": notice,
                                                                   "fmt_phone": closer.fmt_phone})


@app.post("/admin/calls/{lead_id}/log")
def admin_call_log(request: Request, lead_id: int, outcome: str = Form(""), note: str = Form(""), phone: str = Form("")):
    if not _is_admin(request):
        return _redirect("/login")
    from . import closer
    lead = db.get_lead(lead_id)
    if not lead:
        return _redirect("/admin/calls")
    if phone.strip():
        d = closer._data(lead); d["phone"] = closer.norm_phone(phone) or phone.strip()[:20]; closer._save(lead_id, d); lead = db.get_lead(lead_id)
    if not outcome:
        return _redirect(f"/admin/calls/{lead_id}?notice=บันทึกเบอร์แล้ว")
    try:
        note_ = closer.log_call(lead, outcome, note, request.session.get("email") or "owner")
    except ValueError as e:
        note_ = str(e)
    return _redirect(f"/admin/calls?notice={lead['domain']}: {note_}")


# ---------- SMS (ThaiBulkSMS): ช่องทางสุดท้ายถึงลีดที่มีแต่เบอร์มือถือ ----------
@app.post("/admin/settings/sms")
def admin_settings_sms(request: Request, api_key: str = Form(""), api_secret: str = Form(""), sender: str = Form(""), daily: str = Form("30"), on: str = Form(""),
                       prospects: str = Form("")):
    if not _is_admin(request):
        return _redirect("/login")
    sms.save_config(api_key, api_secret, sender, daily, bool(on), bool(prospects))
    return templates.TemplateResponse(request, "admin_settings.html", _settings_ctx(request, saved=True))


@app.post("/admin/settings/sms/test")
def admin_settings_sms_test(request: Request, phone: str = Form("")):
    if not _is_admin(request):
        return _redirect("/login")
    try:
        msg = sms.send(phone, "เจอ.AI: ทดสอบส่ง SMS จากระบบ — ถ้าได้รับข้อความนี้ แปลว่าตั้งค่าถูกต้องแล้ว")
        return templates.TemplateResponse(request, "admin_settings.html", _settings_ctx(request, saved=True) | {"sms_test": "✓ " + msg})
    except Exception as e:
        return templates.TemplateResponse(request, "admin_settings.html", _settings_ctx(request) | {"sms_test": f"✗ {e}"})


# ---------- สั่งงาน CEO (หน้าแชต · ทาง LINE ของเจ้าของใช้ ceo_chat เดียวกัน) ----------
@app.get("/admin/ceo")
def admin_ceo(request: Request):
    if not _is_admin(request):
        return _redirect("/app" if _tid(request) else "/login")
    from . import ceo_chat, line_oa
    return templates.TemplateResponse(request, "admin_ceo.html", {"log": ceo_chat.log()[-40:], "pending": ceo_chat._pending(),
                                                                  "line_owner": bool(line_oa.owner_id()), "actions": ceo_chat.ACTIONS})


@app.post("/admin/ceo")
def admin_ceo_send(request: Request, text: str = Form("")):
    if not _is_admin(request):
        return _redirect("/login")
    from . import ceo_chat
    ceo_chat.handle(text[:500], request.session.get("email") or "owner")
    return _redirect("/admin/ceo#end")


@app.post("/admin/ceo/send.json")
async def admin_ceo_send_json(request: Request):
    """ช่องแชตในออฟฟิศ 3D — ส่งคำสั่งแล้วได้คำตอบกลับทันที (ไม่ reload หน้า)"""
    if not _is_admin(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from . import ceo_chat
    import asyncio
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = str(body.get("text") or "")[:500]
    reply = await asyncio.to_thread(ceo_chat.handle, text, request.session.get("email") or "owner") if text.strip() else ""
    return JSONResponse({"reply": reply, "pending": bool(ceo_chat._pending()), "log": ceo_chat.log()[-20:]}, headers={"Cache-Control": "no-store"})


@app.get("/admin/ceo/log.json")
def admin_ceo_log_json(request: Request):
    if not _is_admin(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    from . import ceo_chat
    return JSONResponse({"log": ceo_chat.log()[-20:], "pending": bool(ceo_chat._pending())}, headers={"Cache-Control": "no-store"})


# ---------- งานที่ CEO แจกให้เอเจนต์ ----------
@app.get("/admin/tasks")
def admin_tasks(request: Request, notice: str = ""):
    if not _is_admin(request):
        return _redirect("/app" if _tid(request) else "/login")
    from . import orchestrator
    return templates.TemplateResponse(request, "admin_tasks.html", {"tasks": db.list_tasks(None, None, 100), "names": orchestrator.NAME, "notice": notice,
                                                                    "paused": execs.paused()})


@app.post("/admin/tasks/plan")
def admin_tasks_plan(request: Request):
    if not _is_admin(request):
        return _redirect("/login")
    from . import orchestrator
    res = orchestrator.plan_and_run()
    note = f"CEO แจก {len(res['tasks'])} งาน" + (" (พักไว้ — หยุดฉุกเฉินอยู่)" if res["held"] else " กำลังทำ") if res["tasks"] else "วันนี้ไม่มีงานใหม่ที่ต้องแจก"
    return _redirect(f"/admin/tasks?notice={note}")


# ---------- Inbox: อีเมลที่ลีดตอบกลับ ----------
def _inbox_ctx(notice: str = "") -> dict:
    from . import inbox
    with db.get_conn() as c:
        rows = c.execute("SELECT * FROM inbox_messages ORDER BY id DESC LIMIT 100").fetchall()
    cfg = inbox.config()
    try:
        last = json.loads(db.get_setting("inbox_last") or "null")
    except Exception:
        last = None
    return {"rows": rows, "cats": inbox.CATS, "notice": notice, "imap": {"host": db.get_setting("imap_host") or "", "user": db.get_setting("imap_user") or "",
            "folder": db.get_setting("imap_folder") or "INBOX", "ready": bool(cfg)}, "addr": inbox.our_address(), "last": last,
            "hook_url": f"{_public_base()}/inbox/hook?secret={inbox.hook_secret()}",
            "pending": [d for d in db.list_decisions("proposed", 200) if d["kind"] == "reply"]}


@app.get("/admin/inbox")
def admin_inbox(request: Request, notice: str = ""):
    if not _is_admin(request):
        return _redirect("/app" if _tid(request) else "/login")
    return templates.TemplateResponse(request, "admin_inbox.html", _inbox_ctx(notice))


@app.post("/admin/inbox/settings")
def admin_inbox_settings(request: Request, host: str = Form(""), user: str = Form(""), password: str = Form(""), folder: str = Form("INBOX")):
    if not _is_admin(request):
        return _redirect("/login")
    from . import inbox
    inbox.save_config(host, user, password, folder)
    return _redirect("/admin/inbox?notice=บันทึกแล้ว — กด \"อ่านตอนนี้\" เพื่อทดสอบ")


@app.post("/admin/inbox/poll")
def admin_inbox_poll(request: Request):
    if not _is_admin(request):
        return _redirect("/login")
    from . import inbox
    try:
        r = inbox.poll()
        note = r.get("skipped") or f"ตรวจ {r['checked']} ฉบับ · ใหม่ {r['processed']}"
    except Exception as e:
        note = f"อ่านกล่องอีเมลไม่สำเร็จ: {type(e).__name__}: {str(e)[:120]}"
    return _redirect(f"/admin/inbox?notice={note}")


@app.post("/inbox/hook")
async def inbox_hook(request: Request, secret: str = ""):
    """Cloudflare Email Worker ส่งอีเมลดิบมา (message.raw) · ต้องมีรหัสลับ"""
    from . import inbox
    import hmac as _hm
    if not secret or not _hm.compare_digest(secret, inbox.hook_secret()):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    raw = await request.body()
    if not raw or len(raw) > 5_000_000:
        return JSONResponse({"error": "empty or too large"}, status_code=400)
    m = inbox.parse_raw(raw)
    return JSONResponse(inbox.process(m))
