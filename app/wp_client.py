"""WordPress REST publisher — execution layer (Phase B).

เชื่อมเว็บลูกค้าผ่าน WP REST API (/wp-json) ด้วย Application Password
- encrypt/decrypt รหัสด้วย Fernet (คีย์ derive จาก SESSION_SECRET)
- test_connection: ยืนยันสิทธิ์
- publish_post: ส่งคอนเทนต์เป็น "ร่าง" ใน WP (ปลอดภัย — ให้ลูกค้า/ทีม review ก่อนเผยแพร่จริง)

หมายเหตุ: schema JSON-LD ระดับเว็บ + Rank Math meta ทำผ่าน REST แกนตรง ๆ ไม่ได้
→ ใช้ชุดติดตั้ง on-site (Phase A) หรือปลั๊กอิน Connector (Phase C)
"""
from __future__ import annotations
import os
import re
import html as _html
import base64
import hashlib

import httpx
from cryptography.fernet import Fernet


def _fernet() -> Fernet:
    secret = os.getenv("SESSION_SECRET", "dev-secret-change-me").encode("utf-8")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret).digest()))


def encrypt(s: str) -> str:
    return _fernet().encrypt(s.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")


def _api(site_url: str) -> str:
    u = (site_url or "").strip().rstrip("/")
    if not u.startswith("http"):
        u = "https://" + u
    return u + "/wp-json/wp/v2"


def _auth(user: str, app_password: str):
    # Application Password ใช้ Basic auth (ตัด space ที่ WP แสดงเป็นบล็อกออก)
    return (user.strip(), (app_password or "").replace(" ", ""))


def _explain_error(r) -> str:
    """แปลง response ที่ล้มเหลวเป็นข้อความไทยที่บอกวิธีแก้ตรงจุด"""
    code = ""
    try:
        code = ((r.json() or {}).get("code") or "")
    except Exception:
        pass
    sc = r.status_code
    if code == "rest_not_logged_in":
        return ("เซิร์ฟเวอร์ตัด Authorization header ทิ้ง — WordPress ไม่เห็นรหัสเลย "
                "(ไม่ใช่รหัสผิด). แก้ที่ .htaccess ของเว็บ เพิ่ม: "
                "RewriteCond %{HTTP:Authorization} . แล้ว "
                "RewriteRule .* - [E=HTTP_AUTHORIZATION:%{HTTP:Authorization}]")
    if "incorrect_password" in code:
        return ("รหัสไม่ถูกต้อง — ต้องใช้ Application Password (สร้างที่ ผู้ใช้ → โปรไฟล์ → "
                "Application Passwords) ไม่ใช่รหัสเข้าระบบปกติ")
    if "invalid_username" in code:
        return "ชื่อผู้ใช้ไม่ถูกต้อง — ลองใช้ username ของ WordPress แทนอีเมล"
    if code == "application_passwords_disabled":
        return "เว็บนี้ปิดใช้งาน Application Passwords — เปิดก่อน (เว็บต้องเป็น HTTPS)"
    if code == "application_passwords_disabled_for_user":
        return "ผู้ใช้นี้ถูกปิด Application Passwords — เปิดให้ผู้ใช้ก่อน"
    if sc == 403:
        return (f"ถูกปฏิเสธ (HTTP 403{(' · ' + code) if code else ''}) — "
                "อาจมีปลั๊กอินความปลอดภัย (Wordfence ฯลฯ) บล็อก REST API")
    return f"ปฏิเสธ (HTTP {sc}{(' · ' + code) if code else ''})"


def test_connection(site_url: str, user: str, app_password: str) -> dict:
    try:
        r = httpx.get(
            _api(site_url) + "/users/me",
            auth=_auth(user, app_password),
            params={"context": "edit"},
            timeout=15,
            follow_redirects=True,
        )
        if r.status_code == 200:
            return {"ok": True, "msg": f"เชื่อมต่อสำเร็จ ({r.json().get('name', '?')})"}
        return {"ok": False, "msg": _explain_error(r)}
    except Exception as e:
        return {"ok": False, "msg": f"เชื่อมต่อไม่ได้: {e}"}


def _md_inline(s: str) -> str:
    """escape HTML + แปลง inline markdown (รูป, ลิงก์, ตัวหนา)"""
    s = _html.escape(s, quote=False)
    # รูป ![alt](url) — ต้องก่อนลิงก์ เพราะมี [..](..) เหมือนกัน
    s = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)",
               r'<img src="\2" alt="\1" loading="lazy" style="max-width:100%;height:auto;border-radius:12px;margin:6px 0">', s)
    s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    return s


