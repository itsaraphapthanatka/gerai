"""ทดสอบการแปลหน้าแอปหลังล็อกอิน: โครงหน้า (base.html) · /app · /brands/<id> · คำถาม · มอนิเตอร์ · คอนเทนต์ · WordPress · แจ้งเตือน · ถามข้อมูล
ทุกภาษา (en zh ja ko): เปิดได้ · <html lang> ถูก · ไม่มีไทยในข้อความที่เห็นทั้งหน้า (รวมเมนูข้าง แถบบนมือถือ แถบทดลองใช้) และใน placeholder/aria/title
· ข้อความที่โค้ดเก็บ/ส่งมาเป็นไทย (โควตา สถานะ WordPress เหตุผล gap สถานะ auto แจ้งเตือน) แปลได้ · ภาษาไทยยังแสดงข้อความเดิม
ข้อมูลทดสอบเป็นภาษาอังกฤษทั้งหมด (ชื่อแบรนด์ คำถาม บทความ) เพื่อให้เช็ค "ไม่มีไทย" ได้ตรง ๆ"""
import os, tempfile, re, time, html as H, datetime as dt
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-app-i18n-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["GEO_DEEP_AUDIT"] = "0"; os.environ["GEO_CS"] = "0"
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.i18n as I, app.pipeline as P, app.image_finder as IF
from app.main import app

db.init_db()
I.load(force=True)
P.alert_admins = lambda *a, **k: None
IF._CAP.update(ts=time.time() + 10**6, data={"image_model": "img-test", "generate": False, "models": [], "error": None})   # ไม่ถาม proxy จริง
OTHER = [l for l in I.LANGS if l != "th"]
THAI = re.compile(r"[฀-๿]")
ATTR = re.compile(r'(?:placeholder|aria-label|alt|title|content|data-loading)="([^"]*)"')


def visible(html: str) -> str:
    v = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>", " ", html)
    return re.sub(r"เจอ\.AI|เจอ|฿|ไทย", "", H.unescape(v))                 # "ไทย" = ชื่อภาษาในตัวเลือกภาษา


def no_thai(html: str, where: str):
    left = re.findall(r"\S*[฀-๿]+\S*", visible(html))
    left += [a for a in ATTR.findall(html) if THAI.search(re.sub(r"เจอ\.AI|฿", "", H.unescape(a)))]
    assert not left, (where, left[:8])


def get(c, url, lg, must=(), status=200):
    r = c.get(url + ("&" if "?" in url else "?") + "lang=" + lg)
    assert r.status_code == status, (url, lg, r.status_code)
    assert f'<html lang="{I.HTML_LANG[lg]}">' in r.text, (url, lg)
    if lg != "th":
        no_thai(r.text, f"{url} [{lg}]")
    for s in must:
        assert s in r.text or s in H.unescape(r.text), (url, lg, s)
    return r


def post(c, url, lg, data, must=()):
    c.get("/app?lang=" + lg)                                                # จำภาษาลงคุกกี้ก่อนส่งฟอร์ม
    r = c.post(url, data=data)
    assert r.status_code == 200, (url, lg, r.status_code)
    assert f'<html lang="{I.HTML_LANG[lg]}">' in r.text, (url, lg)
    if lg != "th":
        no_thai(r.text, f"POST {url} [{lg}]")
    for s in must:
        assert s in r.text or s in H.unescape(r.text), (url, lg, s)
    return r


def login(email, name, admin=False):
    if not db.get_tenant_by_email(email):
        db.create_tenant(email, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), name, is_admin=admin)
    c = TestClient(app)
    c.post("/login", data={"email": email, "password": "pw123456"})
    return c, db.get_tenant_by_email(email)


