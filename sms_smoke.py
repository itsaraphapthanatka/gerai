"""ทดสอบ SMS: เบอร์มือถือไทย · ข้อความสั้นมีลิงก์ + ลิงก์ไม่รับ · ส่งผ่าน ThaiBulkSMS (ปลอม) · นอกเวลา → ค้างไว้ · เพดานต่อวัน
· ช่องทางสุดท้ายของ pipeline (อีเมล → LINE → SMS) · ลีดหาเองที่ไม่มีอีเมลแต่มีมือถือ → CEO ทักทาง SMS · ติดตามผลทาง SMS · หน้าตั้งค่า + ทดสอบส่ง"""
import os, tempfile, json, datetime as dt
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke12-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["GEO_DEEP_AUDIT"] = "0"; os.environ["GEO_CS"] = "0"
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.sms as S, app.pipeline as P, app.mailer as mailer, app.execs as X, app.ai_client as ai, app.closer as C, app.prospecting as PR, app.site_health as sh
from app.main import app
db.init_db()
ai._read_setting = lambda k: None
ai._chat = lambda m, max_tokens=2048, timeout=120: (_ for _ in ()).throw(RuntimeError("no model"))
mailer.config = lambda: {"host": "x"}; mailer.send = lambda *a, **k: None; mailer.is_deliverable = lambda e: bool(e) and e.endswith(".co.th")
P.RUN_INLINE = True
calls = []
def fake_post(url, data, auth):
    calls.append((url, data, auth))
    if data["msisdn"] == "66899999999":
        return 400, {"error": {"code": 400, "description": "Invalid phone number"}}
    return 201, {"remaining_credit": 99, "phone_number_list": [{"number": data["msisdn"], "message_id": "m1", "used_credit": 2}], "bad_phone_number_list": []}
S.POST = fake_post
S.in_hours = lambda now=None: True

assert S.mobile("โทร 081-234-5678 หรือ 02-111-2222") == "0812345678" and S.mobile("+66 91 234 5678") == "0912345678" and S.mobile("02 111 2222") == ""
t = S.compact("ผลตรวจเว็บ shop.co.th: 3 ข้อที่ขวางไม่ให้ลูกค้าหาเจอ", "ดูที่ https://x/audit/abc แล้ว https://x/u/zz", "https://x/u/zz")
assert t.startswith("เจอ.AI:") and "https://x/audit/abc" in t and t.endswith("ไม่รับ: https://x/u/zz") and len(t) <= S.MAX_CHARS + 60
try:
    S.send("0812345678", "x"); raise SystemExit("ยังไม่ตั้งค่าต้องโยน")
except RuntimeError:
    pass
S.save_config("key123", "sec456", "JerAI", "2", True)
assert S.available() and S.config()["sender"] == "JerAI"
assert S.send("0812345678", "สวัสดี").startswith("ส่ง SMS ถึง 081-234-5678") and calls[-1][1]["msisdn"] == "66812345678" and calls[-1][2] == ("key123", "sec456")
try:
    S.send("0899999999", "x"); raise SystemExit("เบอร์ผิดต้องโยน")
except RuntimeError as e:
    assert "Invalid phone" in str(e)
S.send("0811111111", "x")
try:
    S.send("0812222222", "x"); raise SystemExit("ครบเพดานต้องโยน")
except RuntimeError as e:
    assert "เพดาน" in str(e)
S.save_config("", "", "JerAI", "30", True); assert S.config()["key"] == "key123"           # เว้นว่าง = คงเดิม
S.in_hours = lambda now=None: False
try:
    S.send("0812345678", "x"); raise SystemExit("นอกเวลาต้องโยน")
except S.QuietHours:
    pass
S.in_hours = lambda now=None: True
assert S.HOURS == (8, 20)
print("sms: มือถือ · ข้อความสั้น+ลิงก์ไม่รับ · ส่ง/เบอร์ผิด/เพดาน/นอกเวลา · คงคีย์เดิม OK")

