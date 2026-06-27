"""ทดสอบฟีเจอร์ admin god-view + ปิดสมัครเอง (ใช้ DB แยก ตั้งผ่าน GEO_DB_PATH)."""
import re
import bcrypt
from fastapi.testclient import TestClient
import app.db as db
from app.main import app

db.init_db()

# สร้าง admin ตรงๆ
admin_email = "admin@geo.local"
if not db.get_tenant_by_email(admin_email):
    db.create_tenant(admin_email, bcrypt.hashpw(b"adminpass", bcrypt.gensalt()).decode(), "Operator", is_admin=True)

c = TestClient(app)

r = c.post("/login", data={"email": admin_email, "password": "adminpass"})
print("admin login ->", r.status_code, "| final:", str(r.url))
assert str(r.url).rstrip("/").endswith("/admin"), str(r.url)

r = c.get("/admin")
print("GET /admin ->", r.status_code, "| create form:", "สร้างบัญชีลูกค้าใหม่" in r.text)
assert r.status_code == 200

r = c.post("/admin/tenants", data={"email": "client@jkp.com", "password": "clientpass", "name": "JKP"})
print("admin creates customer ->", r.status_code, "| listed:", "client@jkp.com" in r.text)
assert "client@jkp.com" in r.text

# register ปิด
r = c.get("/register")
print("GET /register -> final:", str(r.url), "(ควรเป็น /login)")
assert str(r.url).rstrip("/").endswith("/login")
before = len(db.list_all_tenants())
c.post("/register", data={"email": "sneaky@x.com", "password": "x", "name": "x"})
after = len(db.list_all_tenants())
print("POST /register blocked (no new tenant):", before == after, "| tenants:", after)
assert before == after

# ลูกค้า login -> ไม่ใช่ admin, เข้าหน้า /
c2 = TestClient(app)
r = c2.post("/login", data={"email": "client@jkp.com", "password": "clientpass"})
print("client login -> final:", str(r.url), "(ควรเป็น /)")
assert str(r.url).rstrip("/").endswith("8099") or str(r.url).endswith("/")
r = c2.get("/admin")
print("client GET /admin -> final:", str(r.url), "(ต้องไม่ใช่ /admin)")
assert not str(r.url).rstrip("/").endswith("/admin")

# ลูกค้าสร้างแบรนด์
r = c2.post("/brands", data={"name": "JKP Property", "domain": "jkppropertyagency.com", "market": "warehouse"})
bid = re.search(r"/brands/(\d+)", str(r.url)).group(1)
print("client created brand id:", bid)

# admin เปิดแบรนด์ลูกค้าได้ (god view) — ต้องได้หน้าแบรนด์จริง ไม่ใช่ redirect
r = c.get(f"/brands/{bid}", follow_redirects=False)
print("admin opens client's brand ->", r.status_code, "(ควร 200 ไม่ redirect)")
assert r.status_code == 200 and "รันมอนิเตอร์ตอนนี้" in r.text

# ผู้ใช้อื่น (ไม่ใช่เจ้าของ/ไม่ใช่ admin) ต้องโดนปฏิเสธ → redirect 303 ไป /
c3 = TestClient(app)
db.create_tenant("other@x.com", bcrypt.hashpw(b"otherpass", bcrypt.gensalt()).decode(), "Other")
c3.post("/login", data={"email": "other@x.com", "password": "otherpass"})
r = c3.get(f"/brands/{bid}", follow_redirects=False)
print("other user opens client's brand ->", r.status_code, "->", r.headers.get("location"), "(ควร 303 -> /)")
assert r.status_code == 303 and r.headers.get("location") == "/"

print("ALL ADMIN TESTS OK")
