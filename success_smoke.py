"""ทดสอบดูแลหลังการขาย: ยืนยันยอด → เริ่มงานทันที (แผน 30 วัน + Autopilot) · วันที่ 3 ติดตั้งแล้ว/ยังไม่ติดตั้ง (เตือน + วันที่ 7 เสี่ยงหลุด)
· วันที่ 7 งานชิ้นแรก (มีร่าง → ชวนตรวจ · ไม่มี → สั่ง Autopilot อีกรอบ → วันที่ 10 เสี่ยงหลุด) · วันที่ 25 รายงานเดือน · แผนฟรี = เสี่ยงหลุด
· ทำแต่ละเรื่องครั้งเดียว · ไทม์ไลน์/การ์ดในหน้าลีด · สวิตช์ในตั้งค่า"""
import os, tempfile, json, datetime as dt
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke10-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["GEO_DEEP_AUDIT"] = "0"
os.environ["GEO_CS"] = "1"
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.success as S, app.pipeline as P, app.mailer as mailer, app.line_oa as LO
from app.main import app
db.init_db()
mails = []; mailer.config = lambda: {"host": "x"}; mailer.send = lambda to, subj, html, text=None: mails.append((to, subj, text))
mailer.is_deliverable = lambda e: bool(e) and not e.endswith(".local")
LO._post = lambda path, payload: True
P.RUN_INLINE = True; S.RUN_INLINE = True
runs = []; S.HOOKS["autopilot"] = lambda bid: runs.append(bid)
health = {}
def fake_health(bid):
    rep = {"checks": [{"key": "connector", "label": "ตัวเชื่อม", "status": "ok" if health.get(bid) else "fail", "detail": "ติดตั้งแล้ว" if health.get(bid) else "/geo ตอบ 404"}]}
    db.add_health_check(bid, {"checked_at": dt.datetime.now().isoformat(timespec="microseconds"), "ok": bool(health.get(bid)), "n_fail": 0 if health.get(bid) else 1, "n_warn": 0, "checks": rep["checks"]})
S.HOOKS["health"] = fake_health

def customer(dom, email):
    lid = db.add_lead(f"https://{dom}", dom, source="prospect", email=email)
    q = P.create_quote(db.get_lead(lid), "pro")
    db.update_quote(q["id"] if isinstance(q, dict) or hasattr(q, "keys") else q, status="accepted")
    q = db.list_quotes(lid)[0]
    P.confirm_payment(q, "test")
    return lid, db.list_quotes(lid)[0]

lid, q = customer("shop-a.co.th", "owner@shop-a.co.th")
assert runs == [q["brand_id"]], runs                                                          # Autopilot รอบแรกทันที
kick = [m for m in mails if "แผน 30 วันแรก" in m[1]]; assert kick and kick[0][0] == "owner@shop-a.co.th" and "/assets" in kick[0][2]
assert "cs_kickoff" in S.done_keys(lid)
c = next(x for x in S.customers() if x["lead"]["id"] == lid)
assert S.step(c, c["paid_at"] + dt.timedelta(hours=5)) == []                                 # ทำครั้งเดียว
print("kickoff: ยืนยันยอด → แผน 30 วัน + ชุดติดตั้ง + Autopilot ทันที · ไม่ซ้ำ OK")

# ---- วันที่ 3/7: ยังไม่ติดตั้ง → เตือน → เสี่ยงหลุด ----
mails.clear(); d3 = c["paid_at"] + dt.timedelta(days=3, hours=1)
acts = S.step(c, d3); assert acts == ["install_reminder"], acts
assert any("ติดตั้งตัวเชื่อม" in m[1] for m in mails) and "cs_install_reminder" in S.done_keys(lid)
acts = S.step(c, c["paid_at"] + dt.timedelta(days=7, hours=1)); assert "risk:install" in acts and "autopilot_rerun" in acts, acts   # ไม่มีคอนเทนต์ด้วย
assert len(runs) == 2
assert S.step(c, c["paid_at"] + dt.timedelta(days=8)) == []                                  # เตือนครั้งเดียว
health[c["brand"]["id"]] = True
assert "installed" in S.step(c, c["paid_at"] + dt.timedelta(days=9))
print("install: วันที่ 3 เตือน · วันที่ 7 เสี่ยงหลุด + Autopilot อีกรอบ · ติดตั้งแล้วบันทึก OK")

