"""รายงานตรวจเว็บฉบับเต็ม (หน้า /audit/<token>) — 2 ชั้น
ชั้น 1 ฟรีทุกคน (ต้นทุนเกือบศูนย์): ไต่เว็บสูงสุด 100 หน้า → Site health + ปัญหาพร้อมวิธีแก้ · เลือก 5 คำค้นที่ลูกค้าใช้หา → อันดับ Google + ใคร
ติด Top 10 ต่อคำ · คู่แข่งหลัก (ตัดแพลตฟอร์มอย่าง YouTube/Facebook ออก เพราะไม่ใช่คู่แข่งทางธุรกิจ)
ชั้น 2 (มีค่าใช้จ่ายจริง ~฿2/คำถาม เมื่อถาม ChatGPT พร้อมค้นเว็บ): 20 คำถามที่ลูกค้าถาม AI → ถาม ChatGPT จริง → แบรนด์/คู่แข่งถูกเอ่ยถึงหรือถูกอ้างอิงไหม
รันเมื่อมีสัญญาณซื้อ (ทิ้งช่องทางติดต่อ · ขอใบเสนอราคา) หรือแอดมินกด · มีเพดานต่อวัน (ตั้งค่า deep_ai_daily)
ผลเก็บใน leads.deep_json / leads.deep_ai_json แยกกัน (สองเธรดเขียนไม่ทับกัน)"""
from __future__ import annotations
import concurrent.futures as cf
import datetime as dt
import html as _html
import json
import os
import re
import threading
import time
from collections import Counter, defaultdict
from urllib.parse import urljoin, urlparse
from urllib import robotparser

from . import db, leads as L, office

UA = "Mozilla/5.0 (compatible; JerAI-Audit/1.0; +https://geo.appreview.cloud/audit)"
MAX_PAGES = 100
WORKERS = 4                 # สุภาพกับเว็บเขา — ไม่ยิงพร้อมกันเกิน 4
TIME_BUDGET = 110           # วินาที — ไต่ไม่ครบก็สรุปเท่าที่ได้
MAX_BYTES = 800_000
N_KEYWORDS = 5
N_PROMPTS = 20
AI_DAILY_DEFAULT = 5        # เพดานรายต่อวัน · วัดจริง ChatGPT+ค้นเว็บ ≈ $1.15 (฿40) ต่อ 20 คำถาม → ≤ ฿200/วัน
ASK_PARALLEL = 4            # ถามพร้อมกัน 4 คำถาม (ทีละข้อใช้ ~9 นาที)
STALE_MIN = 15              # รันค้างเกินนี้ = ล่ม ให้รันใหม่ได้
BENCHMARK = 70
ENGINE_LABEL = {"chatgpt": "ChatGPT", "perplexity": "Perplexity", "gemini": "Gemini", "claude": "Claude"}
PLATFORMS = {"youtube.com", "facebook.com", "instagram.com", "tiktok.com", "linkedin.com", "th.linkedin.com", "x.com", "twitter.com",
             "wikipedia.org", "th.wikipedia.org", "en.wikipedia.org", "pantip.com", "shopee.co.th", "lazada.co.th", "google.com",
             "play.google.com", "apps.apple.com", "blockdit.com", "medium.com", "reddit.com", "wongnai.com", "line.me"}
GROUPS = ["Crawl & Index", "On-page core", "Content", "Schema & links", "Media & speed"]
SEV_ORDER = {"Error": 0, "Warning": 1, "Notice": 2}
_SEM = threading.BoundedSemaphore(2)       # รายงานเต็มพร้อมกันได้ไม่เกิน 2 ราย (กันเครื่องเราหนัก)
RUN_INLINE = False                          # เทสต์ตั้ง True → รันในเธรดเดียว
HOOKS = {"client": None, "search": None, "llm": None, "ask": None, "engine": None}   # เทสต์ฉีดของปลอม (ไม่ยิงเน็ตจริง)


def enabled() -> bool:
    """ปิดได้ด้วย GEO_DEEP_AUDIT=0 (เทสต์ชุดอื่นปิดไว้ ไม่ให้ไต่เว็บจริงเบื้องหลัง)"""
    return os.getenv("GEO_DEEP_AUDIT", "1") != "0"