# ---- ลูกค้าแผนฟรี (ช่วงทดลองใช้) + แบรนด์พร้อมข้อมูล ----
c, t = login("owner@example.com", "Test Owner")
bid = db.create_brand(t["id"], "Acme Widgets", "acme.example.com", "Industrial widgets")
q1 = db.add_question(bid, "best widget supplier", "en")
q2 = db.add_question(bid, "widget price comparison", "en")
q3 = db.add_question(bid, "top 5 widget brands", "en", source="auto")
with db.get_conn() as cx:
    now = db.now()
    cx.execute(db.q("INSERT INTO monitoring_runs(brand_id,started_at,status,questions_total) VALUES(?,?,?,?)"), (bid, now, "running", 3))
    rid = cx.execute(db.q("INSERT INTO monitoring_runs(brand_id,started_at,finished_at,status,questions_total,brand_hits,share_of_voice) "
                          "VALUES(?,?,?,?,?,?,?) RETURNING id"), (bid, now, now, "done", 2, 1, 0.5)).fetchone()["id"]
    cx.execute(db.q("INSERT INTO run_results(run_id,question_id,question,brand_present) VALUES(?,?,?,?)"), (rid, q1, "best widget supplier", 1))
    cx.execute(db.q("INSERT INTO run_results(run_id,question_id,question,brand_present) VALUES(?,?,?,?)"), (rid, q2, "widget price comparison", 0))
cid = db.create_content_item(bid, q1, "en", "Choosing a widget supplier", "Widget supplier guide", "How to choose", "Plain body text", "{}", "auto")
db.set_image_note(cid, "ไม่มีรูป — เว็บแบรนด์ไม่มีรูปให้เลือก · ข้ามการสร้างรูป — proxy ไม่มีโมเดลรูป “img-test”")
db.set_auto_content(bid, 3)
db.set_auto_publish(bid, 3)
db.set_auto_image(bid, 1)
db.set_auto_schedule(bid, 7, "08:00")
db.add_health_check(bid, {"checked_at": now, "ok": False, "n_fail": 2, "n_warn": 0, "checks": []})
db.upsert_wp_connection(bid, "https://acme.example.com", "", "", "Connector เชื่อมต่อแล้ว (v1.4.0) · ตั้งค่า schema + llms.txt อัตโนมัติแล้ว", "connector", None)
db.add_chat_message(bid, "user", "How did we do this month?")
db.add_chat_message(bid, "assistant", "Share of Voice is 50% this month.")
for m in ("🔴 Acme Widgets: เข้าเว็บไม่ได้ — ต่อเซิร์ฟเวอร์ไม่ได้ (timeout หรือถูกปฏิเสธ)",
          "🟢 Acme Widgets: เว็บกลับมาเข้าได้แล้ว — ล่มไป 2 วัน",
          "🔧 ซ่อม AEO เสร็จแล้ว — ปรับปรุง 2 ชิ้น · เผยแพร่ 1 ชิ้น",
          "✅ เปิดใช้แผน Pro แล้ว — ขอบคุณที่ใช้บริการ",
          "✅ ยืนยันแพ็กเกจ คอนเทนต์เพิ่ม 20 ชิ้น แล้ว ทีมงานจะดำเนินการให้",
          "ต่อเวลาทดลองใช้ฟรีถึง 10/11/2569 แล้ว",
          "แผนของคุณถูกตั้งเป็น Pro",
          "คอนเทนต์ 3 ชิ้นของ acme.example.com รอคุณตรวจ"):
    db.add_notification(t["id"], m, f"/brands/{bid}", "info")

B = f"/brands/{bid}"
PAGES = ["/app", B, B + "/questions", B + "/monitor", B + "/content", B + "/wp", B + "/chat"]
for lg in OTHER:
    for url in PAGES:
        get(c, url, lg)
    # แจ้งเตือน: เปิดแล้วถูกนับว่าอ่าน — เพิ่มใหม่ทุกภาษาเพื่อให้เห็นป้าย "ใหม่"
    db.add_notification(t["id"], "🔴 Acme Widgets: ยังเข้าเว็บไม่ได้ (3 วันแล้ว) — เซิร์ฟเวอร์ตอบ 502", "/app", "warn")
    get(c, "/notifications", lg)
    get(c, B + "/chat?error=" + "ถามโมเดลไม่สำเร็จ: TimeoutError: slow", lg)
