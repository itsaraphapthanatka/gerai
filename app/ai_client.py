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


def generate_questions(brand_name: str, domain: str, market: str, n: int = 8) -> list[dict]:
    """สร้างคำถามเป้าหมาย GEO สำหรับแบรนด์ — คืน list of {question, lang}"""
    prompt = f"""คุณเป็นผู้เชี่ยวชาญ GEO (Generative Engine Optimization)
ช่วยสร้างคำถามที่กลุ่มเป้าหมายมักถาม AI เพื่อหาบริษัทหรือสินค้าแบบนี้

ข้อมูลแบรนด์:
- ชื่อ: {brand_name}
- โดเมน: {domain}
- ธุรกิจ/ตลาด: {market}

สร้างคำถาม {n} ข้อ ที่ถ้า AI ตอบได้ดี จะทำให้แบรนด์นี้ปรากฏในคำตอบ
- ส่วนใหญ่เป็นภาษาไทย (อย่างน้อย 5 ข้อ)
- บางส่วนเป็นภาษาอังกฤษ (1-2 ข้อ)
- เป็นคำถามที่คนจริงๆ จะถาม ไม่ใช่คำ keywords แห้งๆ
- หลากหลาย: ถามหาบริการ, เปรียบเทียบ, ขอคำแนะนำ, ถามราคา
- เน้นแนว People Also Ask / autocomplete ของ Google (ที่ไหน, ราคาเท่าไหร่, ดีไหม, เทียบกับ, วิธี/ขั้นตอน, ทำไม) — แบบ AEO

ตอบเฉพาะ JSON array เท่านั้น รูปแบบ:
[
  {{"question": "คำถามภาษาไทย", "lang": "th"}},
  {{"question": "English question", "lang": "en"}}
]
ห้ามมีข้อความอื่นนอกจาก JSON"""

    raw = _chat([{"role": "user", "content": prompt}])

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
