"""ทดสอบหน้ารายงานตรวจเว็บฟรี (/audit/<token>) ทุกภาษา: en zh ja ko ต้องไม่มีภาษาไทยหลง (ทั้งข้อความ แอตทริบิวต์ และสคริปต์)
· ข้อมูลตั้งต้นเป็นภาษาอังกฤษทั้งหมด (ชื่อหน้า คำค้น คำถาม AI) เพื่อให้จับไทยหลงได้เข้ม — ไทยที่เหลือต้องมาจากข้อความของระบบเท่านั้น
· ผลตรวจหน้าแรกสร้างจากตัวตรวจจริง (site_health.judge_*) · รายงานเต็มสร้างจาก deep_audit จริง (ไต่เว็บปลอม + อันดับปลอม + AI ปลอม)
· ครอบคลุม: ผลครบ 3 แท็บ · กำลังไต่ · คิว AI เต็ม · ค้นอันดับไม่ได้ · ผ่านทุกข้อ · รับเรื่อง/ลูกค้าแล้ว/ขอไม่รับ · ฟอร์มติดต่อผิด · progress
· หน้าไทยยังแสดงข้อความไทยเดิม · แชตขาย (ตอบไทยเท่านั้น) แสดงเฉพาะหน้าไทย"""
import os, tempfile, json, re
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-report-i18n-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["GEO_DEEP_AUDIT"] = "0"                      # ไม่ไต่เว็บจริงเบื้องหลัง
import httpx
from fastapi.testclient import TestClient
import app.db as db, app.deep_audit as D, app.site_health as sh, app.i18n as I
from app.main import app

db.init_db()
I.load(force=True)
OTHER = [l for l in I.LANGS if l != "th"]
THAI = re.compile(r"[฀-๿]")
SITE, APEX = "https://www.acme-print.example", "acme-print.example"

# ---------- ผลตรวจหน้าแรก (ตัวตรวจจริง ข้อมูลปลอม) ----------
home_html = '<html><head><title>Acme Print | Custom T-shirts</title></head><body><div id="root"></div><script src="/app.js"></script></body></html>'
checks = []
checks += sh.judge_home(200, {"X-Robots-Tag": "noindex"}, home_html)
checks += sh.judge_csr(sh.page_shape(home_html))
checks += sh.judge_soft404(200)
checks += sh.judge_www(200, "", APEX)
checks += sh.judge_robots(200, "User-agent: GPTBot\nDisallow: /\n")
checks += sh.judge_links("llms", "llms.txt", 404, [], {})
checks += sh.judge_links("sitemap", "geo-sitemap.xml", 404, [], {})
checks += sh.judge_sitemap_domain(["https://cdn.other-host.example/a"], APEX)
checks += sh.judge_pages("Acme Print | Custom T-shirts", [
    {"url": f"{SITE}/geo/1", "title": "Acme Print | Custom T-shirts", "canonical": f"{SITE}/"},
    {"url": f"{SITE}/geo/2", "title": "Acme Print | Custom T-shirts", "canonical": ""}])
checks += sh.judge_connector({"/geo": {"status": 404, "url": f"{SITE}/geo", "html": ""}}, set(), APEX)
checks += sh.judge_orphan(0, 3, 2)
checks += sh.judge_indexed(0, None, "", 0)
checks += sh.judge_freshness(None, 0, None)
assert {c["status"] for c in checks} >= {"fail", "warn"} and any(THAI.search(c["detail"] or "") for c in checks)
REPORT = sh.summarize(checks)
ALL_OK = sh.summarize(sh.judge_home(200, {}, "<html><title>ok</title><h1>ok</h1></html>")
                      + sh.judge_robots(200, "User-agent: *\nAllow: /\nSitemap: https://acme-print.example/sitemap.xml\n")
                      + sh.judge_orphan(3, 3, 2) + sh.judge_indexed(None, None, "", 0))

# ---------- รายงานเต็ม (deep_audit จริง เว็บปลอม) ----------
def page(title, h1="", desc="", links=(), imgs=0, noindex=False):
    return (f"<html><head><title>{title}</title>" + (f'<meta name="description" content="{desc}">' if desc else "")
            + ('<meta name="robots" content="noindex">' if noindex else "") + "</head><body>" + (f"<h1>{h1}</h1>" if h1 else "")
            + "".join(f'<a href="{l}">x</a>' for l in links) + '<img src="/logo.png">' * imgs + "</body></html>")
