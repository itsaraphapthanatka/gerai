"""ตรวจสุขภาพเว็บลูกค้า — จับปัญหาที่ทำให้ GEO ไม่ได้ผลทั้งที่คอนเทนต์พร้อมแล้ว

ปัญหาที่เจอจริงและเป็นที่มาของแต่ละเช็ค:
  noindex        เว็บ WordPress ตั้ง "ห้ามเสิร์ชเอนจินทำดัชนี" ไว้ → ทุกหน้าติด noindex 2 ปี
  soft 404       SPA ตอบ 200 + หน้าแรกให้ทุก path ที่ไม่มีจริง → Google เก็บหน้าขยะไม่จำกัด
  page_identity  try_files ไม่มี {path}.html → ทุก URL ได้ HTML ของหน้าแรก
  canonical      root route ใส่ canonical ตายตัว → ทุกหน้าบอกว่าตัวเองซ้ำหน้าแรก
  www            เสิร์ฟทั้ง www และ apex โดยไม่ redirect → Google นับเป็นสองเว็บ
  llms/sitemap   ลูกค้าย้ายเว็บ ลิงก์เก่าใน DB ตายหมดแต่ไม่มีใครรู้ 2 เดือน
  freshness      connector ตายเงียบ คอนเทนต์ค้างเป็นร่าง ไม่มีอะไรเผยแพร่

ฟังก์ชัน judge_* รับข้อมูลที่ดึงมาแล้ว ไม่ยิงเน็ตเอง — เทสต์ได้โดยไม่ต้องมีเว็บจริง
(ตัวตรวจที่ฟ้องผิดอันตรายกว่าไม่มีตัวตรวจ เพราะคนจะเลิกเชื่อแล้วเมินไฟแดงทั้งหมด)
"""
from __future__ import annotations
import re
import json
import datetime
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
AI_BOTS = ("GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "PerplexityBot", "Google-Extended")
MAX_URL_PROBES = 12          # จำกัดจำนวน URL ที่ยิงเช็คต่อรอบ กันรันนานเกินไป
STALE_DAYS = 30              # ไม่มีคอนเทนต์ใหม่เกินนี้ = เตือน

OK, FAIL, WARN, SKIP = "ok", "fail", "warn", "skip"


def _c(key, label, status, detail=""):
    return {"key": key, "label": label, "status": status, "detail": detail}


def host_of(url: str) -> str:
    """host เปล่า ๆ สำหรับเทียบโดเมน — ต้องตัด path ออกก่อน ไม่งั้นทุก URL ที่มี path
    จะถูกตัดสินว่าอยู่นอกโดเมน (บั๊กนี้เคยทำให้ตัวตรวจฟ้องผิดทั้ง 5 แบรนด์)"""
    s = (url or "").strip()
    s = re.sub(r"^[a-z][a-z0-9+.\-]*://", "", s, flags=re.I)
    s = s.split("/")[0].split("?")[0].split("#")[0].split("@")[-1].split(":")[0].lower()
    return s[4:] if s.startswith("www.") else s.strip(".")


def _title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.S | re.I)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def _canonical(html: str):
    m = re.search(r"""(?i)<link[^>]+rel=["']canonical["'][^>]*href=["']([^"']*)""", html or "")
    return m.group(1) if m else None


def _noindex_reason(headers: dict, html: str):
    xrt = ""
    for k, v in (headers or {}).items():
        if k.lower() == "x-robots-tag":
            xrt = str(v)
    if "noindex" in xrt.lower():
        return f"X-Robots-Tag: {xrt.strip()}"
    for m in re.findall(r"""(?i)<meta[^>]+name=["']robots["'][^>]*content=["']([^"']*)""", html or ""):
        if "noindex" in m.lower():
            return f"meta robots: {m.strip()}"
    return None


# ---------- judge: รับข้อมูลที่ดึงมาแล้ว ----------
def judge_home(status: int, headers: dict, html: str) -> list:
    if status != 200:
        return [_c("home", "หน้าแรกเข้าถึงได้", FAIL, f"ตอบ {status}"),
                _c("noindex", "ไม่ถูกสั่งห้าม index", SKIP, "ข้ามเพราะหน้าแรกเข้าไม่ได้")]
    out = [_c("home", "หน้าแรกเข้าถึงได้", OK)]
    reason = _noindex_reason(headers, html)
    out.append(_c("noindex", "ไม่ถูกสั่งห้าม index", FAIL, reason) if reason
               else _c("noindex", "ไม่ถูกสั่งห้าม index", OK))
    return out


