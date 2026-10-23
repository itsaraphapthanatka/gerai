"""ทดสอบฝ่ายส่งอีเมล: ตรวจ SPF/DKIM/DMARC/บัญชีดำ · ตรวจเนื้อหาเสี่ยงสแปม · ทดลอง A/B (แบ่งคงที่ · แนวเขียนเข้า prompt/แม่แบบ · นับเมื่อส่งอีเมลจริง
· ตัดสินที่ 30 รายและ p < 0.05 · กันขอไม่รับ · ครบ 120 ไม่ต่างคงเดิม) · สัญญาณ 30 วัน → หยุดส่งอัตโนมัติ · Resend webhook (ลายเซ็น Svix · เด้ง · ร้องเรียน · ส่งถึง)
· ทดสอบกล่อง (เก็บรหัสเข้ารหัส · ส่ง · ตรวจผล) · เพิ่มจำนวนทักเมื่อสุขภาพดี · หน้าเว็บ · แชต CEO"""
import os, tempfile, json, datetime as dt
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke-deliv-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["GEO_DEEP_AUDIT"] = "0"; os.environ["GEO_CS"] = "0"
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.deliverability as D, app.execs as X, app.pipeline as P, app.mailer as mailer, app.ai_client as ai
import app.site_health as sh, app.line_oa as LO, app.ceo_chat as C, app.office as O, app.skills as SK, app.prospecting as PR
from app.main import app
db.init_db()
ai._read_setting = lambda k: None
ai._chat = lambda m, max_tokens=2048, timeout=120: (_ for _ in ()).throw(RuntimeError("no model"))
mails = []; mailer.config = lambda: {"host": "x", "smtp_from": "report@sender.test"}
mailer.send = lambda to, subj, html, text="", attachments=(): mails.append((to, subj, text))
mailer.is_deliverable = lambda e: bool(e) and e.endswith(".co.th")
LO._post = lambda path, payload: True
alerts = []; P.alert_admins = lambda msg, link, type_="lead", subject=None: alerts.append(msg)
P.RUN_INLINE = True; C.RUN_INLINE = True
REPORT = {"checks": [{"key": k, "label": f"ข้อ {k}", "status": "fail", "detail": ""} for k in ("csr", "noindex", "robots")]}
sh.run_checks = lambda site, **kw: REPORT
db.set_setting("auto_override_outreach", "1"); db.set_setting("auto_override_lead_review", "1")

# ---- DNS ----
GOOD = {("sender.test", "TXT"): ["v=spf1 include:_spf.mx.cloudflare.net ~all"], ("send.sender.test", "TXT"): ["v=spf1 include:amazonses.com ~all"],
        ("resend._domainkey.sender.test", "TXT"): ["p=MIGfMA0GCSq"], ("_dmarc.sender.test", "TXT"): ["v=DMARC1; p=quarantine;"]}
zone = {}
D.HOOKS["dns"] = lambda name, typ: zone.get((name, typ), [])
zone.update(GOOD); r = D.check_dns(); assert r["ok"] and r["domain"] == "sender.test", r
assert {i["key"]: i["status"] for i in r["items"]}["dkim"] == "ok" and "resend" in [i for i in r["items"] if i["key"] == "dkim"][0]["detail"]
zone.pop(("_dmarc.sender.test", "TXT")); r = D.check_dns(); assert not r["ok"] and any(i["key"] == "dmarc" and i["status"] == "fail" for i in r["items"])
zone.update(GOOD); zone[("sender.test.dbl.spamhaus.org", "A")] = ["127.255.255.254"]; r = D.check_dns(); assert r["ok"]                # ตรวจไม่ได้ ≠ ติดบัญชีดำ
zone[("sender.test.multi.surbl.org", "A")] = ["127.0.0.64"]; r = D.check_dns(); assert not r["ok"] and any("SURBL" in i["label"] and i["status"] == "fail" for i in r["items"])
zone.pop(("sender.test.multi.surbl.org", "A")); zone[("sender.test", "TXT")] = ["v=spf1 a ~all", "v=spf1 mx ~all"]; r = D.check_dns(); assert not r["ok"]
zone.update(GOOD)
print("dns: SPF/DKIM/DMARC · บัญชีดำ (ติด/ตรวจไม่ได้) · SPF ซ้ำ OK")

