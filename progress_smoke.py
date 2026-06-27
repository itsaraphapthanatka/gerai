"""M4 smoke test — SoV impact page (/brands/{id}/impact)"""
import os, re, json, bcrypt
os.environ["LITELLM_BASE_URL"] = ""
from fastapi.testclient import TestClient
import app.db as db
from app.main import app

db.init_db()
c = TestClient(app)

email = "impact@test.com"
if not db.get_tenant_by_email(email):
    db.create_tenant(email, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "ImpactTest", is_admin=True)
c.post("/login", data={"email": email, "password": "pw123456"})
r = c.post("/brands", data={"name": "Impact Co", "domain": "impact.co", "market": "test"})
bid = int(re.search(r"/brands/(\d+)", str(r.url)).group(1))
qid = db.add_question(bid, "คำถามทดสอบ", "th")

# impact page loads (ยังไม่มี runs)
r = c.get(f"/brands/{bid}/impact")
assert r.status_code == 200, r.status_code
assert "SoV Impact" in r.text
print("impact page loads (no runs): OK")

# สร้าง run จำลอง 2 รัน
with db.get_conn() as conn:
    run1 = conn.execute(
        db.q("INSERT INTO monitoring_runs(brand_id,started_at,status,questions_total,brand_hits,share_of_voice) VALUES(?,?,?,?,?,?) RETURNING id"),
        (bid, "2026-06-01T10:00:00", "done", 4, 1, 0.25)
    ).fetchone()[0]
    run2 = conn.execute(
        db.q("INSERT INTO monitoring_runs(brand_id,started_at,status,questions_total,brand_hits,share_of_voice) VALUES(?,?,?,?,?,?) RETURNING id"),
        (bid, "2026-06-20T10:00:00", "done", 4, 3, 0.75)
    ).fetchone()[0]
    for run_id, present in [(run1, 0), (run2, 1)]:
        conn.execute(
            db.q("INSERT INTO run_results(run_id,question_id,question,brand_present) VALUES(?,?,?,?)"),
            (run_id, qid, "คำถามทดสอบ", present)
        )

# สร้าง content แล้ว mark published
cid = db.create_content_item(bid, qid, "th", "บทความทดสอบ", "meta title", "meta desc", "body", "{}", "template")
db.mark_content_published(cid, 99, "https://example.com/?p=99")
item = db.get_content(cid)
assert item["status"] == "published"
assert item["published_at"] is not None
print("mark_content_published sets published_at:", item["published_at"][:10])

# impact page แสดงตัวเลข
r = c.get(f"/brands/{bid}/impact")
assert r.status_code == 200
assert "25.0%" in r.text, "SoV เริ่มต้น 25% ไม่ปรากฏ"
assert "75.0%" in r.text, "SoV ล่าสุด 75% ไม่ปรากฏ"
assert "+50.0%" in r.text, "delta +50% ไม่ปรากฏ"
assert "บทความทดสอบ" in r.text, "ชื่อ content ไม่ปรากฏ"
print("impact page shows SoV delta and content list: OK")

# question_results JSON ฝังในหน้าถูกต้อง
m = re.search(r'const qResults = (\[.*?\]);', r.text, re.DOTALL)
assert m, "qResults ไม่พบใน JS"
rows = json.loads(m.group(1))
assert any(row["run_id"] == run1 for row in rows), "run1 ไม่มีใน qResults"
assert any(row["run_id"] == run2 for row in rows), "run2 ไม่มีใน qResults"
print("question_results embedded correctly: OK")

# brand page มีลิงก์ impact
rb = c.get(f"/brands/{bid}")
assert "ดู SoV Impact" in rb.text, "ลิงก์ impact ไม่มีในหน้าแบรนด์"
print("brand page shows impact link: OK")

print("\nALL M4 TESTS OK ✅")
