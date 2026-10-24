"""ทดสอบฝ่ายบัญชี: เลขผู้เสียภาษี · บาทเป็นตัวอักษร · แยก VAT · ใบเสร็จงวดแรก (Stripe เก็บผู้ซื้อจาก Checkout / PromptPay) · ต่ออายุ Stripe ไม่ซ้ำ
· เปิด VAT = ใบเสร็จรับเงิน/ใบกำกับภาษี · ใบแจ้งหนี้รอบถัดไป (รายเดือน 7 วัน · รายปี 30 วัน) · แนบสลิป → อนุมัติ → ใบเสร็จ · หัก ณ ที่จ่าย
· เตือน 0/+3/+7 + แจ้งเจ้าของ +7 + เสนอระงับ +14 · ระงับ/คืนแผน · ยกเลิก/ออกใหม่ เลขไม่ซ้ำ · สรุป/CSV · หน้าเว็บ · แชต CEO · ออฟฟิศ/สรุปเช้า"""
import os, tempfile, json, datetime as dt
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke-acct-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["GEO_DEEP_AUDIT"] = "0"; os.environ["GEO_CS"] = "0"
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.accounting as A, app.pipeline as P, app.stripe_pay as SP, app.mailer as mailer, app.ai_client as ai
import app.site_health as sh, app.line_oa as LO, app.ceo_chat as C, app.office as O, app.execs as X, app.skills as SK
from app.main import app
db.init_db()
ai._read_setting = lambda k: None
ai._chat = lambda m, max_tokens=2048, timeout=120: (_ for _ in ()).throw(RuntimeError("no model"))
mails = []; mailer.config = lambda: {"host": "x"}
mailer.send = lambda to, subj, html, text="", attachments=(): mails.append({"to": to, "subj": subj, "text": text, "att": [a[0] for a in attachments]})
mailer.is_deliverable = lambda e: bool(e) and e.endswith(".co.th")
LO._post = lambda path, payload: True
alerts = []; P.alert_admins = lambda msg, link, type_="lead", subject=None: alerts.append(msg)
A.HOOKS["pdf"] = lambda html: b"%PDF-fake"
P.RUN_INLINE = True; C.RUN_INLINE = True
REPORT = {"checks": [{"key": "csr", "label": "csr", "status": "fail", "detail": ""}]}
sh.run_checks = lambda site, **kw: REPORT

def tax_id(prefix):
    s = sum(int(prefix[i]) * (13 - i) for i in range(12)); return prefix + str((11 - s % 11) % 10)
SELLER_TAX, BUYER_TAX = tax_id("010555512345"), tax_id("010556698765")

# ---- พื้นฐาน ----
assert A.valid_tax_id(SELLER_TAX) and not A.valid_tax_id(SELLER_TAX[:-1] + str((int(SELLER_TAX[-1]) + 1) % 10)) and not A.valid_tax_id("123")
assert A.bahttext(9900) == "เก้าพันเก้าร้อยบาทถ้วน" and A.bahttext(98604) == "เก้าหมื่นแปดพันหกร้อยสี่บาทถ้วน"
assert A.bahttext(21.5) == "ยี่สิบเอ็ดบาทห้าสิบสตางค์" and A.bahttext(1_000_011) == "หนึ่งล้านสิบเอ็ดบาทถ้วน" and A.bahttext(101) == "หนึ่งร้อยเอ็ดบาทถ้วน"
assert A.split(9900, True) == (9252.34, 647.66) and A.split(9900, False) == (9900.0, 0.0) and A.wht_of(9252.34) == 277.57
assert A.add_months(dt.date(2026, 1, 31), 1) == dt.date(2026, 2, 28) and A.add_months(dt.date(2026, 10, 23), 12) == dt.date(2027, 10, 23)
print("basics: เลขผู้เสียภาษี · บาทเป็นตัวอักษร · VAT 7/107 · หัก ณ ที่จ่าย · บวกเดือน OK")

