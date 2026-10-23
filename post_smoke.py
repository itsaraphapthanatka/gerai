"""ทดสอบสตูดิโอโพสต์: สถิติจากการตรวจจริง (≥10 เว็บ · ไม่นับข้อที่วัดตัวเชื่อมของเรา) · ลองถาม AI จริง (ไม่ระบุชื่อลีด) · เคล็ดลับหมุนเวียน
· โมเดลใส่ตัวเลขนอกข้อมูล/คำต้องห้าม → แม่แบบ · บทความโมเดลล้ม → ไม่โพสต์ · การ์ดรูป → Facebook โพสต์รูป / LINE ส่งรูป+ข้อความ · เคสศึกษาไม่โชว์เลขที่แย่ลง"""
import os, tempfile, json
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke13-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["GEO_DEEP_AUDIT"] = "0"; os.environ["GEO_CS"] = "0"
from pathlib import Path
import app.db as db, app.post_studio as PS, app.marketing as MK, app.line_oa as LO, app.pipeline as P
db.init_db()
PS.GEN_DIR = Path(tempfile.mkdtemp(prefix="cards-"))
rendered = []; PS.RENDER = lambda h: (rendered.append(h) or b"\x89PNG fake")
P.RUN_INLINE = True

def chk(*fails):
    return json.dumps({"checks": [{"key": k, "status": "fail"} for k in fails] + [{"key": "home", "status": "ok"}]})
for i in range(12):
    lid = db.add_lead(f"https://s{i}.co.th", f"s{i}.co.th", source="prospect")
    db.update_lead(lid, audit_json=chk(*(["llms", "sitemap", "connector"] + (["www"] if i < 5 else []))), audit_at=db.now())
ds = PS.dataset(); assert ds["n"] == 12 and ds["stats"][0]["key"] == "llms" and ds["stats"][0]["pct"] == 100 and all(s["key"] not in ("sitemap", "connector") for s in ds["stats"])
f = PS.facts_for("insight"); assert f["big"] == "100%" and "12 จาก 12 เว็บ" in f["lines"][0] and "5 จาก 12 เว็บ (42%)" in f["lines"][1]
print("dataset: 12 เว็บ · ไม่นับข้อที่วัดตัวเชื่อมของเรา · เปอร์เซ็นต์ OK")

good = lambda m: json.dumps({"hook": "12 เว็บธุรกิจไทย ไม่มีไฟล์ที่ AI ใช้อ่านเลยสักเว็บ", "bullets": ["12 จาก 12 เว็บไม่มี llms.txt", "5 จาก 12 เว็บ www ไม่ชี้ที่เดียวกัน", "แก้ได้ครั้งเดียวจบ"], "question": "เว็บคุณมีหรือยัง", "hashtags": ["#SEO", "#AI"]})
c = PS.write(f, good); assert c["source"] == "llm" and c["hook"].startswith("12 เว็บ")
fake = lambda m: json.dumps({"hook": "90% ของธุรกิจเสียลูกค้า 300 รายต่อเดือน", "bullets": ["a", "b", "c"], "question": "q", "hashtags": []})
assert PS.write(f, fake)["source"].startswith("template") and "ตรวจเว็บธุรกิจไทย 12 เว็บ" in PS.write(f, fake)["hook"]   # ตัวเลขแต่ง → แม่แบบ
bad = lambda m: json.dumps({"hook": "การันตีติดอันดับ 1", "bullets": ["a", "b"], "question": "q", "hashtags": []})
assert PS.write(f, bad)["source"].startswith("template")
print("write: โมเดลตามข้อมูลจริง · ตัวเลขแต่ง/คำต้องห้าม → แม่แบบ OK")

