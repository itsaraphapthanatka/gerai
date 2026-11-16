"""ทดสอบ stage 09: Today + Customer 360 + รายงานทดลองจับคู่ตัวตน (flag customer360)
บทบาท: เจ้าของ · ฝ่ายขายที่ได้รับมอบหมาย/ไม่ได้รับ · การตลาด · บัญชี · ดูอย่างเดียว · พันธมิตร · ลูกค้า (คนละบัญชี)
· ผู้ติดต่อ 0/หลายคน · มอบหมายแบบ compare-and-set · dry-run ไม่เขียนข้อมูล · flag ปิด = 404
ไม่ยิงเครือข่าย · DB ชั่วคราว"""
import os, re, json, tempfile, datetime as dt
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
import app.c360 as C
import app.plan_request as PR
import app.pipeline as pipeline
from app.main import app

db.init_db()
pipeline.alert_admins = lambda *a, **kw: None
PW = bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode()


def staff_user(email, role):
    tid = db.create_tenant(email, PW, email.split("@")[0], is_admin=True)
    db.set_staff(tid, role)
    cl = TestClient(app)
    assert cl.post("/login", data={"email": email, "password": "pw123456"}).status_code == 200
    return tid, cl


def csrf_of(html):
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


own_id, owner = staff_user("owner@t.test", "owner")
sa_id, sales_a = staff_user("sa@t.test", "sales")
sb_id, sales_b = staff_user("sb@t.test", "sales")
mk_id, mkt = staff_user("mk@t.test", "marketing")
fn_id, fin = staff_user("fn@t.test", "finance")
vw_id, viewer = staff_user("vw@t.test", "viewer")


def mk_lead(dom, **kw):
    lid = db.add_lead(f"https://{dom}", dom, source="audit", ip="1.2.3.4")
    db.update_lead(lid, token="TK" + dom.split(".")[0].upper()[:10], audit_json=json.dumps({"ok": True, "checks": []}), audit_at=db.now(), **kw)
    return lid


def plan(lid, ch="phone", val="0812345678", name="คุณเอ"):
    v = {"name": name, "role": "ผู้จัดการ", "contact_channel": ch, "contact_value": val, "desired_window": "", "web_readiness": "",
         "budget": "", "message": "", "idem_key": f"{lid:032d}", "marketing": False}
    return PR.submit(db.get_lead(lid), v)[0]


la = mk_lead("alpha.co.th")                  # มอบหมายให้ sales A
lb = mk_lead("beta.co.th")                   # ยังไม่มอบหมาย
lc = mk_lead("gamma.co.th")                  # ไม่มีผู้ติดต่อ ไม่มีคำขอ
plan(la); plan(lb, "email", "b@beta.co.th")
db.add_lead_contact(la, name="คุณบี", role="IT", phone="0899999999", email="it@alpha.co.th")
db.add_lead_contact(la, name="คุณซี", role="การตลาด", email="mk@alpha.co.th")
with db.get_conn() as c:
    c.execute(db.q("UPDATE leads SET owner_staff_id=? WHERE id=?"), (sa_id, la))
db.update_lead(lb, next_action="โทรติดตาม", next_at=(dt.datetime.now() - dt.timedelta(hours=2)).isoformat(timespec="seconds"))
db.update_lead(lc, unsub_at=db.now())
# ลูกค้าที่จ่ายเงิน + พันธมิตร + ข้อมูลชนกันสำหรับ dry-run
cust = db.create_tenant("cust@alpha.co.th", PW, "Alpha Co", is_admin=False)
with db.get_conn() as c:
    c.execute(db.q("UPDATE tenants SET plan='pro' WHERE id=?"), (cust,))
db.create_brand(cust, "Alpha", "https://www.alpha.co.th")
cust2 = db.create_tenant("other@x.test", PW, "Other", is_admin=False)
db.create_brand(cust2, "Alpha dup", "alpha.co.th")
pid = db.add_partner(name="Agency One", domain="beta.co.th", status="active", code="agency1")
db.update_lead(la, partner_id=pid)
dup = mk_lead("alpha.co.th")                                                            # ลีดซ้ำโดเมนเดียวกัน
db.update_lead(dup, email="b@beta.co.th")                                               # อีเมลเดียวกับลีดอีกโดเมน

# ---------- flag ปิด = 404 ----------
assert owner.get("/admin/today").status_code == 404 and owner.get("/admin/c360/alpha.co.th").status_code == 404
assert owner.get("/admin/staff/identity").status_code == 404
F.set_enabled("customer360", True)

# ---------- Today: เจ้าของเห็นทุกส่วน ----------
html = owner.get("/admin/today").text
for sec in ("sec-requests", "sec-meetings", "sec-overdue", "sec-context", "sec-approvals", "sec-blocked", "sec-suppression"):
    assert sec in html, sec
