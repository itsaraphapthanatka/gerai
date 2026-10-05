"""หา/สร้างรูปประกอบคอนเทนต์ GEO
1) scrape รูปจากเว็บแบรนด์ (img + og:image)
2) ให้โมเดล vision เลือกรูปที่เกี่ยวข้องที่สุด + คำบรรยาย — ส่งรูปเป็น base64 (proxy/แบ็กเอนด์ดึง URL ภายนอกเองไม่ได้)
3) ถ้าไม่มี → ขอโมเดลรูป (AI_IMAGE_MODEL) generate ให้ — ถ้า proxy ไม่มีโมเดลรูป ข้ามและบอกเหตุผล
ทุกขั้นคืน "เหตุผล" กลับไปด้วย เพื่อบันทึกไว้ที่ชิ้นงานและโชว์บนหน้าเว็บ ไม่เงียบเหมือนเดิม
"""
from __future__ import annotations
import os
import re
import io
import json
import time
import base64
import urllib.request
import urllib.error
from urllib.parse import urljoin

from . import ai_client

MAX_CANDIDATES = 5          # ส่งให้โมเดลดูไม่เกินนี้ (คำขอใหญ่ขึ้นตามจำนวนรูป)
SHRINK_OVER_BYTES = 600_000  # รูปใหญ่กว่านี้ย่อก่อนส่ง — โมเดลไม่ต้องการความละเอียดสูง, base64 บวกอีก 33%
INLINE_MAX_PX = 1024
MAX_INLINE_BYTES = 2_500_000

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


def _mime(raw: bytes) -> str:
    """ดูชนิดรูปจาก magic bytes — นามสกุลใน URL เชื่อไม่ได้"""
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if raw[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return ""


def _shrink(raw: bytes, max_px: int = INLINE_MAX_PX) -> bytes:
    """ย่อให้ด้านยาวไม่เกิน max_px เป็น JPEG — ย่อไม่ได้ (ไม่มี Pillow/ไฟล์เสีย) คืนของเดิม"""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        im.thumbnail((max_px, max_px))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=82)
        return buf.getvalue()
    except Exception:
        return raw


def inline_image(url: str, fetch=None) -> tuple[str | None, str]:
    """ดึงรูปแล้วแปลงเป็น data: URL ให้โมเดล vision — คืน (data_url, เหตุผล) · ไม่ได้ = (None, เหตุผล)"""
    raw = (fetch or _fetch_bytes)(url)
    if not raw:
        return None, "ดึงรูปไม่ได้"
    mime = _mime(raw)
    if not mime:
        return None, "ไฟล์ไม่ใช่รูป"
    if len(raw) > SHRINK_OVER_BYTES or mime == "image/gif":
        small = _shrink(raw)
        if small is not raw:
            raw, mime = small, "image/jpeg"
    if len(raw) > MAX_INLINE_BYTES:
        return None, "รูปใหญ่เกิน"
    return f"data:{mime};base64," + base64.b64encode(raw).decode(), "ok"


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


def pick_relevant(candidates: list[dict], topic: str, brand_name: str, fetch=None) -> tuple[dict | None, str]:
    """vision: ให้โมเดลดูรูปจริงแล้วเลือกรูปที่เหมาะกับหัวข้อ + คำบรรยาย — คืน (รูปที่เลือก | None, เหตุผล)
    รูปถูกส่งเป็น base64 เพราะ proxy/แบ็กเอนด์ดึง URL ภายนอกเองแล้วล้ม (HTTP 500) — เลขลำดับที่โมเดลเห็นนับเฉพาะรูปที่แนบได้"""
    cands, skipped = [], []
    for c in candidates:
        if len(cands) >= MAX_CANDIDATES:
            break
        durl, why = inline_image(c["url"], fetch)
        if durl:
            cands.append({**c, "data": durl})
        else:
            skipped.append(why)
    if not cands:
        return None, ("รูปบนเว็บใช้ไม่ได้ (" + ", ".join(sorted(set(skipped))) + ")") if skipped else "เว็บแบรนด์ไม่มีรูปให้เลือก"
    parts = [{"type": "text", "text":
        f"รูปต่อไปนี้มาจากเว็บไซต์ของ {brand_name}. บทความหัวข้อ: \"{topic}\".\n"
        f"รูปไหน (ลำดับ 1-{len(cands)}) เหมาะใช้ประกอบบทความนี้ที่สุด? "
        "ตอบรูปแบบเดียว: เลข|คำบรรยายภาพภาษาไทยสั้นๆ (เช่น 2|โกดังพื้นที่กว้างใกล้ถนนใหญ่). "
        "ถ้าไม่มีรูปไหนเกี่ยวข้องเลย ตอบ 0"}]
    for i, c in enumerate(cands, 1):
        parts.append({"type": "text", "text": f"รูปที่ {i}:"})
        parts.append({"type": "image_url", "image_url": {"url": c["data"]}})
    try:
        raw = ai_client._chat([{"role": "user", "content": parts}], max_tokens=2048, timeout=160)
    except Exception as e:
        return None, f"โมเดล vision ตอบไม่ได้ ({type(e).__name__}: {str(e)[:60]})"
    m = re.search(r"(\d+)\s*\|\s*(.+)", raw or "")
    if m:
        idx, cap = int(m.group(1)), m.group(2).strip()
    else:
        m2 = re.search(r"\b(\d)\b", raw or "")
        if not m2:
            return None, "อ่านคำตอบโมเดลไม่ออก"
        idx, cap = int(m2.group(1)), ""
    if idx == 0:
        return None, "โมเดลบอกว่าไม่มีรูปบนเว็บที่เกี่ยวข้อง"
    if idx < 1 or idx > len(cands):
        return None, "โมเดลตอบเลขรูปที่ไม่มี"
    ch = cands[idx - 1]
    alt = cap or ch.get("alt") or topic
    return {"url": ch["url"], "alt": alt}, f"เลือกรูปจากเว็บ: {alt[:60]}"


