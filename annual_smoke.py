"""ทดสอบรายปี: ลด 17% จาก 12 เดือน (ส่วนลดพิเศษซ้อนได้) · GEO Setup ฟรี · คืนเงินเฉพาะ 7 วันแรก · CFO ไม่นับ 17% เป็นส่วนลดของดีล
· Stripe ราคารายปี (interval year) ไม่มีบรรทัด Setup · หน้าใบเสนอราคา/สัญญา/ฟอร์มแอดมิน · CEO เสนอรายปีเมื่อลูกค้าเอ่ยถึง
· บทโทร · แจ้งต่ออายุล่วงหน้า 30 วัน · รายเดือนเดิมไม่เปลี่ยน"""
import os, tempfile, json, re, datetime as dt
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke-annual-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["GEO_DEEP_AUDIT"] = "0"; os.environ["GEO_CS"] = "1"
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.quotes as Q, app.pipeline as P, app.execs as X, app.stripe_pay as SP, app.ceo_chat as C, app.closer as CL
import app.success as S, app.mailer as mailer, app.ai_client as ai, app.site_health as sh, app.line_oa as LO
from app.main import app
db.init_db()
ai._read_setting = lambda k: None
ai._chat = lambda m, max_tokens=2048, timeout=120: (_ for _ in ()).throw(RuntimeError("no model"))
mails = []; mailer.config = lambda: {"host": "x"}; mailer.send = lambda to, subj, html, text=None, attachments=(): mails.append((to, subj, text))
mailer.is_deliverable = lambda e: bool(e) and e.endswith(".co.th")
LO._post = lambda path, payload: True
P.RUN_INLINE = True; C.RUN_INLINE = True; S.RUN_INLINE = True; S.HOOKS["autopilot"] = lambda bid: None; S.HOOKS["health"] = lambda bid: None
REPORT = {"checks": [{"key": k, "label": f"ข้อ {k}", "status": "fail", "detail": ""} for k in ("csr", "noindex", "robots")]}
sh.run_checks = lambda site, **kw: REPORT

# ---- ราคา ----
m = Q.price_for("pro", 0, True)
assert m["cycle"] == "month" and m["net"] == 9900 and m["setup_price"] == 9900 and m["first_total"] == 19800 and m["period_total"] == 9900
y = Q.price_for("pro", 0, True, "year")
assert y["cycle"] == "year" and y["period_total"] == 98604 and y["first_total"] == 98604 and y["net"] == 8217 and y["setup_price"] == 0 and y["setup_list"] == 9900
y5 = Q.price_for("pro", 5, False, "year"); assert y5["period_total"] == round(118800 * 0.83 * 0.95) and y5["discount_pct"] == 5
try:
    Q.price_for("pro", 0, False, "week"); raise SystemExit("ต้องปฏิเสธรอบบิลแปลก")
except ValueError:
    pass
print("price: รายปี 118,800 → 98,604 (ลด 17%) · Setup ฟรี · ส่วนลดพิเศษซ้อน · รายเดือนเดิม OK")

# ---- CFO ----
assert X.MANDATE["yearly_discount_pct"] == 17
c1 = X.cfo_check({"price": 9900, "discount_pct": 0, "cycle": "year"}); assert c1["ok"] and "รายปี 98,604" in c1["note"], c1
assert X.cfo_check({"price": 9900, "discount_pct": 10, "cycle": "year"})["ok"]
assert not X.cfo_check({"price": 9900, "discount_pct": 12, "cycle": "year"})["ok"]                  # ส่วนลดพิเศษยังติดเพดาน 10%
X.set_mandate("yearly_discount_pct", 20); assert Q.price_for("pro", 0, False, "year")["period_total"] == 95040; X.set_mandate("yearly_discount_pct", 17)
print("cfo: 17% ไม่กินเพดานส่วนลด · พิเศษเกิน 10% ส่งเจ้าของ · ปรับ % รายปีได้ที่กรอบอำนาจ OK")

