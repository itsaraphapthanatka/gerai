"""ทดสอบออฟฟิศเอเจนต์: สถานะ "กำลังทำงาน" จาก working()/track() (เริ่ม-จบ นับรอบ ค้างนานถือว่าว่าง) · เหตุการณ์วันนี้จาก lead_events/decisions/posts
แปลงเป็นตัวละคร+ชนิดถูกต้อง (auto → เอเจนต์ · อีเมลคน → เจ้าของ · customer → ลูกค้า) · กระดานขั้นดีล/ตัวเลขวันนี้ · ผู้มาเยือนตามสถานะ
· รอบถัดไปตามตารางงาน · หน้า /admin/office และ state.json (แอดมินเท่านั้น ไม่แคช) · ฮุคในงานจริง (ตรวจเว็บ → CTO นับรอบ)"""
import os, tempfile, json, datetime as dt
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke7-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ.setdefault("GEO_DEEP_AUDIT", "0")
os.environ.setdefault("GEO_CS", "0")   # ไม่สั่ง Autopilot จริงตอนยืนยันยอดในเทสต์นี้   # ไม่ไต่เว็บจริงเบื้องหลังในเทสต์นี้
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.office as O, app.pipeline as P, app.site_health as sh, app.ai_client as ai, app.mailer as mailer, app.leads as L
from app.main import app

def rep(*fails, ok=10):
    checks = [{"key": k, "label": f"ข้อ {k}", "status": "fail", "detail": ""} for k in fails]
    return {"ok": not checks, "n_fail": len(checks), "n_ok": ok, "checks": checks + [{"key": "x", "label": "ผ่าน", "status": "ok"}] * ok}
