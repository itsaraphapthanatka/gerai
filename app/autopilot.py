"""Autopilot — ให้ระบบทำงาน GEO ครบวงจรเองรายสัปดาห์ต่อแบรนด์ แล้วรายงานว่าทำอะไรไป

ลำดับ: วัด (SoV → อันดับ Google → Search Console → สุขภาพ → ความเร็ว → ผู้ช่วย AI → AI ของ Google)
       → ทำ (เขียนคอนเทนต์ปิดช่องว่างที่ยังไม่มีคอนเทนต์ แล้วเผยแพร่ตามโหมด) → รายงาน (PDF ส่งอีเมลถ้ามี SMTP) → แจ้งเตือนในแอป
ของที่แพงหรือช้าไม่ทำซ้ำถ้าเพิ่งทำ: ผู้ช่วย AI / AI ของ Google ข้ามถ้าทำมาไม่ถึง FRESH_COSTLY_DAYS วัน
(cron รายสัปดาห์ของแต่ละตัววัดอาจเพิ่งรันไป) · ตัววัดที่ไม่มีค่าใช้จ่ายข้ามถ้าทำมาไม่ถึง FRESH_CHEAP_HOURS ชม.
แต่ละขั้นล้มได้โดยไม่ล้มทั้งรอบ — เก็บผล/เวลา/ข้อผิดพลาดต่อขั้นลง autopilot_runs ให้คนดูย้อนหลังได้
run() รับฟังก์ชันของแต่ละขั้นเป็น dict (main.py เป็นคนต่อสายของจริง) จึงเทสต์ลำดับ/การข้าม/การล้มได้โดยไม่ยิงอะไร
"""
from __future__ import annotations
import time
import datetime

STEPS = [("sov", "มอนิเตอร์ SoV"), ("rank", "อันดับ Google"), ("gsc", "Search Console"), ("health", "สุขภาพเว็บ"),
         ("speed", "ความเร็วเว็บ"), ("ai", "ผู้ช่วย AI"), ("aiserp", "Google AI Overview / AI Mode"),
         ("content", "เขียนคอนเทนต์ปิดช่องว่าง"), ("report", "รายงาน + อีเมล"), ("notify", "แจ้งเตือน")]
LABEL = dict(STEPS)
MODES = {"draft": "สร้างเป็นร่าง — คนตรวจก่อนเผยแพร่", "publish": "สร้างแล้วเผยแพร่เลย"}
FRESH_COSTLY_DAYS = 3        # ai, aiserp
FRESH_CHEAP_HOURS = 20       # sov, rank, gsc, health, speed
COSTLY = ("ai", "aiserp")
MEASURE = ("sov", "rank", "gsc", "health", "speed", "ai", "aiserp")
OK, SKIP, ERROR = "ok", "skip", "error"


def _age_hours(iso: str | None, now: datetime.datetime) -> float | None:
    if not iso:
        return None
    try:
        return (now - datetime.datetime.fromisoformat(str(iso)[:19])).total_seconds() / 3600
    except Exception:
        return None


def plan_steps(last: dict, now: datetime.datetime, content_n: int) -> dict:
    """ตัดสินว่าขั้นไหนรัน/ข้าม จากเวลาที่ทำครั้งล่าสุด — คืน {step: (run?, เหตุผล)}"""
    out = {}
    for step in MEASURE:
        age = _age_hours(last.get(step), now)
        limit = FRESH_COSTLY_DAYS * 24 if step in COSTLY else FRESH_CHEAP_HOURS
        if age is not None and age < limit:
            out[step] = (False, f"เพิ่งทำเมื่อ {age:.0f} ชม.ก่อน" if age >= 1 else "เพิ่งทำไปไม่ถึงชั่วโมง")
        else:
            out[step] = (True, "")
    out["content"] = (content_n > 0, "" if content_n > 0 else "ตั้งไว้ 0 ชิ้น (วัดอย่างเดียว)")
    out["report"] = (True, "")
    out["notify"] = (True, "")
    return out


