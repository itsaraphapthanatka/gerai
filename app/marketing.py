"""ฝ่ายการตลาด (CMO เอเจนต์) — ทำให้ปากกรวยเต็ม โดยเจ้าของไม่ต้องโพสต์เอง
1) เผยแพร่คอนเทนต์ของเจอ.AI เอง (ร่างที่ผ่าน QA ของ AEO) ให้เจอ.AI โผล่ใน Google/AI ด้วยตัวเอง
2) โพสต์ LINE OA (broadcast) และ Facebook Page: บทความใหม่ · ชวนตรวจเว็บฟรี · เคสศึกษาจากผลจริงของลูกค้า
3) ทุกลิงก์ติดรหัสช่องทาง (?src=line|fb|article) → รู้ว่าลีดมาจากไหน
โหมดเสนอก่อนทำเหมือน CEO (decision kind "post") · สลับเป็นทำเองได้เมื่อผ่านด่านหรือเจ้าของปลดล็อก · คำต้องห้าม (การันตี ฯลฯ) ไม่ผ่าน"""
from __future__ import annotations
import datetime
import json
import os
import re

import httpx

from . import db, execs, geo_content, line_oa, wp_client
from . import office

CHANNELS = {"line": "LINE OA", "facebook": "Facebook Page"}
POST_KINDS = {"article": "บทความใหม่", "audit_cta": "ชวนตรวจเว็บฟรี", "case_study": "เคสศึกษา",
              "insight": "สถิติจากการตรวจจริง", "ai_test": "ลองถาม AI จริง", "tip": "เคล็ดลับเช็กเอง"}
WEEKLY_DEFAULT = 3
GRAPH = "https://graph.facebook.com/v21.0"
SRC = {"line": "line", "facebook": "fb"}


def _base() -> str:
    return os.getenv("PUBLIC_BASE_URL", "https://geo.appreview.cloud").rstrip("/")


# ---------- ตั้งค่า ----------
def enabled() -> bool:
    return (db.get_setting("cmo_enabled") or "1") == "1"


def weekly() -> int:
    try:
        return max(1, min(14, int(db.get_setting("cmo_weekly") or WEEKLY_DEFAULT)))
    except ValueError:
        return WEEKLY_DEFAULT


def line_on() -> bool:
    return (db.get_setting("cmo_line") or "1") == "1" and line_oa.available()


def fb_config() -> dict | None:
    pid = (db.get_setting("fb_page_id") or "").strip()
    tok = (db.get_setting("fb_page_token") or "").strip()
    if not pid or not tok:
        return None
    try:
        return {"page_id": pid, "token": wp_client.decrypt(tok)}
    except Exception:
        return None


def fb_identity(token: str) -> dict | None:
    """ถาม Graph ว่า token นี้เป็นของเพจไหน (GET /me) — ใช้ตรวจและแก้ Page ID ที่กรอกผิดให้เอง"""
    try:
        r = httpx.get(f"{GRAPH}/me", params={"fields": "id,name,link", "access_token": token}, timeout=20)
        b = r.json() if r.content else {}
        if r.status_code < 400 and b.get("id"):
            return {"id": b["id"], "name": b.get("name") or "", "link": b.get("link") or ""}
    except Exception:
        pass
    return None


NEEDED_SCOPES = ("pages_manage_posts", "pages_read_engagement")


def fb_app() -> dict | None:
    aid = (db.get_setting("fb_app_id") or "").strip()
    sec = (db.get_setting("fb_app_secret") or "").strip()
    if not aid or not sec:
        return None
    try:
        return {"id": aid, "secret": wp_client.decrypt(sec)}
    except Exception:
        return None


def fb_debug(token: str) -> dict | None:
    """ข้อมูล token จาก /debug_token (ต้องมี App ID + secret): ประเภท หมดอายุเมื่อไร สิทธิ์อะไร"""
    app = fb_app()
    if not app:
        return None
    try:
        r = httpx.get(f"{GRAPH}/debug_token", params={"input_token": token, "access_token": f"{app['id']}|{app['secret']}"}, timeout=20)
        d = (r.json() or {}).get("data") or {}
        if r.status_code >= 400 or not d:
            return None
        scopes = list(d.get("scopes") or [])
        for g in d.get("granular_scopes") or []:                 # token ของเพจมักระบุสิทธิ์ไว้ที่ granular_scopes แทน
            if g.get("scope") and g["scope"] not in scopes:
                scopes.append(g["scope"])
        return {"type": d.get("type"), "expires_at": int(d.get("expires_at") or 0), "scopes": scopes, "valid": bool(d.get("is_valid"))}
    except Exception:
        return None


