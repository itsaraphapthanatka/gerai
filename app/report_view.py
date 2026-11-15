"""รายงานแบบ 4 ช่อง (stage 05-core, flag report_v2) — ตัวแปลงข้อมูลเดิมเป็นมุมมองเดียว ไม่เขียนฐานข้อมูล
อ่านจาก leads.audit_json (ตรวจหน้าแรก) · snapshot_json (04A) · deep_json (ไต่เว็บ + อันดับ Google) · deep_ai_json (ถาม AI)
นิยามช่องและสถานะตาม docs/rebuild/METRIC_DICTIONARY.md:
  website_readiness = ความพร้อมของเว็บ · web_search_presence = การปรากฏในผลค้นเว็บ (ไม่ใช่ AI)
  ai_brand_mention = AI พูดถึงแบรนด์ · ai_citation = AI อ้างอิงเว็บของคุณ
  state: found | absent | partial | not_tested | unavailable · not_tested/unavailable ไม่มีตัวเลข (value = None) ห้ามเป็น 0"""
from __future__ import annotations

import hashlib
import json
from urllib.parse import urlparse

from . import db, deep_audit, leads as L

ENGINE_LABEL = {"chatgpt": "ChatGPT", "gemini": "Gemini", "perplexity": "Perplexity", "claude": "Claude"}
TESTED = ("found", "absent", "partial")


def _json(lead, col: str) -> dict:
    try:
        raw = lead[col] if col in lead.keys() else None
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _lane(key: str, title: str, state: str, value: str | None = None, detail: str = "", method: str = "", denominator=None,
          label: str | None = None, m: dict | None = None) -> dict:
    if state not in TESTED:
        value = None                                  # ไม่ได้ทดสอบ/ระบบไม่พร้อม = ไม่มีตัวเลข
    return {"key": key, "title": title, "state": state, "label": label, "value": value, "detail": detail, "method": method,
            "denominator": denominator if state in TESTED else None, "m": m if state in TESTED or state == "unavailable" else None,
            "evidence_refs": []}


def _measure(protocol: dict | None, default_provider: str | None, method: str, at: str | None, attempted=None, succeeded=None,
             qset: list | None = None) -> dict:
    """ข้อมูลประกอบตัวเลขตาม METRIC_DICTIONARY §3 · รอบเก่าที่ไม่ได้บันทึกวิธีวัด = protocol_id None (legacy)"""
    p = protocol or {}
    failed = (attempted - succeeded) if (attempted is not None and succeeded is not None) else None
    return {"protocol_id": p.get("id"), "protocol_version": p.get("version"), "provider": p.get("provider") or default_provider,
            "method": method, "measured_at": (at or "")[:16].replace("T", " ") or None, "attempted": attempted, "succeeded": succeeded,
            "failed": failed, "denominator": succeeded, "qset": _qhash(qset) if qset else None}


def _qhash(items: list) -> str:
    return hashlib.sha1("\n".join(sorted(str(x).strip().lower() for x in items)).encode()).hexdigest()[:10]


