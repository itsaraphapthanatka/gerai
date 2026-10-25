"""ฝ่ายดูแลการส่งอีเมล (Deliverability) — ให้อีเมลทักเข้ากล่องจดหมายจริง และหาข้อความที่ได้คนตอบมากที่สุด
· ตรวจโดเมนผู้ส่งทุกวัน: SPF · DKIM · DMARC · บัญชีดำ (Spamhaus DBL · SURBL · URIBL) ผ่าน DNS-over-HTTPS
· ทดสอบกล่องจริงทุกสัปดาห์: ส่งอีเมลหน้าตาแบบอีเมลทักไปกล่องทดสอบของเจ้าของ (IMAP อ่านอย่างเดียว) → กล่องหลัก/โปรโมชัน/สแปม/ไม่ถึง
· เฝ้าสัญญาณ 30 วัน: ส่ง · เด้ง · ร้องเรียนสแปม · ขอไม่รับ · เปิดรายงาน · ตอบกลับ (เด้ง/ร้องเรียนจาก Resend webhook + อีเมลเด้งใน Inbox)
· กันชื่อเสียงโดเมน: เด้ง > 5% · ร้องเรียน > 0.3% · ขอไม่รับ > 3% · หรือทดสอบกล่องตกสแปม → หยุดส่งเองเฉพาะข้อความทัก/ติดตามผล รอเจ้าของปลด
· เพิ่มจำนวนทักทีละขั้นเมื่อสุขภาพดี (สัปดาห์ละ +5 ราย/วัน ไม่เกินเพดานหาลูกค้า)
· ทดลองข้อความทัก A/B ทีละเรื่อง: แบ่งลีดครึ่งต่อครึ่ง วัด "เปิดรายงานหรือตอบกลับ" · ครบ 30 รายต่อแบบและต่างอย่างมีนัย (p < 0.05) → ใช้แบบที่ชนะเป็นค่าเริ่มต้น
  ครบ 120 รายยังไม่ต่าง → คงแบบเดิม · แบบที่ชนะแต่ทำให้ขอไม่รับเพิ่มเกิน 2 จุด ไม่นำมาใช้"""
from __future__ import annotations
import base64
import datetime as dt
import hashlib
import hmac
import imaplib
import json
import math
import re
import secrets
import time

from . import db, office

HOOKS = {"dns": None, "imap": None}            # เทสต์ฉีดของปลอม
WINDOW_DAYS = 30
MIN_SENDS = 20
LIMITS = {"bounce": 5.0, "complaint": 0.3, "unsub": 3.0}
HEALTHY = {"bounce": 3.0, "complaint": 0.0, "unsub": 2.0}
RAMP_STEP, RAMP_EVERY_DAYS = 5, 7
MIN_ARM, MAX_ARM, ALPHA, UNSUB_MARGIN = 30, 120, 0.05, 2.0
DKIM_SELECTORS = ("resend", "google", "default", "selector1", "selector2", "s1", "s2", "k1", "mail", "dkim", "mx")
CHECK_URL = {"dbl.spamhaus.org": "check.spamhaus.org", "multi.surbl.org": "surbl.org/surbl-analysis", "multi.uribl.com": "admin.uribl.com"}
BLOCKLISTS = (("dbl.spamhaus.org", "Spamhaus DBL"), ("multi.surbl.org", "SURBL"), ("multi.uribl.com", "URIBL"))
PLACE_TH = {"inbox": "กล่องหลัก", "promotions": "แท็บโปรโมชัน", "spam": "สแปม", "missing": "ไม่ถึง", "pending": "รอตรวจ", "error": "ตรวจไม่ได้"}

EXPERIMENTS = [
    {"key": "subject_q", "title": "หัวข้อเป็นคำถาม", "what": "หัวข้ออีเมล",
     "prompt": "หัวข้ออีเมลเป็นคำถามสั้นไม่เกิน 10 คำ ที่อ้างปัญหาข้อแรกที่ตรวจเจอของเว็บนี้ และไม่มีชื่อเจอ.AI ในหัวข้อ",
     "tpl": {"subject": "{dom}: ลูกค้าหาคุณใน Google และ AI เจอไหม?"}},
    {"key": "opener_finding", "title": "เปิดด้วยผลตรวจทันที", "what": "ประโยคแรก",
     "prompt": "ประโยคแรกของอีเมลบอกผลตรวจที่สำคัญที่สุดของเว็บนี้ทันที ไม่ต้องแนะนำตัวก่อน แนะนำตัวสั้น ๆ ไว้ท้ายอีเมล",
     "tpl": {"opener": True}},
    {"key": "short", "title": "อีเมลสั้น ปัญหาข้อเดียว", "what": "ความยาว",
     "prompt": "เขียนให้สั้นไม่เกิน 70 คำ เหลือปัญหาข้อเดียวที่สำคัญที่สุด ลิงก์ผลตรวจ และคำถามปิดท้ายหนึ่งข้อ",
     "tpl": {"short": True}},
]
EXP = {e["key"]: e for e in EXPERIMENTS}
SPAM_WORDS = re.compile(r"(ด่วนที่สุด|รวยเร็ว|คลิกเลย|โปรโมชั่น|ลดราคา|ฟรี\s*!|100\s*%|!!|\$\$|รับประกัน|การันตี|winner|act now|click here)", re.I)


def _get(key, default=None):
    v = db.get_setting(key)
    return v if v not in (None, "") else default


def _json(key, default):
    try:
        return json.loads(db.get_setting(key) or "null") or default
    except Exception:
        return default


def _set_json(key, v) -> None:
    db.set_setting(key, json.dumps(v, ensure_ascii=False))