def _host(u: str) -> str:
    h = (urlparse(u).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def _norm(u: str) -> str:
    p = urlparse(u)
    path = p.path or "/"
    return f"{p.scheme}://{p.netloc.lower()}{path}"


# ---------- อ่านหน้า ----------
def parse_page(html: str, url: str, apex: str) -> dict:
    h = html or ""
    title = re.search(r"(?is)<title[^>]*>(.*?)</title>", h)
    desc = re.search(r"""(?is)<meta[^>]+name=["']description["'][^>]*>""", h)
    desc_c = re.search(r"""(?is)content=["']([^"']*)""", desc.group(0)) if desc else None
    h1s = re.findall(r"(?is)<h1[^>]*>(.*?)</h1>", h)
    imgs = re.findall(r"(?is)<img\b[^>]*>", h)
    no_alt = sum(1 for i in imgs if not re.search(r"""(?i)\balt\s*=\s*["'][^"']+["']""", i))
    robots = re.search(r"""(?is)<meta[^>]+name=["']robots["'][^>]*content=["']([^"']*)""", h)
    links = set()
    for href in re.findall(r"""(?is)<a\b[^>]*?href\s*=\s*["']([^"']*)["']""", h):
        href = _html.unescape(href.strip())
        if not href or href.startswith("#") or re.match(r"(?i)^(mailto|tel|javascript|data|sms|line|whatsapp):", href):
            continue
        full = urljoin(url, href).split("#")[0]
        if not full.lower().startswith("http") or _host(full) != apex or "/cdn-cgi/" in full:
            continue                              # /cdn-cgi/l/email-protection = Cloudflare ซ่อนอีเมล เบราว์เซอร์ถอดเองด้วย JS ไม่ใช่ลิงก์เสีย
        if re.search(r"(?i)\.(jpg|jpeg|png|gif|webp|svg|pdf|zip|mp4|mp3|docx?|xlsx?|pptx?|css|js)(\?|$)", full):
            continue
        links.add(_norm(full.split("?")[0]))
    clean = lambda s: re.sub(r"\s+", " ", _html.unescape(re.sub(r"(?s)<[^>]+>", " ", s or ""))).strip()
    return {"title": clean(title.group(1))[:200] if title else "", "desc": (desc_c.group(1).strip() if desc_c else ""),
            "h1": [clean(x)[:150] for x in h1s][:3], "n_img": len(imgs), "no_alt": no_alt,
            "noindex": bool(robots and "noindex" in robots.group(1).lower()), "links": links}


def _sitemap_urls(client, base: str, apex: str, limit: int = 300) -> list[str]:
    out, todo, seen = [], [base.rstrip("/") + "/sitemap.xml", base.rstrip("/") + "/sitemap_index.xml", base.rstrip("/") + "/wp-sitemap.xml"], set()
    while todo and len(out) < limit and len(seen) < 8:
        sm = todo.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        try:
            r = client.get(sm)
            if r.status_code != 200 or "<" not in r.text[:200]:
                continue
        except Exception:
            continue
        locs = [x.strip() for x in re.findall(r"(?is)<loc>\s*(.*?)\s*</loc>", r.text)]
        for u in locs:
            if u.lower().endswith(".xml") or "sitemap" in u.lower().rsplit("/", 1)[-1]:
                todo.append(u)
            elif _host(u) == apex:
                out.append(_norm(u))
    return out[:limit]


def crawl(site: str, client=None, max_pages: int = MAX_PAGES, budget: float = TIME_BUDGET) -> dict:
    """ไต่จากหน้าแรก + sitemap แบบกว้างก่อน (BFS) · เคารพ robots.txt · เก็บสถานะ/title/H1/description/alt/ลิงก์ขาเข้า"""
    import httpx
    own = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=12, headers={"User-Agent": UA})
    t0 = time.time()
    base = site.rstrip("/")
    apex = _host(base)
    rp = robotparser.RobotFileParser()
    try:
        r = client.get(base + "/robots.txt")
        rp.parse(r.text.splitlines() if r.status_code == 200 else [])
    except Exception:
        rp.parse([])
    allowed = lambda u: rp.can_fetch(UA, u) if rp else True
    pages, inbound, ref = {}, defaultdict(set), {}
    home = _norm(base + "/")
    queue, queued = [home], {home}
    from_sitemap = set()
    try:
        for u in _sitemap_urls(client, base, apex):
            from_sitemap.add(u)
            if u not in queued:
                queued.add(u)
                queue.append(u)
    except Exception:
        pass
    blocked = 0

    def fetch(u):
        try:
            with client.stream("GET", u) as resp:
                ctype = resp.headers.get("content-type", "")
                body = b""
                if "html" in ctype:
                    for chunk in resp.iter_bytes():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            break
                final = str(resp.url)
                return {"url": u, "status": resp.status_code, "final": final, "ctype": ctype,
                        "redirected": _norm(final) != u, "html": body.decode(resp.encoding or "utf-8", "replace") if body else ""}
        except Exception as e:
            return {"url": u, "status": 0, "error": f"{type(e).__name__}", "html": ""}

    with cf.ThreadPoolExecutor(WORKERS) as ex:
        while queue and len(pages) < max_pages and time.time() - t0 < budget:
            batch = []
            while queue and len(batch) < WORKERS * 2 and len(pages) + len(batch) < max_pages:
                u = queue.pop(0)
                if not allowed(u):
                    blocked += 1
                    continue
                batch.append(u)
            for res in ex.map(fetch, batch):
                u = res["url"]
                rec = {"url": u, "status": res["status"], "redirected": res.get("redirected", False), "final": res.get("final"),
                       "error": res.get("error"), "html": bool(res["html"]), "in_sitemap": u in from_sitemap}
                if res["html"] and 200 <= res["status"] < 300:
                    info = parse_page(res["html"], res.get("final") or u, apex)
                    for l in info.pop("links"):
                        if l != u:
                            inbound[l].add(u)
                            ref.setdefault(l, u)
                        if l not in queued and len(queued) < max_pages * 4:
                            queued.add(l)
                            queue.append(l)
                    rec.update(info)
                pages[u] = rec
    for u, rec in pages.items():
        rec["inbound"] = len(inbound.get(u, ()))
        rec["found_on"] = ref.get(u)
    https_redirect = None
    try:
        r = client.get(f"http://{urlparse(base).netloc}/", follow_redirects=False)
        loc = r.headers.get("location", "")
        https_redirect = r.status_code in (301, 302, 307, 308) and loc.lower().startswith("https")
    except Exception:
        https_redirect = None                     # เปิด http ไม่ได้เลย = ไม่ถือว่าผิด
    if own:
        client.close()
    return {"site": base, "apex": apex, "pages": list(pages.values()), "n": len(pages), "queued": len(queued), "blocked": blocked,
            "truncated": bool(queue) or len(pages) >= max_pages, "https_redirect": https_redirect,
            "seconds": round(time.time() - t0, 1), "at": db.now()}


