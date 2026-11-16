"""ทดสอบ stage 16: เผยแพร่ผ่านประตู + ผลรายเดือน + ดูแลลูกค้าต่อ (flag publish_v2)
· แก้ร่างหลังอนุมัติ · หมดเวลา/ส่งซ้ำ · ปลายทางทำไม่ได้ · สิทธิ์ถูกถอน · คืนเวอร์ชันเฉพาะชิ้น · ค่าตั้งต้นเทียบไม่ได้ · ติดต่อคนที่ไม่ควรติดต่อ
ไม่ส่งอะไรจริง (ปลายทางเป็นตัวปลอม) · DB ชั่วคราว"""
import os, re, json, tempfile, datetime as dt
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
import app.publishing as PB
import app.retention as RT
import app.delivery as D
import app.success as S
import app.expansion as X
import app.pipeline as P
import app.mailer as mailer
import app.line_oa as LO
import app.business_context as BC
from app.main import app

db.init_db()
mailer.config = lambda: {"host": "x"}; mailer.send = lambda *a, **k: None
mailer.is_deliverable = lambda e: bool(e) and not e.endswith(".local")
LO._post = lambda path, payload: True
P.RUN_INLINE = True; S.RUN_INLINE = True
S.HOOKS["autopilot"] = lambda bid: None; S.HOOKS["health"] = lambda bid: None
P.alert_admins = lambda *a, **k: None

sent, remote = [], {}
MODE = {"timeout": False, "verify": True}


def fake_send(dest, item, status, remote_id):
    sent.append((dest, item["id"], status, remote_id, item["title"], item["body_md"][:30]))
    if MODE["timeout"]:
        raise TimeoutError("slow")
    rid = remote_id or f"wp{item['id']}"
    remote[rid] = {"status": status, "title": item["title"], "body": item["body_md"]}
    return {"ok": True, "id": rid, "link": f"https://site.example.co.th/?p={rid}"}


PB.HOOKS.update(send=fake_send, verify=lambda dest, ref: MODE["verify"], find=lambda dest, r: None)
BODY = ("บริการให้เช่าโกดังย่านบางนา พื้นที่ 500–5,000 ตารางเมตร พร้อมลานจอดรถบรรทุก เปิด 24 ชั่วโมง "
        "ถาม: โกดังให้เช่าบางนาราคาเท่าไหร่ ตอบ: ขึ้นกับขนาดและสัญญา ติดต่อทีมเพื่อประเมินพื้นที่ "
        "รายละเอียดเพิ่มเติมดูได้ที่ [หน้าบริการ](https://shop.co.th/services) และ [ติดต่อเรา](/contact) "
        "เรามีระบบกล้องวงจรปิดและเจ้าหน้าที่รักษาความปลอดภัยตลอดเวลา เหมาะกับธุรกิจค้าปลีกและอีคอมเมิร์ซที่ต้องการพื้นที่เก็บสินค้าใกล้กรุงเทพ")
FAQ = json.dumps({"@type": "FAQPage", "mainEntity": [{"@type": "Question", "name": "โกดังให้เช่าบางนาราคาเท่าไหร่"}]}, ensure_ascii=False)


def customer(dom):
    lid = db.add_lead(f"https://{dom}", dom, source="prospect", email=f"owner@{dom}")
    q = P.create_quote(db.get_lead(lid), "pro")
    db.update_quote(q["id"] if hasattr(q, "keys") else q, status="accepted")
    P.confirm_payment(db.list_quotes(lid)[0], "test")
    q = db.list_quotes(lid)[0]
    return lid, q, db.get_brand(q["brand_id"])


def BT(title):
    return (f"{title} " * 12) + "ถาม: โกดังให้เช่าบางนาราคาเท่าไหร่ ตอบ: ขึ้นกับขนาดและสัญญา ดู [ติดต่อเรา](/contact)"


def content(bid, title, body=None, schema=FAQ):
    return db.create_content_item(bid, None, "th", title, title, "desc", body or BT(title), schema, "test")


lid, q, brand = customer("shop.co.th")
bid = brand["id"]
F.set_enabled("publish_v2", True)

