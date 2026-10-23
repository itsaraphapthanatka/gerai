"""Inbox — อ่านอีเมลที่ลีดตอบกลับมาที่ report@appreview.cloud แล้วทำต่อให้:
  สนใจ/ถามราคา → นับเป็น "ตอบกลับ" → CEO ทำใบเสนอราคา (หยุดติดตามผลอัตโนมัติ)
  ถามคำถาม    → นับเป็น "ตอบกลับ" + CEO ร่างคำตอบเป็นข้อเสนอให้เจ้าของอนุมัติก่อนส่ง
  ไม่สนใจ      → ปิดลีด ยกเลิกข้อความที่ค้าง · ขอไม่รับ → บันทึกขอไม่รับ (ไม่ติดต่ออีก)
  ตอบกลับอัตโนมัติ (out of office) → ไม่นับ · อีเมลเด้ง → จดว่าอีเมลใช้ไม่ได้
ทางเข้า 2 ทาง: (1) IMAP กล่องที่ Cloudflare ส่งต่อไปถึง (อ่านเฉพาะฉบับที่ส่งถึงที่อยู่ผู้ส่งของเรา · BODY.PEEK ไม่เปลี่ยนสถานะอ่าน)
              (2) Cloudflare Email Worker POST อีเมลดิบมาที่ /inbox/hook (มีรหัสลับ)
อีเมลที่ไม่ตรงกับลีดรายไหน ไม่เก็บเนื้อหา"""
from __future__ import annotations
import email
import email.policy
import hashlib
import imaplib
import json
import re
from email.utils import parseaddr

from . import db, leads as L, office, wp_client

CATS = {"interested": "สนใจ/ถามราคา", "question": "ถามคำถาม", "not_interested": "ไม่สนใจ", "unsubscribe": "ขอไม่รับ",
        "auto": "ตอบกลับอัตโนมัติ", "bounce": "อีเมลเด้ง", "other": "อื่น ๆ"}
AUTO_RE = re.compile(r"(out of office|automatic reply|auto.?reply|autoreply|ตอบกลับอัตโนมัติ|ไม่อยู่ที่สำนักงาน|vacation|ลาพักร้อน)", re.I)
BOUNCE_FROM = re.compile(r"(mailer-daemon|postmaster|mail delivery)", re.I)
UNSUB_RE = re.compile(r"(unsubscribe|ไม่รับ|เลิกส่ง|หยุดส่ง|อย่าส่ง|remove me|ลบออก|stop)", re.I)
NO_RE = re.compile(r"(ไม่สนใจ|ไม่ต้องการ|ไม่เอา|not interested|no thanks|no thank you|ไม่ใช่ตอนนี้|มีแล้ว|มีเจ้าประจำ)", re.I)
YES_RE = re.compile(r"(สนใจ|ราคา|ใบเสนอ|ค่าบริการ|เท่าไหร่|เท่าไร|โทร|นัด|คุย|ติดต่อกลับ|interested|quote|price|call me)", re.I)
TOKEN_RE = re.compile(r"/(?:audit|u|q)/([A-Za-z0-9_\-]{8,})")
FETCH_LIMIT = 50
# อีเมลฟรี — ไม่รู้ว่าเป็นธุรกิจไหน จึงไม่สร้างลีดใหม่จากที่อยู่พวกนี้ (ถ้าตรงกับลีดเดิมยังจับคู่ได้ตามปกติ)
FREE_MAIL = {"gmail.com", "googlemail.com", "hotmail.com", "hotmail.co.th", "outlook.com", "outlook.co.th", "live.com", "msn.com", "yahoo.com",
             "yahoo.co.th", "ymail.com", "icloud.com", "me.com", "mac.com", "proton.me", "protonmail.com", "aol.com", "gmx.com", "zoho.com",
             "windowslive.com", "mail.com", "qq.com", "163.com"}
