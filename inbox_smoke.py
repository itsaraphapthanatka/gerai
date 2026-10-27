"""ทดสอบ Inbox: แยกอีเมลตอบกลับ (สนใจ/ถาม/ไม่สนใจ/ขอไม่รับ/อัตโนมัติ/เด้ง) · จับคู่ลีดจากอีเมลผู้ส่ง/ลิงก์ในอีเมลเดิม/โดเมน · ตัดส่วนอ้างอิงเดิม
· ทำต่อ (ใบเสนอราคา · ร่างคำตอบรออนุมัติ → ส่ง · ปิดลีด · ขอไม่รับ · ล้างอีเมลที่เด้ง) · หยุดติดตามผล · ไม่ซ้ำ · ไม่เก็บอีเมลที่ไม่ใช่ลีด
· IMAP อ่านอย่างเดียวเฉพาะที่ส่งถึงที่อยู่ของเรา · webhook ต้องมีรหัสลับ · หน้าแอดมิน"""
import os, tempfile, json
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke16-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["GEO_DEEP_AUDIT"] = "0"; os.environ["GEO_CS"] = "0"
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.inbox as I, app.pipeline as P, app.mailer as mailer, app.ai_client as ai, app.followup as F, app.line_oa as LO
from app.main import app
db.init_db()
ai._read_setting = lambda k: None
ai._chat = lambda m, max_tokens=2048, timeout=120: (_ for _ in ()).throw(RuntimeError("no model"))
sent = []; mailer.config = lambda: {"host": "x", "smtp_from": "report@appreview.cloud"}; mailer.send = lambda to, subj, html, text=None: sent.append((to, subj, text))
mailer.is_deliverable = lambda e: bool(e) and e.endswith(".co.th")
LO.notify_owner = lambda t: True
P.RUN_INLINE = True
db.set_setting("smtp_from", "report@appreview.cloud")
REPORT = json.dumps({"checks": [{"key": "csr", "status": "fail", "label": "x"}]})
def lead(dom, email):
    lid = db.add_lead(f"https://{dom}", dom, source="prospect", email=email)
    db.update_lead(lid, status="contacted", audit_json=REPORT, score=60, token=f"tok{dom.replace('.', '')}xx")
    db.add_lead_event(lid, "contacted", "", "auto")
    return lid
A = lead("a.co.th", "info@a.co.th"); B = lead("b.co.th", "info@b.co.th"); C = lead("c.co.th", "info@c.co.th")
Dd = lead("d.co.th", "info@d.co.th"); E = lead("e.co.th", "sale@e.co.th"); G = lead("g.co.th", "info@g.co.th")
def raw(frm, subj, body, to="report@appreview.cloud", mid=None, extra=""):
    return (f"From: {frm}\r\nTo: {to}\r\nSubject: {subj}\r\nMessage-ID: <{mid or abs(hash(frm + subj + body))}@x>\r\n{extra}Content-Type: text/plain; charset=utf-8\r\n\r\n{body}").encode("utf-8")

m = I.parse_raw(raw("Khun A <info@a.co.th>", "Re: ผลตรวจเว็บ a.co.th", "สนใจครับ ขอใบเสนอราคาหน่อย\n\nOn Mon, เจอ.AI wrote:\n> ผลตรวจ https://x/audit/tokacothxx"))
assert m["body"] == "สนใจครับ ขอใบเสนอราคาหน่อย" and m["from"] == "info@a.co.th"
r = I.process(m); assert r["category"] == "interested" and db.get_lead(A)["status"] == "call" and F.history(A)["replied"]   # สนใจ = ขั้นดีล "อยากคุย"
assert not any(d["kind"] == "quote" and d["lead_id"] == A for d in db.list_decisions(None, 50)) and not db.list_quotes(A)       # โหมดเอเจนซี: ไม่ทำใบเสนอราคาเอง
assert any(e["kind"] == "wants_call" for e in db.list_lead_events(A)) and "นัดโทร" in r["action"]
assert sent and sent[-1][0] == "info@a.co.th" and "30 นาที" in sent[-1][2] and "/u/" in sent[-1][2]                            # ตอบลีดทันทีว่าจะติดต่อไปนัดคุย
assert I.process(m).get("skip") == "seen"
print("interested: ตัดส่วนอ้างอิง · ตอบกลับ → แจ้งเจ้าของนัดโทร (ไม่มีใบเสนอราคา) · ตอบลีดเรื่องนัดคุย · หยุดติดตามผล · ไม่ซ้ำ OK")

