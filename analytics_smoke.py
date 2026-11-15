"""ทดสอบ stage 08B: เหตุการณ์ funnel ภายใน (flag analytics)
- flag ปิด = ไม่เก็บอะไร ไม่มีช่องซ่อน ไม่มีสคริปต์ endpoint 404 (โหมดปิด)
- เหตุการณ์เซิร์ฟเวอร์: audit_started/completed · business_context_confirmed · plan_requested · booking_link_clicked
- เบราว์เซอร์: evidence_opened ผ่าน same-origin + CSRF · ชื่อ/ค่านอกรายการถูกทิ้ง · กันซ้ำ · เพดานต่อวัน
- ที่มา allowlist + ลำดับความสำคัญ · ไม่เก็บ PII/token/URL/IP · ทีมงานไม่ถูกนับ · ไม่มีการนัดปลอม
ไม่ยิงเครือข่าย · DB ชั่วคราว"""
import os, re, json, tempfile
os.environ.pop("DATABASE_URL", None)
os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["AI_BASE_URL"] = "http://127.0.0.1:9"
os.environ["GEO_DEEP_AUDIT"] = "0"
for k in list(os.environ):
    if k.startswith("GEO_FLAG_"):
        os.environ.pop(k)
import bcrypt
from fastapi.testclient import TestClient
import app.db as db
import app.flags as F
import app.snapshot as S
import app.site_health as sh
import app.pipeline as pipeline
import app.analytics as A
from app.main import app

db.init_db()
pipeline.alert_admins = lambda *a, **kw: None
S.RUN_INLINE = True
REPORT = {"ok": True, "n_fail": 0, "n_ok": 1, "checks": [{"key": "home", "label": "หน้าแรก", "status": "ok"}]}


def fake(site, progress=None, timeout=20, deadline=None, **kw):
    for k, _ in S.STEPS:
        progress and progress(k)
    return dict(REPORT)


sh.run_checks = fake
ev = lambda: [dict(r) for r in reversed(db.list_analytics_events(500))]
names = lambda: [e["name"] for e in ev()]
tok_of = lambda r: str(r.url).rstrip("/").split("#")[0].rsplit("/", 1)[-1]

# ---------- ที่มา (allowlist + ลำดับ) ----------
assert A.classify(partner=True, utm_source="google", src="line", entry="social")["source"] == "partner"
assert A.classify(utm_source="Facebook", src="line") == {"source": "social", "utm_source": "facebook"}
assert A.classify(utm_source="evil<script>", src="line")["source"] == "line" and A.classify(utm_source="evil")["utm_source"] is None
assert A.classify(src="home", entry="organic")["source"] == "organic"                 # src=home คือจุดวางฟอร์ม ไม่ใช่ที่มา
assert A.classify(src="home")["source"] == "direct" and A.classify(entry="hacker")["source"] == "direct"
assert A.entry_from_referer("https://www.google.co.th/search?q=x", "ours.example") == "organic"
assert A.entry_from_referer("https://l.facebook.com/l.php?u=abc", "ours.example") == "social"
assert A.entry_from_referer("https://line.me/R/ti/p/x", "ours.example") == "line"
assert A.entry_from_referer("https://blog.someone.co.th/post", "ours.example") == "referral"
assert A.entry_from_referer("https://ours.example/how-it-works", "ours.example") is None and A.entry_from_referer(None, "x") is None
assert A.entry_from_referer("https://notgoogle.com/", "x") == "referral"

# ---------- flag ปิด = โหมดปิด ----------
F.set_enabled("snapshot_v2", True); F.set_enabled("report_v2", True); F.set_enabled("marketing_v2", True)
F.set_enabled("plan_request_v2", True); F.set_enabled("context_v2", True)
c = TestClient(app)
home = c.get("/?utm_source=google", headers={"referer": "https://www.google.com/"}).text
assert 'name="utm_source"' not in home and 'name="ent"' not in home
r = c.post("/audit", data={"url": "off-shop.co.th", "utm_source": "google"}, headers={"x-forwarded-for": "1.1.1.1"})
tok0 = tok_of(r)
assert ev() == [] and "sendBeacon" not in c.get(f"/audit/{tok0}").text
html = c.get(f"/audit/{tok0}").text
csrf = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
assert c.post(f"/audit/{tok0}/event", data={"csrf_token": csrf, "name": "evidence_opened", "section": "lane"}).status_code == 404