def fb_longlive(token: str, page_id: str = "") -> tuple[str, str]:
    """แปลง token อายุสั้นจาก Graph API Explorer เป็น token ถาวรของเพจ (ต้องมี App ID + secret)
    user token → แลกเป็นอายุยาว → /me/accounts → token ของเพจ (ไม่หมดอายุ) · page token → ขอแลกเป็นอายุยาวตรง ๆ · คืน (token, หมายเหตุ)"""
    app = fb_app()
    if not app:
        return token, "ไม่มี App ID/secret จึงใช้ token ตามที่วาง (ถ้ามาจาก Graph API Explorer จะหมดอายุใน ~1 ชม.)"
    try:
        r = httpx.get(f"{GRAPH}/oauth/access_token", params={"grant_type": "fb_exchange_token", "client_id": app["id"],
                                                             "client_secret": app["secret"], "fb_exchange_token": token}, timeout=20)
        long_tok = (r.json() or {}).get("access_token") if r.status_code < 400 else None
    except Exception:
        long_tok = None
    if not long_tok:
        return token, "แลก token อายุยาวไม่สำเร็จ — ใช้ตามที่วาง"
    try:
        r = httpx.get(f"{GRAPH}/me/accounts", params={"fields": "id,name,access_token", "access_token": long_tok}, timeout=20)
        pages = ((r.json() or {}).get("data") or []) if r.status_code < 400 else []
    except Exception:
        pages = []
    if pages:                                           # เป็น user token → เลือกเพจ
        pick = next((p for p in pages if p.get("id") == page_id), None) or (pages[0] if len(pages) == 1 else None)
        if pick and pick.get("access_token"):
            return pick["access_token"], f"แปลงเป็น token ถาวรของเพจ {pick.get('name')} แล้ว"
        return long_tok, "token นี้ดูแลหลายเพจ — กรอก Page ID ของเพจที่ต้องการแล้วบันทึกอีกครั้ง"
    return long_tok, "แลกเป็น token อายุยาวแล้ว"


def fb_fix_app_id(token: str) -> str:
    """โทเค็นมาจากแอปอื่นที่ไม่ใช่ App ID ที่ตั้งไว้ (เคยเกิด: แลก token ถาวรไม่ได้ทั้งที่ secret ถูก) → ถ้า secret ใช้ได้กับแอปของโทเค็น แก้ App ID ให้"""
    app = fb_app()
    if not app:
        return ""
    try:
        tok_app = (httpx.get(f"{GRAPH}/app", params={"access_token": token}, timeout=20).json() or {}).get("id")
        if not tok_app or tok_app == app["id"]:
            return ""
        r = httpx.get(f"{GRAPH}/oauth/access_token", params={"client_id": tok_app, "client_secret": app["secret"], "grant_type": "client_credentials"}, timeout=20)
        if r.status_code < 400:
            db.set_setting("fb_app_id", tok_app)
            return f"แก้ App ID เป็น {tok_app} ให้ตรงกับแอปที่ออกโทเค็น"
        return f"โทเค็นมาจากแอป {tok_app} แต่ App ID ที่ตั้งไว้คือ {app['id']} — ใช้ App ID/secret ของแอปเดียวกับที่ออกโทเค็น"
    except Exception:
        return ""


def fb_accounts(token: str) -> list[dict]:
    """เพจที่ user token นี้ดูแล (พร้อม page token ของแต่ละเพจ) · token ของเพจเองจะได้ [] """
    try:
        r = httpx.get(f"{GRAPH}/me/accounts", params={"fields": "id,name,access_token,tasks", "access_token": token}, timeout=20)
        return ((r.json() or {}).get("data") or []) if r.status_code < 400 else []
    except Exception:
        return []


def fb_pick_page(token: str, want_id: str = "") -> tuple[dict | None, list[dict]]:
    """ได้ user token มา → เลือกเพจ: ตรง Page ID ที่ต้องการ > มีเพจเดียว > ชื่อเพจมี "เจอ" · คืน (เพจที่เลือก, เพจทั้งหมด)"""
    pages = fb_accounts(token)
    if not pages:
        return None, []
    pick = (next((p for p in pages if p.get("id") == want_id), None) or (pages[0] if len(pages) == 1 else None)
            or next((p for p in pages if "เจอ" in (p.get("name") or "")), None))
    return pick, pages


