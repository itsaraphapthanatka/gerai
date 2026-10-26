"""ฝ่ายพันธมิตร — ให้เอเจนซี่เว็บ/การตลาดและฟรีแลนซ์ส่งลูกค้ามาให้ (ช่องทางขายที่ไม่ต้องพึ่งการทักคนแปลกหน้า)
· หาพันธมิตร: ค้นเอเจนซี่ตามประเภท × จังหวัด (Serper เดิม) → หาอีเมลบนเว็บ → ร่างคำชวน → เจ้าของอนุมัติ (ไม่ส่งเอง) · ติดตามครั้งเดียววันที่ 5
· สมัครเอง: หน้า /partners → เจ้าของอนุมัติ → ได้รหัสแนะนำ ลิงก์ /p/<รหัส> และแดชบอร์ด /partner/<token>
· ติดตามที่มา: ลีดที่มาทางลิงก์พันธมิตร (คุกกี้ 60 วัน) หรือที่พันธมิตรส่งชื่อมาในแดชบอร์ด → leads.partner_id (ครั้งแรกเท่านั้น ไม่แย่งกัน)
· ค่าคอม: ทุกใบเสร็จของลูกค้าที่พันธมิตรแนะนำ (ไม่รวม VAT) × % ตามกรอบอำนาจ ภายใน N เดือนแรก · รอพ้นช่วงคืนเงิน 7 วันก่อนอนุมัติ
  ใบเสร็จถูกยกเลิก = ค่าคอมยกเลิก · เจ้าของโอนเองแล้วกด "จ่ายแล้ว" (ระบบไม่โอนเงิน)"""
from __future__ import annotations
import datetime as dt
import html as _html
import json
import re
import secrets

from . import db, office

SEGMENTS = ["รับทำเว็บไซต์", "บริษัทรับทำ SEO", "เอเจนซี่การตลาดออนไลน์", "รับทำเว็บ WordPress", "digital marketing agency"]
DAILY_DEFAULT, DAILY_MAX = 5, 15
FOLLOW_DAYS = 5
COOKIE = "jr_ref"
COOKIE_DAYS = 60
STATUS_TH = {"prospect": "พบจากการค้นหา", "invited": "ส่งคำชวนแล้ว", "replied": "ตอบกลับแล้ว", "applied": "สมัครแล้ว รออนุมัติ",
             "active": "พันธมิตร", "paused": "พักไว้", "rejected": "ไม่รับ", "unsubscribed": "ขอไม่รับการติดต่อ"}
CSTATUS_TH = {"pending": "รอถึงกำหนด", "approved": "ถึงกำหนดจ่าย", "paid": "จ่ายแล้ว", "void": "ยกเลิก"}
FORBIDDEN = re.compile(r"(การันตี|รับประกันอันดับ|อันดับ\s*1|100\s*%|รวยเร็ว)")
HOOKS = {"search": None, "contact": None, "llm": None}


def enabled() -> bool:
    return (db.get_setting("partner_on") or "1") == "1"


def daily_limit() -> int:
    try:
        return max(1, min(DAILY_MAX, int(db.get_setting("partner_daily") or DAILY_DEFAULT)))
    except ValueError:
        return DAILY_DEFAULT


def pct() -> float:
    from . import execs
    return float(execs.MANDATE["partner_commission_pct"])


def months() -> int:
    from . import execs
    return int(execs.MANDATE["partner_commission_months"])


def _base() -> str:
    from . import pipeline
    return pipeline.public_base()


def ref_url(p) -> str:
    return f"{_base()}/p/{p['code']}"


def dash_url(p) -> str:
    return f"{_base()}/partner/{p['token']}"


def unsub_url(p) -> str:
    return f"{_base()}/partner/{p['token']}/unsubscribe"


def _record(pid: int, note: str) -> None:
    p = db.get_partner(pid)
    log = (p["notes"] or "") if p else ""
    db.update_partner(pid, notes=(f"{db.now()[:16].replace('T', ' ')} {note}\n" + log)[:4000])


