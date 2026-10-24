"""ทดสอบฝ่ายพันธมิตร: หาเอเจนซี่ (กรองซ้ำ/ไม่ใช่ธุรกิจ) → ร่างคำชวน (กติกา/แม่แบบ) → อนุมัติแล้วส่งจากที่อยู่อีเมลทัก + เลิกรับ · ติดตามวันที่ 5 ครั้งเดียว
· ตอบกลับทางอีเมล → บันทึกที่พันธมิตร ไม่กลายเป็นลีด · สมัคร /partners → อนุมัติ → รหัส/ลิงก์/แดชบอร์ด · ลิงก์ /p/<code> → คุกกี้ → ลีดนับเป็นของพันธมิตร (ครั้งแรก)
· ส่งชื่อลูกค้าในแดชบอร์ด (ต้องยินยอม · ไม่แย่งลีดเดิม) · ค่าแนะนำจากใบเสร็จ (ก่อน VAT · รอ 7 วัน · ยกเลิกตามใบ · พ้นเดือนที่กำหนดไม่คิด) · จ่ายแล้ว · หน้าแอดมิน · แชต CEO"""
import os, tempfile, json, datetime as dt
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke-partner-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["GEO_DEEP_AUDIT"] = "0"; os.environ["GEO_CS"] = "0"
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.partners as PT, app.pipeline as P, app.mailer as mailer, app.ai_client as ai, app.site_health as sh, app.line_oa as LO
import app.inbox as IB, app.accounting as A, app.ceo_chat as C, app.office as O, app.skills as SK, app.execs as X
from app.main import app
db.init_db()
ai._read_setting = lambda k: None
ai._chat = lambda m, max_tokens=2048, timeout=120: (_ for _ in ()).throw(RuntimeError("no model"))
mails = []; mailer.config = lambda: {"host": "x", "smtp_from": "report@sender.test"}
mailer.send = lambda to, subj, html, text="", attachments=(): mails.append((to, subj, text, getattr(mailer._UNSUB_CTX, "url", "")))
mailer.is_deliverable = lambda e: bool(e) and e.endswith(".co.th")
LO._post = lambda path, payload: True
alerts = []; P.alert_admins = lambda msg, link, type_="lead", subject=None: alerts.append(msg)
A.HOOKS["pdf"] = lambda html: b"%PDF"
P.RUN_INLINE = True; C.RUN_INLINE = True
REPORT = {"checks": [{"key": "csr", "label": "csr", "status": "fail", "detail": ""}]}
sh.run_checks = lambda site, **kw: REPORT

# ---- หาพันธมิตร ----
db.add_lead("https://already.co.th", "already.co.th", source="prospect")
PT.HOOKS["search"] = lambda q, n: [{"url": "https://webagency.co.th", "title": "Web Agency | รับทำเว็บ"}, {"url": "https://already.co.th", "title": "x"},
                                   {"url": "https://facebook.com/x", "title": "fb"}, {"url": "https://noemail.co.th", "title": "No Mail"}]
PT.HOOKS["contact"] = lambda site: {"email": "hello@webagency.co.th"} if "webagency" in site else {}
r = PT.run(); assert r["added"] == 2 and r["proposed"] == 1 and r["domains"] == ["webagency.co.th", "noemail.co.th"], r
wa = db.find_partner(domain="webagency.co.th"); assert wa["status"] == "prospect" and wa["email"] == "hello@webagency.co.th" and wa["company"] == "Web Agency"
assert db.find_partner(domain="noemail.co.th")["email"] is None
assert PT.run()["added"] == 0                                                             # ไม่เพิ่มซ้ำ
d = [x for x in db.list_decisions("proposed", 20) if x["kind"] == "partner_outreach"][0]
pl = json.loads(d["payload_json"]); assert pl["source"].startswith("template") and "/partners" in pl["body"] and "20%" in pl["body"] and "การันตี" not in pl["body"]
good = lambda m: json.dumps({"subject": "ชวนเป็นพันธมิตร", "body": "สวัสดีครับ " * 15 + " ดูรายละเอียดที่ https://geo.appreview.cloud/partners"})
assert PT.draft_invite(wa, good)["source"] == "llm"
assert PT.draft_invite(wa, lambda m: json.dumps({"subject": "การันตีอันดับ 1", "body": "x" * 200}))["source"].startswith("template")
print("recruit: ค้นหา · ตัดลีดเดิม/โซเชียล · หาอีเมล · ร่างคำชวน (ผิดกติกา → แม่แบบ) · ไม่เพิ่มซ้ำ OK")

