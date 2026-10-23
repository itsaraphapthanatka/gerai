"""ทดสอบฝ่ายการตลาด (CMO): QA + เผยแพร่ร่างของเจอ.AI เอง · เขียนโพสต์ (บทความ/ชวนตรวจ/เคสศึกษา) · เสนอเป็น decision kind post
· โควตาต่อสัปดาห์ ไม่โพสต์บทความซ้ำ · อนุมัติ → broadcast LINE / โพสต์ Facebook (ปลอม) · โหมดตัดสินเอง · รหัสช่องทางบน /audit → ตารางลีดตามช่องทาง · หน้าแอดมิน"""
import os, tempfile, json, re
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke5-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ.setdefault("GEO_DEEP_AUDIT", "0")
os.environ.setdefault("GEO_CS", "0")   # ไม่สั่ง Autopilot จริงตอนยืนยันยอดในเทสต์นี้   # ไม่ไต่เว็บจริงเบื้องหลังในเทสต์นี้
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.marketing as MK, app.execs as X, app.pipeline as P, app.line_oa as LO, app.site_health as sh, app.ai_client as ai, app.mailer as mailer
from app.main import app

def rep(*fails, ok=10):
    checks = [{"key": k, "label": f"ข้อ {k}", "status": "fail", "detail": ""} for k in fails]
    return {"ok": not checks, "n_fail": len(checks), "n_ok": ok, "checks": checks + [{"key": "x", "label": "ผ่าน", "status": "ok"}] * ok}
