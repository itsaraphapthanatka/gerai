"""ทดสอบโหมดเอเจนซี (เจ้าของขายและปิดดีลเอง):
· ลีดสนใจ (ปุ่มบนหน้าผลตรวจ · พิมพ์สนใจทาง LINE · ตอบอีเมลว่าสนใจ · ตัวตอบแชตเจอสัญญาณซื้อ) → ไม่สร้าง/ไม่ส่งใบเสนอราคา · แจ้งเจ้าของ
  "อยากคุย — นัดโทรภายในวันนี้" · ขึ้นบนสุดของรายการโทร · ตอบลีดในภาษาของเขาว่าจะติดต่อไปนัดคุย 30 นาที
· ใบเสนอราคาไม่มีทางส่งเอง (เรื่อง "ใบเสนอราคา" ปลดล็อกอัตโนมัติไม่ได้ แม้ค่าเก่าในฐานข้อมูลเปิดไว้)
· ใบราคาที่เจ้าของตั้งเอง (รายเดือน/รายปี + Setup + ขอบเขตงาน) → หน้าใบเสนอราคา · สัญญา · HTML ของ PDF · Stripe price_data · PromptPay ยอดเดียวกัน
· CFO เตือน (ไม่บล็อก) เมื่อต่ำกว่าต้นทุน AI · ใบแบบตารางเดิมคำนวณเหมือนเดิม · ไม่ยิงเครือข่าย"""
import os, tempfile, json, re, datetime as dt
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-agency-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["GEO_DEEP_AUDIT"] = "0"; os.environ["GEO_CS"] = "0"
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.pipeline as P, app.quotes as Q, app.execs as X, app.closer as C, app.salesbot as SB, app.inbox as IB
import app.line_oa as LO, app.mailer as mailer, app.deliverability as D, app.stripe_pay as SP, app.report as R, app.i18n as I, app.ai_client as ai
from app.main import app

db.init_db()
I.load(force=True)
P.RUN_INLINE = True
C.FETCH = lambda u: ""
ai._read_setting = lambda k: None
ai._chat = lambda m, max_tokens=2048, timeout=120: (_ for _ in ()).throw(RuntimeError("no model"))
THAI = re.compile(r"[฀-๿]")
alerts, replies, pushes, mails, pdfs = [], [], [], [], []
P.alert_admins = lambda msg, link, type_="lead", subject=None: alerts.append((msg, link, subject))
LO.reply = lambda tok, texts: replies.append(texts) or True
LO.push = lambda uid, texts: pushes.append((uid, texts)) or True
mailer.config = lambda: {"host": "x"}
mailer.is_deliverable = lambda e: bool(e) and "@" in e
mailer.send = lambda to, subject, html, text="", attachments=(): mails.append({"to": to, "subj": subject, "text": text, "html": html})
D.mx_ok = lambda e: True
R.to_pdf = lambda html: pdfs.append(html) or b"%PDF-1.4 test"
REPORT = {"checks": [{"key": "csr", "label": "เนื้อหาอยู่ใน HTML ไม่ต้องรอ JavaScript", "status": "fail", "detail": ""},
                     {"key": "noindex", "label": "ไม่ถูกสั่งห้าม index", "status": "fail", "detail": ""}]}

def visible(html: str) -> str:
    h = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html)
    attrs = " ".join(re.findall(r'(?:placeholder|title|alt|aria-label)="([^"]*)"', h))
    h = re.sub(r"<[^>]+>", " ", h) + " " + attrs
    return re.sub(r"เจอ\.AI|เจอ|฿|ไทย", "", h)

def no_thai(html, where):
    v = visible(html)
    assert not THAI.search(v), (where, re.findall(r"\S*[฀-๿]+\S*", v)[:8])

def mk(dom, lang="th", status="contacted", email=True, consent=1, **kw):
    lid = db.add_lead(f"https://{dom}", dom, source="prospect", email=f"owner@{dom}" if email else None)
    db.update_lead(lid, status=status, audit_json=json.dumps(REPORT), score=60, token=f"tok-{dom.replace('.', '-')}", consent=consent, lang=lang,
                   audit_at="2026-10-20T09:00:00", **kw)
    db.add_lead_event(lid, "contacted", "", "auto")
    return lid

