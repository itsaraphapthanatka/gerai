"""ใบเสนอราคาและสัญญาบริการมาตรฐาน (เฟส 2) — ตรรกะล้วน
ราคามาจากตารางแพ็กเกจเท่านั้น (ห้ามตั้งราคาพิเศษ) · ส่วนลดเป็นเรื่องของ CFO (กรอบใน execs.MANDATE)
สัญญาเป็น "ร่างมาตรฐาน" จนกว่าทนายจะตรวจ (settings contract_reviewed=1) — หน้าเว็บจะติดป้ายบอกลูกค้าตรง ๆ"""
from __future__ import annotations
import datetime

from . import billing, i18n

VALID_DAYS = 14
PACKAGES = ("pro", "agency")
STRUCTURAL = {"csr", "page_identity", "canonical", "noindex"}      # ตกข้อพวกนี้ = ต้องแก้ตัวเว็บ → เสนอ GEO Setup เป็นงานแยก
STATUS_LABEL = {"draft": "ร่าง", "sent": "ส่งแล้ว", "viewed": "ลูกค้าเปิดดูแล้ว", "accepted": "ลูกค้ายืนยันแล้ว รอชำระ",
                "claimed": "แจ้งชำระแล้ว รอยืนยันยอด", "paid": "ชำระแล้ว เปิดบัญชีแล้ว", "expired": "หมดอายุ", "cancelled": "ยกเลิก"}
OPEN = {"draft", "sent", "viewed", "accepted", "claimed"}          # ใบที่ยังเดินอยู่ (มีได้ทีละใบต่อลูกค้า)


def number(ym: str, seq: int) -> str:
    """เลขที่ใบเสนอราคา Q202510-001 (ปีเดือน + ลำดับในเดือน)"""
    return f"Q{ym.replace('-', '')}-{seq:03d}"


def needs_setup(report: dict | None) -> bool:
    return any(c.get("status") == "fail" and c.get("key") in STRUCTURAL for c in (report or {}).get("checks") or [])


def setup_addon() -> dict:
    return next(a for a in billing.ADDONS if a["key"] == "setup")


CYCLES = {"month": "รายเดือน", "year": "รายปี"}
YEARLY_DISCOUNT_DEFAULT = 17       # ลดเมื่อจ่ายรายปี (เจ้าของกำหนด 23 ต.ค.) · แก้ได้ที่กรอบอำนาจ (mandate_yearly_discount_pct)


def yearly_discount() -> float:
    try:
        from . import execs
        return float(execs.MANDATE["yearly_discount_pct"])
    except Exception:
        return float(YEARLY_DISCOUNT_DEFAULT)


def price_for(package: str, discount_pct: float = 0, setup: bool = False, cycle: str = "month") -> dict:
    """คำนวณจากตารางแพ็กเกจ · รายเดือน: ราคา − ส่วนลด + GEO Setup ครั้งเดียว
    · รายปี: 12 เดือน ลด yearly_discount() % (ส่วนลดพิเศษของดีลซ้อนได้อีก) GEO Setup ฟรี จ่ายครั้งเดียวทั้งปี
    คืน net = เทียบต่อเดือน (ใช้กับ CFO/ลีด/KPI) · period_total = ยอดต่อรอบบิล · first_total = ยอดงวดแรก"""
    if package not in PACKAGES:
        raise ValueError("แพ็กเกจไม่ถูกต้อง")
    if cycle not in CYCLES:
        raise ValueError("รอบบิลไม่ถูกต้อง")
    plan = billing.PLANS[package]
    disc = min(100.0, max(0.0, float(discount_pct or 0)))
    setup_list = setup_addon()["price"] if setup else 0
    if cycle == "year":
        yd = yearly_discount()
        list_period = plan["price"] * 12
        period_total = round(list_period * (1 - yd / 100) * (1 - disc / 100))
        return {"package": package, "label": plan["label"], "price": plan["price"], "discount_pct": disc, "net": round(period_total / 12),
                "setup": bool(setup), "setup_price": 0, "setup_list": setup_list, "first_total": period_total, "perks": plan["perks"],
                "cycle": "year", "period_total": period_total, "list_period": list_period, "yearly_discount": yd}
    net = round(plan["price"] * (1 - disc / 100))
    return {"package": package, "label": plan["label"], "price": plan["price"], "discount_pct": disc, "net": net,
            "setup": bool(setup), "setup_price": setup_list, "setup_list": setup_list, "first_total": net + setup_list, "perks": plan["perks"],
            "cycle": "month", "period_total": net, "list_period": plan["price"], "yearly_discount": 0}


