"""ทดสอบโหมดเอเจนซี (ลูกค้า managed): ทีมงานตั้งค่าและลงมือทำให้ ลูกค้าดูผลอย่างเดียว
· โหมดเปิด: ลูกค้าที่จ่ายเงินเปิดทุกหน้าดูผลได้ 200 ครบ 5 ภาษา · ไม่มีปุ่ม/ฟอร์มลงมือทำ · มีป้าย "ทีมดูแลให้" ที่แปลแล้ว
· ทุก route ที่เปลี่ยนข้อมูลถูกปฏิเสธ (303 + ?managed=1) และฐานข้อมูลไม่เปลี่ยน · แชตถามข้อมูล/เปลี่ยนรหัสผ่าน/จ่ายบิลค้างยังทำได้
· แอดมินทำได้ทุกอย่างในแบรนด์ลูกค้า · สวิตช์ที่ /admin · production ตั้งค่าเริ่มต้นเปิดตอน startup (ไม่มีแถว = ปิด)
· โหมดปิด: พฤติกรรมเดิม (ปุ่มอยู่ ทำงานได้)
ข้อมูลทดสอบเป็นภาษาอังกฤษ เพื่อเช็ค "ไม่มีไทย" ได้ตรง ๆ"""
import os, re, json, tempfile, datetime as dt, html as H
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-managed-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["GEO_DEEP_AUDIT"] = "0"; os.environ["GEO_CS"] = "0"
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.i18n as I, app.pipeline as P, app.main as M
from app.main import app

db.init_db()
I.load(force=True)
P.alert_admins = lambda *a, **k: None
LANGS = list(I.LANGS)
OTHER = [l for l in LANGS if l != "th"]
THAI = re.compile(r"[฀-๿]")
NOTE_TH = "ทีมเจอ.AI ดูแลส่วนนี้ให้"


def login(email, name, admin=False):
    if not db.get_tenant_by_email(email):
        db.create_tenant(email, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), name, is_admin=admin)
    c = TestClient(app)
    c.post("/login", data={"email": email, "password": "pw123456"})
    return c, db.get_tenant_by_email(email)


def visible(s: str) -> str:
    v = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>", " ", s)
    return re.sub(r"เจอ\.AI|เจอ|฿|ไทย", "", H.unescape(v))


# ---- ข้อมูล: ลูกค้าจ่ายเงิน (แผน pro) + แบรนด์พร้อมผลทุกตัววัด ----
c, t = login("owner@example.com", "Test Owner")
db.set_plan(t["id"], "pro")
bid = db.create_brand(t["id"], "Acme Widgets", "acme.example.com", "Industrial widgets")
q1 = db.add_question(bid, "best widget supplier", "en")
q2 = db.add_question(bid, "widget price comparison", "en")
now = db.now()
with db.get_conn() as cx:
    rid = cx.execute(db.q("INSERT INTO monitoring_runs(brand_id,started_at,finished_at,status,questions_total,brand_hits,share_of_voice) "
                          "VALUES(?,?,?,?,?,?,?) RETURNING id"), (bid, now, now, "done", 2, 1, 0.5)).fetchone()["id"]
    cx.execute(db.q("INSERT INTO run_results(run_id,question_id,question,brand_present,top_domains) VALUES(?,?,?,?,?)"), (rid, q1, "best widget supplier", 1, json.dumps(["rival.test"])))
    cx.execute(db.q("INSERT INTO run_results(run_id,question_id,question,brand_present,top_domains) VALUES(?,?,?,?,?)"), (rid, q2, "widget price comparison", 0, json.dumps(["rival.test"])))