# ---------- ปัญหา + คะแนน ----------
def _path(u: str, limit: int = 60) -> str:
    """path ที่คนอ่านได้: ถอด %E0%B8.. เป็นภาษาไทย · ตัดให้สั้น (ไว้แสดงผล)"""
    from urllib.parse import unquote
    p = urlparse(u or "")
    s = unquote((p.path or "/") + (("?" + p.query) if p.query else ""))
    return s if len(s) <= limit else s[:limit - 1] + "…"


def issues(cr: dict) -> list[dict]:
    """ปัญหาเรียงตามความสำคัญ: กลุ่ม (Crawl & Index ก่อน) → ระดับ → จำนวนหน้า"""
    pages = cr["pages"]
    ok = [p for p in pages if 200 <= p["status"] < 300 and p.get("html")]
    out = []
    add = lambda key, group, sev, label, urls, fix, count=None: out.append(
        {"key": key, "group": group, "severity": sev, "label": label, "urls": urls[:40], "count": count if count is not None else len(urls), "fix": fix})
    e5 = [p for p in pages if p["status"] >= 500 or p["status"] == 0]
    if e5:
        add("5xx", "Crawl & Index", "Error", "หน้าตอบกลับ 5XX หรือเปิดไม่ได้ — Google และ AI เข้าไม่ได้", [p["url"] for p in e5],
            "ดู log ของเซิร์ฟเวอร์/ปลั๊กอินที่ทำให้หน้า error ถ้าไม่ใช้หน้านี้แล้วให้เอาออกจากเมนูและ sitemap")
    e4 = [p for p in pages if 400 <= p["status"] < 500]
    mail = [p for p in e4 if "@" in _path(p["url"], 10000).rstrip("/").rsplit("/", 1)[-1]]
    rest = [p for p in e4 if p not in mail]
    if mail:
        add("mailto", "Crawl & Index", "Error", "ลิงก์อีเมลไม่มี mailto: — กลายเป็นหน้า 4XX จำนวนมาก", [p["url"] for p in mail],
            "แก้ลิงก์อีเมลในเมนู/ส่วนท้าย/ข้อความให้ขึ้นต้นด้วย mailto: (เช่น mailto:info@โดเมน) ทุกจุด"
            + (f" · พบที่หน้า {_path(mail[0]['found_on'])}" if mail[0].get("found_on") else ""))
    if rest:
        add("4xx", "Crawl & Index", "Error", "ลิงก์ภายในพาไปหน้า 4XX (หน้าหาย/เปลี่ยน URL)", [p["url"] for p in rest],
            "ทำ 301 redirect ไปหน้าที่ถูกต้อง หรือกู้หน้าคืน แล้วแก้ลิงก์ที่ชี้มา"
            + (f" · ตัวอย่างลิงก์อยู่ที่หน้า {_path(rest[0]['found_on'])}" if rest[0].get("found_on") else ""))
    if cr.get("https_redirect") is False:
        add("https", "Crawl & Index", "Error", "เวอร์ชัน http ไม่ redirect ไป https (ไม่เข้ารหัส)", [f"http://{urlparse(cr['site']).netloc}/"],
            "ตั้ง 301 redirect จาก http ไป https ทุกหน้า และให้ canonical/sitemap เป็น https")
    noidx = [p for p in ok if p.get("noindex")]
    if noidx:
        add("noindex", "Crawl & Index", "Warning", "หน้าถูกตั้ง noindex — Google จะไม่เก็บ", [p["url"] for p in noidx],
            "ถ้าอยากให้คนหาเจอ ให้เอา noindex ออก (มักตั้งไว้ในปลั๊กอิน SEO)")
    no_title = [p for p in ok if not p.get("title")]
    if no_title:
        add("title_missing", "On-page core", "Error", "ไม่มี Title", [p["url"] for p in no_title], "ใส่ Title ที่บอกว่าหน้านี้ขายอะไร/ตอบเรื่องอะไร 50–60 ตัวอักษร")
    by_title = defaultdict(list)
    for p in ok:
        if p.get("title"):
            by_title[p["title"].strip().lower()].append(p["url"])
    dups = [us for us in by_title.values() if len(us) > 1]
    if dups:
        flat = [u for us in dups for u in us]
        add("dup_title", "On-page core", "Error", f"Title ซ้ำกันหลายหน้า ({len(dups)} ชุด)", flat,
            "ถ้าเป็นหน้าซ้ำ (เช่น /contact กับ /contact-2) ให้เก็บหน้าเดียวแล้ว 301 ที่เหลือ ถ้าคนละหน้าให้เขียน Title ให้ต่างกัน")
    no_h1 = [p for p in ok if not p.get("h1")]
    if no_h1:
        add("h1_missing", "On-page core", "Warning", "ไม่มี H1", [p["url"] for p in no_h1], "เพิ่ม H1 หนึ่งอันต่อหน้า บอก Google/AI ว่าหน้านี้เกี่ยวกับอะไร")
    no_desc = [p for p in ok if not p.get("desc")]
    if no_desc:
        add("desc_missing", "On-page core", "Warning", "ไม่มี meta description", [p["url"] for p in no_desc],
            "ใส่ description 120–160 ตัวอักษรผ่านปลั๊กอิน SEO หรือ noindex หน้า archive/tag ที่ไม่ได้ใช้หาลูกค้า")
    thin = [p for p in ok if p.get("title") and len(p.get("title", "")) < 15]
    if thin:
        add("short_title", "Content", "Notice", "Title สั้นเกินไป (ไม่ถึง 15 ตัวอักษร)", [p["url"] for p in thin], "เขียน Title ให้มีชื่อบริการและจุดขาย")
    home = _norm(cr["site"] + "/")
    orphan = [p for p in ok if p["inbound"] == 0 and p["url"] != home]
    if orphan:
        add("orphan", "Schema & links", "Error", "หน้าที่ไม่มีลิงก์ภายในชี้เข้ามา (orphan)", [p["url"] for p in orphan],
            "ใส่ลิงก์จากเมนู/ส่วนท้าย/หน้าที่เกี่ยวข้อง หรือถ้าเป็นหน้าทิ้ง (เช่น /sample-page) ให้ลบหรือ noindex")
    alt = [p for p in ok if p.get("no_alt")]
    if alt:
        add("alt_missing", "Media & speed", "Warning", "รูปไม่มี alt text", [p["url"] for p in alt],
            "ใส่ alt อธิบายรูปสั้น ๆ ช่วยทั้งการค้นหารูปและผู้ใช้ที่มองไม่เห็น", count=sum(p["no_alt"] for p in alt))
    out.sort(key=lambda x: (GROUPS.index(x["group"]), SEV_ORDER[x["severity"]], -x["count"]))
    return out


