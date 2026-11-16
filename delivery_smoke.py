"""ทดสอบ stage 15: ส่งมอบตามขอบเขต + พื้นที่งานลูกค้า (flag delivery_v2)
· จ่ายแล้วแต่ยังไม่มีสิทธิ์เข้าถึง · ทีมเว็บเดิมเป็นเจ้าของงาน · ข้อมูลเปลี่ยนทำให้ร่างใช้ไม่ได้ · งานที่ต้องรอ · ส่งมอบบางส่วน
· สิทธิ์แพ็กเกจเดิมไม่เปลี่ยน · ไม่มี Autopilot วิ่งเองโดยไม่ตั้งใจ · ลูกค้าเดิมไม่กระทบ · พันธมิตรเห็นเฉพาะที่ควรเห็น
ไม่ส่งอะไรจริง · DB ชั่วคราว"""
import os, re, json, tempfile
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
import app.db as db
import app.flags as F
import app.delivery as D
import app.success as S
import app.pipeline as P
import app.mailer as mailer
import app.line_oa as LO
import app.billing as billing
import app.business_context as BC
import app.main as M
from app.main import app

db.init_db()
mailer.config = lambda: {"host": "x"}; mailer.send = lambda *a, **k: None
mailer.is_deliverable = lambda e: bool(e) and not e.endswith(".local")
LO._post = lambda path, payload: True
P.RUN_INLINE = True; S.RUN_INLINE = True
runs = []
S.HOOKS["autopilot"] = lambda bid: runs.append(bid)
S.HOOKS["health"] = lambda bid: None
DEEP = {"state": "done", "brand": "Shop", "keywords": ["โกดังให้เช่า"],
        "issues": [{"key": "title_missing", "severity": "Error", "label": "หน้าไม่มีชื่อหน้า", "fix": "ใส่ชื่อหน้า", "urls": ["https://shop-b.co.th/contact"]},
                   {"key": "duplicate_title", "severity": "Warning", "label": "ชื่อหน้าซ้ำ", "urls": ["https://shop-b.co.th/a", "https://shop-b.co.th/b"]},
                   {"key": "img_alt", "severity": "Notice", "label": "รูปไม่มีคำอธิบาย", "urls": []}],
        "pages": [{"url": "https://shop-b.co.th/", "title": "Shop B หน้าแรก"}]}


def customer(dom, email):
    lid = db.add_lead(f"https://{dom}", dom, source="prospect", email=email)
    db.update_lead(lid, deep_json=json.dumps(DEEP, ensure_ascii=False))
    q = P.create_quote(db.get_lead(lid), "pro")
    db.update_quote(q["id"] if hasattr(q, "keys") else q, status="accepted")
    q = db.list_quotes(lid)[0]
    P.confirm_payment(q, "test")
    return lid, db.list_quotes(lid)[0]


# ---------- flag ปิด = แบบเดิม (ลูกค้าเดิม) ----------
lid0, q0 = customer("legacy.co.th", "owner@legacy.co.th")
assert runs == [q0["brand_id"]] and D.list_all() == []
F.set_enabled("delivery_v2", True)
assert D.autopilot_gate(q0["brand_id"]) == (True, None, "")                                   # ลูกค้าเดิมไม่มีพื้นที่งาน = ตามเดิม

# ---------- จ่ายแล้ว → พื้นที่งาน "รอข้อมูล" · ไม่วิ่งเอง ----------
runs.clear()
lid, q = customer("shop-b.co.th", "owner@shop-b.co.th")
bid = q["brand_id"]
ws = D.for_brand(bid)
assert ws and ws["state"] == "awaiting_customer" and ws["publish_allowed"] == 0 and runs == []
assert M.run_autopilot(bid)["skipped"] and db.last_autopilot_run(bid) is None             # รอบรายสัปดาห์/กดเองก็ไม่วิ่ง
c = next(x for x in S.customers() if x["lead"]["id"] == lid)
import datetime as dt
S.step(c, c["paid_at"] + dt.timedelta(days=7, hours=1))
assert runs == []                                                                            # วันที่ 7 ไม่สั่ง Autopilot ซ้ำเอง
assert D.open_for_quote(q) == ws["id"]                                                       # เปิดซ้ำ = ที่เดิม
# สิทธิ์แพ็กเกจเดิมไม่เปลี่ยน
tenant = db.get_tenant(q["tenant_id"])
ent = D.entitlements(tenant)
assert tenant["plan"] == "pro" and ent["content_month"] == billing.PLANS["pro"]["content_month"] and ent["runs_month"] == billing.PLANS["pro"]["runs_month"]
assert D.scope(ws["id"])["status"] == "draft" and str(billing.PLANS["pro"]["content_month"]) in json.dumps(D.scope(ws["id"])["scope"], ensure_ascii=False)
db.set_setting("offer_drafts", json.dumps([{"key": "sprint", "label": "Foundation Sprint", "deliverables": ["ตรวจ", "แผน"]}]))
assert D.offer_drafts()[0]["price_thb"] is None and D.offer_drafts()[0]["status"] == "draft"  # ไม่เติมราคาเอง

