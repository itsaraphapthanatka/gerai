"""ข้อมูลทดสอบ UAT (stage 19) — สร้าง DB SQLite ใหม่ที่แยกขาด ข้อมูลสมมติทั้งหมด · ไม่ต่อเน็ต · ไม่ส่งอะไรจริง

ใช้:
  .venv/Scripts/python.exe scripts/uat_seed.py --db C:/temp/jorai-uat/uat.db        # สร้างใหม่ (ไฟล์ต้องยังไม่มี)
  .venv/Scripts/python.exe scripts/uat_seed.py --db C:/temp/jorai-uat/uat.db --serve  # เปิดเซิร์ฟเวอร์ 127.0.0.1:8790 บน DB นั้น
รีเซ็ต = ลบโฟลเดอร์แล้วสร้างใหม่ · บัญชีและรหัสผ่านสุ่มเขียนไว้ที่ <โฟลเดอร์ของ db>/uat_logins.txt (ไม่อยู่ใน repo)
กันพลาด: ไม่ยอมทำงานถ้ามี DATABASE_URL หรือไฟล์ db มีอยู่แล้ว · ฟังได้แค่ 127.0.0.1"""
from __future__ import annotations
import argparse
import json
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAFE_ENV = {"GEO_AUTORUN": "0", "GEO_DEEP_AUDIT": "0", "AI_BASE_URL": "http://127.0.0.1:9", "LITELLM_BASE_URL": "", "PYTHONUTF8": "1",
            "SERPER_API_KEY": "", "PUBLIC_BASE_URL": "http://127.0.0.1:8790"}
ROLES = (("owner", "owner@uat.test", None), ("sales", "sales@uat.test", "sales"), ("marketing", "marketing@uat.test", "marketing"),
         ("finance", "finance@uat.test", "finance"), ("delivery", "delivery@uat.test", "manager"))


def _env(db_path: Path) -> None:
    if os.getenv("DATABASE_URL"):
        sys.exit("หยุด: มี DATABASE_URL อยู่ — UAT ใช้ SQLite แยกเท่านั้น")
    os.environ.update(SAFE_ENV)
    os.environ["GEO_DB_PATH"] = str(db_path)
    for k in list(os.environ):
        if k.startswith("GEO_FLAG_"):
            os.environ.pop(k)
    sys.path.insert(0, str(ROOT))


