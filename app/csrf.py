"""กัน CSRF แบบ opt-in สำหรับ route ใหม่ (route เดิมยังไม่บังคับ เพื่อไม่ให้ของเดิมพัง)

ใช้งาน:
    from .csrf import require_csrf
    @app.post("/x", dependencies=[Depends(require_csrf)])
    def x(...): ...

ในเทมเพลต: {{ csrf_input(request) }} ในทุก <form method="post"> · หรือส่ง header X-CSRF-Token จาก JS (meta csrf_token(request))
โทเคนเก็บใน session (cookie ลงนามแล้วอยู่แล้ว) อายุเท่า session · เทียบด้วย compare_digest
"""
from __future__ import annotations

import hmac
import secrets

from fastapi import HTTPException, Request
from markupsafe import Markup

FIELD = "csrf_token"
HEADER = "x-csrf-token"
_KEY = "_csrf"


def token(request: Request) -> str:
    tok = request.session.get(_KEY)
    if not tok:
        tok = secrets.token_urlsafe(32)
        request.session[_KEY] = tok
    return tok


def input_html(request: Request) -> Markup:
    return Markup(f'<input type="hidden" name="{FIELD}" value="{token(request)}">')


def valid(request: Request, sent: str | None) -> bool:
    expected = request.session.get(_KEY)
    return bool(expected and sent) and hmac.compare_digest(str(expected), str(sent))


async def require_csrf(request: Request) -> None:
    """dependency: ไม่ผ่าน → 403 · อ่านจาก header ก่อน แล้วค่อยฟอร์ม"""
    sent = request.headers.get(HEADER)
    if not sent:
        ctype = request.headers.get("content-type", "")
        if ctype.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            form = await request.form()
            sent = form.get(FIELD)
    if not valid(request, sent):
        raise HTTPException(status_code=403, detail="ฟอร์มหมดอายุหรือไม่ถูกต้อง กรุณาโหลดหน้าใหม่แล้วลองอีกครั้ง")


def install(env) -> None:
    """ลงทะเบียนตัวช่วยใน Jinja"""
    env.globals["csrf_input"] = input_html
    env.globals["csrf_token"] = token
