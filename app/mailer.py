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


PLACEHOLDER_SUFFIXES = (".local", ".invalid", ".test", ".example", "@localhost", "@example.com")


import re as _re0
# อีเมลตัวอย่างที่ติดมากับธีม/แม่แบบเว็บ (เช่น company@email.com) — ส่งไปเด้งแน่นอน (พบจริงในการทักลูกค้า 24 ต.ค.)
PLACEHOLDER_EMAIL = _re0.compile(
    r"(@(email|domain|company|website|mysite|sitename|yourdomain|yourcompany|yoursite|yourwebsite|example|sample|test|mail\.example)\.[a-z.]+$"
    r"|@your[a-z]*\.|@xxx|^(yourname|youremail|your\.?email|your\.?name|name|username|user|email|abc|xxx|test|sample|someone|john\.?doe|jane\.?doe)@)", _re0.I)


def is_placeholder(email: str) -> bool:
    return bool(PLACEHOLDER_EMAIL.search((email or "").strip()))


def is_deliverable(email: str) -> bool:
    """ที่อยู่สมมติที่ติดมาตั้งแต่ตั้งระบบ (admin@geo.local ฯลฯ) และอีเมลตัวอย่างในธีมเว็บ ส่งไปแล้วเด้งเปล่า ๆ และทำชื่อเสียงโดเมนเสีย — ข้าม"""
    e = (email or "").strip().lower()
    return "@" in e and not e.endswith(PLACEHOLDER_SUFFIXES) and not is_placeholder(e)


def config() -> dict | None:
    """ค่าที่ใช้ส่งจริง (รหัสถอดแล้ว) — None = ยังตั้งไม่ครบ (host + from อย่างน้อย)"""
    from . import db, wp_client
    c = {k: (db.get_setting(k) or "").strip() for k in KEYS}
    if not c["smtp_host"] or not c["smtp_from"]:
        return None
    c["smtp_tls"] = c["smtp_tls"] if c["smtp_tls"] in TLS_MODES else "starttls"
    c["smtp_port"] = int(c["smtp_port"] or DEFAULT_PORT[c["smtp_tls"]])
    # พอร์ตกับโหมดเข้ารหัสต้องคู่กัน: 465/2465 = SSL ตั้งแต่ต่อ, 587/2587 = STARTTLS — คนเลือกสลับกันบ่อย
    # (รอบแรกของจริง: 465 + STARTTLS → รอ banner ที่ไม่มีวันมาจน timeout) จึงยึดตามพอร์ตเป็นหลัก
    if c["smtp_port"] in (465, 2465) and c["smtp_tls"] == "starttls":
        c["smtp_tls"] = "ssl"
    elif c["smtp_port"] in (587, 2587) and c["smtp_tls"] == "ssl":
        c["smtp_tls"] = "starttls"
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


def build_message(cfg: dict, to: str, subject: str, html: str, text: str = "", attachments=(), unsub_url: str = "") -> EmailMessage:
    """attachments = [(filename, bytes, mime 'application/pdf')] · text ว่าง = ใช้ html ถอดแท็กคร่าว ๆ"""
    import re
    msg = EmailMessage()
    msg["Subject"] = subject
    sender = getattr(_UNSUB_CTX, "from_addr", "") or cfg["smtp_from"]                  # อีเมลทักใช้ที่อยู่แยก (ถ้าตั้งและตรวจเส้นทางตอบกลับแล้ว)
    msg["From"] = formataddr((getattr(_UNSUB_CTX, "from_name", "") or cfg.get("smtp_from_name") or "เจอ.AI", sender))
    msg["To"] = to
    msg["Message-ID"] = make_msgid(domain=cfg["smtp_from"].split("@")[-1] or "geo.appreview.cloud")
    # ข้อเดียวที่ mail-tester ติ (10/10 ที่เหลือผ่านหมด) — Gmail/Yahoo ใช้ header นี้แยก "จดหมายที่สมัครไว้" ออกจากสแปม
    # ใช้ mailto ไปที่ผู้ส่ง (report@ รับเมลได้แล้วผ่าน Cloudflare Email Routing) คนกดเลิกรับจะมาถึงผู้ดูแลเป็นอีเมลธรรมดา
    msg["List-Unsubscribe"] = (f"<{unsub_url}>, " if unsub_url else "") + f"<mailto:{sender}?subject=unsubscribe>"
    if unsub_url:                                          # RFC 8058: กดเลิกรับในกล่องจดหมายได้ทันที (POST /u/<token> ยกเลิกเลย)
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
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
    msg = build_message(cfg, to, subject, html, text, attachments, getattr(_UNSUB_CTX, "url", ""))
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


# ---------- อีเมลแบบคนพิมพ์เอง (อีเมลทัก/ติดตามผล) ----------
import threading as _th
_UNSUB_CTX = _th.local()


