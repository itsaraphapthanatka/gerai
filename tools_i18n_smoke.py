"""ทดสอบหน้าเครื่องมือหลังล็อกอิน (app/i18n/tools.pages) ทุกภาษา: เปิดได้ · <html lang> ถูก · ไม่มีไทยหลงในเนื้อหาของหน้า
· ข้อมูลตัวอย่างครบทุกส่วน (AI, Google AI, อันดับ, ความเร็ว, สุขภาพ, Search Console, SoV, รายงาน, Autopilot, ชุดติดตั้ง)
· ตรวจเฉพาะ <main> (block ของหน้า) + <title> — เมนูของ base.html แปลแยก · report.html ตรวจทั้งหน้า
· ข้อความจาก site_health อยู่ในคลังของอีกกลุ่ม (report.json) — ถ้ายังไม่มีคำแปลจะข้ามแล้วบอกไว้ ไม่นับว่าพัง
· ภาษาไทย (ค่าเริ่มต้น) ต้องเหมือนเดิม"""
import os, re, json, tempfile, datetime
os.environ["LITELLM_BASE_URL"] = ""
os.environ.setdefault("GEO_DEEP_AUDIT", "0")
os.environ.setdefault("GEO_CS", "0")
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-tools-i18n-"), "smoke.db")
import bcrypt
from fastapi.testclient import TestClient
import app.db as db
import app.i18n as I
import app.autopilot as autopilot
from app.main import app

db.init_db()
I.load(force=True)
THAI = re.compile(r"[฀-๿]")
OTHER = [l for l in I.LANGS if l != "th"]
c = TestClient(app)

