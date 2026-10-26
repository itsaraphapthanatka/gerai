"""ดูแลหลังการขาย (Customer Success) — ทำให้ลูกค้าที่จ่ายแล้วเห็นผลก่อนรอบบิลถัดไป
ลำดับต่อลูกค้า (นับจากวันจ่าย):
  วันที่ 0  เริ่มงาน: อีเมลแผน 30 วัน + ชุดติดตั้ง · สั่ง Autopilot รอบแรกทันที (ตรวจเว็บ วัด AI ร่างคอนเทนต์ชิ้นแรก)
  วันที่ 3  เช็กติดตั้ง: ตัวเชื่อม /geo · llms.txt · sitemap หรือ WordPress — ยังไม่ติดตั้ง → อีเมลขั้นตอน + เตือนเจ้าของ
  วันที่ 7  งานชิ้นแรก: มีคอนเทนต์ไหม — ไม่มี → สั่ง Autopilot อีกรอบ + เตือนเจ้าของ · มีร่างรอตรวจ → อีเมลชวนตรวจ
  ทุกเดือน วันที่ 25 (5 วันก่อนรอบบิล) รายงานผลจริง: สุขภาพเว็บ คอนเทนต์ที่ทำ การมองเห็นใน AI
  รายปี: รายงานทุกเดือนเหมือนกัน + แจ้งต่ออายุล่วงหน้า 30 วันก่อนครบปี (ยกเลิกการต่ออายุได้ก่อนวันตัดบัตร) และเตือนเจ้าของ
สัญญาณเสี่ยงหลุด (เตือนเจ้าของทาง LINE ครั้งเดียวต่อเรื่อง): ยังไม่ติดตั้งเมื่อครบ 7 วัน · ไม่มีคอนเทนต์เมื่อครบ 10 วัน · กลับเป็นแผนฟรี
ทุกจุดสัมผัสบันทึกในไทม์ไลน์ลูกค้า (lead_events cs_*) · ข้อความเป็นแม่แบบที่เขียนจากข้อมูลจริง ไม่ใช้โมเดลแต่ง"""
from __future__ import annotations
import datetime as dt
import html as _html
import json
import threading

from . import db, mailer, office

BASE_URL = "https://geo.appreview.cloud"
STEP_TH = {"cs_kickoff": "เริ่มงาน: แผน 30 วัน + ชุดติดตั้ง", "cs_installed": "ติดตั้งตัวเชื่อมแล้ว", "cs_install_reminder": "เตือนติดตั้ง",
           "cs_first_content": "งานชิ้นแรก", "cs_review_nudge": "ชวนตรวจร่างคอนเทนต์", "cs_report": "รายงานก่อนรอบบิล", "cs_risk": "เสี่ยงหลุด",
           "cs_renewal": "แจ้งต่ออายุรายปี"}
RENEWAL_NOTICE_DAYS = 30
HOOKS = {"autopilot": None, "health": None}        # เทสต์ฉีดของปลอม
RUN_INLINE = False


def _base() -> str:
    import os
    return os.getenv("PUBLIC_BASE_URL", BASE_URL).rstrip("/")


def enabled() -> bool:
    """ปิดได้ที่ตั้งค่า (cs_on) · เทสต์ชุดอื่นปิดด้วย GEO_CS=0 ไม่ให้สั่ง Autopilot จริง"""
    import os
    return os.getenv("GEO_CS", "1") != "0" and (db.get_setting("cs_on") or "1") == "1"


def set_enabled(on: bool) -> None:
    db.set_setting("cs_on", "1" if on else "0")


def _ts(s):
    try:
        return dt.datetime.fromisoformat(str(s)[:19])
    except Exception:
        return None


