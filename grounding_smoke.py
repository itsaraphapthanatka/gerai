"""ทดสอบบริบทเว็บสำหรับเขียนคอนเทนต์: หน้าแรก + หน้าเกี่ยวกับเรา/FAQ (โดเมนเดียวกัน) · ไม่เก็บหน้า placeholder ของเซิร์ฟเวอร์
· บริบทเก่าที่เป็น placeholder/บางเกินถูกดึงใหม่ตอนเขียน · ไม่ยิงเครือข่าย"""
import os, tempfile
os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-ground-"), "smoke.db")
import app.db as db, app.ai_client as ai, app.main as M

pages = {
    "https://shop.co.th": ("https://shop.co.th/th", '<title>ร้านโกดัง</title><nav>เมนู</nav><h1>หาโกดัง</h1>'
                           '<a href="/th/about">เกี่ยวกับเรา</a> <a href="/th/faq">คำถามพบบ่อย</a> <a href="/th/property/1">ทรัพย์ 1</a>'
                           '<a href="https://other.com/about">about</a> <a href="/th/about">ซ้ำ</a>'),
    "https://shop.co.th/th/about": ("https://shop.co.th/th/about", "<title>เกี่ยวกับเรา</title><p>ก่อตั้งปี 2015 ใบอนุญาตนายหน้า</p>"),
    "https://shop.co.th/th/faq": ("https://shop.co.th/th/faq", "<title>FAQ</title><p>ค่าคอมมิชชันจ่ายโดยเจ้าของทรัพย์</p>"),
}
calls = []
def fetch(u):
    calls.append(u); return pages[u]
t = ai.site_context("shop.co.th", fetch=fetch)
assert "หาโกดัง" in t and "[/th/about]" in t and "ก่อตั้งปี 2015" in t and "[/th/faq]" in t and "ค่าคอมมิชชัน" in t, t
assert "https://other.com/about" not in calls and "https://shop.co.th/th/property/1" not in calls and calls.count("https://shop.co.th/th/about") == 1
assert "เมนู" not in t                                            # ตัด nav ทิ้ง
ph = ("https://x.co.th", "<title>Welcome to https://x.co.th!</title><h1>Success! The https://x.co.th server block is working</h1>")
assert ai.site_context("x.co.th", fetch=lambda u: ph) == ""
assert ai.is_placeholder("Welcome to nginx!") and ai.is_placeholder("Apache2 Ubuntu Default Page") and not ai.is_placeholder("โกดังให้เช่า")
assert ai.context_stale("") and ai.context_stale("Title: x") and ai.context_stale("Title: Welcome ... server block is working By Appreviews!" + "x" * 500)
assert not ai.context_stale("Title: ร้าน\nContent: " + "ก" * 500)
print("site_context: หน้าแรก + เกี่ยวกับเรา/FAQ บนโดเมนเดียวกัน ไม่ซ้ำ · ตัด nav · placeholder = ว่าง OK")

db.init_db()
bid = db.create_brand(db.create_tenant("g@test.local", "x", "G"), "Shop", "shop.co.th")
db.set_brand_site_context(bid, "Title: Welcome to https://shop.co.th!\nHeadings: Success! The https://shop.co.th server block is working")
_orig = ai.site_context
M.ai_client.site_context = lambda url, **kw: _orig(url, fetch=fetch)
b = M._brand_grounded(db.get_brand(bid))
assert "ก่อตั้งปี 2015" in b["site_context"] and not ai.is_placeholder(b["site_context"])
good = b["site_context"]
M.ai_client.site_context = lambda url, **kw: (_ for _ in ()).throw(AssertionError("ไม่ควรดึงซ้ำเมื่อบริบทดีอยู่แล้ว"))
assert M._brand_grounded(db.get_brand(bid))["site_context"] == good
db.set_brand_site_context(bid, "Title: Welcome to nginx!")
M.ai_client.site_context = lambda url, **kw: ""                  # เว็บยังเป็น placeholder → ล้างทิ้ง ไม่ใช้
assert M._brand_grounded(db.get_brand(bid))["site_context"] == ""
print("grounding: placeholder เดิมถูกแทนด้วยเว็บจริง · บริบทดีไม่ดึงซ้ำ · ยังเป็น placeholder = ล้างทิ้ง OK")
print("GROUNDING SMOKE OK")
