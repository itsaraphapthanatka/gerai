"""AI Overview และ AI Mode ของ Google — แบรนด์โผล่ในคำตอบ AI ของ Google เองไหม

SoV/อันดับที่มีอยู่วัด "ผลค้นหา 10 ลิงก์" ส่วน ai_visibility วัดผู้ช่วย AI นอก Google (ChatGPT ฯลฯ)
แต่คนไทยส่วนใหญ่เจอ AI ของ Google ก่อนเพื่อน: AI Overview (กล่องสรุปเหนือผลค้น) และ AI Mode (แท็บคุยกับ Gemini)
สองอย่างนี้ไม่มี API จาก Google และ Serper ที่ใช้วัด SoV ก็ไม่ให้ จึงใช้ SerpApi (คีย์แยก ใส่ที่ตั้งค่าระบบ)
  AI Overview: ค้นปกติ (engine=google) → ถ้ามี ai_overview ในผลอ่านเลย ถ้าให้แค่ page_token ค่อยยิงต่อ
               (engine=google_ai_overview, token อายุ 1 นาที) · AI Mode: engine=google_ai_mode
ตัดสินด้วย judge_mention เดียวกับผู้ช่วย AI (อ้างอิง > เอ่ยชื่อ > ไม่พูดถึง) และแยก "Google ไม่แสดง AI Overview"
ออกต่างหาก — คำถามที่ Google ไม่ขึ้นกล่อง AI ไม่ใช่ความผิดของแบรนด์ จึงไม่นับเข้าตัวหาร
ค่าใช้จ่าย: 1 search/คำถาม/แบบ (AI Overview บางคำถามใช้ 2 ถ้าต้องยิง token) · SerpApi คิดเป็นโควตา search/เดือน
extract_*/blocks_text ไม่ยิงเน็ต — เทสต์ได้จาก JSON ตัวอย่าง (aiserp_smoke.py)
"""
from __future__ import annotations
import os
import datetime

from .ai_visibility import judge_mention, _read_key, stored_key_preview, CITED, NAMED, ABSENT, ERROR, SKIP
from .site_health import host_of

BASE = "https://serpapi.com/search.json"
SPEC = {"label": "SerpApi", "db_key": "serpapi_key", "env": "SERPAPI_API_KEY", "hint": "serpapi.com/manage-api-key"}
OVERVIEW, MODE = "overview", "mode"
KINDS = {OVERVIEW: "AI Overview", MODE: "AI Mode"}
NONE_SHOWN = "none"              # Google ไม่แสดง AI Overview สำหรับคำถามนี้
MAX_Q = int(os.getenv("GEO_AISERP_MAX_QUESTIONS", "8"))
TIMEOUT = 60
HL, GL = "th", "th"


def _key():
    return _read_key(SPEC)


def key_preview() -> str:
    return stored_key_preview(SPEC)


def available() -> bool:
    return bool(_key())


def _get(params: dict) -> dict:
    import httpx
    r = httpx.get(BASE, params={**params, "api_key": _key()}, timeout=TIMEOUT)
    r.raise_for_status()
    j = r.json()
    if j.get("error"):                       # SerpApi ตอบ 200 พร้อม error เมื่อโควตาหมด/คีย์ผิด
        raise RuntimeError(j["error"])
    return j


# ---------- pure ----------
def blocks_text(blocks) -> str:
    """รวมข้อความจาก text_blocks ของ SerpApi (paragraph/heading/list ซ้อนกันได้)"""
    out = []
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        for k in ("snippet", "title"):
            if b.get(k):
                out.append(str(b[k]))
        for item in b.get("list") or []:
            if isinstance(item, dict):
                out.append(" ".join(str(item[k]) for k in ("title", "snippet") if item.get(k)))
                if item.get("list") or item.get("text_blocks"):
                    out.append(blocks_text(item.get("list") or item.get("text_blocks")))
        if b.get("text_blocks"):
            out.append(blocks_text(b["text_blocks"]))
    return "\n".join(s for s in out if s)


def refs_links(refs) -> list:
    return [r["link"] for r in (refs or []) if isinstance(r, dict) and r.get("link")]