def save_config(on: bool, weekly_n: str, line: bool, fb_page_id: str, fb_token: str, fb_app_id: str = "", fb_app_secret: str = "") -> str:
    """บันทึก · token ใหม่ → แปลงเป็นถาวร (ถ้ามี App ID/secret) → ถาม Graph ว่าเป็นเพจไหนแล้วใช้ Page ID จริง → เช็กสิทธิ์/วันหมดอายุ"""
    db.set_setting("cmo_enabled", "1" if on else "0")
    try:
        db.set_setting("cmo_weekly", str(max(1, min(14, int(weekly_n or WEEKLY_DEFAULT)))))
    except ValueError:
        pass
    db.set_setting("cmo_line", "1" if line else "0")
    if fb_app_id.strip():
        db.set_setting("fb_app_id", fb_app_id.strip()[:40])
    if fb_app_secret.strip():
        db.set_setting("fb_app_secret", wp_client.encrypt(fb_app_secret.strip()))
    if fb_page_id.strip():
        db.set_setting("fb_page_id", fb_page_id.strip()[:40])
    notes = []
    if fb_token.strip():
        cand = fb_token.strip()
        if not fb_identity(cand):                                   # ตรวจก่อนเก็บ — โทเค็นที่คัดลอกมาไม่ครบจะได้ไม่ทับของเดิม
            notes.append("โทเค็นที่วางใช้ไม่ได้ (Graph ตอบว่าไม่รู้จัก/Bad signature) — คงโทเค็นเดิมไว้ ลองคัดลอกใหม่ด้วยปุ่มคัดลอกใน Graph API Explorer")
        else:
            fix = fb_fix_app_id(cand)
            if fix:
                notes.append(fix)
            tok, note = fb_longlive(cand, (db.get_setting("fb_page_id") or "").strip())
            if not fb_identity(tok):
                tok, note = cand, "แลกโทเค็นถาวรไม่สำเร็จ — ใช้โทเค็นตามที่วาง"
            db.set_setting("fb_page_token", wp_client.encrypt(tok))
            notes.append(note)
    raw = (db.get_setting("fb_page_token") or "").strip()
    if not raw:
        return "บันทึกแล้ว"
    try:
        token = wp_client.decrypt(raw)
    except Exception:
        return "บันทึกแล้ว แต่ token อ่านไม่ได้ — วางใหม่"
    ident = fb_identity(token)
    if not ident:
        db.set_setting("fb_token_valid", "0")
        return "บันทึกแล้ว แต่โทเค็นในระบบใช้ไม่ได้ (Graph ไม่รู้จัก) — วางโทเค็นใหม่" + (" · " + " · ".join(notes) if notes else "")
    pick, pages = fb_pick_page(token, (fb_page_id.strip() or db.get_setting("fb_page_want") or "").strip())
    if pages:                                    # วาง user token (ของบัญชีส่วนตัว) มา — โพสต์ด้วยตัวนี้ไม่ได้ ต้องใช้ token ของเพจ
        if not pick or not pick.get("access_token"):
            db.set_setting("fb_token_valid", "0")
            return ("บันทึกแล้ว แต่โทเค็นนี้เป็นของบัญชีส่วนตัว ดูแล " + str(len(pages)) + " เพจ: "
                    + ", ".join(f"{p.get('name')} (ID {p.get('id')})" for p in pages) + " — กรอก Page ID ของเพจที่จะโพสต์แล้วบันทึกอีกครั้ง")
        token = pick["access_token"]
        db.set_setting("fb_page_token", wp_client.encrypt(token))
        ident = {"id": pick["id"], "name": pick.get("name") or ""}
        notes.append(f"โทเค็นที่วางเป็นของบัญชีส่วนตัว — เลือกเพจ {ident['name']} และใช้ token ของเพจแทนให้แล้ว")
    db.set_setting("fb_page_want", ident["id"])
    db.set_setting("fb_token_valid", "1")
    db.set_setting("fb_page_id", ident["id"])
    db.set_setting("fb_page_name", ident["name"])
    dbg = fb_debug(token)
    if dbg:
        db.set_setting("fb_token_expires", str(dbg["expires_at"]))
        db.set_setting("fb_token_scopes", ",".join(dbg["scopes"]))
        missing = [sc for sc in NEEDED_SCOPES if sc not in dbg["scopes"]]
        if missing:
            notes.append("ขาดสิทธิ์ " + ", ".join(missing) + " — ออก token ใหม่โดยติ๊กสิทธิ์เหล่านี้")
        notes.append("ไม่หมดอายุ" if dbg["expires_at"] == 0 else "หมดอายุ " + datetime.datetime.fromtimestamp(dbg["expires_at"]).strftime("%Y-%m-%d %H:%M"))
    return f"บันทึกแล้ว · Facebook Page: {ident['name']} (ID {ident['id']})" + (" · " + " · ".join(notes) if notes else "")


