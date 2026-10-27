"""ตัวตอบแชตขาย (Sales chat) — ตอบคำถามลีดทันทีจากข้อเท็จจริงในระบบเท่านั้น (ราคา แพ็กเกจ ขั้นตอน ผลตรวจเว็บของเขา ใบเสนอราคา)
ช่องทาง: LINE OA (ตอบทันที) · กล่องแชตบนหน้าเว็บ (ตอบทันที) · อีเมลตอบกลับ (ร่างรออนุมัติ — ปลดล็อกทำเองได้ตามด่าน ≥ 20 เรื่อง ≥ 95%)
กติกา: ตัวเลขในคำตอบต้องอยู่ในคลังข้อเท็จจริงเท่านั้น (ไม่งั้นไม่ส่ง) · ห้ามการันตี · เรื่องนอกกรอบ (ต่อรองราคา คืนเงิน สัญญา ร้องเรียน
ขอคุยกับคน งานองค์กร) → ตอบรับเรื่องแล้วส่งต่อเจ้าของทันที ไม่ตอบแทน"""
from __future__ import annotations
import datetime as dt
import json
import re

from . import db, office

DAILY_PER_USER = 30
ESCALATE = [
    # คำภาษาอื่นต่อท้าย (ลีดที่เลือกภาษาอื่นบนเว็บ) — เรื่องนอกกรอบแบบเดียวกัน
    ("ต่อรองราคา/ขอส่วนลด", re.compile(r"ส่วนลด|ลดได้|ลดให้|ลดราคา|ต่อรอง|ถูกกว่านี้|discount|cheaper|ขอราคาพิเศษ|折扣|打折|优惠|便宜|割引|値引|할인|깎", re.I)),
    ("คืนเงิน/ยกเลิก", re.compile(r"คืนเงิน|refund|ยกเลิกบริการ|ยกเลิกสัญญา|เลิกใช้|unsubscribe me|cancel (?:my|the) (?:service|subscription|contract)|退款|退钱|返金|払い戻し|解約|환불|해지", re.I)),
    ("สัญญา/กฎหมาย", re.compile(r"แก้สัญญา|เปลี่ยนสัญญา|ข้อสัญญา|ทนาย|ฟ้อง|กฎหมาย|legal|nda|pdpa|lawyer|合同|律师|契約書|弁護士|계약서|변호사", re.I)),
    ("ร้องเรียน/ไม่พอใจ", re.compile(r"ร้องเรียน|ไม่พอใจ|ผิดหวัง|แย่มาก|โกง|หลอก|spam|สแปม|รำคาญ|complain|scam|投诉|骗|苦情|詐欺|불만|사기", re.I)),
    ("ขอคุยกับคน", re.compile(r"ขอคุยกับ|คุยกับคน|เจ้าหน้าที่|แอดมิน|โทรหา|โทรกลับ|ขอเบอร์|นัดคุย|นัดประชุม|call me|human|real person|人工|客服|担当者|電話|담당자|상담원|전화", re.I)),
    ("งานองค์กร/งานพิเศษ", re.compile(r"องค์กร|enterprise|หลายสาขา|หลายเว็บ|white ?label|custom|ทำเว็บใหม่|ทำเว็บให้|ออกแบบเว็บ|รับทำเว็บ", re.I)),
    ("ชำระเงินมีปัญหา", re.compile(r"จ่ายไม่ได้|ตัดบัตรไม่ผ่าน|โอนแล้ว|ชำระแล้ว|ใบกำกับ.*ผิด|ใบเสร็จ.*ผิด|เงินเข้า", re.I)),
]
HOLD = "รับเรื่องแล้วครับ เรื่องนี้ขอให้ผู้ดูแลตอบเองเพื่อความถูกต้อง จะติดต่อกลับภายในวันทำการนี้"
# สัญญาณซื้อ (โหมดเอเจนซี): สนใจ/ถามราคา/อยากเริ่ม/ขอนัด → ตอบคำถาม + บอกว่าทีมจะติดต่อไปนัดคุย 30 นาที · แจ้งเจ้าของทันที · ขึ้นบนสุดของรายการโทร
BUY_RE = re.compile(r"(?<!ไม่)(?<!ไม่ )สนใจ|ราคา|ค่าบริการ|ใบเสนอ|สมัคร|อยากใช้|เริ่มใช้|อยากเริ่ม|ซื้อ|นัดคุย|อยากคุย|คุยต่อ|โทรกลับ|เอาเลย|ทำเลย"
                    r"|(?<!not )(?<!n't )\binterested\b|\bpric(?:e|es|ing)\b|how much|\bquote\b|sign ?up|\bsubscribe\b|\bbuy\b|purchase|get started|book a call|schedule a call|call me"
                    r"|感兴趣|报价|价格|多少钱|购买|签约|预约|興味|見積|料金|価格|申し込|購入|관심|견적|가격|비용|구매|신청", re.I)
