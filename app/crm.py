"""CRM งานขายของเจ้าของ (โหมดเอเจนซี: เอเจนต์หา/ตรวจ/ร่าง — เจ้าของคุยและปิดเอง)
หนึ่งลีด = หนึ่งดีล (บริษัท/เว็บ) · ขั้นดีลบนกระดาน: ใหม่ → ทักแล้ว → อยากคุย → คุยแล้ว → ส่งข้อเสนอ → ปิดได้ (ไม่สนใจ / ขอไม่รับ อยู่นอกสาย)
· มูลค่าดีล/เดือน: ที่เจ้าของใส่เอง → ใบเสนอราคาที่ยังเปิดล่าสุด → ราคาตาราง Pro
· งานถัดไป + เวลา: เก็บเป็นเวลาเครื่องแบบไม่มีโซน (รูปเดียวกับ db.now() — คอนเทนเนอร์เป็น UTC) แสดงเป็นเวลาไทยเสมอ
· เตือนเจ้าของ (run_monitors.py --crm ทุก 15 นาที): ถึงกำหนดภายใน 15 นาทีหรือเลยกำหนด → กระดิ่ง + LINE (อีเมลเฉพาะนัดประชุม) ครั้งเดียวต่อนัด
· บันทึกกิจกรรม (โทร/ประชุม/LINE/อีเมล/โน้ต) ลงไทม์ไลน์ lead_events · ผู้ติดต่อหลายคนต่อบริษัท (lead_contacts)
· คำขอนัดคุยจากฟอร์มหน้าเว็บ (/api/contact) ขึ้นกล่องบนกระดานจนกว่าเจ้าของจะผูกกับดีล
หน้าแอดมินเป็นภาษาไทยอย่างเดียว"""
from __future__ import annotations
import datetime as dt
import json
import re
from zoneinfo import ZoneInfo

from . import db, leads as L, quotes as Q

BKK = ZoneInfo("Asia/Bangkok")
BOARD = [("new", "ใหม่"), ("contacted", "ทักแล้ว"), ("call", "อยากคุย"), ("met", "คุยแล้ว"), ("quoted", "ส่งข้อเสนอ"), ("won", "ปิดได้")]
BOARD_LABEL = dict(BOARD)
COLUMN_OF = {"replied": "contacted"}          # ตอบกลับ (ยังไม่ขอคุย) อยู่คอลัมน์ทักแล้ว พร้อมป้าย 💬 และขึ้นบนสุด
OUTSIDE = [("lost", "ไม่สนใจ"), ("unsubscribed", "ขอไม่รับการติดต่อ")]
ACTIVE = {"replied", "call", "met", "quoted"}  # ขั้นที่เจ้าของต้องมีงานถัดไปเสมอ (ไม่มี = ติดธงบนกระดาน)
WON_DAYS = 30                                  # คอลัมน์ปิดได้แสดงเฉพาะดีลที่ปิดในช่วงนี้ (ลูกค้าทั้งหมดอยู่หน้าลูกค้า)
COLUMN_LIMIT = 60
ALERT_WINDOW_MIN = 15
ACT_KINDS = {"call": "โทร", "meeting": "ประชุม", "line": "LINE", "email_out": "อีเมล", "note": "โน้ต"}
NEXT_KINDS = {"call": "โทร", "meeting": "ประชุม", "line": "LINE", "email": "อีเมล", "other": "อื่น ๆ"}
PRESETS = [("today14", "วันนี้ 14:00"), ("tomorrow10", "พรุ่งนี้ 10:00"), ("plus3", "+3 วัน"), ("custom", "เลือกเอง")]
REQUEST_NEXT = "โทรนัด"
REQUEST_DUE_HOURS = 2
TH_DAY = ["จ.", "อ.", "พ.", "พฤ.", "ศ.", "ส.", "อา."]
TH_MON = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


# ---------- เวลา ----------
def _ts(s) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(s)[:19])
    except Exception:
        return None


def now_local() -> dt.datetime:
    return dt.datetime.now().replace(microsecond=0)


