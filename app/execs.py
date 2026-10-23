"""ผู้บริหารเอเจนต์ CEO · CFO · CTO — โหมดเสนอก่อนทำ
ทุกข้อเสนอเป็นแถวในตาราง decisions รอคนกดยืนยัน (เฟส 1) · กรอบอำนาจ (MANDATE) บังคับที่นี่ในโค้ด ไม่ใช่ใน prompt
เพราะเอเจนต์อ่านอีเมล/เว็บลูกค้าตลอดเวลา ข้อความเหล่านั้นสั่งมันได้ถ้าปล่อยให้ตัดสินใจอิสระ"""
from __future__ import annotations
import datetime
import json
import re

from . import leads as L
from . import office

MANDATE_DEFAULT = {
    "max_discount_pct": 10,
    "max_deal_thb": 50000,      # ต่อรายต่อเดือน
    "refund_days": 7,
    "ai_cost_cap_thb": 570,     # ต้นทุน AI/เครื่องมือต่อลูกค้าต่อเดือน (จากแผน)
    "min_margin_pct": 70,
}
MANDATE_LABEL = {"max_discount_pct": "ส่วนลดสูงสุด (%)", "max_deal_thb": "วงเงินต่อดีล (บาท/เดือน)", "refund_days": "คืนเงินเต็มภายใน (วัน)",
                 "ai_cost_cap_thb": "เพดานต้นทุน AI ต่อลูกค้า (บาท/เดือน)", "min_margin_pct": "กำไรขั้นต้นขั้นต่ำ (%)"}


class _Mandate(dict):
    """กรอบอำนาจ: ค่าเริ่มต้นจากแผน แก้ทับได้ที่หน้าทักษะเอเจนต์ (settings mandate_<key>) — อ่านสดทุกครั้งที่ใช้"""
    def __getitem__(self, key):
        if key == "mode":
            return "propose"
        try:
            from . import db
            v = db.get_setting(f"mandate_{key}")
        except Exception:                      # DB ยังไม่พร้อม (เช่น เรียกจากตรรกะล้วนก่อน init_db) → ใช้ค่าเริ่มต้น
            v = None
        if v is not None and str(v).strip() != "":
            try:
                return int(float(v))
            except ValueError:
                pass
        return MANDATE_DEFAULT[key]

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def as_dict(self) -> dict:
        return {k: self[k] for k in MANDATE_DEFAULT}


MANDATE = _Mandate()


def set_mandate(key: str, value) -> None:
    from . import db
    if key not in MANDATE_DEFAULT:
        raise ValueError("ไม่รู้จักกรอบนี้")
    v = int(float(value))
    if v < 0:
        raise ValueError("ค่าต้องไม่ติดลบ")
    db.set_setting(f"mandate_{key}", str(v))
AGENTS = {"ceo": "CEO", "cfo": "CFO", "cto": "CTO", "cmo": "CMO", "sdr": "SDR"}
KINDS = {"lead_review": "คัดลูกค้า", "outreach": "ข้อความทัก", "quote": "ใบเสนอราคา", "payment": "ยืนยันยอดเข้า", "post": "โพสต์การตลาด", "followup": "ติดตามผล", "reply": "ตอบอีเมลลูกค้า", "digest": "สรุปเช้า"}
# สลับเป็น "ตัดสินเองในกรอบ" ได้ทีละเรื่อง เมื่อมีหลักฐานพอ (หรือเจ้าของปลดล็อกเอง) · ใบเสนอราคาอยู่ในกรอบ CFO (ราคาตามตาราง ส่วนลด ≤ 10%)
# ส่วน "ยืนยันยอดเข้า" ต้องเป็นคนเสมอ เพราะระบบมองไม่เห็นบัญชีธนาคาร · ข้อเสนอที่นอกกรอบ (escalate) ไม่ทำเองไม่ว่าโหมดไหน
AUTO_ELIGIBLE = {"lead_review", "outreach", "quote", "post", "followup"}
AUTO_MIN_DECIDED = 20
AUTO_MIN_AGREEMENT = 95
PLAN_START = "2026-10-22"            # วันเริ่มแผน 45 วัน — ใช้นับ "ลูกค้าใหม่ที่จ่ายเงินสะสม"
# ด่านท้ายเฟส (ตัวเลขจากเอกสารแผน) — ผ่านด้วยตัวเลขจริง ไม่ใช่ตามปฏิทิน
GATES = {
    1: {"audits_week": 20, "reply_rate": 10, "close_rate": 20, "new_paying": 1, "agreement": 95, "escalations_week": None},
    2: {"audits_week": 50, "reply_rate": 15, "close_rate": 25, "new_paying": 3, "agreement": 95, "escalations_week": 10},
    3: {"audits_week": 80, "reply_rate": 15, "close_rate": 30, "new_paying": 5, "agreement": None, "escalations_week": 5},
}
GATE_LABEL = {"audits_week": "เว็บที่ตรวจฟรีต่อสัปดาห์", "reply_rate": "ผลตรวจ → คุยต่อ (%)", "close_rate": "คุยต่อ → จ่ายเงิน (%)",
              "new_paying": "ลูกค้าใหม่ที่จ่ายเงินสะสม", "agreement": "ตัดสินใจตรงกับเจ้าของ (%)", "escalations_week": "เรื่องส่งกลับเจ้าของต่อสัปดาห์ (ไม่เกิน)"}
