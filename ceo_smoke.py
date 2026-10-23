"""ทดสอบสั่งงาน CEO: กฎคำสั่งพบบ่อย · โมเดลเลือกงานจากรายการเท่านั้น · งานติดต่อลูกค้า/ราคา/ค่าใช้จ่ายถามยืนยัน (ยืนยัน/ยกเลิก/หมดเวลา)
· ทักลีดแม้โหมดเสนอ · ส่งใบเสนอราคา (CFO เตือนเกินกรอบ) · ตรวจเว็บ · จดบันทึก · หยุด/ทำงานต่อ · LINE เจ้าของ → CEO (ลูกค้าคนอื่นไม่โดน) · หน้าแชต + JSON ในออฟฟิศ"""
import os, tempfile, json, datetime as dt
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke14-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["GEO_DEEP_AUDIT"] = "0"; os.environ["GEO_CS"] = "0"
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.ceo_chat as C, app.pipeline as P, app.mailer as mailer, app.execs as X, app.ai_client as ai, app.line_oa as LO, app.site_health as sh
from app.main import app
db.init_db()
ai._read_setting = lambda k: None
ai._chat = lambda m, max_tokens=2048, timeout=120: (_ for _ in ()).throw(RuntimeError("no model"))
sent = []; mailer.config = lambda: {"host": "x"}; mailer.send = lambda to, subj, html, text=None: sent.append((to, subj)); mailer.is_deliverable = lambda e: bool(e) and e.endswith(".co.th")
P.RUN_INLINE = True; C.RUN_INLINE = True
REPORT = {"checks": [{"key": k, "label": f"ข้อ {k}", "status": "fail", "detail": ""} for k in ("csr", "noindex", "robots")]}
sh.run_checks = lambda site, **kw: REPORT
lid = db.add_lead("https://shop.co.th", "shop.co.th", source="prospect", email="info@shop.co.th")
db.update_lead(lid, audit_json=json.dumps(REPORT), score=60, token="t-shop")
pick = {}
C.LLM = lambda m: json.dumps(pick)

assert "สถานะ" in C.handle("สรุปวันนี้") and "รออนุมัติ" in C.log()[-1]["text"]
assert "สั่งได้เช่น" in C.handle("ช่วยหน่อย")
pick.update(action="delete_everything", args={}); r = C.handle("ลบทุกอย่าง"); assert "ยังไม่แน่ใจ" in r and not C._pending()   # งานนอกรายการ → ไม่ทำ ถามกลับ
assert C.handle("สถานะ shop.co.th เป็นไงบ้าง").startswith("shop.co.th ·")                                      # มีโดเมน = ดูลีดรายนั้น
print("rules: สรุป · ช่วย · งานนอกรายการไม่ทำ · สถานะลีดรายตัว OK")

# ทัก (ยังเป็นโหมดเสนอ) → ถามยืนยัน → ยืนยัน → ส่งจริง
pick.clear(); pick.update(action="contact_lead", args={"domain": "shop.co.th"})
r = C.handle("ทัก shop.co.th"); assert "ยืนยัน" in r and C._pending() and not sent
assert C.handle("ยืนยัน") and sent and sent[-1][0] == "info@shop.co.th" and db.get_lead(lid)["status"] == "contacted" and not C._pending()
pick.update(action="contact_lead", args={"domain": "nope.co.th"}); assert "ยังไม่มีลีด nope.co.th" in C.handle("ทัก nope.co.th") and C._pending()
assert C.handle("ยกเลิก") == "ยกเลิกแล้ว"
pick.update(action="contact_lead", args={"domain": "zzz"}); assert "ไม่เจอลีด" in C.handle("ทัก zzz") and not C._pending()       # อ่านโดเมนไม่ออก
print("contact: ถามยืนยัน → ส่งจริงแม้โหมดเสนอ · ไม่เจอลีดไม่ค้าง OK")

