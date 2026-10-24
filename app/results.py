"""ฝ่ายผลลัพธ์ลูกค้า (Client Results · GEO Strategist) — ดูแลว่าแบรนด์ลูกค้า "ถูก AI แนะนำจริงไหม" ทุกสัปดาห์
ต่อแบรนด์ (ทุกแบรนด์ยกเว้นแบรนด์ของเจอ.AI เอง) หลังรอบวัดผล:
  1. อ่านผลวัด: ถาม AI จริง (ai_visibility) · Google AI Overview/AI Mode (ai_serp) → แนวโน้มรายสัปดาห์
  2. ใครถูกอ้างแทน: โดเมน + ชนิดหน้าที่ AI อ้าง (หน้ารวมรายการตามทำเล/หมวด · บทความ · หน้าแรก · หน้ารายการเดี่ยว)
  3. สุขภาพคอนเทนต์ที่เผยแพร่: รูปหัวบทความเสีย/เป็นโลโก้ → เอาออกเอง · ลิงก์ภายในตาย · บริบทเว็บที่ใช้เขียนบาง → ดึงใหม่ · index ใน Google
  4. งานที่ทีมเว็บลูกค้าต้องทำ (result_tasks): ตรวจเองว่าทำแล้วหรือยัง (URL ขึ้น / หน้ามีลิงก์) → เสร็จแล้ววัดผลซ้ำทันที
     ค้างเกิน 14 วัน → ร่างข้อความตามงานให้เจ้าของส่งเอง (ระบบไม่ส่งถึงลูกค้าเอง)
  5. ไม่ขยับ 4 สัปดาห์ติด → เตือนเจ้าของพร้อมสิ่งที่ควรทำ · ขยับขึ้น → แจ้งเจ้าของ + ส่งต่อ CMO ทำเคสศึกษา
ตัวเลขทุกตัวมาจากผลวัดจริง ไม่ใช้โมเดลแต่ง"""
from __future__ import annotations
import datetime as dt
import json
import re
from collections import Counter
from urllib.parse import urlparse, unquote

from . import db, office

STALL_WEEKS = 4
REVIEW_EVERY_DAYS = 6
NUDGE_AFTER_DAYS = 14
STALL_ALERT_EVERY_DAYS = 28
CHECK_KINDS = {"exists": "หน้านี้ต้องเปิดได้", "contains": "หน้านี้ต้องมีข้อความ/ลิงก์", "manual": "เจ้าของกดเสร็จเอง"}
STATE_TH = {"win": "ขยับขึ้น", "ok": "กำลังวัด", "waiting": "งานทีมเว็บเพิ่งเสร็จ · รอผลวัด", "stalled": "ไม่ขยับ 4 สัปดาห์", "blocked": "ไม่ขยับ · รองานทีมเว็บ", "new": "ยังไม่มีผลวัด"}
KIND_TH = {"listing": "หน้ารวมรายการตามทำเล/หมวด", "article": "บทความ/คู่มือ", "home": "หน้าแรก", "detail": "หน้ารายการเดี่ยว", "other": "หน้าอื่น"}


def enabled() -> bool:
    return (db.get_setting("results_on") or "1") == "1"


def _now() -> dt.datetime:
    return dt.datetime.now()


def _ts(s) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(s)[:19]) if s else None
    except ValueError:
        return None


def _apex(d: str) -> str:
    d = (d or "").lower().strip()
    d = urlparse(d).netloc or d if "://" in d else d
    return d.split("/")[0].removeprefix("www.")


def brands() -> list:
    from . import marketing
    sb = marketing.self_brand()
    return [b for b in db.list_all_brands() if not (sb and b["id"] == sb["id"])]


# ---------- ผลวัด ----------
def _rows(report) -> list:
    try:
        return (json.loads(report) if isinstance(report, str) else report or {}).get("rows") or []
    except Exception:
        return []


def _hits(rows) -> int:
    return sum(1 for r in rows if r.get("cited") is True or r.get("named") is True or r.get("status") in ("cited", "named"))