# ---------- flag เปิด ----------
F.set_enabled("analytics", True)
home = c.get("/?utm_source=google&utm_campaign=secret", headers={"referer": "https://www.google.com/search?q=abc"}).text
assert '<input type="hidden" name="utm_source" value="google">' in home and '<input type="hidden" name="ent" value="organic">' in home
assert "secret" not in home and "q=abc" not in home
assert '<input type="hidden" name="utm_source" value="">' in c.get("/?utm_source=<b>x</b>").text      # นอกรายการ = ว่าง
r = c.post("/audit", data={"url": "on-shop.co.th", "utm_source": "google", "ent": "organic", "src": "home"},
           headers={"x-forwarded-for": "2.2.2.2"})
tok = tok_of(r)
lead = db.get_lead_by_token(tok)
e = ev()
assert [x["name"] for x in e] == ["audit_started", "audit_completed"], e
assert e[0]["source"] == "organic" and e[0]["utm_source"] == "google" and json.loads(e[0]["props_json"]) == {"reused": False}
assert e[1]["source"] == "organic" and json.loads(e[1]["props_json"]) == {"status": "completed"} and e[1]["lead_id"] == lead["id"]
# กดตรวจเว็บเดิมซ้ำใน 24 ชม. = ใช้ผลเดิม
c.post("/audit", data={"url": "on-shop.co.th"}, headers={"x-forwarded-for": "2.2.2.2"})
assert json.loads(ev()[-1]["props_json"]) == {"reused": True}
# พันธมิตรมาก่อนทุกอย่าง
c.post("/audit", data={"url": "ref-shop.co.th", "utm_source": "facebook", "ref": "abcd1234"}, headers={"x-forwarded-for": "3.3.3.3"})
assert ev()[-2]["source"] == "partner" and ev()[-2]["name"] == "audit_started"
# ตรวจล้ม
sh.run_checks = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("down"))
c.post("/audit", data={"url": "fail-shop.co.th"}, headers={"x-forwarded-for": "4.4.4.4"})
assert ev()[-1]["name"] == "audit_completed" and json.loads(ev()[-1]["props_json"]) == {"status": "failed"} and ev()[-1]["source"] == "direct"
sh.run_checks = fake

# ---------- ยืนยันข้อมูลธุรกิจ (นับหลังบันทึกสำเร็จเท่านั้น) ----------
html = c.get(f"/audit/{tok}").text
assert "sendBeacon" in html and 'data-ev="lane"' in html
csrf = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
n0 = len(ev())
assert c.post(f"/audit/{tok}/context", data={"csrf_token": csrf, "primary_service": ""}).status_code == 422
assert len(ev()) == n0                                                                   # กรอกผิด = ไม่นับ
c.post(f"/audit/{tok}/context", data={"csrf_token": csrf, "primary_service": "ให้เช่าโกดัง"}, follow_redirects=False)
assert ev()[-1]["name"] == "business_context_confirmed" and json.loads(ev()[-1]["props_json"]) == {"version": 1}

# ---------- เปิดหลักฐาน (เบราว์เซอร์) ----------
url = f"/audit/{tok}/event"
assert c.post(url, data={"name": "evidence_opened", "section": "lane"}).status_code == 403                 # ไม่มี CSRF
assert c.post(url, data={"csrf_token": csrf, "name": "evidence_opened", "section": "lane", "lane": "ai_citation"}).status_code == 204
x = ev()[-1]
assert x["name"] == "evidence_opened" and x["origin"] == "browser" and json.loads(x["props_json"]) == {"section": "lane", "lane": "ai_citation"}
n1 = len(ev())
assert c.post(url, data={"csrf_token": csrf, "name": "evidence_opened", "section": "lane", "lane": "ai_citation"}).status_code == 204
assert len(ev()) == n1                                                                   # ซ้ำใน 10 นาที = ไม่นับ
c.post(url, data={"csrf_token": csrf, "name": "evidence_opened", "section": "<script>", "lane": "x" * 500})
assert json.loads(ev()[-1]["props_json"]) == {}                                          # ค่านอกรายการถูกทิ้ง
assert c.post(url, data={"csrf_token": csrf, "name": "plan_requested", "channel": "phone"}).status_code == 400   # เหตุการณ์สำเร็จปลอมจากเบราว์เซอร์ไม่ได้
assert c.post(url, data={"csrf_token": csrf, "name": "meeting_booked"}).status_code == 400
assert c.post("/audit/NOPE0000/event", data={"csrf_token": csrf, "name": "evidence_opened"}).status_code == 404
old_cap = A.DAILY_CAP
A.DAILY_CAP = len([e for e in ev() if e["lead_id"] == lead["id"]])
c.post(url, data={"csrf_token": csrf, "name": "evidence_opened", "section": "action"})
assert json.loads(ev()[-1]["props_json"]) != {"section": "action"}                       # ครบเพดาน = ไม่นับ
A.DAILY_CAP = old_cap

