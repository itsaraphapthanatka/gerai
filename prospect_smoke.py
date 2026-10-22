"""ทดสอบหาลูกค้าเอง: กรองโดเมน · หาอีเมลบนเว็บ · รอบค้น→เพิ่ม→ตรวจ→อีเมล→CEO เสนอ · ข้ามรายที่รู้จักแล้ว · ปิดสวิตช์ · ไม่มีอีเมลไม่เสนอ
· ตั้งค่า/หาตอนนี้/แก้ช่องทางติดต่อผ่านหน้าแอดมิน · ไม่ยิงเครือข่าย"""
import os, tempfile, json
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke3-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.prospecting as PR, app.pipeline as P, app.site_health as sh, app.ai_client as ai, app.mailer as mailer, app.geo_worker as gw
from app.main import app

def rep(*fails, ok=10):
    checks = [{"key": k, "label": f"ข้อ {k}", "status": "fail", "detail": ""} for k in fails]
    return {"ok": not checks, "n_fail": len(checks), "n_ok": ok, "checks": checks + [{"key": "x", "label": "ผ่าน", "status": "ok"}] * ok}
db.init_db()
sh.run_checks = lambda site, **kw: rep("csr", "indexed")
ai._read_setting = lambda k: None
ai._chat = lambda m, max_tokens=2048, timeout=120: (_ for _ in ()).throw(RuntimeError("no model"))
mailer.config = lambda: {"host": "x"}; mailer.send = lambda *a, **k: None
P.RUN_INLINE = True

# ---- กรองโดเมน ----
assert PR.apex("sub.shop.co.th") == "shop.co.th" and PR.apex("a.b.com") == "b.com" and PR.apex("m.facebook.com") == "facebook.com"
assert PR.is_business_site("example.co.th") and PR.is_business_site("lawfirm.com")
for bad in ("facebook.com", "www.pantip.com", "m.facebook.com", "dmsc.go.th", "chula.ac.th", "shopee.co.th", "ddproperty.com", "1.2.3.4", "redcross.or.th"):
    assert not PR.is_business_site(bad), bad
qs = PR.next_queries(3); assert len(qs) == 3 and all("กรุงเทพ" in q for q in qs) and len(set(qs)) == 3
qs2 = PR.next_queries(3); assert qs2 != qs                                  # หมุนต่อ ไม่ค้นซ้ำ
print("filter: โดเมนธุรกิจ/ไม่ใช่ธุรกิจ · หมุนคำค้น OK")

# ---- หาอีเมล ----
pages = {"https://lawfirm.co.th": '<html><a href="/contact">ติดต่อเรา</a> <img src="logo@2x.png"> noreply@lawfirm.co.th</html>',
         "https://lawfirm.co.th/contact": '<a href="mailto:Info@LawFirm.co.th">อีเมล</a> sales@lawfirm.co.th someone@gmail.com <a href="https://lin.ee/abc123">LINE</a>'}
c = PR.find_contact("https://lawfirm.co.th", fetch=lambda u: pages.get(u.rstrip("/"), ""))
assert c["email"] == "info@lawfirm.co.th" and c["line"] == "https://lin.ee/abc123" and c["n"] == 3, c
c2 = PR.find_contact("https://nothing.co.th", fetch=lambda u: "<html>hi</html>"); assert c2["email"] == "" and c2["n"] == 0
c3 = PR.find_contact("https://gmailonly.co.th", fetch=lambda u: "ติดต่อ shopowner@gmail.com"); assert c3["email"] == "shopowner@gmail.com"   # อีเมลธุรกิจที่เปิดเผย แม้เป็น gmail
print("contact: mailto/regex · หน้าติดต่อ · ชอบ info@ บนโดเมนเดียวกัน · ข้าม noreply/รูป OK")

# ---- รอบค้นหา ----
results = [{"url": "https://www.facebook.com/x", "title": "fb"}, {"url": "https://lawfirm.co.th/", "title": "สำนักงานกฎหมาย เอ"},
           {"url": "https://pantip.com/topic/1", "title": "p"}, {"url": "https://shop.co.th", "title": "ร้าน"}, {"url": "not a url", "title": ""}]