def seed(db_path: Path) -> dict:
    import bcrypt
    import app.db as db
    import app.flags as F
    import app.mailer as mailer
    import app.line_oa as LO
    import app.pipeline as P
    import app.success as S
    import app.business_context as BC
    mailer.send = lambda *a, **k: None                       # ระหว่างสร้างข้อมูล: ไม่ส่งอะไร
    LO._post = lambda *a, **k: True
    P.alert_admins = lambda *a, **k: None
    P.RUN_INLINE = True; S.RUN_INLINE = True
    S.HOOKS["autopilot"] = lambda bid: None; S.HOOKS["health"] = lambda bid: None
    db.init_db()
    logins = {}
    pw = lambda: secrets.token_urlsafe(10)
    h = lambda p: bcrypt.hashpw(p.encode(), bcrypt.gensalt(rounds=6)).decode()
    for role, email, staff_role in ROLES:
        p = pw()
        tid = db.create_tenant(email, h(p), f"UAT {role}", is_admin=True)
        if staff_role:
            db.set_staff(tid, staff_role)
        logins[role] = {"email": email, "password": p}
    for f in F.FLAGS:
        F.set_enabled(f, f not in ("scheduler_lease",))
    db.set_setting("appt_slots", json.dumps({"days": [0, 1, 2, 3, 4], "times": ["10:00", "14:00"], "lead_hours": 2, "horizon_days": 10}))
    db.set_setting("budget_ai_usd_daily", "0")              # UAT: ทดสอบ AI แบบเสียเงินถูกบล็อกด้วยงบ 0 เสมอ
    deep = {"state": "done", "at": db.now(), "brand": "UAT Warehouse", "keywords": ["โกดังให้เช่า บางนา"], "crawl": {"n": 5},
            "pages": [{"url": "https://uat-buyer.example-uat.test/", "title": "UAT Warehouse หน้าแรก"}],
            "issues": [{"key": "title_missing", "severity": "Error", "label": "หน้าไม่มีชื่อหน้า", "fix": "ใส่ชื่อหน้า", "urls": ["https://uat-buyer.example-uat.test/contact"]}],
            "health": {"score": 80, "errors": 1}, "ranking": {"rows": [], "competitors": [], "n": 0, "ok": 0, "page1": 0}}
    audit = json.dumps({"ok": True, "checks": [{"key": "home", "label": "หน้าแรก", "status": "ok"}]})
    # ผู้เยี่ยมชม: รายงานพร้อมเปิด
    v = db.add_lead("https://uat-visitor.example-uat.test", "uat-visitor.example-uat.test", source="audit", ip="127.0.0.1")
    db.update_lead(v, token="UATVISIT0001", audit_json=audit, audit_at=db.now(), deep_json=json.dumps(deep, ensure_ascii=False))
    # ผู้ซื้อ: จ่ายแล้ว → พื้นที่งาน
    b = db.add_lead("https://uat-buyer.example-uat.test", "uat-buyer.example-uat.test", source="audit", email="buyer@uat.test")
    db.update_lead(b, token="UATBUYER0001", audit_json=audit, audit_at=db.now(), deep_json=json.dumps(deep, ensure_ascii=False))
    q = P.create_quote(db.get_lead(b), "pro")
    db.update_quote(q["id"] if hasattr(q, "keys") else q, status="accepted")
    P.confirm_payment(db.list_quotes(b)[0], "uat")
    qb = db.list_quotes(b)[0]
    p = pw()
    with db.get_conn() as c:
        c.execute(db.q("UPDATE tenants SET password_hash=? WHERE id=?"), (h(p), qb["tenant_id"]))
    logins["buyer"] = {"email": db.get_tenant(qb["tenant_id"])["email"], "password": p}
    db.create_content_item(qb["brand_id"], None, "th", "โกดังให้เช่าบางนา", "โกดังให้เช่าบางนา", "desc",
                           ("โกดังให้เช่าบางนา พื้นที่หลายขนาด " * 15) + " [ติดต่อเรา](/contact)", "", "uat")
    # ขาย: ลีดหลายสถานะ
    for i, st in enumerate(("new", "contacted", "replied", "call", "lost")):
        lid = db.add_lead(f"https://uat-lead{i}.example-uat.test", f"uat-lead{i}.example-uat.test", source="prospect", email=f"lead{i}@uat.test")
        db.update_lead(lid, status=st, audit_json=audit, audit_at=db.now())
    unsub = db.add_lead("https://uat-unsub.example-uat.test", "uat-unsub.example-uat.test", source="prospect", email="unsub@uat.test")
    db.update_lead(unsub, status="unsubscribed", unsub_at=db.now())
    # พันธมิตร
    pid = db.add_partner(status="active", kind="agency", name="UAT Partner", company="UAT Agency", email="partner@uat.test",
                         code="UATP01", token=secrets.token_urlsafe(16))
    db.update_lead(b, partner_id=pid)
    logins["partner"] = {"url": f"/partner/{db.get_partner(pid)['token']}"}
    logins["visitor"] = {"url": "/audit/UATVISIT0001"}
    return logins


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--serve", action="store_true")
    a = ap.parse_args()
    dbp = Path(a.db).resolve()
    if ROOT in dbp.parents:
        sys.exit("หยุด: เก็บ DB UAT นอก repo")
    _env(dbp)
    if a.serve:
        if not dbp.exists():
            sys.exit("ยังไม่มี DB — สร้างก่อนด้วย --db โดยไม่ใส่ --serve")
        import uvicorn
        from app.main import app
        uvicorn.run(app, host="127.0.0.1", port=8790, log_level="warning")
        return 0
    if dbp.exists():
        sys.exit("หยุด: ไฟล์ DB มีอยู่แล้ว — รีเซ็ตด้วยการลบโฟลเดอร์ก่อน")
    dbp.parent.mkdir(parents=True, exist_ok=True)
    logins = seed(dbp)
    out = dbp.parent / "uat_logins.txt"
    out.write_text(json.dumps(logins, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"สร้าง UAT DB แล้ว: {dbp}\nบัญชี/รหัสผ่าน: {out}\nเปิดเซิร์ฟเวอร์: scripts/uat_seed.py --db {dbp} --serve  → http://127.0.0.1:8790")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