def channels() -> list[str]:
    out = []
    if line_on():
        out.append("line")
    if fb_config():
        out.append("facebook")
    return out


def last_run() -> dict | None:
    raw = db.get_setting("cmo_last")
    try:
        return json.loads(raw) if raw else None
    except Exception:
        return None


def missing_scopes() -> list[str]:
    sc = [x for x in (db.get_setting("fb_token_scopes") or "").split(",") if x]
    return [n for n in NEEDED_SCOPES if n not in sc] if sc else []


def public_config() -> dict:
    tok = db.get_setting("fb_page_token") or ""
    if tok and fb_app():                                              # เช็กสถานะโทเค็นสด ๆ ทุกครั้งที่เปิดหน้า (ใช้ได้ไหม สิทธิ์อะไร หมดอายุเมื่อไร)
        try:
            d = fb_debug(wp_client.decrypt(tok))
            if d:
                db.set_setting("fb_token_valid", "1" if d["valid"] else "0")
                db.set_setting("fb_token_scopes", ",".join(d["scopes"]))
                db.set_setting("fb_token_expires", str(d["expires_at"]))
        except Exception:
            pass
    return {"enabled": enabled(), "weekly": weekly(), "line": (db.get_setting("cmo_line") or "1") == "1", "line_ready": line_oa.available(),
            "fb_page_id": db.get_setting("fb_page_id") or "", "fb_page_name": db.get_setting("fb_page_name") or "",
            "fb_token_set": bool(tok), "fb_ready": fb_config() is not None, "fb_app_set": fb_app() is not None,
            "fb_scopes": db.get_setting("fb_token_scopes") or "", "fb_expires": db.get_setting("fb_token_expires") or "",
            "fb_missing": missing_scopes(), "fb_valid": db.get_setting("fb_token_valid") or "",
            "channels": channels(), "last": last_run()}


def status_text() -> str:
    r = last_run()
    if not r:
        return ""
    if r.get("skipped"):
        return f"การตลาด: {r['skipped']}"
    return (f"การตลาด: รอบ {r['at'][:16].replace('T', ' ')} เผยแพร่บทความ {len(r.get('published', []))} · เสนอโพสต์ {r.get('proposed', 0)}"
            f" ({', '.join(r.get('channels') or []) or 'ยังไม่มีช่องทาง'}) · ลีด 7 วันตามช่องทาง " + " ".join(f"{k} {v}" for k, v in (r.get("attribution") or {}).items()))


# ---------- ลิงก์ติดรหัสช่องทาง ----------
def audit_link(src: str) -> str:
    return f"{_base()}/audit?src={src}"


def article_link(cid: int) -> str:
    return f"{_base()}/geo/{cid}"


def self_brand():
    bid = db.get_setting("self_brand_id")
    try:
        return db.get_brand(int(bid)) if bid else None
    except (TypeError, ValueError):
        return None


# ---------- 1) เผยแพร่คอนเทนต์ของเจอ.AI เอง ----------
def qa_ok(item) -> bool:
    """ผ่าน QA = คะแนน AEO ขาดได้ไม่เกิน 1 ข้อ และไม่มีคำโฆษณาต้องห้าม"""
    rep = geo_content.aeo_report_item(item)
    text = f"{item['title'] or ''} {item['body_md'] or ''}"
    return rep["score"] >= max(1, rep["max"] - 1) and not execs.FORBIDDEN.search(text)


def publish_self_drafts(limit: int = 2) -> list[dict]:
    """ร่างของแบรนด์เจอ.AI ที่ผ่าน QA → ขึ้นหน้า /geo บนโดเมนเอง (hosted) ไม่ต้องให้คนกด"""
    b = self_brand()
    if not b:
        return []
    out = []
    for it in db.list_content(b["id"]):
        if it["status"] != "draft" or not qa_ok(it):
            continue
        db.mark_content_published(it["id"], None, article_link(it["id"]))
        out.append({"id": it["id"], "title": it["title"]})
        if len(out) >= limit:
            break
    return out


# ---------- 2) เขียนโพสต์ ----------
def _plain(md: str) -> str:
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md or "")
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"[#>*_`|]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def hook(item, n: int = 160) -> str:
    """ประโยคเปิดจากบทความ (ข้าม TL;DR/รูป) ไม่เกิน n ตัวอักษร"""
    t = _plain(item["body_md"] or "")
    t = re.sub(r"^(สรุป|TL;DR|TLDR)[:：]?\s*", "", t, flags=re.I)
    return (t[:n].rsplit(" ", 1)[0] + "…") if len(t) > n else t