def health(cr: dict, iss: list[dict]) -> dict:
    """คะแนน 0–100: สัดส่วนหน้าที่ไม่มี error (หน้าที่มีแค่ warning หักครึ่ง) — วิธีเดียวกับเครื่องมือ site audit ทั่วไป"""
    n = max(1, cr["n"])
    err_urls, warn_urls = set(), set()
    for i in iss:
        if i["key"] in ("https",):
            continue
        (err_urls if i["severity"] == "Error" else warn_urls if i["severity"] == "Warning" else set()).update(i["urls"])
    warn_only = warn_urls - err_urls
    # = สัดส่วน URL ที่ไม่มี Error (หัก Warning เบา ๆ ไม่เกิน 10 คะแนน) — อธิบายได้ในประโยคเดียว ไม่ใช่สูตรลับ
    score = round(100 * (1 - len(err_urls) / n) - min(10, 10 * len(warn_only) / n))
    if cr.get("https_redirect") is False:
        score -= 5
    errors = sum(i["count"] for i in iss if i["severity"] == "Error")
    warnings = sum(i["count"] for i in iss if i["severity"] == "Warning")
    return {"score": max(0, min(100, score)), "errors": errors, "warnings": warnings, "urls": cr["n"], "benchmark": BENCHMARK}


# ---------- คำค้น + อันดับ ----------
def _brand_name(cr: dict, lead) -> str:
    home = next((p for p in cr.get("pages", []) if p.get("title")), None)
    t = (home or {}).get("title") or ""
    parts = [x.strip() for x in re.split(r"\s[\|\-–—·:]\s|\|", t) if x.strip()]
    stem = L.domain_of(lead["site_url"] if lead else cr["site"]).split(".")[0]
    for p in parts:
        if stem.lower() in p.lower().replace(" ", "") and len(p) <= 40:
            return p
    if parts and 3 <= len(parts[0]) <= 30:            # ส่วนแรกของ title หน้าแรกมักเป็นชื่อแบรนด์ ("ABC Shirt | รับผลิตเสื้อ")
        return parts[0]
    return stem.capitalize()


