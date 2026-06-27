"""ทดสอบ M1: generate content + schema + assets (DB แยก, ปิด LLM ใช้ template path)."""
import os
os.environ["LITELLM_BASE_URL"] = ""  # force template path (deterministic, no network)
import re
import bcrypt
from fastapi.testclient import TestClient
import app.db as db
from app.main import app

db.init_db()
c = TestClient(app)

email = "op@test.com"
if not db.get_tenant_by_email(email):
    db.create_tenant(email, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Op", is_admin=True)
c.post("/login", data={"email": email, "password": "pw123456"})

r = c.post("/seed-demo")
bid = int(re.search(r"/brands/(\d+)", str(r.url)).group(1))
qid = db.list_questions(bid)[0]["id"]
print("seeded brand", bid, "first question id", qid)

# generate Thai content
r = c.post(f"/brands/{bid}/content", data={"question_id": qid, "lang": "th"})
print("generate ->", r.status_code, "| final:", str(r.url))
assert "/content/" in str(r.url)
assert "FAQPage" in r.text and "schema.org" in r.text, "schema missing"
assert "เนื้อหา (Markdown)" in r.text, "body section missing"

# content shows on brand page
rb = c.get(f"/brands/{bid}")
print("brand page lists content:", ("คอนเทนต์ GEO" in rb.text) and ("ร่าง" in rb.text))
assert "คอนเทนต์ GEO" in rb.text

# generate English too
r2 = c.post(f"/brands/{bid}/content", data={"question_id": qid, "lang": "en"})
assert "/content/" in str(r2.url)

# assets page
ra = c.get(f"/brands/{bid}/assets")
print("assets ->", ra.status_code, "| GPTBot:", "GPTBot" in ra.text, "PerplexityBot:", "PerplexityBot" in ra.text, "Google-Extended:", "Google-Extended" in ra.text)
assert ra.status_code == 200 and "GPTBot" in ra.text and "PerplexityBot" in ra.text and "RealEstateAgent" in ra.text

print("content items in db:", len(db.list_content(bid)))
print("ALL CONTENT TESTS OK")
