"""Batch monitoring job — รันมอนิเตอร์ให้แบรนด์ (สำหรับ cron / scheduler / รันมือ).

ใช้:
  python run_monitors.py --all              # รันทุกแบรนด์
  python run_monitors.py --due [--days 7]   # รันเฉพาะแบรนด์ที่ค้างเกิน N วัน
  python run_monitors.py --brand 3          # รันแบรนด์เดียว
"""
import os
import sys
import argparse
import datetime
from pathlib import Path
from dotenv import load_dotenv

try:
    sys.stdout.reconfigure(encoding="utf-8")  # กัน console cp874 (Windows) crash ตอน print ไทย
except Exception:
    pass

load_dotenv(Path(__file__).resolve().parent / ".env")
from app import db, geo_worker


def _due(last_run_at, days: int) -> bool:
    if not last_run_at:
        return True
    try:
        return (datetime.datetime.now() - datetime.datetime.fromisoformat(last_run_at)).days >= days
    except Exception:
        return True


def select_targets(brands, all_=False, due=False, days=7, brand_id=None):
    if brand_id is not None:
        return [b for b in brands if b["id"] == brand_id]
    if due:
        return [b for b in brands if _due(b["last_run_at"], days)]
    return list(brands)  # --all / ดีฟอลต์


def run_targets(targets):
    out = []
    for b in targets:
        try:
            s = geo_worker.run_for_brand(b["id"])
            line = f"  [{b['id']}] {b['name']}: SoV {s['brand_hits']}/{s['questions']}"
            out.append(line)
            print(line)
        except Exception as e:
            line = f"  [{b['id']}] {b['name']}: ERROR {e}"
            out.append(line)
            print(line)
    return out


def main():
    ap = argparse.ArgumentParser(description="รันมอนิเตอร์แบบ batch")
    ap.add_argument("--all", action="store_true", help="รันทุกแบรนด์")
    ap.add_argument("--due", action="store_true", help="รันเฉพาะแบรนด์ที่ค้างเกิน N วัน")
    ap.add_argument("--days", type=int, default=int(os.getenv("GEO_RUN_INTERVAL_DAYS", "7")))
    ap.add_argument("--brand", type=int, help="รันแบรนด์เดียว (ระบุ id)")
    args = ap.parse_args()
    db.init_db()
    brands = db.list_all_brands()
    targets = select_targets(brands, all_=args.all, due=args.due, days=args.days, brand_id=args.brand)
    if not targets:
        print("ไม่มีแบรนด์ที่ต้องรัน")
        return
    print(f"backend={geo_worker.active_backend()} · รัน {len(targets)} แบรนด์")
    run_targets(targets)


if __name__ == "__main__":
    main()