def compose(channel: str, kind: str, item=None, case: dict | None = None) -> dict:
    src = SRC.get(channel, channel)
    cta = f"เว็บธุรกิจของคุณโผล่ใน Google และคำตอบของ AI หรือยัง ตรวจฟรีใน 30 วินาที: {audit_link(src)}"
    if kind == "article" and item is not None:
        link = article_link(item["id"])
        text = f"{item['title']}\n\n{hook(item)}\n\nอ่านต่อ: {link}\n\n{cta}"
        return {"title": item["title"], "text": text[:4800 if channel == "line" else 60000], "link": link, "content_id": item["id"]}
    if kind == "case_study" and case:
        return {"title": case["title"], "text": f"{case['title']}\n\n{case['body']}\n\n{cta}", "link": audit_link(src), "content_id": case.get("content_id")}
    text = ("ลูกค้าถาม ChatGPT, Gemini หรือ Google ก่อนตัดสินใจ — แล้วเขาเจอธุรกิจของคุณไหม?\n\n"
            "เจอ.AI ตรวจเว็บให้ฟรี 16 ข้อที่ทำให้เว็บไม่โผล่ (robots, sitemap, บอท AI, เนื้อหาที่บอทอ่านไม่ออก ฯลฯ) พร้อมบอกว่าต้องแก้อะไรก่อน\n\n"
            f"ตรวจเลย: {audit_link(src)}")
    return {"title": "ชวนตรวจเว็บฟรี", "text": text, "link": audit_link(src), "content_id": None}


def write_post(channel: str, kind: str, item=None, case: dict | None = None, llm=None) -> dict:
    """ให้โมเดลเขียนโพสต์ตามทักษะ CMO จากข้อเท็จจริงที่มี (บทความ/เคส) — ผิดกติกาหรือล้ม → แม่แบบ (compose) · ลิงก์ติดรหัสช่องทางต้องอยู่ครบ"""
    base = compose(channel, kind, item, case)
    if kind == "audit_cta" and item is None and case is None:
        return base                                                 # ข้อความชวนตรวจใช้แม่แบบที่พิสูจน์แล้ว
    if llm is None:
        from . import ai_client
        llm = lambda msgs: ai_client.chat_for("marketing", msgs, max_tokens=700, json_mode=True)
    src = SRC.get(channel, channel)
    limit = "ไม่เกิน 400 ตัวอักษร ขึ้นบรรทัดใหม่บ่อย" if channel == "line" else "ไม่เกิน 120 คำ เว้นบรรทัดระหว่างย่อหน้า"
    facts = (f"หัวข้อบทความ: {item['title']}\nสาระสำคัญ: {hook(item, 400)}\nลิงก์บทความ: {article_link(item['id'])}" if item is not None
             else f"หัวข้อ: {case['title']}\nเนื้อหา: {case['body']}")
    prompt = (f"เขียนโพสต์สำหรับ {CHANNELS.get(channel, channel)} ของ เจอ.AI ประเภท {POST_KINDS.get(kind, kind)} ภาษาไทย {limit}\n"
              f"ใช้ได้เฉพาะข้อเท็จจริงต่อไปนี้ ห้ามเพิ่มตัวเลข/ผลลัพธ์ ห้ามคำว่า การันตี/รับประกัน:\n{facts}\n"
              f"ปิดท้ายด้วย CTA ชวนตรวจเว็บฟรีพร้อมลิงก์นี้ตรงตัว: {audit_link(src)}\n"
              'ตอบเป็น JSON: {"text": "เนื้อโพสต์ทั้งหมด"}')
    try:
        raw = llm([{"role": "user", "content": prompt}])
        m = re.search(r"\{[\s\S]*\}", raw or "")
        text = str((json.loads(m.group(0)) if m else {}).get("text") or "").strip()
        if len(text) < 60 or execs.FORBIDDEN.search(text):
            raise ValueError("ไม่ผ่านกติกา")
        if audit_link(src) not in text:
            text += f"\n\nตรวจเว็บฟรี: {audit_link(src)}"
        if item is not None and article_link(item["id"]) not in text:
            text += f"\nอ่านบทความ: {article_link(item['id'])}"
        return {**base, "text": text[:4800 if channel == "line" else 60000], "source": "llm"}
    except Exception:
        return {**base, "source": "template"}