# ใบเสนอราคาเกินกรอบ → CFO เตือน · ยกเลิกได้ · หมดเวลา
pick.update(action="quote", args={"domain": "shop", "package": "pro", "discount_pct": "25"})
r = C.handle("ส่งใบเสนอราคา shop ลด 25%"); assert "CFO" in r and "พิเศษ 25%" in r and "รายปี" in r     # เสนอรายปีก่อน
assert C.handle("ยกเลิก") == "ยกเลิกแล้ว" and not db.list_quotes(lid)
C.handle("ส่งใบเสนอราคา shop ลด 25%")
p = json.loads(db.get_setting(C.PENDING_KEY)); p["at"] = (dt.datetime.now() - dt.timedelta(minutes=40)).isoformat(timespec="seconds"); db.set_setting(C.PENDING_KEY, json.dumps(p))
pick.update(action="help", args={}); assert "สั่งได้เช่น" in C.handle("ยืนยัน") and not db.list_quotes(lid)                  # หมดเวลา → ไม่ทำ
pick.update(action="quote", args={"domain": "shop.co.th", "package": "pro", "discount_pct": 5})
C.handle("ส่งใบเสนอราคา shop.co.th ลด 5%"); r = C.handle("ยืนยัน")
qs = db.list_quotes(lid); assert qs and qs[0]["status"] == "sent" and qs[0]["discount_pct"] == 5 and "/q/" in r
print("quote: CFO เตือนเกินกรอบ · ยกเลิก · หมดเวลา 30 นาที · ยืนยันแล้วส่งจริง OK")

# ตรวจเว็บ · จด · หยุด/ทำงานต่อ
pick.update(action="audit", args={"url": "newshop.co.th"}); r = C.handle("ตรวจเว็บ newshop.co.th")
assert "ตรวจ newshop.co.th แล้ว" in r and db.find_lead_by_domain("newshop.co.th")
pick.update(action="note", args={"domain": "shop.co.th", "text": "ขอคุยสัปดาห์หน้า"}); C.handle("จด shop.co.th")
assert db.list_lead_events(lid)[0]["note"] == "ขอคุยสัปดาห์หน้า"
assert "ยืนยัน" in C.handle("หยุด") and C.handle("ยืนยัน").startswith("หยุดฉุกเฉินแล้ว") and X.paused()
assert C.handle("ทำงานต่อ").startswith("กลับมาทำงานแล้ว") and not X.paused()
print("audit/note/pause: ตรวจเว็บ · จดไทม์ไลน์ · หยุดต้องยืนยัน · ทำงานต่อ OK")

# เคสจริง 23 ต.ค.: ยังไม่มีลีด · เว็บผ่านทุกข้อ · สั่งทัก → ต้องทัก ไม่ใช่ปิด · ถามทำไม → อธิบาย · คำถามทั่วไป → ไม่โยนรายการคำสั่ง
import app.prospecting as PR, app.closer as CL, app.leads as LL
sh.run_checks = lambda site, **kw: {"checks": [{"key": "csr", "status": "ok", "label": "x"}], "ok": True, "n_fail": 0, "n_ok": 1}
PR.find_contact = lambda site, **kw: {"email": "hello@goodsite.co.th", "line": "", "n": 1, "source": "เว็บ"}
CL.FETCH = lambda u: ""
pick.clear(); pick.update(action="contact_lead", args={"domain": "goodsite.co.th"})
r = C.handle("ติดต่อ goodsite.co.th ดูหน่อย"); assert "ยังไม่มีลีด goodsite.co.th" in r and C._pending()
sent.clear(); r = C.handle("ยืนยัน")
gl = db.find_lead_by_domain("goodsite.co.th")
assert gl["status"] == "contacted" and gl["email"] == "hello@goodsite.co.th" and sent and sent[-1][0] == "hello@goodsite.co.th", (gl["status"], r)
assert "ผ่านทุกข้อ" in r and "เจออีเมล" in r
db.update_lead(gl["id"], status="lost")
pick.update(action="contact_lead", args={"domain": ""})                                                    # "ทักอีกที" ไม่ระบุโดเมน → ใช้รายล่าสุด
r = C.handle("ทักอีกที"); assert "goodsite.co.th" in r and "เปิดใหม่" in r
C.handle("ยกเลิก")
r = C.handle("ปิดทำไม"); assert r.startswith("goodsite.co.th ตอนนี้: ไม่สนใจ") and "ทัก goodsite.co.th" in r
pick.update(action="chat", args={"question": "x"})
r = C.handle("เว็บนี้ดีอยู่แล้วจะขายอะไรเขา"); assert "สั่งได้เช่น" not in r
import app.execs as XX
assert XX.ceo_review_lead({"domain": "z.co.th"}, {"checks": [{"key": "csr", "status": "ok"}]}, "u")["reason"].startswith("เว็บผ่านทุกข้อที่ตรวจ")   # ไม่ใช่ "เข้าเว็บไม่ได้"
print("owner-first: ไม่มีลีดก็ทักได้ · ผ่านทุกข้อไม่ปิดเมื่อเจ้าของสั่ง · หาอีเมลเอง · ใช้โดเมนล่าสุด · อธิบายเหตุผล · ตอบคำถาม OK")

