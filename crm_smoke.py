"""ทดสอบ CRM งานขายของเจ้าของ (/admin/deals):
· ขั้นดีล: อยากคุย (wants_call) → call · โทรติด/ประชุม → met (ขั้นที่ไกลกว่าคงไว้) · ส่งใบเสนอราคา → quoted · ยืนยันยอดเข้า → won · ตอบกลับเฉย ๆ ยังเป็น replied
· รายการโทร: อยากคุยแต่โทรไม่ติด → โทรซ้ำตามรอบ · คุยแล้วยังไม่มีใบ → ขึ้นรายการ
· มูลค่าดีล: ราคา Pro → ใบเสนอราคาที่เปิดอยู่ → ที่เจ้าของใส่เอง (ว่าง = กลับค่าตั้งต้น) · คอลัมน์มีจำนวน + ยอดรวม/เดือน
· กระดาน: การ์ดครบ · ลูกค้าที่จ่ายแล้วไม่อยู่ในดีลที่เปิด · ตัวกรองค้นหา/ที่มา/เลยกำหนด · ย้ายขั้น (fetch JSON + ฟอร์มสำรอง)
· งานถัดไป: ปุ่มลัดเวลาไทย · เตือนครั้งเดียว (ภายใน 15 นาที/เลยกำหนด) · อีเมลเฉพาะนัดประชุม · เปลี่ยนเวลา = เตือนใหม่ · cron --crm
· สรุปเช้า "งานขายวันนี้" · กิจกรรมลงไทม์ไลน์พร้อมไอคอน · ผู้ติดต่อหลายคน (เพิ่ม/แก้/ลบ) · คำขอนัดคุยจากหน้าเว็บ → ผูกดีลเดิม/ดีลใหม่
ข้อมูลทดสอบเป็นของสมมติทั้งหมด (example.com) · ไม่ยิงเครือข่าย · ไม่พิมพ์เบอร์โทรออกมา"""
import os, tempfile, json, re, sys, datetime as dt
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-crm-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["GEO_DEEP_AUDIT"] = "0"; os.environ["GEO_CS"] = "0"
import bcrypt
from zoneinfo import ZoneInfo
from fastapi.testclient import TestClient
import app.db as db, app.pipeline as P, app.crm as CRM, app.closer as C, app.leads as L, app.execs as X, app.site_health as SH
import app.mailer as mailer, app.line_oa as LO, app.office as O, app.followup as F, app.ai_client as ai
from app.main import app

db.init_db()
BKK = ZoneInfo("Asia/Bangkok")
P.RUN_INLINE = True
C.FETCH = lambda u: ""
ai._read_setting = lambda k: None
ai._chat = lambda m, max_tokens=2048, timeout=120: (_ for _ in ()).throw(RuntimeError("no model"))
alerts, mails = [], []
P.alert_admins = lambda msg, link, type_="lead", subject=None: alerts.append({"msg": msg, "link": link, "type": type_, "subject": subject})
mailer.config = lambda: {"host": "x"}
mailer.is_deliverable = lambda e: bool(e) and e.endswith("example.com")
mailer.send = lambda to, subject, html, text="", attachments=(): mails.append((to, subject))
LO._post = lambda path, payload: True
REPORT = {"checks": [{"key": k, "label": f"ข้อ {k}", "status": "fail", "detail": ""} for k in ("csr", "noindex")]}
SH.run_checks = lambda site: REPORT
PRO = CRM.pro_price()

def mk(dom, status="contacted", source="prospect", **kw):
    lid = db.add_lead(f"https://{dom}", dom, source=source, email=kw.pop("email", f"owner@{dom}"), name=kw.pop("name", ""), contact=kw.pop("contact", ""))
    db.update_lead(lid, status=status, audit_json=json.dumps(REPORT), score=60, token=f"t-{dom.replace('.', '-')}", consent=1, **kw)
    return lid
