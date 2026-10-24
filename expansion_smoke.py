"""ทดสอบฝ่ายขยายบัญชี: ยังไม่ติดตั้ง/ยังใหม่ = ไม่เสนอ · รายเดือนครบ ~2 เดือน+ใกล้รอบบิล → รายปี (ตัวเลขประหยัดจริง) · Pro ใช้ใกล้เต็ม → Agency
· คอนเทนต์เต็มโควตา → แพ็กเสริม · เผยแพร่ ≥ 3 → เคสศึกษา+พันธมิตร · เสนอครั้งเดียว · รออนุมัติ → ใบเสนอราคาใหม่ + อีเมล
· ลูกค้าจ่ายใบใหม่ → แผนเปลี่ยนทันที ยกเลิก Stripe เดิม + ใบแจ้งหนี้ค้าง ไม่ส่งอีเมลต้อนรับซ้ำ · แชต CEO · ทะเบียน"""
import os, tempfile, json, datetime as dt
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke-expand-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["GEO_DEEP_AUDIT"] = "0"; os.environ["GEO_CS"] = "0"
import app.db as db, app.expansion as E, app.pipeline as P, app.success as S, app.mailer as mailer, app.stripe_pay as SP, app.accounting as A
import app.ai_client as ai, app.line_oa as LO, app.ceo_chat as C, app.office as O, app.skills as SK, app.execs as X, app.billing as B
db.init_db()
ai._read_setting = lambda k: None
ai._chat = lambda m, max_tokens=2048, timeout=120: (_ for _ in ()).throw(RuntimeError("no model"))
mails = []; mailer.config = lambda: {"host": "x", "smtp_from": "report@sender.test"}
mailer.send = lambda to, subj, html, text="", attachments=(): mails.append((to, subj, text))
mailer.is_deliverable = lambda e: bool(e) and e.endswith(".co.th")
LO._post = lambda path, payload: True
P.alert_admins = lambda *a, **k: None
A.HOOKS["pdf"] = lambda html: b"%PDF"
inst = {"v": False}; S.installed = lambda b: (inst["v"], "x")
deleted = []; SP._delete = lambda path: deleted.append(path)

def customer(dom, pkg="pro", cycle="month", sub=None):
    lid = db.add_lead(f"https://{dom}", dom, source="prospect", email=f"owner@{dom}")
    q = P.create_quote(db.get_lead(lid), pkg, 0, False, cycle=cycle); db.update_quote(q["id"], status="claimed", stripe_subscription_id=sub)
    P.confirm_payment(db.get_quote(q["id"]), "test")
    return lid, db.list_quotes(lid)[0]
def age(q, days):
    with db.get_conn() as c: c.execute(db.q("UPDATE quotes SET paid_at=? WHERE id=?"), ((dt.datetime.now() - dt.timedelta(days=days)).isoformat(timespec="seconds"), q["id"]))
cust = lambda lid: next(x for x in S.customers() if x["lead"]["id"] == lid)

# ---- รายปี ----
lid, q = customer("monthly.co.th", sub="sub_old")
now = dt.datetime.now()
age(q, 57); paid = dt.date.fromisoformat(db.get_quote(q["id"])["paid_at"][:10])
assert E.opportunities(cust(lid)) == []                                                    # ยังไม่ติดตั้ง = ไม่เสนอ
inst["v"] = True
s = E.signals(cust(lid)); assert s["days"] == 57 and s["next_bill"] == A.add_months(paid, 2).isoformat(), s
far = dt.datetime.combine(A.add_months(paid, 2) - dt.timedelta(days=15), dt.time(9))
assert "yearly" not in E.opportunities(cust(lid), far)                                     # ยังไกลรอบบิล
near = dt.datetime.combine(A.add_months(paid, 2) - dt.timedelta(days=5), dt.time(9))
r = E.run(near); assert r["proposed"] == ["monthly.co.th:yearly"], r
assert E.run(near)["proposed"] == []                                                        # ไม่เสนอซ้ำ
d = [x for x in db.list_decisions("proposed", 20) if x["kind"] == "expansion"][0]
pl = json.loads(d["payload_json"]); y = __import__("app.quotes", fromlist=["x"]).price_for("pro", 0, False, "year")
assert d["status"] == "proposed" and f"ประหยัด {9900 * 12 - y['period_total']:,} บาท" in pl["body"] and "ยกเลิกการเก็บเงินรายเดือนเดิม" in pl["body"]
mails.clear(); out = P.execute_decision(d, "owner"); db.set_decision(d["id"], "executed", "owner", out)
nq = db.list_quotes(lid)[0]; assert nq["id"] != q["id"] and nq["cycle"] == "year" and nq["status"] == "sent" and nq["created_by"] == "expand"
assert mails and mails[-1][0] == "owner@monthly.co.th" and f"/q/{nq['token']}" in mails[-1][2] and db.get_lead(lid)["status"] == "won"
print("yearly: ไม่ติดตั้ง/ไกลรอบบิล = ไม่เสนอ · ใกล้รอบบิล → เสนอครั้งเดียว · ตัวเลขประหยัด · อนุมัติ → ใบรายปีใหม่ + อีเมล OK")

