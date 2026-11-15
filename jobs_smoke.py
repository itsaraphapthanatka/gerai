"""ทดสอบ stage 10: คิวงานถาวร · จุดหยุด · งบ · การอนุมัติ · outbox ทดสอบ · ย้ายการทดสอบ AI เข้าคิว (flag durable_jobs)
กรณีตาม PROMPT 10: เหตุการณ์ซ้ำ · แย่งงานพร้อมกัน · lease หมดอายุ · ลองใหม่/เครื่องล่ม · อนุมัติซ้ำ/หมดอายุ/เนื้อหาเปลี่ยน
· งบหมด · ขอไม่รับหลังเข้าคิว · ยกเลิก · scheduler ชนกัน · หยุดที่ตอนเข้าคิวและตอนทำ
ไม่ยิงเครือข่าย (ฉีดตัวถาม AI ปลอม) · DB ชั่วคราว · ไม่ส่งข้อความจริง"""
import os, json, tempfile, threading, datetime as dt
os.environ.pop("DATABASE_URL", None)
os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["AI_BASE_URL"] = "http://127.0.0.1:9"
os.environ["GEO_DEEP_AUDIT"] = "1"
for k in list(os.environ):
    if k.startswith("GEO_FLAG_"):
        os.environ.pop(k)
import bcrypt, re
from fastapi.testclient import TestClient
import app.db as db
import app.flags as F
import app.jobs as J
import app.deep_audit as D
from app.main import app

db.init_db()
assert {r["version"] for r in db.list_schema_versions()} >= {"2026-11-15-10-jobs"}
D.RUN_INLINE = True
asked = []
D.HOOKS.update(ask=lambda q: asked.append(q) or {"ok": True, "text": "แนะนำ shopa", "citations": ["https://shop-a.co.th/"], "cost_usd": 0.05},
               engine="chatgpt", llm=lambda p: {"prompts": [f"คำถาม {i}" for i in range(12)]})
T1 = {"state": "done", "at": db.now(), "brand": "ShopA", "keywords": ["โกดัง"], "crawl": {"n": 1}, "pages": [], "issues": [],
      "health": {"score": 90, "errors": 0}, "ranking": {"rows": [], "competitors": [], "n": 0, "ok": 0, "page1": 0}}


def mk_lead(dom, t1=T1):
    lid = db.add_lead(f"https://{dom}", dom, source="audit", ip="1.2.3.4")
    db.update_lead(lid, token="T" + dom.replace(".", "")[:10].upper(), audit_json=json.dumps({"ok": True, "checks": []}), audit_at=db.now(),
                   deep_json=json.dumps(t1, ensure_ascii=False) if t1 else None)
    return lid


def age(job_id, seconds):
    """ย้อนเวลาของงาน (จำลองเวลาผ่านไป)"""
    t = (dt.datetime.now() - dt.timedelta(seconds=seconds)).isoformat(timespec="seconds")
    with db.get_conn() as c:
        c.execute(db.q("UPDATE jobs SET available_at=?, lease_until=CASE WHEN status='running' THEN ? ELSE lease_until END WHERE id=?"), (t, t, job_id))


# ================= flag ปิด = แบบเดิม (thread) ไม่เข้าคิว =================
lid0 = mk_lead("old-way.co.th")
assert D.start(lid0, ai=True) in ("started", "done")
assert D.tier2(db.get_lead(lid0))["state"] == "done" and J.list_jobs() == []
assert J.run_once() == []                                                                # worker ไม่ทำอะไรเมื่อ flag ปิด

F.set_enabled("durable_jobs", True)

# ================= เหตุการณ์ซ้ำ / idempotency =================
j1, st = J.enqueue("test_message", {"x": 1}, idem_key="dup-1")
j2, st2 = J.enqueue("test_message", {"x": 2}, idem_key="dup-1")
assert st == "queued" and st2 == "exists" and j1 == j2 and len(J.list_jobs()) == 1
try:
    J.enqueue("rm_rf", {}, idem_key="x")
    raise AssertionError("ต้องไม่รับงานที่ไม่รู้จัก")
