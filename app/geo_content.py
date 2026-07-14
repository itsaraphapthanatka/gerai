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


def _llm_generate(brand, question: str, lang: str, ctype: str = "qa"):
    langname = "ภาษาไทย" if lang == "th" else "English"
    # ใช้ฟอร์แมต marker (ไม่ใช่ JSON) — body มี newline/quote/อักขระอะไรก็ได้ ไม่ต้อง escape → parse ทนกว่ามาก
    tldr = "สรุป" if lang == "th" else "Summary"
    # รูปแบบคอนเทนต์ AEO: qa = ถาม-ตอบ, comparison = หน้าเปรียบเทียบ (ROI สูงสุด), listicle = ลิสต์อันดับ
    if ctype == "comparison":
        task = (
            "Write a COMPARISON PAGE (the highest-ROI AEO format): compare the realistic "
            "options/approaches/types implied by the question (e.g. option A vs B, rent vs buy, type X vs Y).\n"
            "MUST include one markdown comparison table (criteria as rows, options as columns).\n"
            "Do NOT name or invent facts about specific competitor companies — compare generic options "
            "fairly, and mention the business naturally as one good choice where honest.\n"
        )
        body_spec = ("(250-350 words markdown with ## headings; MUST contain one markdown comparison "
                     "table using | pipes |; end with a short 'ควรเลือกแบบไหน' guidance section)")
        items_spec = "(the options being compared, one per line as 'I: option name' — 2-4 lines)"
    elif ctype == "listicle":
        task = (
            "Write a LISTICLE ('X ข้อ / X ตัวเลือก / X สิ่งที่ควรรู้') — a numbered list article, "
            "the format AI engines cite most.\n"
            "5-7 items; each item = a **bold name** + 1-2 sentence explanation.\n"
            "Do NOT fabricate rankings of named competitors or fake stats — frame items as "
            "options/tips/criteria, and include the business naturally where honest.\n"
        )
        body_spec = ("(250-350 words markdown: short intro, then a numbered list 1. 2. 3. ... with a "
                     "**bold item name** + explanation each, at least 5 items)")
        items_spec = "(repeat the list item names, one per line as 'I: item name')"
    else:
        task = "Write content an AI engine would cite — mention the business name and its service area.\n"
        body_spec = ("(200-300 words markdown with ## headings; include at least one bullet list; "
                     "add a short comparison table when it helps)")
        items_spec = "(leave this section empty)"
    prompt = (
        f"You are a GEO/AEO content writer for a Thai business. Write everything in {langname}.\n"
        f"Business: {brand['name']} (website: {brand['domain']}). Market/notes: {brand['market'] or '-'}.\n"
        f'A user asks an AI search engine: "{question}".\n'
        + task +
        "Write ANSWER-FIRST (for AEO / featured snippets): give a direct, concise answer immediately, "
        "then the supporting detail.\n"
        "IMPORTANT — content may be auto-published WITHOUT human review, so it must be safe by default:\n"
        "Do NOT invent specific prices, sizes, square meters, numbers, years in business, statistics, awards, or client names. "
        "Keep claims general and always true; for any specifics, tell readers to contact the business to confirm.\n\n"
        "Return EXACTLY this plain-text format (NO JSON, NO code fences). Keep each === marker on its own line:\n"
        "===TITLE===\n(one-line title)\n"
        "===META_TITLE===\n(SEO title, max 60 chars)\n"
        "===META_DESC===\n(meta description, max 155 chars)\n"
        "===ANSWER===\n(direct answer to the question in 1-2 sentences, max 50 words — the featured-snippet answer)\n"
        f"===BODY===\n{body_spec}\n"
        f"===ITEMS===\n{items_spec}\n"
        "===HOWTO===\n(ONLY if the topic is a step-by-step process/how-to, list steps as lines 'S: step text'. "
        "If it is NOT a how-to, leave this section empty)\n"
        "===FAQ===\n"
        "Q: (People-Also-Ask style follow-up question)\nA: (concise answer)\n"
        "Q: (question)\nA: (answer)\n"
        "Q: (question)\nA: (answer)"
    )
    for _attempt in range(3):
        raw = _llm_chat(prompt, max_tokens=2048)
        if not raw:
            continue
        raw = _strip_fences(raw)
        secs = {}
        parts = re.split(r'===\s*(TITLE|META_TITLE|META_DESC|ANSWER|BODY|ITEMS|HOWTO|FAQ)\s*===', raw)
        for i in range(1, len(parts) - 1, 2):
            secs[parts[i].strip().upper()] = parts[i + 1].strip()
        title = (secs.get("TITLE") or "").strip().splitlines()[0].strip() if secs.get("TITLE") else ""
        body = (secs.get("BODY") or "").strip()
        if not title or not body:
            continue  # ฟอร์แมตไม่ครบ → ลองใหม่
        answer = " ".join((secs.get("ANSWER") or "").split())[:400]
        if answer:  # answer-first: วาง TL;DR บนสุดของ body
            body = f"> **{tldr}:** {answer}\n\n{body}"
        steps = []
        for m in re.finditer(r'^\s*(?:S:|[0-9]+[.)])\s*(.+)$', secs.get("HOWTO", ""), re.M):
            st = m.group(1).strip()
            if st:
                steps.append(st)
        items = []
        for m in re.finditer(r'^\s*(?:I:|[0-9]+[.)]|[-*])\s*(.+)$', secs.get("ITEMS", ""), re.M):
            it = m.group(1).strip().strip("*_")
            if it and "(" not in it[:1]:
                items.append(it)
        faqs = []
        for m in re.finditer(r'Q:\s*(.+?)\s*\nA:\s*(.+?)(?=\n\s*Q:|\Z)', secs.get("FAQ", ""), re.S):
            fq, fa = m.group(1).strip(), m.group(2).strip()
            if fq and fa:
                faqs.append({"q": fq, "a": fa})
        return {
            "title": title,
            "meta_title": ((secs.get("META_TITLE") or title).splitlines()[0]).strip()[:65],
            "meta_desc": ((secs.get("META_DESC") or "").splitlines()[0] if secs.get("META_DESC") else "").strip()[:160],
            "answer": answer,
            "body_md": body,
            "steps": steps,
            "items": items,
            "faqs": faqs or [{"q": question, "a": answer}],
            "source": "llm",
        }
    return None