def summarize_step(step: str, res) -> str:
    """ข้อความสั้น ๆ ต่อขั้นจากผลที่แต่ละฟังก์ชันคืน — รูปแบบผลต่างกันตามโมดูลเดิม"""
    r = res or {}
    try:
        if step == "sov":
            return f"SoV {r.get('brand_hits', 0)}/{r.get('questions', 0)} คำถาม"
        if step == "rank":
            return f"ติดอันดับ {r.get('ranked', 0)}/{r.get('checked', r.get('questions', 0))}" + (f" เฉลี่ย #{r['avg_position']:.1f}" if r.get("avg_position") else "")
        if step == "gsc":
            if not r.get("linked"):
                return "ยังไม่ได้เชื่อม — " + (r.get("reason") or "")[:60]
            idx = r.get("index") or {}
            return f"index {idx.get('indexed', 0)}/{idx.get('inspected', 0)}" + (" · ส่ง sitemap ให้แล้ว" if (r.get("sitemap") or {}).get("just_submitted") else "")
        if step == "health":
            return "ผ่านทุกข้อ" if r.get("ok") else f"ไม่ผ่าน {r.get('n_fail', 0)} ข้อ: " + ", ".join(c["label"] for c in r.get("checks", []) if c.get("status") == "fail")[:120]
        if step == "speed":
            m = (r.get("mobile") or {})
            return ("มือถือ " + str((m.get("scores") or {}).get("performance"))) if not m.get("error") else "สแกนไม่ได้ — " + m["error"][:60]
        if step == "ai":
            if r.get("asked"):
                return f"ถูกพูดถึง {r.get('rate', 0)}% จาก {r['asked']} คำตอบ · ${r.get('cost_usd', 0):.2f}"
            return "ข้าม — ยังไม่มีคีย์ AI" if not any(v.get("errors") for v in (r.get("by_engine") or {}).values()) else "ถามไม่สำเร็จ"
        if step == "aiserp":
            if r.get("skip"):
                return r["skip"][:80]
            ov, md = r["by_kind"]["overview"], r["by_kind"]["mode"]
            return f"AI Overview {ov['cited'] + ov['named']}/{ov['shown']} · AI Mode {md['cited'] + md['named']}/{md['shown']}"
        if step == "content":
            made = [x for x in r if x.get("id")]
            quota = [x for x in r if x.get("status") == "quota"]
            if not made and not quota:
                return "ไม่มีช่องว่างที่ยังไม่มีคอนเทนต์"
            pub = sum(1 for x in made if x.get("status") == "published")
            return f"สร้าง {len(made)} ชิ้น" + (f" เผยแพร่ {pub}" if pub else " (ร่าง)") + (" · โควตาแพ็กเกจเต็ม" if quota else "")
        if step == "report":
            return str(r)
        if step == "notify":
            return "แจ้งแล้ว"
    except Exception:
        pass
    return "เสร็จ"


def run(brand: dict, actions: dict, last: dict, content_n: int = 1, mode: str = "draft", now: datetime.datetime | None = None) -> dict:
    """รันทุกขั้นตามแผน — actions = {step: callable} (content รับ (brand, n, mode), report รับ (brand, log), notify รับ (brand, log))"""
    now = now or datetime.datetime.now()
    plan = plan_steps(last, now, content_n)
    log = {"brand_id": brand["id"], "started_at": now.isoformat(timespec="seconds"), "mode": mode, "content_n": content_n, "steps": []}
    for step, label in STEPS:
        do, why = plan.get(step, (True, ""))
        entry = {"step": step, "label": label, "status": SKIP, "detail": why, "seconds": 0}
        if do and step in actions:
            t0 = time.monotonic()
            try:
                if step == "content":
                    res = actions[step](brand, content_n, mode)
                elif step in ("report", "notify"):
                    res = actions[step](brand, log)
                else:
                    res = actions[step](brand["id"])
                entry.update(status=OK, detail=summarize_step(step, res))
                if step == "content":
                    log["content"] = res
            except Exception as e:
                entry.update(status=ERROR, detail=f"{type(e).__name__}: {str(e)[:160]}")
            entry["seconds"] = round(time.monotonic() - t0, 1)
        elif do:
            entry["detail"] = "ไม่มีฟังก์ชันของขั้นนี้"
        log["steps"].append(entry)
    log["finished_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    log["errors"] = sum(1 for s in log["steps"] if s["status"] == ERROR)
    log["ok"] = log["errors"] == 0
    log["summary"] = " · ".join(f"{s['label']}: {s['detail']}" for s in log["steps"] if s["status"] == OK and s["step"] not in ("notify",))[:600]
    return log


def notify_text(brand: dict, log: dict) -> str:
    done = [s for s in log["steps"] if s["status"] == OK and s["step"] not in ("notify", "report")]
    errs = log.get("errors", 0)
    head = f"🤖 Autopilot {brand['name']}: ทำ {len(done)} ขั้น" + (f" · ผิดพลาด {errs}" if errs else "")
    bits = [s["detail"] for s in done if s["step"] in ("sov", "gsc", "content", "health")]
    return (head + " — " + " · ".join(bits))[:300]
