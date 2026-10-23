"""ออฟฟิศเอเจนต์ — ข้อมูลสดสำหรับหน้า /admin/office (ภาพ 3D isometric ของทีม ขยับตามงานจริง)
ตัวละครขยับจากของจริงสามแหล่ง: ไทม์ไลน์ลูกค้าเป้าหมาย (lead_events) · การตัดสินใจ (decisions) · โพสต์ (posts)
ส่วน "กำลังทำงานอยู่" มาจาก working()/track() ที่ครอบรอบงานจริง (หาลูกค้า ตรวจเว็บ สรุปเช้า การตลาด Autopilot ฯลฯ)
เก็บเป็น JSON ใน settings ไม่มีตารางใหม่ · ไม่มีข้อมูลส่วนตัวของลูกค้า นอกจากโดเมนที่เปิดเผยอยู่แล้ว"""
from __future__ import annotations
import contextlib
import datetime as dt
import functools
import json
from zoneinfo import ZoneInfo

from . import db

BKK = ZoneInfo("Asia/Bangkok")
LIVE_KEY = "office_live"
STALE_MIN = 45            # รอบงานค้าง "กำลังทำ" เกินนี้ถือว่ารอบล่ม → แสดงว่าว่าง (กันบอทค้างตลอดกาล)

# ตัวละครในออฟฟิศ — id ตรงกับ decisions.agent / lead_events.actor / ชื่อทักษะ
ACTORS = [
    {"id": "owner", "name": "เจ้าของ", "role": "อนุมัติ · ปลดล็อก · ยืนยันยอดโอน", "hex": "1f2a44", "human": True},
    {"id": "ceo", "name": "CEO", "role": "คัดลูกค้า · ร่างข้อความทัก · สรุปเช้า", "hex": "d97706", "skill": "ceo"},
    {"id": "prospecting", "name": "หาลูกค้า", "role": "ค้นธุรกิจตามกลุ่มเป้าหมาย · หาอีเมล", "hex": "b45309", "skill": "prospecting"},
    {"id": "sdr", "name": "SDR", "role": "ติดตามผลลีดที่เงียบ วันที่ 3 · 7 · 14", "hex": "ea580c", "skill": "sdr"},
    {"id": "cfo", "name": "CFO", "role": "ใบเสนอราคา · กรอบส่วนลด · รับเงิน", "hex": "059669", "skill": "cfo"},
    {"id": "cto", "name": "CTO", "role": "ตรวจเว็บ · เปิดบัญชี · เฝ้าระบบ", "hex": "2563eb", "skill": "cto"},
    {"id": "cmo", "name": "CMO", "role": "โพสต์ LINE/Facebook · เคสศึกษา", "hex": "c0265c", "skill": "cmo"},
    {"id": "delivery", "name": "ส่งมอบ", "role": "คอนเทนต์ลูกค้า · Autopilot · QA", "hex": "7c3aed", "skill": "delivery"},
    {"id": "cs", "name": "ดูแลลูกค้า", "role": "เริ่มงาน · ติดตั้ง · งานชิ้นแรก · รายงานก่อนรอบบิล", "hex": "16a34a", "skill": "cs"},
    {"id": "acct", "name": "บัญชี", "role": "ใบเสร็จ/ใบกำกับภาษี · ใบแจ้งหนี้รอบถัดไป · ทวงชำระ", "hex": "0f766e", "skill": "acct"},
    {"id": "deliv", "name": "ส่งอีเมล", "role": "ตรวจโดเมน · ทดสอบกล่อง · กันสแปม · ทดลองข้อความ A/B", "hex": "4f46e5", "skill": "deliv"},
    {"id": "hop", "name": "LINE OA เจอ.AI", "role": "ต้อนรับ · ตรวจเว็บในแชต · อ่านอีเมลตอบกลับ", "hex": "0891b2"},   # ชื่อตามชื่อ OA ที่ตั้งไว้ (ดู state)
]
IDS = {a["id"] for a in ACTORS}
NAME = {a["id"]: a["name"] for a in ACTORS}