post = PS.make("insight", "facebook", "https://x/audit?src=fb", "https://x", llm=good)
assert post["image"] and post["image"].startswith("https://x/static/gen/posts/insight-facebook-") and (PS.GEN_DIR / post["image"].rsplit("/", 1)[1]).exists()
assert post["text"].startswith("12 เว็บ") and "• 12 จาก 12" in post["text"] and "https://x/audit?src=fb" in post["text"] and "#SEO" in post["text"]
assert "100%" in rendered[-1] and "เจอ.AI" in rendered[-1] and "Sarabun" in rendered[-1]
PS.RENDER = lambda h: (_ for _ in ()).throw(RuntimeError("down")); assert PS.make("tip", "line", "https://x/a", "https://x", llm=good)["image"] is None   # รูปล้ม → ข้อความ
PS.RENDER = lambda h: (rendered.append(h) or b"png")
t0 = PS.facts_for("tip", 0); t1 = PS.facts_for("tip", 1); assert t0["hook"] != t1["hook"] and t0["content_id"] != t1["content_id"]
assert PS.make("article", "line", "https://x/a", "https://x", llm=lambda m: "พัง", article={"id": 5, "title": "บทความ | เจอ.AI", "body_md": "เนื้อหา"}) is None
art = PS.make("article", "line", "https://x/a", "https://x", llm=good, article={"id": 5, "title": "12 เว็บ | เจอ.AI", "body_md": "12 เว็บ llms 5 www"})
assert art and "อ่านต่อ: https://x/geo/5" in art["text"]
print("make: การ์ดรูป · ข้อความครบโครง · รูปล้มโพสต์ข้อความ · เคล็ดลับหมุน · บทความโมเดลล้มไม่โพสต์ OK")

# ลองถาม AI (ไม่ระบุชื่อลีด)
l0 = db.list_leads(None, 1)[0]
db.update_lead(l0["id"], deep_ai_json=json.dumps({"state": "done", "at": db.now(), "asked": 20, "mentioned": 0, "rows": [{"prompt": "แนะนำบริษัทรับทำบัญชีที่ไหนดี", "sources": ["clutch.co"]}],
                                                  "top_sources": [{"domain": l0["domain"], "n": 2}, {"domain": "clutch.co", "n": 9}, {"domain": "upwork.com", "n": 5}]}))
fa = PS.facts_for("ai_test"); assert fa and l0["domain"] not in " ".join(fa["lines"]) and "clutch.co" in fa["lines"][1] and fa["big"] == "0/20"
print("ai_test: คำถามจริง · แหล่งที่ AI อ้าง · ไม่ระบุชื่อลีด OK")

# เผยแพร่: Facebook = โพสต์รูป · LINE = รูป + ข้อความ
db.set_setting("fb_page_id", "123"); MK.fb_config = lambda: {"page_id": "123", "token": "T"}
calls = []
class R:
    def __init__(s, body): s.status_code = 200; s.content = b"x"; s._b = body
    def json(s): return s._b
MK.httpx.post = lambda url, data=None, timeout=20: (calls.append((url, data)) or R({"id": "p1", "post_id": "123_p1"}))
pid = db.add_post("facebook", "insight", 202643, post["title"], post["text"], post["link"]); db.update_post(pid, image=post["image"])
assert "Facebook" in MK.publish_post(db.get_post(pid)) and calls[-1][0].endswith("/123/photos") and calls[-1][1]["url"] == post["image"] and db.get_post(pid)["external_id"] == "123_p1"
sent = []; LO._post = lambda path, payload: (sent.append((path, payload)) or True)
pid2 = db.add_post("line", "insight", 202643, post["title"], post["text"], post["link"]); db.update_post(pid2, image=post["image"])
MK.publish_post(db.get_post(pid2)); msgs = sent[-1][1]["messages"]
assert sent[-1][0] == "/message/broadcast" and msgs[0]["type"] == "image" and msgs[0]["originalContentUrl"] == post["image"] and msgs[1]["type"] == "text"
print("publish: Facebook โพสต์รูป · LINE รูป+ข้อความ OK")

# เคสศึกษา: เลขที่แย่ลงไม่โชว์ · ดีขึ้นโชว์เป็น %
b = {"name": "x", "market": "โกดัง"}
down = MK.case_study_text(b, {"days": 100, "published": 30, "sov_first": 0.0476, "sov_last": 0.0263, "health_ok": 14, "health_fail": 0, "market": "โกดัง"}, named=False, llm=lambda m: "x")
assert "0.04" not in down["body"] and "การมองเห็น" not in down["body"]
up = MK.case_study_text(b, {"days": 100, "published": 30, "sov_first": 0.05, "sov_last": 0.25, "health_ok": 14, "health_fail": 0, "market": "โกดัง"}, named=False, llm=lambda m: "x")
assert "จาก 5% เป็น 25%" in up["body"]
print("case: เลขที่แย่ลงไม่โชว์ · ดีขึ้นเป็น % OK")
print("ALL OK")
