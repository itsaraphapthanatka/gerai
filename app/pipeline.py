"""เส้นทางลูกค้าทั้งสาย (เฟส 2): ตรวจเว็บ → ทัก → ตอบกลับ → ใบเสนอราคา → ยืนยัน → ชำระ → เปิดบัญชี+แบรนด์
รวมตรรกะที่เคยอยู่ใน main.py ไว้ที่เดียว ให้เส้นทางเว็บ, LINE, หน้าแอดมิน และ cron ใช้ร่วมกัน
ผู้บริหารเอเจนต์ยังเสนอก่อนทำ เว้นเรื่องที่สลับเป็นตัดสินเองแล้ว (execs.auto_enabled) · ทุกจุดสัมผัสลง lead_events"""
from __future__ import annotations
import datetime
import html as _html
import json
import re
import os
import secrets
import threading

import bcrypt

from . import db, execs, leads, line_oa, mailer, quotes as Q, site_health
from . import office

RUN_INLINE = False          # True = งานเบื้องหลัง (ตรวจเว็บจาก LINE) รันในเธรดเดียว — ใช้ตอนทดสอบ


def public_base() -> str:
    return os.getenv("PUBLIC_BASE_URL", "https://geo.appreview.cloud").rstrip("/")


def record(lead_id: int, kind: str, note: str = "", actor: str = "system") -> None:
    db.add_lead_event(lead_id, kind, note, actor)