FORBIDDEN = re.compile(r"การันตี|รับประกัน(?:อันดับ|ผล)|อันดับ\s*1\s*แน่นอน|100\s*%", re.I)
MIN_SCORE_TO_PURSUE = 15


def suggest_package(score: int) -> str:
    """เฟส 1 เสนอ Pro ทุกราย — Agency เมื่อรู้ว่าลูกค้าดูแลหลายแบรนด์ ซึ่งผลตรวจบอกไม่ได้"""
    return "pro"


def _plan(name: str) -> dict:
    from . import billing
    return billing.PLANS[name]


def outreach_template(lead, summary: list[dict], result_url: str) -> dict:
    """แม่แบบสำรองเมื่อโมเดลร่างไม่ได้ — ตรงไปตรงมา อ้างเฉพาะข้อที่ตรวจเจอจริง"""
    dom = lead["domain"] if "domain" in lead.keys() else lead.get("domain", "")
    bullets = "\n".join(f"• {s['label']} — {s['why']}" for s in summary[:3]) or \
        "• พื้นฐานผ่านทุกข้อแล้ว ขั้นต่อไปคือทำให้ ChatGPT, Gemini และ Google AI เอ่ยชื่อธุรกิจคุณเวลาลูกค้าถาม ซึ่งต้องมีคอนเทนต์ที่ตอบคำถามจริงของลูกค้า"
    body = (f"สวัสดีครับ ทีม {dom}\n\nผมตรวจเว็บ {dom} ด้วยระบบของเจอ.AI (ตรวจ 16 ข้อที่ทำให้เว็บไม่โผล่ใน Google และคำตอบของ AI) "
            f"พบข้อที่ควรแก้ก่อนดังนี้\n{bullets}\n\nดูผลเต็มได้ที่ {result_url}\n\n"
            "ถ้าสนใจ ผมอธิบายวิธีแก้ให้ฟรี 15 นาที ตอบอีเมลนี้หรือทัก LINE ได้เลยครับ\n\nเจอ.AI")
    return {"subject": f"ผลตรวจเว็บ {dom}: {len(summary)} ข้อที่ขวางไม่ให้ลูกค้าหาเจอ", "body": body, "source": "template"}