def _home_brief(cr: dict) -> str:
    rows = [p for p in cr.get("pages", []) if p.get("title")][:12]
    return "\n".join(f"- {p['title']}" + (f" | H1: {p['h1'][0]}" if p.get("h1") else "") + (f" | {p['desc'][:120]}" if p.get("desc") else "") for p in rows)


def _llm_json(prompt: str, llm=None):
    from . import ai_client
    call = llm or (lambda m: ai_client.chat_for("questions", m, max_tokens=1800, json_mode=True))
    raw = call([{"role": "user", "content": prompt}])
    m = re.search(r"[\[{].*[\]}]", raw or "", re.S)
    return json.loads(m.group(0)) if m else None


def _fallback_keywords(cr: dict, brand: str) -> list[str]:
    seen, out = set(), []
    for p in cr.get("pages", []):
        for t in ([p.get("h1")[0]] if p.get("h1") else []) + [p.get("title") or ""]:
            for part in re.split(r"[\|\-–—·:,]", t):
                k = part.strip()
                if 4 <= len(k) <= 40 and brand.lower() not in k.lower() and k.lower() not in seen:
                    seen.add(k.lower())
                    out.append(k)
    return out[:N_KEYWORDS]


def pick_keywords(cr: dict, brand: str, llm=None) -> list[str]:
    """5 คำที่ลูกค้าพิมพ์หาบริการนี้ใน Google (ไม่ใส่ชื่อแบรนด์) — ให้โมเดลอ่านจาก title/H1/description จริงของเว็บ"""
    prompt = (f"เว็บนี้คือ {cr['site']} (แบรนด์ {brand}) หน้าในเว็บ:\n{_home_brief(cr)}\n\n"
              f"ให้คำค้น Google ภาษาไทย {N_KEYWORDS} คำ ที่ลูกค้าที่กำลังจะซื้อสินค้า/บริการหลักของเว็บนี้พิมพ์หา "
              "คำละ 2–5 คำ ใช้คำที่คนพิมพ์จริง (เช่น 'รับทำแอปมือถือ', 'คลินิกจัดฟัน บางนา') ห้ามใส่ชื่อแบรนด์ ห้ามคำกว้างคำเดียว "
              "ถ้าเว็บระบุพื้นที่ให้ใส่พื้นที่ในบางคำ ตอบเป็น JSON {\"keywords\": [\"...\"]} เท่านั้น")
    try:
        d = _llm_json(prompt, llm)
        kws = [str(k).strip() for k in (d or {}).get("keywords", []) if str(k).strip()]
        kws = [k for k in kws if brand.lower() not in k.lower() and 3 <= len(k) <= 60]
        if len(kws) >= 3:
            return kws[:N_KEYWORDS]
    except Exception:
        pass
    return _fallback_keywords(cr, brand)


def _google_page(q: str, p: int) -> list[dict]:
    """ผล Google ไทยหน้า p (10 ผล): Serper ก่อน → เครดิตหมด/ล่ม ค่อยใช้ SerpApi (หน้าแรกอย่างเดียว ประหยัดโควตา)"""
    from . import geo_worker as gw, ai_serp
    err = None
    if gw.rank_backend() == "serper":
        try:
            return gw._serper_search(q, 10, page=p)
        except Exception as e:
            err = e
    if p == 1 and ai_serp.available():
        raw = ai_serp._get({"engine": "google", "q": q, "hl": "th", "gl": "th", "google_domain": "google.co.th", "num": 10})
        rows = [gw._row(x.get("title"), x.get("link"), i, x.get("snippet")) for i, x in enumerate(raw.get("organic_results") or [])][:10]
        for r in rows:
            r["via"] = "serpapi"
        return rows
    if err:
        raise err
    return gw.search(q, 10) if p == 1 else []