except KeyError:
    pass
J.cancel(j1)

# ================= แย่งงานพร้อมกัน =================
ids = [J.enqueue("test_message", {}, idem_key=f"race-{i}")[0] for i in range(6)]
got, lock = [], threading.Lock()


def grab(n):
    w = f"w{n}"
    while True:
        j = J.claim(w)
        if not j:
            return
        with lock:
            got.append((j["id"], w))


ths = [threading.Thread(target=grab, args=(n,)) for n in range(4)]
[t.start() for t in ths]; [t.join() for t in ths]
claimed = [g[0] for g in got]
assert sorted(claimed) == sorted(ids) and len(set(claimed)) == len(claimed), got           # ทุกงานถูกจองครั้งเดียว
for jid in ids:
    J.cancel(jid)

# ================= lease หมดอายุ / เครื่องล่ม =================
jl, _ = J.enqueue("test_message", {}, idem_key="lease-1", max_attempts=2)
a = J.claim("crashed")
assert a["id"] == jl and a["attempts"] == 1
age(jl, 10)                                                                              # lease หมดแล้ว (worker ล่ม)
assert J.recover() == 1 and J.get(jl)["status"] == "queued"
b = J.claim("w2")
assert b["id"] == jl and b["attempts"] == 2
assert J.complete(jl, "crashed") is False                                                # worker เก่าฟื้นมา บันทึกผลทับไม่ได้
assert J.heartbeat(jl, "w2") and J.complete(jl, "w2", {"ok": 1}) and J.get(jl)["status"] == "succeeded"
jx, _ = J.enqueue("test_message", {}, idem_key="lease-2", max_attempts=1)
J.claim("w3"); age(jx, 10); J.recover()
assert J.get(jx)["status"] == "dead"                                                     # ครบจำนวนครั้ง = dead

# ================= ลองใหม่ + dead-letter =================
calls = []
J.HANDLERS["flaky"] = lambda job, w: calls.append(1) or (_ for _ in ()).throw(RuntimeError("boom"))
jf, _ = J.enqueue("flaky", {}, idem_key="flaky-1", max_attempts=3)
assert J.run_once() == [(jf, "retry")]
j = J.get(jf)
assert j["status"] == "queued" and j["available_at"] > db.now() and "boom" in j["last_error"]   # เว้นระยะก่อนลองใหม่
assert J.backoff_s(1) == 30 and J.backoff_s(2) == 60 and J.backoff_s(20) == J.BACKOFF_MAX_S
age(jf, 1); J.run_once(); age(jf, 1)
assert J.run_once() == [(jf, "dead")] and len(calls) == 3
assert J.retry(jf) and J.get(jf)["status"] == "queued" and J.get(jf)["attempts"] == 0
J.HANDLERS["flaky"] = lambda job, w: {"ok": True}
assert J.run_once() == [(jf, "succeeded")]
del J.HANDLERS["flaky"]

# ================= การอนุมัติ =================
lid = mk_lead("shop-a.co.th")
MSG = {"text": "สวัสดีครับ รายงานพร้อมแล้ว", "channel": "line", "recipient": "Uabc"}


def queue_msg(key, approval_id, text=MSG["text"], recipient="Uabc", lead=lid):
    return J.enqueue("test_message", {"lead_id": lead, "channel": "line", "recipient": recipient, "text": text, "approval_id": approval_id},
                     idem_key=key, lead_id=lead, channel="line")[0]


