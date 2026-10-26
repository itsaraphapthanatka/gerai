"""SDR ติดตามผล — ลีดที่ทักแล้วเงียบ ส่งต่ออีก 3 ครั้ง (วันที่ 3 · 7 · 14 นับจากทักครั้งแรก) แต่ละครั้งมีเรื่องใหม่ ไม่ทวงซ้ำ:
  1) ข้อตกใหม่จากรายงานเต็มที่ยังไม่ได้พูด  2) คู่แข่งที่ติด Google แทน / มุม AI  3) ข้อความปิดแฟ้ม สั้น สุภาพ
หยุดทันทีเมื่อลูกค้าตอบ ขอใบเสนอราคา หรือกดไม่รับ · ห่างจากการติดต่อครั้งก่อน ≥ 2 วันเสมอ
เฉพาะลีดที่เราหาเอง/นำเข้า — คนที่กรอกหน้า /audit เองได้รับสัญญาว่า "ไม่ส่งซ้ำถ้าไม่ตอบ" จึงไม่ติดตาม
ข้อความเป็นข้อเสนอ kind "followup" ของ SDR (ทำเองได้เมื่อเรื่อง "ข้อความทัก" ตัดสินเอง หรือเจ้าของปลดล็อกแยก)"""
from __future__ import annotations
import datetime as dt
import json
import re

from . import db, execs, leads as L, office

STEPS = [(3, "new_issue"), (7, "competitor"), (14, "breakup")]
STEP_TH = {"new_issue": "ข้อตกที่ยังไม่ได้บอก", "competitor": "คู่แข่งที่ติด Google แทน", "breakup": "ข้อความปิดแฟ้ม"}
SOURCES = {"prospect", "import", "manual"}
MIN_GAP_DAYS = 2
MAX_PER_RUN = 40


def enabled() -> bool:
    return (db.get_setting("followup_on") or "1") == "1"


def set_enabled(on: bool) -> None:
    db.set_setting("followup_on", "1" if on else "0")


def _ts(s):
    try:
        return dt.datetime.fromisoformat(str(s)[:19])
    except Exception:
        return None


def history(lead_id: int) -> dict:
    ev = db.list_lead_events(lead_id, 500)
    contacted = sorted(t for t in (_ts(e["created_at"]) for e in ev if e["kind"] == "contacted") if t)
    fups = [e for e in ev if e["kind"] == "followup"]
    replied = any(e["kind"] in ("replied", "unsubscribed", "quote_accepted") for e in ev)
    last_touch = max([t for t in (_ts(e["created_at"]) for e in ev if e["kind"] in ("contacted", "followup")) if t] or [None]) if ev else None
    return {"first": contacted[0] if contacted else None, "n": len(fups), "replied": replied, "last": last_touch}


def eligible(lead) -> str:
    """'' = ติดตามได้ · อื่น ๆ = เหตุผลที่ไม่ติดตาม"""
    from . import pipeline
    if lead["status"] != "contacted":
        return f"สถานะ {L.STATUS_LABEL.get(lead['status'], lead['status'])}"
    if (lead["source"] or "") not in SOURCES:
        return "ลีดกรอกหน้าตรวจฟรีเอง — สัญญาไว้ว่าไม่ส่งซ้ำถ้าไม่ตอบ"
    if lead["unsub_at"]:
        return "ขอไม่รับการติดต่อ"
    if not pipeline.send_channel(lead):
        return "ไม่มีอีเมล/LINE ที่ส่งได้"
    return ""


def next_step(lead, now: dt.datetime | None = None) -> dict | None:
    """ครั้งถัดไปที่ถึงกำหนด (None = ยังไม่ถึง/ครบแล้ว/ไม่ควรติดตาม)"""
    now = now or dt.datetime.now()
    if eligible(lead):
        return None
    h = history(lead["id"])
    if h["replied"] or not h["first"] or h["n"] >= len(STEPS):
        return None
    day, kind = STEPS[h["n"]]
    due_at = max(h["first"] + dt.timedelta(days=day), (h["last"] or h["first"]) + dt.timedelta(days=MIN_GAP_DAYS))
    return {"step": h["n"] + 1, "kind": kind, "day": day, "due_at": due_at.isoformat(timespec="seconds"), "due": now >= due_at}


