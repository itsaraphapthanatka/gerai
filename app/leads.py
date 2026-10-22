"""ลูกค้าเป้าหมาย (leads) — ตรรกะล้วน ไม่แตะ DB/เครือข่าย
ทำ URL ให้สะอาด · ให้คะแนน "โอกาสขาย" จากผลตรวจสุขภาพเว็บ (ยิ่งเว็บล่องหนต่อบอทมาก เรายิ่งช่วยได้มาก)
สรุปผลเป็นภาษาคนให้คนนอกอ่าน · อ่านรายชื่อที่วางมาเป็นข้อความ"""
from __future__ import annotations
import re
import secrets
from urllib.parse import urlparse

STATUSES = [("new", "ใหม่"), ("contacted", "ทักแล้ว"), ("replied", "ตอบกลับ"),
            ("quoted", "เสนอราคาแล้ว"), ("won", "ปิดดีล"), ("lost", "ไม่สนใจ")]
STATUS_LABEL = dict(STATUSES)
SOURCES = {"audit": "ตรวจเว็บฟรี", "import": "นำเข้า", "manual": "เพิ่มเอง"}
AUDITS_PER_IP_PER_DAY = 5     # กันบอทยิงตรวจเว็บฟรีไม่หยุด (ทุกครั้งเราดึงเว็บจริง ~30 วิ)
REUSE_AUDIT_HOURS = 24        # โดเมนเดิมภายในเวลานี้ใช้ผลเดิม ไม่ตรวจซ้ำ
# ข้อที่วัดจากข้อมูลในแพลตฟอร์มของเรา (คอนเทนต์ที่เราเผยแพร่ให้ / ปลั๊กอิน WordPress) — เว็บคนนอกยังไม่มี จึงไม่นับและไม่โชว์
PUBLIC_IGNORE = {"freshness", "wp"}

# น้ำหนักต่อข้อที่ตก — ข้อที่ทำให้เว็บล่องหนต่อบอทหนักสุด เพราะคือสิ่งที่บริการเราแก้ให้ได้จริง
WEIGHTS = {"csr": 30, "noindex": 30, "indexed": 20, "robots": 15, "ai_bots": 15, "sitemap": 10, "canonical": 10,
           "page_identity": 10, "connector": 8, "orphan": 8, "freshness": 8, "robots_sitemap": 6, "www": 6, "sitemap_domain": 6}
# คำอธิบายให้คนนอกอ่าน — สั้น ไม่มีศัพท์เกินจำเป็น
EXPLAIN = {
    "home": "เข้าหน้าแรกไม่ได้ ตรวจข้ออื่นต่อไม่ได้",
    "csr": "หน้าเว็บสร้างด้วย JavaScript ล้วน บอท AI และ Google ที่ไม่รัน JavaScript จึงไม่เห็นเนื้อหา",
    "noindex": "เว็บสั่งไม่ให้ Google เก็บเข้า index ทั้งที่อยากให้คนหาเจอ",
    "indexed": "หน้าส่วนใหญ่ยังไม่อยู่ใน Google ค้นชื่อเรื่องแล้วไม่เจอ",
    "robots": "ไฟล์ robots.txt กันบอทไว้ Google และ AI เข้าอ่านไม่ได้",
    "ai_bots": "บอทของ ChatGPT Claude และ Perplexity ถูกกันไว้ AI จึงไม่รู้จักเว็บนี้",
    "sitemap": "ไม่มี sitemap หรือ sitemap เสีย Google ไม่รู้ว่ามีหน้าไหนบ้าง",
    "robots_sitemap": "robots.txt ไม่ได้บอกที่อยู่ sitemap",
    "canonical": "canonical ชี้ผิด ทุกหน้าบอก Google ว่าเป็นหน้าซ้ำ",
    "page_identity": "ทุก URL ส่งเนื้อหาหน้าแรกออกมาเหมือนกันหมด",
    "connector": "ยังไม่มีหน้า /geo llms.txt และ sitemap สำหรับ AI",
    "orphan": "คอนเทนต์ไม่มีลิงก์จากหน้าอื่นในเว็บ Google ให้น้ำหนักน้อย",
    "freshness": "ไม่มีเนื้อหาใหม่มานาน AI และ Google มองว่าเว็บไม่เคลื่อนไหว",
    "www": "แบบมี www กับไม่มี www ไม่ชี้ที่เดียวกัน คะแนนกระจาย",
    "sitemap_domain": "sitemap อ้าง URL โดเมนอื่น Google ตีตกทั้งไฟล์",
    "wp": "ตัวเชื่อม WordPress ไม่ตอบ",
}


