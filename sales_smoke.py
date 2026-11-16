"""ทดสอบ stage 11: ความยินยอมกลางตอนส่ง · ช่องทางที่ได้รับอนุญาต (ไม่ไล่ข้ามช่องทาง) · ตอบกลับหยุดติดตามก่อนจัดประเภท
· ไม่อยู่/ยังไม่ใช่ตอนนี้ · อีเมลซ้ำ · ขั้นโอกาสขาย · คะแนนคัดกรอง · การจ่ายเงินต้องยืนยันโดยทีม · ใบเสนอราคาเปิดด้วยลิงก์
ไม่ยิงเครือข่าย (แทนตัวส่งอีเมล/LINE/SMS ด้วยตัวเก็บ) · DB ชั่วคราว"""
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
import app.pipeline as P
import app.mailer as M
import app.line_oa as LO
import app.sms as SMS
import app.deliverability as DV
import app.inbox as IB
import app.followup as FU
import app.sales as S
import app.plan_request as PR
import app.business_context as BC
import app.appointments as AP
from app.main import app

db.init_db()
sent = []
P.alert_admins = lambda *a, **kw: None
M.config = lambda: {"host": "x"}
M.is_deliverable = lambda e: bool(e and "@" in e)
M.send_plain = lambda to, *a, **kw: sent.append(("email", to))
M.send = lambda to, *a, **kw: sent.append(("email", to))
DV.mx_ok = lambda e: True
DV.active_from = lambda: ("me@jor.ai", "Jor")
LO.push = lambda uid, msgs: sent.append(("line", uid)) or PUSH_OK[0]
PUSH_OK = [True]
SMS.available = lambda: True
SMS.lead_mobile = lambda lead: "0812345678"
SMS.send = lambda m, txt: sent.append(("sms", m)) or "ส่ง SMS แล้ว"
IB.LLM = lambda msgs: '{"category": "other"}'


def mk(dom, **kw):
    lid = db.add_lead(f"https://{dom}", dom, source=kw.pop("source", "prospect"), ip="1.2.3.4")
    db.update_lead(lid, token="T" + dom.split(".")[0][:10].upper(), audit_json=json.dumps({"ok": True, "checks": []}), audit_at=db.now(), **kw)
    return lid


def decision(lid, kind="followup", body="สวัสดีครับ"):
    did = db.add_decision("sdr", kind, "t", "p", {"subject": "s", "body": body, "step": 1}, lead_id=lid)
    return db.get_decision(did)


# ================= ความยินยอมกลาง (ทำงานเสมอ ไม่ผ่าน flag) =================
lid = mk("race.co.th", email="a@race.co.th", email_src="site", status="contacted")
db.add_lead_event(lid, "contacted", "ทักแล้ว", "sdr")
assert S.contact_block(db.get_lead(lid)) == ""
d = decision(lid, "outreach")
db.update_lead(lid, unsub_at=db.now())                                                    # ขอไม่รับหลังข้อเสนอถูกสร้าง (ข้อเสนอยังไม่ถูกยกเลิก)
sent.clear()
assert P.execute_decision(d, "owner").startswith("ไม่ส่ง") and sent == []
assert any("ไม่ส่งข้อความทัก" in (e["note"] or "") for e in db.list_lead_events(lid))
lid2 = mk("stopline.co.th", email="b@stopline.co.th", email_src="form", status="replied", line_user_id="U2")
v = {"name": "ก", "role": "", "contact_channel": "line", "contact_value": "แชต", "desired_window": "", "web_readiness": "", "budget": "",
     "message": "", "idem_key": "1" * 32, "marketing": False}
pid2, _ = PR.submit(db.get_lead(lid2), v, source="line_oa")
db.save_line_session("U2", lead_id=lid2, plan_id=pid2, step="stopped", fails=0, fail_at=None)
assert "LINE" in S.contact_block(db.get_lead(lid2))
for kind in ("outreach", "reply", "followup"):
    assert P.execute_decision(decision(lid2, kind), "owner").startswith("ไม่ส่ง"), kind
db.update_plan_request(pid2, status="withdrawn")
assert "คำขอแผน" in S.contact_block(db.get_lead(lid2))
assert sent == []

