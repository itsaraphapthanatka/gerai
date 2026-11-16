"""ทดสอบ stage 19: ข้อมูล UAT สร้างได้ แยกขาด และทุกบทบาทเริ่มจากจุดที่เอกสาร UAT บอกได้จริง
· ฝ่ายขาย/การตลาดเปิดหน้าการเงินไม่ได้ · ฝ่ายบัญชีเปิดได้ · ลูกค้าเห็นพื้นที่งานของตัวเอง · พันธมิตรเห็นแค่สถานะ · ผู้เยี่ยมชมเปิดรายงานได้
· ไม่มี route สำหรับเทสต์/fixture เปิดสู่สาธารณะ"""
import os, re, json, sys, tempfile, subprocess
from pathlib import Path
ROOT = Path(__file__).resolve().parent
d = Path(tempfile.mkdtemp(prefix="jorai-uat-")) / "uat.db"
env = {**os.environ, "PYTHONUTF8": "1"}
env.pop("DATABASE_URL", None)
r = subprocess.run([sys.executable, str(ROOT / "scripts" / "uat_seed.py"), "--db", str(d)], capture_output=True, text=True, env=env, timeout=600)
assert r.returncode == 0, r.stderr[-2000:]
logins = json.loads((d.parent / "uat_logins.txt").read_text(encoding="utf-8"))
assert set(logins) >= {"owner", "sales", "marketing", "finance", "delivery", "buyer", "partner", "visitor"}

os.environ.pop("DATABASE_URL", None)
os.environ.update({"GEO_DB_PATH": str(d), "GEO_AUTORUN": "0", "GEO_DEEP_AUDIT": "0", "AI_BASE_URL": "http://127.0.0.1:9", "LITELLM_BASE_URL": ""})
from fastapi.testclient import TestClient
from fastapi.routing import APIRoute
from app.main import app


def login(role):
    c = TestClient(app)
    assert c.post("/login", data={"email": logins[role]["email"], "password": logins[role]["password"]}).status_code == 200, role
    return c


START = {"owner": ["/admin", "/admin/tasks", "/admin/jobs", "/admin/delivery", "/admin/revenue", "/admin/today"],
         "sales": ["/admin/today", "/admin/deals"], "marketing": ["/admin/analytics"], "finance": ["/admin/revenue", "/admin/accounting"],
         "delivery": ["/admin/delivery"], "buyer": ["/app", "/app/delivery"]}
for role, paths in START.items():
    c = login(role)
    for p in paths:
        x = c.get(p)
        assert x.status_code == 200, (role, p, x.status_code)
for role in ("sales", "marketing"):
    x = login(role).get("/admin/revenue", follow_redirects=False)
    assert x.status_code in (302, 303, 403) or "รายได้ · ที่มา · ต้นทุน" not in x.text, role          # ไม่ใช่ฝ่ายบัญชี = ไม่เห็นเงิน
buyer = login("buyer")
assert "รอคุณอยู่" in buyer.get("/app/delivery").text
anon = TestClient(app)
assert anon.get(logins["visitor"]["url"]).status_code == 200
pg = anon.get(logins["partner"]["url"]).text
assert "uat-buyer.example-uat.test" in pg and "บริการหลัก" not in pg                                  # พันธมิตรเห็นสถานะ ไม่เห็นข้อมูลธุรกิจ
paths = [r.path for r in app.routes if isinstance(r, APIRoute)]
assert not [p for p in paths if re.search(r"(fixture|/debug|/__|/test/|uat)", p)]                       # ไม่มี route ทดสอบ/fixture
print("uat_fixture_smoke OK")