def draft_outreach(lead, summary: list[dict], result_url: str, llm=None) -> dict:
    """ให้โมเดลร่างอีเมลทักครั้งแรกจากผลตรวจจริง — คืน {subject, body, source} · ผิดกติกา/ล้ม → แม่แบบ"""
    dom = lead["domain"] if "domain" in lead.keys() else lead.get("domain", "")
    if llm is None:
        from . import ai_client
        llm = lambda msgs: ai_client.chat_for("sales", msgs, max_tokens=900, json_mode=True)
    bullets = "\n".join(f"- {s['label']}: {s['why']}" for s in summary[:3])
    prompt = f"""คุณคือทีมขายของ เจอ.AI บริการช่วยให้เว็บธุรกิจไทยโผล่ใน Google และในคำตอบของ AI (ChatGPT, Gemini, Perplexity)
เขียนอีเมลทักครั้งแรกถึงเจ้าของเว็บ {dom} เป็นภาษาไทย สุภาพ เป็นกันเอง ไม่เกิน 150 คำ

ผลตรวจจริงของเว็บนี้ (อ้างได้เฉพาะข้อเหล่านี้ ห้ามเพิ่มเอง):
{bullets}
ลิงก์ผลตรวจเต็ม: {result_url}

กติกา: ห้ามคำว่า "การันตี" หรือรับประกันอันดับ/ผลลัพธ์ · ห้ามบอกราคา · ห้ามกดดัน · ปิดท้ายด้วยข้อเสนอคุยฟรี 15 นาที
ตอบเป็น JSON เท่านั้น: {{"subject": "หัวข้ออีเมลสั้น ๆ", "body": "เนื้ออีเมล ขึ้นบรรทัดใหม่ด้วย \\n"}}"""
    try:
        raw = llm([{"role": "user", "content": prompt}])
        m = re.search(r"\{[\s\S]*\}", raw or "")
        d = json.loads(m.group(0)) if m else {}
        subject = str(d.get("subject") or "").strip()
        body = str(d.get("body") or "").strip()
        if not subject or len(body) < 80:
            raise ValueError("คำตอบสั้นเกิน")
        if FORBIDDEN.search(subject + " " + body):
            raise ValueError("มีคำโฆษณาต้องห้าม")
        if result_url not in body:
            body += f"\n\nดูผลตรวจเต็ม: {result_url}"
        return {"subject": subject[:120], "body": body[:2000], "source": "llm"}
    except Exception as e:
        out = outreach_template(lead, summary, result_url)
        out["source"] = f"template ({type(e).__name__})"
        return out


def ceo_review_lead(lead, report: dict, result_url: str, llm=None) -> dict:
    """CEO คัดลูกค้าจากผลตรวจ: รับ (พร้อมแพ็กเกจ + ร่างข้อความทัก) หรือไม่รับ (พร้อมเหตุผล)"""
    sc = L.score_audit(report)
    if sc["level"] == "เข้าเว็บไม่ได้":
        return {"action": "decline", "score": 0, "reason": "เข้าเว็บไม่ได้ — ตรวจใหม่เมื่อเว็บกลับมา ยังไม่ทัก"}
    if sc["score"] < MIN_SCORE_TO_PURSUE:
        why = "เว็บผ่านทุกข้อที่ตรวจ" if not sc["n_fail"] else f"เว็บผ่านเกือบทุกข้อ (ตก {sc['n_fail']} ข้อเล็ก)"
        return {"action": "decline", "score": sc["score"], "reason": f"{why} โอกาสขายต่ำ ไม่ควรเสียเวลาทัก (คะแนนโอกาส {sc['score']}/100 ต่ำกว่าเกณฑ์ {MIN_SCORE_TO_PURSUE})"}
    pkg = suggest_package(sc["score"])
    summary = L.public_summary(report)
    draft = draft_outreach(lead, summary, result_url, llm)
    return {"action": "accept", "score": sc["score"], "level": sc["level"], "n_fail": sc["n_fail"], "reasons": sc["reasons"],
            "package": pkg, "price": _plan(pkg)["price"], "discount_pct": 0, **draft}


def cfo_check(payload: dict) -> dict:
    """CFO ตรวจราคา/ส่วนลด/กำไรกับกรอบอำนาจ — นอกกรอบ = ต้องให้เจ้าของอนุมัติ ไม่ใช่ปฏิเสธ"""
    price = int(payload.get("price") or 0)
    disc = float(payload.get("discount_pct") or 0)
    if disc > MANDATE["max_discount_pct"]:
        return {"ok": False, "net": price, "margin_pct": 0,
                "note": f"ส่วนลด {disc:.0f}% เกินกรอบ {MANDATE['max_discount_pct']}% — ต้องให้เจ้าของอนุมัติ"}
    net = round(price * (1 - disc / 100))
    margin = round((net - MANDATE["ai_cost_cap_thb"]) / net * 100) if net else 0
    ok = 0 < net <= MANDATE["max_deal_thb"] and margin >= MANDATE["min_margin_pct"]
    note = f"ราคา {net:,} บาท/เดือน กำไรขั้นต้นราว {margin}% (ต้นทุน AI ไม่เกิน {MANDATE['ai_cost_cap_thb']} บาท)"
    if not ok:
        note += " — นอกกรอบ ส่งเจ้าของ"
    return {"ok": ok, "net": net, "margin_pct": margin, "note": note}


