"""ไฟล์สำหรับบอทบนโดเมนของเจอ.AI เอง: robots.txt ไม่มีคำแนะนำของลูกค้าและกันหลังบ้าน/ลิงก์ลับ · sitemap มีหน้าแรก 5 ภาษา (hreflang) + /audit /partners /geo + บทความ
· /sitemap.xml ใช้ได้ · llms.txt สรุปแบบเอเจนซี่จากระบบ (ราคา Pro ไม่ hardcode) + หน้าหลัก · หน้าแรกลิงก์ไปบทความ (ไม่เป็นหน้ากำพร้า)
· robots ที่แนะนำลูกค้ายังเหมือนเดิม"""
import os, tempfile
os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-self-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.billing as B, app.geo_content as G
from app.main import app

db.init_db()
tid = db.create_tenant("admin@example.com", bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(), "Admin", is_admin=True)
bid = db.create_brand(tid, "เจอ.AI", "https://ours.example", "แพลตฟอร์มเก่า แพ็กเกจ Pro")
db.set_setting("self_brand_id", str(bid))
qid = db.add_question(bid, "how to get recommended by AI", "th")
cid = db.create_content_item(bid, qid, "th", "บทความทดสอบหนึ่ง", "บทความทดสอบหนึ่ง", "desc", "body", "[]", "template")
db.mark_content_published(cid, None, f"https://ours.example/geo/{cid}")
c = TestClient(app)

r = c.get("/robots.txt").text
assert "Yoast" not in r and "User-agent: *" in r and "Disallow: /admin" in r and "Disallow: /audit/" in r and "Disallow: /app$" in r
assert "Disallow: /audit\n" not in r and "Disallow: /partners" not in r and "Sitemap: https://ours.example/geo-sitemap.xml" in r
gp = r.split("User-agent: GPTBot")[1].split("User-agent:")[0]; assert "Allow: /" in gp and "Disallow: /admin" in gp
cust = G.robots_snippet(db.get_brand(bid))
assert "Yoast" in cust and "Disallow" not in cust                       # ที่แนะนำลูกค้าเหมือนเดิม
print("robots: ไม่มีคำแนะนำลูกค้า · กันหลังบ้าน/ลิงก์ลับทุกกลุ่มบอท · /audit กับ /partners ยังเปิด · ของลูกค้าเหมือนเดิม OK")

for path in ("/geo-sitemap.xml", "/sitemap.xml"):
    x = c.get(path); assert x.status_code == 200 and "xml" in x.headers["content-type"], path
    t = x.text
    for p in ("/", "/en", "/zh", "/ja", "/ko", "/audit", "/partners", "/geo", f"/geo/{cid}"):
        assert f"<loc>https://ours.example{p}</loc>" in t, (path, p)
    assert 'hreflang="ja" href="https://ours.example/ja"' in t and 'hreflang="x-default"' in t and "xmlns:xhtml" in t
print("sitemap: /geo-sitemap.xml และ /sitemap.xml · หน้าแรก 5 ภาษา + hreflang · ตรวจเว็บ · พันธมิตร · บทความ OK")

l = c.get("/llms.txt").text
assert "เอเจนซี่ GEO/AEO" in l and f"{B.PLANS['pro']['price']:,} บาท/เดือน" in l and "แพลตฟอร์มเก่า" not in l
assert "## หน้าหลัก / Key pages" in l and "https://ours.example/audit" in l and "บทความทดสอบหนึ่ง" in l
B.PLANS["pro"]["price"] = 12345
assert "12,345 บาท/เดือน" in c.get("/llms.txt").text
print("llms: สรุปแบบเอเจนซี่จากระบบ (ราคาตาม Pro) · หน้าหลัก · บทความ OK")

h = c.get("/?lang=th").text
assert 'href="/geo"' in h and f'href="/geo/{cid}"' in h and "บทความล่าสุดจากทีมเจอ.AI" in h
e = TestClient(app).get("/en").text; assert 'href="/geo"' in e and "Articles (Thai)" in e and f'href="/geo/{cid}"' not in e
print("หน้าแรก: ลิงก์ไปหน้ารวมบทความ + บทความล่าสุด (ไทย) · ภาษาอื่นมีลิงก์ 'Articles (Thai)' OK")
# ---- ลิงก์ภายในบทความ · หน้ารวม · IndexNow ----
import app.indexnow as IX
cid2 = db.create_content_item(bid, qid, "th", "บทความทดสอบสอง", "บทความทดสอบสอง", "desc2", "body", "[]", "template")
db.mark_content_published(cid2, None, f"https://ours.example/geo/{cid2}")
a1 = c.get(f"/geo/{cid}").text
assert f'href="https://ours.example/geo/{cid2}"' in a1 and 'href="https://ours.example/geo"' in a1 and "บทความอื่น" in a1 and "/audit?src=geo" in a1
ix = c.get("/geo").text
assert '<link rel="canonical" href="https://ours.example/geo">' in ix and '"@type": "ItemList"' in ix and "desc2" in ix
print("บทความ: ลิงก์บทความอื่น + หน้ารวม + ชวนตรวจเว็บ (โดเมนเรา) · หน้ารวมมี canonical + ItemList OK")

k = c.get("/geo/indexnow.txt").text.strip(); assert len(k) == 32 and k == IX.key()
emb = db.get_brand(bid)["embed_key"]; assert c.get(f"/e/{emb}/a/indexnow.txt").text.strip() == k
assert not IX.enabled() and IX.submit("https://ours.example", [f"https://ours.example/geo/{cid}"])["sent"] == 0   # เทสต์ไม่มีแถว = ปิด
db.set_setting("indexnow_on", "1")
got = []
class R: status_code = 202
res = IX.submit("https://ours.example", [f"https://ours.example/geo/{cid}", "https://ours.example/blog/x", f"https://ours.example/geo/{cid}"],
                post=lambda u, j: got.append((u, j)) or R())
assert res == {"sent": 1, "status": 202, "error": ""} and got[0][0] == IX.ENDPOINT
assert got[0][1] == {"host": "ours.example", "key": k, "keyLocation": "https://ours.example/geo/indexnow.txt", "urlList": [f"https://ours.example/geo/{cid}"]}
db.set_setting("indexnow_on", "0")
print("IndexNow: คีย์ 32 ตัว · ไฟล์ยืนยันใต้ /geo ทั้งโดเมนเราและผ่าน proxy ลูกค้า · ส่งเฉพาะ URL ใต้ /geo ไม่ซ้ำ · ปิดในเทสต์ OK")
print("ALL OK")