WANTS_HUMAN = "ขอคุยกับคน"


def buying_intent(text: str) -> bool:
    return bool(BUY_RE.search(text or ""))


def enabled(channel: str) -> bool:
    return (db.get_setting(f"salesbot_{channel}") or "1") == "1"


def set_enabled(channel: str, on: bool) -> None:
    db.set_setting(f"salesbot_{channel}", "1" if on else "0")


# ---------- คลังข้อเท็จจริง (ทุกตัวเลขในคำตอบต้องมาจากที่นี่) ----------
def knowledge(lead=None) -> list[str]:
    from . import billing, quotes as Q, execs, leads as L, pipeline, marketing
    vat = Q.vat_rate_now()
    fx = lambda x: f"{x:,.0f}"
    pro = billing.PLANS["pro"]
    facts = [
        "เจอ.AI คือบริการทำให้เว็บธุรกิจถูก Google และผู้ช่วย AI (ChatGPT · Gemini · Perplexity · Google AI Overview) หยิบไปตอบและแนะนำ: "
        "ตรวจเว็บ · เขียนคำตอบคำถามที่ลูกค้าถามจริงลงเว็บของลูกค้าเอง · ใส่ schema และ llms.txt · วัดผลทุกสัปดาห์ด้วยการถาม AI จริง",
        # โหมดเอเจนซี: ไม่มีเมนูแพ็กเกจให้เลือก — บอกราคาเริ่มต้น แล้วราคาสุดท้ายตามขอบเขตงานหลังคุยกัน
        f"ราคาเริ่มต้น {fx(pro['price'])} บาท/เดือน — ราคาสุดท้ายขึ้นกับขอบเขตงานของแต่ละธุรกิจ ทีมเราสรุปราคาและขอบเขตงานให้หลังคุยกัน 30 นาที",
        "ขั้นตอน: ตรวจเว็บฟรี → นัดคุย 30 นาทีกับทีมเรา พาดูรายงานของคุณ → ทีมเราส่งใบเสนอราคาพร้อมขอบเขตงานและสัญญา → ชำระ → เริ่มงาน",
        (f"ราคายังไม่รวม VAT {vat:g}%" if vat else "ราคาไม่มี VAT เพิ่ม"),
        f"จ่ายรายปีได้ส่วนลด {Q.yearly_discount():g}% จากราคารายเดือนที่ตกลงกัน",
        (f"ทดลองใช้ฟรี {billing.TRIAL_DAYS} วัน (1 แบรนด์ · มอนิเตอร์ 4 ครั้ง · สร้างคอนเทนต์ 3 ชิ้น)" if billing.free_open()
         else "ตอนนี้ไม่มีแผนทดลองใช้ฟรี — เริ่มได้จากตรวจเว็บฟรี แล้วนัดคุย 30 นาทีกับทีมเรา"),
        (f"คืนเงินเต็มได้ภายใน {execs.MANDATE['refund_days']} วันแรก · รายเดือนยกเลิกได้ทุกเมื่อ · รายปีหลัง {execs.MANDATE['refund_days']} วันไม่คืนส่วนที่เหลือแต่ใช้ได้จนครบปี"
         if execs.MANDATE["refund_days"] else "ค่าบริการที่ชำระแล้วไม่คืนเงิน · รายเดือนยกเลิกได้ทุกเมื่อ มีผลสิ้นรอบที่ชำระแล้ว · รายปีใช้บริการได้จนครบปี"),
        "ชำระด้วยบัตรผ่าน Stripe (ต่ออายุอัตโนมัติ) หรือโอน PromptPay · ออกใบเสร็จ/ใบกำกับภาษีทุกครั้ง · นิติบุคคลหัก ณ ที่จ่าย 3% ได้",
        "หลังชำระ: วันแรกเปิดบัญชีและเริ่มงาน · วันที่ 3 ช่วยติดตั้งตัวเชื่อมกับเว็บ (WordPress หรือโค้ดสั้น ๆ) · วันที่ 7 ได้งานชิ้นแรก · รายงานผลก่อนรอบบิลทุกเดือน",
        "เราไม่การันตีอันดับหรือว่า AI จะแนะนำ แต่วัดให้เห็นทุกสัปดาห์จากการถาม AI จริงและ Search Console",
        f"ตรวจเว็บฟรีได้ที่ {pipeline.public_base()}/audit (ราวครึ่งนาที)",
    ]
    for b in db.list_all_brands():                                      # เคสจริงที่ลูกค้าอนุญาตใช้ชื่อ และผลถาม AI ดีขึ้นจริง
        if b["case_study_ok"]:
            t = marketing.ai_trend(b["id"])
            if t:
                facts.append(f"เคสจริง: {b['name']} ถูก AI แนะนำ {t['first']['hits']} → {t['last']['hits']} จาก {t['asked']} คำถามจริง ({t['first']['at']} → {t['last']['at']})")
                break
    if lead:
        facts.append(f"ลูกค้ารายนี้: เว็บ {lead['domain']} · รายงานตรวจเว็บของเขา {pipeline.result_url(lead)}")
        try:
            rep = json.loads(lead["audit_json"] or "{}") if lead["audit_json"] else {}
            top = L.public_summary(rep, 3)
            if top:
                facts.append("ปัญหาหลักบนเว็บของเขา: " + " · ".join(f"{s['label']} ({s['why']})" if s["why"] else s["label"] for s in top))
        except Exception:
            pass
        qs = [q for q in db.list_quotes(lead["id"]) if q["status"] in Q.OPEN]
        if qs:
            q = qs[0]
            facts.append(f"ใบเสนอราคาที่เปิดอยู่: {q['number']} {Q.label_of(q)} {Q.CYCLES[Q.cycle_of(q)]} "
                         f"ยอดชำระ {Q.baht(Q.payable(q))} บาท · สถานะ {Q.STATUS_LABEL.get(q['status'], q['status'])} · ดู/ยืนยันที่ {pipeline.quote_url(q)}")
    return facts


