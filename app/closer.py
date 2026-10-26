"""บทโทรปิดดีล (Closer) — คนโทร เอเจนต์เตรียมทุกอย่าง
คัดลีดที่ควรโทรวันนี้ เรียงตามความร้อน:
  100 ยืนยันข้อเสนอแล้วแต่ยังไม่จ่าย ≥ 1 วัน  ·  80 เปิดดูใบเสนอราคาแล้วไม่ยืนยัน ≥ 2 วัน  ·  70 ตอบกลับแล้วยังไม่มีใบเสนอราคา
  50+ ทักแล้วเงียบ ≥ 7 วัน และโอกาสสูง (คะแนน ≥ 50)
เตรียมให้: เบอร์โทร (จากช่องติดต่อหรือหาบนเว็บ) · ทำไมโทรตอนนี้ · 3 ข้อจากรายงานจริงเป็นภาษาคน · คู่แข่ง/ผล AI · ราคาและเพดานส่วนลด
(กรอบ CFO) · คำตอบข้อโต้แย้งที่พบบ่อย · ปิดท้ายด้วยลิงก์จ่าย/ขั้นถัดไป · บันทึกผลโทร → ไทม์ไลน์ (จะจ่าย/ขอคิด/ไม่สนใจ/ไม่รับสาย)
ข้อห้ามเดียวกับทุกเอเจนต์: ไม่การันตีอันดับหรือผลลัพธ์"""
from __future__ import annotations
import datetime as dt
import json
import re
from urllib.parse import urljoin, urlparse

from . import db, execs, leads as L, quotes as Q

OUTCOMES = {"will_pay": "จะจ่าย — ส่งลิงก์ชำระแล้ว", "think": "ขอคิดก่อน — นัดโทรอีกครั้ง", "no": "ไม่สนใจ", "no_answer": "ไม่รับสาย",
            "wrong": "เบอร์ผิด/ไม่ใช่ผู้ตัดสินใจ"}
RECALL_DAYS = {"think": 3, "no_answer": 1, "will_pay": 2, "wrong": 30}
PHONE_RE = re.compile(r"(?<![\d])(?:\+66[\s\-]?|0)(?:[2-9]\d?)[\s\-]?\d{3}[\s\-]?\d{3,4}(?![\d])")
TEL_RE = re.compile(r"""href=["']tel:([^"']+)["']""", re.I)
CONTACT_HINT = re.compile(r"contact|ติดต่อ|about|เกี่ยวกับ", re.I)
FETCH = None                                    # เทสต์ฉีด fetch(url) → html


def _ts(s):
    try:
        return dt.datetime.fromisoformat(str(s)[:19])
    except Exception:
        return None


def _data(lead) -> dict:
    try:
        return json.loads(lead["call_json"] or "{}") if "call_json" in lead.keys() and lead["call_json"] else {}
    except Exception:
        return {}


def _save(lead_id: int, d: dict) -> None:
    db.update_lead(lead_id, call_json=json.dumps(d, ensure_ascii=False))


# ---------- เบอร์โทร ----------
def norm_phone(s: str) -> str:
    d = re.sub(r"[^\d+]", "", s or "")
    if d.startswith("+66"):
        d = "0" + d[3:]
    return d if re.fullmatch(r"0\d{8,9}", d) else ""


def fmt_phone(p: str) -> str:
    if len(p) == 10:
        return f"{p[:3]}-{p[3:6]}-{p[6:]}"
    if len(p) == 9:
        return f"{p[:2]}-{p[2:5]}-{p[5:]}"
    return p


def phones_in(html: str) -> list[str]:
    out = []
    for m in TEL_RE.finditer(html or ""):
        p = norm_phone(m.group(1))
        if p and p not in out:
            out.append(p)
    text = re.sub(r"<[^>]+>", " ", html or "")
    for m in PHONE_RE.finditer(text):
        p = norm_phone(m.group(0))
        if p and p not in out:
            out.append(p)
    return out


