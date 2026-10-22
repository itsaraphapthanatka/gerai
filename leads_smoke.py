"""ทดสอบเฟส 1: ตรวจเว็บฟรี → ลูกค้าเป้าหมาย → CEO เสนอ (โหมดเสนอ) → แอดมินอนุมัติ/ปฏิเสธ → สรุปเช้า
ไม่ยิงเครือข่าย: ตัวตรวจสุขภาพเว็บ + โมเดล + อีเมล ถูกแทนด้วยของปลอม · DB ชั่วคราว"""
import os, tempfile, json, re
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
import bcrypt
from fastapi.testclient import TestClient
import app.db as db
import app.leads as L
import app.execs as X
import app.site_health as sh
import app.mailer as mailer
import app.ai_client as ai
from app.main import app

# ---- ตรรกะล้วน ----
assert L.normalize_url(" Example.CO.TH/path?x=1 ") == "https://example.co.th"
assert L.normalize_url("http://www.shop.com:8080/a") == "http://www.shop.com:8080"
assert L.normalize_url("ไม่ใช่เว็บ") is None and L.normalize_url("") is None and L.normalize_url("no-dot") is None
assert L.domain_of("https://www.example.co.th") == "example.co.th"
def rep(*fails, ok=10, home_fail=False):
    checks = [{"key": k, "label": f"ข้อ {k}", "status": "fail", "detail": "รายละเอียด"} for k in fails]
    if home_fail:
        checks.append({"key": "home", "label": "เข้าหน้าแรก", "status": "fail", "detail": "timeout"})
    return {"ok": not checks, "n_fail": len(checks), "n_ok": ok, "checks": checks + [{"key": "x", "label": "ผ่าน", "status": "ok"}] * ok}
sc = L.score_audit(rep("csr", "indexed", "robots_sitemap"))
assert sc["score"] == 56 and sc["level"] == "สูง" and sc["n_fail"] == 3 and sc["reasons"][0].startswith("หน้าเว็บสร้างด้วย JavaScript")
assert L.score_audit(rep(home_fail=True))["score"] == 0
assert L.score_audit(rep("www"))["level"] == "ต่ำ" and L.score_audit(rep())["score"] == 0
assert L.score_audit(rep("freshness", "wp"))["n_fail"] == 0 and L.public_summary(rep("freshness", "csr"))[0]["key"] == "csr"   # ข้อของแพลตฟอร์มไม่นับกับเว็บคนนอก
assert [s["key"] for s in L.public_summary(rep("www", "csr", "sitemap", "orphan"))] == ["csr", "sitemap", "orphan"]
rows = L.parse_import("domain, email, name\nexample.co.th, Sales@Example.co.th, คุณสมชาย\nwww.example.co.th\nbad\tx\nshop.com;info@shop.com")
assert [r["domain"] for r in rows] == ["example.co.th", "shop.com"] and rows[0]["email"] == "sales@example.co.th" and rows[0]["name"] == "คุณสมชาย"
print("leads: normalize/score/summary/import OK")

# ---- ผู้บริหารเอเจนต์ ----
lead = {"domain": "example.co.th", "site_url": "https://example.co.th"}
good = lambda msgs: json.dumps({"subject": "เว็บ example.co.th ยังไม่โผล่ใน AI", "body": "สวัสดีครับ " + "ผมตรวจเว็บของคุณแล้วพบว่า " * 6 + "คุยฟรี 15 นาทีได้ครับ"})
pl = X.ceo_review_lead(lead, rep("csr", "indexed"), "https://geo.appreview.cloud/audit/t1", llm=good)
assert pl["action"] == "accept" and pl["package"] == "pro" and pl["price"] == 9900 and pl["source"] == "llm" and "https://geo.appreview.cloud/audit/t1" in pl["body"]
bad = lambda msgs: json.dumps({"subject": "การันตีอันดับ 1", "body": "รับประกันผล " * 20})
pl2 = X.ceo_review_lead(lead, rep("csr"), "https://x/audit/t", llm=bad)
assert pl2["source"].startswith("template") and "การันตี" not in pl2["body"]                       # คำต้องห้าม → แม่แบบ
assert X.ceo_review_lead(lead, rep(home_fail=True), "u")["action"] == "decline"
assert X.ceo_review_lead(lead, rep("www"), "u")["action"] == "decline"                            # โอกาสต่ำ ไม่ทัก
cfo = X.cfo_check({"price": 9900, "discount_pct": 0}); assert cfo["ok"] and cfo["net"] == 9900 and cfo["margin_pct"] == 94
assert not X.cfo_check({"price": 9900, "discount_pct": 15})["ok"]                                 # เกินกรอบส่วนลด
assert not X.cfo_check({"price": 60000, "discount_pct": 0})["ok"]                                 # เกินวงเงิน
print("execs: CEO รับ/ไม่รับ · กรองคำโฆษณา · CFO กรอบส่วนลด/วงเงิน OK")

