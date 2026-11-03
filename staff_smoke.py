"""ทดสอบทีมงานหลังบ้าน + แยกสิทธิ์ (app/staff.py, /admin/staff)
· ทุก route /admin เข้าหมวดตั้งใจ (route ใหม่ที่ลืมใส่กฎ = เจ้าของเท่านั้น แต่เทสต์นี้จะเตือน)
· แอดมินเดิม (ไม่มี staff_role) = เจ้าของ · ฝ่ายขายเข้าดีลได้ บัญชี/ตั้งค่าไม่ได้ · ดูอย่างเดียวกดบันทึกไม่ได้
· เมนูข้างซ่อนหมวดที่ไม่มีสิทธิ์ · แบรนด์ลูกค้าต้องมีหมวดลูกค้า · เปลี่ยนสิทธิ์/ปิดบัญชีมีผลทันที
· กันล็อกเจ้าของคนสุดท้าย · คนที่ไม่ใช่เจ้าของแก้บัญชีทีมงานด้วยกันไม่ได้ · แจ้งเตือนส่งตามสิทธิ์ · ประวัติการทำงาน"""
import os, tempfile, re
os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-staff-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.main as M, app.staff as S

db.init_db()
pw = lambda p: bcrypt.hashpw(p.encode(), bcrypt.gensalt(4)).decode()


def login(email, password="pass12345"):
    c = TestClient(M.app)
    r = c.post("/login", data={"email": email, "password": password}, follow_redirects=False)
    assert r.status_code == 303, (email, r.status_code)
    return c


# ---- กฎ: ทุก route หลังบ้านเข้าหมวด · ตัวอย่างที่เจาะจง ----
paths = sorted({r.path for r in M.app.routes if getattr(r, "path", "").startswith("/admin")})
loose = [p for p in paths if S.area_for(p) == S.OWNER_ONLY and not p.startswith("/admin/staff")]
assert not loose, f"route หลังบ้านที่ยังไม่มีหมวดใน staff.RULES: {loose}"
assert S.area_for("/admin") is None and S.area_for("/app") == "" and S.area_for("/admin/something-new") == "staff"
assert S.area_for("/admin/quotes/5/send") == "sales" and S.area_for("/admin/quotes/5/confirm") == "finance"
assert S.area_for("/admin/inbox") == "sales" and S.area_for("/admin/inbox/settings") == "settings"
assert S.area_for("/admin/deliverability") == "marketing" and S.area_for("/admin/deliverability/add") == "settings"
assert S.area_for("/admin/accounting.csv") == "finance" and S.area_for("/admin/customers.csv") == "customers"
print(f"กฎ: route หลังบ้าน {len(paths)} เส้นเข้าหมวดครบ · ตัวที่ไม่มีกฎ = เจ้าของเท่านั้น OK")

# ---- บทบาท/สิทธิ์ ----
assert S.normalize("sales", {}) == ("sales", None)
assert S.normalize("custom", {a: v for a, v in S.ROLES["finance"][1].items()}) == ("finance", None)   # ตรงชุด = เก็บเป็นบทบาท
r, pj = S.normalize("custom", {"sales": "view", "bogus": "edit", "finance": "root"})
assert r == "custom" and pj == '{"sales": "view"}'
legacy = {"is_admin": 1, "disabled": 0, "staff_role": None}
assert S.role_key(legacy) == "owner" and S.is_owner(legacy)                     # แอดมินเดิม = เจ้าของ
assert not S.can({"is_admin": 0}, "sales") and not S.can({**legacy, "disabled": 1}, "sales")
print("บทบาท: แอดมินเดิม = เจ้าของ · ตรงชุดเก็บเป็นบทบาท · ค่าแปลกถูกทิ้ง · ปิดบัญชี = ไม่มีสิทธิ์ OK")

owner_id = db.create_tenant("owner@x.test", pw("pass12345"), "Owner", is_admin=True)
cust_id = db.create_tenant("cust@x.test", pw("pass12345"), "Cust")
db.set_plan(cust_id, "pro")
brand = db.create_brand(cust_id, "ร้านลูกค้า", "cust.example", "ร้านค้า")
own = login("owner@x.test")