def readiness(report: dict, snap: dict, t1: dict, at: str | None = None) -> dict:
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
    proto = {"id": "home-check-v1" + (" + crawl-v1" if hl else ""), "version": 1, "provider": "httpx"}
    m = _measure(proto, "httpx", "อ่านหน้าแรก robots.txt sitemap" + (" และไต่เว็บ" if hl else ""), at, len(checks), len(checks))
    if partial:
        parts.append("หมดเวลาก่อนตรวจครบ ส่วนที่ไม่ได้ตรวจไม่นับเป็นไม่ผ่าน")
    return _lane("website_readiness", title, "partial" if partial else ("absent" if n_fail else "found"),
                 value=f"{n_ok}/{len(checks)}", detail=" · ".join(parts),
                 method="อ่านหน้าเว็บสาธารณะ robots.txt และ sitemap" + (" + ไต่เว็บ" if hl else ""), denominator=len(checks),
                 label=None if partial else ("มีข้อที่ต้องแก้" if n_fail else "ผ่านทุกข้อที่ตรวจ"), m=m)


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
    m = _measure(rk.get("protocol"), None, "ค้น Google ประเทศไทยด้วยคำค้นที่เลือกจากเนื้อหาเว็บ ดูว่าโดเมนคุณอยู่อันดับไหน (ไล่ถึง 20)",
                 t1.get("at"), n, ok, [r.get("keyword") for r in rk.get("rows") or []])
    if ok == 0:
        return _lane("web_search_presence", title, "unavailable", detail="ระบบค้นไม่ตอบชั่วคราว ไม่นับเป็นไม่ติด", method=method, m=m)
    p1 = rk.get("page1") or 0
    detail = f"ติดหน้าแรก {p1} จาก {ok} คำที่ค้นสำเร็จ"
    if ok < n:
        detail += f" · ค้นไม่สำเร็จ {n - ok} คำ (ไม่นับ)"
        state = "partial"
    else:
        state = "found" if p1 else "absent"
    return _lane("web_search_presence", title, state, value=f"{p1}/{ok}", detail=detail, method=method, denominator=ok, m=m)


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
    m = _measure(t2.get("protocol"), t2.get("engine"), f"ถาม {eng} พร้อมค้นเว็บ แล้วดูว่าคำตอบ" + ("เอ่ยชื่อหรืออ้างเว็บคุณ" if field == "mentioned" else "ลิงก์หรืออ้างเว็บคุณ"),
                 t2.get("at"), total, asked, [r.get("prompt") for r in t2.get("rows") or []])
    if not asked:
        return _lane(key, title, "unavailable", detail="AI ไม่ตอบทุกคำถามรอบนี้", method=method, m=m)
    hit = t2.get(field) or 0
    detail = f"{word} {hit} จาก {asked} คำถามที่ถามสำเร็จ"
    if total > asked:
        detail += f" · ถามไม่สำเร็จ {total - asked} ข้อ (ไม่นับ)"
        state = "partial"
    else:
        state = "found" if hit else "absent"
    return _lane(key, title, state, value=f"{hit}/{asked}", detail=detail, method=method + f" · {(t2.get('at') or '')[:10]}", denominator=asked, m=m)


def _path(u: str) -> str:
    return ("/" + u.split("/", 3)[3]) if u and u.count("/") >= 3 else "/"


def _host(u: str) -> str:
    try:
        h = (urlparse(u).hostname or "").lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def safe_url(u: str | None, apex: str) -> str | None:
    """ลิงก์ออกที่แสดงได้: http/https และเป็นโดเมนของลูกค้าเท่านั้น · อย่างอื่นแสดงเป็นข้อความ (ไม่ทำลิงก์)"""
    if not u or not apex:
        return None
    u = u.strip()
    try:
        pu = urlparse(u)
    except ValueError:
        return None
    h = _host(u)
    if pu.scheme not in ("http", "https") or not h or any(c in u for c in "\"'<> \n\r\t\\"):
        return None
    return u if (h == apex or h.endswith("." + apex)) else None


class Evidence:
    """หลักฐานที่สร้างจากข้อมูลดิบเดิม (อ่านอย่างเดียว) · id คงที่ภายในรายงานเดียว · ข้อความดิบให้ Jinja escape ตอนแสดง (ห้าม |safe)"""
    KIND = {"page_check": "ตรวจหน้าแรก", "page_status": "ไต่เว็บ", "search_result": "ผลค้น Google", "ai_answer": "คำตอบ AI"}

    def __init__(self, apex: str):
        self.apex, self.items = apex, {}

    def _show(self, x: str) -> dict:
        h = _host(x)
        own = bool(self.apex) and (h == self.apex or h.endswith("." + self.apex))
        return {"text": _path(x) if own else x, "href": safe_url(x, self.apex)}

    def add(self, kind: str, excerpt: str, url: str | None = None, at: str | None = None, provider: str | None = None,
            protocol: str | None = None, urls: list | None = None) -> str:
        eid = f"ev{len(self.items) + 1}"
        self.items[eid] = {"id": eid, "kind": kind, "kind_label": self.KIND.get(kind, kind), "excerpt": (excerpt or "")[:600],
                           "urls": [self._show(x) for x in ([url] if url else []) + list(urls or []) if isinstance(x, str)][:5],
                           "captured_at": (at or "")[:16].replace("T", " ") or None, "provider": provider, "protocol": protocol}
        return eid