ap = J.grant("test_message", payload=MSG, policy="owner_manual", approver="owner@test", lead_id=lid, recipient="Uabc", channel="line")
jm = queue_msg("msg-ok", ap)
assert J.run_once() == [(jm, "succeeded")]
ob = J.outbox_list()
assert len(ob) == 1 and ob[0]["mode"] == "test" and ob[0]["recipient_hash"] != "Uabc" and "Uabc" not in json.dumps([dict(r) for r in ob])
jr = queue_msg("msg-replay", ap)                                                         # ใช้การอนุมัติเดิมซ้ำ
assert J.run_once() == [(jr, "dead")] and "already_used" in J.get(jr)["last_error"] and len(J.outbox_list()) == 1
ok, why = J.consume(ap, action="test_message", payload=MSG, job_id=jm, lead_id=lid, recipient="Uabc", channel="line")
assert ok and why == "already_consumed_by_this_job"                                       # งานเดิมทำซ้ำหลังเครื่องล่ม = ผ่าน
ap2 = J.grant("test_message", payload=MSG, policy="owner_manual", approver="owner@test", lead_id=lid, recipient="Uabc", channel="line")
jc = queue_msg("msg-changed", ap2, text="สวัสดีครับ ลด 50% วันนี้เท่านั้น")                    # เนื้อหาเปลี่ยนหลังอนุมัติ
assert J.run_once() == [(jc, "dead")] and "mismatch:content" in J.get(jc)["last_error"]
jc2 = queue_msg("msg-recipient", ap2, recipient="Uother")
assert J.run_once() == [(jc2, "dead")] and "recipient" in J.get(jc2)["last_error"]
ap3 = J.grant("test_message", payload=MSG, policy="owner_manual", approver="owner@test", lead_id=lid, recipient="Uabc", channel="line", ttl_hours=-1)
je = queue_msg("msg-expired", ap3)
assert J.run_once() == [(je, "dead")] and "expired" in J.get(je)["last_error"]
ap4 = J.grant("test_message", payload=MSG, policy="owner_manual", approver="owner@test", lead_id=lid, recipient="Uabc", channel="line")
assert J.revoke(ap4)
assert J.consume(ap4, action="test_message", payload=MSG, job_id=1, lead_id=lid, recipient="Uabc", channel="line") == (False, "revoked")
ap5 = J.grant("x", payload={"a": 1}, policy="p", approver="o", max_cost=1.0)
assert J.consume(ap5, action="x", payload={"a": 1}, job_id=9, cost=2.0)[1] == "mismatch:cost"
res = []
ths = [threading.Thread(target=lambda n=n: res.append(J.consume(ap5, action="x", payload={"a": 1}, job_id=100 + n, cost=0.5))) for n in range(5)]
[t.start() for t in ths]; [t.join() for t in ths]
assert sum(1 for ok, _ in res if ok) == 1                                                 # ใช้สิทธิ์พร้อมกัน ได้คนเดียว
assert len(J.outbox_list()) == 1

# ================= ขอไม่รับหลังเข้าคิว / ยกเลิก =================
ap6 = J.grant("test_message", payload=MSG, policy="owner_manual", approver="o", lead_id=lid, recipient="Uabc", channel="line")
js = queue_msg("msg-unsub", ap6)
db.update_lead(lid, unsub_at=db.now())
assert J.run_once() == [(js, "cancelled")] and len(J.outbox_list()) == 1
assert J.consume(ap6, action="test_message", payload=MSG, job_id=0, lead_id=lid, recipient="Uabc", channel="line")[0]   # อนุมัติยังไม่ถูกใช้
db.update_lead(lid, unsub_at=None)
jq = queue_msg("msg-cancel", 0)
assert J.cancel(jq, "owner") == "cancelled" and J.run_once() == []
jrun = queue_msg("msg-cancel-running", 0)
w = "wc"
j = J.claim(w)
assert J.cancel(jrun, "owner") == "requested"
assert J.dispatch(j, w) == "cancelled" and J.get(jrun)["status"] == "cancelled"
assert J.cancel(jm) == "noop"                                                            # เสร็จแล้วยกเลิกไม่ได้