def cto_status() -> dict:
    """CTO รายงานสภาพระบบจากของที่มีอยู่: แบรนด์ที่สุขภาพเว็บไม่ผ่าน · โมเดลที่ถอยกลับ · โมเดลรูป"""
    from . import ai_client, db, image_finder
    unhealthy = []
    for b in db.list_all_brands():
        h = db.last_health_check(b["id"])
        rep = json.loads(h["report"]) if h and isinstance(h.get("report"), str) else (h or {})
        if rep and not rep.get("ok"):
            unhealthy.append(f"{b['name']} (ตก {rep.get('n_fail', '?')} ข้อ)")
    cap = image_finder.capabilities()
    return {"unhealthy": unhealthy, "fallbacks": dict(ai_client._LAST_FALLBACK),
            "image_generate": cap.get("generate"), "image_model": cap.get("image_model")}


def current_phase() -> int:
    from . import db
    try:
        return int(db.get_setting("agent_phase") or 2)
    except ValueError:
        return 2


def paused() -> bool:
    """ปุ่มหยุดฉุกเฉิน: ไม่มีเรื่องใดทำอัตโนมัติ (ข้อเสนอยังสร้างได้ แต่รอคนทั้งหมด)"""
    from . import db
    return db.get_setting("agents_paused") == "1"


def set_paused(on: bool) -> None:
    from . import db
    db.set_setting("agents_paused", "1" if on else "0")


def auto_gate(kind: str) -> dict:
    """เรื่องนี้สลับเป็นตัดสินเองได้ไหม: อยู่ในรายการเฟสนี้ + ตัดสินแล้ว ≥ 20 เรื่อง + ตรงกัน ≥ 95% · บังคับในโค้ด ไม่ใช่แค่ปุ่ม"""
    from . import db
    st = db.decision_stats(kind)
    eligible = kind in AUTO_ELIGIBLE
    raw_override, raw_on = db.get_setting(f"auto_override_{kind}"), db.get_setting(f"auto_{kind}")
    if kind == "followup" and raw_on is None:          # ติดตามผล = ข้อความทักชุดเดียวกัน → ยังไม่ตั้งแยก ใช้ตามเรื่อง "ข้อความทัก"
        raw_override, raw_on = db.get_setting("auto_override_outreach"), db.get_setting("auto_outreach")
        st = db.decision_stats("outreach")
    override = raw_override == "1"                     # เจ้าของสั่งปลดล็อกเองโดยไม่รอด่าน (บันทึกไว้ชัด ๆ)
    passed = st["decided"] >= AUTO_MIN_DECIDED and (st["agreement_pct"] or 0) >= AUTO_MIN_AGREEMENT
    enough = passed or override
    on = raw_on == "1"
    if not eligible:
        reason = "ต้องเป็นคนเสมอ — ระบบมองไม่เห็นบัญชีธนาคาร" if kind == "payment" else "เรื่องนี้ยังต้องเป็นคน"
    elif not enough:
        reason = (f"ต้องตัดสินแล้ว ≥ {AUTO_MIN_DECIDED} เรื่องและตรงกัน ≥ {AUTO_MIN_AGREEMENT}% "
                  f"(ตอนนี้ {st['decided']} เรื่อง · {st['agreement_pct'] if st['agreement_pct'] is not None else '—'}%)")
    else:
        reason = ""
    return {"kind": kind, "label": KINDS.get(kind, kind), "stats": st, "eligible": eligible, "enough": enough, "passed": passed,
            "override": override, "allowed": eligible and enough, "on": bool(on and eligible and enough), "reason": reason}