print(f"หน้าแอป {len(PAGES) + 2} หน้า × 4 ภาษา: เปิดได้ · <html lang> ถูก · ไม่มีไทยหลง (รวมเมนู/แถบบน/แถบทดลองใช้) OK")

# ---- ข้อความเฉพาะที่ควรเห็น (อังกฤษ/ญี่ปุ่น) ----
get(c, "/app", "en", must=["Your brands", "Free trial: 14 days left", "Log out", "Notifications", "Add a new brand", "1 questions to write for", "Latest SoV"])
get(c, "/app", "ja", must=["あなたのブランド", "無料トライアル残り 14 日", "ログアウト"])
get(c, B, "en", must=["Latest Share of Voice", "1/2 questions", "Status: Done", "Questions to write for (2)", "Not appearing + no content yet",
                       "Not measured yet — run the monitor", "Connector connected (v1.4.0) · schema + llms.txt set up automatically",
                       "2 issues", "Off — let the system do it weekly", "Install GEO on your site", "Delete brand Acme Widgets and all its data?"])
get(c, B + "/monitor", "en", must=["Monitor history", "Every week", "Running", "Done"])
get(c, B + "/monitor", "zh", must=["监测记录", "每周"])
get(c, B + "/questions", "ko", must=[I.tr("คำถามเป้าหมาย", "ko"), "시스템 추가"])               # คำสั้นที่หลายคลังใช้ร่วมกัน เทียบกับคลังจริง
get(c, B + "/content", "en", must=["Created this week: 1/3", "3 pieces/week", "Auto after 3 days", "No image · The brand's site has no images to choose from",
                                   "Can't create new images right now", "Auto-publishes in", "Draft"])
get(c, B + "/wp", "en", must=["Connected", "Connector connected (v1.4.0) · schema + llms.txt set up automatically", "Disconnect", "Download the latest plugin"])
get(c, B + "/wp", "ja", must=["接続済み", "接続を解除"])
get(c, "/notifications", "en", must=["🔴 Acme Widgets: the site can't be reached — Can't connect to the server (timeout or refused)",
                                     "🟢 Acme Widgets: the site is reachable again — it was down for 2 days",
                                     "🔧 AEO fix done — 2 pieces improved · 1 published", "✅ Pro plan activated",
                                     "✅ 20 extra content pieces confirmed", "Your plan has been set to Pro",
                                     "3 content pieces for acme.example.com are waiting for your review"])
get(c, B + "/chat?error=" + "ถามโมเดลไม่สำเร็จ: TimeoutError: slow", "en", must=["Couldn't get an answer from the model: TimeoutError: slow", "Did the brand get better or worse this month, and why?"])
print("ข้อความจากโค้ด (gap · สถานะ WordPress · สถานะ auto · เหตุผลไม่มีรูป · แจ้งเตือน · error แชต) แปลได้ OK")

# ---- ข้อความแจ้งจากฟอร์ม ----
for lg, s in (("en", "Over the Free plan quota — Brands 1/1. Upgrade your plan to get more."), ("ko", "Free 플랜 한도를 초과했습니다 — 브랜드 수 1/1"),
              ("th", "เกินโควตาแผน Free — จำนวนแบรนด์ 1/1")):
    post(c, "/brands", lg, {"name": "Second Brand", "domain": "second.example.com", "market": "x"}, must=[s])
for lg, s in (("en", "Enter either a Connector API Key or (username + Application Password)"), ("zh", "请填写 Connector API Key"),
              ("th", "กรอก Connector API Key หรือ (ชื่อผู้ใช้ + Application Password) อย่างใดอย่างหนึ่ง")):
    post(c, B + "/wp", lg, {"site_url": "https://acme.example.com"}, must=[s])
