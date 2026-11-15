"""ทดสอบ stage 06A-1: คุยต่อใน LINE จากรายงาน (flag line_appointments) — รหัสใช้ครั้งเดียว · ปุ่มตอบเร็ว · ขอเบอร์เฉพาะคนที่ขอให้โทร · พักบอท
ไม่ยิงเครือข่าย (แทน line_oa._post ด้วยตัวเก็บข้อความ) · DB ชั่วคราว · ไม่ส่งข้อความจริง"""
import os, re, json, base64, hashlib, hmac, tempfile, datetime as dt
from urllib.parse import unquote
os.environ.pop("DATABASE_URL", None)
os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["AI_BASE_URL"] = "http://127.0.0.1:9"
os.environ["GEO_DEEP_AUDIT"] = "0"
os.environ.pop("GEO_LINE_OA_ID", None)
for k in list(os.environ):
    if k.startswith("GEO_FLAG_"):
        os.environ.pop(k)
import bcrypt
from fastapi.testclient import TestClient
import app.db as db
import app.leads as L
import app.pipeline as pipeline
import app.line_oa as LO
import app.line_plan as LP
import app.plan_request as PR
import app.flags as F
import app.salesbot as SB
from app.main import app

db.init_db()
alerts, sent = [], []
pipeline.alert_admins = lambda *a, **kw: alerts.append(a[0])
pipeline.RUN_INLINE = True
LO.config = lambda: {"token": "tok", "secret": "sec"}
LO._post = lambda path, payload: sent.append((path, payload)) or True
SB.set_enabled("line", False)
c = TestClient(app)

REPORT = {"ok": False, "checks": [{"key": "home", "label": "หน้าแรก", "status": "ok"},
                                  {"key": "robots", "label": "robots.txt กันบอท AI", "status": "fail", "detail": "Disallow: / สำหรับ GPTBot"}]}


def mk_lead(dom, **kw):
    lid = db.add_lead(f"https://{dom}", dom, source="audit", ip="1.2.3.4")
    tok = L.new_token()
    db.update_lead(lid, token=tok, audit_json=json.dumps(REPORT, ensure_ascii=False), audit_at=db.now(), **kw)
    return lid, tok


def msg(uid, text):
    sent.clear()
    return pipeline.handle_line_webhook({"events": [{"type": "message", "source": {"userId": uid}, "replyToken": "rt",
                                                     "message": {"type": "text", "text": text}}]})[0]


def pb(uid, data):
    sent.clear()
    return pipeline.handle_line_webhook({"events": [{"type": "postback", "source": {"userId": uid}, "replyToken": "rt", "postback": {"data": data}}]})[0]


def texts():
    return [m.get("text", "") for _, p in sent for m in p.get("messages", [])]


def quick():
    return [i["action"]["data"] for _, p in sent for m in p.get("messages", []) for i in (m.get("quickReply") or {}).get("items", [])]


lid, tok = mk_lead("shop-a.co.th")

# ---------- ปิด flag / ยังตั้งค่าไม่ครบ = ไม่มีปุ่ม ไม่มีลิงก์ ----------
F.set_enabled("report_v2", True); F.set_enabled("plan_request_v2", True)
assert c.get(f"/audit/{tok}/line", follow_redirects=False).status_code == 404
assert 'id="b-line"' not in c.get(f"/audit/{tok}").text
F.set_enabled("line_appointments", True)

# LINE ID ของ OA: ถามจาก OA ที่เชื่อมอยู่ (GET /v2/bot/info) · ไม่ได้ = ไม่มีปุ่ม และไม่ถามซ้ำทุกครั้งที่เปิดหน้า
calls = []


class _R:
    def __init__(self, code, data):
        self.status_code, self._d = code, data

    def json(self):
        return self._d


def _down(url, **kw):
    calls.append(url)
    raise OSError("LINE down")


