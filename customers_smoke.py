"""ทดสอบหน้า "ลูกค้า" ของเจ้าของ (/admin/customers): KPI (MRR/ARR รายเดือน · รายปี ÷ 12 · ราคาตั้งเอง) · ครบรอบใน 30 วัน · ค้างชำระ
· เรียงคนที่ต้องดูแลก่อน · สัญญาณเสี่ยง · ใช้เอง/ทีมดูแล · ตัวกรอง · CSV (ไม่มีลิงก์โทเค็น) · บัญชีฟรี/แอดมินไม่นับ · คนที่ไม่ใช่แอดมินถูกส่งกลับ"""
import os, tempfile, json, datetime as dt
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke-cust-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["GEO_DEEP_AUDIT"] = "0"
os.environ["GEO_CS"] = "0"
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.crm_customers as C, app.accounting as A
from app.main import app
db.init_db()

now = dt.datetime.now()
today = now.date()
iso = lambda d: d.isoformat(timespec="seconds")
pw = bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode()
admin = "admin@example.com"; db.create_tenant(admin, pw, "Owner", is_admin=True)
a = TestClient(app); a.post("/login", data={"email": admin, "password": "pw123456"})

# ---- ยังไม่มีลูกค้า → หน้าว่าง ----
r = a.get("/admin/customers"); assert r.status_code == 200 and "ยังไม่มีลูกค้าที่จ่ายเงิน" in r.text, r.text[:300]
assert C.context()["total"] == 0
print("empty: ยังไม่มีลูกค้า → หน้าว่างพร้อมทางไปหน้าลีด OK")

seq = [0]
def customer(name, dom, plan, paid_days, cycle="month", period=None, net=None, custom=None, custom_year=None, vat=0, via="promptpay", package="pro", old=None):
    tid = db.create_tenant(f"owner@{dom}", pw, name); db.set_plan(tid, plan)
    bid = db.create_brand(tid, name, dom, "retail")
    lid = db.add_lead(f"https://{dom}", dom, source="prospect", email=f"owner@{dom}")
    if old:                                                     # ใบเก่าของลีดเดียวกัน (ก่อนอัปเกรด) — ต้องไม่นับซ้ำ
        oq = db.add_quote(lid, "Q-TEST-OLD", "tok-old", package, old, 0, old, False, 0, old, "2099-01-01", period_total=old)
        db.update_quote(oq, status="paid", paid_at=iso(now - dt.timedelta(days=400)), tenant_id=tid, brand_id=bid)
    seq[0] += 1
    net = net if net is not None else (period if cycle == "month" else round(period / 12))
    qid = db.add_quote(lid, f"Q-TEST-{seq[0]:03d}", f"tok-{dom}", package, net, 0, net, False, 0, period, "2099-01-01",
                       cycle=cycle, period_total=period, vat_pct=vat)
    f = {"status": "paid", "paid_at": iso(now - dt.timedelta(days=paid_days)), "tenant_id": tid, "brand_id": bid, "paid_via": via}
    if custom is not None:
        f.update(custom_price=custom, custom_year=custom_year, custom_label="ดูแล GEO เต็มรูปแบบ")
    db.update_quote(qid, **f)
    return tid, bid, lid, qid

def installed(bid):
    db.add_health_check(bid, {"checked_at": iso(now), "ok": True, "n_fail": 0, "n_warn": 1,
                              "checks": [{"key": "connector", "label": "ตัวเชื่อม", "status": "ok", "detail": "ติดตั้งแล้ว"}]})

def content(bid):
    db.create_content_item(bid, None, "th", "บทความ", "t", "d", "body", "[]", "test")

def vis(bid, days_ago, hits, asked=5):
    rows = [{"engine": "chatgpt", "status": "named" if i < hits else "none", "named": i < hits, "cited": False} for i in range(asked)]
    db.add_ai_visibility(bid, {"checked_at": iso(now - dt.timedelta(days=days_ago)), "asked": asked, "cited": 0, "named": hits, "rate": 0, "rows": rows})

def run(bid, days_ago, sov):
    with db.get_conn() as cx:
        cx.execute(db.q("INSERT INTO monitoring_runs(brand_id,started_at,finished_at,status,questions_total,brand_hits,share_of_voice) VALUES(?,?,?,?,?,?,?)"),
                   (bid, iso(now - dt.timedelta(days=days_ago)), iso(now - dt.timedelta(days=days_ago)), "done", 10, 1, sov))