# ================= จุดหยุด: ตอนเข้าคิว และตอนทำ =================
J.set_pause("global", "*", True, "ทดสอบ", "owner")
assert J.enqueue("test_message", {}, idem_key="p-1")[1].startswith("paused:global")
J.set_pause("global", "*", False)
jp, _ = J.enqueue("test_message", {}, idem_key="p-2", channel="line", lead_id=lid)
J.set_pause("channel", "line", True, "LINE มีปัญหา")                                       # หยุดหลังเข้าคิวแล้ว
assert J.run_once() == [(jp, "paused")] and J.get(jp)["status"] == "queued" and J.get(jp)["attempts"] == 0
J.set_pause("channel", "line", False)
J.set_pause("account", str(lid), True)
assert J.enqueue("test_message", {}, idem_key="p-3", lead_id=lid)[1].startswith("paused:account")
J.set_pause("account", str(lid), False)
J.set_pause("workflow", "deep_ai", True)
assert J.paused("deep_ai") and not J.paused("test_message")
J.set_pause("workflow", "deep_ai", False)
J.cancel(jp)

# ================= งบ =================
db.set_setting("budget_test_daily", "3")
r1 = J.reserve("test", 1.2); r2 = J.reserve("test", 1.2)
try:
    J.reserve("test", 1.2)
    raise AssertionError("ต้องเกินงบ")
except J.BudgetExhausted:
    pass
assert J.budget_used("test")["reserved"] == 2.4                                          # แถวที่เกินถูกยกเลิกทั้งธุรกรรม
assert J.settle(r1, 0.4) and not J.settle(r1, 9)                                         # ปิดยอดได้ครั้งเดียว
assert J.release(r2) and J.budget_used("test") == {"reserved": 0.0, "committed": 0.4, "limit": 3.0, "day": J._today()}
db.set_setting("budget_race_daily", "5")
ok_n = []


def try_reserve():
    try:
        J.reserve("race", 1.0)
        ok_n.append(1)
    except J.BudgetExhausted:
        pass


ths = [threading.Thread(target=try_reserve) for _ in range(12)]
[t.start() for t in ths]; [t.join() for t in ths]
assert len(ok_n) == 5 and J.budget_used("race")["reserved"] == 5.0                       # จองพร้อมกันไม่เกินเพดาน
with db.get_conn() as c:                                                                 # จองค้าง (งานตาย) เกิน 2 ชม. = คืน
    c.execute(db.q("UPDATE budget_reservations SET created_at=? WHERE budget_key='race'"),
              ((dt.datetime.now() - dt.timedelta(hours=3)).isoformat(timespec="seconds"),))
J.recover()
assert J.budget_used("race")["reserved"] == 0.0

# ================= ย้ายการทดสอบ AI เข้าคิว =================
asked.clear()
J.KICK_INLINE = True
lid2 = mk_lead("queued-ai.co.th")
assert D.start(lid2, ai=True) == "queued"
t2 = D.tier2(db.get_lead(lid2))
assert t2["state"] == "done" and t2["asked"] > 0 and asked                               # worker รอบ kick ทำให้แล้ว
jd = [j for j in J.list_jobs() if j["kind"] == "deep_ai"][0]
assert jd["status"] == "succeeded" and jd["lead_id"] == lid2
u = J.budget_used("ai_usd")
assert u["committed"] == float(t2["cost_usd"]) and u["reserved"] == 0                     # ปิดยอดด้วยค่าจริง
n_jobs = len(J.list_jobs())
D.start(lid2, ai=True)
assert len(J.list_jobs()) == n_jobs                                                      # เสร็จแล้ว ไม่เข้าคิวซ้ำ

# รอชั้นแรก: ยังไม่มีรายงานชั้นแรก → เลื่อนโดยไม่นับความพยายาม
J.KICK_INLINE = False
lid3 = mk_lead("wait-t1.co.th", t1=None)
D.RUN_INLINE = False
os.environ["GEO_DEEP_AUDIT"] = "1"
jw, _ = J.enqueue("deep_ai", {"lead_id": lid3, "force": False}, idem_key="wait-t1", lead_id=lid3)
D._running = lambda d: True if d is not None and d.get("state") == "running" else False
D._save(lid3, "deep_json", {"state": "running", "started_at": db.now()})
assert J.run_once(kinds=("deep_ai",)) == [(jw, "deferred")] and J.get(jw)["attempts"] == 0 and "ชั้นแรก" in J.get(jw)["note"]
J.cancel(jw)
D.RUN_INLINE = True