db.init_db()
sh.run_checks = lambda site, **kw: rep("csr")
ai._read_setting = lambda k: None
ai._chat = lambda m, max_tokens=2048, timeout=120: (_ for _ in ()).throw(RuntimeError("no model"))
mailer.config = lambda: {"host": "x"}; mailer.send = lambda *a, **k: None
P.RUN_INLINE = True
pushes = []; LO._post = lambda path, payload: (pushes.append((path, payload)) or True)
fb_calls = []; MK.fb_post = lambda cfg, message, link: (fb_calls.append((cfg["page_id"], message, link)) or "123_456")
c = TestClient(app)
admin = "admin@test.local"; db.create_tenant(admin, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Op", is_admin=True)
a = TestClient(app); a.post("/login", data={"email": admin, "password": "pw123456"})

# ---- แบรนด์ของตัวเอง + ร่าง 3 ชิ้น (2 ผ่าน QA, 1 ไม่ผ่าน) ----
r = a.post("/seed-demo"); bid = int(re.search(r"/brands/(\d+)", str(r.url)).group(1)); db.set_setting("self_brand_id", str(bid))
good_body = "> สรุป: ทำให้ AI รู้จักธุรกิจคุณใน 3 ขั้น\n\n## ขั้นที่ 1\n- ติดตั้ง /geo\n- ใส่ llms.txt\n\n## ขั้นที่ 2\nเขียนคำตอบ"
faq = json.dumps([{"@type": "FAQPage", "mainEntity": [{"name": "q1"}, {"name": "q2"}]}])
c1 = db.create_content_item(bid, None, "th", "ทำให้ AI รู้จักธุรกิจคุณใน 3 ขั้น", "ทำให้ AI รู้จักธุรกิจคุณ", "คู่มือสั้น ๆ ที่เจ้าของธุรกิจทำเองได้ใน 1 วัน ตั้งแต่ติดตั้งจนถึงวัดผล แบบไม่ต้องเขียนโค้ด", good_body, faq, "test")
c2 = db.create_content_item(bid, None, "th", "robots.txt กันบอท AI อยู่หรือเปล่า", "robots.txt กับบอท AI", "เช็ก 5 บรรทัดใน robots.txt ที่ทำให้ ChatGPT และ Gemini อ่านเว็บคุณไม่ได้ พร้อมวิธีแก้", good_body, faq, "test")
c3 = db.create_content_item(bid, None, "th", "การันตีอันดับ 1 ใน 7 วัน", "การันตีอันดับ", "สั้น", "ไม่มีโครงสร้าง", "[]", "test")
pub = MK.publish_self_drafts(5)
assert sorted(x["id"] for x in pub) == sorted([c1, c2]) and db.get_content(c3)["status"] == "draft" and db.get_content(c1)["status"] == "published"
assert db.get_content(c1)["wp_link"].endswith(f"/geo/{c1}")
print("publish: ร่างที่ผ่าน QA ขึ้น /geo เอง · คำต้องห้าม/ไม่มีโครงสร้างไม่ผ่าน OK")

# ---- เขียนโพสต์ ----
it = db.get_content(c1)
p1 = MK.compose("line", "article", it); assert it["title"] in p1["text"] and f"/geo/{c1}" in p1["text"] and "/audit?src=line" in p1["text"] and "สรุป:" not in p1["text"]
p2 = MK.compose("facebook", "audit_cta"); assert "/audit?src=fb" in p2["text"] and p2["content_id"] is None
cs = MK.case_study_text({"name": "ร้านเอ", "market": "เสื้อผ้า"}, {"days": 30, "published": 12, "sov_first": 0, "sov_last": 3, "health_ok": 9, "health_fail": 1, "market": "เสื้อผ้า"}, named=False,
                        llm=lambda m: json.dumps({"title": "เคสจริง", "body": "ธุรกิจเสื้อผ้าแห่งหนึ่ง เผยแพร่ 12 ชิ้นใน 30 วัน การมองเห็นใน AI จาก 0 เป็น 3 สุขภาพเว็บผ่าน 9 ข้อ"}))
assert "ร้านเอ" not in cs["body"] and "12" in cs["body"]
cs2 = MK.case_study_text({"name": "ร้านเอ", "market": "เสื้อผ้า"}, {"days": 30, "published": 12, "sov_first": None, "sov_last": None, "health_ok": None, "health_fail": None, "market": "เสื้อผ้า"}, named=True,
                         llm=lambda m: json.dumps({"title": "การันตีอันดับ 1", "body": "รับประกันผล 100% ภายใน 7 วัน ลูกค้าเพิ่ม 300%"}))
assert "การันตี" not in cs2["title"] and "300" not in cs2["body"] and "ร้านเอ" in cs2["title"]            # คำต้องห้าม/ตัวเลขแต่ง → แม่แบบ
print("compose: บทความ+ลิงก์ติดรหัสช่องทาง · ชวนตรวจ · เคสศึกษาไม่ระบุชื่อ/กันคำต้องห้าม OK")

# ---- เสนอโพสต์ (LINE พร้อม, Facebook ยังไม่มี) → รอคน · โควตา · ไม่ซ้ำ ----
LO.save_config("tok", "sec")
assert MK.channels() == ["line"]
import app.post_studio as PS
PS.RENDER = lambda h: (_ for _ in ()).throw(RuntimeError("no render"))          # การ์ดรูปทดสอบแยกใน post_smoke
MK._LLM = lambda m: json.dumps({"hook": "ลองเช็กเว็บของคุณดูสักนิด", "bullets": ["ข้อแรกที่ควรดู", "ข้อสองที่มักพลาด", "ข้อสามที่แก้ง่าย"], "question": "เว็บคุณเป็นแบบไหน", "hashtags": ["#SEO", "#AI"]})
res = MK.run(llm=lambda m: "{}")
assert res["proposed"] == 3 and res["by_channel"] == {"line": 3}, res                                   # 2 บทความ + เคล็ดลับ 1 (ยังไม่มีสถิติพอทำ insight)
pend = [d for d in db.list_decisions("proposed") if d["kind"] == "post"]; assert len(pend) == 3 and all(d["agent"] == "cmo" for d in pend)
assert MK.run(llm=lambda m: "{}")["proposed"] == 0                                                       # รอบถัดไป: ครบโควตา 3/สัปดาห์ และไม่ซ้ำ
r = a.get("/admin/decisions"); assert "โพสต์การตลาด" in r.text and "CMO" in r.text
post_row = db.get_post(json.loads(pend[0]["payload_json"])["post_id"]); assert post_row["status"] == "proposed" and post_row["channel"] == "line"
pushes.clear(); r = a.post(f"/admin/decisions/{pend[0]['id']}/approve"); assert "โพสต์" in r.text and "LINE OA" in r.text
assert pushes and pushes[-1][0] == "/message/broadcast" and db.get_post(post_row["id"])["status"] == "published"
a.post(f"/admin/decisions/{pend[1]['id']}/reject", data={"reason": "ยังไม่เอา"}); assert db.get_decision(pend[1]["id"])["status"] == "rejected"
print("propose: เสนอเป็น decision ของ CMO · โควตา/ไม่ซ้ำ · อนุมัติ → broadcast LINE OK")

# ---- Facebook: ตั้ง Page → ช่องทางเพิ่ม · โหมดตัดสินเอง (ปลดล็อกโดยเจ้าของ) → โพสต์เองทันที ----
MK.fb_identity = lambda token: {"id": "1234567890", "name": "เพจทดสอบ", "link": "https://facebook.com/x"} if token in ("EAAfaketoken", "EAAlonglived") else None
r = a.post("/admin/marketing/settings", data={"enabled": "1", "weekly": "5", "line": "1", "fb_page_id": "61500000000000", "fb_token": "EAAfaketoken"})
assert "Facebook Page: เพจทดสอบ (ID 1234567890)" in r.text and db.get_setting("fb_page_id") == "1234567890" and "หมดอายุใน ~1 ชม." in r.text   # แก้ Page ID ที่กรอกผิดให้เอง · เตือนอายุสั้น
# มี App ID/secret → แปลงเป็น token ถาวรของเพจ + รู้สิทธิ์/วันหมดอายุ
import httpx as _hx
def fake_get(url, params=None, timeout=20):
    class R:
        def __init__(self, code, body): self.status_code = code; self._b = body; self.content = b"x"
        def json(self): return self._b
    if url.endswith("/oauth/access_token"): return R(200, {"access_token": "EAAlonglived"})
    if url.endswith("/me/accounts"): return R(200, {"data": [{"id": "1234567890", "name": "เพจทดสอบ", "access_token": "EAApagetoken"}]})
    if url.endswith("/debug_token"): return R(200, {"data": {"type": "PAGE", "expires_at": 0, "scopes": ["pages_show_list"], "granular_scopes": [{"scope": "pages_manage_posts", "target_ids": ["1234567890"]}, {"scope": "pages_read_engagement"}], "is_valid": True}})
    return R(400, {"error": {"message": "unexpected " + url}})
MK.httpx.get = fake_get
MK.fb_identity = lambda token: {"id": "1234567890", "name": "เพจทดสอบ", "link": ""} if token in ("EAApagetoken", "EAAshort", "EAAlonglived") else None
r = a.post("/admin/marketing/settings", data={"enabled": "1", "weekly": "5", "line": "1", "fb_page_id": "", "fb_token": "EAAshort", "fb_app_id": "9999", "fb_app_secret": "appsecret"})
assert "แปลงเป็น token ถาวรของเพจ เพจทดสอบ" in r.text and "ไม่หมดอายุ" in r.text and MK.fb_config()["token"] == "EAApagetoken" and "ขาดสิทธิ์" not in r.text, r.text[-400:]
r = a.get("/admin/marketing"); assert "ไม่หมดอายุ" in r.text and "pages_manage_posts" in r.text
# วาง user token ของบัญชีส่วนตัว (แลกอายุยาวไม่ได้) → ต้องเลือกเพจและใช้ page token ไม่ใช่ตั้ง Page ID เป็นโปรไฟล์คน
_saved = (MK.fb_longlive, MK.fb_identity, MK.fb_accounts)
MK.fb_longlive = lambda tok, pid="": (tok, "แลก token อายุยาวไม่สำเร็จ — ใช้ตามที่วาง")
MK.fb_identity = lambda token: {"id": "555", "name": "คนดูแลเพจ", "link": ""} if token == "EAAuser" else ({"id": "1234567890", "name": "เพจทดสอบ", "link": ""} if token == "EAApage2" else None)
MK.fb_accounts = lambda token: [{"id": "1234567890", "name": "เพจทดสอบ", "access_token": "EAApage2"}, {"id": "777", "name": "อีกเพจ", "access_token": "EAAx"}] if token == "EAAuser" else []
r = a.post("/admin/marketing/settings", data={"enabled": "1", "weekly": "5", "line": "1", "fb_page_id": "", "fb_token": "EAAuser"})
assert db.get_setting("fb_page_id") == "1234567890" and MK.fb_config()["token"] == "EAApage2" and "บัญชีส่วนตัว" in r.text, r.text[-300:]
db.set_setting("fb_page_want", "")
r = a.post("/admin/marketing/settings", data={"enabled": "1", "weekly": "5", "line": "1", "fb_page_id": "", "fb_token": "EAAuser"})
assert "กรอก Page ID" in r.text and db.get_setting("fb_page_id") != "555"                                     # หลายเพจ ไม่รู้จะเลือกอันไหน → ถาม
r = a.post("/admin/marketing/settings", data={"enabled": "1", "weekly": "5", "line": "1", "fb_page_id": "1234567890", "fb_token": "EAAuser"})
assert MK.fb_config()["token"] == "EAApage2" and db.get_setting("fb_page_id") == "1234567890"
MK.fb_longlive, MK.fb_identity, MK.fb_accounts = _saved
db.set_setting("fb_page_token", MK.wp_client.encrypt("EAApagetoken"))
# วางโทเค็นที่คัดลอกมาไม่ครบ → ไม่ทับของเดิม
r = a.post("/admin/marketing/settings", data={"enabled": "1", "weekly": "5", "line": "1", "fb_page_id": "", "fb_token": "EAAbroken"})
assert "คงโทเค็นเดิมไว้" in r.text and MK.fb_config()["token"] == "EAApagetoken"
_real_debug = MK.fb_debug
MK.fb_debug = lambda token: {"type": "PAGE", "expires_at": 0, "scopes": ["pages_show_list", "public_profile"], "valid": True}   # โทเค็นที่สิทธิ์ไม่ครบ
r = a.get("/admin/marketing"); assert MK.missing_scopes() == ["pages_manage_posts", "pages_read_engagement"] and "ขาดสิทธิ์ pages_manage_posts, pages_read_engagement" in r.text
MK.fb_debug = lambda token: {"type": "PAGE", "expires_at": 0, "scopes": [], "valid": False}                                  # โทเค็นเสีย
r = a.get("/admin/marketing"); assert "ใช้ไม่ได้ — วางใหม่" in r.text
MK.fb_debug = _real_debug; a.get("/admin/marketing"); assert MK.missing_scopes() == []
# ทดสอบสิทธิ์โพสต์แบบไม่เผยแพร่ (ปลอม Graph): สร้าง → ลบ · กรณีไม่มีสิทธิ์
posted = []
MK.httpx.post = lambda url, data=None, timeout=20: type("R", (), {"status_code": 200, "content": b"x", "json": lambda self: {"id": "1358_999"}})() if posted.append((url, data)) is None else None
MK.httpx.delete = lambda url, params=None, timeout=20: type("R", (), {"status_code": 200, "content": b"x", "json": lambda self: {"success": True}})()
assert MK.fb_test_post().startswith("โพสต์ได้") and posted[-1][1]["published"] == "false"
MK.httpx.post = lambda url, data=None, timeout=20: type("R", (), {"status_code": 403, "content": b"x", "json": lambda self: {"error": {"message": "(#200) requires pages_read_engagement"}}})()
assert "pages_read_engagement" in MK.fb_test_post()
r = a.post("/admin/marketing/fb-test"); assert "Facebook: โพสต์ไม่ได้" in r.text
assert MK.channels() == ["line", "facebook"] and MK.weekly() == 5
assert X.set_auto("post", True, override=True)[0] and X.auto_enabled("post")
fb_calls.clear(); res = MK.run(llm=lambda m: "{}")
assert res["by_channel"]["facebook"] == 3 and fb_calls and fb_calls[0][0] == "1234567890" and "/audit?src=fb" in " ".join(x[1] for x in fb_calls)
assert all(p["status"] == "published" for p in db.list_posts(50, channel="facebook")), [dict(p) for p in db.list_posts(50, channel="facebook")]
X.set_auto("post", False)
print("facebook: ตั้ง Page → โพสต์ · โหมดตัดสินเอง → โพสต์ทันทีไม่รอ OK")

# ---- รหัสช่องทางบน /audit → ตารางลีดตามช่องทาง ----
r = c.get("/audit?src=line"); assert 'name="src" value="line"' in r.text
r = c.post("/audit", data={"url": "fromline.co.th", "src": "line"}); assert "/audit/" in str(r.url)
r = c.post("/audit", data={"url": "direct.co.th", "src": "<bad>"})
assert db.find_lead_by_domain("fromline.co.th")["src"] == "line" and not db.find_lead_by_domain("direct.co.th")["src"]
att = MK.attribution(7); assert att.get("line") == 1 and att.get("direct") == 1, att
k = X.kpis(2); assert k["values"]["audits_by_src"].get("line") == 1
r = a.get("/admin/decisions"); assert "ตรวจฟรี 7 วันตามช่องทาง" in r.text and "line 1" in r.text
print("attribution: src บนลิงก์ → ลีดจำช่องทาง → KPI/หน้าการตัดสินใจ OK")

# ---- เคสศึกษา: ลูกค้าอายุ ≥ 28 วัน ----
with db.get_conn() as cx:
    cx.execute(db.q("UPDATE brands SET created_at=? WHERE id=?"), ("2026-01-01T00:00:00", bid)); cx.commit()
tid2 = db.create_tenant("cust@shop.co.th", bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(), "cust")
b2 = db.create_brand(tid2, "ร้านบี", "https://shopb.co.th", "เสื้อผ้า")
with db.get_conn() as cx:
    cx.execute(db.q("UPDATE brands SET created_at=? WHERE id=?"), ("2026-01-01T00:00:00", b2)); cx.commit()
for _i in range(3):                                                                                        # เคสศึกษาต้องมีงานเผยแพร่ ≥ 3 ชิ้น
    cid = db.create_content_item(b2, None, "th", f"บทความลูกค้า {_i}", "t", "d", good_body, faq, "test"); db.mark_content_published(cid, None, "x")
n = MK.propose_case_studies(llm=lambda m: json.dumps({"title": "เคสจริง: ธุรกิจเสื้อผ้า", "body": "เผยแพร่ 1 ชิ้นใน 29x วัน ไม่มีการันตี ตัวเลขจากระบบวัดจริง"}))
assert n == 2 and MK.propose_case_studies(llm=lambda m: "{}") == 0                                        # เดือนละครั้งต่อช่องทาง
posts_cs = [p for p in db.list_posts(50) if p["kind"] == "case_study"]; assert len(posts_cs) == 2 and "ร้านบี" not in posts_cs[0]["text"]
a.post(f"/admin/marketing/brand/{b2}/case-study", data={"ok": "1"}); assert db.get_brand(b2)["case_study_ok"] == 1
r = a.get("/admin/marketing"); assert r.status_code == 200 and "ลีดจากช่องทาง" in r.text and "เคสศึกษา" in r.text and "ใช้ชื่อได้" in r.text and "fromline.co.th" not in r.text
assert "รันรอบการตลาดตอนนี้" in r.text and "line" in r.text
print("case-study: เดือนละเรื่องต่อช่องทาง · ไม่ระบุชื่อจนกว่าจะอนุญาต · หน้าการตลาด OK")

# ---- ปิดฝ่ายการตลาด → ข้าม · สรุปเช้ามีบรรทัดการตลาด ----
db.set_setting("cmo_enabled", "0"); assert MK.run()["skipped"]; db.set_setting("cmo_enabled", "1")
assert "การตลาด:" in X.morning_digest("https://x")["text"]
print("digest/toggle OK")
print("\\nmarketing_smoke: ผ่านทั้งหมด")