# A: Pro รายเดือน 9,900 + VAT 7% · ใบแจ้งหนี้เลยกำหนด 9 วัน · ยังไม่ติดตั้ง/ไม่มีคอนเทนต์ (ครบ 40 วัน) · ทีมดูแล
tA, bA, lA, qA = customer("Alpha Shop", "alpha.example.com", "pro", 40, period=9900, vat=7)
db.add_acct_doc(kind="invoice", number="INV-TEST-001", token="inv-tok-a", quote_id=qA, lead_id=lA, tenant_id=tA,
                period_start=(today - dt.timedelta(days=9)).isoformat(), due_date=(today - dt.timedelta(days=9)).isoformat(),
                subtotal=9900, vat=693, total=10593, status="open", source="promptpay", description="ค่าบริการ")
# B: Agency รายปี 288,840 (ครบรอบใน ~20 วัน) · ใบรายเดือนเก่าของลีดเดียวกัน = อัปเกรดแล้ว (ไม่นับซ้ำ) · ใช้เอง · SoV 10% → 25%
tB, bB, lB, qB = customer("Bravo Agency", "bravo.example.com", "agency", 345, cycle="year", period=288840, package="agency", old=29000)
installed(bB); content(bB); run(bB, 30, 0.10); run(bB, 0, 0.25); vis(bB, 14, 1); vis(bB, 0, 3)
# C: ราคาตั้งเอง รายเดือน 15,000 · Stripe · ตั้งเป็นทีมดูแล (แผน Agency ปกติใช้เอง)
tC, bC, lC, qC = customer("Charlie Clinic", "charlie.example.com", "agency", 50, period=15000, custom=15000, via="stripe", package="agency")
db.set_tenant_self_serve(tC, False); installed(bC); content(bC)
# D: ราคาตั้งเอง รายปี 200,000 (MRR = 16,666.67) · ผลไม่ขยับ 4 สัปดาห์
tD, bD, lD, qD = customer("Delta Hotel", "delta.example.com", "agency", 100, cycle="year", period=200000, custom=20000, custom_year=200000, package="agency")
installed(bD); content(bD)
for w in range(4):
    vis(bD, 7 * w, 0)
# E: เคยจ่าย แต่กลับเป็นแผนฟรี → โชว์ + สัญญาณ แต่ไม่นับรายได้
tE, bE, lE, qE = customer("Echo Store", "echo.example.com", "free", 60, period=9900)
installed(bE); content(bE)
# F: แผน Pro ที่เปิดให้ด้วยมือ ไม่มีใบเสนอราคา → ไม่นับใน MRR แจ้งแยก
tF = db.create_tenant("owner@foxtrot.example.com", pw, "Foxtrot Co"); db.set_plan(tF, "pro"); db.create_brand(tF, "Foxtrot Co", "foxtrot.example.com")
# G: แผนฟรีไม่เคยจ่าย · H: แอดมินแผน Pro → ไม่นับทั้งคู่
tG = db.create_tenant("owner@golf.example.com", pw, "Golf Free"); db.create_brand(tG, "Golf Free", "golf.example.com")
tH = db.create_tenant("ops@example.com", pw, "Ops", is_admin=True); db.set_plan(tH, "pro")

rs = C.rows()
names = [r["name"] for r in rs]
assert "Golf Free" not in names and "Ops" not in names and "Owner" not in names, names
assert len(rs) == 6, names
by = {r["name"]: r for r in rs}
# ---- MRR / ARR ----
assert by["Alpha Shop"]["mrr"] == 9900 and by["Bravo Agency"]["mrr"] == 288840 / 12 == 24070
assert by["Charlie Clinic"]["mrr"] == 15000 and abs(by["Delta Hotel"]["mrr"] - 200000 / 12) < 1e-9
assert by["Echo Store"]["mrr"] == 0 and not by["Echo Store"]["active"] and by["Foxtrot Co"]["mrr"] == 0
assert by["Bravo Agency"]["n_quotes"] == 1                                    # ใบเก่าของลีดเดียวกันไม่นับซ้ำ
assert by["Charlie Clinic"]["plan_label"] == "ราคาตั้งเอง" and by["Charlie Clinic"]["plan_key"] == "custom" and by["Alpha Shop"]["plan_label"] == "Pro"
k = C.kpis(rs)
mrr = 9900 + 24070 + 15000 + 200000 / 12
assert k["active"] == 5 and k["inactive"] == 1, k
assert k["mrr"] == round(mrr, 2) == 65636.67 and k["arr"] == round(mrr * 12, 2) == 787640.0, k
assert k["mrr_vat"] == 693.0, k                                               # VAT แยก: 7% ของ Alpha เท่านั้น
assert k["noquote_n"] == 1 and k["noquote_list"] == 9900, k
print(f"kpi: ลูกค้า {k['active']} · MRR {k['mrr']:,} (รายปี ÷ 12 · ราคาตั้งเอง) · ARR {k['arr']:,} · ไม่มีใบ {k['noquote_n']} OK")

