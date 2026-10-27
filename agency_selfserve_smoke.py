"""โหมดเอเจนซี + พันธมิตร: บัญชีแผน Agency (เอเจนซี/พันธมิตร) ใช้แอปเองได้ · แผนอื่นทีมดูแล (ดูผล) · เจ้าของตั้งรายบัญชีได้ที่ /admin
· เปิดบัญชีจากใบราคาตั้งเอง = ทีมดูแล · จากแพ็กเกจ Agency ตามตาราง = ใช้เอง · ฟอร์มใบเสนอราคาเลือก "แพ็กเกจมาตรฐาน" = ราคาตาราง"""
import os, tempfile
os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-ss-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["GEO_DEEP_AUDIT"] = "0"; os.environ["GEO_CS"] = "0"
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.pipeline as P, app.quotes as Q
from app.main import app

db.init_db()
P.alert_admins = lambda *a, **k: None
db.set_setting("agency_mode", "1")


def login(email, admin=False, plan=None):
    if not db.get_tenant_by_email(email):
        db.create_tenant(email, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), email.split("@")[0], is_admin=admin)
    t = db.get_tenant_by_email(email)
    if plan:
        db.set_plan(t["id"], plan)
    c = TestClient(app); c.post("/login", data={"email": email, "password": "pw123456"})
    return c, db.get_tenant_by_email(email)


def blocked(c, bid):
    r = c.post(f"/brands/{bid}/questions", data={"question": "new question", "language": "en"}, follow_redirects=False)
    return r.status_code in (302, 303) and "managed=1" in r.headers.get("location", "")


biz, tb = login("biz@example.com", plan="pro")
agy, ta = login("agency@example.com", plan="agency")
bb = db.create_brand(tb["id"], "Biz", "biz.example.com", "retail")
ba = db.create_brand(ta["id"], "Agency Client", "client.example.com", "retail")
assert blocked(biz, bb) and not blocked(agy, ba)
assert not db.tenant_self_serve(db.get_tenant(tb["id"])) and db.tenant_self_serve(db.get_tenant(ta["id"]))
print("ตามแผน: Pro = ทีมดูแล (สั่งงานเองไม่ได้) · Agency = เอเจนซี/พันธมิตรใช้เอง OK")

adm, _ = login("admin@example.com", admin=True)
r = adm.get("/admin"); assert "ใช้แอปเอง" in r.text and "ทีมดูแล (ดูผล)" in r.text
adm.post(f"/admin/tenants/{ta['id']}/self-serve", data={"mode": "managed"})
adm.post(f"/admin/tenants/{tb['id']}/self-serve", data={"mode": "self"})
assert blocked(agy, ba) and not blocked(biz, bb)
adm.post(f"/admin/tenants/{ta['id']}/self-serve", data={"mode": "auto"})
assert db.get_tenant(ta["id"])["self_serve"] is None and not blocked(agy, ba)
print("รายบัญชี: เจ้าของตั้งใช้เอง/ทีมดูแล/ตามแผน ที่ /admin ได้ OK")

# ---- เปิดบัญชีจากใบเสนอราคา ----
def paid_tenant(domain, **kw):
    lid = db.add_lead(f"https://{domain}", domain, source="manual")
    db.update_lead(lid, email=f"owner@{domain}", name=domain)
    q = P.create_quote(db.get_lead(lid), created_by="admin", cycle="month", **kw)
    P.provision_customer(db.get_quote(q["id"]))
    return db.get_tenant_by_email(f"owner@{domain}")

tc = paid_tenant("custom.example.com", package=Q.CUSTOM_BASE, custom=15000, custom_label=Q.CUSTOM_LABEL)
assert tc["self_serve"] == 0 and not db.tenant_self_serve(tc)
tp = paid_tenant("partner.example.com", package="agency")
assert tp["self_serve"] == 1 and db.tenant_self_serve(tp)
print("เปิดบัญชี: ราคาตั้งเอง = ทีมดูแล · แพ็กเกจ Agency ตามตาราง = ใช้เอง OK")

# ---- ฟอร์มใบเสนอราคา: แพ็กเกจมาตรฐาน = ราคาตาราง แม้มีค่าในช่องราคาตั้งเอง ----
lid = db.add_lead("https://pkg.example.com", "pkg.example.com", source="manual"); db.update_lead(lid, email="owner@pkg.example.com")
r = adm.get(f"/admin/leads/{lid}"); assert 'name="pricing" value="package"' in r.text and "แพ็กเกจมาตรฐาน" in r.text
adm.post(f"/admin/leads/{lid}/quote", data={"pricing": "package", "package": "agency", "price": "1234", "cycle": "month"})
q = db.list_quotes(lid)[0]
assert q["package"] == "agency" and not Q.is_custom(q) and Q.price_for("agency")["net"] == q["net"]
adm.post(f"/admin/leads/{lid}/quote", data={"pricing": "custom", "package": "pro", "price": "4321", "cycle": "month"})
q2 = db.list_quotes(lid)[0]; assert Q.is_custom(q2) and q2["net"] == 4321
print("ใบเสนอราคา: เลือกแพ็กเกจมาตรฐาน = ราคาตาราง · ตั้งราคาเอง = ราคาที่พิมพ์ OK")

# ---- พันธมิตรยังเปิดรับ ----
assert TestClient(app).get("/partners").status_code == 200 and "พันธมิตร" in TestClient(app).get("/").text
print("พันธมิตร: หน้าสมัครเปิด · หน้าแรกมีลิงก์ OK")
print("ALL OK")