def judge_soft404(status: int) -> list:
    if status == 200:
        return [_c("soft404", "path ที่ไม่มีจริงตอบ 404", FAIL,
                   "ตอบ 200 — Google เก็บหน้าที่ไม่มีอยู่จริงเข้า index ได้ไม่จำกัด")]
    if status == 404:
        return [_c("soft404", "path ที่ไม่มีจริงตอบ 404", OK)]
    return [_c("soft404", "path ที่ไม่มีจริงตอบ 404", WARN, f"ตอบ {status}")]


def judge_www(status, redirect_to: str, apex: str) -> list:
    if status in (0, None) or status >= 500:
        return [_c("www", "www เด้งไป apex", SKIP, "www ไม่มี DNS หรือเข้าไม่ได้")]
    if status == 200:
        return [_c("www", "www เด้งไป apex", FAIL,
                   "www เสิร์ฟเนื้อหาเองโดยไม่ redirect — Google นับเป็นสองเว็บ สัญญาณอันดับถูกแบ่ง")]
    if 300 <= status < 400:
        if host_of(redirect_to) == apex:
            return [_c("www", "www เด้งไป apex", OK)]
        return [_c("www", "www เด้งไป apex", WARN, f"เด้งไป {redirect_to}")]
    return [_c("www", "www เด้งไป apex", SKIP, f"ตอบ {status}")]


def judge_robots(status: int, text: str) -> list:
    if status != 200:
        return [_c("robots", "robots.txt", FAIL, f"ตอบ {status}")]
    if "<html" in (text or "")[:300].lower():
        return [_c("robots", "robots.txt", FAIL, "คืน HTML ไม่ใช่ข้อความ — ไม่มีไฟล์จริง")]
    out = []
    blocked = [b for b in AI_BOTS
               if re.search(rf"(?is)user-agent:\s*{re.escape(b)}\s*\n\s*disallow:\s*/\s*(?:\n|$)", text or "")]
    out.append(_c("ai_bots", "เปิดทางบอท AI", FAIL, "ถูกบล็อก: " + ", ".join(blocked)) if blocked
               else _c("ai_bots", "เปิดทางบอท AI", OK))
    if re.search(r"ใส่ URL sitemap", text or ""):
        out.append(_c("robots_sitemap", "robots.txt ชี้ sitemap", FAIL,
                      "ยังเป็นข้อความตัวอย่างที่ไม่ได้แก้"))
    elif re.search(r"(?im)^sitemap:", text or ""):
        out.append(_c("robots_sitemap", "robots.txt ชี้ sitemap", OK))
    else:
        out.append(_c("robots_sitemap", "robots.txt ชี้ sitemap", WARN, "ไม่มีบรรทัด Sitemap"))
    return out


def judge_links(kind: str, label: str, status: int, urls: list, probe: dict) -> list:
    """probe = {url: http_code} ของ URL ที่ยิงเช็คไปจริง"""
    if status != 200:
        return [_c(kind, label, FAIL, f"ตอบ {status}")]
    if not urls:
        return [_c(kind, label, WARN, "ไม่มีลิงก์ข้างใน")]
    dead = sorted(u for u, c in probe.items() if c != 200)
    if dead:
        ex = ", ".join(u.split("/", 3)[-1][:34] for u in dead[:2])
        return [_c(kind, label, FAIL, f"ลิงก์เสีย {len(dead)}/{len(probe)} เช่น {ex}")]
    return [_c(kind, label, OK, f"{len(urls)} ลิงก์" + (f" (สุ่มเช็ค {len(probe)})" if len(probe) < len(urls) else ""))]


def judge_sitemap_domain(locs: list, apex: str) -> list:
    off = [u for u in locs if host_of(u) != apex]
    if off:
        return [_c("sitemap_domain", "URL ใน sitemap อยู่ใต้โดเมนเดียวกัน", FAIL,
                   f"{len(off)} URL อยู่นอกโดเมน — Google ตีตกทั้งไฟล์ เช่น {off[0][:56]}")]
    return [_c("sitemap_domain", "URL ใน sitemap อยู่ใต้โดเมนเดียวกัน", OK)]