# ================= flag ปิด = ลำดับช่องทางเดิม =================
lid3 = mk("public.co.th", email="info@public.co.th", email_src="site", status="contacted")
assert P.send_channel(db.get_lead(lid3)).startswith("อีเมล")

# ================= sales_v2: ช่องทางที่ได้รับอนุญาตเท่านั้น =================
F.set_enabled("sales_v2", True)
lead3 = db.get_lead(lid3)
assert S.channel_basis(lead3) == {} and P.send_channel(lead3) == ""                     # อีเมลสาธารณะไม่ใช่สิทธิ์
sent.clear()
assert P._send_text(lead3, "s", "b", plain=True) == "" and sent == []                    # ไม่ไล่ไป SMS แทน
db.set_setting("allow_public_email_outreach", "1")
assert S.pick_channel(db.get_lead(lid3)) == "email"
db.set_setting("allow_public_email_outreach", "0")
lid4 = mk("prefline.co.th", email="x@prefline.co.th", email_src="form", line_user_id="U4", consent=1, source="audit")
v4 = dict(v, contact_channel="line", idem_key="4" * 32)
PR.submit(db.get_lead(lid4), v4)
lead4 = db.get_lead(lid4)
assert set(S.channel_basis(lead4)) == {"email", "line"} and S.pick_channel(lead4) == "line"   # ตามที่ลูกค้าเลือก
PUSH_OK[0] = False
sent.clear()
assert P._send_text(lead4, "s", "b") == "" and sent == [("line", "U4")]                  # LINE ล้ม = ไม่ส่งอีเมล/SMS แทน
PUSH_OK[0] = True
lid5 = mk("phone.co.th", consent=1, source="audit")
PR.submit(db.get_lead(lid5), dict(v, contact_channel="phone", contact_value="0812345678", idem_key="5" * 32))
assert S.pick_channel(db.get_lead(lid5)) == "sms"

# ================= อีเมลตอบกลับ: หยุดติดตามก่อน แล้วค่อยจัดประเภท =================
lid6 = mk("reply.co.th", email="c@reply.co.th", email_src="site", status="contacted")
db.add_lead_event(lid6, "contacted", "ทักแล้ว", "sdr")
fu = decision(lid6, "followup")
cancelled_before = []
orig_classify = IB.classify
IB.classify = lambda m, llm=None: cancelled_before.append(db.get_decision(fu["id"])["status"]) or orig_classify(m, llm)
r = IB.process({"from": "c@reply.co.th", "subject": "Re: ผลตรวจ", "body": "ขอบคุณครับ ส่งมาให้ทีมดูแล้ว", "message_id": "m1"})
IB.classify = orig_classify
assert cancelled_before == ["cancelled"]                                                 # ยกเลิกก่อนจัดประเภท
assert IB.process({"from": "c@reply.co.th", "subject": "Re: ผลตรวจ", "body": "ขอบคุณครับ", "message_id": "m1"}) == {"skip": "seen"}   # อีเมลซ้ำ

# ไม่อยู่ (OOO) → พักติดตาม ไม่นับเป็นตอบกลับ
lid7 = mk("ooo.co.th", email="d@ooo.co.th", email_src="site", status="contacted")
first = (dt.datetime.now() - dt.timedelta(days=4)).isoformat(timespec="seconds")
with db.get_conn() as c:
    c.execute(db.q("INSERT INTO lead_events(lead_id,created_at,kind,note,actor) VALUES(?,?,?,?,?)"), (lid7, first, "contacted", "ทักแล้ว", "sdr"))
fu7 = decision(lid7, "followup")
back = (dt.datetime.now() + dt.timedelta(days=10))
r = IB.process({"from": "d@ooo.co.th", "subject": "Automatic reply: Out of office", "message_id": "m2",
                "body": f"I am out of office until {back:%d/%m/%Y}"})