def find_phone(lead, fetch=None) -> str:
    """เบอร์จากช่องติดต่อของลีดก่อน → หน้าแรก + หน้าติดต่อของเว็บ (tel: ก่อนตัวเลขในเนื้อหา) · มือถือ (06/08/09) มาก่อนเบอร์บ้าน"""
    own = phones_in(lead["contact"] or "")
    if own:
        return own[0]
    fetch = fetch or FETCH
    if fetch is None:
        from .prospecting import _fetch as fetch
    site = lead["site_url"]
    home = fetch(site) or ""
    found = phones_in(home)
    host = L.domain_of(site)
    for m in re.finditer(r"""<a[^>]+href=["']([^"'#]+)["'][^>]*>(.*?)</a>""", home, re.I | re.S):
        href, text = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
        if CONTACT_HINT.search(href) or CONTACT_HINT.search(text):
            u = urljoin(site, href)
            if L.domain_of(u) == host:
                found += [p for p in phones_in(fetch(u) or "") if p not in found]
                if len(found) >= 3:
                    break
    found.sort(key=lambda p: 0 if p[:2] in ("06", "08", "09") else 1)
    return found[0] if found else ""


# ---------- คัดลีดที่ควรโทร ----------
def _events(lead_id: int) -> list:
    return db.list_lead_events(lead_id, 300)


