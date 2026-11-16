"""ซ้อมย้ายฐานข้อมูล (stage 18) — ข้อมูลสมมติเท่านั้น · ไม่แตะฐานข้อมูลจริง · ไม่ต่อเน็ต

ขั้นตอน (วัดเวลาจริงทุกขั้น):
  1. สร้าง DB ด้วย schema ของโค้ดก่อนเริ่มงานรอบนี้ (commit BASE) แล้วใส่ข้อมูลสมมติ
  2. สำรอง (sqlite backup API)
  3. ย้าย = รัน init_db() ของโค้ดปัจจุบันบนไฟล์เดิม · ตรวจ: จำนวนแถวเดิมไม่หาย · ตารางใหม่มี · integrity_check · schema_migrations
  4. ย้ายซ้ำ (ต้องไม่เปลี่ยนอะไร = idempotent)
  5. ถอยโค้ด: โค้ดเก่า init_db() + อ่านข้อมูลบน DB ที่ย้ายแล้ว (ตารางที่เพิ่มต้องไม่ทำให้โค้ดเก่าล้ม)
  6. กู้คืนจากไฟล์สำรอง → จำนวนแถวตรงกับก่อนย้าย
  7. backfill แบบ dry-run: รายงานจับคู่ตัวตน (stage 09) — นับอย่างเดียว ไม่เขียน · รันซ้ำได้ผลเท่าเดิม (resume ได้)
ผลเขียนเป็น JSON ที่ stdout (และไฟล์ถ้าระบุ --out) · Postgres ไม่ได้ทดสอบในสคริปต์นี้

ใช้: .venv/Scripts/python.exe scripts/migration_rehearsal.py --leads 2000 --out docs/rebuild/rehearsal_result.json"""
from __future__ import annotations
import argparse
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "a35405f"            # commit สุดท้ายก่อนเริ่ม rebuild (stage 00)


def _extract_old(dst: Path) -> None:
    raw = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=zip", BASE, "app"], capture_output=True, check=True).stdout
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        z.extractall(dst)