def _template_generate(brand, question: str, lang: str, ctype: str = "qa"):
    name, domain, market = brand["name"], brand["domain"], (brand["market"] or "")
    if lang == "th":
        title = f"{question} | {name}"  # ctype comparison/listicle จะ override ด้านล่าง
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
    # โครงตามรูปแบบ (fallback ตอน AI ล่ม — ให้ลูกค้าเติมเอง)
    items = []
    if ctype == "comparison":
        if lang == "th":
            title = f"เปรียบเทียบตัวเลือก: {question} | {name}"
            body += (
                "\n## ตารางเปรียบเทียบ\n\n"
                "| เกณฑ์ | ตัวเลือก A | ตัวเลือก B |\n|---|---|---|\n"
                "| ความเหมาะสม | (เติม) | (เติม) |\n| ค่าใช้จ่าย | (เติม) | (เติม) |\n"
                "| ความยืดหยุ่น | (เติม) | (เติม) |\n\n"
                "## ควรเลือกแบบไหน\n(สรุปคำแนะนำการเลือก)\n"
            )
            items = ["ตัวเลือก A", "ตัวเลือก B"]
        else:
            title = f"Comparison: {question} | {name}"
            body += (
                "\n## Comparison table\n\n| Criteria | Option A | Option B |\n|---|---|---|\n"
                "| Fit | (fill) | (fill) |\n| Cost | (fill) | (fill) |\n\n## Which to choose\n(fill)\n"
            )
            items = ["Option A", "Option B"]
    elif ctype == "listicle":
        if lang == "th":
            title = f"5 สิ่งควรรู้: {question} | {name}"
            body += (
                "\n## ลิสต์\n1. **(หัวข้อที่ 1)** — (อธิบาย)\n2. **(หัวข้อที่ 2)** — (อธิบาย)\n"
                "3. **(หัวข้อที่ 3)** — (อธิบาย)\n4. **(หัวข้อที่ 4)** — (อธิบาย)\n5. **(หัวข้อที่ 5)** — (อธิบาย)\n"
            )
            items = [f"(หัวข้อที่ {i})" for i in range(1, 6)]
        else:
            title = f"5 things to know: {question} | {name}"
            body += "\n## List\n" + "\n".join(f"{i}. **(item {i})** — (explain)" for i in range(1, 6)) + "\n"
            items = [f"(item {i})" for i in range(1, 6)]
    answer = faqs[0]["a"]
    tldr = "สรุป" if lang == "th" else "Summary"
    body = f"> **{tldr}:** {answer}\n\n" + body   # answer-first
    return {
        "title": title,
        "meta_title": title[:65],
        "meta_desc": meta_desc[:160],
        "answer": answer,
        "body_md": body,
        "steps": [],
        "items": items,
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


def howto_schema(name: str, steps: list) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "HowTo",
        "name": name,
        "step": [
            {"@type": "HowToStep", "position": i, "name": s[:70], "text": s}
            for i, s in enumerate(steps, 1)
        ],
    }