# ---- จ่ายใบใหม่ → หยุดเก็บเงินแผนเดิม ----
inv = A.issue_invoice(db.get_quote(q["id"]), A.add_months(paid, 2), send_mail=False)
mails.clear(); db.update_quote(nq["id"], status="claimed"); res = P.confirm_payment(db.get_quote(nq["id"]), "owner")
assert deleted == ["/subscriptions/sub_old"] and db.get_acct_doc(inv["id"])["status"] == "void" and "อัปเกรดจาก" in res, res
assert any("อัปเกรด" in m[1] for m in mails) and not any("พร้อมแล้ว" in m[1] for m in mails)             # ไม่ส่งอีเมลต้อนรับซ้ำ
assert any(e["kind"] == "exp_upgraded" for e in db.list_lead_events(lid)) and cust(lid)["quote"]["id"] == nq["id"]
print("upgrade: จ่ายใบใหม่ → ยกเลิก Stripe เดิม · ยกเลิกใบแจ้งหนี้ค้าง · อีเมลอัปเกรด (ไม่ใช่ต้อนรับ) OK")

# ---- Agency · แพ็กเสริม · เคสศึกษา ----
lid2, q2 = customer("busy.co.th"); age(q2, 30)
t2 = cust(lid2)["tenant"]
for i in range(3): db.create_brand(t2["id"], f"b{i}", f"https://b{i}.busy.co.th")
assert E.signals(cust(lid2))["brands"] >= 4 and "agency" in E.opportunities(cust(lid2))
r = E.run(); assert "busy.co.th:agency" in r["proposed"]
da = [x for x in db.list_decisions("proposed", 30) if x["kind"] == "expansion" and x["lead_id"] == lid2][0]
assert "แบรนด์ 4/5" in json.loads(da["payload_json"])["body"]
P.execute_decision(da, "owner"); assert db.list_quotes(lid2)[0]["package"] == "agency"
lid3, q3 = customer("writer.co.th"); age(q3, 50)
b3 = cust(lid3)["brand"]
for i in range(3):
    cid = db.create_content_item(b3["id"], None, "th", f"บทความ {i}", "t", "d", "body", "[]", "test")
    db.mark_content_published(cid, None, None)
B.usage = (lambda orig: (lambda tid: {**orig(tid), "content": 30} if tid == cust(lid3)["tenant"]["id"] else orig(tid)))(B.usage)
ops = E.opportunities(cust(lid3)); assert "agency" in ops or "content" in ops, ops
assert S.content_counts(b3["id"])["published"] == 3 and "advocate" in ops
assert "/partners?src=customer" in E.draft(cust(lid3), "advocate", E.signals(cust(lid3)))["body"]
print("agency/content/advocate: ใช้ใกล้เต็ม → Agency (ใบใหม่) · ชนโควตา → แพ็ก/อัปเกรด · เผยแพร่ ≥ 3 → เคสศึกษา+พันธมิตร OK")

# ---- แชต · ทะเบียน · ปิดได้ ----
assert "ขยายบัญชี" in C.handle("สรุปขยายบัญชีลูกค้าเดิม")
assert "expand" in O.IDS and any(x[0] == "expand" for x in O.SCHEDULE) and "expand" in SK.AGENTS and SK.load("expand").startswith("# ทักษะ") and X.KINDS["expansion"]
db.set_setting("expand_on", "0"); assert E.run()["skipped"] == "ปิดอยู่"
print("\nexpansion_smoke: ผ่านทั้งหมด")