assert r["category"] == "ooo" and db.get_lead(lid7)["status"] == "contacted"
assert db.get_decision(fu7["id"])["status"] == "cancelled"
pu = S.followup_paused_until(lid7)
assert pu and pu[:10] == (back + dt.timedelta(days=1)).date().isoformat()
st = FU.next_step(db.get_lead(lid7))
assert st and not st["due"] and st["due_at"] >= pu                                       # วันที่ 3 ถึงแล้ว แต่รอจนกลับ
assert S.ooo_until("ลาพักร้อน ไม่มีวันที่").date() == (dt.datetime.now() + dt.timedelta(days=7)).date()
assert S.ooo_until("until 31/02") .date() == (dt.datetime.now() + dt.timedelta(days=7)).date()

# ยังไม่ใช่ตอนนี้ → รอจังหวะ (ไม่ปิดเป็นไม่สนใจ)
lid8 = mk("notnow.co.th", email="e@notnow.co.th", email_src="site", status="contacted")
r = IB.process({"from": "e@notnow.co.th", "subject": "Re", "body": "ไม่สนใจตอนนี้ครับ ไว้ก่อน ยังไม่ใช่ตอนนี้", "message_id": "m3"})
l8 = db.get_lead(lid8)
assert r["category"] == "not_now" and l8["status"] != "lost" and l8["opp_override"] == "nurture" and l8["lost_reason"] == "not_now"
assert S.opportunity(l8)["stage"] == "nurture" and FU.next_step(l8) is None
# ไม่สนใจจริง → lost แบบเดิม
lid9 = mk("no.co.th", email="f@no.co.th", email_src="site", status="contacted")
assert IB.process({"from": "f@no.co.th", "subject": "Re", "body": "ไม่สนใจครับ ขอบคุณ", "message_id": "m4"})["category"] == "not_interested"
assert db.get_lead(lid9)["status"] == "lost"

# ================= ขั้นโอกาสขาย =================
for st, want in (("new", "new"), ("contacted", "new"), ("replied", "needs_context"), ("call", "qualified"), ("met", "meeting_held"),
                 ("quoted", "proposal_sent"), ("won", "won"), ("lost", "lost"), ("unsubscribed", "lost")):
    l = mk(f"st-{st}.co.th", status=st)
    o = S.opportunity(db.get_lead(l))
    assert o["stage"] == want, (st, o)
    assert (o["permission"] == "blocked") == (st == "unsubscribed")                       # ขอไม่รับ = สิทธิ์ ไม่ใช่ขั้น
lq = mk("qual.co.th", status="replied", source="audit")
PR.submit(db.get_lead(lq), dict(v, contact_channel="phone", contact_value="0811111111", idem_key="9" * 32, desired_window="1m", web_readiness="own_team"))
assert S.opportunity(db.get_lead(lq))["stage"] == "needs_context"
q0 = S.qualify(db.get_lead(lq))
assert {f["key"]: f["points"] for f in q0["factors"]}["fit"] is None and q0["known"] == 2 and q0["score"] is not None
cv, _ = BC.validate({"primary_service": "ให้เช่าโกดัง"}, db.get_lead(lq))
BC.save(db.get_lead(lq), cv)
assert S.opportunity(db.get_lead(lq))["stage"] == "qualified"
q1 = S.qualify(db.get_lead(lq))
assert q1["known"] == 3 and q1["score"] == 100 and q1["version"] == "qual-v1"
# น้ำหนักปรับได้ · การเปิดดูรายงานไม่นับเป็นสัญญาณ
lo = mk("opened.co.th", status="contacted")
db.add_lead_event(lo, "opened", "เปิดอ่านผลตรวจ", "customer")
assert {f["key"]: f["points"] for f in S.qualify(db.get_lead(lo))["factors"]}["intent"] is None and S.qualify(db.get_lead(lo))["score"] is None
db.set_setting("qual_weights", json.dumps({"intent": 0}))
assert S.weights()["intent"] == 0.0 and S.weights()["fit"] == 1.0
db.set_setting("qual_weights", "")
# นัดยืนยัน/คุยแล้ว/ยกเลิก/ไม่มา
F.set_enabled("line_appointments", True)
AP.save_config({**AP.DEFAULT_CFG, "days": list(range(7)), "times": ["10:00", "14:00"], "lead_hours": 1})
la = mk("meet.co.th", status="call", line_user_id="UM")
slot = AP.slots()[0]
a = AP.request(la, slot, line_user_id="UM")
assert S.opportunity(db.get_lead(la))["stage"] == "qualified"                             # เลือกเวลา ≠ นัดแล้ว
AP.confirm(a["id"], "owner", AP.get(a["id"])["version"])
assert S.opportunity(db.get_lead(la))["stage"] == "meeting_booked"
AP.cancel(a["id"], "customer")
assert S.opportunity(db.get_lead(la))["stage"] == "qualified"                             # ยกเลิก = กลับไปขั้นก่อน
a2 = AP.request(la, AP.slots()[0], line_user_id="UM")
AP.confirm(a2["id"], "owner", AP.get(a2["id"])["version"])
with db.get_conn() as c:
    c.execute(db.q("UPDATE appointments SET slot_start=? WHERE id=?"), ((AP.now() - dt.timedelta(hours=2)).isoformat(timespec="seconds"), a2["id"]))