# ห้าม generate รูปคน — ใช้เป็น negative prompt (ถ้า server รองรับ) + ย้ำในตัว prompt
_NO_PEOPLE = ("person, people, human, humans, man, woman, women, men, child, children, "
              "kid, boy, girl, face, faces, portrait, selfie, crowd, body, body parts, hands, "
              "arms, legs, skin, model, worker, customer")


def image_model() -> str:
    return os.getenv("AI_IMAGE_MODEL", "dreamshaper")


def generate_image(prompt: str, size: str = "768x512", negative_prompt: str = None) -> tuple[bytes | None, str]:
    """ขอโมเดลรูปที่ proxy สร้างภาพจาก prompt → (PNG bytes | None, เหตุผล) — ไม่สร้างรูปคน"""
    model = image_model()
    payload = json.dumps({
        "model": model, "prompt": prompt, "n": 1, "size": size,
        "negative_prompt": negative_prompt or _NO_PEOPLE,
    }).encode()
    req = urllib.request.Request(
        ai_client.BASE_URL.rstrip("/") + "/images/generations", data=payload,
        headers={"Authorization": f"Bearer {ai_client.API_KEY}", "Content-Type": "application/json", "User-Agent": _UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=160) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read(600).decode("utf-8", "ignore")
        except Exception:
            pass
        if e.code in (400, 404) and ("Invalid model" in body or "model" in body.lower() and "not" in body.lower()):
            return None, f"proxy ไม่มีโมเดลรูป “{model}”"
        return None, f"proxy ตอบ HTTP {e.code} ตอนสร้างรูป"
    except Exception as e:
        return None, f"สร้างรูปไม่ได้ ({type(e).__name__})"
    item = (data.get("data") or [{}])[0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"]), "สร้างรูปใหม่"
    if item.get("url"):
        png = _fetch_bytes(item["url"])
        return (png, "สร้างรูปใหม่") if png else (None, "ดึงรูปที่สร้างไม่ได้")
    return None, "โมเดลรูปไม่ส่งรูปกลับมา"


# ---- proxy มีโมเดลรูปไหม (เช็ค /models แล้วจำไว้ 1 ชม.) — ใช้ข้ามขั้นสร้างรูป และบอกสถานะบนหน้าเว็บ ----
_CAP: dict = {"ts": 0.0, "data": None}


def list_models(timeout: int = 8) -> list[str]:
    req = urllib.request.Request(ai_client.BASE_URL.rstrip("/") + "/models",
                                 headers={"Authorization": f"Bearer {ai_client.API_KEY}", "User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    return [m.get("id") for m in (data.get("data") or []) if m.get("id")]


def capabilities(fresh: bool = False, ttl: int = 3600) -> dict:
    """{'image_model', 'generate': True|False|None(ตรวจไม่ได้), 'models', 'error', 'checked_at'}"""
    now = time.time()
    if not fresh and _CAP["data"] and now - _CAP["ts"] < ttl:
        return _CAP["data"]
    model = image_model()
    try:
        ids = list_models()
        data = {"image_model": model, "generate": model in ids, "models": ids, "error": None}
    except Exception as e:
        data = {"image_model": model, "generate": None, "models": [], "error": f"{type(e).__name__}: {str(e)[:80]}"}
    data["checked_at"] = now
    _CAP.update(ts=now, data=data)
    return data


def describe(cap: dict) -> str:
    """ข้อความสถานะสำหรับหน้าคอนเทนต์"""
    if cap.get("generate") is True:
        return f"✓ สร้างรูปใหม่ได้ด้วยโมเดล {cap['image_model']}"
    if cap.get("generate") is False:
        return (f"⚠️ สร้างรูปใหม่ไม่ได้ตอนนี้ — proxy AI ไม่มีโมเดลรูป “{cap['image_model']}” (มีแต่โมเดลข้อความ) "
                f"จึงใช้ได้เฉพาะรูปจากเว็บแบรนด์ · เพิ่มโมเดลรูปที่ proxy แล้วตั้ง AI_IMAGE_MODEL ให้ตรงชื่อ")
    return f"⚠️ ตรวจโมเดลสร้างรูปไม่ได้ ({cap.get('error') or 'proxy ไม่ตอบ'})"


def _scene_prompt(brand, topic: str) -> str:
    """ใช้ text AI แปลงหัวข้อบทความ → image prompt ของ 'ฉากที่ไม่มีคน'
    (สถานที่/วัตถุ/อุปกรณ์/บรรยากาศ) เพราะหัวข้อมักพูดถึงคนตรงๆ ทำให้โมเดลวาดคน"""
    market = brand["market"] or ""
    scene = ""
    try:
        ask = ("Write a short English image-generation prompt (one line, under 40 words) for a "
               "professional commercial photo that illustrates this article topic but contains "
               "ABSOLUTELY NO people. Describe only the place, interior, objects, tools, products, "
               "or scenery.\n"
               f"Business: {market}\nTopic: {topic}\n"
               "Answer with the prompt text only, no quotes.")
        raw = ai_client._chat([{"role": "user", "content": ask}], max_tokens=900, timeout=70)
        lines = [l.strip(" \"'`*-•") for l in raw.splitlines() if l.strip(" \"'`*-•")]
        scene = lines[-1] if lines else ""
    except Exception:
        scene = ""
    if len(scene) < 8:
        scene = f"interior, objects and scenery of a {market or 'business'}"
    return (f"Professional realistic commercial photograph: {scene}. "
            f"The scene is completely empty of people — no humans, no faces, no hands, "
            f"no body parts, no silhouettes. Bright, clean, high quality, no text, no watermark.")


def _has_person(png: bytes) -> bool:
    """vision: ในรูปมีคนไหม (ถ้าเช็คไม่ได้ คืน False = ไม่บล็อก)"""
    try:
        durl = "data:image/png;base64," + base64.b64encode(png).decode()
        parts = [{"type": "text", "text":
                  "Is any person, human, face, or body part visible in this image? "
                  "Answer with one word only: YES or NO."},
                 {"type": "image_url", "image_url": {"url": durl}}]
        ans = ai_client._chat([{"role": "user", "content": parts}], max_tokens=1200, timeout=120)
        toks = re.findall(r"\b(yes|no)\b", ans.lower())
        return toks[-1] == "yes" if toks else False
    except Exception:
        return False


def _generate_no_people(brand, topic: str) -> tuple[bytes | None, str]:
    """generate รูป + ตรวจ vision ว่าไม่มีคน — ยังมีคนก็ลองใหม่ ถ้า 2 รอบยังมีคน คืน None
    (ยอมไม่มีรูป ดีกว่าใส่รูปคน) · proxy ไม่มีโมเดลรูป → หยุดทันที ไม่ลองรอบสอง"""
    attempts = [
        _scene_prompt(brand, topic),
        (f"Wide professional photograph of an empty {brand['market'] or 'business'} environment — "
         f"only objects, tools, furniture and scenery. Absolutely no people, no living beings, "
         f"no faces, no body parts. Bright, clean, high quality, no text, no watermark."),
    ]
    reasons = []
    for p in attempts:
        png, why = generate_image(p)
        if not png:
            reasons.append(why)
            if "ไม่มีโมเดลรูป" in why or "HTTP 4" in why:
                break
            continue
        if not _has_person(png):
            return png, why
        reasons.append("รูปที่สร้างมีคน")
    return None, "; ".join(dict.fromkeys(reasons)) or "สร้างรูปไม่ได้"


def image_for_article(brand, topic: str, site_url: str) -> dict:
    """หารูปจากเว็บก่อน (vision) → ไม่มีค่อย generate (รูปที่ไม่มีคนเท่านั้น)
    คืน dict เสมอ: {'mode': 'scraped','url','alt','note'} | {'mode': 'generated','png','alt','note'} | {'mode': 'none','note'}
    note = เหตุผลที่ได้/ไม่ได้ (บันทึกไว้ที่ชิ้นงาน ให้คนดูรู้ว่าทำไมไม่มีรูป)"""
    notes = []
    try:
        cands = scrape_site_images(site_url)
    except Exception as e:
        cands, notes = [], [f"ดึงรูปจากเว็บแบรนด์ไม่ได้ ({type(e).__name__})"]
    if cands:
        pick, why = pick_relevant(cands, topic, brand["name"])
        if pick:
            return {"mode": "scraped", "url": pick["url"], "alt": pick["alt"], "note": why}
        notes.append(why)
    elif not notes:
        notes.append("เว็บแบรนด์ไม่มีรูปให้เลือก")
    cap = capabilities()
    if cap.get("generate") is False:
        notes.append(f"ข้ามการสร้างรูป — proxy ไม่มีโมเดลรูป “{cap['image_model']}”")
        return {"mode": "none", "note": " · ".join(notes)}
    png, why = _generate_no_people(brand, topic)
    if png:
        return {"mode": "generated", "png": png, "alt": topic, "note": why}
    notes.append(why)
    return {"mode": "none", "note": " · ".join(notes)}
