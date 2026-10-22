"""หาลูกค้าเอง (Prospecting agent) — เจ้าของไม่ต้องเอาข้อความไปโพสต์
ค้นธุรกิจตามกลุ่มเป้าหมาย (search backend เดิม: Serper/Brave ถ้ามีคีย์ ไม่งั้น DuckDuckGo ฟรี) → กรองเว็บที่ไม่ใช่ธุรกิจ (โซเชียล พอร์ทัล
ไดเรกทอรี ข่าว ราชการ สถานศึกษา) → เพิ่มเป็นลูกค้าเป้าหมาย → ตรวจเว็บฟรี → หาอีเมลธุรกิจที่เปิดเผยบนเว็บ → CEO เสนอทัก (เจ้าของอนุมัติทุกเช้า)
กติกาจากแผน: ใช้เฉพาะอีเมลธุรกิจที่เปิดเผยสาธารณะ ส่งครั้งเดียวพร้อมผลตรวจจริงและลิงก์ยกเลิก ไม่ส่งซ้ำถ้าไม่ตอบ"""
from __future__ import annotations
import json
import re
from urllib.parse import urljoin, urlparse

import httpx

from . import db, geo_worker, leads, pipeline

DEFAULT_SEGMENTS = [
    "อสังหาริมทรัพย์เชิงพาณิชย์ ให้เช่าสำนักงาน โกดัง",
    "สำนักงานกฎหมาย ทนายความ รับว่าความ",
    "บริษัทรับทำซอฟต์แวร์ พัฒนาระบบ แอป",
    "คลินิกทันตกรรม จัดฟัน",
    "บริษัทรับเหมาก่อสร้าง ตกแต่งภายใน",
    "สำนักงานบัญชี รับทำบัญชี ตรวจสอบบัญชี",
    "โรงพยาบาลสัตว์ คลินิกสัตวแพทย์",
    "โรงเรียนสอนภาษา สถาบันติว",
]
AREAS = ["กรุงเทพ", "นนทบุรี", "เชียงใหม่", "ชลบุรี", "ภูเก็ต", "ขอนแก่น", "นครราชสีมา", "สมุทรปราการ"]
DAILY_DEFAULT = 10
DAILY_MAX = 30
QUERIES_PER_RUN = 3
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# เว็บที่ไม่ใช่ธุรกิจเป้าหมาย: โซเชียล · พอร์ทัล/ไดเรกทอรี · มาร์เก็ตเพลส · ข่าว · เครื่องมือ
BLOCK_HOSTS = {
    "facebook.com", "instagram.com", "youtube.com", "tiktok.com", "twitter.com", "x.com", "line.me", "lin.ee", "linkedin.com",
    "pinterest.com", "threads.net", "google.com", "google.co.th", "goo.gl", "maps.google.com", "wikipedia.org", "wikihow.com",
    "pantip.com", "wongnai.com", "shopee.co.th", "lazada.co.th", "kaidee.com", "ddproperty.com", "livinginsider.com",
    "propertyhub.in.th", "hipflat.co.th", "dotproperty.co.th", "thailand-property.com", "renthub.in.th", "baania.com",
    "prakard.com", "fazwaz.com", "propertyscout.co.th", "homes.co.th", "yellowpages.co.th", "thaiyellowpages.com",
    "jobsdb.com", "jobthai.com", "jobbkk.com", "tripadvisor.com", "agoda.com", "booking.com", "traveloka.com",
    "bangkokbiznews.com", "thairath.co.th", "matichon.co.th", "posttoday.com", "sanook.com", "kapook.com", "mthai.com",
    "dek-d.com", "blockdit.com", "medium.com", "trueid.net", "scribd.com", "slideshare.net", "issuu.com", "apple.com",
    "play.google.com", "amazon.com", "aliexpress.com", "alibaba.com", "quora.com", "reddit.com", "yelp.com", "foursquare.com",
    "thaifranchisecenter.com", "smeleader.com", "brandage.com", "marketingoops.com", "techsauce.co", "longtunman.com",
    "bing.com", "yahoo.com", "duckduckgo.com", "webstagram.one", "picuki.com",
}
BLOCK_SUFFIXES = (".go.th", ".ac.th", ".or.th", ".mi.th", ".net.th", ".gov", ".edu", ".org")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SKIP_EMAIL = re.compile(r"(noreply|no-reply|donotreply|example\.|sentry|wixpress|@2x|\.png|\.jpg|\.jpeg|\.gif|\.svg|\.webp|schema\.org|w3\.org|yourdomain|domain\.com)", re.I)
PREFER = ("info", "contact", "sales", "hello", "hi", "admin", "office", "marketing", "support", "cs", "enquiry", "inquiry")
# กล่องที่ไม่ใช่ที่ขายของ (ฝ่ายบุคคล บัญชี กฎหมาย ไอที) — เอาไว้ท้ายสุด ใช้เมื่อไม่มีอย่างอื่นจริง ๆ
AVOID = ("hr", "career", "careers", "job", "jobs", "recruit", "recruitment", "press", "media", "invoice", "billing", "accounting",
         "accounts", "account", "finance", "privacy", "dpo", "legal", "webmaster", "abuse", "security", "it", "dev", "noc", "payroll")
