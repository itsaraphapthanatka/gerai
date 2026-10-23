"""ทดสอบ CEO แจกงาน: วางแผนจากด่านที่ขาด (หาลูกค้าในกลุ่มที่ได้ผลดีที่สุด · หาช่องทางซ้ำ · วัด AI ให้ลีดที่สนใจ · การตลาด · ติดตามผล · ฝากเจ้าของโทร)
· ไม่แจกซ้ำในวันเดียว · หยุดฉุกเฉิน = พักไว้ · ลงมือผ่านฟังก์ชันเดิม · ออฟฟิศเห็นการแจกงาน · แชต CEO "วางแผนวันนี้" · หน้า /admin/tasks"""
import os, tempfile, json, datetime as dt
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke15-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["GEO_DEEP_AUDIT"] = "0"; os.environ["GEO_CS"] = "0"
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.orchestrator as O, app.prospecting as PR, app.deep_audit as D, app.marketing as MK, app.followup as F, app.closer as C
import app.execs as X, app.pipeline as P, app.line_oa as LO, app.sms as S, app.office as OF, app.ceo_chat as CC
from app.main import app
db.init_db()
P.RUN_INLINE = True; O.RUN_INLINE = True; CC.RUN_INLINE = True
C.FETCH = lambda u: ""
notified = []; LO.notify_owner = lambda t: notified.append(t) or True
S.in_hours = lambda now=None: True
REPORT = json.dumps({"checks": [{"key": "csr", "status": "fail"}]})
ago = lambda d: (dt.datetime.now() - dt.timedelta(days=d)).isoformat(timespec="seconds")
seg_good, seg_bad = PR.segments()[0], PR.segments()[1]
def lead(dom, seg, status="contacted", opened=False, email=True):
    lid = db.add_lead(f"https://{dom}", dom, source="prospect", email=f"info@{dom}" if email else "")
    db.update_lead(lid, status=status, audit_json=REPORT, score=60, notes=f"พบจากคำค้น: {seg} กรุงเทพ · x", token=f"t-{dom}")
    if status != "new":
        db.add_lead_event(lid, "contacted", "", "auto")
    if opened:
        db.add_lead_event(lid, "opened", "", "customer")
    return lid
for i in range(3):
    lead(f"g{i}.co.th", seg_good, opened=(i < 2))
for i in range(3):
    lead(f"b{i}.co.th", seg_bad)
nomail = lead("nomail.co.th", seg_bad, status="new", email=False)
with db.get_conn() as c:
    c.execute(db.q("UPDATE leads SET created_at=? WHERE id=?"), (ago(2), nomail))
hot = lead("hot.co.th", seg_good, status="quoted")
q = P.create_quote(db.get_lead(hot), "pro"); db.update_quote(q["id"], status="accepted", accepted_at=ago(1))
LO.save_config("tok", "sec")                                                      # มีช่องทาง LINE → CMO มีงาน

seg, area, why = O.pick_segment(); assert seg == seg_good and "ได้ผลดีที่สุด" in why, (seg, why)
ids = O.plan()
tasks = {(db.get_task(i)["agent"], db.get_task(i)["kind"]): db.get_task(i) for i in ids}
assert ("owner", "call") in tasks and "hot.co.th" in tasks[("owner", "call")]["title"]
fl = tasks[("prospecting", "find_leads")]; assert seg_good in fl["title"] and "ตรวจฟรีสัปดาห์นี้" in fl["reason"]
assert ("prospecting", "refind") in tasks and nomail in json.loads(tasks[("prospecting", "refind")]["args_json"])["lead_ids"]
ai = [t for k, t in tasks.items() if k == ("cto", "ai_report")]; assert ai and ("g0.co.th" in ai[0]["title"] or "g1.co.th" in ai[0]["title"])
assert ("cmo", "marketing_round") in tasks and ("sdr", "followup_round") in tasks
assert O.plan() == []                                                              # วันเดียวกันไม่แจกซ้ำ
print("plan: โทร · หาลูกค้ากลุ่มที่ดีที่สุด · หาช่องทางซ้ำ · วัด AI · การตลาด · ติดตามผล · ไม่ซ้ำ OK")

calls = []
PR.run = lambda limit=None, search=None, contact=None, queries=None, force=False: calls.append(("prospect", limit, queries, force)) or {"added": 3, "audited": 3, "with_email": 2, "proposed": 2}
PR.refind_contact = lambda l: calls.append(("refind", l["domain"])) or {"email": ""}
D.run_ai = lambda lid, **k: calls.append(("ai", lid)) or {"state": "done", "mentioned": 0, "asked": 20}
D.run_tier1 = lambda lid, **k: {"state": "done"}
MK.run = lambda *a, **k: calls.append(("cmo",)) or {"proposed": 2}
F.run = lambda *a, **k: calls.append(("sdr",)) or {"proposed": 1}
O.dispatch(ids)
st = {db.get_task(i)["kind"]: db.get_task(i)["status"] for i in ids}
assert all(v == "done" for v in st.values()), st
assert ("prospect", fl and json.loads(fl["args_json"])["n"], [f"{seg_good} {json.loads(fl['args_json'])['area']}"], True) in calls
assert any(c[0] == "refind" for c in calls) and any(c[0] == "ai" for c in calls) and ("cmo",) in calls and ("sdr",) in calls
assert notified and notified[-1].startswith("CEO แจกงานวันนี้") and "หาลูกค้า" in notified[-1]
evs = OF.events_today(OF.today_start_local(), {})
assert any(e["type"] == "assign" and e["to"] == "prospecting" for e in evs) and any(e["type"] == "task_done" and e["actor"] == "cto" for e in evs)
print("run: ลงมือผ่านฟังก์ชันเดิม · แจ้งเจ้าของ · ออฟฟิศเห็นการแจก/งานเสร็จ OK")

# หยุดฉุกเฉิน → พักไว้
with db.get_conn() as c:
    c.execute("DELETE FROM agent_tasks")
X.set_paused(True); res = O.plan_and_run(notify=False)
assert res["held"] and res["tasks"] and all(db.get_task(i)["status"] == "held" for i in res["tasks"])
X.set_paused(False)
print("pause: แจกได้แต่พักไว้ OK")

# แชต CEO + หน้า
with db.get_conn() as c:
    c.execute("DELETE FROM agent_tasks")
r = CC.handle("วางแผนวันนี้"); assert r.startswith("CEO แจกงาน:") and "หาลูกค้า" in r
assert CC.handle("งานวันนี้").startswith("CEO แจกงาน:")
admin = "admin@test.local"; db.create_tenant(admin, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Op", is_admin=True)
a = TestClient(app); a.post("/login", data={"email": admin, "password": "pw123456"})
r = a.get("/admin/tasks"); assert "งานที่ CEO แจก" in r.text and "หาลูกค้า" in r.text and "เหตุผล" in r.text
r = a.post("/admin/tasks/plan"); assert r.status_code == 200
assert "CEO แจกงานวันนี้" in O.status_text()
print("chat/page: วางแผนวันนี้ · งานวันนี้ · หน้า /admin/tasks · สรุปเช้า OK")
print("ALL OK")