# งานโดยประมาณต่อชนิดปัญหา (ติดป้าย "ประมาณ" ทุกครั้ง) · ไม่มีในตาราง = ประเมินหลังดูเว็บ
EFFORT = {"mailto": "~1 ชม.", "title_missing": "1–3 ชม.", "dup_title": "1–3 ชม.", "https": "1–2 ชม. (ตั้งค่าเซิร์ฟเวอร์)", "4xx": "1–3 ชม.",
          "orphan": "2–4 ชม.", "noindex": "~1 ชม.", "robots": "~30 นาที", "ai_bots": "~30 นาที", "llms": "~1 ชม.",
          "robots_sitemap": "~15 นาที", "sitemap": "~1 ชม.", "canonical": "1–2 ชม.", "www": "~1 ชม.", "sitemap_domain": "~1 ชม."}
SERVER_SIDE = {"5xx", "https", "www", "csr", "page_identity", "canonical"}
OWNER_LABEL = {"customer_web_team": "ทีมเว็บหรือผู้ดูแลเซิร์ฟเวอร์ของคุณ", "either": "ทีมเว็บของคุณ หรือให้เจอ.AI ช่วย"}
CONF_LABEL = {"high": "สูง", "medium": "ปานกลาง", "low": "ต่ำ"}


def _down(c: str) -> str:
    return {"high": "medium", "medium": "low"}.get(c, "low")


def actions(report: dict, t1: dict, ev: Evidence | None = None, limit: int = 3, partial: bool = False, at: str | None = None,
            site: str | None = None) -> list[dict]:
    """สิ่งที่ควรทำก่อน — จากปัญหาที่ตรวจเจอจริงเท่านั้น (Error จากการไต่เว็บก่อน แล้วข้อที่ตกในหน้าแรก)
    ความมั่นใจ = จากหลักฐาน (พบหลายหน้า/มีรายละเอียด = สูง) · ตรวจได้บางส่วน = ลดลงหนึ่งขั้น · เวลาทำงาน = ประมาณการตามชนิดปัญหา"""
    ev = ev or Evidence("")
    out, seen = [], set()
    if t1.get("state") == "done":
        for i in t1.get("issues") or []:
            if i.get("severity") != "Error" or len(out) >= limit:
                continue
            urls = i.get("urls") or []
            nu = i.get("n_urls") or len(urls)
            conf = "high" if nu >= 2 else "medium"
            ref = ev.add("page_status", f"{i.get('label') or ''} · พบใน {nu} หน้า", urls=urls[:5], at=t1.get("at"), provider="httpx", protocol="crawl-v1")
            out.append({"key": i.get("key"), "title": i.get("label") or "", "why": i.get("fix") or "", "page": _path(urls[0]) if urls else "",
                        "evidence": f"พบใน {nu} หน้า" if nu else "", "source": "ไต่เว็บ", "evidence_refs": [ref],
                        "confidence": _down(conf) if partial else conf, "effort": EFFORT.get(i.get("key")),
                        "owner": "customer_web_team" if i.get("key") in SERVER_SIDE else "either"})
            seen.add(i.get("key"))
    for s in L.public_summary(report, limit=limit):
        if len(out) >= limit:
            break
        if s["key"] in seen:
            continue
        conf = "high" if s["detail"] else "medium"
        ref = ev.add("page_check", s["detail"] or s["label"], url=site, at=at, provider="httpx", protocol="home-check-v1")
        out.append({"key": s["key"], "title": s["label"], "why": s["why"], "page": "หน้าแรก", "evidence": (s["detail"] or "")[:140],
                    "source": "ตรวจหน้าแรก", "evidence_refs": [ref], "confidence": _down(conf) if partial else conf,
                    "effort": EFFORT.get(s["key"]), "owner": "customer_web_team" if s["key"] in SERVER_SIDE else "either"})
    for a in out:
        a["confidence_label"] = CONF_LABEL[a["confidence"]]
        a["owner_label"] = OWNER_LABEL[a["owner"]]
    return out


