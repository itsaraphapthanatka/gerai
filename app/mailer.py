"""ส่งอีเมลผ่าน SMTP ที่ตั้งค่าในหน้าผู้ดูแล — Gmail/Google Workspace (App Password), SES, Mailgun SMTP ฯลฯ

ตั้งใจใช้ SMTP ธรรมดาแทน API ของผู้ให้บริการใดเจ้าหนึ่ง เพราะลูกค้าของแพลตฟอร์มนี้มีกล่องเมลของตัวเองอยู่แล้ว
และไม่ต้องเพิ่ม dependency · รหัสผ่านเก็บเข้ารหัส (Fernet เดียวกับรหัส WordPress) โชว์กลับแค่ว่าตั้งแล้ว
build_message() เป็น pure (เทสต์ได้) · send() ยิงจริง
"""
from __future__ import annotations
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

KEYS = ("smtp_host", "smtp_port", "smtp_user", "smtp_pass", "smtp_from", "smtp_from_name", "smtp_tls")
TLS_MODES = ("starttls", "ssl", "none")
DEFAULT_PORT = {"starttls": 587, "ssl": 465, "none": 25}
TIMEOUT = 30


def config() -> dict | None:
    """ค่าที่ใช้ส่งจริง (รหัสถอดแล้ว) — None = ยังตั้งไม่ครบ (host + from อย่างน้อย)"""
    from . import db, wp_client
    c = {k: (db.get_setting(k) or "").strip() for k in KEYS}
    if not c["smtp_host"] or not c["smtp_from"]:
        return None
    c["smtp_tls"] = c["smtp_tls"] if c["smtp_tls"] in TLS_MODES else "starttls"
    c["smtp_port"] = int(c["smtp_port"] or DEFAULT_PORT[c["smtp_tls"]])
    if c["smtp_pass"]:
        try:
            c["smtp_pass"] = wp_client.decrypt(c["smtp_pass"])
        except Exception:
            c["smtp_pass"] = ""
    return c


def public_config() -> dict:
    """สำหรับหน้าตั้งค่า — ไม่มีรหัสผ่าน บอกแค่ว่าตั้งแล้วหรือยัง"""
    from . import db
    c = {k: (db.get_setting(k) or "").strip() for k in KEYS if k != "smtp_pass"}
    c["pass_set"] = bool(db.get_setting("smtp_pass"))
    c["ready"] = bool(c.get("smtp_host") and c.get("smtp_from"))
    return c


def save_config(host: str, port: str, user: str, password: str, from_: str, from_name: str, tls: str) -> None:
    from . import db, wp_client
    db.set_setting("smtp_host", host.strip())
    db.set_setting("smtp_port", (port or "").strip())
    db.set_setting("smtp_user", user.strip())
    if password.strip():                                   # เว้นว่าง = คงรหัสเดิม
        db.set_setting("smtp_pass", wp_client.encrypt(password.strip()))
    db.set_setting("smtp_from", from_.strip())
    db.set_setting("smtp_from_name", from_name.strip())
    db.set_setting("smtp_tls", tls if tls in TLS_MODES else "starttls")


def build_message(cfg: dict, to: str, subject: str, html: str, text: str = "", attachments=()) -> EmailMessage:
    """attachments = [(filename, bytes, mime 'application/pdf')] · text ว่าง = ใช้ html ถอดแท็กคร่าว ๆ"""
    import re
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg.get("smtp_from_name") or "เจอ.AI", cfg["smtp_from"]))
    msg["To"] = to
    msg["Message-ID"] = make_msgid(domain=cfg["smtp_from"].split("@")[-1] or "geo.appreview.cloud")
    plain = text or re.sub(r"\s+\n", "\n", re.sub(r"<[^>]+>", "", html.replace("</p>", "\n").replace("<br>", "\n"))).strip()
    msg.set_content(plain)
    msg.add_alternative(html, subtype="html")
    for fname, data, mime in attachments or ():
        maintype, _, subtype = (mime or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream", filename=fname)
    return msg


def send(to: str, subject: str, html: str, text: str = "", attachments=()) -> None:
    """ส่งจริง — โยน exception ให้ผู้เรียกจัดการ (campaign เก็บลง log, ปุ่มทดสอบโชว์ข้อความ)"""
    cfg = config()
    if not cfg:
        raise RuntimeError("ยังไม่ได้ตั้งค่า SMTP (ผู้ดูแล → อีเมล)")
    msg = build_message(cfg, to, subject, html, text, attachments)
    if cfg["smtp_tls"] == "ssl":
        server = smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"], timeout=TIMEOUT, context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=TIMEOUT)
    with server:
        server.ehlo()
        if cfg["smtp_tls"] == "starttls":
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        if cfg["smtp_user"]:
            server.login(cfg["smtp_user"], cfg["smtp_pass"])
        server.send_message(msg)


def wrap_html(title: str, body_html: str, footer: str = "") -> str:
    """กรอบอีเมลเรียบ ๆ แบบ inline style — client อีเมลไม่โหลด CSS ภายนอก"""
    return f"""<!doctype html><html lang="th"><body style="margin:0;background:#f4f4f8;font-family:'Sarabun','Segoe UI',Tahoma,sans-serif;color:#1c1c2b">
<div style="max-width:640px;margin:0 auto;padding:24px 16px">
  <div style="font-weight:700;color:#7132f5;font-size:18px;margin-bottom:10px">เจอ.AI <span style="color:#6b6b7b;font-weight:400;font-size:12px">GEO Platform</span></div>
  <div style="background:#fff;border:1px solid #e6e6ee;border-radius:12px;padding:20px 22px;font-size:15px;line-height:1.65">
    <h1 style="font-size:20px;margin:0 0 12px">{title}</h1>
    {body_html}
  </div>
  <div style="color:#8a8a99;font-size:12px;margin-top:12px;line-height:1.6">{footer}</div>
</div></body></html>"""
