"""ทดสอบ stage 01: ชิ้นส่วนหน้าเว็บสาธารณะชุดใหม่ + หน้า /preview/ui-kit หลัง flag · ไม่ยิงเครือข่าย · DB ชั่วคราว"""
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
from app.main import app, templates

db.init_db()
pw = bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode()
db.create_tenant("admin@mk.test", pw, "Admin", is_admin=True)
db.create_tenant("user@mk.test", pw, "User", is_admin=False)

def login(email):
    c = TestClient(app)
    r = c.post("/login", data={"email": email, "password": "pw123456"})
    assert r.status_code == 200, r.status_code
    return c

anon = TestClient(app)
admin = login("admin@mk.test")
user = login("user@mk.test")

# ---- flag ปิด: ไม่มีใครเห็น ยกเว้นแอดมินที่ตั้งคุกกี้ preview ----
assert anon.get("/preview/ui-kit").status_code == 404
assert admin.get("/preview/ui-kit").status_code == 404                       # แอดมินแต่ไม่ได้ขอ preview
anon.cookies.set(F.PREVIEW_COOKIE, "marketing_v2"); user.cookies.set(F.PREVIEW_COOKIE, "marketing_v2")
assert anon.get("/preview/ui-kit").status_code == 404                        # คุกกี้อย่างเดียวไม่พอ
assert user.get("/preview/ui-kit").status_code == 404                        # ลูกค้าที่ล็อกอินก็ไม่เห็น
admin.cookies.set(F.PREVIEW_COOKIE, "marketing_v2")
r = admin.get("/preview/ui-kit")
assert r.status_code == 200, r.status_code
assert "noindex" in r.headers.get("x-robots-tag", "") and '<meta name="robots" content="noindex, nofollow">' in r.text
html = r.text
for s in ("พบแล้ว", "ไม่พบ (ทดสอบแล้ว)", "ได้บางส่วน", "ยังไม่ได้ทดสอบ", "ระบบไม่พร้อม"):
    assert s in html, s
assert "ความครอบคลุมของรายงานนี้: ตรวจแล้ว 2 จาก 4 ส่วน" in html
assert 'class="mk-actbar"' in html and 'href="#main"' in html
assert "ยังไม่มีวิดีโอสาธิตที่อนุมัติ" in html and "<video" not in html                # ไม่มีปุ่มเล่นปลอม
assert 'disabled aria-describedby="b-sample-why"' in html and "stage 07" in html  # ปุ่มกดไม่ได้ต้องบอกเหตุผล
# ทุก <input> มี label ที่ผูก id (ยกเว้น honeypot ที่ซ่อนจาก screen reader)
ids = re.findall(r'<input id="([^"]+)"', html)
assert ids and all(f'<label for="{i}">' in html for i in ids), ids
# ลิงก์ในเมนู/ท้ายหน้าชี้ไป route ที่มีอยู่จริงเท่านั้น
FLAGGED = ("/callback", "/how-it-works", "/services", "/solutions/industrial-property")             # หน้าหลัง flag marketing_v2 (คนนอกเห็นเมื่อเปิด flag)
for href in {h for h in re.findall(r'<a [^>]*href="(/[^"#]*)"', html) if not h.startswith("/preview/")}:
    st = (admin if href in FLAGGED else anon).get(href, follow_redirects=False).status_code
    assert st in (200, 302, 303, 307), (href, st)

# ---- flag เปิดจริง: หน้าภายในยังติด noindex เสมอ และยังต้องเป็นแอดมิน ----
F.set_enabled("marketing_v2", True)
assert anon.get("/preview/ui-kit").status_code == 404
r = admin.get("/preview/ui-kit"); assert r.status_code == 200 and "noindex" in r.headers.get("x-robots-tag", "")
F.set_enabled("marketing_v2", False)

# ---- macro escape ข้อมูลจากภายนอก (หลักฐานจากเว็บที่ crawl มาเป็นข้อมูลไม่น่าเชื่อถือ) ----
t = templates.env.from_string('{% import "mk/_ui.html" as ui %}{{ ui.evidence(x, u, "now", "absent", f) }}{{ ui.status("bogus") }}')
out = t.render(x='<script>alert(1)</script>', u='/a"><img src=x onerror=1>', f="<b>x</b>")
assert "<script>" not in out and "&lt;script&gt;" in out and "onerror=1>" not in out and "&lt;b&gt;" in out
assert "ยังไม่ได้ทดสอบ" in out                                                    # สถานะที่ไม่รู้จัก = ยังไม่ได้ทดสอบ ไม่ใช่ "ไม่พบ"

