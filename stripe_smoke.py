"""ทดสอบ Stripe (ตามแผนจาก Stripe planner): ตั้งค่า/โหมด · ลายเซ็น webhook · แคตตาล็อก (สร้างครั้งแรก แล้วแคช) + คูปองส่วนลดเฉพาะแพ็กเกจ
· Checkout จากราคาในแคตตาล็อก + เก็บเลขผู้เสียภาษี · จ่ายสำเร็จ → เปิดบัญชี + ผูก customer · ใบแจ้งหนี้เก็บไว้โชว์ลูกค้า · ต่ออายุ/ค้างชำระ/ยกเลิก/คืนเงิน/ใบลดหนี้
· Customer Portal · ?paid=1 ถาม Stripe ตรง · ไม่ยิงเครือข่าย"""
import os, tempfile, json, time, re
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke4-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.stripe_pay as SP, app.pipeline as P, app.site_health as sh, app.ai_client as ai, app.mailer as mailer
from app.main import app

def rep(*fails, ok=10):
    checks = [{"key": k, "label": f"ข้อ {k}", "status": "fail", "detail": ""} for k in fails]
    return {"ok": not checks, "n_fail": len(checks), "n_ok": ok, "checks": checks + [{"key": "x", "label": "ผ่าน", "status": "ok"}] * ok}
db.init_db()
sh.run_checks = lambda site, **kw: rep("csr", "indexed")
ai._read_setting = lambda k: None
ai._chat = lambda m, max_tokens=2048, timeout=120: (_ for _ in ()).throw(RuntimeError("no model"))
sent = []; mailer.config = lambda: {"host": "x"}; mailer.send = lambda to, subject, html, text="", attachments=(): sent.append((to, subject, text))
c = TestClient(app)
WH = "whsec_testsecret_123456"

# ---- ตั้งค่า ----
assert not SP.available() and SP.status() == "ยังไม่มีคีย์" and SP.mode() == ""
SP.save_config("sk_test_abcdefghijklmnop", ""); assert SP.available() and SP.mode() == "test" and "ยังไม่มี webhook secret" in SP.status()
SP.save_config("", WH); assert SP.status() == "พร้อม (test)" and SP.public_config()["ready"]
assert SP.public_config()["key_mask"].startswith("sk_test") and "abcdefghijkl" not in SP.public_config()["key_mask"]
print("config: คีย์เข้ารหัส · โหมด · สถานะ OK")

# ---- ลายเซ็น ----
body = b'{"id":"evt_1","type":"ping"}'
assert SP.verify(body, SP.sign(body, WH)) and not SP.verify(body, SP.sign(body, "whsec_wrong")) and not SP.verify(body + b" ", SP.sign(body, WH))
assert not SP.verify(body, SP.sign(body, WH, ts=int(time.time()) - 900)) and not SP.verify(body, "") and not SP.verify(body, "garbage")
print("webhook: ลายเซ็นถูก/ผิด/เก่า OK")

# ---- Stripe API ปลอม: แคตตาล็อกว่าง → สร้าง · คูปองไม่มี → สร้าง · checkout/portal คืน URL ----
calls = []
store = {"prices": {}, "coupons": set()}
def fake_get(path):
    if path.startswith("/prices?lookup_keys[]="):
        key = re.search(r"lookup_keys\[\]=([^&]+)", path).group(1)
        return {"data": [store["prices"][key]] if key in store["prices"] else []}
    if path.startswith("/coupons/"):
        cid = path.split("/")[-1]
        if cid in store["coupons"]:
            return {"id": cid}
        raise RuntimeError("No such coupon")
    raise RuntimeError("unexpected GET " + path)
def fake_post(path, data, idem=None):
    calls.append((path, data, idem))
    if path == "/products": return {"id": f"prod_{len(calls)}"}
    if path == "/prices":
        pr = {"id": f"price_{len(calls)}", "product": data["product"]}; store["prices"][data["lookup_key"]] = pr; return pr
    if path == "/coupons": store["coupons"].add(data["id"]); return {"id": data["id"]}
    if path == "/checkout/sessions": return {"id": "cs_test_1", "url": "https://checkout.stripe.com/c/pay/cs_test_1"}
    if path == "/billing_portal/sessions": return {"url": "https://billing.stripe.com/p/session/xyz"}
    raise RuntimeError("unexpected POST " + path)
SP._get_json = fake_get; SP._post = fake_post

pro = SP.ensure_price("pro", "บริษัท ทดสอบ"); assert pro["price"].startswith("price_") and pro["product"].startswith("prod_")
assert [x[0] for x in calls] == ["/products", "/prices"] and calls[1][1]["lookup_key"] == "geo_pro_month_9900" and calls[1][1]["unit_amount"] == "990000" and calls[1][1]["recurring[interval]"] == "month"
assert SP.ensure_price("pro") == pro and len(calls) == 2                       # แคชแล้ว ไม่สร้างซ้ำ
setup = SP.ensure_price("setup"); assert "recurring[interval]" not in calls[-1][1] and calls[-1][1]["unit_amount"] == "990000"
cid = SP.ensure_coupon(10, pro["product"], "pro"); assert cid == "geo-disc-10-pro" and calls[-1][0] == "/coupons" and calls[-1][1]["applies_to[products][]"] == pro["product"] and calls[-1][1]["percent_off"] == "10"
assert SP.ensure_coupon(10, pro["product"], "pro") == cid and calls[-1][0] == "/coupons"   # มีแล้วไม่สร้างซ้ำ
print("catalog: สร้าง product/price ครั้งแรก · แคช · คูปองเฉพาะแพ็กเกจ OK")

