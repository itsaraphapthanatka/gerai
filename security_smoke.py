"""ทดสอบเกราะระดับคำขอ (app/security.py + ส่วนที่ต่อใน main.py):
IP จริงหลัง proxy · จำกัดการลองล็อกอิน · กัน CSRF · คุกกี้ Secure · /docs ปิด · /healthz · ไฟล์สลิป · Stripe webhook ตอบ 500 เมื่อพัง"""
import os
import tempfile
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke-"), "smoke.db")
os.environ.setdefault("SESSION_SECRET", "smoke-only-not-a-real-secret")
os.environ["LITELLM_BASE_URL"] = ""
for k in ("TRUSTED_PROXY_HOPS", "CSRF_TRUSTED_ORIGINS", "PUBLIC_BASE_URL", "GEO_BASE_URL", "API_DOCS"):
    os.environ.pop(k, None)
import json
from pathlib import Path
import bcrypt
from fastapi.testclient import TestClient
import app.db as db
import app.main as M
from app import security as S, billing, stripe_pay as SP
from app.main import app

db.init_db()
ADMIN, PW = "sec-admin@geo.local", "adminpass-123"
if not db.get_tenant_by_email(ADMIN):
    db.create_tenant(ADMIN, bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(), "Sec", is_admin=True)
tmp = Path(tempfile.mkdtemp(prefix="geo-sec-"))
M.BASE = tmp / "app"                                   # สลิปที่เทสต์อัปโหลดลงโฟลเดอร์ชั่วคราว ไม่ปนกับ uploads/ ของโปรเจกต์


# ---- IP จริงหลัง proxy ----
class Req:
    def __init__(self, xff=None, peer="10.0.0.9"):
        self.headers = {"x-forwarded-for": xff} if xff is not None else {}
        self.client = type("C", (), {"host": peer})()

assert S.client_ip(Req("1.2.3.4")) == "1.2.3.4"
assert S.client_ip(Req("6.6.6.6, 1.2.3.4")) == "1.2.3.4", "ค่าซ้ายสุดผู้ใช้ปลอมเองได้ — ต้องใช้ค่าที่ proxy ของเราต่อท้าย"
assert S.client_ip(Req(None, peer="9.9.9.9")) == "9.9.9.9"
os.environ["TRUSTED_PROXY_HOPS"] = "2"
assert S.client_ip(Req("6.6.6.6, 1.2.3.4, 172.16.0.1")) == "1.2.3.4" and S.client_ip(Req("1.2.3.4")) == "1.2.3.4"
os.environ["TRUSTED_PROXY_HOPS"] = "0"
assert S.client_ip(Req("6.6.6.6", peer="9.9.9.9")) == "9.9.9.9"
os.environ.pop("TRUSTED_PROXY_HOPS")
print("client_ip: ใช้ค่าที่ proxy ต่อท้าย ไม่ใช่ค่าที่ผู้ใช้ส่งมา · ตั้งจำนวนชั้น proxy ได้ OK")

# ---- ตัวนับ ----
L = S.Limiter(3, 60)
for t in (0, 10, 20):
    assert L.retry_after("k", now=t) == 0; L.hit("k", now=t)
assert L.retry_after("k", now=21) == 40 and L.retry_after("other", now=21) == 0
assert L.retry_after("k", now=61) == 0, "ครั้งแรกหลุดหน้าต่างแล้วต้องลองได้อีก"
L.clear("k"); assert L.retry_after("k", now=21) == 0
print("limiter: หน้าต่างเลื่อน · แยกตาม key · ล้างได้ OK")

# ---- ล็อกอิน: ผิดถี่ = ถูกพัก ----
def login(c, pw, ip="203.0.113.7", email=ADMIN):
    return c.post("/login", data={"email": email, "password": pw}, headers={"X-Forwarded-For": ip}, follow_redirects=False)

c = TestClient(app)
for i in range(S.LOGIN_PAIR.limit):
    r = login(c, "wrong"); assert r.status_code == 200 and "ไม่ถูกต้อง" in r.text, (i, r.status_code)
