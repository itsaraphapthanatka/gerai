"""Page Speed — ความเร็วหน้าเว็บจาก Google PageSpeed Insights (Lighthouse + ข้อมูลผู้ใช้จริง CrUX)

ความเร็วเป็นสัญญาณอันดับของ Google (Core Web Vitals) และหน้าที่โหลดช้ามากบอทอ่านได้ไม่ครบ/หมดเวลา
เช็คหน้าแรกทั้ง mobile และ desktop สัปดาห์ละครั้งผ่าน API ของ Google โดยตรง — ไม่มีคีย์ก็เรียกได้
(โควตาต่ำ พอสำหรับหลักสิบครั้ง/วัน) ใส่คีย์จาก Google Cloud เมื่อแบรนด์เยอะขึ้น
summarize/grade ไม่ยิงเน็ต — เทสต์ได้จาก JSON ตัวอย่าง (speed_smoke.py)
"""
from __future__ import annotations
import os
import datetime

API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
SPEC = {"label": "PageSpeed Insights", "db_key": "pagespeed_key", "env": "PAGESPEED_API_KEY",
        "hint": "console.cloud.google.com → เปิด PageSpeed Insights API → Credentials → API key"}
TIMEOUT = int(os.getenv("GEO_PSI_TIMEOUT", "120"))      # Lighthouse ใช้เวลา 20–60 วิต่อหน้า
CATEGORIES = (("performance", "ประสิทธิภาพ"), ("seo", "SEO"),
              ("accessibility", "การเข้าถึง"), ("best-practices", "แนวปฏิบัติ"))
LAB = (("largest-contentful-paint", "LCP"), ("cumulative-layout-shift", "CLS"),
       ("total-blocking-time", "TBT"), ("first-contentful-paint", "FCP"),
       ("speed-index", "Speed Index"), ("interactive", "TTI"))
FIELD = (("LARGEST_CONTENTFUL_PAINT_MS", "LCP"), ("INTERACTION_TO_NEXT_PAINT", "INP"),
         ("CUMULATIVE_LAYOUT_SHIFT_SCORE", "CLS"), ("FIRST_CONTENTFUL_PAINT_MS", "FCP"))
GOOD, OK, POOR = "good", "ok", "poor"
STRATEGIES = ("mobile", "desktop")


def _key():
    from . import db
    v = (db.get_setting(SPEC["db_key"]) or os.getenv(SPEC["env"], "") or "").strip()
    return v or None


def key_preview() -> str:
    from .ai_visibility import stored_key_preview
    return stored_key_preview(SPEC)


def grade(score) -> str:
    """เกณฑ์เดียวกับ Lighthouse: 90 ขึ้นไปเขียว, 50–89 ส้ม, ต่ำกว่า 50 แดง"""
    if score is None:
        return ""
    return GOOD if score >= 90 else (OK if score >= 50 else POOR)


def summarize(raw: dict) -> dict:
    """ย่อผล PSI ให้เหลือสิ่งที่ตัดสินใจได้ — คะแนน 4 หมวด, เมตริก lab, ข้อมูลผู้ใช้จริง, สิ่งที่ควรแก้เรียงตามเวลาที่ประหยัดได้"""
    lh = raw.get("lighthouseResult") or {}
    cats = lh.get("categories") or {}
    scores = {}
    for cid, _ in CATEGORIES:
        c = cats.get(cid) or {}
        scores[cid] = round(float(c["score"]) * 100) if c.get("score") is not None else None
    audits = lh.get("audits") or {}
    metrics = []
    for aid, label in LAB:
        a = audits.get(aid) or {}
        if not a:
            continue
        metrics.append({"id": aid, "label": label,
                        "display": (a.get("displayValue") or "").replace(" ", " "),
                        "score": round(float(a["score"]) * 100) if a.get("score") is not None else None})
    # ข้อมูลผู้ใช้จริง (CrUX) — Google ให้เฉพาะเว็บที่มีคนเข้ามากพอ ส่วนใหญ่ของลูกค้า SME จะไม่มี
    field = None
    le = raw.get("loadingExperience") or {}
    if le.get("metrics"):
        field = {"overall": le.get("overall_category"), "origin_fallback": bool(le.get("origin_fallback")), "metrics": []}
        for mid, label in FIELD:
            m = le["metrics"].get(mid)
            if m:
                field["metrics"].append({"id": mid, "label": label, "percentile": m.get("percentile"),
                                         "category": m.get("category")})
    opps = []
    for aid, a in audits.items():
        d = a.get("details") or {}
        if d.get("type") == "opportunity" and (d.get("overallSavingsMs") or 0) > 0:
            opps.append({"id": aid, "title": a.get("title") or aid, "savings_ms": int(d["overallSavingsMs"]),
                         "display": (a.get("displayValue") or "").replace(" ", " ")})
    opps.sort(key=lambda o: -o["savings_ms"])
    return {"scores": scores, "metrics": metrics, "field": field, "opportunities": opps[:6],
            "final_url": lh.get("finalDisplayedUrl") or lh.get("finalUrl") or "",
            "fetch_time": lh.get("fetchTime"), "lighthouse": lh.get("lighthouseVersion")}


def _err(e: Exception) -> str:
    try:
        import httpx
        if isinstance(e, httpx.HTTPStatusError):
            r = e.response
            try:
                m = (r.json().get("error") or {}).get("message") or r.text[:200]
            except Exception:
                m = r.text[:200]
            return f"Google ตอบ {r.status_code}: {str(m)[:220]}"
    except ImportError:
        pass
    return f"{type(e).__name__}: {str(e)[:200]}"


def fetch(url: str, strategy: str) -> dict:
    import httpx
    params = [("url", url), ("strategy", strategy), ("locale", "th")] + [("category", c) for c, _ in CATEGORIES]
    key = _key()
    if key:
        params.append(("key", key))
    r = httpx.get(API, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def scan(site_url: str) -> dict:
    """สแกนหน้าแรก mobile + desktop — ฝั่งใดล้มเก็บเหตุผลไว้ ไม่ล้มทั้งรอบ"""
    out = {"checked_at": datetime.datetime.now().isoformat(timespec="seconds"), "url": site_url, "with_key": bool(_key())}
    for strategy in STRATEGIES:
        try:
            out[strategy] = summarize(fetch(site_url, strategy))
        except Exception as e:
            msg = _err(e)
            # แบบไม่มีคีย์ใช้โควตากลางของ Google ร่วมกับทุกคนบนโลก — หมดเกือบตลอดเวลา (เจอ 429 ตั้งแต่ครั้งแรกที่ลอง)
            if not out["with_key"] and "429" in msg:
                msg = ("โควตาแบบไม่มีคีย์ของ Google หมด (ใช้ร่วมกับทุกคน) — ใส่ PageSpeed Insights API key ที่ตั้งค่าระบบ "
                       "ฟรี 25,000 ครั้ง/วัน สร้างในโปรเจกต์ Google Cloud เดียวกับ Search Console ได้")
            out[strategy] = {"error": msg}
    return out
