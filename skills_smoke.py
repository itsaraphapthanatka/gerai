"""ทดสอบทักษะเอเจนต์: โหลด/แก้ทับ/คืนค่า · แนบเป็น system prompt ใน chat_for ตามงาน · CMO เขียนโพสต์ตามทักษะ (ลิงก์ครบ คำต้องห้ามถอยแม่แบบ)
· กรอบอำนาจแก้ได้จากหน้าเว็บและ CFO ใช้ค่าใหม่ทันที · หน้าแอดมิน"""
import os, tempfile, json
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke6-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ.setdefault("GEO_DEEP_AUDIT", "0")
os.environ.setdefault("GEO_CS", "0")   # ไม่สั่ง Autopilot จริงตอนยืนยันยอดในเทสต์นี้   # ไม่ไต่เว็บจริงเบื้องหลังในเทสต์นี้
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.skills as SK, app.ai_client as ai, app.execs as X, app.marketing as MK
from app.main import app
db.init_db()

# ---- โหลด / แก้ทับ / คืนค่า ----
assert set(SK.AGENTS) == {"ceo", "cfo", "cto", "cmo", "prospecting", "delivery", "sdr", "cs", "acct", "deliv", "partner"}
for n in SK.AGENTS:
    t = SK.load(n); assert len(t) > 800 and t.startswith("# ทักษะ"), n
assert "การันตี" in SK.load("ceo") and "answer-first" in SK.load("delivery").lower() or "TL;DR" in SK.load("delivery")
SK.save("ceo", SK.default("ceo") + "\n\n## กฎเพิ่ม\nเรียกลูกค้าว่า 'คุณลูกค้า' เสมอ")
assert SK.is_overridden("ceo") and "กฎเพิ่ม" in SK.load("ceo")
SK.save("ceo", SK.default("ceo")); assert not SK.is_overridden("ceo")                      # บันทึกเท่าค่าเริ่มต้น = ไม่นับว่าแก้ทับ
SK.save("ceo", "x"); SK.reset("ceo"); assert SK.load("ceo") == SK.default("ceo")
try:
    SK.save("nobody", "x"); raise SystemExit("ต้องปฏิเสธชื่อที่ไม่รู้จัก")
except ValueError:
    pass
print("skills: โหลด 6 ทักษะ · แก้ทับ · คืนค่า OK")

# ---- แนบเป็น system prompt ตามงาน ----
msgs = [{"role": "user", "content": "สวัสดี"}]
out = SK.with_skill("sales", msgs); assert out[0]["role"] == "system" and out[0]["content"] == SK.load("ceo") and out[1] == msgs[0]
assert SK.with_skill("questions", msgs) == msgs                                            # งานคิดคำถามไม่ผูกทักษะ
assert SK.with_skill("sales", [{"role": "system", "content": "เดิม"}] + msgs)[0]["content"] == "เดิม"   # มี system อยู่แล้วไม่ทับ
seen = []
ai._read_setting = lambda k: None
ai._chat = lambda m, max_tokens=2048, timeout=120: (seen.append(m) or '{"ok": true}')
ai.chat_for("sales", msgs); assert seen[-1][0]["role"] == "system" and "CEO" in seen[-1][0]["content"]
ai.chat_for("marketing", msgs); assert "CMO" in seen[-1][0]["content"]
ai.chat_for("questions", msgs); assert seen[-1][0]["role"] == "user"
print("router: sales→CEO · marketing→CMO · questions ไม่แนบ OK")

# ---- CMO เขียนโพสต์ตามทักษะ ----
item = {"id": 7, "title": "robots.txt กันบอท AI อยู่หรือเปล่า", "body_md": "> สรุป: เช็ก 5 บรรทัด\n\nรายละเอียด..."}
good = lambda m: json.dumps({"text": "ลูกค้าถาม AI ก่อนโทรหาคุณไหม?\n\nเช็ก robots.txt 5 บรรทัด\n\nตรวจเว็บฟรี: https://geo.appreview.cloud/audit?src=line"})
p = MK.write_post("line", "article", item, llm=good)
assert p["source"] == "llm" and "/audit?src=line" in p["text"] and "/geo/7" in p["text"]        # เติมลิงก์บทความให้ถ้าขาด
bad = lambda m: json.dumps({"text": "การันตีอันดับ 1 ภายใน 7 วัน ลูกค้าเพิ่ม 300% แน่นอน ตรวจเลย https://geo.appreview.cloud/audit?src=line"})
p2 = MK.write_post("line", "article", item, llm=bad); assert p2["source"] == "template" and "การันตี" not in p2["text"]
assert MK.write_post("facebook", "audit_cta", llm=bad)["content_id"] is None                  # ชวนตรวจใช้แม่แบบเสมอ
print("cmo: เขียนโพสต์ด้วยโมเดล · ลิงก์ครบ · คำต้องห้ามถอยแม่แบบ OK")