r = login(c, PW); assert r.status_code == 429 and "session" not in r.cookies, "ครบโควตาแล้วรหัสถูกก็ต้องรอ (ไม่งั้นเดาต่อได้เรื่อย ๆ)"
r = login(c, PW, ip="6.6.6.6, 203.0.113.7"); assert r.status_code == 429, "ปลอม X-Forwarded-For ข้างหน้าไม่ช่วยให้หลุด"
r = login(c, PW, ip="198.51.100.4"); assert r.status_code == 303 and r.headers["location"] == "/admin", "เจ้าของบัญชีจากเครื่องอื่นต้องเข้าได้ — ไม่ล็อกทั้งบัญชี"
S.LOGIN_PAIR.reset(); S.LOGIN_IP.reset()
for i in range(S.LOGIN_PAIR.limit - 1):
    login(c, "wrong")
assert login(c, PW).status_code == 303
for i in range(S.LOGIN_PAIR.limit - 1):
    assert login(c, "wrong").status_code == 200, "ล็อกอินสำเร็จแล้วต้องเริ่มนับใหม่"
S.LOGIN_PAIR.reset(); S.LOGIN_IP.reset()
for i in range(S.LOGIN_IP.limit):
    login(c, "wrong", email=f"u{i}@example.com")
assert login(c, "wrong", email="fresh@example.com").status_code == 429, "IP เดียวไล่เดาหลายบัญชีก็ต้องถูกพัก"
S.LOGIN_PAIR.reset(); S.LOGIN_IP.reset()
print("login: ผิดถี่ถูกพัก 429 · ปลอม header ไม่หลุด · ไม่ล็อกทั้งบัญชี · สำเร็จแล้วนับใหม่ OK")

# ---- CSRF ----
a = TestClient(app); assert login(a, PW).status_code == 303
def write(headers, client=a): return client.post("/account/password", data={"current": "x", "new": "y", "confirm": "z"}, headers=headers, follow_redirects=False)
assert write({"Origin": "https://evil.example"}).status_code == 403
assert write({"Sec-Fetch-Site": "cross-site"}).status_code == 403
assert write({"Sec-Fetch-Site": "same-site", "Origin": "https://other.testserver.example"}).status_code == 403, "เว็บอื่นใต้โดเมนแม่เดียวกันก็ไม่ใช่เรา"
assert write({"Origin": "null", "Sec-Fetch-Site": "cross-site"}).status_code == 403
assert write({}).status_code != 403, "ไม่มี Origin/Sec-Fetch-Site (ไม่ใช่เบราว์เซอร์) ต้องผ่าน"
assert write({"Origin": "http://testserver"}).status_code != 403
assert write({"Sec-Fetch-Site": "same-origin", "Origin": "https://behind-proxy.example"}).status_code != 403, "เบราว์เซอร์ยืนยันว่า same-origin = ผ่าน แม้ proxy ไม่ส่ง Host เดิมมา"
assert write({"Origin": "https://geo.example.com", "X-Forwarded-Host": "geo.example.com"}).status_code != 403
os.environ["CSRF_TRUSTED_ORIGINS"] = "https://admin.example.com"
assert write({"Origin": "https://admin.example.com"}).status_code != 403
os.environ.pop("CSRF_TRUSTED_ORIGINS"); assert write({"Origin": "https://admin.example.com"}).status_code == 403
anon = TestClient(app)
assert anon.post("/audit", data={"url": ""}, headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}).status_code != 403, "ไม่ล็อกอิน = ไม่มีสิทธิ์ให้ขโมย ฟอร์มสาธารณะต้องใช้ได้"
assert a.get("/admin", headers={"Origin": "https://evil.example"}, follow_redirects=False).status_code == 200, "GET ไม่ถูกบล็อก"
print("csrf: POST ข้ามเว็บพร้อมคุกกี้ล็อกอิน → 403 · same-origin / ไม่ใช่เบราว์เซอร์ / ไม่ล็อกอิน ผ่าน OK")