_HR = re.compile(r"^\s*([-*_])\s*(?:\1\s*){2,}$")


def _cells(line: str) -> list:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_row(line: str) -> bool:
    """แถวตาราง — ต้องขึ้นต้นด้วย | ไม่งั้นประโยคธรรมดาที่มีขีดคั่นจะถูกนับเป็นตาราง"""
    s = line.strip()
    return s.startswith("|") and s.count("|") >= 2


def _is_sep(line: str) -> bool:
    """แถวคั่นหัวตาราง |---|:---:| — ทุกช่องมีแต่ขีดกับทวิภาค"""
    cs = _cells(line)
    return bool(cs) and all(c and "-" in c and set(c) <= set(":-") for c in cs)


def _table(head: list, body: list) -> str:
    n = len(head)
    th = "".join(f"<th>{_md_inline(c)}</th>" for c in head)
    rows = ["<tr>" + "".join(f"<td>{_md_inline(c)}</td>" for c in (r + [""] * n)[:n]) + "</tr>"
            for r in body]
    return ('<div class="tbl"><table><thead><tr>' + th + "</tr></thead><tbody>"
            + "".join(rows) + "</tbody></table></div>")


def md_to_html(md: str) -> str:
    """แปลง markdown → HTML แบบทีละบรรทัด (ทนต่อ output ของ LLM ที่ใช้ single newline).

    รองรับ หัวข้อ · ย่อหน้า · รายการแบบจุดและแบบนับเลข · กรอบคำพูด (>) · ตาราง GFM
    · เส้นคั่น · และ inline (ตัวหนา ลิงก์ รูป โค้ด)

    ตารางเปรียบเทียบกับกรอบ "สรุป:" เป็นของที่ AI เขียนมาแทบทุกบทความ ก่อนหน้านี้
    ตกลงไปเป็นย่อหน้าเดียวที่มีขีดกับ | ปนกันทั้งก้อน อ่านไม่ออกทั้งคนและบอท
    """
    lines = (md or "").replace("\r\n", "\n").split("\n")
    out, para, items, quote = [], [], [], []
    ordered = [False]

    def flush_para():
        if para:
            out.append("<p>" + "<br>".join(para) + "</p>")
            para.clear()

    def flush_list():
        if items:
            tag = "ol" if ordered[0] else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{x}</li>" for x in items) + f"</{tag}>")
            items.clear()
        ordered[0] = False

    def flush_quote():
        if quote:
            out.append("<blockquote><p>" + "<br>".join(quote) + "</p></blockquote>")
            quote.clear()

    def flush_all():
        flush_para(); flush_list(); flush_quote()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            flush_all(); i += 1; continue
        # ตาราง: แถวหัวต้องมีแถวคั่นต่อท้ายทันที ไม่งั้นไม่ใช่ตาราง
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if _is_row(line) and _is_row(nxt) and _is_sep(nxt):
            flush_all()
            head = _cells(line)
            i += 2
            body = []
            while i < len(lines) and _is_row(lines[i]):
                body.append(_cells(lines[i])); i += 1
            out.append(_table(head, body))
            continue
        mh = re.match(r"^(#{1,6})\s+(.*)$", line)
        if mh:
            flush_all()
            tag = "h2" if len(mh.group(1)) <= 2 else ("h3" if len(mh.group(1)) == 3 else "h4")
            out.append(f"<{tag}>{_md_inline(mh.group(2).strip())}</{tag}>")
            i += 1; continue
        mq = re.match(r"^>\s?(.*)$", line)
        if mq:
            flush_para(); flush_list()
            quote.append(_md_inline(mq.group(1).strip()))
            i += 1; continue
        flush_quote()
        if _HR.match(line):
            flush_all(); out.append("<hr>"); i += 1; continue
        mb = re.match(r"^[-*]\s+(.*)$", line)
        mo = re.match(r"^\d{1,3}[.)]\s+(.*)$", line)
        if mb or mo:
            flush_para()
            want = bool(mo)
            if items and ordered[0] != want:
                flush_list()
            ordered[0] = want
            items.append(_md_inline((mo or mb).group(1).strip()))
            i += 1; continue
        flush_list()
        para.append(_md_inline(line))
        i += 1
    flush_all()
    return "\n".join(out)