def _nums(s: str) -> set:
    """ตัวเลข 2 หลักขึ้นไป (ราคา จำนวน วันที่) — เลขหลักเดียวเช่น "3 ชิ้น" ไม่ตรวจ"""
    out = set()
    for n in re.findall(r"\d[\d,]*(?:\.\d+)?", s or ""):
        n = n.replace(",", "").rstrip(".")
        if len(n.split(".")[0]) >= 2:
            out.add(n)
    return out


def escalation(text: str) -> str | None:
    for why, rx in ESCALATE:
        if rx.search(text or ""):
            return why
    return None


def answer(question: str, lead=None, channel: str = "line", llm=None, lang: str | None = None, add_call: bool = True) -> dict:
    """คืน {answer, escalate (เหตุผล|None), source, intent} — เรื่องนอกกรอบ = HOLD + escalate · ตัวเลขนอกคลัง = ไม่ใช้คำตอบโมเดล
    · intent = สัญญาณซื้อ (ต่อท้ายคำตอบด้วยการชวนนัดคุย 30 นาที เว้น add_call=False เมื่อผู้เรียกบอกเองแล้ว) · ขอคุยกับคน = ข้อความนัดคุย
    · lang = ภาษาที่ตอบ (None = ภาษาที่ลีดเลือกบนเว็บ · ไม่มีลีด = ไทย) · ไทย = prompt/ข้อความเดิมทุกตัวอักษร"""
    from . import execs, i18n
    if not i18n.valid(lang):
        lang = db.row_lang(lead) if lead is not None else "th"
    t = lambda x: i18n.tr(x, lang)
    q = (question or "").strip()[:1500]
    why = escalation(q)
    intent = buying_intent(q) or why == WANTS_HUMAN
    facts = knowledge(lead)
    if why == WANTS_HUMAN:                                              # ขอคุยกับคน = อยากนัดคุย → บอกว่าจะติดต่อไปนัด 30 นาที + ส่งต่อเจ้าของ
        from . import pipeline
        return {"answer": pipeline.call_reply(lead, lang), "escalate": why, "source": "rule", "intent": True}
    if why and why != "ต่อรองราคา/ขอส่วนลด":
        return {"answer": t(HOLD), "escalate": why, "source": "rule", "intent": intent}
    llm = llm or LLM
    if llm is None:
        from . import ai_client
        llm = lambda msgs: ai_client.chat_for("salesbot", msgs, max_tokens=900, json_mode=True)
    from . import deliverability as D
    style = ("ข้อความแชตสั้น ๆ ไม่เกิน 70 คำ" if channel in ("line", "web")
             else "อีเมลตอบกลับแบบคนพิมพ์ ไม่เกิน 100 คำ ไม่ทำเป็นรายการหัวข้อ ไม่ต้องลงชื่อ (ระบบลงชื่อให้)")
    prompt = ("คุณคือผู้ช่วยขายของ เจอ.AI ตอบคำถามลูกค้าเป็นภาษาไทย (ถ้าลูกค้าถามภาษาอื่นให้ตอบภาษานั้น) แบบ" + style + "\n"
              "ใช้ได้เฉพาะข้อเท็จจริงด้านล่าง ห้ามเพิ่มตัวเลข ราคา ระยะเวลา หรือผลลัพธ์อื่น ห้ามคำว่า การันตี/รับประกัน ถ้าข้อเท็จจริงไม่พอตอบ ให้ needs_human=true\n"
              "ข้อเท็จจริง:\n- " + "\n- ".join(facts)
              + "\nอย่าเสนอเมนูแพ็กเกจให้เลือก ถ้าถามราคา บอกราคาเริ่มต้นและว่าราคาสุดท้ายขึ้นกับขอบเขตงาน ทีมเราสรุปให้หลังคุยกัน 30 นาที"
              + ("\nลูกค้าขอส่วนลด: บอกส่วนลดรายปีตามข้อเท็จจริง และบอกว่าส่วนลดอื่นต้องให้ผู้ดูแลพิจารณา" if why else "")
              + f"\n\nคำถามลูกค้า: {q}\n"
              'ตอบ JSON {"answer": "...", "needs_human": false}') + D.write_in(lang).replace("หัวข้อและเนื้อหา", "คำตอบ").replace("the subject and body", "the answer")
    try:
        raw = llm([{"role": "user", "content": prompt}])
        m = re.search(r"\{[\s\S]*\}", raw or "")
        d = json.loads(m.group(0)) if m else {}
        ans = str(d.get("answer") or "").strip()
        need = bool(d.get("needs_human"))
        bad_nums = _nums(ans) - _nums(" ".join(facts) + " " + q)
        if len(ans) < 10 or execs.FORBIDDEN.search(ans) or bad_nums:
            raise ValueError(f"ไม่ผ่านกติกา{' ตัวเลขนอกคลัง ' + ','.join(sorted(bad_nums))[:60] if bad_nums else ''}")
        if D.wrong_language(ans, lang):
            raise ValueError("ไม่ได้ตอบเป็นภาษาของลูกค้า")
        if need:
            return {"answer": ans + ("\n\n" if ans else "") + t("เรื่องนี้ขอให้ผู้ดูแลตอบเพิ่ม จะติดต่อกลับภายในวันทำการนี้"), "escalate": "ข้อมูลไม่พอตอบ", "source": "llm", "intent": intent}
        if intent and add_call:
            from . import pipeline
            ans = ans[:1200] + "\n\n" + pipeline.call_reply(lead, lang)          # สัญญาณซื้อ → ชวนนัดคุย 30 นาที (ไม่เสนอแพ็กเกจ/ใบเสนอราคา)
        if channel == "email":
            ans = D.sign(ans)
        return {"answer": ans[:1500], "escalate": why, "source": "llm", "intent": intent}
    except Exception as e:
        return {"answer": t(HOLD), "escalate": why or f"ตอบเองไม่ได้ ({str(e)[:80]})", "source": "fallback", "intent": intent}


