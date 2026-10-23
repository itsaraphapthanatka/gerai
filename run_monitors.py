"""Batch monitoring job — รันมอนิเตอร์ให้แบรนด์ (สำหรับ cron / scheduler / รันมือ).

ใช้:
  python run_monitors.py --all              # รันทุกแบรนด์
  python run_monitors.py --due [--days 7]   # รันเฉพาะแบรนด์ที่ค้างเกิน N วัน
  python run_monitors.py --brand 3          # รันแบรนด์เดียว
  python run_monitors.py --all --rank       # เช็คอันดับ Google (ไม่ใช่ SoV)
  python run_monitors.py --all --health     # ตรวจสุขภาพเว็บลูกค้า (เต็ม ใช้เวลานาน)
  python run_monitors.py --all --uptime     # เช็คเร็ว ๆ ว่าเว็บยังเปิดได้ไหม (cron รายวัน)
  python run_monitors.py --all --ai         # ถาม AI จริง — เฉพาะเจ้าที่ตั้งคีย์ไว้ (ChatGPT/Claude/Perplexity/Gemini)
  python run_monitors.py --all --gsc        # ซิงค์ Google Search Console (index จริง + sitemap + คลิก) รันก่อน --health
  python run_monitors.py --all --speed      # สแกนความเร็วหน้าแรก (PageSpeed Insights) mobile + desktop
  python run_monitors.py --all --aiserp     # เช็ค Google AI Overview / AI Mode (SerpApi — ต้องมีคีย์)
  python run_monitors.py --email            # ส่งอีเมลแคมเปญที่ถึงกำหนด (cron รายวัน — ไม่ขึ้นกับแบรนด์)
  python run_monitors.py --autopilot        # รอบ Autopilot ให้แบรนด์ที่เปิดไว้ (cron รายสัปดาห์) · --brand N รันแบรนด์เดียว
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
from app import office


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


def _cto_label(targets, rank=False, health=False, uptime=False, ai=False, gsc=False, speed=False, aiserp=False):
    what = ("เช็กอันดับ Google" if rank else "ตรวจสุขภาพเว็บลูกค้า" if health else "เช็กว่าเว็บลูกค้ายังเปิดอยู่" if uptime else
            "ถาม AI ว่าเอ่ยถึงแบรนด์ลูกค้าไหม" if ai else "ซิงค์ Search Console" if gsc else "สแกนความเร็วเว็บลูกค้า" if speed else
            "เช็ก Google AI Overview" if aiserp else "วัดการมองเห็นใน AI")
    return f"{what} ({len(targets)} แบรนด์)"


@office.track("cto", _cto_label)
def run_targets(targets, rank=False, health=False, uptime=False, ai=False, gsc=False, speed=False, aiserp=False):
    out = []
    for b in targets:
        try:
            if speed:
                from app.main import run_pagespeed
                s = run_pagespeed(b["id"])
                def _sc(k):
                    v = s.get(k) or {}
                    return f"ผิดพลาด ({v['error'][:50]})" if v.get("error") else f"{(v.get('scores') or {}).get('performance')}"
                line = f"  [{b['id']}] {b['name']}: ประสิทธิภาพ มือถือ {_sc('mobile')} · เดสก์ท็อป {_sc('desktop')}"
            elif aiserp:
                from app.main import run_ai_serp
                s = run_ai_serp(b["id"])
                if s.get("skip"):
                    line = f"  [{b['id']}] {b['name']}: ข้าม — {s['skip']}"
                else:
                    ov, md = s["by_kind"]["overview"], s["by_kind"]["mode"]
                    line = (f"  [{b['id']}] {b['name']}: AI Overview โผล่ {ov['cited'] + ov['named']}/{ov['shown']} (Google ไม่แสดง {ov['none']})"
                            f" · AI Mode {md['cited'] + md['named']}/{md['shown']} · {s['searches']} searches"
                            + (f" · ผิดพลาด {ov['errors'] + md['errors']}" if ov["errors"] + md["errors"] else ""))
            elif gsc:
                from app.main import run_gsc_sync
                s = run_gsc_sync(b["id"])
                if not s.get("linked"):
                    tail = "ยังไม่ได้เชื่อม — " + (s.get("reason") or "เพิ่มบัญชีบริการเป็นผู้ใช้ใน Search Console")
                else:
                    idx, an = s["index"], s["analytics"]["site"]
                    sm = s.get("sitemap") or {}
                    tail = (f"{s['property']} · index {idx['indexed']}/{idx['total']}"
                            f" · {an.get('clicks', 0):,} คลิก / {an.get('impressions', 0):,} impressions ใน 28 วัน"
                            + (" · sitemap ส่งแล้ว" if sm.get("submitted") else " · sitemap ยังไม่ได้ส่ง")
                            + (f" · ผิดพลาด {len(s['errors'])}" if s.get("errors") else ""))
                line = f"  [{b['id']}] {b['name']}: {tail}"
            elif ai:
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
    ap.add_argument("--gsc", action="store_true",
                    help="ซิงค์ Google Search Console — index จริงรายหน้า, ส่ง/เช็ค sitemap, คลิก 28 วัน (รันก่อน --health)")
    ap.add_argument("--speed", action="store_true", help="สแกนความเร็วหน้าแรกด้วย PageSpeed Insights (mobile + desktop)")
    ap.add_argument("--aiserp", action="store_true", help="เช็ค Google AI Overview / AI Mode ผ่าน SerpApi (ต้องมีคีย์)")
    ap.add_argument("--email", action="store_true", help="ส่งอีเมลแคมเปญที่ถึงกำหนด (ไม่ขึ้นกับแบรนด์)")
    ap.add_argument("--digest", action="store_true", help="สรุปเช้าของผู้บริหารเอเจนต์ (CEO/CFO/CTO) → หน้าการตัดสินใจ + อีเมลแอดมิน")
    ap.add_argument("--prospect", action="store_true", help="หาลูกค้าเอง: ค้นธุรกิจตามกลุ่มเป้าหมาย → ตรวจเว็บ → หาอีเมล → CEO เสนอทัก")
    ap.add_argument("--success", action="store_true", help="ดูแลลูกค้าที่จ่ายแล้ว: เริ่มงาน · เช็กติดตั้งวันที่ 3 · งานชิ้นแรกวันที่ 7 · รายงานก่อนรอบบิล · เตือนเสี่ยงหลุด")
    ap.add_argument("--followup", action="store_true", help="SDR: ติดตามผลลีดที่ทักแล้วเงียบ (วันที่ 3 · 7 · 14) หยุดเมื่อตอบ/ขอไม่รับ")
    ap.add_argument("--cmo", action="store_true", help="ฝ่ายการตลาด: เผยแพร่คอนเทนต์ของเจอ.AI ที่ผ่าน QA → เสนอโพสต์ LINE/Facebook → เคสศึกษา")
    ap.add_argument("--autopilot", action="store_true", help="รอบ Autopilot ให้แบรนด์ที่เปิดไว้ (วัดทุกตัว → เขียนคอนเทนต์ → รายงาน)")
    args = ap.parse_args()
    db.init_db()
    if args.success:
        from app import success
        res = success.run()
        print(success.status_text() or str(res))
        return
    if args.followup:
        from app import followup
        res = followup.run()
        print(followup.status_text() or str(res))
        return
    if args.cmo:
        from app import marketing
        res = marketing.run()
        print("cmo · " + (res.get("skipped") or f"เผยแพร่ {len(res['published'])} บทความ · เสนอโพสต์ {res['proposed']} · ช่องทาง {', '.join(res['channels']) or 'ยังไม่มี'} · ลีด 7 วัน {res['attribution']}"))
        return
    if args.prospect:
        from app import prospecting
        res = prospecting.run()
        print("prospect · " + (res.get("skipped") or f"พบ {res['found']} เว็บ เพิ่ม {res['added']} ตรวจ {res['audited']} มีอีเมล {res['with_email']} เสนอทัก {res['proposed']}"
                                                   + (f" ล้ม {res['errors']}" if res.get("errors") else "") + f" · {', '.join(res['leads'])}"))
        return
    if args.digest:
        from app import execs
        res = execs.morning_digest(os.getenv("GEO_BASE_URL", "https://geo.appreview.cloud"))
        print(res["text"])
        print(f"digest · ข้อเสนอรอ {res['pending']} · ส่งอีเมล {len(res['sent_to'])} ฉบับ" + (" (" + ", ".join(res["sent_to"]) + ")" if res["sent_to"] else " — ไม่มีแอดมินที่อีเมลใช้ได้จริง"))
        return
    if args.email:
        # แคมเปญไม่ผูกกับแบรนด์ที่เลือก — ส่งทุกอันที่ถึงกำหนด แล้วจบ
        from app import campaigns, mailer
        if not mailer.config():
            print("email · ยังไม่ได้ตั้งค่า SMTP (admin → อีเมล) — ข้าม")
            return
        res = campaigns.send_due(os.getenv("GEO_BASE_URL", "https://geo.appreview.cloud"))
        print(f"email · แคมเปญถึงกำหนด {len(res)} รายการ")
        for r in res:
            print(f"  {r['campaign']}: ส่ง {r['sent']}/{r['recipients']}" + (f" · ล้ม {r['failed']} ({'; '.join(r['errors'])})" if r["failed"] else "")
                  + (f" · รอบถัดไป {r['next_at']}" if r.get("next_at") else " · จบ"))
        return
    brands = db.list_all_brands()
    if args.autopilot and args.brand is None:
        brands = [b for b in brands if b["autopilot"]]       # เฉพาะแบรนด์ที่เปิด Autopilot
        args.all = True
    if args.aiserp and args.brand is None:
        brands = [b for b in brands if b["aiserp_auto"]]     # เฉพาะแบรนด์ที่เปิดเช็ค Google AI อัตโนมัติ (โควตา SerpApi)
        args.all = True
    targets = select_targets(brands, all_=args.all, due=args.due, days=args.days, brand_id=args.brand)
    if not targets:
        print("ไม่มีแบรนด์ที่ต้องรัน" + (" — ยังไม่มีแบรนด์ไหนเปิด Autopilot" if args.autopilot else "")
              + (" — ยังไม่มีแบรนด์ไหนเปิดเช็ค Google AI อัตโนมัติ (ติ๊กที่หน้า AI Overview ของแบรนด์)" if args.aiserp else ""))
        return
    if args.autopilot:
        from app.main import run_autopilot
        print(f"autopilot · {len(targets)} แบรนด์")
        for b in targets:
            try:
                log = run_autopilot(b["id"])
                print(f"  [{b['id']}] {b['name']}: " + ("สำเร็จ" if log["ok"] else f"ล้ม {log['errors']} ขั้น") + " — " + (log["summary"] or "")[:220])
            except Exception as e:
                print(f"  [{b['id']}] {b['name']}: ERROR {e}")
        return
    if args.speed:
        print(f"speed · สแกน {len(targets)} แบรนด์ (mobile + desktop)")
    elif args.aiserp:
        from app import ai_serp
        print(f"ai-serp · {len(targets)} แบรนด์ · SerpApi: " + ("มีคีย์" if ai_serp.available() else "ยังไม่ได้ตั้ง (admin → ตั้งค่า)"))
    elif args.gsc:
        from app import gsc
        info = gsc.key_info()
        print(f"gsc · {len(targets)} แบรนด์ · บัญชีบริการ: " + (info["email"] if info else "ยังไม่ได้ตั้ง (admin → ตั้งค่า → Search Console)"))
    elif args.ai:
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
    run_targets(targets, rank=args.rank, health=args.health, uptime=args.uptime, ai=args.ai, gsc=args.gsc,
                speed=args.speed, aiserp=args.aiserp)


if __name__ == "__main__":
    main()