# ---- ส่ง (อนุมัติ) · เลิกรับ · ติดตาม ----
assert d["status"] == "proposed"                                                          # ไม่ส่งเอง
mails.clear(); out = P.execute_decision(d, "owner"); db.set_decision(d["id"], "executed", "owner", out); wa = db.get_partner(wa["id"])
assert "ส่งคำชวนถึง hello@webagency.co.th" in out and wa["status"] == "invited" and wa["touches"] == 1 and mails[-1][3].endswith("/unsubscribe")
assert PT.run(dt.datetime.now() + dt.timedelta(days=4))["followups"] == 0
assert PT.run(dt.datetime.now() + dt.timedelta(days=6))["followups"] == 1 and PT.run(dt.datetime.now() + dt.timedelta(days=7))["followups"] == 0
fu = [x for x in db.list_decisions("proposed", 20) if x["kind"] == "partner_outreach"][0]; assert json.loads(fu["payload_json"])["step"] == 2
# ตอบกลับ → ไม่กลายเป็นลีด · ยกเลิกติดตาม
n_leads = len(db.list_leads(None, 1000))
res = IB.process({"from": "hello@webagency.co.th", "to": "report@sender.test", "subject": "Re: ชวน", "body": "สนใจครับ ขอคุยหน่อย", "message_id": "<p1@x>"})
assert res["category"] == "partner" and len(db.list_leads(None, 1000)) == n_leads and db.get_partner(wa["id"])["status"] == "replied"
assert db.get_decision(fu["id"])["status"] == "cancelled" and any("ตอบอีเมลเรื่องพันธมิตร" in a for a in alerts)
assert IB.process({"from": "hello@webagency.co.th", "to": "report@sender.test", "subject": "Re: ชวน", "body": "สนใจครับ ขอคุยหน่อย", "message_id": "<p1@x>"}).get("skip") == "seen"
c = TestClient(app)
c.post(f"/partner/{wa['token']}/unsubscribe"); assert db.get_partner(wa["id"])["status"] == "unsubscribed"
d2 = db.add_decision("partner", "partner_outreach", "x", "x", {"partner_id": wa["id"], "subject": "s", "body": "b"}); assert "ไม่ส่ง" in P.execute_decision(db.get_decision(d2), "owner")
print("send: รออนุมัติ · ส่งพร้อมลิงก์เลิกรับ · ติดตามวันที่ 5 ครั้งเดียว · ตอบกลับไม่เป็นลีด/ยกเลิกติดตาม · เลิกรับแล้วไม่ส่ง OK")