LLM = None   # เทสต์ฉีดได้


# ---------- บันทึก + ช่องทาง ----------
def _log(channel: str, user_ref: str, lead, q: str, res: dict, contact: str = "", lang: str | None = None) -> int:
    from . import i18n
    lg = i18n.valid(lang) or (db.row_lang(lead) if lead is not None else "th")
    with db.get_conn() as c:
        cur = c.execute(db.q("INSERT INTO sales_chats(created_at,channel,user_ref,lead_id,question,answer,escalated,reason,source,contact,lang) "
                             "VALUES(?,?,?,?,?,?,?,?,?,?,?) RETURNING id"),
                        (db.now(), channel, (user_ref or "")[:120], lead["id"] if lead else None, q[:2000], res["answer"][:2000],
                         1 if res.get("escalate") else 0, (res.get("escalate") or "")[:200], res.get("source") or "", contact[:200] or None, lg))
        return cur.fetchone()["id"]


def _count_today(channel: str, user_ref: str) -> int:
    since = dt.datetime.now().replace(hour=0, minute=0, second=0).isoformat(timespec="seconds")
    with db.get_conn() as c:
        return c.execute(db.q("SELECT COUNT(*) AS n FROM sales_chats WHERE channel=? AND user_ref=? AND created_at>=?"),
                         (channel, user_ref, since)).fetchone()["n"]


