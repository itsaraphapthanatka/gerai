"""ทดสอบ stage 04A: ตรวจเว็บแบบเร็วเบื้องหลัง (flag snapshot_v2) · ไม่ยิงเครือข่าย · DB ชั่วคราว
กรณี: flag ปิด = แบบเดิม · สำเร็จ · หมดเวลา (partial) · ล้ม + ตรวจใหม่ · ค้าง (stale) · กดซ้ำ · เปิดเว็บไม่ได้ · URL ภายใน · โควตา · deadline จริงใน run_checks"""
import os, re, json, tempfile, time, datetime as dt
os.environ.pop("DATABASE_URL", None)
os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["AI_BASE_URL"] = "http://127.0.0.1:9"
os.environ["GEO_DEEP_AUDIT"] = "0"
for k in list(os.environ):
    if k.startswith("GEO_FLAG_"):
        os.environ.pop(k)
import httpx
from fastapi.testclient import TestClient
import app.db as db
import app.snapshot as S
import app.site_health as sh
import app.leads as L
from app.main import app

db.init_db()
S.RUN_INLINE = True
_real_run_checks = sh.run_checks
REPORT = {"ok": True, "n_fail": 0, "n_ok": 2, "checks": [{"key": "home", "label": "หน้าแรก", "status": "ok"}, {"key": "x", "label": "ผ่าน", "status": "ok"}]}
calls = []

def fake(site, progress=None, timeout=20, deadline=None, **kw):
    calls.append({"site": site, "timeout": timeout, "deadline": deadline})
    for k, _ in S.STEPS:
        progress and progress(k)
    return dict(REPORT)
sh.run_checks = fake
c = TestClient(app)
post = lambda url, ip="1.2.3.4", cl=c: cl.post("/audit", data={"url": url}, headers={"x-forwarded-for": ip})
tok = lambda r: str(r.url).rstrip("/").rsplit("/", 1)[-1]

# ---------- flag ปิด: แบบเดิมทุกอย่าง (ไม่มี snapshot_json) ----------
sh.run_checks = lambda s: dict(REPORT)                     # แบบเดิมเรียกด้วย URL อย่างเดียว
r = post("legacy-shop.co.th")
lead = db.get_lead_by_token(tok(r))
assert lead["audit_json"] and not lead["snapshot_json"] and "กำลังตรวจ" not in r.text
sh.run_checks = fake

# ---------- flag เปิด: สำเร็จ ----------
os.environ["GEO_FLAG_SNAPSHOT_V2"] = "1"
r = post("good-shop.co.th")
lead = db.get_lead_by_token(tok(r))
snap = json.loads(lead["snapshot_json"])
assert snap["state"] == "completed" and snap["home_ok"] and all(s["state"] == "done" for s in snap["steps"]), snap
assert lead["audit_json"] and lead["score"] is not None
assert calls[-1]["timeout"] == S.TIMEOUT_S and calls[-1]["deadline"] is not None          # มีเพดานเวลาทั้งต่อ request และรวม
assert "ผลตรวจ" in r.text and "กำลังตรวจ" not in r.text                                    # เสร็จแล้ว = หน้าผลเดิม

# ---------- กำลังตรวจ: หน้าสถานะจริง ไม่มีเปอร์เซ็นต์ รีเฟรชเองไม่ต้องใช้ JS ----------
lid = lead["id"]
running = {"state": "running", "site": "https://good-shop.co.th", "queued_at": db.now(), "updated_at": db.now(), "budget_s": 90,
           "steps": [{"key": k, "label": l, "state": st} for (k, l), st in zip(S.STEPS, ["done", "running", "pending", "pending", "pending", "pending"])]}
db.update_lead(lid, snapshot_json=json.dumps(running, ensure_ascii=False))
r = c.get(f"/audit/{lead['token']}")
assert r.status_code == 200 and "กำลังตรวจ good-shop.co.th" in r.text and '<meta http-equiv="refresh" content="3">' in r.text
assert "noindex" in r.headers.get("x-robots-tag", "") and r.headers.get("cache-control") == "no-store"
assert not re.search(r"\d+\s*%", re.sub(r"(?s)<style.*?</style>", "", r.text))                # ไม่มี % ความคืบหน้าปลอม
assert "เสร็จ" in r.text and "กำลังทำ" in r.text and "รอ" in r.text

# กดซ้ำระหว่างกำลังตรวจ = ไม่เริ่มงานซ้ำ
n = len(calls)
assert S.start(lid, "https://good-shop.co.th") == "running" and len(calls) == n

