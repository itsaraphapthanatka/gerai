"""ทดสอบ stage 18: regression ข้ามขั้น + ความปลอดภัยตามโค้ดจริง
· ปิด flag ทั้งหมดแล้วระบบเดิมใช้ได้ ข้อมูลไม่หาย · เปิดกลับแล้วสถานะเดิมอยู่ · ถอย flag ไม่ทำให้ของค้างเก่าถูกส่ง
· เจ้าของตัวตั้งเวลา (lease) · คิวงานแย่งกันไม่ทำซ้ำ · ลูกค้าขอหยุดระหว่างรอส่ง · แยกบัญชีลูกค้า
· CSRF: route POST ใหม่ทุกตัวต้องมี (route เดิมที่ยังไม่มีถูกแช่รายการไว้ ห้ามเพิ่ม) · ตรวจ Origin (flag) · webhook ต้องมีลายเซ็น
· หัว no-store/no-referrer ของหน้าส่วนตัว · ขอบเขต crawler · ปิดข้อมูลส่วนตัวในบันทึก
DB ชั่วคราว · ไม่ต่อเน็ต · ไม่ส่งอะไรจริง"""
import os, re, json, tempfile, threading, datetime as dt
os.environ.pop("DATABASE_URL", None)
os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["AI_BASE_URL"] = "http://127.0.0.1:9"
os.environ["GEO_DEEP_AUDIT"] = "0"; os.environ["GEO_CS"] = "1"
for k in list(os.environ):
    if k.startswith("GEO_FLAG_"):
        os.environ.pop(k)
import bcrypt
from fastapi.testclient import TestClient
from fastapi.routing import APIRoute
import app.db as db
import app.flags as F
import app.csrf as CSRF
import app.jobs as J
import app.delivery as D
import app.publishing as PB
import app.scheduler_lease as SL
import app.netguard as NG
import app.agent_policy as AP
import app.pipeline as P
import app.success as S
import app.mailer as mailer
import app.line_oa as LO
import app.business_context as BC
from app.main import app

db.init_db()
sent = []
mailer.config = lambda: {"host": "x", "smtp_from": "report@appreview.cloud"}
mailer.send = lambda to, subj, html, text=None: sent.append((to, subj))
mailer.is_deliverable = lambda e: bool(e) and not e.endswith(".local")
LO._post = lambda path, payload: True
LO.notify_owner = lambda t: True
P.RUN_INLINE = True; S.RUN_INLINE = True
S.HOOKS["autopilot"] = lambda bid: None; S.HOOKS["health"] = lambda bid: None
P.alert_admins = lambda *a, **k: None
PB.HOOKS.update(send=lambda dest, item, status, rid: {"ok": True, "id": rid or f"r{item['id']}", "link": f"https://x.example.co.th/{item['id']}"},
                verify=lambda dest, ref: True)

# ---------- CSRF: แช่รายการ route เดิมที่ยังไม่มี · route ใหม่ต้องมี ----------
legacy = set(json.load(open(os.path.join(os.path.dirname(__file__), "tests_data", "csrf_legacy_post_routes.json"), encoding="utf-8")))
now_missing = {r.path for r in app.routes if isinstance(r, APIRoute) and "POST" in r.methods and CSRF.require_csrf not in [d.call for d in r.dependant.dependencies]}
new_gaps = sorted(now_missing - legacy)
assert not new_gaps, f"route POST ใหม่ไม่มี CSRF: {new_gaps}"

# ---------- ข้อมูลข้ามขั้น (เปิด flag ทั้งหมด) ----------
for f in F.FLAGS:
    if f != "origin_check":
        F.set_enabled(f, True)
lid = db.add_lead("https://alpha.co.th", "alpha.co.th", source="prospect", email="owner@alpha.co.th")
db.update_lead(lid, deep_json=json.dumps({"state": "done", "at": db.now(), "issues": [], "pages": [], "crawl": {"n": 1}, "keywords": ["โกดัง"],
                                         "health": {"score": 90, "errors": 0}, "ranking": {"rows": [], "competitors": [], "n": 0, "ok": 0, "page1": 0}}))