cid = db.create_content_item(bid, q1, "en", "Choosing a widget supplier", "Widget supplier guide", "How to choose", "> Pick a certified supplier.\n\n- one\n- two", "[]", "template")
cid2 = db.create_content_item(bid, q2, "en", "Widget prices", "Widget prices", "Prices", "Plain body", "[]", "template")
db.mark_content_published(cid2, None, "https://acme.example.com/geo/%d" % cid2)
db.add_health_check(bid, {"checked_at": now, "ok": False, "n_fail": 2, "n_warn": 0, "checks": []})
db.add_ai_visibility(bid, {"checked_at": now, "rows": [], "by_engine": {}, "asked": 3, "cited": 1, "named": 2, "rate": 67, "cost_usd": 0.0})
db.upsert_wp_connection(bid, "https://acme.example.com", "", "", "Connector เชื่อมต่อแล้ว (v1.4.0) · ตั้งค่า schema + llms.txt อัตโนมัติแล้ว", "connector", None)
db.add_chat_message(bid, "user", "How did we do this month?")
db.add_chat_message(bid, "assistant", "Share of Voice is 50% this month.")
db.add_notification(t["id"], "แผนของคุณถูกตั้งเป็น Pro", "/upgrade", "plan")
# ใบแจ้งหนี้ค้างชำระ (บิลรอบต่ออายุ) — ลูกค้าต้องจ่ายได้เสมอ
lid = db.add_lead("https://acme.example.com", "acme.example.com", source="direct", email="owner@example.com")
inv_token = "tok" + "x" * 20
inv = db.add_acct_doc(kind="invoice", number="INV-0001", token=inv_token, lead_id=lid, tenant_id=t["id"], subtotal=4900, total=4900, status="open")

B = f"/brands/{bid}"
VIEWS = ["/app", B, B + "/questions", B + "/monitor", B + "/content", f"/content/{cid}", B + "/wp", B + "/ai", B + "/ai-serp",
         B + "/rank", B + "/speed", B + "/health", B + "/gsc", B + "/impact", B + "/progress", B + "/report", f"/runs/{rid}",
         B + "/autopilot", B + "/assets", B + "/chat", "/notifications", "/upgrade", "/account/password"]
# ฟอร์ม/ลิงก์ที่ลูกค้า managed ต้องไม่เห็น (action ของ route ที่เปลี่ยนข้อมูล)
ACTIONS = ['action="/brands"', 'action="/seed-demo"', f'action="{B}/run"', f'action="{B}/delete"', f'action="{B}/questions"',
           f'action="{B}/questions/generate"', 'action="/questions/', f'action="{B}/schedule"', f'action="{B}/auto-content"',
           f'action="{B}/auto-publish"', f'action="{B}/auto-image"', f'action="{B}/facts"', f'action="{B}/content"',
           f'action="{B}/content/fix-all"', f'action="/content/{cid}/regenerate"', f'action="/content/{cid}/publish"',
           f'action="/content/{cid}/delete"', f'action="{B}/wp"', f'action="{B}/wp/delete"', f'action="{B}/wp/sync"',
           f'action="{B}/ai/run"', f'action="{B}/ai-serp/run"', f'action="{B}/ai-serp/auto"', f'action="{B}/rank/run"',
           f'action="{B}/speed/run"', f'action="{B}/health/run"', f'action="{B}/gsc/run"', f'action="{B}/autopilot/run"',
           f'action="{B}/autopilot/settings"', f'action="{B}/schema-type"', 'href="/upgrade/pay/agency"', 'href="/upgrade/pay/content20"',
           'action="/upgrade"']
NOTE_PAGES = ["/app", B, B + "/questions", B + "/monitor", B + "/content", f"/content/{cid}", B + "/wp", B + "/ai", B + "/rank",
              B + "/speed", B + "/health", B + "/gsc", B + "/ai-serp", B + "/autopilot", B + "/assets"]


def snapshot():
    out = {}
    with db.get_conn() as cx:
        for tb in ("brands", "target_questions", "content_items", "monitoring_runs", "wp_connections", "payments", "tenants",
                   "health_checks", "ai_visibility", "rank_results"):
            out[tb] = [tuple(r) for r in cx.execute(f"SELECT * FROM {tb} ORDER BY id").fetchall()]
    return json.dumps(out, default=str, sort_keys=True)


# ---- ค่าเริ่มต้น: ไม่มีแถว setting = ปิด (สเมอก์เทสต์เดิมไม่กระทบ) · startup ของ production ใส่ "1" ครั้งแรก · ค่าที่แอดมินตั้งคงอยู่ ----
assert db.get_setting("agency_mode") is None and not M.agency_mode()
r = c.get(B)
assert f'action="{B}/run"' in r.text and "managed-note" not in r.text, "ไม่มีแถว = โหมดเดิม"
M._startup()
assert db.get_setting("agency_mode") == "1" and M.agency_mode(), "startup ต้องเปิดโหมดเอเจนซีให้ production"
db.set_setting("agency_mode", "0"); M._startup()
assert db.get_setting("agency_mode") == "0", "startup ต้องไม่เขียนทับค่าที่แอดมินตั้งไว้"
print("ค่าเริ่มต้น: ไม่มีแถว = ปิด · startup ตั้ง 1 ครั้งแรก · ไม่ทับค่าที่ตั้งเอง OK")