# ---------- เนื้อหา ----------
def facts(lead) -> dict:
    from . import deep_audit
    report = json.loads(lead["audit_json"] or "{}") if lead["audit_json"] else {}
    summary = L.public_summary(report, limit=6)
    t1 = deep_audit.tier1(lead)
    issues = [i for i in (t1.get("issues") or []) if i["severity"] == "Error"] if t1.get("state") == "done" else []
    rk = (t1.get("ranking") or {}) if t1.get("state") == "done" else {}
    t2 = deep_audit.tier2(lead)
    return {"summary_new": summary[3:], "issues": issues[:3], "health": (t1.get("health") or {}).get("score"),
            "pages": (t1.get("health") or {}).get("urls"), "competitors": rk.get("competitors") or [],
            "keywords": t1.get("keywords") or [], "page1": rk.get("page1"), "n_kw": rk.get("n"),
            "ai": {"mentioned": t2.get("mentioned"), "asked": t2.get("asked")} if t2.get("state") == "done" else None}


def _tl(lang: str):
    from . import i18n
    return lambda x, **kw: i18n.tr(x, lang, **kw)


def _fact_lines(kind: str, f: dict, lang: str = "th") -> list[str]:
    t = _tl(lang)
    if kind == "new_issue":
        out = [t("{label} ({count} จุด)", label=t(i['label']), count=i['count']) for i in f["issues"]]
        out += [f"{t(s['label'])}: {t(s['why'])}" for s in f["summary_new"]]
        if f["health"] is not None:
            out.append(t("คะแนนสุขภาพเว็บ {score}/100 จาก {pages} หน้าที่ไต่", score=f['health'], pages=f['pages']))
        return out[:3]
    if kind == "competitor":
        out = []
        if f["competitors"] and f["n_kw"]:
            c = f["competitors"][0]
            out.append(t("{competitor} ติด 10 อันดับแรกของ Google ใน {hits} จาก {total} คำค้นที่ลูกค้าใช้หา ส่วนเว็บนี้ติดหน้าแรก {n} คำ",
                         competitor=c['domain'], hits=c['hits'], total=f['n_kw'], n=f['page1']))
        if f["keywords"]:
            out.append(t("คำค้นที่ตรวจ เช่น \"{keyword}\"", keyword=f['keywords'][0]))
        if f["ai"]:
            out.append(t("ถาม ChatGPT {asked} คำถามที่ลูกค้าถาม เว็บนี้ถูกเอ่ยถึง {mentioned} ครั้ง", asked=f['ai']['asked'], mentioned=f['ai']['mentioned']))
        return out[:3]
    return []


def template(lead, kind: str, f: dict, link: str) -> dict:
    """แบบคนเขียน (เหมือนอีเมลทัก): เรื่องเดียวเป็นประโยค + ลิงก์รายงาน + ลงชื่อคน — ไม่มีรายการหัวข้อ ไม่ชวนนัดกี่นาที
    · ภาษาตามที่ลีดเลือกบนเว็บ (ไทย = ข้อความเดิมทุกตัวอักษร)"""
    from . import deliverability as D
    lang = db.row_lang(lead)
    t = _tl(lang)
    dom = lead["domain"]
    lines = _fact_lines(kind, f, lang)
    first = lines[0] if lines else ""
    hi = t("สวัสดีครับ")
    if kind == "new_issue":
        body = (hi + "\n\n" + t("ต่อจากผลตรวจที่ส่งไปเมื่อไม่กี่วันก่อน ผมไต่เว็บ {dom} ทั้งเว็บแล้วเจอเพิ่มอีกเรื่อง", dom=dom)
                + (t(" คือ{fact}", fact=first) if first else t(" ที่ทำให้ Google และ AI ข้ามบางหน้าไป")) + "\n\n"
                + t("รายละเอียดกับวิธีแก้อยู่ในรายงาน: {url}", url=link) + "\n\n" + t("ถ้าอยากให้ช่วยดูว่าควรแก้อะไรก่อน ตอบกลับได้เลยครับ"))
        subj = t("เจอเพิ่มอีกเรื่องบนเว็บ {dom}", dom=dom)
    elif kind == "competitor":
        body = (hi + "\n\n" + t("อีกมุมจากรายงานของเว็บ {dom}: ตอนลูกค้าค้นหาบริการแบบของคุณ ", dom=dom)
                + (f"{first}" if first else t("มีเว็บอื่นขึ้นมาแทน")) + "\n\n" + t("ดูได้ในรายงาน: {url}", url=link) + "\n\n"
                + t("ถ้าอยากรู้ว่าต้องทำอะไรถึงจะขึ้นไปแทนได้ ตอบกลับได้เลยครับ"))
        subj = t("ใครขึ้น Google แทน {dom} ตอนลูกค้าค้นหา", dom=dom)
    else:
        body = (hi + "\n\n" + t("ผมส่งผลตรวจเว็บ {dom} ไปสองสามครั้งแล้ว ไม่อยากรบกวนเกินไป ฉบับนี้จะเป็นฉบับสุดท้ายครับ", dom=dom) + "\n\n"
                + t("รายงานยังเปิดดูได้ที่ {url} ถ้าวันไหนพร้อม ตอบกลับมาคำเดียวว่า \"สนใจ\" ผมจะส่งแผนแก้และราคาให้", url=link) + "\n\n" + t("ขอบคุณครับ"))
        subj = t("ปิดแฟ้มผลตรวจเว็บ {dom}", dom=dom)
    return {"subject": subj, "body": D.sign(body), "source": "template"}