PAGES = {
    "/": page("Acme Print | Custom T-shirts", "Custom T-shirts", "Screen printing", ["/about/", "/services/", "/services-2/", "/contact/", "info@acme-print.example", "/gone/", "/broken/"], imgs=2),
    "/about/": page("About", "", "", ["/"]),
    "/services/": page("Our services at Acme Print", "Services", "Printing", ["/"]),
    "/services-2/": page("Our services at Acme Print", "Services", "Printing", ["/"]),
    "/contact/": page("Contact Acme Print team", "Contact", "Contact us", ["/"], noindex=True),
    "/hidden/": page("Hidden page at Acme Print", "Hidden", "No links", ["/"]),
}
def handler(req: httpx.Request):
    if req.url.scheme == "http":
        return httpx.Response(200, html="<html>plain http</html>")
    p = req.url.path
    if p == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
    if p == "/sitemap.xml":
        return httpx.Response(200, text=f"<urlset><url><loc>{SITE}/</loc></url><url><loc>{SITE}/hidden/</loc></url></urlset>", headers={"content-type": "application/xml"})
    if p == "/broken/":
        return httpx.Response(500, html="err")
    if p in PAGES:
        return httpx.Response(200, html=PAGES[p])
    return httpx.Response(404, html="<html><title>404</title></html>")
cr = D.crawl(SITE, client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True))
iss = D.issues(cr)
assert {"mailto", "4xx", "5xx", "https", "noindex", "dup_title", "h1_missing", "desc_missing", "short_title", "alt_missing"} <= {i["key"] for i in iss}, [i["key"] for i in iss]
hl = D.health(cr, iss)
KW = ["custom t-shirts", "screen printing shop", "team shirts cheap", "print hoodies fast", "logo embroidery"]
def search(q, p):
    if q == "logo embroidery":
        raise RuntimeError("search down")
    if p == 2:
        return []
    doms = ["youtube.com", "rival-print.example", "www.shirtco.example", "facebook.com", "a.example", "b.example", "c.example", "d.example", "e.example", "f.example"]
    rows = [{"domain": d, "url": f"https://{d}/x", "position": i + 1} for i, d in enumerate(doms)]
    if q == "custom t-shirts":
        rows[2] = {"domain": "www." + APEX, "url": SITE + "/services/", "position": 3}
    return rows
rk = D.ranking(APEX, KW, search=search)
assert rk["page1"] == 1 and any(r.get("error") for r in rk["rows"]) and rk["competitors"]
PROMPTS = [f"question {i} about custom t-shirts" for i in range(20)]
def ask(q):
    i = int(re.search(r"\d+", q).group(0))
    if i == 3:
        return {"ok": False, "reason": "timeout"}
    cites = [SITE + "/about/"] if i == 1 else (["https://rival-print.example/blog/1"] if i % 2 == 0 else [])
    return {"ok": True, "text": "Acme Print and rival-print.example" if i in (1, 5) else "rival-print.example is good", "citations": cites, "cost_usd": 0.01}
ai = D.ask_prompts(PROMPTS, APEX, "Acme Print", [c["domain"] for c in rk["competitors"]][:3], ask=ask, engine="chatgpt")
T1 = {"state": "done", "at": db.now(), "brand": "Acme Print", "crawl": {k: cr[k] for k in ("n", "queued", "blocked", "truncated", "https_redirect", "seconds", "at")},
      "pages": [], "issues": iss, "health": hl, "keywords": KW, "ranking": rk}
T2 = {"state": "done", "at": db.now(), **ai}

def lead(dom, token, audit=REPORT, deep=None, deep_ai=None, **kw):
    lid = db.add_lead(f"https://www.{dom}", dom, source="audit")
    db.update_lead(lid, audit_json=json.dumps(audit, ensure_ascii=False), audit_at=db.now(), token=token,
                   **({"deep_json": json.dumps(deep, ensure_ascii=False)} if deep else {}),
                   **({"deep_ai_json": json.dumps(deep_ai, ensure_ascii=False)} if deep_ai else {}), **kw)
    return lid

rank_fail = dict(rk, ok=0, page1=0, rows=[dict(r, error="RuntimeError", position=None) for r in rk["rows"]], competitors=[])
no_rank = dict(rk, page1=0, rows=[dict(r, position=None) for r in rk["rows"] if not r.get("error")], ok=4)
lead("acme-print.example", "full", deep=T1, deep_ai=T2, consent=1, email="owner@acme-print.example", status="contacted")
lead("acme-running.example", "running", deep={"state": "running", "started_at": db.now(), "step": "ไต่เว็บ"})
lead("acme-queued.example", "queued", deep=dict(T1, ranking=rank_fail), deep_ai={"state": "queued", "at": db.now(), "reason": "ครบเพดานวันนี้ 5 ราย — ทีมจะวัดให้ภายใน 1 วันทำการ"})
lead("acme-norank.example", "norank", audit=ALL_OK, deep=dict(T1, ranking=no_rank, crawl=dict(T1["crawl"], truncated=True, blocked=2)))
lead("acme-quoted.example", "quoted", deep=T1, consent=1, status="quoted")
lead("acme-won.example", "won", deep=T1, consent=1, status="won")
lead("acme-unsub.example", "unsub", deep=T1, consent=1, status="unsubscribed")
TOKENS = ["full", "running", "queued", "norank", "quoted", "won", "unsub"]