AP.mark(a2["id"], "no_show", "owner")
assert S.opportunity(db.get_lead(la))["stage"] == "qualified"                             # ไม่มา ≠ คุยแล้ว
# ทีมตั้งขั้น (CAS + ต้องมีเหตุผล)
assert S.set_opportunity(la, "lost", None, "owner", "call") == (False, "เลือกเหตุผลที่ไม่ได้ดีล")
assert S.set_opportunity(la, "nurture", "no_budget", "owner", "replied")[0] is False      # สถานะเปลี่ยนไปแล้ว
fu_la = decision(la, "followup")
assert S.set_opportunity(la, "nurture", "no_budget", "owner", "call") == (True, "ok")
assert S.opportunity(db.get_lead(la))["stage"] == "nurture" and db.get_decision(fu_la["id"])["status"] == "cancelled"
assert S.set_opportunity(la, "lost", "price", "owner", "call")[0] and db.get_lead(la)["status"] == "lost"
assert S.opportunity(db.get_lead(la))["lost_reason_label"] == "ราคา"

# ================= หน้าแอดมิน =================
db.create_tenant("own@s.test", bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Own", is_admin=True)
adm = TestClient(app)
assert adm.post("/login", data={"email": "own@s.test", "password": "pw123456"}).status_code == 200
page = adm.get(f"/admin/leads/{lq}").text
assert 'id="opportunity"' in page and "qual-v1" in page and "ช่องทางที่ส่งได้" in page
cs = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
assert adm.post(f"/admin/leads/{lq}/opportunity", data={"action": "nurture", "expected_status": "replied"}).status_code == 403
adm.post(f"/admin/leads/{lq}/opportunity", data={"csrf_token": cs, "action": "nurture", "reason": "not_now", "expected_status": "replied"})
assert db.get_lead(lq)["opp_override"] == "nurture"
pol = adm.get("/admin/sales/policy").text
assert "อีเมลสาธารณะไม่ใช่การยินยอม" in pol
adm.post("/admin/sales/policy", data={"csrf_token": cs, "public_email": "1", "w_fit": "3", "w_intent": "abc"})
assert db.get_setting("allow_public_email_outreach") == "1" and S.weights()["fit"] == 3.0 and S.weights()["intent"] == 2.0

# ================= การจ่ายเงินต้องยืนยันโดยทีม · ใบเสนอราคาเปิดด้วยลิงก์ =================
lp = mk("pay.co.th", status="quoted", email="g@pay.co.th", email_src="form")
qid = db.add_quote(lp, "Q-9", "paytok", "pro", 1000, 0, 1000, False, 0, 1000, "2026-12-31")
db.update_quote(qid, status="sent")
P.claim_payment(db.get_quote(qid), None)                                                  # ลูกค้ากด "แจ้งชำระ"
assert db.get_lead(lp)["status"] == "quoted" and db.get_quote(qid)["status"] == "claimed"
assert S.opportunity(db.get_lead(lp))["stage"] == "proposal_sent"                         # ยังไม่ won จนทีมยืนยันยอด
assert TestClient(app).get("/q/wrongtoken", follow_redirects=False).status_code in (302, 303, 404)
F.set_enabled("sales_v2", False)
assert 'id="opportunity"' not in adm.get(f"/admin/leads/{lq}").text
print("sales_smoke OK")
