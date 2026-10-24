"""ทดสอบรายงานเต็ม: ไต่เว็บ (BFS + sitemap + robots) · ปัญหา 4XX/5XX/ลิงก์อีเมลไม่มี mailto/http ไม่ไป https/title ซ้ำ/ไม่มี H1/description/alt/orphan
· คะแนน health · เลือกคำค้น · อันดับ + Top 10 + คู่แข่ง (ตัดแพลตฟอร์ม) · 20 คำถาม AI (เอ่ยชื่อ/อ้างอิง/คู่แข่ง/ค่าใช้จ่าย) · เพดานต่อวัน
· หน้า /audit/<token> 3 แท็บ + progress · ติดต่อ/ขอใบเสนอราคา → วัด AI · ปุ่มแอดมิน"""
import os, tempfile, json, re
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke8-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["GEO_DEEP_AUDIT"] = "1"
import httpx, bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.deep_audit as D, app.pipeline as P, app.site_health as sh, app.ai_client as ai, app.mailer as mailer, app.leads as L
from app.main import app

SITE = "https://www.shopabc.co.th"
def page(title, h1="", desc="", links=(), imgs=0, alt=0):
    a = "".join(f'<a href="{l}">x</a>' for l in links)
    im = "".join('<img src="/a.png">' for _ in range(imgs)) + "".join('<img src="/b.png" alt="รูป">' for _ in range(alt))
    return (f"<html><head><title>{title}</title>" + (f'<meta name="description" content="{desc}">' if desc else "") + "</head><body>"
            + (f"<h1>{h1}</h1>" if h1 else "") + a + im + "</body></html>")
PAGES = {
    "/": page("ABC Shirt | รับผลิตเสื้อยืด สกรีนเสื้อ", "รับผลิตเสื้อยืด", "โรงงานผลิตเสื้อ", ["/about/", "/services/", "/services-2/", "/contact/", "info@shopabc.co.th",
              "/old-page/", "/broken/", "/private/x", "mailto:sale@shopabc.co.th", "https://facebook.com/abc", "/catalog.pdf"], imgs=2, alt=1),
    "/about/": page("เกี่ยวกับเรา ABC Shirt", "", "", ["/"]),
    "/services/": page("บริการของเรา", "บริการ", "บริการสกรีน", ["/", "/contact/"]),
    "/services-2/": page("บริการของเรา", "บริการ", "บริการสกรีน", ["/"]),
    "/contact/": page("ติดต่อ ABC Shirt", "ติดต่อ", "ติดต่อเรา", ["/", "info@shopabc.co.th"]),
    "/orphan/": page("หน้าลับ ABC Shirt", "หน้าลับ", "ไม่มีใครลิงก์มา", ["/"]),
}
def handler(req: httpx.Request):
    u = req.url
    if u.scheme == "http":
        return httpx.Response(200, html="<html>http</html>")                     # ไม่ redirect ไป https = ปัญหา
    p = u.path
    if p == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
    if p == "/sitemap.xml":
        return httpx.Response(200, text="<urlset><url><loc>%s/</loc></url><url><loc>%s/orphan/</loc></url></urlset>" % (SITE, SITE), headers={"content-type": "application/xml"})
    if p == "/broken/":
        return httpx.Response(500, html="err")
    if p in PAGES:
        return httpx.Response(200, html=PAGES[p])
    return httpx.Response(404, html="<html><title>404</title></html>")
mk_client = lambda: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True, headers={"User-Agent": D.UA})

# ---- ไต่ + ปัญหา + คะแนน ----
cr = D.crawl(SITE, client=mk_client())
urls = {p["url"]: p for p in cr["pages"]}
assert f"{SITE}/orphan/" in urls and urls[f"{SITE}/orphan/"]["inbound"] == 0, "sitemap ต้องพาไปหน้า orphan"
assert not any("/private" in u for u in urls) and cr["blocked"] == 1, "ต้องเคารพ robots.txt"
assert not any(u.endswith(".pdf") or "facebook" in u for u in urls) and cr["https_redirect"] is False
assert urls[f"{SITE}/info@shopabc.co.th"]["status"] == 404 and urls[f"{SITE}/contact/info@shopabc.co.th"]["status"] == 404
iss = D.issues(cr); by = {i["key"]: i for i in iss}
assert by["mailto"]["count"] == 2 and "mailto:" in by["mailto"]["fix"] and by["4xx"]["count"] == 1 and by["5xx"]["count"] == 1
assert by["https"]["severity"] == "Error" and set(by["dup_title"]["urls"]) == {f"{SITE}/services/", f"{SITE}/services-2/"}
assert f"{SITE}/about/" in by["h1_missing"]["urls"] and f"{SITE}/about/" in by["desc_missing"]["urls"] and by["alt_missing"]["count"] == 1   # รูปเดียวกันซ้ำหลายจุด/หลายหน้า นับครั้งเดียว
assert by["orphan"]["urls"] == [f"{SITE}/orphan/"]
assert [i["group"] for i in iss] == sorted([i["group"] for i in iss], key=D.GROUPS.index) and iss[0]["group"] == "Crawl & Index"
hl = D.health(cr, iss); assert 0 < hl["score"] < 70 and hl["urls"] == cr["n"] and hl["errors"] >= 7, hl
print(f"crawl: {cr['n']} หน้า · sitemap · robots · mailto/4xx/5xx/https/ซ้ำ/H1/desc/alt/orphan · health {hl['score']} OK")

