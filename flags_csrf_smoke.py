"""ทดสอบ stage 01 (ฐาน): feature flags กลาง + กัน CSRF แบบ opt-in · ไม่ยิงเครือข่าย · DB ชั่วคราว"""
import os, tempfile
os.environ.pop("DATABASE_URL", None)
os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["AI_BASE_URL"] = "http://127.0.0.1:9"
for k in list(os.environ):
    if k.startswith("GEO_FLAG_"):
        os.environ.pop(k)
from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
import app.db as db
import app.flags as F
import app.csrf as C

db.init_db()

# ---------- flags ----------
assert all(v is False for v in F.snapshot().values())                  # ค่าเริ่มต้นปิดทั้งหมด
try:
    F.enabled("no_such_flag"); raise AssertionError("ชื่อผิดต้องโยน")
except KeyError:
    pass
F.set_enabled("report_v2", True); assert F.enabled("report_v2")
F.set_enabled("report_v2", False); assert not F.enabled("report_v2")
os.environ["GEO_FLAG_REPORT_V2"] = "1"; assert F.enabled("report_v2")   # env ชนะ DB
os.environ["GEO_FLAG_REPORT_V2"] = "0"; F.set_enabled("report_v2", True); assert not F.enabled("report_v2")
os.environ.pop("GEO_FLAG_REPORT_V2"); F.set_enabled("report_v2", False)

class Req:                                                               # request จำลองสำหรับ preview
    def __init__(self, admin, cookie):
        self.session = {"is_admin": admin} if admin is not None else {}
        self.cookies = {F.PREVIEW_COOKIE: cookie} if cookie else {}
assert F.previewing("marketing_v2", Req(True, "marketing_v2,report_v2"))
assert not F.previewing("marketing_v2", Req(False, "marketing_v2"))   # ไม่ใช่แอดมิน = ไม่เห็น preview
assert not F.previewing("marketing_v2", Req(None, "marketing_v2"))
assert not F.previewing("snapshot_v2", Req(True, "marketing_v2"))
assert F.active("marketing_v2", Req(True, "marketing_v2")) and not F.active("marketing_v2", Req(False, ""))
assert not F.active("marketing_v2")

# ---------- CSRF ----------
mini = FastAPI()
mini.add_middleware(SessionMiddleware, secret_key="test")
@mini.get("/form", response_class=HTMLResponse)
def form(request: Request):
    return f"<form method=post>{C.input_html(request)}</form>"
@mini.post("/save", dependencies=[Depends(C.require_csrf)])
def save(x: str = Form("")):
    return {"ok": True, "x": x}

c = TestClient(mini)
html = c.get("/form").text
tok = html.split('value="')[1].split('"')[0]
assert len(tok) > 30
assert c.post("/save", data={"x": "1"}).status_code == 403                               # ไม่มีโทเคน
assert c.post("/save", data={"x": "1", "csrf_token": "wrong"}).status_code == 403
r = c.post("/save", data={"x": "1", "csrf_token": tok}); assert r.status_code == 200 and r.json()["x"] == "1"   # ฟอร์มยังอ่านค่าได้หลัง dependency
assert c.post("/save", data={"x": "2"}, headers={"X-CSRF-Token": tok}).status_code == 200
other = TestClient(mini)                                                                  # session อื่นใช้โทเคนนี้ไม่ได้
other.get("/form")
assert other.post("/save", data={"x": "1", "csrf_token": tok}).status_code == 403

# ---------- ติดตั้งในแอปจริงแล้ว และไม่กระทบหน้าเดิม ----------
from app.main import app, templates
assert "csrf_input" in templates.env.globals and "flag_active" in templates.env.globals
m = TestClient(app)
assert m.get("/").status_code == 200 and m.get("/audit").status_code == 200

print("flags_csrf_smoke OK")