def _new_code(name: str) -> str:
    base = re.sub(r"[^a-z0-9]", "", (name or "").lower())[:10] or "partner"
    for _ in range(20):
        code = f"{base}{secrets.randbelow(900) + 100}"
        if not db.find_partner(code=code):
            return code
    return secrets.token_hex(4)


# ---------- หาพันธมิตร ----------
def next_queries(k: int = 2) -> list[str]:
    from . import prospecting
    cur = int(db.get_setting("partner_cursor") or 0)
    out = [f"{SEGMENTS[(cur + i) % len(SEGMENTS)]} {prospecting.AREAS[((cur + i) // len(SEGMENTS)) % len(prospecting.AREAS)]}" for i in range(k)]
    db.set_setting("partner_cursor", str(cur + k))
    return out


def draft_invite(p, llm=None) -> dict:
    """คำชวนเป็นพันธมิตร — สั้น เป็นกันเอง บอกประโยชน์ต่อลูกค้าของเขา ไม่การันตีผล · ผิดกติกา → แม่แบบ"""
    who = p["company"] or p["domain"]
    pc, mo = f"{pct():g}", months()
    tpl_body = (f"สวัสดีครับ ทีม {who}\n\nผมจากเจอ.AI ระบบที่ช่วยให้เว็บธุรกิจไทยถูก ChatGPT, Gemini และ Google AI หยิบไปตอบเวลาลูกค้าถาม\n\n"
                f"เห็นว่า {who} ดูแลเว็บและการตลาดให้ลูกค้าอยู่แล้ว เลยอยากชวนเป็นพันธมิตรครับ ลูกค้าของคุณได้รายงานตรวจเว็บฟรี "
                f"ส่วนคุณได้ค่าแนะนำ {pc}% ของค่าบริการ {mo} เดือนแรก และใช้แพ็กเกจ Agency ดูแลหลายเว็บในบัญชีเดียวได้\n\n"
                f"รายละเอียดและสมัคร: {_base()}/partners?src=invite\n\nสนใจคุยสั้น ๆ 15 นาที ตอบอีเมลนี้ได้เลยครับ\n\nเจอ.AI")
    tpl = {"subject": f"ชวน {who} เป็นพันธมิตรเจอ.AI (ค่าแนะนำ {pc}%)", "body": tpl_body, "source": "template"}
    llm = llm or HOOKS["llm"]
    if llm is None:
        from . import ai_client
        llm = lambda m: ai_client.chat_for("partner", m, max_tokens=2500, json_mode=True)
    prompt = (f"เขียนอีเมลภาษาไทยสั้นไม่เกิน 120 คำ ชวน {who} ({p['website'] or p['domain']}) ซึ่งเป็นเอเจนซี่/ผู้รับทำเว็บหรือการตลาด มาเป็นพันธมิตรของเจอ.AI\n"
              f"ข้อเท็จจริงที่ใช้ได้เท่านั้น: เจอ.AI ช่วยให้เว็บธุรกิจไทยถูก ChatGPT/Gemini/Google AI หยิบไปตอบ · ลูกค้าของพันธมิตรได้ตรวจเว็บฟรี · "
              f"ค่าแนะนำ {pc}% ของค่าบริการ {mo} เดือนแรก · แพ็กเกจ Agency ดูแลหลายเว็บในบัญชีเดียว · ลิงก์สมัคร {_base()}/partners?src=invite\n"
              "ห้ามการันตีผลหรืออันดับ ห้ามบอกตัวเลขอื่นที่ไม่ได้ให้ ปิดท้ายด้วยชวนคุย 15 นาที · ตอบ JSON เท่านั้น: {\"subject\": \"...\", \"body\": \"...\"}")
    try:
        raw = llm([{"role": "user", "content": prompt}])
        m = re.search(r"\{[\s\S]*\}", raw or "")
        d = json.loads(m.group(0)) if m else {}
        subj, body = str(d.get("subject") or "").strip(), str(d.get("body") or "").strip()
        from . import deliverability
        if not subj or len(body) < 80 or FORBIDDEN.search(subj + body) or deliverability.spam_flags(subj, body):
            raise ValueError("ไม่ผ่านกติกา")
        if "/partners" not in body:
            body += f"\n\nรายละเอียดและสมัคร: {_base()}/partners?src=invite"
        return {"subject": subj[:120], "body": body[:2000], "source": "llm"}
    except Exception as e:
        tpl["source"] = f"template ({type(e).__name__})"
        return tpl