LO.httpx.get = _down
assert c.get(f"/audit/{tok}/line", follow_redirects=False).status_code == 404       # ถาม LINE ไม่ได้ = ไม่มีลิงก์
assert 'id="b-line"' not in c.get(f"/audit/{tok}").text
assert len(calls) == 1 and calls[0].endswith("/v2/bot/info")                         # ถามครั้งเดียว แล้วเว้น 1 ชม.
LO.httpx.get = lambda url, **kw: calls.append(url) or _R(200, {"basicId": "@123abcde", "displayName": "x"})
assert LO.oa_id() == "" and len(calls) == 1
assert LO.fetch_basic_id(force=True) == "@123abcde" and LO.oa_id() == "@123abcde" and LO.oa_id_source() == "auto"
assert "@123abcde" in TestClient(app).get(f"/audit/{tok}/line", follow_redirects=False).headers.get("location", "").replace("%40", "@")
LO.save_config("new-token", "")                                                       # เปลี่ยน OA → ลืมค่าที่ถามไว้
assert db.get_setting("line_oa_id_auto") == ""
LO.httpx.get = _down

assert LO.save_oa_id("bad id!") is False
assert LO.save_oa_id("jorai") is True and LO.oa_id() == "@jorai"                  # ลืม @ = เติมให้
F.set_enabled("plan_request_v2", False)
assert c.get(f"/audit/{tok}/line", follow_redirects=False).status_code == 404       # ต้องเปิดคู่กับฟอร์มขอแผน
F.set_enabled("plan_request_v2", True)

# ---------- ปุ่มในรายงาน → ลิงก์ oaMessage พร้อมรหัส (ไม่มี token รายงาน) ----------
html = c.get(f"/audit/{tok}").text
assert 'id="b-line"' in html and f'href="/audit/{tok}/line"' in html and "ถือว่ายินยอม" in html
r = c.get(f"/audit/{tok}/line", follow_redirects=False)
assert r.status_code == 303 and "noindex" in r.headers.get("x-robots-tag", "") and r.headers.get("referrer-policy") == "no-referrer"
loc = r.headers["location"]
assert loc.startswith("https://line.me/R/oaMessage/%40jorai/?"), loc
pre = unquote(loc.split("?", 1)[1])
assert pre.startswith("ขอแผนจากรายงาน shop-a.co.th · รหัส ") and tok not in loc
code = LP.find_code(pre)
assert code and len(code) == 6 and not set(code) & set("0O1IL")
assert c.get(f"/audit/{tok}/line", follow_redirects=False).headers["location"] == loc      # กดซ้ำ = รหัสเดิม

# ---------- ส่งรหัส → ผูกแชต + บันทึกคำขอ + ถามข้อ 1 ----------
n_alert = len(alerts)
assert msg("U1", pre) == "code_linked"
lead = db.get_lead(lid)
assert lead["line_user_id"] == "U1" and lead["consent"] == 1
rows = db.list_plan_requests(lid)
assert len(rows) == 1 and rows[0]["contact_channel"] == "line" and rows[0]["desired_window"] is None
cons = json.loads(rows[0]["consents_json"])
assert cons[0]["purpose"] == "fulfil_request" and cons[0]["source"] == "line_oa" and cons[1]["granted"] is False   # รับข่าวสาร: ค่าเริ่มต้น = ไม่รับ
assert len(alerts) == n_alert + 1 and "shop-a.co.th" in alerts[-1] and "U1" not in alerts[-1]
assert any(e["kind"] == "wants_call" for e in db.list_lead_events(lid))
t = texts()
assert len(t) == 3 and "ได้รับคำขอแผนของ shop-a.co.th" in t[0] and "/audit/" in t[0] and "robots.txt" in t[0]
assert "หยุดติดต่อ" in t[1]                                                          # แจ้งการใช้ข้อมูล + วิธีถอน
assert quick() == ["jp:win:asap", "jp:win:1m", "jp:win:3m", "jp:win:explore"]
assert all(len(i["action"]["label"]) <= 20 for _, p in sent for m in p["messages"] for i in (m.get("quickReply") or {}).get("items", []))