# ---- สวิตช์ที่ /admin ----
ca, ta = login("admin@example.com", "Site Admin", admin=True)
r = ca.get("/admin"); assert r.status_code == 200 and 'action="/admin/agency-mode"' in r.text and "เปิดโหมดเอเจนซี" in r.text
c.post("/admin/agency-mode", data={"on": "1"}); assert db.get_setting("agency_mode") == "0", "ลูกค้าสลับโหมดเองไม่ได้"
ca.post("/admin/agency-mode", data={"on": "1"}); assert db.get_setting("agency_mode") == "1"
assert "ปิดโหมดเอเจนซี" in ca.get("/admin").text
print("สวิตช์ /admin: แอดมินสลับได้ · ลูกค้าสลับไม่ได้ OK")

# ---- โหมดเปิด: ทุกหน้าดูผลได้ ครบ 5 ภาษา · ไม่มีปุ่มลงมือทำ · ป้ายแปลแล้ว ----
for lg in LANGS:
    for url in VIEWS:
        r = c.get(url + ("&" if "?" in url else "?") + "lang=" + lg)
        assert r.status_code == 200, (url, lg, r.status_code)
        assert f'<html lang="{I.HTML_LANG[lg]}">' in r.text, (url, lg)
        for a in ACTIONS:
            assert a not in r.text, (url, lg, "ยังมีปุ่ม/ฟอร์ม", a)
        if url in NOTE_PAGES:
            assert 'class="managed-note"' in r.text, (url, lg, "ไม่มีป้ายทีมดูแลให้")
        for note in re.findall(r'<div class="managed-note"[\s\S]*?</div>', r.text):
            if lg != "th":
                assert not THAI.search(visible(note)), (url, lg, visible(note))
            else:
                assert NOTE_TH in note
        if lg != "th" and url not in (B + "/report",):
            main = re.search(r"<main>([\s\S]*)</main>", re.sub(r"<pre[\s\S]*?</pre>", "", r.text))   # <pre> = โค้ด/ไฟล์ที่ระบบสร้าง (llms.txt robots) ไม่ใช่ข้อความหน้า
            nav = re.search(r'<aside class="sidebar">([\s\S]*?)</aside>', r.text)
            for part, where in ((main.group(1) if main else "", "main"), (nav.group(1) if nav else "", "sidebar")):
                left = [w for w in re.findall(r"\S*[฀-๿]+\S*", visible(part))]
                assert not left, (url, lg, where, left[:6])
print(f"โหมดเปิด: {len(VIEWS)} หน้า × {len(LANGS)} ภาษา เปิดได้ · ไม่มีปุ่ม/ฟอร์มลงมือทำ · ป้ายทีมดูแลให้แปลครบ OK")

# ข้อความเฉพาะ
r = c.get("/app?lang=th")
assert "รายงานล่าสุด" in r.text and f'href="{B}/report.pdf"' in r.text and "เผยแพร่แล้ว 1/2 ชิ้น" in r.text and "ถูกพูดถึง 67%" in r.text
assert "เพิ่มแบรนด์ใหม่" not in r.text and "ข้อมูลตัวอย่าง" not in r.text
r = c.get("/app?lang=en"); assert "Latest reports" in r.text and "1/2 pieces published" in r.text and "Mentioned 67%" in r.text
r = c.get("/upgrade?lang=th")
assert "แพ็กเกจและการชำระเงิน" in r.text and "ต้องการปรับแพ็กเกจ? คุยกับทีมงาน" in r.text and "มีใบแจ้งหนี้รอชำระ 1 ใบ" in r.text
assert f'href="/bill/{inv_token}"' in r.text and 'href="/upgrade/pay/pro"' in r.text and 'class="plans"' not in r.text and 'class="a-buy"' not in r.text
r = c.get("/upgrade?lang=ja"); assert "プランとお支払い" in r.text and "今すぐ支払う" in r.text
r = c.get(B + "/monitor?lang=en"); assert "Automatic measurement" in r.text and "Every week" in r.text
assert 'href="' + B + '/wp"' not in c.get(B).text, "เมนู/การ์ด WordPress ซ่อนสำหรับลูกค้า managed"
print("ข้อความ: แดชบอร์ดตัวเลขหลัก + รายงานล่าสุด · /upgrade ไม่มีปุ่มซื้อ มีบิลค้าง + คุยกับทีม · เมนูตั้งค่าซ่อน OK")