# ---- ใบเสนอราคา + เงื่อนไข + สัญญา ----
lid = db.add_lead("https://year.co.th", "year.co.th", source="prospect", email="owner@year.co.th")
db.update_lead(lid, audit_json=json.dumps(REPORT), score=60)
lead = db.get_lead(lid)
q = P.create_quote(lead, "pro", 0, True, created_by="admin", cycle="year")
assert q["cycle"] == "year" and q["period_total"] == 98604 and q["first_total"] == 98604 and q["setup_price"] == 0 and q["setup"]
assert db.get_lead(lid)["monthly_thb"] == 8217
txt = Q.summary_text(q, "year.co.th"); assert "98,604 บาท/ปี" in txt and "ฟรีเมื่อจ่ายรายปี" in txt and "ครั้งเดียวทั้งปี" in txt, txt
terms = Q.terms_for(q); assert "รายปี" in terms[0] and "7 วัน" in terms[1] and "ไม่คืนเงินส่วนที่เหลือ" in terms[1]
sec = json.dumps(Q.contract_sections({"name": "เจอ.AI"}, {"name": "ลูกค้า"}, q), ensure_ascii=False)
assert "98,604 บาทต่อปี" in sec and "หนึ่งปี" in sec and "ไม่คืนค่าบริการส่วนที่เหลือของปี" in sec and "ปีสัญญาปัจจุบัน" in sec and "สามเดือน" not in sec
qm = P.create_quote(lead, "pro", 0, True, created_by="admin")
assert qm["cycle"] == "month" and Q.terms_for(qm) == Q.TERMS and "บาท/เดือน" in Q.summary_text(qm, "year.co.th")
sm = json.dumps(Q.contract_sections({"name": "เจอ.AI"}, {"name": "ลูกค้า"}, qm), ensure_ascii=False); assert "บาทต่อเดือน" in sm and "สามเดือน" in sm
db.update_quote(qm["id"], status="cancelled")
print("quote: เก็บรอบบิล · สรุป · เงื่อนไข · สัญญารายปี (ต่อปี · คืนเงิน 7 วัน · จำกัดความรับผิดต่อปี) · รายเดือนเดิม OK")

# ---- หน้าเว็บ ----
c = TestClient(app)
P.send_quote(q, "admin"); q = db.get_quote(q["id"])
r = c.get(f"/q/{q['token']}"); assert r.status_code == 200
for s in ("รายปี", "ส่วนลดจ่ายรายปี 17%", "118,800", "ฟรี", "ยอดชำระครั้งเดียวทั้งปี", "98,604", "8,217"):
    assert s in r.text, s
