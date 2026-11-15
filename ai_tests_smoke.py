"""ทดสอบ stage 04C: การทดสอบ AI แบบเสียเงินผ่านคิวงาน — สวิตช์ทดสอบจริง · สิทธิ์ตามเหตุผลที่สั่ง · ต้องมีข้อมูลพอ · งบ
· ลองใหม่ไม่จ่ายซ้ำ · ไม่มีคีย์ = ไม่ได้ทดสอบ · ผู้ให้บริการล่ม · ยกเลิก · ปิดสวิตช์หลังเข้าคิว
ไม่เรียก AI จริง (ฉีดตัวถามปลอมที่นับครั้งและค่าใช้จ่าย) · DB ชั่วคราว"""
import os, re, json, tempfile, datetime as dt
os.environ.pop("DATABASE_URL", None)
os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["AI_BASE_URL"] = "http://127.0.0.1:9"
os.environ["GEO_DEEP_AUDIT"] = "1"
for k in list(os.environ):
    if k.startswith("GEO_FLAG_"):
        os.environ.pop(k)
import bcrypt
from fastapi.testclient import TestClient
import app.db as db
import app.flags as F
import app.jobs as J
import app.deep_audit as D
import app.ai_tests as AT
import app.report_view as RV
import app.business_context as BC
from app.main import app

db.init_db()
D.RUN_INLINE = True
J.KICK_INLINE = True
_kick = J.kick
J.kick = lambda: _kick() if J.KICK_INLINE else None                                     # ไม่ใช้ thread เบื้องหลังในเทสต์
calls = []
FAIL = [False]


def fake_ask(q):
    calls.append(q)
    if FAIL[0]:
        return {"ok": False, "reason": "provider down"}
    return {"ok": True, "text": "แนะนำ ShopA", "citations": ["https://shop-a.co.th/"], "cost_usd": 0.05}


D.HOOKS.update(ask=fake_ask, engine="chatgpt", llm=lambda p: {"prompts": [f"คำถาม {i}" for i in range(12)]})
T1 = {"state": "done", "at": db.now(), "brand": "ShopA", "keywords": ["โกดัง"], "crawl": {"n": 1}, "pages": [], "issues": [],
      "health": {"score": 90, "errors": 0}, "ranking": {"rows": [], "competitors": [], "n": 0, "ok": 0, "page1": 0}}


def mk(dom, t1=T1, **kw):
    lid = db.add_lead(f"https://{dom}", dom, source="audit", ip="1.2.3.4")
    db.update_lead(lid, token="T" + dom.split(".")[0][:10].upper(), audit_json=json.dumps({"ok": True, "checks": []}), audit_at=db.now(),
                   deep_json=json.dumps(t1, ensure_ascii=False), **kw)
    return lid


t2 = lambda lid: D.tier2(db.get_lead(lid))
lane = lambda lid: next(x for x in RV.build(db.get_lead(lid))["lanes"] if x["key"] == "ai_brand_mention")
deep_jobs = lambda lid: [j for j in J.list_jobs() if j["kind"] == "deep_ai" and j["lead_id"] == lid]

F.set_enabled("durable_jobs", True)

# ---------- สวิตช์ทดสอบจริงปิด (ค่าเริ่มต้น) = ไม่เรียก AI ไม่เข้าคิว ----------
assert AT.live() is False
l1 = mk("off.co.th")
D.start(l1, ai=True, reason="plan_requested")
assert calls == [] and deep_jobs(l1) == [] and t2(l1)["state"] == "disabled"
x = lane(l1)
assert x["state"] == "not_tested" and x["value"] is None and "ยังไม่เปิดการทดสอบ AI" in x["detail"]

AT.set_live(True)

# ---------- สิทธิ์ตามเหตุผล ----------
for reason in ("replied", "agent", "unspecified"):
    l = mk(f"r-{reason}.co.th")
    D.start(l, ai=True, reason=reason)
    assert deep_jobs(l) == [] and t2(l)["state"] == "awaiting_staff" and "ทีมกด" in t2(l)["reason"], reason
assert calls == []
lu = mk("unsub.co.th", unsub_at=db.now())
assert AT.eligibility(db.get_lead(lu), "plan_requested") == (False, "ลูกค้าขอไม่รับการติดต่อ")

# ---------- ต้องมีข้อมูลพอ ----------
ln = mk("noinput.co.th", t1={**T1, "keywords": []})
D.start(ln, ai=True, reason="plan_requested")
assert t2(ln)["state"] == "needs_input" and deep_jobs(ln) == [] and lane(ln)["state"] == "not_tested"
v, _ = BC.validate({"primary_service": "ให้เช่าโกดัง"}, db.get_lead(ln))
BC.save(db.get_lead(ln), v)
assert AT.eligibility(db.get_lead(ln), "plan_requested")[0] is True                      # ยืนยันบริการหลักแล้วทำได้

# ---------- ทำได้: ลูกค้าขอแผน → เรียก AI · ปิดยอดด้วยค่าจริง ----------
l2 = mk("ok.co.th")
calls.clear()
assert D.start(l2, ai=True, reason="plan_requested") == "queued"
d = t2(l2)
assert d["state"] == "done" and d["asked"] == len(calls) > 0
j = deep_jobs(l2)[0]
assert j["status"] == "succeeded" and json.loads(j["payload_json"])["reason"] == "plan_requested"
u = J.budget_used("ai_usd")
assert abs(u["committed"] - 0.05 * len(calls)) < 1e-6 and u["reserved"] == 0
# รอบ 30 วัน: อัตโนมัติได้ครั้งเดียว
db.update_lead(l2, deep_ai_json=None)
D.start(l2, ai=True, reason="contact_form")
assert len(deep_jobs(l2)) == 1 and t2(l2)["state"] == "awaiting_staff" and "30 วัน" in t2(l2)["reason"]
# ทีมกดได้ไม่เกิน 3 ครั้ง/30 วัน
for i in range(3):
    J.enqueue("deep_ai", {"lead_id": l2, "force": True, "reason": "staff"}, idem_key=f"staff-{i}", lead_id=l2)
