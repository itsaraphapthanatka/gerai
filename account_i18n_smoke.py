"""ทดสอบการแปลหน้าเข้าสู่ระบบ บัญชี และพันธมิตร: /login · /partners · /u/<token> · /account/password · /upgrade · /upgrade/pay/<item> · /partner/<token>
ทุกภาษา (en zh ja ko): เปิดได้ · <html lang> ถูก · ไม่มีไทยในข้อความที่เห็น (หน้าที่อยู่ใน base.html ตรวจเฉพาะ <main> และ <title>)
· ข้อความที่โค้ดส่งมา (ข้อผิดพลาด/แจ้งเตือน/สถานะ) แปลด้วย · ภาษาไทยยังแสดงข้อความเดิม"""
import os, tempfile, re, html as H, datetime as dt
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-acct-i18n-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["GEO_DEEP_AUDIT"] = "0"; os.environ["GEO_CS"] = "0"
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.i18n as I, app.pipeline as P
from app.main import app

db.init_db()
I.load(force=True)
P.alert_admins = lambda *a, **k: None
OTHER = [l for l in I.LANGS if l != "th"]
THAI = re.compile(r"[฀-๿]")
ATTR = re.compile(r'(?:placeholder|aria-label|alt|title|content)="([^"]*)"')


def visible(html: str) -> str:
    v = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>", " ", html)
    return re.sub(r"เจอ\.AI|เจอ|฿|ไทย", "", v)


def no_thai(html: str, where: str, scope_main: bool = False):
    part = html
    if scope_main:
        m = re.search(r"<main>([\s\S]*)</main>", html); assert m, where
        t = re.search(r"<title>([\s\S]*?)</title>", html)
        part = m.group(1) + " " + (t.group(1) if t else "")
    left = re.findall(r"\S*[฀-๿]+\S*", visible(part))
    left += [a for a in ATTR.findall(part) if THAI.search(re.sub(r"เจอ\.AI|฿", "", a))]
    assert not left, (where, left[:8])


def check(c, url, lg, scope_main=False, must=None, method="get", **kw):
    if method == "get":
        r = c.get(url + ("&" if "?" in url else "?") + "lang=" + lg, **kw)
    else:
        c.get("/login?lang=" + lg)                                       # จำภาษาลงคุกกี้ก่อน แล้วค่อยส่งฟอร์ม
        r = c.post(url, **kw)
    assert r.status_code == 200, (url, lg, r.status_code)
    if not scope_main:
        assert f'<html lang="{I.HTML_LANG[lg]}">' in r.text, (url, lg)
    if lg != "th":
        no_thai(r.text, f"{method} {url} [{lg}]", scope_main)
    for s in (must or []):
        assert s in r.text or s in H.unescape(r.text), (url, lg, s)
    return r


EN = {"login": "Sign in", "partners": "Become a partner", "unsub": "No further contact", "pw": "Change password", "upgrade": "Upgrade plan",
      "pay": "Payment", "dash": "Partner dashboard"}
JA = {"login": "ログイン", "partners": "パートナーに申し込む", "dash": "パートナーダッシュボード", "upgrade": "プランのアップグレード"}

# ---- หน้าสาธารณะ ----
c = TestClient(app)
for lg in OTHER:
    check(c, "/login", lg)
    check(c, "/partners", lg)
check(c, "/login", "en", must=[EN["login"], "Get your brand<br>into <span class=\"ac\">AI answers</span>"])
check(c, "/login", "ja", must=[JA["login"]])
r = check(c, "/login", "th", must=["เข้าสู่ระบบ", "ให้แบรนด์คุณ<br>โผล่ใน<span class=\"ac\">คำตอบ AI</span>", "บัญชีสร้างโดยผู้ดูแลระบบ"])
assert '<html lang="th">' in r.text
check(c, "/partners", "en", must=[EN["partners"], "Refer clients to เจอ.AI"])
check(c, "/partners", "th", must=["สมัครเป็นพันธมิตร", "ค่าแนะนำคิดจากใบเสร็จจริง"])
for lg, s in (("en", "Incorrect email or password"), ("zh", "邮箱或密码不正确"), ("th", "อีเมลหรือรหัสผ่านไม่ถูกต้อง")):
    check(c, "/login", lg, method="post", data={"email": "nobody@example.com", "password": "x"}, must=[s])