# ---- ทุก route ที่เปลี่ยนข้อมูลถูกปฏิเสธ และ DB ไม่เปลี่ยน ----
MUT = [("/brands", {"name": "New", "domain": "new.example.com", "market": "x"}), ("/seed-demo", {"industry": "property"}),
       ("/upgrade", {"plan": "agency"}), (B + "/auto-content", {"weekly": "3"}), (B + "/auto-publish", {"days": "0"}),
       (B + "/auto-image", {"on": "1"}), (B + "/schema-type", {"schema_type": "Store"}), (B + "/facts", {"facts": "Founded 2001"}),
       (B + "/schedule", {"auto_run_days": "1", "auto_run_time": "09:00"}), (B + "/delete", {}),
       (B + "/questions", {"question": "new question", "lang": "en"}), (B + "/questions/generate", {}),
       (f"/questions/{q1}/delete", {"brand_id": str(bid)}), (B + "/run", {}), (B + "/health/run", {}), (B + "/ai/run", {}),
       (B + "/gsc/run", {}), (B + "/speed/run", {}), (B + "/ai-serp/run", {}), (B + "/ai-serp/auto", {"on": "1"}),
       (B + "/autopilot/run", {}), (B + "/autopilot/settings", {"on": "1", "mode": "publish", "content_n": "3"}),
       (B + "/rank/run", {}), (B + "/content", {"question_id": str(q2), "lang": "en", "ctype": "qa"}),
       (B + "/content/fix-all", {"publish_drafts": "1"}), (f"/content/{cid}/regenerate", {}), (f"/content/{cid}/delete", {}),
       (f"/content/{cid}/publish", {"status": "publish"}), (B + "/wp", {"site_url": "https://evil.example.com", "api_key": "k"}),
       (B + "/wp/delete", {}), (B + "/wp/sync", {})]
before = snapshot()
for url, data in MUT:
    r = c.post(url, data=data, follow_redirects=False)
    assert r.status_code in (302, 303), (url, r.status_code)
    assert "managed=1" in r.headers["location"], (url, r.headers["location"])
assert snapshot() == before, "route ที่ถูกปฏิเสธต้องไม่เปลี่ยนข้อมูล"
r = c.post("/api/classify-market", json={"url": "acme.example.com"}); assert r.status_code == 403
for item in ("agency", "content20", "setup"):
    r = c.get(f"/upgrade/pay/{item}", follow_redirects=False); assert r.status_code == 303 and "managed=1" in r.headers["location"], item
    r = c.post(f"/upgrade/pay/{item}", files={"slip": ("s.png", b"x", "image/png")}, follow_redirects=False)
    assert r.status_code == 303 and "managed=1" in r.headers["location"], item
assert snapshot() == before
r = c.post(B + "/run?lang=en"); assert r.status_code == 200 and "managed-note" in r.text and "takes care of this" in r.text, "ปฏิเสธแล้วเห็นป้ายบอก"
print(f"ปฏิเสธ {len(MUT) + 7} route ที่เปลี่ยนข้อมูล (303 + ป้าย) · DB ไม่เปลี่ยน OK")

# ---- สิ่งที่ลูกค้า managed ยังทำได้ ----
r = c.post(B + "/chat", data={"message": "How are we doing?"}, follow_redirects=False)
assert r.status_code == 303 and "managed" not in r.headers["location"], "แชตถามข้อมูลต้องใช้ได้"
r = c.post(B + "/chat/clear", follow_redirects=False); assert "managed" not in r.headers["location"]
r = c.post("/account/password", data={"current": "pw123456", "new": "pw7654321", "confirm": "pw7654321"}); assert r.status_code == 200
assert M.verify_pw("pw7654321", db.get_tenant(t["id"])["password_hash"])
r = c.post("/account/password", data={"current": "pw7654321", "new": "pw123456", "confirm": "pw123456"})
r = c.get("/upgrade/pay/pro"); assert r.status_code == 200, "จ่ายค่าแผนปัจจุบัน (ต่ออายุ) ได้"
n0 = len(db.list_pending_payments())
r = c.post("/upgrade/pay/pro", data={"x": "1"}); assert r.status_code == 200 and len(db.list_pending_payments()) == n0 + 1
r = c.post(f"/bill/{inv_token}/pay", data={"website": ""}, follow_redirects=False)                     # ไม่แนบสลิป — ไม่เขียนไฟล์ลง uploads/
assert r.status_code == 303 and db.get_acct_doc_by_token(inv_token)["status"] == "claimed", "จ่ายบิลค้างได้"
print("ยังทำได้: แชตถามข้อมูล · ล้างแชต · เปลี่ยนรหัสผ่าน · จ่ายค่าแผนปัจจุบัน · จ่ายบิลค้าง OK")