def send_plain(to: str, subject: str, text: str, unsub_url: str = "", from_addr: str = "", from_name: str = "") -> None:
    """อีเมลทัก/ติดตามผล: ข้อความแบบคนพิมพ์ + หัวจดหมายเลิกรับคลิกเดียวไปที่ลิงก์ของลีด · from_addr = ที่อยู่ส่งอีเมลทักที่แยกไว้"""
    _UNSUB_CTX.url, _UNSUB_CTX.from_addr, _UNSUB_CTX.from_name = unsub_url, from_addr, from_name
    try:
        send(to, subject, plain_html(text), text=text)
    finally:
        _UNSUB_CTX.url = _UNSUB_CTX.from_addr = _UNSUB_CTX.from_name = ""


def plain_html(text: str) -> str:
    """HTML ขั้นต่ำเหมือนอีเมลที่คนพิมพ์ใน Gmail: ไม่มีแบนเนอร์ รูป ตาราง หรือปุ่ม — ตัวกรองไม่จัดเป็นอีเมลการตลาด
    (อีเมลทักแบบมีแบนเนอร์+ปุ่มตกแท็บโปรโมชันในการทดสอบกล่องจริง 23 ต.ค.)"""
    import html as _hh
    import re as _rr
    esc = _hh.escape(text or "")
    esc = _rr.sub(r"(https?://[^\s<]+)", lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>', esc)
    return '<div dir="auto">' + esc.replace("\n", "<br>") + "</div>"


# ---------- หน้าตาอีเมล (แบบเดียวกับอีเมลแจ้งเตือนของผู้ให้บริการใหญ่: แบนเนอร์ · หัวเรื่องกลาง · ปุ่มหลัก · ท้ายจดหมายบริษัท) ----------
import html as _h
import os as _os
import re as _re

BANNER_PATH = "/static/email/header-v2.png"      # ดีไซน์เดียวกับหน้าแรก (ดำ + ส้มแดง) · ชื่อไฟล์ใหม่กันกล่องจดหมายแคชรูปเก่า
_CTA_RULES = [("/q/", "ดูใบเสนอราคาและยืนยัน"), ("/audit/", "ดูผลตรวจเว็บของคุณ"), ("/login", "เข้าสู่ระบบ"),
              ("/assets", "เปิดชุดติดตั้ง"), ("/content", "ตรวจคอนเทนต์"), ("/report", "ดูรายงาน"), ("/brands/", "เปิดแบรนด์ของคุณ"),
              ("/admin/", "เปิดในระบบ")]
_UNSUB = _re.compile(r"(?:<br>\s*)*—\s*เจอ\.AI \(ข้อความนี้ส่งจากระบบอัตโนมัติ\).*?(https?://\S+?/u/[\w\-]+).*?เราจะไม่ติดต่ออีก", _re.S)


def _base_url() -> str:
    return (_os.getenv("PUBLIC_BASE_URL") or _os.getenv("GEO_BASE_URL") or "https://geo.appreview.cloud").rstrip("/")


def _company() -> dict:
    from . import db
    g = lambda k: (db.get_setting(k) or "").strip()
    return {"name": g("company_name") or "เจอ.AI", "address": g("company_address"), "tax_id": g("company_tax_id"),
            "phone": g("company_phone"), "email": g("company_email")}


def _short(url: str, n: int = 46) -> str:
    t = _re.sub(r"^https?://", "", url)
    return t if len(t) <= n else t[:n - 1] + "…"


def _linkify(body_html: str) -> str:
    """URL ดิบในเนื้อหา → ลิงก์สั้นอ่านง่าย (ลิงก์ที่เป็น <a> อยู่แล้วไม่แตะ)"""
    def rep(m):
        u = m.group(0).rstrip(".,)")
        tail = m.group(0)[len(u):]
        return f"<a href='{u}' style='color:#5b2bd9;text-decoration:underline'>{_h.escape(_short(u))}</a>{tail}"
    parts = _re.split(r"(<a\b[^>]*>.*?</a>)", body_html, flags=_re.S | _re.I)
    return "".join(p if p.lower().startswith("<a") else _re.sub(r"https?://[^\s<>'\"]+", rep, p) for p in parts)


def _boxes(body_html: str) -> str:
    """รายการ "• ..." ตั้งแต่ 2 บรรทัดติดกัน → กล่องสรุปสีอ่อน อ่านง่ายกว่าบรรทัดเปล่า (เช่น รายละเอียดใบเสนอราคา)"""
    def rep(m):
        items = [x.strip() for x in _re.split(r"<br>\s*", m.group(0)) if x.strip()]
        rows = "".join(f"<div style='padding:3px 0'><span style='color:#ff5530;font-weight:700'>•</span> {x.lstrip('•').strip()}</div>" for x in items)
        return f"<div style='background:#f6f3ff;border:1px solid #e4dcff;border-radius:10px;padding:12px 16px;margin:8px 0 10px'>{rows}</div>"
    return _re.sub(r"(?:•[^<]*(?:<a\b[^>]*>.*?</a>[^<]*)*(?:<br>\s*)?){2,}", rep, body_html)


def auto_cta(body_html: str):
    """ปุ่มหลักจากลิงก์แรกที่สำคัญในเนื้อหา (ใบเสนอราคา/ผลตรวจ/เข้าสู่ระบบ ...) — ไม่ใช้ลิงก์ไม่รับ"""
    for u in _re.findall(r"https?://[^\s<>'\"]+", body_html or ""):
        u = u.rstrip(".,)")
        if "/u/" in u:
            continue
        for key, label in _CTA_RULES:
            if key in u:
                return label, u
    return None


def button(label: str, url: str) -> str:
    """ปุ่มแบบตาราง (bulletproof) — Outlook/Gmail แสดงเหมือนกัน"""
    return (f"<table role='presentation' cellspacing='0' cellpadding='0' border='0' align='center' style='margin:26px auto 6px'><tr>"
            f"<td bgcolor='#0a0a0a' style='border-radius:9999px'><a href='{url}' style='display:inline-block;padding:14px 32px;font-size:16px;font-weight:700;"
            f"color:#ffffff;text-decoration:none;border-radius:9999px;font-family:Sarabun,Tahoma,sans-serif'>{_h.escape(label)}</a></td></tr></table>")


def wrap_html(title: str, body_html: str, footer: str = "", cta=None, preheader: str = "") -> str:
    """กรอบอีเมลของเจอ.AI — แบนเนอร์แบรนด์ · หัวเรื่องกลาง · เนื้อหา · ปุ่มหลัก (เดาจากลิงก์ถ้าไม่ระบุ · cta=False = ไม่ใส่)
    · ท้ายจดหมาย "ขอบคุณที่ใช้บริการ" + ข้อมูลบริษัท · ลิงก์ไม่รับย้ายไปท้ายสุดตัวเล็ก · inline style ทั้งหมด (client อีเมลไม่โหลด CSS ภายนอก)"""
    base, co = _base_url(), _company()
    unsub = ""
    m = _UNSUB.search(body_html or "")
    if m:
        unsub = m.group(1)
        body_html = (body_html[:m.start()] + body_html[m.end():]).rstrip()
        body_html = _re.sub(r"(<br>\s*)+(</p>)?\s*$", r"\2", body_html)
    if cta is None:
        cta = auto_cta(body_html)
    body = _boxes(_linkify(body_html))
    btn = button(*cta) if cta else ""
    info = " · ".join(x for x in [co["address"], ("โทร " + co["phone"]) if co["phone"] else "", co["email"]] if x)
    tax = f"<br>เลขประจำตัวผู้เสียภาษี {_h.escape(co['tax_id'])}" if co["tax_id"] else ""
    pre = f"<div style='display:none;max-height:0;overflow:hidden;opacity:0'>{_h.escape(preheader)}</div>" if preheader else ""
    unsub_html = (f"<br>ไม่ต้องการรับอีเมลจากเรา <a href='{unsub}' style='color:#8a8a99'>กดที่นี่</a> เราจะไม่ติดต่ออีก") if unsub else ""
    return f"""<!doctype html><html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_h.escape(title)}</title></head>
<body style="margin:0;padding:0;background:#f3f1fb;font-family:Sarabun,'Segoe UI',Tahoma,sans-serif;color:#1c1c2b">{pre}
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#f3f1fb"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:600px;background:#ffffff;border-radius:14px;overflow:hidden">
  <tr><td bgcolor="#2a1670" style="background:#2a1670"><a href="{base}" style="text-decoration:none"><img src="{base}{BANNER_PATH}" width="600" alt="เจอ.AI — ให้ลูกค้าหาคุณเจอใน Google และ AI"
      style="display:block;width:100%;max-width:600px;height:auto;border:0;color:#ffffff;font-size:22px;font-weight:700;line-height:60px;text-align:center"></a></td></tr>
  <tr><td style="padding:34px 40px 8px;text-align:center"><h1 style="margin:0;font-size:23px;line-height:1.4;color:#1c1c2b;font-weight:700">{title}</h1></td></tr>
  <tr><td style="padding:18px 40px 6px;font-size:16px;line-height:1.75;color:#2d2d3d">{body}{btn}</td></tr>
  <tr><td style="padding:22px 40px 30px;text-align:right;font-size:15px;line-height:1.6;color:#2d2d3d"><b>ขอบคุณที่ใช้บริการ</b><br>จาก ทีมงาน เจอ.AI</td></tr>
  <tr><td bgcolor="#efeaff" style="background:#efeaff;padding:22px 30px;text-align:center;font-size:13px;line-height:1.7;color:#4b4b63">
      <div style="font-size:16px;font-weight:700;color:#4a23b8">{_h.escape(co['name'])}</div>{_h.escape(info)}{tax}
      <div style="margin-top:8px"><a href="{base}" style="color:#5b2bd9;text-decoration:none;font-weight:600">geo.appreview.cloud</a></div></td></tr>
</table>
<div style="max-width:600px;color:#8a8a99;font-size:12px;line-height:1.6;padding:14px 10px;text-align:center">{footer}{unsub_html}</div>
</td></tr></table></body></html>"""
