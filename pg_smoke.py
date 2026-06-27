r"""ทดสอบ db layer บน Postgres จริง — ตั้ง DATABASE_URL ชี้ Postgres ก่อนรัน.

ตัวอย่าง:
  $env:DATABASE_URL="postgresql://postgres:test@127.0.0.1:55432/geotest"
  .\.venv\Scripts\python.exe pg_smoke.py
"""
import json
import bcrypt
import app.db as db

assert db.IS_PG, "ต้องตั้ง DATABASE_URL ชี้ Postgres ก่อน"
db.init_db()
print("init_db OK (Postgres)")

email = "pgtest@example.com"
t = db.get_tenant_by_email(email)
tid = t["id"] if t else db.create_tenant(email, bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode(), "PG", is_admin=True)
print("tenant id:", tid, "| count_admins:", db.count_admins())

bid = db.create_brand(tid, "PG Brand", "pgbrand.com", "market")
qid = db.add_question(bid, "pg question", "th")
print("brand:", bid, "| question:", qid)

conn = db.get_conn()
cur = conn.execute(
    db.q("INSERT INTO monitoring_runs(brand_id,started_at,status,questions_total,brand_hits,share_of_voice) "
         "VALUES(?,?,?,?,?,?) RETURNING id"),
    (bid, db.now(), "done", 1, 1, 0.5),
)
run_id = cur.fetchone()["id"]
conn.execute(
    db.q("INSERT INTO run_results(run_id,question_id,question,brand_present,top_domains) VALUES(?,?,?,?,?)"),
    (run_id, qid, "pg question", 1, json.dumps(["a.com"])),
)
conn.commit()
conn.close()

runs = db.list_runs(bid)
res = db.get_results(run_id)
allb = [b for b in db.list_all_brands() if b["id"] == bid][0]
print("runs:", len(runs), "| results:", len(res), "| last_run_at:", allb["last_run_at"], "| last_sov:", allb["last_sov"])
assert len(runs) >= 1 and len(res) == 1 and allb["last_run_at"]

cid = db.create_content_item(bid, qid, "th", "T", "MT", "MD", "body", "{}", "template")
db.mark_content_published(cid, 42, "http://x/p")
db.upsert_wp_connection(bid, "https://s.com", "u", "enc", "ok", "connector", "k")
wp = db.get_wp_connection(bid)
assert db.get_content(cid)["status"] == "published" and wp["mode"] == "connector"
print("content:", db.get_content(cid)["status"], "| wp mode:", wp["mode"])
print("ALL POSTGRES DB TESTS OK")