def trigger(lead, now: dt.datetime) -> dict | None:
    """ทำไมต้องโทรรายนี้ตอนนี้ (None = ยังไม่ควรโทร)"""
    if lead["status"] in L.NO_CONTACT:
        return None
    qs = [q for q in db.list_quotes(lead["id"]) if q["status"] in Q.OPEN]
    q = qs[0] if qs else None
    if q and q["status"] in ("accepted", "claimed"):
        t = _ts(q["accepted_at"]) or now
        hrs = (now - t).total_seconds() / 3600
        if hrs >= 12 and q["status"] == "accepted":             # ยืนยันแล้วครึ่งวันยังไม่จ่าย = โทรได้เลย (ร้อนที่สุด)
            ago = f"{int(hrs // 24)} วัน" if hrs >= 24 else f"{int(hrs)} ชั่วโมง"
            return {"prio": 100, "kind": "accepted_unpaid", "why": f"ยืนยันรับข้อเสนอ {q['number']} แล้ว {ago} แต่ยังไม่ได้ชำระ", "quote": q}
        return None
    if q and q["status"] == "viewed":
        t = _ts(q["viewed_at"]) or now
        if (now - t).days >= 2:
            return {"prio": 80, "kind": "quote_viewed", "why": f"เปิดดูใบเสนอราคา {q['number']} แล้ว {(now - t).days} วัน ยังไม่ยืนยัน", "quote": q}
        return None
    if q and q["status"] == "sent":
        t = _ts(q["sent_at"]) or now
        if (now - t).days >= 3:
            return {"prio": 75, "kind": "quote_unopened", "why": f"ส่งใบเสนอราคา {q['number']} ไป {(now - t).days} วัน ยังไม่เปิดดู", "quote": q}
        return None
    if lead["status"] == "replied" and not q:
        return {"prio": 70, "kind": "replied", "why": "ตอบกลับ/ขอใบเสนอราคาแล้ว แต่ยังไม่มีใบเสนอราคาที่ส่งได้", "quote": None}
    if lead["status"] == "contacted" and (lead["score"] or 0) >= 50:
        first = min([_ts(e["created_at"]) for e in _events(lead["id"]) if e["kind"] == "contacted"] or [None], key=lambda x: x or now)
        if first and (now - first).days >= 7:
            return {"prio": 50 + min(30, (lead["score"] or 0) // 4), "kind": "silent_hot",
                    "why": f"ทักไป {(now - first).days} วันแล้วเงียบ แต่โอกาสสูง ({lead['score']}/100)", "quote": None}
    if lead["status"] == "new" and not lead["email"] and lead["audit_json"] and (lead["score"] or 0) >= 30:
        d = _data(lead)
        m = d.get("maps") or {}
        if d.get("phone"):                                  # ไม่มีอีเมลให้ทัก แต่มีเบอร์ (ส่วนใหญ่จาก Google Maps) → โทรเปิดตัวพร้อมผลตรวจ
            pop = m.get("reviews") or 0
            why = "ยังไม่มีอีเมล แต่มีเบอร์" + (f" · Google Maps {pop:,} รีวิว" + (f" ★{m['rating']}" if m.get("rating") else "") if m else "") \
                  + f" · เว็บยังอ่อน ({lead['score']}/100)"
            return {"prio": 30 + min(15, (lead["score"] or 0) // 7) + (10 if pop >= 300 else 5 if pop >= 50 else 0),
                    "kind": "cold_phone", "why": why, "quote": None}
    return None


def due(lead, now: dt.datetime) -> bool:
    """โทรแล้วรอรอบถัดไปตามผลครั้งก่อน (ไม่รับสาย 1 วัน · ขอคิด 3 วัน · จะจ่าย 2 วัน)"""
    last = (_data(lead).get("calls") or [None])[-1]
    if not last:
        return True
    t = _ts(last.get("at"))
    return not t or (now - t).days >= RECALL_DAYS.get(last.get("outcome"), 3)


def call_list(now: dt.datetime | None = None, limit: int = 20) -> list[dict]:
    now = now or dt.datetime.now()
    out = []
    for lead in db.list_leads(None, 5000):
        if lead["status"] in L.NO_CONTACT:
            continue
        t = trigger(lead, now)
        if t and due(lead, now):
            d = _data(lead)
            out.append({"lead": lead, "trigger": t, "phone": d.get("phone") or "", "calls": d.get("calls") or []})
    out.sort(key=lambda x: (-x["trigger"]["prio"], x["lead"]["id"]))
    return out[:limit]


# ---------- บทโทร ----------
OBJECTIONS = [
    ("แพงไป / ขอลดได้ไหม", "เทียบกับค่าโฆษณาต่อเดือน: นี่คือการทำให้เว็บถูกหาเจอเองทุกวันโดยไม่ต้องจ่ายต่อคลิก "
     "ทางเลือกแรก: จ่ายรายปีลด {yd}% และ GEO Setup ฟรี{refund} · "
     "ถ้ายังติดราคา ลดได้ไม่เกิน {disc}% เฉพาะปิดในสายนี้ — เกินกว่านั้นต้องให้ผู้บริหารอนุมัติ"),
    ("เดี๋ยวให้ทีมเว็บแก้เอง", "ได้เลยครับ รายงานมีวิธีแก้ทีละข้ออยู่แล้ว ส่วนที่ทีมเว็บทำเองยาก คือการเขียนคำตอบให้ AI ทุกสัปดาห์และวัดผลซ้ำ ซึ่งเป็นงานหลักของเรา"),
    ("ไม่แน่ใจว่าจะได้ผลไหม", "เราไม่รับประกันอันดับครับ แต่วัดให้เห็นทุกเดือนจากการถาม AI จริงและ Search Console — เดือนแรกคุณจะเห็นว่าอะไรเปลี่ยน ถ้าไม่เห็นค่าก็ยกเลิกได้"),
    ("ขอคิดก่อน / ขอปรึกษาหุ้นส่วน", "ได้ครับ ขอนัดเวลาโทรกลับที่แน่นอน และส่งรายงานให้คนที่ต้องตัดสินใจดูด้วย — ถามว่าเขากังวลเรื่องไหนที่สุด"),
    ("ทำ SEO / จ้างเอเจนซี่อยู่แล้ว", "ดีเลยครับ ของเราเสริมกันได้: เน้นให้ ChatGPT, Gemini, Google AI หยิบไปตอบ ซึ่ง SEO ทั่วไปยังไม่วัด — ลองดูแท็บ AI Visibility ในรายงาน"),
]


def script(lead) -> dict:
    """บทโทรจากข้อมูลจริงของลีด (ไม่ให้โมเดลแต่งตัวเลข)"""
    from . import deep_audit, pipeline
    now = dt.datetime.now()
    t = trigger(lead, now) or {"prio": 0, "kind": "manual", "why": "เจ้าของเลือกโทรเอง", "quote": None}
    report = json.loads(lead["audit_json"] or "{}") if lead["audit_json"] else {}
    summary = L.public_summary(report, limit=3)
    t1, t2 = deep_audit.tier1(lead), deep_audit.tier2(lead)
    issues = [i for i in (t1.get("issues") or []) if i["severity"] == "Error"][:2] if t1.get("state") == "done" else []
    rk = t1.get("ranking") or {} if t1.get("state") == "done" else {}
    points = [f"{s['label']} — {s['why']}" for s in summary]
    points += [f"{i['label']} ({i['count']} จุด)" for i in issues]
    q = t["quote"]
    if q is None:
        qs = [x for x in db.list_quotes(lead["id"]) if x["status"] in Q.OPEN]
        q = qs[0] if qs else None
    disc = execs.MANDATE["max_discount_pct"]
    yearly = bool(q) and Q.cycle_of(q) == "year"
    price = (f"แพ็กเกจ {q['package'].capitalize()} รายปี {Q.period_total(q):,} บาท (เทียบ {q['net']:,} บาท/เดือน ลด {Q.yearly_discount():.0f}%"
             + (" · GEO Setup ฟรี" if q["setup"] else "") + ")" + (f" · คืนเงินได้ภายใน {Q.refund_days(q)} วันแรก" if Q.refund_days(q) else "") + f" · ถ้าอยากเริ่มเบา ๆ มีรายเดือน {Q.alternative(q)['net']:,} บาท/เดือน") if yearly else \
            (f"แพ็กเกจ {q['package'].capitalize()} {q['net']:,} บาท/เดือน" + (f" + GEO Setup {q['setup_price']:,} บาท (ครั้งเดียว)" if q["setup"] else "")
             + f" · งวดแรก {Q.baht(Q.payable(q))} บาท" + (" รวม VAT" if Q.vat_pct(q) else "") + (f" (ลดแล้ว {q['discount_pct']:.0f}%)" if q["discount_pct"] else "")
             + f" · เสนอรายปีได้ ประหยัด {Q.alternative(q)['saving_year']:,} บาท (Setup ฟรี)") if q else \
            (f"แพ็กเกจ Pro รายปี {Q.price_for('pro', 0, False, 'year')['period_total']:,} บาท (เทียบ {Q.price_for('pro', 0, False, 'year')['net']:,} บาท/เดือน · Setup ฟรี) "
             f"หรือรายเดือน {execs._plan('pro')['price']:,} บาท (ยังไม่มีใบเสนอราคา — ทำในระบบหลังวางสาย)")
    comp = rk.get("competitors") or []
    context = []
    if comp and rk.get("n"):
        context.append(f"{comp[0]['domain']} ติด 10 อันดับแรกของ Google ใน {comp[0]['hits']} จาก {rk['n']} คำที่ลูกค้าใช้หา (คุณติดหน้าแรก {rk.get('page1', 0)} คำ)")
    if t2.get("state") == "done":
        context.append(f"ถาม ChatGPT {t2['asked']} คำถามที่ลูกค้าของคุณถาม — แบรนด์คุณถูกเอ่ยถึง {t2['mentioned']} ครั้ง")
    if (t1.get("health") or {}).get("score") is not None:
        context.append(f"คะแนนสุขภาพเว็บ {t1['health']['score']}/100 จาก {t1['health']['urls']} หน้าที่ไต่")
    report_url = pipeline.result_url(lead)
    pay_url = pipeline.quote_url(q) if q else ""
    opener = {
        "accepted_unpaid": f"สวัสดีครับ ผมจากเจอ.AI โทรเรื่องใบเสนอราคา {q['number'] if q else ''} ที่คุณกดยืนยันไว้ครับ อยากเช็กว่าติดขัดตรงขั้นชำระเงินไหม หรือมีคำถามก่อนเริ่มงาน",
        "quote_viewed": "สวัสดีครับ ผมจากเจอ.AI เห็นว่าคุณเปิดดูใบเสนอราคาแล้ว อยากถามว่ามีตรงไหนที่ยังไม่ชัด หรือไม่ตรงกับที่ต้องการไหมครับ",
        "quote_unopened": "สวัสดีครับ ผมจากเจอ.AI ส่งใบเสนอราคาไปทางอีเมลเมื่อไม่กี่วันก่อน ไม่แน่ใจว่าถึงหรือเปล่า เลยโทรมาสรุปให้ฟังสั้น ๆ ครับ",
        "replied": "สวัสดีครับ ผมจากเจอ.AI ขอบคุณที่ตอบกลับมาครับ โทรมาถามความต้องการสักสองสามข้อ จะได้ทำใบเสนอราคาให้ตรง",
        "silent_hot": f"สวัสดีครับ ผมจากเจอ.AI ส่งผลตรวจเว็บ {lead['domain']} ไปทางอีเมล มีข้อหนึ่งที่สำคัญเลยอยากโทรมาบอกเองครับ ขอเวลา 2 นาที",
        "cold_phone": f"สวัสดีครับ ผมจากเจอ.AI เห็นร้านใน Google Maps รีวิวดีมาก แต่พอลองถาม ChatGPT/Google ว่าแนะนำที่ไหน ยังไม่ค่อยเจอเว็บ {lead['domain']} "
                      "เลยตรวจเว็บให้ฟรีแล้วครับ ขอเวลา 2 นาทีสรุปให้ฟัง แล้วส่งรายงานให้ทาง LINE หรืออีเมลได้ไหมครับ",
    }.get(t["kind"], f"สวัสดีครับ ผมจากเจอ.AI โทรเรื่องผลตรวจเว็บ {lead['domain']} ครับ ขอเวลา 2 นาที")
    close = {
        "accepted_unpaid": "ถ้าพร้อม ผมส่งลิงก์ชำระเงินให้ทาง LINE/อีเมลตอนนี้เลย — จ่ายแล้วระบบเปิดบัญชีและเริ่มงานวันเดียวกัน",
        "quote_viewed": "ถ้าตรงแล้ว กดยืนยันในใบเสนอราคาได้เลย ผมส่งลิงก์ให้อีกครั้งระหว่างที่คุยครับ",
        "quote_unopened": "ผมส่งลิงก์ใบเสนอราคาให้ทาง LINE ตอนนี้เลยได้ไหมครับ ดูแล้วกดยืนยันได้ในหน้าเดียว",
        "replied": "ผมทำใบเสนอราคาให้ภายในวันนี้ ขอช่องทางที่สะดวกให้ส่งครับ",
        "cold_phone": "ขออีเมลหรือ LINE ของคนที่ดูแลเว็บ/การตลาดครับ ผมส่งรายงานเต็มให้ดูก่อน ไม่มีค่าใช้จ่าย — แล้วจดช่องทางในหน้าลูกค้าทันที",
    }.get(t["kind"], "ถ้าสนใจ ผมทำใบเสนอราคาให้ภายในวันนี้ หรือนัดคุย 15 นาทีพร้อมดูรายงานด้วยกันครับ")
    evs = _events(lead["id"])
    hist = [f"{e['created_at'][:16].replace('T', ' ')} {L.EVENT_LABEL.get(e['kind'], e['kind'])}" for e in evs[:6]]
    notes = []
    if any(e["kind"] == "cs_kickoff" for e in evs) and not any(x["status"] == "paid" for x in db.list_quotes(lead["id"])):
        notes.append("ลูกค้าได้รับอีเมลต้อนรับ/แผน 30 วันไปก่อนชำระ (ระบบเปิดบัญชีให้ผิดจังหวะ) — ถ้าเขาถาม ขอโทษสั้น ๆ และบอกว่างานจะเริ่มจริงเมื่อชำระ")
    return {"trigger": t, "opener": opener, "points": points[:4], "context": context, "price": price, "max_discount": disc,
            "objections": [(o, a.format(disc=disc, yd="%g" % Q.yearly_discount(), refund=(f" (คืนเงินได้ภายใน {Q.refund_days(q)} วันแรก)" if Q.refund_days(q) else ""))) for o, a in OBJECTIONS], "close": close, "report_url": report_url, "pay_url": pay_url,
            "history": hist, "notes": notes, "rules": "ห้ามการันตีอันดับหรือผลลัพธ์ · ห้ามลดเกินกรอบ · จดผลทันทีหลังวางสาย"}


def prepare(lead, fetch=None) -> dict:
    """หาเบอร์ (ถ้ายังไม่มี) แล้วเก็บไว้"""
    d = _data(lead)
    try:
        from . import deep_audit
        if lead["audit_json"] and deep_audit.tier1(lead).get("state") != "done":
            deep_audit.start(lead["id"])           # บทโทรจะได้มีคู่แข่ง/สุขภาพเว็บ (ทำเบื้องหลัง เปิดหน้าใหม่อีกครั้งก็เห็น)
    except Exception:
        pass
    if not d.get("phone"):
        d["phone"] = find_phone(lead, fetch=fetch)
        d["phone_at"] = db.now()
        _save(lead["id"], d)
    return d


def log_call(lead, outcome: str, note: str = "", who: str = "owner") -> str:
    from . import pipeline
    if outcome not in OUTCOMES:
        raise ValueError("ผลโทรไม่ถูกต้อง")
    d = _data(lead)
    d.setdefault("calls", []).append({"at": db.now(), "outcome": outcome, "note": (note or "")[:300]})
    _save(lead["id"], d)
    db.add_lead_event(lead["id"], "call", f"{OUTCOMES[outcome]}" + (f" · {note.strip()[:200]}" if note.strip() else ""), who)
    if outcome == "no":
        db.update_lead(lead["id"], status="lost")
        pipeline._cancel_pending(lead["id"], {"outreach", "lead_review", "followup", "quote"}, "โทรแล้วลูกค้าไม่สนใจ")
        return "บันทึกแล้ว · ปิดเป็นไม่สนใจ ยกเลิกข้อความที่ค้าง"
    if outcome == "will_pay":
        qs = [x for x in db.list_quotes(lead["id"]) if x["status"] in Q.OPEN]
        if qs and lead["email"] and pipeline.send_channel(lead):
            try:
                from . import i18n
                lg = pipeline.lead_lang(lead)                          # ภาษาที่ลีดเลือกบนเว็บ (ไทย = ข้อความเดิม)
                pipeline._send_text(lead, i18n.tr("ลิงก์ชำระเงิน {number}", lg, number=qs[0]["number"]),
                                    i18n.tr("สวัสดีครับ ตามที่คุยทางโทรศัพท์ ลิงก์ใบเสนอราคาและชำระเงินอยู่ที่นี่ครับ", lg) + f"\n{pipeline.quote_url(qs[0])}\n\n"
                                    + i18n.tr("จ่ายแล้วระบบเปิดบัญชีและเริ่มงานวันเดียวกัน", lg) + "\nเจอ.AI"
                                    + pipeline.outreach_footer(lead))
                return "บันทึกแล้ว · ส่งลิงก์ชำระเงินทางอีเมลให้แล้ว"
            except Exception:
                pass
        return "บันทึกแล้ว · ส่งลิงก์ชำระเงินเองระหว่างคุย (ไม่มีช่องทางส่งอัตโนมัติ)"
    return f"บันทึกแล้ว · โทรอีกครั้งใน {RECALL_DAYS.get(outcome, 3)} วัน"


def status_text() -> str:
    lst = call_list(limit=10)
    if not lst:
        return ""
    return "ควรโทรวันนี้: " + " · ".join(f"{x['lead']['domain']} ({x['trigger']['why']})" for x in lst[:3]) + (f" และอีก {len(lst) - 3} ราย" if len(lst) > 3 else "")
