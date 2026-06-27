"""ทดสอบ billing: แผน + นับ usage + บังคับโควตา + admin เปลี่ยนแผน."""
import os
os.environ["LITELLM_BASE_URL"] = ""
import re
import bcrypt
from fastapi.testclient import TestClient
import app.db as db
import app.billing as billing
from app.main import app

db.init_db()
c = TestClient(app)

email = "biz@test.com"
if not db.get_tenant_by_email(email):
    db.create_tenant(email, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Biz")  # free, ไม่ใช่ admin
c.post("/login", data={"email": email, "password": "pw123456"})
t = db.get_tenant_by_email(email)
assert billing.plan_key(t) == "free"
print("tenant on free plan")

# แบรนด์แรก: ผ่าน (free brands=1)
r = c.post("/brands", data={"name": "B1", "domain": "b1.com", "market": "m"})
assert "/brands/" in str(r.url), str(r.url)
# แบรนด์ที่สอง: ต้องโดนบล็อก
r = c.post("/brands", data={"name": "B2", "domain": "b2.com", "market": "m"})
assert "เกินโควตา" in r.text, "free ต้องบล็อกแบรนด์ที่ 2"
print("free plan blocks 2nd brand OK")

u = billing.usage(t["id"])
assert u["brands"] == 1, u
print("usage:", u)

# admin เปลี่ยนแผนเป็น pro
admin = "adm@test.com"
if not db.get_tenant_by_email(admin):
    db.create_tenant(admin, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Adm", is_admin=True)
ca = TestClient(app)
ca.post("/login", data={"email": admin, "password": "pw123456"})
ca.post(f"/admin/tenants/{t['id']}/plan", data={"plan": "pro"})
assert db.get_tenant(t["id"])["plan"] == "pro"
print("admin set plan -> pro OK")

# คราวนี้แบรนด์ที่สองผ่าน
r = c.post("/brands", data={"name": "B2", "domain": "b2.com", "market": "m"})
assert "/brands/" in str(r.url), "pro ต้องสร้างแบรนด์ที่ 2 ได้"
print("pro plan allows 2nd brand OK")

# dashboard แสดงแผน + usage
rd = c.get("/")
assert "แผน Pro" in rd.text and "แบรนด์ 2/10" in rd.text
print("dashboard shows plan + usage OK")
print("ALL BILLING TESTS OK")