# ---- กรอบอำนาจแก้ได้ · CFO ใช้ค่าใหม่ ----
assert X.MANDATE["max_discount_pct"] == 10 and X.cfo_check({"price": 9900, "discount_pct": 12})["ok"] is False
X.set_mandate("max_discount_pct", "15"); assert X.MANDATE["max_discount_pct"] == 15 and X.cfo_check({"price": 9900, "discount_pct": 12})["ok"]
X.set_mandate("max_discount_pct", 10); assert X.MANDATE.as_dict()["max_discount_pct"] == 10
try:
    X.set_mandate("max_deal_thb", "-5"); raise SystemExit("ต้องปฏิเสธค่าติดลบ")
except ValueError:
    pass
print("mandate: แก้จาก settings · CFO ใช้ทันที · กันค่าผิด OK")

# ---- หน้าแอดมิน ----
admin = "admin@test.local"; db.create_tenant(admin, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Op", is_admin=True)
a = TestClient(app); a.post("/login", data={"email": admin, "password": "pw123456"})
r = a.get("/admin/skills"); assert r.status_code == 200 and "ทักษะเอเจนต์" in r.text and "CEO — ขายและคัดลูกค้า" in r.text and "กรอบอำนาจ" in r.text and "ค่าเริ่มต้น" in r.text
r = a.get("/admin/skills?edit=cmo"); assert "<textarea" in r.text and "ทักษะ CMO" in r.text
r = a.post("/admin/skills/cmo", data={"text": SK.default("cmo") + "\n## เพิ่ม\nโพสต์ตอน 09:30"}); assert "บันทึกทักษะ" in r.text and SK.is_overridden("cmo") and "แก้ทับแล้ว" in r.text
r = a.post("/admin/skills/cmo/reset"); assert "คืนค่าเริ่มต้น" in r.text and not SK.is_overridden("cmo")
r = a.post("/admin/skills/mandate", data={"max_discount_pct": "12", "max_deal_thb": "", "refund_days": "7", "ai_cost_cap_thb": "570", "min_margin_pct": "70"})
assert "ส่วนลดสูงสุด (%): 10 → 12" in r.text and X.MANDATE["max_discount_pct"] == 12
assert any(d["title"] == "บอร์ดแก้กรอบอำนาจ" for d in db.list_decisions(None, 5))
r = a.post("/admin/skills/mandate", data={"max_discount_pct": "abc"}); assert "ค่าไม่ถูกต้อง" in r.text
r = a.post("/admin/skills/mandate", data={"yearly_discount_pct": "10"}); assert "ส่วนลดเมื่อจ่ายรายปี (%): 17 → 10" in r.text and X.MANDATE["yearly_discount_pct"] == 10
import app.quotes as Q; assert Q.price_for("pro", 0, False, "year")["period_total"] == 106920
r = a.post("/admin/skills/mandate", data={"yearly_discount_pct": "150"}); assert "ไม่เกิน 100" in r.text and X.MANDATE["yearly_discount_pct"] == 10
X.set_mandate("yearly_discount_pct", 17)
r = a.get("/admin/decisions"); assert "ส่วนลด ≤ 12%" in r.text                                # หน้าอื่นเห็นค่าใหม่
X.set_mandate("max_discount_pct", 10)
print("admin: หน้าทักษะ · แก้/คืนค่า · กรอบอำนาจ → บันทึกเป็นการตัดสินใจของบอร์ด OK")
print("\nskills_smoke: ผ่านทั้งหมด")