def sender_domain() -> str:
    from . import mailer
    cfg = mailer.config() or {}
    return ((cfg.get("smtp_from") or "").split("@")[-1] or "").lower()


# ---------- DNS ----------
def _dns(name: str, typ: str) -> list[str] | None:
    """คืนรายการคำตอบ (TXT ถอดเครื่องหมายคำพูดแล้ว) · [] = ไม่มี · None = ถาม DNS ไม่ได้"""
    if HOOKS["dns"]:
        return HOOKS["dns"](name, typ)
    import httpx
    try:
        r = httpx.get("https://cloudflare-dns.com/dns-query", params={"name": name, "type": typ},
                      headers={"accept": "application/dns-json"}, timeout=10)
        d = r.json()
    except Exception:
        return None
    if d.get("Status") not in (0, 3):
        return None
    out = []
    for a in d.get("Answer") or []:
        v = str(a.get("data") or "")
        out.append("".join(re.findall(r'"([^"]*)"', v)) if typ == "TXT" and '"' in v else v)
    return out


def _dns_a(name: str) -> list[str] | None:
    """ระเบียน A ผ่านตัวแก้ชื่อของเครื่อง (บัญชีดำหลายเจ้าไม่ตอบ DNS สาธารณะอย่าง Cloudflare) · [] = ไม่มี (ไม่ติด) · None = ถามไม่ได้"""
    if HOOKS["dns"]:
        return HOOKS["dns"](name, "A")
    import socket
    try:
        return socket.gethostbyname_ex(name)[2]
    except (socket.gaierror, socket.herror) as e:
        return [] if getattr(e, "errno", None) in (socket.EAI_NONAME, 1, -2, -5) or "not known" in str(e) or "Unknown host" in str(e) else None
    except Exception:
        return None


def check_dns(domain: str | None = None) -> dict:
    domain = domain or sender_domain()
    res = {"domain": domain, "at": db.now(), "items": [], "ok": True}
    if not domain:
        res.update(ok=False, items=[{"key": "domain", "label": "โดเมนผู้ส่ง", "status": "fail", "detail": "ยังไม่ได้ตั้งค่า SMTP"}])
        return res

    def add(key, label, status, detail):
        res["items"].append({"key": key, "label": label, "status": status, "detail": detail})
        if status == "fail":
            res["ok"] = False
    for host, label in ((domain, f"SPF ของ {domain}"), (f"send.{domain}", f"SPF ของ send.{domain} (ที่อยู่ตีกลับของ Resend)")):
        txt = _dns(host, "TXT")
        spf = [t for t in (txt or []) if t.lower().startswith("v=spf1")]
        if txt is None:
            add("spf", label, "warn", "ถาม DNS ไม่ได้ตอนนี้")
        elif len(spf) > 1:
            add("spf", label, "fail", "มี SPF มากกว่าหนึ่งระเบียน — ผู้รับจะถือว่าไม่ผ่าน ต้องรวมเป็นระเบียนเดียว")
        elif spf:
            add("spf", label, "ok" if not spf[0].strip().endswith("+all") else "fail", spf[0][:160])
        else:
            add("spf", label, "warn" if host != domain else "warn", "ไม่มีระเบียน SPF")
    found = []
    for sel in DKIM_SELECTORS:
        txt = _dns(f"{sel}._domainkey.{domain}", "TXT") or []
        if any("p=" in t for t in txt):
            found.append(sel)
    add("dkim", "DKIM (ลายเซ็นอีเมล)", "ok" if found else "fail",
        f"พบ selector: {', '.join(found)}" if found else "ไม่พบระเบียน DKIM ของ selector ที่รู้จัก — ตั้งตามหน้า Domains ของ Resend")
    txt = _dns(f"_dmarc.{domain}", "TXT")
    dm = next((t for t in (txt or []) if t.lower().startswith("v=dmarc1")), "")
    if txt is None:
        add("dmarc", "DMARC", "warn", "ถาม DNS ไม่ได้ตอนนี้")
    elif not dm:
        add("dmarc", "DMARC", "fail", "ไม่มีระเบียน DMARC — Gmail/Yahoo บังคับให้ผู้ส่งจำนวนมากต้องมี")
    else:
        pol = (re.search(r"\bp=(\w+)", dm) or [None, "none"])[1].lower()
        add("dmarc", "DMARC", "ok" if pol in ("quarantine", "reject") else "warn", f"นโยบาย p={pol} · {dm[:120]}")
    for zone, label in BLOCKLISTS:
        a = _dns_a(f"{domain}.{zone}")
        if a is None:
            add("bl_" + zone, f"บัญชีดำ {label}", "warn", "ถามไม่ได้ตอนนี้")
        elif not a:
            add("bl_" + zone, f"บัญชีดำ {label}", "ok", "ไม่อยู่ในบัญชีดำ")
        elif any(x.startswith("127.255.255.") for x in a) or a == ["127.0.0.1"]:
            add("bl_" + zone, f"บัญชีดำ {label}", "warn", f"ผู้ให้บริการปฏิเสธคำถามจากเซิร์ฟเวอร์นี้ — ตรวจอัตโนมัติไม่ได้ ตรวจเองที่ {CHECK_URL.get(zone, zone)}")
        else:
            add("bl_" + zone, f"บัญชีดำ {label}", "fail", f"โดเมนอยู่ในบัญชีดำ ({', '.join(a)}) — อีเมลที่มีลิงก์โดเมนนี้มักตกสแปม")
    _set_json("deliv_dns", res)
    return res


# ---------- โดเมนผู้รับมีเซิร์ฟเวอร์รับอีเมลไหม (MX) ----------
_MX_CACHE: dict = {}
MX_TTL = 6 * 3600