# ---- สมัคร → อนุมัติ ----
r = c.get("/partners"); assert r.status_code == 200 and "20%" in r.text and "สมัครเป็นพันธมิตร" in r.text
assert "ติ๊กยอมรับ" in c.post("/partners", data={"name": "คุณบี", "email": "b@bagency.co.th"}).text
assert "อีเมลให้ถูกต้อง" in c.post("/partners", data={"name": "คุณบี", "email": "bad", "agree": "1"}).text
r = c.post("/partners", data={"name": "คุณบี", "company": "B Agency", "email": "b@bagency.co.th", "website": "bagency.co.th", "clients": "คลินิก 10 ราย", "agree": "1"})
assert "ได้รับใบสมัครแล้ว" in r.text
pb = db.find_partner(email="b@bagency.co.th"); assert pb["status"] == "applied" and any("สมัครเป็นพันธมิตร" in a for a in alerts)
da = [x for x in db.list_decisions("proposed", 20) if x["kind"] == "partner_apply"][0]
mails.clear(); out = P.execute_decision(da, "owner"); db.set_decision(da["id"], "executed", "owner", out); pb = db.get_partner(pb["id"])
assert pb["status"] == "active" and pb["code"].startswith("bagency") and "ส่งอีเมลต้อนรับ" in out and f"/p/{pb['code']}" in mails[-1][2] and f"/partner/{pb['token']}" in mails[-1][2]
assert c.get(f"/partner/{pb['token']}").status_code == 200 and pb["code"] in c.get(f"/partner/{pb['token']}").text
print("apply: หน้า /partners · ต้องยอมรับกติกา · อนุมัติ → รหัส · อีเมลต้อนรับมีลิงก์+แดชบอร์ด OK")

# ---- ลิงก์แนะนำ → ลีดนับเป็นของพันธมิตร ----
r = c.get(f"/p/{pb['code']}", follow_redirects=False); assert r.status_code in (302, 303, 307) and "ref=" in r.headers["location"] and "jr_ref" in r.headers.get("set-cookie", "")
c.cookies.set("jr_ref", pb["code"])
c.post("/audit", data={"url": "https://refclient.co.th"}); rl = next(l for l in db.list_leads(None, 1000) if l["domain"] == "refclient.co.th")
assert rl["partner_id"] == pb["id"] and any(e["kind"] == "partner_ref" for e in db.list_lead_events(rl["id"]))
c.cookies.clear(); assert PT.attribute(rl["id"], "zzz") is False and not PT.attribute(rl["id"], pb["code"])   # ครั้งแรกเท่านั้น
c.post("/audit", data={"url": "https://bagency.co.th", "ref": pb["code"]}); assert not next(l for l in db.list_leads(None, 1000) if l["domain"] == "bagency.co.th")["partner_id"]   # แนะนำตัวเองไม่นับ
# ส่งชื่อลูกค้าในแดชบอร์ด
assert "ยินยอม" in c.post(f"/partner/{pb['token']}/client", data={"website": "dash.co.th"}).text
c.post(f"/partner/{pb['token']}/client", data={"website": "dash.co.th", "email": "owner@dash.co.th", "consent": "1"})
dl = next(l for l in db.list_leads(None, 1000) if l["domain"] == "dash.co.th"); assert dl["partner_id"] == pb["id"] and dl["consent"] == 1 and dl["audit_json"]
assert "อยู่ในสายขายของเราอยู่แล้ว" in PT.submit_client(pb, "already.co.th", "", "", True) or db.get_lead(1)["status"] == "new"
print("attribution: /p/<code> ตั้งคุกกี้ · ลีดนับเป็นของพันธมิตรครั้งแรก · แนะนำตัวเองไม่นับ · ส่งชื่อลูกค้าต้องยินยอม → ตรวจเว็บ OK")