def visibility(bid: int) -> dict:
    """แนวโน้มรายสัปดาห์ (เก่า → ใหม่): ถาม AI จริงกี่ครั้ง ถูกแนะนำกี่ครั้ง + AI Overview"""
    series = []
    for r in reversed(db.list_ai_visibility(bid, STALL_WEEKS + 4)):
        rows = [x for x in _rows(r["report"]) if x.get("status") != "skip"]
        series.append({"at": str(r["checked_at"])[:10], "asked": len(rows) or (r["asked"] or 0), "hits": _hits(rows) if rows else (r["cited"] or 0) + (r["named"] or 0)})
    serp = []
    for r in reversed(db.list_ai_serp(bid, STALL_WEEKS + 4)):
        rows = _rows(r["report"])
        if rows:
            serp.append({"at": str(r["checked_at"])[:10], "asked": len(rows), "shown": sum(1 for x in rows if x.get("status") != "none"), "hits": _hits(rows)})
    last = series[-1] if series else None
    prev_best = max((s["hits"] for s in series[:-1]), default=0)
    sl = serp[-1] if serp else None
    sprev = max((s["hits"] for s in serp[:-1]), default=0)
    recent = series[-STALL_WEEKS:]
    return {"series": series, "serp": serp, "last": last, "serp_last": sl,
            "improved": bool(last and last["hits"] > prev_best and len(series) > 1) or bool(sl and sl["hits"] > sprev and len(serp) > 1),
            "stalled": len(recent) >= STALL_WEEKS and all(s["hits"] == 0 for s in recent) and not (sl and sl["hits"])}


def page_kind(url: str) -> str:
    u = unquote(url or "").lower()
    path = urlparse(u).path
    if path in ("", "/") and "?" not in u:
        return "home"
    if re.search(r"property_detail|/property/|/detail|/item/|/listing/\d|/p/\d", u):
        return "detail"
    if re.search(r"/(blog|blogs|article|articles|news|knowledge|guide|guides|tips|บทความ|geo)/", path):
        return "article"
    if re.search(r"for-rent|for-sale|/rent\b|/sale\b|/properties|/listings?\b|/category|/in-[a-z0-9-]+|เช่า|ขาย|/warehouse|/factory|/[a-z-]+/[a-z-]+-th\d+", u):
        return "listing"
    return "other"


def who_wins(brand) -> dict:
    """โดเมนและชนิดหน้าที่ AI อ้างแทนแบรนด์ (จากรอบวัดล่าสุดของทั้งสองแบบ)"""
    me = _apex(brand["domain"])
    doms, kinds, examples = Counter(), Counter(), {}
    srcs = []
    v = db.last_ai_visibility(brand["id"])
    for r in _rows(v["report"]) if v else []:
        doms.update(d for d in (r.get("others") or []) if _apex(d) != me)
        srcs += r.get("citations") or []
    s = db.last_ai_serp(brand["id"])
    for r in _rows(s["report"]) if s else []:
        doms.update(d for d in (r.get("others") or []) if _apex(d) != me and "google." not in d)
        srcs += r.get("sources") or []
    for u in srcs:
        host = _apex(urlparse(u).netloc)
        if not host or host == me or host.startswith("google.") or host.endswith(".google.com"):
            continue
        k = page_kind(u)
        kinds[k] += 1
        examples.setdefault(k, [])
        clean = u.split("?utm_")[0]
        if clean not in examples[k] and len(examples[k]) < 3:
            examples[k].append(clean)
    top_kind = kinds.most_common(1)[0][0] if kinds else None
    return {"domains": doms.most_common(6), "kinds": kinds.most_common(), "top_kind": top_kind, "examples": examples, "n": sum(kinds.values())}


# ---------- สุขภาพคอนเทนต์ ----------
IMG_MD = re.compile(r"\n*!\[([^\]]*)\]\(([^)]+)\)\n*")
LINK_MD = re.compile(r"\]\((https?://[^)\s]+)\)")