# ---- ครบรอบใน 30 วัน · ค้างชำระ ----
assert 15 <= by["Bravo Agency"]["next_in"] <= 25 and by["Bravo Agency"]["next_date"] == A.add_months((now - dt.timedelta(days=345)).date(), 12)
assert by["Delta Hotel"]["next_in"] > 200 and by["Foxtrot Co"]["next_date"] is None and by["Echo Store"]["next_date"] is None
ren = sorted(r["name"] for r in rs if r["active"] and r["next_in"] is not None and r["next_in"] <= 30)
assert ren == ["Alpha Shop", "Bravo Agency", "Charlie Clinic"], ren
assert k["renew_n"] == 3 and k["renew_amount"] == 9900 + 288840 + 15000 and k["renew_vat"] == 693.0 and k["renew_year"] == 1, k
assert k["overdue_n"] == 1 and k["overdue_amount"] == 10593, k
assert by["Alpha Shop"]["pay"]["key"] == "overdue" and by["Alpha Shop"]["pay"]["overdue_days"] == 9 and "ค้างชำระ 9 วัน" in by["Alpha Shop"]["pay"]["label"]
assert by["Charlie Clinic"]["pay"]["label"] == "ตัดบัตรอัตโนมัติ" and by["Bravo Agency"]["pay"]["key"] == "paid"
print(f"renewal/overdue: ครบรอบใน 30 วัน {k['renew_n']} ราย {k['renew_amount']:,} + VAT {k['renew_vat']} · ค้าง {k['overdue_n']} ใบ {k['overdue_amount']:,} OK")

# ---- สัญญาณเสี่ยง · ผลงาน · ใช้เอง/ทีมดูแล ----
fl = {r["name"]: [f["key"] for f in r["flags"]] for r in rs}
assert fl["Alpha Shop"] == ["overdue", "install", "content"], fl
assert fl["Echo Store"] == ["free"] and fl["Delta Hotel"] == ["flat"], fl
assert fl["Bravo Agency"] == [] and fl["Charlie Clinic"] == [] and fl["Foxtrot Co"] == [], fl
assert by["Bravo Agency"]["sov"] == {"pct": 25.0, "delta": 15.0, "at": today.isoformat()}, by["Bravo Agency"]["sov"]
assert by["Bravo Agency"]["ai"]["first"] == 1 and by["Bravo Agency"]["ai"]["last"] == 3 and by["Bravo Agency"]["ai"]["up"]
assert by["Bravo Agency"]["health"]["ok"] and by["Alpha Shop"]["health"] is None
assert by["Bravo Agency"]["self_serve"] and not by["Charlie Clinic"]["self_serve"] and not by["Alpha Shop"]["self_serve"]
assert by["Alpha Shop"]["lead_id"] == lA and by["Foxtrot Co"]["lead_id"] is None
# เรียง: คนที่ต้องดูแลก่อน (ค้างชำระ > กลับเป็นฟรี > ผลไม่ขยับ) แล้วตามวันครบรอบ
assert names == ["Alpha Shop", "Echo Store", "Delta Hotel", "Charlie Clinic", "Bravo Agency", "Foxtrot Co"], names
print("flags/sort: ค้างชำระ+ยังไม่ติดตั้ง+ไม่มีคอนเทนต์ · กลับเป็นฟรี · ผลไม่ขยับ · เรียงต้องดูแลก่อน · SoV +15 จุด · AI 1→3 · ใช้เอง/ทีมดูแล OK")

