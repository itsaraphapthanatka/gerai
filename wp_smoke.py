"""ทดสอบ M2: encrypt/decrypt + md->html + บันทึก WP connection + publish (mock)."""
import os
os.environ["LITELLM_BASE_URL"] = ""  # template path (deterministic)
import re
import bcrypt
from fastapi.testclient import TestClient
import app.db as db
import app.wp_client as wp
from app.main import app

# 1) crypto roundtrip
sec = "xxxx yyyy zzzz 1234 5678 90ab"
enc = wp.encrypt(sec)
assert enc != sec and wp.decrypt(enc) == sec
print("encrypt/decrypt roundtrip OK")

# 2) markdown -> html
h = wp.md_to_html("# Head\n\n## Sub\n\n- a\n- b\n\npara line")
assert "<h2>Head</h2>" in h and "<ul><li>a</li>" in h and "<p>para line</p>" in h
print("md_to_html OK")

db.init_db()
c = TestClient(app)
email = "op@test.com"
if not db.get_tenant_by_email(email):
    db.create_tenant(email, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Op", is_admin=True)
c.post("/login", data={"email": email, "password": "pw123456"})
r = c.post("/seed-demo")
bid = int(re.search(r"/brands/(\d+)", str(r.url)).group(1))
qid = db.list_questions(bid)[0]["id"]
rc = c.post(f"/brands/{bid}/content", data={"question_id": qid, "lang": "th"})
cid = int(re.search(r"/content/(\d+)", str(rc.url)).group(1))
print("content id", cid)

# 3) save WP connection (fake site; test_connection จะ fail แต่ row ต้องถูกบันทึก + decrypt ได้)
c.post(f"/brands/{bid}/wp", data={"site_url": "https://fake.example.com", "user": "admin", "app_password": "abcd efgh ijkl mnop qrst uvwx"})
conn = db.get_wp_connection(bid)
assert conn is not None and wp.decrypt(conn["auth_secret"]) == "abcd efgh ijkl mnop qrst uvwx"
print("wp connection saved + secret decrypts | status:", conn["status"])
rb = c.get(f"/brands/{bid}")
assert "เชื่อมต่อแล้ว" in rb.text
print("brand page shows connected")

# 4) publish ด้วย mock (ไม่ยิงเว็บจริง) -> content ต้องถูก mark published
wp.publish_post = lambda *a, **k: {"ok": True, "id": 777, "link": "https://fake.example.com/?p=777", "msg": "ส่งเข้า WordPress แล้ว (เป็นร่าง)"}
rp = c.post(f"/content/{cid}/publish")
print("publish ->", rp.status_code, "| badge:", "เผยแพร่แล้ว" in rp.text, "| post#777:", "777" in rp.text)
assert rp.status_code == 200 and "เผยแพร่แล้ว" in rp.text
it = db.get_content(cid)
assert it["status"] == "published" and it["wp_post_id"] == 777 and it["wp_link"]
print("db: status", it["status"], "| post", it["wp_post_id"], "| link", it["wp_link"])
print("ALL WP TESTS OK")