# ---- ใบเสนอราคาที่ลูกค้ายืนยันแล้ว → Checkout จากแคตตาล็อก ----
lid = db.add_lead("https://pay.co.th", "pay.co.th", source="audit", email="owner@pay.co.th"); P.run_lead_audit(lid, "https://pay.co.th")
lead = db.get_lead(lid); q = P.create_quote(lead, "pro", 10, True); P.send_quote(q, "admin"); q = db.get_quote(q["id"])
assert P.accept_quote(q, "คุณซี", "1.2.3.4") == ""; q = db.get_quote(q["id"]); assert q["status"] == "accepted" and q["first_total"] == 18810
r = c.get(f"/q/{q['token']}"); assert "ชำระผ่าน Stripe" in r.text and "โอนผ่าน PromptPay" in r.text
n = len(calls); r = c.post(f"/q/{q['token']}/stripe", follow_redirects=False)
assert r.status_code == 303 and r.headers["location"] == "https://checkout.stripe.com/c/pay/cs_test_1"
sess_call = [x for x in calls[n:] if x[0] == "/checkout/sessions"][0]; prm = sess_call[1]
assert prm["mode"] == "subscription" and prm["line_items[0][price]"] == pro["price"] and prm["line_items[1][price]"] == setup["price"] and prm["discounts[0][coupon]"] == "geo-disc-10-pro"
assert prm["tax_id_collection[enabled]"] == "true" and prm["billing_address_collection"] == "required" and prm["customer_email"] == "owner@pay.co.th" and prm["success_url"].endswith(f"/q/{q['token']}?paid=1")
assert sess_call[2].startswith(f"geo-checkout-{q['id']}-") and db.get_quote(q["id"])["stripe_session_id"] == "cs_test_1"
print("checkout: ราคาแคตตาล็อก + คูปอง + Setup · เก็บเลขผู้เสียภาษี/ที่อยู่ · idempotency OK")

# ---- webhook: จ่ายสำเร็จ → เปิดบัญชี · ผูก customer · ใบแจ้งหนี้ ----
def event(typ, obj): return json.dumps({"id": "evt_x", "type": typ, "data": {"object": obj}}).encode()
def post(b): return c.post("/stripe/webhook", content=b, headers={"Stripe-Signature": SP.sign(b, WH)}).json()["result"]
sess = {"id": "cs_test_1", "object": "checkout.session", "payment_status": "paid", "customer": "cus_1", "subscription": "sub_1",
        "metadata": {"quote_id": str(q["id"]), "lead_id": str(lid)}, "client_reference_id": f"quote-{q['id']}"}
assert c.post("/stripe/webhook", content=event("checkout.session.completed", sess), headers={"Stripe-Signature": "t=1,v1=bad"}).status_code == 400
sent.clear(); assert post(event("checkout.session.completed", sess)) == "paid"
q = db.get_quote(q["id"]); assert q["status"] == "paid" and q["paid_via"] == "stripe" and q["stripe_subscription_id"] == "sub_1" and q["tenant_id"] and q["brand_id"]
t = db.get_tenant(q["tenant_id"]); assert t["email"] == "owner@pay.co.th" and t["plan"] == "pro" and t["stripe_customer_id"] == "cus_1"
assert db.get_lead(lid)["status"] == "won" and any(s[0] == "owner@pay.co.th" and "พร้อมแล้ว" in s[1] for s in sent)
assert post(event("checkout.session.completed", sess)) == "already-paid" and len([x for x in db.list_all_tenants() if x["email"] == "owner@pay.co.th"]) == 1
inv1 = {"id": "in_1", "object": "invoice", "subscription": "sub_1", "billing_reason": "subscription_create", "amount_paid": 1881000, "number": "GEO-0001", "hosted_invoice_url": "https://invoice.stripe.com/i/1", "invoice_pdf": "https://pay.stripe.com/invoice/1/pdf"}
assert post(event("invoice.paid", inv1)) == "first-invoice"
invs = db.list_stripe_invoices(t["id"]); assert len(invs) == 1 and invs[0]["amount"] == 18810 and invs[0]["hosted_url"].endswith("/i/1")
assert post(event("invoice.paid", inv1)) == "first-invoice" and len(db.list_stripe_invoices(t["id"])) == 1       # ส่งซ้ำไม่ซ้ำ
print("paid: webhook → บัญชี pro + customer ผูก · ใบแจ้งหนี้บันทึก · ส่งซ้ำไม่ซ้ำ OK")