def propose_invite(p, step: int = 1) -> int:
    d = draft_invite(p)
    if step > 1:
        d = {**d, "subject": "Re: " + d["subject"].removeprefix("Re: "),
             "body": f"สวัสดีครับ ทีม {p['company'] or p['domain']}\n\nขอตามเรื่องที่ชวนเป็นพันธมิตรเจอ.AI เมื่อไม่กี่วันก่อนครับ ถ้ายังไม่สะดวกไม่เป็นไรครับ "
                     f"ถ้าสนใจ ดูรายละเอียดได้ที่ {_base()}/partners?src=follow หรือตอบอีเมลนี้\n\nเจอ.AI"}
    from . import pipeline
    did = db.add_decision("partner", "partner_outreach", f"ชวนพันธมิตร {p['domain']}" + (" (ติดตาม)" if step > 1 else ""),
                          f"ถึง {p['email']} · {p['company'] or p['domain']}\n\nหัวข้อ: {d['subject']}\n\n{d['body']}",
                          {"partner_id": p["id"], "step": step, **d})
    return pipeline._maybe_auto(did)


@office.track("partner", "หาพันธมิตร: เอเจนซี่เว็บ · การตลาด")
def run(now: dt.datetime | None = None, limit: int | None = None, force: bool = False) -> dict:
    from . import geo_worker, leads, prospecting
    now = now or dt.datetime.now()
    res = {"at": db.now(), "found": 0, "added": 0, "proposed": 0, "followups": 0, "approved_commissions": 0, "domains": []}
    res["approved_commissions"] = approve_due(now)
    if not enabled() and not force:
        res["skipped"] = "ปิดอยู่"
        db.set_setting("partner_last", json.dumps(res, ensure_ascii=False))
        return res
    # ติดตามครั้งเดียว
    for p in db.list_partners("invited"):
        if p["touches"] == 1 and p["last_contacted_at"] and (now - dt.datetime.fromisoformat(p["last_contacted_at"][:19])).days >= FOLLOW_DAYS:
            if not any(d["kind"] == "partner_outreach" and json.loads(d["payload_json"] or "{}").get("partner_id") == p["id"]
                       for d in db.list_decisions("proposed", 300)):
                propose_invite(p, step=2)
                res["followups"] += 1
    # หาใหม่
    search = HOOKS["search"] or geo_worker.search
    contact = HOOKS["contact"] or prospecting.find_contact
    taken = {(p["domain"] or "").lower() for p in db.list_partners()} | {l["domain"] for l in db.list_leads(None, 100000)}
    lim = limit or daily_limit()
    for q in next_queries():
        if res["added"] >= lim:
            break
        try:
            rows = search(q, 10) or []
        except Exception:
            rows = []
        for r in rows:
            if res["added"] >= lim:
                break
            site = leads.normalize_url(r.get("url") or "")
            dom = leads.domain_of(site) if site else ""
            if not dom or dom in taken or not prospecting.is_business_site(dom):
                continue
            taken.add(dom)
            res["found"] += 1
            try:
                c = contact(site)
            except Exception:
                c = {}
            email = (c.get("email") or "").strip().lower()
            pid = db.add_partner(status="prospect", kind="agency", company=(r.get("title") or "").split("|")[0].split(" - ")[0].strip()[:120] or dom,
                                 email=email or None, website=site, domain=dom, source=f"ค้นหา: {q}")
            res["added"] += 1
            res["domains"].append(dom)
            if email:
                propose_invite(db.get_partner(pid))
                res["proposed"] += 1
    db.set_setting("partner_last", json.dumps(res, ensure_ascii=False))
    return res