def local_str(aware: dt.datetime) -> str:
    """เวลาที่มีโซน → เวลาเครื่องแบบไม่มีโซน (รูปเดียวกับ db.now()) ไว้เก็บและเทียบใน SQL"""
    return aware.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")


def to_bkk(s) -> dt.datetime | None:
    t = _ts(s)
    return t.astimezone(BKK) if t else None          # naive = เวลาเครื่อง


def fmt_ts(s, now: dt.datetime | None = None) -> str:
    """เวลาที่เก็บไว้ → เวลาไทยอ่านง่าย: วันนี้ 14:00 · พรุ่งนี้ 10:00 · เมื่อวาน 09:30 · ศ. 30 ต.ค. 10:00"""
    t = to_bkk(s)
    if not t:
        return ""
    n = (now or now_local()).astimezone(BKK)
    d = (t.date() - n.date()).days
    hm = t.strftime("%H:%M")
    if d == 0:
        return f"วันนี้ {hm}"
    if d == 1:
        return f"พรุ่งนี้ {hm}"
    if d == -1:
        return f"เมื่อวาน {hm}"
    y = f" {t.year + 543}" if t.year != n.year else ""
    return f"{TH_DAY[t.weekday()]} {t.day} {TH_MON[t.month - 1]}{y} {hm}"


def fmt_short(s) -> str:
    """ไทม์ไลน์: 27/10 15:42 (เวลาไทย)"""
    t = to_bkk(s)
    return t.strftime("%d/%m %H:%M") if t else ""


def age(s, now: dt.datetime | None = None) -> str:
    t = _ts(s)
    if not t:
        return ""
    sec = ((now or now_local()) - t).total_seconds()
    if sec < 3600:
        return "เมื่อสักครู่" if sec < 120 else f"{int(sec // 60)} นาทีก่อน"
    if sec < 86400:
        return f"{int(sec // 3600)} ชม.ก่อน"
    return f"{int(sec // 86400)} วันก่อน"