AI_RESULT = {"cited": "อ้างอิงเว็บคุณ", "named": "เอ่ยชื่อแบรนด์", "none": "ไม่พบแบรนด์คุณ"}


def ai_tests(t2: dict, ev: Evidence, customer_qs: list[str]) -> list[dict]:
    """คำถามที่ใช้ทดสอบ AI รอบล่าสุด + ผลรายข้อ (สิ่งที่สังเกตได้จริง) · ข้อที่ถามไม่สำเร็จแสดงว่าไม่นับ"""
    if t2.get("state") != "done":
        return []
    mine = {q.strip().lower() for q in customer_qs if q}
    proto = t2.get("protocol") or {}
    prov = proto.get("provider") or t2.get("engine")
    out = []
    for r in t2.get("rows") or []:
        q = r.get("prompt") or ""
        origin = "customer" if q.strip().lower() in mine else "system"
        if "status" not in r:
            out.append({"q": q, "origin": origin, "result": "error", "result_label": "ถามไม่สำเร็จ (ไม่นับ)", "sources": [], "ref": None})
            continue
        res = "cited" if r.get("cited") else ("named" if r.get("named") else "none")
        ref = ev.add("ai_answer", r.get("excerpt") or "รอบนี้ไม่ได้เก็บข้อความคำตอบ (บันทึกเฉพาะผลสรุป)", at=t2.get("at"), provider=prov,
                     protocol=proto.get("id"))
        out.append({"q": q, "origin": origin, "result": res, "result_label": AI_RESULT[res],
                    "sources": [s for s in r.get("sources") or [] if isinstance(s, str)][:6], "ref": ref})
    return out


def _dom_in(comp: str, doms: list) -> bool:
    return any(d == comp or (d or "").endswith("." + comp) for d in doms or [])


def competitor_view(t1: dict, t2: dict, bc: dict | None) -> dict | None:
    """เทียบกับคู่แข่ง 1 ราย: เฉพาะสิ่งที่สังเกตได้ในรอบนี้ ไม่สรุปสาเหตุ · คู่แข่ง = ที่อยู่ในข้อมูลธุรกิจ ไม่งั้นโดเมนที่ติดผลค้นบ่อยสุด"""
    comp, source = None, None
    cf = ((bc or {}).get("fields") or {}).get("competitor") or {}
    if cf.get("value") and cf.get("source") in ("confirmed", "inferred"):
        comp, source = str(cf["value"]).lower(), cf["source"]
    elif t1.get("state") == "done" and (t1.get("ranking") or {}).get("competitors"):
        comp, source = t1["ranking"]["competitors"][0]["domain"], "inferred"
    if not comp:
        return None
    facts, gaps = [], []
    rows = [r for r in ((t1.get("ranking") or {}).get("rows") or []) if not r.get("error")] if t1.get("state") == "done" else []
    if rows:
        theirs = [r for r in rows if _dom_in(comp, (r.get("top") or [])[:10])]
        ours = [r for r in rows if r.get("position") and r["position"] <= 10]
        facts.append(f"ผลค้น Google: {comp} ติดหน้าแรก {len(theirs)} จาก {len(rows)} คำ · เว็บคุณติดหน้าแรก {len(ours)} จาก {len(rows)} คำ")
        gaps = [r.get("keyword") for r in theirs if not (r.get("position") and r["position"] <= 10)][:5]
    if t2.get("state") == "done" and t2.get("asked"):
        if comp in (t2.get("competitors") or []):
            cm = (t2.get("comp_mentions") or {}).get(comp, 0)
            facts.append(f"คำตอบ AI: พูดถึง {comp} ใน {cm} จาก {t2['asked']} คำถาม · พูดถึงแบรนด์คุณใน {t2.get('mentioned') or 0} จาก {t2['asked']} คำถาม")
        else:
            facts.append(f"คำตอบ AI: รอบนี้ยังไม่ได้นับการพูดถึง {comp} จะนับในการทดสอบรอบถัดไป")
    if not facts:
        return None
    return {"domain": comp, "source": source, "facts": facts, "gaps": gaps}


