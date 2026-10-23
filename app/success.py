"""ดูแลหลังการขาย (Customer Success) — ทำให้ลูกค้าที่จ่ายแล้วเห็นผลก่อนรอบบิลถัดไป
ลำดับต่อลูกค้า (นับจากวันจ่าย):
  วันที่ 0  เริ่มงาน: อีเมลแผน 30 วัน + ชุดติดตั้ง · สั่ง Autopilot รอบแรกทันที (ตรวจเว็บ วัด AI ร่างคอนเทนต์ชิ้นแรก)
  วันที่ 3  เช็กติดตั้ง: ตัวเชื่อม /geo · llms.txt · sitemap หรือ WordPress — ยังไม่ติดตั้ง → อีเมลขั้นตอน + เตือนเจ้าของ
  วันที่ 7  งานชิ้นแรก: มีคอนเทนต์ไหม — ไม่มี → สั่ง Autopilot อีกรอบ + เตือนเจ้าของ · มีร่างรอตรวจ → อีเมลชวนตรวจ
  ทุกเดือน วันที่ 25 (5 วันก่อนรอบบิล) รายงานผลจริง: สุขภาพเว็บ คอนเทนต์ที่ทำ การมองเห็นใน AI
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
           "cs_first_content": "งานชิ้นแรก", "cs_review_nudge": "ชวนตรวจร่างคอนเทนต์", "cs_report": "รายงานก่อนรอบบิล", "cs_risk": "เสี่ยงหลุด"}
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
def _send(c: dict, subject: str, body_html: str, text: str, link: str) -> str:
    t = c["tenant"]
    db.add_notification(t["id"], subject, link, "info")              # กระดิ่งในแอปเสมอ
    if not (mailer.config() and mailer.is_deliverable(t["email"])):
        return "แจ้งในแอป (ไม่มีอีเมลที่ส่งได้)"
    mailer.send(t["email"], subject, mailer.wrap_html(subject, body_html, "ข้อความนี้ส่งจากทีมดูแลลูกค้าของเจอ.AI"), text=text)
    return f"อีเมลถึง {t['email']} + แจ้งในแอป"


def kickoff_message(c: dict) -> tuple[str, str, str, str]:
    b, base = c["brand"], _base()
    kit, link = f"{base}/brands/{b['id']}/assets", f"{base}/brands/{b['id']}"
    subject = f"แผน 30 วันแรกของ {c['lead']['domain']} กับเจอ.AI"
    steps = [("วันนี้", "ระบบตรวจเว็บ วัดว่า AI เอ่ยถึงแบรนด์คุณหรือยัง และเริ่มร่างคอนเทนต์ชิ้นแรกให้แล้ว"),
             ("ภายใน 3 วัน", f"ติดตั้งตัวเชื่อม /geo · llms.txt · sitemap บนเว็บของคุณ (ทำครั้งเดียว ใช้เวลาราว 15 นาที) ตามชุดติดตั้ง: {kit}"),
             ("ภายใน 7 วัน", "คอนเทนต์ชิ้นแรกพร้อมให้ตรวจในหน้า คอนเทนต์ — ตรวจแล้วกดเผยแพร่ได้เลย"),
             ("วันที่ 25", "รายงานผลจริงเดือนแรก: สุขภาพเว็บ งานที่ทำ และการมองเห็นใน AI ก่อนรอบบิลถัดไป")]
    text = (f"สวัสดีครับ\n\nขอบคุณที่เลือกเจอ.AI ดูแล {c['lead']['domain']} ครับ นี่คือสิ่งที่จะเกิดขึ้นใน 30 วันแรก\n\n"
            + "\n".join(f"• {w}: {d}" for w, d in steps)
            + f"\n\nเข้าแบรนด์ของคุณ: {link}\nติดขัดตรงไหน ตอบอีเมลนี้ได้เลยครับ\n\nทีมดูแลลูกค้า เจอ.AI")
    html = ("<p>ขอบคุณที่เลือกเจอ.AI ดูแล <b>" + _html.escape(c['lead']['domain']) + "</b> ครับ นี่คือสิ่งที่จะเกิดขึ้นใน 30 วันแรก</p><ul>"
            + "".join(f"<li><b>{_html.escape(w)}</b> — {_html.escape(d)}</li>" for w, d in steps)
            + f"</ul><p><a href='{link}'>เข้าแบรนด์ของคุณ</a> · <a href='{kit}'>ชุดติดตั้ง</a></p><p>ติดขัดตรงไหน ตอบอีเมลนี้ได้เลยครับ</p>")
    return subject, html, text, f"/brands/{b['id']}"


def install_message(c: dict, detail: str) -> tuple[str, str, str, str]:
    b, base = c["brand"], _base()
    kit = f"{base}/brands/{b['id']}/assets"
    subject = f"ขั้นเดียวที่เหลือ: ติดตั้งตัวเชื่อมบน {c['lead']['domain']}"
    text = (f"สวัสดีครับ\n\nระบบเช็กเว็บ {c['lead']['domain']} แล้ว ยังไม่พบตัวเชื่อม /geo · llms.txt · sitemap สำหรับ AI ({detail})\n\n"
            "ขั้นนี้ทำครั้งเดียว ถ้ายังไม่ติดตั้ง คอนเทนต์ที่เราเขียนจะยังไม่ขึ้นบนโดเมนของคุณ และ AI จะยังอ่านไม่เจอ\n\n"
            f"• ใช้ WordPress: ติดตั้งปลั๊กอินตามหน้า WordPress ในระบบ\n• เว็บอื่น: ส่งชุดติดตั้งนี้ให้ทีมเว็บ {kit}\n\n"
            "ถ้าอยากให้เราคุยกับทีมเว็บของคุณโดยตรง ตอบอีเมลนี้พร้อมช่องทางติดต่อได้เลยครับ\n\nทีมดูแลลูกค้า เจอ.AI")
    html = (f"<p>ระบบเช็กเว็บ <b>{_html.escape(c['lead']['domain'])}</b> แล้ว ยังไม่พบตัวเชื่อม /geo · llms.txt · sitemap สำหรับ AI</p>"
            "<p>ขั้นนี้ทำครั้งเดียว ถ้ายังไม่ติดตั้ง คอนเทนต์ที่เราเขียนจะยังไม่ขึ้นบนโดเมนของคุณ และ AI จะยังอ่านไม่เจอ</p>"
            f"<ul><li>ใช้ WordPress: ติดตั้งปลั๊กอินตามหน้า <a href='{base}/brands/{b['id']}/wp'>WordPress</a></li><li>เว็บอื่น: ส่ง <a href='{kit}'>ชุดติดตั้ง</a> ให้ทีมเว็บ</li></ul>"
            "<p>ถ้าอยากให้เราคุยกับทีมเว็บของคุณโดยตรง ตอบอีเมลนี้พร้อมช่องทางติดต่อได้เลยครับ</p>")
    return subject, html, text, f"/brands/{b['id']}/assets"


def review_message(c: dict, n_draft: int) -> tuple[str, str, str, str]:
    b, base = c["brand"], _base()
    link = f"{base}/brands/{b['id']}/content"
    subject = f"คอนเทนต์ {n_draft} ชิ้นของ {c['lead']['domain']} รอคุณตรวจ"
    text = (f"สวัสดีครับ\n\nระบบร่างคอนเทนต์ที่ตอบคำถามจริงของลูกค้าให้ {c['lead']['domain']} แล้ว {n_draft} ชิ้น ตั้งไว้เป็นโหมดร่างเพื่อให้คุณตรวจก่อน\n\n"
            f"ตรวจและกดเผยแพร่ได้ที่ {link}\nถ้าอยากให้เผยแพร่เองโดยไม่ต้องตรวจ เปลี่ยนได้ที่หน้า Autopilot\n\nทีมดูแลลูกค้า เจอ.AI")
    html = (f"<p>ระบบร่างคอนเทนต์ที่ตอบคำถามจริงของลูกค้าให้ <b>{_html.escape(c['lead']['domain'])}</b> แล้ว {n_draft} ชิ้น ตั้งไว้เป็นโหมดร่างเพื่อให้คุณตรวจก่อน</p>"
            f"<p><a href='{link}'>ตรวจและกดเผยแพร่</a> · ถ้าอยากให้เผยแพร่เองโดยไม่ต้องตรวจ เปลี่ยนได้ที่หน้า Autopilot</p>")
    return subject, html, text, f"/brands/{b['id']}/content"


def report_message(c: dict, month: int) -> tuple[str, str, str, str]:
    b, base = c["brand"], _base()
    cc = content_counts(b["id"])
    ok, det = installed(b)
    hc = db.last_health_check(b["id"])
    av = db.last_ai_visibility(b["id"])
    lines = [f"ติดตั้งตัวเชื่อม: {'เรียบร้อย' if ok else 'ยังไม่เรียบร้อย — ' + det}",
             f"คอนเทนต์: เผยแพร่แล้ว {cc['published']} ชิ้น · รอตรวจ {cc['draft']} ชิ้น"]
    if hc:
        lines.append(f"สุขภาพเว็บล่าสุด: ต้องแก้ {hc['n_fail']} ข้อ · ควรดู {hc['n_warn']} ข้อ")
    if av:
        try:
            lines.append(f"AI เอ่ยถึงแบรนด์: {av['named']} จาก {av['asked']} คำถาม · อ้างอิงเว็บคุณ {av['cited']} ครั้ง")
        except Exception:
            pass
    link = f"{base}/brands/{b['id']}/report"
    subject = f"รายงานเดือนที่ {month} ของ {c['lead']['domain']} — ก่อนรอบบิลถัดไป"
    text = (f"สวัสดีครับ\n\nสรุปสิ่งที่เกิดขึ้นกับ {c['lead']['domain']} ในเดือนที่ {month}\n\n" + "\n".join(f"• {x}" for x in lines)
            + f"\n\nรายงานเต็มพร้อม PDF: {link}\nอยากคุยแผนเดือนหน้า ตอบอีเมลนี้ได้เลยครับ\n\nทีมดูแลลูกค้า เจอ.AI")
    html = (f"<p>สรุปสิ่งที่เกิดขึ้นกับ <b>{_html.escape(c['lead']['domain'])}</b> ในเดือนที่ {month}</p><ul>" + "".join(f"<li>{_html.escape(x)}</li>" for x in lines)
            + f"</ul><p><a href='{link}'>รายงานเต็มพร้อม PDF</a> · อยากคุยแผนเดือนหน้า ตอบอีเมลนี้ได้เลยครับ</p>")
    return subject, html, text, f"/brands/{b['id']}/report"


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