def _status(url: str, http=None) -> int:
    if http:
        return http(url)
    import httpx
    try:
        r = httpx.get(url, timeout=12, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (compatible; JerAI-results)"})
        return r.status_code
    except Exception:
        return 0


def _page_html(url: str) -> str:
    import httpx
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (compatible; JerAI-results)"})
        return r.text if r.status_code == 200 else ""
    except Exception:
        return ""


def site_links_to_articles(brand, page=None) -> dict:
    """เว็บลูกค้าลิงก์ไปบทความ /geo แล้วหรือยัง — หน้าแรก (เมนู) + หน้าแรกที่ลิงก์ไปภายในอีก 1 หน้า (กล่องบทความที่เกี่ยวข้อง)"""
    from . import geo_content
    from urllib.parse import urljoin
    page = page or _page_html
    base = geo_content._site_url(brand)
    home = page(base) or ""
    out = {"hub": bool(re.search(r'<a\b[^>]*href=["\'][^"\']*/geo["\'/?#]', home)), "articles_on_inner": None, "inner": None, "checked": 0}
    me = _apex(brand["domain"])
    seen, cands = set(), []
    for href in re.findall(r'<a\b[^>]*href=["\']([^"\'#]+)["\']', home):     # เฉพาะลิงก์ที่คนกดได้ ไม่ใช่ <link>/รูป/manifest
        u = urljoin(base + "/", href).split("?")[0].rstrip("/")
        path = urlparse(u).path
        if (_apex(urlparse(u).netloc) != me or u in seen or "/geo" in path or path in ("", "/th", "/en", "/zh")
                or re.search(r"/(api|feed|wp-json|cdn-cgi|static|_next|assets)(/|$)|\.(css|js|png|jpe?g|svg|webp|ico|xml|json|webmanifest|pdf)$", path, re.I)):
            continue
        seen.add(u)
        # กล่องบทความที่เกี่ยวข้องมักอยู่ในหน้าสินค้า/หมวด/บริการ — ตรวจหน้าพวกนี้ก่อน หน้าเกี่ยวกับเรา/ติดต่อ/นโยบายไว้ท้าย
        generic = bool(re.search(r"about|contact|faq|privacy|terms|policy|login|register|signin|cart|career|เกี่ยวกับ|ติดต่อ|คำถาม", unquote(path), re.I))
        cands.append((0 if generic else 1 + path.strip("/").count("/"), u))
    cands.sort(key=lambda x: -x[0])
    for _, u in cands:
        html = page(u) or ""
        if not html:
            continue
        out["checked"] += 1
        if re.search(r'href=["\'][^"\']*/geo/\d+', html):
            out["inner"], out["articles_on_inner"] = u, True
            break
        out["inner"], out["articles_on_inner"] = u, False
        if out["checked"] >= 3:
            break
    return out


def content_health(brand, http=None, fix: bool = True, page=None) -> dict:
    """รูปหัวบทความเสีย/เป็นโลโก้ → เอาออก (fix) · ลิงก์ภายในที่ตาย → รายงาน · บริบทเว็บบาง → ดึงใหม่ · index ใน Google"""
    from . import image_finder as IMF, ai_client, geo_content
    me = _apex(brand["domain"])
    items = [it for it in db.list_content(brand["id"]) if it["status"] == "published"]
    img_fixed, img_ok, no_img, dead, checked = [], 0, 0, [], {}
    for it in items:
        body = it["body_md"] or ""
        m = IMG_MD.search(body)
        if not m:
            no_img += 1
        else:
            alt, url = m.group(1), m.group(2)
            own = "/static/gen/" in url                         # รูปที่ระบบสร้างเอง: alt = หัวข้อบทความ ไม่ใช่โลโก้
            bad = not own and bool(IMF._SKIP_ALT.search(alt) or IMF._GOV_HOST.search(urlparse(url).netloc))
            if not bad:
                code = checked.setdefault(url, _status(url, http))
                bad = code not in (200, 0)                      # 0 = เครือข่ายล้ม ไม่ตัดสินว่าเสีย
            if bad:
                if fix:
                    db.update_content_body(it["id"], IMG_MD.sub("\n\n", body, count=1).strip())
                    db.set_image_note(it["id"], f"ไม่มีรูป — ฝ่ายผลลัพธ์เอารูปที่เสีย/เป็นโลโก้ออก (เดิม: ![{alt[:80]}]({url[:180]}))")
                img_fixed.append(it["id"])
            else:
                img_ok += 1
        for u in LINK_MD.findall(body):
            if _apex(urlparse(u).netloc) != me or u in checked or len(checked) > 60:
                continue
            code = checked.setdefault(u, _status(u, http))
            if code == 404:
                dead.append({"content_id": it["id"], "url": u})
    ctx_refreshed = False
    if fix and ai_client.context_stale(brand["site_context"]):
        try:
            txt = ai_client.site_context(geo_content._site_url(brand))
            if txt and len(txt) > len(brand["site_context"] or ""):
                db.set_brand_site_context(brand["id"], txt); ctx_refreshed = True
        except Exception:
            pass
    idx = {}
    g = db.last_gsc(brand["id"])
    try:
        idx = (json.loads(g["report"]) or {}).get("index") or {} if g and g["report"] else {}
    except Exception:
        idx = {}
    links = site_links_to_articles(brand, page) if items else {"hub": None, "articles_on_inner": None, "inner": None}
    return {"links": links, "published": len(items), "img_ok": img_ok, "img_fixed": img_fixed, "no_img": no_img, "dead_links": dead[:10],
            "ctx_refreshed": ctx_refreshed, "ctx_chars": len((db.get_brand(brand["id"])["site_context"] or "")),
            "indexed": idx.get("indexed"), "index_total": idx.get("total"), "discovered": idx.get("discovered"), "unknown": idx.get("unknown")}


# ---------- งานของทีมเว็บลูกค้า ----------
def check_task(t, fetch=None) -> tuple[bool | None, str]:
    """(เสร็จไหม, หมายเหตุ) — None = ตรวจเองไม่ได้ (manual)"""
    kind, url = t["check_kind"] or "manual", (t["check_url"] or "").strip()
    if kind == "manual" or not url:
        return None, "รอเจ้าของกดเสร็จ"
    if fetch is None:
        import httpx

        def fetch(u):
            r = httpx.get(u, timeout=15, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (compatible; JerAI-results)"})
            return r.status_code, r.text
    try:
        code, html = fetch(url)
    except Exception as e:
        return False, f"เปิดไม่ได้ ({type(e).__name__})"
    if kind == "exists":
        return (code == 200), (f"เปิดได้ ({code})" if code == 200 else f"ยังขึ้น {code}")
    need = (t["check_text"] or "").strip()
    if code != 200:
        return False, f"หน้าตรวจขึ้น {code}"
    return (need in (html or "")), (f"พบ \"{need}\" แล้ว" if need in (html or "") else f"ยังไม่พบ \"{need}\"")


def nudge_text(brand, t, days: int) -> str:
    when = str(t["created_at"])[:10]
    return (f"สวัสดีครับ ขอติดตามงาน \"{t['title']}\" ของเว็บ {brand['domain']} ที่ส่งไปเมื่อ {when} ({days} วัน)\n"
            + (f"ระบบตรวจล่าสุด: {t['last_check_note']} ({t['check_url']})\n" if t["check_url"] and t["last_check_note"] else "")
            + "งานนี้เป็นตัวที่จะทำให้ AI เริ่มหยิบเว็บไปตอบ — ถ้าติดตรงไหนบอกได้เลยครับ เราช่วยดูสเปกหรือตรวจหน้าให้ก่อนขึ้นจริงได้\n"
            "พอขึ้นแล้วเราจะวัดผลซ้ำทันทีและส่งตัวเลขก่อน/หลังให้ครับ")


def tasks_step(brand, now, fetch=None, remeasure=None) -> dict:
    done, open_, nudges = [], [], []
    for t in db.list_result_tasks(brand["id"], "open"):
        ok, note = check_task(t, fetch)
        db.update_result_task(t["id"], last_check_at=db.now(), last_check_note=note)
        if ok:
            db.update_result_task(t["id"], status="done", done_at=db.now())
            done.append({"id": t["id"], "title": t["title"], "note": note})
            continue
        age = (now - (_ts(t["created_at"]) or now)).days
        item = {"id": t["id"], "title": t["title"], "age": age, "note": note}
        last_n = _ts(t["nudged_at"])
        if age >= NUDGE_AFTER_DAYS and (not last_n or (now - last_n).days >= NUDGE_AFTER_DAYS):
            db.update_result_task(t["id"], nudged_at=db.now())
            item["nudge"] = nudge_text(brand, db.get_result_task(t["id"]), age)
            nudges.append(item)
        open_.append(item)
    if done and remeasure:
        try:
            remeasure(brand["id"])
        except Exception:
            pass
    return {"done": done, "open": open_, "nudges": nudges}


# ---------- สิ่งที่ควรทำ (กติกาจากข้อมูลจริง) ----------
def actions(brand, vis, wins, health, tasks) -> list[str]:
    out = []
    has_task = lambda word: any(word in (t["title"] or "") for t in db.list_result_tasks(brand["id"]))
    if wins["top_kind"] == "listing" and not has_task("ทำเล") and not has_task("หมวด"):
        ex = (wins["examples"].get("listing") or [""])[0]
        out.append(f"AI อ้าง{KIND_TH['listing']}ของคู่แข่งมากที่สุด ({dict(wins['kinds']).get('listing', 0)} จาก {wins['n']} แหล่ง เช่น {ex}) "
                   "→ เพิ่มงานให้ทีมเว็บ: หน้ารวมรายการตามทำเล × ประเภท ที่มีรายการใน HTML + schema ItemList")
    elif wins["top_kind"] == "article" and health["published"] < 10:
        out.append("AI อ้างบทความ/คู่มือของคู่แข่งมากที่สุด → เพิ่มจำนวนบทความต่อสัปดาห์ (ตอนนี้เผยแพร่ "
                   f"{health['published']} ชิ้น)")
    if health["index_total"] and (health["indexed"] or 0) * 2 < health["index_total"]:
        lk = health.get("links") or {}
        head = (f"Google index บทความแค่ {health['indexed']} จาก {health['index_total']} หน้า "
                f"(เห็นแล้วยังไม่อ่าน {health['discovered'] or 0} · ยังไม่รู้จัก {health['unknown'] or 0})")
        if lk.get("hub") and lk.get("articles_on_inner"):     # ลิงก์ภายในมีครบแล้ว — อย่าสั่งทีมเว็บทำซ้ำ
            out.append(head + " · เว็บลิงก์ไปบทความครบแล้ว (เมนู + กล่องบทความในหน้าภายใน) → ขอ index ราย URL ใน Search Console แล้วรอ Google มาอ่าน")
        elif lk.get("hub") and lk.get("articles_on_inner") is False:
            out.append(head + f" · มีลิงก์ \"บทความ\" ในเมนูแล้ว แต่หน้าภายในที่สุ่มตรวจ {lk.get('checked', 1)} หน้ายังไม่ลิงก์ไปบทความ → เพิ่มกล่องบทความที่เกี่ยวข้อง 2–3 ลิงก์ในหน้าสินค้า/หมวด")
        elif lk.get("hub"):
            out.append(head + " · มีลิงก์ \"บทความ\" ในเมนูแล้ว → ขอ index ราย URL ใน Search Console แล้วรอ Google มาอ่าน")
        else:
            out.append(head + " → เพิ่มลิงก์ \"บทความ\" ในเมนู และกล่องบทความที่เกี่ยวข้องในหน้าสินค้า/หมวด")
    if health["img_fixed"]:
        out.append(f"เอารูปหัวบทความที่เสีย/เป็นโลโก้ออกแล้ว {len(health['img_fixed'])} ชิ้น")
    if health["dead_links"]:
        out.append(f"ลิงก์ภายในในบทความเปิดไม่ได้ {len(health['dead_links'])} ลิงก์ (เช่น {health['dead_links'][0]['url']}) → แก้หรือเขียนชิ้นนั้นใหม่")
    if health["ctx_refreshed"]:
        out.append("ดึงข้อมูลเว็บที่ใช้เขียนคอนเทนต์ใหม่ (ของเดิมบาง/เป็นหน้า placeholder)")
    for t in tasks["open"]:
        if t["age"] >= NUDGE_AFTER_DAYS:
            out.append(f"งานทีมเว็บ \"{t['title']}\" ค้าง {t['age']} วัน ({t['note']}) → ตามทีมเว็บ")
    for t in tasks["done"]:
        out.append(f"ทีมเว็บทำ \"{t['title']}\" แล้ว ({t['note']}) → สั่งวัดผลซ้ำแล้ว")
    return out


# ---------- รีวิวรายแบรนด์ ----------
def due(brand, now=None) -> bool:
    now = now or _now()
    last = db.last_result_review(brand["id"])
    if not last:
        return True
    lt = _ts(last["created_at"]) or now
    v = db.last_ai_visibility(brand["id"])
    newer = bool(v and (_ts(v["checked_at"]) or lt) > lt)
    return (now - lt).days >= REVIEW_EVERY_DAYS or (newer and (now - lt).days >= 1)


def _recent_done(bid: int, now) -> bool:
    for t in db.list_result_tasks(bid, "done"):
        d = _ts(t["done_at"])
        if d and (now - d).days < STALL_WEEKS * 7:
            return True
    return False


def review(brand, now=None, http=None, fetch=None, remeasure=None, alert=None) -> dict:
    now = now or _now()
    vis = visibility(brand["id"])
    wins = who_wins(brand)
    health = content_health(brand, http=http)
    tasks = tasks_step(brand, now, fetch=fetch, remeasure=remeasure)
    if not vis["series"] and not vis["serp"]:
        state = "new"
    elif vis["improved"]:
        state = "win"
    elif vis["stalled"] and any(t["age"] >= NUDGE_AFTER_DAYS for t in tasks["open"]):
        state = "blocked"
    elif vis["stalled"] and _recent_done(brand["id"], now):
        state = "waiting"                                  # ทีมเว็บเพิ่งทำงานเสร็จ — ให้เวลา Google/AI เก็บหน้าก่อน ไม่เตือนว่าไม่ขยับ
    elif vis["stalled"]:
        state = "stalled"
    else:
        state = "ok"
    rep = {"at": db.now(), "state": state, "vis": vis, "wins": wins, "health": health, "tasks": tasks,
           "actions": actions(brand, vis, wins, health, tasks)}
    prev = db.last_result_review(brand["id"])
    db.add_result_review(brand["id"], state, json.dumps(rep, ensure_ascii=False, default=str), now.isoformat(timespec="seconds"))
    _alerts(brand, rep, prev, now, alert)
    return rep


def _alerts(brand, rep, prev, now, alert=None) -> None:
    if alert is None:
        from . import pipeline
        alert = lambda msg, link: pipeline.alert_admins(msg, link, "results")
    link = "/admin/results"
    name = brand["name"] or brand["domain"]
    if rep["state"] == "win" and (not prev or prev["state"] != "win"):
        v = rep["vis"]["last"] or {}
        alert(f"🎉 {name} ถูก AI แนะนำแล้ว {v.get('hits', 0)} จาก {v.get('asked', 0)} ครั้ง — ส่งต่อ CMO ทำเคสศึกษา"
              + ("" if brand["case_study_ok"] else " (ยังไม่ได้รับอนุญาตใช้ชื่อแบรนด์)"), link)
    if rep["state"] in ("stalled", "blocked"):
        last_alert = _ts(db.get_setting(f"results_stall_alert_{brand['id']}"))
        if not last_alert or (now - last_alert).days >= STALL_ALERT_EVERY_DAYS:
            db.set_setting(f"results_stall_alert_{brand['id']}", db.now())
            first = (rep["actions"] or ["ดูหน้าผลลัพธ์ลูกค้า"])[0]
            alert(f"{name}: AI ยังไม่แนะนำเลย {STALL_WEEKS} สัปดาห์ติด — ควรทำ: {first[:220]}", link)
    for n in rep["tasks"]["nudges"]:
        alert(f"{name}: งาน \"{n['title']}\" ค้าง {n['age']} วัน — ร่างข้อความตามทีมเว็บไว้ให้แล้ว (คัดลอกส่งเอง)", link)


def _remeasure(bid: int) -> None:
    from . import geo_worker
    geo_worker.run_for_brand(bid)


@office.track("results", "รีวิวผลลัพธ์ลูกค้ารายสัปดาห์")
def run(now=None, force: bool = False, http=None, fetch=None, remeasure=_remeasure, alert=None) -> dict:
    if not enabled() and not force:
        return {"skipped": "ปิดอยู่"}
    now = now or _now()
    out = {"at": db.now(), "reviewed": 0, "win": 0, "stalled": 0, "img_fixed": 0, "tasks_done": 0, "nudges": 0, "brands": []}
    for b in brands():
        if not force and not due(b, now):
            continue
        try:
            rep = review(b, now, http=http, fetch=fetch, remeasure=remeasure, alert=alert)
        except Exception as e:
            out["brands"].append({"id": b["id"], "error": type(e).__name__})
            continue
        out["reviewed"] += 1
        out["win"] += rep["state"] == "win"
        out["stalled"] += rep["state"] in ("stalled", "blocked")
        out["img_fixed"] += len(rep["health"]["img_fixed"])
        out["tasks_done"] += len(rep["tasks"]["done"])
        out["nudges"] += len(rep["tasks"]["nudges"])
        out["brands"].append({"id": b["id"], "state": rep["state"]})
    db.set_setting("results_last", json.dumps(out, ensure_ascii=False))
    return out


def run_one(bid: int, **kw) -> dict:
    return review(db.get_brand(bid), **kw)


# ---------- สรุป ----------
def summary() -> list[dict]:
    out = []
    for b in brands():
        r = db.last_result_review(b["id"])
        rep = json.loads(r["report"]) if r and r["report"] else None
        out.append({"brand": b, "review": rep, "state": r["state"] if r else "new", "at": str(r["created_at"])[:16].replace("T", " ") if r else "",
                    "tasks": db.list_result_tasks(b["id"])})
    order = {"blocked": 0, "stalled": 1, "win": 2, "waiting": 3, "ok": 4, "new": 5}
    out.sort(key=lambda x: (order.get(x["state"], 5), x["brand"]["id"]))
    return out


def status_text() -> str:
    rows = [s for s in summary() if s["review"]]
    if not rows:
        return ""
    by = Counter(s["state"] for s in rows)
    open_t = sum(1 for s in rows for t in s["tasks"] if t["status"] == "open")
    old = sum(1 for s in rows for t in (s["review"]["tasks"]["open"] if s["review"] else []) if t["age"] >= NUDGE_AFTER_DAYS)
    parts = [f"ผลลัพธ์ลูกค้า: {len(rows)} แบรนด์"]
    if by.get("win"):
        parts.append(f"ขยับขึ้น {by['win']}")
    if by.get("waiting"):
        parts.append(f"รอผลหลังทีมเว็บทำงานเสร็จ {by['waiting']}")
    if by.get("stalled") or by.get("blocked"):
        parts.append(f"ไม่ขยับ {STALL_WEEKS} สัปดาห์ {by.get('stalled', 0) + by.get('blocked', 0)}")
    if open_t:
        parts.append(f"งานทีมเว็บค้าง {open_t}" + (f" (เกิน {NUDGE_AFTER_DAYS} วัน {old})" if old else ""))
    return " · ".join(parts)
