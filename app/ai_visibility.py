"""ถาม AI ผู้ช่วยจริง ๆ ว่าแบรนด์ถูกเอ่ยถึงหรือถูกอ้างอิงไหม

ต่างจาก geo_worker ที่วัด Share of Voice: ตัวนั้นถาม *search engine* (ddgs/brave/serper)
แล้วดูว่าโดเมนแบรนด์โผล่ในผลค้นไหม ซึ่งเป็นตัวแทนที่ใช้ได้แต่ไม่ใช่สิ่งเดียวกัน —
สิ่งที่ลูกค้าซื้อคือ "พิมพ์ถาม ChatGPT แล้วแบรนด์ถูกแนะนำไหม" โมดูลนี้วัดอันนั้นตรง ๆ

ต้องเปิด web search ของแต่ละเจ้าเสมอ:
  โมเดลเปล่า ๆ ตอบจากข้อมูลที่จำมาตอนเทรน ซึ่งเก่าและไม่มีแหล่งอ้างอิง ธุรกิจ SME
  ไทยแทบไม่มีทางอยู่ในนั้น ผลที่ได้จะเป็น "ไม่ถูกเอ่ยถึง" ทุกแบรนด์ทุกคำถามจนไร้ความหมาย
  สิ่งที่วัดได้จริงคือ "พอผู้ช่วยไปค้นเว็บมาตอบ มันหยิบแบรนด์นี้มาอ้างไหม"

ฟังก์ชัน parse_* และ judge_mention รับข้อมูลที่ดึงมาแล้ว ไม่ยิงเน็ตเอง — เทสต์ได้
โดยไม่ต้องมีคีย์ (ตัวตรวจที่ฟ้องผิดอันตรายกว่าไม่มีตัวตรวจ)
"""
from __future__ import annotations
import os
import re
import json
import datetime

from .site_health import host_of   # ตัดโดเมนให้เหลือ host เปล่า ใช้ตัวเดียวกันทั้งระบบ

CITED, NAMED, ABSENT, SKIP, ERROR = "cited", "named", "absent", "skip", "error"

TIMEOUT = int(os.getenv("GEO_AI_TIMEOUT", "90"))     # ค้นเว็บ+เรียบเรียง ใช้เวลาหลายสิบวินาที
MAX_Q = int(os.getenv("GEO_AI_MAX_QUESTIONS", "8"))  # เพดานคำถามต่อรอบ — ทุกคำถามมีค่าใช้จ่าย

# แต่ละเจ้าใช้คีย์คนละตัว ไม่มีคีย์ = ข้าม ไม่ใช่ตก (แบบเดียวกับ Serper ใน site_health)
ENGINES = {
    "chatgpt":    {"label": "ChatGPT",    "env": "OPENAI_API_KEY"},
    "claude":     {"label": "Claude",     "env": "ANTHROPIC_API_KEY"},
    "perplexity": {"label": "Perplexity", "env": "PERPLEXITY_API_KEY"},
    "gemini":     {"label": "Gemini",     "env": "GEMINI_API_KEY"},
}


def _key(engine: str):
    return (os.getenv(ENGINES[engine]["env"]) or "").strip() or None


def available_engines() -> list[str]:
    """เจ้าที่ตั้งคีย์ไว้แล้ว — ที่เหลือจะถูกบันทึกเป็น skip พร้อมเหตุผล"""
    return [e for e in ENGINES if _key(e)]


# ---------- judge: ตัดสินจากคำตอบที่ได้มาแล้ว ----------
def judge_mention(text: str, citations: list, apex: str, brand_name: str,
                  aliases=()) -> dict:
    """แยก 3 ระดับ เพราะความหมายทางธุรกิจต่างกันมาก

    cited  = ผู้ช่วยเอาเว็บแบรนด์ไปเป็นแหล่งอ้างอิง (ดีที่สุด คอนเทนต์เราถูกใช้จริง)
    named  = ถูกเอ่ยชื่อแต่อ้างจากที่อื่น (ดี แต่เราไม่ได้คุมเนื้อหาที่เขาอ่าน)
    absent = ไม่ถูกพูดถึงเลย

    ถ้าเหมารวมเป็น "เห็น/ไม่เห็น" จะมองไม่ออกว่าต้องแก้อะไร — cited ต่ำแต่ named สูง
    แปลว่าคอนเทนต์เราไม่ถูกหยิบ ส่วน named ต่ำทั้งคู่แปลว่าแบรนด์ยังไม่อยู่ในบทสนทนา
    """
    apex = (apex or "").lower()
    cited_hosts = [host_of(u) for u in (citations or []) if u]
    cited = apex in cited_hosts if apex else False

    body = (text or "").lower()
    # ชื่อสั้นกว่า 3 ตัวอักษรเจอโดยบังเอิญได้ง่ายเกินไป เลยไม่นับ
    names = [n.strip().lower() for n in (brand_name, *aliases) if n and len(n.strip()) >= 3]
    named = any(n in body for n in names)

    status = CITED if cited else (NAMED if named else ABSENT)
    return {
        "status": status,
        "cited": cited,
        "named": named,
        # โดเมนอื่นที่ถูกอ้าง = คู่แข่งที่ชนะคำถามนี้ มีค่าพอ ๆ กับผลของเราเอง
        "others": sorted({h for h in cited_hosts if h and h != apex}),
    }


