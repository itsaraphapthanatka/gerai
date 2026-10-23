"""ทดสอบ SDR ติดตามผล: กำหนดวันที่ 3/7/14 + ห่างขั้นต่ำ 2 วัน · เฉพาะลีดที่หาเอง/นำเข้า (ไม่ติดตามคนที่กรอก /audit เอง)
· หยุดเมื่อตอบ/ขอไม่รับ · ร่างจากข้อมูลจริง (กันคำต้องห้าม/ตัวเลขแต่ง → แม่แบบ) · ข้อเสนอ kind followup ของ SDR · ทำเองตามเรื่อง "ข้อความทัก"
· ส่งแล้วบันทึกไทม์ไลน์ · ตอบกลับยกเลิกที่ค้าง · หน้าแอดมิน + ตั้งค่า"""
import os, tempfile, json, datetime as dt
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke9-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["GEO_DEEP_AUDIT"] = "0"
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.followup as F, app.pipeline as P, app.execs as X, app.mailer as mailer, app.ai_client as ai, app.skills as SK
from app.main import app
db.init_db()
ai._read_setting = lambda k: None
ai._chat = lambda m, max_tokens=2048, timeout=120: (_ for _ in ()).throw(RuntimeError("no model"))
sent = []; mailer.config = lambda: {"host": "x"}; mailer.send = lambda to, subj, html, text=None: sent.append((to, subj, text))
mailer.is_deliverable = lambda e: bool(e) and e.endswith(".co.th")
P.RUN_INLINE = True
REPORT = {"checks": [{"key": k, "label": f"ข้อ {k}", "status": "fail", "detail": ""} for k in ("csr", "noindex", "robots", "sitemap", "canonical")]}
DEEP = {"state": "done", "at": db.now(), "brand": "X", "keywords": ["รับทำบัญชี บางนา"], "health": {"score": 41, "urls": 60, "errors": 9, "warnings": 4},
        "issues": [{"key": "mailto", "severity": "Error", "label": "ลิงก์อีเมลไม่มี mailto:", "count": 12, "urls": [], "group": "Crawl & Index", "fix": ""}],
        "ranking": {"rows": [], "competitors": [{"domain": "rival.co.th", "hits": 4}], "page1": 0, "n": 5, "ok": 5}}

def mk(dom, source="prospect", days_ago=4, email=True):
    lid = db.add_lead(f"https://{dom}", dom, source=source, email=f"info@{dom}" if email else "")
    db.update_lead(lid, status="contacted", audit_json=json.dumps(REPORT), deep_json=json.dumps(DEEP), token=f"t-{dom}")
    eid = db.add_lead_event(lid, "contacted", "ส่งอีเมลแล้ว", "auto")
    when = (dt.datetime.now() - dt.timedelta(days=days_ago)).isoformat(timespec="seconds")
    with db.get_conn() as c:
        c.execute(db.q("UPDATE lead_events SET created_at=? WHERE id=?"), (when, eid))
    return lid

a = mk("acc-a.co.th", days_ago=4)          # ถึงครั้งที่ 1
b = mk("acc-b.co.th", days_ago=1)          # ยังไม่ถึง
cself = mk("self-c.co.th", source="audit", days_ago=10)   # กรอกหน้าตรวจฟรีเอง → ไม่ติดตาม
d = mk("noemail-d.com", days_ago=10, email=False)          # ไม่มีช่องทาง
now = dt.datetime.now()
assert F.next_step(db.get_lead(a), now)["due"] and F.next_step(db.get_lead(a), now)["kind"] == "new_issue"
assert not F.next_step(db.get_lead(b), now)["due"]
assert F.next_step(db.get_lead(cself), now) is None and "สัญญา" in F.eligible(db.get_lead(cself))
assert F.next_step(db.get_lead(d), now) is None and "ไม่มีอีเมล" in F.eligible(db.get_lead(d))
print("schedule: วันที่ 3 · ยังไม่ถึง · ไม่ติดตามคนกรอกเอง · ไม่มีช่องทาง OK")

# ---- ร่าง ----
f = F.facts(db.get_lead(a)); link = "https://x/audit/t#audit"
t = F.draft(db.get_lead(a), "new_issue", f, link, llm=lambda m: json.dumps({"subject": "การันตีอันดับ 1", "body": "x" * 100}))
assert t["source"].startswith("template") and "12 จุด" in t["body"] and link in t["body"]                       # คำต้องห้าม → แม่แบบ
t = F.draft(db.get_lead(a), "new_issue", f, link, llm=lambda m: json.dumps({"subject": "เจอเพิ่ม", "body": "เว็บคุณเสียลูกค้า 300 รายต่อเดือนจากลิงก์อีเมลที่ไม่มี mailto ดูรายงานได้เลยครับ ยาวพอสมควรให้ผ่านเกณฑ์"}))
assert t["source"].startswith("template")                                                                         # ตัวเลขแต่ง (300) → แม่แบบ
t = F.draft(db.get_lead(a), "new_issue", f, link, llm=lambda m: json.dumps({"subject": "เจอเพิ่มบนเว็บคุณ", "body": "สวัสดีครับ ระบบไต่เว็บทั้งเว็บแล้วเจอลิงก์อีเมลที่ไม่มี mailto: 12 จุด ทำให้เกิดหน้าเสีย ดูวิธีแก้ได้ในรายงานครับ"}))
assert t["source"] == "llm" and link in t["body"]                                                                 # ลิงก์ถูกเติมให้เสมอ
t2 = F.draft(db.get_lead(a), "competitor", f, link, llm=lambda m: "พัง"); assert "rival.co.th" in t2["body"] and "4 จาก 5" in t2["body"]
t3 = F.template(db.get_lead(a), "breakup", f, link); assert "ฉบับสุดท้าย" in t3["body"] and "สนใจ" in t3["body"]
assert "แนะนำ" in SK.load("sdr") or "SDR" in SK.load("sdr")
msgs = SK.with_skill("followup", [{"role": "user", "content": "x"}]); assert msgs[0]["role"] == "system" and "SDR" in msgs[0]["content"]
print("draft: ข้อมูลจริง · กันคำต้องห้าม/ตัวเลขแต่ง · ลิงก์ · คู่แข่ง · ปิดแฟ้ม · ทักษะ SDR OK")