def preset_at(preset: str, custom: str = "", now: dt.datetime | None = None) -> str | None:
    """ปุ่มลัดเวลา (ตามเวลาไทย) → เวลาเครื่องสำหรับเก็บ · custom = ค่าจาก <input type=datetime-local> เป็นเวลาไทย · ไม่ถูกต้อง = None"""
    n = (now or now_local()).astimezone(BKK)
    if preset == "today14":
        t = n.replace(hour=14, minute=0, second=0, microsecond=0)
    elif preset == "tomorrow10":
        t = (n + dt.timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    elif preset == "plus3":
        t = (n + dt.timedelta(days=3)).replace(hour=10, minute=0, second=0, microsecond=0)
    elif preset == "in2h":
        t = n + dt.timedelta(hours=REQUEST_DUE_HOURS)
    else:
        try:
            t = dt.datetime.fromisoformat((custom or "").strip()[:16]).replace(tzinfo=BKK)
        except Exception:
            return None
    return local_str(t)


def bkk_input(s) -> str:
    """ค่าเริ่มต้นของ <input type=datetime-local> (เวลาไทย)"""
    t = to_bkk(s)
    return t.strftime("%Y-%m-%dT%H:%M") if t else ""


# ---------- มูลค่าดีล ----------
def pro_price() -> int:
    from . import execs
    try:
        return int(execs._plan("pro")["price"])
    except Exception:
        from . import billing
        return int(billing.PLANS["pro"]["price"])


def deal_value(lead, qs=None) -> dict:
    """{"value": บาท/เดือน, "src": manual | won | quote | list}"""
    if lead["deal_value"] is not None:
        return {"value": int(lead["deal_value"]), "src": "manual"}
    qs = list(qs) if qs is not None else list(db.list_quotes(lead["id"]))
    if lead["status"] == "won":
        paid = next((q for q in sorted(qs, key=lambda q: -q["id"]) if q["status"] == "paid"), None)
        if lead["monthly_thb"] or paid:
            return {"value": int(lead["monthly_thb"] or paid["net"]), "src": "won"}
    oq = next((q for q in sorted(qs, key=lambda q: -q["id"]) if q["status"] in Q.OPEN), None)
    if oq:
        return {"value": int(oq["net"]), "src": "quote"}
    return {"value": pro_price(), "src": "list"}


VALUE_SRC = {"manual": "ตั้งเอง", "won": "ยอดที่จ่าย", "quote": "ตามใบเสนอราคา", "list": "ราคา Pro (ประมาณ)"}


def parse_money(s) -> int | None:
    d = re.sub(r"[^\d.]", "", str(s or ""))
    if not d:
        return None
    try:
        return max(0, int(round(float(d))))
    except ValueError:
        return None


def set_value(lead_id: int, value, who: str = "owner") -> int | None:
    """ใส่มูลค่า/เดือนเอง · ว่าง = กลับไปใช้ค่าตั้งต้น (ใบเสนอราคา/ราคา Pro)"""
    v = parse_money(value)
    db.update_lead(lead_id, deal_value=v)
    db.add_lead_event(lead_id, "deal_value", f"{v:,} บาท/เดือน" if v is not None else "กลับไปใช้ค่าตั้งต้น", who)
    return v


# ---------- ขั้นดีล ----------
def advance(lead_id: int, target: str, who: str = "owner") -> bool:
    """เลื่อนไปขั้น target เฉพาะเมื่อยังอยู่ขั้นก่อนหน้า (หรือไม่สนใจ) · ขอไม่รับการติดต่อ/ขั้นที่ไกลกว่าแล้ว = คงไว้"""
    lead = db.get_lead(lead_id)
    if not lead:
        return False
    cur = lead["status"]
    if cur == "unsubscribed" or cur == target:
        return False
    if cur in L.PIPELINE and L.PIPELINE.index(cur) >= L.PIPELINE.index(target):
        return False
    db.update_lead(lead_id, status=target)
    db.add_lead_event(lead_id, "status", f"{L.STATUS_LABEL.get(cur, cur)} → {L.STATUS_LABEL[target]}", who)
    return True


def set_stage(lead, status: str, who: str = "owner") -> bool:
    """เจ้าของย้ายขั้นเอง (ลากบนกระดาน / เลือกในการ์ด) — แบบเดียวกับตัวเลือกสถานะในหน้าลูกค้าเป้าหมาย"""
    if status not in L.STATUS_LABEL or status == lead["status"]:
        return False
    if status == "unsubscribed":
        from . import pipeline
        pipeline.unsubscribe(lead, who)
        return True
    db.update_lead(lead["id"], status=status)
    db.add_lead_event(lead["id"], "status", f"{L.STATUS_LABEL[lead['status']]} → {L.STATUS_LABEL[status]}", who)
    return True


# ---------- งานถัดไป ----------
def set_next(lead_id: int, action: str, at: str | None, kind: str = "call", who: str = "owner") -> None:
    """ตั้ง/เปลี่ยนงานถัดไป (ล้างสถานะ 'เตือนแล้ว' ให้เตือนใหม่ตามเวลาใหม่) · action ว่าง = ล้างงาน"""
    action = (action or "").strip()[:200]
    if not action:
        db.update_lead(lead_id, next_action=None, next_at=None, next_kind=None, next_alerted_at=None)
        return
    kind = kind if kind in NEXT_KINDS else "call"
    db.update_lead(lead_id, next_action=action, next_at=at, next_kind=kind, next_alerted_at=None)
    db.add_lead_event(lead_id, "next_action", action + (f" · {fmt_ts(at)}" if at else " · ยังไม่ตั้งเวลา"), who)


def is_overdue(lead, now: dt.datetime | None = None) -> bool:
    t = _ts(lead["next_at"]) if lead["next_action"] else None
    return bool(t and t < (now or now_local()))


# ---------- กิจกรรม ----------
def log_activity(lead, kind: str, outcome: str = "", reached: bool = True, who: str = "owner") -> str:
    """บันทึกโทร/ประชุม/LINE/อีเมล/โน้ต ลงไทม์ไลน์ · คุยได้จริง (โทรติด/ประชุม) → ขั้นคุยแล้ว · ทักเอง (LINE/อีเมล) ลีดใหม่ → ทักแล้ว"""
    if kind not in ACT_KINDS:
        raise ValueError("ชนิดกิจกรรมไม่ถูกต้อง")
    outcome = (outcome or "").strip()[:500]
    lid = lead["id"]
    if kind == "call":
        from . import closer
        d = closer._data(lead)
        d.setdefault("calls", []).append({"at": db.now(), "outcome": "think" if reached else "no_answer", "note": outcome[:300]})
        closer._save(lid, d)                                  # รายการโทร (closer) นับรอบโทรถัดไปจากครั้งนี้ด้วย
        note = ("คุยแล้ว" if reached else "ติดต่อไม่ได้") + (f" · {outcome}" if outcome else "")
    else:
        note = outcome or ACT_KINDS[kind]
    db.add_lead_event(lid, kind, note, who)
    moved = ""
    if (kind == "call" and reached) or kind == "meeting":
        if advance(lid, "met", who):
            moved = " · ย้ายไปขั้นคุยแล้ว"
    elif kind in ("line", "email_out") and lead["status"] == "new":
        db.update_lead(lid, status="contacted", last_contacted_at=db.now())
        db.add_lead_event(lid, "status", f"{L.STATUS_LABEL['new']} → {L.STATUS_LABEL['contacted']}", who)
        moved = " · ย้ายไปขั้นทักแล้ว"
    elif kind in ("line", "email_out"):
        db.update_lead(lid, last_contacted_at=db.now())
    return f"บันทึก{ACT_KINDS[kind]}แล้ว{moved}"


# ---------- ผู้ติดต่อ ----------
EMAIL_RE = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
LINE_RE = re.compile(r"(?:line|ไลน์)\s*(?:id)?\s*[:：]?\s*(@?[\w.\-]{3,40})", re.I)


def split_contact(s: str) -> dict:
    """ข้อความช่องทางติดต่ออิสระ ('LINE: @shop · 08x' / อีเมล / ลิงก์ line.me) → ช่องของผู้ติดต่อ"""
    from . import closer
    s = (s or "").strip()
    out = {}
    m = EMAIL_RE.search(s)
    if m:
        out["email"] = m.group(0).lower()
    ph = closer.phones_in(s)
    if ph:
        out["phone"] = closer.fmt_phone(ph[0])
    if "line.me" in s.lower():
        out["line_id"] = next((t for t in s.split() if "line.me" in t.lower()), s)[:120]
    else:
        m = LINE_RE.search(s)
        if m:
            out["line_id"] = m.group(1)
        elif re.fullmatch(r"@[\w.\-]{3,40}", s):
            out["line_id"] = s
    if not out and s:
        out["note"] = s[:300]
    return out


def contacts(lead) -> list:
    """ผู้ติดต่อของบริษัทนี้ · ยังไม่มีเลยแต่ลีดมีชื่อ/อีเมล/ช่องทาง → สร้างผู้ติดต่อหลักจากข้อมูลลีดให้ครั้งเดียว"""
    rows = db.list_lead_contacts(lead["id"])
    if rows or not (lead["name"] or lead["email"] or lead["contact"]):
        return [dict(r) for r in rows]
    f = split_contact(lead["contact"] or "")
    if not f.get("phone"):
        from . import closer
        p = closer._data(lead).get("phone")
        if p:
            f["phone"] = closer.fmt_phone(p)
    db.add_lead_contact(lead["id"], name=lead["name"] or "", email=lead["email"] or f.get("email", ""), phone=f.get("phone", ""),
                        line_id=f.get("line_id", ""), note=f.get("note", ""), role="ผู้ติดต่อหลัก")
    return [dict(r) for r in db.list_lead_contacts(lead["id"])]


# ---------- คำขอนัดคุยจากหน้าเว็บ ----------
def find_lead(text: str):
    """ช่องค้นหาดีลเดิม: '#12' · โดเมน/URL · ชื่อ/อีเมลที่ตรงตัว"""
    t = (text or "").strip()
    if not t:
        return None
    m = re.fullmatch(r"#?(\d{1,9})", t)
    if m:
        return db.get_lead(int(m.group(1)))
    url = L.normalize_url(t.split()[0])
    if url:
        lead = db.find_lead_by_domain(L.domain_of(url))
        if lead:
            return lead
    tl = t.lower()
    return next((l for l in db.list_leads(None, 5000) if tl in ((l["name"] or "").lower(), (l["email"] or "").lower())), None)


def link_request(cid: int, lead_text: str = "", new_domain: str = "", who: str = "owner") -> tuple[int | None, str]:
    """ผูกคำขอนัดคุยกับดีล: ดีลเดิม (ค้นหา) หรือดีลใหม่จากเว็บที่เจ้าของพิมพ์ (ตรวจเว็บแบบลีดที่เพิ่มเอง)
    → ผู้ติดต่อของบริษัท + กิจกรรม + ขั้นอยากคุย + งานถัดไป 'โทรนัด' ภายใน 2 ชั่วโมง → คำขอนี้ปิด"""
    from . import pipeline
    c = db.get_contact(cid)
    if not c:
        return None, "ไม่พบคำขอนี้"
    if c["status"] != "new":
        return c["lead_id"], "คำขอนี้จัดการไปแล้ว"
    created = False
    if new_domain.strip():
        url = L.normalize_url(new_domain)
        if not url:
            return None, "อ่านที่อยู่เว็บไม่ออก ลองพิมพ์แบบ example.co.th"
        lead = db.find_lead_by_domain(L.domain_of(url))
        if not lead:
            lid = db.add_lead(url, L.domain_of(url), source="manual", name=c["name"])
            lead, created = db.get_lead(lid), True
    else:
        lead = find_lead(lead_text)
        if not lead:
            return None, "ไม่พบดีลที่ค้นหา — พิมพ์โดเมนให้ตรง หรือสร้างดีลใหม่"
    lid = lead["id"]
    if lead["status"] == "unsubscribed":
        return None, f"{lead['domain']} ขอไม่รับการติดต่อไว้ — ไม่ผูกคำขอ (ติดต่อกลับเองถ้าลูกค้าเปลี่ยนใจ)"
    f = split_contact(c["contact"] or "")
    db.add_lead_contact(lid, name=c["name"] or "", role="ขอนัดคุยจากหน้าเว็บ", email=f.get("email", ""), phone=f.get("phone", ""),
                        line_id=f.get("line_id", ""), note=" · ".join(x for x in [f.get("note", ""), (c["message"] or "")[:200]] if x))
    fill = {}
    if not lead["name"] and c["name"]:
        fill["name"] = c["name"]
    if not lead["email"] and f.get("email"):
        fill.update(email=f["email"], email_src="form")
    if not lead["contact"] and c["contact"]:
        fill["contact"] = c["contact"][:160]
    if fill or not lead["consent"]:
        db.update_lead(lid, consent=1, **fill)                   # กรอกฟอร์มขอให้ติดต่อกลับเอง = ยินยอมให้ติดต่อ
    plan = (c["plan"] or "").strip()
    db.add_lead_event(lid, "wants_call", f"ขอนัดคุยผ่านฟอร์มหน้าเว็บ: {c['name']}" + (f" · สนใจ {plan}" if plan else "")
                      + (f" · {(c['message'] or '')[:160]}" if c["message"] else ""), "customer")
    advance(lid, "call", who)                                     # ขั้นที่ไกลกว่า (คุยแล้ว/ส่งข้อเสนอ) คงไว้
    set_next(lid, REQUEST_NEXT, preset_at("in2h"), "call", who)
    db.link_contact(cid, lid, "handled")
    if created:
        def work():
            try:
                pipeline.run_lead_audit(lid, lead["site_url"])
                from . import deep_audit
                deep_audit.start(lid, ai=True)                    # ขอนัดคุยเอง = สนใจจริง → วัด AI ให้ไว้คุยในสาย
            except Exception:
                pass
        pipeline._spawn(work)
    return lid, (f"สร้างดีล {lead['domain']} แล้ว (กำลังตรวจเว็บ)" if created else f"ผูกกับ {lead['domain']} แล้ว") + f" · งานถัดไป: {REQUEST_NEXT} ภายใน {REQUEST_DUE_HOURS} ชม."


# ---------- กระดานดีล ----------
def _maps():
    """ข้อมูลรวมต่อลีดด้วย SQL ไม่กี่ครั้ง (กระดานโหลดลีดทีละหลายร้อย)"""
    last, wc, called, won_at = {}, {}, {}, {}
    with db.get_conn() as c:
        for r in c.execute("SELECT lead_id, kind, MAX(created_at) AS last_ts FROM lead_events GROUP BY lead_id, kind").fetchall():
            lid, k, at = r["lead_id"], r["kind"], r["last_ts"] or ""
            if k not in ("next_action", "deal_value", "status") and at > last.get(lid, ("", ""))[0]:
                last[lid] = (at, k)
            if k == "wants_call":
                wc[lid] = at
            elif k in ("call", "meeting"):
                called[lid] = max(called.get(lid, ""), at)
            elif k == "won":
                won_at[lid] = at
        qs = {}
        for r in c.execute("SELECT * FROM quotes ORDER BY id DESC").fetchall():
            qs.setdefault(r["lead_id"], []).append(r)
        first = {}
        for r in c.execute("SELECT * FROM lead_contacts ORDER BY id").fetchall():
            first.setdefault(r["lead_id"], r)
    return last, wc, called, won_at, qs, first


def _card(l, now, maps, cmap) -> dict | None:
    last, wc, called, won_at, qs, first = maps
    st = l["status"]
    col = COLUMN_OF.get(st, st)
    if col not in BOARD_LABEL:
        return None
    cust = L.customer_brand(l["domain"], cmap)
    if col != "won" and cust:
        return None                                          # ลูกค้าที่จ่ายแล้ว ไม่ใช่ดีลที่ยังเปิด
    if col == "won":
        at = _ts(won_at.get(l["id"]) or l["updated_at"])
        if not at or (now - at).days > WON_DAYS:
            return None
    lq = qs.get(l["id"], [])
    val = deal_value(l, lq)
    oq = next((q for q in lq if q["status"] in Q.OPEN), None)
    la = last.get(l["id"])
    wants = bool(wc.get(l["id"]) and wc[l["id"]] > called.get(l["id"], "") and st not in ("won", "quoted"))
    pc = first.get(l["id"])
    has_next = bool(l["next_action"])
    return {"id": l["id"], "domain": l["domain"], "name": (pc["name"] if pc and pc["name"] else l["name"]) or "",
            "status": st, "column": col, "status_label": L.STATUS_LABEL.get(st, st), "source": l["source"],
            "source_label": L.SOURCES.get(l["source"], l["source"]), "score": l["score"],
            "value": val["value"], "value_src": val["src"], "value_manual": l["deal_value"],
            "next_action": l["next_action"] or "", "next_at": l["next_at"], "next_kind": l["next_kind"] or "call",
            "next_fmt": fmt_ts(l["next_at"], now) if has_next and l["next_at"] else "", "next_input": bkk_input(l["next_at"]),
            "overdue": is_overdue(l, now), "soon": bool(has_next and _ts(l["next_at"]) and now <= _ts(l["next_at"]) <= now + dt.timedelta(hours=24)),
            "no_next": st in ACTIVE and not has_next, "wants_call": wants, "replied": st == "replied",
            "last_at": la[0] if la else (l["updated_at"] or l["created_at"]), "last_kind": L.EVENT_LABEL.get(la[1], la[1]) if la else "",
            "last_age": age(la[0] if la else (l["updated_at"] or l["created_at"]), now),
            "quote": {"number": oq["number"], "label": Q.STATUS_LABEL.get(oq["status"], oq["status"]), "status": oq["status"]} if oq else None,
            "search": " ".join(str(x or "") for x in (l["domain"], l["name"], l["email"], l["contact"], l["next_action"], l["notes"],
                                                       pc["name"] if pc else "")).lower()}


def board(q: str = "", source: str = "", overdue: bool = False, now: dt.datetime | None = None) -> dict:
    now = now or now_local()
    maps = _maps()
    cmap = L.customer_map()
    rows = db.list_leads(None, 10000)
    cards = [c for c in (_card(l, now, maps, cmap) for l in rows) if c]
    qq = (q or "").strip().lower()
    if qq:
        cards = [c for c in cards if qq in c["search"]]
    if source:
        cards = [c for c in cards if c["source"] == source]
    if overdue:
        cards = [c for c in cards if c["overdue"]]
    cols = []
    for key, label in BOARD:
        cs = sorted((c for c in cards if c["column"] == key), key=lambda c: c["last_at"] or "", reverse=True)
        cs.sort(key=lambda c: (not c["overdue"], not c["wants_call"], not c["replied"], c["next_at"] or "9999") if c["next_action"] else
                (not c["overdue"], not c["wants_call"], not c["replied"], "9999"))
        cols.append({"key": key, "label": label, "n": len(cs), "total": sum(c["value"] for c in cs), "cards": cs[:COLUMN_LIMIT],
                     "more": max(0, len(cs) - COLUMN_LIMIT)})
    by = {c["key"]: c for c in cols}
    outside = {k: sum(1 for l in rows if l["status"] == k) for k, _ in OUTSIDE}
    open_keys = ("call", "met", "quoted")
    return {"columns": cols, "outside": outside,
            "kpi": {"pipeline": sum(by[k]["total"] for k in open_keys), "pipeline_n": sum(by[k]["n"] for k in open_keys),
                    "overdue": sum(1 for c in cards if c["overdue"]), "today": sum(1 for c in cards if c["next_action"] and not c["overdue"] and c["next_fmt"].startswith("วันนี้")),
                    "no_next": sum(1 for c in cards if c["no_next"]), "won": by["won"]["total"], "won_n": by["won"]["n"]},
            "filters": {"q": q or "", "source": source or "", "overdue": bool(overdue)}}


def requests_inbox(limit: int = 30) -> list[dict]:
    now = now_local()
    return [{**dict(c), "age": age(c["created_at"], now), "when": fmt_ts(c["created_at"], now)} for c in db.list_new_contacts(limit)]


def lead_options(limit: int = 400) -> list[dict]:
    """ตัวเลือกช่องค้นหาดีลเดิม (ดีลที่ยังเปิดก่อน)"""
    rows = [l for l in db.list_leads(None, 5000) if l["status"] != "unsubscribed"]
    rows.sort(key=lambda l: (l["status"] in ("lost", "won"), -(l["id"])))
    return [{"id": l["id"], "domain": l["domain"], "name": l["name"] or ""} for l in rows[:limit]]


# ---------- หน้าลูกค้าเป้าหมาย ----------
def lead_panel(lead) -> dict:
    now = now_local()
    val = deal_value(lead)
    return {"crm": {"value": val["value"], "value_src": val["src"], "value_src_label": VALUE_SRC[val["src"]], "value_manual": lead["deal_value"],
                    "next_action": lead["next_action"] or "", "next_fmt": fmt_ts(lead["next_at"], now) if lead["next_action"] and lead["next_at"] else "",
                    "next_input": bkk_input(lead["next_at"]), "next_kind": lead["next_kind"] or "call", "overdue": is_overdue(lead, now),
                    "no_next": lead["status"] in ACTIVE and not lead["next_action"],
                    "contacts": contacts(lead), "act_kinds": ACT_KINDS, "next_kinds": NEXT_KINDS, "presets": PRESETS,
                    "stages": BOARD, "column": COLUMN_OF.get(lead["status"], lead["status"])},
            "icons": L.EVENT_ICON, "bkk": fmt_short}


# ---------- เตือนเจ้าของ (cron ทุก 15 นาที) ----------
def due_leads(now: dt.datetime | None = None, window_min: int = ALERT_WINDOW_MIN):
    now = now or now_local()
    edge = (now + dt.timedelta(minutes=window_min)).isoformat(timespec="seconds")
    with db.get_conn() as c:
        return c.execute(db.q("SELECT * FROM leads WHERE next_action IS NOT NULL AND next_at IS NOT NULL AND next_at <= ? "
                              "AND next_alerted_at IS NULL AND status != 'unsubscribed' ORDER BY next_at"), (edge,)).fetchall()


def _who(lead) -> str:
    """ผู้ติดต่อหลักสั้น ๆ สำหรับข้อความเตือน (ชื่อ · เบอร์/LINE) — เจ้าของกดโทรจาก LINE ได้เลย"""
    rows = db.list_lead_contacts(lead["id"])
    pc = rows[0] if rows else None
    if not pc:
        return lead["name"] or ""
    return " · ".join(x for x in (pc["name"], pc["phone"] or pc["line_id"] or pc["email"]) if x)


def run_reminders(now: dt.datetime | None = None) -> dict:
    """งานที่ถึงกำหนดภายใน 15 นาทีหรือเลยกำหนดแล้ว → แจ้งเจ้าของครั้งเดียวต่อนัด (เปลี่ยนเวลา = เตือนใหม่)"""
    from . import pipeline
    now = now or now_local()
    out = []
    for l in due_leads(now):
        t = _ts(l["next_at"])
        if not t:
            continue
        kind = l["next_kind"] or "call"
        when = fmt_ts(l["next_at"], now)
        who = _who(l)
        if t < now - dt.timedelta(minutes=1):
            msg = f"⏰ เลยกำหนด ({when}): {l['next_action']} — {l['domain']}"
        else:
            mins = max(0, int((t - now).total_seconds() // 60))
            msg = f"⏰ อีก {mins} นาที ({when}): {l['next_action']} — {l['domain']}"
        msg += (f" · {who}" if who else "") + f" [{NEXT_KINDS.get(kind, kind)} · {BOARD_LABEL.get(COLUMN_OF.get(l['status'], l['status']), L.STATUS_LABEL.get(l['status'], l['status']))}]"
        subject = f"⏰ นัดประชุม {l['domain']} {when}" if kind == "meeting" else None
        try:
            pipeline.alert_admins(msg, f"/admin/leads/{l['id']}", "crm", subject=subject)
        except Exception:
            continue                                             # รอบหน้าลองใหม่ (ยังไม่จดว่าเตือนแล้ว)
        db.update_lead(l["id"], next_alerted_at=db.now())
        out.append({"lead_id": l["id"], "domain": l["domain"], "overdue": t < now, "email": bool(subject)})
    return {"alerted": out, "n": len(out)}


# ---------- สรุปเช้า ----------
def digest_text(now: dt.datetime | None = None) -> str:
    """ส่วน "งานขายวันนี้" ในสรุปเช้า 08:00: นัดวันนี้ · เลยกำหนด · อยากคุยแต่ยังไม่มีนัด (พังเอง = ไม่ใส่ ไม่ให้สรุปเช้าล้ม)"""
    try:
        return _digest(now)
    except Exception as e:
        return f"งานขายวันนี้: อ่านไม่สำเร็จ ({type(e).__name__}) — ดูที่ /admin/deals"


def _digest(now: dt.datetime | None = None) -> str:
    now = now or now_local()
    nb = now.astimezone(BKK)
    end = local_str(nb.replace(hour=23, minute=59, second=59, microsecond=0))
    nows = now.isoformat(timespec="seconds")
    cmap = L.customer_map()
    rows = [l for l in db.list_leads(None, 10000) if l["status"] != "unsubscribed"]
    withnext = sorted((l for l in rows if l["next_action"] and l["next_at"]), key=lambda l: l["next_at"])
    today = [l for l in withnext if nows <= l["next_at"] <= end]
    late = [l for l in withnext if l["next_at"] < nows]
    idle = [l for l in rows if l["status"] == "call" and not l["next_action"] and not L.customer_brand(l["domain"], cmap)]
    line = lambda l: f"  • {fmt_ts(l['next_at'], now)} {l['next_action']} — {l['domain']}"
    out = [f"งานขายวันนี้: นัด {len(today)} · เลยกำหนด {len(late)} · อยากคุยแต่ยังไม่มีนัด {len(idle)} (/admin/deals)"]
    out += [line(l) for l in today[:8]]
    if late:
        out.append(f"  เลยกำหนด {len(late)} รายการ:")
        out += [line(l) for l in late[:8]]
    if idle:
        out.append("  อยากคุยแต่ยังไม่มีนัด: " + ", ".join(l["domain"] for l in idle[:8]) + (f" และอีก {len(idle) - 8}" if len(idle) > 8 else ""))
    return "\n".join(out)
