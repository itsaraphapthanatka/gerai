"""รายงานแบบ 4 ช่อง (stage 05-core, flag report_v2) — ตัวแปลงข้อมูลเดิมเป็นมุมมองเดียว ไม่เขียนฐานข้อมูล
อ่านจาก leads.audit_json (ตรวจหน้าแรก) · snapshot_json (04A) · deep_json (ไต่เว็บ + อันดับ Google) · deep_ai_json (ถาม AI)
นิยามช่องและสถานะตาม docs/rebuild/METRIC_DICTIONARY.md:
  website_readiness = ความพร้อมของเว็บ · web_search_presence = การปรากฏในผลค้นเว็บ (ไม่ใช่ AI)
  ai_brand_mention = AI พูดถึงแบรนด์ · ai_citation = AI อ้างอิงเว็บของคุณ
  state: found | absent | partial | not_tested | unavailable · not_tested/unavailable ไม่มีตัวเลข (value = None) ห้ามเป็น 0"""
from __future__ import annotations

import json

from . import deep_audit, leads as L

ENGINE_LABEL = {"chatgpt": "ChatGPT", "gemini": "Gemini", "perplexity": "Perplexity", "claude": "Claude"}
TESTED = ("found", "absent", "partial")


def _json(lead, col: str) -> dict:
    try:
        raw = lead[col] if col in lead.keys() else None
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _lane(key: str, title: str, state: str, value: str | None = None, detail: str = "", method: str = "", denominator=None,
          label: str | None = None) -> dict:
    if state not in TESTED:
        value = None                                  # ไม่ได้ทดสอบ/ระบบไม่พร้อม = ไม่มีตัวเลข
    return {"key": key, "title": title, "state": state, "label": label, "value": value, "detail": detail, "method": method,
            "denominator": denominator if state in TESTED else None}


def readiness(report: dict, snap: dict, t1: dict) -> dict:
    title = "ความพร้อมของเว็บ"
    checks = [c for c in (report.get("checks") or []) if c.get("key") not in L.PUBLIC_IGNORE]
    home = next((c for c in checks if c.get("key") == "home"), None)
    if not checks:
        st = snap.get("state")
        if st in ("queued", "running"):
            return _lane("website_readiness", title, "not_tested", detail="กำลังตรวจ")
        return _lane("website_readiness", title, "unavailable" if st == "failed" else "not_tested",
                     detail="ตรวจไม่สำเร็จ ลองตรวจใหม่" if st == "failed" else "ยังไม่ได้ตรวจ")
    if (home and home.get("status") == "fail") or snap.get("home_ok") is False:
        return _lane("website_readiness", title, "unavailable", detail="เปิดหน้าแรกของเว็บไม่ได้ระหว่างตรวจ จึงยังสรุปไม่ได้")
    n_ok = sum(1 for c in checks if c.get("status") == "ok")
    n_fail = sum(1 for c in checks if c.get("status") == "fail")
    hl = t1.get("health") if t1.get("state") == "done" else None
    parts = [f"หน้าแรกผ่าน {n_ok} จาก {len(checks)} ข้อ" + (f" · ต้องแก้ {n_fail} ข้อ" if n_fail else "")]
    if hl:
        parts.append(f"ไต่ {t1.get('crawl', {}).get('n', hl.get('urls'))} หน้า: คะแนน {hl['score']}/100 · Error {hl['errors']}")
    partial = report.get("partial") or snap.get("state") == "partial"
    if partial:
        parts.append("หมดเวลาก่อนตรวจครบ ส่วนที่ไม่ได้ตรวจไม่นับเป็นไม่ผ่าน")
    return _lane("website_readiness", title, "partial" if partial else ("absent" if n_fail else "found"),
                 value=f"{n_ok}/{len(checks)}", detail=" · ".join(parts),
                 method="อ่านหน้าเว็บสาธารณะ robots.txt และ sitemap" + (" + ไต่เว็บ" if hl else ""), denominator=len(checks),
                 label=None if partial else ("มีข้อที่ต้องแก้" if n_fail else "ผ่านทุกข้อที่ตรวจ"))


def search_presence(t1: dict, prog: str) -> dict:
    title = "การปรากฏในผลค้นเว็บ"
    method = "ผลค้น Google ประเทศไทย ครั้งเดียว ณ วันที่ตรวจ (เป็นผลค้นเว็บ ไม่ใช่คำตอบ AI)"
    if prog == "running":
        return _lane("web_search_presence", title, "not_tested", detail="กำลังเช็กอันดับ", method=method)
    if t1.get("state") == "error":
        return _lane("web_search_presence", title, "unavailable", detail="เช็กอันดับไม่สำเร็จรอบนี้", method=method)
    if t1.get("state") != "done":
        return _lane("web_search_presence", title, "not_tested", detail="ยังไม่ได้เช็กอันดับ", method=method)
    rk = t1.get("ranking") or {}
    n, ok = rk.get("n") or 0, rk.get("ok", rk.get("n") or 0)
    if not n:
        return _lane("web_search_presence", title, "not_tested", detail="ยังหาคำค้นจากเนื้อหาเว็บไม่ได้", method=method)
    if ok == 0:
        return _lane("web_search_presence", title, "unavailable", detail="ระบบค้นไม่ตอบชั่วคราว ไม่นับเป็นไม่ติด", method=method)
    p1 = rk.get("page1") or 0
    detail = f"ติดหน้าแรก {p1} จาก {ok} คำที่ค้นสำเร็จ"
    if ok < n:
        detail += f" · ค้นไม่สำเร็จ {n - ok} คำ (ไม่นับ)"
        state = "partial"
    else:
        state = "found" if p1 else "absent"
    return _lane("web_search_presence", title, state, value=f"{p1}/{ok}", detail=detail, method=method, denominator=ok)