def auto_enabled(kind: str) -> bool:
    return not paused() and auto_gate(kind)["on"]


def set_auto(kind: str, on: bool, override: bool = False) -> tuple[bool, str]:
    """เปิด/ปิดตัดสินเองต่อเรื่อง · override = เจ้าของสั่งปลดล็อกโดยไม่รอด่าน (ใช้ได้เฉพาะเรื่องที่ไม่ใช่เงิน) · ปิด = ถอนปลดล็อกด้วย"""
    from . import db
    if kind not in AUTO_ELIGIBLE and on:
        return False, auto_gate(kind)["reason"]
    if on and override:
        db.set_setting(f"auto_override_{kind}", "1")
    if not on:
        db.set_setting(f"auto_override_{kind}", "0")
    gate = auto_gate(kind)
    if on and not gate["allowed"]:
        return False, gate["reason"]
    db.set_setting(f"auto_{kind}", "1" if on else "0")
    return True, ""


def mode_label() -> str:
    if paused():
        return "หยุดฉุกเฉิน"
    on = [KINDS[k] for k in AUTO_ELIGIBLE if auto_gate(k)["on"]]
    return "โหมดเสนอ" + (f" · ตัดสินเอง: {', '.join(on)}" if on else "")


def quote_proposal_text(lead, q, cfo: dict, url: str) -> str:
    from . import quotes as Q
    who = lead["domain"] + (f" ({lead['name']})" if lead["name"] else "")
    return f"ส่งใบเสนอราคาให้ {who}\n{Q.summary_text(q, lead['domain'])}\nCFO: {cfo['note']}\nลิงก์ที่ลูกค้าจะได้รับ: {url}"


def kpis(phase: int | None = None) -> dict:
    """ตัวเลขด่านของเฟสปัจจุบัน: ตรวจฟรี/สัปดาห์ · อัตราคุยต่อและปิดดีล (28 วัน) · ลูกค้าจ่ายสะสม · ความเห็นตรงกัน · เรื่องส่งกลับ/สัปดาห์"""
    from . import db
    phase = phase or current_phase()
    now = datetime.datetime.now()
    wk = (now - datetime.timedelta(days=7)).isoformat(timespec="seconds")
    m28 = (now - datetime.timedelta(days=28)).isoformat(timespec="seconds")
    contacted = db.leads_with_event("contacted", m28)
    replied = db.leads_with_event("replied", m28)
    paid = db.leads_with_event("paid", m28)
    esc = 0
    for d in db.list_decisions_since(wk):
        try:
            pl = json.loads(d["payload_json"] or "{}")
        except Exception:
            pl = {}
        if pl.get("escalate") or (isinstance(pl.get("cfo"), dict) and not pl["cfo"].get("ok", True)):
            esc += 1
    vals = {"audits_week": db.count_events("audit", wk), "audits_by_src": db.count_audits_by_src(wk),
            "reply_rate": round(len(replied & contacted) / len(contacted) * 100) if contacted else None,
            "close_rate": round(len(paid) / len(replied) * 100) if replied else None,
            "new_paying": db.count_quotes_paid_since(PLAN_START),
            "agreement": db.decision_stats()["agreement_pct"],
            "escalations_week": esc}
    rows = []
    for k, target in GATES.get(phase, GATES[2]).items():
        v = vals[k]
        if target is None:
            ok = None
        elif k == "escalations_week":
            ok = v <= target
        else:
            ok = v is not None and v >= target
        rows.append({"key": k, "label": GATE_LABEL[k], "value": v, "target": target, "ok": ok})
    judged = [r for r in rows if r["ok"] is not None]
    return {"phase": phase, "values": vals, "rows": rows, "n_pass": sum(1 for r in judged if r["ok"]), "n_rows": len(judged),
            "passed": bool(judged) and all(r["ok"] for r in judged), "window_days": 28}


