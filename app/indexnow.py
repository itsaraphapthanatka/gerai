"""IndexNow — บอก Bing (และเสิร์ชเอนจินที่ร่วม IndexNow) ทันทีเมื่อมีบทความใหม่บนหน้า /geo
ChatGPT ค้นเว็บผ่านดัชนีของ Bing จึงช่วยให้ AI เห็นบทความเร็วขึ้น ไม่ต้องรอบอทวนมาเอง

· คีย์เดียวทั้งระบบ (settings indexnow_key) · ไฟล์ยืนยันอยู่ที่ {เว็บ}/geo/indexnow.txt (ใต้ /geo ที่เว็บลูกค้า proxy มาหาเราแล้ว)
  ขอบเขตของคีย์ = URL ใต้ /geo/ เท่านั้น — จึงส่งเฉพาะหน้า /geo (โพสต์ WordPress ของลูกค้าไม่ส่ง)
· settings indexnow_on: startup ของ production ตั้ง "1" ให้ครั้งแรก · "0" = ปิด · ล้มเงียบ ไม่กระทบการเผยแพร่
"""
from __future__ import annotations

import secrets
import threading

ENDPOINT = "https://api.indexnow.org/indexnow"
KEY_PATH = "/geo/indexnow.txt"


def enabled() -> bool:
    from . import db
    return (db.get_setting("indexnow_on") or "0") == "1"     # production เปิดตอน startup · ไม่มีแถว (เทสต์) = ปิด ไม่ยิงเน็ตจริง


def key() -> str:
    from . import db
    k = db.get_setting("indexnow_key")
    if not k:
        k = secrets.token_hex(16)
        db.set_setting("indexnow_key", k)
    return k


def _host(site: str) -> str:
    return site.split("://", 1)[-1].split("/", 1)[0]


def geo_urls(site: str, urls: list[str]) -> list[str]:
    pre = site.rstrip("/") + "/geo/"
    return [u for u in dict.fromkeys(urls) if u and u.startswith(pre)]


def submit(site: str, urls: list[str], post=None) -> dict:
    """ส่ง URL ของเว็บหนึ่ง (ใต้ /geo/) · คืน {"sent": n, "status": code|None, "error": str}"""
    urls = geo_urls(site, urls)
    if not urls or not enabled():
        return {"sent": 0, "status": None, "error": "" if urls else "ไม่มี URL ใต้ /geo/"}
    body = {"host": _host(site), "key": key(), "keyLocation": site.rstrip("/") + KEY_PATH, "urlList": urls[:10000]}
    try:
        if post is None:
            import httpx
            post = lambda u, j: httpx.post(u, json=j, timeout=15)
        r = post(ENDPOINT, body)
        code = getattr(r, "status_code", None)
        return {"sent": len(urls) if code in (200, 202) else 0, "status": code, "error": "" if code in (200, 202) else f"HTTP {code}"}
    except Exception as e:
        return {"sent": 0, "status": None, "error": f"{type(e).__name__}: {str(e)[:120]}"}


def notify_content(content_id: int, background: bool = True) -> None:
    """เรียกหลังเผยแพร่บทความ — ส่ง URL ของบทความนั้นและหน้ารวม /geo"""
    def go():
        try:
            from . import db, geo_content
            it = db.get_content(content_id)
            if not it or it["status"] != "published":
                return
            brand = db.get_brand(it["brand_id"])
            site = geo_content._site_url(brand)
            submit(site, [geo_content.content_url(brand, it), f"{site}/geo/{it['id']}"])
        except Exception:
            pass
    threading.Thread(target=go, daemon=True).start() if background else go()