# ---- คุกกี้ Secure เมื่อเข้าทาง HTTPS ----
h = TestClient(app, base_url="https://testserver")
r = login(h, PW); sc = r.headers.get("set-cookie", "")
assert r.status_code == 303 and "session=" in sc and "secure" in sc.lower() and "httponly" in sc.lower() and "samesite=lax" in sc.lower(), sc
assert h.get("/admin", follow_redirects=False).status_code == 200
r = h.get("/login?lang=en"); assert r.headers.get("set-cookie", "").lower().count("secure") == 1, "คุกกี้ที่ติด Secure อยู่แล้วไม่ต่อซ้ำ"
r = login(TestClient(app), PW); assert "secure" not in r.headers["set-cookie"].lower(), "http (dev/เทสต์) ต้องยังล็อกอินได้"
print("cookie: HTTPS → Secure + HttpOnly + SameSite=Lax · HTTP ไม่ติด Secure OK")

# ---- /docs ปิด · /healthz ----
for p in ("/docs", "/redoc", "/openapi.json"):
    assert anon.get(p).status_code == 404, p
r = anon.get("/healthz"); assert r.status_code == 200 and r.json() == {"ok": True}
real = db.get_conn
def down(): raise ConnectionError("db down: password=hunter2")
db.get_conn = down
try:
    r = anon.get("/healthz"); assert r.status_code == 503 and r.json() == {"ok": False, "db": "ConnectionError"} and "hunter2" not in r.text
finally:
    db.get_conn = real
print("docs: ปิด · healthz: 200 เมื่อฐานตอบ / 503 เมื่อฐานล่ม (ไม่คายรายละเอียด) OK")