def _hash_pw(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def _ensure_token(lead):
    if not lead["token"]:
        db.update_lead(lead["id"], token=leads.new_token())
        lead = db.get_lead(lead["id"])
    return lead


def result_url(lead, via: str = "") -> str:
    lead = _ensure_token(lead)
    return f"{public_base()}/audit/{lead['token']}" + (f"?via={via}" if via else "")


def unsubscribe_url(lead) -> str:
    lead = _ensure_token(lead)
    return f"{public_base()}/u/{lead['token']}"


def quote_url(q) -> str:
    return f"{public_base()}/q/{q['token']}"


def lead_lang(lead) -> str:
    """ภาษาที่ลีดเลือกบนเว็บ (ลีดที่เราหาเอง/นำเข้า = ไทย)"""
    return db.row_lang(lead)


def _tl(lang: str):
    from . import i18n
    return lambda s, **kw: i18n.tr(s, lang, **kw)


def outreach_footer(lead, lang: str | None = None) -> str:
    lang = lang or lead_lang(lead)
    return "\n\n" + _tl(lang)("— เจอ.AI (ข้อความนี้ส่งจากระบบอัตโนมัติ) · ไม่อยากรับการติดต่อจากเรา กดที่ {url} เราจะไม่ติดต่ออีก",
                              url=unsubscribe_url(lead))


def alert_admins(message: str, link: str, type_: str = "lead", subject: str | None = None) -> None:
    """แจ้งเจ้าของทุกช่องทางที่มี: กระดิ่งในแอป · LINE (ถ้าผูกแล้ว) · อีเมลแอดมินที่ส่งได้ (เฉพาะเรื่องที่ต้องลงมือ มี subject)"""
    db.notify_admins(message, link, type_)
    url = f"{public_base()}{link}"
    try:
        line_oa.notify_owner(f"{message}\n{url}")
    except Exception:
        pass
    if subject and mailer.config():
        for t in db.list_all_tenants():
            if t["is_admin"] and mailer.is_deliverable(t["email"]):
                try:
                    mailer.send(t["email"], subject, mailer.wrap_html(subject, "<p>" + _html.escape(message) + f"</p><p><a href='{url}'>{url}</a></p>"),
                                text=f"{message}\n{url}")
                except Exception:
                    pass


def _cancel_pending(lead_id: int, kinds: set, why: str) -> None:
    """ข้อเสนอที่ค้างแต่หมดความหมายแล้ว (เช่น จะทักทั้งที่ลูกค้าตอบกลับมาเองแล้ว) → ยกเลิก ไม่นับเป็นความเห็นไม่ตรงกัน"""
    for d in db.list_decisions("proposed", 500):
        if d["lead_id"] == lead_id and d["kind"] in kinds:
            db.set_decision(d["id"], "cancelled", "system", why)


# ---------- ตรวจเว็บ ----------
@office.track("cto", lambda lead_id, site, ip="": f"ตรวจเว็บ {leads.domain_of(site)}")
def run_lead_audit(lead_id: int, site: str, ip: str = "") -> str:
    """ดึงเว็บจริงมาตรวจ (ราวครึ่งนาที) เก็บผล + คะแนน + token สำหรับหน้าผล · คืน token"""
    report = site_health.run_checks(site)
    sc = leads.score_audit(report)
    lead = db.get_lead(lead_id)
    token = lead["token"] or leads.new_token()
    db.update_lead(lead_id, audit_json=json.dumps(report, ensure_ascii=False), audit_at=db.now(),
                   score=sc["score"], token=token, **({"ip": ip} if ip else {}))
    record(lead_id, "audit", f"ตก {sc['n_fail']} ข้อ · โอกาส {sc['level']} {sc['score']}/100")
    return token


# ---------- CEO เสนอ (หรือทำเองเมื่อเรื่องนั้นสลับเป็น auto แล้ว) ----------
def _maybe_auto(did: int) -> int:
    d = db.get_decision(did)
    if not d or d["status"] != "proposed":
        return did
    try:
        escalate = bool(json.loads(d["payload_json"] or "{}").get("escalate"))
    except Exception:
        escalate = False
    if escalate:                                     # นอกกรอบ CFO → รอคนเสมอ ไม่ว่าโหมดไหน
        return did
    if d["kind"] == "post" and json.loads(d["payload_json"] or "{}").get("kind") == "case_study":
        return did                                   # เคสศึกษามีชื่อและตัวเลขของลูกค้า → เจ้าของอนุมัติเสมอ แม้เปิดโพสต์อัตโนมัติ
    if d["kind"] in ("outreach", "followup") and d["lead_id"]:
        ld = db.get_lead(d["lead_id"])
        if ld and ld["email_src"] == "search" and send_channel(ld).startswith("อีเมล"):
            return did                               # อีเมลเดาจากผลค้นหา = เสี่ยงเด้ง → ไม่ส่งเอง ให้เจ้าของเลือก
    if execs.auto_enabled(d["kind"]):
        try:
            result = execute_decision(d, "auto")
            db.set_decision(did, "auto", "ceo", result)
        except Exception as e:                       # ทำเองไม่สำเร็จ → ค้างไว้ให้คนดู ไม่เงียบ
            db.set_decision(did, "proposed", None, f"ทำอัตโนมัติไม่สำเร็จ: {type(e).__name__}: {str(e)[:120]}")
    return did


def run_pending_auto(kind: str | None = None) -> int:
    """ข้อเสนอที่ค้างอยู่ก่อนเปิดโหมดตัดสินเอง → ทำให้เลย (เว้นนอกกรอบ) · เรียกตอนสลับโหมดและตอนสรุปเช้า · คืนจำนวนที่ทำ"""
    n = 0
    for d in db.list_decisions("proposed", 500):
        if kind and d["kind"] != kind:
            continue
        if d["kind"] in execs.AUTO_ELIGIBLE and execs.auto_enabled(d["kind"]):
            _maybe_auto(d["id"])
            if db.get_decision(d["id"])["status"] == "auto":
                n += 1
    return n


@office.track("ceo", lambda lead: f"คัดและร่างข้อความทัก {lead['domain']}")
def ceo_propose(lead) -> int | None:
    """CEO คัด + ร่างข้อความทัก · CFO ตรวจราคากับกรอบ → แถวใน decisions (รอคน หรือทำเองถ้าเรื่องนั้นเป็น auto)"""
    if lead["status"] in leads.NO_CONTACT:
        return None                                  # ไม่ทักซ้ำคนที่ขอไม่รับ/ปิดไปแล้ว
    report = json.loads(lead["audit_json"] or "{}") if lead["audit_json"] else {}
    if not report:
        return None
    pl = execs.ceo_review_lead(lead, report, result_url(lead, "mail"))
    if pl["action"] == "decline":
        return _maybe_auto(db.add_decision("ceo", "lead_review", f"ไม่รับ {lead['domain']}", pl["reason"], pl, lead_id=lead["id"]))
    pl["cfo"] = execs.cfo_check(pl)
    pl["escalate"] = not pl["cfo"]["ok"]
    pl["channel"] = send_channel(lead)
    risky = lead["email_src"] == "search" and pl["channel"].startswith("อีเมล")
    if risky:
        pl["risky_email"] = True
    proposal = (("⚠️ อีเมลนี้เดาจากผลค้นหา ไม่ได้อยู่บนเว็บของเขา — เสี่ยงเด้ง (เด้งเยอะ = โดเมนเราเสียชื่อ) ระบบไม่ส่งเอง"
                 + (f" · มีช่องทาง {lead['contact']} ลองทักทางนั้นแทนได้" if lead["contact"] else "") + "\n\n") if risky else "") + (f"รับลูกค้า · แพ็กเกจ {pl['package']} {pl['price']:,} บาท/เดือน · โอกาส {pl['level']} ({pl['score']}/100)\n"
                f"เหตุผล: {'; '.join(pl['reasons'])}\nCFO: {pl['cfo']['note']}\nส่งทาง: {pl['channel'] or 'ไม่มีช่องทางอัตโนมัติ — เติมอีเมลที่หน้าลูกค้าก่อน ไม่งั้นอนุมัติแล้วก็ส่งไม่ได้'}"
                f"\n\nหัวข้อ: {pl['subject']}\n\n{pl['body']}")
    return _maybe_auto(db.add_decision("ceo", "outreach", f"ทัก {lead['domain']} ({pl['package']})", proposal, pl, lead_id=lead["id"]))


def send_channel(lead) -> str:
    """ช่องทางที่ระบบส่งถึงลูกค้ารายนี้ได้เอง ('' = ไม่มี): อีเมลที่ส่งได้ > LINE ที่เขาทักเรามา"""
    if lead["email"] and mailer.is_deliverable(lead["email"]):
        return f"อีเมล {lead['email']}"
    if lead["line_user_id"]:
        return "LINE (แชตที่ลูกค้าทักมา)"
    from . import sms
    m = sms.lead_mobile(lead)
    if m and sms.available():
        return f"SMS {m[:3]}-{m[3:6]}-{m[6:]}"
    return ""


def _send_text(lead, subject: str, body: str, plain: bool = False) -> str:
    """ส่งถึงลูกค้าทางที่มี: อีเมลที่ส่งได้ก่อน → LINE → ไม่มี (คืน '' ให้คนส่งเอง)
    plain = อีเมลทัก/ติดตามผล: ข้อความธรรมดาแบบคนพิมพ์ + หัวจดหมายเลิกรับคลิกเดียว (ไม่ให้ตกแท็บโปรโมชัน)
    ข้อความมาเป็นภาษาของลีดแล้ว (lead.lang) — กรอบอีเมลแปลตามภาษาเดียวกัน"""
    lang = lead_lang(lead)
    if lead["email"] and mailer.is_deliverable(lead["email"]) and mailer.config():
        from . import deliverability
        if not deliverability.mx_ok(lead["email"]):          # โดเมนผู้รับไม่มีเซิร์ฟเวอร์รับอีเมล → ส่งไปเด้งแน่ ไม่ส่ง และล้างอีเมลทิ้ง
            record(lead["id"], "note", f"ไม่ส่งถึง {lead['email']} — โดเมนนี้ไม่มีเซิร์ฟเวอร์รับอีเมล (ไม่มี MX) ล้างอีเมลออกแล้ว ใช้ช่องทางอื่นแทน", "deliv")
            db.update_lead(lead["id"], email=None, email_src=None)
            return ""
        if plain:
            from . import deliverability
            fa, fn = deliverability.active_from()
            mailer.send_plain(lead["email"], subject, body, unsubscribe_url(lead), fa, fn, **mailer.lang_kw(lang))
        else:
            mailer.send(lead["email"], subject, mailer.wrap_html(subject, "<p>" + _html.escape(body).replace("\n", "<br>") + "</p>", lang=lang), text=body)
        return f"ส่งอีเมลถึง {lead['email']} แล้ว"
    if lead["line_user_id"] and line_oa.push(lead["line_user_id"], [body[:4900]]):
        return "ส่งทาง LINE แล้ว"
    from . import sms
    m = sms.lead_mobile(lead)
    if m and sms.available():
        try:
            return sms.send(m, sms.compact(subject, body, unsubscribe_url(lead)))   # QuietHours โยนต่อ → ข้อเสนอค้างไว้ส่งรอบถัดไป
        except sms.QuietHours:
            raise
        except RuntimeError:
            return ""
    return ""


def execute_decision(d, who: str) -> str:
    """ทำตามข้อเสนอที่อนุมัติ (หรือ auto): ทัก · ปิดลูกค้าที่ไม่รับ · ส่งใบเสนอราคา · ยืนยันยอดเข้าแล้วเปิดบัญชี"""
    pl = json.loads(d["payload_json"] or "{}")
    lead = db.get_lead(d["lead_id"]) if d["lead_id"] else None
    if d["kind"] == "outreach" and lead:
        if lead["status"] in leads.NO_CONTACT:
            return f"ไม่ส่ง — ลูกค้าอยู่ในสถานะ {leads.STATUS_LABEL.get(lead['status'], lead['status'])}"
        if not send_channel(lead):
            record(lead["id"], "note", "อนุมัติข้อความทักแล้วแต่ส่งไม่ได้ — ไม่มีอีเมล/LINE ที่ส่งได้ (เติมอีเมลที่หน้าลูกค้าแล้วกดให้ CEO เสนอใหม่)", who)
            return "ส่งไม่ได้ — ไม่มีอีเมล/LINE ที่ส่งได้ สถานะยังเป็นเดิม เติมอีเมลที่หน้าลูกค้าแล้วกดให้ CEO เสนอใหม่"
        body = pl.get("body", "") + outreach_footer(lead)
        sent = _send_text(lead, pl.get("subject") or _tl(lead_lang(lead))("ผลตรวจเว็บ {dom}", dom=lead["domain"]), body, plain=True)
        if not sent:
            record(lead["id"], "note", "ส่งข้อความทักไม่สำเร็จ (ช่องทางไม่ตอบ) — ลองใหม่ได้จากหน้าลูกค้า", who)
            return "ส่งไม่สำเร็จ — ช่องทางไม่ตอบ สถานะยังเป็นเดิม"
        db.update_lead(lead["id"], status="contacted", last_contacted_at=db.now())
        if pl.get("exp") and pl.get("arm") and sent.startswith("ส่งอีเมล"):
            db.update_lead(lead["id"], exp_arm=f"{pl['exp']}:{pl['arm']}")          # นับเข้าการทดลองข้อความเฉพาะที่ส่งอีเมลจริง
        record(lead["id"], "contacted", sent, who)
        return sent
    if d["kind"] == "lead_review" and lead and pl.get("action") == "decline":
        db.update_lead(lead["id"], status="lost")
        record(lead["id"], "lost", pl.get("reason", ""), who)
        return "ปิดลูกค้ารายนี้ตามที่ CEO เสนอ"
    if d["kind"] == "quote" and pl.get("quote_id"):
        q = db.get_quote(pl["quote_id"])
        return send_quote(q, who) if q else "ไม่พบใบเสนอราคา"
    if d["kind"] == "expansion":
        from . import expansion
        return expansion.execute(d, who)
    if d["kind"] in ("partner_outreach", "partner_apply"):
        from . import partners
        return partners.execute(d, who)
    if d["kind"] == "payment" and pl.get("acct_doc_id"):
        from . import accounting
        inv = db.get_acct_doc(pl["acct_doc_id"])
        return accounting.mark_paid(inv, who) if inv else "ไม่พบใบแจ้งหนี้"
    if d["kind"] == "payment" and pl.get("quote_id"):
        q = db.get_quote(pl["quote_id"])
        return confirm_payment(q, who) if q else "ไม่พบใบเสนอราคา"
    if d["kind"] == "reply" and lead:
        if lead["status"] == "unsubscribed":
            return "ไม่ส่ง — ลูกค้าขอไม่รับการติดต่อ"
        to = (pl.get("to") or lead["email"] or "").strip()
        if not (to and mailer.is_deliverable(to) and mailer.config()):
            return "ส่งไม่ได้ — ไม่มีอีเมลที่ส่งได้"
        body = pl.get("body", "") + outreach_footer(lead)
        subj = pl.get("subject") or _tl(lead_lang(lead))("เรื่องเว็บ {dom}", dom=lead["domain"])
        from . import deliverability
        fa, fn = deliverability.active_from()
        mailer.send_plain(to, subj, body, unsubscribe_url(lead), fa, fn, **mailer.lang_kw(lead_lang(lead)))        # ตอบในรูปแบบและจากที่อยู่เดียวกับอีเมลทัก
        record(lead["id"], "note", f"ตอบอีเมลลูกค้าแล้ว: {pl.get('body', '')[:200]}", who)
        return f"ตอบอีเมลถึง {to} แล้ว"
    if d["kind"] == "followup" and lead:
        from . import followup
        why = followup.eligible(lead)
        if why or followup.history(lead["id"])["replied"]:
            return f"ไม่ส่ง — {why or 'ลูกค้าตอบกลับแล้ว'}"
        sent = _send_text(lead, pl.get("subject") or _tl(lead_lang(lead))("ผลตรวจเว็บ {dom}", dom=lead["domain"]), pl.get("body", "") + outreach_footer(lead), plain=True)
        if not sent:
            return "ส่งไม่สำเร็จ — ช่องทางไม่ตอบ"
        db.update_lead(lead["id"], last_contacted_at=db.now())
        record(lead["id"], "followup", f"ครั้งที่ {pl.get('step')}: {sent}", who)
        return f"ติดตามครั้งที่ {pl.get('step')}/3 · {sent}"
    if d["kind"] == "post" and pl.get("post_id"):
        from . import marketing
        post = db.get_post(pl["post_id"])
        return marketing.publish_post(post) if post else "ไม่พบโพสต์"
    return "รับทราบ"


# ---------- จุดสัมผัสจากลูกค้า ----------
def mark_opened(lead) -> None:
    """ลูกค้าเปิดผลตรวจจากลิงก์ในอีเมล (?via=mail) — นับครั้งแรกครั้งเดียว"""
    if lead["status"] in ("contacted", "quoted") and not any(e["kind"] == "opened" for e in db.list_lead_events(lead["id"], 200)):
        record(lead["id"], "opened", "", "customer")


def lead_replied(lead, message: str = "", via: str = "เว็บ", actor: str = "customer") -> int | None:
    """ลูกค้ากด 'คุยต่อ/ขอใบเสนอราคา' หรือพิมพ์สนใจทาง LINE → สถานะตอบกลับ → CEO เสนอใบเสนอราคา (ถ้ายังไม่มีใบค้าง)"""
    if lead["status"] == "unsubscribed":
        return None
    if lead["status"] in ("new", "contacted", "lost"):
        db.update_lead(lead["id"], status="replied")
    record(lead["id"], "replied", (message or "").strip()[:500] or f"กดคุยต่อผ่าน{via}", actor)
    _cancel_pending(lead["id"], {"outreach", "lead_review", "followup"}, "ลูกค้าตอบกลับมาเองแล้ว ไม่ต้องทัก")
    alert_admins(f"💬 {lead['domain']} ตอบกลับ/ขอใบเสนอราคา ผ่าน{via}" + (f": {message.strip()[:80]}" if message.strip() else ""),
                 f"/admin/leads/{lead['id']}", "lead", subject=f"💬 {lead['domain']} ตอบกลับ — CEO กำลังเสนอใบเสนอราคา")
    try:
        from . import deep_audit
        deep_audit.start(lead["id"], ai=True)          # สนใจจริง → วัด AI 20 คำถามให้ (ชั้น 2 มีค่าใช้จ่าย จึงรอสัญญาณซื้อ)
    except Exception:
        pass
    if any(q["status"] in Q.OPEN for q in db.list_quotes(lead["id"])):
        return None
    lead = db.get_lead(lead["id"])
    return propose_quote(lead) if lead["audit_json"] else None


def unsubscribe(lead, actor: str = "customer") -> None:
    """ขอไม่รับการติดต่อ: ปิดทุกช่องทาง ยกเลิกข้อเสนอที่ค้าง (ไม่นับเป็นไม่ตรงกัน) เก็บเวลาไว้เป็นหลักฐาน"""
    db.update_lead(lead["id"], status="unsubscribed", consent=0, unsub_at=db.now())
    record(lead["id"], "unsubscribed", "", actor)
    for d in db.list_decisions("proposed", 500):
        if d["lead_id"] == lead["id"]:
            db.set_decision(d["id"], "cancelled", "system", "ลูกค้าขอไม่รับการติดต่อ")


# ---------- ใบเสนอราคา ----------
def create_quote(lead, package: str = "pro", discount_pct: float = 0, setup: bool | None = None,
                 created_by: str = "ceo", notes: str = "", cycle: str = "month"):
    report = json.loads(lead["audit_json"] or "{}") if lead["audit_json"] else {}
    if setup is None:
        setup = Q.needs_setup(report)
    pr = Q.price_for(package, discount_pct, setup, cycle)
    ym = db.now()[:7]
    qid = db.add_quote(lead["id"], Q.number(ym, db.count_quotes_month(ym) + 1), leads.new_token(), pr["package"], pr["price"],
                       pr["discount_pct"], pr["net"], pr["setup"], pr["setup_price"], pr["first_total"], Q.valid_until(), created_by, notes,
                       cycle=pr["cycle"], period_total=pr["period_total"], vat_pct=Q.vat_rate_now())
    db.update_lead(lead["id"], package=package, monthly_thb=pr["net"])
    return db.get_quote(qid)


YEARLY_RE = re.compile(r"(รายปี|ทั้งปี|จ่ายปี|12\s*เดือน|annual|yearly|per year)", re.I)
MONTHLY_RE = re.compile(r"(รายเดือน|จ่ายเดือน|เดือนต่อเดือน|ทีละเดือน|monthly|per month)", re.I)


def propose_quote(lead, package: str = "pro", discount_pct: float = 0, setup: bool | None = None, cycle: str | None = None) -> int:
    """CEO เสนอใบเสนอราคา · CFO ตรวจส่วนลด/วงเงิน · เสนอรายปีก่อน (ค่าเริ่มต้น) ยกเว้นลูกค้าขอรายเดือนเอง · ลูกค้าเปลี่ยนรอบบิลได้ในหน้าใบเสนอราคา"""
    if cycle is None:
        last = next((e["note"] or "" for e in db.list_lead_events(lead["id"], 20) if e["kind"] == "replied"), "")
        cycle = "year" if YEARLY_RE.search(last) else ("month" if MONTHLY_RE.search(last) else Q.default_cycle())
    q = create_quote(lead, package, discount_pct, setup, created_by="ceo", cycle=cycle)
    cfo = execs.cfo_check({"price": q["price"], "discount_pct": q["discount_pct"], "cycle": cycle})
    pl = {"quote_id": q["id"], "number": q["number"], "package": q["package"], "price": q["price"], "discount_pct": q["discount_pct"],
          "net": q["net"], "setup": bool(q["setup"]), "first_total": q["first_total"], "cfo": cfo, "escalate": not cfo["ok"]}
    did = db.add_decision("ceo", "quote", f"เสนอราคา {lead['domain']} ({q['number']})",
                          execs.quote_proposal_text(lead, q, cfo, quote_url(q)), pl, lead_id=lead["id"])
    db.update_quote(q["id"], decision_id=did)
    return _maybe_auto(did)


def change_cycle(q, cycle: str, who: str = "customer") -> str:
    """ลูกค้าสลับรายปี/รายเดือนก่อนยืนยัน (แพ็กเกจ ส่วนลด Setup เดิม) · คืนข้อความผิดพลาด ('' = สำเร็จ)"""
    if cycle not in Q.CYCLES:
        return "รอบบิลไม่ถูกต้อง"
    if q["status"] not in ("draft", "sent", "viewed"):
        return "ยืนยันข้อเสนอแล้ว เปลี่ยนรอบบิลไม่ได้ — ติดต่อเราเพื่อขอใบใหม่"
    if Q.cycle_of(q) == cycle:
        return ""
    pr = Q.price_for(q["package"], q["discount_pct"], bool(q["setup"]), cycle)
    db.update_quote(q["id"], cycle=cycle, period_total=pr["period_total"], net=pr["net"], setup_price=pr["setup_price"], first_total=pr["first_total"])
    db.update_lead(q["lead_id"], monthly_thb=pr["net"])
    record(q["lead_id"], "note", f"{q['number']} เปลี่ยนเป็น{Q.CYCLES[cycle]} (ยอดแรก {pr['first_total']:,} บาท)", who)
    return ""


REVISABLE = ("draft", "sent", "viewed", "accepted")


def revise_quote(q, package: str | None = None, discount_pct: float | None = None, setup: bool | None = None,
                 cycle: str | None = None, who: str = "owner") -> str:
    """ปรับใบเสนอราคาเดิม (ลิงก์เดิม เลขเดิม) — ใช้เมื่อเจ้าของสั่งเปลี่ยนเงื่อนไขของใบที่ยังไม่ได้จ่าย
    ใบที่ลูกค้ายืนยันแล้วต้องยืนยันใหม่ (เงื่อนไขเปลี่ยน) · ใบที่แจ้งชำระ/จ่ายแล้วแก้ไม่ได้ · คืนข้อความผิดพลาด ('' = สำเร็จ)"""
    if q["status"] not in REVISABLE:
        return {"claimed": "ลูกค้าแจ้งชำระใบนี้แล้ว — ตรวจยอดก่อน ไม่ควรเปลี่ยนเงื่อนไข", "paid": "ใบนี้ชำระแล้ว"}.get(q["status"], f"ใบนี้อยู่ในสถานะ {q['status']} แก้ไม่ได้")
    package = package if package in Q.PACKAGES else q["package"]
    disc = float(q["discount_pct"] if discount_pct is None else discount_pct)
    setup = bool(q["setup"]) if setup is None else bool(setup)
    cycle = cycle if cycle in Q.CYCLES else Q.cycle_of(q)
    pr = Q.price_for(package, disc, setup, cycle)
    was = f"{q['package']} {Q.CYCLES[Q.cycle_of(q)]} {q['first_total']:,}"
    db.update_quote(q["id"], package=package, price=pr["price"], discount_pct=pr["discount_pct"], setup=1 if setup else 0, cycle=cycle,
                    period_total=pr["period_total"], net=pr["net"], setup_price=pr["setup_price"], first_total=pr["first_total"],
                    status="sent" if q["status"] in ("accepted", "viewed") else q["status"], accepted_at=None, accepted_name=None,
                    stripe_session_id=None, valid_until=Q.valid_until(), vat_pct=Q.vat_rate_now())
    db.update_lead(q["lead_id"], package=package, monthly_thb=pr["net"])
    record(q["lead_id"], "note", f"ปรับ {q['number']}: {was} → {package} {Q.CYCLES[cycle]} {pr['first_total']:,} บาท"
           + (" (ลูกค้าต้องยืนยันใหม่)" if q["status"] == "accepted" else ""), who)
    return ""


def send_quote(q, who: str = "admin", revised: bool = False) -> str:
    lead = db.get_lead(q["lead_id"])
    if lead["status"] == "unsubscribed":
        return "ไม่ส่ง — ลูกค้าขอไม่รับการติดต่อ"
    url = quote_url(q)
    lang = lead_lang(lead)
    t = _tl(lang)
    dom, num = lead["domain"], q["number"]
    greet = lead["name"] or t("ทีม {dom}", dom=dom)
    intro = (t("ผมปรับใบเสนอราคา {number} สำหรับเว็บ {dom} ให้ใหม่ครับ (ลิงก์เดิม) ช่วยเปิดดูและยืนยันอีกครั้ง", number=num, dom=dom) if revised
             else t("ตามที่สนใจ ผมส่งใบเสนอราคาสำหรับเว็บ {dom} มาให้ครับ", dom=dom))
    body = (t("สวัสดีครับ คุณ{greet}", greet=greet) + f"\n\n{intro}\n\n{Q.summary_text(q, dom, lang)}\n\n"
            + t("เปิดดูรายละเอียด เงื่อนไข และยืนยันรับข้อเสนอได้ที่ {url}", url=url) + "\n" + t("มีคำถามตอบอีเมลนี้ได้เลยครับ") + outreach_footer(lead, lang))
    sent = _send_text(lead, (t("ปรับใบเสนอราคา {number} สำหรับ {dom}", number=num, dom=dom) if revised
                             else t("ใบเสนอราคา {number} สำหรับ {dom}", number=num, dom=dom)), body)
    db.update_quote(q["id"], status="sent", sent_at=db.now())
    db.update_lead(lead["id"], status="quoted", last_contacted_at=db.now())
    _cancel_pending(lead["id"], {"outreach", "lead_review"}, f"ส่งใบเสนอราคา {q['number']} แล้ว")
    record(lead["id"], "quote_sent", f"{q['number']} · " + (sent or "ไม่มีช่องทางส่งอัตโนมัติ"), who)
    if sent:
        return f"ส่งใบเสนอราคา {q['number']} แล้ว ({sent})"
    return f"สร้างใบเสนอราคา {q['number']} แล้ว แต่ไม่มีอีเมล/LINE ที่ส่งได้ — ส่งลิงก์ {url} ให้ลูกค้าเอง"


def view_quote(q) -> None:
    if q["status"] == "sent":
        db.update_quote(q["id"], status="viewed", viewed_at=db.now())
        record(q["lead_id"], "quote_viewed", q["number"], "customer")


def accept_quote(q, name: str, ip: str = "") -> str:
    """ลูกค้ายืนยันรับข้อเสนอ (ชื่อ + ติ๊กยอมรับเงื่อนไข) → รอชำระ · คืนข้อความผิดพลาด ('' = สำเร็จ)"""
    if Q.is_expired(q):
        return "ใบเสนอราคานี้หมดอายุแล้ว ติดต่อเราเพื่อขอใบใหม่"
    if q["status"] not in ("sent", "viewed"):
        return "" if q["status"] in ("accepted", "claimed", "paid") else "ใบเสนอราคานี้ใช้ไม่ได้แล้ว"
    if len((name or "").strip()) < 2:
        return "กรอกชื่อผู้ยืนยัน"
    db.update_quote(q["id"], status="accepted", accepted_at=db.now(), accepted_name=name.strip()[:120], accepted_ip=ip[:64])
    record(q["lead_id"], "quote_accepted", f"{q['number']} โดย {name.strip()[:120]}", "customer")
    dom = db.get_lead(q["lead_id"])["domain"]
    alert_admins(f"✅ {dom} ยืนยันรับข้อเสนอ {q['number']} แล้ว (โดย {name.strip()[:60]}) รอชำระ {Q.baht(Q.payable(q))} บาท — เมื่อเงินเข้าแล้วกด \"ยืนยันยอดเข้า\" ระบบจะเปิดบัญชีให้",
                 f"/admin/leads/{q['lead_id']}", "lead", subject=f"✅ {dom} ยืนยันรับข้อเสนอ {q['number']} รอชำระ {Q.baht(Q.payable(q))} บาท")
    return ""


def claim_payment(q, slip_path: str | None) -> int:
    """ลูกค้าแจ้งชำระ (แนบสลิป) → CFO เสนอ 'ยืนยันยอดเข้า' ให้เจ้าของเช็กบัญชีแล้วกดอนุมัติ → เปิดบัญชี"""
    db.update_quote(q["id"], status="claimed", claimed_at=db.now(), slip_path=slip_path)
    lead = db.get_lead(q["lead_id"])
    record(lead["id"], "payment_claimed", f"{q['number']} ยอด {Q.baht(Q.payable(q))} บาท" + (" · แนบสลิป" if slip_path else ""), "customer")
    pl = {"quote_id": q["id"], "number": q["number"], "amount": Q.payable(q), "package": q["package"], "slip": bool(slip_path)}
    proposal = (f"ยืนยันยอดเข้า {Q.baht(Q.payable(q))} บาท จาก {lead['domain']} ({q['number']} · แพ็กเกจ {q['package']})\n"
                f"เช็กบัญชีรับเงินแล้วกดอนุมัติ → ระบบเปิดบัญชี+แบรนด์ และส่งอีเมลต้อนรับให้ลูกค้าเอง" + ("" if slip_path else "\nลูกค้าไม่ได้แนบสลิป"))
    did = db.add_decision("cfo", "payment", f"ยอดเข้า {Q.baht(Q.payable(q))} บาท — {lead['domain']}", proposal, pl, lead_id=lead["id"])
    alert_admins(f"💳 {lead['domain']} แจ้งชำระ {Q.baht(Q.payable(q))} บาท ({q['number']}) — เช็กบัญชีแล้วอนุมัติข้อเสนอของ CFO",
                 "/admin/decisions", "payment", subject=f"💳 {lead['domain']} แจ้งชำระ {Q.baht(Q.payable(q))} บาท รอยืนยันยอด")
    return did


def provision_customer(q) -> dict:
    """เปิดบัญชี (ถ้ายังไม่มี) + ตั้งแผน + สร้างแบรนด์จากโดเมนลูกค้า + เปิดคอนเทนต์อัตโนมัติแบบร่างเหมือนลูกค้ารายอื่น"""
    lead = db.get_lead(q["lead_id"])
    email = (lead["email"] or "").lower().strip() or f"{lead['domain']}@geo.local"     # ไม่มีอีเมล = ที่อยู่สมมติ ให้แอดมินแก้ทีหลัง
    tenant = db.get_tenant_by_email(email)
    temp_pw, created = None, False
    if tenant:
        tid = tenant["id"]
    else:
        temp_pw = secrets.token_urlsafe(9)
        tid = db.create_tenant(email, _hash_pw(temp_pw), lead["name"] or lead["domain"], is_admin=False)
        db.set_tenant_lang(tid, lead_lang(lead))          # อีเมลถึงลูกค้าหลังเปิดบัญชีใช้ภาษาที่เขาเลือกบนเว็บ
        created = True
    db.set_plan(tid, q["package"])
    existing = [b for b in db.list_all_brands() if leads.domain_of(b["domain"]) == lead["domain"]]
    if existing:
        bid = existing[0]["id"]
        b = db.get_brand(bid)
        if not b["autopilot"]:                       # แบรนด์เดิมที่ถูกพักไว้ (เช่น เคยย้อนสถานะเพราะยังไม่จ่าย) → จ่ายจริงแล้วเปิดงานกลับ
            db.set_auto_content(bid, 3)
            db.set_autopilot(bid, True, "draft", 1)
    else:
        bid = db.create_brand(tid, lead["name"] or lead["domain"], lead["site_url"], "")
        db.set_auto_content(bid, 3)
        db.set_autopilot(bid, True, "draft", 1)
    return {"tenant_id": tid, "brand_id": bid, "email": email, "temp_password": temp_pw, "created": created, "brand_new": not existing}


@office.track("cfo", lambda q, who="admin": f"ยืนยันยอด {q['number']} และเปิดบัญชี")
def confirm_payment(q, who: str = "admin") -> str:
    """เจ้าของ/CFO ยืนยันยอดเข้า → เปิดบัญชีและแบรนด์ → ลูกค้าเป้าหมายปิดดีล → อีเมลต้อนรับ"""
    if q["status"] == "paid":
        return "ใบนี้ยืนยันไปแล้ว"
    prev = [x for x in db.list_quotes(q["lead_id"]) if x["status"] == "paid" and x["id"] != q["id"]]   # ลูกค้าเดิมจ่ายใบใหม่ = อัปเกรด/เปลี่ยนรอบบิล
    prov = provision_customer(q)
    db.update_quote(q["id"], status="paid", paid_at=db.now(), tenant_id=prov["tenant_id"], brand_id=prov["brand_id"])
    lead = db.get_lead(q["lead_id"])
    db.update_lead(lead["id"], status="won", package=q["package"], monthly_thb=q["net"])
    record(lead["id"], "paid", f"{q['number']} ยอด {Q.baht(Q.payable(q))} บาท", who)
    record(lead["id"], "provisioned", f"บัญชี #{prov['tenant_id']} ({'ใหม่' if prov['created'] else 'เดิม'}) · แบรนด์ #{prov['brand_id']}", who)
    record(lead["id"], "won", f"แพ็กเกจ {q['package']} " + (f"รายปี {Q.period_total(q):,} บาท/ปี (เทียบ {q['net']:,}/เดือน)" if Q.cycle_of(q) == "year" else f"{q['net']:,} บาท/เดือน"), who)
    base = public_base()
    lang = db.customer_lang(lead, db.get_tenant(prov["tenant_id"]))
    t = _tl(lang)
    dom, num = lead["domain"], q["number"]
    cyc = t("รายปี") if Q.cycle_of(q) == "year" else t("รายเดือน")
    note = ""
    if prev:
        from . import expansion
        stop = expansion.on_upgrade(db.get_quote(q["id"]), prev)
        note = f" · อัปเกรดจาก {prev[0]['number']}" + (f" · {stop}" if stop else "")
        if mailer.is_deliverable(prov["email"]) and mailer.config():
            try:
                mailer.send(prov["email"], t("อัปเกรด {dom} เรียบร้อยแล้ว", dom=dom),
                            mailer.wrap_html(t("อัปเกรดเรียบร้อยแล้ว"), "<p>" + t("เรายืนยันยอดชำระตามใบเสนอราคา {number} แล้ว บัญชีของ {dom} เปลี่ยนเป็นแพ็กเกจ {package} {cycle} ทันที และเราหยุดเก็บเงินแผนเดิมให้แล้ว",
                                                                                   number=num, dom=_html.escape(dom), package=_html.escape(q["package"]), cycle=cyc) + "</p>"
                                             + "<p>" + t("เข้าใช้งาน:") + f" <a href='{base}/login'>{base}/login</a></p>", "ข้อความนี้ส่งจากระบบของเจอ.AI", lang=lang),
                            text=t("ยืนยันยอด {number} แล้ว {dom} เปลี่ยนเป็น {package} ทันที และหยุดเก็บเงินแผนเดิมแล้ว", number=num, dom=dom, package=q["package"]))
            except Exception:
                pass
    elif mailer.is_deliverable(prov["email"]) and mailer.config():
        pw_line = ("<p>" + t("รหัสผ่านชั่วคราว: <code>{pw}</code> — เข้าแล้วเปลี่ยนได้ที่เมนู <b>บัญชี → เปลี่ยนรหัสผ่าน</b>", pw=_html.escape(prov["temp_password"])) + "</p>"
                   if prov["temp_password"] else "<p>" + t("ใช้บัญชีเดิมที่มีอยู่เข้าสู่ระบบได้เลย") + "</p>")
        html = mailer.wrap_html(t("บัญชีของ {dom} พร้อมแล้ว", dom=dom),
                                "<p>" + t("ขอบคุณที่ใช้บริการเจอ.AI ครับ เรายืนยันยอดชำระตามใบเสนอราคา {number} แล้ว", number=num) + "</p>"
                                + "<p>" + t("เข้าสู่ระบบ:") + f" <a href='{base}/login'>{base}/login</a><br>" + t("อีเมล:") + f" {_html.escape(prov['email'])}</p>{pw_line}"
                                + "<p>" + t("ขั้นต่อไป: เปิดแบรนด์ของคุณในระบบ → หน้า \"ติดตั้ง\" เพื่อเชื่อม /geo, llms.txt และ sitemap เข้ากับเว็บ จากนั้นระบบจะคิดคำถาม วัดผล และร่างคอนเทนต์ให้ทุกสัปดาห์ (เริ่มเป็นโหมดร่าง คุณตรวจก่อนเผยแพร่ได้)") + "</p>"
                                + "<p>" + t("สัญญาบริการ:") + f" <a href='{base}/q/{q['token']}/contract'>{base}/q/{q['token']}/contract</a></p>",
                                "ข้อความนี้ส่งจากระบบของเจอ.AI", lang=lang)
        try:
            mailer.send(prov["email"], t("บัญชีเจอ.AI ของ {dom} พร้อมแล้ว", dom=dom), html,
                        text=t("ยืนยันยอดชำระ {number} แล้ว เข้าสู่ระบบที่ {url} ด้วยอีเมล {email}", number=num, url=f"{base}/login", email=prov["email"]))
            note = f" · ส่งอีเมลต้อนรับถึง {prov['email']}"
        except Exception as e:
            note = f" · ส่งอีเมลต้อนรับไม่สำเร็จ ({type(e).__name__})"
    else:
        note = " · ไม่มีอีเมลจริงของลูกค้า — แจ้งรหัสเข้าใช้เอง (แก้อีเมลบัญชีที่หน้าผู้ดูแล)"
    try:
        from . import accounting
        accounting.first_receipt(db.get_quote(q["id"]))   # ฝ่ายบัญชี: ใบเสร็จ (หรือใบกำกับภาษีเมื่อเปิด VAT) ส่งให้ลูกค้าทันที
    except Exception as e:
        db.notify_admins(f"⚠️ ออกใบเสร็จ {q['number']} ไม่สำเร็จ ({type(e).__name__}) — ออกเองได้ที่หน้าบัญชี", "/admin/accounting", "payment")
    try:
        from . import success
        success.kickoff_quote(q)                       # ดูแลหลังการขายเริ่มทันที: แผน 30 วัน + Autopilot รอบแรก
    except Exception:
        pass
    if prev:
        return f"ยืนยันยอด {Q.baht(Q.payable(q))} บาทแล้ว · {lead['domain']} เป็น {q['package']} {'รายปี' if Q.cycle_of(q) == 'year' else 'รายเดือน'}" + note
    return (f"ยืนยันยอด {Q.baht(Q.payable(q))} บาทแล้ว · เปิดบัญชี{'ใหม่' if prov['created'] else 'เดิม'} + แบรนด์{'ใหม่' if prov['brand_new'] else 'เดิม'} "
            f"แพ็กเกจ {q['package']} · ลูกค้า {lead['domain']} ปิดดีล" + note)


# ---------- LINE: ลูกค้าทักเข้ามาเอง ----------
def _spawn(fn) -> None:
    if RUN_INLINE:
        fn()
    else:
        threading.Thread(target=fn, daemon=True).start()


def line_result_message(lead) -> str:
    rep = json.loads(lead["audit_json"] or "{}") if lead["audit_json"] else {}
    sc = leads.score_audit(rep)
    top = leads.public_summary(rep, 3)
    lines = [f"ผลตรวจ {lead['domain']}: ผ่าน {sc['n_ok']} ข้อ ต้องแก้ {sc['n_fail']} ข้อ"]
    lines += [f"• {s['label']} — {s['why']}" for s in top]
    lines.append(f"ดูผลเต็ม: {result_url(lead, 'line')}")
    lines.append("อยากให้ส่งแผนแก้พร้อมราคา พิมพ์ \"สนใจ\" ได้เลยครับ")
    return "\n".join(lines)


def line_text(user_id: str, text: str, reply_token: str | None = None) -> str:
    """ข้อความจากผู้ใช้ LINE: รหัสผูกเจ้าของ · ที่อยู่เว็บ → ตรวจฟรี · 'สนใจ' → ขอใบเสนอราคา · อื่น ๆ → วิธีใช้ · คืนชื่อกิ่งที่เดิน"""
    t = (text or "").strip()
    if t and t == line_oa.link_code():
        line_oa.set_owner(user_id)
        line_oa.reply(reply_token, ["ผูกบัญชีเจ้าของแล้ว ✓ จากนี้สรุปเช้า lead ใหม่ และข้อเสนอที่รออนุมัติจะส่งมาที่นี่"])
        return "owner_linked"
    if user_id and user_id == line_oa.owner_id():          # เจ้าของพิมพ์ = สั่งงาน CEO
        return owner_command(user_id, t, reply_token)
    url = leads.first_url(t)
    if url:
        return line_site_request(user_id, url, reply_token)
    lead = db.get_lead_by_line_user(user_id)
    if lead and any(k in t for k in ("สนใจ", "ราคา", "ใบเสนอ", "แพ็กเกจ", "คุยต่อ")):
        if not lead["consent"]:
            db.update_lead(lead["id"], consent=1, contact=lead["contact"] or "LINE")
        did = lead_replied(lead, t, via="LINE")
        promise = f"ทีม {line_oa.bot_name()} จะส่งใบเสนอราคาสำหรับ {lead['domain']} ให้ภายใน 1 วันทำการ" + (" (รอเจ้าของอนุมัติ)" if did else "")
        from . import salesbot
        if salesbot.enabled("line") and re.search(r"\?|ไหม|มั้ย|เท่าไ|อะไร|ยังไง|อย่างไร|กี่|ได้บ้าง|ต่างจาก|how|what", t, re.I):
            res = salesbot.handle("line", user_id, t, lead)               # ถามจริง → ตอบก่อน แล้วบอกว่าใบเสนอราคากำลังมา
            line_oa.reply(reply_token, [(res["answer"] + "\n\n" + promise)[:4900]])
            return "replied_answered"
        line_oa.reply(reply_token, [f"รับทราบครับ {promise}"])
        return "replied"
    from . import salesbot
    if salesbot.enabled("line") and len(t) >= 3 and not re.fullmatch(r"(สวัสดี\S*|hi|hello|ดีครับ|ดีค่ะ|👋|ok|โอเค)", t, re.I):
        res = salesbot.handle("line", user_id, t, lead)                    # คำถามทั่วไป → ตอบทันทีจากข้อเท็จจริงในระบบ · นอกกรอบส่งต่อเจ้าของ
        tail = "" if lead else f"\n\nอยากรู้ว่าเว็บคุณโผล่ใน AI ไหม พิมพ์ที่อยู่เว็บมาได้เลย {line_oa.bot_name()} ตรวจให้ฟรี"
        line_oa.reply(reply_token, [(res["answer"] + tail)[:4900]])
        return "salesbot_escalated" if res.get("escalate") else "salesbot"
    line_oa.reply(reply_token, [f"พิมพ์ที่อยู่เว็บของคุณมาได้เลย (เช่น example.co.th) {line_oa.bot_name()} จะตรวจ 16 ข้อที่ทำให้เว็บไม่โผล่ใน Google และคำตอบของ AI ให้ฟรี ใช้เวลาราวครึ่งนาที"])
    return "help"


OWNER_REPLY_WAIT = 8          # วินาที — งานเสร็จทันใช้ reply (ฟรี) ไม่ทันส่ง "กำลังทำ" แล้ว push ผลตามไป


def owner_command(user_id: str, text: str, reply_token: str | None = None) -> str:
    from . import ceo_chat
    import threading as _th
    box, done = {}, _th.Event()

    def work():
        try:
            box["r"] = ceo_chat.handle(text, "owner (LINE)")
        except Exception as e:
            box["r"] = f"CEO ทำไม่สำเร็จ: {type(e).__name__}"
        done.set()
        if box.get("late"):
            line_oa.push(user_id, [box["r"][:4900]])
    if RUN_INLINE:
        work()
    else:
        _th.Thread(target=work, daemon=False).start()
        done.wait(OWNER_REPLY_WAIT)
    if done.is_set():
        line_oa.reply(reply_token, [box["r"][:4900]])
    else:
        box["late"] = True
        line_oa.reply(reply_token, ["รับคำสั่งแล้ว กำลังทำ… เสร็จแล้วจะส่งผลตามมา"])
        if done.is_set() and not box.get("pushed"):        # เสร็จพอดีระหว่างตอบ
            line_oa.push(user_id, [box["r"][:4900]])
    return "owner_command"


def line_site_request(user_id: str, url: str, reply_token: str | None = None) -> str:
    site = leads.normalize_url(url)
    if not site:
        line_oa.reply(reply_token, ["อ่านที่อยู่เว็บไม่ออก ลองพิมพ์แบบ example.co.th"])
        return "bad_url"
    since = (datetime.datetime.now() - datetime.timedelta(hours=leads.REUSE_AUDIT_HOURS)).isoformat(timespec="seconds")
    if db.count_line_audits_since(user_id, since) >= leads.LINE_AUDITS_PER_USER_PER_DAY:
        line_oa.reply(reply_token, [f"วันนี้ตรวจครบ {leads.LINE_AUDITS_PER_USER_PER_DAY} เว็บแล้ว พรุ่งนี้ตรวจต่อได้ครับ"])
        return "limit"
    dom = leads.domain_of(site)
    recent = db.find_lead_by_domain(dom)
    if recent and recent["audit_json"] and recent["token"] and (recent["audit_at"] or "") >= since:
        if not recent["line_user_id"]:
            db.update_lead(recent["id"], line_user_id=user_id)
        record(recent["id"], "line", f"ขอผลตรวจซ้ำ (ใช้ผลเดิมใน {leads.REUSE_AUDIT_HOURS} ชม.)", "customer")
        line_oa.reply(reply_token, [line_result_message(db.get_lead(recent["id"]))])
        return "cached"
    if recent and not recent["line_user_id"]:
        db.update_lead(recent["id"], line_user_id=user_id)
        lid = recent["id"]
    elif recent:
        lid = recent["id"]
    else:
        lid = db.add_lead(site, dom, source="line")
        db.update_lead(lid, line_user_id=user_id, contact="LINE", src="line_chat")
    record(lid, "line", f"ขอตรวจ {dom}", "customer")
    line_oa.reply(reply_token, [f"รับเรื่องแล้วครับ กำลังตรวจ {dom} ใช้เวลาราวครึ่งนาที เดี๋ยวส่งผลมาที่แชตนี้"])

    def work():
        try:
            run_lead_audit(lid, site)
            line_oa.push(user_id, [line_result_message(db.get_lead(lid))])
        except Exception as e:
            line_oa.push(user_id, [f"ตรวจ {dom} ไม่สำเร็จ ({type(e).__name__}) ลองใหม่อีกครั้ง หรือเช็กว่าเว็บเปิดได้"])
    _spawn(work)
    return "started"


def handle_line_webhook(body: dict) -> list[str]:
    """รับ event ที่ตรวจลายเซ็นแล้ว · คืนรายการกิ่งที่เดิน (ไว้ log/ทดสอบ)"""
    out = []
    for ev in line_oa.parse_events(body):
        if ev["type"] == "follow":
            line_oa.reply(ev["reply_token"], [f"สวัสดีครับ นี่คือ {line_oa.bot_name()} 👋 พิมพ์ที่อยู่เว็บของคุณมาได้เลย เราจะตรวจฟรีว่าเว็บโผล่ใน Google และคำตอบของ AI หรือยัง"])
            out.append("follow")
        elif ev["type"] == "message" and ev["text"]:
            out.append(line_text(ev["user_id"], ev["text"], ev["reply_token"]))
        else:
            out.append(ev["type"] or "ignored")
    return out
