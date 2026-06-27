"""GEO content & schema generator — execution layer (Phase A engine).

สร้างร่างคอนเทนต์ (ไทย/อังกฤษ) + JSON-LD + meta จากคำถามเป้าหมาย
ใช้ LLM ผ่าน LiteLLM ถ้าตั้งค่าไว้ (LITELLM_BASE_URL) ไม่งั้น fallback เป็น template
— JSON-LD เป็น deterministic จึง valid เสมอ ไม่ว่าจะมี LLM หรือไม่
"""
from __future__ import annotations
import os
import re
import json


def _llm_chat(prompt: str, max_tokens: int = 1300):
    # ใช้ AI client ตัวเดียวกับ generate คำถาม (AI_BASE_URL = consoletoken) — มี User-Agent ที่ผ่าน 403
    try:
        from . import ai_client
        if ai_client.API_KEY:
            return ai_client._chat([{"role": "user", "content": prompt}], max_tokens=max_tokens)
    except Exception:
        pass
    # (legacy) LiteLLM ถ้าตั้ง LITELLM_BASE_URL ไว้
    base = os.getenv("LITELLM_BASE_URL", "").strip()
    if not base:
        return None
    key = os.getenv("LITELLM_API_KEY", "").strip()
    try:
        import httpx

        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        r = httpx.post(
            base.rstrip("/") + "/chat/completions",
            headers=headers,
            json={
                "model": os.getenv("LITELLM_MODEL", "claude-haiku-4-5"),
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.4,
            },
            timeout=20,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception:
        return None


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s)
    return s.strip()


def _loads_lenient(s: str):
    """parse JSON แบบทนต่อข้อผิดพลาดที่ LLM ทำบ่อย (backslash escape ผิด)"""
    try:
        return json.loads(s)
    except Exception:
        pass
    try:
        # ซ่อม backslash ที่ไม่ใช่ escape ที่ถูกต้อง → ดับเบิลให้เป็น \\
        fixed = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', s)
        return json.loads(fixed)
    except Exception:
        return None


def _llm_generate(brand, question: str, lang: str):
    langname = "ภาษาไทย" if lang == "th" else "English"
    # ใช้ฟอร์แมต marker (ไม่ใช่ JSON) — body มี newline/quote/อักขระอะไรก็ได้ ไม่ต้อง escape → parse ทนกว่ามาก
    prompt = (
        f"You are a GEO/AEO content writer for a Thai business. Write everything in {langname}.\n"
        f"Business: {brand['name']} (website: {brand['domain']}). Market/notes: {brand['market'] or '-'}.\n"
        f'A user asks an AI search engine: "{question}".\n'
        "Write content an AI engine would cite — mention the business name and its service area.\n"
        "IMPORTANT — content may be auto-published WITHOUT human review, so it must be safe by default:\n"
        "Do NOT invent specific prices, sizes, square meters, numbers, years in business, statistics, awards, or client names. "
        "Keep claims general and always true; for any specifics, tell readers to contact the business to confirm.\n\n"
        "Return EXACTLY this plain-text format (NO JSON, NO code fences). Keep each === marker on its own line:\n"
        "===TITLE===\n(one-line title)\n"
        "===META_TITLE===\n(SEO title, max 60 chars)\n"
        "===META_DESC===\n(meta description, max 155 chars)\n"
        "===BODY===\n(200-300 words, markdown with ## headings)\n"
        "===FAQ===\n"
        "Q: (question)\nA: (answer)\n"
        "Q: (question)\nA: (answer)\n"
        "Q: (question)\nA: (answer)"
    )
    for _attempt in range(3):
        raw = _llm_chat(prompt, max_tokens=2048)
        if not raw:
            continue
        raw = _strip_fences(raw)
        secs = {}
        parts = re.split(r'===\s*(TITLE|META_TITLE|META_DESC|BODY|FAQ)\s*===', raw)
        for i in range(1, len(parts) - 1, 2):
            secs[parts[i].strip().upper()] = parts[i + 1].strip()
        title = (secs.get("TITLE") or "").strip().splitlines()[0].strip() if secs.get("TITLE") else ""
        body = (secs.get("BODY") or "").strip()
        if not title or not body:
            continue  # ฟอร์แมตไม่ครบ → ลองใหม่
        faqs = []
        for m in re.finditer(r'Q:\s*(.+?)\s*\nA:\s*(.+?)(?=\n\s*Q:|\Z)', secs.get("FAQ", ""), re.S):
            fq, fa = m.group(1).strip(), m.group(2).strip()
            if fq and fa:
                faqs.append({"q": fq, "a": fa})
        return {
            "title": title,
            "meta_title": ((secs.get("META_TITLE") or title).splitlines()[0]).strip()[:65],
            "meta_desc": ((secs.get("META_DESC") or "").splitlines()[0] if secs.get("META_DESC") else "").strip()[:160],
            "body_md": body,
            "faqs": faqs or [{"q": question, "a": ""}],
            "source": "llm",
        }
    return None