# ---- body ใหญ่เกิน ----
old = M.MAX_BODY_BYTES; M.MAX_BODY_BYTES = 1000
try:
    assert anon.post("/audit", content=b"x" * 2000, headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 413
    assert anon.post("/audit", data={"url": ""}).status_code == 200
finally:
    M.MAX_BODY_BYTES = old
print("body: Content-Length เกินเพดาน → 413 OK")

# ---- สลิป ----
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
assert S.sniff_slip(b"\xff\xd8\xff\xe0abc") == ".jpg" and S.sniff_slip(PNG) == ".png" and S.sniff_slip(b"RIFF\0\0\0\0WEBPVP8 ") == ".webp"
assert S.sniff_slip(b"%PDF-1.7\n") == ".pdf" and S.sniff_slip(b"\0\0\0\x18ftypheic\0\0\0\0") == ".heic"
assert S.sniff_slip(b"<html><script>alert(1)</script>") is None and S.sniff_slip(b"MZ\x90\0") is None and S.sniff_slip(b"") is None
item = next(k for k, p in billing.PLANS.items() if k != "free" and not p.get("hidden"))
slips = tmp / "uploads" / "slips"
def pay(name, data, ct="image/jpeg"): return a.post(f"/upgrade/pay/{item}", files={"slip": (name, data, ct)}, follow_redirects=False)
def payments():
    with db.get_conn() as cn:
        return [dict(r) for r in cn.execute("SELECT * FROM payments ORDER BY id").fetchall()]
n0 = len(payments())
r = pay("slip.jpg", b"<html><script>alert(document.cookie)</script></html>")
assert r.status_code == 400 and "ไฟล์สลิปต้องเป็นรูป" in r.text and len(payments()) == n0 and not slips.exists(), "HTML ปลอมนามสกุล .jpg ต้องถูกปฏิเสธ ไม่ถูกเก็บ"
r = pay("big.png", PNG + b"\0" * S.SLIP_MAX_BYTES, "image/png")
assert r.status_code == 400 and "8 MB" in r.text and len(payments()) == n0 and not slips.exists(), "ไฟล์ใหญ่เกินต้องถูกปฏิเสธ ไม่ใช่ตัดท้ายแล้วเก็บ"
r = pay("../../evil.html", PNG, "text/html")
assert r.status_code == 200 and "ได้รับแจ้งชำระเงิน" in r.text, r.status_code
row = payments()[-1]; saved = list(slips.iterdir())
assert len(payments()) == n0 + 1 and len(saved) == 1 and saved[0].suffix == ".png" and saved[0].read_bytes() == PNG and row["slip_path"] == f"slips/{saved[0].name}"
assert "evil" not in saved[0].name and saved[0].parent == slips, "ชื่อไฟล์ของผู้ใช้ต้องไม่ถูกใช้"
r = a.get(f"/admin/slip/{row['id']}")
assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png" and r.headers["x-content-type-options"] == "nosniff"
(slips / "old.jpg").write_bytes(b"<script>1</script>")       # สลิปเก่าก่อนมีการตรวจ: เนื้อเป็นอะไรก็ได้ แต่เบราว์เซอร์ต้องไม่รันเป็น HTML
with db.get_conn() as cn:
    cn.execute(db.q("UPDATE payments SET slip_path=? WHERE id=?"), ("slips/old.jpg", row["id"]))
r = a.get(f"/admin/slip/{row['id']}"); assert r.headers["content-type"] == "image/jpeg" and r.headers["x-content-type-options"] == "nosniff"
with db.get_conn() as cn:
    cn.execute(db.q("UPDATE payments SET slip_path=? WHERE id=?"), ("../../etc/passwd", row["id"]))
assert a.get(f"/admin/slip/{row['id']}").status_code == 404, "path ที่หลุดออกนอก uploads/ ต้องไม่ถูกเสิร์ฟ"
r = a.post(f"/upgrade/pay/{item}", follow_redirects=False); assert r.status_code == 200 and payments()[-1]["slip_path"] is None, "ไม่แนบสลิปยังแจ้งชำระได้เหมือนเดิม"
print("slip: ตรวจชนิดจากเนื้อไฟล์ · ใหญ่เกินปฏิเสธ · ชื่อไฟล์สุ่มเอง · เสิร์ฟแบบ nosniff · กัน path หลุด OK")

# ---- Stripe webhook: ทำไม่สำเร็จ → 500 ให้ Stripe ส่งใหม่ ----
WH = "whsec_testsecret_123456"; SP.save_config("sk_test_abcdefghijklmnop", WH)
body = json.dumps({"id": "evt_boom", "type": "invoice.paid", "data": {"object": {}}}).encode()
def hook(b=body): return anon.post("/stripe/webhook", content=b, headers={"Stripe-Signature": SP.sign(b, WH)})
notes = []; real_notify, real_handle = db.notify_admins, SP.handle_event
db.notify_admins = lambda msg, *a_, **k: notes.append(msg)
def boom(evt): raise RuntimeError("db is down sk_test_abcdefghijklmnop")
SP.handle_event = boom
try:
    r = hook(); assert r.status_code == 500 and r.json()["ok"] is False and "sk_test_abcdefghijklmnop" not in r.text, r.text
    assert hook().status_code == 500 and len(notes) == 1, "Stripe ส่งซ้ำหลายรอบ — แจ้งทีมงานครั้งเดียวต่อ event"
    SP.handle_event = lambda evt: "renewed"
    r = hook(); assert r.status_code == 200 and r.json() == {"ok": True, "result": "renewed"}, "ส่งซ้ำรอบที่ทำสำเร็จ → 200"
    assert anon.post("/stripe/webhook", content=body, headers={"Stripe-Signature": "t=1,v1=bad"}).status_code == 400
finally:
    db.notify_admins, SP.handle_event = real_notify, real_handle
r = hook(json.dumps({"id": "evt_other", "type": "something.unknown", "data": {"object": {}}}).encode())
assert r.status_code == 200 and r.json()["result"] == "ignored", "event ที่ไม่สนใจต้องตอบ 200 ไม่งั้น Stripe ส่งซ้ำไม่จบ"
print("stripe: handler พัง → 500 + แจ้งครั้งเดียว · สำเร็จ/ไม่สนใจ → 200 · ลายเซ็นผิด → 400 OK")

print("ALL SECURITY TESTS OK")