def draft(lead, kind: str, f: dict, link: str, llm=None) -> dict:
    lang = db.row_lang(lead)
    lines = _fact_lines(kind, f, lang)
    if kind != "breakup" and not lines:
        out = template(lead, kind, f, link)
        out["source"] = "template (ไม่มีข้อมูลใหม่)"
        return out
    from . import deliverability as _D
    if llm is None:
        from . import ai_client
        llm = lambda m: ai_client.chat_for("followup", m, max_tokens=700, json_mode=True)
    goal = {"new_issue": "บอกข้อตกใหม่ที่ยังไม่ได้พูดในอีเมลแรก", "competitor": "ชี้ว่าใครติด Google/AI แทน เพื่อให้เห็นสิ่งที่เสียไป",
            "breakup": "ข้อความสุดท้าย บอกว่าจะไม่ส่งอีก ทิ้งลิงก์ไว้ เปิดทางให้ตอบคำเดียว"}[kind]
    prompt = (f"เขียนอีเมลติดตามผลถึงเจ้าของเว็บ {lead['domain']} (ส่งผลตรวจเว็บไปแล้วแต่ยังไม่ตอบ) ภาษาไทย สุภาพ ไม่เกิน 90 คำ\n"
              f"เป้าหมายฉบับนี้: {goal}\n"
              + (f"ข้อมูลจริงที่อ้างได้ (ห้ามเพิ่มตัวเลขเอง):\n" + "\n".join(f"- {x}" for x in lines) + "\n" if lines else "")
              + f"ลิงก์รายงาน (ต้องใส่): {link}\n"
              "กติกา: ห้ามขึ้นต้นด้วย 'ติดตาม' หรือทวงว่าทำไมไม่ตอบ · ห้ามคำว่าการันตี/รับประกันผล · ห้ามบอกราคา · ห้ามสร้างความเร่งด่วนปลอม\n"
              f"รูปแบบ (สำคัญมาก ไม่งั้นตกแท็บโปรโมชัน): {_D.PERSONAL_RULES} · ไม่ต้องลงชื่อ ระบบลงชื่อให้\n"
              'ตอบ JSON เท่านั้น: {"subject": "...", "body": "... ขึ้นบรรทัดใหม่ด้วย \\n"}') + _D.write_in(lang)
    try:
        raw = llm([{"role": "user", "content": prompt}])
        m = re.search(r"\{[\s\S]*\}", raw or "")
        d = json.loads(m.group(0)) if m else {}
        subject, body = str(d.get("subject") or "").strip(), str(d.get("body") or "").strip()
        if not subject or len(body) < 60:
            raise ValueError("คำตอบสั้นเกิน")
        if execs.FORBIDDEN.search(subject + " " + body):
            raise ValueError("มีคำต้องห้าม")
        nums = set(re.findall(r"\d+", body)) - set(re.findall(r"\d+", " ".join(lines) + " " + link + " " + lead["domain"]))
        if nums - {"15", "1", "2", "3"}:
            raise ValueError("มีตัวเลขที่ไม่ได้มาจากข้อมูลจริง")
        if _D.wrong_language(subject + " " + body, lang):
            raise ValueError("ไม่ได้เขียนเป็นภาษาของลีด")
        if link not in body:
            body += "\n\n" + _tl(lang)("รายงาน:") + f" {link}"
        why = _D.personal_problems(body)
        if why:
            raise ValueError(f"ไม่ใช่แบบคนเขียน ({why})")
        return {"subject": subject[:120], "body": _D.sign(body)[:1500], "source": "llm"}
    except Exception as e:
        out = template(lead, kind, f, link)
        out["source"] = f"template ({type(e).__name__})"
        return out