# ---------- ลูกค้า ----------
def customers() -> list[dict]:
    """ลูกค้าที่จ่ายแล้ว 1 แถวต่อแบรนด์ (ใบล่าสุด)"""
    out, seen = [], set()
    for q in db.list_quotes(status="paid", limit=5000):
        if not q["brand_id"] or q["brand_id"] in seen:
            continue
        brand, tenant, lead = db.get_brand(q["brand_id"]), db.get_tenant(q["tenant_id"]) if q["tenant_id"] else None, db.get_lead(q["lead_id"])
        if not (brand and tenant and lead):
            continue
        seen.add(q["brand_id"])
        out.append({"quote": q, "brand": brand, "tenant": tenant, "lead": lead, "paid_at": _ts(q["paid_at"])})
    return out


def done_keys(lead_id: int) -> dict:
    """เหตุการณ์ cs_* ที่ทำแล้ว → {kind: [note...]}"""
    out = {}
    for e in db.list_lead_events(lead_id, 500):
        if (e["kind"] or "").startswith("cs_"):
            out.setdefault(e["kind"], []).append(e["note"] or "")
    return out


def installed(brand) -> tuple[bool, str]:
    """ติดตั้งแล้วไหม: เชื่อม WordPress แล้ว หรือผลตรวจล่าสุด ข้อ connector ผ่าน"""
    if db.get_wp_connection(brand["id"]):
        return True, "เชื่อม WordPress แล้ว"
    hc = db.last_health_check(brand["id"])
    if hc and hc["report"]:
        try:
            rep = json.loads(hc["report"])
            c = next((x for x in rep.get("checks", []) if x.get("key") == "connector"), None)
            if c:
                return c.get("status") == "ok", (c.get("detail") or c.get("label") or "")[:140]
        except Exception:
            pass
    return False, "ยังไม่มีผลตรวจ"


def content_counts(brand_id: int) -> dict:
    rows = db.list_content(brand_id)
    return {"total": len(rows), "published": sum(1 for r in rows if r["status"] == "published"), "draft": sum(1 for r in rows if r["status"] == "draft")}


# ---------- ข้อความ ----------
# ทุกประโยคเป็นภาษาไทยต้นฉบับ (msgid) แปลด้วย i18n.tr ตามภาษาของลูกค้า (คำแปลอยู่ที่ app/i18n/email.json) · lang="th" = ข้อความเดิมทุกตัวอักษร
def lang_of(c: dict) -> str:
    """ภาษาของอีเมลถึงลูกค้ารายนี้: ภาษาของบัญชี (คัดลอกจากลีดตอนเปิดบัญชี / เปลี่ยนตาม ?lang= ตอนล็อกอิน)"""
    return db.customer_lang(c.get("lead"), c.get("tenant"))


def _t(c: dict):
    from . import i18n
    lang = lang_of(c)
    return lambda s, **kw: i18n.tr(s, lang, **kw)


def _send(c: dict, subject: str, body_html: str, text: str, link: str) -> str:
    t = c["tenant"]
    db.add_notification(t["id"], subject, link, "info")              # กระดิ่งในแอปเสมอ
    if not (mailer.config() and mailer.is_deliverable(t["email"])):
        return "แจ้งในแอป (ไม่มีอีเมลที่ส่งได้)"
    mailer.send(t["email"], subject, mailer.wrap_html(subject, body_html, "ข้อความนี้ส่งจากทีมดูแลลูกค้าของเจอ.AI", lang=lang_of(c)), text=text)
    return f"อีเมลถึง {t['email']} + แจ้งในแอป"


def _sign(c: dict) -> str:
    return _t(c)("ทีมดูแลลูกค้า เจอ.AI")