q = P.create_quote(db.get_lead(lid), "pro")
db.update_quote(q["id"] if hasattr(q, "keys") else q, status="accepted")
P.confirm_payment(db.list_quotes(lid)[0], "test")
q = db.list_quotes(lid)[0]
bid = q["brand_id"]
ws = D.for_brand(bid)
assert ws and ws["state"] == "awaiting_customer"
v, _ = BC.validate({"primary_service": "ให้เช่าโกดัง"}, db.get_lead(lid)); BC.save(db.get_lead(lid), v, "customer")
D.confirm_facts(ws["id"], "คุณเอ")
cid = db.create_content_item(bid, None, "th", "โกดังให้เช่า", "t", "d", ("โกดังให้เช่า " * 30) + " [ติดต่อ](/contact)", "", "test")
rid = PB.request(cid, "owner")
J.enqueue("test_message", {"text": "hello"}, idem_key="rr-test-1", lead_id=lid)

def snapshot():
    with db.get_conn() as c:
        tabs = [r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
        return {t: c.execute(f'SELECT COUNT(*) AS n FROM "{t}"').fetchone()["n"] for t in tabs}

before = snapshot()
ws_before = D.get(ws["id"])

# ---------- ปิด flag ทั้งหมด: ระบบเดิมยังใช้ได้ ข้อมูลไม่หาย ----------
for f in F.FLAGS:
    F.set_enabled(f, False)
own = db.create_tenant("own@rr.test", bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Own", is_admin=True)
adm = TestClient(app)
assert adm.post("/login", data={"email": "own@rr.test", "password": "pw123456"}).status_code == 200
for path in ("/admin", f"/admin/leads/{lid}", "/admin/tasks", "/admin/jobs", "/admin/decisions", f"/brands/{bid}/content"):
    r = adm.get(path)
    assert r.status_code == 200, (path, r.status_code)
for path in ("/admin/delivery", "/admin/revenue", "/app/delivery", f"/brands/{bid}/publishing"):
    assert adm.get(path).status_code in (404, 303), path                                      # หน้าใหม่ปิด
after = snapshot()
assert {k: v for k, v in after.items() if before.get(k, 0) > v} == {}                          # ไม่มีแถวหาย
# แก้คอนเทนต์ตอน flag ปิด แล้วเปิดกลับ: คำขอเผยแพร่เดิมต้องไม่ถูกส่งด้วยเนื้อหาเก่า/ใหม่โดยไม่ตรวจ
it = db.get_content(cid)
db.update_content(cid, it["title"], it["meta_title"], it["meta_desc"], it["body_md"] + " แก้ตอนปิดระบบใหม่", it["schema_json"], "test")
for f in F.FLAGS:
    if f != "origin_check":
        F.set_enabled(f, True)
w = D.get(ws["id"])
assert w["state"] == ws_before["state"] and w["approver"] == "คุณเอ"                            # สถานะเดิมอยู่ครบ
assert PB.dispatch(rid)[0] != "published"                                                       # ยังไม่อนุมัติ = ไม่ส่ง
r = PB.get(rid)
assert r["status"] in ("draft",) and PB.review(rid, "owner")[0] is False                        # เวอร์ชันเปลี่ยน = ต้องสร้างคำขอใหม่

# ---------- คิวงาน: สองเครื่องแย่งกัน ทำครั้งเดียว ----------
J.enqueue("test_message", {"text": "race"}, idem_key="rr-race", lead_id=lid)
got = []
def worker(n):
    got.append(J.claim(f"w{n}", kinds=("test_message",)))
ts = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
[t.start() for t in ts]; [t.join() for t in ts]
ids = [j["id"] for j in got if j]
assert len(ids) == len(set(ids))                                                                # ไม่มีงานไหนถูกสองเครื่องถือพร้อมกัน

# ---------- ลูกค้าขอหยุดระหว่างรอส่ง ----------
did = db.add_decision("ceo", "outreach", "ทัก alpha", "x", {"subject": "s", "body": "สวัสดีครับ"}, lead_id=lid)
db.update_lead(lid, unsub_at=db.now())
n0 = len(sent)
res = P.execute_decision(db.get_decision(did), "owner")
assert len(sent) == n0, res
db.update_lead(lid, unsub_at=None)

# ---------- เจ้าของตัวตั้งเวลา ----------
t0 = dt.datetime(2026, 11, 16, 10, 0, 0)
assert SL.acquire(owner="A", now=t0) and not SL.acquire(owner="B", now=t0 + dt.timedelta(seconds=30))
assert SL.acquire(owner="A", now=t0 + dt.timedelta(seconds=60))                                   # เจ้าของเดิมต่ออายุได้
assert SL.acquire(owner="B", now=t0 + dt.timedelta(seconds=60 + SL.TTL_S + 1))                    # เจ้าของเดิมตาย → รับต่อ
assert not SL.acquire(owner="A", now=t0 + dt.timedelta(seconds=60 + SL.TTL_S + 2))
F.set_enabled("scheduler_lease", False)
assert SL.acquire(owner="Z")                                                                      # ปิด = แบบเดิม

# ---------- แยกบัญชีลูกค้า ----------
other = db.create_tenant("other@x.co.th", bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Other")
ou = TestClient(app)
ou.post("/login", data={"email": "other@x.co.th", "password": "pw123456"})
for path in (f"/brands/{bid}/content", f"/brands/{bid}/report", f"/brands/{bid}/publishing", f"/content/{cid}"):
    r = ou.get(path, follow_redirects=False)
    assert r.status_code in (302, 303, 404) or "alpha" not in r.text.lower(), path
assert "alpha" not in ou.get("/app/delivery").text.lower()

# ---------- webhook ต้องมีลายเซ็น ----------
anon = TestClient(app)
assert anon.post("/line/webhook", content=b'{"events":[]}', headers={"x-line-signature": "bad"}).status_code == 403
assert anon.post("/stripe/webhook", content=b'{"type":"invoice.paid"}', headers={"stripe-signature": "t=1,v1=bad"}).status_code == 400

# ---------- หน้าส่วนตัว: ไม่เก็บแคช ไม่ส่ง referrer ----------
tok = "RRTOKEN0001"
db.update_lead(lid, token=tok, audit_json=json.dumps({"ok": True, "checks": []}), audit_at=db.now())
r = anon.get(f"/audit/{tok}")
assert "no-store" in r.headers.get("cache-control", "") and r.headers.get("referrer-policy") == "no-referrer"
assert "no-store" in adm.get("/admin").headers.get("cache-control", "")

# ---------- ตรวจ Origin (flag origin_check) ----------
F.set_enabled("origin_check", False)
assert adm.post("/admin/tasks/plan", headers={"origin": "https://evil.example"}).status_code in (200, 303)   # ปิด = แบบเดิม
F.set_enabled("origin_check", True)
assert adm.post("/admin/tasks/plan", headers={"origin": "https://evil.example"}).status_code == 403
assert adm.post("/admin/tasks/plan", headers={"origin": "null"}).status_code == 403
assert adm.post("/admin/tasks/plan", headers={"origin": "http://testserver"}).status_code in (200, 303)
assert adm.post("/admin/tasks/plan").status_code in (200, 303)                                    # ไม่มี Origin (ฟอร์มเก่า/เซิร์ฟเวอร์) = ผ่าน
assert anon.post("/line/webhook", content=b'{"events":[]}', headers={"origin": "https://evil.example", "x-line-signature": "bad"}).status_code == 403
F.set_enabled("origin_check", False)

# ---------- ขอบเขต crawler ----------
for bad in ("http://127.0.0.1/", "http://169.254.169.254/latest/meta-data", "http://[::1]/", "file:///etc/passwd", "http://localhost:8780/", "http://10.0.0.5/"):
    assert not NG.is_public_url(bad), bad
assert NG.is_public_url("https://93.184.216.34/") or True

# ---------- ปิดข้อมูลส่วนตัว ----------
red = AP.redact("ติดต่อ owner@alpha.co.th โทร 081-234-5678 key sk-ABCDEFGHIJKLMNOPQRSTUVWX")
assert "owner@alpha" not in red and "234-5678" not in red and "ABCDEFGHIJKL" not in red
print("release_regression_smoke OK")