# ---- เนื้อหา ----
assert D.spam_flags("ผลตรวจเว็บ shop.co.th", "สวัสดีครับ ดูผลที่ https://a.b") == []
assert D.spam_flags("ด่วนที่สุด ลดราคา!!", "x") and D.spam_flags("ok", " ".join(["https://x.y"] * 4)) and D.spam_flags("FREE MONEY NOW", "x")
print("content: คำเสี่ยง · ลิงก์เกิน · ตัวพิมพ์ใหญ่ OK")

# ---- A/B: แบ่งคงที่ · prompt · แม่แบบ ----
def mk(dom):
    lid = db.add_lead(f"https://{dom}", dom, source="prospect", email=f"info@{dom}")
    db.update_lead(lid, audit_json=json.dumps(REPORT), score=70); return db.get_lead(lid)
lead0 = mk("zero.co.th")
assert D.style_for(lead0)["exp"] is None and not D.active_experiment()                          # ปิดอยู่ = ไม่ทดลอง
db.set_setting("deliv_exp_on", "1"); assert D.active_experiment()["key"] == "subject_q"
arms = [D.style_for({"id": i})["arm"] for i in range(1, 81)]; assert 25 < arms.count("B") < 55 and D.style_for({"id": 7})["arm"] == D.style_for({"id": 7})["arm"]
bl = next(i for i in range(1, 81) if D.style_for({"id": i})["arm"] == "B"); al = next(i for i in range(1, 81) if D.style_for({"id": i})["arm"] == "A")
assert "คำถามสั้น" in D.style_for({"id": bl})["prompt"] and D.style_for({"id": al})["prompt"] == ""
seen = []
def llm(msgs):
    seen.append(msgs[-1]["content"]); return json.dumps({"subject": "เว็บคุณหาเจอไหม", "body": "สวัสดีครับ " * 20})
summary = [{"label": "ข้อ csr", "why": "บอทอ่านไม่ได้"}]
d = X.draft_outreach({"id": bl, "domain": "b.co.th"}, summary, "https://r/x", llm, D.style_for({"id": bl})); assert "แนวเขียนเพิ่ม" in seen[-1] and d["source"] == "llm"
X.draft_outreach({"id": al, "domain": "a.co.th"}, summary, "https://r/x", llm, D.style_for({"id": al})); assert "แนวเขียนเพิ่ม" not in seen[-1]
t = X.draft_outreach({"id": bl, "domain": "b.co.th"}, summary, "https://r/x", lambda m: "not json", D.style_for({"id": bl}))
assert t["subject"] == "b.co.th: ลูกค้าหาคุณใน Google และ AI เจอไหม?" and t["source"].startswith("template")
sp = X.draft_outreach({"id": al, "domain": "a.co.th"}, summary, "https://r/x", lambda m: json.dumps({"subject": "ด่วนที่สุด!!", "body": "ลดราคา " * 30}))
assert sp["source"].startswith("template")                                                        # เนื้อหาเสี่ยงสแปม → แม่แบบ
short = X.outreach_template({"domain": "s.co.th"}, summary * 3, "https://r/x", {"tpl": {"short": True}}); assert short["body"].count("•") == 1
print("ab-style: ปิด = ไม่ทดลอง · แบ่งคงที่ครึ่งต่อครึ่ง · B เข้า prompt · แม่แบบสำรองตามแบบ · เนื้อหาเสี่ยงถอยแม่แบบ OK")

# ---- ส่งจริงแล้วนับเข้าการทดลอง ----
lb = mk("arm.co.th"); P.ceo_propose(lb)
dd = [x for x in db.list_decisions(None, 20) if x["lead_id"] == lb["id"] and x["kind"] == "outreach"][0]
pl = json.loads(dd["payload_json"]); assert pl["exp"] == "subject_q" and pl["arm"] in ("A", "B")
if dd["status"] == "proposed":
    P.execute_decision(dd, "owner")