# ---- แอดมินทำได้ทุกอย่างในแบรนด์ลูกค้า (โหมดเปิดอยู่) ----
r = ca.get(B); assert r.status_code == 200 and f'action="{B}/run"' in r.text and "managed-note" not in r.text
for url in (B + "/content", f"/content/{cid}", B + "/wp", B + "/autopilot", B + "/questions"):
    assert "managed-note" not in ca.get(url).text, url
ADMIN_OK = [(B + "/schedule", {"auto_run_days": "1", "auto_run_time": "09:00"}), (B + "/auto-content", {"weekly": "3"}),
            (B + "/questions", {"question": "admin question", "lang": "en"}), (B + "/facts", {"facts": "Founded 2001"}),
            (B + "/schema-type", {"schema_type": "Store"}), (B + "/autopilot/settings", {"on": "1", "mode": "draft", "content_n": "1"}),
            (B + "/ai-serp/auto", {"on": "1"}), (B + "/wp/delete", {}), (f"/content/{cid}/publish", {"status": "publish"})]
for url, data in ADMIN_OK:
    r = ca.post(url, data=data, follow_redirects=False)
    assert r.status_code in (200, 302, 303) and "managed" not in r.headers.get("location", ""), (url, r.status_code)
b = db.get_brand(bid)
assert b["auto_run_days"] == 1 and b["auto_content_weekly"] == 3 and b["facts"] == "Founded 2001" and b["schema_type"] == "Store" and b["autopilot"] and b["aiserp_auto"]
assert any(q["question"] == "admin question" for q in db.list_questions(bid)) and not db.get_wp_connection(bid)
assert db.get_content(cid)["status"] == "published"
r = ca.post(f"/content/{cid2}/delete", follow_redirects=False); assert not db.get_content(cid2)
r = ca.post(B + "/delete", follow_redirects=False); assert r.status_code == 303 and not db.get_brand(bid)
print("แอดมิน: เห็นปุ่มครบ · ตั้งค่า/แก้คอนเทนต์/เผยแพร่/ลบในแบรนด์ลูกค้าได้ OK")

# ---- โหมดปิด: พฤติกรรมเดิม ----
ca.post("/admin/agency-mode", data={"on": ""}); assert db.get_setting("agency_mode") == "0"
bid2 = db.create_brand(t["id"], "Beta Gadgets", "beta.example.com", "Gadgets")
B2 = f"/brands/{bid2}"
cid3 = db.create_content_item(bid2, None, "en", "Gadget guide", "Gadget guide", "Guide", "Body", "[]", "template")
r = c.get(B2); assert f'action="{B2}/run"' in r.text and f'action="{B2}/delete"' in r.text and "managed-note" not in r.text
r = c.get("/app"); assert 'action="/brands"' in r.text and 'action="/seed-demo"' in r.text and "managed-note" not in r.text
r = c.get("/upgrade?lang=th"); assert 'class="plans"' in r.text and "อัปเกรดแผน" in r.text
assert f'action="/content/{cid3}/publish"' in c.get(f"/content/{cid3}").text
assert f'action="{B2}/questions"' in c.get(B2 + "/questions").text and f'action="{B2}/schedule"' in c.get(B2 + "/monitor").text
r = c.post(B2 + "/questions", data={"question": "gadget question", "lang": "en"}, follow_redirects=False)
assert r.status_code == 303 and "managed" not in r.headers["location"] and any(q["question"] == "gadget question" for q in db.list_questions(bid2))
r = c.post(B2 + "/schedule", data={"auto_run_days": "3", "auto_run_time": "07:30"}); assert db.get_brand(bid2)["auto_run_days"] == 3
r = c.post(f"/content/{cid3}/publish", data={"status": "publish"}); assert db.get_content(cid3)["status"] == "published"
r = c.get("/upgrade/pay/agency"); assert r.status_code == 200
r = c.post(B2 + "/delete", follow_redirects=False); assert r.status_code == 303 and not db.get_brand(bid2)
print("โหมดปิด: ปุ่มครบ · ลูกค้าทำเองได้ตามเดิม OK")
print("ALL OK")