# ---------- parser: แปลง response ดิบของแต่ละเจ้าเป็น (text, citations) ----------
def parse_claude(resp: dict) -> tuple[str, list]:
    """Anthropic Messages API + web_search tool

    ผล search มาเป็นบล็อก web_search_tool_result ซึ่ง .content เป็น *list* ตอนสำเร็จ
    แต่เป็น *object* ตอนพลาด (เช่น {"error_code": "max_uses_exceeded"}) และไม่ raise
    เพราะตอบ HTTP 200 — ต้องแยกก่อน index ไม่งั้นพังตอนโควตาหมดเท่านั้น ซึ่งหายาก
    """
    text, urls = [], []
    for block in resp.get("content") or []:
        t = block.get("type")
        if t == "text":
            text.append(block.get("text") or "")
            for c in block.get("citations") or []:
                if c.get("url"):
                    urls.append(c["url"])
        elif t == "web_search_tool_result":
            content = block.get("content")
            if isinstance(content, list):          # object = error block, ข้าม
                for r in content:
                    if r.get("url"):
                        urls.append(r["url"])
    return "\n".join(text), _dedupe(urls)


def parse_openai(resp: dict) -> tuple[str, list]:
    """OpenAI Responses API + web_search tool — ลิงก์อยู่ใน annotations ชนิด url_citation"""
    text, urls = [], []
    for item in resp.get("output") or []:
        for part in item.get("content") or []:
            if part.get("type") in ("output_text", "text"):
                text.append(part.get("text") or "")
            for a in part.get("annotations") or []:
                if a.get("type") == "url_citation" and a.get("url"):
                    urls.append(a["url"])
    return "\n".join(text), _dedupe(urls)


def parse_perplexity(resp: dict) -> tuple[str, list]:
    """Perplexity sonar — ค้นเว็บมาในตัว ลิงก์อยู่ที่ search_results หรือ citations"""
    text = ""
    for ch in resp.get("choices") or []:
        text = (ch.get("message") or {}).get("content") or text
    urls = [r["url"] for r in (resp.get("search_results") or []) if r.get("url")]
    urls += [u for u in (resp.get("citations") or []) if isinstance(u, str)]
    return text, _dedupe(urls)


def parse_gemini(resp: dict) -> tuple[str, list]:
    """Gemini + google_search grounding — ลิงก์อยู่ใน groundingChunks[].web.uri"""
    text, urls = [], []
    for cand in resp.get("candidates") or []:
        for part in (cand.get("content") or {}).get("parts") or []:
            if part.get("text"):
                text.append(part["text"])
        for chunk in (cand.get("groundingMetadata") or {}).get("groundingChunks") or []:
            uri = (chunk.get("web") or {}).get("uri")
            if uri:
                urls.append(uri)
    return "\n".join(text), _dedupe(urls)


PARSERS = {"claude": parse_claude, "chatgpt": parse_openai,
           "perplexity": parse_perplexity, "gemini": parse_gemini}


def _dedupe(urls: list) -> list:
    seen, out = set(), []
    for u in urls:
        u = (u or "").strip()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


# ---------- เรียก API จริง ----------
# โมเดลและ endpoint ตั้งทับได้ด้วย env — สเปกของแต่ละเจ้าเปลี่ยนบ่อยกว่าที่โค้ดนี้จะตามทัน
# และแก้ env ง่ายกว่ารอ deploy ใหม่เวลาผู้ให้บริการขยับชื่อโมเดล
MODELS = {
    "claude":     os.getenv("GEO_AI_MODEL_CLAUDE", "claude-opus-5-5"),
    "chatgpt":    os.getenv("GEO_AI_MODEL_OPENAI", "gpt-5"),
    "perplexity": os.getenv("GEO_AI_MODEL_PPLX", "sonar"),
    "gemini":     os.getenv("GEO_AI_MODEL_GEMINI", "gemini-2.5-flash"),
}


# ปลายทางตั้งทับได้ — ใช้ยิงผ่าน gateway/พร็อกซีขององค์กร และใช้ชี้ไปเซิร์ฟเวอร์จำลอง
# ตอนทดสอบเส้นทางทั้งเส้นโดยไม่ต้องจ่ายเงินจริง
BASES = {
    "claude":     os.getenv("GEO_AI_BASE_CLAUDE", "https://api.anthropic.com"),
    "chatgpt":    os.getenv("GEO_AI_BASE_OPENAI", "https://api.openai.com"),
    "perplexity": os.getenv("GEO_AI_BASE_PPLX", "https://api.perplexity.ai"),
    "gemini":     os.getenv("GEO_AI_BASE_GEMINI", "https://generativelanguage.googleapis.com"),
}


def _post(url: str, headers: dict, payload: dict) -> dict:
    import httpx
    with httpx.Client(timeout=TIMEOUT) as c:
        r = c.post(url, headers=headers, json=payload)
        r.raise_for_status()
        return r.json()