def normalize_url(raw: str | None) -> str | None:
    """'example.co.th' / 'https://www.example.co.th/path' → 'https://www.example.co.th' (ใช้แค่รากเว็บ) · ไม่ใช่โดเมน → None"""
    s = (raw or "").strip()
    if not s:
        return None
    if not re.match(r"^https?://", s, re.I):
        s = "https://" + s
    try:
        p = urlparse(s)
    except Exception:
        return None
    host = (p.hostname or "").strip().lower()
    if not host or "." not in host or any(ch.isspace() for ch in host) or host.endswith("."):
        return None
    if re.search(r"[^a-z0-9.\-฀-๿]", host):   # ยอมโดเมนไทย (ตัวอักษร ก-๛) และ a-z 0-9 . -
        return None
    port = f":{p.port}" if p.port and p.port not in (80, 443) else ""
    return f"{p.scheme.lower()}://{host}{port}"


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or url).lower()
    return host[4:] if host.startswith("www.") else host


def score_audit(report: dict) -> dict:
    """คะแนนโอกาสขาย 0–100 จากข้อที่ตก + เหตุผล 3 ข้อแรก · เข้าเว็บไม่ได้ = 0 (ขายไม่ได้จนกว่าเว็บจะกลับมา)"""
    checks = [c for c in (report.get("checks") or []) if c.get("key") not in PUBLIC_IGNORE]
    by_key = {c.get("key"): c for c in checks}
    home = by_key.get("home")
    if home and home.get("status") == "fail":
        return {"score": 0, "level": "เข้าเว็บไม่ได้", "reasons": [EXPLAIN["home"]], "n_fail": 1, "n_ok": 0}
    fails = sorted([c for c in checks if c.get("status") == "fail"], key=lambda c: -WEIGHTS.get(c.get("key"), 5))
    score = min(100, sum(WEIGHTS.get(c.get("key"), 5) for c in fails))
    level = "สูง" if score >= 50 else ("กลาง" if score >= 25 else "ต่ำ")
    reasons = [EXPLAIN.get(c.get("key"), c.get("label") or "") for c in fails][:3]
    return {"score": score, "level": level, "reasons": reasons, "n_fail": len(fails),
            "n_ok": sum(1 for c in checks if c.get("status") == "ok"), "n_checked": len(checks)}


def public_summary(report: dict, limit: int = 3) -> list[dict]:
    """ข้อที่ตกเรียงจากหนักไปเบา สำหรับหน้าผลตรวจที่คนนอกอ่าน"""
    fails = sorted([c for c in (report.get("checks") or []) if c.get("status") == "fail" and c.get("key") not in PUBLIC_IGNORE],
                   key=lambda c: -WEIGHTS.get(c.get("key"), 5))
    return [{"key": c.get("key"), "label": c.get("label") or "", "why": EXPLAIN.get(c.get("key"), ""),
             "detail": c.get("detail") or ""} for c in fails[:limit]]


def parse_import(text: str) -> list[dict]:
    """วางรายชื่อมาเป็นบรรทัด: 'โดเมน, อีเมล, ชื่อ' (คั่นด้วย , ; หรือ tab) — คืนเฉพาะโดเมนที่ใช้ได้ ไม่ซ้ำกัน"""
    out, seen = [], set()
    for line in (text or "").splitlines():
        parts = [p.strip() for p in re.split(r"[,\t;]", line) if p.strip()]
        if not parts:
            continue
        if parts[0].lower() in ("domain", "url", "website", "เว็บ", "โดเมน"):
            continue   # บรรทัดหัวตาราง
        url = normalize_url(parts[0])
        if not url:
            continue
        dom = domain_of(url)
        if dom in seen:
            continue
        seen.add(dom)
        email = next((p for p in parts[1:] if "@" in p), "")
        name = next((p for p in parts[1:] if "@" not in p), "")
        out.append({"url": url, "domain": dom, "email": email.lower(), "name": name[:120]})
    return out


def new_token() -> str:
    return secrets.token_urlsafe(9)