def kickoff_message(c: dict) -> tuple[str, str, str, str]:
    b, base, t = c["brand"], _base(), _t(c)
    dom = c['lead']['domain']
    kit, link = f"{base}/brands/{b['id']}/assets", f"{base}/brands/{b['id']}"
    subject = t("แผน 30 วันแรกของ {dom} กับเจอ.AI", dom=dom)
    steps = [(t("วันนี้"), t("ระบบตรวจเว็บ วัดว่า AI เอ่ยถึงแบรนด์คุณหรือยัง และเริ่มร่างคอนเทนต์ชิ้นแรกให้แล้ว")),
             (t("ภายใน 3 วัน"), t("ติดตั้งตัวเชื่อม /geo · llms.txt · sitemap บนเว็บของคุณ (ทำครั้งเดียว ใช้เวลาราว 15 นาที) ตามชุดติดตั้ง: {kit}", kit=kit)),
             (t("ภายใน 7 วัน"), t("คอนเทนต์ชิ้นแรกพร้อมให้ตรวจในหน้า คอนเทนต์ — ตรวจแล้วกดเผยแพร่ได้เลย")),
             (t("วันที่ 25"), t("รายงานผลจริงเดือนแรก: สุขภาพเว็บ งานที่ทำ และการมองเห็นใน AI ก่อนรอบบิลถัดไป"))]
    text = (t("สวัสดีครับ") + "\n\n" + t("ขอบคุณที่เลือกเจอ.AI ดูแล {dom} ครับ นี่คือสิ่งที่จะเกิดขึ้นใน 30 วันแรก", dom=dom) + "\n\n"
            + "\n".join(f"• {w}: {d}" for w, d in steps)
            + "\n\n" + t("เข้าแบรนด์ของคุณ:") + f" {link}\n" + t("ติดขัดตรงไหน ตอบอีเมลนี้ได้เลยครับ") + "\n\n" + _sign(c))
    html = ("<p>" + t("ขอบคุณที่เลือกเจอ.AI ดูแล {dom} ครับ นี่คือสิ่งที่จะเกิดขึ้นใน 30 วันแรก", dom="<b>" + _html.escape(dom) + "</b>") + "</p><ul>"
            + "".join(f"<li><b>{_html.escape(w)}</b> — {_html.escape(d)}</li>" for w, d in steps)
            + f"</ul><p><a href='{link}'>" + t("เข้าแบรนด์ของคุณ") + f"</a> · <a href='{kit}'>" + t("ชุดติดตั้ง") + "</a></p><p>" + t("ติดขัดตรงไหน ตอบอีเมลนี้ได้เลยครับ") + "</p>")
    return subject, html, text, f"/brands/{b['id']}"


def install_message(c: dict, detail: str) -> tuple[str, str, str, str]:
    b, base, t = c["brand"], _base(), _t(c)
    dom = c['lead']['domain']
    kit = f"{base}/brands/{b['id']}/assets"
    subject = t("ขั้นเดียวที่เหลือ: ติดตั้งตัวเชื่อมบน {dom}", dom=dom)
    once = t("ขั้นนี้ทำครั้งเดียว ถ้ายังไม่ติดตั้ง คอนเทนต์ที่เราเขียนจะยังไม่ขึ้นบนโดเมนของคุณ และ AI จะยังอ่านไม่เจอ")
    talk = t("ถ้าอยากให้เราคุยกับทีมเว็บของคุณโดยตรง ตอบอีเมลนี้พร้อมช่องทางติดต่อได้เลยครับ")
    text = (t("สวัสดีครับ") + "\n\n" + t("ระบบเช็กเว็บ {dom} แล้ว ยังไม่พบตัวเชื่อม /geo · llms.txt · sitemap สำหรับ AI ({detail})", dom=dom, detail=t(detail)) + "\n\n"
            + once + "\n\n"
            + "• " + t("ใช้ WordPress: ติดตั้งปลั๊กอินตามหน้า WordPress ในระบบ") + "\n• " + t("เว็บอื่น: ส่งชุดติดตั้งนี้ให้ทีมเว็บ {kit}", kit=kit) + "\n\n"
            + talk + "\n\n" + _sign(c))
    html = ("<p>" + t("ระบบเช็กเว็บ {dom} แล้ว ยังไม่พบตัวเชื่อม /geo · llms.txt · sitemap สำหรับ AI", dom="<b>" + _html.escape(dom) + "</b>") + "</p>"
            + "<p>" + once + "</p>"
            + "<ul><li>" + t("ใช้ WordPress: ติดตั้งปลั๊กอินตามหน้า <a href='{url}'>WordPress</a>", url=f"{base}/brands/{b['id']}/wp") + "</li><li>"
            + t("เว็บอื่น: ส่ง <a href='{kit}'>ชุดติดตั้ง</a> ให้ทีมเว็บ", kit=kit) + "</li></ul>"
            + "<p>" + talk + "</p>")
    return subject, html, text, f"/brands/{b['id']}/assets"