CONTACT_HINT = re.compile(r"contact|ติดต่อ|about|เกี่ยวกับ", re.I)
LINE_RE = re.compile(r"https?://(?:line\.me/(?:R/)?ti/p/|lin\.ee/)[A-Za-z0-9@_\-~%]+")
LINK_RE = re.compile(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', re.I | re.S)


# ---------- ตั้งค่า ----------
def enabled() -> bool:
    return (db.get_setting("prospect_enabled") or "1") == "1"


def daily_limit() -> int:
    try:
        return max(1, min(DAILY_MAX, int(db.get_setting("prospect_daily") or DAILY_DEFAULT)))
    except ValueError:
        return DAILY_DEFAULT


def segments() -> list[str]:
    raw = db.get_setting("prospect_segments") or ""
    out = [s.strip() for s in raw.splitlines() if s.strip()]
    return out or list(DEFAULT_SEGMENTS)


def save_config(segments_text: str, daily: str, on: bool) -> None:
    db.set_setting("prospect_segments", "\n".join(s.strip() for s in (segments_text or "").splitlines() if s.strip())[:4000])
    try:
        db.set_setting("prospect_daily", str(max(1, min(DAILY_MAX, int(daily or DAILY_DEFAULT)))))
    except ValueError:
        pass
    db.set_setting("prospect_enabled", "1" if on else "0")


def last_run() -> dict | None:
    raw = db.get_setting("prospect_last")
    try:
        return json.loads(raw) if raw else None
    except Exception:
        return None


def status_text() -> str:
    """บรรทัดสำหรับสรุปเช้า"""
    r = last_run()
    if not r:
        return ""
    if r.get("skipped"):
        return f"หาลูกค้าเอง: {r['skipped']}"
    return (f"หาลูกค้าเอง: รอบ {r['at'][:16].replace('T', ' ')} พบ {r['found']} เว็บ เพิ่ม {r['added']} ตรวจ {r['audited']} "
            f"มีอีเมล {r['with_email']} เสนอทัก {r['proposed']}" + (f" ล้ม {r['errors']}" if r.get("errors") else ""))


def public_config() -> dict:
    return {"enabled": enabled(), "daily": daily_limit(), "segments_text": "\n".join(segments()),
            "default_text": "\n".join(DEFAULT_SEGMENTS), "last": last_run(), "backend": geo_worker.active_backend()}


# ---------- กรองโดเมน ----------
def apex(domain: str) -> str:
    """shop.co.th → shop.co.th · sub.shop.com → shop.com (รองรับ second-level ของไทย/ทั่วไป)"""
    d = (domain or "").lower().strip(".")
    parts = d.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "in", "or", "ac", "go", "net", "com", "mi") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else d


def is_business_site(domain: str) -> bool:
    d = (domain or "").lower()
    if not d or "." not in d or re.fullmatch(r"[\d.]+", d):
        return False
    if d.endswith(BLOCK_SUFFIXES):
        return False
    a = apex(d)
    return a not in BLOCK_HOSTS and d not in BLOCK_HOSTS