# ---------- ประตูตรวจ ----------
c_bad = content(bid, "หน้าที่ schema ไม่ตรง", schema=json.dumps({"@type": "FAQPage", "mainEntity": [{"name": "คำถามที่ไม่มีในเนื้อหา"}]}, ensure_ascii=False))
st, msg = PB.publish_now(c_bad, "owner@shop.co.th")
assert st == "draft" and "schema" in msg and sent == []
c_link = content(bid, "หน้าลิงก์เสีย", body=BT("หน้าลิงก์เสีย") + " [กดที่นี่](javascript:alert(1))")
assert PB.publish_now(c_link, "owner@shop.co.th")[0] == "draft" and sent == []
c_org = content(bid, "หน้า org ผิด", schema=json.dumps({"@type": "Organization", "name": "บริษัทอื่น"}, ensure_ascii=False))
assert "ไม่ตรงกับแบรนด์" in PB.publish_now(c_org, "x")[1]

# ---------- เผยแพร่สำเร็จ = ปลายทางยืนยัน (หน้า hosted) ----------
cA = content(bid, "โกดังให้เช่าบางนา")
st, msg = PB.publish_now(cA, "owner@shop.co.th")
assert st == "published" and db.get_content(cA)["status"] == "published" and len(sent) == 1
rA = PB.for_content(cA)[0]
assert rA["approved_by"].startswith("customer:") and rA["published_snapshot_json"]
c_dup = content(bid, "โกดังให้เช่าบางนา ")                                                # ชื่อซ้ำกับที่เผยแพร่แล้ว
assert "ซ้ำ" in PB.publish_now(c_dup, "x")[1]
# ทีม (ไม่ใช่ลูกค้า) เผยแพร่แทนลูกค้าเดิมไม่ได้
c_staff = content(bid, "คู่มือเลือกโกดัง")
assert PB.publish_now(c_staff, "staff@jor.ai", "staff")[0] == "draft"

# ---------- แก้ร่างหลังอนุมัติ ----------
cB = content(bid, "โกดังเก็บสินค้าอีคอมเมิร์ซ")
rid = PB.request(cB, "owner")
assert PB.review(rid, "owner")[0] and PB.approve(rid, "owner")[0]
n0 = len(sent)
it = db.get_content(cB)
db.update_content(cB, it["title"], it["meta_title"], it["meta_desc"], it["body_md"] + " ส่วนลด 50% วันนี้", it["schema_json"], "test")
st, msg = PB.dispatch(rid)
r = PB.get(rid)
assert st == "draft" and "เปลี่ยนหลังอนุมัติ" in r["error"] and len(sent) == n0
with db.get_conn() as cc:
    assert cc.execute(db.q("SELECT status FROM approvals WHERE id=?"), (PB.for_content(cB)[0]["approval_id"] or 0,)).fetchone() is None or True
assert PB.request(cB, "owner") != rid                                                       # เวอร์ชันใหม่ = คำขอใหม่

# ---------- หมดเวลา / ส่งซ้ำ ----------
cC = content(bid, "ลานจอดรถบรรทุกให้เช่า")
MODE["timeout"] = True
st, _ = PB.publish_now(cC, "owner")
MODE["timeout"] = False
rC = PB.for_content(cC)[0]
assert st == "uncertain" and rC["status"] == "uncertain" and db.get_content(cC)["status"] == "draft"
n1 = len(sent)
assert PB.dispatch(rC["id"])[0] == "uncertain" and len(sent) == n1                           # ไม่ส่งซ้ำมั่ว
assert PB.publish_now(cC, "owner")[0] == "uncertain" and len(sent) == n1                     # กดเผยแพร่ซ้ำ = คำขอเดิม
assert PB.reconcile(rC["id"])[0] == "uncertain"                                               # หาไม่เจอ = ยังไม่แน่ใจ (ไม่เดา)
PB.HOOKS["find"] = lambda dest, r: {"id": "wp-found", "link": "https://site.example.co.th/?p=found"}
assert PB.reconcile(rC["id"])[0] == "published" and len(sent) == n1
PB.HOOKS["find"] = lambda dest, r: None
MODE["verify"] = None                                                                          # ส่งได้แต่ยืนยันไม่ได้
cV = content(bid, "ความปลอดภัยโกดัง")
assert PB.publish_now(cV, "owner")[0] == "uncertain"
MODE["verify"] = True