def review_message(c: dict, n_draft: int) -> tuple[str, str, str, str]:
    b, base, t = c["brand"], _base(), _t(c)
    dom = c['lead']['domain']
    link = f"{base}/brands/{b['id']}/content"
    subject = t("คอนเทนต์ {n} ชิ้นของ {dom} รอคุณตรวจ", n=n_draft, dom=dom)
    auto = t("ถ้าอยากให้เผยแพร่เองโดยไม่ต้องตรวจ เปลี่ยนได้ที่หน้า Autopilot")
    text = (t("สวัสดีครับ") + "\n\n" + t("ระบบร่างคอนเทนต์ที่ตอบคำถามจริงของลูกค้าให้ {dom} แล้ว {n} ชิ้น ตั้งไว้เป็นโหมดร่างเพื่อให้คุณตรวจก่อน", dom=dom, n=n_draft) + "\n\n"
            + t("ตรวจและกดเผยแพร่ได้ที่ {url}", url=link) + "\n" + auto + "\n\n" + _sign(c))
    html = ("<p>" + t("ระบบร่างคอนเทนต์ที่ตอบคำถามจริงของลูกค้าให้ {dom} แล้ว {n} ชิ้น ตั้งไว้เป็นโหมดร่างเพื่อให้คุณตรวจก่อน", dom="<b>" + _html.escape(dom) + "</b>", n=n_draft) + "</p>"
            + f"<p><a href='{link}'>" + t("ตรวจและกดเผยแพร่") + "</a> · " + auto + "</p>")
    return subject, html, text, f"/brands/{b['id']}/content"


def report_message(c: dict, month: int) -> tuple[str, str, str, str]:
    b, base, t = c["brand"], _base(), _t(c)
    dom = c['lead']['domain']
    cc = content_counts(b["id"])
    ok, det = installed(b)
    hc = db.last_health_check(b["id"])
    av = db.last_ai_visibility(b["id"])
    lines = [t("ติดตั้งตัวเชื่อม: {state}", state=t("เรียบร้อย") if ok else t("ยังไม่เรียบร้อย") + " — " + t(det)),
             t("คอนเทนต์: เผยแพร่แล้ว {a} ชิ้น · รอตรวจ {b} ชิ้น", a=cc['published'], b=cc['draft'])]
    if hc:
        lines.append(t("สุขภาพเว็บล่าสุด: ต้องแก้ {a} ข้อ · ควรดู {b} ข้อ", a=hc['n_fail'], b=hc['n_warn']))
    if av:
        try:
            lines.append(t("AI เอ่ยถึงแบรนด์: {named} จาก {asked} คำถาม · อ้างอิงเว็บคุณ {cited} ครั้ง", named=av['named'], asked=av['asked'], cited=av['cited']))
        except Exception:
            pass
    link = f"{base}/brands/{b['id']}/report"
    yearly = _yearly(c)
    subject = t("รายงานเดือนที่ {month} ของ {dom}", month=month, dom=dom) + ("" if yearly else t(" — ก่อนรอบบิลถัดไป"))
    talk = t("อยากคุยแผนเดือนหน้า ตอบอีเมลนี้ได้เลยครับ")
    text = (t("สวัสดีครับ") + "\n\n" + t("สรุปสิ่งที่เกิดขึ้นกับ {dom} ในเดือนที่ {month}", dom=dom, month=month) + "\n\n" + "\n".join(f"• {x}" for x in lines)
            + "\n\n" + t("รายงานเต็มพร้อม PDF:") + f" {link}\n" + talk + "\n\n" + _sign(c))
    html = ("<p>" + t("สรุปสิ่งที่เกิดขึ้นกับ {dom} ในเดือนที่ {month}", dom="<b>" + _html.escape(dom) + "</b>", month=month) + "</p><ul>" + "".join(f"<li>{_html.escape(x)}</li>" for x in lines)
            + f"</ul><p><a href='{link}'>" + t("รายงานเต็มพร้อม PDF") + "</a> · " + talk + "</p>")
    return subject, html, text, f"/brands/{b['id']}/report"