def kinds(lid): return [e["kind"] for e in db.list_lead_events(lid, 300)]
def no_quote(lid): return not db.list_quotes(lid) and not [d for d in db.list_decisions(None, 500) if d["kind"] == "quote" and d["lead_id"] == lid]
def hot(lid): return any("อยากคุย — นัดโทรภายในวันนี้" in a[0] and a[1] == f"/admin/leads/{lid}" for a in alerts)

admin = "owner@agency.example.com"
db.create_tenant(admin, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Op", is_admin=True)
a = TestClient(app); a.post("/login", data={"email": admin, "password": "pw123456"})
c = TestClient(app)

# ---- ลีดร้อนอยู่แล้ว (ยืนยันใบแล้วไม่จ่าย) เพื่อดูว่าคนขอนัดคุยขึ้นบนกว่า ----
hot_q = mk("accepted.example.com", status="quoted")
qa = P.create_quote(db.get_lead(hot_q), "pro", cycle="month"); db.update_quote(qa["id"], status="accepted", accepted_at=(dt.datetime.now() - dt.timedelta(days=2)).isoformat(timespec="seconds"))
assert C.call_list()[0]["lead"]["id"] == hot_q

# ================= 1) ปุ่มบนหน้าผลตรวจ =================
web = mk("web-en.example.com", lang="en")
tok = db.get_lead(web)["token"]
r = c.get(f"/audit/{tok}?lang=en"); assert r.status_code == 200 and "Book a 30-minute call with our team" in r.text and "Request a quote" not in r.text
no_thai(r.text, "report-en")
r = c.post(f"/audit/{tok}/reply", data={"message": "Tuesday afternoon works"})
assert r.status_code == 200 and "book a 30-minute call" in r.text and "within this business day" in r.text, r.text[-1500:]
no_thai(r.text, "report-en-replied")
assert no_quote(web) and hot(web) and "wants_call" in kinds(web) and db.get_lead(web)["status"] == "call"   # ขั้นดีล "อยากคุย"
top = C.call_list()[0]; assert top["lead"]["id"] == web and top["trigger"]["kind"] == "wants_call" and top["trigger"]["prio"] > 100, top["trigger"]
r = c.get(f"/audit/{tok}?lang=th"); assert "รับเรื่องแล้ว ทีมเจอ.AI จะติดต่อกลับเพื่อนัดคุย 30 นาที" in r.text
n = len(alerts); c.post(f"/audit/{tok}/reply", data={}); assert len(alerts) == n and no_quote(web)        # กดซ้ำ: ไม่แจ้งซ้ำ ไม่มีใบ
s = C.script(db.get_lead(web)); assert "30 นาที" in s["opener"] and "ใบเสนอราคา" in s["close"] and "เริ่มต้น 9,900" in s["price"]
r = a.get(f"/admin/leads/{web}"); assert "ลูกค้าขอนัดคุย" in r.text and "/admin/calls/" in r.text and "ตั้งราคาเอง" in r.text and "แพ็กเกจมาตรฐาน" in r.text
print("report page: ปุ่มนัดคุย 30 นาที (ภาษาของลูกค้า) → ไม่มีใบเสนอราคา · แจ้งเจ้าของ · บนสุดของรายการโทร · กดซ้ำไม่แจ้งซ้ำ · บทโทร OK")

# ================= 2) LINE พิมพ์ "สนใจ" =================
ln = mk("line.example.com", lang="th", email=False, line_user_id="U-line-1")
out = P.line_text("U-line-1", "สนใจครับ", "rt1")
assert out == "replied" and no_quote(ln) and hot(ln) and "wants_call" in kinds(ln)
assert replies[-1][0] == P.call_reply(db.get_lead(ln)) and "30 นาที" in replies[-1][0] and "ใบเสนอราคา" not in replies[-1][0]
assert "สะดวกคุยวันไหน" in replies[-1][0]                                                                   # มีช่องทาง (LINE) แล้ว → ถามแค่เวลา
assert "30 นาที" in P.line_result_message(db.get_lead(ln)) and "ราคา" not in P.line_result_message(db.get_lead(ln)).splitlines()[-1]
assert C.call_list()[0]["trigger"]["kind"] == "wants_call"
print("LINE: สนใจ → ตอบนัดคุย 30 นาที · ไม่มีใบเสนอราคา · แจ้งเจ้าของ · รายการโทร OK")

# ================= 3) อีเมลตอบกลับว่าสนใจ (ลีดภาษาเกาหลี) =================
em = mk("mail-ko.example.com", lang="ko")
mails.clear()
raw = (f"From: Kim <owner@mail-ko.example.com>\r\nTo: report@appreview.cloud\r\nSubject: Re: site\r\nMessage-ID: <ko1@x>\r\n"
       f"Content-Type: text/plain; charset=utf-8\r\n\r\n관심 있습니다. 연락 주세요").encode("utf-8")
res = IB.process(IB.parse_raw(raw))
assert res["category"] == "interested" and no_quote(em) and hot(em) and "นัดโทร" in res["action"], res
ack = [m for m in mails if m["to"] == "owner@mail-ko.example.com"]
assert ack and "30분" in ack[-1]["text"] and not THAI.search(re.sub(r"เจอ\.?AI", "", ack[-1]["text"])) and "/u/" in ack[-1]["text"], ack
assert 'lang="ko"' in ack[-1]["html"]
print("email: สนใจ → ไม่มีใบเสนอราคา · แจ้งเจ้าของ · ตอบลีดทันทีเป็นภาษาเกาหลีว่าจะนัดคุย 30 นาที OK")

# ================= 4) ตัวตอบแชตเจอสัญญาณซื้อ =================
SB.LLM = lambda m: json.dumps({"answer": "起价 9,900 泰铢/月，最终价格取决于工作范围。", "needs_human": False}, ensure_ascii=False)
zh = mk("chat-zh.example.com", lang="zh")
r = c.post("/chat/ask", json={"q": "我很感兴趣，价格多少？", "token": db.get_lead(zh)["token"]}).json()
assert "9,900" in r["answer"] and "30 分钟" in r["answer"] and not THAI.search(re.sub(r"เจอ\.AI", "", r["answer"])), r
assert no_quote(zh) and hot(zh) and "wants_call" in kinds(zh)
assert SB.buying_intent("สนใจครับ") and SB.buying_intent("ราคาเท่าไหร่") and not SB.buying_intent("ไม่สนใจครับ") and not SB.buying_intent("ใช้กับ Wix ได้ไหม")
n = len(alerts)
SB.LLM = lambda m: json.dumps({"answer": "We start at 9,900 THB/month; the final price depends on scope.", "needs_human": False})
r = c.post("/chat/ask", json={"q": "I'm interested, how do I get started?"}).json()                          # ผู้เยี่ยมชมที่ยังไม่มีลีด
assert r["escalate"] and r["id"] and len(alerts) == n + 1 and "อยากคุย" in alerts[-1][0], (r, alerts[-1])
k = " ".join(SB.knowledge()); assert "ราคาเริ่มต้น 9,900 บาท/เดือน" in k and "29,000" not in k and "Agency" not in k
seen = {}
SB.answer("แพ็กเกจมีอะไรบ้าง ราคาเท่าไหร่", None, "line", llm=lambda m: seen.setdefault("p", m[0]["content"]) and json.dumps({"answer": "เริ่มต้น 9,900 บาท/เดือนครับ", "needs_human": False}))
assert "อย่าเสนอเมนูแพ็กเกจ" in seen["p"] and "30 นาที" in seen["p"]
print("salesbot: สัญญาณซื้อ (ลีดจีน) → ตอบ + ชวนนัดคุย 30 นาทีภาษาจีน · แจ้งเจ้าของ · ผู้เยี่ยมชมไม่มีลีดส่งต่อเจ้าของ · คลังข้อเท็จจริงไม่มีเมนูแพ็กเกจ OK")

# ================= 5) ใบเสนอราคาไม่มีทางส่งเอง =================
assert "quote" not in X.AUTO_ELIGIBLE and not X.set_auto("quote", True, override=True)[0]
db.set_setting("auto_quote", "1"); db.set_setting("auto_override_quote", "1")                                # ค่าเก่าค้างในฐานข้อมูล
assert not X.auto_enabled("quote")
mails.clear(); did = P.propose_quote(db.get_lead(em)); d = db.get_decision(did)
assert d["status"] == "proposed" and db.get_quote(json.loads(d["payload_json"])["quote_id"])["status"] == "draft" and not mails
_orig_auto = X.auto_enabled
X.auto_enabled = lambda kind: True                                                                           # ต่อให้ด่านเปิดทุกเรื่อง
assert P._maybe_auto(did) == did and db.get_decision(did)["status"] == "proposed" and P.run_pending_auto() >= 0 and db.get_decision(did)["status"] == "proposed"
X.auto_enabled = _orig_auto
db.set_setting("auto_quote", "0"); db.set_setting("auto_override_quote", "0")
db.set_decision(did, "cancelled", "test", "x"); db.update_quote(json.loads(d["payload_json"])["quote_id"], status="cancelled")
print("no auto quote: ใบเสนอราคาปลดล็อกอัตโนมัติไม่ได้ · ค่าเก่าเปิดไว้ก็ไม่ส่ง · ร่างรอเจ้าของกดส่งเสมอ OK")

# ================= 6) ใบราคาตั้งเอง: รายเดือน + Setup + ขอบเขตงาน =================
SP.save_config("sk_test_abcdefghijklmnop", "whsec_x")
db.set_setting("promptpay_id", "0812345678"); db.set_setting("promptpay_name", "Agency Example")
calls = []
def fake_get(path):
    if path.startswith("/tax_rates"): return {"data": []}
    if path.startswith("/prices?lookup_keys[]="): return {"data": []}
    if path.startswith("/coupons/"): raise RuntimeError("No such coupon")
    raise RuntimeError("unexpected GET " + path)
def fake_post(path, data, idem=None):
    calls.append((path, data))
    if path == "/checkout/sessions": return {"id": f"cs_test_{len(calls)}", "url": f"https://checkout.stripe.com/c/pay/cs_test_{len(calls)}"}
    if path == "/tax_rates": return {"id": "txr_vat7"}
    if path == "/products": return {"id": f"prod_{len(calls)}"}
    if path == "/prices": return {"id": f"price_{len(calls)}", "product": data["product"]}
    if path == "/coupons": return {"id": data["id"]}
    raise RuntimeError("unexpected POST " + path)
SP._get_json, SP._post = fake_get, fake_post

lm = mk("custom-month.example.com", lang="th", name="คุณทดสอบ")
mails.clear()
scope = "• ติดตั้ง llms.txt และ schema ทั้งเว็บ\n\n- เขียนบทความตอบคำถามลูกค้า 8 ชิ้น/เดือน\nประชุมสรุปผลทุกเดือน"
r = a.post(f"/admin/leads/{lm}/quote", data={"package": "custom", "price": "15,000", "cycle": "month", "setup_amount": "5000", "scope": scope, "send": "1"})
assert r.status_code == 200 and "ส่งใบเสนอราคา" in r.text and "CFO" in r.text, r.text[:500]
q = db.list_quotes(lm)[0]
assert Q.is_custom(q) and q["custom_price"] == 15000 and q["price"] == 15000 and q["net"] == 15000 and q["discount_pct"] == 0
assert q["setup"] == 1 and q["setup_price"] == 5000 and q["first_total"] == 20000 and Q.period_total(q) == 15000 and Q.payable(q) == 20000
assert q["package"] == Q.CUSTOM_BASE and Q.label_of(q) == "แบบกำหนดเอง" and Q.scope_lines(q) == ["ติดตั้ง llms.txt และ schema ทั้งเว็บ", "เขียนบทความตอบคำถามลูกค้า 8 ชิ้น/เดือน", "ประชุมสรุปผลทุกเดือน"]
assert q["status"] == "sent" and Q.alternative(q) is None
em_q = [m for m in mails if m["to"] == "owner@custom-month.example.com"][-1]["text"]
assert "แบบกำหนดเอง 15,000 บาท/เดือน" in em_q and "เขียนบทความตอบคำถามลูกค้า 8 ชิ้น/เดือน" in em_q and "GEO Setup" in em_q and "20,000" in em_q and "รายปี" not in em_q
r = c.get(f"/q/{q['token']}?lang=th"); t = r.text
assert r.status_code == 200 and "แบบกำหนดเอง — ตามขอบเขตงานที่ตกลง" in t and "ขอบเขตงาน" in t and "ประชุมสรุปผลทุกเดือน" in t and "20,000" in t and "5,000" in t
assert "เปลี่ยนเป็นรายปี" not in t and "ราคาตามตารางแพ็กเกจมาตรฐาน" not in t and "ราคาและขอบเขตงานตามที่ตกลงกับทีมเจอ.AI" in t
assert "ค่า GEO Setup ชำระครั้งเดียวพร้อมงวดแรก" in t and "5 แบรนด์" not in t and "30 แบรนด์" not in t                       # แบบกำหนดเอง = ขอบเขตงานแทนรายการแพ็กเกจ
assert "เปลี่ยนรอบบิลเองไม่ได้" in c.post(f"/q/{q['token']}/cycle", data={"cycle": "year"}).text and Q.cycle_of(db.get_quote(q["id"])) == "month"
r = c.get(f"/q/{q['token']}/contract?lang=th"); t = r.text
assert "ตามขอบเขตงานที่ตกลงในใบเสนอราคา" in t and "ติดตั้ง llms.txt และ schema ทั้งเว็บ" in t and "ค่าบริการ 5,000 บาท" in t and "ค่าบริการ 15,000 บาทต่อเดือน" in t
pdfs.clear(); assert c.get(f"/q/{q['token']}.pdf?lang=th").status_code == 200 and c.get(f"/q/{q['token']}/contract.pdf?lang=th").status_code == 200
assert len(pdfs) == 2 and "ประชุมสรุปผลทุกเดือน" in pdfs[0] and "20,000" in pdfs[0] and "ประชุมสรุปผลทุกเดือน" in pdfs[1] and "ค่าบริการ 5,000 บาท" in pdfs[1]
assert P.accept_quote(db.get_quote(q["id"]), "คุณทดสอบ", "1.2.3.4") == ""
r = c.get(f"/q/{q['token']}?lang=th"); assert "ชำระผ่าน Stripe" in r.text and 'alt="PromptPay QR"' in r.text and '<div class="amt">20,000 ' in r.text
n = len(calls); r = c.post(f"/q/{q['token']}/stripe", follow_redirects=False); assert r.status_code == 303 and r.headers["location"].startswith("https://checkout.stripe.com/")
sess = [x for x in calls[n:] if x[0] == "/checkout/sessions"]; assert len(sess) == 1 and not [x for x in calls[n:] if x[0] in ("/prices", "/products")]
p = sess[0][1]
assert p["mode"] == "subscription" and "line_items[0][price]" not in p and "discounts[0][coupon]" not in p
assert p["line_items[0][price_data][currency]"] == "thb" and p["line_items[0][price_data][unit_amount]"] == "1500000" and p["line_items[0][price_data][recurring][interval]"] == "month"
assert p["line_items[1][price_data][unit_amount]"] == "500000" and "line_items[1][price_data][recurring][interval]" not in p and "tax_rates" not in json.dumps(p)
assert "แบบกำหนดเอง" in p["line_items[0][price_data][product_data][name]"] and p["metadata[quote_id]"] == str(q["id"]) and p["customer_email"] == "owner@custom-month.example.com"
print("custom monthly: ราคาตั้งเอง + Setup + ขอบเขตงาน → อีเมล · หน้าใบ · สัญญา · PDF · ลูกค้าสลับรอบบิลไม่ได้ · Stripe price_data 15,000/เดือน + Setup 5,000 · PromptPay 20,000 OK")

# ================= 7) ใบราคาตั้งเอง: รายปี (ยอดทั้งปีที่ใส่เอง · VAT · ลูกค้าภาษาอังกฤษ) =================
def tax_id(prefix):
    s_ = sum(int(prefix[i]) * (13 - i) for i in range(12)); return prefix + str((11 - s_ % 11) % 10)
db.set_setting("company_tax_id", tax_id("010555512345")); db.set_setting("company_vat", "1")
ly = mk("custom-year.example.com", lang="en", name="Alex Example")
db.update_lead(ly, audit_json=json.dumps({"checks": []}))
r = a.post(f"/admin/leads/{ly}/quote", data={"package": "pro", "price": "12000", "cycle": "year", "year_total": "120000", "setup_amount": "",
                                              "scope": "Monthly strategy call\nTwo answer pages per week"})
qy = db.list_quotes(ly)[0]
assert qy["status"] == "draft" and "เป็นร่าง" in r.text and qy["custom_price"] == 12000 and qy["custom_year"] == 120000 and Q.cycle_of(qy) == "year"
assert Q.period_total(qy) == 120000 and qy["net"] == 10000 and qy["first_total"] == 120000 and qy["setup"] == 0 and Q.vat_pct(qy) == 7 and Q.payable(qy) == 128400
assert c.get(f"/q/{qy['token']}", follow_redirects=False).status_code == 303                                       # ร่างยังไม่เปิดสาธารณะจนเจ้าของกดส่ง
a.post(f"/admin/quotes/{qy['id']}/send"); qy = db.get_quote(qy["id"]); assert qy["status"] == "sent"
r = c.get(f"/q/{qy['token']}?lang=en"); t = r.text
assert "Pro — per the agreed scope of work" in t and "Scope of work" in t and "Two answer pages per week" in t and "120,000" in t and "128,400" in t
assert "144,000" in t and "Annual payment discount" in t and "24,000" in t                                         # 12 × 12,000 − ยอดที่ตกลง
no_thai(t, "custom-year-en")
r = c.get(f"/q/{qy['token']}/contract?lang=en"); no_thai(r.text, "custom-year-contract-en")
assert "Monthly strategy call" in r.text and "120,000" in r.text
txt = json.dumps([Q.terms_for(qy, lg) for lg in ("en", "zh", "ja", "ko")] + [Q.contract_sections({}, {"domain": "custom-year.example.com"}, qy, lg) for lg in ("en", "zh", "ja", "ko")]
                 + [Q.summary_text(qy, "custom-year.example.com", lg) for lg in ("en", "zh", "ja", "ko")], ensure_ascii=False)
assert not THAI.search(re.sub(r"เจอ\.AI", "", txt)), re.findall(r"\S*[฀-๿]+\S*", txt)[:6]
P.accept_quote(db.get_quote(qy["id"]), "Alex Example")
n = len(calls); c.post(f"/q/{qy['token']}/stripe", follow_redirects=False)
p = [x for x in calls[n:] if x[0] == "/checkout/sessions"][0][1]
assert p["line_items[0][price_data][unit_amount]"] == "12000000" and p["line_items[0][price_data][recurring][interval]"] == "year"
assert p["line_items[0][tax_rates][0]"] == "txr_vat7" and "line_items[1][price_data][currency]" not in p
qauto = P.create_quote(db.get_lead(ly), "agency", cycle="year", custom=10000)                                    # ไม่ใส่ยอดทั้งปี = 12 × ราคา × (1 − ส่วนลดรายปี)
assert Q.period_total(qauto) == round(10000 * 12 * (1 - Q.yearly_discount() / 100)) == 99600 and qauto["custom_year"] is None
db.update_quote(qauto["id"], status="cancelled")
db.set_setting("company_vat", "0")
print("custom yearly: ยอดทั้งปีที่ใส่เอง 120,000 + VAT = 128,400 · ร่างจนกว่าจะกดส่ง · หน้าใบ/สัญญาอังกฤษไม่มีไทยหลง · Stripe price_data รายปี + tax rate · ไม่ใส่ยอด = ลดรายปีอัตโนมัติ OK")

# ================= 8) CFO เตือนแต่ไม่บล็อก =================
lw = mk("cheap.example.com")
r = a.post(f"/admin/leads/{lw}/quote", data={"package": "pro", "price": "300", "cycle": "month"})
qw = db.list_quotes(lw)[0]; assert qw["net"] == 300 and qw["first_total"] == 300 and "ต่ำกว่าต้นทุน AI" in r.text and "เตือนเท่านั้น" in r.text
assert X.cfo_custom(0)["warn"] and not X.cfo_custom(20000)["warn"] and "กำไรขั้นต้นต่ำกว่ากรอบ" in X.cfo_custom(1500)["warn"]
assert "ใส่ราคาต่อเดือน" in a.post(f"/admin/leads/{lw}/quote", data={"package": "custom", "price": ""}).text
r = a.post(f"/admin/leads/{lw}/quote", data={"package": "pro", "price": "0", "cycle": "month"}); assert db.list_quotes(lw)[0]["net"] == 0   # 0 บาทได้ (เจ้าของตัดสิน)
print("cfo: ต่ำกว่าต้นทุน AI → เตือน (สร้างใบได้) · แบบกำหนดเองต้องใส่ราคา · 0 บาทได้ OK")

# ================= 9) ใบแบบตารางเดิม: เหมือนเดิมทุกอย่าง =================
lo = mk("old-style.example.com")
qo = P.create_quote(db.get_lead(lo), "pro", 0, True, cycle="month")
assert not Q.is_custom(qo) and qo["first_total"] == 19800 and qo["net"] == 9900 and qo["setup_price"] == 9900 and Q.alternative(qo)["saving_year"] > 0
assert Q.price_for("pro", 10, True, "month")["first_total"] == 18810 and Q.price_for("pro", 0, True, "year")["first_total"] == 98604
assert "• แพ็กเกจ Pro 9,900 บาท/เดือน" in Q.summary_text(qo, "old-style.example.com") and "หรือจ่ายรายปี" in Q.summary_text(qo, "old-style.example.com")
r = a.post(f"/admin/leads/{lo}/quote", data={"package": "pro", "discount_pct": "5", "setup": "1", "cycle": "month"})
qo2 = db.list_quotes(lo)[0]; assert not Q.is_custom(qo2) and qo2["net"] == 9405 and qo2["first_total"] == 9405 + 9900 and "เป็นร่าง" in r.text
P.send_quote(qo2, "test"); P.accept_quote(db.get_quote(qo2["id"]), "คุณเก่า")
n = len(calls); c.post(f"/q/{qo2['token']}/stripe", follow_redirects=False)
p = [x for x in calls[n:] if x[0] == "/checkout/sessions"][0][1]
assert p["line_items[0][price]"].startswith("price_") and "line_items[0][price_data][currency]" not in p and p["discounts[0][coupon]"] == "geo-disc-5-pro"
assert "ราคาตามตารางแพ็กเกจมาตรฐาน" in c.get(f"/q/{qo2['token']}?lang=th").text
print("old-style: ราคาตาราง/ส่วนลด/Setup/รายปีเหมือนเดิม · ฟอร์มเว้นราคาว่าง = แบบเดิม · Stripe ใช้แคตตาล็อก + คูปอง OK")

# ================= 10) โทรแล้ว → ออกจากหัวรายการ =================
C.log_call(db.get_lead(web), "think", "ขอคุยกับหุ้นส่วนก่อน")
assert all(x["trigger"]["kind"] != "wants_call" or x["lead"]["id"] != web for x in C.call_list())
assert P.wants_call_pending(web) is None and P.wants_call_pending(ln)
print("closer: โทรแล้วไม่อยู่หัวรายการ · คนที่ยังไม่ได้โทรยังอยู่ OK")
print("ALL OK")