# ---- ลีดไม่มีอีเมล มีแต่มือถือ → ช่องทาง SMS ----
REPORT = {"checks": [{"key": k, "label": f"ข้อ {k}", "status": "fail", "detail": ""} for k in ("csr", "noindex", "robots")]}
lid = db.add_lead("https://nomail.co.th", "nomail.co.th", source="prospect", contact="ร้าน 089-111-2233")
db.update_lead(lid, audit_json=json.dumps(REPORT), score=60, token="t-nomail")
assert P.send_channel(db.get_lead(lid)) == ""                                               # ลีดหาเอง ยังไม่เปิดสวิตช์ → ไม่ส่ง
own = db.add_lead("https://own.co.th", "own.co.th", source="audit", contact="0897776655"); db.update_lead(own, consent=1)
assert P.send_channel(db.get_lead(own)) == "SMS 089-777-6655"                               # ให้เบอร์เองพร้อมยินยอม → ส่งได้
S.save_config("", "", "JerAI", "30", True, True)
assert P.send_channel(db.get_lead(lid)) == "SMS 089-111-2233"
db.set_setting("auto_override_outreach", "1"); db.set_setting("auto_outreach", "1")
calls.clear(); did = P.ceo_propose(db.get_lead(lid)); d = db.get_decision(did)
assert d["status"] == "auto" and "SMS" in d["result"] and db.get_lead(lid)["status"] == "contacted", (d["status"], d["result"])
msg = calls[-1][1]["message"]; assert "/audit/t-nomail" in msg and "/u/t-nomail" in msg and msg.startswith("เจอ.AI:")
# นอกเวลา → ค้างไว้ แล้วรอบ 08:00 ส่ง
lid2 = db.add_lead("https://nomail2.co.th", "nomail2.co.th", source="prospect", contact="0892223344")
db.update_lead(lid2, audit_json=json.dumps(REPORT), score=60, token="t-nomail2")
S.in_hours = lambda now=None: False
did2 = P.ceo_propose(db.get_lead(lid2)); assert db.get_decision(did2)["status"] == "proposed" and "QuietHours" in db.get_decision(did2)["result"]
assert db.get_lead(lid2)["status"] == "new"
S.in_hours = lambda now=None: True
assert P.run_pending_auto() >= 1 and db.get_decision(did2)["status"] == "auto" and db.get_lead(lid2)["status"] == "contacted"
print("pipeline: ช่องทางสุดท้าย SMS · CEO ทักเอง · นอกเวลาค้าง → รอบเช้าส่ง OK")

# ---- prospecting: ไม่มีอีเมล → หามือถือบนเว็บ → ทักทาง SMS ----
sh.run_checks = lambda site, **kw: REPORT
C.FETCH = lambda u: '<a href="tel:0861234567">โทร</a>' if "mobile-shop" in u else ""
res = PR.run(limit=1, search=lambda q, limit=10: [{"url": "https://mobile-shop.co.th", "domain": "mobile-shop.co.th", "title": "ร้านมือถือ", "position": 1}],
             contact=lambda site: {"email": "", "line": "", "n": 0})
pl = db.find_lead_by_domain("mobile-shop.co.th")
assert json.loads(pl["call_json"])["phone"] == "0861234567" and res.get("with_sms") == 1 and pl["status"] == "contacted", (res, pl["status"])
print("prospect: ไม่มีอีเมล → มือถือจากเว็บ → ทัก SMS OK")

# ---- หน้าตั้งค่า + ทดสอบส่ง ----
admin = "admin@test.local"; db.create_tenant(admin, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Op", is_admin=True)
a = TestClient(app); a.post("/login", data={"email": admin, "password": "pw123456"})
S.balance = lambda: "เครดิตคงเหลือ 99"
r = a.get("/admin/settings"); assert "PDPA" in r.text and "SMS (ThaiBulkSMS)" in r.text and "••••y123" in r.text and "เครดิตคงเหลือ 99" in r.text and "key123" not in r.text
r = a.post("/admin/settings/sms/test", data={"phone": "081-555-6666"}); assert "✓ ส่ง SMS ถึง 081-555-6666" in r.text
r = a.post("/admin/settings/sms/test", data={"phone": "02-111-2222"}); assert "ไม่ใช่เบอร์มือถือ" in r.text
a.post("/admin/settings/sms", data={"sender": "JerAI", "daily": "30"}); assert not S.available() and P.send_channel(db.get_lead(lid)) == ""
print("admin: การ์ดตั้งค่า (ไม่โชว์คีย์) · ทดสอบส่ง · ปิดสวิตช์ OK")
print("ALL OK")
