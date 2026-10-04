"""Google Search Console — ข้อมูลจริงจาก Google แทนการเดา

ก่อนหน้านี้ "Google เก็บเข้า index แล้วหรือยัง" วัดจาก site: ผ่าน Serper ซึ่งเสียเครดิตและเชื่อได้
ครึ่งเดียว (site: ไม่โชว์ทุกหน้าที่อยู่ใน index) และการส่ง sitemap ต้องให้คนไปกดใน Search Console เอง
ทีละเว็บ — JKP มี sitemap ถูกทุกอย่างแต่ไม่มีใครส่ง Google เลยเก็บหน้าอื่นไป 10 หน้าโดยไม่แตะ /geo/*

ใช้บัญชีบริการ (service account) ของ Google Cloud หนึ่งบัญชี แล้วให้ลูกค้าเพิ่มอีเมลของมันเป็นผู้ใช้
สิทธิ์ Full ใน Search Console ของเว็บตัวเอง — ไม่ต้องขอรหัสผ่านใคร และ Full ส่ง sitemap/ตรวจ URL ได้
(Restricted ดูได้อย่างเดียว)

ต่อรอบซิงค์ 1 แบรนด์: ส่ง sitemap ถ้ายังไม่ได้ส่ง · ตรวจ index รายหน้า (URL Inspection, โควตา
2,000 ครั้ง/property/วัน) · ดึงคลิก/impressions 28 วัน (Search Analytics ช้ากว่าปัจจุบัน ~3 วัน)
ฟังก์ชันที่ไม่ยิงเน็ต (parse_key, match_property, classify, health_summary) แยกไว้ให้เทสต์ได้
คีย์มี private key — เก็บเข้ารหัส (Fernet เดียวกับรหัส WordPress) ไม่พิมพ์ ไม่ส่งออกหน้าเว็บ
"""
from __future__ import annotations
import os
import json
import time
import base64
import datetime
from urllib.parse import quote

from .site_health import host_of, _noindex_reason, UA as _BOT_UA

SCOPE = "https://www.googleapis.com/auth/webmasters"
TOKEN_URL = "https://oauth2.googleapis.com/token"
WMT = "https://www.googleapis.com/webmasters/v3"
INSPECT_URL = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"
SITEMAP_PATH = "/geo-sitemap.xml"          # ต้องตรงกับ geo_content.SITEMAP_PATH
TIMEOUT = 30
MAX_INSPECT = int(os.getenv("GEO_GSC_MAX_INSPECT", "40"))   # หน้าต่อแบรนด์ต่อรอบ — กันกินโควตา
ANALYTICS_DAYS = 28
DATA_LAG_DAYS = 3                           # Search Console มีข้อมูลถึงราว 3 วันก่อนเท่านั้น
SITEMAP_STALE_DAYS = 7                      # Google ไม่อ่าน sitemap นานกว่านี้ → ส่งซ้ำให้อ่านใหม่

DB_KEY = "gsc_service_account"              # settings: JSON ที่เข้ารหัสแล้ว
STATUS_KEY = "gsc_status"                   # settings: ผลทดสอบล่าสุดตอนบันทึก (ไม่มีความลับ)
ENV_FILE = "GSC_SERVICE_ACCOUNT_FILE"       # ทางเลือก: path ไฟล์ JSON ใน .env

# กลุ่มสถานะที่สรุปจาก URL Inspection — Google มี coverageState หลายสิบแบบ แต่ที่ต้องทำต่างกันมีแค่นี้
INDEXED, CRAWLED, DISCOVERED, UNKNOWN, BLOCKED, OTHER, ERROR = (
    "indexed", "crawled", "discovered", "unknown", "blocked", "other", "error")
GROUPS = (INDEXED, CRAWLED, DISCOVERED, UNKNOWN, BLOCKED, OTHER, ERROR)
GROUP_LABELS = {
    INDEXED: "อยู่ใน index",
    CRAWLED: "Google อ่านแล้ว ยังไม่เก็บ",
    DISCOVERED: "Google เห็นแล้ว ยังไม่อ่าน",
    UNKNOWN: "Google ยังไม่รู้จัก",
    BLOCKED: "ถูกบล็อก (noindex/robots)",
    OTHER: "อื่น ๆ",
    ERROR: "ตรวจไม่ได้",
}
PERM_LABELS = {"siteOwner": "Owner", "siteFullUser": "Full", "siteRestrictedUser": "Restricted",
               "siteUnverifiedUser": "ยังไม่ยืนยัน"}