print("POST: เกินโควตา · ฟอร์ม WordPress ไม่ครบ — แปลตามภาษา OK")

# ---- ทดลองใช้หมดอายุ ----
c2, t2 = login("expired@example.com", "Late Owner")
db.set_trial_until(t2["id"], (dt.date.today() - dt.timedelta(days=3)).isoformat())
for lg in OTHER:
    get(c2, "/app", lg)
get(c2, "/app", "en", must=["Your 14-day free trial has ended", "monitoring and content creation have stopped"])
post(c2, "/brands", "en", {"name": "New Brand", "domain": "new.example.com", "market": "x"}, must=["Your 14-day free trial has ended (on"])
post(c2, "/brands", "th", {"name": "New Brand", "domain": "new.example.com", "market": "x"}, must=["ช่วงทดลองใช้ฟรี 14 วันหมดแล้ว (ถึง"])
print("ทดลองใช้หมดอายุ: แถบเตือน + ข้อความโควตา แปลได้ OK")

# ---- ผู้ดูแลระบบ: เมนูแอดมินในแถบข้าง ----
c3, t3 = login("admin@example.com", "Site Admin", admin=True)
for lg in OTHER:
    get(c3, "/app", lg)
    get(c3, "/notifications", lg)
get(c3, "/app", "en", must=["Unlimited", "Leads", "Decisions", "Agent skills", "Brands 0 · Runs 0 · Content 0"])
print("แอดมิน: เมนูผู้ดูแล + ไม่จำกัด แปลได้ OK")

# ---- ภาษาไทยคงเดิม ----
get(c, "/app", "th", must=["แบรนด์ของคุณ", "ทดลองใช้ฟรี เหลือ 14 วัน", "ออกจากระบบ", "SoV ล่าสุด", "1 คำถามควรเขียน"])
get(c, B, "th", must=["Share of Voice ล่าสุด", "1/2 คำถาม", "สถานะ: done", "ยังไม่โผล่ + ยังไม่มีคอนเทนต์", "2 ปัญหา",
                      "Connector เชื่อมต่อแล้ว (v1.4.0) · ตั้งค่า schema + llms.txt อัตโนมัติแล้ว", "รันมอนิเตอร์"])
get(c, B + "/content", "th", must=["สัปดาห์นี้สร้างแล้ว 1/3 ชิ้น", "3 ชิ้น/สัปดาห์", "auto หลัง 3 วัน", "เว็บแบรนด์ไม่มีรูปให้เลือก", "ไม่มีรูป"])
get(c, B + "/monitor", "th", must=["ประวัติมอนิเตอร์", "ทุกสัปดาห์", "กำลังรัน"])
get(c, B + "/questions", "th", must=["คำถามเป้าหมาย", "🤖 ระบบเพิ่ม", "ไทย"])
get(c, B + "/wp", "th", must=["✓ เชื่อมต่อแล้ว", "ยกเลิกการเชื่อมต่อ"])
r = get(c, B + "/chat", "th", must=["ถามข้อมูลแบรนด์", "ล้างประวัติ", "เดือนนี้แบรนด์ดีขึ้นหรือแย่ลง เพราะอะไร"])
assert ">ถาม</button>" in r.text, "ปุ่มถามภาษาไทยต้องเป็นคำเดิม"
db.add_notification(t["id"], "🔴 Acme Widgets: เข้าเว็บไม่ได้ — เซิร์ฟเวอร์ตอบ 500", "/app", "warn")
get(c, "/notifications", "th", must=["🔴 Acme Widgets: เข้าเว็บไม่ได้ — เซิร์ฟเวอร์ตอบ 500", "ใหม่"])
print("ภาษาไทย: ข้อความเดิมครบ OK")
print("ALL OK")