def next_queries(k: int = QUERIES_PER_RUN) -> list[str]:
    """หมุนกลุ่มเป้าหมาย × พื้นที่ ไปเรื่อย ๆ ทุกรอบ (เก็บตำแหน่งใน settings) จะได้ไม่ค้นซ้ำที่เดิม"""
    segs = segments()
    try:
        cur = int(db.get_setting("prospect_cursor") or 0)
    except ValueError:
        cur = 0
    out = []
    for i in range(k):
        n = cur + i
        out.append(f"{segs[n % len(segs)]} {AREAS[(n // len(segs)) % len(AREAS)]}")
    db.set_setting("prospect_cursor", str(cur + k))
    return out


# ---------- หาช่องทางติดต่อบนเว็บ ----------
def _fetch(url: str) -> str:
    try:
        r = httpx.get(url, follow_redirects=True, timeout=15, headers={"User-Agent": UA, "Accept-Language": "th,en;q=0.8"})
        if r.status_code >= 400 or "html" not in (r.headers.get("content-type") or "html"):
            return ""
        return r.text[:600_000]
    except Exception:
        return ""


def _rank_email(e: str):
    local = e.split("@")[0]
    tier = 0 if any(local.startswith(p) for p in PREFER) else (2 if any(local == a or local.startswith(a + ".") or local.startswith(a + "-") for a in AVOID) else 1)
    return (tier, len(e))


def find_email_via_search(domain: str, search=None) -> str:
    """เว็บไม่มีอีเมล → ถามเครื่องมือค้นหาว่าธุรกิจนี้ประกาศอีเมลไว้ที่ไหน (ไดเรกทอรี โปรไฟล์ ประกาศงาน) รับเฉพาะอีเมลบนโดเมน/โดเมนหลักเดียวกัน"""
    search = search or geo_worker.search
    a = apex(domain)
    found = set()
    for qtext in (f'"@{a}"', f"{a} อีเมล ติดต่อ", f"{a} email contact"):
        try:
            rows = search(qtext, 10)
        except Exception:
            rows = []
        for r in rows or []:
            text = f"{r.get('title') or ''} {r.get('snippet') or ''}"
            for m in EMAIL_RE.finditer(text):
                e = m.group(0).strip().lower().rstrip(".")
                if (e.endswith("@" + a) or e.endswith("." + a)) and not SKIP_EMAIL.search(e):
                    found.add(e)
        if found:
            break
    return sorted(found, key=_rank_email)[0] if found else ""


def find_contact(site_url: str, fetch=None, search=None) -> dict:
    """อีเมลธุรกิจที่เปิดเผยบนหน้าแรก/หน้าติดต่อ (ชอบ info@/contact@/sales@ บนโดเมนเดียวกัน) + ลิงก์ LINE ถ้ามี
    ไม่เจอบนเว็บ → หาจากผลค้นหา (search=False ปิดขั้นนี้)"""
    fetch = fetch or _fetch
    emails, line = set(), ""
    host = leads.domain_of(site_url)

    def harvest(html: str):
        nonlocal line
        for m in re.finditer(r'mailto:([^"\'?\s>]+)', html or ""):
            emails.add(m.group(1))
        for m in EMAIL_RE.finditer(html or ""):
            emails.add(m.group(0))
        if not line:
            m = LINE_RE.search(html or "")
            if m:
                line = m.group(0)

    home = fetch(site_url)
    harvest(home)
    seen, more = set(), []
    for m in LINK_RE.finditer(home or ""):
        href, text = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
        if CONTACT_HINT.search(href) or CONTACT_HINT.search(text):
            u = urljoin(site_url, href)
            h = (urlparse(u).hostname or "").lower()
            if u not in seen and (h == host or h.endswith("." + host)):
                seen.add(u)
                more.append(u)
    for u in more[:3]:
        harvest(fetch(u))
    clean = sorted({e.strip().lower().rstrip(".") for e in emails if e and not SKIP_EMAIL.search(e)})
    same = [e for e in clean if e.split("@")[-1].endswith(host)]
    others = [e for e in clean if e not in same]
    ordered = sorted(same, key=_rank_email) + sorted(others, key=_rank_email)
    email, source = (ordered[0] if ordered else ""), "เว็บ"
    if not email and search is not False:
        email = find_email_via_search(host, None if search is None else search)
        source = "ผลค้นหา" if email else ""
    return {"email": email, "line": line, "n": len(clean), "source": source}