def default_cycle() -> str:
    """รอบบิลที่เสนอก่อน — เจ้าของสั่ง 24 ต.ค. ให้เสนอรายปีก่อน (รายเดือนเป็นทางเลือกที่ลูกค้าเปลี่ยนเองได้ในหน้าใบเสนอราคา)"""
    try:
        from . import db
        v = db.get_setting("quote_default_cycle") or "year"
    except Exception:
        v = "year"
    return v if v in CYCLES else "year"


def alternative(q) -> dict:
    """ราคาอีกรอบบิลของใบเดียวกัน (แพ็กเกจ/ส่วนลด/Setup เดิม) ไว้โชว์เทียบและให้ลูกค้าเปลี่ยน"""
    other = "month" if cycle_of(q) == "year" else "year"
    pr = price_for(q["package"], q["discount_pct"], bool(q["setup"]), other)
    yearly = pr if other == "year" else price_for(q["package"], q["discount_pct"], bool(q["setup"]), "year")
    monthly = pr if other == "month" else price_for(q["package"], q["discount_pct"], bool(q["setup"]), "month")
    saving = monthly["net"] * 12 + monthly["setup_price"] - yearly["period_total"]
    return {**pr, "saving_year": saving}


VAT_RATE = 7


def vat_rate_now() -> float:
    """ราคาแพ็กเกจยังไม่รวม VAT (เจ้าของกำหนด 24 ต.ค.) → บวก 7% เมื่อบริษัทจด VAT แล้ว (ตั้งค่าบริษัท) · ยังไม่จด = 0"""
    try:
        from . import accounting
        return float(VAT_RATE) if accounting.seller()["vat"] else 0.0
    except Exception:
        return 0.0


def vat_pct(q) -> float:
    try:
        return float((q["vat_pct"] if "vat_pct" in q.keys() else 0) or 0)
    except Exception:
        return float((q.get("vat_pct") if isinstance(q, dict) else 0) or 0)


def with_vat(amount: float, pct: float) -> float:
    return round(float(amount) * (1 + pct / 100), 2)


def payable(q) -> float:
    """ยอดที่ลูกค้าจ่ายจริงงวดแรก (รวม VAT ที่บวกเพิ่ม) · ใบเก่าก่อนเปลี่ยนนโยบาย vat_pct = 0 → ราคาเดิมตามที่ตกลง"""
    return with_vat(q["first_total"], vat_pct(q))


def payable_period(q) -> float:
    return with_vat(period_total(q), vat_pct(q))


def baht(x: float) -> str:
    x = round(float(x), 2)
    return f"{x:,.0f}" if x == int(x) else f"{x:,.2f}"


def cycle_of(q) -> str:
    try:
        return (q["cycle"] if "cycle" in q.keys() else None) or "month"
    except Exception:
        return (q.get("cycle") if isinstance(q, dict) else None) or "month"


def period_total(q) -> int:
    try:
        v = q["period_total"] if "period_total" in q.keys() else None
    except Exception:
        v = q.get("period_total") if isinstance(q, dict) else None
    return int(v) if v else (int(q["first_total"]) if cycle_of(q) == "year" else int(q["net"]))


def valid_until(from_dt: datetime.datetime | None = None) -> str:
    return ((from_dt or datetime.datetime.now()) + datetime.timedelta(days=VALID_DAYS)).strftime("%Y-%m-%d")


def is_expired(q, today: str | None = None) -> bool:
    today = today or datetime.date.today().isoformat()
    return q["status"] in ("sent", "viewed") and bool(q["valid_until"]) and q["valid_until"] < today


