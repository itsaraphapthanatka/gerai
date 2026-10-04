"""Batch monitoring job — รันมอนิเตอร์ให้แบรนด์ (สำหรับ cron / scheduler / รันมือ).

ใช้:
  python run_monitors.py --all              # รันทุกแบรนด์
  python run_monitors.py --due [--days 7]   # รันเฉพาะแบรนด์ที่ค้างเกิน N วัน
  python run_monitors.py --brand 3          # รันแบรนด์เดียว
  python run_monitors.py --all --rank       # เช็คอันดับ Google (ไม่ใช่ SoV)
  python run_monitors.py --all --health     # ตรวจสุขภาพเว็บลูกค้า (เต็ม ใช้เวลานาน)
  python run_monitors.py --all --uptime     # เช็คเร็ว ๆ ว่าเว็บยังเปิดได้ไหม (cron รายวัน)
  python run_monitors.py --all --ai         # ถาม AI จริง (ChatGPT/Claude/Perplexity/Gemini)
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


def run_targets(targets, rank=False, health=False, uptime=False, ai=False):
    out = []
    for b in targets:
        try:
            if ai:
                from app.main import run_ai_visibility
                s = run_ai_visibility(b["id"])
                eng = ", ".join(f"{v['label']} {v['cited']}+{v['named']}/{v['asked']}"
                                for v in s["by_engine"].values() if v["asked"])
                # asked=0 ไม่ได้แปลว่าไม่มีคีย์เสมอไป — อาจมีคีย์แต่ยิงแล้วพังทุกครั้ง (เช่น 401)
                # ถ้าเหมารวมว่า "ยังไม่ได้ตั้งคีย์" คนจะไปใส่คีย์ซ้ำทั้งที่ปัญหาคือคีย์ผิด
                errs = {v["label"]: v["errors"] for v in s["by_engine"].values() if v["errors"]}
                if s["asked"]:
                    tail = f"ถูกพูดถึง {s['rate']}% ({eng}) · ${s.get('cost_usd', 0):.3f}"
                    # คำตอบที่ไม่ได้อิงการค้นเว็บไม่เข้าตัวหาร — บอกจำนวนไว้ให้รู้ว่า % มาจากกี่คำตอบ
                    extra = [f"ไม่ได้ค้น {s['unsearched']}" if s.get("unsearched") else "",
                             f"ไม่มีคำตอบ {s['no_answer']}" if s.get("no_answer") else ""]
                    if any(extra):
                        tail += " · " + ", ".join(x for x in extra if x)
                    if errs:
                        tail += " · ผิดพลาด: " + ", ".join(f"{k} {n}" for k, n in errs.items())
                elif errs:
                    first = next((r["reason"] for r in s["rows"] if r["status"] == "error"), "")
                    tail = ("ถามไม่สำเร็จ — " + ", ".join(f"{k} ผิดพลาด {n}" for k, n in errs.items())
                            + (f" ({first[:70]})" if first else ""))
                else:
                    reasons = list(dict.fromkeys(r["reason"] for r in s["rows"] if r["reason"]))
                    tail = "ข้ามทุกเจ้า — " + ("; ".join(reasons) if reasons else "ไม่มีคำถามเป้าหมาย")
                line = f"  [{b['id']}] {b['name']}: {tail}"
            elif uptime:
                from app.main import run_uptime_check
                s = run_uptime_check(b["id"], notify_tenant=b["tenant_id"])
                line = (f"  [{b['id']}] {b['name']}: "
                        + ("ปกติ" if s["up"] else f"ล่ม — {s['reason']}"))
            elif health:
                from app.main import run_health_check
                s = run_health_check(b["id"], notify_tenant=b["tenant_id"])
                line = (f"  [{b['id']}] {b['name']}: "
                        + ("ผ่านทั้งหมด" if s["ok"] else f"{s['n_fail']} ปัญหา — "
                           + ", ".join(c["label"] for c in s["checks"] if c["status"] == "fail")))
            elif rank:
                s = geo_worker.check_rank_for_brand(b["id"])
                avg = f" (เฉลี่ย #{s['avg_position']:.1f})" if s["avg_position"] else ""
                line = f"  [{b['id']}] {b['name']}: ติดอันดับ {s['ranked']}/{s['checked']}{avg}"
            else:
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
    ap.add_argument("--rank", action="store_true", help="เช็คอันดับ Google แทนการมอนิเตอร์ SoV")
    ap.add_argument("--health", action="store_true", help="ตรวจสุขภาพเว็บลูกค้า (แจ้งเตือนเมื่อเจอปัญหา)")
    ap.add_argument("--ai", action="store_true",
                    help="ถาม AI ผู้ช่วยจริงว่าแบรนด์ถูกเอ่ยถึงไหม (มีค่าใช้จ่ายต่อคำถาม)")
    ap.add_argument("--uptime", action="store_true",
                    help="เช็คเร็ว ๆ ว่าเว็บยังเปิดได้ไหม — แจ้งเตือนตอนล่ม/กลับมา (สำหรับ cron รายวัน)")
    args = ap.parse_args()
    db.init_db()
    brands = db.list_all_brands()
    targets = select_targets(brands, all_=args.all, due=args.due, days=args.days, brand_id=args.brand)
    if not targets:
        print("ไม่มีแบรนด์ที่ต้องรัน")
        return
    if args.ai:
        from app import ai_visibility
        av = ai_visibility.available_engines()
        # บอกด้วยว่าไปทางไหน — คีย์ตรง หรือผ่าน OpenRouter — เพราะค่าใช้จ่ายและที่ต้องไปแก้ต่างกัน
        lab = lambda e: ai_visibility.ENGINES[e]["label"] + (" (OpenRouter)" if ai_visibility.route(e) == "openrouter" else "")
        print(f"ai · {len(targets)} แบรนด์ · เจ้าที่ถามได้: " + (", ".join(lab(e) for e in av) if av else "ยังไม่มีเลย"))
    elif args.uptime:
        print(f"uptime · เช็ค {len(targets)} แบรนด์")
    elif args.health:
        print(f"health · ตรวจ {len(targets)} แบรนด์")
    else:
        backend = geo_worker.rank_backend() if args.rank else geo_worker.active_backend()
        print(f"{'rank' if args.rank else 'monitor'} · backend={backend} · รัน {len(targets)} แบรนด์")
    run_targets(targets, rank=args.rank, health=args.health, uptime=args.uptime, ai=args.ai)


if __name__ == "__main__":
    main()