def call_claude(question: str) -> dict:
    return _post(
        f"{BASES['claude']}/v1/messages",
        {"x-api-key": _key("claude"), "anthropic-version": "2023-06-01",
         "content-type": "application/json"},
        {"model": MODELS["claude"], "max_tokens": 2048,
         # ต้องมี web search ไม่งั้นตอบจากความจำตอนเทรน ซึ่งไม่ใช่สิ่งที่เราวัด
         "tools": [{"type": "web_search_20260209", "name": "web_search"}],
         "messages": [{"role": "user", "content": question}]})


def call_openai(question: str) -> dict:
    return _post(
        f"{BASES['chatgpt']}/v1/responses",
        {"Authorization": f"Bearer {_key('chatgpt')}", "Content-Type": "application/json"},
        {"model": MODELS["chatgpt"], "tools": [{"type": "web_search"}], "input": question})


def call_perplexity(question: str) -> dict:
    return _post(
        f"{BASES['perplexity']}/chat/completions",
        {"Authorization": f"Bearer {_key('perplexity')}", "Content-Type": "application/json"},
        {"model": MODELS["perplexity"],
         "messages": [{"role": "user", "content": question}]})


def call_gemini(question: str) -> dict:
    return _post(
        f"{BASES['gemini']}/v1beta/models/"
        f"{MODELS['gemini']}:generateContent?key={_key('gemini')}",
        {"Content-Type": "application/json"},
        {"contents": [{"parts": [{"text": question}]}],
         "tools": [{"google_search": {}}]})


CALLERS = {"claude": call_claude, "chatgpt": call_openai,
           "perplexity": call_perplexity, "gemini": call_gemini}


def ask(engine: str, question: str) -> dict:
    """ถาม 1 เจ้า 1 คำถาม — คืน dict เดียวกันหมดไม่ว่าสำเร็จหรือพลาด

    ไม่โยน exception ออกไป เพราะรอบหนึ่งยิงหลายสิบครั้ง ถ้าเจ้าหนึ่งล่มแล้วทั้งรอบพัง
    เราจะเสียผลของเจ้าที่เหลือไปด้วยทั้งที่มันใช้ได้
    """
    if not _key(engine):
        return {"ok": False, "skip": True, "text": "", "citations": [],
                "reason": f"ยังไม่ได้ตั้ง {ENGINES[engine]['env']}"}
    try:
        raw = CALLERS[engine](question)
        text, cites = PARSERS[engine](raw)
        return {"ok": True, "skip": False, "text": text, "citations": cites, "reason": ""}
    except Exception as e:
        return {"ok": False, "skip": False, "text": "", "citations": [],
                "reason": f"{type(e).__name__}: {str(e)[:120]}"}


def check_brand(brand, questions, aliases=()) -> dict:
    """ถามทุกเจ้า × ทุกคำถาม แล้วสรุปผล — brand ต้องมี name กับ domain"""
    from . import geo_content
    apex = host_of(geo_content._site_url(brand))
    engines = list(ENGINES)
    qs = [dict(qq) for qq in questions][:MAX_Q]
    checked_at = datetime.datetime.now().isoformat(timespec="microseconds")
    rows = []
    for e in engines:
        for qq in qs:
            text = qq.get("question") or qq.get("text") or ""
            r = ask(e, text)
            if r["skip"]:
                v = {"status": SKIP, "cited": False, "named": False, "others": []}
            elif not r["ok"]:
                v = {"status": ERROR, "cited": False, "named": False, "others": []}
            else:
                v = judge_mention(r["text"], r["citations"], apex, brand["name"], aliases)
            rows.append({"engine": e, "question_id": qq.get("id"), "question": text,
                         "status": v["status"], "cited": v["cited"], "named": v["named"],
                         "others": v["others"], "reason": r["reason"],
                         "answer": (r["text"] or "")[:1500], "checked_at": checked_at})
    return summarize(rows, checked_at, apex)


def summarize(rows: list, checked_at: str, apex: str = "") -> dict:
    live = [r for r in rows if r["status"] in (CITED, NAMED, ABSENT)]
    by_engine = {}
    for e in ENGINES:
        er = [r for r in rows if r["engine"] == e]
        el = [r for r in er if r["status"] in (CITED, NAMED, ABSENT)]
        by_engine[e] = {
            "label": ENGINES[e]["label"],
            "asked": len(el),
            "cited": sum(1 for r in el if r["cited"]),
            "named": sum(1 for r in el if r["named"]),
            "skipped": sum(1 for r in er if r["status"] == SKIP),
            "errors": sum(1 for r in er if r["status"] == ERROR),
        }
    return {
        "checked_at": checked_at, "apex": apex, "rows": rows, "by_engine": by_engine,
        "asked": len(live),
        "cited": sum(1 for r in live if r["cited"]),
        "named": sum(1 for r in live if r["named"]),
        # เอ่ยถึงกี่ % ของคำถามที่ถามได้จริง — ไม่หารด้วยคำถามที่ข้ามเพราะไม่มีคีย์
        "rate": round(100 * sum(1 for r in live if r["named"] or r["cited"]) / len(live)) if live else None,
    }
