"""ตรวจเว็บแบบเร็ว (stage 04A, flag snapshot_v2) — ทำเบื้องหลัง เก็บสถานะจริงทีละขั้นลง leads.snapshot_json

ใช้ตัวตรวจเดิม (site_health.run_checks) ไม่สร้าง engine ใหม่ · จำกัด: อ่านหน้าเว็บไม่เกิน 5 หน้า (MAX_CRAWL เดิม),
timeout ต่อ request และเวลารวมทั้งการตรวจ (ตั้งได้ที่ settings snapshot_budget_s / snapshot_timeout_s)

สถานะ: queued → running → completed | partial (หมดเวลา ตรวจได้บางส่วน) | failed (ทำไม่สำเร็จ/ค้าง)
ขั้น: pending → running → done | skipped (หมดเวลา ไม่ได้ตรวจ) · ไม่มีเปอร์เซ็นต์ปลอม
ข้อจำกัด: งานอยู่ใน thread ของ process เดียว — รีสตาร์ตระหว่างทำ = ค้าง → เกิน STALE_S ถือว่าล้ม ให้เริ่มใหม่ได้
(คิวงานถาวรมาใน stage 10)
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time

from . import db

STEPS = [
    ("home", "อ่านหน้าแรก"),
    ("basics", "ตรวจหน้าที่ไม่มีอยู่จริงและ www"),
    ("robots", "ตรวจ robots.txt และการเข้าถึงของบอท AI"),
    ("files", "ตรวจ llms.txt และ sitemap"),
    ("pages", "อ่านหน้าอื่นอีกไม่เกิน 4 หน้า"),
    ("index", "ตรวจการเก็บเข้า index ของ Google"),
]
LABEL = dict(STEPS)
BUDGET_S = 90            # เวลารวมสูงสุดต่อการตรวจ 1 ครั้ง
TIMEOUT_S = 10           # ต่อ request
STALE_S = 5 * 60         # running/queued นานเกินนี้ = ค้าง (process ตาย) → ล้ม ให้เริ่มใหม่
RUN_INLINE = False       # เทสต์ตั้ง True
DONE_STATES = ("completed", "partial")
_LOCK = threading.Lock()


def _setting_int(key: str, default: int) -> int:
    try:
        v = int(db.get_setting(key) or default)
        return v if v > 0 else default
    except Exception:
        return default


def budget_s() -> int:
    return _setting_int("snapshot_budget_s", BUDGET_S)


def timeout_s() -> int:
    return _setting_int("snapshot_timeout_s", TIMEOUT_S)


def get(lead) -> dict:
    try:
        return json.loads(lead["snapshot_json"] or "{}") if lead and lead["snapshot_json"] else {}
    except Exception:
        return {}


def _save(lead_id: int, d: dict) -> None:
    db.update_lead(lead_id, snapshot_json=json.dumps(d, ensure_ascii=False))


def _age_s(d: dict) -> float:
    try:
        return (dt.datetime.now() - dt.datetime.fromisoformat(d.get("updated_at") or d.get("queued_at") or "")).total_seconds()
    except Exception:
        return 1e9


def view(lead) -> dict:
    """สถานะสำหรับแสดงผล — งานที่ค้างเกิน STALE_S แสดงเป็นล้ม (ไม่หลอกว่ายังทำอยู่)"""
    d = get(lead)
    if d.get("state") in ("queued", "running") and _age_s(d) > STALE_S:
        d = {**d, "state": "failed", "error": "การตรวจหยุดกลางทาง (ระบบอาจรีสตาร์ต) กรุณาเริ่มตรวจใหม่"}
    return d


def active(lead) -> bool:
    return view(lead).get("state") in ("queued", "running")


def start(lead_id: int, site: str, ip: str = "") -> str:
    """เริ่มตรวจเบื้องหลัง · กดซ้ำระหว่างกำลังตรวจ = ไม่เริ่มซ้ำ (คืน "running") · คืน "started" """
    with _LOCK:                                            # กันสอง request เริ่มงานของลีดเดียวกันพร้อมกัน (ใน process เดียว)
        lead = db.get_lead(lead_id)
        if active(lead):
            return "running"
        now = db.now()
        d = {"state": "queued", "site": site, "queued_at": now, "updated_at": now, "budget_s": budget_s(),
             "steps": [{"key": k, "label": l, "state": "pending"} for k, l in STEPS]}
        _save(lead_id, d)
    job = lambda: run(lead_id, site, ip)
    if RUN_INLINE:
        job()
    else:
        threading.Thread(target=job, daemon=True, name=f"snapshot-{lead_id}").start()
    return "started"


def run(lead_id: int, site: str, ip: str = "") -> dict:
    from . import pipeline, site_health
    d = get(db.get_lead(lead_id))
    d.update(state="running", started_at=db.now(), updated_at=db.now())
    _save(lead_id, d)
    seen: list[str] = []

    def progress(key: str) -> None:
        for s in d["steps"]:
            if s["key"] in seen and s["state"] == "running":
                s["state"] = "done"
            if s["key"] == key:
                s["state"] = "running"
        seen.append(key)
        d["updated_at"] = db.now()
        _save(lead_id, d)

    t0 = time.monotonic()
    try:
        report = site_health.run_checks(site, progress=progress, timeout=timeout_s(), deadline=t0 + d.get("budget_s", BUDGET_S))
    except Exception as e:
        d.update(state="failed", error=f"ตรวจไม่สำเร็จ ({type(e).__name__}) ลองใหม่อีกครั้ง หรือเช็กว่าเว็บเปิดได้", updated_at=db.now())
        for s in d["steps"]:
            if s["state"] == "running":
                s["state"] = "failed"
        _save(lead_id, d)
        return d
    skipped = set(report.get("skipped") or [])
    for s in d["steps"]:
        if s["key"] in skipped:
            s["state"] = "skipped"
        elif s["state"] in ("running", "pending") and s["key"] in seen:
            s["state"] = "done"
        elif s["state"] == "pending":
            s["state"] = "skipped"
    home = next((c for c in report.get("checks", []) if c.get("key") == "home"), {})
    d["home_ok"] = home.get("status") == "ok"
    d["elapsed_s"] = round(time.monotonic() - t0, 1)
    d["state"] = "partial" if skipped else "completed"
    d["finished_at"] = d["updated_at"] = db.now()
    pipeline.save_lead_audit(lead_id, report, ip)
    _save(lead_id, d)
    try:
        from . import deep_audit
        deep_audit.start(lead_id)                         # รายงานเต็มชั้น 1 แบบเดิม (ไม่เปลี่ยนสิทธิ์/เพดานเดิม)
    except Exception:
        pass
    return d
