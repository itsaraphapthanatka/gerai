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
MIN_KEY_LEN = 16                                      # สั้นกว่านี้ไม่ใช่คีย์จริงของเจ้าไหนเลย

# แต่ละเจ้าใช้คีย์คนละตัว ไม่มีคีย์ = ข้าม ไม่ใช่ตก (แบบเดียวกับ Serper ใน site_health)
# db_key = ชื่อใน settings ที่แอดมินวางคีย์ผ่านหน้าเว็บ · env = ทางเลือกใน .env
ENGINES = {
    "chatgpt":    {"label": "ChatGPT",    "db_key": "ai_key_openai",    "env": "OPENAI_API_KEY",
                   "hint": "platform.openai.com"},
    "claude":     {"label": "Claude",     "db_key": "ai_key_anthropic", "env": "ANTHROPIC_API_KEY",
                   "hint": "console.anthropic.com"},
    "perplexity": {"label": "Perplexity", "db_key": "ai_key_pplx",      "env": "PERPLEXITY_API_KEY",
                   "hint": "perplexity.ai/settings/api"},
    "gemini":     {"label": "Gemini",     "db_key": "ai_key_gemini",    "env": "GEMINI_API_KEY",
                   "hint": "aistudio.google.com"},
}


def _key(engine: str):
    """DB settings ก่อน (แอดมินวางผ่านหน้าเว็บ) แล้วค่อย .env — ลำดับเดียวกับ geo_worker._cfg
    เพื่อให้เปิดใช้ได้โดยไม่ต้องแตะเซิร์ฟเวอร์"""
    spec = ENGINES[engine]
    v = None
    try:
        from . import db
        v = (db.get_setting(spec["db_key"]) or "").strip() or None
    except Exception:
        pass
    v = v or (os.getenv(spec["env"]) or "").strip() or None
    # คีย์จริงของทุกเจ้ายาวหลายสิบตัวอักษร ค่าสั้น ๆ คือ placeholder ที่ถูกวางค้างไว้
    # (เกิดจริง: "..." จากคำสั่งตัวอย่างหลุดเข้า .env แล้วระบบยิง API จริง 48 ครั้งได้ 401 หมด)
    # ถือว่าไม่มีคีย์ดีกว่ายิงทิ้ง — และบอกเหตุผลให้ชัดแทนที่จะเงียบ
    if v and len(v) < MIN_KEY_LEN:
        return None
    return v


def key_problem(engine: str) -> str:
    """เหตุผลที่เจ้านี้ใช้ไม่ได้ — แยก "ไม่ได้ตั้ง" กับ "ตั้งแต่ใช้ไม่ได้" ให้คนอ่านรู้ว่าต้องทำอะไร"""
    spec = ENGINES[engine]
    raw = ""
    try:
        from . import db
        raw = (db.get_setting(spec["db_key"]) or "").strip()
    except Exception:
        pass
    raw = raw or (os.getenv(spec["env"]) or "").strip()
    if raw and len(raw) < MIN_KEY_LEN:
        return f"คีย์ {spec['env']} สั้นผิดปกติ ({len(raw)} ตัวอักษร) — น่าจะเป็น placeholder ค้างอยู่"
    return f"ยังไม่ได้ตั้ง {spec['env']}"


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


# ---------- usage: ดึง token/จำนวนค้นจาก response ของแต่ละเจ้า ----------
# ค่าใช้จ่ายจริงต้องมาจาก usage ที่ API ส่งกลับ ไม่ใช่ประมาณจากความยาวข้อความ —
# ตอนประเมินด้วยมือสมมติ 5,000 token/คำถาม ซึ่งอาจคลาดได้เป็นเท่าตัว
def _i(x) -> int:
    try:
        return int(x or 0)
    except (TypeError, ValueError):
        return 0


def usage_claude(resp: dict) -> dict:
    u = resp.get("usage") or {}
    # cache write/read มีราคาต่างจาก input ปกติ แต่เราไม่ได้ใช้ cache ในการเรียกนี้ จึงรวมเข้า in
    # ให้ภาพรวมถูก (ถ้าวันหน้าเปิด cache ค่อยแยก)
    return {"in": _i(u.get("input_tokens")) + _i(u.get("cache_creation_input_tokens"))
                  + _i(u.get("cache_read_input_tokens")),
            "out": _i(u.get("output_tokens")),
            "searches": _i((u.get("server_tool_use") or {}).get("web_search_requests"))}


def usage_openai(resp: dict) -> dict:
    u = resp.get("usage") or {}
    # Responses API ไม่สรุปจำนวนค้นใน usage — นับจาก output item ชนิด web_search_call
    n = sum(1 for it in (resp.get("output") or []) if it.get("type") == "web_search_call")
    return {"in": _i(u.get("input_tokens")), "out": _i(u.get("output_tokens")), "searches": n}


def usage_perplexity(resp: dict) -> dict:
    u = resp.get("usage") or {}
    # sonar คิด "request fee" ต่อคำขอ ไม่ใช่ต่อครั้งที่ค้น — 1 คำขอ = 1 หน่วย
    return {"in": _i(u.get("prompt_tokens")), "out": _i(u.get("completion_tokens")), "searches": 1}


def usage_gemini(resp: dict) -> dict:
    u = resp.get("usageMetadata") or {}
    grounded = any(((c.get("groundingMetadata") or {}).get("groundingChunks"))
                   for c in (resp.get("candidates") or []))
    return {"in": _i(u.get("promptTokenCount")), "out": _i(u.get("candidatesTokenCount")),
            "searches": 1 if grounded else 0}


