r"""ย้ายข้อมูลจาก SQLite (geo_platform.db) → Postgres.

ตั้ง DATABASE_URL ชี้ Postgres ก่อนรัน:
  $env:DATABASE_URL="postgresql://geo:...@127.0.0.1:5434/geo_platform"
  .\.venv\Scripts\python.exe migrate_sqlite_to_pg.py
รันซ้ำได้ (ON CONFLICT DO NOTHING) — คัดลอกเฉพาะแถวที่ยังไม่มี
"""
import sqlite3
from pathlib import Path
import app.db as db

assert db.IS_PG, "ต้องตั้ง DATABASE_URL ชี้ Postgres ก่อน"
SRC = str(Path(__file__).resolve().parent / "geo_platform.db")
assert Path(SRC).exists(), f"ไม่พบ SQLite ต้นทาง: {SRC}"

db.init_db()  # สร้างตารางใน Postgres

TABLES = ["tenants", "brands", "target_questions", "monitoring_runs", "run_results", "content_items", "wp_connections"]
src = sqlite3.connect(SRC)
src.row_factory = sqlite3.Row
pg = db.get_conn()
try:
    for t in TABLES:
        rows = src.execute(f"SELECT * FROM {t}").fetchall()
        if not rows:
            print(f"{t}: 0 rows")
            continue
        cols = list(rows[0].keys())
        collist = ",".join(cols)
        ph = ",".join(["%s"] * len(cols))
        for r in rows:
            pg.execute(
                f"INSERT INTO {t} ({collist}) VALUES ({ph}) ON CONFLICT (id) DO NOTHING",
                tuple(r[col] for col in cols),
            )
        pg.execute(f"SELECT setval(pg_get_serial_sequence('{t}','id'), GREATEST((SELECT MAX(id) FROM {t}), 1))")
        print(f"{t}: {len(rows)} rows")
    pg.commit()
finally:
    pg.close()
    src.close()
print("MIGRATION DONE")