def send_invite(d, who: str) -> str:
    """ทำตามข้อเสนอ partner_outreach ที่อนุมัติแล้ว"""
    from . import mailer, deliverability
    pl = json.loads(d["payload_json"] or "{}")
    p = db.get_partner(pl.get("partner_id") or 0)
    if not p:
        return "ไม่พบพันธมิตร"
    if p["status"] in ("unsubscribed", "rejected", "active", "applied", "replied"):
        return f"ไม่ส่ง — สถานะ {STATUS_TH.get(p['status'], p['status'])}"
    if not (p["email"] and mailer.is_deliverable(p["email"]) and mailer.config()):
        return "ส่งไม่ได้ — ไม่มีอีเมลที่ส่งได้"
    body = pl.get("body", "") + f"\n\n— เจอ.AI · ไม่อยากรับการติดต่อจากเรา กดที่ {unsub_url(p)} เราจะไม่ติดต่ออีก"
    fa, fn = deliverability.active_from()
    mailer.send_plain(p["email"], pl.get("subject") or "ชวนเป็นพันธมิตรเจอ.AI", body, unsub_url(p), fa, fn)
    db.update_partner(p["id"], status="invited", touches=int(p["touches"] or 0) + 1, last_contacted_at=db.now())
    _record(p["id"], f"ส่งคำชวนครั้งที่ {int(p['touches'] or 0) + 1} ({who})")
    return f"ส่งคำชวนถึง {p['email']} แล้ว"