st = lambda lid: db.get_lead(lid)["status"]
kinds = lambda lid: [e["kind"] for e in db.list_lead_events(lid, 300)]
card = lambda b, lid: next((c for col in b["columns"] for c in col["cards"] if c["id"] == lid), None)
col = lambda b, key: next(c for c in b["columns"] if c["key"] == key)

# ================= 1) ขั้นดีล =================
assert L.PIPELINE == ["new", "contacted", "replied", "call", "met", "quoted", "won"] and L.STATUS_LABEL["call"] == "อยากคุย" and L.STATUS_LABEL["met"] == "คุยแล้ว"
assert {"call", "met"}.isdisjoint(L.NO_CONTACT) and [k for k, _ in CRM.BOARD] == ["new", "contacted", "call", "met", "quoted", "won"]
hot = mk("hot.example.com")
P.lead_replied(db.get_lead(hot), "สนใจครับ อยากคุย", via="อีเมล", wants_call=True)
assert st(hot) == "call" and "wants_call" in kinds(hot) and any("อยากคุย" in a["msg"] for a in alerts)
warm = mk("warm.example.com")
P.lead_replied(db.get_lead(warm), "ขอบคุณครับ", via="อีเมล", wants_call=False)
assert st(warm) == "replied"                                                     # ตอบกลับเฉย ๆ = เหมือนเดิม
P.lead_replied(db.get_lead(warm), "ขอนัดคุยหน่อย", via="LINE")
assert st(warm) == "call"                                                        # ตอบกลับแล้วค่อยขอคุย → อยากคุย
far = mk("far.example.com", "quoted")
P.lead_replied(db.get_lead(far), "ขอคุยอีกรอบ", via="เว็บ")
assert st(far) == "quoted"                                                       # ขั้นที่ไกลกว่าแล้วคงไว้
lostl = mk("back.example.com", "lost")
P.lead_replied(db.get_lead(lostl), "เปลี่ยนใจครับ", via="อีเมล")
assert st(lostl) == "call"
assert F.eligible(db.get_lead(hot)).startswith("สถานะ")                           # อยากคุย ไม่ส่งชุดติดตามผลอัตโนมัติ
assert C.trigger(db.get_lead(hot), dt.datetime.now())["kind"] == "wants_call"
# โทรไม่ติด → ยังอยากคุย · โทรซ้ำตามรอบ (1 วัน)
C.log_call(db.get_lead(warm), "no_answer", "", "owner")
assert st(warm) == "call" and C.trigger(db.get_lead(warm), dt.datetime.now())["kind"] == "call_retry"
assert "warm.example.com" not in [x["lead"]["domain"] for x in C.call_list()]
assert "warm.example.com" in [x["lead"]["domain"] for x in C.call_list(now=dt.datetime.now() + dt.timedelta(days=1, hours=1))]
# โทรติด → คุยแล้ว
C.log_call(db.get_lead(hot), "think", "ขอดูขอบเขตงานก่อน", "owner")
assert st(hot) == "met" and C.trigger(db.get_lead(hot), dt.datetime.now())["kind"] == "met"
C.log_call(db.get_lead(far), "will_pay", "", "owner"); assert st(far) == "quoted"   # ไกลกว่าคุยแล้ว = คงไว้
assert C.script(db.get_lead(warm))["opener"].startswith("สวัสดีครับ")
# ส่งใบเสนอราคา → quoted · ยืนยันยอด → won
q = P.create_quote(db.get_lead(hot), "pro"); P.send_quote(q)
assert st(hot) == "quoted"
db.update_quote(q["id"], status="claimed"); P.confirm_payment(db.get_quote(q["id"]), "owner")
assert st(hot) == "won" and L.customer_brand("hot.example.com")
dup = mk("hot.example.com", "new")                                                 # ลูกค้าเดิมมาลองตรวจฟรีอีก = ไม่ใช่ดีลที่เปิด
assert L.funnel(db.list_leads(None, 1000))["call"] >= 2 and L.funnel(db.list_leads(None, 1000))["met"] >= 0
assert all(v["status"] in O.VISITOR_STATUSES[:4] + ("quoted", "won") for v in O.state()["visitors"])   # ออฟฟิศ: อยากคุย/คุยแล้วนั่งแถวตอบกลับ
print("stages: อยากคุย → call · ตอบกลับคงเดิม · ไกลกว่าคงไว้ · โทรไม่ติดโทรซ้ำ · โทรติด → met · ใบเสนอ → quoted · จ่าย → won · ออฟฟิศ OK")