def lead_q(dom, cycle="month", setup=True, disc=0):
    lid = db.add_lead(f"https://{dom}", dom, source="prospect", email=f"owner@{dom}")
    db.update_lead(lid, audit_json=json.dumps(REPORT), score=60)
    q = P.create_quote(db.get_lead(lid), "pro", disc, setup, created_by="admin", cycle=cycle)
    db.update_quote(q["id"], status="accepted")
    return lid, db.get_quote(q["id"])

# ---- งวดแรกผ่าน Stripe: เก็บผู้ซื้อจาก Checkout → ใบเสร็จ ----
lid1, q1 = lead_q("card.co.th")
sess = {"id": "cs_1", "payment_status": "paid", "customer": "cus_1", "subscription": "sub_1", "metadata": {"quote_id": str(q1["id"])},
        "customer_details": {"name": "บริษัท การ์ด จำกัด", "email": "owner@card.co.th", "address": {"line1": "1 ถนนสุขุมวิท", "city": "กรุงเทพฯ", "postal_code": "10110"},
                             "tax_ids": [{"type": "th_vat", "value": BUYER_TAX}]}}
assert SP.handle_event({"type": "checkout.session.completed", "data": {"object": sess}}) == "paid"
r1 = db.list_acct_docs("receipt", lead_id=lid1)
assert len(r1) == 1 and r1[0]["number"].startswith(f"RC-{dt.date.today().year + 543}-0001") and r1[0]["source"] == "stripe"
assert r1[0]["total"] == 19800 and r1[0]["vat"] == 0 and r1[0]["buyer_name"] == "บริษัท การ์ด จำกัด" and r1[0]["buyer_tax_id"] == BUYER_TAX and "สุขุมวิท" in r1[0]["buyer_address"]
assert len(json.loads(r1[0]["items_json"])) == 2 and A.title(r1[0]) == "ใบเสร็จรับเงิน"
m = [x for x in mails if r1[0]["number"] in x["subj"]]; assert m and m[0]["to"] == "owner@card.co.th" and m[0]["att"] == [f"{r1[0]['number']}.pdf"]
# ต่ออายุ Stripe → ใบเสร็จของเรา · ส่งซ้ำไม่ออกซ้ำ
ts = int(dt.datetime(2026, 11, 23).timestamp()); te = int(dt.datetime(2026, 12, 23).timestamp())
inv = {"id": "in_2", "subscription": "sub_1", "amount_paid": 990000, "billing_reason": "subscription_cycle", "lines": {"data": [{"period": {"start": ts, "end": te}}]}}
SP.handle_event({"type": "invoice.paid", "data": {"object": inv}}); SP.handle_event({"type": "invoice.paid", "data": {"object": inv}})
r1 = db.list_acct_docs("receipt", lead_id=lid1); assert len(r1) == 2 and r1[0]["stripe_ref"] == "in_2" and r1[0]["total"] == 9900 and r1[0]["period_start"] == "2026-11-23"
SP.handle_event({"type": "invoice.paid", "data": {"object": dict(inv, id="in_1", billing_reason="subscription_create")}})
assert len(db.list_acct_docs("receipt", lead_id=lid1)) == 2                                  # บิลแรกออกตอน checkout แล้ว
assert not A.customers() or all(q["paid_via"] != "stripe" for q in A.customers())             # ลูกค้าบัตรไม่ต้องออกใบแจ้งหนี้
print("stripe: ใบเสร็จงวดแรก (ผู้ซื้อจาก Checkout) + แนบ PDF · ต่ออายุออกใบของเรา ไม่ซ้ำ · บิลแรกไม่ซ้ำ OK")