# ---- คำค้น + อันดับ ----
brand = D._brand_name(cr, None); assert brand == "ABC Shirt", brand
kw = D.pick_keywords(cr, brand, llm=lambda m: json.dumps({"keywords": ["รับผลิตเสื้อยืด", "สกรีนเสื้อ ABC Shirt", "โรงงานผลิตเสื้อ บางนา", "สกรีนเสื้อด่วน", "เสื้อทีม ราคาถูก"]}))
assert len(kw) == 4 and all("ABC" not in k for k in kw)                                          # ตัดคำที่มีชื่อแบรนด์
assert D.pick_keywords(cr, brand, llm=lambda m: "พัง")                                            # โมเดลพัง → คำจาก title/H1
def search(q, p):
    if p == 2:
        return [{"domain": "shopabc.co.th", "url": SITE + "/services/", "position": 4}] if q == "สกรีนเสื้อด่วน" else []
    rows = [{"domain": d, "url": f"https://{d}/x", "position": i + 1} for i, d in enumerate(["youtube.com", "rival.co.th", "www.other.com", "facebook.com", "th.wikipedia.org", "a.com", "b.com", "c.com", "d.com", "e.com"])]
    if q == "รับผลิตเสื้อยืด":
        rows[2] = {"domain": "www.shopabc.co.th", "url": SITE + "/", "position": 3}
    return rows
rk = D.ranking("shopabc.co.th", kw, search=search)
r0 = next(r for r in rk["rows"] if r["keyword"] == "รับผลิตเสื้อยืด"); assert r0["position"] == 3 and r0["top"][2] == "shopabc.co.th"
r1 = next(r for r in rk["rows"] if r["keyword"] == "สกรีนเสื้อด่วน"); assert r1["position"] == 14                 # หน้า 2 → อันดับ 10+4
assert rk["page1"] == 1 and rk["competitors"][0] == {"domain": "rival.co.th", "hits": 4} and all(c["domain"] not in D.PLATFORMS for c in rk["competitors"])
print("ranking: คำค้นไม่มีชื่อแบรนด์ · อันดับหน้า 1/2 · Top 10 · คู่แข่งไม่นับแพลตฟอร์ม OK")

# ---- 20 คำถาม AI ----
pr = D.make_prompts(cr, brand, kw, llm=lambda m: json.dumps({"prompts": [f"คำถาม {i} รับผลิตเสื้อ" for i in range(25)] + ["ABC Shirt ดีไหม"]}))
assert len(pr) == 20 and not any("ABC" in p for p in pr)
assert len(D.make_prompts(cr, brand, kw, llm=lambda m: "x")) == 16                                # แม่แบบ 4 × 4 คำ
def ask(q):
    i = int(re.search(r"\d+", q).group(0)) if re.search(r"\d+", q) else 0
    if i == 3:
        return {"ok": False, "reason": "timeout"}
    cites = [SITE + "/about/"] if i == 1 else (["https://rival.co.th/blog/1"] if i % 2 == 0 else [])
    text = "แนะนำ ABC Shirt และ Rival" if i in (1, 5) else ("rival.co.th ดีที่สุด" if i % 2 == 0 else "ลองหาในกูเกิล")
    return {"ok": True, "text": text, "citations": cites, "cost_usd": 0.02}
res = D.ask_prompts(pr, "shopabc.co.th", brand, ["rival.co.th", "other.com"], ask=ask, engine="chatgpt")
assert res["asked"] == 19 and res["mentioned"] == 2 and res["cited"] == 1 and res["our_urls"][0]["url"] == SITE + "/about/"
assert res["comp_mentions"]["rival.co.th"] >= 10 and res["comp_urls"]["rival.co.th"][0]["url"] == "https://rival.co.th/blog/1" and abs(res["cost_usd"] - 0.38) < 1e-6
assert any("error" in r for r in res["rows"])
print("ai: 20 คำถาม ไม่มีชื่อแบรนด์ · เอ่ยชื่อ/อ้างอิง · คู่แข่ง · URL ที่ถูกอ้าง · ค่าใช้จ่าย · คำถามพังไม่ล้มทั้งรอบ OK")

# ---- ทั้งสาย: /audit → รายงานเต็ม → หน้า 3 แท็บ → ติดต่อ → วัด AI ----
def rep(*fails, ok=10):
    checks = [{"key": k, "label": f"ข้อ {k}", "status": "fail", "detail": ""} for k in fails]
    return {"ok": not checks, "n_fail": len(checks), "n_ok": ok, "checks": checks + [{"key": "x", "label": "ผ่าน", "status": "ok"}] * ok}
