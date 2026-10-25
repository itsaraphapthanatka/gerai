"""OpenAI-compatible AI client สำหรับ generate คำถาม GEO"""
import os, json, re
import urllib.request

BASE_URL = os.getenv("AI_BASE_URL", "https://consoletoken.aunjai.org/api/v1")
API_KEY  = os.getenv("AI_API_KEY", "")
MODEL    = os.getenv("AI_MODEL", "gemma-4-12b")


def _chat(messages: list[dict], max_tokens: int = 2048, timeout: int = 120) -> str:
    payload = json.dumps({"model": MODEL, "messages": messages, "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "curl/8.5.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"].strip()


# ---- เลือกโมเดลต่องาน (ตั้งผ่านหน้าตั้งค่าระบบ → settings llm_<task>) ----
# ทุกงานใช้โมเดลในเครื่องผ่าน proxy (_chat) เป็นค่าเริ่มต้น · งาน "คิดคำถาม" สลับไป Gemini ได้
# ด้วยคีย์ Gemini ตัวเดียวกับที่ใช้วัดผู้ช่วย AI — Gemini ตอบไม่ได้/ไม่มีคีย์ → ถอยมาใช้โมเดลในเครื่องเอง งานไม่ล้ม
TASKS = {"questions": "ตัวคิดคำถามเป้าหมาย", "sales": "ตัวร่างข้อความทักลูกค้า (CEO เอเจนต์)",
         "marketing": "ตัวเขียนโพสต์/เคสศึกษา (CMO เอเจนต์)", "content": "ตัวเขียนคอนเทนต์ให้ลูกค้า (Delivery)",
         "followup": "ตัวร่างข้อความติดตามผล (SDR)", "partner": "ตัวร่างคำชวนพันธมิตร (ฝ่ายพันธมิตร)",
         "salesbot": "ตัวตอบแชตขาย (LINE · เว็บ · อีเมลตอบกลับ)"}
MODEL_CHOICES = [("local", "โมเดลในเครื่อง (ค่าเริ่มต้น)"), ("gemini", "Gemini (คีย์เดียวกับผู้ช่วย AI)")]
GEMINI_TIMEOUT = 120
_LAST_FALLBACK: dict = {}   # task → เหตุผลครั้งล่าสุดที่ Gemini ใช้ไม่ได้ (หน้าตั้งค่าโชว์ให้เห็น ไม่เงียบ)


def _read_setting(key: str):
    """อ่าน settings จาก DB — แยกเป็นฟังก์ชันให้เทสต์ฉีดค่าได้ และเลี่ยง import วนตอนโหลดโมดูล"""
    from . import db
    return db.get_setting(key)


def task_setting(task: str) -> str:
    """ค่าที่แอดมินเลือกไว้ (local|gemini) — อ่านทุกครั้ง เปลี่ยนในหน้าตั้งค่าแล้วมีผลทันที"""
    try:
        v = (_read_setting(f"llm_{task}") or "").strip().lower()
    except Exception:
        v = ""
    return v if v in dict(MODEL_CHOICES) else "local"


def _gemini_conf():
    """(key, model, base) จากโมดูลผู้ช่วย AI — import ตอนเรียก เพราะ ai_visibility ไม่ได้พึ่งโมดูลนี้แต่กลับกัน"""
    from . import ai_visibility as av
    return av._key("gemini"), av.MODELS["gemini"], av.BASES["gemini"]


def gemini_body(messages: list[dict], max_tokens: int, json_mode: bool) -> dict:
    """แปลง messages แบบ chat → รูปแบบ generateContent (system → systemInstruction, ที่เหลือรวมเป็น user เดียว)"""
    system = [str(m["content"]) for m in messages if m.get("role") == "system"]
    users = [str(m["content"]) for m in messages if m.get("role") != "system"]
    gen = {"maxOutputTokens": max_tokens}
    if json_mode:
        gen["responseMimeType"] = "application/json"   # บังคับให้ตอบเป็น JSON ล้วน ไม่มีข้อความห่อ
    body = {"contents": [{"role": "user", "parts": [{"text": "\n\n".join(users)}]}], "generationConfig": gen}
    if system:
        body["systemInstruction"] = {"parts": [{"text": "\n".join(system)}]}
    return body


def gemini_text(resp: dict) -> str:
    cands = resp.get("candidates") or [{}]
    parts = ((cands[0].get("content") or {}).get("parts")) or []
    return "".join(p.get("text", "") for p in parts)


def gemini_chat(messages: list[dict], max_tokens: int = 2048, timeout: int = GEMINI_TIMEOUT, json_mode: bool = False) -> str:
    key, model, base = _gemini_conf()
    if not key:
        raise RuntimeError("ไม่มีคีย์ Gemini")
    req = urllib.request.Request(
        f"{base}/v1beta/models/{model}:generateContent?key={key}",
        data=json.dumps(gemini_body(messages, max_tokens, json_mode)).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    text = gemini_text(data)
    if not text.strip():
        reason = ((data.get("candidates") or [{}])[0]).get("finishReason") or "ไม่มีคำตอบ"
        raise RuntimeError(f"Gemini ตอบว่าง ({reason})")
    return text


def chat_for(task: str, messages: list[dict], max_tokens: int = 2048, timeout: int = 120, json_mode: bool = False) -> str:
    """เรียกโมเดลตามที่ตั้งไว้สำหรับงานนี้ — Gemini ใช้ไม่ได้ก็ถอยมาโมเดลในเครื่อง และจำเหตุผลไว้โชว์"""
    from . import skills as _skills
    messages = _skills.with_skill(task, messages)        # ทักษะของเอเจนต์เป็น system prompt (ceo/cmo/delivery)
    if task_setting(task) == "gemini":
        try:
            out = gemini_chat(messages, max_tokens=max_tokens, timeout=timeout, json_mode=json_mode)
            _LAST_FALLBACK.pop(task, None)
            return out
        except Exception as e:
            _LAST_FALLBACK[task] = f"{type(e).__name__}: {str(e)[:120]}"
    return _chat(messages, max_tokens=max_tokens, timeout=timeout)


def task_status(task: str) -> dict:
    """สำหรับหน้าตั้งค่า: เลือกอะไรไว้ · Gemini พร้อมไหม · ชื่อโมเดล · เหตุผลที่ถอยกลับครั้งล่าสุด"""
    try:
        key, model, _ = _gemini_conf()
    except Exception:
        key, model = None, None
    return {"choice": task_setting(task), "gemini_ready": bool(key), "gemini_model": model,
            "local_model": MODEL, "fallback": _LAST_FALLBACK.get(task)}


def _fetch_text(url: str, timeout: int = 18) -> str:
    """ดึงหน้าแรกของเว็บไซต์ → คืน title + meta description + headings + เนื้อหาย่อ"""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        html = r.read(500_000).decode("utf-8", "ignore")

    def _grp(pat):
        m = re.search(pat, html, re.I | re.S)
        return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""

    title = _grp(r"<title[^>]*>(.*?)</title>")
    desc = (re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)', html, re.I)
            or re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)', html, re.I))
    desc = desc.group(1).strip() if desc else ""
    heads = [re.sub(r"<[^>]+>", "", h).strip()
             for h in re.findall(r"<h[1-3][^>]*>(.*?)</h[1-3]>", html, re.I | re.S)]
    heads = [h for h in heads if h][:10]
    body = re.sub(r"<(script|style|nav|footer)[\s\S]*?</\1>", " ", html, flags=re.I)
    body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()
    parts = [f"Title: {title}", f"Description: {desc}"]
    if heads:
        parts.append("Headings: " + " | ".join(heads))
    parts.append("Content: " + body[:1500])
    return "\n".join(p for p in parts if p.split(": ", 1)[-1])


PLACEHOLDER_SITE = re.compile(r"server block is working|welcome to nginx|apache2? (ubuntu |debian )?default page|it works!|"
                              r"site (is )?under construction|coming soon|domain (is )?parked|default web site page|"
                              r"this domain (is|may be) for sale", re.I)
THIN_SITE_CHARS = 400            # บริบทสั้นกว่านี้ = แทบไม่มีข้อเท็จจริงให้เขียน (เช่น SPA ที่ HTML ว่าง) → ลองดึงใหม่
CONTEXT_PAGE_HINT = re.compile(r"about|เกี่ยวกับ|faq|คำถาม|service|บริการ|why|เหตุผล", re.I)


def is_placeholder(text: str) -> bool:
    """หน้าเริ่มต้นของเซิร์ฟเวอร์/ยังไม่เปิดเว็บ — ห้ามใช้เป็นข้อมูลจริงของแบรนด์"""
    return bool(PLACEHOLDER_SITE.search(text or ""))


def context_stale(text: str) -> bool:
    return not (text or "").strip() or is_placeholder(text) or len(text) < THIN_SITE_CHARS


def site_context(url: str, fetch=None, max_pages: int = 3, limit: int = 4000) -> str:
    """บริบทเว็บจริงสำหรับ grounding: หน้าแรก + หน้าเกี่ยวกับเรา/คำถามพบบ่อย/บริการ (โดเมนเดียวกัน ≤ max_pages หน้า)
    หน้าแรกของเว็บขายของมักมีแต่เมนูกับช่องค้น — ข้อเท็จจริง (บริการ ใบรับรอง พื้นที่) อยู่ในหน้าเหล่านี้ · คืน '' ถ้าเป็นหน้า placeholder"""
    from urllib.parse import urljoin, urlparse
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    fetch = fetch or _fetch_page
    home_url, html = fetch(url)
    home = _page_text(html, body_chars=1000)                # แบ่งงบให้ทุกหน้าได้พื้นที่ — หน้าแรกมักเป็นรายการสินค้า/เมนูยาว
    if not home or is_placeholder(home):
        return ""
    host = urlparse(home_url).netloc.lower().removeprefix("www.")
    seen, picks = {home_url.rstrip("/")}, []
    for m in re.finditer(r"""<a[^>]+href=["']([^"'#]+)["'][^>]*>(.*?)</a>""", html, re.I | re.S):
        href, label = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
        u = urljoin(home_url, href).split("?")[0].rstrip("/")
        if urlparse(u).netloc.lower().removeprefix("www.") != host or u in seen:
            continue
        if CONTEXT_PAGE_HINT.search(urlparse(u).path) or CONTEXT_PAGE_HINT.search(label):
            seen.add(u)
            picks.append(u)
        if len(picks) >= max_pages - 1:
            break
    parts = [home]
    for u in picks:
        try:
            t = _page_text(fetch(u)[1], body_chars=1000)
        except Exception:
            continue
        if t and not is_placeholder(t):
            parts.append(f"[{urlparse(u).path}]\n{t}")
    return "\n\n".join(parts)[:limit]


def _fetch_page(url: str, timeout: int = 18) -> tuple[str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.geturl(), r.read(500_000).decode("utf-8", "ignore")


def _page_text(html: str, body_chars: int = 1500) -> str:
    def _grp(pat):
        m = re.search(pat, html, re.I | re.S)
        return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""
    title = _grp(r"<title[^>]*>(.*?)</title>")
    desc = (re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)', html, re.I)
            or re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)', html, re.I))
    desc = desc.group(1).strip() if desc else ""
    heads = [re.sub(r"<[^>]+>", "", h).strip() for h in re.findall(r"<h[1-3][^>]*>(.*?)</h[1-3]>", html, re.I | re.S)]
    heads = [h for h in heads if h][:10]
    body = re.sub(r"<(script|style|nav|footer|header)[\s\S]*?</\1>", " ", html, flags=re.I)
    body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()
    parts = [f"Title: {title}", f"Description: {desc}"]
    if heads:
        parts.append("Headings: " + " | ".join(heads))
    parts.append("Content: " + body[:body_chars])
    return "\n".join(p for p in parts if p.split(": ", 1)[-1])


def classify_market(url: str, name: str = "") -> str:
    """อ่านเว็บไซต์ → ระบุ 'ตลาด / ธุรกิจ' สั้นๆ เป็นภาษาไทย (คืน '' ถ้าทำไม่ได้)"""
    try:
        info = _fetch_text(url)
    except Exception:
        info = ""
    prompt = f"""วิเคราะห์เว็บไซต์นี้แล้วระบุ "ตลาด / ธุรกิจ" แบบสั้นกระชับ เพื่อใช้จัดหมวดหมู่แบรนด์

ชื่อแบรนด์: {name or '-'}
เว็บไซต์: {url}
ข้อมูลที่ดึงจากเว็บไซต์:
{info or '(อ่านเนื้อหาไม่ได้ — ให้เดาจากชื่อ/โดเมน)'}

ตอบเป็นภาษาไทย บรรทัดเดียว รูปแบบ "ประเภทธุรกิจ + ตลาด/พื้นที่" เช่น:
อสังหาริมทรัพย์ กรุงเทพ
ร้านอาหารไทย เชียงใหม่
สำนักงานกฎหมาย ที่ปรึกษาธุรกิจ
คลินิกสัตวแพทย์ และร้านสินค้าสัตว์เลี้ยง

กฎ: ตอบเฉพาะข้อความหมวดหมู่ ไม่เกิน 12 คำ ไม่ต้องอธิบาย ไม่ต้องมีเครื่องหมายคำพูด"""
    raw = _chat([{"role": "user", "content": prompt}], max_tokens=1500)
    # เอาบรรทัดสุดท้ายที่มีเนื้อหา (โมเดล reasoning มักวางคำตอบไว้ท้าย) แล้วล้างสัญลักษณ์
    lines = [ln.strip(" \t\"'`*-•·:") for ln in raw.splitlines() if ln.strip(" \t\"'`*-•·:")]
    ans = lines[-1] if lines else raw.strip()
    ans = re.sub(r"^(ตลาด|ธุรกิจ|หมวดหมู่|คำตอบ|ตลาด\s*/\s*ธุรกิจ)\s*[:：]\s*", "", ans).strip(" \"'`*")
    return ans[:90]


def generate_questions(brand_name: str, domain: str, market: str, n: int = 8, existing: list[str] | None = None) -> list[dict]:
    """สร้างคำถามเป้าหมาย GEO สำหรับแบรนด์ — คืน list of {question, lang}
    existing = คำถามที่มีอยู่แล้ว → สั่งห้ามซ้ำ/ใกล้เคียง ให้หามุมใหม่ (ใช้ตอนระบบคิดเพิ่มเองเมื่อปิดครบทุกข้อ)"""
    n_en = max(1, n // 4)
    avoid = ""
    if existing:
        shown = [f"- {x}" for x in existing[-60:]]  # พอให้โมเดลเห็นภาพ ไม่ยัดจนล้น context
        avoid = f"""
คำถามที่มีอยู่แล้ว {len(existing)} ข้อ — ห้ามซ้ำหรือใกล้เคียงกับข้อเหล่านี้ ให้หา "มุมใหม่" (เจตนา/บริบท/ทำเล/งบ/สถานการณ์ที่ยังไม่มี):
{chr(10).join(shown)}
"""
    prompt = f"""คุณคือคนธรรมดาที่กำลังพิมพ์ถาม ChatGPT/AI เพื่อหาสินค้า-บริการ ไม่ใช่นักการตลาด

ธุรกิจที่อยากให้โผล่ในคำตอบ AI:
- ชื่อ: {brand_name}
- โดเมน: {domain}
- ธุรกิจ/ตลาด: {market}

เขียนคำถาม {n} ข้อ ที่ "ลูกค้าตัวจริง" ของธุรกิจแบบนี้จะพิมพ์ถาม AI จริงๆ
- ภาษาไทยอย่างน้อย {n - n_en} ข้อ, อังกฤษ {n_en} ข้อ
{avoid}
สไตล์ต้องเหมือนคนพิมพ์จริง (สำคัญที่สุด):
- ภาษาพูด ไม่เป็นทางการ ลงท้าย มั้ย/ไหม/หน่อย/ดี/อ่ะ ได้
- สั้นตรงประเด็นแบบที่คนพิมพ์แชท บางข้อห้วนแบบพิมพ์ค้นหาก็ได้
- ปนบริบทส่วนตัวบ้าง เช่น งบ, ทำเล, สถานการณ์ ("เพิ่งเปิดร้าน", "ของเริ่มเยอะ", "งบไม่เกิน...")
- คละเจตนา: หาเจ้า/แนะนำ, ถามราคา, เทียบ (อันไหนดีกว่า/ถูกกว่า), ถามวิธี-ขั้นตอน, เช็คความน่าเชื่อถือ (ดีมั้ย รีวิวเป็นไง)

ห้ามเด็ดขาด:
- ภาษาทางการ/ภาษาโบรชัวร์: "หากต้องการ", "ท่าน", "ควรเลือกพาร์ทเนอร์แบบใด", "ที่มีความเชี่ยวชาญ", "ครบวงจร", "โซลูชัน"
- ประโยคยาวเกิน 25 คำ หรือซ้อนเงื่อนไขหลายชั้น
- โจทย์เรียงความ ("จงเปรียบเทียบข้อดีของ...", "อธิบายความแตกต่างระหว่าง...")

ตัวอย่างสไตล์ที่ถูก (อย่าลอกเนื้อหา ให้ลอกโทน):
- "โกดังให้เช่าแถวบางนา ที่ไหนถูกสุด"
- "จะเปิดร้านออนไลน์ ของเริ่มเยอะ หาโกดังเก็บของแถวสมุทรปราการ แนะนำหน่อย"
- "จ้างทำแอพร้านอาหาร ราคาประมาณเท่าไหร่"
- "ทนายรับทำคดีแรงงานที่ไหนดี ค่าจ้างแพงมั้ย"

ตอบเฉพาะ JSON array เท่านั้น รูปแบบ:
[
  {{"question": "คำถามภาษาไทย", "lang": "th"}},
  {{"question": "English question", "lang": "en"}}
]
ห้ามมีข้อความอื่นนอกจาก JSON"""

    raw = chat_for("questions", [{"role": "user", "content": prompt}], json_mode=True)

    # parse JSON — รองรับ markdown code block และทั้ง string[] และ object[]
    m = re.search(r'\[[\s\S]*\]', raw)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
        result = []
        for i in items:
            if isinstance(i, str) and i.strip():
                # string array — ตรวจภาษาจาก content
                lang = "en" if re.search(r'[a-zA-Z]{4,}', i) and not re.search(r'[฀-๿]', i) else "th"
                result.append({"question": i.strip(), "lang": lang})
            elif isinstance(i, dict) and i.get("question"):
                result.append({"question": str(i["question"]).strip(), "lang": str(i.get("lang", "th"))})
        return result[:n]
    except Exception:
        return []