def draft(t1: dict, bc: dict | None, brand: str) -> dict | None:
    """ตัวอย่างประกอบ 1 ชิ้น: ชื่อหน้าและคำอธิบายหน้าแรก สร้างจากข้อมูลธุรกิจเท่านั้น (ไม่เติมข้อเท็จจริงใหม่) · ต้องให้ลูกค้ายืนยันก่อนใช้"""
    f = (bc or {}).get("fields") or {}
    svc = f.get("primary_service") or {}
    if not svc.get("value") or svc.get("source") not in ("confirmed", "inferred"):
        return None

    def val(k):
        x = f.get(k) or {}
        return x.get("value") if x.get("source") in ("confirmed", "inferred") else None

    region, aud = val("region"), val("audience")
    pages = (t1.get("pages") or []) if t1.get("state") == "done" else []
    home = next((p for p in pages if _path(p.get("url") or "") in ("/", "")), pages[0] if pages else {})
    title = f"{svc['value']}{' ' + region if region else ''} | {brand}"[:65]
    desc = f"{brand} ให้บริการ{svc['value']}" + (f" ในพื้นที่{region}" if region else "") + (f" สำหรับ{aud}" if aud else "")
    guessed = [lb for k, lb in (("primary_service", "บริการหลัก"), ("region", "พื้นที่"), ("audience", "ลูกค้าเป้าหมาย"))
               if (f.get(k) or {}).get("source") == "inferred" and (f.get(k) or {}).get("value")]
    return {"before": {"title": (home.get("title") or "").strip() or None, "desc": (home.get("desc") or "").strip() or None},
            "after": {"title": title, "desc": desc[:160]}, "requires_fact_confirmation": True, "guessed": guessed,
            "page": _path(home.get("url") or "") if home else "/"}


STATUS_LABEL = {"completed": "ครบทุกด้าน", "partial": "ได้บางส่วน", "provider_unavailable": "บางระบบไม่พร้อม", "failed": "ตรวจไม่สำเร็จ"}


def overall(lanes: list[dict]) -> str:
    st = [x["state"] for x in lanes]
    if lanes[0]["state"] == "unavailable" or not any(s in TESTED for s in st):
        return "failed"
    if "unavailable" in st:
        return "provider_unavailable"
    if "partial" in st or "not_tested" in st:
        return "partial"
    return "completed"


def summary(lanes: list[dict], status: str) -> dict:
    """สรุปสั้นจากผลที่วัดได้จริง + ข้อจำกัด (ความไม่แน่นอน) · ไม่มีตัวเลขยอดขายหรือลูกค้าที่เสียไป"""
    said = [f"{x['title']}: {x['detail']}" for x in lanes if x["state"] in TESTED]
    unc = []
    nt = [x["title"] for x in lanes if x["state"] == "not_tested"]
    ua = [x["title"] for x in lanes if x["state"] == "unavailable"]
    pa = [x["title"] for x in lanes if x["state"] == "partial"]
    if nt:
        unc.append("ยังไม่ได้ทดสอบ: " + ", ".join(nt))
    if ua:
        unc.append("ระบบไม่พร้อมรอบนี้: " + ", ".join(ua) + " (ไม่ใช่ผลว่าไม่ผ่าน)")
    if pa:
        unc.append("ได้บางส่วน ตัวหารจึงเล็กลง: " + ", ".join(pa))
    if any(x["state"] in TESTED for x in lanes[1:]):
        unc.append("ผลค้นและคำตอบ AI วัดครั้งเดียว ณ วันที่ตรวจ และเปลี่ยนได้ทุกวัน")
    return {"lines": said, "uncertainty": unc, "status": status, "status_label": STATUS_LABEL[status]}


