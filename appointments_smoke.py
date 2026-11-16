"""ทดสอบ stage 06A-2: เลือกเวลานัดใน LINE · เลื่อน · ยกเลิก · ทีมยืนยัน = นัดสำเร็จ · เตือนผ่านคิวงาน (outbox ทดสอบ ไม่ส่งจริง)
ไม่ยิงเครือข่าย (แทน line_oa._post ด้วยตัวเก็บข้อความ) · DB ชั่วคราว"""
import os, re, json, tempfile, threading, datetime as dt
os.environ.pop("DATABASE_URL", None)
os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["AI_BASE_URL"] = "http://127.0.0.1:9"
os.environ["GEO_DEEP_AUDIT"] = "0"
os.environ["GEO_LINE_OA_ID"] = "@jortest"
for k in list(os.environ):
    if k.startswith("GEO_FLAG_"):
        os.environ.pop(k)
import bcrypt
from fastapi.testclient import TestClient
import app.db as db
import app.pipeline as pipeline
import app.line_oa as LO
import app.line_plan as LP
import app.plan_request as PR
import app.flags as F
import app.jobs as J
import app.appointments as AP
from app.main import app

db.init_db()
alerts, sent = [], []
pipeline.alert_admins = lambda *a, **kw: alerts.append(a[0])
LO.config = lambda: {"token": "tok", "secret": "sec"}
LO._post = lambda path, payload: sent.append((path, payload)) or True
F.set_enabled("line_appointments", True)
F.set_enabled("plan_request_v2", True)


def mk(dom, uid):
    lid = db.add_lead(f"https://{dom}", dom, source="audit", ip="1.2.3.4")
    db.update_lead(lid, token="TK" + dom.split(".")[0].upper()[:8], audit_json=json.dumps({"ok": True, "checks": []}), audit_at=db.now())
    v = {"name": "คุณเอ", "role": "", "contact_channel": "line", "contact_value": "แชต LINE OA (ผูกแล้ว)", "desired_window": "", "web_readiness": "",
         "budget": "", "message": "", "idem_key": (dom.replace(".", "") * 4)[:32].ljust(32, "0"), "marketing": False}
    pid, _ = PR.submit(db.get_lead(lid), v, source="line_oa")
    db.update_lead(lid, line_user_id=uid)
    db.save_line_session(uid, lead_id=lid, plan_id=pid, step="done", fails=0, fail_at=None)
    return lid, pid


def last_reply():
    path, payload = sent[-1]
    assert path.endswith("/reply"), path                                                # ตอบกลับข้อความลูกค้าเท่านั้น ไม่มี push
    return payload["messages"][-1]


def quick(msg):
    return [(i["action"]["label"], i["action"]["data"]) for i in (msg.get("quickReply") or {}).get("items", [])]


lid, pid = mk("shop-a.co.th", "Ua")

# ---------- ยังไม่ตั้งช่องเวลา = แบบเดิม ----------
assert not AP.configured() and AP.slots() == []
assert ("เลือกเวลานัดคุย", "jp:appt:pick") not in quick(LP._done_msg())
assert LP.postback("Ua", "jp:appt:pick", "r0") == "postback_ignored"