CAN_SUBMIT = ("siteOwner", "siteFullUser")

_TOK: dict = {}                             # cache access token ต่อ client_email (อายุ 1 ชม.)


# ---------- คีย์ ----------
def parse_key(text: str) -> dict:
    """ตรวจไฟล์ JSON ที่วางมา — บอกให้ชัดว่าผิดตรงไหน คนมักวางผิดไฟล์ (OAuth client แทน service account)"""
    try:
        key = json.loads(text or "")
    except Exception:
        raise ValueError("ไม่ใช่ JSON — วางเนื้อหาไฟล์ .json ทั้งไฟล์ (เริ่มด้วย { และจบด้วย })")
    if not isinstance(key, dict) or key.get("type") != "service_account":
        raise ValueError("ไฟล์นี้ไม่ใช่คีย์ service account (type ต้องเป็น service_account) — "
                         "สร้างจาก IAM → Service Accounts → Keys → Add key → JSON")
    for f in ("client_email", "private_key"):
        if not key.get(f):
            raise ValueError(f"ไฟล์ขาดฟิลด์ {f}")
    if "PRIVATE KEY" not in key["private_key"]:
        raise ValueError("private_key ไม่ใช่รูปแบบ PEM")
    return key


def load_key() -> dict | None:
    from . import db
    raw = db.get_setting(DB_KEY)
    if raw:
        from . import wp_client
        try:
            return json.loads(wp_client.decrypt(raw))
        except Exception:
            return None
    path = os.getenv(ENV_FILE, "").strip()
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return parse_key(f.read())
        except Exception:
            return None
    return None


def key_info() -> dict | None:
    """สิ่งที่หน้าเว็บโชว์ได้: อีเมล (ลูกค้าต้องเอาไปเพิ่มเอง) + โปรเจกต์ — ไม่มีส่วนลับ"""
    key = load_key()
    if not key:
        return None
    from . import db
    return {"email": key.get("client_email"), "project": key.get("project_id", ""),
            "source": "db" if db.get_setting(DB_KEY) else "env"}


def status() -> dict | None:
    from . import db
    try:
        return json.loads(db.get_setting(STATUS_KEY) or "null")
    except Exception:
        return None


def save_key(text: str) -> dict:
    """ตรวจ + ทดสอบจริง (ขอ token และดึงรายชื่อ property) ก่อนเก็บ — คีย์ที่ใช้ไม่ได้ไม่ทับของเดิม"""
    try:
        key = parse_key(text)
    except ValueError as e:
        return {"ok": False, "msg": str(e)}
    try:
        _TOK.pop(key["client_email"], None)
        sites = list_sites(key)
        msg = f"เข้าถึงได้ {len(sites)} property" + (
            ": " + ", ".join(s.get("siteUrl", "") for s in sites[:6]) + (" …" if len(sites) > 6 else "")
            if sites else " — ยังไม่มีเว็บไหนเพิ่มบัญชีนี้เป็นผู้ใช้")
    except Exception as e:
        return {"ok": False, "msg": _err(e)}
    from . import db, wp_client
    db.set_setting(DB_KEY, wp_client.encrypt(json.dumps(key)))
    db.set_setting(STATUS_KEY, json.dumps({"ok": True, "msg": msg, "sites": len(sites),
                                           "checked_at": datetime.datetime.now().isoformat(timespec="seconds")},
                                          ensure_ascii=False))
    return {"ok": True, "msg": msg, "email": key["client_email"], "sites": len(sites)}


# ---------- OAuth: service account → access token ----------
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def make_jwt(key: dict, now: int | None = None) -> str:
    """JWT RS256 ตาม OAuth 2.0 service-account flow — เซ็นด้วย cryptography ที่มีอยู่แล้ว ไม่ต้องลง google-auth"""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    now = int(now if now is not None else time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    if key.get("private_key_id"):
        header["kid"] = key["private_key_id"]
    claims = {"iss": key["client_email"], "scope": SCOPE,
              "aud": key.get("token_uri") or TOKEN_URL, "iat": now, "exp": now + 3600}
    signing = (_b64(json.dumps(header, separators=(",", ":")).encode()) + "."
               + _b64(json.dumps(claims, separators=(",", ":")).encode()))
    priv = serialization.load_pem_private_key(key["private_key"].encode(), password=None)
    sig = priv.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256())
    return signing + "." + _b64(sig)