def judge_pages(home_title: str, pages: list) -> list:
    """pages = [{'url':..,'title':..,'canonical':..}] ของบทความที่สุ่มมา"""
    if not pages:
        return [_c("page_identity", "แต่ละหน้าส่งเนื้อหาของตัวเอง", SKIP, "ไม่มีหน้าให้ตรวจ"),
                _c("canonical", "canonical ชี้ที่ตัวเอง", SKIP, "")]
    out = []
    titles = [p["title"] for p in pages if p["title"]]
    if len(pages) > 1 and titles and len(set(titles)) == 1 and titles[0] == home_title:
        out.append(_c("page_identity", "แต่ละหน้าส่งเนื้อหาของตัวเอง", FAIL,
                      "ทุกหน้าส่ง title เดียวกับหน้าแรก — เสิร์ฟหน้าผิด crawler เห็นเนื้อหาหน้าแรกทุก URL"))
    else:
        out.append(_c("page_identity", "แต่ละหน้าส่งเนื้อหาของตัวเอง", OK))
    bad = [p for p in pages if p["canonical"] and p["canonical"].rstrip("/") != p["url"].rstrip("/")]
    if bad:
        p = bad[0]
        out.append(_c("canonical", "canonical ชี้ที่ตัวเอง", FAIL,
                      f"{p['url'].split('/',3)[-1][:30]} ชี้ไป {p['canonical'][:52]}"))
    else:
        out.append(_c("canonical", "canonical ชี้ที่ตัวเอง", OK))
    return out


def judge_indexed(site_hits, page_hits, sample_url: str, n_published: int) -> list:
    """Google เก็บเว็บ/หน้า GEO เข้า index หรือยัง — hits=None แปลว่าเช็คไม่ได้

    เป็นเช็คที่สำคัญที่สุดแต่คนมองข้ามบ่อยสุด: เขียนคอนเทนต์ 17 ชิ้นแล้ว Google
    ไม่เคยเห็นสักหน้า การเพิ่มคอนเทนต์ตอนนั้นคือเพิ่มของที่มองไม่เห็นให้มากขึ้น
    """
    if site_hits is None:
        return [_c("indexed", "Google เก็บเข้า index แล้ว", SKIP,
                   "ต้องตั้ง Serper API key ถึงจะเช็คได้")]
    if site_hits == 0:
        return [_c("indexed", "Google เก็บเข้า index แล้ว", FAIL,
                   "ทั้งเว็บยังไม่อยู่ใน Google — ส่ง sitemap เข้า Search Console "
                   "และยืนยันความเป็นเจ้าของก่อน ไม่งั้นคอนเทนต์ที่เขียนไม่มีใครเห็น")]
    if not n_published:
        return [_c("indexed", "Google เก็บเข้า index แล้ว", OK, "เว็บอยู่ใน index (ยังไม่มีคอนเทนต์ให้ตรวจ)")]
    if not sample_url:
        # ไม่มี sitemap จึงไม่รู้ว่าจะตรวจหน้าไหน — อย่าตอบ ok ลอย ๆ เพราะแบรนด์แบบนี้
        # มักเป็นกลุ่มที่แย่ที่สุด (ไม่มีทางให้ Google เจอคอนเทนต์เลย)
        return [_c("indexed", "Google เก็บเข้า index แล้ว", WARN,
                   "เว็บอยู่ใน index แต่ตรวจหน้าคอนเทนต์ไม่ได้ เพราะยังไม่มี sitemap")]
    if page_hits == 0:
        return [_c("indexed", "Google เก็บเข้า index แล้ว", FAIL,
                   f"เว็บอยู่ใน index แต่หน้าคอนเทนต์ยังไม่ถูกเก็บ (เช็คจาก {sample_url[:48]}) "
                   "— ส่ง geo-sitemap.xml เข้า Search Console")]
    if page_hits is None:
        return [_c("indexed", "Google เก็บเข้า index แล้ว", OK, "เว็บอยู่ใน index")]
    return [_c("indexed", "Google เก็บเข้า index แล้ว", OK, "ทั้งเว็บและหน้าคอนเทนต์อยู่ใน index")]


def judge_freshness(last_published: str, n_published: int, today: datetime.date) -> list:
    if not n_published:
        return [_c("freshness", "มีคอนเทนต์เผยแพร่ต่อเนื่อง", FAIL, "ยังไม่เคยเผยแพร่สักชิ้น")]
    if not last_published:
        return [_c("freshness", "มีคอนเทนต์เผยแพร่ต่อเนื่อง", WARN, f"{n_published} ชิ้น (ไม่ทราบวันที่)")]
    try:
        d = datetime.date.fromisoformat(str(last_published)[:10])
    except Exception:
        return [_c("freshness", "มีคอนเทนต์เผยแพร่ต่อเนื่อง", SKIP, "")]
    days = (today - d).days
    msg = f"ล่าสุด {d} ({days} วัน) · {n_published} ชิ้น"
    if days > STALE_DAYS:
        return [_c("freshness", "มีคอนเทนต์เผยแพร่ต่อเนื่อง", FAIL,
                   msg + " — GEO ต้องเติมคอนเทนต์ต่อเนื่อง ปล่อยนิ่ง SoV ไม่ขยับ")]
    return [_c("freshness", "มีคอนเทนต์เผยแพร่ต่อเนื่อง", OK, msg)]