assert P.accept_quote(q, "คุณปี", "1.1.1.1") == ""
r = c.get(f"/q/{q['token']}/contract"); assert r.status_code == 200 and "98,604 บาทต่อปี" in r.text
admin = "admin@test.local"; db.create_tenant(admin, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Op", is_admin=True)
a = TestClient(app); a.post("/login", data={"email": admin, "password": "pw123456"})
r = a.get(f"/admin/leads/{lid}"); assert 'name="cycle"' in r.text and "รายปี (ลด 17%" in r.text and "รายปี</span>" in r.text
lid2 = db.add_lead("https://y2.co.th", "y2.co.th", source="prospect", email="a@y2.co.th"); db.update_lead(lid2, audit_json=json.dumps(REPORT))
r = a.post(f"/admin/leads/{lid2}/quote", data={"package": "agency", "discount_pct": "0", "setup": "1", "cycle": "year"})
q2 = db.list_quotes(lid2)[0]; assert q2["cycle"] == "year" and q2["setup_price"] == 0 and "รายปี" in r.text
print("web: หน้าใบเสนอราคารายปี · สัญญา · ฟอร์มแอดมินเลือกรอบบิล OK")

# ---- Stripe รายปี ----
calls, store = [], {"prices": {}, "coupons": set()}
def fake_get(path):
    if path.startswith("/prices?lookup_keys[]="):
        key = re.search(r"lookup_keys\[\]=([^&]+)", path).group(1)
        return {"data": [store["prices"][key]] if key in store["prices"] else []}
    if path.startswith("/coupons/"):
        if path.split("/")[-1] in store["coupons"]:
            return {"id": path.split("/")[-1]}
        raise RuntimeError("No such coupon")
    raise RuntimeError(path)
def fake_post(path, data, idem=None):
    calls.append((path, data))
    if path == "/products": return {"id": f"prod_{len(calls)}"}
    if path == "/prices":
        pr = {"id": f"price_{len(calls)}", "product": data["product"]}; store["prices"][data["lookup_key"]] = pr; return pr
    if path == "/coupons": store["coupons"].add(data["id"]); return {"id": data["id"]}
    if path == "/checkout/sessions": return {"id": "cs_y", "url": "https://checkout.stripe.com/c/pay/cs_y"}
    raise RuntimeError(path)
SP.save_config("sk_test_abcdefghijklmnop", "whsec_x"); SP._get_json = fake_get; SP._post = fake_post
q = db.get_quote(q["id"]); url = SP.create_checkout(q, db.get_lead(lid), "https://geo.example")
pr = [d for p_, d in calls if p_ == "/prices"][0]; prod = [d for p_, d in calls if p_ == "/products"][0]
assert pr["lookup_key"] == "geo_pro_year_98604" and pr["unit_amount"] == "9860400" and pr["recurring[interval]"] == "year" and "รายปี" in prod["name"]
sess = [d for p_, d in calls if p_ == "/checkout/sessions"][0]
assert "line_items[1][price]" not in sess and "discounts[0][coupon]" not in sess and sess["mode"] == "subscription"   # Setup ฟรี · ไม่มีคูปอง
lid3 = db.add_lead("https://y3.co.th", "y3.co.th", source="prospect", email="a@y3.co.th"); db.update_lead(lid3, audit_json=json.dumps(REPORT))
q3 = P.create_quote(db.get_lead(lid3), "pro", 5, False, cycle="year"); SP.create_checkout(q3, db.get_lead(lid3), "https://geo.example")
s3 = [d for p_, d in calls if p_ == "/checkout/sessions"][-1]; pr3 = [d for p_, d in calls if p_ == "/prices"][-1]
assert "discounts[0][coupon]" not in s3 and pr3["lookup_key"] == f"geo_pro_year_{q3['period_total']}" and pr3["unit_amount"] == str(q3["period_total"] * 100)   # ส่วนลดพิเศษรวมในราคาตามใบ
X.set_mandate("yearly_discount_pct", 10)                                                          # เปลี่ยนส่วนลดรายปีทีหลัง
n0 = len(calls); SP.create_checkout(db.get_quote(q["id"]), db.get_lead(lid), "https://geo.example")
assert not [d for p_, d in calls[n0:] if p_ == "/prices"]                                        # ไม่สร้างราคาใหม่ให้ใบเดิม
assert [d for p_, d in calls[n0:] if p_ == "/checkout/sessions"][-1]["line_items[0][price]"] == store["prices"]["geo_pro_year_98604"]["id"]
assert Q.price_for("pro", 0, False, "year")["period_total"] == 106920                            # ใบใหม่ใช้ 10%
X.set_mandate("yearly_discount_pct", 17)
print("stripe: ราคารายปีตรงยอดในใบเสมอ (98,604 · ส่วนลดพิเศษรวมในราคา ไม่ใช้คูปอง) · เปลี่ยนส่วนลดรายปีทีหลัง ใบเดิมยังเก็บยอดเดิม · ไม่มี Setup OK")

# ---- CEO ----
lid4 = db.add_lead("https://ask.co.th", "ask.co.th", source="prospect", email="info@ask.co.th"); db.update_lead(lid4, audit_json=json.dumps(REPORT), score=60)
P.record(lid4, "replied", "ขอราคาแบบจ่ายรายปีได้ไหมครับ", "customer")
P.propose_quote(db.get_lead(lid4)); q4 = db.list_quotes(lid4)[0]; assert q4["cycle"] == "year", dict(q4)
d4 = [d for d in db.list_decisions(None, 50) if d["lead_id"] == lid4 and d["kind"] == "quote"][0]; assert "98,604 บาท/ปี" in json.dumps(dict(d4), ensure_ascii=False)
pick = {"action": "quote", "args": {"domain": "y2.co.th", "package": "pro"}}; C.LLM = lambda m: json.dumps(pick)
db.update_quote(q2["id"], status="cancelled")
r = C.handle("ส่งใบเสนอราคา y2.co.th แบบรายปี"); assert "รายปี" in r and "98,604" in r and "Setup ฟรี" in r, r
r = C.handle("ยืนยัน"); qq = db.list_quotes(lid2)[0]; assert qq["cycle"] == "year" and qq["status"] == "sent", r
print("ceo: ลูกค้าเอ่ยรายปี → เสนอรายปี · เจ้าของสั่ง \"รายปี\" ในแชต OK")

# ---- บทโทร ----
sc = CL.script(db.get_lead(lid4)); assert "รายปี 98,604" in sc["price"] and "7 วันแรก" in sc["price"], sc["price"]
assert any("รายปีลด 17%" in a_ for o_, a_ in sc["objections"])
print("closer: บทโทรบอกราคารายปี · ข้อโต้แย้งราคาเสนอรายปีก่อนลด OK")

# ---- ดูแลหลังการขาย: รายงานรายเดือน + แจ้งต่ออายุ ----
q = db.get_quote(q["id"]); P.confirm_payment(q, "test")
cu = next(x for x in S.customers() if x["lead"]["id"] == lid)
S.step(cu, cu["paid_at"] + dt.timedelta(hours=1))
mails.clear(); acts = S.step(cu, cu["paid_at"] + dt.timedelta(days=25, hours=1)); assert "report:1" in acts
assert any("รายงานเดือนที่ 1" in m_[1] and "รอบบิล" not in m_[1] for m_ in mails)
assert not any(a_.startswith("renewal") for a_ in S.step(cu, cu["paid_at"] + dt.timedelta(days=300)))
mails.clear(); acts = S.step(cu, cu["paid_at"] + dt.timedelta(days=336)); assert "renewal:1" in acts, acts
rn = [m_ for m_ in mails if "ต่ออายุ" in m_[1]]; assert rn and rn[0][0] == "owner@year.co.th" and "98,604" in rn[0][2] and "ตอบอีเมลนี้ก่อน" in rn[0][2]
assert not any(a_.startswith("renewal") for a_ in S.step(cu, cu["paid_at"] + dt.timedelta(days=340)))   # แจ้งครั้งเดียวต่อปี
assert "รายปี" in [e for e in db.list_lead_events(lid, 50) if e["kind"] == "won"][0]["note"]
print("cs: รายงานทุกเดือน · แจ้งต่ออายุล่วงหน้า 30 วัน ครั้งเดียวต่อปี OK")

# ---- เสนอรายปีก่อน (ค่าเริ่มต้น) · ลูกค้าสลับเองได้ก่อนยืนยัน ----
assert Q.default_cycle() == "year"
l6 = db.add_lead("https://first.co.th", "first.co.th", source="prospect", email="owner@first.co.th"); db.update_lead(l6, audit_json=json.dumps(REPORT), score=60)
P.record(l6, "replied", "สนใจครับ ขอใบเสนอราคา", "customer"); P.propose_quote(db.get_lead(l6)); q6 = db.list_quotes(l6)[0]
assert q6["cycle"] == "year" and q6["first_total"] == 98604                                     # ไม่ได้เอ่ยรอบบิล → รายปี
l7 = db.add_lead("https://monthly.co.th", "monthly.co.th", source="prospect", email="owner@monthly.co.th"); db.update_lead(l7, audit_json=json.dumps(REPORT), score=60)
P.record(l7, "replied", "ขอแบบจ่ายรายเดือนก่อนได้ไหม", "customer"); P.propose_quote(db.get_lead(l7)); assert db.list_quotes(l7)[0]["cycle"] == "month"
alt = Q.alternative(q6); assert alt["cycle"] == "month" and alt["net"] == 9900 and alt["setup_price"] == 9900 and alt["saving_year"] == 9900 * 12 + 9900 - 98604
assert "หรือเลือกจ่ายรายเดือน 9,900 บาท/เดือน" in Q.summary_text(q6, "first.co.th")
assert "หรือจ่ายรายปี 98,604 บาท" in Q.summary_text(db.list_quotes(l7)[0], "monthly.co.th")
P.send_quote(q6, "admin"); q6 = db.get_quote(q6["id"])
pg = c.get(f"/q/{q6['token']}").text; assert "อยากเริ่มแบบรายเดือนก่อน" in pg and "เปลี่ยนเป็นรายเดือน" in pg
c.post(f"/q/{q6['token']}/cycle", data={"cycle": "month"}); q6 = db.get_quote(q6["id"])
assert q6["cycle"] == "month" and q6["first_total"] == 19800 and q6["setup_price"] == 9900 and q6["net"] == 9900 and db.get_lead(l6)["monthly_thb"] == 9900
assert "จ่ายรายปีประหยัด" in c.get(f"/q/{q6['token']}").text
c.post(f"/q/{q6['token']}/cycle", data={"cycle": "year"}); q6 = db.get_quote(q6["id"]); assert q6["cycle"] == "year" and q6["first_total"] == 98604 and q6["setup_price"] == 0
assert P.accept_quote(q6, "คุณเฟิร์ส", "1.1.1.1") == ""
assert "เปลี่ยนรอบบิลไม่ได้" in P.change_cycle(db.get_quote(q6["id"]), "month") and "เปลี่ยนเป็นรายเดือน" not in c.get(f"/q/{q6['token']}").text
r = a.get(f"/admin/leads/{l7}").text; assert '<option value="year" selected>' in r
assert "ถ้าอยากเริ่มเบา ๆ มีรายเดือน 9,900" in CL.script(db.get_lead(l6))["price"]
db.set_setting("quote_default_cycle", "month"); assert Q.default_cycle() == "month"; db.set_setting("quote_default_cycle", "year")
print("yearly-first: เสนอรายปีเป็นค่าเริ่มต้น · ขอรายเดือน → รายเดือน · ทางเลือกอีกแบบในอีเมล/หน้าใบ · ลูกค้าสลับเองได้ก่อนยืนยัน · ยืนยันแล้วล็อก · บทโทร OK")
print("\nannual_smoke: ผ่านทั้งหมด")
