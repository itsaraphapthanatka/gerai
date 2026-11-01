"""ธีมหน้าบทความใต้โดเมนลูกค้า: ค่าที่ไม่ผ่านตัวกรองไม่หลุดเข้า <style> · แบรนด์ที่ไม่ตั้งธีมหน้าตาเหมือนเดิม
· ฟอร์มตั้งค่าเก็บเฉพาะค่าที่ต่างจากค่าเริ่มต้น · ล้างธีมได้ · markdown แปลงตาราง/กรอบสรุป/รายการนับเลขเป็น HTML จริง"""
import os, tempfile, json, re
os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-theme-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.hosted_theme as T, app.wp_client as wp
from app.main import app

# ---- markdown: ของที่ AI เขียนมาทุกบทความ ----
h = wp.md_to_html("> **สรุป:** ก\n\n## หัว\n| ก | ข |\n| :--- | ---: |\n| **1** | 2 |\n\n1. หนึ่ง\n2. สอง\n\n---\nมี `code`")
assert "<blockquote><p><strong>สรุป:</strong> ก</p></blockquote>" in h, h
assert '<div class="tbl"><table><thead><tr><th>ก</th><th>ข</th></tr></thead>' in h, h
assert "<td><strong>1</strong></td><td>2</td>" in h and "<ol><li>หนึ่ง</li><li>สอง</li></ol>" in h, h
assert "<hr>" in h and "<code>code</code>" in h, h
assert "|" not in h, "ยังมีขีดคั่นของตารางหลงเป็นข้อความ"
# ประโยคธรรมดาที่มีขีดคั่นต้องไม่ถูกนับเป็นตาราง · ของเดิมที่เคยแปลงได้ต้องเหมือนเดิม
assert wp.md_to_html("ราคา 10 | 20 | 30 บาท") == "<p>ราคา 10 | 20 | 30 บาท</p>"
old = wp.md_to_html("# Head\n\n## Sub\n\n- a\n- b\n\npara line")
assert "<h2>Head</h2>" in old and "<ul><li>a</li>" in old and "<p>para line</p>" in old
print("markdown: ตาราง · กรอบสรุป · รายการนับเลข · เส้นคั่น · โค้ด · ของเดิมไม่เปลี่ยน OK")

db.init_db()
c = TestClient(app)
email = "theme@test.com"
tid = db.create_tenant(email, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Op", is_admin=True)
bid = db.create_brand(tid, "ลูกค้าทดสอบ", "shop.example", "ทดสอบ")
qid = db.add_question(bid, "q", "th")
cid = db.create_content_item(bid, qid, "th", "เรื่องหนึ่ง", "เรื่องหนึ่ง", "ย่อ", "เนื้อหา", "[]", "t")
db.mark_content_published(cid, None, "")
key = db.get_brand(bid)["embed_key"]

# ---- ไม่ตั้งธีม = ชุดของเจอ.AI ทุกค่า ----
page = c.get(f"/e/{key}/a/").text
assert "--accent:#7132f5;" in page and "fonts.googleapis.com" not in page
print("ไม่ตั้งธีม: สีม่วงของเจอ.AI · ไม่โหลดฟอนต์ภายนอก OK")

# ---- ค่าที่ไม่ผ่านตัวกรองต้องตกกลับเป็นค่าเริ่มต้น ไม่หลุดเข้า <style> ----
db.set_brand_theme(bid, json.dumps({
    "accent": "#0D6C3B",                       # ผ่าน
    "page": "red;}body{display:none",          # ไม่ผ่าน — ปิด declaration หนี
    "logo": "javascript:alert(1)",             # ไม่ผ่าน — ไม่ใช่ path/https
    "font_url": "https://evil.example/x.css",  # ไม่ผ่าน — ไม่ใช่ Google Fonts
    "radius": "20px", "ลืมปิด": "x",           # คีย์ที่ไม่รู้จักถูกทิ้ง
}, ensure_ascii=False))
th = T.theme_for(db.get_brand(bid))
assert th["accent"] == "#0D6C3B" and th["radius"] == "20px"
assert th["page"] == T.DEFAULT["page"] and th["logo"] == "" and th["font_url"] == ""
page = c.get(f"/e/{key}/a/").text
for bad in ("}body{", "javascript:", "evil.example"):
    assert bad not in page, bad
print("ตัวกรอง: ค่าดีเข้า ค่าเสียตกกลับ default · ไม่มีอะไรหลุดเข้าหน้าเว็บ OK")

# ---- ฟอร์มตั้งค่า ----
c.post("/login", data={"email": email, "password": "pw123456"})
r = c.get(f"/brands/{bid}/theme")
assert r.status_code == 200 and 'name="accent"' in r.text and 'id="prev"' in r.text
assert db.get_brand(bid)["name"] in r.text
print("หน้าตั้งค่า: เปิดได้ · มีช่องสี · มีกล่องตัวอย่าง OK")

form = {k: T.DEFAULT[k] for k in T.FORM_KEYS}
form.update({"font_preset": "noto", "accent": "#034956", "cta": "#2DFB91", "cta_ink": "#022310",
             "foot": "#000000", "foot_text": "#8E8B84", "foot_head": "#FFFFFF",
             "foot_round": "34", "radius": "18", "logo_h": "40",
             "logo": "/assets/a.png", "logo_foot": "/assets/b.png",
             "cta_url": "/contact", "cta_label": "ติดต่อเรา", "cta_title": "สนใจสอบถาม"})
c.post(f"/brands/{bid}/theme", data=form)
saved = json.loads(db.get_brand(bid)["theme"])
assert "surface" not in saved and "text" not in saved, "เก็บค่าที่เท่ากับ default มาด้วย"
assert saved["accent"] == "#034956" and saved["radius"] == "18px" and saved["foot_round"] == "34px"
assert saved["font"].startswith("'Noto Sans Thai'") and "Noto+Sans+Thai" in saved["font_url"]
assert saved["foot_border"] == "#00000000", "ฟุตเตอร์คนละสีกับพื้นหน้า ต้องไม่มีเส้นคั่น"
print(f"บันทึก: เก็บเฉพาะค่าที่ต่างจาก default ({len(saved)} คีย์) · หน่วย px · เส้นคั่นท้ายหน้าคิดเอง OK")

art = c.get(f"/e/{key}/a/{cid}").text
assert "--accent:#034956;" in art and "--cta:#2DFB91;" in art and "Noto+Sans+Thai" in art
assert 'src="/assets/a.png"' in art and 'src="/assets/b.png"' in art
assert "สนใจสอบถาม" in art and 'href="/contact"' in art and "BreadcrumbList" in art
print("หน้าบทความ: ใช้สี ฟอนต์ โลโก้ แถบชวนติดต่อ ของแบรนด์ · มี breadcrumb OK")

# ฟุตเตอร์สีเดียวกับพื้นหน้า = ต้องมีเส้นคั่น
c.post(f"/brands/{bid}/theme", data={**form, "foot": T.DEFAULT["page"], "border": "#E7E3DC"})
assert json.loads(db.get_brand(bid)["theme"])["foot_border"] == "#E7E3DC"

c.post(f"/brands/{bid}/theme", data={"reset": "1"})
assert db.get_brand(bid)["theme"] is None
assert "--accent:#7132f5;" in c.get(f"/e/{key}/a/").text
print("ล้างธีม: กลับไปใช้ชุดของเจอ.AI OK")

print("ALL OK")