# ---------- ตั้งช่องเวลา (ผิด → 422 · ถูก → บันทึก) ----------
db.create_tenant("admin@ap.test", bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Admin", is_admin=True)
adm = TestClient(app)
assert adm.post("/login", data={"email": "admin@ap.test", "password": "pw123456"}).status_code == 200
page = adm.get("/admin/appointments").text
csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
assert adm.post("/admin/appointments/settings", data={"days": ["0"], "times": "10:00"}).status_code == 403
r = adm.post("/admin/appointments/settings", data={"csrf_token": csrf, "days": ["0"], "times": "25:00, 9am", "blackout": "พรุ่งนี้"})
assert r.status_code == 422 and "10:00, 13:30" in r.text and not AP.configured()
r = adm.post("/admin/appointments/settings", data={"csrf_token": csrf, "days": [str(d) for d in range(7)], "times": "14:00, 10:00",
                                                     "duration_min": "30", "lead_hours": "2", "horizon_days": "5", "capacity": "1",
                                                     "blackout": "", "format": "video"}, follow_redirects=False)
assert r.status_code in (302, 303) and AP.configured() and AP.config()["times"] == ["10:00", "14:00"]

# ---------- ช่องเวลา ----------
now = AP.now()
ss = AP.slots(limit=50)
assert ss and all(AP.parse(x) >= now + dt.timedelta(hours=2) for x in ss) and ss == sorted(ss)
assert all(x.endswith("+07:00") for x in ss) and len(AP.slots()) == AP.OFFER_N
d1 = AP.parse(ss[0]).date().isoformat()
AP.save_config({**AP.config(), "blackout": [d1]})
assert all(not x.startswith(d1) for x in AP.slots(limit=50))                              # วันหยุดไม่เสนอ
AP.save_config({**AP.config(), "blackout": []})
assert re.fullmatch(r"(จ|อ|พ|พฤ|ศ|ส|อา)\. \d{1,2} \S+ \d\d:\d\d", AP.label(ss[0])) and len(AP.label(ss[0])) <= 20
assert AP.from_code(AP.slot_code(ss[0])) == ss[0] and AP.from_code("20261399") is None

# ---------- ลูกค้าเลือกเวลาใน LINE ----------
assert ("เลือกเวลานัดคุย", "jp:appt:pick") in quick(LP._done_msg())
assert LP.postback("Ua", "jp:appt:pick", "r1") == "appt_offered"
q = quick(last_reply())
assert len(q) == AP.OFFER_N + 1 and q[-1] == ("ไม่สะดวกช่วงนี้", "jp:appt:none") and all(len(lb) <= 20 for lb, _ in q)
first = q[0][1]
assert LP.postback("Ua", first, "r2") == "appt_requested"
a = AP.active_for_lead(lid)
assert a["status"] == "pending_confirmation" and a["slot_start"] == AP.from_code(first.split(":")[2]) and a["line_user_id"] == "Ua"
assert "ยังไม่ใช่นัดที่ยืนยัน" in last_reply()["text"] and any("รอยืนยัน" in x for x in alerts)
assert a["slot_start"] not in AP.slots(limit=50)                                         # ช่องถูกกันไว้
# ปุ่มปลอม/เวลาที่ไม่ได้เสนอ
past = (AP.now() - dt.timedelta(days=1)).strftime("%Y%m%d") + "1000"
assert LP.postback("Ua", f"jp:slot:{past}", "r3") == "appt_not_offered"
assert LP.postback("Ua", "jp:slot:209912311000", "r3") == "appt_not_offered"
assert LP.postback("Ux", first, "r3") == "postback_ignored"                              # ไม่มีสถานะแชต
assert AP.active_for_lead(lid)["version"] == 1

# ---------- สองลีดแย่งช่องเดียวกัน ----------
lid_b, _ = mk("shop-b.co.th", "Ub")
lid_c, _ = mk("shop-c.co.th", "Uc")
target = AP.slots()[0]
res = []


def pick(l, u):
    try:
        AP.request(l, target, line_user_id=u)
        res.append("ok")
    except (AP.SlotTaken, AP.NotOffered):
        res.append("taken")


ts = [threading.Thread(target=pick, args=(l, u)) for l, u in ((lid_b, "Ub"), (lid_c, "Uc"))]
[t.start() for t in ts]; [t.join() for t in ts]
assert sorted(res) == ["ok", "taken"], res
loser = "Uc" if AP.active_for_lead(lid_b) else "Ub"
assert LP.postback(loser, f"jp:slot:{AP.slot_code(target)}", "r4") in ("appt_taken", "appt_not_offered")
assert "เลือก" in last_reply()["text"]

# ---------- เลื่อนนัด (ลูกค้าพิมพ์) ----------
assert LP.text("Ua", "ขอเลื่อนนัดหน่อยครับ", "r5") == "appt_offered"
new = quick(last_reply())[0][1]
old_slot = AP.active_for_lead(lid)["slot_start"]
assert LP.postback("Ua", new, "r6") == "appt_rescheduled"
a = AP.active_for_lead(lid)
assert a["version"] == 2 and a["slot_start"] != old_slot and old_slot in AP.slots(limit=50)   # ช่องเดิมว่างแล้ว
assert len(AP.history(a)) == 2 and AP.history(a)[-1]["reason"] == "เลื่อนเวลา"

# ---------- ทีมยืนยัน = นัดสำเร็จ ----------
F.set_enabled("durable_jobs", True); F.set_enabled("analytics", True)
J.KICK_INLINE = True
ok, why = AP.confirm(a["id"], "owner@test", version=1)
assert not ok and "เปลี่ยนเวลา" in why                                                    # ทีมเห็นเวลาเก่า = ไม่ยืนยันเวลาเก่า
page = adm.get(f"/admin/leads/{lid}").text
assert 'id="appointment"' in page and "ยังไม่ใช่นัดที่ยืนยัน" in page
assert adm.post(f"/admin/appointments/{a['id']}/confirm", data={"version": "2"}, follow_redirects=False).status_code == 403
r = adm.post(f"/admin/appointments/{a['id']}/confirm", data={"csrf_token": csrf, "version": "2"}, follow_redirects=False)
assert r.status_code in (302, 303)
a = AP.get(a["id"])
assert a["status"] == "confirmed" and a["confirmed_by"] == "admin@ap.test" and a["version"] == 3
lead = db.get_lead(lid)
assert lead["next_kind"] == "meeting" and lead["next_at"] and "นัดคุย" in lead["next_action"]           # ทีมได้เตือนจาก CRM เดิม
assert [e["name"] for e in [dict(x) for x in db.list_analytics_events(50)]].count("meeting_booked") == 1
assert not AP.confirm(a["id"], "x", 3)[0]                                                # กดยืนยันซ้ำไม่มีผล
jobs_ = [j for j in J.list_jobs() if j["kind"] == "appt_message"]
kinds = {json.loads(j["payload_json"])["kind"]: j for j in jobs_}
assert "confirmed" in kinds and kinds["confirmed"]["status"] == "succeeded"
ob = J.outbox_list()
assert len(ob) == 1 and ob[0]["mode"] == "test" and "ยืนยันนัดคุย" in ob[0]["preview"] and ob[0]["recipient_hash"] != "Ua"
for k in ("reminder_24h", "reminder_1h"):
    if k in kinds:
        assert kinds[k]["status"] == "queued" and kinds[k]["available_at"] > db.now()   # เตือนตั้งเวลาไว้ ยังไม่ถึง
assert all(p.endswith("/reply") for p, _ in sent)                                        # ไม่มีการ push จริงเลย
r = c = TestClient(app)
html = c.get(f"/audit/{lead['token']}").text

# ---------- ลูกค้าเลื่อนหลังยืนยัน → เตือนเก่าไม่ทำ ----------
assert LP.postback("Ua", "jp:appt:pick", "r7") == "appt_offered"
assert LP.postback("Ua", quick(last_reply())[0][1], "r8") == "appt_rescheduled"
a = AP.get(a["id"])
assert a["status"] == "pending_confirmation" and a["confirmed_by"] is None
for k in ("reminder_24h", "reminder_1h"):
    if k in kinds:
        with db.get_conn() as cc:
            cc.execute(db.q("UPDATE jobs SET available_at=? WHERE id=?"), (db.now(), kinds[k]["id"]))
J.run_once()
for k in ("reminder_24h", "reminder_1h"):
    if k in kinds:
        assert J.get(kinds[k]["id"])["status"] == "cancelled"
assert len(J.outbox_list()) == 1                                                         # ไม่มีข้อความเตือนเวลาเก่า

# ---------- ลูกค้ายกเลิก (พิมพ์) ----------
assert LP.text("Ua", "ยกเลิกนัดครับ", "r9") == "appt_cancelled"
assert AP.get(a["id"])["status"] == "cancelled" and AP.active_for_lead(lid) is None and any("ยกเลิกนัด" in x for x in alerts)
assert LP.text("Ua", "ยกเลิกนัด", "r10") == "appt_nothing"

# ---------- ทีมยกเลิกนัดที่ยืนยันแล้ว → ข้อความยกเลิกเข้า outbox ----------
LP.postback("Ua", "jp:appt:pick", "r11"); LP.postback("Ua", quick(last_reply())[0][1], "r12")
a2 = AP.active_for_lead(lid)
assert AP.confirm(a2["id"], "owner", a2["version"])[0]
n_ob = len(J.outbox_list())
assert AP.cancel(a2["id"], "owner", "ทีมติดธุระ")
assert len(J.outbox_list()) == n_ob + 1 and "ถูกยกเลิก" in J.outbox_list()[0]["preview"]
assert db.get_lead(lid)["next_kind"] is None

# ---------- ขอไม่รับหลังยืนยัน → เตือนถูกยกเลิก ----------
LP.postback("Ua", "jp:appt:pick", "r13"); LP.postback("Ua", quick(last_reply())[-2][1], "r14")
a3 = AP.active_for_lead(lid)
AP.confirm(a3["id"], "owner", a3["version"])
rem = [j for j in J.list_jobs() if j["kind"] == "appt_message" and json.loads(j["payload_json"])["appt_id"] == a3["id"]
       and json.loads(j["payload_json"])["kind"].startswith("reminder")]
db.update_lead(lid, unsub_at=db.now())
n_ob = len(J.outbox_list())
for j in rem:
    with db.get_conn() as cc:
        cc.execute(db.q("UPDATE jobs SET available_at=? WHERE id=?"), (db.now(), j["id"]))
J.run_once()
assert all(J.get(j["id"])["status"] == "cancelled" for j in rem) and len(J.outbox_list()) == n_ob
db.update_lead(lid, unsub_at=None)

# ---------- หลังเวลานัด: คุยแล้ว / ไม่มา ----------
assert AP.mark(a3["id"], "completed", "owner") == (False, "ยังไม่ถึงเวลานัด")
with db.get_conn() as cc:
    cc.execute(db.q("UPDATE appointments SET slot_start=? WHERE id=?"), ((AP.now() - dt.timedelta(hours=1)).isoformat(timespec="seconds"), a3["id"]))
r = adm.post(f"/admin/appointments/{a3['id']}/complete", data={"csrf_token": csrf}, follow_redirects=False)
assert AP.get(a3["id"])["status"] == "completed"
assert AP.mark(a3["id"], "no_show", "owner")[0] is False

# ---------- รายงานบอกสถานะนัดตามจริง ----------
F.set_enabled("report_v2", True)
LP.postback("Ua", "jp:appt:pick", "r15"); LP.postback("Ua", quick(last_reply())[0][1], "r16")
a4 = AP.active_for_lead(lid)
cu = TestClient(app)
html = cu.get(f"/audit/{db.get_lead(lid)['token']}").text
assert 'data-step="meeting_pending"' in html and "ยังไม่ใช่นัดที่ยืนยัน" in html
AP.confirm(a4["id"], "owner", a4["version"])
assert 'data-step="meeting_confirmed"' in cu.get(f"/audit/{db.get_lead(lid)['token']}").text

# ---------- ลูกค้าขอหยุดติดต่อ → ปุ่มนัดไม่ทำงาน ----------
assert LP.text("Ua", "หยุดติดต่อ", "r17") == "stopped"
assert LP.postback("Ua", "jp:appt:pick", "r18") == "postback_ignored"

# ---------- หน้ารวม + flag ปิด ----------
page = adm.get("/admin/appointments").text
assert "นัดคุยจาก LINE" in page and "ยืนยันแล้ว" in page and "ช่องที่ลูกค้าจะเห็นตอนนี้" in page
assert TestClient(app).get("/admin/appointments", follow_redirects=False).status_code in (302, 303)
F.set_enabled("line_appointments", False)
assert ("เลือกเวลานัดคุย", "jp:appt:pick") not in quick(LP._done_msg())
print("appointments_smoke OK")