# รหัสเดิม: บัญชีเดิม = ส่งสรุปซ้ำ ไม่สร้างคำขอใหม่ · บัญชีอื่น = ใช้ไม่ได้
assert msg("U1", pre) == "code_again" and len(db.list_plan_requests(lid)) == 1
assert msg("U2", pre) == "code_invalid" and db.get_lead(lid)["line_user_id"] == "U1"
assert "หมดอายุหรือถูกใช้ไปแล้ว" in texts()[0]

# ---------- ปุ่มตอบเร็ว ----------
assert pb("U2", "jp:win:asap") == "postback_ignored"                                # ไม่มีสถานะแชต = ไม่รับ
assert pb("U1", "jp:win:zzz") == "postback_ignored" and pb("U1", "jp:budget:gt30k") == "postback_ignored"
assert pb("U1", "jp:win:asap") == "answered_win" and quick() == ["jp:web:own_team", "jp:web:agency", "jp:web:none"]
assert pb("U1", "jp:web:agency") == "answered_web" and quick() == ["jp:mkt:1", "jp:mkt:0"]
assert pb("U1", "jp:mkt:1") == "answered_mkt" and quick() == ["jp:call:1"] and "1 วันทำการ" in texts()[0]
row = db.list_plan_requests(lid)[0]
assert row["desired_window"] == "asap" and row["web_readiness"] == "agency"
b = PR.brief(db.get_lead(lid))
assert b["marketing"] is True and ("ช่วงเวลาที่อยากเริ่ม", "เร็วที่สุด") in b["confirmed"] and ("คนดูแลเว็บ", "ใช้เอเจนซีอยู่") in b["confirmed"]
assert "LINE OA" in b["next"]
assert len(json.loads(row["consents_json"])) == 3                                    # เปลี่ยนใจ = เพิ่มบันทึก เก็บของเดิมไว้

# ---------- ขอให้โทร → ขอเบอร์ในแชตเท่านั้น ----------
assert pb("U1", "jp:call:1") == "ask_phone" and "เบอร์โทร" in texts()[0]
assert msg("U1", "08123") == "phone_invalid"
n_alert = len(alerts)
assert msg("U1", "081 234 5678") == "phone_saved"
row = db.list_plan_requests(lid)[0]
assert row["contact_channel"] == "phone" and row["contact_value"] == "081 234 5678"
assert db.get_lead(lid)["contact"] == "โทรศัพท์: 081 234 5678"
assert len(alerts) == n_alert + 1 and "5678" not in alerts[-1]                       # เบอร์ไม่ออกไปกับข้อความแจ้งทีม
assert pb("U1", "jp:call:1") == "ask_phone" and msg("U1", "เว็บผมช้ามากครับ") not in ("phone_saved", "phone_invalid")   # พิมพ์เรื่องอื่น = เลิกรอเบอร์
assert db.get_line_session("U1")["step"] == "done"

# ---------- พักบอท (ทีมตอบเอง) ----------
pw = bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode()
db.create_tenant("admin@lp.test", pw, "Admin", is_admin=True)
adm = TestClient(app)
assert adm.post("/login", data={"email": "admin@lp.test", "password": "pw123456"}).status_code == 200
page = adm.get(f"/admin/leads/{lid}").text
assert "พักบอท 24 ชม." in page and "Sales Brief" in page
csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
assert adm.post(f"/admin/leads/{lid}/line-bot", data={"action": "pause"}, follow_redirects=False).status_code == 403
assert adm.post(f"/admin/leads/{lid}/line-bot", data={"action": "pause", "csrf_token": csrf}, follow_redirects=False).status_code in (302, 303)
assert LP.paused("U1") and "บอท LINE พักถึง" in adm.get(f"/admin/leads/{lid}").text
assert msg("U1", "สนใจครับ ราคาเท่าไร") == "paused" and sent == []                     # บอทเงียบ
assert pb("U1", "jp:win:1m") == "paused" and sent == []
assert c.post(f"/admin/leads/{lid}/line-bot", data={"action": "resume"}, follow_redirects=False).status_code in (302, 303, 403)
assert LP.paused("U1")                                                               # คนนอกเปิดบอทไม่ได้
adm.post(f"/admin/leads/{lid}/line-bot", data={"action": "resume", "csrf_token": csrf}, follow_redirects=False)
assert not LP.paused("U1")