# ---------- 3) เคสศึกษาจากผลจริง ----------
def metrics_for(brand) -> dict:
    """ตัวเลขจริงที่มีอยู่ของแบรนด์: คอนเทนต์ที่เผยแพร่ · วันที่เริ่ม · การมองเห็น (จาก monitoring_runs ถ้ามีคอลัมน์ตัวเลข)"""
    items = db.list_content(brand["id"])
    published = [i for i in items if i["status"] == "published"]
    started = brand["created_at"][:10] if brand["created_at"] else ""
    days = (datetime.date.today() - datetime.date.fromisoformat(started)).days if started else 0
    sov_first = sov_last = None
    try:
        with db.get_conn() as c:
            rows = c.execute(db.q("SELECT * FROM monitoring_runs WHERE brand_id=? ORDER BY id"), (brand["id"],)).fetchall()
        def pick(r):
            for k in ("sov_pct", "sov", "share_of_voice", "hits", "mentions", "found", "n_hit"):
                if k in r.keys() and isinstance(r[k], (int, float)):
                    return r[k]
            return None
        if rows:
            sov_first, sov_last = pick(rows[0]), pick(rows[-1])
    except Exception:
        pass
    h = db.last_health_check(brand["id"])
    try:
        hrep = json.loads(h["report"]) if h and isinstance(h.get("report"), str) else (h or {})
    except Exception:
        hrep = {}
    return {"published": len(published), "days": days, "sov_first": sov_first, "sov_last": sov_last,
            "health_ok": hrep.get("n_ok"), "health_fail": hrep.get("n_fail"), "market": brand["market"] or ""}


def case_study_text(brand, m: dict, named: bool, llm=None) -> dict:
    """เคสศึกษาสั้น ๆ จากตัวเลขจริงเท่านั้น (ห้ามแต่งตัวเลข ห้ามการันตี) · ไม่ได้รับอนุญาตใช้ชื่อ → เรียกตามประเภทธุรกิจ"""
    who = brand["name"] if named else (f"ธุรกิจ{m['market']}" if m.get("market") else "ลูกค้ารายหนึ่ง")
    facts = [f"ลูกค้า: {who}", f"ใช้บริการมา {m['days']} วัน", f"เผยแพร่คอนเทนต์ตอบคำถามลูกค้าแล้ว {m['published']} ชิ้น"]
    sf, sl = m.get("sov_first"), m.get("sov_last")
    if sf is not None and sl is not None and sl > sf:                 # บอกเฉพาะเมื่อดีขึ้นจริง และเป็นเปอร์เซ็นต์ที่คนอ่านรู้เรื่อง
        pct = lambda v: f"{v * 100:.0f}%" if v <= 1 else f"{v:.0f}"
        facts.append(f"การมองเห็นในคำตอบ AI จาก {pct(sf)} เป็น {pct(sl)}")
    if m.get("health_ok") is not None:
        facts.append(f"สุขภาพเว็บผ่าน {m['health_ok']} ข้อ ตก {m['health_fail']} ข้อ")
    fallback = {"title": f"เคสจริง: {who} กับการทำให้ AI รู้จักธุรกิจ",
                "body": "\n".join("• " + f for f in facts[1:]) + "\n\nทุกตัวเลขมาจากระบบวัดจริงรายสัปดาห์ ไม่ใช่การประมาณ"}
    if llm is None:
        from . import ai_client
        llm = lambda msgs: ai_client.chat_for("sales", msgs, max_tokens=700, json_mode=True)
    prompt = ("เขียนโพสต์เคสศึกษาสั้น ๆ ภาษาไทย ไม่เกิน 120 คำ สำหรับโซเชียลของ เจอ.AI (บริการทำให้เว็บโผล่ใน Google และคำตอบ AI)\n"
              "ใช้ได้เฉพาะข้อเท็จจริงเหล่านี้ ห้ามเพิ่มตัวเลขหรือผลลัพธ์อื่น ห้ามคำว่า การันตี/รับประกัน:\n- " + "\n- ".join(facts)
              + '\nตอบเป็น JSON: {"title": "หัวเรื่องสั้น", "body": "เนื้อโพสต์"}')
    try:
        raw = llm([{"role": "user", "content": prompt}])
        mobj = re.search(r"\{[\s\S]*\}", raw or "")
        d = json.loads(mobj.group(0)) if mobj else {}
        title, body = str(d.get("title") or "").strip(), str(d.get("body") or "").strip()
        if len(body) < 40 or execs.FORBIDDEN.search(title + body) or re.search(r"\d+\s*%", body) and "%" not in " ".join(facts):
            raise ValueError("ไม่ผ่านกติกา")
        return {"title": title[:100], "body": body[:1500]}
    except Exception:
        return fallback


