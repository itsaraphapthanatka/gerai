"""ลีดที่โดเมนเป็นของลูกค้าเดิม: ไม่ถูกคัด/เสนอขาย · หน้าลีดขึ้น "ลูกค้าแล้ว" แทนคะแนนโอกาส · แจ้งเจ้าของว่าเป็นลูกค้าเดิม
· แบรนด์ของเจอ.AI เองไม่นับเป็นลูกค้า · ลีดทั่วไปยังทำงานเหมือนเดิม"""
import os, tempfile, json
os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-cl-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.leads as L, app.pipeline as P, app.main as M

db.init_db()
P.RUN_INLINE = True
alerts = []
P.alert_admins = lambda msg, link, type_="info", subject=None: alerts.append(msg)
owner = db.create_tenant("cust@shop.example", bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(), "Shop Owner")
db.set_plan(owner, "pro")
bid = db.create_brand(owner, "Shop Co", "https://www.shop.example", "retail")
trial = db.create_tenant("trial@deal.example", bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(), "Deal")   # เปิดบัญชีก่อนจ่าย = ยังเป็นลีด
db.create_brand(trial, "Deal Co", "deal.example", "retail")
ad = db.create_tenant("admin@x.example", bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode(), "Admin", is_admin=True)
db.create_brand(ad, "Our Brand", "ours.example", "saas")

REP = {"checks": [{"key": "home", "status": "ok"}, {"key": "csr", "status": "fail", "label": "JS"}]}
cust = db.add_lead("https://shop.example", "shop.example", source="audit")
db.update_lead(cust, audit_json=json.dumps(REP), token="tokcust")
other = db.add_lead("https://other.example", "other.example", source="audit")
db.update_lead(other, audit_json=json.dumps(REP), token="tokother")
mine = db.add_lead("https://ours.example", "ours.example", source="audit")

assert L.customer_brand("shop.example")["id"] == bid and L.customer_brand("https://www.shop.example/x")["id"] == bid
assert L.customer_brand("other.example") is None and L.customer_brand("ours.example") is None and L.customer_brand("") is None
assert L.customer_brand("deal.example") is None                          # แผนฟรี (ยังไม่จ่าย) ไม่ใช่ลูกค้า — ยังขายต่อได้
assert P.ceo_propose(db.get_lead(cust)) is None and not db.count_decisions_for_lead(cust, "proposed")
import app.followup as FU
man = db.add_lead("https://www.shop.example", "www.shop.example", source="manual")   # เจ้าของสั่งทักเองแล้ว (สถานะทักแล้ว)
db.update_lead(man, status="contacted", email="info@shop.example")
assert "ลูกค้าที่จ่ายเงินแล้ว" in FU.eligible(db.get_lead(man)) and FU.next_step(db.get_lead(man)) is None
print("customer_brand: ตรงโดเมน (มี/ไม่มี www) · ไม่นับแบรนด์ของเราเอง/บัญชีแผนฟรี · ลูกค้าเดิมไม่ถูกเสนอขาย OK")

c = TestClient(M.app)
c.post("/login", data={"email": "admin@x.example", "password": "pw"})
r = c.get("/admin/leads"); assert r.status_code == 200
row_of = lambda dom: r.text[r.text.index(dom):].split("</tr>")[0]
row = row_of("shop.example")
assert "ลูกค้าแล้ว" in row and f'/brands/{bid}' in row
orow = row_of("other.example"); assert "ลูกค้าแล้ว" not in orow
r = c.get(f"/admin/leads/{cust}"); assert "ลูกค้าแล้ว · Shop Co" in r.text
print("หน้าลีด: ลูกค้าเดิมขึ้น 'ลูกค้าแล้ว' (ลิงก์แบรนด์) · ลีดอื่นยังเป็นคะแนนโอกาส OK")

c2 = TestClient(M.app)
c2.post("/audit/tokcust/contact", data={"name": "Ann", "email": "ann@shop.example", "consent": "1"})
assert any("ลูกค้าเดิม Shop Co" in a for a in alerts), alerts
c2.post("/audit/tokother/contact", data={"name": "Bob", "email": "bob@other.example", "consent": "1"})
assert any("ลูกค้าเป้าหมายใหม่จากตรวจเว็บฟรี: other.example" in a for a in alerts), alerts
print("แจ้งเจ้าของ: ลูกค้าเดิมทิ้งช่องทาง = แจ้งว่าเป็นลูกค้าเดิม · ลีดใหม่แจ้งแบบเดิม OK")
print("ALL OK")