def summary_text(q, domain: str, lang: str = "th") -> str:
    """สรุปใบเสนอราคาเป็นข้อความ (ใช้ในข้อเสนอของ CEO และอีเมล) · lang = ภาษาของลูกค้า (ข้อเสนอถึงเจ้าของเป็นไทยเสมอ)"""
    t = _tr(lang)
    label = t(billing.PLANS[q['package']]['label'])
    if cycle_of(q) == "year":
        lines = [t("ใบเสนอราคา {number} สำหรับ {dom} (รายปี)", number=q['number'], dom=domain),
                 t("• แพ็กเกจ {label} รายปี {total} บาท/ปี (เทียบ {net} บาท/เดือน จากปกติ {price}", label=label, total=f"{period_total(q):,}",
                   net=f"{q['net']:,}", price=f"{q['price']:,}")
                 + t(" · ลด {pct}%", pct=f"{yearly_discount():.0f}") + (t(" และส่วนลดพิเศษอีก {pct}%", pct=f"{q['discount_pct']:.0f}") if q["discount_pct"] else "") + ")"]
        if q["setup"]:
            lines.append(t("• GEO Setup (แก้โครงสร้างเว็บให้บอทอ่านได้) ฟรีเมื่อจ่ายรายปี (ปกติ {price} บาท)", price=f"{setup_addon()['price']:,}"))
        lines.append(t("• ยอดชำระครั้งเดียวทั้งปี {total} บาท", total=f"{q['first_total']:,}")
                     + (t(" + VAT {vat}% = {payable} บาท", vat=f"{vat_pct(q):g}", payable=baht(payable(q))) if vat_pct(q) else "")
                     + t(" · ใบเสนอราคาใช้ได้ถึง {date}", date=q['valid_until']))
        alt = alternative(q)
        lines.append(t("• หรือเลือกจ่ายรายเดือน {net} บาท/เดือน", net=f"{alt['net']:,}")
                     + (t(" + GEO Setup {price} บาท", price=f"{alt['setup_price']:,}") if alt["setup_price"] else "")
                     + t(" (รายปีประหยัดกว่า {saving} บาท) — เปลี่ยนได้ในหน้าใบเสนอราคา", saving=f"{alt['saving_year']:,}"))
        return "\n".join(lines)
    lines = [t("ใบเสนอราคา {number} สำหรับ {dom}", number=q['number'], dom=domain),
             t("• แพ็กเกจ {label} {price} บาท/เดือน", label=label, price=f"{q['price']:,}")
             + (t(" ลด {pct}% เหลือ {net} บาท/เดือน", pct=f"{q['discount_pct']:.0f}", net=f"{q['net']:,}") if q["discount_pct"] else "")]
    if q["setup"]:
        lines.append(t("• GEO Setup (แก้โครงสร้างเว็บให้บอทอ่านได้) ครั้งเดียว {price} บาท", price=f"{q['setup_price']:,}"))
    lines.append(t("• ยอดชำระงวดแรก {total} บาท", total=f"{q['first_total']:,}")
                 + (t(" + VAT {vat}% = {payable} บาท", vat=f"{vat_pct(q):g}", payable=baht(payable(q))) if vat_pct(q) else "")
                 + t(" · ใบเสนอราคาใช้ได้ถึง {date}", date=q['valid_until']))
    alt = alternative(q)
    lines.append(t("• หรือจ่ายรายปี {total} บาท (ประหยัด {saving} บาท", total=f"{alt['period_total']:,}", saving=f"{alt['saving_year']:,}")
                 + (t(" · GEO Setup ฟรี") if q["setup"] else "") + t(") — เปลี่ยนได้ในหน้าใบเสนอราคา"))
    return "\n".join(lines)


# นโยบายคืนเงิน: ตามกรอบอำนาจ refund_days (0 = ไม่คืนเงิน) · เจ้าของเลิกคืนเงิน 25 ต.ค. 2569
# ใบเสนอราคาที่ออกก่อนวันนั้นใช้เงื่อนไขที่ส่งให้ลูกค้าไปแล้ว (คืนเงิน 7 วัน) — ไม่เปลี่ยนข้อเสนอย้อนหลัง
REFUND_POLICY_ENDED = "2026-10-25"
LEGACY_REFUND_DAYS = 7