# ---------- ข้อมูลธุรกิจ: ต้องยืนยันจริง + ผู้อนุมัติ ----------
wid = ws["id"]
assert D.confirm_facts(wid, "คุณเอ")[0] is False                                             # ยังเป็นค่าที่ระบบเดา
v, err = BC.validate({"primary_service": "ให้เช่าโกดัง", "region": "บางนา"}, db.get_lead(lid)); assert not err
BC.save(db.get_lead(lid), v, "customer")
assert D.confirm_facts(wid, "")[0] is False
assert D.confirm_facts(wid, "คุณเอ")[0] and D.get(wid)["state"] == "awaiting_access"

# ---------- จ่ายแล้วแต่ยังไม่มีสิทธิ์เข้าถึง ----------
ok, msg = D.plan(wid)
assert not ok and "สิทธิ์เข้าถึง" in msg and D.autopilot_gate(bid)[0] is False
ok, msg = D.set_access(wid, "connector")
assert not ok and D.get(wid)["state"] == "awaiting_access"                                     # ตรวจไม่ผ่าน = ไม่นับว่ามีสิทธิ์
assert D.set_access(wid, "existing_team", "")[0] is False

# ---------- ทีมเว็บเดิมเป็นเจ้าของงาน ----------
assert D.set_access(wid, "existing_team", "คุณบี (ทีมเว็บ)", publish_allowed=True)[0]
assert D.get(wid)["publish_allowed"] == 0                                                     # ทีมเดิมเผยแพร่เอง เราไม่ได้สิทธิ์
assert D.get(wid)["state"] == "ready"
assert not D.plan(wid)[0]                                                                     # ยังไม่มีผลวัดตั้งต้น
base = D.take_baseline(wid)
assert base["lanes"]["ai_mention"]["state"] == "not_tested" and base["lanes"]["ai_mention"]["value"] is None
assert D.plan(wid)[0]
its = D.view(wid)["work"]
kinds = [i["kind"] for i in its]
assert "investigate" in kinds and "consolidate" in kinds and kinds.count("fix_technical") == 1    # Notice ไม่เข้าแผน · ซ้ำ = รวมหน้า
assert all(i["owner"] == "customer_team" for i in its if i["kind"] in ("fix_technical", "consolidate"))
assert all(i["evidence"] is not None and i["acceptance"] for i in its)

# AI ไม่เอ่ยถึง 12 คำถาม → งานเดียวที่หน้าบริการ ไม่ใช่ 12 บทความ
db.add_ai_visibility(bid, {"checked_at": db.now(), "asked": 12, "cited": 0, "named": 0, "rate": 0, "results": []})
D.take_baseline(wid)
assert D.get(wid)["baseline_json"] and json.loads(D.get(wid)["baseline_json"])["lanes"]["ai_mention"]["state"] == "absent"
assert D.plan(wid)[0]
its = D.view(wid)["work"]
kinds = [i["kind"] for i in its]
assert kinds.count("create_page") + kinds.count("update_existing") == 1 and "create_article" not in kinds and "internal_links" in kinds
assert "investigate" not in kinds                                                              # แผนเดิมถูกแทน ไม่ซ้อน

# ---------- แผนต้องอนุมัติก่อนเริ่ม · งานที่ต้องรอ ----------
page = next(i for i in its if i["kind"] in ("create_page", "update_existing"))
links = next(i for i in its if i["kind"] == "internal_links")
assert links["depends_on"] == page["id"]
assert D.move(page["id"], "start")[0] is False                                                 # ยังไม่อนุมัติแผน
assert D.approve_plan(wid, "คุณเอ")[0]
ok, msg = D.move(links["id"], "start")
assert not ok and "ต้องเสร็จก่อน" in msg and D.item(links["id"])["status"] == "blocked"
assert D.move(page["id"], "start")[0] and D.get(wid)["state"] == "in_progress"
gate = D.autopilot_gate(bid)
assert gate[0] is True and gate[1] == "draft"                                                  # เริ่มงานแล้ววิ่งได้ แต่เป็นร่างเท่านั้น

