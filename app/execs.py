"""ผู้บริหารเอเจนต์ CEO · CFO · CTO — โหมดเสนอก่อนทำ
ทุกข้อเสนอเป็นแถวในตาราง decisions รอคนกดยืนยัน (เฟส 1) · กรอบอำนาจ (MANDATE) บังคับที่นี่ในโค้ด ไม่ใช่ใน prompt
เพราะเอเจนต์อ่านอีเมล/เว็บลูกค้าตลอดเวลา ข้อความเหล่านั้นสั่งมันได้ถ้าปล่อยให้ตัดสินใจอิสระ"""
from __future__ import annotations
import datetime
import json
import re

from . import leads as L

MANDATE = {
    "mode": "propose",          # propose = เสนอทุกเรื่อง คนกดยืนยัน (เฟส 1) · auto = ทำเองในกรอบ (หลังผ่านด่าน)
    "max_discount_pct": 10,
    "max_deal_thb": 50000,      # ต่อรายต่อเดือน
    "refund_days": 7,
    "ai_cost_cap_thb": 570,     # ต้นทุน AI/เครื่องมือต่อลูกค้าต่อเดือน (จากแผน)
    "min_margin_pct": 70,
}
AGENTS = {"ceo": "CEO", "cfo": "CFO", "cto": "CTO"}
KINDS = {"lead_review": "คัดลูกค้า", "outreach": "ข้อความทัก", "quote": "ใบเสนอราคา", "digest": "สรุปเช้า"}
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
    bullets = "\n".join(f"• {s['label']} — {s['why']}" for s in summary[:3]) or "• ยังไม่พบข้อที่ตก"
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
    if sc["score"] == 0:
        return {"action": "decline", "score": 0, "reason": "เข้าเว็บไม่ได้ — ตรวจใหม่เมื่อเว็บกลับมา ยังไม่ทัก"}
    if sc["score"] < MIN_SCORE_TO_PURSUE:
        return {"action": "decline", "score": sc["score"], "reason": f"เว็บผ่านเกือบทุกข้อ (ตก {sc['n_fail']} ข้อเล็ก) โอกาสขายต่ำ ไม่ควรเสียเวลาทัก"}
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


def digest_text(today: str, leads_new: int, leads_total: int, pending: list, payments: int, cto: dict, stats: dict) -> str:
    lines = [f"สรุปเช้า {today} — ผู้บริหารเอเจนต์ (โหมดเสนอ)", ""]
    lines.append(f"ลูกค้าเป้าหมาย: ใหม่ 24 ชม. {leads_new} · ทั้งหมด {leads_total}")
    lines.append(f"ข้อเสนอรออนุมัติ: {len(pending)}" + (" — " + "; ".join(d["title"] for d in pending[:5]) if pending else ""))
    lines.append(f"ตัดสินใจตรงกับเจ้าของ: {stats.get('agreement_pct', '-')}% จาก {stats.get('decided', 0)} เรื่อง")
    lines.append(f"CFO: การชำระรอยืนยัน {payments} รายการ")
    lines.append("CTO: " + ("สุขภาพเว็บไม่ผ่าน " + ", ".join(cto["unhealthy"]) if cto["unhealthy"] else "สุขภาพเว็บผ่านทุกแบรนด์")
                 + (" · โมเดลถอยกลับ: " + ", ".join(f"{k}: {v}" for k, v in cto["fallbacks"].items()) if cto["fallbacks"] else "")
                 + (" · สร้างรูปใหม่ไม่ได้ (ไม่มีโมเดลรูปบน proxy)" if cto.get("image_generate") is False else ""))
    return "\n".join(lines)


def morning_digest(base_url: str) -> dict:
    """รวบรวม → เก็บเป็นการตัดสินใจชนิด digest (auto) → อีเมลถึงแอดมินที่มีที่อยู่จริง"""
    from . import db, mailer
    now = datetime.datetime.now()
    since = (now - datetime.timedelta(days=1)).isoformat(timespec="seconds")
    pending = [dict(d) for d in db.list_decisions("proposed", 50)]
    text = digest_text(now.strftime("%Y-%m-%d"), db.count_leads_since(since), len(db.list_leads(None, 10000)),
                       pending, len(db.list_pending_payments()), cto_status(), db.decision_stats())
    db.add_decision("ceo", "digest", f"สรุปเช้า {now.strftime('%Y-%m-%d')}", text, {"pending": len(pending)}, status="auto")
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
    return {"text": text, "sent_to": sent, "pending": len(pending)}