# ---- วันที่ 10 ไม่มีคอนเทนต์ = เสี่ยงหลุด · มีร่าง → ชวนตรวจ ----
acts = S.step(c, c["paid_at"] + dt.timedelta(days=10, hours=1)); assert "risk:content" in acts, acts
bid = c["brand"]["id"]
db.create_content_item(bid, None, "th", "บทความ 1", "t", "d", "body", "[]", "test")
mails.clear(); acts = S.step(c, c["paid_at"] + dt.timedelta(days=11)); assert acts == ["first_content", "review_nudge"], acts
assert any("รอคุณตรวจ" in m[1] and "/content" in m[2] for m in mails)
print("content: วันที่ 10 เสี่ยงหลุด · มีร่าง → งานชิ้นแรก + ชวนตรวจ OK")

# ---- วันที่ 25 รายงานเดือน (ครั้งเดียวต่อเดือน) · เดือนถัดไป ----
mails.clear(); acts = S.step(c, c["paid_at"] + dt.timedelta(days=25, hours=1)); assert acts == ["report:1"], acts
assert any("รายงานเดือนที่ 1" in m[1] and "/report" in m[2] and "เผยแพร่แล้ว 0" in m[2] for m in mails)
assert S.step(c, c["paid_at"] + dt.timedelta(days=27)) == []
assert S.step(c, c["paid_at"] + dt.timedelta(days=55, hours=1)) == ["report:2"]
print("report: วันที่ 25 · ครั้งเดียวต่อเดือน · เดือนที่ 2 OK")

# ---- แผนฟรี = เสี่ยงหลุด · รอบรายวัน · หน้าแอดมิน ----
lid2, q2 = customer("shop-b.co.th", "owner@shop-b.co.th")
db.set_plan(q2["tenant_id"], "free")
res = S.run(); assert res["customers"] == 2 and res["actions"]["shop-b.co.th"] == ["risk:free"], res
assert "ดูแลลูกค้า: ลูกค้า 2 ราย" in S.status_text()
admin = "admin@test.local"; db.create_tenant(admin, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Op", is_admin=True)
a = TestClient(app); a.post("/login", data={"email": admin, "password": "pw123456"})
r = a.get(f"/admin/leads/{lid}"); assert "ดูแลหลังการขาย" in r.text and "เริ่มงาน" in r.text and "ดูแลลูกค้า: รายงานเดือน" in r.text
r = a.get("/admin/settings"); assert "ดูแลลูกค้าหลังการขาย" in r.text
a.post("/admin/settings/prospect", data={"segments": "", "daily": "10", "followup_on": "1"}); assert not S.enabled() and S.run()["skipped"]
print("free/run/admin: แผนฟรีเสี่ยงหลุด · รอบรายวัน · การ์ดในหน้าลีด · สวิตช์ OK")
# ---- ยืนยันยอดด้วยมือไม่ได้ ถ้า Stripe บอกว่ายังไม่จ่าย ----
import app.stripe_pay as SP
lid3 = db.add_lead("https://shop-c.co.th", "shop-c.co.th", source="prospect", email="owner@shop-c.co.th")
q3 = P.create_quote(db.get_lead(lid3), "pro"); db.update_quote(q3["id"], status="accepted", stripe_session_id="cs_test_x")
SP.session_paid = lambda q: False
r = a.post(f"/admin/quotes/{q3['id']}/confirm"); assert db.get_quote(q3["id"])["status"] == "accepted" and "Stripe ยังไม่ได้รับเงิน" in r.text
SP.session_paid = lambda q: None                                              # ไม่มี session/ถามไม่ได้ → ยืนยันได้ตามเดิม
db.set_setting("cs_on", "0"); a.post(f"/admin/quotes/{q3['id']}/confirm"); assert db.get_quote(q3["id"])["status"] == "paid"
print("guard: Stripe ยังไม่จ่าย → ห้ามยืนยันด้วยมือ OK")
print("ALL OK")