# ---- เส้นทางจริงผ่าน TestClient (DB สด) ----
db.init_db()
sh.run_checks = lambda site, **kw: rep("csr", "indexed", "orphan")      # ไม่ดึงเว็บจริง
ai._read_setting = lambda k: None                                         # โมเดลในเครื่อง (ไม่มี) → แม่แบบ
ai._chat = lambda m, max_tokens=2048, timeout=120: (_ for _ in ()).throw(RuntimeError("no model"))
sent = []
mailer.config = lambda: {"host": "x"}
mailer.send = lambda to, subject, html, text="", attachments=(): sent.append((to, subject))
c = TestClient(app)
r = c.post("/audit", data={"url": "example.co.th"})
assert "/audit/" in str(r.url) and r.status_code == 200 and "ผลตรวจ example.co.th" in r.text and "3" in r.text, r.url
token = str(r.url).rsplit("/", 1)[-1]
assert c.post("/audit", data={"url": "www.example.co.th"}).url.path.endswith(token)              # โดเมนเดิมใน 24 ชม. ใช้ผลเดิม
assert c.post("/audit", data={"url": "x", "website": "bot"}).url.path == "/audit"                # honeypot
assert "กรอกที่อยู่เว็บให้ถูกต้อง" in c.post("/audit", data={"url": "ไม่ใช่เว็บ"}).text
r = c.post(f"/audit/{token}/contact", data={"name": "สมชาย", "email": "owner@example.co.th", "consent": ""})
assert "ติ๊กยินยอม" in r.text
r = c.post(f"/audit/{token}/contact", data={"name": "สมชาย", "email": "owner@example.co.th", "consent": "1"})
assert "รับเรื่องแล้ว" in r.text
lead_row = db.get_lead_by_token(token)
assert lead_row["email"] == "owner@example.co.th" and lead_row["consent"] == 1 and lead_row["score"] == 58
pend = db.list_decisions("proposed")
assert len(pend) == 1 and pend[0]["kind"] == "outreach" and pend[0]["lead_id"] == lead_row["id"]
print("public: ตรวจ → ผล → ใช้ผลเดิมซ้ำ → honeypot → ยินยอม → CEO เสนอข้อความทัก OK")

admin = "admin@test.local"
db.create_tenant(admin, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Op", is_admin=True)
a = TestClient(app); a.post("/login", data={"email": admin, "password": "pw123456"})
assert "example.co.th" in a.get("/admin/leads").text and "รอยืนยัน 1" in a.get("/admin/leads").text
r = a.get("/admin/decisions"); assert "ทัก example.co.th (pro)" in r.text and "CFO:" in r.text
did = pend[0]["id"]
r = a.post(f"/admin/decisions/{did}/approve")
assert "ส่งอีเมลถึง owner@example.co.th" in r.text and sent and sent[0][0] == "owner@example.co.th"
assert db.get_lead(lead_row["id"])["status"] == "contacted" and db.get_decision(did)["status"] == "executed"
st = db.decision_stats(); assert st["decided"] == 1 and st["agreement_pct"] == 100
# นำเข้า + ตรวจ + CEO ไม่รับ (เว็บผ่านเกือบทุกข้อ) → ปฏิเสธข้อเสนอ → ตัวเลขตรงกันลด
a.post("/admin/leads/import", data={"text": "clean.co.th, hello@clean.co.th\nexample.co.th"})
lead2 = db.find_lead_by_domain("clean.co.th"); assert lead2 and lead2["source"] == "import" and len(db.list_leads()) == 2
sh.run_checks = lambda site, **kw: rep("www")
a.post(f"/admin/leads/{lead2['id']}/audit"); lead2 = db.get_lead(lead2["id"]); assert lead2["audit_json"] and lead2["token"]
a.post(f"/admin/leads/{lead2['id']}/propose")
d2 = db.list_decisions("proposed")[0]; assert d2["kind"] == "lead_review" and d2["title"].startswith("ไม่รับ clean.co.th")
a.post(f"/admin/decisions/{d2['id']}/reject", data={"reason": "อยากลองทักดู"})
st = db.decision_stats(); assert st["decided"] == 2 and st["agreement_pct"] == 50 and db.get_decision(d2["id"])["result"] == "อยากลองทักดู"
a.post(f"/admin/leads/{lead2['id']}/status", data={"status": "won"}); assert db.get_lead(lead2["id"])["status"] == "won"
print("admin: รายชื่อ · นำเข้า · ตรวจ · CEO เสนอ/ไม่รับ · อนุมัติส่งอีเมล · ปฏิเสธ · สถิติตรงกัน OK")

# ---- สรุปเช้า: เก็บไว้เสมอ ส่งอีเมลเฉพาะแอดมินที่อีเมลใช้ได้จริง ----
sent.clear()
res = X.morning_digest("https://geo.appreview.cloud")
assert "สรุปเช้า" in res["text"] and "ลูกค้าเป้าหมาย: ใหม่ 24 ชม. 2" in res["text"] and res["sent_to"] == []   # admin@test.local = ที่อยู่สมมติ
assert db.list_decisions("auto")[0]["kind"] == "digest"
assert "ทำสรุปเช้าแล้ว" in a.post("/admin/digest/run").text
print("digest: เก็บเป็นการตัดสินใจ auto · ไม่ส่งไปที่อยู่สมมติ OK")

# ---- หน้า GEO ของแพลตฟอร์มเอง ----
assert c.get("/geo").status_code == 404 and c.get("/llms.txt").status_code == 404
r = a.post("/seed-demo"); bid = int(re.search(r"/brands/(\d+)", str(r.url)).group(1))
db.set_setting("self_brand_id", str(bid))
assert c.get("/geo").status_code == 200 and c.get("/llms.txt").status_code == 200 and c.get("/geo-sitemap.xml").status_code == 200
print("self-brand: /geo · /llms.txt · /geo-sitemap.xml บนโดเมนเอง OK")
print("\nleads_smoke: ผ่านทั้งหมด")