# ---- เจ้าของเพิ่มทีมงาน ----
r = own.get("/admin/staff")
assert r.status_code == 200 and "ทีมงานและสิทธิ์" in r.text and "owner@x.test" in r.text
assert own.post("/admin/staff", data={"email": "s@x.test", "name": "Sale", "password": "short", "role": "sales"}).url.query.startswith(b"error=")
assert "error=" in str(own.post("/admin/staff", data={"email": "cust@x.test", "password": "pass12345", "role": "sales"}).url)   # บัญชีลูกค้าเป็นทีมงานไม่ได้
r = own.post("/admin/staff", data={"email": "sale@x.test", "name": "Sale", "password": "pass12345", "role": "sales"})
assert "notice=" in str(r.url)
sale = db.get_tenant_by_email("sale@x.test")
assert sale["is_admin"] and S.role_key(sale) == "sales" and sale["plan"] == "unlimited"
own.post("/admin/staff", data={"email": "view@x.test", "password": "pass12345", "role": "viewer"})
print("เพิ่มทีมงาน: รหัสสั้น/อีเมลลูกค้าถูกปฏิเสธ · เพิ่มฝ่ายขาย + ดูอย่างเดียว OK")

# ---- ฝ่ายขาย: ดีลได้ · บัญชี/ตั้งค่า/ทีมงานไม่ได้ · เมนูตามสิทธิ์ · แบรนด์ลูกค้าดูได้ แก้ไม่ได้ ----
sc = login("sale@x.test")
assert sc.get("/admin/deals").status_code == 200 and sc.get("/admin/leads").status_code == 200
for p in ("/admin/accounting", "/admin/settings", "/admin/staff", "/admin/marketing"):
    r = sc.get(p)
    assert r.status_code == 403 and "ไม่มีสิทธิ์เข้าหน้านี้" in r.text, p
assert sc.get("/admin/office/state.json").status_code == 403 and sc.get("/admin/office/state.json").json()["area"] == "agents"
r = sc.get("/admin")
assert r.status_code == 200 and 'href="/admin/deals"' in r.text and 'href="/admin/settings"' not in r.text and 'href="/admin/staff"' not in r.text
assert "สร้างบัญชีลูกค้าใหม่" not in r.text and "/admin/tenants/" not in r.text     # หมวดบัญชีผู้ใช้ลูกค้า: ไม่มีสิทธิ์
assert "แบรนด์ทั้งหมด" in r.text                                                     # หมวดลูกค้าและผลงาน: ดูได้
assert sc.get(f"/brands/{brand}").status_code == 200                              # ลูกค้าและผลงาน: ดูได้
assert sc.post("/admin/free-plan", data={"on": "1"}).status_code == 403
assert db.get_setting("free_plan_on") is None
print("ฝ่ายขาย: ดีล/ลีดเข้าได้ · บัญชี ตั้งค่า ทีมงาน การตลาด = 403 · JSON ได้ 403 · เมนูซ่อน · ตั้งค่าไม่เปลี่ยน OK")

# ---- ดูอย่างเดียว: เปิดได้ กดบันทึกไม่ได้ ----
vc = login("view@x.test")
assert vc.get("/admin/deals").status_code == 200 and vc.get("/admin/accounting").status_code == 200
r = vc.post("/admin/contacts/1/handled")
assert r.status_code == 403 and "ดูได้อย่างเดียว" in r.text
assert vc.get("/admin/settings").status_code == 403
print("ดูอย่างเดียว: เปิดทุกหมวดยกเว้นตั้งค่า/ทีมงาน · กดบันทึก = 403 'ดูได้อย่างเดียว' OK")

# ---- ไม่ใช่เจ้าของแก้บัญชีทีมงานด้วยกันไม่ได้ (กันเปลี่ยนอีเมลเจ้าของ) ----
mgr_id = db.create_tenant("mgr@x.test", pw("pass12345"), "Mgr", is_admin=True)
db.set_staff(mgr_id, "manager")
mc = login("mgr@x.test")
assert mc.post(f"/admin/tenants/{owner_id}/email", data={"email": "evil@x.test"}).status_code == 403
assert db.get_tenant(owner_id)["email"] == "owner@x.test"
assert mc.post(f"/admin/tenants/{cust_id}/email", data={"email": "cust2@x.test"}).status_code == 200
assert db.get_tenant(cust_id)["email"] == "cust2@x.test"
assert mc.get("/admin/staff").status_code == 403
print("ผู้จัดการ: แก้อีเมลลูกค้าได้ · แก้อีเมลเจ้าของ = 403 · หน้าทีมงาน = 403 OK")