def itemlist_schema(name: str, items: list) -> dict:
    """ItemList สำหรับ listicle/comparison — ให้ AI/Google เห็นเป็นลิสต์อันดับ"""
    return {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": name,
        "itemListElement": [
            {"@type": "ListItem", "position": i, "name": s[:120]}
            for i, s in enumerate(items, 1)
        ],
    }


def aeo_report_item(item) -> dict:
    """เช็คความพร้อม AEO ของคอนเทนต์ที่เก็บไว้ (answer-first, ลิสต์, FAQ, meta ฯลฯ)"""
    body = item["body_md"] or ""
    head = body.lstrip()[:140].lower()
    md = item["meta_desc"] or ""
    mt = item["meta_title"] or item["title"] or ""
    faq_n, has_howto = 0, False
    try:
        s = json.loads(item["schema_json"] or "[]")
        for o in (s if isinstance(s, list) else [s]):
            if isinstance(o, dict) and o.get("@type") == "FAQPage":
                faq_n = len(o.get("mainEntity") or [])
            if isinstance(o, dict) and o.get("@type") == "HowTo":
                has_howto = True
    except Exception:
        pass
    checks = [
        {"label": "ตอบก่อน (answer-first / TL;DR)", "ok": head.startswith(">") or "สรุป" in head or "summary" in head},
        {"label": "มีหัวข้อย่อย (##)", "ok": "## " in body},
        {"label": "มีลิสต์หรือตาราง", "ok": ("\n- " in body) or ("\n* " in body) or ("|" in body) or bool(re.search(r"\n\d+[.)]", body))},
        {"label": f"FAQ อย่างน้อย 2 ข้อ ({faq_n} ข้อ)", "ok": faq_n >= 2},
        {"label": "Meta description 50–160 ตัว", "ok": 50 <= len(md) <= 160},
        {"label": "Title ≤ 60 ตัว", "ok": 0 < len(mt) <= 60},
        {"label": "HowTo schema (เฉพาะบทความ how-to)", "ok": has_howto, "info": True},
    ]
    score = sum(1 for c in checks if c["ok"] and not c.get("info"))
    total = sum(1 for c in checks if not c.get("info"))
    return {"items": checks, "score": score, "max": total}


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


def generate_content(brand, question: str, lang: str = "th", ctype: str = "qa") -> dict:
    if ctype not in ("qa", "comparison", "listicle"):
        ctype = "qa"
    data = _llm_generate(brand, question, lang, ctype) or _template_generate(brand, question, lang, ctype)
    schemas = [faq_schema(data["faqs"])]
    if data.get("steps"):   # เพิ่ม HowTo เฉพาะบทความที่เป็นขั้นตอน (เลี่ยง schema ไม่ตรงเนื้อหา)
        schemas.append(howto_schema(data.get("title") or question, data["steps"]))
    if ctype in ("comparison", "listicle") and data.get("items"):
        schemas.append(itemlist_schema(data.get("title") or question, data["items"]))
    data["schema_json"] = json.dumps(schemas, ensure_ascii=False, indent=2)
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