def refund_days(q=None) -> int:
    """จำนวนวันที่คืนเงินเต็มได้ (0 = ไม่คืนเงิน) · ส่งใบเสนอราคามา = ใช้เงื่อนไขตามวันที่ออกใบ"""
    if q is not None:
        try:
            created = str(q["created_at"] or "")[:10]
        except (KeyError, IndexError, TypeError):
            created = ""
        if created and created < REFUND_POLICY_ENDED:
            return LEGACY_REFUND_DAYS
    from . import execs
    return int(execs.MANDATE["refund_days"] or 0)


# เงื่อนไขหลักที่โชว์บนใบเสนอราคา (สั้น อ่านจบในหน้าเดียว) — ตรงกับกรอบอำนาจ: นโยบายคืนเงิน ไม่รับประกันผล
# ทุกประโยคเป็นภาษาไทยต้นฉบับ (msgid) แปลด้วย i18n.tr ตามภาษาที่ลูกค้าเลือก (คำแปลอยู่ที่ app/i18n/quote.json) · lang="th" = ข้อความเดิมทุกตัวอักษร
TERMS_COMMON = [
    "เราไม่รับประกันอันดับหรือการถูกเอ่ยถึงใน AI เพราะ Google และผู้ให้บริการ AI เป็นบุคคลที่สาม แต่วัดและรายงานตัวเลขจริงทุกเดือน",
    "คอนเทนต์ที่สร้างให้เป็นของลูกค้าเมื่อชำระแล้ว และลูกค้าตรวจก่อนเผยแพร่ได้ (เริ่มต้นเป็นโหมดร่าง)",
    "ข้อมูลที่ให้ไว้ใช้เพื่อให้บริการนี้เท่านั้น ไม่ส่งต่อให้ใคร ลบได้เมื่อขอ (PDPA)",
]


def _tr(lang: str):
    return lambda s, **kw: i18n.tr(s, lang, **kw)


def terms_for(q, lang: str = "th") -> list[str]:
    t = _tr(lang)
    d = refund_days(q)
    if cycle_of(q) == "year":
        base = [t("ค่าบริการเป็นรายปี ชำระครั้งเดียวล่วงหน้าทั้งปี ต่ออายุอัตโนมัติเมื่อครบปี ยกเลิกการต่ออายุได้ทุกเมื่อ มีผลเมื่อสิ้นปีสัญญา"),
                (t("ไม่พอใจภายใน {d} วันนับจากชำระ คืนเงินเต็มจำนวน — หลังจากนั้นไม่คืนเงินส่วนที่เหลือของปี แต่ใช้บริการได้จนครบปี", d=d) if d
                 else t("ค่าบริการที่ชำระแล้วไม่คืนเงิน รวมถึงส่วนที่เหลือของปี แต่ใช้บริการได้จนครบปี")),
                t("GEO Setup ฟรีเมื่อจ่ายรายปี และรายงานผลจริงทุกเดือนเหมือนรายเดือน")]
    else:
        base = [t("ค่าบริการเป็นรายเดือน ชำระล่วงหน้าต้นรอบ ยกเลิกได้ทุกเมื่อ มีผลเมื่อสิ้นรอบที่ชำระแล้ว"),
                (t("ไม่พอใจภายใน {d} วันนับจากชำระงวดแรก คืนเงินเต็มจำนวน", d=d) if d else t("ค่าบริการที่ชำระแล้วไม่คืนเงิน"))]
    v = vat_pct(q)
    return base + [t(x) for x in TERMS_COMMON] + ([t("ราคายังไม่รวมภาษีมูลค่าเพิ่ม {v}% ซึ่งบวกเพิ่มในยอดชำระและใบกำกับภาษี", v=f"{v:g}")] if v else [])