secs = {s["key"]: s for s in C.today(db.get_tenant(own_id))}
req_leads = {i["domain"] for i in secs["requests"]["items"]}
assert req_leads == {"alpha.co.th", "beta.co.th"}
it = secs["requests"]["items"][0]
assert it["reason"] and it["due"] and it["next"] and it["href"].startswith("/admin/leads/") and it["source"].startswith("plan_requests:")
assert {i["owner"] for i in secs["requests"]["items"]} == {"sa", "ยังไม่มอบหมาย (ฝ่ายขาย)"}
assert [i["domain"] for i in secs["overdue"]["items"]] == ["beta.co.th"] and secs["overdue"]["overdue"] == 1
assert len(secs["context"]["items"]) == 2                                               # ยังไม่ยืนยันข้อมูลธุรกิจ
assert [i["domain"] for i in secs["suppression"]["items"]] == ["gamma.co.th"]
assert secs["approvals"]["items"] == [] and "ไม่มีรายการในตอนนี้" in html                 # ว่างอย่างซื่อตรง

# ---------- ฝ่ายขาย: เห็นเฉพาะของตัวเอง + ยังไม่มอบหมาย ----------
sa = {s["key"]: s for s in C.today(db.get_tenant(sa_id))}
sb = {s["key"]: s for s in C.today(db.get_tenant(sb_id))}
assert {i["domain"] for i in sa["requests"]["items"]} == {"alpha.co.th", "beta.co.th"}
assert {i["domain"] for i in sb["requests"]["items"]} == {"beta.co.th"}                 # ไม่เห็นลีดที่มอบหมายให้ A
assert "approvals" not in sa and "blocked" not in sa                                    # ฝ่ายขายไม่มีหมวดเอเจนต์
v = C.view("alpha.co.th", db.get_tenant(sb_id))
assert all(x["lead"]["id"] != la for x in v["leads"]) and v["hidden_leads"] == 1          # ลีดของ A ถูกซ่อน (ลีดซ้ำที่ยังไม่มอบหมายเห็นได้)
html = sales_b.get("/admin/c360/alpha.co.th").text
assert "0899999999" not in html and f"ลีด #{la}" not in html and "มอบหมายให้คนอื่น" in html
html = sales_a.get("/admin/c360/alpha.co.th").text
assert "0899999999" in html and "คุณซี" in html and "Sales Brief" in html                # ผู้ติดต่อหลายคน
assert PW not in html and "password_hash" not in html and "Ua" not in html  # ไม่มีรหัสผ่าน (hash) หรือ LINE user id

# ---------- มอบหมาย (CSRF + compare-and-set) ----------
page = sales_b.get("/admin/c360/beta.co.th").text
cs = csrf_of(page)
assert sales_b.post("/admin/c360/beta.co.th/assign", data={"lead_id": lb, "owner": "me", "expected": 0}).status_code == 403
r = sales_b.post("/admin/c360/beta.co.th/assign", data={"csrf_token": cs, "lead_id": lb, "owner": "me", "expected": 0}, follow_redirects=False)
assert r.status_code in (302, 303) and db.get_lead(lb)["owner_staff_id"] == sb_id
r = sales_a.post("/admin/c360/beta.co.th/assign", data={"csrf_token": csrf_of(sales_a.get("/admin/c360/alpha.co.th").text), "lead_id": lb, "owner": "me",
                                                         "expected": 0}, follow_redirects=False)
assert db.get_lead(lb)["owner_staff_id"] == sb_id                                       # A เห็นเจ้าของเก่า (0) → ไม่ทับ
assert sales_a.get("/admin/c360/beta.co.th").status_code == 404                          # ตอนนี้เป็นของ B แล้ว
r = sales_b.post("/admin/c360/beta.co.th/assign", data={"csrf_token": cs, "lead_id": lb, "owner": str(own_id), "expected": sb_id},
                 follow_redirects=False)
assert "มอบหมายแบบนี้ไม่ได้" in r.headers["location"] or "%" in r.headers["location"]
assert db.get_lead(lb)["owner_staff_id"] == sb_id                                       # ฝ่ายขายยกให้คนอื่นไม่ได้
assert C.assign(lb, own_id, sb_id, "owner") and db.get_lead(lb)["owner_staff_id"] == own_id
assert any("มอบหมายลีด" in (e["note"] or "") for e in db.list_lead_events(lb))
assert not C.assign(lb, sa_id, sb_id, "x")                                               # ค่า expected เก่า = ไม่ทับ
assert db.get_lead(lb)["owner_staff_id"] == own_id
cross = sales_a.post("/admin/c360/alpha.co.th/assign", data={"csrf_token": csrf_of(sales_a.get("/admin/c360/alpha.co.th").text), "lead_id": lb,
                                                              "owner": "me", "expected": own_id}, follow_redirects=False)