# ตารางงานประจำ (crontab ของโฮสต์ เวลาไทย) — ใช้บอก "รอบถัดไป" ของแต่ละคน · weekday: None = ทุกวัน, 0 = จันทร์
SCHEDULE = [
    ("prospecting", None, "06:30", "หาลูกค้าใหม่ตามกลุ่มเป้าหมาย"),
    ("delivery", None, "07:30", "ส่งอีเมลแคมเปญที่ถึงกำหนด"),
    ("ceo", None, "08:00", "สรุปเช้าให้เจ้าของ"),
    ("cto", None, "08:00", "เช็กว่าเว็บลูกค้ายังเปิดอยู่"),
    ("cmo", None, "09:00", "รอบการตลาด: เผยแพร่ · โพสต์ · เคสศึกษา"),
    ("sdr", None, "10:00", "ติดตามผลลีดที่ทักแล้วเงียบ"),
    ("cs", None, "10:30", "ดูแลลูกค้าที่จ่ายแล้ว"),
    ("acct", None, "08:15", "ออกใบแจ้งหนี้รอบถัดไป · ทวงชำระ"),
    ("deliv", None, "07:45", "ตรวจโดเมนผู้ส่ง · ทดสอบกล่อง · ทดลองข้อความ"),
    ("cto", 0, "09:00", "เช็กอันดับ Google"),
    ("cto", 0, "09:15", "ซิงค์ Search Console"),
    ("cto", 0, "09:30", "ตรวจสุขภาพเว็บลูกค้า"),
    ("cto", 0, "10:00", "ถาม AI ว่าเอ่ยถึงแบรนด์ลูกค้าไหม"),
    ("cto", 0, "10:30", "เช็ก Google AI Overview"),
    ("cto", 0, "11:00", "สแกนความเร็วเว็บลูกค้า"),
    ("delivery", 1, "02:00", "รอบ Autopilot เขียนคอนเทนต์ให้ลูกค้า"),
]

# เหตุการณ์ลูกค้าเป้าหมาย → (ตัวละครเริ่มต้น, ชนิดสำหรับหน้าเว็บ, ข้อความ) · {dom} = โดเมน
LEAD_EVENTS = {
    "prospect": ("prospecting", "found", "พบ {dom}"),
    "contact_found": ("prospecting", "contact", "พบอีเมลของ {dom}"),
    "no_contact": ("prospecting", "nocontact", "ไม่พบอีเมลของ {dom}"),
    "audit": ("cto", "audit", "ตรวจเว็บ {dom}"),
    "contacted": ("ceo", "outreach", "ทัก {dom}"),
    "opened": ("lead", "opened", "{dom} เปิดอ่านผลตรวจ"),
    "replied": ("lead", "replied", "{dom} ตอบกลับ / ขอใบเสนอราคา"),
    "quote_sent": ("cfo", "quote", "ส่งใบเสนอราคาให้ {dom}"),
    "quote_viewed": ("lead", "viewed", "{dom} เปิดดูใบเสนอราคา"),
    "quote_accepted": ("lead", "accepted", "{dom} ยืนยันรับข้อเสนอ"),
    "payment_claimed": ("lead", "claimed", "{dom} แจ้งชำระเงิน"),
    "paid": ("cfo", "paid", "รับเงินจาก {dom}"),
    "provisioned": ("cto", "provisioned", "เปิดบัญชีให้ {dom}"),
    "won": ("ceo", "won", "ปิดดีล {dom} 🎉"),
    "lost": ("ceo", "lost", "{dom} ไม่สนใจ"),
    "unsubscribed": ("lead", "unsub", "{dom} ขอไม่รับการติดต่อ"),
    "line": ("hop", "line", "{dom} ทักมาทาง LINE"),
    "deep_audit": ("cto", "audit", "รายงานเต็ม {dom}: ไต่เว็บ + อันดับ Google"),
    "followup": ("sdr", "outreach", "ติดตามผล {dom}"),
    "cs_kickoff": ("cs", "cs", "เริ่มงานกับ {dom}: แผน 30 วัน"),
    "acct_receipt": ("acct", "paid", "ออกใบเสร็จให้ {dom}"),
    "acct_invoice": ("acct", "quote", "ออกใบแจ้งหนี้รอบถัดไปให้ {dom}"),
    "acct_reminder": ("acct", "outreach", "เตือน {dom} ชำระค่าบริการ"),
    "acct_overdue": ("acct", "risk", "{dom} ค้างชำระ"),
    "bounced": ("deliv", "risk", "อีเมลถึง {dom} เด้ง"),
    "complained": ("deliv", "risk", "{dom} ร้องเรียนว่าอีเมลเป็นสแปม"),
    "cs_installed": ("cs", "cs", "{dom} ติดตั้งตัวเชื่อมแล้ว"),
    "cs_install_reminder": ("cs", "cs", "เตือน {dom} ติดตั้งตัวเชื่อม"),
    "cs_first_content": ("cs", "cs", "{dom} ได้งานชิ้นแรกแล้ว"),
    "cs_review_nudge": ("cs", "cs", "ชวน {dom} ตรวจร่างคอนเทนต์"),
    "cs_report": ("cs", "cs", "ส่งรายงานเดือนให้ {dom}"),
    "cs_risk": ("cs", "risk", "{dom} เสี่ยงหลุด"),
    "call": ("owner", "call", "เจ้าของโทรหา {dom}"),
    "ai_check": ("cto", "audit", "ถาม AI 20 คำถามเรื่อง {dom}"),
}
VISITOR_STATUSES = ("new", "contacted", "replied", "quoted", "won")
KIND_TH = {"lead_review": "คัดลูกค้า", "outreach": "ข้อความทัก", "quote": "ใบเสนอราคา", "payment": "ยืนยันยอดเข้า", "post": "โพสต์การตลาด", "followup": "ติดตามผล"}
CHANNEL_TH = {"line": "LINE OA", "facebook": "Facebook Page"}


