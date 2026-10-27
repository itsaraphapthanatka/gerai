"""ทดสอบตัวตอบแชตขาย: คลังข้อเท็จจริงจากระบบจริง · ตัวเลขนอกคลังไม่ส่ง · เรื่องนอกกรอบส่งต่อเจ้าของ · ขอส่วนลดตอบเฉพาะรายปี + ส่งต่อ
· LINE ตอบทันที · อีเมลร่างรออนุมัติ (ปลดล็อกแล้วส่งเอง · นอกกรอบรอคนเสมอ) · กล่องแชตบนเว็บ + ทิ้งช่องทางติดต่อ · จำกัดต่อวัน · ไม่ยิงเครือข่าย"""
import os, tempfile, json
os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-sb-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
import bcrypt
from fastapi.testclient import TestClient
import app.quotes as Q
import app.db as db, app.salesbot as SB, app.pipeline as P, app.line_oa as LO, app.inbox as IN, app.execs as EX, app.mailer as mailer

db.init_db()
P.RUN_INLINE = True
alerts, replies, sent = [], [], []
P.alert_admins = lambda msg, link, type_="info", subject=None: alerts.append(msg)
LO.reply = lambda tok, texts: replies.append(texts) or True
mailer.config = lambda: {"host": "x"}; mailer.is_deliverable = lambda e: True
mailer.send_plain = lambda to, subj, text, unsub, fa=None, fn=None: sent.append((to, text))
lid = db.add_lead("https://shop.co.th", "shop.co.th", source="prospect")
db.update_lead(lid, email="info@shop.co.th", audit_json=json.dumps({"checks": [{"key": "llms", "label": "ไม่มี llms.txt", "status": "fail"}]}), token="tok123")
lead = db.get_lead(lid)

# ---- คลังข้อเท็จจริง ----
k = " ".join(SB.knowledge(lead))
assert "9,900 บาท/เดือน" in k and "29,000" not in k and "ราคาสุดท้ายขึ้นกับขอบเขตงาน" in k and "30 นาที" in k and "ทดลองใช้ฟรี 14 วัน" in k   # โหมดเอเจนซี: ราคาเริ่มต้น ไม่มีเมนูแพ็กเกจ and "ชำระแล้วไม่คืนเงิน" in k and "คืนเงินเต็ม" not in k
assert "shop.co.th" in k and "ไม่มี llms.txt" in k and "/audit/tok123" in k or "tok123" in k
print("knowledge: ราคาเริ่มต้น (ไม่มีเมนูแพ็กเกจ) · นัดคุย 30 นาที · ทดลอง/คืนเงินจากระบบ + ผลตรวจของลีด OK")

# ---- answer: ผ่านกติกา / ตัวเลขนอกคลัง / นอกกรอบ / ส่วนลด ----
ok_llm = lambda m: json.dumps({"answer": "Pro 9,900 บาท/เดือน ยังไม่รวม VAT ทดลองฟรี 14 วันได้ครับ", "needs_human": False})
r = SB.answer("ราคาเท่าไหร่", lead, "line", llm=ok_llm)
assert r["source"] == "llm" and "9,900" in r["answer"] and not r["escalate"], r
r = SB.answer("ราคาเท่าไหร่", lead, "line", llm=lambda m: json.dumps({"answer": "Pro 4,500 บาท/เดือน เห็นผลใน 30 วัน", "needs_human": False}))
assert r["source"] == "fallback" and r["answer"] == SB.HOLD and r["escalate"], r                 # 4,500 / 30 ไม่อยู่ในคลัง → ไม่ส่ง
r = SB.answer("รับประกันติดอันดับ 1 ไหม", lead, "line", llm=lambda m: json.dumps({"answer": "รับประกันติดอันดับแน่นอนครับ", "needs_human": False}))
assert r["answer"] == SB.HOLD, r                                                                   # คำต้องห้าม
called = []
r = SB.answer("ขอคืนเงินได้ไหม", lead, "line", llm=lambda m: called.append(1) or "")
assert r["escalate"] == "คืนเงิน/ยกเลิก" and r["answer"] == SB.HOLD and not called                  # นอกกรอบ ไม่ถามโมเดลเลย
seen = {}
r = SB.answer("ลดให้หน่อยได้ไหม", lead, "line", llm=lambda m: seen.setdefault("p", m[0]["content"]) and json.dumps({"answer": f"จ่ายรายปีลด {Q.yearly_discount():g}% ครับ ส่วนลดอื่นผู้ดูแลจะพิจารณาให้", "needs_human": False}))
assert r["escalate"] == "ต่อรองราคา/ขอส่วนลด" and "รายปีลด" in r["answer"] and "ส่วนลดอื่นต้องให้ผู้ดูแลพิจารณา" in seen["p"]
r = SB.answer("ทำ SEO ภาษาญี่ปุ่นได้ไหม", lead, "line", llm=lambda m: json.dumps({"answer": "ขอเช็กก่อนนะครับ", "needs_human": True}))
assert r["escalate"] == "ข้อมูลไม่พอตอบ" and "ผู้ดูแล" in r["answer"]
print("answer: ตัวเลขนอกคลัง/คำต้องห้ามไม่ส่ง · นอกกรอบส่งต่อไม่ถามโมเดล · ส่วนลดตอบรายปี+ส่งต่อ · ไม่รู้ = ส่งต่อ OK")