# ---------- ขอแผน + กดคุยต่อใน LINE · ไม่มีการนัดปลอม ----------
r = c.post(f"/audit/{tok}/plan", data={"csrf_token": csrf, "idem_key": "a" * 32, "name": "สมชาย ใจดี", "contact_channel": "phone",
                                       "contact_value": "0812345678", "consent_fulfil": "1"}, follow_redirects=False)
assert r.status_code in (302, 303)
assert ev()[-1]["name"] == "plan_requested" and json.loads(ev()[-1]["props_json"]) == {"channel": "phone"} and ev()[-1]["source"] == "organic"
c.post(f"/audit/{tok}/plan", data={"csrf_token": csrf, "idem_key": "a" * 32, "name": "สมชาย ใจดี", "contact_channel": "phone",
                                   "contact_value": "0812345678", "consent_fulfil": "1"}, follow_redirects=False)
assert names().count("plan_requested") == 1                                              # กดส่งซ้ำ = ไม่นับซ้ำ
import app.line_oa as LO
os.environ["GEO_LINE_OA_ID"] = "@jortest"
LO.config = lambda: {"token": "tok", "secret": "sec"}                                   # ไม่ยิง LINE จริง
F.set_enabled("line_appointments", True)
r = c.get(f"/audit/{tok}/line", follow_redirects=False)
assert r.status_code == 303, r.status_code
if True:
    assert ev()[-1]["name"] == "booking_link_clicked" and json.loads(ev()[-1]["props_json"]) == {"target": "line"}
assert "meeting_booked" not in names()

# ---------- ไม่เก็บข้อมูลส่วนตัว/token/URL/IP ----------
dump = json.dumps(ev(), ensure_ascii=False)
for bad in ("สมชาย", "0812345678", tok, "on-shop.co.th", "2.2.2.2", "http", "secret"):
    assert bad not in dump, bad
cols = set(ev()[0].keys())
assert cols == {"id", "created_at", "name", "origin", "lead_id", "source", "utm_source", "props_json"}, cols

# ---------- ทีมงาน/แอดมินไม่ถูกนับ · หน้า funnel ----------
db.create_tenant("admin@an.test", bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Admin", is_admin=True)
adm = TestClient(app)
assert adm.post("/login", data={"email": "admin@an.test", "password": "pw123456"}).status_code == 200
n2 = len(ev())
adm.post("/audit", data={"url": "staff-shop.co.th"}, headers={"x-forwarded-for": "5.5.5.5"})
assert len(ev()) == n2 and "sendBeacon" not in adm.get(f"/audit/{tok}").text
page = adm.get("/admin/analytics")
assert page.status_code == 200 and "audit_started" in page.text and "ค้นหา" in page.text and "พันธมิตร" in page.text
f = A.funnel(30)
row = {r["name"]: r for r in f["rows"]}
assert row["audit_started"]["cells"]["organic"] == 1 and row["plan_requested"]["total"] == 1 and row["meeting_booked"]["total"] == 0
assert c.get("/admin/analytics", follow_redirects=False).status_code in (302, 303)        # คนนอกเข้าไม่ได้

# ---------- ปิด flag อีกครั้ง = หยุดเก็บทันที ----------
F.set_enabled("analytics", False)
n3 = len(ev())
c.post("/audit", data={"url": "late-shop.co.th"}, headers={"x-forwarded-for": "6.6.6.6"})
assert len(ev()) == n3
print("analytics_smoke OK")