# ค้างเกินเวลา (process ตาย) = แสดงว่าล้ม ไม่หลอกว่ายังทำอยู่ และเริ่มใหม่ได้
old = (dt.datetime.now() - dt.timedelta(seconds=S.STALE_S + 30)).isoformat(timespec="seconds")
db.update_lead(lid, snapshot_json=json.dumps({**running, "updated_at": old, "queued_at": old}, ensure_ascii=False))
r = c.get(f"/audit/{lead['token']}")
assert "ตรวจ good-shop.co.th ไม่สำเร็จ" in r.text and "ระบบอาจรีสตาร์ต" in r.text and 'action="/audit"' in r.text
assert '<meta http-equiv="refresh"' not in r.text
assert S.start(lid, "https://good-shop.co.th") == "started" and len(calls) == n + 1

# ---------- หมดเวลา: partial · ส่วนที่ไม่ได้ตรวจไม่นับเป็นไม่ผ่าน ----------
def slow(site, progress=None, timeout=20, deadline=None, **kw):
    progress("home"); progress("basics")
    return {**REPORT, "partial": True, "skipped": ["robots", "files", "pages", "index"]}
sh.run_checks = slow
r = post("slow-shop.co.th")
lead = db.get_lead_by_token(tok(r)); snap = json.loads(lead["snapshot_json"])
assert snap["state"] == "partial"
assert [s["state"] for s in snap["steps"]] == ["done", "done", "skipped", "skipped", "skipped", "skipped"], snap["steps"]
assert "ตรวจได้บางส่วน" in r.text and "ตรวจ llms.txt และ sitemap" in r.text

# ---------- เปิดหน้าแรกไม่ได้: บอกตรง ๆ ----------
sh.run_checks = lambda site, progress=None, **kw: (progress("home"), {"ok": False, "n_fail": 1, "n_ok": 0,
    "checks": [{"key": "home", "label": "หน้าแรก", "status": "fail", "detail": "ตอบ 0"}]})[1]
r = post("down-shop.co.th")
assert json.loads(db.get_lead_by_token(tok(r))["snapshot_json"])["home_ok"] is False
assert "เปิดหน้าแรกของเว็บนี้ไม่ได้ระหว่างตรวจ" in r.text

# ---------- ตัวตรวจพัง: failed + ปุ่มตรวจใหม่ แล้วตรวจใหม่ได้จริง ----------
def boom(site, **kw):
    kw["progress"]("home"); raise TimeoutError("x")
sh.run_checks = boom
r = post("broken-shop.co.th", ip="7.7.7.7")
lead = db.get_lead_by_token(tok(r)); snap = json.loads(lead["snapshot_json"])
assert snap["state"] == "failed" and snap["steps"][0]["state"] == "failed" and "TimeoutError" in snap["error"]
assert "ตรวจใหม่อีกครั้ง" in r.text and 'value="https://broken-shop.co.th"' in r.text
sh.run_checks = fake
r = post("broken-shop.co.th", ip="7.7.7.7")                                                 # คนเดิมกดตรวจใหม่ → ลีดเดิม สำเร็จ
assert tok(r) == lead["token"] and json.loads(db.get_lead(lead["id"])["snapshot_json"])["state"] == "completed"

# ---------- URL ภายใน และโควตา ----------
r = post("169.254.169.254")
assert "กรอกที่อยู่เว็บให้ถูกต้อง" in r.text
since = (dt.datetime.now() - dt.timedelta(hours=24)).isoformat(timespec="seconds")
before = db.count_audits_since("8.8.4.4", since)
S.RUN_INLINE = False; started = []; _Thread = S.threading.Thread
S.threading.Thread = lambda target, **kw: type("T", (), {"start": lambda self: started.append(target)})()   # ไม่รันจริง = ค้างในคิว
try:
    r = post("queued-shop.co.th", ip="8.8.4.4")
    assert "รอคิว" in r.text and db.count_audits_since("8.8.4.4", since) == before + 1        # อยู่ในคิวก็นับโควตาแล้ว
    assert len(started) == 1
finally:
    S.threading.Thread = _Thread; S.RUN_INLINE = True

# ---------- deadline จริงใน run_checks: หมดเวลาหลังหน้าแรก = ข้ามที่เหลือ ----------
_Client = httpx.Client
httpx.Client = lambda *a, **kw: _Client(*a, transport=httpx.MockTransport(lambda r: httpx.Response(200, html="<html><title>x</title><body>hi</body></html>")), **kw)
try:
    seen = []
    res = _real_run_checks("https://deadline-test.example", progress=seen.append, timeout=2, deadline=time.monotonic() - 1)
    assert seen == ["home"] and res["partial"] and res["skipped"] == ["basics", "robots", "files", "pages", "index"], (seen, res.get("skipped"))
    assert any(c["key"] == "home" for c in res["checks"])
    res2 = _real_run_checks("https://deadline-test.example")                                     # ไม่ส่ง deadline = แบบเดิม ไม่มี partial
    assert "partial" not in res2 and "skipped" not in res2
finally:
    httpx.Client = _Client

print("snapshot_smoke OK")