# ---------- สมัคร / อนุมัติ ----------
def apply(name: str, company: str, email: str, phone: str, website: str, clients: str, payout: str = "", src: str = "") -> tuple[str, int | None]:
    from . import leads
    email = (email or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", email):
        return "กรอกอีเมลให้ถูกต้อง", None
    if len((name or "").strip()) < 2:
        return "กรอกชื่อผู้ติดต่อ", None
    site = leads.normalize_url(website or "")
    dom = leads.domain_of(site) if site else ""
    p = db.find_partner(email=email) or (db.find_partner(domain=dom) if dom else None)
    fields = dict(name=name.strip()[:120], company=(company or "").strip()[:160] or None, email=email, phone=(phone or "").strip()[:40] or None,
                  website=site or None, domain=dom or None, clients=(clients or "").strip()[:200] or None, payout=(payout or "").strip()[:300] or None)
    if p and p["status"] == "active":
        return "อีเมลนี้เป็นพันธมิตรอยู่แล้ว — ลิงก์แดชบอร์ดส่งไปในอีเมลต้อนรับ", p["id"]
    if p:
        db.update_partner(p["id"], status="applied", applied_at=db.now(), **fields)
        pid = p["id"]
    else:
        pid = db.add_partner(status="applied", kind="agency", applied_at=db.now(), source=f"สมัครเอง{(' · ' + src) if src else ''}", **fields)
    _record(pid, "สมัครผ่านหน้า /partners")
    p = db.get_partner(pid)
    from . import pipeline
    did = db.add_decision("partner", "partner_apply", f"รับพันธมิตร {p['company'] or p['name']}",
                          f"{p['name']} · {p['company'] or '-'} · {p['email']} · {p['phone'] or '-'}\nเว็บ: {p['website'] or '-'}\nลูกค้าที่ดูแล: {p['clients'] or '-'}\n"
                          f"อนุมัติ = ออกรหัสแนะนำ ส่งอีเมลต้อนรับพร้อมลิงก์และแดชบอร์ด · ค่าแนะนำ {pct():g}% {months()} เดือนแรก",
                          {"partner_id": pid})
    pipeline.alert_admins(f"🤝 {p['company'] or p['name']} สมัครเป็นพันธมิตร — อนุมัติที่หน้าการตัดสินใจ", "/admin/partners", "lead",
                          subject=f"🤝 ผู้สมัครพันธมิตรใหม่: {p['company'] or p['name']}")
    return "", did


def activate(p, who: str = "admin") -> str:
    from . import mailer
    if p["status"] == "active":
        return "เป็นพันธมิตรอยู่แล้ว"
    code = p["code"] or _new_code(p["company"] or p["domain"] or p["name"] or "")
    db.update_partner(p["id"], status="active", code=code, activated_at=db.now())
    p = db.get_partner(p["id"])
    _record(p["id"], f"อนุมัติเป็นพันธมิตร ({who}) รหัส {code}")
    note = ""
    if p["email"] and mailer.is_deliverable(p["email"]) and mailer.config():
        text = (f"สวัสดีครับ คุณ{p['name'] or p['company'] or ''}\n\nยินดีต้อนรับสู่พันธมิตรเจอ.AI ครับ\n\n"
                f"• ลิงก์แนะนำของคุณ: {ref_url(p)} (ลูกค้าที่ตรวจเว็บฟรีผ่านลิงก์นี้ภายใน {COOKIE_DAYS} วัน นับเป็นของคุณ)\n"
                f"• แดชบอร์ด (ดูลีด ลูกค้า ค่าแนะนำ และส่งชื่อลูกค้าให้เราโดยตรง): {dash_url(p)}\n"
                f"• ค่าแนะนำ {pct():g}% ของค่าบริการ (ก่อน VAT) {months()} เดือนแรกของลูกค้าแต่ละราย จ่ายทุกเดือนหลังพ้นช่วงคืนเงิน 7 วัน\n\n"
                "กติกาสั้น ๆ: ไม่การันตีอันดับหรือผลลัพธ์กับลูกค้า · ส่งชื่อลูกค้าเฉพาะคนที่ยินยอมแล้ว · ไม่ส่งอีเมลเหมารวม\n\nทีมพันธมิตร เจอ.AI")
        try:
            mailer.send(p["email"], "ยินดีต้อนรับสู่พันธมิตรเจอ.AI", mailer.wrap_html("ยินดีต้อนรับสู่พันธมิตรเจอ.AI", "<p>" + _html.escape(text).replace("\n", "<br>") + "</p>"), text=text)
            note = f" · ส่งอีเมลต้อนรับถึง {p['email']}"
        except Exception as e:
            note = f" · ส่งอีเมลต้อนรับไม่สำเร็จ ({type(e).__name__})"
    return f"รับ {p['company'] or p['name']} เป็นพันธมิตรแล้ว รหัส {code}{note}"


def execute(d, who: str) -> str:
    pl = json.loads(d["payload_json"] or "{}")
    if d["kind"] == "partner_outreach":
        return send_invite(d, who)
    if d["kind"] == "partner_apply":
        p = db.get_partner(pl.get("partner_id") or 0)
        return activate(p, who) if p else "ไม่พบผู้สมัคร"
    return "ไม่รู้จักเรื่องนี้"


def unsubscribe(p) -> None:
    db.update_partner(p["id"], status="unsubscribed")
    _record(p["id"], "ขอไม่รับการติดต่อ")
    for d in db.list_decisions("proposed", 300):
        if d["kind"] == "partner_outreach" and json.loads(d["payload_json"] or "{}").get("partner_id") == p["id"]:
            db.set_decision(d["id"], "cancelled", "system", "ขอไม่รับการติดต่อ")


def match_inbox(m: dict):
    """อีเมลจากพันธมิตร/ผู้ที่เราชวน → บันทึกที่พันธมิตร ไม่ให้กลายเป็นลีดลูกค้า"""
    frm = (m.get("from") or "").lower()
    p = db.find_partner(email=frm) or db.find_partner(domain=frm.split("@")[-1]) if "@" in frm else None
    if not p:
        return None
    if p["status"] in ("prospect", "invited"):
        db.update_partner(p["id"], status="replied", replied_at=db.now())
    _record(p["id"], f"ตอบอีเมล: {(m.get('body') or '')[:200]}")
    for d in db.list_decisions("proposed", 300):
        if d["kind"] == "partner_outreach" and json.loads(d["payload_json"] or "{}").get("partner_id") == p["id"]:
            db.set_decision(d["id"], "cancelled", "system", "ตอบกลับแล้ว")
    from . import pipeline
    pipeline.alert_admins(f"🤝 {p['company'] or p['domain']} ตอบอีเมลเรื่องพันธมิตร: {(m.get('body') or '')[:120]}", "/admin/partners", "lead",
                          subject=f"🤝 {p['company'] or p['domain']} ตอบเรื่องพันธมิตร")
    return p


# ---------- ที่มาของลีด ----------
def attribute(lead_id: int, code: str, how: str = "ลิงก์") -> bool:
    """ลีดนี้มาจากพันธมิตร (ครั้งแรกเท่านั้น · พันธมิตรต้อง active)"""
    if not code:
        return False
    p = db.find_partner(code=code)
    lead = db.get_lead(lead_id)
    if not p or p["status"] != "active" or not lead or lead["partner_id"]:
        return False
    if p["domain"] and lead["domain"] == p["domain"]:
        return False                                        # แนะนำตัวเองไม่นับ
    db.update_lead(lead_id, partner_id=p["id"])
    db.add_lead_event(lead_id, "partner_ref", f"มาจากพันธมิตร {p['company'] or p['name']} ({how} · {p['code']})", "partner")
    return True


def submit_client(p, website: str, email: str, name: str, consent: bool) -> str:
    """พันธมิตรส่งชื่อลูกค้าให้เรา → ลีด + ตรวจเว็บ + CEO เสนอทัก (ระบุว่าแนะนำโดยพันธมิตร)"""
    from . import leads, pipeline
    if p["status"] != "active":
        return "บัญชีพันธมิตรยังไม่เปิดใช้งาน"
    if not consent:
        return "ติ๊กยืนยันว่าลูกค้ายินยอมให้เราติดต่อก่อน"
    site = leads.normalize_url(website or "")
    if not site:
        return "กรอกเว็บไซต์ของลูกค้าให้ถูกต้อง"
    dom = leads.domain_of(site)
    old = next((l for l in db.list_leads(None, 100000) if l["domain"] == dom), None)
    if old and old["partner_id"] and old["partner_id"] != p["id"]:
        return "เว็บนี้มีพันธมิตรรายอื่นแนะนำไว้แล้ว"
    if old and old["status"] not in ("new", "lost"):
        return "เว็บนี้อยู่ในสายขายของเราอยู่แล้ว จึงนับเป็นการแนะนำไม่ได้"
    lid = old["id"] if old else db.add_lead(site, dom, source="partner", email=(email or "").strip().lower() or None)
    db.update_lead(lid, src="partner", consent=1, name=(name or "").strip()[:120] or None,
                   **({"email": email.strip().lower()} if email and "@" in email else {}))
    attribute(lid, p["code"], "ส่งผ่านแดชบอร์ด")
    try:
        pipeline.run_lead_audit(lid, site)
        lead = db.get_lead(lid)
        if lead["audit_json"]:
            pipeline.ceo_propose(lead)
    except Exception:
        pass
    _record(p["id"], f"ส่งลูกค้า {dom}")
    return ""


# ---------- ค่าแนะนำ ----------
def accrue(receipt) -> int | None:
    """เรียกจากฝ่ายบัญชีทุกครั้งที่ออกใบเสร็จ"""
    from . import execs
    lead = db.get_lead(receipt["lead_id"]) if receipt["lead_id"] else None
    if not lead or not lead["partner_id"] or db.commission_for_receipt(receipt["id"]):
        return None
    p = db.get_partner(lead["partner_id"])
    if not p or p["status"] not in ("active", "paused"):
        return None
    first = [r for r in db.list_acct_docs("receipt", lead_id=lead["id"], limit=500) if r["status"] != "void"]
    start = min((r["created_at"] for r in first), default=receipt["created_at"])
    if (dt.datetime.fromisoformat(receipt["created_at"][:19]) - dt.datetime.fromisoformat(start[:19])).days > months() * 31:
        return None                                       # พ้นช่วงได้ค่าแนะนำแล้ว
    base = float(receipt["subtotal"])
    amt = round(base * pct() / 100, 2)
    eligible = (dt.datetime.fromisoformat(receipt["created_at"][:19]) + dt.timedelta(days=int(execs.MANDATE["refund_days"]))).isoformat(timespec="seconds")
    cid = db.add_commission(partner_id=p["id"], lead_id=lead["id"], quote_id=receipt["quote_id"], receipt_id=receipt["id"], base=base, pct=pct(),
                            amount=amt, status="pending", eligible_at=eligible, note=receipt["number"])
    _record(p["id"], f"ค่าแนะนำ {amt:,.2f} บาท จาก {lead['domain']} ({receipt['number']})")
    return cid


def void_for_receipt(receipt) -> None:
    c = db.commission_for_receipt(receipt["id"])
    if c and c["status"] in ("pending", "approved"):
        db.update_commission(c["id"], status="void", note=f"{c['note'] or ''} · ใบเสร็จถูกยกเลิก")


def approve_due(now: dt.datetime | None = None) -> int:
    now = now or dt.datetime.now()
    n = 0
    for c in db.list_commissions(status="pending"):
        r = db.get_acct_doc(c["receipt_id"]) if c["receipt_id"] else None
        if r and r["status"] == "void":
            db.update_commission(c["id"], status="void")
            continue
        if c["eligible_at"] and dt.datetime.fromisoformat(c["eligible_at"][:19]) <= now:
            db.update_commission(c["id"], status="approved")
            n += 1
    return n


def mark_paid(pid: int, who: str = "admin") -> str:
    due = db.list_commissions(pid, "approved")
    if not due:
        return "ไม่มีค่าแนะนำที่ถึงกำหนดจ่าย"
    total = round(sum(c["amount"] for c in due), 2)
    for c in due:
        db.update_commission(c["id"], status="paid", paid_at=db.now())
    _record(pid, f"จ่ายค่าแนะนำ {total:,.2f} บาท ({len(due)} รายการ) โดย {who}")
    return f"บันทึกว่าจ่ายค่าแนะนำ {total:,.2f} บาทแล้ว"


def stats(p) -> dict:
    ls = db.leads_of_partner(p["id"])
    cs = db.list_commissions(p["id"])
    s = lambda st: round(sum(c["amount"] for c in cs if c["status"] == st), 2)
    return {"leads": len(ls), "won": sum(1 for l in ls if l["status"] == "won"), "pending": s("pending"), "approved": s("approved"), "paid": s("paid"),
            "lead_rows": ls, "commissions": cs}


def summary() -> dict:
    ps = db.list_partners()
    cs = db.list_commissions()
    by = lambda st: sum(1 for p in ps if p["status"] == st)
    return {"active": by("active"), "applied": by("applied"), "invited": by("invited"), "replied": by("replied"), "prospect": by("prospect"),
            "due": round(sum(c["amount"] for c in cs if c["status"] == "approved"), 2),
            "pending": round(sum(c["amount"] for c in cs if c["status"] == "pending"), 2),
            "leads": sum(1 for l in db.list_leads(None, 100000) if l["partner_id"])}


def status_text() -> str | None:
    s = summary()
    if not any((s["active"], s["applied"], s["invited"], s["prospect"])):
        return None
    t = f"พันธมิตร: {s['active']} ราย · รออนุมัติ {s['applied']} · ชวนแล้ว {s['invited']} · ตอบ {s['replied']} · ลีดจากพันธมิตร {s['leads']}"
    if s["due"]:
        t += f" · ค่าแนะนำถึงกำหนดจ่าย {s['due']:,.0f} บาท"
    return t