# ================= 2) มูลค่าดีล =================
v = mk("value.example.com", "met")
assert CRM.deal_value(db.get_lead(v)) == {"value": PRO, "src": "list"}
qv = P.create_quote(db.get_lead(v), "pro", custom=15000)
assert CRM.deal_value(db.get_lead(v)) == {"value": db.get_quote(qv["id"])["net"], "src": "quote"} and db.get_quote(qv["id"])["net"] == 15000
assert CRM.set_value(v, "฿12,500") == 12500 and CRM.deal_value(db.get_lead(v)) == {"value": 12500, "src": "manual"}
CRM.set_value(v, ""); assert CRM.deal_value(db.get_lead(v))["src"] == "quote"
assert CRM.deal_value(db.get_lead(hot)) == {"value": db.get_quote(q["id"])["net"], "src": "won"}
print("value: ราคา Pro → ใบเปิด → ตั้งเอง · ว่าง = ค่าตั้งต้น · ปิดแล้ว = ยอดจ่าย OK")

# ================= 3) กระดาน + ตัวกรอง =================
admin = "owner@crm.example.com"
db.create_tenant(admin, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Owner", is_admin=True)
cl = TestClient(app); cl.post("/login", data={"email": admin, "password": "pw123456"})
assert TestClient(app).get("/admin/deals", follow_redirects=False).status_code == 303          # ไม่ล็อกอิน = ไม่เห็น
newl = mk("fresh.example.com", "new", source="import")
quoted = mk("deal.example.com", "contacted", name="คุณดีล")
q2 = P.create_quote(db.get_lead(quoted), "pro", custom=20000); P.send_quote(q2); assert st(quoted) == "quoted"
b = CRM.board()
assert card(b, dup) is None and card(b, hot) and card(b, hot)["column"] == "won"                 # ลูกค้าจ่ายแล้ว: ไม่อยู่ในดีลเปิด · ปิดได้ 30 วัน
assert card(b, warm)["column"] == "call" and card(b, warm)["wants_call"] is False                  # โทรแล้ว (ไม่ติด) = ไม่ใช่ 🔥 ใหม่
assert card(b, lostl)["wants_call"] and card(b, lostl)["no_next"]                                  # อยากคุยแต่ยังไม่มีงานถัดไป = ติดธง
assert card(b, warm)["column"] == "call" and card(b, newl)["column"] == "new" and not card(b, newl)["no_next"]
mk("reply.example.com", "replied"); b = CRM.board()
assert next(c for c in col(b, "contacted")["cards"] if c["domain"] == "reply.example.com")["replied"]   # ตอบกลับอยู่คอลัมน์ทักแล้ว
cq = col(b, "quoted"); assert cq["total"] == sum(c["value"] for c in cq["cards"]) and card(b, quoted)["value"] == 20000
assert b["kpi"]["pipeline"] == sum(col(b, k)["total"] for k in ("call", "met", "quoted"))
r = cl.get("/admin/deals"); assert r.status_code == 200
for lb in ("ใหม่", "ทักแล้ว", "อยากคุย", "คุยแล้ว", "ส่งข้อเสนอ", "ปิดได้"):
    assert f"<h3>{lb}</h3>" in r.text, lb
assert "deal.example.com" in r.text and f"฿{cq['total']:,}" in r.text and "🔥 อยากคุย" in r.text and "ยังไม่มีงานถัดไป" in r.text
assert 'id="deal-%d"' % dup not in r.text and "/admin/deals/%d/stage" % quoted in r.text and 'draggable="true"' in r.text
assert 'href="/admin/deals"' in r.text and 'href="/admin/customers"' in r.text                    # เมนูข้าง
r = cl.get("/admin/deals?q=deal.example"); assert 'id="deal-%d"' % quoted in r.text and 'id="deal-%d"' % warm not in r.text
r = cl.get("/admin/deals?q=คุณดีล"); assert 'id="deal-%d"' % quoted in r.text
r = cl.get("/admin/deals?source=import"); assert 'id="deal-%d"' % newl in r.text and 'id="deal-%d"' % quoted not in r.text
print("board: 6 คอลัมน์ · จำนวน + ยอดรวม · ลูกค้าไม่อยู่ในดีลเปิด · ธงไม่มีงานถัดไป · ค้นหา · ที่มา OK")

# ย้ายขั้น: fetch JSON และฟอร์มสำรอง
J = {"X-Requested-With": "fetch", "Accept": "application/json"}
r = cl.post(f"/admin/deals/{newl}/stage", data={"status": "contacted"}, headers=J)
assert r.json() == {"ok": True, "status": "contacted", "label": "ทักแล้ว", "column": "contacted"} and st(newl) == "contacted" and "status" in kinds(newl)
r = cl.post(f"/admin/deals/{newl}/stage", data={"status": "nope"}, headers=J); assert r.status_code == 400
r = cl.post(f"/admin/deals/{newl}/stage", data={"status": "met"}, headers={"referer": "http://testserver/admin/deals?q=x"}, follow_redirects=False)
assert r.status_code == 303 and r.headers["location"].startswith("/admin/deals?q=x") and st(newl) == "met"
r = cl.post(f"/admin/deals/{newl}/stage", data={"status": "met"}, headers={"referer": "https://evil.example.com/x"}, follow_redirects=False)
assert r.headers["location"].startswith("/admin/leads/")                                         # referer ภายนอกไม่ใช้
assert TestClient(app).post(f"/admin/deals/{newl}/stage", data={"status": "won"}, headers=J).status_code == 403
r = cl.post(f"/admin/deals/{v}/value", data={"value": "18,000"}, headers=J); assert r.json()["value"] == 18000 and r.json()["src"] == "manual"
print("stage/value: ย้ายด้วย fetch · ขั้นผิด 400 · ฟอร์มสำรองกลับหน้าเดิม · ไม่รับ referer ภายนอก · ไม่ล็อกอิน 403 · แก้มูลค่า OK")

# ================= 4) งานถัดไป + เตือน =================
now = CRM.now_local()
nb = now.astimezone(BKK)
at = CRM.preset_at("tomorrow10", now=now); t = CRM.to_bkk(at)
assert t.hour == 10 and t.minute == 0 and t.date() == (nb + dt.timedelta(days=1)).date()
assert CRM.to_bkk(CRM.preset_at("today14", now=now)).hour == 14 and CRM.to_bkk(CRM.preset_at("plus3", now=now)).date() == (nb + dt.timedelta(days=3)).date()
assert CRM.to_bkk(CRM.preset_at("custom", "2026-11-02T15:30")).strftime("%Y-%m-%d %H:%M") == "2026-11-02 15:30"
assert CRM.preset_at("custom", "ไม่ใช่เวลา") is None and CRM.fmt_ts(at, now).startswith("พรุ่งนี้ 10:00")
r = cl.post(f"/admin/deals/{warm}/next", data={"action": "โทรนัดสาธิต", "preset": "tomorrow10", "kind": "call"}, follow_redirects=False)
assert r.status_code == 303 and db.get_lead(warm)["next_action"] == "โทรนัดสาธิต" and db.get_lead(warm)["next_at"] == at and "next_action" in kinds(warm)
cl.post(f"/admin/deals/{lostl}/next", data={"action": "นัดประชุม", "preset": "custom", "at": "2030-01-15T09:00", "kind": "meeting"})
assert CRM.to_bkk(db.get_lead(lostl)["next_at"]).strftime("%H:%M") == "09:00" and db.get_lead(lostl)["next_kind"] == "meeting"
b = CRM.board(); assert not card(b, lostl)["no_next"] and card(b, warm)["next_fmt"].startswith("พรุ่งนี้")
# เตือน: ภายใน 15 นาที · เลยกำหนด · ยังไกล = ไม่เตือน · ครั้งเดียว · นัดประชุมส่งอีเมล
iso = lambda d: d.isoformat(timespec="seconds")
soon = mk("soon.example.com", "met"); CRM.set_next(soon, "โทรยืนยันขอบเขต", iso(now + dt.timedelta(minutes=10)), "call")
late = mk("late.example.com", "call"); CRM.set_next(late, "ส่งใบเสนอราคา", iso(now - dt.timedelta(hours=3)), "other")
meet = mk("meet.example.com", "met"); CRM.set_next(meet, "ประชุมสรุปงาน", iso(now + dt.timedelta(minutes=5)), "meeting")
later = mk("later.example.com", "met"); CRM.set_next(later, "โทรตาม", iso(now + dt.timedelta(hours=2)), "call")
gone = mk("gone.example.com", "met"); CRM.set_next(gone, "โทร", iso(now - dt.timedelta(minutes=30)), "call"); db.update_lead(gone, status="unsubscribed")
db.add_lead_contact(soon, name="คุณเร็ว", phone="081-000-0000")
alerts.clear()
res = CRM.run_reminders(now)
got = {a["domain"]: a for a in res["alerted"]}
assert set(got) == {"soon.example.com", "late.example.com", "meet.example.com"}, set(got)
assert got["late.example.com"]["overdue"] and not got["soon.example.com"]["overdue"] and got["meet.example.com"]["email"] and not got["soon.example.com"]["email"]
m = {a["link"]: a for a in alerts}
assert m[f"/admin/leads/{late}"]["msg"].startswith("⏰ เลยกำหนด") and "ส่งใบเสนอราคา" in m[f"/admin/leads/{late}"]["msg"]
assert m[f"/admin/leads/{soon}"]["msg"].startswith("⏰ อีก ") and "คุณเร็ว" in m[f"/admin/leads/{soon}"]["msg"] and m[f"/admin/leads/{soon}"]["subject"] is None
assert m[f"/admin/leads/{meet}"]["subject"] and "meet.example.com" in m[f"/admin/leads/{meet}"]["subject"]
assert CRM.run_reminders(now)["n"] == 0 and CRM.run_reminders(now + dt.timedelta(minutes=30))["n"] == 0   # เตือนครั้งเดียว
assert CRM.run_reminders(now + dt.timedelta(hours=1, minutes=50))["alerted"][0]["domain"] == "later.example.com"
CRM.set_next(late, "ส่งใบเสนอราคา (เลื่อน)", iso(now - dt.timedelta(minutes=1)), "other")              # เปลี่ยนนัด = เตือนใหม่
assert [a["domain"] for a in CRM.run_reminders(now)["alerted"]] == ["late.example.com"]
import io, contextlib, run_monitors as RM                     # ในโปรเซสเดียว (ไม่เปิดโปรเซสใหม่ — .env ของเครื่องจะชี้ฐานข้อมูลจริง)
CRM.set_next(later, "โทรตาม (เลื่อน)", iso(CRM.now_local() - dt.timedelta(minutes=5)), "call")
buf = io.StringIO(); sys.argv = ["run_monitors.py", "--crm"]
with contextlib.redirect_stdout(buf):
    RM.main()
assert "เตือน 1 รายการ: later.example.com (เลยกำหนด)" in buf.getvalue(), buf.getvalue()
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    RM.main()
assert buf.getvalue() == ""                                                                   # ไม่มีอะไรถึงกำหนด = เงียบ
print("next/remind: ปุ่มลัดเวลาไทย · ภายใน 15 นาที/เลยกำหนดเตือนครั้งเดียว · ไกลยังไม่เตือน · ขอไม่รับข้าม · ประชุม = อีเมล · เลื่อนนัดเตือนใหม่ · --crm รันได้ OK")

# ================= 5) สรุปเช้า =================
CRM.set_next(soon, "โทรยืนยันขอบเขต", iso(now + dt.timedelta(minutes=10)), "call")
idle = mk("idle.example.com", "call")
dg = CRM.digest_text(now)
assert dg.startswith("งานขายวันนี้:") and "ส่งใบเสนอราคา (เลื่อน) — late.example.com" in dg and "idle.example.com" in dg
endday = now.astimezone(BKK).replace(hour=23, minute=59)
if now + dt.timedelta(minutes=10) <= endday.astimezone().replace(tzinfo=None):
    assert "โทรยืนยันขอบเขต — soon.example.com" in dg
assert "เลยกำหนด" in dg and "อยากคุยแต่ยังไม่มีนัด" in dg and "dup" not in dg
txt = X.morning_digest("https://geo.example.com")["text"]
assert "งานขายวันนี้:" in txt and "idle.example.com" in txt
print("digest: งานขายวันนี้ · เลยกำหนด · อยากคุยแต่ยังไม่มีนัด · อยู่ในสรุปเช้า OK")

# ================= 6) กิจกรรม =================
act = mk("act.example.com", "call")
CRM.set_next(act, "โทรนัด", iso(now + dt.timedelta(hours=1)), "call")
r = cl.post(f"/admin/deals/{act}/activity", data={"kind": "call", "outcome": "ไม่รับสาย", "reached": "0"}, headers={"referer": f"http://testserver/admin/leads/{act}"}, follow_redirects=False)
assert r.headers["location"].startswith(f"/admin/leads/{act}") and st(act) == "call" and db.get_lead(act)["next_action"] == "โทรนัด"
assert json.loads(db.get_lead(act)["call_json"])["calls"][-1]["outcome"] == "no_answer"
r = cl.post(f"/admin/deals/{act}/activity", data={"kind": "call", "outcome": "คุยแล้ว สนใจรายปี", "done_next": "1",
                                                  "next_action": "ส่งใบเสนอราคา", "preset": "today14", "next_kind": "email"})
assert st(act) == "met" and db.get_lead(act)["next_action"] == "ส่งใบเสนอราคา" and db.get_lead(act)["next_kind"] == "email"
assert P.wants_call_pending(act) is None
cl.post(f"/admin/deals/{act}/activity", data={"kind": "meeting", "outcome": "ประชุมสาธิต", "done_next": "1"})
assert db.get_lead(act)["next_action"] is None and "meeting" in kinds(act)                                # ปิดงานถัดไปเดิม
cl.post(f"/admin/deals/{act}/activity", data={"kind": "email_out", "outcome": "ส่งสรุปการประชุม"})
cl.post(f"/admin/deals/{act}/activity", data={"kind": "line", "outcome": "ส่งตัวอย่างงานทาง LINE"})
cl.post(f"/admin/deals/{act}/activity", data={"kind": "note", "outcome": "ผู้ตัดสินใจคือหุ้นส่วน"})
r = cl.post(f"/admin/deals/{act}/activity", data={"kind": "note", "outcome": "  "}, follow_redirects=False); assert "notice=" in r.headers["location"]
r = cl.post(f"/admin/deals/{act}/activity", data={"kind": "hack", "outcome": "x"}, follow_redirects=False); assert "notice=" in r.headers["location"]
ks = kinds(act); assert {"call", "meeting", "email_out", "line", "note"} <= set(ks) and st(act) == "met"
fresh2 = mk("line.example.com", "new"); CRM.log_activity(db.get_lead(fresh2), "line", "ทักไปทาง LINE"); assert st(fresh2) == "contacted"
mt = mk("meet2.example.com", "replied"); CRM.log_activity(db.get_lead(mt), "meeting", ""); assert st(mt) == "met"
assert all(k in L.EVENT_LABEL and k in L.EVENT_ICON for k in CRM.ACT_KINDS)
r = cl.get(f"/admin/leads/{act}"); assert r.status_code == 200
for s_ in ("🤝", "📞", "✉️", "📝", "ประชุมสาธิต", "ผู้ตัดสินใจคือหุ้นส่วน", "บันทึกกิจกรรม", "มูลค่าดีล / เดือน", "งานถัดไป (เวลาไทย)", 'aria-current="step"'):
    assert s_ in r.text, s_
assert CRM.fmt_short(db.list_lead_events(act)[0]["created_at"]) in r.text                                 # เวลาไทยในไทม์ไลน์
# บันทึกผลโทรจากหน้าโทรปิดดีลก็ขึ้นไทม์ไลน์และเลื่อนขั้น
cc = mk("closer.example.com", "call")
cl.post(f"/admin/calls/{cc}/log", data={"outcome": "think", "note": "ขอคิดก่อน"}); assert st(cc) == "met" and "call" in kinds(cc)
print("activity: โทรไม่ติดไม่ย้าย · โทรติด/ประชุม → คุยแล้ว · ปิด/ตั้งงานถัดไป · LINE/อีเมล ลีดใหม่ → ทักแล้ว · ไทม์ไลน์มีไอคอน+เวลาไทย · /admin/calls เลื่อนขั้น OK")

# ================= 7) ผู้ติดต่อ =================
co = mk("contact.example.com", "met", name="คุณหลัก", email="boss@contact.example.com", contact="LINE: @contactshop")
r = cl.get(f"/admin/leads/{co}"); assert r.status_code == 200
rows = db.list_lead_contacts(co); assert len(rows) == 1 and rows[0]["name"] == "คุณหลัก" and rows[0]["email"] == "boss@contact.example.com" and rows[0]["line_id"] == "@contactshop"
cl.get(f"/admin/leads/{co}"); assert len(db.list_lead_contacts(co)) == 1                                  # สร้างครั้งเดียว
cl.post(f"/admin/deals/{co}/contacts", data={"name": "คุณการตลาด", "role": "การตลาด", "phone": "02-000-0000", "email": "MKT@contact.example.com"})
rows = db.list_lead_contacts(co); assert len(rows) == 2 and rows[1]["email"] == "mkt@contact.example.com" and rows[1]["role"] == "การตลาด"
r = cl.post(f"/admin/deals/{co}/contacts", data={"name": " "}, follow_redirects=False); assert len(db.list_lead_contacts(co)) == 2
cl.post(f"/admin/deals/{co}/contacts/{rows[1]['id']}", data={"name": "คุณการตลาด", "role": "ผู้จัดการการตลาด", "phone": "", "email": "mkt@contact.example.com", "line_id": "", "note": "ตัดสินใจร่วม"})
x = db.get_lead_contact(rows[1]["id"]); assert x["role"] == "ผู้จัดการการตลาด" and x["note"] == "ตัดสินใจร่วม" and x["phone"] is None
cl.post(f"/admin/deals/{act}/contacts/{rows[1]['id']}/delete"); assert db.get_lead_contact(rows[1]["id"])            # ผิดดีล = ไม่ลบ
cl.post(f"/admin/deals/{co}/contacts/{rows[1]['id']}/delete"); assert db.get_lead_contact(rows[1]["id"]) is None
r = cl.get(f"/admin/leads/{co}"); assert "ผู้ติดต่อ (1)" in r.text and "@contactshop" in r.text
assert CRM.split_contact("ติดต่อ line id: shop_x หรือ 0812345678")["line_id"] == "shop_x" and CRM.split_contact("a@b.example.com")["email"] == "a@b.example.com"
print("contacts: ผู้ติดต่อหลักจากข้อมูลลีด (ครั้งเดียว) · เพิ่ม/แก้/ลบ · ผิดดีลไม่ลบ · อ่านช่องทางอิสระ OK")

# ================= 8) คำขอนัดคุยจากหน้าเว็บ =================
pub = TestClient(app)
assert pub.post("/api/contact", json={"name": "คุณขอคุย", "contact": "ask@shop.example.com", "plan": "นัดคุย", "message": "อยากรู้เรื่องแพ็กเกจ"}).json()["ok"]
assert pub.post("/api/contact", json={"name": "คุณร้านใหม่", "contact": "LINE: @newshop", "plan": "นัดคุย", "message": ""}).json()["ok"]
assert pub.post("/api/contact", json={"name": "สแปม", "contact": "x", "plan": "", "message": "buy now"}).json()["ok"]
assert any(a["link"] == "/admin/deals#requests" and "คำขอนัดคุยใหม่" in a["msg"] for a in alerts)
reqs = {c["name"]: c for c in db.list_new_contacts()}
r = cl.get("/admin/deals"); assert "คำขอนัดคุยใหม่" in r.text and "คุณขอคุย" in r.text and "คุณร้านใหม่" in r.text and "อยากรู้เรื่องแพ็กเกจ" in r.text
# ผูกกับดีลเดิม
ex = mk("shop.example.com", "contacted", email=None)
r = cl.post(f"/admin/deals/requests/{reqs['คุณขอคุย']['id']}/link", data={"lead": "shop.example.com"}, follow_redirects=False)
assert r.status_code == 303 and f"#deal-{ex}" in r.headers["location"]
L1 = db.get_lead(ex); assert L1["status"] == "call" and L1["next_action"] == CRM.REQUEST_NEXT and L1["email"] == "ask@shop.example.com" and L1["name"] == "คุณขอคุย"
due_h = (CRM._ts(L1["next_at"]) - CRM.now_local()).total_seconds() / 3600; assert 1.9 < due_h <= 2.01, due_h
assert "wants_call" in kinds(ex) and any(c["name"] == "คุณขอคุย" and c["email"] == "ask@shop.example.com" for c in db.list_lead_contacts(ex))
c1 = db.get_contact(reqs["คุณขอคุย"]["id"]); assert c1["status"] == "handled" and c1["lead_id"] == ex
assert CRM.link_request(reqs["คุณขอคุย"]["id"], "shop.example.com")[1] == "คำขอนี้จัดการไปแล้ว"
assert CRM.link_request(reqs["คุณร้านใหม่"]["id"], "nothing-here.example.com")[0] is None                     # หาไม่เจอ = ไม่ปิดคำขอ
assert db.get_contact(reqs["คุณร้านใหม่"]["id"])["status"] == "new"
# ดีลใหม่จากเว็บที่เจ้าของพิมพ์ → ตรวจเว็บแบบลีดที่เพิ่มเอง
r = cl.post(f"/admin/deals/requests/{reqs['คุณร้านใหม่']['id']}/link", data={"domain": "https://www.newshop.example.com/about"}, follow_redirects=False)
nl = db.find_lead_by_domain("newshop.example.com"); assert nl and nl["source"] == "manual" and nl["status"] == "call" and nl["audit_json"] and nl["score"] is not None
assert nl["next_action"] == CRM.REQUEST_NEXT and db.list_lead_contacts(nl["id"])[0]["line_id"] == "@newshop" and "audit" in kinds(nl["id"])
assert db.get_contact(reqs["คุณร้านใหม่"]["id"])["lead_id"] == nl["id"]
r = cl.post(f"/admin/deals/requests/{reqs['สแปม']['id']}/link", data={"domain": "not a domain"}, follow_redirects=False); assert "notice=" in r.headers["location"]
cl.post(f"/admin/deals/requests/{reqs['สแปม']['id']}/dismiss"); assert db.get_contact(reqs["สแปม"]["id"])["status"] == "handled"
r = cl.get("/admin/deals"); assert "คำขอนัดคุยใหม่" not in r.text and 'id="deal-%d"' % nl["id"] in r.text
r = cl.get("/admin/deals?overdue=1"); assert 'id="deal-%d"' % late in r.text and 'id="deal-%d"' % nl["id"] not in r.text
print("requests: ฟอร์มหน้าเว็บขึ้นกล่องบนกระดาน · ผูกดีลเดิม (ผู้ติดต่อ + กิจกรรม + อยากคุย + โทรนัดใน 2 ชม.) · ดีลใหม่ตรวจเว็บ · หาไม่เจอไม่ปิด · ซ่อนสแปม · เลยกำหนด OK")
print("ALL OK")