# ---------- เวลา ----------
def tz_str() -> str:
    """offset ของนาฬิกาเครื่อง (ที่ db.now() ใช้) เช่น +07:00 — หน้าเว็บใช้แปลงเวลาที่เก็บแบบไม่มีโซน"""
    off = dt.datetime.now().astimezone().utcoffset() or dt.timedelta(0)
    s = int(off.total_seconds())
    sign = "+" if s >= 0 else "-"
    s = abs(s)
    return f"{sign}{s // 3600:02d}:{(s % 3600) // 60:02d}"


def today_bkk() -> str:
    return dt.datetime.now(BKK).date().isoformat()


def today_start_local() -> str:
    """เที่ยงคืนวันนี้ตามเวลาไทย แปลงเป็นเวลาเครื่องแบบไม่มีโซน (รูปเดียวกับ db.now()) ไว้เทียบใน SQL"""
    bkk0 = dt.datetime.now(BKK).replace(hour=0, minute=0, second=0, microsecond=0)
    return bkk0.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")


def next_run(actor: str, now: dt.datetime | None = None) -> dict | None:
    now = now or dt.datetime.now(BKK)
    best = None
    for a, wd, hm, label in SCHEDULE:
        if a != actor:
            continue
        h, m = (int(x) for x in hm.split(":"))
        for dd in range(0, 8):
            t = (now + dt.timedelta(days=dd)).replace(hour=h, minute=m, second=0, microsecond=0)
            if (wd is None or t.weekday() == wd) and t > now:
                if best is None or t < best[0]:
                    best = (t, label)
                break
    return {"at": best[0].isoformat(timespec="minutes"), "label": best[1]} if best else None


