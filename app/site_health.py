"""ตรวจสุขภาพเว็บลูกค้า — จับปัญหาที่ทำให้ GEO ไม่ได้ผลทั้งที่คอนเทนต์พร้อมแล้ว

ปัญหาที่เจอจริงและเป็นที่มาของแต่ละเช็ค:
  noindex        เว็บ WordPress ตั้ง "ห้ามเสิร์ชเอนจินทำดัชนี" ไว้ → ทุกหน้าติด noindex 2 ปี
  soft 404       SPA ตอบ 200 + หน้าแรกให้ทุก path ที่ไม่มีจริง → Google เก็บหน้าขยะไม่จำกัด
  page_identity  try_files ไม่มี {path}.html → ทุก URL ได้ HTML ของหน้าแรก
  canonical      root route ใส่ canonical ตายตัว → ทุกหน้าบอกว่าตัวเองซ้ำหน้าแรก
  www            เสิร์ฟทั้ง www และ apex โดยไม่ redirect → Google นับเป็นสองเว็บ
  llms/sitemap   ลูกค้าย้ายเว็บ ลิงก์เก่าใน DB ตายหมดแต่ไม่มีใครรู้ 2 เดือน
  freshness      connector ตายเงียบ คอนเทนต์ค้างเป็นร่าง ไม่มีอะไรเผยแพร่
  csr            เว็บ React/Vite ส่ง HTML เปล่า <div id="root"></div> → บอท AI ไม่รัน JS เห็นเว็บว่าง
  connector      ตั้ง redirect แทน rewrite → geo-sitemap.xml เด้งข้ามโดเมน Google ตีตก ทั้งที่เช็คเดิมผ่าน
  wp             ปลั๊กอิน/REST ของ WordPress ตาย → สั่งเผยแพร่แล้วไปไม่ถึงเว็บ กว่าจะรู้คอนเทนต์ค้างเป็นเดือน

ฟังก์ชัน judge_* รับข้อมูลที่ดึงมาแล้ว ไม่ยิงเน็ตเอง — เทสต์ได้โดยไม่ต้องมีเว็บจริง
(ตัวตรวจที่ฟ้องผิดอันตรายกว่าไม่มีตัวตรวจ เพราะคนจะเลิกเชื่อแล้วเมินไฟแดงทั้งหมด)
"""
from __future__ import annotations
import re
import html as _html
import json
import datetime
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
AI_BOTS = ("GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "PerplexityBot", "Google-Extended")
MAX_URL_PROBES = 12          # จำกัดจำนวน URL ที่ยิงเช็คต่อรอบ กันรันนานเกินไป
STALE_DAYS = 30              # ไม่มีคอนเทนต์ใหม่เกินนี้ = เตือน
MAX_CRAWL = 5                # ไต่ลิงก์จากหน้าแรกกี่หน้า เพื่อหา orphan

# csr: HTML ที่เสิร์ฟมาครั้งแรก (ยังไม่รัน JavaScript) ต้องมีเนื้อหาให้บอทอ่าน — บอท AI
# (GPTBot, ClaudeBot, PerplexityBot) ไม่รัน JS เว็บ React/Vite ที่ส่ง <div id="root"></div>
# เปล่า ๆ จึงเป็นเว็บว่างในสายตา AI ทั้งที่คนเปิดดูเห็นครบ (hop-property และ tanawat-lawyer
# ส่งข้อความมา 32 และ 37 ตัวอักษร ขณะที่เว็บปกติส่งมา 4,000–13,000)
CSR_MIN_TEXT = 300           # ตัวอักษรที่มองเห็นต่ำกว่านี้ = แทบไม่มีเนื้อหา
CSR_THIN_TEXT = 800          # ต่ำกว่านี้ + มีกรอบ mount เปล่า = มีเนื้อหาสำรองแค่บางส่วน
MOUNT_IDS = ("root", "app", "__next", "__nuxt", "___gatsby", "q-app", "svelte")
CONNECTOR_PATHS = ("/geo", "/llms.txt", "/geo-sitemap.xml")   # path ที่เว็บลูกค้าต้อง rewrite มาที่ platform

# uptime: เช็คเบา ๆ รายวัน ว่าเว็บยังเปิดได้ไหม — แยกจาก run_checks เพราะตัวเต็มยิงเน็ต
# หลายสิบครั้งต่อแบรนด์ รันรายวันไม่ไหว และเวลาเว็บล่มจริง ผลตรวจเต็มจะฟ้องตกหลายข้อ
# จนอ่านไม่ออกว่าสาเหตุคืออะไร (appreview.cloud ล่ม ก.ย. 2026 ขึ้นว่า "ไม่ผ่าน 4 ข้อ"
# ทั้งที่ปัญหาจริงมีข้อเดียว คือ DNS ของโดเมนหลักหาย)
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
UPTIME_RETRY_WAIT = 5        # วินาที — ลองซ้ำก่อนประกาศว่าล่ม กัน network blip แจ้งเตือนผิด

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


def links_in(html: str, base: str, apex: str) -> set:
    """URL ภายในโดเมนทั้งหมดที่ปรากฏใน href ของหน้านี้ (ตัด fragment/query ออก)"""
    out = set()
    for href in re.findall(r"""(?i)<a[^>]+href=["']([^"'#]+)""", html or ""):
        href = href.strip()
        if href.startswith("//"):
            href = "https:" + href
        if href.startswith("/"):
            href = base.rstrip("/") + href
        elif not href.lower().startswith("http"):
            continue
        if host_of(href) != apex:
            continue
        out.add(href.split("?")[0].rstrip("/"))
    return out


def crawl_order(links: set, content: set) -> list:
    """เรียงว่าควรไต่หน้าไหนก่อน — หน้ารวมคอนเทนต์มักอยู่ที่ path ต้นทางเดียวกับคอนเทนต์
    (/geo เป็นต้นทางของ /geo/102) จึงเอาหน้าพวกนั้นขึ้นก่อน

    เรียงตามตัวอักษรเฉย ๆ ไม่พอ เพราะเราไต่ได้จำกัดจำนวนหน้า ถ้าเว็บมีลิงก์อื่นที่ชื่อ
    มาก่อน (/area/... มาก่อน /geo) หน้ารวมบทความจะไม่ถูกไต่เลย แล้วฟ้องว่าเป็นหน้ากำพร้า
    ทั้งที่เว็บลิงก์ไว้ถูกแล้ว — เกิดจริงกับ petgo.asia หลังเพิ่งเพิ่มลิงก์เข้าเมนู"""
    return sorted(links, key=lambda u: (not any(c.startswith(u + "/") for c in content), u))


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


def judge_orphan(reachable: int, total: int, pages_crawled: int) -> list:
    """หน้าคอนเทนต์มีลิงก์จากเว็บไหม — sitemap บอก Google ว่าหน้ามีอยู่ แต่ลิงก์บอกว่าหน้าสำคัญ

    JKP มี sitemap ถูกทุกอย่าง หน้าไม่มี noindex robots ไม่บล็อก แต่ Google เก็บหน้า
    อื่นไป 10 หน้าโดยไม่แตะ /geo/* เลย — เพราะไม่มีหน้าไหนในเว็บลิงก์มาหาเลย
    """
    if not total:
        return [_c("orphan", "คอนเทนต์มีลิงก์จากเว็บ", SKIP, "ไม่มีหน้าให้ตรวจ")]
    if not pages_crawled:
        return [_c("orphan", "คอนเทนต์มีลิงก์จากเว็บ", SKIP, "ไต่หน้าเว็บไม่ได้")]
    if reachable == 0:
        return [_c("orphan", "คอนเทนต์มีลิงก์จากเว็บ", FAIL,
                   f"ไม่มีหน้าไหนในเว็บลิงก์มาหาคอนเทนต์เลย (ตรวจ {pages_crawled} หน้า) — "
                   "Google ให้ความสำคัญหน้าที่มีลิงก์จริงมากกว่าหน้าที่มีแต่ใน sitemap "
                   "เพิ่มเมนู/บล็อก บทความ ที่ชี้มาที่หน้ารวมคอนเทนต์")]
    if reachable < total:
        return [_c("orphan", "คอนเทนต์มีลิงก์จากเว็บ", OK,
                   f"เข้าถึงจากลิงก์ได้ {reachable}/{total}")]
    return [_c("orphan", "คอนเทนต์มีลิงก์จากเว็บ", OK, f"เข้าถึงได้ครบ {total} หน้า")]


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


def judge_uptime(dns_ok: bool, status: int) -> dict:
    """ตัดสินว่าเว็บล่มไหม จากผลที่ยิงมาแล้ว — แยก "DNS ไม่มีเรคคอร์ด" ออกจาก "เข้าเซิร์ฟเวอร์
    ไม่ได้" เพราะสองอย่างนี้แก้คนละที่ (DNS แก้ที่ Cloudflare, origin แก้ที่เซิร์ฟเวอร์)
    บอกรวม ๆ ว่า "เว็บล่ม" ทำให้ไล่หาสาเหตุผิดทางเสียเวลา"""
    if not dns_ok:
        return {"up": False, "code": "dns",
                "reason": "โดเมนแปลงเป็น IP ไม่ได้ — เรคคอร์ด DNS หาย โดเมนหมดอายุ หรือ nameserver ผิด"}
    if status == 0:
        return {"up": False, "code": "connect", "reason": "ต่อเซิร์ฟเวอร์ไม่ได้ (timeout หรือถูกปฏิเสธ)"}
    if status in (521, 522, 523, 524, 530):
        return {"up": False, "code": "origin",
                "reason": f"Cloudflare ตอบ {status} — เข้าถึง origin ไม่ได้ (tunnel หรือเซิร์ฟเวอร์ล่ม)"}
    if status >= 500:
        return {"up": False, "code": "server", "reason": f"เซิร์ฟเวอร์ตอบ {status}"}
    if status >= 400:
        return {"up": False, "code": "client", "reason": f"หน้าแรกตอบ {status}"}
    return {"up": True, "code": "ok", "reason": f"ตอบ {status}"}


# ---------- csr: เนื้อหาอยู่ใน HTML หรือรอ JavaScript ----------
def visible_text(html: str) -> str:
    """ข้อความที่เหลือหลังตัด comment/script/style/แท็กออก — ใกล้เคียงสิ่งที่บอทที่ไม่รัน JS อ่านได้"""
    h = re.sub(r"(?is)<!--.*?-->", " ", html or "")
    h = re.sub(r"(?is)<(script|style|template|svg)\b[^>]*>.*?</\1\s*>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", _html.unescape(h)).strip()


def page_shape(html: str) -> dict:
    """สรุปรูปร่าง HTML ดิบของหน้า — ตัวเลขที่ judge_csr ใช้ตัดสิน (แยกไว้ให้เทสต์/ดีบักดูค่าได้)"""
    h = html or ""
    ids = "|".join(re.escape(i) for i in MOUNT_IDS)
    h1 = re.search(r"(?is)<h1\b[^>]*>(.*?)</h1\s*>", h)
    return {
        "text": len(visible_text(h)),
        "links": len(re.findall(r"(?i)<a\b[^>]+href=", h)),
        "h1": bool(h1 and visible_text(h1.group(1))),
        # กรอบที่ JavaScript จะมาเติมแต่ตอนนี้ว่าง — ลายเซ็นของ React/Vue/Next ที่ไม่ได้ทำ SSR
        "empty_mount": bool(re.search(rf"""(?is)<(div|main)\b[^>]+id=["'](?:{ids})["'][^>]*>\s*</\1\s*>""", h)),
        "scripts": len(re.findall(r"(?i)<script\b[^>]+src=", h)),
    }


def judge_csr(shape: dict | None) -> list:
    """หน้าแรกส่งเนื้อหามาใน HTML เลยไหม — บอท AI ไม่รัน JavaScript เว็บ CSR จึงเป็นเว็บว่าง
    ในสายตา AI ทั้งที่คนเปิดดูเห็นครบ และเช็คข้ออื่นผ่านหมด (ทำ /geo เป็น rewrite ไว้ถูกแล้ว
    แต่ตัวเว็บเองไม่มีอะไรให้อ่าน) · มี <script type=module> อย่างเดียวไม่นับ — เว็บ SSR ที่
    hydrate ก็มีเหมือนกัน ตัดสินจากปริมาณข้อความเป็นหลัก"""
    label = "เนื้อหาอยู่ใน HTML ไม่ต้องรอ JavaScript"
    if not shape:
        return [_c("csr", label, SKIP, "ข้ามเพราะหน้าแรกเข้าไม่ได้")]
    t, n = shape["text"], shape["links"]
    stat = f"ข้อความ {t:,} ตัวอักษร · ลิงก์ {n}" + ("" if shape["h1"] else " · ไม่มี h1")
    if t < CSR_MIN_TEXT and (shape["empty_mount"] or shape["scripts"]):
        return [_c("csr", label, FAIL,
                   f"HTML ที่ส่งให้บอทมีแค่ {stat} — เนื้อหาถูกเติมด้วย JavaScript ทีหลัง (CSR) "
                   "บอท AI ไม่รัน JS จึงเห็นเว็บว่างเปล่า ต้องทำ SSR/prerender "
                   "หรืออย่างน้อยใส่เนื้อหาและลิงก์หลักลงใน HTML ตั้งแต่เซิร์ฟเวอร์")]
    if t < CSR_MIN_TEXT:
        return [_c("csr", label, WARN, f"หน้าแรกแทบไม่มีเนื้อหาให้บอทอ่าน — {stat}")]
    if t < CSR_THIN_TEXT and shape["empty_mount"]:
        return [_c("csr", label, WARN, f"มีเนื้อหาสำรองบางส่วน ({stat}) แต่ส่วนหลักยังรอ JavaScript")]
    return [_c("csr", label, OK, stat)]


# ---------- connector: path ที่ต้อง rewrite มาที่ platform ----------
def judge_connector(probes: dict, content: set, apex: str, wp: bool = False) -> list:
    """path ที่เว็บลูกค้าต้อง rewrite มาที่ platform ตอบบนโดเมนตัวเองจริงไหม

    probes = {path: {"status", "url" (ปลายทางหลังตามรีไดเรกต์), "html"}, "pages": [{"url", "final"}]}
    redirect กับ rewrite ให้ 200 เหมือนกันเมื่อตามลิงก์ไป เช็ค llms/sitemap เดิมจึงผ่านทั้งคู่
    แต่ Google เห็นต่างกัน: Hop ตั้ง geo-sitemap.xml เป็น redirect ไป geo.appreview.cloud →
    sitemap อยู่คนละโดเมนกับ URL ข้างใน ถูกตีตกทั้งไฟล์ และหน้าที่ redirect ออกไปไม่นับเป็นของเว็บ
    ส่วน /geo ที่ตอบ 200 ต้องดูด้วยว่าเป็นหน้ารวมบทความจริง ไม่ใช่ SPA ที่ตอบหน้าแรกให้ทุก path
    """
    label = "ตัวเชื่อม /geo · llms.txt · sitemap เป็น rewrite บนโดเมนเอง"
    paths = [probes.get(p) or {} for p in CONNECTOR_PATHS]
    if all(not p.get("status") for p in paths):
        return [_c("connector", label, SKIP, "เข้าเว็บไม่ได้")]
    base = f"https://{apex}"
    bad, good = [], []
    for path, p in zip(CONNECTOR_PATHS, paths):
        st, final, body = p.get("status") or 0, p.get("url") or "", p.get("html") or ""
        name = path if path == "/geo" else path.lstrip("/")
        if final and host_of(final) != apex:
            bad.append(f"{name} redirect ไป {host_of(final)} — ต้องเป็น rewrite ให้ตอบ 200 บนโดเมนเอง")
        elif path == "/geo":
            if st == 200:
                links = links_in(body, base, apex)
                n_art = len([u for u in links if u in content or re.search(r"/geo/\d+$", u)])
                if n_art or not content or 'name="generator" content="เจอ.AI' in body:
                    good.append("/geo ✓" + (f" ลิงก์บทความ {n_art}" if n_art else ""))
                else:
                    bad.append("/geo ตอบ 200 แต่ไม่มีลิงก์ไปบทความสักชิ้น — เป็นหน้า fallback ของเว็บเอง (SPA) "
                               "rewrite ยังไม่ทำงาน")
            elif st in (404, 410) and wp:
                good.append("/geo ไม่ต้องมี (WordPress)")
            elif st in (404, 410):
                bad.append("/geo ยังไม่ได้ตั้ง rewrite (ตอบ 404) — บอทไม่มีทางเจอหน้ารวมบทความ")
            elif st:
                bad.append(f"/geo ตอบ {st}")
            else:
                bad.append("/geo เข้าไม่ได้")
        elif st == 200:
            if path == "/llms.txt" and "<html" in body[:300].lower():
                bad.append("llms.txt ตอบ HTML (หน้า fallback ของเว็บ) — rewrite ยังไม่ทำงาน")
            else:
                good.append(f"{name} ✓")
        # llms/sitemap ที่ตอบ 404/5xx มีข้อของตัวเองฟ้องอยู่แล้ว ไม่นับซ้ำที่นี่
    off = [pg for pg in probes.get("pages") or [] if pg.get("final") and host_of(pg["final"]) != apex]
    if off:
        bad.append(f"บทความ {len(off)} หน้า redirect ไป {host_of(off[0]['final'])} — ต้องเป็น rewrite /geo/*")
    if bad:
        return [_c("connector", label, FAIL, " · ".join(bad))]
    return [_c("connector", label, OK, " · ".join(good) + " — ตอบบนโดเมนเองทั้งหมด")]


def judge_wp(ping: dict | None) -> list:
    """WordPress ของแบรนด์ยังรับคอนเทนต์ไหม — ping คือผล wp_client ที่ main ยิงให้ เพราะต้องใช้
    ความลับที่เก็บใน DB (site_health ไม่แตะ DB) · None = แบรนด์ไม่ได้ต่อ WordPress ไม่มีข้อนี้
    ก่อนหน้านี้ connector ตายเงียบแล้วไปโผล่ที่เช็ค freshness หลังคอนเทนต์ค้างเป็นเดือน"""
    if not ping:
        return []
    label = "WordPress รับคอนเทนต์ได้ (ปลั๊กอิน/REST)"
    how = "ปลั๊กอิน" if ping.get("mode") == "connector" else "REST API"
    msg = (ping.get("msg") or "").strip()
    if ping.get("ok"):
        return [_c("wp", label, OK, f"{how}: {msg}")]
    return [_c("wp", label, FAIL, f"{how}: {msg} — สั่งเผยแพร่แล้วจะไปไม่ถึงเว็บ แก้ที่หน้า WordPress ของแบรนด์")]


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


def run_checks(site_url: str, last_published=None, n_published: int = 0, today=None, wp=None) -> dict:
    """ตรวจเว็บ 1 แบรนด์ — คืน dict พร้อมเก็บลง DB / แสดงผล
    wp = ผล ping WordPress ที่ main ยิงให้ (None = แบรนด์โหมด hosted ไม่มี WordPress)"""
    import httpx
    today = today or datetime.date.today()
    apex = host_of(site_url)
    base = f"https://{apex}"
    checks: list = []

    def fetch(url, redirects=True):
        """คืน (status, headers, text, final_url) — final_url บอกว่าตามรีไดเรกต์ไปจบที่โดเมนไหน"""
        try:
            with httpx.Client(follow_redirects=redirects, timeout=20,
                              headers={"User-Agent": UA}) as c:
                r = c.get(url)
                return r.status_code, dict(r.headers), r.text, str(r.url)
        except Exception:
            return 0, {}, "", url

    def code(url):
        try:
            with httpx.Client(follow_redirects=True, timeout=20, headers={"User-Agent": UA}) as c:
                return c.get(url).status_code
        except Exception:
            return 0

    st, hd, home, _ = fetch(base + "/")
    checks += judge_home(st, hd, home)
    home_title = _title(home)
    checks += judge_csr(page_shape(home) if st == 200 and home else None)

    checks += judge_soft404(code(f"{base}/zz-health-check-not-a-real-path"))

    try:
        with httpx.Client(follow_redirects=False, timeout=20, headers={"User-Agent": UA}) as c:
            r = c.get(f"https://www.{apex}/")
            checks += judge_www(r.status_code, r.headers.get("location", ""), apex)
    except Exception:
        checks += judge_www(0, "", apex)

    rst, _, rtxt, _ = fetch(base + "/robots.txt")
    checks += judge_robots(rst, rtxt)

    lst, _, ltxt, lurl = fetch(base + "/llms.txt")
    llinks = re.findall(r"https?://\S+", ltxt or "")[1:]
    lprobe = {u: code(u) for u in llinks[:MAX_URL_PROBES]}
    checks += judge_links("llms", "llms.txt", lst, llinks, lprobe)

    sst, _, sxml, surl = fetch(base + "/geo-sitemap.xml")
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
        _, _, h, fu = fetch(u)
        pages.append({"url": u, "title": _title(h), "canonical": _canonical(h), "final": fu})
    checks += judge_pages(home_title, pages)

    content = {u.split("?")[0].rstrip("/") for u in locs if u.rstrip("/") != base}

    # connector: ดูว่าตามรีไดเรกต์แล้วไปจบที่โดเมนไหน — llms/sitemap ใช้ผลที่ดึงมาแล้ว ยิงเพิ่มแค่ /geo
    gst, _, ghtml, gurl = fetch(base + "/geo")
    checks += judge_connector({
        "/geo": {"status": gst, "url": gurl, "html": ghtml},
        "/llms.txt": {"status": lst, "url": lurl, "html": ltxt},
        "/geo-sitemap.xml": {"status": sst, "url": surl, "html": sxml},
        "pages": [{"url": p["url"], "final": p["final"]} for p in pages],
    }, content, apex, wp=bool(wp))
    checks += judge_wp(wp)

    # orphan: ไต่จากหน้าแรก 2 ชั้น แล้วดูว่าเจอ URL คอนเทนต์จาก sitemap ไหม
    seen_links, crawled = set(), 0
    if content:
        first = links_in(home, base, apex)
        seen_links |= first
        crawled = 1
        # ชั้นสอง: ตัดตัวคอนเทนต์เอง และตัดหน้าแรกที่เพิ่งไต่ไป (ลิงก์ #anchor ตัด fragment
        # แล้วเหลือ URL หน้าแรก ถ้าไม่ตัดจะกินโควตาไต่ไปเปล่า ๆ หนึ่งหน้า)
        for u in crawl_order(first - content - {base.rstrip("/")}, content)[:MAX_CRAWL - 1]:
            _, _, h2, _ = fetch(u)
            if h2:
                seen_links |= links_in(h2, base, apex)
                crawled += 1
    checks += judge_orphan(len(content & seen_links), len(content), crawled)

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


# ---------- uptime probe ----------
def dns_resolves(host: str) -> bool:
    import socket
    try:
        socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        return True
    except OSError:
        return False


def probe_uptime(site_url: str, attempts: int = 2, wait: float = UPTIME_RETRY_WAIT) -> dict:
    """ยิงหน้าแรกครั้งเดียว (ลองซ้ำถ้าไม่ผ่าน) — ใช้ UA เบราว์เซอร์ปกติ ไม่ใช่ Googlebot
    เพราะที่อยากรู้รายวันคือ "คนเข้าเว็บได้ไหม" ไม่ใช่ "บอทเข้าได้ไหม" และ bot protection
    ของ Cloudflare ชอบตอบ 403 ให้ UA บอทจนแจ้งเตือนผิด"""
    import time
    import httpx
    apex = host_of(site_url)
    url = f"https://{apex}/"
    tries = max(1, attempts)
    res: dict = {}
    for i in range(tries):
        t0 = time.monotonic()
        dns_ok = dns_resolves(apex)
        status = 0
        if dns_ok:
            try:
                with httpx.Client(follow_redirects=True, timeout=15,
                                  headers={"User-Agent": BROWSER_UA}) as c:
                    status = c.get(url).status_code
            except Exception:
                status = 0
        res = judge_uptime(dns_ok, status)
        res.update(status=status, url=url, attempts=i + 1,
                   ms=int((time.monotonic() - t0) * 1000),
                   checked_at=datetime.datetime.now().isoformat(timespec="seconds"))
        if res["up"]:
            break
        if i + 1 < tries:
            time.sleep(wait)
    return res