def mx_check_on() -> bool:
    return _get("mx_check", "0") == "1"


def mx_status(domain: str) -> bool | None:
    """True = มี MX · False = ไม่มีเซิร์ฟเวอร์รับอีเมล (ส่งไปเด้งแน่) · None = ถาม DNS ไม่ได้ (ไม่บล็อก)"""
    d = (domain or "").strip().lower().rstrip(".")
    if not d:
        return None
    hit = _MX_CACHE.get(d)
    if hit and time.time() - hit[1] < MX_TTL:
        return hit[0]
    ans = _dns(d, "MX")
    st = None if ans is None else bool([a for a in ans if a.strip() and not a.strip().endswith(" .")])
    _MX_CACHE[d] = (st, time.time())
    return st


def mx_ok(email: str) -> bool:
    """ใช้ก่อนส่ง: บล็อกเฉพาะเมื่อแน่ใจว่าไม่มี MX · ปิดสวิตช์ (mx_check) = ไม่ตรวจ"""
    if not mx_check_on() or "@" not in (email or ""):
        return True
    return mx_status(email.split("@")[-1]) is not False


# ---------- ตรวจเนื้อหาก่อนส่ง ----------
def spam_flags(subject: str, body: str) -> list[str]:
    flags = []
    text = f"{subject}\n{body}"
    if SPAM_WORDS.search(text):
        flags.append(f"คำที่ตัวกรองสแปมไม่ชอบ: {SPAM_WORDS.search(text).group(0)}")
    links = re.findall(r"https?://", body)
    if len(links) > 3:
        flags.append(f"ลิงก์ {len(links)} ลิงก์ (ควรไม่เกิน 3)")
    if len(subject) > 90:
        flags.append("หัวข้อยาวเกิน 90 ตัวอักษร")
    latin = re.sub(r"[^A-Za-z]", "", subject)
    if len(latin) >= 8 and latin.isupper():
        flags.append("หัวข้อเป็นตัวพิมพ์ใหญ่ทั้งหมด")
    return flags


# ---------- ทดลองข้อความ A/B ----------
def exp_on() -> bool:
    return _get("deliv_exp_on", "0") == "1"


def active_experiment() -> dict | None:
    if not exp_on():
        return None
    idx = int(_get("deliv_exp_idx", "0"))
    return EXPERIMENTS[idx] if idx < len(EXPERIMENTS) else None


def adopted() -> list[str]:
    return _json("deliv_adopted", [])


# ---------- อีเมลแบบคนเขียน (ทดสอบกล่องจริง 25 ต.ค.: แม่แบบที่มีรายการ • / "ตรวจ 16 ข้อ" / ชวนนัด 15 นาที / ลงชื่อแบรนด์ = แท็บโปรโมชัน
# ทั้งกล่องเก่าและกล่องใหม่ · ปัญหาข้อเดียวเป็นประโยค + ลิงก์รายงาน 1 ลิงก์ + ลงชื่อคน = กล่องหลัก แม้มีลิงก์และหัว List-Unsubscribe) ----------
PERSONAL_RULES = ("เขียนเหมือนคนพิมพ์อีเมลถึงคนคนเดียว ไม่เกิน 70 คำ · พูดถึงเรื่องเดียวเป็นประโยคธรรมดา "
                  "ห้ามทำเป็นรายการหัวข้อ ห้ามใช้ • หรือ - นำหน้าบรรทัด · ห้ามบอกว่าตรวจกี่ข้อ ห้ามโปรยชื่อระบบ · ลิงก์ไม่เกิน 1 ลิงก์ · "
                  "ห้ามชวนนัดคุยหรือเสนอคุยฟรีกี่นาที ปิดด้วยประโยคชวนตอบกลับอีเมลสั้น ๆ ประโยคเดียว")
_LISTY = re.compile(r"(?m)^\s*(?:[•\-*▪●]|\d+[.)])\s+")
_BULKY = re.compile(r"ตรวจ\s*\d+\s*ข้อ|\d+\s*ข้อที่ทำให้|ฟรี\s*\d+\s*นาที|\d+\s*นาที|ทัก LINE", re.I)


def llm_writes_outreach() -> bool:
    return _get("outreach_llm", "0") == "1"


def signer() -> str:
    """ชื่อคนที่ลงท้ายอีเมลทัก/ติดตาม/ตอบลูกค้า (ตั้งที่การ์ดที่อยู่ส่งอีเมลทัก) — ว่าง = ลงแค่ เจอ.AI"""
    try:
        return (_get("outreach_signer", "") or "").strip()[:40]
    except Exception:                                 # ยังไม่มีฐานข้อมูล (เทสต์แม่แบบล้วน) → ลงแค่ เจอ.AI
        return ""


def signature() -> str:
    s = signer()
    return f"{s}\nเจอ.AI" if s else "เจอ.AI"


def personal_problems(body: str) -> str | None:
    """อีเมลนี้ดูเป็นอีเมลส่งหมู่ไหม (เหตุผล) · None = ผ่าน"""
    if _LISTY.search(body or ""):
        return "เป็นรายการหัวข้อ"
    if _BULKY.search(body or ""):
        return "มีคำแบบอีเมลส่งหมู่"
    if len(re.findall(r"https?://", body or "")) > 1:
        return "ลิงก์เกิน 1"
    if len(body or "") > 700:
        return "ยาวเกิน"
    return None


def sign(body: str) -> str:
    """ลงชื่อคนท้ายอีเมล (แทนบรรทัด เจอ.AI ท้ายสุดถ้ามี)"""
    b = re.sub(r"\n+\s*(?:ขอบคุณครับ\s*\n)?\s*เจอ\.?AI\s*$", "", (body or "").rstrip())
    return b + "\n\n" + signature()