def _yearly(c: dict) -> bool:
    from . import quotes as Q
    return Q.cycle_of(c["quote"]) == "year"


def renewal_message(c: dict, year: int, renew_on: dt.date) -> tuple[str, str, str, str]:
    """แจ้งต่ออายุรายปีล่วงหน้า — บอกยอด วันตัด และวิธียกเลิกให้ชัด (ไม่ให้ลูกค้าโดนตัดเงินแบบไม่รู้ตัว)"""
    from . import quotes as Q
    q, dom, base, t = c["quote"], c["lead"]["domain"], _base(), _t(c)
    amt = Q.payable_period(q)
    # ไทยใช้ปี พ.ศ. · ภาษาอื่นใช้ปี ค.ศ. แบบ ISO (อ่านได้ทุกภาษา ไม่สับสนวัน/เดือน)
    when = renew_on.strftime("%d/%m/") + str(renew_on.year + 543) if lang_of(c) == "th" else renew_on.isoformat()
    vat = " " + t("(รวม VAT)") if Q.vat_pct(q) else ""
    subject = t("แจ้งล่วงหน้า: บริการรายปีของ {dom} จะต่ออายุวันที่ {when}", dom=dom, when=when)
    text = (t("สวัสดีครับ") + "\n\n" + t("บริการเจอ.AI รายปีของ {dom} (ปีที่ {year}) จะครบกำหนดและต่ออายุอัตโนมัติวันที่ {when} ยอด {amount} บาทต่อปี",
                                           dom=dom, year=year, when=when, amount=Q.baht(amt)) + vat + "\n\n"
            + t("ถ้าต้องการต่อ ไม่ต้องทำอะไร · ถ้าไม่ต้องการต่อ ตอบอีเมลนี้ก่อนวันดังกล่าว เราจะยกเลิกการต่ออายุให้ และคุณยังใช้บริการได้จนครบปีปัจจุบัน") + "\n\n"
            + t("สรุปผลทั้งปี:") + f" {base}/brands/{c['brand']['id']}/report\n\n" + _sign(c))
    html = ("<p>" + t("บริการเจอ.AI รายปีของ {dom} (ปีที่ {year}) จะต่ออายุอัตโนมัติวันที่ {when} ยอด {amount}",
                      dom="<b>" + _html.escape(dom) + "</b>", year=year, when=f"<b>{when}</b>", amount="<b>" + t("{amount} บาทต่อปี", amount=Q.baht(amt)) + "</b>") + vat + "</p>"
            "<p>" + t("ต้องการต่อ ไม่ต้องทำอะไร · ไม่ต้องการต่อ ตอบอีเมลนี้ก่อนวันดังกล่าว เราจะยกเลิกการต่ออายุให้ และคุณยังใช้บริการได้จนครบปีปัจจุบัน") + "</p>")
    return subject, html, text, f"/brands/{c['brand']['id']}/report"


# ---------- งานเบื้องหลัง ----------
def _bg(fn) -> None:
    if RUN_INLINE:
        fn()
    else:
        threading.Thread(target=fn, daemon=False).start()


def _autopilot(brand_id: int) -> None:
    fn = HOOKS["autopilot"]
    if fn is None:
        from .main import run_autopilot as fn
    try:
        fn(brand_id)
    except Exception:
        pass


