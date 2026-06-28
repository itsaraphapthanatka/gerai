"""หา/สร้างรูปประกอบคอนเทนต์ GEO
1) scrape รูปจากเว็บแบรนด์ (img + og:image)
2) ให้ gemma (vision) เลือกรูปที่เกี่ยวข้องที่สุด + คำบรรยาย
3) ถ้าไม่มี → dreamshaper generate รูปให้ (คืน PNG bytes)
"""
from __future__ import annotations
import os
import re
import json
import base64
import urllib.request
from urllib.parse import urljoin

from . import ai_client

_UA = "curl/8.5.0"
_PHOTO_EXT = (".jpg", ".jpeg", ".png", ".webp")
_SKIP = ("logo", "icon", "sprite", "favicon", "avatar", "placeholder", "spacer", "1x1", "loading", "blank")


def _fetch_text(url: str) -> str:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read(2_000_000).decode("utf-8", "ignore")
    except Exception:
        return ""


def _fetch_bytes(url: str) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read(8_000_000)
    except Exception:
        return None


def _ok_img(u: str) -> bool:
    low = u.lower()
    if low.startswith("data:"):
        return False
    if any(x in low for x in _SKIP):
        return False
    return low.split("?")[0].endswith(_PHOTO_EXT)


def scrape_site_images(site_url: str, limit: int = 8) -> list[dict]:
    """ดึง URL รูป (absolute) + alt จากหน้าเว็บ"""
    html = _fetch_text(site_url)
    if not html:
        return []
    out, seen = [], set()
    for m in re.finditer(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', html, re.I):
        u = urljoin(site_url, m.group(1))
        if _ok_img(u) and u not in seen:
            seen.add(u); out.append({"url": u, "alt": ""})
    for m in re.finditer(r"<img\b[^>]*>", html, re.I):
        tag = m.group(0)
        s = re.search(r'\bsrc=["\']([^"\']+)', tag, re.I) or re.search(r'\bdata-src=["\']([^"\']+)', tag, re.I)
        if not s:
            continue
        u = urljoin(site_url, s.group(1))
        if not _ok_img(u) or u in seen:
            continue
        a = re.search(r'\balt=["\']([^"\']*)', tag, re.I)
        seen.add(u); out.append({"url": u, "alt": a.group(1) if a else ""})
        if len(out) >= limit:
            break
    return out[:limit]


def pick_relevant(candidates: list[dict], topic: str, brand_name: str) -> dict | None:
    """vision: ให้โมเดลดูรูปจริงแล้วเลือกรูปที่เหมาะกับหัวข้อ + คำบรรยาย"""
    cands = candidates[:5]
    if not cands:
        return None
    parts = [{"type": "text", "text":
        f"รูปต่อไปนี้มาจากเว็บไซต์ของ {brand_name}. บทความหัวข้อ: \"{topic}\".\n"
        f"รูปไหน (ลำดับ 1-{len(cands)}) เหมาะใช้ประกอบบทความนี้ที่สุด? "
        "ตอบรูปแบบเดียว: เลข|คำบรรยายภาพภาษาไทยสั้นๆ (เช่น 2|โกดังพื้นที่กว้างใกล้ถนนใหญ่). "
        "ถ้าไม่มีรูปไหนเกี่ยวข้องเลย ตอบ 0"}]
    for i, c in enumerate(cands, 1):
        parts.append({"type": "text", "text": f"รูปที่ {i}:"})
        parts.append({"type": "image_url", "image_url": {"url": c["url"]}})
    try:
        raw = ai_client._chat([{"role": "user", "content": parts}], max_tokens=2048, timeout=160)
    except Exception:
        return None
    m = re.search(r"(\d+)\s*\|\s*(.+)", raw)
    if m:
        idx, cap = int(m.group(1)), m.group(2).strip()
    else:
        m2 = re.search(r"\b([1-9])\b", raw or "")
        if not m2:
            return None
        idx, cap = int(m2.group(1)), ""
    if idx < 1 or idx > len(cands):
        return None
    ch = cands[idx - 1]
    return {"url": ch["url"], "alt": cap or ch["alt"] or topic}


def generate_image(prompt: str, size: str = "768x512") -> bytes | None:
    """dreamshaper: สร้างภาพจาก prompt → คืน PNG bytes"""
    payload = json.dumps({
        "model": os.getenv("AI_IMAGE_MODEL", "dreamshaper"),
        "prompt": prompt, "n": 1, "size": size,
    }).encode()
    req = urllib.request.Request(
        ai_client.BASE_URL.rstrip("/") + "/images/generations", data=payload,
        headers={"Authorization": f"Bearer {ai_client.API_KEY}", "Content-Type": "application/json", "User-Agent": _UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=160) as r:
            data = json.loads(r.read())
        item = (data.get("data") or [{}])[0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        if item.get("url"):
            return _fetch_bytes(item["url"])
    except Exception:
        return None
    return None


def _gen_prompt(brand, topic: str) -> str:
    market = brand["market"] or ""
    return (f"Professional realistic commercial photograph for: {topic}. "
            f"Business context: {market}. Clean, bright, high quality, no text, no watermark.")


def image_for_article(brand, topic: str, site_url: str) -> dict | None:
    """หารูปจากเว็บก่อน (vision) → ไม่มีค่อย generate.
    คืน {'mode':'scraped','url','alt'} หรือ {'mode':'generated','png':bytes,'alt'} หรือ None
    """
    try:
        cands = scrape_site_images(site_url)
        pick = pick_relevant(cands, topic, brand["name"]) if cands else None
        if pick:
            return {"mode": "scraped", "url": pick["url"], "alt": pick["alt"]}
    except Exception:
        pass
    png = generate_image(_gen_prompt(brand, topic))
    if png:
        return {"mode": "generated", "png": png, "alt": topic}
    return None