def style_for(lead) -> dict:
    """แนวเขียนข้อความทักของลีดรายนี้: แบบที่ชนะแล้วทุกข้อ + แบบ B ของการทดลองปัจจุบัน (ครึ่งหนึ่งของลีด)"""
    keys = list(adopted())
    out = {"exp": None, "arm": None}
    e = active_experiment()
    if e:
        arm = "B" if int(hashlib.sha256(f"{e['key']}:{lead['id']}".encode()).hexdigest(), 16) % 2 else "A"
        out.update(exp=e["key"], arm=arm)
        if arm == "B":
            keys.append(e["key"])
    out["prompt"] = "\n".join(f"- {EXP[k]['prompt']}" for k in keys if k in EXP)
    tpl = {}
    for k in keys:
        if k in EXP:
            tpl.update(EXP[k]["tpl"])
    out["tpl"] = tpl
    return out


def _p_two(x1, n1, x2, n2) -> float:
    if not (n1 and n2):
        return 1.0
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    z = abs(x2 / n2 - x1 / n1) / se
    return math.erfc(z / math.sqrt(2))


def exp_stats(key: str) -> dict:
    arms = {"A": {"n": 0, "engaged": 0, "replied": 0, "unsub": 0}, "B": {"n": 0, "engaged": 0, "replied": 0, "unsub": 0}}
    for lead in db.leads_with_arm(key + ":"):
        arm = (lead["exp_arm"] or ":").split(":")[1]
        if arm not in arms:
            continue
        kinds = {e["kind"] for e in db.list_lead_events(lead["id"], 300)}
        a = arms[arm]
        a["n"] += 1
        a["engaged"] += bool(kinds & {"opened", "replied"})
        a["replied"] += "replied" in kinds
        a["unsub"] += bool(kinds & {"unsubscribed", "complained"})
    for a in arms.values():
        for m in ("engaged", "replied", "unsub"):
            a[m + "_pct"] = round(a[m] / a["n"] * 100, 1) if a["n"] else 0.0
    arms["p"] = round(_p_two(arms["A"]["engaged"], arms["A"]["n"], arms["B"]["engaged"], arms["B"]["n"]), 4)
    return arms


def evaluate_experiment() -> dict | None:
    """ตัดสินการทดลองปัจจุบันเมื่อข้อมูลพอ → ใช้แบบที่ชนะ แล้วเริ่มเรื่องถัดไป"""
    e = active_experiment()
    if not e:
        return None
    s = exp_stats(e["key"])
    A, B = s["A"], s["B"]
    verdict = None
    if min(A["n"], B["n"]) >= MIN_ARM and s["p"] < ALPHA:
        win = "B" if B["engaged_pct"] > A["engaged_pct"] else "A"
        if win == "B" and B["unsub_pct"] - A["unsub_pct"] > UNSUB_MARGIN:
            verdict = "B ได้คนเปิดมากกว่า แต่ขอไม่รับเพิ่มเกินเกณฑ์ — คงแบบเดิม"
            win = "A"
        else:
            verdict = f"แบบ {win} ชนะ ({'ใช้' if win == 'B' else 'คง'}แนวนี้เป็นค่าเริ่มต้น)"
    elif min(A["n"], B["n"]) >= MAX_ARM:
        win, verdict = "A", "ครบจำนวนแล้วยังไม่ต่างกันอย่างมีนัย — คงแบบเดิม"
    if not verdict:
        return {"key": e["key"], "stats": s, "done": False}
    if win == "B":
        ad = adopted()
        if e["key"] not in ad:
            ad.append(e["key"])
            _set_json("deliv_adopted", ad)
    results = _json("deliv_exp_results", [])
    results.append({"key": e["key"], "title": e["title"], "winner": win, "verdict": verdict, "at": db.now(), "stats": s})
    _set_json("deliv_exp_results", results)
    db.set_setting("deliv_exp_idx", str(int(_get("deliv_exp_idx", "0")) + 1))
    try:
        from . import pipeline
        pipeline.alert_admins(f"🧪 ทดลองข้อความทัก \"{e['title']}\" จบแล้ว: {verdict} · เปิด/ตอบ A {A['engaged_pct']}% ({A['n']}) vs B {B['engaged_pct']}% ({B['n']})",
                              "/admin/deliverability", "lead")
    except Exception:
        pass
    return {"key": e["key"], "stats": s, "done": True, "winner": win, "verdict": verdict}


def reset_adopted(key: str | None = None) -> None:
    ad = [k for k in adopted() if key and k != key]
    _set_json("deliv_adopted", ad)


# ---------- สัญญาณ 30 วัน ----------
def metrics(days: int = WINDOW_DAYS, now: dt.datetime | None = None, since_ts: str | None = None) -> dict:
    since = ((now or dt.datetime.now()) - dt.timedelta(days=days)).isoformat(timespec="seconds")
    if since_ts and since_ts > since:
        since = since_ts
    ev = db.events_since(("contacted", "followup", "bounced", "complained", "unsubscribed", "opened", "replied"), since)
    sends = sum(1 for e in ev if e["kind"] in ("contacted", "followup") and "ส่งอีเมลถึง" in (e["note"] or ""))
    cnt = {k: len({e["lead_id"] for e in ev if e["kind"] == k}) for k in ("bounced", "complained", "unsubscribed", "opened", "replied")}
    pct = lambda n: round(n / sends * 100, 1) if sends else 0.0
    return {"days": days, "sends": sends, **cnt, "bounce_pct": pct(cnt["bounced"]), "complaint_pct": pct(cnt["complained"]),
            "unsub_pct": pct(cnt["unsubscribed"]), "open_pct": pct(cnt["opened"]), "reply_pct": pct(cnt["replied"]),
            "delivered": sum(v.get("delivered", 0) for d, v in _json("deliv_counts", {}).items() if d >= since[:10]), "since": since}