def _health(brand_id: int) -> None:
    fn = HOOKS["health"]
    if fn is None:
        from .main import run_health_check
        fn = lambda bid: run_health_check(bid, notify_tenant=None)
    try:
        fn(brand_id)
    except Exception:
        pass


def _record(c: dict, kind: str, note: str) -> None:
    db.add_lead_event(c["lead"]["id"], kind, note[:900], "cs")


def _risk(c: dict, key: str, text: str, done: dict) -> bool:
    if any(n.startswith(key + ":") for n in done.get("cs_risk", [])):
        return False
    from . import pipeline
    _record(c, "cs_risk", f"{key}: {text}")
    pipeline.alert_admins(f"⚠️ ลูกค้าเสี่ยงหลุด {c['lead']['domain']}: {text}", f"/admin/leads/{c['lead']['id']}", "lead",
                          subject=f"⚠️ ลูกค้าเสี่ยงหลุด: {c['lead']['domain']}")
    return True


def kickoff(c: dict) -> str:
    s, h, t, l = kickoff_message(c)
    how = _send(c, s, h, t, l)
    _record(c, "cs_kickoff", f"แผน 30 วัน · {how} · สั่ง Autopilot รอบแรก")
    _bg(lambda: _autopilot(c["brand"]["id"]))
    return how


def kickoff_quote(q) -> None:
    """เรียกตอนยืนยันยอด — เริ่มงานทันทีไม่รอรอบรายวัน (ล้มก็ไม่กระทบการเปิดบัญชี รอบ 10:30 เก็บตก)"""
    try:
        q = db.get_quote(q["id"])
        c = next((x for x in customers() if x["quote"]["id"] == q["id"]), None)
        if c and enabled() and "cs_kickoff" not in done_keys(c["lead"]["id"]):
            kickoff(c)
    except Exception:
        pass


def step(c: dict, now: dt.datetime) -> list[str]:
    """งานที่ถึงกำหนดของลูกค้ารายนี้ (ทำแต่ละเรื่องครั้งเดียว) · คืนรายการที่ทำ"""
    out, done = [], done_keys(c["lead"]["id"])
    b, days = c["brand"], (now - c["paid_at"]).days if c["paid_at"] else 0
    if c["tenant"]["plan"] == "free":
        if _risk(c, "free", "บัญชีกลับเป็นแผนฟรี (ยกเลิกหรือจ่ายไม่ผ่าน)", done):
            out.append("risk:free")
        return out
    if "cs_kickoff" not in done:
        out.append("kickoff · " + kickoff(c))
        return out
    if days >= 3 and "cs_installed" not in done:
        _health(b["id"])
        ok, det = installed(b)
        if ok:
            _record(c, "cs_installed", det or "ตัวเชื่อมผ่าน")
            out.append("installed")
        else:
            if "cs_install_reminder" not in done:
                s, h, t, l = install_message(c, det)
                _record(c, "cs_install_reminder", f"{det} · {_send(c, s, h, t, l)}")
                out.append("install_reminder")
            if days >= 7 and _risk(c, "install", f"ครบ {days} วันแล้วยังไม่ติดตั้งตัวเชื่อม ({det})", done):
                out.append("risk:install")
    if days >= 7 and "cs_first_content" not in done:
        cc = content_counts(b["id"])
        if cc["total"]:
            _record(c, "cs_first_content", f"มีคอนเทนต์ {cc['total']} ชิ้น (เผยแพร่ {cc['published']} · ร่าง {cc['draft']})")
            out.append("first_content")
            if cc["draft"] and not cc["published"] and "cs_review_nudge" not in done:
                s, h, t, l = review_message(c, cc["draft"])
                _record(c, "cs_review_nudge", f"ร่าง {cc['draft']} ชิ้น · {_send(c, s, h, t, l)}")
                out.append("review_nudge")
        else:
            if not any(n.startswith("rerun") for n in done.get("cs_risk", [])) and days < 10:
                _record(c, "cs_risk", "rerun: ครบ 7 วันยังไม่มีคอนเทนต์ — สั่ง Autopilot อีกรอบ")
                _bg(lambda: _autopilot(b["id"]))
                out.append("autopilot_rerun")
            if days >= 10 and _risk(c, "content", f"ครบ {days} วันแล้วยังไม่มีคอนเทนต์ชิ้นแรก", done):
                out.append("risk:content")
    month = days // 30 + 1
    if days % 30 >= 25 and not any(n.startswith(f"เดือน {month}") for n in done.get("cs_report", [])):
        s, h, t, l = report_message(c, month)
        _record(c, "cs_report", f"เดือน {month} · {_send(c, s, h, t, l)}")
        out.append(f"report:{month}")
    if _yearly(c) and c["paid_at"]:
        year = days // 365 + 1
        renew_on = (c["paid_at"] + dt.timedelta(days=365 * year)).date()
        if (renew_on - now.date()).days <= RENEWAL_NOTICE_DAYS and not any(n.startswith(f"ปี {year}") for n in done.get("cs_renewal", [])):
            s, h, t, l = renewal_message(c, year, renew_on)
            _record(c, "cs_renewal", f"ปี {year} · ต่ออายุ {renew_on} · {_send(c, s, h, t, l)}")
            try:
                from . import pipeline
                pipeline.alert_admins(f"📅 {c['lead']['domain']} รายปีจะต่ออายุ {renew_on} — ส่งอีเมลแจ้งลูกค้าแล้ว ควรโทรคุยผลทั้งปีก่อนวันตัด")
            except Exception:
                pass
            out.append(f"renewal:{year}")
    return out