def digest_text(today: str, leads_new: int, leads_total: int, pending: list, payments: int, cto: dict, stats: dict,
                kpi: dict | None = None, mode: str = "โหมดเสนอ", extra: list | None = None) -> str:
    lines = [f"สรุปเช้า {today} — ผู้บริหารเอเจนต์ ({mode})", ""]
    lines.append(f"ลูกค้าเป้าหมาย: ใหม่ 24 ชม. {leads_new} · ทั้งหมด {leads_total}")
    lines.append(f"ข้อเสนอรออนุมัติ: {len(pending)}" + (" — " + "; ".join(d["title"] for d in pending[:5]) if pending else ""))
    lines.append(f"ตัดสินใจตรงกับเจ้าของ: {stats.get('agreement_pct') if stats.get('agreement_pct') is not None else '-'}% จาก {stats.get('decided', 0)} เรื่อง")
    if kpi:
        parts = [f"{r['label']} {r['value'] if r['value'] is not None else '—'}/{r['target']}" for r in kpi["rows"] if r["target"] is not None]
        lines.append(f"ด่านเฟส {kpi['phase']}: ผ่าน {kpi['n_pass']}/{kpi['n_rows']} — " + " · ".join(parts))
    for x in (extra or []):
        if x:
            lines.append(x)
    lines.append(f"CFO: การชำระรอยืนยัน {payments} รายการ")
    lines.append("CTO: " + ("สุขภาพเว็บไม่ผ่าน " + ", ".join(cto["unhealthy"]) if cto["unhealthy"] else "สุขภาพเว็บผ่านทุกแบรนด์")
                 + (" · โมเดลถอยกลับ: " + ", ".join(f"{k}: {v}" for k, v in cto["fallbacks"].items()) if cto["fallbacks"] else "")
                 + (" · สร้างรูปใหม่ไม่ได้ (ไม่มีโมเดลรูปบน proxy)" if cto.get("image_generate") is False else ""))
    return "\n".join(lines)


@office.track("ceo", "สรุปเช้าให้เจ้าของ")
def morning_digest(base_url: str) -> dict:
    """รวบรวม → เก็บเป็นการตัดสินใจชนิด digest (auto) → อีเมลถึงแอดมินที่มีที่อยู่จริง + LINE ถึงเจ้าของ (ถ้าผูกไว้)"""
    from . import db, mailer, line_oa, prospecting, pipeline, marketing, followup, success, closer, orchestrator, inbox
    now = datetime.datetime.now()
    since = (now - datetime.timedelta(days=1)).isoformat(timespec="seconds")
    pipeline.run_pending_auto()                                   # เรื่องที่สลับเป็นตัดสินเองแล้วแต่ยังค้าง → เคลียร์ก่อนสรุป
    pending = [dict(d) for d in db.list_decisions("proposed", 50)]
    kpi = kpis()
    text = digest_text(now.strftime("%Y-%m-%d"), db.count_leads_since(since), len(db.list_leads(None, 10000)),
                       pending, len(db.list_pending_payments()) + len(db.list_quotes(None, "claimed")), cto_status(),
                       db.decision_stats(), kpi, mode_label(), [orchestrator.status_text(), inbox.status_text(), closer.status_text(), prospecting.status_text(), followup.status_text(), success.status_text(), marketing.status_text()])
    db.add_decision("ceo", "digest", f"สรุปเช้า {now.strftime('%Y-%m-%d')}", text, {"pending": len(pending), "kpi": kpi["values"]}, status="auto")
    sent = []
    if mailer.config():
        for t in db.list_all_tenants():
            if t["is_admin"] and mailer.is_deliverable(t["email"]):
                try:
                    html = mailer.wrap_html("สรุปเช้า เจอ.AI", "<pre style='white-space:pre-wrap;font-family:inherit'>" + text + f"</pre><p><a href='{base_url}/admin/decisions'>เปิดหน้าการตัดสินใจ</a></p>")
                    mailer.send(t["email"], f"สรุปเช้า เจอ.AI {now.strftime('%d/%m')}", html, text=text)
                    sent.append(t["email"])
                except Exception:
                    pass
    try:
        if line_oa.notify_owner(text[:4500] + f"\n\n{base_url}/admin/decisions"):
            sent.append("LINE")
    except Exception:
        pass
    return {"text": text, "sent_to": sent, "pending": len(pending), "kpi": kpi}