# ---------- เสนอ/โพสต์ ----------
def publish_post(post) -> str:
    """ส่งโพสต์จริงตามช่องทาง · คืนข้อความผล (บันทึกสถานะที่แถว posts ด้วย)"""
    try:
        image = post["image"] if "image" in post.keys() else None
        if post["channel"] == "line":
            ok = (line_oa.broadcast_messages([{"type": "image", "originalContentUrl": image, "previewImageUrl": image}, {"type": "text", "text": post["text"][:5000]}])
                  if image else line_oa.broadcast([post["text"]]))
            if not ok:
                raise RuntimeError("LINE broadcast ไม่สำเร็จ (โควตาหมดหรือ token ผิด)")
            ext = "line-broadcast"
        elif post["channel"] == "facebook":
            cfg = fb_config()
            if not cfg:
                raise RuntimeError("ยังไม่ตั้ง Facebook Page")
            ext = fb_photo(cfg, post["text"], image) if image else fb_post(cfg, post["text"], post["link"])
        else:
            raise RuntimeError("ไม่รู้จักช่องทาง")
    except Exception as e:
        db.update_post(post["id"], status="failed", result=f"{type(e).__name__}: {str(e)[:160]}")
        raise
    db.update_post(post["id"], status="published", published_at=db.now(), external_id=ext, result="โพสต์แล้ว")
    return f"โพสต์ {POST_KINDS.get(post['kind'], post['kind'])} ทาง {CHANNELS.get(post['channel'], post['channel'])} แล้ว"


def fb_post(cfg: dict, message: str, link: str | None) -> str:
    data = {"message": message, "access_token": cfg["token"]}
    if link:
        data["link"] = link
    r = httpx.post(f"{GRAPH}/{cfg['page_id']}/feed", data=data, timeout=20)
    body = r.json() if r.content else {}
    if r.status_code >= 400 or "id" not in body:
        raise RuntimeError((body.get("error") or {}).get("message") or f"Facebook HTTP {r.status_code}")
    return body["id"]


def fb_photo(cfg: dict, message: str, image_url: str) -> str:
    """โพสต์รูป + ข้อความ (ลิงก์ในข้อความกดได้) — คนหยุดดูรูปมากกว่าการ์ดลิงก์เปล่า"""
    r = httpx.post(f"{GRAPH}/{cfg['page_id']}/photos", data={"url": image_url, "message": message, "access_token": cfg["token"]}, timeout=40)
    body = r.json() if r.content else {}
    if r.status_code >= 400 or not (body.get("post_id") or body.get("id")):
        raise RuntimeError((body.get("error") or {}).get("message") or f"Facebook HTTP {r.status_code}")
    return body.get("post_id") or body["id"]


def fb_test_post() -> str:
    """ทดสอบสิทธิ์โพสต์จริงโดยไม่เผยแพร่: สร้างโพสต์แบบ unpublished แล้วลบทิ้งทันที · คืนข้อความผล (ไม่โยน error)"""
    cfg = fb_config()
    if not cfg:
        return "ยังไม่ตั้ง Facebook Page"
    try:
        r = httpx.post(f"{GRAPH}/{cfg['page_id']}/feed", data={"message": "ทดสอบสิทธิ์โพสต์จากระบบ เจอ.AI (ไม่เผยแพร่ ลบอัตโนมัติ)", "published": "false",
                                                           "access_token": cfg["token"]}, timeout=20)
        body = r.json() if r.content else {}
        if r.status_code >= 400 or "id" not in body:
            return "โพสต์ไม่ได้: " + ((body.get("error") or {}).get("message") or f"HTTP {r.status_code}")[:200]
        httpx.delete(f"{GRAPH}/{body['id']}", params={"access_token": cfg["token"]}, timeout=20)
        return "โพสต์ได้ ✓ (ทดสอบแบบไม่เผยแพร่และลบแล้ว)"
    except Exception as e:
        return f"ทดสอบไม่สำเร็จ: {type(e).__name__}"


def _propose(channel: str, kind: str, c: dict) -> int:
    from . import pipeline
    pid = db.add_post(channel, kind, c.get("content_id"), c["title"], c["text"], c.get("link"))
    if c.get("image"):
        db.update_post(pid, image=c["image"])
    title = f"โพสต์ {POST_KINDS[kind]} ทาง {CHANNELS[channel]}: {c['title'][:60]}"
    did = db.add_decision("cmo", "post", title, c["text"], {"post_id": pid, "channel": channel, "kind": kind, "content_id": c.get("content_id")})
    db.update_post(pid, decision_id=did)
    pipeline._maybe_auto(did)
    return did


_LLM = None   # เทสต์ตั้งเป็นฟังก์ชันปลอมได้ · None = ใช้โมเดลตามตั้งค่า