def _escalate(channel: str, lead, q: str, res: dict, contact: str = "") -> None:
    from . import pipeline
    who = lead["domain"] if lead else ("ผู้เยี่ยมชมเว็บ" if channel == "web" else "ผู้ใช้ LINE")
    link = f"/admin/leads/{lead['id']}" if lead else "/admin/salesbot"
    if res.get("intent") and res["escalate"] in (WANTS_HUMAN, WANTS_CALL):
        pipeline.alert_admins(f"🔥 {who} อยากคุย — นัดโทรภายในวันนี้: {q[:160]}" + (f" · ติดต่อกลับ: {contact}" if contact else ""), link, "lead",
                              subject=f"🔥 {who} อยากคุย — นัดโทรภายในวันนี้")
        return
    pipeline.alert_admins(f"💬 {who} ถาม ({res['escalate']}) — ต้องให้คุณตอบ: {q[:160]}" + (f" · ติดต่อกลับ: {contact}" if contact else ""), link, "lead")


WANTS_CALL = "สนใจซื้อ/อยากคุย"


@office.track("salesbot", "ตอบแชตลูกค้า")
def handle(channel: str, user_ref: str, text: str, lead=None, contact: str = "", llm=None, lang: str | None = None, noted: bool = False) -> dict:
    """คำถามหนึ่งข้อ → คำตอบ + บันทึก + แจ้งเจ้าของเมื่อต้องส่งต่อ · คืน {answer, escalate, id} · lang = ภาษาของหน้าที่ถาม (แชตบนเว็บ)
    · สัญญาณซื้อ: มีลีด → pipeline.lead_replied (แจ้งเจ้าของ "นัดโทรภายในวันนี้" + ขึ้นบนสุดของรายการโทร) · ยังไม่มีลีด → ส่งต่อเจ้าของ + ขอช่องทางติดต่อ
    · noted = ผู้เรียกบันทึกความสนใจและบอกลูกค้าเรื่องนัดคุยเองแล้ว (LINE พิมพ์ "สนใจ")"""
    from . import i18n
    q = (text or "").strip()
    if _count_today(channel, user_ref) >= DAILY_PER_USER:
        lg = i18n.valid(lang) or (db.row_lang(lead) if lead is not None else "th")
        return {"answer": i18n.tr("วันนี้ถามได้ครบแล้วครับ ทิ้งคำถามไว้ทางอีเมลได้ ทีมงานจะตอบให้", lg), "escalate": None, "id": None, "limited": True}
    res = answer(q, lead, channel, llm, **({"lang": lang} if i18n.valid(lang) else {}), add_call=not noted)
    if res.get("intent") and not noted and lead is None and not res.get("escalate"):
        res = {**res, "escalate": WANTS_CALL}                         # คนที่ยังไม่มีลีดสนใจ → ส่งต่อเจ้าของ (กล่องแชตบนเว็บจะขอช่องทางติดต่อ)
    cid = _log(channel, user_ref, lead, q, res, contact, lang)
    if lead:
        from . import pipeline
        pipeline.record(lead["id"], "chat", f"ถามทาง{ {'line': 'LINE', 'web': 'แชตบนเว็บ', 'email': 'อีเมล'}.get(channel, channel)}: {q[:150]}"
                        + (f" · ส่งต่อเจ้าของ ({res['escalate']})" if res.get("escalate") else " · ตัวตอบแชตตอบแล้ว"), "salesbot")
        if res.get("intent") and not noted:
            try:
                pipeline.lead_replied(lead, q, via={"line": "LINE", "web": "แชตบนเว็บ", "email": "อีเมล"}.get(channel, channel), actor="customer")
            except Exception:
                pass
            if res.get("escalate") in (WANTS_HUMAN, WANTS_CALL):
                return {**res, "id": cid}                             # แจ้งเจ้าของแล้วใน lead_replied ไม่แจ้งซ้ำ
    if res.get("escalate"):
        try:
            _escalate(channel, lead, q, res, contact)
        except Exception:
            pass
    return {**res, "id": cid}