USAGE = {"claude": usage_claude, "chatgpt": usage_openai,
         "perplexity": usage_perplexity, "gemini": usage_gemini}

# ราคาทางการ ณ 4 ต.ค. 2026 (USD) — in/out ต่อ 1M token · search ต่อ 1,000 ครั้ง
# ตั้งทับได้ด้วย GEO_AI_PRICES เป็น JSON รูปเดียวกัน เพราะราคาเปลี่ยนบ่อยกว่าที่จะ deploy ตาม
# ที่มา: docs.perplexity.ai/getting-started/pricing · developers.openai.com/api/docs/pricing
#        ai.google.dev/gemini-api/docs/pricing · platform.claude.com/docs/en/about-claude/pricing
_DEFAULT_PRICES = {
    "claude": {
        "claude-opus-5-5":   {"in": 4.00, "out": 20.00, "search": 10.0},
        "claude-sonnet-5-5": {"in": 2.00, "out": 10.00, "search": 10.0},
        "claude-haiku-4-5":  {"in": 1.00, "out": 5.00,  "search": 10.0},
    },
    "chatgpt": {   # web search $10/1k สำหรับ reasoning model + token ของผลค้นคิดตามโมเดล
        "gpt-5":      {"in": 1.25, "out": 10.00, "search": 10.0},
        "gpt-5-mini": {"in": 0.25, "out": 2.00,  "search": 10.0},
        "gpt-5-nano": {"in": 0.05, "out": 0.40,  "search": 10.0},
    },
    "perplexity": {   # "search" = request fee ระดับ medium context ($5/$8/$12 ตาม low/med/high)
        "sonar":     {"in": 1.00, "out": 1.00,  "search": 8.0},
        "sonar-pro": {"in": 3.00, "out": 15.00, "search": 10.0},
    },
    "gemini": {   # grounding ฟรี 1,500 ครั้ง/วันสำหรับ 2.5 (เราใช้ไม่ถึง 200/สัปดาห์) จึงคิด 0
        "gemini-2.5-flash": {"in": 0.30, "out": 2.50,  "search": 0.0},
        "gemini-2.5-pro":   {"in": 1.25, "out": 10.00, "search": 0.0},
    },
}


def prices() -> dict:
    raw = os.getenv("GEO_AI_PRICES", "").strip()
    if not raw:
        return _DEFAULT_PRICES
    try:
        over = json.loads(raw)
        out = {e: dict(m) for e, m in _DEFAULT_PRICES.items()}
        for e, models in over.items():
            out.setdefault(e, {}).update(models or {})
        return out
    except Exception:
        return _DEFAULT_PRICES


def estimate_cost(engine: str, model: str, usage: dict) -> float:
    """USD ของการเรียก 1 ครั้ง — โมเดลที่ไม่รู้ราคาใช้ราคาของโมเดลตั้งต้นของเจ้านั้น
    (ดีกว่าให้เป็น 0 แล้วหลอกว่าฟรี)"""
    table = prices().get(engine) or {}
    p = table.get(model) or table.get(MODELS.get(engine, "")) or next(iter(table.values()), None)
    if not p or not usage:
        return 0.0
    return round(usage.get("in", 0) / 1e6 * p["in"]
                 + usage.get("out", 0) / 1e6 * p["out"]
                 + usage.get("searches", 0) / 1000 * p["search"], 6)


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
                "reason": key_problem(engine)}
    try:
        raw = CALLERS[engine](question)
        text, cites = PARSERS[engine](raw)
        usage = USAGE[engine](raw)
        return {"ok": True, "skip": False, "text": text, "citations": cites, "reason": "",
                "usage": usage, "cost_usd": estimate_cost(engine, MODELS[engine], usage)}
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
                         "answer": (r["text"] or "")[:1500], "checked_at": checked_at,
                         # skip/error ไม่มี usage — บันทึก 0 (error ที่เรียกสำเร็จบางส่วนอาจถูกคิดเงิน
                         # แต่เราไม่มีตัวเลข จึงไม่เดา)
                         "usage": r.get("usage") or {"in": 0, "out": 0, "searches": 0},
                         "cost_usd": r.get("cost_usd") or 0.0})
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
            "cost_usd": round(sum(r.get("cost_usd") or 0 for r in er), 4),
            "tokens_in": sum((r.get("usage") or {}).get("in", 0) for r in er),
            "tokens_out": sum((r.get("usage") or {}).get("out", 0) for r in er),
            "searches": sum((r.get("usage") or {}).get("searches", 0) for r in er),
        }
    return {
        "checked_at": checked_at, "apex": apex, "rows": rows, "by_engine": by_engine,
        "asked": len(live),
        "cited": sum(1 for r in live if r["cited"]),
        "named": sum(1 for r in live if r["named"]),
        # เอ่ยถึงกี่ % ของคำถามที่ถามได้จริง — ไม่หารด้วยคำถามที่ข้ามเพราะไม่มีคีย์
        "rate": round(100 * sum(1 for r in live if r["named"] or r["cited"]) / len(live)) if live else None,
        # ค่าใช้จ่ายจริงของรอบนี้จาก usage ที่ API ส่งกลับ — ไม่ใช่ประมาณการ
        "cost_usd": round(sum(r.get("cost_usd") or 0 for r in rows), 4),
        "tokens_in": sum((r.get("usage") or {}).get("in", 0) for r in rows),
        "tokens_out": sum((r.get("usage") or {}).get("out", 0) for r in rows),
        "searches": sum((r.get("usage") or {}).get("searches", 0) for r in rows),
    }