# ---- รอบรายวัน: โหมดเสนอ → รอคน ----
res = F.run(); assert res["proposed"] == 1 and res["sent"] == 0 and res["waiting"] >= 1, res
dec = [x for x in db.list_decisions("proposed") if x["kind"] == "followup"]; assert len(dec) == 1 and dec[0]["agent"] == "sdr" and dec[0]["lead_id"] == a
assert F.run()["proposed"] == 0                                                                                    # มีข้อเสนอค้าง ไม่เสนอซ้ำ
admin = "admin@test.local"; db.create_tenant(admin, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Op", is_admin=True)
c = TestClient(app); c.post("/login", data={"email": admin, "password": "pw123456"})
r = c.post(f"/admin/decisions/{dec[0]['id']}/approve"); assert r.status_code == 200
assert sent and sent[-1][0] == "info@acc-a.co.th" and "/u/" in sent[-1][2]                                        # ลิงก์ขอไม่รับทุกฉบับ
evs = db.list_lead_events(a); assert evs[0]["kind"] == "followup" and "ครั้งที่ 1" in evs[0]["note"]
assert db.get_lead(a)["status"] == "contacted" and F.next_step(db.get_lead(a))["step"] == 2 and not F.next_step(db.get_lead(a))["due"]   # ห่าง ≥ 2 วัน
print("run: เสนอ → อนุมัติ → ส่ง + ลิงก์ไม่รับ → ไทม์ไลน์ · ครั้งถัดไปรอ OK")

# ---- ทำเองตามเรื่อง "ข้อความทัก" · ตอบกลับหยุด ----
e = mk("acc-e.co.th", days_ago=8)
for k in ("outreach",):
    db.set_setting(f"auto_override_{k}", "1"); db.set_setting(f"auto_{k}", "1")
assert X.auto_enabled("followup")
res = F.run(); assert res["sent"] == 1, res
assert any(x[0] == "info@acc-e.co.th" for x in sent)
db.set_setting("auto_followup", "0"); assert not X.auto_enabled("followup")                                       # ปิดแยกได้
db.set_setting("auto_followup", None) if False else None
g = mk("acc-g.co.th", days_ago=5)
F.run(); pend = [x for x in db.list_decisions("proposed") if x["kind"] == "followup" and x["lead_id"] == g]; assert pend
P.lead_replied(db.get_lead(g), "สนใจครับ")
assert db.get_decision(pend[0]["id"])["status"] == "cancelled" and F.next_step(db.get_lead(g)) is None
h = mk("acc-h.co.th", days_ago=20)
P.unsubscribe(db.get_lead(h)); assert F.next_step(db.get_lead(h)) is None
print("auto/stop: ทำเองตามข้อความทัก · ปิดแยกได้ · ตอบกลับยกเลิกที่ค้าง · ขอไม่รับหยุด OK")

# ---- ครบ 3 ครั้ง ----
k = mk("acc-k.co.th", days_ago=30)
for i in range(3):
    db.add_lead_event(k, "followup", f"ครั้งที่ {i+1}", "auto")
assert F.next_step(db.get_lead(k)) is None
# ---- หน้าแอดมิน + ตั้งค่า + สรุปเช้า ----
r = c.get(f"/admin/leads/{a}"); assert "SDR ติดตามผล" in r.text and "ส่งแล้ว 1/3" in r.text
r = c.get(f"/admin/leads/{cself}"); assert "ไม่ส่งซ้ำถ้าไม่ตอบ" in r.text
r = c.get("/admin/settings"); assert "SDR ติดตามผลลีด" in r.text
c.post("/admin/settings/prospect", data={"segments": "", "daily": "10"}); assert not F.enabled() and "ปิดอยู่" in F.run()["skipped"]
c.post("/admin/settings/prospect", data={"segments": "", "daily": "10", "followup_on": "1"}); assert F.enabled()
assert "ติดตามผล" in F.status_text() or F.status_text() == ""
r = c.get("/admin/decisions"); assert "ติดตามผล" in r.text
print("admin: สถานะในหน้าลีด · เหตุผลไม่ติดตาม · สวิตช์ในตั้งค่า · หน้าการตัดสินใจ OK")
print("ALL OK")