# ---- เปลี่ยนสิทธิ์มีผลทันที (ไม่ต้องล็อกอินใหม่) · กำหนดเอง ----
r = own.post(f"/admin/staff/{sale['id']}/perms", data={"role": "custom", "lv_sales": "view", "lv_finance": "edit"})
assert "notice=" in str(r.url)
sale = db.get_tenant(sale["id"])
assert S.role_key(sale) == "custom" and S.perms(sale)["finance"] == "edit" and S.perms(sale)["customers"] == "none"
assert sc.get("/admin/accounting").status_code == 200 and sc.post("/admin/contacts/1/handled").status_code == 403
assert sc.get(f"/brands/{brand}", follow_redirects=False).status_code == 303                      # ไม่มีหมวดลูกค้าแล้ว = เปิดแบรนด์ลูกค้าไม่ได้
print("สิทธิ์ใหม่มีผลทันที: บัญชีเข้าได้ · ดีลดูอย่างเดียว · แบรนด์ลูกค้าเข้าไม่ได้ OK")

# ---- กันล็อกเจ้าของคนสุดท้าย · ปิดตัวเองไม่ได้ ----
r = own.post(f"/admin/staff/{owner_id}/perms", data={"role": "sales"})
assert "error=" in str(r.url) and S.is_owner(db.get_tenant(owner_id))
assert "error=" in str(own.post(f"/admin/staff/{owner_id}/active", data={"on": ""}).url)
print("เจ้าของคนสุดท้าย: ลดสิทธิ์/ปิดตัวเองไม่ได้ OK")

# ---- ปิดบัญชี: ออกจากระบบทันที · ล็อกอินไม่ได้ · เปิดคืนได้ ----
own.post(f"/admin/staff/{sale['id']}/active", data={"on": ""})
r = sc.get("/admin/accounting", follow_redirects=False)
assert r.status_code == 303 and r.headers["location"] == "/login"
assert sc.get("/app", follow_redirects=False).headers["location"] == "/login"          # session ถูกล้างแล้ว
r = TestClient(M.app).post("/login", data={"email": "sale@x.test", "password": "pass12345"})
assert "ถูกปิดการใช้งาน" in r.text
own.post(f"/admin/staff/{sale['id']}/active", data={"on": "1"})
sc = login("sale@x.test")
print("ปิดบัญชี: ออกจากระบบทันที · ล็อกอินไม่ได้ · เปิดคืนแล้วเข้าได้ OK")

# ---- ตั้งรหัสใหม่ / เปลี่ยนอีเมลจากหน้าทีมงาน ----
own.post(f"/admin/staff/{sale['id']}/password", data={"password": "newpass123"})
login("sale@x.test", "newpass123")
own.post(f"/admin/staff/{sale['id']}/email", data={"email": "sale2@x.test"})
assert db.get_tenant(sale["id"])["email"] == "sale2@x.test"
print("ตั้งรหัสใหม่ + เปลี่ยนอีเมลทีมงาน OK")

# ---- แจ้งเตือนตามสิทธิ์ ----
before = {t: db.count_unread(t) for t in (owner_id, sale["id"], mgr_id)}
db.notify_admins("ทดสอบงานตั้งค่า", "/admin/settings")
db.notify_admins("ทดสอบดีล", "/admin/leads/1")
after = {t: db.count_unread(t) for t in before}
assert after[owner_id] - before[owner_id] == 2 and after[mgr_id] - before[mgr_id] == 2   # ผู้จัดการ: ตั้งค่าดูได้
assert after[sale["id"]] - before[sale["id"]] == 1                                      # กำหนดเอง: ดีลดูได้ ตั้งค่าไม่ได้
assert S.receives(db.get_tenant(sale["id"]), "https://geo.example/admin/leads/3?x=1") and not S.receives(db.get_tenant(sale["id"]), "/admin/settings")
print("แจ้งเตือน: ส่งเฉพาะทีมงานที่เปิดลิงก์นั้นได้ OK")

# ---- ประวัติการทำงาน ----
log = db.list_staff_log(200)
assert any(l["method"] == "LOGIN" and l["tenant_id"] == sale["id"] for l in log)
assert any(l["method"] == "POST" and l["path"] == f"/admin/tenants/{cust_id}/email" and l["tenant_id"] == mgr_id for l in log)
assert all(l["status"] == "denied" for l in log if l["method"] == "GET")             # เปิดดูปกติไม่เก็บ · เปิดหน้าที่ไม่มีสิทธิ์เก็บ
assert any(l["status"] == "denied" and l["path"] == "/admin/free-plan" and l["action"] == "เปิด/ปิดแผน Free" for l in log)
r = own.get("/admin/staff")
assert "ประวัติการแก้ไขล่าสุด" in r.text and "แก้อีเมลบัญชี" in r.text and "เข้าสู่ระบบ" in r.text and "ถูกปฏิเสธ" in r.text
print("ประวัติ: เข้าสู่ระบบ + ทุกการบันทึก + ที่ถูกปฏิเสธ (ไม่เก็บการเปิดดูปกติ) แสดงที่หน้าทีมงาน OK")