db.init_db()
sh.run_checks = lambda site, **kw: rep("csr")
ai._read_setting = lambda k: None
ai._chat = lambda m, max_tokens=2048, timeout=120: (_ for _ in ()).throw(RuntimeError("no model"))
mailer.config = lambda: None
P.RUN_INLINE = True; D.RUN_INLINE = True; L.AUDITS_PER_IP_PER_DAY = 50
D.HOOKS.update(client=mk_client, search=search, ask=ask, engine="chatgpt",
               llm=lambda m: json.dumps({"keywords": kw} if "คำค้น Google" in m[-1]["content"] else {"prompts": pr}))
c = TestClient(app)
r = c.post("/audit", data={"url": "www.shopabc.co.th"}); assert r.status_code == 200
lead = db.list_leads(None, 5)[0]; t1 = D.tier1(lead)
assert t1["state"] == "done" and t1["health"]["score"] == hl["score"] and t1["ranking"]["page1"] == 1 and D.tier2(lead) == {}
assert any(e["kind"] == "deep_audit" for e in db.list_lead_events(lead["id"]))
html = r.text
for s in ("Ranking", "AI Visibility", "Site Audit", "รับผลิตเสื้อยืด", "rival.co.th", "ลิงก์อีเมลไม่มี mailto", f"{hl['score']}<small>/100", "สรุปสำคัญ", "วัดฟรีเมื่อขอแผนแก้", "ส่งแผนแก้ให้ฉัน"):
    assert s in html, s
assert c.get(f"/audit/{lead['token']}/progress").json() == {"tier1": "done", "ai": "none", "step": None}
r = c.post(f"/audit/{lead['token']}/contact", data={"email": "owner@shopabc.co.th", "consent": "1"}); assert r.status_code == 200
lead = db.get_lead(lead["id"]); t2 = D.tier2(lead); assert t2["state"] == "done" and t2["asked"] == 19 and D.ai_today() == 1
assert "20 คำถามที่ลูกค้าถาม AI" in r.text and "อ้างอิง ✓" in r.text and "/about/" in r.text
print("flow: /audit → ชั้น 1 อัตโนมัติ → หน้า 3 แท็บ → ทิ้งช่องทางติดต่อ → วัด AI → แสดงผล OK")

# ---- เพดานต่อวัน + ปุ่มแอดมิน + เปิดหน้าลีดเก่า ----
db.set_setting("deep_ai_daily", "1")
lid2 = db.add_lead("https://www.shopabc.co.th", "shopabc2.co.th", source="prospect"); db.update_lead(lid2, deep_json=json.dumps(t1), audit_json=json.dumps(rep()), token="tok2")
assert D.run_ai(lid2)["state"] == "queued" and "เพดาน" in D.tier2(db.get_lead(lid2))["reason"]
assert "คิวผลวัด AI เต็ม" in c.get("/audit/tok2").text
admin = "admin@test.local"; db.create_tenant(admin, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Op", is_admin=True)
a = TestClient(app); a.post("/login", data={"email": admin, "password": "pw123456"})
r = a.post(f"/admin/leads/{lid2}/deep", data={"ai": "1"}); assert D.tier2(db.get_lead(lid2))["state"] == "done"          # ปุ่มแอดมินไม่นับเพดาน
assert "รายงานเต็ม" in r.text and "ถาม AI 20 คำถาม" in r.text
lid3 = db.add_lead(SITE, "shopabc3.co.th", source="prospect"); db.update_lead(lid3, audit_json=json.dumps(rep()), token="tok3")
assert "Site Audit" in c.get("/audit/tok3").text and D.tier1(db.get_lead(lid3))["state"] == "done"             # ลีดเก่าเปิดหน้าครั้งแรก → ทำรายงานเต็ม
os.environ["GEO_DEEP_AUDIT"] = "0"; assert D.start(lid3) == "disabled"
print("cap/admin: เพดานต่อวัน → คิว · ปุ่มแอดมินข้ามเพดาน · ลีดเก่าเปิดหน้าแล้วทำให้ · สวิตช์ปิด OK")
import app.deep_audit as _DA
pp = _DA.parse_page('<img src="/x.png"><img src="/x.png"><img src="/y.png" alt=""><img src="data:image/png;base64,AA"><img data-src="/z.png">', "https://s.co.th/", "s.co.th")
assert pp["no_alt"] == 2 and pp["no_alt_src"] == ["/x.png", "/z.png"]          # alt="" = รูปตกแต่ง ถูกต้อง · data: ไม่นับ · ซ้ำนับครั้งเดียว
old = {"issues": [{"key": "alt_missing", "severity": "Warning", "label": "รูปไม่มี alt text", "urls": ["a", "b"], "count": 19126, "fix": "ใส่ alt"},
                  {"key": "desc_missing", "severity": "Warning", "label": "x", "urls": ["a"], "count": 4, "fix": "x"}], "health": {"warnings": 19130}}
nd = _DA._normalize(old); assert nd["issues"][0]["count"] == 2 and nd["health"]["warnings"] == 6     # รายงานเก่าแสดงถูกโดยไม่ต้องไต่ใหม่
print("alt: นับรูปไม่ซ้ำ · alt ว่าง = รูปตกแต่ง · รายงานเก่าแก้ตัวเลขตอนแสดง OK")
print("ALL OK")