db.init_db()
sh.run_checks = lambda site, **kw: rep("csr", "connector")
ai._read_setting = lambda k: None
ai._chat = lambda m, max_tokens=2048, timeout=120: (_ for _ in ()).throw(RuntimeError("no model"))
mailer.config = lambda: None
P.RUN_INLINE = True
c = TestClient(app)
admin = "admin@test.local"; db.create_tenant(admin, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Op", is_admin=True)
a = TestClient(app); a.post("/login", data={"email": admin, "password": "pw123456"})

# ---- สถานะกำลังทำงาน ----
assert all(v["status"] == "idle" and v["runs_today"] == 0 for v in O.live().values())
with O.working("cmo", "รอบการตลาด"):
    lv = O.live()["cmo"]; assert lv["status"] == "working" and lv["label"] == "รอบการตลาด" and lv["since"]
lv = O.live()["cmo"]; assert lv["status"] == "idle" and lv["runs_today"] == 1 and lv["last"]
@O.track("delivery", lambda n: f"งาน {n}")
def job(n):
    assert O.live()["delivery"]["status"] == "working"
    if n == 2:
        raise ValueError("boom")
    return n * 10
assert job(1) == 10
try:
    job(2); raise SystemExit("ต้องโยน error ของงานจริงออกมา")
except ValueError:
    pass
lv = O.live()["delivery"]; assert lv["status"] == "idle" and lv["runs_today"] == 2 and lv["label"] == "งาน 2"      # จบแม้ล้ม · ป้ายงานล่าสุด
d = O._load(); d["cto"] = {"status": "working", "since": (dt.datetime.now() - dt.timedelta(minutes=O.STALE_MIN + 5)).isoformat(timespec="seconds"), "label": "ค้าง", "day": O.today_bkk(), "runs_today": 3}
O._save(d); assert O.live()["cto"]["status"] == "idle" and O.live()["cto"]["runs_today"] == 3                      # ค้างเกินกำหนด = ว่าง
d["cto"]["day"] = "2000-01-01"; O._save(d); assert O.live()["cto"]["runs_today"] == 0                                # นับเฉพาะวันนี้
print("live: เริ่ม/จบ · นับรอบ · จบแม้ล้ม · ค้างนานถือว่าว่าง · นับเฉพาะวันนี้ OK")

# ---- ตารางงาน ----
mon = dt.datetime(2026, 10, 26, 9, 5, tzinfo=O.BKK)          # จันทร์ 09:05 → CTO รอบถัดไป 09:15 ซิงค์ Search Console
n = O.next_run("cto", mon); assert n["at"].startswith("2026-10-26T09:15") and "Search Console" in n["label"], n
n = O.next_run("prospecting", mon); assert n["at"].startswith("2026-10-27T06:30"), n
n = O.next_run("delivery", dt.datetime(2026, 10, 27, 3, 0, tzinfo=O.BKK)); assert n["at"].startswith("2026-10-27T07:30"), n   # อังคาร 03:00 → อีเมล 07:30 (Autopilot 02:00 ผ่านไปแล้ว)
assert O.next_run("hop") is None and O.next_run("owner") is None
print("schedule: รอบถัดไปตาม crontab เวลาไทย OK")

# ---- เหตุการณ์ + ผู้มาเยือน + กระดาน ----
L.AUDITS_PER_IP_PER_DAY = 50
r = c.post("/audit", data={"url": "shop-a.co.th"}); lead_a = db.list_leads(None, 10)[0]; assert lead_a["domain"] == "shop-a.co.th"
assert O.live()["cto"]["runs_today"] == 1 and "shop-a.co.th" in O.live()["cto"]["label"]                       # ฮุคตรวจเว็บ → CTO
lid_b = db.add_lead("https://shop-b.co.th", "shop-b.co.th", source="import", email="contact@shop-b.co.th")
db.update_lead(lid_b, status="contacted", email="contact@shop-b.co.th")
db.add_lead_event(lid_b, "contacted", "ส่งอีเมลถึง contact@shop-b.co.th แล้ว", "auto")
db.add_lead_event(lid_b, "opened", "", "customer")
db.add_lead_event(lead_a["id"], "contacted", "ส่งเองจากหน้าลูกค้า", admin)
db.add_lead_event(lid_b, "prospect", "คำค้น: x", "prospecting")
did1 = db.add_decision("ceo", "outreach", "ทัก shop-b.co.th (pro)", "...", {"body": "x"}, lead_id=lid_b)
db.set_decision(did1, "auto", "ceo", "ส่งอีเมลแล้ว")
did2 = db.add_decision("cfo", "quote", "ใบเสนอราคา shop-a.co.th", "...", {"quote_id": 1}, lead_id=lead_a["id"])
did3 = db.add_decision("cmo", "post", "โพสต์ชวนตรวจ", "...", {"post_id": 1})
db.set_decision(did3, "executed", admin, "โพสต์แล้ว")
did4 = db.add_decision("ceo", "lead_review", "ปิด shop-x", "...", {"action": "decline"})
db.set_decision(did4, "rejected", admin, "ยังอยากลอง")
db.add_decision("ceo", "digest", "สรุปเช้า", "...", {}, status="auto")
pid = db.add_post("facebook", "audit_cta", None, "ชวนตรวจเว็บฟรี", "...", None, status="published"); db.update_post(pid, published_at=db.now())
st = O.state()
by = {e["id"]: e for e in st["events"]}
assert by[f"dec:{did1}:auto"]["actor"] == "ceo" and by[f"dec:{did1}:auto"]["type"] == "auto" and by[f"dec:{did1}:auto"]["kind"] == "outreach" and by[f"dec:{did1}:auto"]["lead"]["domain"] == "shop-b.co.th"
assert f"dec:{did1}:proposed" not in by                                                                        # ทำเองทันที ไม่มีขั้นเสนอ
assert by[f"dec:{did2}:proposed"]["type"] == "propose" and by[f"dec:{did2}:proposed"]["actor"] == "cfo"
assert by[f"dec:{did3}:approve"]["actor"] == "owner" and by[f"dec:{did3}:approve"]["to"] == "cmo" and by[f"dec:{did3}:proposed"]["actor"] == "cmo"
assert by[f"dec:{did4}:reject"]["actor"] == "owner" and by[f"dec:{did4}:reject"]["type"] == "reject"
assert any(e["type"] == "digest" and e["actor"] == "ceo" for e in st["events"])
assert by[f"post:{pid}"]["actor"] == "cmo" and by[f"post:{pid}"]["channel"] == "facebook"
evs = [e for e in st["events"] if e["id"].startswith("ev:")]
kinds = {(e["actor"], e["type"]) for e in evs}
assert ("cto", "audit") in kinds and ("ceo", "outreach") in kinds and ("owner", "outreach") in kinds and ("lead", "opened") in kinds and ("prospecting", "found") in kinds, kinds
assert all(e["at"] <= st["events"][i + 1]["at"] for i, e in enumerate(st["events"][:-1]))                      # เรียงตามเวลา
b = st["board"]; assert b["pending"] == 1 and b["audits_today"] >= 1 and b["outreach_today"] == 2 and b["posts_today"] == 1 and b["prospects_today"] == 1
assert b["funnel"]["contacted"] >= 1 and b["kpi"]["phase"] in (1, 2, 3) and len(b["kpi"]["rows"]) == 6 and b["won_today"] == 0
vs = {v["domain"]: v for v in st["visitors"]}; assert vs["shop-b.co.th"]["status"] == "contacted" and "shop-a.co.th" in vs
db.update_lead(lid_b, status="lost"); assert "shop-b.co.th" not in {v["domain"] for v in O.state()["visitors"]}   # ไม่สนใจ = ออกจากล็อบบี้
ids = [x["id"] for x in st["actors"]]; assert ids == [x["id"] for x in O.ACTORS] and all("next" in x and "status" in x for x in st["actors"])
assert next(x for x in st["actors"] if x["id"] == "hop")["name"] == "LINE OA เจอ.AI"                                   # บอทล็อบบี้ใช้ชื่อ OA จริง (ค่าเริ่มต้น เจอ.AI)
assert st["tz"][0] in "+-" and len(st["tz"]) == 6 and st["mode"]
print("state: เหตุการณ์แยกตัวละคร/ชนิดถูก · กระดานวันนี้ · ผู้มาเยือนตามสถานะ · ตัวละครครบ OK")

# ---- หน้าเว็บ ----
r = c.get("/admin/office", follow_redirects=False); assert r.status_code == 303                                   # ไม่ล็อกอิน → ไปหน้า login
r = c.get("/admin/office/state.json"); assert r.status_code == 401
r = a.get("/admin/office"); assert r.status_code == 200 and "ออฟฟิศ" in r.text and "three.min.js" in r.text and "/admin/office/state.json" in r.text and "{% raw %}" not in r.text
r = a.get("/admin/office/state.json"); assert r.status_code == 200 and r.headers.get("cache-control") == "no-store"
j = r.json(); assert j["actors"][0]["id"] == "owner" and isinstance(j["events"], list) and j["board"]["pending"] == 1
r = a.get("/admin/decisions"); assert "/admin/office" in r.text                                                   # เมนู
print("page: /admin/office + state.json (แอดมินเท่านั้น · no-store · เมนู) OK")
print("ALL OK")