def publish_post(site_url, user, app_password, title, body_md, status="draft", wp_post_id=None) -> dict:
    payload = {"title": title, "content": md_to_html(body_md), "status": status}
    auth = _auth(user, app_password)
    base = _api(site_url)
    url = f"{base}/posts/{wp_post_id}" if wp_post_id else f"{base}/posts"
    try:
        r = httpx.post(url, json=payload, auth=auth, timeout=30, follow_redirects=True)
        if r.status_code in (200, 201):
            j = r.json()
            lbl = "เผยแพร่ (public)" if status == "publish" else "ร่าง (draft)"
            return {"ok": True, "id": j.get("id"), "link": j.get("link", ""), "msg": f"ส่งเข้า WordPress แล้ว — {lbl}"}
        return {"ok": False, "msg": _explain_error(r)}
    except Exception as e:
        return {"ok": False, "msg": f"ส่งไม่สำเร็จ: {e}"}


# ---------- Connector plugin (Phase C) ----------
def _connector_base(site_url: str) -> str:
    u = (site_url or "").strip().rstrip("/")
    if not u.startswith("http"):
        u = "https://" + u
    return u + "/wp-json/jor-ai/v1"


def connector_ping(site_url: str, api_key: str) -> dict:
    try:
        r = httpx.get(_connector_base(site_url) + "/ping", headers={"X-JorAI-Key": api_key},
                      timeout=15, follow_redirects=True)
        if r.status_code == 200 and r.json().get("connected"):
            return {"ok": True, "msg": f"Connector เชื่อมต่อแล้ว (v{r.json().get('version', '?')})"}
        return {"ok": False, "msg": f"Connector ปฏิเสธ (HTTP {r.status_code})"}
    except Exception as e:
        return {"ok": False, "msg": f"ติดต่อ Connector ไม่ได้: {e}"}


def connector_push_settings(site_url, api_key, org_schema=None, llms_txt=None, ai_bots=None) -> dict:
    """ตั้งค่า org schema + llms.txt + เปิด AI bots ในปลั๊กอินอัตโนมัติ (ส่งเฉพาะฟิลด์ที่ไม่ None)"""
    payload = {}
    if ai_bots is not None:
        payload["ai_bots"] = 1 if ai_bots else 0
    if org_schema is not None:
        payload["org_schema"] = org_schema
    if llms_txt is not None:
        payload["llms_txt"] = llms_txt
    try:
        r = httpx.post(_connector_base(site_url) + "/settings", json=payload,
                       headers={"X-JorAI-Key": api_key}, timeout=20, follow_redirects=True)
        if r.status_code in (200, 201):
            return {"ok": True, "msg": "ตั้งค่า schema + llms.txt อัตโนมัติแล้ว"}
        if r.status_code == 404:
            return {"ok": False, "msg": "ปลั๊กอินเวอร์ชันเก่า — อัปเดตปลั๊กอินเป็นเวอร์ชันล่าสุดก่อน (มี endpoint /settings)"}
        return {"ok": False, "msg": "ตั้งค่าอัตโนมัติไม่สำเร็จ: " + _explain_error(r)}
    except Exception as e:
        return {"ok": False, "msg": f"ตั้งค่าอัตโนมัติไม่ได้: {e}"}


def connector_publish(site_url, api_key, title, body_md, schema_json=None, meta_title=None,
                      meta_desc=None, status="draft", wp_post_id=None) -> dict:
    payload = {"title": title, "content_html": md_to_html(body_md), "status": status}
    if schema_json:
        payload["schema_json"] = schema_json
    if meta_title:
        payload["meta_title"] = meta_title
    if meta_desc:
        payload["meta_desc"] = meta_desc
    if wp_post_id:
        payload["post_id"] = wp_post_id
    try:
        r = httpx.post(_connector_base(site_url) + "/publish", json=payload,
                       headers={"X-JorAI-Key": api_key}, timeout=30, follow_redirects=True)
        if r.status_code in (200, 201):
            j = r.json()
            lbl = "เผยแพร่ (public)" if status == "publish" else "ร่าง (draft)"
            return {"ok": True, "id": j.get("id"), "link": j.get("link", ""), "msg": f"ส่งผ่าน Connector แล้ว — {lbl} (พร้อม schema)"}
        return {"ok": False, "msg": f"Connector ตอบ HTTP {r.status_code}: {r.text[:160]}"}
    except Exception as e:
        return {"ok": False, "msg": f"ส่งไม่สำเร็จ: {e}"}