def released_at() -> str:
    return _get("deliv_released_at", "") or ""


def guard_metrics(now: dt.datetime | None = None) -> dict:
    """ตัวเลขที่ใช้ตัดสินหยุดส่ง: 30 วันล่าสุด แต่ถ้าเจ้าของปลดการหยุดหลังแก้ต้นเหตุ → นับใหม่ตั้งแต่ปลด (ของเก่าที่แก้แล้วไม่ลากมาหยุดซ้ำ)"""
    return metrics(now=now, since_ts=released_at() or None)


def on_hold() -> bool:
    return bool(_get("deliv_hold"))


def hold_reason() -> str:
    return _get("deliv_hold", "")


def set_hold(reason: str) -> bool:
    if on_hold():
        return False
    db.set_setting("deliv_hold", reason[:300])
    try:
        from . import pipeline
        pipeline.alert_admins(f"⛔ หยุดส่งข้อความทัก/ติดตามผลอัตโนมัติชั่วคราว: {reason} — ข้อเสนอจะรอคุณอนุมัติ ปลดได้ที่หน้าการส่งอีเมล",
                              "/admin/deliverability", "lead", subject="⛔ หยุดส่งอีเมลทักชั่วคราว (กันโดเมนเสียชื่อ)")
    except Exception:
        pass
    return True


def release_hold() -> None:
    db.set_setting("deliv_hold", "")
    db.set_setting("deliv_released_at", db.now())          # นับเด้ง/ร้องเรียน/ขอไม่รับใหม่ตั้งแต่ตอนนี้ (ต้องส่งครบ MIN_SENDS ก่อนตัดสิน)


def guard(m: dict | None = None) -> str | None:
    m = m or guard_metrics()
    if m["sends"] >= MIN_SENDS:
        for key, lim in (("bounce", LIMITS["bounce"]), ("complaint", LIMITS["complaint"]), ("unsub", LIMITS["unsub"])):
            v = m[f"{key}_pct"]
            if v > lim:
                th = {"bounce": "อีเมลเด้ง", "complaint": "ร้องเรียนว่าเป็นสแปม", "unsub": "ขอไม่รับ"}[key]
                r = f"{th} {v}% จาก {m['sends']} ฉบับ" + (f" ตั้งแต่ปลดการหยุด {m['since'][5:16].replace('T', ' ')}" if released_at() and m["since"] == released_at() else f" ใน {m['days']} วัน") + f" (เกณฑ์ {lim}%)"
                set_hold(r)
                return r
    if m["complained"] >= 2:
        r = f"ถูกร้องเรียนว่าเป็นสแปม {m['complained']} ครั้ง" + (" ตั้งแต่ปลดการหยุด" if released_at() and m["since"] == released_at() else f"ใน {m['days']} วัน")
        set_hold(r)
        return r
    last = last_placement()
    if last and last["results"] and all(x["place"] == "spam" for x in last["results"]):
        r = "ทดสอบกล่องล่าสุดตกสแปมทุกกล่อง"
        set_hold(r)
        return r
    return None


def healthy(m: dict) -> tuple[bool, str]:
    if on_hold():
        return False, "หยุดส่งอยู่"
    if m["sends"] < MIN_SENDS:
        return False, f"ส่งยังไม่ถึง {MIN_SENDS} ฉบับ"
    for key, lim in HEALTHY.items():
        if m[f"{key}_pct"] > lim:
            return False, f"{key} {m[f'{key}_pct']}% เกิน {lim}%"
    last = last_placement()
    if not last or not last["results"]:
        return False, "ยังไม่มีผลทดสอบกล่อง"
    if (dt.datetime.now() - dt.datetime.fromisoformat(last["sent_at"][:19])).days > 8:
        return False, "ผลทดสอบกล่องเก่าเกิน 8 วัน"
    if any(x["place"] not in ("inbox", "promotions") for x in last["results"]):
        return False, "ทดสอบกล่องล่าสุดไม่เข้ากล่องหลักทุกกล่อง"
    return True, "สุขภาพดี"


def ramp(m: dict, now: dt.datetime | None = None) -> str | None:
    """สุขภาพดีครบ → เพิ่มจำนวนหาลูกค้าต่อวันทีละขั้น (สัปดาห์ละครั้ง)"""
    from . import prospecting
    if _get("deliv_ramp", "1") != "1":
        return None
    now = now or dt.datetime.now()
    last = _get("deliv_ramp_last")
    if last and (now.date() - dt.date.fromisoformat(last[:10])).days < RAMP_EVERY_DAYS:
        return None
    ok, why = healthy(m)
    if not ok:
        return None
    cur = prospecting.daily_limit()
    if cur >= prospecting.DAILY_MAX:
        return None
    new = min(prospecting.DAILY_MAX, cur + RAMP_STEP)
    db.set_setting("prospect_daily", str(new))
    db.set_setting("deliv_ramp_last", now.date().isoformat())
    try:
        from . import pipeline
        pipeline.alert_admins(f"📈 อีเมลสุขภาพดี (เด้ง {m['bounce_pct']}% · ขอไม่รับ {m['unsub_pct']}% · ทดสอบเข้ากล่องหลัก) — เพิ่มหาลูกค้าจาก {cur} เป็น {new} รายต่อวัน",
                              "/admin/deliverability", "lead")
    except Exception:
        pass
    return f"{cur}→{new}"