for lg, s in (("ja", "先にパートナー規約への同意にチェックしてください"), ("ko", "올바른 이메일을 입력해 주세요"), ("th", "ติ๊กยอมรับกติกาพันธมิตรก่อน")):
    data = {"name": "Test", "email": "p1@example.com"} if lg != "ko" else {"name": "Test", "email": "bad", "agree": "1"}
    check(c, "/partners", lg, method="post", data=data, must=[s])
check(c, "/partners", "en", method="post", data={"name": "Test Person", "email": "p2@example.com", "agree": "1"}, must=["We've received your application"])
print("public: /login · /partners × 4 ภาษา · ข้อผิดพลาดจากโค้ดแปลได้ · ไทยคงเดิม OK")

# ---- ขอไม่รับการติดต่อ ----
for lg in OTHER + ["th"]:
    tok = f"unsubtok{lg}"
    db.add_lead("https://example.com", f"{lg}.example.com", token=tok)
    check(c, f"/u/{tok}", lg, must=[f"{lg}.example.com"] + ([EN["unsub"]] if lg == "en" else ["ยืนยัน ไม่รับการติดต่อ"] if lg == "th" else []))
    check(c, f"/u/{tok}", lg, method="post", must=[f"{lg}.example.com"] + (["เรียบร้อย"] if lg == "th" else []))
print("unsubscribe: /u/<token> ก่อน/หลังยืนยัน × 5 ภาษา OK")

