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
pick.update(action="delete_everything", args={}); assert "สั่งได้เช่น" in C.handle("ลบทุกอย่าง")              # งานนอกรายการ → help
assert C.handle("สถานะ shop.co.th เป็นไงบ้าง").startswith("shop.co.th ·")                                      # มีโดเมน = ดูลีดรายนั้น
print("rules: สรุป · ช่วย · งานนอกรายการไม่ทำ · สถานะลีดรายตัว OK")

# ทัก (ยังเป็นโหมดเสนอ) → ถามยืนยัน → ยืนยัน → ส่งจริง
pick.clear(); pick.update(action="contact_lead", args={"domain": "shop.co.th"})
r = C.handle("ทัก shop.co.th"); assert "ยืนยัน" in r and C._pending() and not sent
assert C.handle("ยืนยัน") and sent and sent[-1][0] == "info@shop.co.th" and db.get_lead(lid)["status"] == "contacted" and not C._pending()
pick.update(action="contact_lead", args={"domain": "nope.co.th"}); assert "ไม่เจอลีด" in C.handle("ทัก nope") and not C._pending()
print("contact: ถามยืนยัน → ส่งจริงแม้โหมดเสนอ · ไม่เจอลีดไม่ค้าง OK")

# ใบเสนอราคาเกินกรอบ → CFO เตือน · ยกเลิกได้ · หมดเวลา
pick.update(action="quote", args={"domain": "shop", "package": "pro", "discount_pct": "25"})
r = C.handle("ส่งใบเสนอราคา shop ลด 25%"); assert "CFO" in r and "ลด 25%" in r
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