def propose_posts(now: datetime.datetime | None = None) -> dict:
    """ต่อช่องทาง: บทความใหม่ที่ยังไม่โพสต์ (ใหม่ก่อน) + โพสต์ชวนตรวจเว็บฟรีสัปดาห์ละครั้ง ภายในโควตาต่อสัปดาห์"""
    now = now or datetime.datetime.now()
    wk = (now - datetime.timedelta(days=7)).isoformat(timespec="seconds")
    b = self_brand()
    articles = [i for i in (db.list_content(b["id"]) if b else []) if i["status"] == "published"]
    done = db.posted_keys()
    out = {"proposed": 0, "by_channel": {}}
    from . import post_studio as PS
    iso = now.isocalendar()
    week_key = iso[0] * 100 + iso[1]
    for ch in channels():
        budget = weekly() - db.count_posts_since(ch, wk)
        n = 0
        cta = audit_link(SRC.get(ch, ch))
        tip_idx = sum(1 for k in done if k[0] == ch and k[1] == "tip")
        plan = [("insight", None)]
        if not db.count_posts_since(ch, (now - datetime.timedelta(days=14)).isoformat(timespec="seconds"), kind="ai_test"):
            plan.append(("ai_test", None))
        plan += [("tip", None)] + [("article", it) for it in articles if (ch, "article", it["id"]) not in done][:2]
        for kind, it in plan:
            if budget <= 0:
                break
            if kind == "insight" and (ch, "insight", week_key) in done:
                continue
            if db.count_posts_since(ch, wk, kind=kind) and kind in ("insight", "tip"):
                continue
            c = PS.make(kind, ch, cta, _base(), idx=tip_idx, llm=_LLM, article=it)
            if not c:
                continue
            if kind == "insight":
                c["content_id"] = week_key
            if (ch, kind, c.get("content_id")) in done:
                continue
            _propose(ch, kind, c)
            done.add((ch, kind, c.get("content_id")))
            budget -= 1
            n += 1
        out["by_channel"][ch] = n
        out["proposed"] += n
    return out


def propose_case_studies(now: datetime.datetime | None = None, llm=None) -> int:
    """เดือนละเรื่องต่อช่องทาง จากลูกค้าที่ใช้บริการ ≥ 28 วันและมีคอนเทนต์เผยแพร่ · ใช้ชื่อเฉพาะแบรนด์ที่ติ๊กอนุญาต"""
    now = now or datetime.datetime.now()
    month = (now - datetime.timedelta(days=28)).isoformat(timespec="seconds")
    sb = self_brand()
    cands = []
    for b in db.list_all_brands():
        if sb and b["id"] == sb["id"]:
            continue
        m = metrics_for(b)
        if m["days"] >= 28 and m["published"] >= 3:
            cands.append((b, m))
    if not cands:
        return 0
    def _won(b):                                    # ฝ่ายผลลัพธ์เห็นว่าขยับขึ้นแล้ว = เรื่องที่ดีที่สุดสำหรับเคสศึกษา
        r = db.last_result_review(b["id"])
        return bool(r and r["state"] == "win")
    cands.sort(key=lambda x: -(x[1]["published"] + (10 if x[0]["case_study_ok"] else 0) + (50 if _won(x[0]) else 0)))
    b, m = cands[0]
    case = case_study_text(b, m, bool(b["case_study_ok"]), llm)
    n = 0
    for ch in channels():
        if db.count_posts_since(ch, month, kind="case_study"):
            continue
        _propose(ch, "case_study", write_post(ch, "case_study", case=case, llm=_LLM))
        n += 1
    return n


def attribution(days: int = 7) -> dict:
    since = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat(timespec="seconds")
    return db.count_audits_by_src(since)


@office.track("cmo", "รอบการตลาด: เผยแพร่ · เสนอโพสต์ · เคสศึกษา")
def run(now: datetime.datetime | None = None, llm=None) -> dict:
    """รอบรายวันของ CMO: เผยแพร่ร่างที่ผ่าน QA → เสนอโพสต์ → เคสศึกษา (เดือนละครั้ง) → สรุปไว้ให้สรุปเช้า"""
    if not enabled():
        res = {"skipped": "ปิดอยู่ (เปิดได้ที่หน้าการตลาด)", "at": db.now()}
        db.set_setting("cmo_last", json.dumps(res, ensure_ascii=False))
        return res
    now = now or datetime.datetime.now()
    res = {"at": db.now(), "channels": channels(), "published": publish_self_drafts(2)}
    pp = propose_posts(now)
    res["proposed"] = pp["proposed"] + propose_case_studies(now, llm)
    res["by_channel"] = pp["by_channel"]
    res["attribution"] = attribution(7)
    db.set_setting("cmo_last", json.dumps(res, ensure_ascii=False))
    return res