# LINE: เจ้าของ → CEO · คนอื่นยังเป็นบอทลูกค้า
replies = []; LO.reply = lambda tok, texts: replies.append(texts) or True; LO.push = lambda uid, texts: replies.append(texts) or True
db.set_setting("line_owner_user_id", "Uowner")
assert P.line_text("Uowner", "สรุปวันนี้", "rt") == "owner_command" and "สถานะ" in replies[-1][0]
assert P.line_text("Ucustomer", "สรุปวันนี้", "rt2") == "help"
print("line: เจ้าของสั่ง CEO · ลูกค้าเห็นบอทเดิม OK")

admin = "admin@test.local"; db.create_tenant(admin, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Op", is_admin=True)
a = TestClient(app); a.post("/login", data={"email": admin, "password": "pw123456"})
r = a.post("/admin/ceo", data={"text": "วันนี้ควรโทรใคร"}); assert r.status_code == 200 and "สั่งงาน CEO" in r.text and "ควรโทร" in r.text
j = a.post("/admin/ceo/send.json", json={"text": "สรุปวันนี้"}).json(); assert "สถานะ" in j["reply"] and j["log"][-1]["role"] == "ceo"
assert a.get("/admin/ceo/log.json").json()["log"] and TestClient(app).post("/admin/ceo/send.json", json={"text": "x"}).status_code == 401
r = a.get("/admin/office"); assert "สั่งงาน CEO" in r.text and "/admin/ceo/send.json" in r.text
print("admin: หน้าแชต · JSON ในออฟฟิศ (แอดมินเท่านั้น) OK")
print("ALL OK")

# ---- ใบเดิมเปิดอยู่: เจ้าของสั่งรายปี → ยืนยันบอกก่อนว่าจะปรับใบเดิม → ปรับลิงก์เดิม ลูกค้ายืนยันใหม่ · ส่งซ้ำไม่ทำซ้ำ · "แก้ไขใหม่" ทำต่อคำสั่งเดิม ----
import app.quotes as Q
ld = db.add_lead("https://dt.co.th", "dt.co.th", source="prospect", email="buyer@dt.co.th"); db.update_lead(ld, audit_json=json.dumps(REPORT), score=60)
q0 = P.create_quote(db.get_lead(ld), "pro", 0, True, cycle="month"); P.send_quote(q0, "admin"); P.accept_quote(db.get_quote(q0["id"]), "คุณดี", "1.1.1.1")
assert db.get_quote(q0["id"])["status"] == "accepted" and db.get_quote(q0["id"])["first_total"] == 19800
pick.clear(); pick.update(action="quote", args={"domain": "dt.co.th"})
r = C.handle("dt.co.th เสนอรายปีเลย"); assert f"มีใบ {q0['number']} อยู่แล้ว" in r and "ปรับใบเดิม" in r and "98,604" in r and "ยืนยันใหม่" in r, r
assert C.handle("dt.co.th เสนอรายปีเลย") == r and len([e for e in C.log() if e["text"] == "dt.co.th เสนอรายปีเลย"]) == 1          # ส่งซ้ำภายใน 90 วิ ไม่ทำซ้ำ
sent.clear(); r = C.handle("ยืนยัน"); qn = db.get_quote(q0["id"])
assert "ปรับ" in r and qn["cycle"] == "year" and qn["first_total"] == 98604 and qn["status"] == "sent" and qn["accepted_at"] is None and qn["token"] == q0["token"], (r, dict(qn))
assert len(db.list_quotes(ld)) == 1 and sent and "ปรับใบเสนอราคา" in sent[-1][1]
assert any("ปรับ" in e["note"] and "ยืนยันใหม่" in e["note"] for e in db.list_lead_events(ld))
db.update_quote(q0["id"], status="claimed")
pick.update(action="quote", args={"domain": "dt.co.th", "cycle": "month"}); r = C.handle("dt.co.th ทำรายเดือน"); assert "ตรวจยอดก่อน" in r and not C._pending()
db.update_quote(q0["id"], status="sent")
pick.update(action="help", args={}); r = C.handle("แก้ไขใหม่สิ"); assert "ปรับใบเดิม" in r and C._pending(), r        # ทำต่อคำสั่งล่าสุด (รายเดือน)
assert C.handle("ยกเลิก") == "ยกเลิกแล้ว"
print("revise: ใบเปิดอยู่ → บอกก่อนยืนยัน · ปรับใบเดิมลิงก์เดิม ลูกค้ายืนยันใหม่ · แจ้งชำระแล้วห้ามแก้ · ส่งซ้ำไม่ทำซ้ำ · 'แก้ไขใหม่' ทำต่อ OK")
