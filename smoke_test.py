"""End-to-end smoke test ผ่าน TestClient (รันในโปรเซส, ยิง ddgs จริงตอน /run)."""
import re
import bcrypt
from fastapi.testclient import TestClient
from app.main import app
import app.db as db

db.init_db()
c = TestClient(app)

r = c.get("/login")
print("GET /login ->", r.status_code)
assert r.status_code == 200

# ระบบปิดรับสมัครเอง — สร้าง tenant ตรง ๆ แล้วล็อกอิน
email = "demo@test.com"
if not db.get_tenant_by_email(email):
    db.create_tenant(email, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Demo", is_admin=True)
r = c.post("/login", data={"email": email, "password": "pw123456"})
print("POST /login ->", r.status_code, "(final:", str(r.url) + ")")
assert r.status_code == 200

r = c.post("/seed-demo")
brand_url = str(r.url)
print("POST /seed-demo ->", r.status_code, "| ->", brand_url)
assert r.status_code == 200

r = c.get(brand_url)
print("GET brand ->", r.status_code, "| seeded Q:", "โกดังให้เช่า สมุทรปราการ" in r.text)

bid = re.search(r"/brands/(\d+)", brand_url).group(1)
print(f"running monitor for brand {bid} (ddgs network)...")
r = c.post(f"/brands/{bid}/run")
print("POST run ->", r.status_code, "| ->", str(r.url))
assert r.status_code == 200
m = re.search(r'class="sov[^"]*"[^>]*>\s*(\d+/\d+)', r.text)
print("report SoV:", m.group(1) if m else "?", "| has competitors table:", "ครองพื้นที่บ่อยสุด" in r.text)
print("ALL OK")