email = "tools-i18n@test.com"
if not db.get_tenant_by_email(email):
    db.create_tenant(email, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Tools", is_admin=True)
c.post("/login", data={"email": email, "password": "pw123456"})
r = c.post("/brands", data={"name": "Acme Widgets", "domain": "acme-widgets.test", "market": "widgets"})
bid = int(re.search(r"/brands/(\d+)", str(r.url)).group(1))
q1 = db.add_question(bid, "best widget shop", "en")
q2 = db.add_question(bid, "widget repair near me", "en")
now = datetime.datetime.now()
iso = lambda d=0, h=0: (now - datetime.timedelta(days=d, hours=h)).isoformat(timespec="seconds")

# ข้อความจาก site_health (คลังของอีกกลุ่ม) — ถ้ายังไม่มีคำแปลให้ข้าม
EXTERNAL = ["หน้าแรกเข้าถึงได้", "ตอบ 503"]

# ---------- ข้อมูลตัวอย่าง ----------
# คอนเทนต์ (ร่าง + เผยแพร่แล้ว)
cid = db.create_content_item(bid, q1, "en", "Best widget shop guide", "Best widget shop", "How to pick a widget shop",
                             "> Summary: pick a certified shop.\n\n## Why\n- reason one\n- reason two", "[]", "template")
cid2 = db.create_content_item(bid, q2, "en", "Widget repair guide", "Widget repair", "Repair tips", "body", "[]", "template")
db.mark_content_published(cid2, None, "https://acme-widgets.test/geo/%d" % cid2)

# SoV — 2 รอบ
with db.get_conn() as conn:
    runs = []
    for at, hits, sov in (("2026-06-01T10:00:00", 0, 0.0), ("2026-06-20T10:00:00", 1, 0.5)):
        runs.append(conn.execute(db.q("INSERT INTO monitoring_runs(brand_id,started_at,status,questions_total,brand_hits,share_of_voice) VALUES(?,?,?,?,?,?) RETURNING id"),
                                 (bid, at, "done", 2, hits, sov)).fetchone()[0])
    for rid, present in ((runs[0], 0), (runs[1], 1)):
        conn.execute(db.q("INSERT INTO run_results(run_id,question_id,question,brand_present,position,top_domains) VALUES(?,?,?,?,?,?)"),
                     (rid, q1, "best widget shop", present, 3 if present else None, json.dumps(["rival.test", "other.test"])))
        conn.execute(db.q("INSERT INTO run_results(run_id,question_id,question,brand_present,position,top_domains) VALUES(?,?,?,?,?,?)"),
                     (rid, q2, "widget repair near me", 0, None, json.dumps(["rival.test"])))

# อันดับ Google — 2 รอบ (ดีขึ้น / หลุด)
for at, p1, p2 in ((iso(7), 8, 5), (iso(0), 4, None)):
    db.add_rank_result(bid, q1, "best widget shop", p1, "https://acme-widgets.test/a", "serper", at)
    db.add_rank_result(bid, q2, "widget repair near me", p2, "https://acme-widgets.test/b" if p2 else None, "serper", at)

# ผู้ช่วย AI — ทุกสถานะ
rows = []
for e, st in (("chatgpt", "cited"), ("chatgpt", "absent"), ("perplexity", "named"), ("perplexity", "unsearched"),
              ("gemini", "no_answer"), ("gemini", "error")):
    rows.append({"engine": e, "question": "best widget shop" if len([x for x in rows if x["engine"] == e]) == 0 else "widget repair near me",
                 "status": st, "cited": st == "cited", "named": st in ("cited", "named"), "others": ["rival.test"],
                 "reason": "HTTPStatusError: 500" if st == "error" else "", "citations": ["https://acme-widgets.test/geo/1", "https://rival.test"],
                 "usage": {"in": 100, "out": 50, "searches": 1}, "cost_usd": 0.01})
by_engine = {e: {"label": e.title(), "asked": 1, "cited": int(e == "chatgpt"), "named": 1, "skipped": 1, "errors": int(e == "gemini"),
                 "unsearched": int(e == "perplexity"), "no_answer": int(e == "gemini"), "cost_usd": 0.02, "tokens_in": 200, "tokens_out": 100, "searches": 2}
             for e in ("chatgpt", "perplexity", "gemini")}
db.add_ai_visibility(bid, {"checked_at": iso(0), "apex": "acme-widgets.test", "rows": rows, "by_engine": by_engine, "asked": 3,
                           "unsearched": 1, "no_answer": 1, "cited": 1, "named": 2, "rate": 67, "cost_usd": 0.06,
                           "tokens_in": 600, "tokens_out": 300, "searches": 6})

# Google AI Overview / AI Mode
srows = [{"kind": k, "question": qq, "status": st, "cited": st == "cited", "named": st == "named", "others": ["rival.test"],
          "sources": ["https://rival.test/x"], "excerpt": "Widgets are great.", "reason": "RuntimeError: quota" if st == "error" else "", "searches": 1}
         for k, qq, st in (("overview", "best widget shop", "cited"), ("overview", "widget repair near me", "none"),
                           ("mode", "best widget shop", "named"), ("mode", "widget repair near me", "error"))]
bk = {"overview": {"label": "AI Overview", "asked": 2, "shown": 1, "cited": 1, "named": 0, "absent": 0, "none": 1, "errors": 0, "rate": 100},
      "mode": {"label": "AI Mode", "asked": 2, "shown": 1, "cited": 0, "named": 1, "absent": 0, "none": 0, "errors": 1, "rate": 100}}
db.add_ai_serp(bid, {"checked_at": iso(0), "rows": srows, "by_kind": bk, "asked": 4, "searches": 5, "skip": ""})

# ความเร็ว — มือถือครบ / เดสก์ท็อปพัง (ข้อความ Google ตอบ …)
mobile = {"scores": {"performance": 45, "seo": 92, "accessibility": 80, "best-practices": 95},
          "metrics": [{"id": "largest-contentful-paint", "label": "LCP", "display": "4.1 s", "score": 30},
                      {"id": "cumulative-layout-shift", "label": "CLS", "display": "0.02", "score": 99}],
          "field": {"overall": "AVERAGE", "origin_fallback": True,
                    "metrics": [{"id": "LARGEST_CONTENTFUL_PAINT_MS", "label": "LCP", "percentile": 2900, "category": "AVERAGE"},
                                {"id": "CUMULATIVE_LAYOUT_SHIFT_SCORE", "label": "CLS", "percentile": 5, "category": "FAST"}]},
          "opportunities": [{"id": "render-blocking-resources", "title": "Eliminate render-blocking resources", "savings_ms": 1200}],
          "final_url": "https://acme-widgets.test/", "lighthouse": "12.0.0"}
for d in (1, 0):
    db.add_pagespeed(bid, {"checked_at": iso(d), "url": "https://acme-widgets.test", "with_key": False,
                           "mobile": mobile, "desktop": {"error": "Google ตอบ 500: Internal error"}})

# สุขภาพเว็บ — หน้าแรกพัง (รายงานเชื่อไม่ได้) + เว็บล่ม
db.add_health_check(bid, {"checked_at": iso(0), "ok": False, "n_fail": 1, "n_warn": 1, "n_ok": 1, "site": "https://acme-widgets.test",
                          "checks": [{"key": "home", "label": EXTERNAL[0], "status": "fail", "detail": EXTERNAL[1]},
                                     {"key": "robots", "label": "robots.txt", "status": "ok", "detail": "OK"},
                                     {"key": "sitemap", "label": "sitemap.xml", "status": "warn", "detail": "3 URLs"},
                                     {"key": "llms", "label": "llms.txt", "status": "skip", "detail": ""}]})
db.set_setting(f"uptime:{bid}", json.dumps({"up": False, "since": iso(2), "reason": "HTTP 503", "code": 503, "checked_at": iso(0)}))

# Search Console — เชื่อมแล้ว ทุกกลุ่มสถานะ + หมายเหตุ sitemap + ขั้นที่พัง
pages = [{"title": "Page %s" % g, "url": "https://acme-widgets.test/geo/%d" % i, "group": g, "state": "Some state", "stale": g == "blocked" and i == 9,
          "last_crawl": "2026-10-01T00:00:00Z", "clicks": 1, "impressions": 10, "position": 5.5, "in_sitemap": True, "referrers": 2,
          "link": "https://search.google.com/x"}
         for i, g in enumerate(("indexed", "crawled", "discovered", "unknown", "blocked", "other", "error", "blocked", "indexed", "blocked"))]
gsc_rep = {"synced_at": iso(0), "linked": True, "email": "svc@project.iam.gserviceaccount.test", "property": "sc-domain:acme-widgets.test",
           "permission": "siteUnverifiedUser", "tried": ["sc-domain:acme-widgets.test"], "sites_seen": 1, "reason": "",
           "sitemap": {"url": "https://acme-widgets.test/geo-sitemap.xml", "submitted": True, "just_submitted": True, "re_submitted": True,
                       "is_pending": True, "last_submitted": "2026-10-01", "last_downloaded": None, "errors": 1, "warnings": 2, "n_urls": 12,
                       "note": "Google ไม่ได้อ่าน sitemap มา 40 วัน — ส่งซ้ำให้แล้วเพื่อกระตุ้นให้อ่านใหม่"},
           "index": {"indexed": 2, "crawled": 1, "discovered": 1, "unknown": 1, "blocked": 3, "other": 1, "error": 1, "total": 30, "inspected": 10},
           "pages": pages,
           "analytics": {"start": "2026-09-01", "end": "2026-09-28", "site": {"clicks": 1234, "impressions": 5678, "position": 7.2},
                         "content": {"clicks": 3}, "content_queries": [{"query": "widget shop", "clicks": 2, "impressions": 20, "position": 4.1}],
                         "top_queries": [{"query": "acme widgets", "clicks": 9, "impressions": 30, "position": 1.2}]},
           "errors": [{"step": "analytics", "msg": "Google ตอบ 403: forbidden"}]}
db.add_gsc_snapshot(bid, gsc_rep)

# Autopilot — ข้อความทุกขั้นมาจากโค้ดจริง (autopilot.run ด้วยฟังก์ชันจำลอง)
log = autopilot.run(
    {"id": bid, "name": "Acme Widgets"},
    {"sov": lambda b: {"brand_hits": 1, "questions": 2}, "rank": lambda b: {"ranked": 1, "checked": 2, "avg_position": 4.0},
     "gsc": lambda b: gsc_rep, "health": lambda b: {"ok": False, "n_fail": 1, "checks": [{"label": "robots.txt", "status": "fail"}]},
     "speed": lambda b: {"mobile": mobile}, "ai": lambda b: {"asked": 3, "rate": 67, "cost_usd": 0.06},
     "aiserp": lambda b: {"skip": "ยังไม่ได้ตั้งคีย์ SerpApi (ผู้ดูแล → ตั้งค่า → Google AI Overview / AI Mode)"},
     "content": lambda b, n, m: [{"id": cid, "title": "Best widget shop guide", "status": "draft"},
                                 {"status": "await_measure", "msg": "ปิดครบทุกคำถามที่วัดแล้ว · คำถามใหม่ 2 ข้อรอมอนิเตอร์รอบหน้า วัดก่อน — จะเขียนให้เฉพาะข้อที่ยังไม่โผล่"},
                                 {"status": "topup", "questions": ["widget price", "widget warranty"]},
                                 {"status": "quota", "question": "widget care", "msg": "quota"},
                                 {"id": cid2, "title": "Widget repair guide", "status": "เผยแพร่ไม่สำเร็จ: Connector ตอบ HTTP 500: oops"}],
     "report": lambda b, lg: "ไม่มี SMTP — ดูรายงานได้ที่หน้า รายงาน & ส่งออก", "notify": lambda b, lg: True},
    {"speed": iso(0, 3)}, content_n=1, mode="draft")
db.add_autopilot_run(bid, log)
assert any(s["detail"] == "เพิ่งทำเมื่อ 3 ชม.ก่อน" for s in log["steps"]), log["steps"]

PAGES = {
    "content": f"/content/{cid}", "ai": f"/brands/{bid}/ai", "aiserp": f"/brands/{bid}/ai-serp?saved=1", "rank": f"/brands/{bid}/rank",
    "speed": f"/brands/{bid}/speed", "health": f"/brands/{bid}/health", "gsc": f"/brands/{bid}/gsc", "impact": f"/brands/{bid}/impact",
    "progress": f"/brands/{bid}/progress", "run": f"/runs/{runs[1]}", "autopilot": f"/brands/{bid}/autopilot?saved=1",
    "assets": f"/brands/{bid}/assets?saved=1", "report": f"/brands/{bid}/report",
}
# ข้อความไทยเดิมที่ต้องยังอยู่ในหน้า lang=th
TH_MARK = {"content": "ความพร้อม AEO / GEO", "ai": "การมองเห็นบน AI", "aiserp": "AI Overview พูดถึงเรา", "rank": "อันดับเฉลี่ย",
           "speed": "ข้อมูลผู้ใช้จริง (Chrome UX Report)", "health": "ผลตรวจด้านล่างเชื่อไม่ได้", "gsc": "หน้าคอนเทนต์รายหน้า",
           "impact": "ผลกระทบ SoV", "progress": "รายคำถาม: ก่อน → หลัง", "run": "รายงานการมองเห็นบน AI", "autopilot": "ประวัติการรัน",
           "assets": "ชุดติดตั้ง on-site", "report": "รายงาน GEO รายเดือน"}
base_converted = "html_lang()" in open("app/templates/base.html", encoding="utf-8").read()


def page_part(name: str, html: str) -> str:
    """ส่วนที่หน้านี้รับผิดชอบ: report = ทั้งหน้า · อื่น ๆ = <title> + <main> · ตัด <pre> (ไฟล์/โค้ดให้คัดลอก = ข้อมูล)"""
    if name == "report":
        part = html
    else:
        t = re.search(r"<title>(.*?)</title>", html, re.S).group(1)
        m = re.search(r"<main>([\s\S]*)</main>", html)
        assert m, (name, "ไม่เจอ <main>")
        part = t + "\n" + m.group(1)
    part = re.sub(r"<pre[\s\S]*?</pre>", " ", part)
    part = re.sub(r"<style[\s\S]*?</style>|<!--[\s\S]*?-->", " ", part)
    part = re.sub(r"เจอ(?=<span class=\"ai\">|\.AI)|฿", "", part)
    return part


skipped_external = set()
for lg in OTHER:
    c.cookies.set(I.COOKIE, lg)
    for name, url in PAGES.items():
        r = c.get(url)
        assert r.status_code == 200, (name, lg, r.status_code)
        want = f'<html lang="{I.HTML_LANG[lg]}">'
        if name == "report" or base_converted:
            assert want in r.text, (name, lg, "html lang")
        part = page_part(name, r.text)
        for s in EXTERNAL:
            if not I.has(s) and s in part:
                part = part.replace(s, " ")
                skipped_external.add(s)
        left = re.findall(r"[^\s<>\"']*[฀-๿]+[^\s<>\"']*", part)
        assert not left, (name, lg, left[:8])
    # ยืนยันว่าแปลจริง (ไม่ใช่แค่ไม่มีไทย)
    en_ok = c.get(PAGES["ai"]).text
    if lg == "en":
        assert "AI visibility" in en_ok and "Not mentioned" in en_ok and "No search" in en_ok
        assert "Google replied 500: Internal error" in c.get(PAGES["speed"]).text
        g = c.get(PAGES["gsc"]).text
        assert "Crawled by Google, not indexed" in g and "Google hasn&#39;t read the sitemap for 40 days" in g and "Unverified permission" in g
        a = c.get(PAGES["autopilot"]).text
        assert "done 3 h ago" in a and "All questions covered — added 2 new questions" in a and "Mode: Create as draft" in a and "publish failed: Connector replied HTTP 500: oops" in a
        rp = c.get(PAGES["report"]).text
        assert "Next steps" in rp and "Monthly GEO report" in rp
    # ปุ่มเผยแพร่ (เว็บไม่เชื่อม WP → notice จาก main.py)
    r = c.post(f"/content/{cid}/publish", data={"status": "draft"})
    assert r.status_code == 200 and not THAI.search(page_part("content", r.text)), (lg, "publish notice", re.findall(r"\S*[฀-๿]+\S*", page_part("content", r.text))[:4])
    if lg == "en":
        assert "This site isn&#39;t connected to WordPress — click" in r.text, "publish notice en"
    print(f"{lg}: {len(PAGES)} หน้า + notice เผยแพร่ — 200 · ไม่มีไทยในเนื้อหาของหน้า OK")

c.cookies.set(I.COOKIE, "ja")
assert '<html lang="ja">' in c.get(PAGES["report"]).text and "AI での露出" in c.get(PAGES["ai"]).text
print("base.html ยังไม่ได้แปล — ข้ามการตรวจ <html lang> ของหน้าที่ extends base" if not base_converted else "<html lang> ถูกทุกหน้า OK")
if skipped_external:
    print("ข้าม (คลังของ site_health ยังไม่มีคำแปล):", sorted(skipped_external))

# ---------- ไทยเหมือนเดิม ----------
c.cookies.set(I.COOKIE, "th")
for name, url in PAGES.items():
    r = c.get(url + ("&" if "?" in url else "?") + "lang=th")
    assert r.status_code == 200 and TH_MARK[name] in r.text, (name, "th")
    assert '<html lang="th">' in r.text, (name, "th html lang")
assert "ยังไม่ได้ตั้ง OPENAI_API_KEY" == I.tr("ยังไม่ได้ตั้ง OPENAI_API_KEY", "th")
assert I.tr("ยังไม่ได้ตั้ง OPENAI_API_KEY", "ko") == "OPENAI_API_KEY이(가) 설정되지 않았습니다"
print("th: ทุกหน้าคงข้อความไทยเดิม · <html lang=\"th\"> OK")

# ---------- PDF (render_html ใช้ jinja env แยก ไม่มี request — ภาษามาจากพารามิเตอร์ lang) ----------
import inspect
import app.report as report
d = report.collect(bid)
html = report.render_html(d, pdf=True)
assert "รายงาน GEO รายเดือน" in html and '<html lang="th">' in html and "fonts.googleapis" not in html
if "lang" in inspect.signature(report.render_html).parameters:
    for lg in OTHER:
        html = report.render_html(d, pdf=True, lang=lg)
        assert f'<html lang="{I.HTML_LANG[lg]}">' in html, lg
        left = re.findall(r"[^\s<>\"']*[฀-๿]+[^\s<>\"']*", page_part("report", html))
        assert not left, ("pdf", lg, left[:8])
    print("render_html(pdf=True): ไทยเดิม · 4 ภาษาไม่มีไทยหลง OK")
else:
    print("render_html(pdf=True): ไทยเดิม OK (report.py ยังไม่รับ lang)")
print("ALL OK")