r = I.process(I.parse_raw(raw("someone@gmail.com", "Re: ผลตรวจ", "ใช้กับ Wix ได้ไหมครับ\n> ลิงก์เดิม https://x/u/tokbcothxx")))
assert r["category"] == "question" and r["lead_id"] == B                                          # จับคู่จากลิงก์ในอีเมลเดิม
rd = next(d for d in db.list_decisions("proposed", 50) if d["kind"] == "reply" and d["lead_id"] == B)
assert "Wix" in rd["proposal"] and json.loads(rd["payload_json"])["to"] == "someone@gmail.com"
mailer.is_deliverable = lambda e: bool(e) and (e.endswith(".co.th") or e.endswith("gmail.com"))
admin = "admin@test.local"; db.create_tenant(admin, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Op", is_admin=True)
a = TestClient(app); a.post("/login", data={"email": admin, "password": "pw123456"})
a.post(f"/admin/decisions/{rd['id']}/approve"); assert sent and sent[-1][0] == "someone@gmail.com" and "/u/" in sent[-1][2]
print("question: จับคู่จากลิงก์เดิม · CEO ร่างคำตอบรออนุมัติ → อนุมัติแล้วส่ง OK")

assert I.process(I.parse_raw(raw("info@c.co.th", "Re: x", "ขอบคุณครับ ตอนนี้ไม่สนใจ")))["category"] == "not_interested" and db.get_lead(C)["status"] == "lost"
assert I.process(I.parse_raw(raw("info@d.co.th", "Re: x", "กรุณาหยุดส่งอีเมลมาครับ")))["category"] == "unsubscribe" and db.get_lead(Dd)["status"] == "unsubscribed"
r = I.process(I.parse_raw(raw("sale@e.co.th", "Automatic reply: ผลตรวจ", "ไม่อยู่ที่สำนักงานถึงวันจันทร์", extra="Auto-Submitted: auto-replied\r\n")))
assert r["category"] == "auto" and db.get_lead(E)["status"] == "contacted" and not F.history(E)["replied"]
r = I.process(I.parse_raw(raw("MAILER-DAEMON@mx.google.com", "Delivery Status Notification (Failure)", "Your message to info@g.co.th could not be delivered\n\n info@g.co.th")))
assert r["category"] == "bounce" and db.get_lead(G)["email"] is None
# ธุรกิจใหม่อีเมลมาเอง → ลีดใหม่ ตรวจเว็บ → แจ้งเจ้าของนัดโทร (ไม่ทำใบเสนอราคาเอง)
import app.site_health as sh
sh.run_checks = lambda site, **kw: {"checks": [{"key": "csr", "status": "fail", "label": "x"}], "ok": False, "n_fail": 1, "n_ok": 0}
r = I.process(I.parse_raw(raw("Khun Som <som@newbiz.co.th>", "สอบถามบริการ", "สนใจทำให้เว็บขึ้นใน ChatGPT ครับ ราคาเท่าไหร่")))
nl = db.find_lead_by_domain("newbiz.co.th")
assert r["category"] == "interested" and nl and nl["source"] == "email" and nl["consent"] == 1 and nl["name"] == "Khun Som" and nl["audit_json"]
assert "ลีดใหม่จากอีเมล" in r["action"] and not db.list_quotes(nl["id"]) and any(e["kind"] == "wants_call" for e in db.list_lead_events(nl["id"]))
assert I.process(I.parse_raw(raw("noreply@shopee.co.th", "Automatic reply", "x", extra="Auto-Submitted: auto-generated\r\n")))["category"] == "unmatched"   # ระบบอัตโนมัติไม่สร้างลีด
assert I.process(I.parse_raw(raw("news@vendor.co.th", "โปรเดือนนี้", "ลดราคาพิเศษ สนใจคลิก", extra="List-Unsubscribe: <mailto:u@vendor.co.th>\r\n")))["category"] == "unmatched"   # จดหมายข่าว
assert I.process(I.parse_raw(raw("notifications@tool.co.th", "แจ้งเตือน", "บัญชีของคุณ")))["category"] == "unmatched"           # แจ้งเตือนระบบ
assert not db.find_lead_by_domain("vendor.co.th") and not db.find_lead_by_domain("tool.co.th")
r = I.process(I.parse_raw(raw("friend@hotmail.com", "เย็นนี้กินข้าว", "ไปไหม")))
assert r["category"] == "unmatched"
with db.get_conn() as c:
    row = c.execute("SELECT * FROM inbox_messages WHERE category='unmatched'").fetchone()
assert row["body"] == "" and row["from_addr"] == "" and row["subject"] == ""                       # ไม่เก็บเนื้อหาอีเมลที่ไม่ใช่ลีด
print("others: ไม่สนใจ → ปิด · ขอไม่รับ · อัตโนมัติไม่นับ · เด้ง → ล้างอีเมล · ธุรกิจใหม่ → ลีดใหม่ · อีเมลฟรี/อัตโนมัติไม่เก็บ OK")

# IMAP: อ่านอย่างเดียว · เฉพาะที่ส่งถึงที่อยู่ของเรา
msgs = {b"1": raw("info@b.co.th", "Re: ผลตรวจ", "สนใจครับ โทรมาได้เลย", mid="imap1"),
        b"2": raw("boss@company.com", "ประชุม", "ส่วนตัว", to="me@gmail.com", mid="imap2")}
calls = []
class FakeImap:
    def __init__(s, host): calls.append(("host", host))
    def login(s, u, p): calls.append(("login", u))
    def select(s, f, readonly=False): calls.append(("select", f, readonly)); return ("OK", [b"2"])
    def search(s, charset, crit): calls.append(("search", crit)); return ("OK", [b"1 2"])
    def fetch(s, i, what): calls.append(("fetch", i, what)); return ("OK", [(b"x", msgs[i])])
    def logout(s): pass
I.IMAP = FakeImap
I.save_config("imap.gmail.com", "me@gmail.com", "app-pass", "INBOX")
assert "app-pass" not in (db.get_setting("imap_pass") or "")                                       # รหัสผ่านเข้ารหัสเก็บ
res = I.poll(); assert res["processed"] == 1 and res["by"] == {"interested": 1}, res
assert ("select", "INBOX", True) in calls and all(c[2] == "(BODY.PEEK[])" for c in calls if c[0] == "fetch") and any('TO "report@appreview.cloud"' in c[1] for c in calls if c[0] == "search")
print("imap: อ่านอย่างเดียว · BODY.PEEK · เฉพาะที่ส่งถึงที่อยู่ของเรา · รหัสผ่านเข้ารหัส OK")

c = TestClient(app)
assert c.post("/inbox/hook?secret=wrong", content=b"x").status_code == 403
r = c.post(f"/inbox/hook?secret={I.hook_secret()}", content=raw("info@g.co.th", "Re", "ราคาเท่าไหร่ครับ", mid="hook1")); assert r.json().get("category") in ("interested", "question")
r = a.get("/admin/inbox"); assert "อีเมลตอบกลับ" in r.text and "สนใจ/ถามราคา" in r.text and "app-pass" not in r.text and "Email Worker" in r.text
print("hook/admin: webhook ต้องมีรหัสลับ · หน้าแอดมินไม่โชว์รหัสผ่าน OK")
print("ALL OK")