# ---- stage 02: หน้าแรกชุดใหม่ (preview เท่านั้น) ----
assert anon.get("/preview/home").status_code == 404 and user.get("/preview/home").status_code == 404
r = admin.get("/preview/home?src=line&ref=<bad>")
assert r.status_code == 200 and "noindex" in r.headers.get("x-robots-tag", "")
home = r.text
assert home.count('action="/audit"') == 2 and home.count('name="url"') == 2 and home.count('name="website"') == 2   # hero + CTA ท้ายหน้า, มี honeypot
assert 'name="src" value="line"' in home and 'name="ref" value=""' in home                     # ค่าจาก URL ผ่านตัวกรองเดิม
assert "data-actbar-anchor" in home and re.search(r'<form [^>]*method="post" action="/audit"', home)   # ฟอร์มเป็น HTML ธรรมดา ปิด JS ก็ส่งได้ (JS มีแค่แถบปุ่มล่างจอกับแชตเดิม)
assert "ตัวอย่างสาธิต ไม่ใช่ผลตรวจเว็บไซต์ของคุณ" in home
import app.billing as B
assert "฿{:,}".format(B.PLANS["pro"]["price"]) in home                                            # ราคาจากโค้ด ไม่พิมพ์เอง
visible = re.sub(r"(?s)<style.*?</style>|<[^>]+>", " ", home)                                   # ตรวจเฉพาะข้อความที่คนเห็น
for banned in ("2 นาที", "จนกว่า AI", "ร้านคุณ", "100%", "อันดับ 1"):
    assert banned not in visible, banned
assert not re.search(r"(?<!ไม่)(การันตี|รับประกันผล)", visible)                               # ได้เฉพาะรูปปฏิเสธ "ไม่การันตี"
main_html = home.split('<main id="main">')[1].split("</main>")[0]                                  # เนื้อหาหน้าเรา (แชตเดิมอยู่นอก main)
ids = re.findall(r'<input id="([^"]+)"', main_html)
assert ids and all(f'<label for="{i}">' in main_html for i in ids)
for href in {h for h in re.findall(r'<a [^>]*href="(/[^"#]*)"', home) if not h.startswith("/preview/")}:
    cl = admin if href in FLAGGED else anon
    assert cl.get(href, follow_redirects=False).status_code in (200, 302, 303, 307), href

# ---- stage 02: ต่อหน้าแรก / หลัง flag ----
old_home = anon.get("/").text
assert 'class="mk"' not in old_home                                                             # flag ปิด = หน้าเดิม
r = admin.get("/")                                                                               # แอดมินที่ preview เห็นหน้าใหม่ แต่ noindex
assert 'class="mk"' in r.text and "noindex" in r.headers.get("x-robots-tag", "") and 'rel="canonical"' not in r.text
F.set_enabled("marketing_v2", True)
fresh = TestClient(app)
r = fresh.get("/")
assert r.status_code == 200 and 'class="mk"' in r.text
assert "x-robots-tag" not in r.headers and 'name="robots"' not in r.text                         # เปิดจริง = ให้ Google เก็บได้
assert '<link rel="canonical" href="http://testserver/">' in r.text
assert r.text.count('rel="alternate" hreflang=') == 6 and 'hreflang="en" href="http://testserver/en"' in r.text
assert 'name="src" value="home"' in r.text                                                       # ที่มาลีดเหมือนหน้าเดิม
assert 'href="/en" hreflang="en"' in r.text and 'href="/?lang=th"' in r.text                    # เปลี่ยนภาษาได้เหมือนหน้าเดิม
import app.salesbot as SB
SB.set_enabled("web", True)
assert 'class="mk"' in fresh.get("/").text and ("chat" in fresh.get("/").text.lower())            # แชตเดิมยังอยู่เมื่อเปิดไว้
assert 'class="mk"' not in fresh.get("/en").text                                                 # ภาษาอื่นยังเป็นหน้าเดิม
en = TestClient(app); en.cookies.set("jr_lang", "en")
assert en.get("/", follow_redirects=False).headers.get("location") == "/en"                      # คนที่เลือกอังกฤษไว้ยังถูกพาไป /en
r = fresh.get("/?lang=th"); assert 'class="mk"' in r.text and r.cookies.get("jr_lang") == "th"
F.set_enabled("marketing_v2", False)
assert 'class="mk"' not in TestClient(app).get("/").text                                         # ปิด flag = กลับหน้าเดิมทันที

# ---- หน้าเดิมไม่เปลี่ยน ----
plain = TestClient(app)                                                                          # ไม่มีคุกกี้ preview
for path in ("/", "/audit", "/partners", "/login"):
    r = plain.get(path); assert r.status_code == 200 and 'class="mk"' not in r.text, path

print("mk_smoke OK")