# ---------- ทดสอบกล่องจริง (seed) ----------
def seeds() -> list[dict]:
    return _json("deliv_seeds", [])


def public_seeds() -> list[dict]:
    return [{"id": s["id"], "label": s["label"], "host": s["host"], "user": s["user"]} for s in seeds()]


def save_seed(label: str, host: str, user: str, pw: str) -> str:
    from . import wp_client
    user, host = (user or "").strip(), (host or "").strip() or "imap.gmail.com"
    if "@" not in user or not pw.strip():
        return "ใส่อีเมลและรหัสผ่านแอป (App Password) ของกล่องทดสอบ"
    ss = [s for s in seeds() if s["user"].lower() != user.lower()]
    ss.append({"id": secrets.token_hex(4), "label": (label or user).strip()[:40], "host": host[:80], "user": user[:120],
               "pass": wp_client.encrypt(pw.strip())})
    _set_json("deliv_seeds", ss[-5:])
    return ""


def remove_seed(sid: str) -> None:
    _set_json("deliv_seeds", [s for s in seeds() if s["id"] != sid])


def _sample() -> tuple[str, str]:
    """อีเมลทดสอบหน้าตาแบบอีเมลทักจริงล่าสุด (ตัวกรองตัดสินจากเนื้อหาแบบเดียวกัน)"""
    for d in db.list_decisions(None, 200):
        if d["kind"] == "outreach":
            try:
                pl = json.loads(d["payload_json"] or "{}")
            except Exception:
                pl = {}
            if pl.get("subject") and pl.get("body"):
                return pl["subject"], pl["body"]
    return ("เรื่องเว็บของคุณ",
            "สวัสดีครับ\n\nผมลองตรวจเว็บของคุณแล้วเจอว่าบอทของ Google และ AI อ่านเนื้อหาบางหน้าไม่ได้ ทำให้เว็บไม่ขึ้นในคำตอบของ ChatGPT\n\n"
            "รายงานเต็ม: https://geo.appreview.cloud/audit\n\nถ้าอยากรู้วิธีแก้ ตอบกลับอีเมลนี้ได้เลยครับ\n\n" + signature())


def send_placement_test() -> dict:
    from . import mailer
    ss = seeds()
    if not ss:
        return {"error": "ยังไม่มีกล่องทดสอบ"}
    if not mailer.config():
        return {"error": "ยังไม่ได้ตั้งค่า SMTP"}
    subj, body = _sample()
    test = {"id": secrets.token_hex(4), "sent_at": db.now(), "subject": subj[:120], "results": []}
    for s in ss:
        token = "JT" + secrets.token_hex(3).upper()
        r = {"seed": s["id"], "label": s["label"], "token": token, "place": "pending", "checked_at": None}
        try:
            fa, fn = active_from()
            mailer.send_plain(s["user"], f"{subj} [{token}]", body, "", fa, fn)        # หน้าตาและผู้ส่งเดียวกับอีเมลทักจริง
        except Exception as e:
            r.update(place="error", detail=f"ส่งไม่ได้ ({type(e).__name__})")
        test["results"].append(r)
    tests = _json("deliv_tests", [])
    tests.append(test)
    _set_json("deliv_tests", tests[-12:])
    return test


def _folders(M) -> tuple[list[str], list[str]]:
    typ, data = M.list()
    inbox, spam = ["INBOX"], []
    for raw in data or []:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        m = re.match(r'\((?P<flags>[^)]*)\)\s+"?(?P<d>[^"\s]*)"?\s+(?P<name>.+)$', line)
        if not m:
            continue
        name = m.group("name").strip().strip('"')
        if "\\Junk" in m.group("flags") or re.search(r"(spam|junk|bulk)", name, re.I):
            spam.append(name)
    return inbox, spam


def _imap_find(seed: dict, token: str) -> str:
    if HOOKS["imap"]:
        return HOOKS["imap"](seed, token)
    from . import wp_client
    M = imaplib.IMAP4_SSL(seed["host"], 993, timeout=20)
    try:
        M.login(seed["user"], wp_client.decrypt(seed["pass"]))
        inbox, spam = _folders(M)
        gmail = "gmail" in seed["host"]
        for folder in inbox + spam:
            if M.select(f'"{folder}"', readonly=True)[0] != "OK":
                continue
            typ, ids = M.search(None, "SUBJECT", token)
            if typ == "OK" and ids and ids[0]:
                if folder in spam:
                    return "spam"
                if gmail:
                    typ, p = M.search(None, "X-GM-RAW", f'"category:promotions subject:{token}"')
                    if typ == "OK" and p and p[0]:
                        return "promotions"
                return "inbox"
        return "missing"
    finally:
        try:
            M.logout()
        except Exception:
            pass


def check_placement(now: dt.datetime | None = None) -> int:
    """ตรวจผลทดสอบที่ยังรอ (ส่งไปแล้ว ≥ 3 นาที) · ไม่เจอเกิน 60 นาที = ไม่ถึง"""
    now = now or dt.datetime.now()
    tests, changed = _json("deliv_tests", []), 0
    by_id = {s["id"]: s for s in seeds()}
    for t in tests:
        age = (now - dt.datetime.fromisoformat(t["sent_at"][:19])).total_seconds() / 60
        if age < 3:
            continue
        for r in t["results"]:
            if r["place"] != "pending" or r["seed"] not in by_id:
                continue
            try:
                place = _imap_find(by_id[r["seed"]], r["token"])
            except Exception as e:
                r.update(place="error", detail=f"เข้ากล่องทดสอบไม่ได้ ({type(e).__name__})", checked_at=db.now())
                changed += 1
                continue
            if place == "missing" and age < 60:
                continue
            r.update(place=place, checked_at=db.now())
            changed += 1
    if changed:
        _set_json("deliv_tests", tests)
    return changed


