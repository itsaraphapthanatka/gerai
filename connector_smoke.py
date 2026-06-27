"""ทดสอบ M3 platform-side: connector mode + publish ผ่าน Connector (mock network)."""
import os
os.environ["LITELLM_BASE_URL"] = ""
import re
import bcrypt
from fastapi.testclient import TestClient
import app.db as db
import app.wp_client as wp
from app.main import app

# mock เครือข่าย connector (ไม่ยิงเว็บจริง)
wp.connector_ping = lambda site, key: {"ok": True, "msg": "Connector เชื่อมต่อแล้ว (v0.1.0)"}
captured = {}
def fake_pub(site, key, title, body_md, schema_json=None, meta_title=None, meta_desc=None, status="draft", wp_post_id=None):
    captured.update({"key": key, "schema": schema_json, "meta_title": meta_title, "status": status})
    return {"ok": True, "id": 555, "link": "https://demo/?p=555", "msg": "ส่งผ่าน Connector แล้ว (พร้อม schema)"}
wp.connector_publish = fake_pub

db.init_db()
c = TestClient(app)
email = "op@test.com"
if not db.get_tenant_by_email(email):
    db.create_tenant(email, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Op", is_admin=True)
c.post("/login", data={"email": email, "password": "pw123456"})
r = c.post("/seed-demo"); bid = int(re.search(r"/brands/(\d+)", str(r.url)).group(1))
qid = db.list_questions(bid)[0]["id"]
rc = c.post(f"/brands/{bid}/content", data={"question_id": qid, "lang": "th"}); cid = int(re.search(r"/content/(\d+)", str(rc.url)).group(1))

# save connection WITH connector api key
c.post(f"/brands/{bid}/wp", data={"site_url": "https://demo.example.com", "user": "admin", "app_password": "a b c d", "api_key": "SECRETKEY123"})
conn = db.get_wp_connection(bid)
assert conn["mode"] == "connector", conn["mode"]
assert wp.decrypt(conn["api_key"]) == "SECRETKEY123"
print("connector mode saved; api_key encrypts/decrypts OK; status:", conn["status"])

# publish -> ต้องใช้ connector path + ส่ง schema
rp = c.post(f"/content/{cid}/publish")
assert "เผยแพร่แล้ว" in rp.text
assert captured.get("key") == "SECRETKEY123", captured
assert captured.get("schema") and "FAQPage" in captured["schema"], "schema ไม่ถูกส่งไป connector"
assert captured.get("status") == "draft"
it = db.get_content(cid)
assert it["status"] == "published" and it["wp_post_id"] == 555
print("publish ใช้ connector + ส่ง schema; post#", it["wp_post_id"])

# brand page แสดงโหมด connector
rb = c.get(f"/brands/{bid}")
assert "Connector" in rb.text and "push schema" in rb.text
print("brand page shows Connector mode")
print("ALL CONNECTOR TESTS OK")