def leftover(html: str) -> list[str]:
    """ไทยที่เหลือในหน้า (ทั้งข้อความ แอตทริบิวต์ และสคริปต์) ยกเว้นชื่อแบรนด์ เจอ.AI · ฿ · ชื่อภาษา 'ไทย' ในตัวเลือกภาษา"""
    s = re.sub(r"<style[\s\S]*?</style>|<!--[\s\S]*?-->", " ", html)
    s = re.sub(r"เจอ\.AI|เจอ|฿|ไทย", "", s)
    return re.findall(r"\S*[฀-๿]+\S*", s)[:8]

c = TestClient(app)
for lg in OTHER:
    for tok in TOKENS:
        r = c.get(f"/audit/{tok}?lang={lg}")
        assert r.status_code == 200, (tok, lg, r.status_code)
        assert f'<html lang="{I.HTML_LANG[lg]}">' in r.text, (tok, lg)
        assert 'class="ds-lang"' in r.text and "jc-btn" not in r.text, (tok, lg)          # แชตขายตอบไทยเท่านั้น → ไม่แสดง
        left = leftover(r.text)
        assert not left, (tok, lg, left)
    # ฟอร์มติดต่อกรอกไม่ครบ → ข้อความผิดพลาดตามภาษา
    for data in ({"email": "", "contact": "", "consent": "1"}, {"email": "owner@acme-running.example", "consent": ""}):
        r = c.post(f"/audit/running/contact?lang={lg}", data=data)
        assert r.status_code == 200 and 'class="err"' in r.text and not leftover(r.text), (lg, data, leftover(r.text))
    p = c.get(f"/audit/running/progress?lang={lg}").json()
    assert p["tier1"] == "running" and p["step"] and not THAI.search(p["step"]), (lg, p)
print(f"report: {len(TOKENS)} สถานะ × {len(OTHER)} ภาษา · ฟอร์มผิด · progress: ไม่มีไทยหลง · <html lang> ถูก OK")

# ข้อความจากระบบที่แปลเฉพาะภาษา (ตัวอย่างที่ต้องเจอ)
en = c.get("/audit/full?lang=en").text
for s in ("Results for acme-print.example", "Key findings", "Email links missing mailto:", "Found on /", "Indexed by Google",
          "The page is built entirely with JavaScript", "characters of text", "Blocked: GPTBot", "Cited ✓", "Got it."):
    assert s in en, s
ja = c.get("/audit/queued?lang=ja").text
assert "Google の順位を一時的に取得できません" in ja and "本日の AI 測定枠はいっぱいです" in ja
ko = c.get("/audit/norank?lang=ko").text
assert "아직 어떤 키워드에서도" in ko and "크롤링 한도" in ko and "홈페이지가 모든 점검 항목을 통과했습니다" in ko
assert "--font:'DM Sans','Noto Sans KR'," in ko, "ต้องใช้ฟอนต์เกาหลี"
print("report: ข้อความจาก site_health / deep_audit / leads แปลจริง · ฟอนต์ตามภาษา OK")

# ---------- หน้าไทยยังเป็นไทยเดิม ----------
th = c.get("/audit/full?lang=th").text
assert '<html lang="th">' in th
for s in ("ผลตรวจ acme-print.example", "สรุปสำคัญ", "ลิงก์อีเมลไม่มี mailto", "หน้าเว็บสร้างด้วย JavaScript ล้วน", "Google เก็บเข้า index แล้ว",
          "20 คำถามที่ลูกค้าถาม AI", "อ้างอิง ✓", "ถูกบล็อก: GPTBot", "รับเรื่องแล้ว ทีมเจอ.AI"):
    assert s in th, s
th2 = c.get("/audit/running").text                                   # คุกกี้จำไทยไว้แล้ว
assert '<html lang="th">' in th2 and "ส่งแผนแก้ให้ฉัน" in th2 and "วัดฟรีเมื่อขอแผนแก้" in th2
assert "jc-btn" in th2, "แชตขายต้องแสดงบนหน้าไทย"
assert c.get("/audit/running/progress").json()["step"] == "ไต่เว็บ"
print("report: หน้าไทยแสดงข้อความไทยเดิม · แชตขายเฉพาะไทย · progress ไทย OK")
print("ALL OK")