assert db.get_lead(lb)["owner_staff_id"] == own_id                                      # ส่ง lead_id ของโดเมนอื่นมา = ไม่ทำ

# ---------- การตลาด: ไม่มีหมวดขาย ----------
mk = {s["key"]: s for s in C.today(db.get_tenant(mk_id))}
assert list(mk) == ["approvals", "blocked", "suppression"] and "requests" not in mk and mk["suppression"]["items"][0]["owner"] == "ฝ่ายการตลาด"
assert mk["suppression"]["items"][0]["href"] == "/admin/marketing"
html = mkt.get("/admin/c360/alpha.co.th").text                                         # เห็นลูกค้า (หมวดลูกค้า) แต่ไม่เห็นลีด/ช่องทาง
assert "Alpha" in html and "0899999999" not in html and "ลีด #" not in html
assert mkt.post("/admin/c360/alpha.co.th/assign", data={"lead_id": la, "owner": "me"},
                follow_redirects=False).status_code in (302, 303, 403)
assert db.get_lead(la)["owner_staff_id"] == sa_id
assert mkt.get("/admin/staff/identity", follow_redirects=False).status_code in (302, 303, 403)

# ---------- บัญชี: เห็นการเงิน ไม่เห็นลีด ----------
fv = C.view("alpha.co.th", db.get_tenant(fn_id))
assert fv is not None and fv["leads"] == [] and fv["brands"]                             # หมวดลูกค้าดูได้ แต่ไม่มีหมวดขาย
assert C.view("gamma.co.th", db.get_tenant(fn_id)) is None

# ---------- ดูอย่างเดียว: เห็น แต่แก้ไม่ได้ ----------
html = viewer.get("/admin/c360/alpha.co.th").text
assert "0899999999" in html and 'action="/admin/c360/alpha.co.th/assign"' not in html
r = viewer.post("/admin/c360/alpha.co.th/assign", data={"lead_id": la, "owner": "me", "expected": sa_id},
                follow_redirects=False)
assert r.status_code in (302, 303, 403) and db.get_lead(la)["owner_staff_id"] == sa_id

# ---------- ลูกค้า (ไม่ใช่ทีมงาน) และพันธมิตร ----------
cu = TestClient(app)
assert cu.post("/login", data={"email": "cust@alpha.co.th", "password": "pw123456"}).status_code == 200
for p in ("/admin/today", "/admin/c360/alpha.co.th", "/admin/staff/identity"):
    r = cu.get(p, follow_redirects=False)
    assert r.status_code in (302, 303, 403, 404) and "0899999999" not in r.text, p
partner_page = TestClient(app).get(f"/partner/{db.get_partner(pid)['token']}")
assert "0899999999" not in partner_page.text and "it@alpha.co.th" not in partner_page.text
assert TestClient(app).get("/admin/c360/alpha.co.th", follow_redirects=False).status_code in (302, 303)

# ---------- ผู้ติดต่อ 0 คน ----------
v = C.view("gamma.co.th", db.get_tenant(own_id))
assert v["leads"][0]["people"] == [] and v["leads"][0]["prefs"]["unsub_at"]
assert "ยังไม่มีผู้ติดต่อ" in owner.get("/admin/c360/gamma.co.th").text
assert owner.get("/admin/c360/nothing-here.co.th").status_code == 404

# ---------- dry-run: ไม่เขียนข้อมูล ----------
def snapshot():
    with db.get_conn() as c:
        return {t: c.execute(db.q(f"SELECT COUNT(*) AS n FROM {t}")).fetchone()["n"] for t in ("leads", "brands", "tenants", "partners", "lead_contacts")}


before = snapshot()
r = C.identity_report()
assert snapshot() == before and r["writes"] == 0
kinds = r["by_kind"]
assert kinds.get("dup_leads") == 1 and kinds.get("multi_tenant") == 1 and kinds.get("partner_is_customer") == 1
assert kinds.get("domain_variants") >= 1 and kinds.get("shared_email") == 1
html = owner.get("/admin/staff/identity").text
assert "dry-run" in html and "ลีดซ้ำโดเมนเดียวกัน" in html and "ไม่มีการรวม" in html
assert sales_a.get("/admin/staff/identity", follow_redirects=False).status_code in (302, 303, 403)

# ---------- ลิงก์หลังบ้านเดิมยังใช้ได้ ----------
assert owner.get(f"/admin/leads/{la}").status_code == 200
F.set_enabled("customer360", False)
assert owner.get("/admin/today").status_code == 404
print("c360_smoke OK")