def _template_generate(brand, question: str, lang: str):
    name, domain, market = brand["name"], brand["domain"], (brand["market"] or "")
    if lang == "th":
        title = f"{question} | {name}"
        body = (
            f"# {question}\n\n"
            f"{name} ให้บริการเกี่ยวกับ {question}{(' ในพื้นที่ ' + market) if market else ''}\n\n"
            f"## บริการของเรา\n- (เติมรายละเอียดบริการ)\n- (จุดเด่น / ทำเลที่ครอบคลุม)\n\n"
            f"## ทำไมต้องเลือก {name}\n- (ประสบการณ์ / ความน่าเชื่อถือ)\n\n"
            f"## ติดต่อ\nเว็บไซต์: {domain}\n"
        )
        faqs = [
            {"q": question, "a": f"{name} ให้บริการ {question}{(' ในพื้นที่ ' + market) if market else ''} — ติดต่อที่ {domain}"},
            {"q": f"{name} ครอบคลุมพื้นที่ไหนบ้าง", "a": f"(ระบุพื้นที่บริการของ {name})"},
        ]
        meta_desc = f"{name} — {question}{(' พื้นที่ ' + market) if market else ''}".strip()
    else:
        title = f"{question} | {name}"
        body = (
            f"# {question}\n\n"
            f"{name} provides services for {question}{(' in ' + market) if market else ''}.\n\n"
            f"## Our services\n- (fill in service details)\n- (coverage / strengths)\n\n"
            f"## Why {name}\n- (experience / trust signals)\n\n"
            f"## Contact\nWebsite: {domain}\n"
        )
        faqs = [
            {"q": question, "a": f"{name} offers {question}{(' in ' + market) if market else ''} — visit {domain}."},
            {"q": f"What areas does {name} cover?", "a": f"(state {name}'s service areas)"},
        ]
        meta_desc = f"{name} — {question}{(' in ' + market) if market else ''}".strip()
    return {
        "title": title,
        "meta_title": title[:65],
        "meta_desc": meta_desc[:160],
        "body_md": body,
        "faqs": faqs,
        "source": "template",
    }


def faq_schema(faqs) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": f["q"], "acceptedAnswer": {"@type": "Answer", "text": f["a"]}}
            for f in faqs
            if f.get("q")
        ],
    }


def _site_url(brand) -> str:
    url = brand["domain"] or ""
    return url if url.startswith("http") else "https://" + url


def org_schema(brand) -> str:
    return json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "RealEstateAgent",
            "name": brand["name"],
            "url": _site_url(brand),
            "areaServed": brand["market"] or "",
        },
        ensure_ascii=False,
        indent=2,
    )


def generate_content(brand, question: str, lang: str = "th") -> dict:
    data = _llm_generate(brand, question, lang) or _template_generate(brand, question, lang)
    data["schema_json"] = json.dumps(faq_schema(data["faqs"]), ensure_ascii=False, indent=2)
    return data


AI_BOTS = [
    "GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-Web",
    "PerplexityBot", "Google-Extended", "CCBot", "Applebot-Extended", "Bingbot",
]


def robots_snippet() -> str:
    lines = []
    for bot in AI_BOTS:
        lines += [f"User-agent: {bot}", "Allow: /", ""]
    lines.append("Sitemap: (ใส่ URL sitemap ของเว็บ)")
    return "\n".join(lines)


def llms_txt(brand, items) -> str:
    site = _site_url(brand)
    out = [f"# {brand['name']}", ""]
    if brand["market"]:
        out += [f"> {brand['market']}", ""]
    out += [f"{brand['name']} — {site}", ""]
    pub = [i for i in items if i["status"] == "published"]
    if pub:
        out.append("## เนื้อหา / Pages")
        for i in pub:
            url = i["wp_link"] or f"{site}/geo/{i['id']}"  # WP → ลิงก์โพสต์, dev → หน้า hosted
            desc = (i["meta_desc"] or "").replace("\n", " ")
            out.append(f"- {i['title']}: {url}" + (f" — {desc}" if desc else ""))
    return "\n".join(out)