def contract_sections(company: dict, customer: dict, q, lang: str = "th") -> list[dict]:
    """สัญญาบริการมาตรฐาน (ร่าง) — เติมชื่อคู่สัญญา แพ็กเกจ ราคา จากใบเสนอราคา · ทนายตรวจแล้วค่อยเอาป้าย 'ร่าง' ออก
    · lang อื่นนอกจากไทย = คำแปลอ้างอิง (ฉบับไทยมีผลบังคับ — หน้าสัญญาแจ้งลูกค้า) · ชื่อ ที่อยู่ เลขภาษี โดเมน อีเมล เป็นข้อมูล ไม่แปล"""
    t = _tr(lang)
    plan = billing.PLANS[q["package"]]
    co = company.get("name") or "เจอ.AI"
    cust = customer.get("name") or customer.get("domain") or t("ผู้รับบริการ")
    net = f"{q['net']:,}"
    yearly = cycle_of(q) == "year"
    items = "; ".join(t(p) for p in plan["perks"])
    setup_txt = ""
    if q["setup"]:
        setup_txt = (" " + t("และงาน GEO Setup ครั้งเดียว (แก้โครงสร้างเว็บให้บอทอ่านได้ ติดตั้ง /geo, llms.txt, sitemap, แก้ robots/canonical/index)") + " "
                     + (t("โดยไม่มีค่าใช้จ่ายเพิ่มเมื่อชำระรายปี") if yearly else t("ค่าบริการ {price} บาท", price=f"{q['setup_price']:,}")))
    vt = vat_pct(q)
    vtxt = ((lambda amt: " " + t("ยังไม่รวมภาษีมูลค่าเพิ่ม ผู้รับบริการชำระภาษีมูลค่าเพิ่มร้อยละ {v} เพิ่ม รวมเป็น {total} บาท", v=f"{vt:g}", total=baht(with_vat(amt, vt))))
            if vt else (lambda amt: ""))
    fee = (t("ค่าบริการ {amount} บาทต่อปี{vat} (เทียบ {net} บาทต่อเดือน) ชำระครั้งเดียวล่วงหน้าทั้งปีผ่านช่องทางที่ผู้ให้บริการกำหนด (บัตรผ่าน Stripe หรือ PromptPay/โอนเงิน)",
             amount=f"{period_total(q):,}", vat=vtxt(period_total(q)), net=net)
           if yearly else t("ค่าบริการ {net} บาทต่อเดือน{vat} ชำระล่วงหน้าต้นรอบผ่านช่องทางที่ผู้ให้บริการกำหนด (PromptPay หรือโอนเงิน)", net=net, vat=vtxt(q["net"])))
    term = (t("สัญญามีผลหนึ่งปีนับจากวันที่ชำระ และต่ออายุอัตโนมัติครั้งละหนึ่งปี ผู้รับบริการยกเลิกการต่ออายุได้ทุกเมื่อโดยแจ้งผ่านอีเมลหรือระบบ มีผลเมื่อสิ้นปีสัญญาที่ชำระแล้ว")
            if yearly else t("สัญญามีผลเป็นรายเดือนและต่ออายุอัตโนมัติ ผู้รับบริการยกเลิกได้ทุกเมื่อโดยแจ้งผ่านอีเมลหรือระบบ มีผลเมื่อสิ้นรอบที่ชำระแล้ว"))
    d = refund_days(q)
    if d:
        refund = (t("ภายใน {d} วันนับจากชำระค่าบริการรายปี หากผู้รับบริการไม่พอใจ ผู้ให้บริการคืนเงินเต็มจำนวน หลังพ้น {d} วัน ผู้ให้บริการไม่คืนค่าบริการส่วนที่เหลือของปี แต่ผู้รับบริการใช้บริการได้จนครบปีสัญญา", d=d)
                  if yearly else t("ภายใน {d} วันนับจากชำระงวดแรก หากผู้รับบริการไม่พอใจ ผู้ให้บริการคืนเงินเต็มจำนวนที่ชำระในงวดนั้น", d=d))
    else:
        refund = (t("ค่าบริการที่ชำระแล้วไม่คืนเงิน รวมถึงค่าบริการส่วนที่เหลือของปี ทั้งนี้ผู้รับบริการใช้บริการได้จนครบปีสัญญา")
                  if yearly else t("ค่าบริการที่ชำระแล้วไม่คืนเงิน การยกเลิกมีผลเมื่อสิ้นรอบที่ชำระแล้ว และไม่มีการเรียกเก็บค่าบริการรอบถัดไป"))
    cap = (t("ความรับผิดของผู้ให้บริการภายใต้สัญญานี้จำกัดไม่เกินค่าบริการที่ผู้รับบริการชำระสำหรับปีสัญญาปัจจุบัน") if yearly
           else t("ความรับผิดของผู้ให้บริการภายใต้สัญญานี้จำกัดไม่เกินค่าบริการที่ผู้รับบริการชำระในสามเดือนล่าสุด"))
    provider = (t("ผู้ให้บริการ: {name}", name=co) + (" " + t("ที่อยู่ {address}", address=company["address"]) if company.get("address") else "")
                + (" " + t("เลขประจำตัวผู้เสียภาษี {tax_id}", tax_id=company["tax_id"]) if company.get("tax_id") else ""))
    receiver = (t("ผู้รับบริการ: {name} เจ้าของเว็บไซต์ {domain}", name=cust, domain=customer.get("domain", ""))
                + (" " + t("อีเมล {email}", email=customer["email"]) if customer.get("email") else ""))
    return [
        {"h": t("1. คู่สัญญา"), "p": [provider, receiver]},
        {"h": t("2. บริการ"), "p": [
            t("ผู้ให้บริการให้บริการแพ็กเกจ {label} ของแพลตฟอร์มเจอ.AI ประกอบด้วย {items}{setup}", label=plan["label"], items=items, setup=setup_txt),
            t("บริการเริ่มเมื่อผู้ให้บริการยืนยันการชำระงวดแรก และเปิดบัญชีให้ผู้รับบริการเข้าใช้แพลตฟอร์ม"),
        ]},
        {"h": t("3. ค่าบริการและการชำระเงิน"), "p": [
            fee,
            t("หากค้างชำระเกิน 7 วันนับจากวันครบกำหนด ผู้ให้บริการระงับบริการชั่วคราวได้จนกว่าจะชำระครบ"),
            t("ราคาตามใบเสนอราคาที่ผู้รับบริการยืนยัน การเปลี่ยนแปลงราคาต้องแจ้งล่วงหน้าอย่างน้อย 30 วันและมีผลรอบถัดไป"),
        ]},
        {"h": t("4. ระยะเวลาและการยกเลิก"), "p": [term, refund]},
        {"h": t("5. ขอบเขตและการไม่รับประกันผล"), "p": [
            t("ผู้ให้บริการไม่รับประกันอันดับบน Google หรือการถูกเอ่ยถึงในคำตอบของ AI เพราะเป็นระบบของบุคคลที่สาม ผู้ให้บริการวัดและรายงานตัวเลขจริงทุกเดือน"),
            t("ผู้ให้บริการใช้ข้อเท็จจริงของธุรกิจจากเว็บไซต์และข้อมูลที่ผู้รับบริการให้ไว้เท่านั้นในการสร้างเนื้อหา"),
        ]},
        {"h": t("6. ความร่วมมือและสิทธิ์ในเนื้อหา"), "p": [
            t("ผู้รับบริการให้สิทธิ์ที่จำเป็นต่อการติดตั้ง เช่น ปลั๊กอิน การตั้งค่า DNS หรือ Search Console และตรวจทานเนื้อหาก่อนเผยแพร่ได้"),
            t("เนื้อหาที่สร้างและเผยแพร่ภายใต้สัญญานี้เป็นของผู้รับบริการเมื่อชำระค่าบริการงวดนั้นแล้ว"),
        ]},
        {"h": t("7. ข้อมูลส่วนบุคคล"), "p": [
            t("คู่สัญญาปฏิบัติตามพระราชบัญญัติคุ้มครองข้อมูลส่วนบุคคล ผู้ให้บริการประมวลผลข้อมูลเท่าที่จำเป็นต่อการให้บริการ ไม่เปิดเผยแก่บุคคลภายนอก และลบเมื่อสิ้นสุดสัญญาเว้นแต่กฎหมายกำหนดให้เก็บ"),
        ]},
        {"h": t("8. ความรับผิด"), "p": [cap]},
        {"h": t("9. การสื่อสารอัตโนมัติ"), "p": [
            t("ข้อความบางส่วนจากผู้ให้บริการส่งโดยระบบอัตโนมัติและระบุไว้เช่นนั้น ผู้รับบริการขอยกเลิกการรับข้อความได้ทุกเมื่อ"),
        ]},
        {"h": t("10. กฎหมายที่ใช้บังคับ"), "p": [
            t("สัญญานี้อยู่ภายใต้กฎหมายไทย การยืนยันรับข้อเสนอผ่านระบบ (ชื่อ วันเวลา และที่อยู่ไอพี) ถือเป็นการตกลงตามสัญญานี้"),
        ]},
    ]