def ranking(apex: str, keywords: list[str], search=None) -> dict:
    """ต่อคำ: Top 10 (โดเมน) + อันดับของเรา (ไล่ถึง 20) · คู่แข่ง = โดเมนธุรกิจที่ติด Top 10 หลายคำที่สุด (ตัดแพลตฟอร์ม)"""
    from . import geo_worker as gw
    page = search or _google_page
    rows, counter, possum = [], Counter(), Counter()
    for kw in keywords:
        try:
            top = page(kw, 1)
        except Exception as e:
            rows.append({"keyword": kw, "error": type(e).__name__, "top": [], "position": None, "url": None})
            continue
        doms = [(r.get("domain") or _host(r.get("url") or "")) for r in top][:10]
        doms = [d[4:] if d.startswith("www.") else d for d in doms]
        pos, url = gw.find_position(apex, top)
        depth = 10 if any(r.get("via") == "serpapi" for r in top) else 20
        if not pos and depth == 20:
            try:
                more = page(kw, 2)
                for r in more:
                    r["position"] = (r.get("position") or 0) + len(top)
                pos, url = gw.find_position(apex, more)
            except Exception:
                pass
        for i, d in enumerate(doms):
            if d and not gw.domain_matches(apex, d) and d not in doms[:i]:
                counter[d] += 1
                possum[d] += i + 1
        rows.append({"keyword": kw, "top": doms, "position": pos, "url": url, "depth": depth})
    ranked = sorted(counter.items(), key=lambda x: (-x[1], possum[x[0]] / x[1], x[0]))          # ติดหลายคำก่อน · เสมอกันดูอันดับเฉลี่ย
    comps = [(d, n) for d, n in ranked if d not in PLATFORMS and not any(d.endswith("." + p) for p in PLATFORMS)]
    return {"rows": rows, "competitors": [{"domain": d, "hits": n} for d, n in comps[:5]], "depth": 20,
            "page1": sum(1 for r in rows if r.get("position") and r["position"] <= 10), "n": len(rows),
            "ok": sum(1 for r in rows if not r.get("error"))}


# ---------- ชั้น 2: 20 คำถาม AI ----------
def _fallback_prompts(keywords: list[str]) -> list[str]:
    tpl = ["{k} ที่ไหนดี", "แนะนำ {k} ที่น่าเชื่อถือ", "{k} ราคาเท่าไหร่", "รีวิว {k} ที่ลูกค้าพอใจ"]
    out = [t.format(k=k) for k in keywords for t in tpl]
    return out[:N_PROMPTS]


def make_prompts(cr: dict, brand: str, keywords: list[str], llm=None) -> list[str]:
    prompt = (f"ธุรกิจนี้คือ {cr['site']} หน้าในเว็บ:\n{_home_brief(cr)}\nคำค้นหลัก: {', '.join(keywords)}\n\n"
              f"เขียนคำถาม {N_PROMPTS} ข้อ ที่ลูกค้าคนไทยพิมพ์ถาม ChatGPT ตอนกำลังหาผู้ให้บริการแบบนี้ "
              "คละเจตนา: หาเจ้าที่ดี/แนะนำ, ถามราคา, เทียบ, เช็กความน่าเชื่อถือ, ถามวิธีเลือก · ห้ามใส่ชื่อแบรนด์ใด ๆ · "
              "ภาษาพูดแบบคนพิมพ์จริง ตอบ JSON {\"prompts\": [\"...\"]} เท่านั้น")
    try:
        d = _llm_json(prompt, llm)
        ps = [str(p).strip() for p in (d or {}).get("prompts", []) if str(p).strip()]
        ps = [p for p in ps if brand.lower() not in p.lower()]
        if len(ps) >= 10:
            return ps[:N_PROMPTS]
    except Exception:
        pass
    return _fallback_prompts(keywords)


def _engine() -> str | None:
    from . import ai_visibility as av
    eng = av.available_engines()
    return "chatgpt" if "chatgpt" in eng else (eng[0] if eng else None)


def ask_prompts(prompts: list[str], apex: str, brand: str, competitors: list[str], ask=None, engine: str | None = None) -> dict:
    from . import ai_visibility as av
    engine = engine or _engine()
    ask = ask or (lambda q: av.ask(engine, q))
    rows, cost, cites = [], 0.0, Counter()
    our_urls, comp_urls = Counter(), defaultdict(Counter)
    comp_names = {c: c.split(".")[0] for c in competitors}
    with cf.ThreadPoolExecutor(ASK_PARALLEL) as ex:
        answers = list(ex.map(ask, prompts))              # ลำดับคงเดิม
    for q, r in zip(prompts, answers):
        if not r.get("ok"):
            rows.append({"prompt": q, "error": (r.get("reason") or "")[:120]})
            continue
        cost += float(r.get("cost_usd") or 0)
        j = av.judge_mention(r.get("text", ""), r.get("citations", []), apex, brand)
        hosts = [_host(u) for u in r.get("citations", []) if u]
        for h in set(hosts):
            if h:
                cites[h] += 1
        for u in r.get("citations", []):
            h = _host(u)
            if h == apex:
                our_urls[u.split("#")[0]] += 1
            elif h in comp_names:
                comp_urls[h][u.split("#")[0]] += 1
        body = (r.get("text") or "").lower()
        comp = {c: ("cited" if c in hosts else "named" if (c in body or (len(n) >= 4 and n in body)) else "") for c, n in comp_names.items()}
        rows.append({"prompt": q, "status": j["status"], "cited": j["cited"], "named": j["named"], "competitors": comp, "sources": sorted(set(h for h in hosts if h))[:6]})
    done = [r for r in rows if "status" in r]
    return {"engine": engine, "rows": rows, "asked": len(done), "mentioned": sum(1 for r in done if r["cited"] or r["named"]),
            "cited": sum(1 for r in done if r["cited"]), "competitors": competitors,
            "comp_mentions": {c: sum(1 for r in done if r["competitors"].get(c)) for c in competitors},
            "top_sources": [{"domain": d, "n": n} for d, n in cites.most_common(8)],
            "our_urls": [{"url": u, "n": n} for u, n in our_urls.most_common(5)],
            "comp_urls": {c: [{"url": u, "n": n} for u, n in comp_urls[c].most_common(3)] for c in competitors if comp_urls.get(c)},
            "cost_usd": round(cost, 4)}