OWN_DOMAINS = {"appreview.cloud", "geo.appreview.cloud"}
MACHINE_LOCAL = re.compile(r"^(no-?reply|do-?not-?reply|donotreply|notifications?|notify|alerts?|newsletter|news|marketing|mailer|bounce|billing|invoice|receipts?|system|automated|support-noreply)([.+_-]|$)", re.I)
LLM = None


# ---------- อ่านอีเมล ----------
def _text(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                try:
                    return part.get_content()
                except Exception:
                    pass
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    return re.sub(r"<[^>]+>", " ", part.get_content())
                except Exception:
                    pass
        return ""
    try:
        c = msg.get_content()
    except Exception:
        return ""
    return re.sub(r"<[^>]+>", " ", c) if msg.get_content_type() == "text/html" else c


def strip_quoted(text: str) -> str:
    """ตัดส่วนที่อ้างอีเมลเดิมออก เหลือเฉพาะที่ลูกค้าพิมพ์ใหม่"""
    out = []
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith(">") or re.match(r"^(On .+wrote:|เมื่อ .+เขียนว่า|.+ เขียนว่า:|-----Original Message-----|From: .+|จาก: .+|Sent from my )", s):
            break
        out.append(line)
    return "\n".join(out).strip()


def parse_raw(raw: bytes) -> dict:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    full = _text(msg) or ""
    name, addr = parseaddr(str(msg.get("From") or ""))
    return {"from": addr.lower(), "name": (name or "").strip()[:120], "to": " ".join(str(msg.get(h) or "") for h in ("To", "Delivered-To", "X-Forwarded-To", "Cc")).lower(),
            "subject": str(msg.get("Subject") or "")[:300], "message_id": str(msg.get("Message-ID") or "").strip()[:300],
            "auto": bool(msg.get("Auto-Submitted") and str(msg.get("Auto-Submitted")).lower() != "no") or bool(msg.get("X-Autoreply") or msg.get("X-Autorespond")),
            "bulk": bool(msg.get("List-Id") or msg.get("List-Unsubscribe") or str(msg.get("Precedence") or "").lower() in ("bulk", "list", "junk")),
            "body": strip_quoted(full)[:4000], "full": full[:20000]}


# ---------- จับคู่ลีด + แยกประเภท ----------
def match_lead(m: dict):
    sender = m["from"]
    for l in db.list_leads(None, 5000):
        if l["email"] and l["email"].lower() == sender:
            return l
    tok = TOKEN_RE.search(m.get("full") or "")
    if tok:
        l = db.get_lead_by_token(tok.group(1))
        if l:
            return l
    dom = sender.split("@")[-1]
    hits = [l for l in db.list_leads(None, 5000) if dom and (l["domain"] == dom or dom.endswith("." + l["domain"]))]
    return hits[0] if len(hits) == 1 else None


def classify(m: dict, llm=None) -> str:
    body, subj = m.get("body") or "", m.get("subject") or ""
    if BOUNCE_FROM.search(m["from"]) or re.search(r"(undeliverable|delivery status notification|returned mail|ส่งไม่ถึง)", subj, re.I):
        return "bounce"
    if m.get("auto") or AUTO_RE.search(subj) or AUTO_RE.search(body[:300]):
        return "auto"
    if UNSUB_RE.search(body[:400]):
        return "unsubscribe"
    if NO_RE.search(body[:400]):
        return "not_interested"
    if YES_RE.search(body[:600]):
        return "interested"
    llm = llm or LLM
    try:
        if llm is None:
            from . import ai_client
            llm = lambda msgs: ai_client.chat_for("sales", msgs, max_tokens=800, json_mode=True)
        raw = llm([{"role": "user", "content": "ลูกค้าตอบอีเมลขายบริการทำเว็บให้โผล่ใน Google/AI ว่า:\n" + body[:1500]
                    + '\nจัดประเภท: interested (สนใจ/อยากคุย) · question (ถามข้อมูล) · not_interested · unsubscribe · other\nตอบ JSON {"category": "..."}'}])
        mm = re.search(r"\{[\s\S]*\}", raw or "")
        cat = (json.loads(mm.group(0)) if mm else {}).get("category")
        if cat in ("interested", "question", "not_interested", "unsubscribe", "other"):
            return cat
    except Exception:
        pass
    return "question" if "?" in body or "ไหม" in body or "มั้ย" in body else "other"


def draft_answer(lead, body: str, llm=None) -> str:
    from . import pipeline
    llm = llm or LLM
    base = (f"สวัสดีครับ ขอบคุณที่ตอบกลับมาครับ\n\nเรื่องที่ถามมา ผมขอสรุปให้ชัด ๆ ทางโทรศัพท์ 10 นาทีได้ไหมครับ หรือดูรายละเอียดและราคาได้ที่รายงานของเว็บคุณ {pipeline.result_url(lead)}"
            "\n\nเจอ.AI")
    try:
        if llm is None:
            from . import ai_client
            llm = lambda msgs: ai_client.chat_for("sales", msgs, max_tokens=1200, json_mode=True)
        raw = llm([{"role": "user", "content": f"ลูกค้าเว็บ {lead['domain']} ตอบอีเมลขายของเจอ.AI ว่า:\n{body[:1500]}\n\nร่างคำตอบภาษาไทยสุภาพ ไม่เกิน 100 คำ ตอบเฉพาะที่ถาม "
                    "ถ้าไม่รู้ข้อเท็จจริงให้บอกว่าจะเช็กแล้วแจ้งกลับ ห้ามการันตีผล ห้ามแต่งตัวเลข ปิดด้วยชวนคุย 10 นาที ลงชื่อ เจอ.AI\nตอบ JSON {\"body\": \"...\"}"}])
        mm = re.search(r"\{[\s\S]*\}", raw or "")
        b = str((json.loads(mm.group(0)) if mm else {}).get("body") or "").strip()
        from . import execs
        if len(b) >= 40 and not execs.FORBIDDEN.search(b):
            return b
    except Exception:
        pass
    return base


# ---------- ทำต่อ ----------
def _seen(mid: str) -> bool:
    with db.get_conn() as c:
        return bool(c.execute(db.q("SELECT 1 FROM inbox_messages WHERE message_id=?"), (mid,)).fetchone())


def process(m: dict, llm=None) -> dict:
    """อีเมลหนึ่งฉบับ → จับคู่ลีด → ประเภท → ทำต่อ · คืน {category, action, lead_id}"""
    from . import pipeline
    mid = m.get("message_id") or hashlib.sha1((m["from"] + m.get("subject", "") + (m.get("body") or "")[:200]).encode()).hexdigest()
    if _seen(mid):
        return {"skip": "seen"}
    lead = match_lead(m)
    created = False
    if not lead and not BOUNCE_FROM.search(m["from"]):
        lead = new_lead_from_email(m, llm)
        created = bool(lead)
    cat = classify(m, llm) if lead or BOUNCE_FROM.search(m["from"]) else "unmatched"
    action = "ลีดใหม่จากอีเมล · " if created else ""
    if cat == "unmatched":
        action = "ไม่ตรงกับลีดรายไหน — ไม่เก็บเนื้อหา"
    elif created and cat in ("auto", "unsubscribe", "not_interested"):
        action += CATS.get(cat, cat)
    elif cat == "bounce":
        tl = lead or next((l for l in db.list_leads(None, 5000) if l["email"] and l["email"] in (m.get("full") or "").lower()), None)
        if tl:
            pipeline.record(tl["id"], "note", f"อีเมล {tl['email']} เด้งกลับ — ใช้ส่งไม่ได้ เติมช่องทางอื่นที่หน้าลีด", "inbox")
            db.update_lead(tl["id"], email=None)
            lead, action = tl, "จดว่าอีเมลเด้ง · ล้างอีเมลออกไม่ให้ส่งซ้ำ"
        else:
            action = "อีเมลเด้งแต่ไม่รู้ว่าของลีดไหน"
    elif cat == "auto":
        action += "ตอบกลับอัตโนมัติ — ไม่นับ"
    elif cat == "unsubscribe":
        pipeline.unsubscribe(lead, actor="customer (อีเมล)")
        action = "บันทึกขอไม่รับการติดต่อ"
    elif cat == "not_interested":
        db.update_lead(lead["id"], status="lost")
        pipeline.record(lead["id"], "lost", f"ตอบอีเมลว่าไม่สนใจ: {(m.get('body') or '')[:200]}", "customer")
        pipeline._cancel_pending(lead["id"], {"outreach", "followup", "lead_review", "quote"}, "ลูกค้าตอบว่าไม่สนใจ")
        action = "ปิดลีดเป็นไม่สนใจ · ยกเลิกข้อความที่ค้าง"
    else:
        did = pipeline.lead_replied(lead, (m.get("body") or "")[:500], via="อีเมล")
        action += ("ทักเข้ามาเอง · ตรวจเว็บให้แล้ว" if created else "นับเป็นตอบกลับ · หยุดติดตามผล") + (" · CEO เสนอใบเสนอราคา" if did else "")
        if cat in ("question", "other"):
            ans = draft_answer(lead, m.get("body") or "", llm)
            rd = db.add_decision("ceo", "reply", f"ตอบอีเมล {lead['domain']}", f"ลูกค้าถาม:\n{(m.get('body') or '')[:800]}\n\nร่างคำตอบ:\n{ans}",
                                 {"subject": "Re: " + (m.get("subject") or "")[:150], "body": ans, "to": m["from"]}, lead_id=lead["id"])
            action += f" · CEO ร่างคำตอบรออนุมัติ (#{rd})"
    keep = cat != "unmatched"
    with db.get_conn() as c:
        c.execute(db.q("INSERT INTO inbox_messages(received_at,message_id,from_addr,subject,body,lead_id,category,action) VALUES(?,?,?,?,?,?,?,?)"),
                  (db.now(), mid, m["from"] if keep else "", (m.get("subject") or "")[:300] if keep else "", (m.get("body") or "")[:4000] if keep else "",
                   lead["id"] if lead else None, cat, action))
    if lead and cat not in ("auto", "unmatched"):
        try:
            pipeline.alert_admins(f"📥 {lead['domain']} ตอบอีเมล ({CATS.get(cat, cat)}): {(m.get('body') or '')[:100]}", f"/admin/leads/{lead['id']}", "lead")
        except Exception:
            pass
    return {"category": cat, "action": action, "lead_id": lead["id"] if lead else None}


def new_lead_from_email(m: dict, llm=None):
    """คนจากโดเมนธุรกิจอีเมลมาเอง (ไม่ใช่ลีดเดิม ไม่ใช่อีเมลฟรี ไม่ใช่ตอบอัตโนมัติ) → สร้างเป็นลีดใหม่ ตรวจเว็บของโดเมนนั้น
    เขาเป็นฝ่ายติดต่อมาก่อน จึงนับว่ายินยอมให้ตอบกลับ · คืนแถวลีด หรือ None"""
    from . import pipeline, prospecting
    sender = m["from"]
    dom = sender.split("@")[-1].lower().strip(".")
    if not dom or dom in FREE_MAIL or dom in OWN_DOMAINS or not prospecting.is_business_site(dom):
        return None
    if m.get("auto") or m.get("bulk") or MACHINE_LOCAL.match(sender.split("@")[0]) or AUTO_RE.search(m.get("subject") or "") or not (m.get("body") or "").strip():
        return None                                     # จดหมายข่าว/แจ้งเตือนจากระบบ ไม่ใช่คนสนใจบริการ
    site = L.normalize_url(dom)
    apex = L.domain_of(site)
    if db.find_lead_by_domain(apex):
        return db.find_lead_by_domain(apex)
    lid = db.add_lead(site, apex, source="email", name=m.get("name") or "", email=sender)
    db.update_lead(lid, consent=1, src="email")
    pipeline.record(lid, "note", f"อีเมลเข้ามาเอง: {(m.get('subject') or '')[:120]}", "customer")

    def audit():
        try:
            pipeline.run_lead_audit(lid, site)
            from . import deep_audit
            deep_audit.start(lid)
            lead = db.get_lead(lid)
            from . import quotes as Q
            if lead["status"] == "replied" and not any(q["status"] in Q.OPEN for q in db.list_quotes(lid)):
                pipeline.propose_quote(lead)                # ตรวจเสร็จแล้วค่อยทำใบเสนอราคา (ตอนอีเมลเข้ายังไม่มีผลตรวจ)
        except Exception:
            pass
    pipeline._spawn(audit)
    return db.get_lead(lid)


# ---------- IMAP ----------
def config() -> dict | None:
    host, user, pw = db.get_setting("imap_host") or "", db.get_setting("imap_user") or "", db.get_setting("imap_pass") or ""
    if not (host and user and pw):
        return None
    try:
        pw = wp_client.decrypt(pw)
    except Exception:
        return None
    return {"host": host, "user": user, "pass": pw, "folder": db.get_setting("imap_folder") or "INBOX"}


def save_config(host: str, user: str, pw: str, folder: str) -> None:
    db.set_setting("imap_host", host.strip()[:120])
    db.set_setting("imap_user", user.strip()[:160])
    if pw.strip():
        db.set_setting("imap_pass", wp_client.encrypt(pw.strip()))
    db.set_setting("imap_folder", (folder or "INBOX").strip()[:80])


def our_address() -> str:
    return (db.get_setting("smtp_from") or "report@appreview.cloud").lower()


IMAP = None   # เทสต์ฉีด factory(host) → client


@office.track("hop", "อ่านอีเมลที่ลีดตอบกลับ")
def poll(llm=None) -> dict:
    cfg = config()
    if not cfg:
        return {"skipped": "ยังไม่ได้ตั้งค่ากล่องอีเมล"}
    res = {"checked": 0, "processed": 0, "by": {}}
    cli = (IMAP or (lambda h: imaplib.IMAP4_SSL(h, 993)))(cfg["host"])
    try:
        cli.login(cfg["user"], cfg["pass"])
        cli.select(cfg["folder"], readonly=True)
        addr = our_address()
        typ, data = cli.search(None, f'(TO "{addr}" SINCE {_since()})')
        ids = (data[0].split() if data and data[0] else [])[-FETCH_LIMIT:]
        for i in ids:
            typ, parts = cli.fetch(i, "(BODY.PEEK[])")
            raw = next((p[1] for p in parts if isinstance(p, tuple)), b"")
            if not raw:
                continue
            m = parse_raw(raw)
            res["checked"] += 1
            if addr not in m["to"] or m["from"] == addr:
                continue
            r = process(m, llm)
            if r.get("skip"):
                continue
            res["processed"] += 1
            res["by"][r["category"]] = res["by"].get(r["category"], 0) + 1
    finally:
        try:
            cli.logout()
        except Exception:
            pass
    db.set_setting("inbox_last", json.dumps({"at": db.now(), **res}, ensure_ascii=False))
    return res


def _since() -> str:
    import datetime as dt
    d = dt.date.today() - dt.timedelta(days=14)
    return d.strftime("%d-%b-%Y")


def hook_secret() -> str:
    s = db.get_setting("inbox_hook_secret")
    if not s:
        import secrets
        s = secrets.token_urlsafe(18)
        db.set_setting("inbox_hook_secret", s)
    return s


def status_text() -> str:
    try:
        r = json.loads(db.get_setting("inbox_last") or "null")
    except Exception:
        r = None
    if not r:
        return ""
    by = " · ".join(f"{CATS.get(k, k)} {v}" for k, v in (r.get("by") or {}).items())
    return f"อีเมลตอบกลับ: รอบ {r['at'][:16].replace('T', ' ')} ใหม่ {r.get('processed', 0)}" + (f" ({by})" if by else "")
