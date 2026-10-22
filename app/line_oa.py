"""LINE Messaging API (เฟส 2) — ช่องทางที่ลูกค้าทักเข้ามาเอง + แจ้งเตือนเจ้าของ
ลูกค้าส่งที่อยู่เว็บมาในแชต → ระบบตรวจฟรีแล้วส่งผลกลับ (ลูกค้าเป้าหมายแหล่ง "line") · เจ้าของผูกบัญชีด้วยรหัส 6 หลักจากหน้าตั้งค่า
แล้วรับสรุปเช้า/lead ใหม่/ข้อเสนอรอทาง LINE · คีย์เก็บเข้ารหัส (Fernet) ใน settings · ไม่มีคีย์ = เงียบและบอกสถานะ "ยังไม่มีคีย์\""""
from __future__ import annotations
import base64
import hashlib
import hmac
import secrets

import httpx

from . import db, wp_client

API = "https://api.line.me/v2/bot"
TIMEOUT = 10
KEYS = ("line_channel_token", "line_channel_secret")


def _get(key: str) -> str:
    v = (db.get_setting(key) or "").strip()
    if not v:
        return ""
    try:
        return wp_client.decrypt(v)
    except Exception:
        return ""


def config() -> dict | None:
    token, secret = _get("line_channel_token"), _get("line_channel_secret")
    return {"token": token, "secret": secret} if token and secret else None


def save_config(token: str, secret: str) -> None:
    """เว้นว่าง = คงค่าเดิม (เหมือนคีย์อื่นในหน้าตั้งค่า)"""
    if token.strip():
        db.set_setting("line_channel_token", wp_client.encrypt(token.strip()))
    if secret.strip():
        db.set_setting("line_channel_secret", wp_client.encrypt(secret.strip()))


def bot_name() -> str:
    """ชื่อที่บอทใช้แนะนำตัวในแชต — ตั้งตามชื่อ OA ที่ผูก (เช่น "Hop") · ว่าง = เจอ.AI"""
    return (db.get_setting("line_bot_name") or "").strip() or "เจอ.AI"


def link_code() -> str:
    """รหัสผูกเจ้าของ: พิมพ์รหัสนี้ใน OA ครั้งเดียว ระบบจำ userId ไว้ส่งแจ้งเตือน"""
    code = db.get_setting("line_link_code")
    if not code:
        code = f"{secrets.randbelow(900000) + 100000}"
        db.set_setting("line_link_code", code)
    return code


def owner_id() -> str | None:
    return db.get_setting("line_owner_user_id") or None


def set_owner(uid: str) -> None:
    db.set_setting("line_owner_user_id", uid)
    db.set_setting("line_link_code", f"{secrets.randbelow(900000) + 100000}")   # ใช้แล้วเปลี่ยนรหัส กันคนอื่นผูกทับ


def public_config() -> dict:
    c = config()
    oid = owner_id()
    return {"token_set": bool(_get("line_channel_token")), "secret_set": bool(_get("line_channel_secret")), "ready": bool(c), "bot_name": bot_name(),
            "owner_set": bool(oid), "owner_mask": (oid[:4] + "…" + oid[-3:]) if oid else "", "link_code": link_code(),
            "webhook_path": "/line/webhook", "status": status()}


def status() -> str:
    if not config():
        return "ยังไม่มีคีย์"
    return "พร้อม" if owner_id() else "พร้อม (ยังไม่ผูกเจ้าของ)"


def available() -> bool:
    return config() is not None


def verify(body: bytes, signature: str) -> bool:
    """X-Line-Signature = base64(HMAC-SHA256(channel secret, body)) — ไม่ตรง = ไม่ใช่ LINE ทิ้งทั้งก้อน"""
    c = config()
    if not c or not signature:
        return False
    mac = hmac.new(c["secret"].encode("utf-8"), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(mac).decode("ascii"), signature.strip())


def _post(path: str, payload: dict) -> bool:
    c = config()
    if not c:
        return False
    try:
        r = httpx.post(f"{API}{path}", json=payload, timeout=TIMEOUT,
                       headers={"Authorization": f"Bearer {c['token']}", "Content-Type": "application/json"})
        return r.status_code < 300
    except Exception:
        return False


def _messages(texts: list[str]) -> list[dict]:
    return [{"type": "text", "text": t[:5000]} for t in texts if t][:5]


def reply(reply_token: str | None, texts: list[str]) -> bool:
    if not reply_token:
        return False
    return _post("/message/reply", {"replyToken": reply_token, "messages": _messages(texts)})


def push(user_id: str, texts: list[str]) -> bool:
    if not user_id:
        return False
    return _post("/message/push", {"to": user_id, "messages": _messages(texts)})


def broadcast(texts: list[str]) -> bool:
    """ส่งถึงผู้ติดตาม OA ทุกคน (นับโควตาต่อผู้รับ — แผนฟรี 200 ข้อความ/เดือน)"""
    return _post("/message/broadcast", {"messages": _messages(texts)})


def notify_owner(text: str) -> bool:
    oid = owner_id()
    return push(oid, [text]) if oid else False


def parse_events(body: dict) -> list[dict]:
    """ย่อ event ของ LINE ให้เหลือที่ใช้: type (follow|message|unfollow) · user_id · text · reply_token"""
    out = []
    for ev in (body or {}).get("events") or []:
        src = ev.get("source") or {}
        msg = ev.get("message") or {}
        out.append({"type": ev.get("type"), "user_id": src.get("userId") or "", "reply_token": ev.get("replyToken"),
                    "text": (msg.get("text") or "").strip() if msg.get("type") == "text" else ""})
    return out
