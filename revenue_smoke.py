"""ทดสอบ stage 17: รายได้ ที่มา ต้นทุน (flag revenue_v2) — ข้อมูลตั้งต้นที่รู้ยอดรวมล่วงหน้า
· คืนเงิน · งานที่ล้ม (งบที่ปล่อยคืนไม่นับ) · ครั้งเดียว vs รายรอบ · หลายผู้ติดต่อในบัญชีเดียว · พันธมิตรปนค้นหา · ข้อมูลขาด
· ไม่นับซ้ำ · สิทธิ์ดูการเงินตามบทบาท · ส่งออกไม่มีข้อมูลส่วนตัว · DB ชั่วคราว ไม่ส่งอะไรจริง"""
import os, re, json, tempfile, datetime as dt
os.environ.pop("DATABASE_URL", None)
os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["AI_BASE_URL"] = "http://127.0.0.1:9"
os.environ["GEO_DEEP_AUDIT"] = "0"; os.environ["GEO_CS"] = "0"
for k in list(os.environ):
    if k.startswith("GEO_FLAG_"):
        os.environ.pop(k)
import bcrypt
from fastapi.testclient import TestClient
import app.db as db
import app.flags as F
import app.revenue as R
import app.accounting as A
import app.jobs as J
import app.staff as ST
from app.main import app

db.init_db()
db.set_setting("acct_on", "1")
T = dt.date.today()
P0, P1 = T.replace(day=1), T


def doc(lead_id, items, total, subtotal, period_start, period_end, status="issued"):
    with db.get_conn() as c:
        rid = c.execute(db.q("INSERT INTO acct_docs(created_at, kind, number, token, lead_id, period_start, period_end, description, items_json, subtotal, vat, total, "
                             "status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id"),
                        (db.now(), "receipt", f"RC-{lead_id}-{total}-{status}", f"tok{lead_id}{total}{status}", lead_id, period_start, period_end,
                         items[0]["desc"], json.dumps(items, ensure_ascii=False), subtotal, round(total - subtotal, 2), total, status)).fetchone()["id"]
    return rid


def lead(dom, **kw):
    lid = db.add_lead(f"https://{dom}", dom, source="audit", ip="1.2.3.4")
    if kw:
        db.update_lead(lid, **kw)
    return lid


def ev(lid, name, source, when, utm=None):
    with db.get_conn() as c:
        c.execute(db.q("INSERT INTO analytics_events(created_at, name, origin, lead_id, source, utm_source, props_json) VALUES(?,?,?,?,?,?,?)"),
                  (when, name, "server", lid, source, utm, "{}"))


ps, pe = P0.isoformat(), (A.add_months(P0, 1) - dt.timedelta(days=1)).isoformat()
ys, ye = P0.isoformat(), (A.add_months(P0, 12) - dt.timedelta(days=1)).isoformat()
# บัญชี A: รายเดือน 10,700 (ก่อน VAT 10,000) + Setup 5,350 (5,000) ในใบเดียว · ค้นหา (organic) → ขอแผน
a1 = lead("acme.co.th", audit_at=db.now())
ev(a1, "audit_completed", "organic", "2026-01-01T10:00:00"); ev(a1, "plan_requested", "organic", "2026-01-02T10:00:00")
rA = doc(a1, [{"desc": "ค่าบริการรายเดือน", "amount": 10700}, {"desc": "GEO Setup ครั้งเดียว", "amount": 5350}], 16050, 15000, ps, pe)
# บัญชี A ผู้ติดต่อคนที่สอง (www. โดเมนเดียวกัน) มาทางพันธมิตร ขอแผนทีหลัง — ไม่มีใบเสร็จของตัวเอง
a2 = lead("www.acme.co.th", partner_id=5)
ev(a2, "plan_requested", "partner", "2026-01-05T10:00:00")
# บัญชี B: รายปี 128,400 (120,000) · พันธมิตร
b1 = lead("beta.co.th", partner_id=7, audit_at=db.now())
ev(b1, "plan_requested", "partner", "2026-01-03T10:00:00")
rB = doc(b1, [{"desc": "ค่าบริการรายปี", "amount": 128400}], 128400, 120000, ys, ye)
# บัญชี C: รายเดือน 10,700 แล้วคืนเงินเต็ม
c1 = lead("gamma.co.th")
rC = doc(c1, [{"desc": "ค่าบริการรายเดือน", "amount": 10700}], 10700, 10000, ps, pe)
# บัญชี D: ใบที่ถูกยกเลิก (ออกใหม่แทน) ไม่นับ · ไม่มีเหตุการณ์ใด ๆ (ข้อมูลขาด)
d1 = lead("delta.co.th")
doc(d1, [{"desc": "ค่าบริการรายเดือน", "amount": 10700}], 10700, 10000, ps, pe, status="void")
rD = doc(d1, [{"desc": "ค่าบริการรายเดือน", "amount": 10700}], 10700, 10000, ps, pe)

F.set_enabled("revenue_v2", True)
assert R.record_adjustment("refund", 10700, ref="ch_1", lead_id=c1, receipt_id=rC, by="stripe")
assert R.record_adjustment("refund", 10700, ref="ch_1", lead_id=c1, receipt_id=rC, by="stripe") is None      # webhook ซ้ำไม่นับซ้ำ