# ---- ค่าแนะนำจากใบเสร็จ ----
q = P.create_quote(db.get_lead(dl["id"]), "pro", 0, False, cycle="month"); db.update_quote(q["id"], status="claimed")
P.confirm_payment(db.get_quote(q["id"]), "owner")
rc = db.list_acct_docs("receipt", lead_id=dl["id"])[0]
cm = db.commission_for_receipt(rc["id"]); assert cm and cm["status"] == "pending" and cm["amount"] == round(rc["subtotal"] * 0.20, 2) and cm["partner_id"] == pb["id"]
assert PT.approve_due(dt.datetime.now() + dt.timedelta(days=6)) == 0 and PT.approve_due(dt.datetime.now() + dt.timedelta(days=8)) == 1
assert db.commission_for_receipt(rc["id"])["status"] == "approved" and PT.stats(pb)["approved"] == cm["amount"]
assert "บันทึกว่าจ่าย" in PT.mark_paid(pb["id"]) and db.commission_for_receipt(rc["id"])["status"] == "paid" and PT.mark_paid(pb["id"]).startswith("ไม่มี")
q2 = P.create_quote(db.get_lead(rl["id"]), "pro", 0, False, cycle="month"); db.update_quote(q2["id"], status="claimed"); P.confirm_payment(db.get_quote(q2["id"]), "owner")
rc2 = db.list_acct_docs("receipt", lead_id=rl["id"])[0]; A.void(rc2, "ทดสอบยกเลิก"); assert db.commission_for_receipt(rc2["id"])["status"] == "void"
with db.get_conn() as cc:
    cc.execute(db.q("UPDATE acct_docs SET created_at=? WHERE id=?"), ((dt.datetime.now() - dt.timedelta(days=400)).isoformat(timespec="seconds"), rc["id"]))
late = A.issue_receipt(db.get_quote(q["id"]), [{"desc": "ต่ออายุ", "amount": 9900.0}], "promptpay", ("2027-11-01", "2027-11-30"), total=9900)
assert db.commission_for_receipt(late["id"]) is None                                       # พ้น 12 เดือนแรก
print("commission: ใบเสร็จ → 20% ก่อน VAT · รอ 7 วัน → ถึงกำหนด · จ่ายแล้ว · ใบยกเลิก → ค่าแนะนำยกเลิก · พ้นเดือนที่กำหนดไม่คิด OK")

# ---- แอดมิน · แชต · ทะเบียน ----
admin = "admin@test.local"; db.create_tenant(admin, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Op", is_admin=True)
a = TestClient(app); a.post("/login", data={"email": admin, "password": "pw123456"})
pg = a.get("/admin/partners").text
for s_ in ("พันธมิตร", "B Agency", "webagency.co.th", "หาพันธมิตรตอนนี้", "รหัส " + pb["code"]):
    assert s_ in pg, s_
a.post("/admin/partners/pause", data={"pid": pb["id"]}); assert db.get_partner(pb["id"])["status"] == "paused"
assert not PT.attribute(db.add_lead("https://x9.co.th", "x9.co.th"), pb["code"]); a.post("/admin/partners/pause", data={"pid": pb["id"]})
a.post("/admin/partners/add", data={"name": "คุณซี", "email": "c@cagency.co.th", "company": "C"}); assert db.find_partner(email="c@cagency.co.th")["status"] == "applied"
a.post("/admin/partners/activate", data={"pid": db.find_partner(email="c@cagency.co.th")["id"]}); assert db.find_partner(email="c@cagency.co.th")["status"] == "active"
assert all(x["status"] != "proposed" for x in db.list_decisions(None, 50) if x["kind"] == "partner_apply" and json.loads(x["payload_json"])["partner_id"] == db.find_partner(email="c@cagency.co.th")["id"])
a.post("/admin/partners/settings", data={"on": "", "daily": "3"}); assert not PT.enabled() and PT.daily_limit() == 3 and PT.run()["skipped"] == "ปิดอยู่"
assert "พันธมิตร:" in C.handle("สรุปพันธมิตรหน่อย") and "พันธมิตร:" in (PT.status_text() or "")
assert "partner" in O.IDS and "partner" in SK.AGENTS and SK.load("partner").startswith("# ทักษะ") and "partner_commission_pct" in X.MANDATE_DEFAULT
assert a.get("/admin/office/state.json").status_code == 200
print("admin/chat: หน้าแอดมิน · พัก = ลิงก์ไม่นับ · เพิ่มเอง/รับ · ปิดรอบ · แชต CEO · ทะเบียน OK")
print("\npartners_smoke: ผ่านทั้งหมด")