def access_token(key: dict) -> str:
    import httpx
    c = _TOK.get(key["client_email"])
    if c and c["exp"] - 120 > time.time():
        return c["token"]
    r = httpx.post(key.get("token_uri") or TOKEN_URL, timeout=TIMEOUT,
                   data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": make_jwt(key)})
    r.raise_for_status()
    j = r.json()
    _TOK[key["client_email"]] = {"token": j["access_token"], "exp": time.time() + int(j.get("expires_in", 3600))}
    return j["access_token"]


def _err(e: Exception) -> str:
    """ข้อความผิดพลาดที่อ่านรู้เรื่อง — Google ใส่คำอธิบายไว้ใน error.message (เช่น API ยังไม่เปิดในโปรเจกต์)"""
    try:
        import httpx
        if isinstance(e, httpx.HTTPStatusError):
            r = e.response
            try:
                j = r.json()
                m = j.get("error")
                m = m.get("message") if isinstance(m, dict) else (j.get("error_description") or m)
            except Exception:
                m = r.text[:200]
            return f"Google ตอบ {r.status_code}: {str(m)[:500]}"
    except ImportError:
        pass
    return f"{type(e).__name__}: {str(e)[:200]}"


def _req(key: dict, method: str, url: str, **kw):
    import httpx
    r = httpx.request(method, url, headers={"Authorization": f"Bearer {access_token(key)}"},
                      timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r.json() if r.content else {}


def _enc(s: str) -> str:
    return quote(s, safe="")


# ---------- API ----------
def list_sites(key: dict) -> list:
    return (_req(key, "GET", f"{WMT}/sites") or {}).get("siteEntry") or []


def candidate_props(site_url: str) -> list:
    """ชื่อ property ที่เป็นไปได้ของเว็บนี้ เรียงจากที่อยากได้ก่อน — domain property ครอบทุก URL"""
    apex = host_of(site_url)
    return [f"sc-domain:{apex}", f"https://{apex}/", f"https://www.{apex}/", f"http://{apex}/", f"http://www.{apex}/"]


def match_property(sites: list, site_url: str):
    """เลือก property ของเว็บนี้จากที่บัญชีมองเห็น → (siteUrl, permissionLevel) หรือ (None, None)"""
    by = {s.get("siteUrl"): s.get("permissionLevel") for s in sites or []}
    for cand in candidate_props(site_url):
        if cand in by:
            return cand, by[cand]
    apex = host_of(site_url)
    for su, perm in by.items():          # URL-prefix ที่ลึกกว่า (https://x.com/th/) — ยอมรับถ้า host ตรง
        if su and su.startswith("http") and host_of(su) == apex:
            return su, perm
    return None, None


def sitemap_status(key: dict, prop: str, sitemap_url: str) -> dict | None:
    import httpx
    try:
        return _req(key, "GET", f"{WMT}/sites/{_enc(prop)}/sitemaps/{_enc(sitemap_url)}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:  # ยังไม่เคยส่ง
            return None
        raise


def submit_sitemap(key: dict, prop: str, sitemap_url: str) -> None:
    _req(key, "PUT", f"{WMT}/sites/{_enc(prop)}/sitemaps/{_enc(sitemap_url)}")


def classify(res: dict) -> dict:
    """ย่อผล URL Inspection ให้เหลือสิ่งที่ตัดสินใจได้ — verdict PASS คืออยู่ใน index ส่วนที่เหลือดู coverageState"""
    r = (res or {}).get("inspectionResult") or {}
    idx = r.get("indexStatusResult") or {}
    verdict, state = idx.get("verdict", ""), idx.get("coverageState", "") or ""
    low = state.lower()
    robots, indexing = idx.get("robotsTxtState", ""), idx.get("indexingState", "")
    # ข้อความ coverageState แปลตาม languageCode — เราขอ en-US ไว้ แต่กัน fallback ไทยด้วย
    # (รอบแรกขอ th แล้ว 12 หน้า "Google ไม่รู้จัก URL" ตกไปอยู่ "อื่น ๆ" ทั้งที่คือ unknown)
    if verdict == "PASS":
        group = INDEXED
    elif (robots == "DISALLOWED" or indexing in ("BLOCKED_BY_META_TAG", "BLOCKED_BY_HTTP_HEADER", "BLOCKED_BY_ROBOTS_TXT")
          or "noindex" in low or "blocked" in low or "บล็อก" in low):
        group = BLOCKED
    elif "unknown to google" in low or "ไม่รู้จัก" in low:
        group = UNKNOWN
    elif low.startswith("discovered") or low.startswith("ค้นพบ"):
        group = DISCOVERED
    elif low.startswith("crawled") or low.startswith("รวบรวมข้อมูล"):
        group = CRAWLED
    else:
        group = OTHER
    return {"group": group, "state": state, "verdict": verdict, "last_crawl": idx.get("lastCrawlTime"),
            "google_canonical": idx.get("googleCanonical"), "user_canonical": idx.get("userCanonical"),
            "indexing": indexing, "fetch": idx.get("pageFetchState", ""), "robots": robots,
            # Google เห็น URL นี้ใน sitemap ไหม / มีหน้าไหนลิงก์มาไหม — บอกว่าทำไมยังไม่ถูกเก็บ
            "in_sitemap": bool(idx.get("sitemap")), "referrers": len(idx.get("referringUrls") or []),
            "link": r.get("inspectionResultLink")}


def inspect(key: dict, prop: str, url: str) -> dict:
    # en-US เพราะ classify จำแนกจากข้อความ coverageState — ป้ายไทยใช้ GROUP_LABELS ของเราเอง
    res = _req(key, "POST", INSPECT_URL, json={"inspectionUrl": url, "siteUrl": prop, "languageCode": "en-US"})
    return {"url": url, **classify(res)}


def analytics(key: dict, prop: str, body: dict) -> list:
    return (_req(key, "POST", f"{WMT}/sites/{_enc(prop)}/searchAnalytics/query", json=body) or {}).get("rows") or []


# ---------- ซิงค์ 1 แบรนด์ ----------
def _norm(u: str) -> str:
    return (u or "").split("#")[0].rstrip("/")


def _live_noindex(url: str):
    """ดึงหน้าสด ๆ ดูว่ายังมี noindex ไหม — Google รายงานจากครั้งที่อ่านล่าสุด ซึ่งอาจเป็นเดือนก่อน
    (appreview เคยตั้ง "ห้ามเสิร์ชเอนจิน" ไว้ แก้แล้ว แต่ Google ยังจำ 4 หน้าว่าติด noindex จากรอบ ก.ค.–ก.ย.)
    คืน None = ไม่มี noindex แล้ว · str = เหตุผลที่ยังติด · "?" = ดึงหน้าไม่ได้"""
    import httpx
    try:
        with httpx.Client(follow_redirects=True, timeout=15, headers={"User-Agent": _BOT_UA}) as c:
            r = c.get(url)
        if r.status_code != 200:
            return f"ตอบ {r.status_code}"
        return _noindex_reason(dict(r.headers), r.text)
    except Exception:
        return "?"


def _days_ago(iso: str | None, today: datetime.date) -> int | None:
    try:
        return (today - datetime.date.fromisoformat(str(iso)[:10])).days
    except Exception:
        return None


def _empty_index() -> dict:
    return {**{g: 0 for g in GROUPS}, "total": 0, "inspected": 0}


def sync_brand(site_url: str, pages: list, key: dict | None = None, today: datetime.date | None = None) -> dict:
    """pages = [{"title", "url"}] ของคอนเทนต์ที่เผยแพร่แล้ว — คืนรายงานทั้งก้อน (เก็บ JSON ลง DB ได้เลย)
    แต่ละขั้นล้มได้โดยไม่ล้มทั้งรอบ — ข้อผิดพลาดเก็บใน errors ให้หน้าเว็บโชว์"""
    key = key or load_key()
    rep = {"synced_at": datetime.datetime.now().isoformat(timespec="seconds"), "linked": False,
           "email": key.get("client_email") if key else None, "property": None, "permission": None,
           "tried": candidate_props(site_url), "sites_seen": 0, "reason": "",
           "sitemap": None, "index": _empty_index(), "pages": [], "analytics": None, "errors": []}
    if not key:
        rep["reason"] = "ยังไม่ได้ตั้งบัญชีบริการ (ผู้ดูแล → ตั้งค่า → Search Console)"
        return rep
    try:
        sites = list_sites(key)
    except Exception as e:
        rep["reason"] = "เรียก Search Console ไม่ได้ — " + _err(e)
        return rep
    rep["sites_seen"] = len(sites)
    prop, perm = match_property(sites, site_url)
    if not prop:
        rep["reason"] = (f"บัญชี {key['client_email']} ยังไม่ได้รับสิทธิ์ใน property ของเว็บนี้"
                         + (f" (เห็น {len(sites)} property อื่น)" if sites else ""))
        return rep
    if perm == "siteUnverifiedUser":
        rep["reason"] = f"property {prop} ยังไม่ผ่านการยืนยันความเป็นเจ้าของ — ยืนยันใน Search Console ก่อน"
        return rep
    rep.update(linked=True, property=prop, permission=perm)

    # sitemap — ส่งให้เลยถ้ายังไม่ได้ส่ง (สิทธิ์ Full/Owner) นี่คืองานที่เคยต้องให้คนไปกดเองทีละเว็บ
    sm_url = site_url.rstrip("/") + SITEMAP_PATH
    sm = {"url": sm_url, "submitted": False, "just_submitted": False, "re_submitted": False, "is_pending": False,
          "last_submitted": None, "last_downloaded": None, "errors": 0, "warnings": 0, "n_urls": 0, "note": ""}
    today = today or datetime.date.today()
    try:
        st = sitemap_status(key, prop, sm_url)
        if st is None:
            if perm in CAN_SUBMIT:
                submit_sitemap(key, prop, sm_url)
                sm["just_submitted"] = True
                st = sitemap_status(key, prop, sm_url)
            else:
                sm["note"] = f"สิทธิ์ {PERM_LABELS.get(perm, perm)} ส่ง sitemap ไม่ได้ — ให้เจ้าของเว็บเพิ่มบัญชีเป็น Full"
        if st:
            sm.update(submitted=True, is_pending=bool(st.get("isPending")),
                      last_submitted=st.get("lastSubmitted"), last_downloaded=st.get("lastDownloaded"),
                      errors=int(st.get("errors") or 0), warnings=int(st.get("warnings") or 0),
                      n_urls=sum(int(c.get("submitted") or 0) for c in st.get("contents") or []))
            # ส่งแล้วแต่ Google ไม่กลับมาอ่านนาน → คอนเทนต์ที่เผยแพร่หลังจากนั้นเป็น "ไม่รู้จัก" ทั้งหมด
            # (appreview: อ่านล่าสุด 20 ก.ย. ส่วน 12 หน้าใหม่ Google ไม่รู้จักเลย) ส่งซ้ำกระตุ้นให้อ่านใหม่
            ago = _days_ago(st.get("lastDownloaded") or st.get("lastSubmitted"), today)
            if not sm["just_submitted"] and perm in CAN_SUBMIT and ago is not None and ago > SITEMAP_STALE_DAYS:
                submit_sitemap(key, prop, sm_url)
                sm.update(re_submitted=True,
                          note=f"Google ไม่ได้อ่าน sitemap มา {ago} วัน — ส่งซ้ำให้แล้วเพื่อกระตุ้นให้อ่านใหม่")
        elif sm["just_submitted"]:
            sm.update(submitted=True, is_pending=True)
    except Exception as e:
        rep["errors"].append({"step": "sitemap", "msg": _err(e)})
    rep["sitemap"] = sm

    # index รายหน้า
    for pg in pages[:MAX_INSPECT]:
        # URL Inspection ช้าเป็นบางครั้ง (petgo: 1 ใน 24 หน้า ReadTimeout) — ลองซ้ำหนึ่งครั้งก่อนบันทึกว่าตรวจไม่ได้
        for attempt in (1, 2):
            try:
                row = inspect(key, prop, pg["url"])
                break
            except Exception as e:
                if attempt == 1 and "Timeout" in type(e).__name__:
                    continue
                row = {"url": pg["url"], "group": ERROR, "state": _err(e), "verdict": "", "last_crawl": None,
                       "google_canonical": None, "user_canonical": None, "indexing": "", "fetch": "", "robots": "", "link": None}
        row.update(title=pg.get("title") or "", clicks=None, impressions=None, position=None)
        rep["pages"].append(row)
    idx = rep["index"]
    idx["total"], idx["inspected"] = len(pages), len(rep["pages"])
    idx["blocked_stale"] = 0
    for row in rep["pages"]:
        idx[row["group"]] = idx.get(row["group"], 0) + 1
        # ติด noindex ตามที่ Google จำ — เช็คสดว่ายังติดจริงไหม จะได้ไม่ส่งคนไปไล่หา noindex ที่แก้ไปแล้ว
        row["stale"] = False
        if row["group"] == BLOCKED and row.get("indexing") in ("BLOCKED_BY_META_TAG", "BLOCKED_BY_HTTP_HEADER"):
            row["live_noindex"] = _live_noindex(row["url"])
            row["stale"] = row["live_noindex"] is None
            idx["blocked_stale"] += int(row["stale"])

    # คลิก/impressions 28 วัน — ทั้งเว็บ, รายหน้าคอนเทนต์ (join กับ URL ของเรา), คำค้น
    end = today - datetime.timedelta(days=DATA_LAG_DAYS)
    start = end - datetime.timedelta(days=ANALYTICS_DAYS - 1)
    rng = {"startDate": str(start), "endDate": str(end)}
    an = {"start": str(start), "end": str(end), "site": {"clicks": 0, "impressions": 0, "ctr": 0.0, "position": None},
          "content": {"clicks": 0, "impressions": 0}, "top_queries": [], "content_queries": []}
    try:
        tot = analytics(key, prop, dict(rng))
        if tot:
            an["site"] = {"clicks": int(tot[0].get("clicks") or 0), "impressions": int(tot[0].get("impressions") or 0),
                          "ctr": float(tot[0].get("ctr") or 0), "position": round(float(tot[0]["position"]), 1) if tot[0].get("position") else None}
        ours = {_norm(p["url"]): p for p in rep["pages"]}
        for r in analytics(key, prop, {**rng, "dimensions": ["page"], "rowLimit": 1000}):
            p = ours.get(_norm((r.get("keys") or [""])[0]))
            if p:
                p.update(clicks=int(r.get("clicks") or 0), impressions=int(r.get("impressions") or 0),
                         position=round(float(r.get("position") or 0), 1) or None)
                an["content"]["clicks"] += p["clicks"]
                an["content"]["impressions"] += p["impressions"]
        an["top_queries"] = [{"query": r["keys"][0], "clicks": int(r.get("clicks") or 0),
                              "impressions": int(r.get("impressions") or 0),
                              "position": round(float(r.get("position") or 0), 1)}
                             for r in analytics(key, prop, {**rng, "dimensions": ["query"], "rowLimit": 10})]
        agg: dict = {}
        for r in analytics(key, prop, {**rng, "dimensions": ["query", "page"], "rowLimit": 2000}):
            if _norm(r["keys"][1]) in ours:
                a = agg.setdefault(r["keys"][0], {"query": r["keys"][0], "clicks": 0, "impressions": 0, "_pos": 0.0})
                imp = int(r.get("impressions") or 0)
                a["clicks"] += int(r.get("clicks") or 0)
                a["impressions"] += imp
                a["_pos"] += float(r.get("position") or 0) * imp
        for a in agg.values():
            a["position"] = round(a.pop("_pos") / a["impressions"], 1) if a["impressions"] else None
        an["content_queries"] = sorted(agg.values(), key=lambda a: (-a["clicks"], -a["impressions"]))[:10]
    except Exception as e:
        rep["errors"].append({"step": "analytics", "msg": _err(e)})
    rep["analytics"] = an
    return rep


def health_summary(report: dict | None) -> dict | None:
    """สรุปให้ site_health.judge_indexed — None = ยังไม่ได้เชื่อม ให้ตัวตรวจใช้ทางเดิม (Serper)"""
    if not report or not report.get("linked"):
        return None
    idx = report.get("index") or {}
    try:
        age = (datetime.date.today() - datetime.date.fromisoformat(report["synced_at"][:10])).days
    except Exception:
        age = 0
    sm = report.get("sitemap") or {}
    out = {"synced_at": report.get("synced_at", ""), "age_days": age,
           "total": int(idx.get("total") or 0), "inspected": int(idx.get("inspected") or 0),
           "sitemap_ok": bool(sm.get("submitted")) and not sm.get("errors"),
           **{g: int(idx.get(g) or 0) for g in GROUPS}}
    # blocked = ที่ยังติดอยู่จริงตอนนี้ · blocked_stale = Google จำของเก่า แก้แล้ว รอมันกลับมาอ่าน
    out["blocked_stale"] = int(idx.get("blocked_stale") or 0)
    out["blocked"] = max(0, out["blocked"] - out["blocked_stale"])
    return out