# ---- ตัวกรอง ----
f = lambda **kw: [r["name"] for r in C.apply_filters(rs, **kw)]
assert f(q="delta.example") == ["Delta Hotel"] and f(q="BRAVO") == ["Bravo Agency"] and f(q="owner@charlie") == ["Charlie Clinic"]
assert f(plan="custom") == ["Delta Hotel", "Charlie Clinic"] and f(plan="pro") == ["Alpha Shop", "Foxtrot Co"]
assert f(attention=True) == ["Alpha Shop", "Echo Store", "Delta Hotel"]
assert f(renewing=True) == ["Alpha Shop", "Charlie Clinic", "Bravo Agency"]
assert f(plan="custom", renewing=True) == ["Charlie Clinic"]
print("filters: ค้นหา · แผน · ต้องดูแล · ครบรอบเร็ว OK")

# ---- หน้าเว็บ ----
r = a.get("/admin/customers"); assert r.status_code == 200
t = r.text
for s in ("Alpha Shop", "Bravo Agency", "ราคาตั้งเอง", "65,637", "787,640", "ค้างชำระ 9 วัน", "10,593", "ทีมดูแล", "ใช้เอง", "กลับเป็นแผนฟรี",
          "ผลไม่ขยับ 4 สัปดาห์", "+15.0", "1→3", f"/admin/leads/{lA}", f"/brands/{bA}/report", "/bill/inv-tok-a", "ครบรอบบิลใน 30 วัน", "VAT 693"):
    assert s in t, s
assert "Golf Free" not in t and "ops@example.com" not in t
assert t.index("Alpha Shop") < t.index("Echo Store") < t.index("Delta Hotel") < t.index("Bravo Agency")
r = a.get("/admin/customers?attention=1"); assert "Delta Hotel" in r.text and "Bravo Agency" not in r.text
r = a.get("/admin/customers?plan=custom&q=charlie"); assert "Charlie Clinic" in r.text and "Delta Hotel" not in r.text.split("<tbody>")[1]
r = a.get("/admin/customers?q=nomatch"); assert "ไม่มีบัญชีที่ตรงกับตัวกรองนี้" in r.text
print("page: KPI · แถว · ลิงก์ · ตัวกรองผ่าน URL · ไม่มีบัญชีฟรี/แอดมิน OK")

# ---- CSV ----
r = a.get("/admin/customers.csv"); assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
body = r.text
assert body.startswith("﻿") and body.splitlines()[0].lstrip("﻿").split(",")[:3] == ["บัญชี", "อีเมลบัญชี", "แบรนด์"]
lines = body.strip().splitlines(); assert len(lines) == 7, len(lines)
assert "16666.67" in body and "24070.00" in body and "ค้างชำระ 9 วัน" in body and "ราคาตั้งเอง (ดูแล GEO เต็มรูปแบบ)" in body
assert "tok-" not in body and "inv-tok" not in body and "/q/" not in body and "password" not in body.lower()
r = a.get("/admin/customers.csv?attention=1"); assert len(r.text.strip().splitlines()) == 4
print("csv: คอลัมน์เดียวกับหน้า · BOM · ตัวกรอง · ไม่มีโทเค็น OK")

# ---- Stripe ตัดบัตรไม่ผ่าน → ค้างชำระ ----
db.add_lead_event(lC, "note", "Stripe: การสมัครอยู่ในสถานะ past_due (ค้างชำระ) — Smart Retries กำลังลองเก็บเงินใหม่", "stripe")
rs2 = C.rows(); c2 = next(r for r in rs2 if r["name"] == "Charlie Clinic")
assert [x["key"] for x in c2["flags"]] == ["overdue"] and c2["pay"]["label"] == "ตัดบัตรไม่ผ่าน (Stripe)" and C.kpis(rs2)["stripe_overdue"] == 1
db.add_lead_event(lC, "paid", "ต่ออายุผ่าน Stripe 15,000 บาท", "stripe")
assert not next(r for r in C.rows() if r["name"] == "Charlie Clinic")["flags"]
print("stripe: past_due → ค้างชำระ · จ่ายแล้วหาย OK")

# ---- ไม่ใช่แอดมิน ----
cu = TestClient(app); cu.post("/login", data={"email": "owner@foxtrot.example.com", "password": "pw123456"})
r = cu.get("/admin/customers", follow_redirects=False); assert r.status_code in (302, 303) and r.headers["location"].endswith("/app"), r.headers
r = cu.get("/admin/customers.csv", follow_redirects=False); assert r.status_code in (302, 303) and "csv" not in r.headers.get("content-type", "")
r = TestClient(app).get("/admin/customers", follow_redirects=False); assert r.headers["location"].endswith("/login")
print("auth: ลูกค้า → /app · ไม่ล็อกอิน → /login OK")
print("customers_smoke: ALL OK")
