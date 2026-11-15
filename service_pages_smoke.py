"""ทดสอบ stage 03: วิธีทำงาน · บริการ · Foundation Sprint · อสังหาฯ อุตสาหกรรม (flag marketing_v2)
ไม่ยิงเครือข่าย · DB ชั่วคราว"""
import os, re, tempfile
os.environ.pop("DATABASE_URL", None)
os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["AI_BASE_URL"] = "http://127.0.0.1:9"
for k in list(os.environ):
    if k.startswith("GEO_FLAG_"):
        os.environ.pop(k)
import bcrypt
from fastapi.testclient import TestClient
import app.db as db
import app.flags as F
import app.billing as B
import app.service_pages as SP
from app.main import app

db.init_db()
pw = bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode()
db.create_tenant("admin@sp.test", pw, "Admin", is_admin=True)
adm = TestClient(app)
assert adm.post("/login", data={"email": "admin@sp.test", "password": "pw123456"}).status_code == 200
adm.cookies.set(F.PREVIEW_COOKIE, "marketing_v2")
anon = TestClient(app)
ALL = SP.PATHS
SCOPED = ["/services", "/services/foundation-sprint"]
visible = lambda html: re.sub(r"(?s)<(style|script)\b.*?</\1>|<[^>]+>", " ", html)

# ---------- flag ปิด: คนนอก 404 · แอดมิน preview เห็น + noindex + แถบร่าง ----------
for p in ALL:
    assert anon.get(p).status_code == 404, p
    r = adm.get(p)
    assert r.status_code == 200 and "noindex" in r.headers.get("x-robots-tag", "") and 'rel="canonical"' not in r.text, p
    assert '<meta name="description"' in r.text and r.text.count("<h1") == 1, p
for p in SCOPED:
    assert "ร่าง รอเจ้าของยืนยัน" in adm.get(p).text, p

# ---------- เนื้อหา: ตามข้อกำหนด stage 03 ----------
fs = adm.get("/services/foundation-sprint").text
for s in ("สิ่งที่ส่งมอบ", "ไม่รวมในงานนี้", "สิ่งที่คุณต้องเตรียม", "ใครทำอะไร", "การตรวจรับ", "ประเมินตามขอบเขต"):
    assert s in fs, s
assert "฿" not in visible(fs)                                                       # ไม่ตั้งราคาใหม่ให้งานครั้งเดียว
sv = adm.get("/services").text
for k in ("pro", "agency"):
    assert "฿{:,}".format(B.PLANS[k]["price"]) in sv, k                            # ราคาแผนรายเดือนมาจาก billing.PLANS เดิม
assert "ไม่ใช่การที่ทีมเราลงมือแก้เว็บให้" in sv                                      # ไม่แปลงแผนโควตาเป็นบริการ
how = adm.get("/how-it-works").text
assert "ยังไม่ได้ทดสอบ ≠ ไม่พบ" in how and "ตัวหาร" in how and "ข้อจำกัด" in how
ind = adm.get("/solutions/industrial-property").text
assert "ไม่ใช่ข้อมูลปริมาณการค้น" in ind and "ตัวอย่างสาธิต ไม่ใช่ข้อมูลทรัพย์จริง" in ind and "การรับน้ำหนักพื้น" in ind
for name, html in (("fs", fs), ("sv", sv), ("how", how), ("ind", ind)):
    v = visible(html)
    for banned in ("100%", "อันดับ 1", "ลูกค้ากว่า", "เพิ่มยอดขาย", "จนกว่า AI"):
        assert banned not in v, (name, banned)
    assert not re.search(r"(?<!ไม่)(?<!ไม่มีการ)(การันตี|รับประกันผล)", v), name
    # ลิงก์ภายในไปหน้าที่มีอยู่จริง
    for href in {h for h in re.findall(r'<a [^>]*href="(/[^"#]*)"', html)}:
        assert "lang=" not in href or href == "/?lang=th", (name, href)               # ไม่มีลิงก์ไปภาษาที่หน้านี้ไม่มี
        cl = adm if href in ALL + ["/callback"] else anon                              # หน้าเดิม (เช่น /login) เช็กด้วยคนนอก ไม่ให้แอดมินหลุดล็อกอิน
        assert cl.get(href, follow_redirects=False).status_code in (200, 302, 303, 307), (name, href)
assert 'href="/services"' in adm.get("/").text                                       # เมนูหน้าแรก (preview) มีบริการ

# ---------- เปิด flag จริง: หน้าทั่วไปเปิด · หน้าบริการยัง 404 จนกว่ายืนยันขอบเขต ----------
F.set_enabled("marketing_v2", True)
fresh = TestClient(app)
for p in ("/how-it-works", "/solutions/industrial-property"):
    r = fresh.get(p)
    assert r.status_code == 200 and "x-robots-tag" not in r.headers, p
    assert f'<link rel="canonical" href="http://testserver{p}">' in r.text and "ร่าง รอเจ้าของยืนยัน" not in r.text, p
    assert 'href="/services' not in r.text, p                                       # ไม่มีลิงก์ไปหน้าที่ยังเปิดไม่ได้
for p in SCOPED:
    assert fresh.get(p).status_code == 404, p
home = fresh.get("/").text
assert 'href="/how-it-works"' in home and 'href="/services"' not in home
from app.main import _self_pages                                                      # /sitemap.xml ต้องมีแบรนด์ของเว็บเอง · เช็กรายการหน้าตรง ๆ
sm_of = lambda: " ".join(p["loc"] for p in _self_pages("https://x"))
sm = sm_of()
assert "/how-it-works" in sm and "/solutions/industrial-property" in sm and "/services" not in sm

SP.SCOPE_STATUS = "confirmed"                                                         # เจ้าของยืนยันขอบเขตแล้ว
for p in SCOPED:
    r = fresh.get(p)
    assert r.status_code == 200 and "ร่าง รอเจ้าของยืนยัน" not in r.text and "x-robots-tag" not in r.headers, p
assert 'href="/services"' in fresh.get("/").text and "/services/foundation-sprint" in sm_of()
SP.SCOPE_STATUS = "draft"
F.set_enabled("marketing_v2", False)
assert TestClient(app).get("/how-it-works").status_code == 404
assert "/how-it-works" not in sm_of()

print("service_pages_smoke OK")
