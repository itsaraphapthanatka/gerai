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
for href in set(re.findall(r'<a [^>]*href="(/[^"#]*)"', html)):
    st = anon.get(href, follow_redirects=False).status_code
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

# ---- หน้าเดิมไม่เปลี่ยน ----
for path in ("/", "/audit", "/partners", "/login"):
    r = anon.get(path); assert r.status_code == 200 and 'class="mk"' not in r.text, path

print("mk_smoke OK")