# ---------- รอบรายวัน ----------
def propose(lead, step: dict, llm=None) -> int | None:
    from . import pipeline
    anchor = {"new_issue": "#audit", "competitor": "#ranking", "breakup": ""}[step["kind"]]
    link = pipeline.result_url(lead, "mail") + anchor
    from . import deep_audit
    if deep_audit.enabled() and deep_audit.tier1(lead).get("state") != "done":
        deep_audit.run_tier1(lead["id"])               # ลีดรุ่นก่อนรายงานเต็ม: ทำก่อน ข้อความจะได้มีข้อมูลใหม่ให้เขา (~30 วิ · Serper ≤ 10 เครดิต)
        lead = db.get_lead(lead["id"])
    f = facts(lead)
    d = draft(lead, step["kind"], f, link, llm=llm)
    pl = {"step": step["step"], "kind": step["kind"], "subject": d["subject"], "body": d["body"], "source": d["source"],
          "channel": pipeline.send_channel(lead)}
    proposal = (f"ติดตามครั้งที่ {step['step']}/3 ({STEP_TH[step['kind']]}) · วันที่ {step['day']} หลังทักครั้งแรก · ส่งทาง {pl['channel']}\n\n"
                f"หัวข้อ: {pl['subject']}\n\n{pl['body']}")
    did = db.add_decision("sdr", "followup", f"ติดตาม {lead['domain']} ครั้งที่ {step['step']}", proposal, pl, lead_id=lead["id"])
    return pipeline._maybe_auto(did)


@office.track("sdr", "ติดตามผลลีดที่ทักแล้วเงียบ")
def run(now: dt.datetime | None = None, llm=None) -> dict:
    if not enabled():
        res = {"skipped": "ปิดอยู่ (เปิดได้ที่ตั้งค่า → หาลูกค้าอัตโนมัติ)", "at": db.now()}
        db.set_setting("followup_last", json.dumps(res, ensure_ascii=False))
        return res
    now = now or dt.datetime.now()
    pending = {d["lead_id"] for d in db.list_decisions("proposed", 1000) if d["kind"] in ("followup", "outreach", "quote")}
    res = {"at": db.now(), "checked": 0, "proposed": 0, "sent": 0, "by_step": {}, "waiting": 0, "errors": 0}
    for lead in db.list_leads("contacted", 5000):
        res["checked"] += 1
        if lead["id"] in pending:
            continue
        st = next_step(lead, now)
        if not st:
            continue
        if not st["due"]:
            res["waiting"] += 1
            continue
        if res["proposed"] >= MAX_PER_RUN:
            break
        try:
            did = propose(lead, st, llm=llm)
            res["proposed"] += 1
            res["by_step"][str(st["step"])] = res["by_step"].get(str(st["step"]), 0) + 1
            if did and db.get_decision(did)["status"] == "auto":
                res["sent"] += 1
        except Exception:
            res["errors"] += 1
    db.set_setting("followup_last", json.dumps(res, ensure_ascii=False))
    return res


def last_run() -> dict | None:
    try:
        return json.loads(db.get_setting("followup_last") or "null")
    except Exception:
        return None


def status_text() -> str:
    r = last_run()
    if not r:
        return ""
    if r.get("skipped"):
        return f"ติดตามผล: {r['skipped']}"
    steps = " ".join(f"ครั้งที่ {k}: {v}" for k, v in sorted(r.get("by_step", {}).items()))
    return (f"ติดตามผล: รอบ {r['at'][:16].replace('T', ' ')} เสนอ {r['proposed']} ราย (ส่งเอง {r['sent']})"
            + (f" · {steps}" if steps else "") + f" · รอถึงกำหนด {r['waiting']}" + (f" · ล้ม {r['errors']}" if r.get("errors") else ""))
