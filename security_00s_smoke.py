"""ทดสอบ stage 00S: กัน URL ภายใน (SSRF) · ไม่ส่งรายงานของลีดคนอื่น (เว็บ + LINE) · กัน LINE event ซ้ำ
ไม่ยิงเครือข่าย: DNS ถูกแทนด้วยของปลอม, HTTP ใช้ MockTransport, ตรวจเว็บ/LINE ถูกแทน · DB ชั่วคราว"""
import os, tempfile, socket
os.environ.pop("DATABASE_URL", None)                      # เทสต์นี้ใช้ SQLite ชั่วคราวเท่านั้น
os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["AI_BASE_URL"] = "http://127.0.0.1:9"
os.environ["GEO_DEEP_AUDIT"] = "0"
os.environ["GEO_CS"] = "0"
os.environ.pop("GEO_NETGUARD", None)
import httpx
from fastapi.testclient import TestClient
import app.netguard as NG
import app.leads as L
import app.db as db
import app.pipeline as P
import app.line_oa as LO
import app.site_health as sh
db.init_db()                                              # บางฟังก์ชันอ่าน settings ระหว่างตรวจ

# ---------- 1. ตัวกัน URL ภายใน ----------
for bad in ("127.0.0.1", "10.0.0.1", "172.16.5.4", "192.168.1.1", "169.254.169.254", "0.0.0.0", "::1", "[::1]",
            "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:169.254.169.254", "2130706433", "0x7f.1", "100.64.0.1",
            "localhost", "db.local", "metadata.google.internal", "224.0.0.1"):
    assert not NG.literal_ok(bad), bad
for good in ("8.8.8.8", "example.co.th", "ร้านค้า.ไทย", "1.1.1.1"):
    assert NG.literal_ok(good), good

for bad in ("http://169.254.169.254/latest/meta-data/", "http://10.0.0.1:8080/", "ftp://example.com/", "file:///etc/passwd",
            "http://[::1]/", "gopher://example.com/"):
    try:
        NG.check_url(bad)
        raise AssertionError(f"ควรถูกบล็อก: {bad}")
    except NG.BlockedURL:
        pass

_real_gai = socket.getaddrinfo
DNS = {"public.example": "93.184.216.34", "evil-rebind.example": "10.1.2.3", "v6-internal.example": "fd00::5"}
def fake_gai(host, *a, **k):
    if host in DNS:
        ip = DNS[host]
        fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(fam, socket.SOCK_STREAM, 6, "", (ip, 0))]
    raise socket.gaierror("no such host")
socket.getaddrinfo = fake_gai
try:
    NG.check_url("https://public.example/")                               # ชื่อที่ชี้ IP สาธารณะ → ผ่าน
    NG.check_url("https://does-not-resolve.example/")                     # resolve ไม่ได้ → ปล่อยผ่าน (ต่อจริงก็ล้มเอง)
    for name in ("evil-rebind.example", "v6-internal.example"):
        try:
            NG.check_url(f"https://{name}/")
            raise AssertionError(name)
        except NG.BlockedURL:
            pass

    # redirect ไปที่อยู่ภายในต้องถูกหยุดกลางทาง ไม่ใช่แค่ตรวจ URL แรก
    seen = []
    def handler(req: httpx.Request):
        seen.append(str(req.url))
        if req.url.host == "public.example":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
        return httpx.Response(200, text="SECRET")
    with NG.client(transport=httpx.MockTransport(handler), follow_redirects=True, timeout=5) as c:
        try:
            c.get("https://public.example/")
            raise AssertionError("redirect เข้า metadata ควรถูกบล็อก")
        except NG.BlockedURL:
            pass
    assert seen == ["https://public.example/"], seen                     # ไม่เคยส่ง request ไป 169.254.169.254

    # site_health ใช้ตัวกันแล้ว: เว็บที่ชี้เข้าเครือข่ายภายใน = เปิดไม่ได้ (status 0) ไม่ใช่ได้เนื้อหาภายใน
    _Client = httpx.Client
    httpx.Client = lambda *a, **kw: _Client(*a, transport=httpx.MockTransport(lambda r: httpx.Response(200, text="SECRET")), **kw)
    try:
        rep = sh.run_checks("https://evil-rebind.example")
        home = next(x for x in rep["checks"] if x["key"] == "home")
        assert home["status"] == "fail", home
    finally:
        httpx.Client = _Client

    # ปิดฉุกเฉินได้
    os.environ["GEO_NETGUARD"] = "0"
    NG.check_url("http://169.254.169.254/")
    assert NG.literal_ok("127.0.0.1")
    os.environ.pop("GEO_NETGUARD")
finally:
    socket.getaddrinfo = _real_gai

# normalize_url ไม่รับ IP ภายในตั้งแต่ทางเข้า แต่ยังรับเว็บปกติแบบเดิม
assert L.normalize_url("169.254.169.254") is None and L.normalize_url("http://10.0.0.1/admin") is None
assert L.normalize_url("127.0.0.1:8095") is None
assert L.normalize_url("example.co.th") == "https://example.co.th"
assert L.normalize_url("http://www.shop.com:8080/a") == "http://www.shop.com:8080"

