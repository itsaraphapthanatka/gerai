"""ทดสอบทดลองใช้ฟรี 14 วัน: นับจากวันสร้างบัญชี · หมดแล้วหยุดงานที่มีต้นทุน (แบรนด์/มอนิเตอร์/คอนเทนต์) · แอดมิน/แผนเสียเงินไม่โดน
· ผู้ดูแลต่อเวลาได้ · รอบอัตโนมัติข้ามบัญชีที่หมดทดลอง · แบนเนอร์ในแอป · หน้าแรกบอก 14 วัน"""
import os, tempfile, datetime as dt
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke-trial-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.billing as B
from app.main import app
import run_monitors as RM
db.init_db()
pw = bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode()
new = db.create_tenant("new@test.local", pw, "New")
old = db.create_tenant("old@test.local", pw, "Old")
paid = db.create_tenant("paid@test.local", pw, "Paid"); db.set_plan(paid, "pro")
adm = db.create_tenant("admin@test.local", pw, "Admin", is_admin=True)
past = (dt.datetime.now() - dt.timedelta(days=15)).isoformat(timespec="seconds")
with db.get_conn() as c:
    for t in (old, paid, adm):
        c.execute(db.q("UPDATE tenants SET created_at=? WHERE id=?"), (past, t))
T = db.get_tenant
tr = B.trial(T(new)); assert tr["on"] and not tr["expired"] and tr["days_left"] == 14, tr
assert B.check(T(new), "runs")[0] and B.can_run(T(new))
tr = B.trial(T(old)); assert tr["on"] and tr["expired"] and tr["days_left"] == 0
ok, msg = B.check(T(old), "content"); assert not ok and "ทดลองใช้ฟรี 14 วันหมดแล้ว" in msg and "ข้อมูลและคอนเทนต์เดิมยังอยู่ครบ" in msg
assert not B.check(T(old), "brands")[0] and not B.can_run(T(old))
assert not B.trial(T(paid))["on"] and B.check(T(paid), "runs")[0] and not B.trial(T(adm))["on"] and B.check(T(adm), "runs")[0]
assert B.trial(T(new), dt.datetime.now() + dt.timedelta(days=13, hours=23))["days_left"] == 1 and B.trial(T(new), dt.datetime.now() + dt.timedelta(days=14, minutes=1))["expired"]
print("trial: 14 วันนับจากสร้างบัญชี · หมดแล้วหยุดแบรนด์/มอนิเตอร์/คอนเทนต์ · แผนเสียเงิน/แอดมินไม่โดน OK")

# ต่อเวลา
a = TestClient(app); a.post("/login", data={"email": "admin@test.local", "password": "pw123456"})
pg = a.get("/admin").text; assert "หมดทดลอง" in pg and "ทดลองเหลือ 14 วัน" in pg and "+14 วัน" in pg
a.post(f"/admin/tenants/{old}/trial", data={"days": "14"}); tr = B.trial(T(old))
assert not tr["expired"] and tr["ends"] == (dt.date.today() + dt.timedelta(days=14)).isoformat() and B.check(T(old), "runs")[0]
a.post(f"/admin/tenants/{new}/trial", data={"days": "14"}); assert B.trial(T(new))["days_left"] >= 27                                                  # ต่อจากวันหมดเดิม ไม่ใช่จากวันนี้
db.set_trial_until(old, (dt.date.today() - dt.timedelta(days=1)).isoformat()); assert B.trial(T(old))["expired"]
print("extend: ผู้ดูแลต่อ +14 วัน (นับต่อจากวันหมดเดิม) · หน้าแอดมินแสดงสถานะ OK")

# รอบอัตโนมัติข้ามบัญชีที่หมดทดลอง
b_new = db.create_brand(new, "n", "https://n.co.th")
if True:
    b_old = db.create_brand(old, "o", "https://o.co.th"); b_paid = db.create_brand(paid, "p", "https://p.co.th")
    ids = {b["id"] for b in RM.select_targets(db.list_all_brands(), all_=True)}
    assert b_new in ids and b_paid in ids and b_old not in ids, ids
    print("cron: รอบอัตโนมัติข้ามแบรนด์ของบัญชีที่หมดทดลอง OK")

# แบนเนอร์ + หน้าแรก
u = TestClient(app); u.post("/login", data={"email": "new@test.local", "password": "pw123456"})
assert "ทดลองใช้ฟรี เหลือ" in u.get("/upgrade").text
o = TestClient(app); o.post("/login", data={"email": "old@test.local", "password": "pw123456"})
assert "ช่วงทดลองใช้ฟรี 14 วันหมดแล้ว" in o.get("/upgrade").text
p = TestClient(app); p.post("/login", data={"email": "paid@test.local", "password": "pw123456"}); pt = p.get("/upgrade").text
assert "ทดลองใช้ฟรี เหลือ" not in pt and "ช่วงทดลองใช้ฟรี 14 วันหมดแล้ว" not in pt
home = TestClient(app).get("/").text; assert "ทดลองใช้ฟรี 14 วัน" in home and "ทดลองฟรี 14 วัน" in home and "14 วัน" in home
print("ui: แบนเนอร์เหลือกี่วัน/หมดแล้ว · แผนเสียเงินไม่เห็น · หน้าแรกบอก 14 วัน OK")
print("\ntrial_smoke: ผ่านทั้งหมด")