def last_placement() -> dict | None:
    done = [t for t in _json("deliv_tests", []) if t["results"] and all(r["place"] != "pending" for r in t["results"])]
    return done[-1] if done else None


# ---------- ที่อยู่ส่งอีเมลทักแยกจากอีเมลระบบ ----------
EMAIL_ADDR = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def outreach_sender() -> dict:
    addr = (_get("outreach_from", "") or "").lower()
    return {"addr": addr, "name": _get("outreach_from_name", ""), "ok": bool(addr) and _get("outreach_from_ok", "0") == "1",
            "checked_at": _get("outreach_from_checked", ""), "sent_check": _get("outreach_route_sent", "")}


def active_from() -> tuple[str, str]:
    """ที่อยู่/ชื่อผู้ส่งของอีเมลทัก — ใช้เฉพาะเมื่อตรวจแล้วว่าอีเมลตอบกลับถึงระบบ ไม่งั้นใช้ที่อยู่ระบบเดิม ('', '')"""
    s = outreach_sender()
    return (s["addr"], s["name"]) if s["ok"] else ("", "")


def save_outreach_sender(addr: str, name: str, signer_name: str | None = None, llm_on: bool | None = None) -> str:
    if signer_name is not None:
        db.set_setting("outreach_signer", signer_name.strip()[:40])
    if llm_on is not None:
        db.set_setting("outreach_llm", "1" if llm_on else "0")
    addr, name = (addr or "").strip().lower(), (name or "").strip()[:60]
    if not addr:
        for k in ("outreach_from", "outreach_from_name", "outreach_from_ok", "outreach_route_token"):
            db.set_setting(k, "")
        return ""
    if not EMAIL_ADDR.match(addr):
        return "ที่อยู่อีเมลไม่ถูกต้อง"
    dom = sender_domain()
    if dom and addr.split("@")[1] != dom:
        return f"ต้องเป็นที่อยู่บนโดเมน {dom} (โดเมนที่ยืนยันกับ Resend แล้ว) ไม่งั้นอีเมลจะไม่ผ่าน SPF/DKIM"
    from . import mailer
    if addr == ((mailer.config() or {}).get("smtp_from") or "").lower():
        return "ต้องเป็นคนละที่อยู่กับที่อยู่ระบบ (อีเมลแจ้งเตือน/สรุปเช้า)"
    changed = addr != (_get("outreach_from", "") or "").lower()
    db.set_setting("outreach_from", addr)
    db.set_setting("outreach_from_name", name)
    if changed:
        db.set_setting("outreach_from_ok", "0")
        db.set_setting("outreach_route_token", "")
    return ""


def send_route_check() -> str:
    """ส่งอีเมลจากที่อยู่ระบบไปหาที่อยู่ส่งอีเมลทัก — ถ้า Cloudflare ส่งต่อเข้าระบบได้ ถือว่าลีดตอบกลับมาแล้วไม่หาย"""
    from . import mailer
    s = outreach_sender()
    if not s["addr"]:
        return "ยังไม่ได้ตั้งที่อยู่ส่งอีเมลทัก"
    if not mailer.config():
        return "ยังไม่ได้ตั้งค่า SMTP"
    tok = "JR" + secrets.token_hex(4).upper()
    db.set_setting("outreach_route_token", tok)
    db.set_setting("outreach_route_sent", db.now())
    try:
        mailer.send(s["addr"], f"ตรวจเส้นทางอีเมลตอบกลับ {tok}", f"<p>อีเมลนี้ระบบส่งเพื่อตรวจว่าอีเมลถึง {s['addr']} เข้าระบบเจอ.AI ได้ ไม่ต้องตอบ</p>",
                    text=f"อีเมลนี้ระบบส่งเพื่อตรวจว่าอีเมลถึง {s['addr']} เข้าระบบเจอ.AI ได้ ไม่ต้องตอบ ({tok})")
    except Exception as e:
        return f"ส่งอีเมลตรวจไม่ได้ ({type(e).__name__})"
    return f"ส่งอีเมลตรวจไปที่ {s['addr']} แล้ว — ถ้าตั้ง Cloudflare ถูก ระบบจะยืนยันเองภายในไม่กี่วินาที กดรีเฟรชหน้านี้"


def route_check(m: dict) -> bool:
    """เรียกจาก inbox.process: อีเมลตรวจเส้นทางมาถึง → เปิดใช้ที่อยู่ส่งอีเมลทัก"""
    tok = _get("outreach_route_token", "")
    if not tok or tok not in (m.get("subject") or ""):
        return False
    db.set_setting("outreach_from_ok", "1")
    db.set_setting("outreach_from_checked", db.now())
    db.set_setting("outreach_route_token", "")
    return True


# ---------- Resend webhook (เด้ง · ร้องเรียน · ส่งถึง) ----------
def webhook_secret() -> str:
    from . import wp_client
    v = db.get_setting("resend_webhook_secret") or ""
    try:
        return wp_client.decrypt(v) if v else ""
    except Exception:
        return ""


def save_webhook_secret(secret: str) -> str:
    from . import wp_client
    secret = (secret or "").strip()
    if not secret.startswith("whsec_"):
        return "รหัสลับต้องขึ้นต้นด้วย whsec_ (คัดลอกจากหน้า Webhooks ของ Resend)"
    db.set_setting("resend_webhook_secret", wp_client.encrypt(secret))
    return ""