# ---------- เก็บ/สถานะ ----------
def _get(lead, col: str) -> dict:
    try:
        return json.loads(lead[col] or "{}") if col in lead.keys() and lead[col] else {}
    except Exception:
        return {}


def tier1(lead) -> dict:
    return _get(lead, "deep_json")


def tier2(lead) -> dict:
    return _get(lead, "deep_ai_json")


def _running(d: dict) -> bool:
    if d.get("state") != "running":
        return False
    try:
        return (dt.datetime.now() - dt.datetime.fromisoformat(d.get("started_at", ""))).total_seconds() < STALE_MIN * 60
    except Exception:
        return False


def _save(lead_id: int, col: str, d: dict) -> None:
    db.update_lead(lead_id, **{col: json.dumps(d, ensure_ascii=False)})


def ai_today() -> int:
    try:
        d = json.loads(db.get_setting("deep_ai_count") or "{}")
    except Exception:
        d = {}
    return int(d.get("n", 0)) if d.get("day") == office.today_bkk() else 0


def _ai_count_up() -> None:
    db.set_setting("deep_ai_count", json.dumps({"day": office.today_bkk(), "n": ai_today() + 1}))


def ai_daily() -> int:
    try:
        return max(0, int(db.get_setting("deep_ai_daily") or AI_DAILY_DEFAULT))
    except Exception:
        return AI_DAILY_DEFAULT


def run_tier1(lead_id: int, search=None, llm=None, client=None) -> dict:
    search, llm = search or HOOKS["search"], llm or HOOKS["llm"]
    client = client or (HOOKS["client"]() if HOOKS["client"] else None)
    lead = db.get_lead(lead_id)
    site = lead["site_url"]
    _save(lead_id, "deep_json", {"state": "running", "started_at": db.now(), "step": "ไต่เว็บ"})
    try:
        with office.working("cto", f"ไต่เว็บ {lead['domain']} (รายงานเต็ม)"):
            cr = crawl(site, client=client)
            iss = issues(cr)
            hl = health(cr, iss)
            brand = _brand_name(cr, lead)
            kws = pick_keywords(cr, brand, llm=llm)
            rk = ranking(cr["apex"], kws, search=search) if kws else {"rows": [], "competitors": [], "page1": 0, "n": 0, "depth": 20}
        slim = [{k: p.get(k) for k in ("url", "status", "title", "h1", "desc", "inbound", "no_alt", "found_on")} for p in cr["pages"]]
        d = {"state": "done", "at": db.now(), "brand": brand, "crawl": {k: cr[k] for k in ("n", "queued", "blocked", "truncated", "https_redirect", "seconds", "at")},
             "pages": slim, "issues": iss, "health": hl, "keywords": kws, "ranking": rk}
        _save(lead_id, "deep_json", d)
        db.add_lead_event(lead_id, "deep_audit", f"ไต่ {cr['n']} หน้า · health {hl['score']}/100 · error {hl['errors']} · ติดหน้าแรก {rk['page1']}/{rk['n']} คำ", "cto")
        return d
    except Exception as e:
        d = {"state": "error", "at": db.now(), "error": f"{type(e).__name__}: {str(e)[:160]}"}
        _save(lead_id, "deep_json", d)
        return d


def run_ai(lead_id: int, force: bool = False, ask=None, llm=None, engine: str | None = None) -> dict:
    ask, llm, engine = ask or HOOKS["ask"], llm or HOOKS["llm"], engine or HOOKS["engine"]
    lead = db.get_lead(lead_id)
    t1 = tier1(lead)
    if t1.get("state") != "done":
        return {"state": "skipped", "reason": "ต้องมีรายงานชั้นแรกก่อน"}
    if not force and ai_today() >= ai_daily():
        d = {"state": "queued", "at": db.now(), "reason": f"ครบเพดานวันนี้ {ai_daily()} ราย — ทีมจะวัดให้ภายใน 1 วันทำการ"}
        _save(lead_id, "deep_ai_json", d)
        return d
    engine = engine or _engine()
    if not engine and not ask:
        d = {"state": "error", "at": db.now(), "error": "ยังไม่มีคีย์ AI ที่ถามได้"}
        _save(lead_id, "deep_ai_json", d)
        return d
    _save(lead_id, "deep_ai_json", {"state": "running", "started_at": db.now()})
    try:
        with office.working("cto", f"ถาม AI 20 คำถามเรื่อง {lead['domain']}"):
            comps = [c["domain"] for c in t1["ranking"].get("competitors", [])][:3]
            prompts = make_prompts({"site": lead["site_url"], "pages": t1.get("pages", [])}, t1["brand"], t1["keywords"], llm=llm)
            res = ask_prompts(prompts, L.domain_of(lead["site_url"]), t1["brand"], comps, ask=ask, engine=engine)
        _ai_count_up()
        d = {"state": "done", "at": db.now(), **res}
        _save(lead_id, "deep_ai_json", d)
        db.add_lead_event(lead_id, "ai_check", f"{res['engine']}: ถูกเอ่ยถึง {res['mentioned']}/{res['asked']} · ถูกอ้างอิง {res['cited']} · ค่าใช้จ่าย ${res['cost_usd']}", "cto")
        return d
    except Exception as e:
        d = {"state": "error", "at": db.now(), "error": f"{type(e).__name__}: {str(e)[:160]}"}
        _save(lead_id, "deep_ai_json", d)
        return d


