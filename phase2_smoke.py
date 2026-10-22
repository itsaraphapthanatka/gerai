"""ทดสอบเฟส 2: ทัก → ลูกค้าเปิดอ่าน/ตอบกลับ → CEO เสนอใบเสนอราคา → ส่ง → ลูกค้ายืนยัน → แจ้งชำระ → CFO เสนอยืนยันยอด → เปิดบัญชี+แบรนด์
+ ขอไม่รับการติดต่อ · ด่านสลับโหมดตัดสินเอง (≥20 เรื่อง ≥95%) · หยุดฉุกเฉิน · LINE webhook (ลายเซ็น, ตรวจเว็บในแชต, สนใจ, ผูกเจ้าของ) · KPI ด่าน
ไม่ยิงเครือข่าย: ตัวตรวจเว็บ/โมเดล/อีเมล/LINE/PDF เป็นของปลอม · DB ชั่วคราว"""
import os, tempfile, json, re, base64, hmac, hashlib
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke2-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.leads as L, app.execs as X, app.quotes as Q, app.pipeline as P, app.line_oa as LO
import app.site_health as sh, app.mailer as mailer, app.ai_client as ai, app.report as report
from app.main import app

def rep(*fails, ok=10):
    checks = [{"key": k, "label": f"ข้อ {k}", "status": "fail", "detail": "รายละเอียด"} for k in fails]
    return {"ok": not checks, "n_fail": len(checks), "n_ok": ok, "checks": checks + [{"key": "x", "label": "ผ่าน", "status": "ok"}] * ok}
def kinds(lead_id): return [e["kind"] for e in db.list_lead_events(lead_id)]