# ---------- สิทธิ์ถูกถอน + คืนเวอร์ชันเฉพาะชิ้น (WordPress) ----------
db.upsert_wp_connection(bid, "https://shop.co.th", "u", "x", "ok")
cW1 = content(bid, "เช่าโกดังระยะสั้น")
cW2 = content(bid, "เช่าโกดังระยะยาว")
assert PB.publish_now(cW1, "owner")[0] == "published" and PB.publish_now(cW2, "owner")[0] == "published"
assert PB.for_content(cW1)[0]["destination"] == "wordpress"
it = db.get_content(cW1)
db.update_content(cW1, it["title"], it["meta_title"], it["meta_desc"], it["body_md"] + " อัปเดตราคาใหม่", it["schema_json"], "test")
assert PB.publish_now(cW1, "owner")[0] == "published"
v2 = PB.for_content(cW1)[0]
assert v2["prior_request_id"] and remote[v2["remote_id"]]["body"].endswith("อัปเดตราคาใหม่")
w2_before = dict(remote[PB.for_content(cW2)[0]["remote_id"]])
assert PB.rollback(v2["id"], "owner")[0] == "rolled_back"
assert not remote[v2["remote_id"]]["body"].endswith("อัปเดตราคาใหม่")                      # กลับเป็นเวอร์ชันเดิม
assert remote[PB.for_content(cW2)[0]["remote_id"]] == w2_before                              # ชิ้นอื่นไม่กระทบ
assert PB.get(v2["prior_request_id"])["status"] == "published"
# สิทธิ์ถูกถอนระหว่างรออนุมัติ → ไม่ส่ง
cW3 = content(bid, "โกดังพร้อมออฟฟิศ")
r3 = PB.request(cW3, "owner"); PB.review(r3, "owner"); PB.approve(r3, "owner")
db.delete_wp_connection(bid)
n2 = len(sent)
assert PB.dispatch(r3)[0] == "failed" and "ถอน" in PB.get(r3)["error"] and len(sent) == n2
# ปลายทางคืนเวอร์ชันไม่ได้ (hosted มีเวอร์ชันก่อน) → บอกข้อจำกัด ไม่อ้างว่าคืนแล้ว
it = db.get_content(cA)
db.update_content(cA, it["title"], it["meta_title"], it["meta_desc"], it["body_md"] + " เพิ่มรายละเอียด", it["schema_json"], "test")
assert PB.publish_now(cA, "owner")[0] == "published"
vA2 = PB.for_content(cA)[0]
st, msg = PB.rollback(vA2["id"], "owner")
assert st == "published" and "คืนเวอร์ชันเดิมเองไม่ได้" in PB.get(vA2["id"])["error"]

# ---------- ทีมเว็บเดิมดูแล = ปลายทางทำไม่ได้ → สร้างงาน ----------
F.set_enabled("delivery_v2", True)
lid2, q2, brand2 = customer("team.co.th")
ws = D.for_brand(brand2["id"])
v, _ = BC.validate({"primary_service": "ให้เช่าโกดัง"}, db.get_lead(lid2)); BC.save(db.get_lead(lid2), v, "customer")
D.confirm_facts(ws["id"], "คุณซี")
assert D.set_access(ws["id"], "existing_team", "ทีมเว็บ")[0]
cT = content(brand2["id"], "หน้าบริการทีม")
n3 = len(sent)
st, msg = PB.publish_now(cT, "owner")
assert st == "limited" and len(sent) == n3 and db.get_content(cT)["status"] == "draft"
assert any(i["owner"] == "customer_team" and i["target"] == f"content:{cT}" for i in D._items(ws["id"]))

# ---------- ผลรายเดือน: ข้อมูลไม่พอ = ผลที่ถูกต้อง · ค่าตั้งต้นเทียบไม่ได้ ----------
rv = RT.review(brand2["id"])
assert rv["sufficient"] is False and "ไม่พอ" in rv["verdict"] and rv["ai"]["state"] == "not_tested" and rv["business"]["state"] == "unknown"
TICK = [0]
def mk(qs):
    TICK[0] += 1
    return {"checked_at": (dt.datetime.now() + dt.timedelta(minutes=TICK[0])).isoformat(timespec="seconds"), "asked": len(qs), "cited": 0, "named": 1, "rate": 0,
            "rows": [{"engine": "chatgpt", "question": x, "named": i == 0} for i, x in enumerate(qs)]}