def add_contact(chat_id: int, contact: str, lang: str | None = None) -> bool:
    """ผู้เยี่ยมชมเว็บทิ้งช่องทางติดต่อหลังคำถามที่ส่งต่อเจ้าของ (ยินยอมแล้ว) · lang = ภาษาที่เขาเลือกบนเว็บ (บันทึกที่แชต และที่ลีดถ้ามี)"""
    from . import i18n
    lg = i18n.valid(lang)
    with db.get_conn() as c:
        row = c.execute(db.q("SELECT * FROM sales_chats WHERE id=?"), (chat_id,)).fetchone()
        if not row or row["channel"] != "web":
            return False
        c.execute(db.q("UPDATE sales_chats SET contact=? WHERE id=?"), (contact[:200], chat_id))
        if lg:
            c.execute(db.q("UPDATE sales_chats SET lang=? WHERE id=?"), (lg, chat_id))
    if lg and row["lead_id"]:
        db.update_lead(row["lead_id"], lang=lg)
    from . import pipeline
    pipeline.alert_admins(f"💬 ผู้เยี่ยมชมเว็บทิ้งช่องทางติดต่อ: {contact[:120]} · คำถาม: {row['question'][:160]}", "/admin/salesbot", "lead")
    return True


def recent(limit: int = 100) -> list:
    with db.get_conn() as c:
        return c.execute(db.q("SELECT * FROM sales_chats ORDER BY id DESC LIMIT ?"), (limit,)).fetchall()


def stats(days: int = 7) -> dict:
    since = (dt.datetime.now() - dt.timedelta(days=days)).isoformat(timespec="seconds")
    with db.get_conn() as c:
        rows = c.execute(db.q("SELECT channel, escalated FROM sales_chats WHERE created_at>=?"), (since,)).fetchall()
    return {"n": len(rows), "escalated": sum(r["escalated"] for r in rows), "by_channel": {ch: sum(1 for r in rows if r["channel"] == ch) for ch in ("line", "web", "email")}}


def status_text() -> str:
    s = stats(1)
    if not s["n"]:
        return ""
    return f"ตัวตอบแชต: 24 ชม. ตอบ {s['n'] - s['escalated']} คำถาม · ส่งต่อคุณ {s['escalated']}" + "".join(
        f" · {lb} {s['by_channel'][k]}" for k, lb in (("line", "LINE"), ("web", "เว็บ"), ("email", "อีเมล")) if s["by_channel"][k])