# ---- ประวัติการแก้ไข: ค่าก่อน → หลัง ----
import json
last = lambda **kw: S_A.view(db.list_staff_log(1, **kw)[0])
import app.staff_audit as S_A
e = last(tenant_id=mgr_id, status="ok")                                                          # ผู้จัดการแก้อีเมลลูกค้าไว้ข้างบน
assert e["action"] == "แก้อีเมลบัญชี" and e["target"] == f"tenants:{cust_id}" and e["target_label"] == f"บัญชี #{cust_id}"
assert {"f": "อีเมล", "a": "cust@x.test", "b": "cust2@x.test"} in e["changes"] and e["form"] == {"email": "cust2@x.test"}
lead = db.add_lead("https://deal.example", "deal.example", name="Deal Co")
own.post(f"/admin/deals/{lead}/value", data={"value": "18,000"})
e = last(target=f"leads:{lead}")
assert e["action"] == "แก้มูลค่าดีล" and {"f": "มูลค่าดีล", "a": "—", "b": "18000"} in e["changes"], e["changes"]
own.post("/admin/free-plan", data={"on": "1"})
e = last(tenant_id=owner_id)
assert {"f": "ตั้งค่า: free_plan_on", "a": "—", "b": "1"} in e["changes"], e["changes"]
own.post(f"/admin/staff/{sale['id']}/password", data={"password": "another123"})
e = last(tenant_id=owner_id)
assert e["form"] == {"password": "•••"} and {"f": "password_hash", "a": "•••", "b": "••• (เปลี่ยนแล้ว)"} in e["changes"]
assert "another123" not in json.dumps([dict(r) for r in db.list_staff_log(500)], ensure_ascii=False)   # รหัสจริงไม่ถูกเก็บที่ไหนเลย
db.set_setting("uptime:9", "x")                                                       # งานเบื้องหลังเขียน setting ไม่นับเป็นการแก้ของทีม
assert S_A.diff({"a": "1", "smtp_pass": "old"}, {"a": "2", "smtp_pass": "new"}) == [
    {"f": "a", "a": "1", "b": "2"}, {"f": "smtp_pass", "a": "•••", "b": "••• (เปลี่ยนแล้ว)"}]
assert S_A.diff({"x": 1}, None) == [{"f": "แถว", "a": "มีอยู่", "b": "ลบแล้ว"}]
posts = {re.sub(r"\{\w+\}", "1", r.path) for r in M.app.routes if "POST" in getattr(r, "methods", ()) and r.path.startswith("/admin")}
unnamed = sorted(p for p in posts if S_A.action_label(p) == p)
assert not unnamed, f"route หลังบ้านที่ยังไม่มีชื่อการกระทำใน staff_audit.ACTIONS: {unnamed}"
print("แก้ไข: ค่าก่อน→หลังของแถว + ค่าตั้งระบบ · รหัส/คีย์เป็น ••• · route หลังบ้านทุกเส้นมีชื่อการกระทำ OK")

# ---- หน้าประวัติ: กรองตามคน/รายการ/ผล/คำค้น · หน้าลีดมีประวัติ · ฝ่ายขายเข้าหน้าประวัติไม่ได้ ----
r = own.get(f"/admin/staff/history?target=leads:{lead}")
assert r.status_code == 200 and "แก้มูลค่าดีล" in r.text and "แก้อีเมลบัญชี" not in r.text and f"ลีด #{lead}" in r.text
assert "แก้อีเมลบัญชี" in own.get(f"/admin/staff/history?who={mgr_id}").text
r = own.get("/admin/staff/history?status=denied")
assert "ถูกปฏิเสธ" in r.text and "แก้มูลค่าดีล" not in r.text
assert "cust2@x.test" in own.get("/admin/staff/history?q=cust2").text
r = own.get(f"/admin/leads/{lead}")
assert "ประวัติการแก้ไขโดยทีมงาน" in r.text and "18000" in r.text
assert sc.get("/admin/staff/history").status_code == 403
print("หน้าประวัติ: กรองได้ทุกแบบ · หน้าลีดแสดงประวัติของลีดนั้น · ไม่ใช่เจ้าของเข้าไม่ได้ OK")

# ---- เจ้าของเห็นครบ ----
r = own.get("/admin")
assert all(f'href="{h}"' in r.text for h in ("/admin/settings", "/admin/staff", "/admin/accounting")) and "สร้างบัญชีลูกค้าใหม่" in r.text
print("ALL OK")