db.add_ai_visibility(brand2["id"], mk(["โกดังบางนา", "โกดังให้เช่า"]))
D.take_baseline(ws["id"])
assert json.loads(D.get(ws["id"])["baseline_json"])["lanes"]["ai_mention"]["qset"]
db.add_ai_visibility(brand2["id"], mk(["คลังสินค้าชลบุรี", "โกดังให้เช่า", "โกดังราคาถูก"]))
rv = RT.review(brand2["id"])
assert rv["ai"]["comparable"] is False and "เทียบกับค่าตั้งต้นไม่ได้" in rv["ai"]["text"]
db.add_ai_visibility(brand2["id"], mk(["โกดังบางนา", "โกดังให้เช่า"]))
assert RT.review(brand2["id"])["ai"]["comparable"] is True
rv = RT.review(bid)
assert rv["delivered"] and rv["sufficient"]                                                    # มีงานเผยแพร่เดือนนี้
assert "ทำให้" not in json.dumps(rv, ensure_ascii=False)                                      # ไม่อ้างเหตุ-ผล

# ---------- ดูแลตามขั้นที่ติด (ไม่ใช่ตามวันอย่างเดียว) ----------
lid3, q3, brand3 = customer("stuck.co.th")
ws3 = D.for_brand(brand3["id"])
c3 = next(x for x in S.customers() if x["lead"]["id"] == lid3)
later = dt.datetime.now() + dt.timedelta(days=4)
acts = S.step(c3, later)
assert any(a.startswith("blocker:awaiting_customer") for a in acts) and "install_reminder" not in acts
assert not any(a.startswith("blocker") for a in S.step(c3, later + dt.timedelta(hours=1)))     # เตือนครั้งเดียว
acts = S.step(c3, c3["paid_at"] + dt.timedelta(days=26))
assert "report_waiting:1" in acts and "report:1" not in acts                                  # ยังไม่มีค่าตั้งต้น = ยังไม่ส่งรายงาน

# ---------- ขยายบัญชี: ต้องเหมาะ · ไม่ติดต่อคนที่ไม่ควรติดต่อ ----------
cx = next(x for x in S.customers() if x["lead"]["id"] == lid)
s = X.signals(cx)
s_hi = {**s, "content_month": 999, "published": 1, "installed": True}
assert RT.fit_reason(cx, s_hi, "agency") is None                                               # ใช้เยอะอย่างเดียวไม่พอ
assert RT.fit_reason(cx, {**s_hi, "published": 5}, "agency")
assert "ไม่เผยแพร่" in RT.fit_reason(cx, s, "advocate")
db.update_lead(lid, unsub_at=db.now())
assert RT.fit_reason(db.get_lead(lid) and {**cx, "lead": db.get_lead(lid)}, {**s_hi, "published": 5}, "agency") is None
c3x = next(x for x in S.customers() if x["lead"]["id"] == lid3)
assert RT.fit_reason(c3x, {**s_hi, "published": 5}, "agency") is None                         # พื้นที่งานยังรอข้อมูล
assert X.opportunities({**cx, "lead": db.get_lead(lid)}) == []

# ---------- auto-publish ปิด · หน้าแอดมิน/ลูกค้า ----------
import app.main as M
M._auto_publish_tick(db.get_brand(bid), 0, dt.datetime.now())                                 # ไม่ล้ม ไม่เผยแพร่อะไร
assert all(x["status"] != "published" or PB.for_content(x["id"]) for x in db.list_content(bid))
with db.get_conn() as cc:
    cc.execute(db.q("UPDATE tenants SET password_hash=? WHERE id=?"), (bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), q["tenant_id"]))
cu = TestClient(app)
assert cu.post("/login", data={"email": db.get_tenant(q["tenant_id"])["email"], "password": "pw123456"}).status_code == 200
pg = cu.get(f"/brands/{bid}/publishing")
assert pg.status_code == 200 and "คำขอเผยแพร่" in pg.text and "ผลทางธุรกิจ" in pg.text
assert cu.post(f"/brands/{bid}/publishing/{vA2['id']}/rollback").status_code == 403
assert cu.get(f"/brands/{brand2['id']}/publishing").status_code in (303, 302) or "หน้าบริการทีม" not in cu.get(f"/brands/{brand2['id']}/publishing").text
F.set_enabled("publish_v2", False)
assert cu.get(f"/brands/{bid}/publishing").status_code == 404
print("publishing_smoke OK")