fin = R.finance(P0, P1)
assert fin["receipts"] == 4 and fin["accounts"] == 4                                                       # ใบยกเลิกไม่นับ · A สองผู้ติดต่อ = บัญชีเดียว
assert fin["one_time"] == 5000 and fin["recurring"] == 10000 + 120000 + 10000 + 10000
assert fin["refunds"] == 10000 and fin["cash_net"] == 5000 + 150000 - 10000
assert fin["mrr"] == 10000 + 10000 + 10000                                                                  # A + B(120,000/12) + D · C คืนเต็ม ไม่นับ · Setup ไม่นับ
assert R.DEF_VERSION == fin["def_version"] == "rev-v1"

# ---------- funnel ต่อบัญชี ----------
fu = R.funnel()
assert fu["accounts"] == 4 and fu["counts"]["won"] == 4 and fu["counts"]["audit_complete"] == 2

# ---------- ที่มา: หลายมุม ไม่บวกข้ามมุม ----------
at = R.attribution_report(P0, P1)
assert at["won_accounts"] == 4
for view in at["views"].values():
    assert sum(v["accounts"] for v in view.values()) == 4                                                   # ต่อมุม = จำนวนบัญชีพอดี
    assert round(sum(v["net"] for v in view.values()), 2) == 155000                                        # เงินต่อมุมไม่เกินยอดจริง
acme = next(r for r in at["rows"] if r["account"].startswith("acme"))
assert acme["first"] == "organic" and acme["last_meaningful"] == "partner" and acme["partner"] is True     # ผู้ติดต่อคนที่สองมาทางพันธมิตร
assert at["views"]["first"]["unknown"]["accounts"] == 2                                                     # C, D ไม่มีข้อมูล = ไม่ทราบ (ไม่ใช่ AI/เข้าตรง)
assert "direct" not in at["views"]["first"] and any("ไม่ได้แปลว่ามาจาก AI" in g for g in at["gaps"])
pub = R.public_summary(P0, P1)
assert set(pub) == set(R.PUBLIC_KEYS)
blob = json.dumps(pub, ensure_ascii=False)
assert "acme" not in blob and "@" not in blob and "155000" not in blob and "net" not in blob

# ---------- ต้นทุน: งานที่ล้ม (ปล่อยงบคืน) ไม่นับ · ข้อมูลไม่ครบ = ไม่บอกกำไร ----------
period = T.strftime("%Y-%m")
r_ok = J.reserve("ai_usd", 1.2); J.settle(r_ok, 0.9)
r_fail = J.reserve("ai_usd", 1.2); J.release(r_fail)                                                        # งานล้ม
J.reserve("ai_usd", 1.2)                                                                                    # ยังจองอยู่ (ไม่ใช่ค่าใช้จ่ายจริง)
cs = R.costs(period)
assert cs["by_alloc"]["acquisition"]["api_usd"] == 0.9 and cs["reserved_usd"] == 1.2
assert cs["by_alloc"]["acquisition"]["api_thb"] is None and any("fx_usd_thb" in m for m in cs["missing"])
mg = R.margin(period)
assert mg["available"] is False and mg["why"]                                                              # ไม่แต่งกำไร
db.set_setting("fx_usd_thb", "36")
R.add_cost("labor", "delivery", 20000, period=period, basis="estimate", note="ประมาณ 40 ชม.")
R.add_cost("tools", "acquisition", 1500, period=period)
mg = R.margin(period)
assert mg["available"] and mg["includes_estimates"] and mg["delivery_cost"] == 20000 and mg["gross"] == 145000 - 20000
assert mg["acquisition_cost_separate"]["actual_thb"] == 1500 and mg["acquisition_cost_separate"]["api_thb"] == round(0.9 * 36, 2)
try:
    R.add_cost("labor", "delivery", -1, period=period); assert False
except ValueError:
    pass

# ---------- สิทธิ์ดูการเงินตามบทบาท ----------
own = db.create_tenant("own@rv.test", bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Own", is_admin=True)
adm = TestClient(app)
assert adm.post("/login", data={"email": "own@rv.test", "password": "pw123456"}).status_code == 200
pg = adm.get("/admin/revenue")
assert pg.status_code == 200 and "rev-v1" in pg.text and "MRR" in pg.text and "ไม่ได้แปลว่ามาจาก AI" in pg.text
assert ST.required(("GET", "/admin/revenue"))[0] == "finance" if hasattr(ST, "required") else True
ok_roles = [k for k, (_, perms) in ST.ROLES.items() if perms.get("finance") in ("view", "edit")]
assert "marketing" not in ok_roles and "sales" not in ok_roles
cs_tok = re.search(r'name="csrf_token" value="([^"]+)"', pg.text).group(1)
assert adm.post("/admin/revenue/cost", data={"category": "api", "alloc": "delivery", "amount_thb": "100", "period": period}).status_code == 403
adm.post("/admin/revenue/cost", data={"csrf_token": cs_tok, "category": "processing", "alloc": "delivery", "amount_thb": "300", "period": period, "basis": "actual"})
assert any(e["category"] == "processing" for e in R.costs(period)["entries"])
F.set_enabled("revenue_v2", False)
assert adm.get("/admin/revenue").status_code == 404
print("revenue_smoke OK")