# ---------- ขอหยุดติดต่อ ----------
n_alert = len(alerts)
assert msg("U1", "หยุดติดต่อ") == "stopped"
row = db.list_plan_requests(lid)[0]
assert row["status"] == "withdrawn"
b = PR.brief(db.get_lead(lid))
assert b["withdrawn"] and b["marketing"] is False and "ห้ามติดต่อ" in b["next"] and len(alerts) == n_alert + 1

# ---------- เดารหัส: ผิดเกิน 5 ครั้ง/ชม. = พัก · รหัสหมดอายุ = ใช้ไม่ได้ ----------
lid2, tok2 = mk_lead("shop-b.co.th")
good = LP.code_for(db.get_lead(lid2))
for i in range(5):
    assert msg("U3", f"รหัส ZZZZZ{i + 2}") == "code_invalid"
assert msg("U3", f"รหัส {good}") == "code_locked" and db.get_lead(lid2)["line_user_id"] is None
db.save_line_session("U3", fail_at=(dt.datetime.now() - dt.timedelta(hours=2)).isoformat(timespec="seconds"))
assert msg("U3", f"รหัส {good.lower()}") == "code_linked"                          # พิมพ์ตัวเล็กก็รับ
past = (dt.datetime.now() - dt.timedelta(hours=1)).isoformat(timespec="seconds")
db.add_line_code("EXPRD2", lid2, past)
assert msg("U4", "รหัส EXPRD2") == "code_invalid"

# ---------- มีคำขอจากฟอร์มแล้ว → ผูกกับคำขอเดิม ไม่สร้างซ้ำ · ลีดมี LINE คนอื่นแล้ว = ไม่เขียนทับ ----------
lid3, tok3 = mk_lead("shop-c.co.th", line_user_id="UX")
v, err = PR.validate({"name": "วิภา", "contact_channel": "email", "contact_value": "w@example.co.th", "consent_fulfil": "1"})
assert not err
PR.submit(db.get_lead(lid3), v)
assert msg("U5", f"รหัส {LP.code_for(db.get_lead(lid3))}") == "code_linked_existing"
assert len(db.list_plan_requests(lid3)) == 1 and db.get_lead(lid3)["line_user_id"] == "UX"
assert pb("U5", "jp:win:3m") == "answered_win" and db.list_plan_requests(lid3)[0]["desired_window"] == "3m"
assert set(db.line_users_of_lead(lid3)) == {"U5", "UX"}

# ---------- ข้อความทั่วไปยังไปทางเดิม ----------
assert msg("U9", "สวัสดี") == "help"
assert msg("U9", "รหัส") == "help"                                                   # คำว่า "รหัส" เฉยๆ ไม่ใช่รหัส

# ---------- ผ่าน route webhook จริง (ตรวจลายเซ็น) ----------
body = json.dumps({"events": [{"type": "postback", "webhookEventId": "ev-1", "source": {"userId": "U5"}, "replyToken": "rt",
                               "postback": {"data": "jp:web:none"}}]}).encode()
sig = base64.b64encode(hmac.new(b"sec", body, hashlib.sha256).digest()).decode()
r = c.post("/line/webhook", content=body, headers={"x-line-signature": sig, "content-type": "application/json"})
assert r.status_code == 200 and r.json()["handled"] == ["answered_web"], r.text
assert c.post("/line/webhook", content=body, headers={"x-line-signature": sig}).json()["handled"] == ["duplicate"]
assert c.post("/line/webhook", content=body, headers={"x-line-signature": "bad"}).status_code == 403

# ---------- ปิด flag = ลิงก์หายทันที (แชตที่ผูกไว้แล้วยังคุยต่อได้) ----------
F.set_enabled("line_appointments", False)
assert c.get(f"/audit/{tok}/line", follow_redirects=False).status_code == 404 and 'id="b-line"' not in c.get(f"/audit/{tok}").text

print("line_plan_smoke OK")
