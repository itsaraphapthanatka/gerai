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