assert db.get_lead(lb["id"])["exp_arm"] == f"subject_q:{pl['arm']}" and db.get_lead(lb["id"])["status"] == "contacted"
print("ab-send: ข้อเสนอติดแบบ · ส่งอีเมลจริงแล้วบันทึกแบบที่ลีด OK")

# ---- ตัดสินผล ----
def seed_arm(key, arm, n, engaged, unsub=0):
    for i in range(n):
        lid = db.add_lead(f"https://{key}{arm}{i}.co.th", f"{key}{arm}{i}.co.th", source="prospect", email=f"x@{key}{arm}{i}.co.th")
        db.update_lead(lid, exp_arm=f"{key}:{arm}", status="contacted")
        db.add_lead_event(lid, "contacted", "ส่งอีเมลถึง x แล้ว", "ceo")
        if i < engaged: db.add_lead_event(lid, "opened", "", "customer")
        if i >= n - unsub: db.add_lead_event(lid, "unsubscribed", "", "customer")
with db.get_conn() as c: c.execute(db.q("UPDATE leads SET exp_arm=NULL"))
seed_arm("subject_q", "A", 29, 3); seed_arm("subject_q", "B", 35, 15)
assert D.evaluate_experiment()["done"] is False                                                      # A ยังไม่ครบ 30
seed_arm("subject_q", "A", 6, 1)
ev = D.evaluate_experiment(); assert ev["done"] and ev["winner"] == "B" and "subject_q" in D.adopted() and D.active_experiment()["key"] == "opener_finding", ev
assert any("ทดลองข้อความทัก" in a for a in alerts) and "คำถามสั้น" in D.style_for({"id": al})["prompt"]                # แบบที่ชนะใช้กับทุกคน
seed_arm("opener_finding", "A", 30, 3); seed_arm("opener_finding", "B", 30, 15, unsub=4)
ev = D.evaluate_experiment(); assert ev["winner"] == "A" and "ขอไม่รับเพิ่ม" in ev["verdict"] and "opener_finding" not in D.adopted()
D.MAX_ARM = 40; seed_arm("short", "A", 40, 8); seed_arm("short", "B", 40, 9)
ev = D.evaluate_experiment(); assert ev["winner"] == "A" and "ยังไม่ต่าง" in ev["verdict"] and D.active_experiment() is None
D.MAX_ARM = 120
print("ab-judge: รอครบ 30 · B ชนะ (p<0.05) ใช้กับทุกคน · ชนะแต่ขอไม่รับเพิ่ม → คงเดิม · ครบเพดานไม่ต่าง → คงเดิม OK")

# ---- สัญญาณ + หยุดส่ง ----
with db.get_conn() as c: c.execute(db.q("DELETE FROM lead_events")); c.execute(db.q("DELETE FROM leads"))
ls = [mk(f"m{i}.co.th") for i in range(25)]
for l in ls: db.add_lead_event(l["id"], "contacted", f"ส่งอีเมลถึง info@{l['domain']} แล้ว", "ceo")
db.add_lead_event(ls[0]["id"], "bounced", "x", "deliv"); db.add_lead_event(ls[1]["id"], "opened", "", "customer")
m = D.metrics(); assert m["sends"] == 25 and m["bounce_pct"] == 4.0 and m["open_pct"] == 4.0
assert D.guard(m) is None and not D.on_hold()
db.add_lead_event(ls[2]["id"], "bounced", "x", "deliv")
r = D.guard(); assert r and "เด้ง 8.0%" in r and D.on_hold() and not X.auto_enabled("outreach") and not X.auto_enabled("followup")
assert any("หยุดส่ง" in a for a in alerts)
real_gate = X.auto_gate; X.auto_gate = lambda k: {"on": True, "reason": ""}                 # จำลองว่าเปิดโหมดทำเอง
assert not X.auto_enabled("outreach") and X.auto_enabled("lead_review")
newl = mk("held.co.th"); P.ceo_propose(newl)
assert [x for x in db.list_decisions(None, 20) if x["lead_id"] == newl["id"] and x["kind"] == "outreach"][0]["status"] == "proposed"   # รอคน
X.auto_gate = real_gate
D.release_hold(); assert not D.on_hold()
print("guard: สัญญาณ 30 วัน · เด้งเกิน 5% → หยุดทัก/ติดตามอัตโนมัติ (ข้อเสนอรอคน) · ปลดได้ OK")