# งบหมด: เลื่อนไปวันถัดไป ไม่เรียก AI · รายงานบอกตามจริง
db.set_setting("budget_ai_usd_daily", "0.5")
asked.clear()
lid4 = mk_lead("no-budget.co.th")
jb, _ = J.enqueue("deep_ai", {"lead_id": lid4, "force": False}, idem_key="no-budget", lead_id=lid4)
assert J.run_once(kinds=("deep_ai",)) == [(jb, "deferred")] and asked == []
j = J.get(jb)
assert j["status"] == "queued" and j["available_at"] > db.now() and "ไม่พอ" in j["note"]
assert "ครบงบ" in D.tier2(db.get_lead(lid4))["reason"]
J.cancel(jb)
db.set_setting("budget_ai_usd_daily", "")

# ================= scheduler ชนกัน (autorun + cron พร้อมกัน) =================
done_ids, lk = [], threading.Lock()
J.HANDLERS["count"] = lambda job, w: (lk.acquire(), done_ids.append(job["id"]), lk.release()) and {"ok": 1}
cids = [J.enqueue("count", {}, idem_key=f"cnt-{i}")[0] for i in range(10)]
ths = [threading.Thread(target=lambda: J.run_once(limit=10, kinds=("count",))) for _ in range(3)]
[t.start() for t in ths]; [t.join() for t in ths]
assert sorted(done_ids) == sorted(cids), done_ids                                        # แต่ละงานทำครั้งเดียว
del J.HANDLERS["count"]

# ================= ปิด flag = หยุด worker (งานค้างรอ) =================
F.set_enabled("durable_jobs", False)
jo, _ = J.enqueue("test_message", {}, idem_key="off-1")
assert J.run_once() == [] and J.get(jo)["status"] == "queued"
J.cancel(jo)
lid5 = mk_lead("back-to-old.co.th")
asked.clear()
D.start(lid5, ai=True)
assert D.tier2(db.get_lead(lid5))["state"] == "done" and not [j for j in J.list_jobs() if j["lead_id"] == lid5]   # กลับทาง thread เดิม

# ================= หน้าแอดมิน =================
db.create_tenant("admin@jobs.test", bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Admin", is_admin=True)
adm = TestClient(app)
assert adm.post("/login", data={"email": "admin@jobs.test", "password": "pw123456"}).status_code == 200
page = adm.get("/admin/jobs").text
assert "คิวงานถาวร" in page and "durable_jobs" in page and "Outbox" in page and "2026-11-15-10-jobs" in page
csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
assert adm.post("/admin/jobs/pause", data={"scope": "global", "key": "*", "on": "1"}, follow_redirects=False).status_code == 403
assert adm.post("/admin/jobs/pause", data={"csrf_token": csrf, "scope": "global", "key": "*", "on": "1"}, follow_redirects=False).status_code in (302, 303)
assert J.paused("x") and "เปิดคิวทั้งหมดกลับ" in adm.get("/admin/jobs").text
adm.post("/admin/jobs/pause", data={"csrf_token": csrf, "scope": "global", "key": "*", "on": "0"})
assert not J.paused("x")
dead = [j for j in J.list_jobs("dead")][0]
adm.post(f"/admin/jobs/{dead['id']}/retry", data={"csrf_token": csrf})
assert J.get(dead["id"])["status"] == "queued"
adm.post(f"/admin/jobs/{dead['id']}/cancel", data={"csrf_token": csrf})
assert J.get(dead["id"])["status"] == "cancelled"
assert TestClient(app).get("/admin/jobs", follow_redirects=False).status_code in (302, 303)
print("jobs_smoke OK")
