"""ทดสอบบทโทรปิดดีล: คัดลีดตามความร้อน (ยืนยันแล้วไม่จ่าย > เปิดใบแล้วเงียบ > ส่งใบแล้วไม่เปิด > ตอบกลับไม่มีใบ > โอกาสสูงเงียบ 7 วัน)
· หาเบอร์ (ช่องติดต่อ → tel: → เนื้อหาเว็บ · มือถือก่อน) · บทพูดจากข้อมูลจริง + ราคา + เพดานส่วนลด · จดผล (นัดรอบถัดไป · ไม่สนใจปิดลีด · จะจ่ายส่งลิงก์)
· หน้า /admin/calls และบทโทร · สรุปเช้า"""
import os, tempfile, json, datetime as dt
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke11-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["GEO_DEEP_AUDIT"] = "0"; os.environ["GEO_CS"] = "0"
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.closer as C, app.pipeline as P, app.mailer as mailer, app.execs as X
from app.main import app
db.init_db()
sent = []; mailer.config = lambda: {"host": "x"}; mailer.send = lambda to, subj, html, text=None: sent.append((to, subj, text)); mailer.is_deliverable = lambda e: bool(e) and e.endswith(".co.th")
P.RUN_INLINE = True
REPORT = {"checks": [{"key": k, "label": f"ข้อ {k}", "status": "fail", "detail": ""} for k in ("csr", "noindex", "robots")]}
SITES = {"https://a.co.th": '<a href="tel:+66 81 234 5678">โทร</a>', "https://b.co.th": '<p>โทร 02-123-4567</p><a href="/contact">ติดต่อเรา</a>',
         "https://b.co.th/contact": "<p>มือถือ 089 765 4321</p>", "https://c.co.th": "<p>ไม่มีเบอร์</p>"}
C.FETCH = lambda u: SITES.get(u.rstrip("/"), "")
ago = lambda d: (dt.datetime.now() - dt.timedelta(days=d)).isoformat(timespec="seconds")

def mk(dom, status="contacted", score=60, contact=""):
    lid = db.add_lead(f"https://{dom}", dom, source="prospect", email=f"info@{dom}", contact=contact)
    db.update_lead(lid, status=status, audit_json=json.dumps(REPORT), score=score, token=f"t-{dom}")
    return lid
def quote(lid, status, **ts):
    q = P.create_quote(db.get_lead(lid), "pro"); db.update_quote(q["id"], status=status, **ts); return db.get_quote(q["id"])

a = mk("a.co.th", "quoted"); quote(a, "accepted", sent_at=ago(4), viewed_at=ago(3), accepted_at=ago(2))
b = mk("b.co.th", "quoted"); quote(b, "viewed", sent_at=ago(5), viewed_at=ago(3))
c = mk("c.co.th", "replied")
d = mk("d.co.th", "contacted", score=70); eid = db.add_lead_event(d, "contacted", "", "auto")
with db.get_conn() as cc: cc.execute(db.q("UPDATE lead_events SET created_at=? WHERE id=?"), (ago(9), eid))
e = mk("e.co.th", "contacted", score=20)                                     # โอกาสต่ำ → ไม่ต้องโทร
f = mk("f.co.th", "quoted"); quote(f, "accepted", accepted_at=db.now())     # เพิ่งยืนยัน → ยังไม่ต้องโทร
g = mk("g.co.th", "unsubscribed")
lst = C.call_list()
assert [x["lead"]["domain"] for x in lst] == ["a.co.th", "b.co.th", "c.co.th", "d.co.th"], [x["lead"]["domain"] for x in lst]
assert lst[0]["trigger"]["prio"] == 100 and "ยังไม่ได้ชำระ" in lst[0]["trigger"]["why"]
print("list: ยืนยันไม่จ่าย > เปิดใบเงียบ > ตอบกลับไม่มีใบ > โอกาสสูงเงียบ · ข้ามโอกาสต่ำ/เพิ่งยืนยัน/ขอไม่รับ OK")

assert C.find_phone(db.get_lead(a)) == "0812345678"
assert C.find_phone(db.get_lead(b)) == "0897654321"                          # มือถือจากหน้าติดต่อมาก่อนเบอร์บ้าน
assert C.find_phone(db.get_lead(c)) == ""
h = mk("h.co.th", contact="LINE: abc · 02 999 8888"); assert C.find_phone(db.get_lead(h)) == "029998888"
assert C.fmt_phone("0812345678") == "081-234-5678" and C.fmt_phone("029998888") == "02-999-8888"
print("phone: tel: · หน้าติดต่อ · มือถือก่อน · ช่องติดต่อของลีด · รูปแบบ OK")

s = C.script(db.get_lead(a))
assert "ใบเสนอราคา" in s["opener"] and "ข้อ csr" in s["points"][0] and "9,900" in s["price"] and s["max_discount"] == X.MANDATE["max_discount_pct"]
assert "/q/" in s["pay_url"] and "ลิงก์ชำระเงิน" in s["close"] and any("ลดได้ไม่เกิน" in o[1] for o in s["objections"])
assert all("การันตี" not in (s["opener"] + s["close"]) for _ in [0])
print("script: เปิดสายตามเหตุ · ข้อจริง · ราคา + เพดานส่วนลด · ข้อโต้แย้ง · ลิงก์จ่าย OK")

admin = "admin@test.local"; db.create_tenant(admin, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Op", is_admin=True)
cl = TestClient(app); cl.post("/login", data={"email": admin, "password": "pw123456"})
r = cl.get("/admin/calls"); assert r.status_code == 200 and "a.co.th" in r.text and "ยังไม่ได้ชำระ" in r.text
r = cl.get(f"/admin/calls/{a}"); assert "081-234-5678" in r.text and "เปิดสาย" in r.text and "ถ้าเขาบอกว่า" in r.text
assert json.loads(db.get_lead(a)["call_json"])["phone"] == "0812345678"
r = cl.post(f"/admin/calls/{a}/log", data={"outcome": "will_pay", "note": "จะโอนพรุ่งนี้"}); assert "ส่งลิงก์ชำระเงิน" in r.text
assert sent and sent[-1][0] == "info@a.co.th" and "/q/" in sent[-1][2] and "/u/" in sent[-1][2]
assert db.list_lead_events(a)[0]["kind"] == "call" and "a.co.th" not in [x["lead"]["domain"] for x in C.call_list()]   # รอ 2 วัน
assert "a.co.th" in [x["lead"]["domain"] for x in C.call_list(now=dt.datetime.now() + dt.timedelta(days=2, hours=1))]
cl.post(f"/admin/calls/{b}/log", data={"outcome": "no_answer"}); assert "b.co.th" in [x["lead"]["domain"] for x in C.call_list(now=dt.datetime.now() + dt.timedelta(days=1, hours=1))]
cl.post(f"/admin/calls/{c}/log", data={"outcome": "no", "note": "มีเอเจนซี่แล้ว"}); assert db.get_lead(c)["status"] == "lost"
cl.post(f"/admin/calls/{d}/log", data={"outcome": "", "phone": "081 111 2222"}); assert json.loads(db.get_lead(d)["call_json"])["phone"] == "0811112222"
r = cl.get(f"/admin/leads/{a}"); assert "/admin/calls/" in r.text and "โทรหาลูกค้า" in r.text
assert C.status_text().startswith("ควรโทรวันนี้")
print("log/admin: จะจ่ายส่งลิงก์ · นัดรอบถัดไปตามผล · ไม่สนใจปิดลีด · แก้เบอร์ · ปุ่มในหน้าลีด · สรุปเช้า OK")
print("ALL OK")