def _bg(fn) -> None:
    if RUN_INLINE:
        fn()
        return

    def runner():
        with _SEM:
            fn()
    threading.Thread(target=runner, daemon=False).start()


def start(lead_id: int, ai: bool = False, force_ai: bool = False) -> str:
    """เริ่มรายงานเต็มเบื้องหลัง (ถ้ายังไม่มีหรือเก่าเกิน 7 วัน) · ai=True ต่อชั้น 2 ด้วย · คืนสถานะสั้น ๆ"""
    if not enabled():
        return "disabled"
    lead = db.get_lead(lead_id)
    if not lead or not lead["site_url"]:
        return "no-lead"
    t1, t2 = tier1(lead), tier2(lead)
    rk = t1.get("ranking") or {}
    rank_failed = bool(rk.get("n")) and rk.get("ok", rk["n"]) == 0
    fresh = t1.get("state") == "done" and not rank_failed and (t1.get("at") or "") >= (dt.datetime.now() - dt.timedelta(days=7)).isoformat(timespec="seconds")
    need1 = not fresh and not _running(t1)
    need2 = ai and not _running(t2) and (force_ai or t2.get("state") not in ("done",))
    if not (need1 or need2):
        return "running" if (_running(t1) or _running(t2)) else "done"
    if need2:
        _save(lead_id, "deep_ai_json", {"state": "running", "started_at": db.now()})

    def job():
        if need1:
            run_tier1(lead_id)
        if need2:
            run_ai(lead_id, force=force_ai)
    _bg(job)
    return "started"


def progress(lead) -> dict:
    t1, t2 = tier1(lead), tier2(lead)
    st = lambda d: "running" if _running(d) else (d.get("state") or "none")
    return {"tier1": st(t1), "ai": st(t2), "step": t1.get("step")}


def highlights(t1: dict, t2: dict) -> list[dict]:
    """สรุปสำคัญ 4 บรรทัด — เขียนจากตัวเลขจริงเท่านั้น"""
    out = []
    rk = t1.get("ranking") or {}
    if rk.get("n") and rk.get("ok", rk["n"]) == 0:
        out.append({"k": "Keyword", "v": "ค้นอันดับ Google ไม่ได้ชั่วคราว — ระบบจะลองใหม่เมื่อเปิดหน้านี้อีกครั้ง"})
    elif rk.get("n"):
        if rk["page1"]:
            out.append({"k": "Keyword", "v": f"ติดหน้าแรก {rk['page1']} จาก {rk['n']} คำ"})
        else:
            depth = min([r.get("depth") or 20 for r in rk.get("rows", []) if not r.get("error")] or [20])
            out.append({"k": "Keyword", "v": f"ทั้ง {rk['n']} คำยังไม่ติด {depth} อันดับแรก — ยังไม่มีหน้าที่ตอบคำเหล่านี้ตรง ๆ"})
    if rk.get("competitors"):
        c = rk["competitors"][0]
        out.append({"k": "คู่แข่ง", "v": f"{c['domain']} ติด Top 10 ใน {c['hits']} จาก {rk['n']} คำ"})
    if t2.get("state") == "done":
        out.append({"k": "AI", "v": f"{ENGINE_LABEL.get(t2.get('engine'), 'AI')} เอ่ยถึงแบรนด์คุณ {t2['mentioned']} จาก {t2['asked']} คำถาม · อ้างอิงเว็บคุณ {t2['cited']} ครั้ง"})
    hl = t1.get("health") or {}
    if hl:
        n_err = sum(1 for i in t1.get("issues", []) if i["severity"] == "Error")
        out.append({"k": "Site Audit", "v": f"ได้ {hl['score']}/100 ({'สูงกว่า' if hl['score'] >= BENCHMARK else 'ต่ำกว่า'} Benchmark {BENCHMARK}) · {n_err} ปัญหาระดับ Error จาก {hl['urls']} หน้า"})
    return out