# ---------- ส่งมอบบางส่วน ----------
assert D.move(page["id"], "review")[0] and D.get(wid)["state"] == "review"
assert D.move(page["id"], "deliver")[0]
assert D.get(wid)["state"] == "in_progress"                                                    # ยังมีงานค้าง ≠ ส่งมอบแล้ว
vw = D.view(wid)
assert len(vw["done"]) == 1 and len(vw["ongoing"]) >= 3 and any("ตรวจงาน" in w for w in vw["waiting"])
assert any("ทีมเว็บของคุณ" in w for w in vw["waiting"])
assert D.move(links["id"], "start")[0]                                                         # งานที่รอปลดล็อกแล้ว

# ---------- ข้อมูลธุรกิจเปลี่ยน → ร่าง/งานที่ยังไม่ส่งใช้ไม่ได้ ----------
v2, _ = BC.validate({"primary_service": "รับฝากสินค้า", "region": "ลาดกระบัง"}, db.get_lead(lid))
BC.save(db.get_lead(lid), v2, "customer")
n = D.sync_facts(wid)
w = D.get(wid)
assert n >= 1 and w["state"] == "awaiting_customer" and not w["plan_approved_at"]
assert D.item(links["id"])["status"] == "stale" and D.item(page["id"])["status"] == "delivered"   # ที่ส่งแล้วไม่ย้อน
assert D.move(links["id"], "start")[0] is False
assert D.autopilot_gate(bid)[0] is False

# ---------- พันธมิตร: เห็นแค่สถานะ/ขั้นถัดไป ----------
db.update_lead(lid, partner_id=777)
rows = D.partner_rows(777)
assert rows and rows[0]["domain"] == "shop-b.co.th" and set(rows[0]) == {"domain", "state", "next"}
assert D.partner_rows(778) == []

# ---------- หน้าลูกค้า / แอดมิน ----------
with db.get_conn() as cc:
    cc.execute(db.q("UPDATE tenants SET password_hash=? WHERE id=?"), (bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), q["tenant_id"]))
cu = TestClient(app)
assert cu.post("/login", data={"email": tenant["email"], "password": "pw123456"}).status_code == 200
pg = cu.get("/app/delivery")
assert pg.status_code == 200 and "รอคุณอยู่" in pg.text and "ยืนยันข้อมูลธุรกิจ" in pg.text and "ไม่รวม" in pg.text
assert "งานส่งมอบ" in cu.get("/app").text
cs = re.search(r'name="csrf_token" value="([^"]+)"', pg.text).group(1)
assert cu.post(f"/app/delivery/{wid}/facts", data={"primary_service": "รับฝากสินค้า", "approver": "คุณเอ"}).status_code == 403
cu.post(f"/app/delivery/{wid}/facts", data={"csrf_token": cs, "primary_service": "รับฝากสินค้า", "region": "ลาดกระบัง", "approver": "คุณเอ"})
assert D.get(wid)["state"] == "ready"                                                          # ยืนยันใหม่แล้ว สิทธิ์เดิมยังอยู่ แผนต้องร่างใหม่
cu.post(f"/app/delivery/items/{page['id']}/accept", data={"csrf_token": cs})
assert D.item(page["id"])["status"] == "accepted"
other = db.create_tenant("other@x.co.th", bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Other")
ou = TestClient(app); ou.post("/login", data={"email": "other@x.co.th", "password": "pw123456"})
opg = ou.get("/app/delivery").text
assert "shop-b" not in opg.lower() and "คุณเอ" not in opg                                       # ไม่เห็นงานของบัญชีอื่น
db.create_tenant("own@dl.test", bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Own", is_admin=True)
adm = TestClient(app)
assert adm.post("/login", data={"email": "own@dl.test", "password": "pw123456"}).status_code == 200
apg = adm.get("/admin/delivery")
assert apg.status_code == 200 and "Foundation Sprint" in apg.text and "ยังไม่ตั้งราคา" in apg.text
acs = re.search(r'name="csrf_token" value="([^"]+)"', apg.text).group(1)
adm.post(f"/admin/delivery/{wid}/cancel", data={"csrf_token": acs, "reason": "ลูกค้าขอหยุด"})
assert D.get(wid)["state"] == "cancelled" and D.autopilot_gate(bid)[0] is False
F.set_enabled("delivery_v2", False)
assert cu.get("/app/delivery").status_code == 404 and D.autopilot_gate(bid) == (True, None, "")   # ปิด flag = แบบเดิม
print("delivery_smoke OK")