# ---------- 2. ไม่ส่งรายงานของลีดคนอื่น ----------
row = lambda **kw: {"name": None, "email": None, "contact": None, "line_user_id": None, "consent": 0, "ip": "", "source": "audit", **kw}
assert L.reusable_for(row(ip="1.1.1.1"), ip="1.1.1.1")
assert not L.reusable_for(row(ip="1.1.1.1"), ip="2.2.2.2")                 # คนละ IP
assert not L.reusable_for(row(ip="1.1.1.1", email="a@b.co"), ip="1.1.1.1") # ให้ช่องทางติดต่อแล้ว = ข้อมูลของคนนั้น
assert not L.reusable_for(row(ip="1.1.1.1", source="prospect"), ip="1.1.1.1")
assert L.reusable_for(row(line_user_id="U1"), line_user_id="U1")
assert not L.reusable_for(row(line_user_id="U1"), line_user_id="U2")
assert not L.reusable_for(row(), line_user_id="U2")                        # ลีดที่ยังไม่ผูก LINE ห้ามผูกจากการพิมพ์โดเมน
assert not L.reusable_for(None, ip="1.1.1.1")

REPORT ={"ok": True, "n_fail": 0, "n_ok": 3, "checks": [{"key": "x", "label": "ผ่าน", "status": "ok"}] * 3}
sh.run_checks = lambda *a, **k: REPORT
P.RUN_INLINE = True
replies, pushes = [], []
LO.reply = lambda token, texts: replies.append(texts) or True
LO.push = lambda uid, texts: pushes.append((uid, texts)) or True
P.alert_admins = lambda *a, **k: None

from app.main import app
c = TestClient(app)

# เว็บ: เจ้าของตัวจริงตรวจ + ให้อีเมลแล้ว → คนอื่นตรวจโดเมนเดียวกันต้องได้ token ใหม่
r = c.post("/audit", data={"url": "victim-shop.co.th"}, headers={"x-forwarded-for": "1.1.1.1"})
victim_token = str(r.url).rstrip("/").rsplit("/", 1)[-1]
victim = db.get_lead_by_token(victim_token)
db.update_lead(victim["id"], email="owner@victim-shop.co.th", consent=1)
r = c.post("/audit", data={"url": "victim-shop.co.th"}, headers={"x-forwarded-for": "9.9.9.9"})
other_token = str(r.url).rstrip("/").rsplit("/", 1)[-1]
assert other_token and other_token != victim_token, (victim_token, other_token)
assert db.get_lead(victim["id"])["email"] == "owner@victim-shop.co.th"
# ผู้มาตรวจนิรนามคนเดิม (IP เดิม) ยังได้ผลเดิมใน 24 ชม. เหมือนก่อน
r1 = c.post("/audit", data={"url": "anon-shop.co.th"}, headers={"x-forwarded-for": "5.5.5.5"})
r2 = c.post("/audit", data={"url": "anon-shop.co.th"}, headers={"x-forwarded-for": "5.5.5.5"})
assert str(r1.url) == str(r2.url)

# LINE: U1 เป็นเจ้าของลีด → U2 พิมพ์โดเมนเดียวกัน ต้องไม่ได้ลิงก์รายงานของ U1 และ U1 ยังเป็นเจ้าของ
db.update_lead(victim["id"], line_user_id="U1")
replies.clear(); pushes.clear()
res = P.line_site_request("U2", "victim-shop.co.th", "rt")
assert res == "started", res
assert db.get_lead(victim["id"])["line_user_id"] == "U1"
everything = " ".join(t for texts in replies for t in texts) + " ".join(t for _, texts in pushes for t in texts)
assert victim_token not in everything, everything
mine = db.get_lead_by_line_user("U2")
assert mine and mine["id"] != victim["id"] and mine["domain"] == "victim-shop.co.th"
# U1 ขอซ้ำ → ได้ผลเดิมของตัวเอง แม้ลีดล่าสุดของโดเมนจะเป็นของ U2 แล้ว
replies.clear()
assert P.line_site_request("U1", "victim-shop.co.th", "rt") == "cached"
assert victim_token in " ".join(replies[0])
# U2 ขอซ้ำ → ได้ผลของ U2 เอง ไม่ใช่ของ U1
replies.clear()
assert P.line_site_request("U2", "victim-shop.co.th", "rt") == "cached"
assert victim_token not in " ".join(replies[0]) and mine["token"] in " ".join(replies[0])

# ---------- 3. LINE event ซ้ำ ----------
assert db.claim_line_event("ev-1") is True and db.claim_line_event("ev-1") is False and db.claim_line_event("") is True
body = {"events": [{"type": "follow", "replyToken": "r1", "source": {"userId": "U9"}, "webhookEventId": "ev-follow",
                    "deliveryContext": {"isRedelivery": False}}]}
replies.clear()
assert P.handle_line_webhook(body) == ["follow"] and len(replies) == 1
body["events"][0]["deliveryContext"]["isRedelivery"] = True
assert P.handle_line_webhook(body) == ["duplicate"] and len(replies) == 1   # ส่งซ้ำ → ไม่ตอบซ้ำ
ev = LO.parse_events(body)[0]
assert ev["event_id"] == "ev-follow" and ev["redelivery"] is True

print("security_00s_smoke OK")