# ---- หน้าในระบบ (ล็อกอิน) ----
email = "owner@example.com"
db.create_tenant(email, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Owner")
u = TestClient(app); u.post("/login", data={"email": email, "password": "pw123456"})
for lg in OTHER:
    check(u, "/account/password", lg, scope_main=True)
    check(u, "/upgrade", lg, scope_main=True)
    check(u, "/upgrade/pay/pro", lg, scope_main=True)
    check(u, "/upgrade/pay/content20", lg, scope_main=True)
check(u, "/account/password", "en", scope_main=True, must=[EN["pw"], "Account owner@example.com"])
check(u, "/upgrade", "en", scope_main=True, must=[EN["upgrade"], "Current plan", "/month", "Everything in Pro"])
check(u, "/upgrade", "ja", scope_main=True, must=[JA["upgrade"]])
check(u, "/upgrade/pay/pro", "en", scope_main=True, must=["Payment · Pro plan", "<code>PROMPTPAY_ID</code>"])
for lg, s in (("en", "Your current password is incorrect"), ("ko", "새 비밀번호 두 개가 일치하지 않습니다"), ("th", "รหัสผ่านปัจจุบันไม่ถูกต้อง")):
    data = {"current": "wrong", "new": "newpass123", "confirm": "newpass123"} if lg != "ko" else {"current": "pw123456", "new": "newpass123", "confirm": "other1234"}
    check(u, "/account/password", lg, scope_main=True, method="post", data=data, must=[s])
check(u, "/upgrade", "zh", scope_main=True, method="post", data={"plan": "agency"}, must=["已提交 Agency 套餐申请 — 团队会与你联系", "已提交 <b>Agency</b> 套餐申请 — 团队正在处理"])
db.set_setting("promptpay_id", "0800000000")
check(u, "/upgrade/pay/pro", "ja", scope_main=True, must=["PromptPay でスキャンして支払う", "受取人:"])
check(u, "/upgrade/pay/pro", "en", scope_main=True, method="post", files={"slip": ("slip.png", b"\x89PNG fake", "image/png")}, must=["Payment notice received"])
check(u, "/upgrade/pay/brand", "ko", scope_main=True, method="post", files={"slip": ("slip.png", b"\x89PNG fake", "image/png")}, must=["추가 패키지 결제 알림"])
r = u.get("/upgrade?lang=th"); assert "อัปเกรดแผน" in r.text and "แผนปัจจุบัน" in r.text and "ส่งคำขอแผน <b>Pro</b> ไว้แล้ว" in r.text   # แจ้งชำระแผน Pro ข้างบน = ขอแผน Pro
r = u.get("/upgrade/pay/pro?lang=th"); assert "ชำระเงิน · แผน Pro" in r.text and "สแกนจ่ายด้วย PromptPay" in r.text
db.create_tenant("admin@example.com", bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Op", is_admin=True)
adm = TestClient(app); adm.post("/login", data={"email": "admin@example.com", "password": "pw123456"})
check(adm, "/upgrade", "en", scope_main=True, must=["You're an administrator", "Customer plan"])
print("app: /account/password · /upgrade · /upgrade/pay × 4 ภาษา (เฉพาะ <main>) · แจ้งเตือนจากโค้ดแปลได้ · ไทยคงเดิม OK")

# ---- แดชบอร์ดพันธมิตร ----
pid = db.add_partner(status="active", kind="agency", code="exref", name="Test Partner", company="Example Agency", email="partner@example.com")
p = db.get_partner(pid)
for i, st in enumerate(["new", "contacted", "won", "unsubscribed"]):
    lid = db.add_lead(f"https://c{i}.example.com", f"c{i}.example.com"); db.update_lead(lid, partner_id=pid, status=st)
soon = (dt.datetime.now() + dt.timedelta(days=5)).isoformat(timespec="seconds")
db.add_commission(partner_id=pid, lead_id=lid, base=9900, pct=20, amount=1980, status="pending", eligible_at=soon, note="RC-0001")
db.add_commission(partner_id=pid, lead_id=lid, base=9900, pct=20, amount=1980, status="void", note="RC-0002 · ใบเสร็จถูกยกเลิก")
db.add_commission(partner_id=pid, lead_id=lid, base=9900, pct=20, amount=1980, status="approved", note="RC-0003")
pc = TestClient(app)
for lg in OTHER:
    check(pc, f"/partner/{p['token']}", lg, must=["exref", "RC-0001"])
check(pc, f"/partner/{p['token']}", "en", must=[EN["dash"], "RC-0002 · receipt cancelled", "Not yet due", "Due for payout", "Contacted", '"copied": "Copied \\u2713"'])
check(pc, f"/partner/{p['token']}", "ja", must=[JA["dash"], "支払日前"])
check(pc, f"/partner/{p['token']}?notice=รับชื่อลูกค้าแล้ว — ตรวจเว็บและทีมขายจะติดต่อพร้อมบอกว่าคุณแนะนำมา", "ko", must=["고객 정보를 받았습니다"])
check(pc, f"/partner/{p['token']}/client", "zh", method="post", data={"website": "new.example.com"}, must=["请先勾选，确认客户已同意我们联系"])
r = pc.get(f"/partner/{p['token']}?lang=th"); assert "แดชบอร์ดพันธมิตร" in r.text and "รอถึงกำหนด" in r.text and "ใบเสร็จถูกยกเลิก" in r.text and '<html lang="th">' in r.text
db.update_partner(pid, status="paused")
check(pc, f"/partner/{p['token']}", "en", must=["Paused — your link isn't counting new leads"])
print("partner: แดชบอร์ด × 4 ภาษา · สถานะลีด/ค่าแนะนำ · หมายเหตุใบเสร็จ · แจ้งเตือน · พักไว้ · ไทยคงเดิม OK")
print("ALL OK")