# ---- Resend webhook ----
c = TestClient(app)
assert D.save_webhook_secret("abc").startswith("รหัสลับ")
import base64
SEC = "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode(); assert D.save_webhook_secret(SEC) == "" and D.webhook_secret() == SEC
assert "whsec" not in (db.get_setting("resend_webhook_secret") or "")                          # เก็บแบบเข้ารหัส
def hook(evt, bad=False):
    body = json.dumps(evt).encode(); h = D.sign_svix(body, SEC)
    if bad: h["svix-signature"] = "v1,AAAA"
    return c.post("/resend/webhook", content=body, headers=h)
assert hook({"type": "email.bounced"}, bad=True).status_code == 400
tb = ls[5]; db.add_decision("sdr", "followup", "x", "x", {"step": 1}, lead_id=tb["id"])
r = hook({"type": "email.bounced", "data": {"to": [f"info@{tb['domain']}"], "bounce": {"type": "Permanent", "message": "mailbox not found"}}})
assert r.status_code == 200 and r.json()["result"] == "bounced" and db.get_lead(tb["id"])["email"] is None
assert any(e["kind"] == "bounced" and "mailbox not found" in e["note"] for e in db.list_lead_events(tb["id"]))
assert all(x["status"] != "proposed" for x in db.list_decisions(None, 50) if x["lead_id"] == tb["id"] and x["kind"] == "followup")
tc = ls[6]; r = hook({"type": "email.complained", "data": {"to": f"info@{tc['domain']}"}}); assert r.json()["result"] == "complained" and db.get_lead(tc["id"])["status"] == "unsubscribed"
assert hook({"type": "email.delivered", "data": {"to": ["a@b.c"]}}).json()["result"] == "delivered" and D.metrics()["delivered"] == 1
old = D.sign_svix(b"{}", SEC, ts=int(dt.datetime.now().timestamp()) - 900); assert not D.verify_svix(b"{}", old, SEC)
D.release_hold()
print("resend: ลายเซ็น Svix (ผิด/เก่า = ปฏิเสธ) · เด้ง → ล้างอีเมล+ยกเลิกที่ค้าง · ร้องเรียน → ไม่ติดต่ออีก · นับส่งถึง · รหัสลับเข้ารหัส OK")

# ---- ทดสอบกล่อง ----
with db.get_conn() as cc: cc.execute(db.q("DELETE FROM lead_events WHERE kind IN ('bounced','complained','unsubscribed')"))
assert D.send_placement_test()["error"] == "ยังไม่มีกล่องทดสอบ"
assert D.save_seed("G", "", "seed@example.com", "") and D.save_seed("Gmail", "", "seed@example.com", "abcd efgh ijkl mnop") == ""
assert D.public_seeds()[0]["host"] == "imap.gmail.com" and "pass" not in D.public_seeds()[0] and "abcd" not in db.get_setting("deliv_seeds")
mails.clear(); t = D.send_placement_test(); tok = t["results"][0]["token"]
assert mails and mails[-1][0] == "seed@example.com" and tok in mails[-1][1] and t["results"][0]["place"] == "pending"
place = {"v": "missing"}; D.HOOKS["imap"] = lambda seed, token: place["v"] if token == tok else "missing"
now0 = dt.datetime.fromisoformat(t["sent_at"][:19])
assert D.check_placement(now0 + dt.timedelta(minutes=1)) == 0                                    # เร็วไป
assert D.check_placement(now0 + dt.timedelta(minutes=5)) == 0 and D.last_placement() is None      # ยังไม่เจอ ไม่ถึง 60 นาที = รอ
place["v"] = "inbox"; assert D.check_placement(now0 + dt.timedelta(minutes=6)) == 1 and D.last_placement()["results"][0]["place"] == "inbox"
t2 = D.send_placement_test(); place["v"] = "spam"; tok = t2["results"][0]["token"]
D.check_placement(dt.datetime.fromisoformat(t2["sent_at"][:19]) + dt.timedelta(minutes=5))
assert D.last_placement()["results"][0]["place"] == "spam" and D.guard() == "ทดสอบกล่องล่าสุดตกสแปมทุกกล่อง" and D.on_hold()
D.release_hold()
print("placement: รหัสผ่านเข้ารหัส ไม่แสดง · ส่งทดสอบ · รอ 3 นาที · ไม่เจอรอได้ 60 นาที · เข้ากล่องหลัก · ตกสแปมทุกกล่อง → หยุดส่ง OK")