@office.track("cs", "ดูแลลูกค้าที่จ่ายแล้ว: ติดตั้ง · งานชิ้นแรก · รายงาน")
def run(now: dt.datetime | None = None) -> dict:
    if not enabled():
        res = {"skipped": "ปิดอยู่", "at": db.now()}
        db.set_setting("cs_last", json.dumps(res, ensure_ascii=False))
        return res
    now = now or dt.datetime.now()
    res = {"at": db.now(), "customers": 0, "actions": {}, "errors": 0}
    for c in customers():
        res["customers"] += 1
        try:
            acts = step(c, now)
            if acts:
                res["actions"][c["lead"]["domain"]] = acts
        except Exception as e:
            res["errors"] += 1
            res["actions"][c["lead"]["domain"]] = [f"error {type(e).__name__}"]
    db.set_setting("cs_last", json.dumps(res, ensure_ascii=False))
    return res


def status(lead) -> dict | None:
    """สถานะสำหรับหน้าลูกค้าเป้าหมาย (เฉพาะรายที่จ่ายแล้ว)"""
    c = next((x for x in customers() if x["lead"]["id"] == lead["id"]), None)
    if not c:
        return None
    done = done_keys(lead["id"])
    ok, det = installed(c["brand"])
    days = (dt.datetime.now() - c["paid_at"]).days if c["paid_at"] else 0
    return {"days": days, "brand": c["brand"], "installed": ok, "install_detail": det, "content": content_counts(c["brand"]["id"]),
            "done": {k: STEP_TH.get(k, k) for k in done}, "risks": done.get("cs_risk", []), "plan": c["tenant"]["plan"]}


def status_text() -> str:
    try:
        r = json.loads(db.get_setting("cs_last") or "null")
    except Exception:
        r = None
    if not r:
        return ""
    if r.get("skipped"):
        return f"ดูแลลูกค้า: {r['skipped']}"
    acts = " · ".join(f"{d}: {', '.join(a)}" for d, a in r.get("actions", {}).items())
    return f"ดูแลลูกค้า: ลูกค้า {r['customers']} ราย" + (f" · {acts}" if acts else " · ไม่มีงานถึงกำหนด")