db.add_lead("https://shop.co.th", "shop.co.th", source="import")           # รู้จักแล้ว → ข้าม
contact = lambda site: {"email": "info@lawfirm.co.th", "line": "", "n": 1} if "lawfirm" in site else {"email": "", "line": "", "n": 0}
res = PR.run(limit=5, search=lambda q, n: results, contact=contact)
assert res["found"] == 1 and res["added"] == 1 and res["audited"] == 1 and res["with_email"] == 1 and res["proposed"] == 1 and res["leads"] == ["lawfirm.co.th"], res
l = db.find_lead_by_domain("lawfirm.co.th")
assert l["source"] == "prospect" and l["email"] == "info@lawfirm.co.th" and l["audit_json"] and "พบจากคำค้น" in l["notes"]
assert [d["kind"] for d in db.list_decisions("proposed")] == ["outreach"]
assert {"prospect", "audit", "contact_found"} <= {e["kind"] for e in db.list_lead_events(l["id"])}
assert PR.last_run()["added"] == 1 and "หาลูกค้าเอง" in PR.status_text() and "เสนอทัก 1" in PR.status_text()
res = PR.run(limit=5, search=lambda q, n: results, contact=contact); assert res["found"] == 0 and res["added"] == 0   # รอบถัดไปไม่เพิ่มซ้ำ
db.set_setting("prospect_enabled", "0"); assert PR.run(limit=1, search=lambda q, n: results)["skipped"]; db.set_setting("prospect_enabled", "1")
res = PR.run(limit=5, search=lambda q, n: [{"url": "https://noemail.co.th", "title": "x"}], contact=lambda s: {"email": "", "line": "", "n": 0})
l2 = db.find_lead_by_domain("noemail.co.th")
assert res["added"] == 1 and res["proposed"] == 0 and "no_contact" in {e["kind"] for e in db.list_lead_events(l2["id"])} and len(db.list_decisions("proposed")) == 1
sh.run_checks = lambda site, **kw: (_ for _ in ()).throw(RuntimeError("down"))
res = PR.run(limit=5, search=lambda q, n: [{"url": "https://down.co.th", "title": "x"}], contact=contact); assert res["errors"] == 1 and res["audited"] == 0
sh.run_checks = lambda site, **kw: rep("csr", "indexed")
print("run: กรอง → เพิ่ม → ตรวจ → อีเมล → CEO เสนอ · ข้ามรายเดิม · ปิดสวิตช์ · ไม่มีอีเมลไม่เสนอ · เว็บล่มไม่ล้มทั้งรอบ OK")

# ---- หน้าแอดมิน ----
admin = "admin@test.local"
db.create_tenant(admin, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Op", is_admin=True)
a = TestClient(app); a.post("/login", data={"email": admin, "password": "pw123456"})
r = a.get("/admin/settings"); assert "หาลูกค้าอัตโนมัติ" in r.text and "down.co.th" in r.text and "ล้ม 1" in r.text   # รอบล่าสุดโชว์บนการ์ด
a.post("/admin/settings/prospect", data={"segments": "คลินิกทันตกรรม\n\nสำนักงานบัญชี ", "daily": "7", "enabled": "1"})
assert PR.segments() == ["คลินิกทันตกรรม", "สำนักงานบัญชี"] and PR.daily_limit() == 7 and PR.enabled()
gw.search = lambda q, n=10: [{"url": "https://third.co.th", "title": "บริษัท ซี"}]
PR.find_contact = lambda site, fetch=None: {"email": "hello@third.co.th", "line": "", "n": 1}
r = a.post("/admin/prospect/run", data={"limit": "1"}); assert "เริ่มหาลูกค้าแล้ว" in r.text
l3 = db.find_lead_by_domain("third.co.th"); assert l3 and l3["email"] == "hello@third.co.th" and len(db.list_decisions("proposed")) == 2
r = a.get("/admin/leads"); assert "หาเอง" in r.text and "third.co.th" in r.text
r = a.get(f"/admin/leads/{l2['id']}"); assert "ไม่พบอีเมลบนเว็บ" in r.text and "แก้ช่องทางติดต่อ" in r.text
assert "รูปแบบอีเมลไม่ถูกต้อง" in a.post(f"/admin/leads/{l2['id']}/contact", data={"email": "not-an-email"}).text
a.post(f"/admin/leads/{l2['id']}/contact", data={"email": "Owner@NoEmail.co.th", "name": "คุณบี", "contact": "@noemail"})
l2 = db.get_lead(l2["id"]); assert l2["email"] == "owner@noemail.co.th" and l2["name"] == "คุณบี" and l2["contact"] == "@noemail"
a.post(f"/admin/leads/{l2['id']}/propose"); assert len(db.list_decisions("proposed")) == 3
print("admin: การ์ดตั้งค่า · บันทึกกลุ่ม/จำนวน · หาตอนนี้ · ป้ายหาเอง · แก้ช่องทางติดต่อแล้วให้ CEO เสนอ OK")
print("\nprospect_smoke: ผ่านทั้งหมด")