# ---- เพิ่มจำนวนทักเมื่อสุขภาพดี ----
t3 = D.send_placement_test(); place["v"] = "inbox"; tok = t3["results"][0]["token"]; D.check_placement(dt.datetime.now() + dt.timedelta(minutes=5))
m = D.metrics(); ok, why = D.healthy(m); assert ok, why
assert PR.daily_limit() == 10 and D.ramp(m) == "10→15" and PR.daily_limit() == 15 and D.ramp(m) is None      # สัปดาห์ละครั้ง
assert D.ramp(m, dt.datetime.now() + dt.timedelta(days=7)) == "15→20"
db.set_setting("deliv_ramp", "0"); assert D.ramp(m, dt.datetime.now() + dt.timedelta(days=30)) is None; db.set_setting("deliv_ramp", "1")
res = D.run(); assert "metrics" in res and res["dns_ok"] is True
print("ramp: สุขภาพดีครบ → +5 ราย/วัน สัปดาห์ละครั้ง · ปิดได้ · รอบประจำวัน OK")

# ---- หน้าเว็บ · แชต · ทะเบียน ----
admin = "admin@test.local"; db.create_tenant(admin, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Op", is_admin=True)
a = TestClient(app); a.post("/login", data={"email": admin, "password": "pw123456"})
pg = a.get("/admin/deliverability").text
for s_ in ("การส่งอีเมล", "โดเมนผู้ส่ง", "ทดสอบกล่องจริง", "seed@example.com", "/resend/webhook", "ทดลองข้อความทัก", "หัวข้อเป็นคำถาม"):
    assert s_ in pg, s_
assert "abcd efgh" not in pg
r = a.post("/admin/deliverability/webhook", data={"secret": "nope"}); assert "whsec_" in r.text
a.post("/admin/deliverability/exp", data={"on": ""}); assert not D.exp_on()
D.set_hold("ทดสอบ"); assert "ปลดการหยุด" in a.get("/admin/deliverability").text; a.post("/admin/deliverability/release"); assert not D.on_hold()
a.post("/admin/deliverability/seed_remove", data={"sid": D.public_seeds()[0]["id"]}); assert D.seeds() == []
assert "ส่งอีเมล 30 วัน" in C.handle("อีเมลเราตกสแปมไหม")
assert "deliv" in O.IDS and any(s_[0] == "deliv" for s_ in O.SCHEDULE) and "deliv" in SK.AGENTS and SK.load("deliv").startswith("# ทักษะ")
assert "ส่งอีเมล 30 วัน" in (D.status_text() or "")
print("web/chat: หน้าการส่งอีเมล (ไม่โชว์รหัส) · ตั้ง webhook · เปิด/ปิดทดลอง · ปลดหยุด · ลบกล่อง · แชต CEO · ออฟฟิศ/ทักษะ OK")
print("\ndeliv_smoke: ผ่านทั้งหมด")