# ---------- รอบหาลูกค้า ----------
def run(limit: int | None = None, search=None, contact=None) -> dict:
    """ค้น → กรอง → เพิ่ม lead → ตรวจเว็บ → หาอีเมล → CEO เสนอทัก · สรุปรอบเก็บใน settings (prospect_last) ให้สรุปเช้าอ่าน"""
    if not enabled():
        res = {"skipped": "ปิดอยู่ (เปิดได้ที่ตั้งค่า → หาลูกค้าอัตโนมัติ)", "at": db.now()}
        db.set_setting("prospect_last", json.dumps(res, ensure_ascii=False))
        return res
    search = search or geo_worker.search
    contact = contact or find_contact
    limit = max(1, min(DAILY_MAX, limit or daily_limit()))
    seen = {leads.domain_of(b["domain"]) for b in db.list_all_brands()} | {l["domain"] for l in db.list_leads(None, 100000)}
    queries = next_queries()
    cands = []
    for q in queries:
        try:
            rows = search(q, 10)
        except Exception:
            rows = []
        for r in rows or []:
            site = leads.normalize_url(r.get("url") or "")
            if not site:
                continue
            dom = leads.domain_of(site)
            if dom in seen or not is_business_site(dom):
                continue
            seen.add(dom)
            cands.append((site, dom, (r.get("title") or "").strip()[:160], q))
        if len(cands) >= limit * 2:
            break
    out = {"at": db.now(), "queries": queries, "found": len(cands), "added": 0, "audited": 0, "with_email": 0, "proposed": 0,
           "errors": 0, "leads": []}
    for site, dom, title, q in cands[:limit]:
        lid = db.add_lead(site, dom, source="prospect")
        db.update_lead(lid, notes=f"พบจากคำค้น: {q}" + (f" · {title}" if title else ""), src="prospect")
        pipeline.record(lid, "prospect", f"คำค้น: {q}" + (f" · {title}" if title else ""), "prospecting")
        out["added"] += 1
        out["leads"].append(dom)
        try:
            pipeline.run_lead_audit(lid, site)
            out["audited"] += 1
        except Exception as e:
            pipeline.record(lid, "note", f"ตรวจเว็บไม่สำเร็จ: {type(e).__name__}", "prospecting")
            out["errors"] += 1
            continue
        try:
            c = contact(site)
        except Exception:
            c = {"email": "", "line": "", "n": 0}
        if c.get("email") or c.get("line"):
            db.update_lead(lid, email=(c.get("email") or None), contact=(c.get("line") or None))
            pipeline.record(lid, "contact_found", (c.get("email") or "") + (f" (จาก{c['source']})" if c.get("email") and c.get("source") else "")
                            + (f" · {c['line']}" if c.get("line") else ""), "prospecting")
        else:
            pipeline.record(lid, "no_contact", "ไม่พบอีเมลธุรกิจบนเว็บ — เพิ่มช่องทางเองที่หน้าลูกค้า แล้วกดให้ CEO เสนอ", "prospecting")
        if c.get("email"):
            out["with_email"] += 1
            try:
                if pipeline.ceo_propose(db.get_lead(lid)):
                    out["proposed"] += 1
            except Exception:
                out["errors"] += 1
    db.set_setting("prospect_last", json.dumps(out, ensure_ascii=False))
    return out


def refind_contact(lead) -> dict:
    """หาช่องทางติดต่อซ้ำให้ลูกค้าเป้าหมายที่ยังไม่มีอีเมล (เว็บ + ผลค้นหา) · เจอแล้วบันทึกและให้ CEO เสนอต่อ"""
    c = find_contact(lead["site_url"])
    out = {"email": c.get("email") or "", "line": c.get("line") or "", "source": c.get("source") or "", "proposed": None}
    if c.get("email") and not lead["email"]:
        db.update_lead(lead["id"], email=c["email"], contact=lead["contact"] or (c.get("line") or None))
        pipeline.record(lead["id"], "contact_found", f"{c['email']} (จาก{c.get('source') or 'เว็บ'}) — หาซ้ำ", "prospecting")
        if lead["status"] in ("new", "lost") and lead["audit_json"]:
            out["proposed"] = pipeline.ceo_propose(db.get_lead(lead["id"]))
    elif c.get("line") and not lead["contact"]:
        db.update_lead(lead["id"], contact=c["line"])
    return out