def summarize(checks: list) -> dict:
    fails = [c for c in checks if c["status"] == FAIL]
    warns = [c for c in checks if c["status"] == WARN]
    return {"ok": not fails, "n_fail": len(fails), "n_warn": len(warns),
            "n_ok": len([c for c in checks if c["status"] == OK]), "checks": checks}


# ---------- ดึงข้อมูลจริงแล้วเรียก judge ----------
def _client():
    import httpx
    return httpx.Client(follow_redirects=True, timeout=20,
                        headers={"User-Agent": UA}, verify=True)


def run_checks(site_url: str, last_published=None, n_published: int = 0, today=None) -> dict:
    """ตรวจเว็บ 1 แบรนด์ — คืน dict พร้อมเก็บลง DB / แสดงผล"""
    import httpx
    today = today or datetime.date.today()
    apex = host_of(site_url)
    base = f"https://{apex}"
    checks: list = []

    def fetch(url, redirects=True):
        try:
            with httpx.Client(follow_redirects=redirects, timeout=20,
                              headers={"User-Agent": UA}) as c:
                r = c.get(url)
                return r.status_code, dict(r.headers), r.text
        except Exception:
            return 0, {}, ""

    def code(url):
        try:
            with httpx.Client(follow_redirects=True, timeout=20, headers={"User-Agent": UA}) as c:
                return c.get(url).status_code
        except Exception:
            return 0

    st, hd, home = fetch(base + "/")
    checks += judge_home(st, hd, home)
    home_title = _title(home)

    checks += judge_soft404(code(f"{base}/zz-health-check-not-a-real-path"))

    try:
        with httpx.Client(follow_redirects=False, timeout=20, headers={"User-Agent": UA}) as c:
            r = c.get(f"https://www.{apex}/")
            checks += judge_www(r.status_code, r.headers.get("location", ""), apex)
    except Exception:
        checks += judge_www(0, "", apex)

    rst, _, rtxt = fetch(base + "/robots.txt")
    checks += judge_robots(rst, rtxt)

    lst, _, ltxt = fetch(base + "/llms.txt")
    llinks = re.findall(r"https?://\S+", ltxt or "")[1:]
    lprobe = {u: code(u) for u in llinks[:MAX_URL_PROBES]}
    checks += judge_links("llms", "llms.txt", lst, llinks, lprobe)

    sst, _, sxml = fetch(base + "/geo-sitemap.xml")
    locs = []
    if sst == 200:
        try:
            locs = [e.text for e in ET.fromstring(sxml).iter(SITEMAP_NS + "loc") if e.text]
        except Exception:
            checks.append(_c("sitemap", "geo-sitemap.xml", FAIL, "ไฟล์ไม่ใช่ XML ที่อ่านได้"))
    sprobe = {u: code(u) for u in locs[:MAX_URL_PROBES]}
    if not (sst == 200 and not locs and any(c["key"] == "sitemap" for c in checks)):
        checks += judge_links("sitemap", "geo-sitemap.xml", sst, locs, sprobe)
    if locs:
        checks += judge_sitemap_domain(locs, apex)

    pages = []
    for u in [x for x in locs if x.rstrip("/") != base][:3]:
        _, _, h = fetch(u)
        pages.append({"url": u, "title": _title(h), "canonical": _canonical(h)})
    checks += judge_pages(home_title, pages)

    # index: ใช้ Serper (มีค่าใช้จ่าย) — ไม่มีคีย์ก็ข้าม ไม่ทำให้ตก
    site_hits = page_hits = None
    sample = next((u for u in locs if u.rstrip("/") != base), "")
    try:
        from . import geo_worker
        if geo_worker.rank_backend() == "serper":
            # site: ครอบ subdomain ด้วย — ต้องกรองให้เหลือเฉพาะโดเมนลูกค้า
            # ไม่งั้น geo.appreview.cloud (แพลตฟอร์มเราเอง) จะถูกนับเป็นเว็บลูกค้า
            hits = geo_worker._serper_search(f"site:{apex}", 10)
            site_hits = len([h for h in hits if host_of(h.get("url") or "") == apex])
            if sample:
                ph = geo_worker._serper_search(f"site:{sample}", 10)
                page_hits = len([h for h in ph if (h.get("url") or "").rstrip("/") == sample.rstrip("/")])
    except Exception:
        site_hits = page_hits = None
    checks += judge_indexed(site_hits, page_hits, sample, n_published)

    checks += judge_freshness(last_published, n_published, today)

    res = summarize(checks)
    res["checked_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    res["site"] = base
    return res