def extract_overview(raw: dict) -> dict:
    ao = (raw or {}).get("ai_overview")
    if not ao or not isinstance(ao, dict):
        return {"present": False, "text": "", "sources": [], "token": None}
    text = blocks_text(ao.get("text_blocks"))
    return {"present": bool(text or ao.get("page_token")), "text": text,
            "sources": refs_links(ao.get("references")), "token": ao.get("page_token")}


def extract_mode(raw: dict) -> dict:
    raw = raw or {}
    text = blocks_text(raw.get("text_blocks")) or (raw.get("reconstructed_markdown") or "")
    return {"text": text, "sources": refs_links(raw.get("references"))}


# ---------- network ----------
def ask_overview(question: str) -> dict:
    raw = _get({"engine": "google", "q": question, "hl": HL, "gl": GL, "google_domain": "google.co.th"})
    ov = extract_overview(raw)
    searches = 1
    if ov["present"] and not ov["text"] and ov["token"]:
        # Google ให้แค่ token — ต้องขอเนื้อหาเต็มอีกครั้งภายใน 1 นาที
        full = _get({"engine": "google_ai_overview", "page_token": ov["token"]})
        ov2 = extract_overview({"ai_overview": full.get("ai_overview") or full})
        ov = {**ov, "text": ov2["text"], "sources": ov2["sources"] or ov["sources"]}
        searches = 2
    return {**ov, "searches": searches}


def ask_mode(question: str) -> dict:
    raw = _get({"engine": "google_ai_mode", "q": question, "hl": HL, "gl": GL})
    return {**extract_mode(raw), "present": True, "searches": 1}


def _empty_kind(label: str) -> dict:
    return {"label": label, "asked": 0, "shown": 0, "cited": 0, "named": 0, "absent": 0, "none": 0, "errors": 0, "rate": 0}


def check_brand(brand, questions, aliases=()) -> dict:
    """ถาม AI Overview + AI Mode ทุกคำถามเป้าหมาย (สูงสุด MAX_Q) แล้วสรุป — ไม่มีคีย์ = skip ทั้งรอบ"""
    from . import geo_content
    checked_at = datetime.datetime.now().isoformat(timespec="microseconds")
    qs = [dict(qq) for qq in questions][:MAX_Q]
    by_kind = {k: _empty_kind(v) for k, v in KINDS.items()}
    res = {"checked_at": checked_at, "rows": [], "by_kind": by_kind, "asked": 0, "searches": 0, "skip": ""}
    if not _key():
        res["skip"] = "ยังไม่ได้ตั้งคีย์ SerpApi (ผู้ดูแล → ตั้งค่า → Google AI Overview / AI Mode)"
        return res
    apex = host_of(geo_content._site_url(brand))
    for kind in (OVERVIEW, MODE):
        bk = by_kind[kind]
        for qq in qs:
            text = qq.get("question") or ""
            row = {"kind": kind, "question_id": qq.get("id"), "question": text, "status": ERROR,
                   "cited": False, "named": False, "others": [], "sources": [], "excerpt": "", "reason": "", "searches": 0}
            try:
                r = ask_overview(text) if kind == OVERVIEW else ask_mode(text)
                row["searches"] = r.get("searches", 1)
                bk["asked"] += 1
                if not r.get("present"):
                    row["status"] = NONE_SHOWN
                    bk["none"] += 1
                else:
                    v = judge_mention(r["text"], r["sources"], apex, brand["name"], aliases)
                    row.update(status=v["status"], cited=v["cited"], named=v["named"], others=v["others"],
                               sources=r["sources"][:12], excerpt=(r["text"] or "")[:800])
                    bk["shown"] += 1
                    bk[v["status"]] += 1
            except Exception as e:
                row["reason"] = f"{type(e).__name__}: {str(e)[:200]}"
                bk["errors"] += 1
            res["searches"] += row["searches"]
            res["rows"].append(row)
        bk["rate"] = round(100 * (bk["cited"] + bk["named"]) / bk["shown"]) if bk["shown"] else 0
    res["asked"] = sum(b["asked"] for b in by_kind.values())
    return res