# ---- เปิด VAT → ใบเสร็จรับเงิน/ใบกำกับภาษี ----
admin = "admin@test.local"; db.create_tenant(admin, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Op", is_admin=True)
a = TestClient(app); a.post("/login", data={"email": admin, "password": "pw123456"})
r = a.post("/admin/settings/company", data={"name": "บริษัท เจอเอไอ จำกัด", "address": "99 ถนนพระราม 9", "tax_id": "", "vat": "1"}); assert "ต้องใส่เลขประจำตัวผู้เสียภาษี" in r.text
r = a.post("/admin/settings/company", data={"name": "x", "tax_id": "1234567890123"}); assert "ไม่ถูกต้อง" in r.text
r = a.post("/admin/settings/company", data={"name": "บริษัท เจอเอไอ จำกัด", "address": "99 ถนนพระราม 9", "tax_id": SELLER_TAX, "vat": "1", "branch": ""})
assert r.status_code == 200 and A.vat_on() and A.seller()["branch"] == "สำนักงานใหญ่"
print("settings: เปิด VAT ต้องมีเลขผู้เสียภาษีที่ถูกต้อง · สาขาค่าเริ่มต้น OK")

# ---- งวดแรก PromptPay → ใบกำกับภาษี · รอบถัดไป ----
lid2, q2 = lead_q("bank.co.th", setup=False)
A.save_buyer(q2["id"], "หจก. แบงก์", BUYER_TAX, "00001", "2 ถนนสีลม")
db.update_quote(q2["id"], status="claimed"); q2 = db.get_quote(q2["id"]); P.confirm_payment(q2, "owner")
q2 = db.get_quote(q2["id"]); paid = dt.date.fromisoformat(q2["paid_at"][:10])
rc2 = db.list_acct_docs("receipt", lead_id=lid2)[0]
assert rc2["vat_mode"] == "vat7" and rc2["vat"] == 693.0 and rc2["subtotal"] == 9900.0 and rc2["total"] == 10593.0 and rc2["source"] == "promptpay" and rc2["buyer_branch"] == "00001"
import app.quotes as QQ
assert QQ.vat_pct(q2) == 7 and QQ.payable(q2) == 10593.0 and QQ.vat_pct(db.get_quote(q1["id"])) == 0     # ราคาไม่รวม VAT · ใบก่อนเปิด VAT คงราคาเดิม
assert "+ VAT 7% = 10,593" in QQ.summary_text(q2, "bank.co.th") and any("ยังไม่รวมภาษีมูลค่าเพิ่ม" in t for t in QQ.terms_for(q2))
assert "รวมเป็น 10,593 บาท" in json.dumps(QQ.contract_sections({"name": "x"}, {"name": "y"}, q2), ensure_ascii=False)
assert A.title(rc2) == "ใบเสร็จรับเงิน/ใบกำกับภาษี" and rc2["number"].endswith("0003")
page = a.get(f"/doc/{rc2['token']}").text
for s_ in ("ใบเสร็จรับเงิน/ใบกำกับภาษี", "ต้นฉบับ", SELLER_TAX, BUYER_TAX, "693.00", "9,900.00", "10,593.00", "หนึ่งหมื่นห้าร้อยเก้าสิบสามบาทถ้วน", "ไม่รวมภาษีมูลค่าเพิ่ม", "หจก. แบงก์", "สำนักงานใหญ่"):
    assert s_ in page, s_
assert a.get(f"/doc/{rc2['token']}.pdf").content == b"%PDF-fake" and a.get(f"/bill/{rc2['token']}").status_code in (200, 303)
assert len(A.customers()) == 1 and A.customers()[0]["id"] == q2["id"]
mails.clear()
assert A.run(dt.datetime.combine(A.add_months(paid, 1) - dt.timedelta(days=8), dt.time(8)))["invoiced"] == []     # ยังไม่ถึง 7 วันก่อนรอบ
res = A.run(dt.datetime.combine(A.add_months(paid, 1) - dt.timedelta(days=7), dt.time(8))); assert len(res["invoiced"]) == 1, res
inv2 = db.list_acct_docs("invoice", lead_id=lid2)[0]
assert inv2["number"].startswith(f"INV-{dt.date.today().year + 543}-0001") and inv2["total"] == 10593.0 and inv2["due_date"] == A.add_months(paid, 1).isoformat() and inv2["status"] == "open"
assert any("ใบแจ้งหนี้" in x["subj"] and "/bill/" in x["text"] for x in mails)
assert A.run(dt.datetime.combine(A.add_months(paid, 1) - dt.timedelta(days=6), dt.time(8)))["invoiced"] == []     # ไม่ออกซ้ำ
bill = TestClient(app).get(f"/bill/{inv2['token']}").text; assert "แจ้งชำระเงินแล้ว" in bill and "ไม่ใช่ใบกำกับภาษี" in bill and "หักภาษี ณ ที่จ่าย 3%" in bill
print("promptpay: งวดแรกได้ใบกำกับภาษี (VAT แยก · ผู้ซื้อ/สาขา) · ใบแจ้งหนี้ล่วงหน้า 7 วัน ไม่ซ้ำ · หน้า /bill มี QR+สลิป OK")

# ---- แนบสลิป → CFO เสนอ → อนุมัติ → ใบเสร็จ ----
TestClient(app).post(f"/bill/{inv2['token']}/pay", files={"slip": ("s.jpg", b"img", "image/jpeg")})
inv2 = db.get_acct_doc(inv2["id"]); assert inv2["status"] == "claimed" and inv2["slip_path"]
dec = [d for d in db.list_decisions("proposed", 50) if json.loads(d["payload_json"]).get("acct_doc_id") == inv2["id"]]; assert dec and dec[0]["kind"] == "payment"
out = P.execute_decision(dec[0], "owner"); assert "ออกใบเสร็จ" in out, out
inv2 = db.get_acct_doc(inv2["id"]); rc = db.find_acct_doc("receipt", invoice_id=inv2["id"])
assert inv2["status"] == "paid" and rc and rc["total"] == 10593.0 and rc["period_start"] == inv2["period_start"] and db.get_decision(dec[0]["id"])["status"] == "executed"
assert A.mark_paid(inv2) == "ใบนี้ชำระแล้ว"
print("claim: แนบสลิป → ข้อเสนอยืนยันยอด → อนุมัติ → ใบเสร็จอ้างอิงใบแจ้งหนี้ · ยืนยันซ้ำไม่ได้ OK")

# ---- เตือน 0/+3/+7 · แจ้งเจ้าของ · เสนอระงับ · ระงับ/คืนแผน · หัก ณ ที่จ่าย ----
due3 = A.add_months(paid, 2)
A.run(dt.datetime.combine(due3 - dt.timedelta(days=7), dt.time(8))); inv3 = db.list_acct_docs("invoice", lead_id=lid2)[0]; assert inv3["due_date"] == due3.isoformat()
mails.clear(); alerts.clear()
day = lambda n: dt.datetime.combine(due3 + dt.timedelta(days=n), dt.time(8))
assert A.run(day(-1))["actions"] == {}
assert A.run(day(0))["actions"][inv3["number"]] == ["remind:1"] and any("ครบกำหนด" in x["subj"] for x in mails)
assert A.run(day(1))["actions"] == {}
assert A.run(day(3))["actions"][inv3["number"]] == ["remind:2"]
assert A.run(day(7))["actions"][inv3["number"]] == ["remind:3", "owner_alert"] and any("ค้างชำระ" in x for x in alerts)
assert A.run(day(8))["actions"] == {}
assert A.run(day(14))["actions"][inv3["number"]] == ["ask_suspend"] and any("ระงับ" in x for x in alerts)
assert A.run(day(20))["actions"] == {}
ap = a.get("/admin/accounting").text; assert "เตือนแล้ว 3 ครั้ง" in ap and "ระงับบริการ" in ap and inv3["number"] in ap
a.post(f"/admin/acct/{inv3['id']}/suspend"); assert db.get_tenant(q2["tenant_id"])["plan"] == "free" and A.customers() == []
r = a.post(f"/admin/acct/{inv3['id']}/paid", data={"wht": "1"}); assert "หัก ณ ที่จ่าย 297.00" in r.text, r.text[:300]
assert db.get_tenant(q2["tenant_id"])["plan"] == "pro"
rc3 = db.find_acct_doc("receipt", invoice_id=inv3["id"]); assert rc3["wht"] == 297.0
p3 = a.get(f"/doc/{rc3['token']}").text; assert "หักภาษี ณ ที่จ่าย 3%" in p3 and "10,296.00" in p3
print("collect: เตือน 0/+3/+7 ครั้งละฉบับ · แจ้งเจ้าของ +7 · เสนอระงับ +14 ครั้งเดียว · ระงับ/คืนแผน · หัก ณ ที่จ่ายในใบเสร็จ OK")

# ---- รายปี PromptPay: ใบแจ้งหนี้ล่วงหน้า 30 วัน ยอดทั้งปี ----
lid4, q4 = lead_q("year.co.th", cycle="year")
db.update_quote(q4["id"], status="claimed"); P.confirm_payment(db.get_quote(q4["id"]), "owner"); q4 = db.get_quote(q4["id"])
rc4 = db.list_acct_docs("receipt", lead_id=lid4)[0]; assert rc4["total"] == 105506.28 and rc4["subtotal"] == 98604.0 and "รายปี" in rc4["description"] and "ฟรี" in rc4["items_json"]
p4 = dt.date.fromisoformat(q4["paid_at"][:10])
assert A.due_invoices(q4, A.add_months(p4, 12) - dt.timedelta(days=31)) == [] and A.due_invoices(q4, A.add_months(p4, 12) - dt.timedelta(days=30)) == [A.add_months(p4, 12)]
inv4 = A.issue_invoice(q4, A.add_months(p4, 12)); assert inv4["total"] == 105506.28 and inv4["period_end"] == (A.add_months(p4, 24) - dt.timedelta(days=1)).isoformat()
print("yearly: ใบเสร็จทั้งปี (Setup ฟรี) · ใบแจ้งหนี้ล่วงหน้า 30 วัน ยอดทั้งปี OK")

# ---- ยกเลิก / ออกใหม่ ----
assert A.void(rc2, "") == "ใส่เหตุผลการยกเลิก"
A.save_buyer(q2["id"], "หจก. แบงก์ (แก้ชื่อ)", BUYER_TAX, "00001", "2 ถนนสีลม")
out = A.reissue(db.get_acct_doc(rc2["id"])); assert "แทน" in out
old, new = db.get_acct_doc(rc2["id"]), db.list_acct_docs("receipt", lead_id=lid2)[0]
assert old["status"] == "void" and new["buyer_name"] == "หจก. แบงก์ (แก้ชื่อ)" and new["number"] != old["number"] and new["total"] == old["total"]
nums = [d["number"] for d in db.list_acct_docs("receipt", limit=100)]; assert len(nums) == len(set(nums))
assert A.save_buyer(q2["id"], "x", "1111111111111") .startswith("เลขประจำตัว")
r = a.post(f"/admin/acct/{inv4['id']}/void", data={"reason": "ลูกค้าเปลี่ยนเป็นบัตร"}); assert db.get_acct_doc(inv4["id"])["status"] == "void"
print("void/reissue: ต้องมีเหตุผล · ออกใหม่ใช้ข้อมูลผู้ซื้อล่าสุด เลขใหม่ ไม่ซ้ำ · ยกเลิกใบแจ้งหนี้ OK")

# ---- สรุป · CSV · หน้าเว็บ · แชต · ออฟฟิศ ----
sm = A.summary(); assert sm["receipts"] >= 5 and sm["wht"] == 297.0 and sm["vat"] > 0
csv_ = a.get(f"/admin/accounting.csv?month={dt.date.today():%Y-%m}"); assert csv_.status_code == 200 and csv_.text.startswith("﻿") and rc3["number"] in csv_.text and "297.00" in csv_.text
assert "attachment" in csv_.headers["content-disposition"]
assert "บัญชี: เดือนนี้รับ" in A.status_text()
lid5, q5 = lead_q("form.co.th"); db.update_quote(q5["id"], status="sent")
c = TestClient(app); assert "ข้อมูลออกใบเสร็จ/ใบกำกับภาษี" in c.get(f"/q/{q5['token']}").text
r = c.post(f"/q/{q5['token']}/accept", data={"name": "คุณฟอร์ม", "agree": "1", "buyer_name": "บจก. ฟอร์ม", "buyer_tax_id": "1234567890123"}); assert "ไม่ถูกต้อง" in r.text
c.post(f"/q/{q5['token']}/accept", data={"name": "คุณฟอร์ม", "agree": "1", "buyer_name": "บจก. ฟอร์ม", "buyer_tax_id": BUYER_TAX, "buyer_branch": "0", "buyer_address": "3 ถนนพหลโยธิน"})
q5 = db.get_quote(q5["id"]); assert q5["status"] == "accepted" and q5["buyer_name"] == "บจก. ฟอร์ม" and q5["buyer_branch"] == "สำนักงานใหญ่"
lp = a.get(f"/admin/leads/{lid2}").text; assert "ข้อมูลออกเอกสาร" in lp and new["number"] in lp
r = a.post(f"/admin/leads/{lid2}/buyer", data={"quote_id": q2["id"], "name": "หจก. แบงก์", "tax_id": BUYER_TAX}); assert db.get_quote(q2["id"])["buyer_name"] == "หจก. แบงก์"
pick = {}; C.LLM = lambda m_: json.dumps(pick)
assert "บัญชีเดือนนี้" in C.handle("ค้างชำระกี่ราย") and "ค้างรับ" in C.handle("สรุปบัญชี")
assert "acct" in O.IDS and any(s_[0] == "acct" for s_ in O.SCHEDULE) and "acct" in SK.AGENTS and SK.load("acct").startswith("# ทักษะ")
assert a.get("/admin/office/state.json").status_code == 200
print("report/web: สรุปเดือน · CSV (BOM) · ฟอร์มผู้ซื้อตอนยืนยัน · หน้าลีด · แชต CEO \"ค้างชำระ\" · ออฟฟิศ/ทักษะ OK")

# ---- ราคาไม่รวม VAT: Stripe บวก VAT · หน้าใบเสนอราคาแสดง VAT ----
import app.stripe_pay as SP2
calls2 = []
SP2.save_config("sk_test_abcdefghijklmnop", "whsec_x")
SP2._get_json = lambda path: {"data": []}
def _fp(path, data, idem=None):
    calls2.append((path, data))
    if path == "/products": return {"id": "prod_v"}
    if path == "/prices": return {"id": "price_v", "product": "prod_v"}
    if path == "/tax_rates": return {"id": "txr_7"}
    if path == "/checkout/sessions": return {"id": "cs_v", "url": "https://checkout.stripe.com/x"}
    raise RuntimeError(path)
SP2._post = _fp
lv, qv = lead_q("vatcard.co.th", setup=True)
assert QQ.vat_pct(qv) == 7 and QQ.payable(qv) == 21186.0
SP2.create_checkout(qv, db.get_lead(lv), "https://geo.example")
tr = [d for p_, d in calls2 if p_ == "/tax_rates"][0]; assert tr["percentage"] == "7" and tr["inclusive"] == "false" and tr["country"] == "TH"
ss = [d for p_, d in calls2 if p_ == "/checkout/sessions"][0]; assert ss["line_items[0][tax_rates][0]"] == "txr_7" and ss["line_items[1][tax_rates][0]"] == "txr_7"
assert db.get_setting("stripe_taxrate_7") == "txr_7"
db.update_quote(qv["id"], status="sent"); pg = TestClient(app).get(f"/q/{qv['token']}").text
assert "ภาษีมูลค่าเพิ่ม 7%" in pg and "21,186" in pg and "(ก่อน VAT)" in pg
db.update_quote(qv["id"], status="accepted"); pg = TestClient(app).get(f"/q/{qv['token']}").text; assert "21,186 บาท (รวม VAT)" in pg
print("vat-exclusive: ใบใหม่บวก VAT 7% · Stripe ใส่ tax rate ทุกบรรทัด · หน้าใบเสนอราคาแสดง ก่อน VAT/VAT/รวม OK")
print("\naccounting_smoke: ผ่านทั้งหมด")