def _child(code: str, root: Path, db_path: Path, extra_env: dict | None = None) -> dict:
    env = {**os.environ, "GEO_DB_PATH": str(db_path), "PYTHONUTF8": "1", "GEO_AUTORUN": "0", "AI_BASE_URL": "http://127.0.0.1:9",
           "LITELLM_BASE_URL": "", "GEO_DEEP_AUDIT": "0", **(extra_env or {})}
    env.pop("DATABASE_URL", None)
    for k in list(env):
        if k.startswith("GEO_FLAG_"):
            env.pop(k)
    r = subprocess.run([sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(root)!r})\n" + code], capture_output=True, text=True, env=env,
                       cwd=str(root), timeout=900)
    if r.returncode != 0:
        raise RuntimeError(f"child failed:\n{r.stderr[-3000:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def counts(path: Path) -> dict:
    c = sqlite3.connect(path)
    try:
        tabs = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {t: c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tabs}
    finally:
        c.close()


def integrity(path: Path) -> dict:
    c = sqlite3.connect(path)
    try:
        return {"integrity": c.execute("PRAGMA integrity_check").fetchone()[0], "fk_violations": len(c.execute("PRAGMA foreign_key_check").fetchall())}
    finally:
        c.close()


SEED = """
import json, random, bcrypt
import app.db as db
db.init_db()
random.seed(7)
N = {n}
pw = bcrypt.hashpw(b"synthetic-only", bcrypt.gensalt(rounds=4)).decode()
t = db.create_tenant("owner@synthetic.test", pw, "Synthetic Owner", is_admin=True)
for i in range(N):
    dom = f"site{{i % (N - N // 10)}}.example-synthetic.test" if i % 10 == 0 else f"site{{i}}.example-synthetic.test"   # ~10% โดเมนซ้ำ (ผู้ติดต่อหลายคน)
    lid = db.add_lead(f"https://{{dom}}", dom, source=random.choice(["audit", "prospect", "line"]), email=f"contact{{i}}@example-synthetic.test")
    db.add_lead_event(lid, "note", "synthetic", "system")
    if i % 25 == 0:
        b = db.create_brand(t, f"Brand {{i}}", dom)
        db.create_content_item(b, None, "th", f"Title {{i}}", "m", "d", "body " * 50, "", "synthetic")
print(json.dumps({{"ok": True}}))
"""

MIGRATE = """
import json, time
import app.db as db
t0 = time.perf_counter(); db.init_db(); t1 = time.perf_counter()
db.record_schema_versions() if hasattr(db, "record_schema_versions") else None
print(json.dumps({"seconds": round(t1 - t0, 3), "versions": [r["version"] for r in db.list_schema_versions()] if hasattr(db, "list_schema_versions") else []}))
"""

OLD_READ = """
import json
import app.db as db
db.init_db()
print(json.dumps({"leads": len(db.list_leads(None, 100000)), "brands": len(db.list_all_brands())}))
"""

DRYRUN = """
import json, time
import app.c360 as c
t0 = time.perf_counter(); r = c.identity_report(); t1 = time.perf_counter()
keep = {"counts": r.get("counts"), "by_kind": r.get("by_kind"), "writes": r.get("writes")}
print(json.dumps({"seconds": round(t1 - t0, 3), "summary": keep, "conflicts": len(r.get("conflicts") or [])}, default=str))
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leads", type=int, default=2000)
    ap.add_argument("--out")
    a = ap.parse_args()
    res: dict = {"base_commit": BASE, "synthetic_leads": a.leads, "postgres": "ไม่ได้ทดสอบ (ไม่มี Postgres ในเครื่องนี้)", "steps": {}}
    with tempfile.TemporaryDirectory(prefix="jorai-rehearsal-") as td:
        td = Path(td)
        old = td / "old"
        old.mkdir()
        _extract_old(old)
        dbp, bak, rst = td / "geo.db", td / "geo.backup.db", td / "geo.restored.db"
        t = time.perf_counter()
        _child(SEED.format(n=a.leads), old, dbp)
        res["steps"]["seed_old_schema_s"] = round(time.perf_counter() - t, 2)
        before = counts(dbp)
        res["before"] = {"tables": len(before), "rows": sum(before.values()), "leads": before.get("leads"), "brands": before.get("brands")}
        t = time.perf_counter()
        src, dst = sqlite3.connect(dbp), sqlite3.connect(bak)
        src.backup(dst); src.close(); dst.close()
        res["steps"]["backup_s"] = round(time.perf_counter() - t, 3)
        res["db_bytes"] = dbp.stat().st_size
        m1 = _child(MIGRATE, ROOT, dbp)
        after = counts(dbp)
        lost = {k: (before[k], after.get(k)) for k in before if after.get(k, -1) < before[k]}
        res["steps"]["migrate_s"] = m1["seconds"]
        res["migrate"] = {"new_tables": sorted(set(after) - set(before)), "lost_rows": lost, "schema_versions": len(m1["versions"]), **integrity(dbp)}
        m2 = _child(MIGRATE, ROOT, dbp)
        res["steps"]["migrate_again_s"] = m2["seconds"]
        res["migrate_again_changed_rows"] = {k: (after[k], v) for k, v in counts(dbp).items() if after.get(k) != v and k != "schema_migrations"}
        r_old = _child(OLD_READ, old, dbp)
        res["old_code_on_migrated_db"] = {"ok": r_old["leads"] == before.get("leads"), **r_old}
        snap = counts(dbp)
        d1 = _child(DRYRUN, ROOT, dbp)
        d2 = _child(DRYRUN, ROOT, dbp)
        res["dry_run_identity"] = {"first": d1, "repeat_same": d1["summary"] == d2["summary"], "wrote_rows": counts(dbp) != snap}
        t = time.perf_counter()
        src, dst = sqlite3.connect(bak), sqlite3.connect(rst)
        src.backup(dst); src.close(); dst.close()
        res["steps"]["restore_s"] = round(time.perf_counter() - t, 3)
        res["restore_matches_before"] = counts(rst) == before
        res["restore_integrity"] = integrity(rst)
    ok = (not res["migrate"]["lost_rows"] and res["migrate"]["integrity"] == "ok" and not res["migrate_again_changed_rows"]
          and res["old_code_on_migrated_db"]["ok"] and res["restore_matches_before"] and not res["dry_run_identity"]["wrote_rows"]
          and res["dry_run_identity"]["repeat_same"])
    res["pass"] = ok
    out = json.dumps(res, ensure_ascii=False, indent=1)
    print(out)
    if a.out:
        Path(a.out).write_text(out, encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