# ---------- ประวัติเวอร์ชันรายงาน + การเทียบข้ามรอบ (METRIC_DICTIONARY §4) ----------
LANE_TITLES = (("website_readiness", "ความพร้อมของเว็บ"), ("web_search_presence", "การปรากฏในผลค้นเว็บ"),
               ("ai_brand_mention", "AI พูดถึงแบรนด์"), ("ai_citation", "AI อ้างอิงเว็บของคุณ"))
COMPARE_LABEL = {"comparable": "เทียบได้", "qualified": "เทียบได้แบบมีเงื่อนไข", "non_comparable": "เทียบไม่ได้ (วิธีวัดเปลี่ยน)",
                 "legacy_search_only": "ผลค้นเว็บรอบเก่า (ไม่ใช่ผลจาก AI)"}


def compare(prev: dict | None, cur: dict | None, key: str) -> tuple[str | None, str]:
    """คืน (ระดับ, เหตุผล) · ช่องที่ไม่ได้ทดสอบรอบใดรอบหนึ่ง = None (ไม่เทียบ)"""
    if not prev or not cur or prev.get("state") not in TESTED or cur.get("state") not in TESTED:
        return None, ""
    pa, pb = prev.get("protocol_id"), cur.get("protocol_id")
    if not pa or not pb:
        if key == "web_search_presence":
            return "legacy_search_only", "รอบเก่าไม่ได้บันทึกผู้ให้บริการค้น ใช้เป็นผลค้นเว็บเท่านั้น"
        if pa != pb:
            return "qualified", "รอบเก่าไม่ได้บันทึกวิธีวัดครบ"
    elif pa != pb or prev.get("protocol_version") != cur.get("protocol_version"):
        return "non_comparable", f"วิธีวัดเปลี่ยน ({pa} v{prev.get('protocol_version')} → {pb} v{cur.get('protocol_version')})"
    why = []
    if (prev.get("provider") or "") != (cur.get("provider") or ""):
        why.append(f"ผู้ให้บริการเปลี่ยน ({prev.get('provider') or 'ไม่ได้บันทึก'} → {cur.get('provider') or 'ไม่ได้บันทึก'})")
    if prev.get("qset") != cur.get("qset"):
        why.append("ชุดคำค้นหรือคำถามเปลี่ยน")
    return ("qualified", " · ".join(why)) if why else ("comparable", "วิธีวัด ผู้ให้บริการ และชุดคำถามเหมือนกัน")


def _snapshot(vm: dict, ctx_version: int) -> dict:
    lanes = {}
    for x in vm["lanes"]:
        m = x.get("m") or {}
        lanes[x["key"]] = {"state": x["state"], "value": x["value"], "denominator": x["denominator"], "protocol_id": m.get("protocol_id"),
                           "protocol_version": m.get("protocol_version"), "provider": m.get("provider"), "qset": m.get("qset"),
                           "measured_at": m.get("measured_at")}
    return {"lanes": lanes, "context_version": ctx_version, "status": vm["summary"]["status"]}