# ---- ต่ออายุ / ค้างชำระ / ยกเลิก / คืนเงิน / ใบลดหนี้ ----
inv2 = dict(inv1, id="in_2", billing_reason="subscription_cycle", amount_paid=891000, number="GEO-0002")
assert post(event("invoice.paid", inv2)) == "renewed" and len(db.list_stripe_invoices(t["id"])) == 2
assert any(e["kind"] == "paid" and "ต่ออายุ" in (e["note"] or "") for e in db.list_lead_events(lid))
assert post(event("invoice.payment_failed", dict(inv2, id="in_3"))) == "failed"
assert post(event("customer.subscription.updated", {"id": "sub_1", "object": "subscription", "status": "past_due"})) == "past_due"
assert post(event("customer.subscription.deleted", {"id": "sub_1", "object": "subscription"})) == "cancelled" and db.get_tenant(t["id"])["plan"] == "free"
assert post(event("customer.subscription.updated", {"id": "sub_1", "object": "subscription", "status": "active"})) == "reactivated" and db.get_tenant(t["id"])["plan"] == "pro"
assert post(event("charge.refunded", {"id": "ch_1", "object": "charge", "customer": "cus_1", "amount_refunded": 990000})) == "refunded"
assert post(event("credit_note.created", {"id": "cn_1", "object": "credit_note", "customer": "cus_1", "amount": 50000})) == "credit_note"
assert post(event("invoice.paid", dict(inv2, id="in_9", subscription="sub_unknown"))) == "no-quote"
assert post(event("payment_intent.created", {"id": "pi_1"})) == "ignored"
print("lifecycle: ต่ออายุ · ค้างชำระ · ยกเลิก → free · กลับมา active → คืนแผน · คืนเงิน · ใบลดหนี้ OK")

# ---- ลูกค้า: Customer Portal + ใบแจ้งหนี้บนหน้าอัปเกรด ----
temp_pw = re.search(r"<code>([^<]+)</code>", [s for s in sent if "พร้อมแล้ว" in s[1]][0][2]) if False else None
cust = TestClient(app)
with db.get_conn() as cx:
    cx.execute(db.q("UPDATE tenants SET password_hash=? WHERE id=?"), (bcrypt.hashpw(b"custpass123", bcrypt.gensalt()).decode(), t["id"])); cx.commit()
cust.post("/login", data={"email": "owner@pay.co.th", "password": "custpass123"})
r = cust.get("/upgrade"); assert "จัดการการสมัคร" in r.text and "GEO-0002" in r.text and "8,910" in r.text
r = cust.get("/account/billing", follow_redirects=False); assert r.status_code == 303 and r.headers["location"].startswith("https://billing.stripe.com/")
assert calls[-1][0] == "/billing_portal/sessions" and calls[-1][1]["customer"] == "cus_1"
print("customer: Portal · ใบแจ้งหนี้ย้อนหลังบนหน้าอัปเกรด OK")

# ---- กลับจากหน้าชำระก่อน webhook → ถาม Stripe ตรง ----
lid2 = db.add_lead("https://pay2.co.th", "pay2.co.th", source="audit", email="owner@pay2.co.th"); P.run_lead_audit(lid2, "https://pay2.co.th")
q2 = P.create_quote(db.get_lead(lid2), "agency", 0, False); P.send_quote(q2, "admin"); q2 = db.get_quote(q2["id"]); P.accept_quote(q2, "คุณดี", "")
c.post(f"/q/{q2['token']}/stripe", follow_redirects=False)
assert [x for x in calls if x[0] == "/prices"][-1][1]["lookup_key"] == "geo_agency_month_29000"              # แพ็กเกจที่สองสร้างราคาใหม่
SP._get_json = lambda path: {"id": "cs_test_1", "payment_status": "paid", "customer": "cus_2", "subscription": "sub_2", "metadata": {"quote_id": str(q2["id"])}} if path.startswith("/checkout/sessions/") else fake_get(path)
r = c.get(f"/q/{q2['token']}?paid=1"); q2 = db.get_quote(q2["id"]); assert q2["status"] == "paid" and q2["stripe_subscription_id"] == "sub_2" and "เปิดใช้งานแล้ว" in r.text
print("sync: ?paid=1 ก่อน webhook → ยืนยันจาก Stripe ตรง OK")

# ---- หน้าตั้งค่า ----
admin = "admin@test.local"; db.create_tenant(admin, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Op", is_admin=True)
a = TestClient(app); a.post("/login", data={"email": admin, "password": "pw123456"})
r = a.get("/admin/settings"); assert "Stripe — รับบัตร" in r.text and "/stripe/webhook" in r.text and "พร้อม (test)" in r.text and "customer.subscription.updated" in r.text
a.post("/admin/settings/stripe", data={"secret": "", "whsec": "whsec_new_secret_000000"}); assert SP.webhook_secret() == "whsec_new_secret_000000" and SP.secret_key().startswith("sk_test")
print("admin: การ์ด Stripe · บันทึกคีย์ OK")
print("\nstripe_smoke: ผ่านทั้งหมด")