db.init_db()
sh.run_checks = lambda site, **kw: rep("csr", "indexed", "orphan")
ai._read_setting = lambda k: None
ai._chat = lambda m, max_tokens=2048, timeout=120: (_ for _ in ()).throw(RuntimeError("no model"))
sent = []
mailer.config = lambda: {"host": "x"}
mailer.send = lambda to, subject, html, text="", attachments=(): sent.append((to, subject, text, html))
pushes = []
LO._post = lambda path, payload: (pushes.append((path, payload)) or True)
P.RUN_INLINE = True
report.to_pdf = lambda html: b"%PDF-1.4 fake\n" + html.encode("utf-8")[:40]
c = TestClient(app)
admin = "admin@test.local"
db.create_tenant(admin, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Op", is_admin=True)
a = TestClient(app); a.post("/login", data={"email": admin, "password": "pw123456"})

# ---- 1) ตรวจ → ยินยอม → CEO เสนอทัก → อนุมัติ: อีเมลมีลิงก์ติดตามการเปิด + ลิงก์ขอไม่รับ ----
r = c.post("/audit", data={"url": "shop.co.th"}); token = str(r.url).rsplit("/", 1)[-1]
c.post(f"/audit/{token}/contact", data={"name": "คุณเอ", "email": "owner@shop.co.th", "consent": "1"})
lead = db.get_lead_by_token(token)
pend = db.list_decisions("proposed"); assert len(pend) == 1 and pend[0]["kind"] == "outreach"
a.post(f"/admin/decisions/{pend[0]['id']}/approve")
assert sent[-1][0] == "owner@shop.co.th" and f"/u/{token}" in sent[-1][2] and f"/audit/{token}?via=mail" in sent[-1][2], sent[-1][2]
assert db.get_lead(lead["id"])["status"] == "contacted" and {"audit", "contacted"} <= set(kinds(lead["id"]))
print("outreach: ส่งอีเมลพร้อมลิงก์ติดตาม + ลิงก์ขอไม่รับการติดต่อ OK")

# ---- 2) ลูกค้าเปิดจากอีเมล → ตอบกลับ → CEO เสนอใบเสนอราคา (ใบเดียว ไม่ซ้ำ) ----
c.get(f"/audit/{token}?via=mail"); assert "opened" in kinds(lead["id"])
r = c.post(f"/audit/{token}/reply", data={"message": "สนใจครับ"}); assert "กำลังจัดทำใบเสนอราคา" in r.text
assert db.get_lead(lead["id"])["status"] == "replied"
qd = [d for d in db.list_decisions("proposed") if d["kind"] == "quote"]; assert len(qd) == 1 and qd[0]["agent"] == "ceo"
q = db.list_quotes(lead["id"])[0]
assert q["status"] == "draft" and q["package"] == "pro" and q["setup"] == 1 and q["first_total"] == 19800 and q["number"].startswith("Q"), dict(q)
c.post(f"/audit/{token}/reply", data={}); assert len(db.list_quotes(lead["id"])) == 1                # ใบเดิมยังเดินอยู่ ไม่สร้างใบใหม่
assert c.get(f"/q/{q['token']}", follow_redirects=False).status_code == 303                           # ร่างยังไม่เปิดสาธารณะ
print("reply: เปิดอ่าน → ตอบกลับ → CEO เสนอใบเสนอราคา (Pro + GEO Setup เพราะตกข้อโครงสร้าง) OK")

# ---- 3) อนุมัติ → ส่งใบเสนอราคา → ลูกค้าเปิดดู/ยืนยัน → สัญญา + PDF ----
sent.clear(); a.post(f"/admin/decisions/{qd[0]['id']}/approve")
assert sent and f"/q/{q['token']}" in sent[-1][2] and db.get_quote(q["id"])["status"] == "sent" and db.get_lead(lead["id"])["status"] == "quoted"
r = c.get(f"/q/{q['token']}"); assert r.status_code == 200 and "ยืนยันรับข้อเสนอ" in r.text and "19,800" in r.text and db.get_quote(q["id"])["status"] == "viewed"
assert "ติ๊กยอมรับ" in c.post(f"/q/{q['token']}/accept", data={"name": "คุณเอ"}).text
r = c.post(f"/q/{q['token']}/accept", data={"name": "คุณเอ", "agree": "1"})
q = db.get_quote(q["id"]); assert q["status"] == "accepted" and q["accepted_name"] == "คุณเอ" and "แจ้งชำระเงินแล้ว" in r.text
r = c.get(f"/q/{q['token']}/contract"); assert r.status_code == 200 and "สัญญาบริการมาตรฐาน" in r.text and "ร่างมาตรฐาน" in r.text and "คุณเอ" in r.text
r = c.get(f"/q/{q['token']}.pdf"); assert r.status_code == 200 and r.headers["content-type"].startswith("application/pdf")
assert c.get(f"/q/{q['token']}/contract.pdf").status_code == 200
print("quote: ส่ง → เปิดดู → ยืนยัน (ชื่อ+ยอมรับ) → สัญญา/PDF OK")

# ---- 4) แจ้งชำระ (สลิป) → CFO เสนอยืนยันยอด → อนุมัติ → เปิดบัญชี+แบรนด์ → ปิดดีล → อีเมลต้อนรับ ----
c.post(f"/q/{q['token']}/pay", files={"slip": ("slip.png", b"\x89PNG fake", "image/png")})
q = db.get_quote(q["id"]); assert q["status"] == "claimed" and q["slip_path"] and "payment_claimed" in kinds(lead["id"])
pd = [d for d in db.list_decisions("proposed") if d["kind"] == "payment"]; assert len(pd) == 1 and pd[0]["agent"] == "cfo"
assert a.get(f"/admin/quotes/{q['id']}/slip").status_code == 200
sent.clear(); a.post(f"/admin/decisions/{pd[0]['id']}/approve")
q = db.get_quote(q["id"]); assert q["status"] == "paid" and q["tenant_id"] and q["brand_id"], dict(q)
t = db.get_tenant(q["tenant_id"]); assert t["email"] == "owner@shop.co.th" and t["plan"] == "pro" and not t["is_admin"]
b = db.get_brand(q["brand_id"]); assert b["tenant_id"] == t["id"] and b["auto_content_weekly"] == 3 and b["autopilot"] == 1 and b["domain"] == "https://shop.co.th"
assert db.get_lead(lead["id"])["status"] == "won" and {"paid", "provisioned", "won"} <= set(kinds(lead["id"]))
assert sent and sent[-1][0] == "owner@shop.co.th" and "พร้อมแล้ว" in sent[-1][1]
temp_pw = re.search(r"<code>([^<]+)</code>", sent[-1][3]).group(1)
cust = TestClient(app); r = cust.post("/login", data={"email": "owner@shop.co.th", "password": temp_pw}); assert r.status_code == 200 and "/login" not in str(r.url)
assert "รหัสผ่านปัจจุบันไม่ถูกต้อง" in cust.post("/account/password", data={"current": "wrong", "new": "newpass123", "confirm": "newpass123"}).text
assert "เปลี่ยนรหัสผ่านแล้ว" in cust.post("/account/password", data={"current": temp_pw, "new": "newpass123", "confirm": "newpass123"}).text
k = X.kpis(2); v = k["values"]
assert v["new_paying"] == 1 and v["audits_week"] >= 1 and v["reply_rate"] == 100 and v["close_rate"] == 100 and v["escalations_week"] == 0 and len(k["rows"]) == 6, v
print("payment: แจ้งชำระ → CFO เสนอ → อนุมัติ → บัญชี pro + แบรนด์ auto 3/สัปดาห์ → ปิดดีล → ล็อกอินด้วยรหัสชั่วคราว → เปลี่ยนรหัส OK")

# ---- 5) หน้าแอดมิน ----
r = a.get("/admin/decisions"); assert "ด่านเฟส 2" in r.text and "โหมดการตัดสินใจต่อเรื่อง" in r.text and "เรื่องเงิน" in r.text
r = a.get(f"/admin/leads/{lead['id']}"); assert r.status_code == 200 and q["number"] in r.text and "ไทม์ไลน์" in r.text and "ชำระแล้ว" in r.text
r = a.get("/admin/leads"); assert "ทักแล้ว → ตอบกลับ" in r.text and f"/admin/leads/{lead['id']}" in r.text
r = a.get("/admin/settings"); assert "LINE Official Account" in r.text and "ข้อมูลบริษัท" in r.text and "ยังไม่มีคีย์" in r.text
a.post("/admin/settings/company", data={"name": "บริษัท ทดสอบ จำกัด", "contract_reviewed": "1"})
r = c.get(f"/q/{q['token']}/contract"); assert "บริษัท ทดสอบ จำกัด" in r.text and "ร่างมาตรฐาน" not in r.text
print("admin: ด่าน · โหมดต่อเรื่อง · หน้าลูกค้า · กรวย · ตั้งค่า LINE/บริษัท · สัญญาเอาป้ายร่างออก OK")

# ---- 6) ขอไม่รับการติดต่อ: ยกเลิกข้อเสนอค้าง ไม่ทักซ้ำ ไม่นับเป็นไม่ตรงกัน ----
r = c.post("/audit", data={"url": "two.co.th"}); t2 = str(r.url).rsplit("/", 1)[-1]
c.post(f"/audit/{t2}/contact", data={"email": "hi@two.co.th", "consent": "1"})
l2 = db.get_lead_by_token(t2); d2 = [d for d in db.list_decisions("proposed") if d["lead_id"] == l2["id"]]; assert len(d2) == 1
assert "ยืนยัน ไม่รับการติดต่อ" in c.get(f"/u/{t2}").text
assert "เรียบร้อย" in c.post(f"/u/{t2}").text
l2 = db.get_lead(l2["id"]); assert l2["status"] == "unsubscribed" and l2["consent"] == 0 and l2["unsub_at"]
assert db.get_decision(d2[0]["id"])["status"] == "cancelled" and P.ceo_propose(l2) is None
assert "ไม่ส่ง" in P.execute_decision(d2[0], "admin") and db.get_lead(l2["id"])["status"] == "unsubscribed"
st = db.decision_stats(); assert st["rejected"] == 0
print("unsubscribe: ปิดช่องทาง · ยกเลิกข้อเสนอค้าง · ไม่ทักซ้ำ OK")

# ---- 7) สลับโหมดตัดสินเอง: ด่านในโค้ด · เรื่องเงินห้าม · หยุดฉุกเฉิน ----
g = X.auto_gate("outreach"); assert not g["allowed"] and "20" in g["reason"]
assert not X.set_auto("outreach", True)[0] and not X.set_auto("quote", True)[0] and not X.set_auto("payment", True)[0]
for i in range(20):
    did = db.add_decision("ceo", "outreach", f"t{i}", "p", {}); db.set_decision(did, "executed", "admin", "ok")
assert X.auto_gate("outreach")["allowed"] and X.set_auto("outreach", True)[0] and X.auto_enabled("outreach")
assert not X.auto_gate("quote")["eligible"]
r = c.post("/audit", data={"url": "three.co.th"}); t3 = str(r.url).rsplit("/", 1)[-1]
sent.clear(); c.post(f"/audit/{t3}/contact", data={"email": "hi@three.co.th", "consent": "1"})
l3 = db.get_lead_by_token(t3); d3 = [d for d in db.list_decisions(None, 50) if d["lead_id"] == l3["id"]]
assert d3 and d3[0]["status"] == "auto" and l3["status"] == "contacted" and sent and sent[-1][0] == "hi@three.co.th", [dict(x) for x in d3]
X.set_paused(True); assert not X.auto_enabled("outreach") and X.mode_label() == "หยุดฉุกเฉิน"; X.set_paused(False)
assert "ตัดสินเอง" in X.mode_label()
a.post("/admin/decisions/auto", data={"kind": "outreach", "on": "0"}); assert not X.auto_enabled("outreach")
assert "สลับไม่ได้" in a.post("/admin/decisions/auto", data={"kind": "quote", "on": "1"}).text
a.post("/admin/agents/pause", data={"on": "1"}); assert X.paused(); a.post("/admin/agents/pause", data={"on": "0"}); assert not X.paused()
print("auto-mode: ด่าน 20 เรื่อง/95% · เรื่องเงินห้าม · ทำเองเมื่อเปิด · หยุดฉุกเฉิน OK")

# ---- 8) LINE: ลายเซ็น · ตรวจเว็บในแชต · 'สนใจ' → ใบเสนอราคา · ผูกเจ้าของ · สรุปเช้าทาง LINE ----
LO.save_config("tok", "sec"); assert LO.available() and LO.status() == "พร้อม (ยังไม่ผูกเจ้าของ)"
assert LO.bot_name() == "เจอ.AI"; db.set_setting("line_bot_name", "Hop"); assert LO.bot_name() == "Hop"      # ชื่อตาม OA ที่ผูก
sign = lambda body: base64.b64encode(hmac.new(b"sec", body, hashlib.sha256).digest()).decode()
body = json.dumps({"events": []}).encode()
assert c.post("/line/webhook", content=body, headers={"X-Line-Signature": "bad"}).status_code == 403
assert c.post("/line/webhook", content=body, headers={"X-Line-Signature": sign(body)}).status_code == 200
def ev(text, uid="U123", rt="rt"): return json.dumps({"events": [{"type": "message", "replyToken": rt, "source": {"userId": uid}, "message": {"type": "text", "text": text}}]}).encode()
body = json.dumps({"events": [{"type": "follow", "replyToken": "rt0", "source": {"userId": "U123"}}]}).encode()
pushes.clear(); assert c.post("/line/webhook", content=body, headers={"X-Line-Signature": sign(body)}).json()["handled"] == ["follow"]
assert "นี่คือ Hop" in pushes[-1][1]["messages"][0]["text"] and "เจอ.AI" not in pushes[-1][1]["messages"][0]["text"]
pushes.clear(); body = ev("ช่วยตรวจ four.co.th หน่อยครับ")
assert c.post("/line/webhook", content=body, headers={"X-Line-Signature": sign(body)}).json()["handled"] == ["started"]
l4 = db.find_lead_by_domain("four.co.th"); assert l4["source"] == "line" and l4["line_user_id"] == "U123" and l4["audit_json"] and l4["contact"] == "LINE"
assert any(p[0] == "/message/reply" for p in pushes) and any(p[0] == "/message/push" and "ผลตรวจ four.co.th" in p[1]["messages"][0]["text"] for p in pushes), pushes
body = ev("four.co.th"); assert c.post("/line/webhook", content=body, headers={"X-Line-Signature": sign(body)}).json()["handled"] == ["cached"]
body = ev("สนใจครับ"); assert c.post("/line/webhook", content=body, headers={"X-Line-Signature": sign(body)}).json()["handled"] == ["replied"]
assert "ทีม Hop จะส่งใบเสนอราคา" in pushes[-1][1]["messages"][0]["text"]
l4 = db.get_lead(l4["id"]); assert l4["status"] == "replied" and l4["consent"] == 1
assert any(d["kind"] == "quote" and d["lead_id"] == l4["id"] for d in db.list_decisions("proposed"))
body = ev("สวัสดี"); assert c.post("/line/webhook", content=body, headers={"X-Line-Signature": sign(body)}).json()["handled"] == ["help"]
code = LO.link_code(); body = ev(code, uid="UOWNER")
assert c.post("/line/webhook", content=body, headers={"X-Line-Signature": sign(body)}).json()["handled"] == ["owner_linked"] and LO.owner_id() == "UOWNER" and LO.link_code() != code
pushes.clear(); res = X.morning_digest("https://x")
assert "LINE" in res["sent_to"] and "ด่านเฟส 2" in res["text"] and any(p[1].get("to") == "UOWNER" for p in pushes)
r = a.get("/admin/settings"); assert "พร้อม" in r.text and "UOWN" in r.text
print("line: ลายเซ็น · follow · ตรวจเว็บในแชต (ลูกค้าเป้าหมายแหล่ง LINE) · ผลเดิม · สนใจ → ใบเสนอราคา · ผูกเจ้าของ · สรุปเช้าทาง LINE OK")
print("\nphase2_smoke: ผ่านทั้งหมด")