def record(lead, vm: dict, ctx_version: int = 0) -> int | None:
    """บันทึกเวอร์ชันใหม่เมื่อผลวัดเปลี่ยน (ยังตรวจอยู่หรือยังไม่มีผลเลย = ไม่บันทึก) · บันทึกล้มไม่ทำให้หน้ารายงานล้ม"""
    if vm.get("running") or not vm.get("tested"):
        return None
    snap = _snapshot(vm, ctx_version)
    fp = hashlib.sha1(json.dumps(snap, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    try:
        return db.add_report_version(lead["id"], fp, json.dumps(snap, ensure_ascii=False))
    except Exception:
        return None


def history(lead, limit: int = 10) -> list[dict]:
    """เวอร์ชันล่าสุดก่อน · แต่ละช่องบอกว่าเทียบกับรอบก่อนหน้าได้แค่ไหน (ไม่ต่อเส้นกราฟข้ามวิธีวัด)"""
    try:
        rows = db.list_report_versions(lead["id"], limit + 1)
    except Exception:
        return []
    snaps = []
    for r in rows:
        try:
            snaps.append({"version": r["version"], "at": (r["created_at"] or "")[:16].replace("T", " "), **json.loads(r["summary_json"])})
        except ValueError:
            continue
    out = []
    for i, s in enumerate(snaps[:limit]):
        prev = snaps[i + 1] if i + 1 < len(snaps) else None
        cells = []
        for key, title in LANE_TITLES:
            cur = (s.get("lanes") or {}).get(key) or {}
            lvl, why = compare(((prev or {}).get("lanes") or {}).get(key), cur, key) if prev else (None, "")
            cells.append({"key": key, "title": title, "state": cur.get("state"), "value": cur.get("value") if cur.get("state") in TESTED else None,
                          "cmp": lvl, "cmp_label": COMPARE_LABEL.get(lvl) if lvl else None, "why": why})
        out.append({"version": s["version"], "at": s["at"], "context_version": s.get("context_version") or 0, "cells": cells})
    return out


# ---------- ขั้นต่อไป: ยืนยันบริบท → ขอแผน → นัดคุย → ใบเสนอราคาที่ทีมส่งแล้ว ----------
QUOTE_SHOWN = ("sent", "viewed", "accepted")


def shown_quote(lead_id: int):
    """ใบเสนอราคาที่ทีมส่งให้ลูกค้าแล้วเท่านั้น (ไม่ใช่ฉบับร่าง ยกเลิก หรือจ่ายแล้ว)"""
    try:
        for qr in db.list_quotes(lead_id=lead_id, limit=5):
            if qr["status"] in QUOTE_SHOWN:
                return qr
    except Exception:
        pass
    return None


def next_step(ctx_on: bool, bc: dict | None, plan_on: bool, plan, quote) -> dict:
    if quote is not None:
        return {"key": "view_quote", "title": "ดูใบเสนอราคาที่ทีมส่งให้", "href": f"/q/{quote['token']}",
                "text": "ทีมเจอ.AI ส่งใบเสนอราคาสำหรับงานนี้แล้ว เปิดดูขอบเขตและราคาได้"}
    if ctx_on and bc and not bc.get("confirmed"):
        return {"key": "confirm_context", "title": "ยืนยันข้อมูลธุรกิจ", "href": "#context",
                "text": "ช่วยตรวจว่าบริการหลักและคู่แข่งที่ระบบเดาถูกไหม ผลทดสอบรอบถัดไปจะตรงกับธุรกิจคุณมากขึ้น"}
    if plan:
        return {"key": "request_meeting", "title": "รอทีมติดต่อกลับเพื่อนัดคุย", "href": "#plan",
                "text": "ได้รับคำขอแผนแล้ว ทีมจะติดต่อกลับภายใน 1 วันทำการเพื่อนัดเวลาคุย"}
    return {"key": "request_plan", "title": "ขอแผนแก้จากทีม", "href": "#plan",
            "text": "ไม่มีค่าใช้จ่าย ทีมจะพาดูรายงานนี้และสรุปว่าควรแก้อะไรก่อน"}


METHODOLOGY = [
    ("ความพร้อมของเว็บ", "home-check-v1 · crawl-v1", "อ่านหน้าแรก robots.txt sitemap และไต่เว็บสาธารณะ (ไม่ล็อกอิน ไม่กรอกฟอร์ม) นับข้อที่ผ่านจากข้อที่ตรวจได้"),
    ("การปรากฏในผลค้นเว็บ", "search-rank-v1", "ค้น Google ประเทศไทยด้วยคำค้นที่เลือกจากเนื้อหาเว็บ นับคำที่โดเมนคุณติดหน้าแรก คำที่ค้นไม่สำเร็จไม่นับ เป็นผลค้นเว็บ ไม่ใช่คำตอบ AI"),
    ("AI พูดถึงแบรนด์", "ai-ask-v1", "ถามผู้ช่วย AI ที่ค้นเว็บได้ ด้วยคำถามแบบที่ลูกค้าพิมพ์ นับคำตอบที่เอ่ยชื่อหรืออ้างเว็บคุณ คำถามที่ AI ไม่ตอบไม่นับ"),
    ("AI อ้างอิงเว็บของคุณ", "ai-ask-v1", "จากคำตอบชุดเดียวกัน นับเฉพาะคำตอบที่ลิงก์หรืออ้างแหล่งเป็นเว็บของคุณ"),
]


def build(lead, snap: dict | None = None, bc: dict | None = None) -> dict:
    """bc = business_context.current(lead) (ถ้าเปิด context_v2) ใช้กับเทียบคู่แข่ง ตัวอย่างร่าง และป้ายคำถามของลูกค้า"""
    report = _json(lead, "audit_json")
    snap = snap or {}
    t1, t2 = deep_audit.tier1(lead), deep_audit.tier2(lead)
    prog = deep_audit.progress(lead)
    site = lead["site_url"] if "site_url" in lead.keys() else ""
    ev = Evidence(L.domain_of(site) if site else "")
    lanes = [readiness(report, snap, t1, lead["audit_at"]), search_presence(t1, prog["tier1"]),
             _ai_lane("ai_brand_mention", "AI พูดถึงแบรนด์", t2, prog["ai"], "mentioned", "เอ่ยถึงแบรนด์คุณ"),
             _ai_lane("ai_citation", "AI อ้างอิงเว็บของคุณ", t2, prog["ai"], "cited", "อ้างอิงเว็บคุณ")]
    tested = sum(1 for x in lanes if x["state"] in TESTED)
    crawl = t1.get("crawl") or {}
    partial = bool(report.get("partial") or snap.get("state") == "partial")
    acts = actions(report, t1, ev, partial=partial, at=lead["audit_at"], site=site)
    if lanes[0]["state"] in TESTED:
        lanes[0]["evidence_refs"] = [r for a in acts for r in a["evidence_refs"]]
    if lanes[1]["state"] in TESTED:
        rk = t1.get("ranking") or {}
        proto = rk.get("protocol") or {}
        for r in [r for r in rk.get("rows") or [] if not r.get("error")][:10]:
            pos = r.get("position")
            text = (f"คำค้น “{r.get('keyword')}” · " + (f"เว็บคุณอยู่อันดับ {pos}" if pos else f"ไม่พบเว็บคุณใน {r.get('depth') or 20} อันดับแรก")
                    + " · 3 อันดับแรก: " + (", ".join((r.get("top") or [])[:3]) or "—"))
            lanes[1]["evidence_refs"].append(ev.add("search_result", text, url=r.get("url"), at=t1.get("at"),
                                                    provider=proto.get("provider"), protocol=proto.get("id")))
    cust_qs = [q.get("text") or "" for q in ((bc or {}).get("fields") or {}).get("questions") or []] if (bc or {}).get("confirmed") else []
    tests = ai_tests(t2, ev, cust_qs)
    if lanes[2]["state"] in TESTED:
        lanes[2]["evidence_refs"] = [t["ref"] for t in tests if t["ref"] and t["result"] != "none"]
    if lanes[3]["state"] in TESTED:
        lanes[3]["evidence_refs"] = [t["ref"] for t in tests if t["ref"] and t["result"] == "cited"]
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
    pages = (t1.get("pages") or []) if t1.get("state") == "done" else []
    status = overall(lanes)
    brand = t1.get("brand") if t1.get("state") == "done" else None
    return {"lanes": lanes, "tested": tested, "total": len(lanes), "actions": acts, "scope": scope,
            "running": prog["tier1"] == "running" or prog["ai"] == "running", "audit_at": (lead["audit_at"] or "")[:16].replace("T", " "),
            "brand": brand,
            "keywords": (t1.get("keywords") or [])[:5] if t1.get("state") == "done" else [],
            "competitors": [c["domain"] for c in ((t1.get("ranking") or {}).get("competitors") or [])[:3]],
            "identity": {"domain": lead["domain"], "pages_checked": len(pages) or None,
                         "pages_failed": sum(1 for p in pages if not p.get("status") or p["status"] >= 400) if pages else None},
            "summary": summary(lanes, status), "evidence": ev.items, "ai_tests": tests,
            "competitor": competitor_view(t1, t2, bc), "draft": draft(t1, bc, brand or lead["domain"]), "methodology": METHODOLOGY}