# ---------- สถานะ "กำลังทำงาน" ----------
def _load() -> dict:
    try:
        return json.loads(db.get_setting(LIVE_KEY) or "{}") or {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    db.set_setting(LIVE_KEY, json.dumps(d, ensure_ascii=False))


def begin(actor: str, label: str = "") -> None:
    """เริ่มงาน — ไม่โยน error ออกไปรบกวนงานจริงไม่ว่ากรณีไหน"""
    try:
        d = _load()
        a = d.get(actor) or {}
        if a.get("day") != today_bkk():
            a["runs_today"] = 0
        a.update(status="working", since=db.now(), label=(label or a.get("label") or "")[:120], day=today_bkk())
        d[actor] = a
        _save(d)
    except Exception:
        pass


def end(actor: str, label: str | None = None) -> None:
    try:
        d = _load()
        a = d.get(actor) or {}
        if a.get("day") != today_bkk():
            a["runs_today"] = 0
        a.update(status="idle", last=db.now(), runs_today=int(a.get("runs_today") or 0) + 1, day=today_bkk())
        if label:
            a["label"] = label[:120]
        d[actor] = a
        _save(d)
    except Exception:
        pass


@contextlib.contextmanager
def working(actor: str, label: str = ""):
    begin(actor, label)
    try:
        yield
    finally:
        end(actor)


def track(actor: str, label=""):
    """เดคอเรเตอร์: ครอบฟังก์ชันงานจริง · label เป็นข้อความหรือฟังก์ชัน(*args, **kwargs) → ข้อความ"""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                lab = label(*args, **kwargs) if callable(label) else label
            except Exception:
                lab = ""
            with working(actor, lab or ""):
                return fn(*args, **kwargs)
        return wrapper
    return deco


def live() -> dict:
    """สถานะต่อตัวละครที่หน้าเว็บใช้ — ค้างเกิน STALE_MIN ถือว่าว่าง · นับรอบเฉพาะวันนี้"""
    d = _load()
    now = dt.datetime.now()
    out = {}
    for a in ACTORS:
        rec = d.get(a["id"]) or {}
        st = rec.get("status") or "idle"
        if st == "working" and rec.get("since"):
            try:
                if (now - dt.datetime.fromisoformat(rec["since"])).total_seconds() > STALE_MIN * 60:
                    st = "idle"
            except Exception:
                st = "idle"
        out[a["id"]] = {"status": st, "label": rec.get("label") or "", "since": rec.get("since"), "last": rec.get("last"),
                        "runs_today": int(rec.get("runs_today") or 0) if rec.get("day") == today_bkk() else 0}
    return out


# ---------- เหตุการณ์วันนี้ ----------
def _actor_of(raw: str | None, default: str) -> str:
    a = (raw or "").strip().lower()
    if a == "customer":
        return "lead"
    if a in IDS:
        return a
    if a in ("auto", "system", "stripe", ""):
        return default
    if "@" in a or a == "admin":
        return "owner"
    return default


def _ev(eid: str, at: str, actor: str, type_: str, text: str, **extra) -> dict:
    e = {"id": eid, "at": at, "actor": actor, "type": type_, "text": text}
    e.update({k: v for k, v in extra.items() if v is not None})
    return e


def events_today(start: str, dom: dict) -> list[dict]:
    out = []
    with db.get_conn() as c:
        evs = c.execute(db.q("SELECT id, created_at, kind, note, actor, lead_id FROM lead_events WHERE created_at >= ? ORDER BY id DESC LIMIT 200"),
                        (start,)).fetchall()
        decs = c.execute(db.q("SELECT * FROM decisions WHERE created_at >= ? OR (decided_at IS NOT NULL AND decided_at >= ?) ORDER BY id DESC LIMIT 120"),
                         (start, start)).fetchall()
        posts = c.execute(db.q("SELECT id, published_at, channel, kind, title FROM posts WHERE status='published' AND published_at >= ? ORDER BY id DESC LIMIT 60"),
                          (start,)).fetchall()
    for e in evs:
        m = LEAD_EVENTS.get(e["kind"])
        if not m:
            continue
        default, type_, tpl = m
        d = dom.get(e["lead_id"]) or ""
        out.append(_ev(f"ev:{e['id']}", e["created_at"], _actor_of(e["actor"], default), type_, tpl.format(dom=d),
                       lead={"id": e["lead_id"], "domain": d}, detail=(e["note"] or "")[:90] or None))
    for d_ in decs:
        agent = d_["agent"] if d_["agent"] in IDS else "ceo"
        lead = {"id": d_["lead_id"], "domain": dom.get(d_["lead_id"]) or ""} if d_["lead_id"] else None
        kind, title, st = d_["kind"], (d_["title"] or "")[:80], d_["status"]
        if kind == "digest":
            if d_["created_at"] >= start:
                out.append(_ev(f"dec:{d_['id']}:digest", d_["created_at"], "ceo", "digest", "CEO ส่งสรุปเช้าให้เจ้าของ"))
            continue
        kth = KIND_TH.get(kind, kind)
        if d_["created_at"] >= start and st != "auto":
            out.append(_ev(f"dec:{d_['id']}:proposed", d_["created_at"], agent, "propose", f"{NAME[agent]} เสนอ{kth}: {title}", kind=kind, lead=lead))
        when = d_["decided_at"] or d_["created_at"]
        if st == "auto":
            out.append(_ev(f"dec:{d_['id']}:auto", when, agent, "auto", f"{NAME[agent]} ทำเองในกรอบ: {title}", kind=kind, lead=lead))
        elif st in ("executed", "approved") and when >= start:
            out.append(_ev(f"dec:{d_['id']}:approve", when, "owner", "approve", f"เจ้าของอนุมัติ{kth}: {title}", kind=kind, lead=lead, to=agent))
        elif st == "rejected" and when >= start:
            out.append(_ev(f"dec:{d_['id']}:reject", when, "owner", "reject", f"เจ้าของไม่อนุมัติ{kth}: {title}", kind=kind, lead=lead, to=agent))
    with db.get_conn() as c:
        tasks = c.execute(db.q("SELECT * FROM agent_tasks WHERE created_at >= ? OR (finished_at IS NOT NULL AND finished_at >= ?) ORDER BY id DESC LIMIT 60"),
                          (start, start)).fetchall()
    for t in tasks:
        to = t["agent"] if t["agent"] in IDS else "ceo"
        if t["created_at"] >= start:
            out.append(_ev(f"task:{t['id']}", t["created_at"], "ceo", "assign", f"CEO แจกงานให้ {NAME.get(to, to)}: {(t['title'] or '')[:60]}", to=to))
        if t["finished_at"] and t["finished_at"] >= start and t["status"] in ("done", "failed"):
            out.append(_ev(f"task:{t['id']}:done", t["finished_at"], to, "task_done", f"{NAME.get(to, to)} {'เสร็จ' if t['status'] == 'done' else 'ทำไม่สำเร็จ'}: {(t['title'] or '')[:50]}"))
    for p in posts:
        ch = CHANNEL_TH.get(p["channel"], p["channel"])
        out.append(_ev(f"post:{p['id']}", p["published_at"], "cmo", "post", f"CMO โพสต์ทาง {ch}: {(p['title'] or '')[:60]}", channel=p["channel"]))
    out.sort(key=lambda x: x["at"])
    return out[-80:]


# ---------- สถานะทั้งหมดสำหรับหน้าเว็บ ----------
def state() -> dict:
    from . import execs, leads as L
    start = today_start_local()
    rows = db.list_leads(None, 2000)
    dom = {r["id"]: r["domain"] for r in rows}
    events = events_today(start, dom)
    with db.get_conn() as c:
        won = c.execute(db.q("SELECT COUNT(*) AS n, COALESCE(SUM(first_total), 0) AS thb FROM quotes WHERE status='paid' AND paid_at >= ?"), (start,)).fetchone()
        pending = c.execute("SELECT COUNT(*) AS n FROM decisions WHERE status='proposed'").fetchone()["n"]
    n_type = lambda t: sum(1 for e in events if e["type"] == t)
    f = L.funnel(rows)
    visitors = [{"id": r["id"], "domain": r["domain"], "status": r["status"], "score": r["score"], "src": r["src"] if "src" in r.keys() else None}
                for r in rows if r["status"] in VISITOR_STATUSES][:150]
    lv = live()
    from . import line_oa
    actors = []
    for a in ACTORS:
        rec = lv[a["id"]]
        row = {**a, **rec, "next": next_run(a["id"])}
        if a["id"] == "hop":                                   # บอทในล็อบบี้ใช้ชื่อ OA จริง (ตั้งค่า LINE → ชื่อบอท)
            row["name"] = f"LINE OA {line_oa.bot_name()}"
        actors.append(row)
    kpi = execs.kpis()
    return {
        "updated": db.now(), "tz": tz_str(), "today": today_bkk(), "mode": execs.mode_label(), "paused": execs.paused(),
        "actors": actors, "events": events, "visitors": visitors,
        "board": {"funnel": f["n"], "reply_rate": f["reply_rate"], "close_rate": f["close_rate"],
                  "won_today": won["n"], "paid_today_thb": int(won["thb"] or 0), "pending": pending,
                  "audits_today": n_type("audit"), "outreach_today": n_type("outreach"), "prospects_today": n_type("found"), "posts_today": n_type("post"),
                  "kpi": {"phase": kpi["phase"], "rows": kpi["rows"], "n_pass": kpi["n_pass"], "n_rows": kpi["n_rows"]}},
    }