def verify_svix(body: bytes, headers: dict, secret: str | None = None, now: float | None = None) -> bool:
    """ลายเซ็น Svix ที่ Resend ใช้: base64(HMAC-SHA256(key, f"{id}.{timestamp}.{body}")) · อายุไม่เกิน 5 นาที"""
    secret = secret if secret is not None else webhook_secret()
    sid, ts, sig = headers.get("svix-id", ""), headers.get("svix-timestamp", ""), headers.get("svix-signature", "")
    if not (secret and sid and ts and sig):
        return False
    try:
        if abs((now or time.time()) - int(ts)) > 300:
            return False
        key = base64.b64decode(secret.split("_", 1)[1])
    except Exception:
        return False
    want = base64.b64encode(hmac.new(key, f"{sid}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    return any(hmac.compare_digest(want, p.split(",", 1)[1]) for p in sig.split() if "," in p)


def sign_svix(body: bytes, secret: str, sid: str = "msg_test", ts: int | None = None) -> dict:
    ts = ts or int(time.time())
    key = base64.b64decode(secret.split("_", 1)[1])
    sig = base64.b64encode(hmac.new(key, f"{sid}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    return {"svix-id": sid, "svix-timestamp": str(ts), "svix-signature": f"v1,{sig}"}


def handle_resend(evt: dict) -> str:
    from . import pipeline
    typ = evt.get("type") or ""
    data = evt.get("data") or {}
    to = data.get("to") or []
    to = [to] if isinstance(to, str) else to
    day = dt.date.today().isoformat()
    if typ in ("email.delivered", "email.delivery_delayed", "email.sent"):
        c = _json("deliv_counts", {})
        k = typ.split(".", 1)[1]
        c.setdefault(day, {})[k] = c.get(day, {}).get(k, 0) + 1
        _set_json("deliv_counts", {d: v for d, v in c.items() if d >= (dt.date.today() - dt.timedelta(days=60)).isoformat()})
        return k
    if typ not in ("email.bounced", "email.complained"):
        return "ignored"
    out = []
    for addr in to:
        lead = db.get_lead_by_email(addr)
        if not lead:
            out.append("no-lead")
            continue
        if typ == "email.bounced":
            b = data.get("bounce") or {}
            why = " ".join(str(x) for x in (b.get("type"), b.get("subType") or b.get("subtype"), b.get("message")) if x)[:200]
            db.add_lead_event(lead["id"], "bounced", f"{addr} เด้ง (Resend){' · ' + why if why else ''} — ล้างอีเมลแล้ว เติมช่องทางอื่นที่หน้าลีด", "deliv")
            db.update_lead(lead["id"], email=None)
            pipeline._cancel_pending(lead["id"], {"outreach", "followup"}, "อีเมลเด้ง")
            out.append("bounced")
        else:
            db.add_lead_event(lead["id"], "complained", f"{addr} กดว่าอีเมลเราเป็นสแปม (Resend) — ไม่ติดต่ออีก", "deliv")
            if lead["status"] != "unsubscribed":
                pipeline.unsubscribe(lead, actor="deliv (ร้องเรียนสแปม)")
            out.append("complained")
    if "complained" in out or "bounced" in out:
        guard()
    return ",".join(out) or "no-recipient"


# ---------- รอบประจำวัน ----------
@office.track("deliv", "ส่งอีเมล: ตรวจโดเมน · ทดสอบกล่อง · ทดลองข้อความ")
def run(now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now()
    res = {"at": db.now()}
    try:
        res["dns_ok"] = check_dns()["ok"]
    except Exception as e:
        res["dns_ok"] = f"error {type(e).__name__}"
    res["placement_checked"] = check_placement(now)
    last_sent = (_json("deliv_tests", []) or [{}])[-1].get("sent_at")
    if seeds() and (now.weekday() == 0 or not last_sent or (now - dt.datetime.fromisoformat(last_sent[:19])).days >= 7):
        if not last_sent or last_sent[:10] != now.date().isoformat():
            t = send_placement_test()
            res["placement_sent"] = t.get("error") or len(t.get("results", []))
    m = metrics(now=now)
    res["metrics"] = {k: m[k] for k in ("sends", "bounce_pct", "complaint_pct", "unsub_pct", "open_pct", "reply_pct")}
    res["hold"] = guard() or (hold_reason() or None)                # ตัดสินจากตัวเลขตั้งแต่ปลดการหยุด (ถ้ามี) ไม่ใช่ 30 วันที่โชว์
    res["ramp"] = ramp(m, now)
    ev = evaluate_experiment()
    if ev:
        res["experiment"] = {"key": ev["key"], "done": ev["done"], "A": ev["stats"]["A"]["n"], "B": ev["stats"]["B"]["n"], "p": ev["stats"]["p"]}
    _set_json("deliv_last", res)
    return res


def status_text() -> str | None:
    m = metrics()
    if not m["sends"] and not seeds():
        return None
    t = f"ส่งอีเมล 30 วัน: {m['sends']} ฉบับ · เด้ง {m['bounce_pct']}% · ขอไม่รับ {m['unsub_pct']}% · เปิดรายงาน {m['open_pct']}% · ตอบ {m['reply_pct']}%"
    lp = last_placement()
    if lp:
        t += " · ทดสอบกล่อง: " + ", ".join(f"{r['label']} {PLACE_TH.get(r['place'], r['place'])}" for r in lp["results"])
    e = active_experiment()
    if e:
        s = exp_stats(e["key"])
        t += f" · ทดลอง \"{e['title']}\" A {s['A']['n']} / B {s['B']['n']} ราย"
    if on_hold():
        t = "⛔ หยุดส่งข้อความทักอัตโนมัติ: " + hold_reason() + " · " + t
    return t