# ---- LINE ----
SB.LLM = ok_llm
db.update_lead(lid, line_user_id="U1")
out = P.line_text("U1", "แพ็กเกจ Pro ได้อะไรบ้าง", "rt")
assert out == "replied_answered" and "9,900" in replies[-1][0] and "30 นาที" in replies[-1][0] and "ใบเสนอราคา" not in replies[-1][0] and "พิมพ์ที่อยู่เว็บ" not in replies[-1][0], (out, replies[-1])
assert replies[-1][0].count("30 นาที") == 1 and not [q for q in db.list_quotes(lid)]                  # สนใจทาง LINE: ชวนนัดคุยครั้งเดียว ไม่สร้างใบเสนอราคา
out = P.line_text("U1", "ใช้เวลาติดตั้งนานไหม", "rt"); assert out == "salesbot" and "9,900" in replies[-1][0]
assert any(e["kind"] == "chat" for e in db.list_lead_events(lid))
out = P.line_text("U9", "ทำงานยังไง", "rt")                                                      # คนที่ยังไม่มีลีด → ชวนตรวจเว็บท้ายคำตอบ
assert out == "salesbot" and "พิมพ์ที่อยู่เว็บ" in replies[-1][0]
out = P.line_text("U9", "สวัสดีครับ", "rt"); assert out == "help"
n_alert = len(alerts)
out = P.line_text("U9", "ขอคุยกับเจ้าหน้าที่หน่อย", "rt")
assert out == "salesbot_escalated" and replies[-1][0].startswith(P.call_reply(None)) and len(alerts) == n_alert + 1 and "อยากคุย" in alerts[-1]   # ขอคุยกับคน = นัดคุย + แจ้งเจ้าของ
SB.set_enabled("line", False); assert P.line_text("U9", "ราคาเท่าไหร่", "rt") == "help"; SB.set_enabled("line", True)
print("LINE: ตอบทันที · คนใหม่ชวนตรวจเว็บ · ทักทาย = วิธีใช้ · ขอคุยกับคนแจ้งเจ้าของ · ปิดได้ OK")

# ---- อีเมล ----
m = {"from": "info@shop.co.th", "subject": "สอบถาม", "body": "ราคาแพ็กเกจเท่าไหร่ครับ", "message_id": "<m1@x>"}
IN.LLM = lambda msgs: json.dumps({"category": "question"}) if "จัดประเภท" in msgs[0]["content"] else ok_llm(msgs)
res = IN.process(m)
d = [x for x in db.list_decisions("proposed") if x["kind"] == "reply"][0]
assert d["agent"] == "salesbot" and "9,900" in json.loads(d["payload_json"])["body"] and "รออนุมัติ" in res["action"] and not sent
db.set_decision(d["id"], "rejected", "owner", "x")
_orig = EX.auto_enabled
EX.auto_enabled = lambda kind: kind == "reply"                                                    # ปลดล็อกตอบอีเมลแล้ว
res = IN.process({**m, "message_id": "<m2@x>", "body": "มีทดลองใช้ไหม"})
assert "ตอบอีเมลแล้ว" in res["action"] and sent and sent[-1][0] == "info@shop.co.th", (res, sent)
n = len(sent)
res = IN.process({**m, "message_id": "<m3@x>", "body": "ขอแก้สัญญาข้อ 5 ได้ไหม"})
assert len(sent) == n and "สัญญา/กฎหมาย" in res["action"]                                        # นอกกรอบ → รอคนแม้ปลดล็อก
EX.auto_enabled = _orig
assert "reply" in EX.AUTO_ELIGIBLE
print("email: ร่างรออนุมัติ · ปลดล็อกแล้วส่งเอง · นอกกรอบรอคนเสมอ OK")

# ---- เว็บ ----
from app.main import app
cl = TestClient(app)
r = cl.post("/chat/ask", json={"q": "ราคาเท่าไหร่", "token": "tok123"}).json()
assert "9,900" in r["answer"] and r["escalate"] is False
r = cl.post("/chat/ask", json={"q": "มีปัญหาตัดบัตรไม่ผ่าน"}).json()
assert r["escalate"] and r["id"]
assert not cl.post("/chat/contact", json={"id": r["id"], "contact": "a@b.co", "consent": False}).json()["ok"]
assert not cl.post("/chat/contact", json={"id": r["id"], "contact": "!!", "consent": True}).json()["ok"]
assert cl.post("/chat/contact", json={"id": r["id"], "contact": "buyer@shop.co.th", "consent": True}).json()["ok"]
assert any("buyer@shop.co.th" in a for a in alerts)
assert "ถามเจอ.AI" in cl.get("/").text and "ถามเจอ.AI" in cl.get("/audit/tok123").text
SB.DAILY_PER_USER = 2                                                                              # ถามไปแล้ว 2 ข้อจาก IP นี้
r = cl.post("/chat/ask", json={"q": "อีกข้อ"}).json(); assert r.get("answer", "").startswith("วันนี้ถามได้ครบแล้ว")
SB.set_enabled("web", False); assert "ถามเจอ.AI" not in cl.get("/").text and "ปิดแชต" in cl.post("/chat/ask", json={"q": "ราคา"}).json()["answer"]
SB.set_enabled("web", True)
print("web: ตอบจากผลตรวจของลีด · ส่งต่อ + ทิ้งช่องทาง (ต้องยินยอม) · กล่องแชตบนหน้าแรก/รายงาน · จำกัดต่อวัน · ปิดได้ OK")

# ---- แอดมิน ----
tid = db.create_tenant("own@test.local", bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode(), "Own", is_admin=True)
cl.post("/login", data={"email": "own@test.local", "password": "pw"})
t = cl.get("/admin/salesbot").text
assert "ตอบแชตขาย" in t and "ขอคุยกับเจ้าหน้าที่หน่อย" in t and "buyer@shop.co.th" in t and "9,900 บาท/เดือน" in t
assert "ตัวตอบแชต: 24 ชม." in SB.status_text()
print("admin: หน้า /admin/salesbot · คลังข้อเท็จจริง · สรุปเช้า OK")
print("SALESBOT SMOKE OK")