assert AT.eligibility(db.get_lead(l2), "staff") == (False, "ทีมสั่งครบ 3 ครั้งในรอบ 30 วันแล้ว")
for jj in deep_jobs(l2):
    if jj["status"] == "queued":
        J.cancel(jj["id"])

# ---------- ลองใหม่ไม่จ่ายซ้ำ ----------
J.KICK_INLINE = False
l3 = mk("retry.co.th")
calls.clear()
D.start(l3, ai=True, reason="plan_requested")
jr = deep_jobs(l3)[0]
orig = D._ai_count_up
D._ai_count_up = lambda: (_ for _ in ()).throw(RuntimeError("crash หลังถามครบ"))          # ล่มหลังจ่ายแล้ว
assert J.run_once(kinds=("deep_ai",)) == [(jr["id"], "retry")]
D._ai_count_up = orig
first_calls = len(calls)
assert first_calls > 0 and t2(l3)["state"] == "error"
spent1 = J.budget_used("ai_usd")["committed"]
with db.get_conn() as c:
    c.execute(db.q("UPDATE jobs SET available_at=? WHERE id=?"), (db.now(), jr["id"]))
assert J.run_once(kinds=("deep_ai",)) == [(jr["id"], "succeeded")]
assert len(calls) == first_calls                                                         # รอบสองไม่ถามซ้ำ
assert t2(l3)["state"] == "done" and t2(l3)["asked"] == first_calls
assert abs(J.budget_used("ai_usd")["committed"] - spent1) < 1e-6                         # ไม่นับเงินซ้ำ
assert abs(spent1 - (u["committed"] + 0.05 * first_calls)) < 1e-6                         # รอบที่ล่มนับตามที่จ่ายจริง

# ---------- ผู้ให้บริการล่ม ----------
J.KICK_INLINE = True
FAIL[0] = True
l4 = mk("down.co.th")
D.start(l4, ai=True, reason="plan_requested")
assert t2(l4)["state"] == "done" and t2(l4)["asked"] == 0
x = lane(l4)
assert x["state"] == "unavailable" and x["value"] is None
FAIL[0] = False

# ---------- ไม่มีคีย์ = ไม่ได้ทดสอบ (ไม่ใช่ล่ม) ----------
saved = dict(D.HOOKS)
D.HOOKS.update(ask=None, engine=None)
D._engine = (lambda orig_engine: (lambda: None))(D._engine)
l5 = mk("nokey.co.th")
D.start(l5, ai=True, reason="plan_requested")
assert t2(l5)["state"] == "not_configured" and lane(l5)["state"] == "not_tested" and "ผู้ให้บริการ AI" in lane(l5)["detail"]
D.HOOKS.update(saved)

# ---------- ปิดสวิตช์หลังเข้าคิว / ทีมยกเลิก ----------
J.KICK_INLINE = False
calls.clear()
l6 = mk("switchoff.co.th")
D.start(l6, ai=True, reason="plan_requested")
AT.set_live(False)
assert J.run_once(kinds=("deep_ai",))[0][1] == "cancelled" and calls == [] and t2(l6)["state"] == "disabled"
AT.set_live(True)
l7 = mk("cancel.co.th")
D.start(l7, ai=True, reason="meeting_request")
assert J.cancel(deep_jobs(l7)[0]["id"], "owner") == "cancelled" and t2(l7)["state"] == "cancelled"
assert lane(l7)["state"] == "not_tested" and calls == []

# ---------- งบหมด ----------
db.set_setting("budget_ai_usd_daily", "0.01")
l8 = mk("nobudget.co.th")
D.start(l8, ai=True, reason="plan_requested")
assert J.run_once(kinds=("deep_ai",))[0][1] == "deferred" and calls == [] and "ครบงบ" in t2(l8)["reason"]
db.set_setting("budget_ai_usd_daily", "")

# ---------- ค่าประมาณ + หน้าแอดมิน ----------
e = AT.estimate()
assert e["prompts"] == D.N_PROMPTS and e["est_per_run_usd"] == J.AI_RUN_EST_USD and e["max_runs_per_day"] == int(e["daily_budget_usd"] // J.AI_RUN_EST_USD)
db.create_tenant("own@ai.test", bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Own", is_admin=True)
adm = TestClient(app)
assert adm.post("/login", data={"email": "own@ai.test", "password": "pw123456"}).status_code == 200
page = adm.get("/admin/jobs").text
assert "การทดสอบ AI แบบเสียเงิน: เปิด" in page and "ต่อรายงาน" in page
cs = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
assert adm.post("/admin/jobs/ai-live", data={"on": "0"}).status_code == 403
adm.post("/admin/jobs/ai-live", data={"csrf_token": cs, "on": "0"})
assert AT.live() is False and "การทดสอบ AI แบบเสียเงิน: ปิด" in adm.get("/admin/jobs").text
# ---------- flag คิวงานปิด = เส้นทางเดิม (ไม่ใช้นโยบายนี้) ----------
F.set_enabled("durable_jobs", False)
l9 = mk("oldpath.co.th")
calls.clear()
D.start(l9, ai=True, reason="replied")
assert t2(l9)["state"] == "done" and calls                                               # thread เดิม ตามเพดานรายวันเดิม
print("ai_tests_smoke OK")