def _ai_lane(key: str, title: str, t2: dict, prog: str, field: str, word: str) -> dict:
    eng = ENGINE_LABEL.get(t2.get("engine"), "AI")
    method = f"ถาม {eng} พร้อมค้นเว็บ ครั้งเดียว ณ วันที่ตรวจ"
    if prog == "running":
        return _lane(key, title, "not_tested", detail="กำลังถาม AI", method=method)
    st = t2.get("state")
    if st == "queued":
        return _lane(key, title, "not_tested", detail="อยู่ในคิว ทีมจะวัดให้ภายใน 1 วันทำการ", method=method)
    if st == "error":
        return _lane(key, title, "unavailable", detail="ถาม AI ไม่สำเร็จรอบนี้", method=method)
    if st != "done":
        return _lane(key, title, "not_tested", detail="ยังไม่ได้ทดสอบ · วัดให้เมื่อขอแผน", method=method)
    asked, total = t2.get("asked") or 0, len(t2.get("rows") or [])
    if not asked:
        return _lane(key, title, "unavailable", detail="AI ไม่ตอบทุกคำถามรอบนี้", method=method)
    hit = t2.get(field) or 0
    detail = f"{word} {hit} จาก {asked} คำถามที่ถามสำเร็จ"
    if total > asked:
        detail += f" · ถามไม่สำเร็จ {total - asked} ข้อ (ไม่นับ)"
        state = "partial"
    else:
        state = "found" if hit else "absent"
    return _lane(key, title, state, value=f"{hit}/{asked}", detail=detail, method=method + f" · {(t2.get('at') or '')[:10]}", denominator=asked)


def _path(u: str) -> str:
    return ("/" + u.split("/", 3)[3]) if u and u.count("/") >= 3 else "/"


def actions(report: dict, t1: dict, limit: int = 3) -> list[dict]:
    """สิ่งที่ควรทำก่อน — จากปัญหาที่ตรวจเจอจริงเท่านั้น (Error จากการไต่เว็บก่อน แล้วข้อที่ตกในหน้าแรก) · ไม่เดาเวลาทำงาน"""
    out, seen = [], set()
    if t1.get("state") == "done":
        for i in t1.get("issues") or []:
            if i.get("severity") != "Error" or len(out) >= limit:
                continue
            urls = i.get("urls") or []
            nu = i.get("n_urls") or len(urls)
            out.append({"title": i.get("label") or "", "why": i.get("fix") or "", "page": _path(urls[0]) if urls else "",
                        "evidence": f"พบใน {nu} หน้า" if nu else "", "source": "ไต่เว็บ"})
            seen.add(i.get("key"))
    for s in L.public_summary(report, limit=limit):
        if len(out) >= limit:
            break
        if s["key"] in seen:
            continue
        out.append({"title": s["label"], "why": s["why"], "page": "หน้าแรก", "evidence": (s["detail"] or "")[:140], "source": "ตรวจหน้าแรก"})
    return out


def build(lead, snap: dict | None = None) -> dict:
    report = _json(lead, "audit_json")
    snap = snap or {}
    t1, t2 = deep_audit.tier1(lead), deep_audit.tier2(lead)
    prog = deep_audit.progress(lead)
    lanes = [readiness(report, snap, t1), search_presence(t1, prog["tier1"]),
             _ai_lane("ai_brand_mention", "AI พูดถึงแบรนด์", t2, prog["ai"], "mentioned", "เอ่ยถึงแบรนด์คุณ"),
             _ai_lane("ai_citation", "AI อ้างอิงเว็บของคุณ", t2, prog["ai"], "cited", "อ้างอิงเว็บคุณ")]
    tested = sum(1 for x in lanes if x["state"] in TESTED)
    crawl = t1.get("crawl") or {}
    scope = {"checked": [], "not_checked": []}
    if report.get("checks"):
        scope["checked"].append("หน้าแรก robots.txt sitemap และไฟล์สำหรับ AI")
    if crawl.get("n"):
        scope["checked"].append(f"ไต่เว็บ {crawl['n']} หน้า" + (" (ครบเพดาน เว็บยังมีหน้ามากกว่านี้)" if crawl.get("truncated") else ""))
    if lanes[1]["state"] in TESTED:
        scope["checked"].append(f"อันดับ Google ของ {lanes[1]['denominator']} คำค้นที่ระบบเลือกจากเนื้อหาเว็บ")
    if lanes[2]["state"] in TESTED:
        scope["checked"].append(f"ถาม {ENGINE_LABEL.get(t2.get('engine'), 'AI')} {lanes[2]['denominator']} คำถาม")
    scope["not_checked"] = ["ข้อมูลจาก Google Search Console (ต้องให้สิทธิ์เข้าถึงก่อน)", "ผลทางธุรกิจ เช่น ยอดติดต่อหรือยอดขาย"]
    for x in lanes:
        if x["state"] not in TESTED:
            scope["not_checked"].append(f"{x['title']}: {x['detail']}")
    return {"lanes": lanes, "tested": tested, "total": len(lanes), "actions": actions(report, t1), "scope": scope,
            "running": prog["tier1"] == "running" or prog["ai"] == "running", "audit_at": (lead["audit_at"] or "")[:16].replace("T", " "),
            "brand": t1.get("brand") if t1.get("state") == "done" else None,
            "keywords": (t1.get("keywords") or [])[:5] if t1.get("state") == "done" else [],
            "competitors": [c["domain"] for c in ((t1.get("ranking") or {}).get("competitors") or [])[:3]]}
