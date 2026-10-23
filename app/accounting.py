"""ฝ่ายบัญชี — ออกเอกสารและเก็บเงินรอบถัดไป ให้ลูกค้าธุรกิจจ่ายได้และเงินเข้าครบทุกรอบ
· ใบเสร็จรับเงิน (หรือใบเสร็จรับเงิน/ใบกำกับภาษี เมื่อเปิด VAT ในตั้งค่าบริษัท) ทุกครั้งที่เงินเข้า: งวดแรก (Stripe/PromptPay) และต่ออายุ Stripe
· ลูกค้าที่จ่ายด้วย PromptPay: ออกใบแจ้งหนี้รอบถัดไปล่วงหน้า (รายเดือน 7 วัน · รายปี 30 วัน) พร้อม QR + แนบสลิป → เจ้าของยืนยันยอด → ใบเสร็จ
· ทวงตามกำหนด: วันครบกำหนด · +3 · +7 วัน (แจ้งเจ้าของ) · +14 วัน เสนอให้เจ้าของพิจารณาระงับบริการ (ระบบไม่ระงับเอง)
· เลขที่เอกสารเรียงต่อเนื่องต่อปี พ.ศ. (INV-2569-0001, RC-2569-0001) · ยกเลิกได้ด้วยเหตุผล ไม่ลบ · ออกใหม่แทนได้
· ราคาในระบบรวม VAT แล้ว (เมื่อเปิด VAT แยกภาษี 7/107) · นิติบุคคลหักภาษี ณ ที่จ่าย 3% ของมูลค่าก่อน VAT ได้ บันทึกตอนยืนยันยอด
· สรุปรายเดือน + CSV ให้ผู้ทำบัญชี (ภาษีขาย ภาษีถูกหัก ณ ที่จ่าย)"""
from __future__ import annotations
import calendar
import csv
import datetime as dt
import html as _html
import io
import json
import os
import re
import secrets
from pathlib import Path

from . import db, mailer, office

VAT_RATE = 7
WHT_RATE = 3
INVOICE_LEAD = {"month": 7, "year": 30}          # ออกใบแจ้งหนี้ก่อนครบรอบกี่วัน
REMIND_AFTER = (0, 3, 7)                         # วันหลังครบกำหนดที่ส่งเตือน (ครั้งละ 1 ฉบับ)
OVERDUE_OWNER = 7
SUSPEND_ASK = 14
BASE_URL = "https://geo.appreview.cloud"
KIND_TH = {"invoice": "ใบแจ้งหนี้", "receipt": "ใบเสร็จรับเงิน"}
STATUS_TH = {"open": "รอชำระ", "claimed": "แจ้งชำระแล้ว รอตรวจยอด", "paid": "ชำระแล้ว", "issued": "ออกแล้ว", "void": "ยกเลิก"}
SOURCE_TH = {"stripe": "บัตร (Stripe)", "promptpay": "PromptPay/โอนเงิน", "manual": "บันทึกโดยผู้ดูแล"}
HOOKS = {"pdf": None}                            # เทสต์ฉีดของปลอม (ไม่เรียก Gotenberg)


def _base() -> str:
    return os.getenv("PUBLIC_BASE_URL", BASE_URL).rstrip("/")


def enabled() -> bool:
    return os.getenv("GEO_ACCT", "1") != "0" and (db.get_setting("acct_on") or "1") == "1"


def set_enabled(on: bool) -> None:
    db.set_setting("acct_on", "1" if on else "0")


def valid_tax_id(v: str) -> bool:
    """เลขประจำตัวผู้เสียภาษี 13 หลัก ตรวจหลักสุดท้ายตามสูตรกรมสรรพากร"""
    d = re.sub(r"\D", "", v or "")
    if len(d) != 13:
        return False
    s = sum(int(d[i]) * (13 - i) for i in range(12))
    return (11 - s % 11) % 10 == int(d[12])


def seller() -> dict:
    g = lambda k: db.get_setting(k) or ""
    vat = g("company_vat") == "1" and valid_tax_id(g("company_tax_id"))
    return {"name": g("company_name") or "เจอ.AI", "address": g("company_address"), "tax_id": g("company_tax_id"),
            "branch": g("company_branch") or "สำนักงานใหญ่", "phone": g("company_phone"), "email": g("company_email"),
            "signer": g("company_signer"), "vat": vat}


def vat_on() -> bool:
    return seller()["vat"]


def split(total: float, vat: bool) -> tuple[float, float]:
    """ราคารวม VAT → (มูลค่าก่อนภาษี, ภาษี)"""
    total = round(float(total), 2)
    if not vat:
        return total, 0.0
    v = round(total * VAT_RATE / (100 + VAT_RATE), 2)
    return round(total - v, 2), v


def wht_of(subtotal: float) -> float:
    return round(float(subtotal) * WHT_RATE / 100, 2)


_D = ["", "หนึ่ง", "สอง", "สาม", "สี่", "ห้า", "หก", "เจ็ด", "แปด", "เก้า"]
_P = ["", "สิบ", "ร้อย", "พัน", "หมื่น", "แสน"]


def _words(n: int) -> str:
    if n == 0:
        return ""
    if n >= 1_000_000:
        return _words(n // 1_000_000) + "ล้าน" + _words(n % 1_000_000)
    out, s = "", str(n)
    for i, ch in enumerate(s):
        d, pos = int(ch), len(s) - i - 1
        if d == 0:
            continue
        if pos == 1 and d == 1:
            out += "สิบ"
        elif pos == 1 and d == 2:
            out += "ยี่สิบ"
        elif pos == 0 and d == 1 and len(s) > 1:
            out += "เอ็ด"
        else:
            out += _D[d] + _P[pos]
    return out


def bahttext(amount: float) -> str:
    """จำนวนเงินเป็นตัวอักษรไทย เช่น 9,900 → เก้าพันเก้าร้อยบาทถ้วน"""
    satang = int(round(float(amount) * 100))
    baht, st = divmod(satang, 100)
    if baht == 0 and st == 0:
        return "ศูนย์บาทถ้วน"
    return (_words(baht) + "บาท" if baht else "") + (_words(st) + "สตางค์" if st else "ถ้วน")


def add_months(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _date(s) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def th_date(s) -> str:
    d = _date(s)
    return f"{d.day:02d}/{d.month:02d}/{d.year + 543}" if d else ""


def _months(q) -> int:
    from . import quotes as Q
    return 12 if Q.cycle_of(q) == "year" else 1


def period_of(q, start: dt.date) -> tuple[str, str]:
    return start.isoformat(), (add_months(start, _months(q)) - dt.timedelta(days=1)).isoformat()


def buyer_of(q, lead) -> dict:
    g = lambda k: (q[k] if k in q.keys() else None) or ""
    email = (lead["email"] or "") if lead else ""
    return {"buyer_name": g("buyer_name") or (lead["name"] if lead and lead["name"] else "") or (lead["domain"] if lead else ""),
            "buyer_tax_id": g("buyer_tax_id"), "buyer_branch": g("buyer_branch"), "buyer_address": g("buyer_address"),
            "buyer_email": email}


def save_buyer(quote_id: int, name: str = "", tax_id: str = "", branch: str = "", address: str = "") -> str:
    """เก็บข้อมูลออกเอกสารของผู้ซื้อ · คืนข้อความผิดพลาด ('' = สำเร็จ)"""
    tid = re.sub(r"\D", "", tax_id or "")
    if tid and not valid_tax_id(tid):
        return "เลขประจำตัวผู้เสียภาษีไม่ถูกต้อง (13 หลัก)"
    br = (branch or "").strip()
    if re.fullmatch(r"0*", br) and br:
        br = "สำนักงานใหญ่"
    db.update_quote(quote_id, buyer_name=(name or "").strip()[:200] or None, buyer_tax_id=tid or None,
                    buyer_branch=br[:60] or None, buyer_address=(address or "").strip()[:400] or None)
    return ""


def _number(kind: str, when: dt.date) -> str:
    prefix = f"{'INV' if kind == 'invoice' else 'RC'}-{when.year + 543}-"
    last = db.last_acct_number(kind, prefix)
    seq = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{seq:04d}"


def _items(q, first: bool) -> list[dict]:
    """รายการในเอกสาร (ยอดรวม VAT แล้ว)"""
    from . import billing, quotes as Q
    label = billing.PLANS[q["package"]]["label"]
    yearly = Q.cycle_of(q) == "year"
    disc = f" ลดพิเศษ {q['discount_pct']:.0f}%" if q["discount_pct"] else ""
    if yearly:
        items = [{"desc": f"ค่าบริการเจอ.AI แพ็กเกจ {label} รายปี (ลด {Q.yearly_discount():.0f}%{disc})", "amount": float(Q.period_total(q))}]
        if first and q["setup"]:
            items.append({"desc": "GEO Setup (ฟรีเมื่อชำระรายปี)", "amount": 0.0})
        return items
    items = [{"desc": f"ค่าบริการเจอ.AI แพ็กเกจ {label} รายเดือน{disc}", "amount": float(q["net"])}]
    if first and q["setup"] and q["setup_price"]:
        items.append({"desc": "GEO Setup (แก้โครงสร้างเว็บให้บอทอ่านได้) ครั้งเดียว", "amount": float(q["setup_price"])})
    return items


def _create(kind: str, q, lead, items: list[dict], period: tuple[str, str], source: str, status: str,
            stripe_ref: str | None = None, due: str | None = None, invoice_id: int | None = None, wht: float = 0, total: float | None = None) -> int:
    s = seller()
    total = round(float(total if total is not None else sum(i["amount"] for i in items)), 2)
    sub, vat = split(total, s["vat"])
    today = dt.date.today()
    for _ in range(5):                                   # เลขที่ชนกัน (สองโปรเซสพร้อมกัน) → ลองเลขถัดไป
        try:
            return db.add_acct_doc(kind=kind, number=_number(kind, today), token=secrets.token_urlsafe(9), quote_id=q["id"], lead_id=q["lead_id"],
                                   tenant_id=q["tenant_id"], invoice_id=invoice_id, period_start=period[0], period_end=period[1],
                                   description=items[0]["desc"], items_json=json.dumps(items, ensure_ascii=False), subtotal=sub, vat=vat,
                                   total=total, wht=wht, vat_mode="vat7" if s["vat"] else "none", source=source, stripe_ref=stripe_ref,
                                   status=status, due_date=due, paid_at=db.now() if kind == "receipt" else None, **buyer_of(q, lead))
        except Exception as e:
            if "unique" not in str(e).lower() and "duplicate" not in str(e).lower():
                raise
    raise RuntimeError("ออกเลขที่เอกสารไม่ได้")


def _record(lead_id: int, kind: str, note: str) -> None:
    db.add_lead_event(lead_id, kind, note, "acct")


# ---------- เอกสาร: หน้าเว็บ · PDF · อีเมล ----------
def title(doc) -> str:
    if doc["kind"] == "receipt":
        return "ใบเสร็จรับเงิน/ใบกำกับภาษี" if doc["vat_mode"] == "vat7" else "ใบเสร็จรับเงิน"
    return "ใบแจ้งหนี้"


def doc_url(doc) -> str:
    return f"{_base()}/{'bill' if doc['kind'] == 'invoice' else 'doc'}/{doc['token']}"


def context(doc, pdf: bool = False) -> dict:
    d = dict(doc)
    items = json.loads(d.get("items_json") or "[]") or [{"desc": d.get("description") or "", "amount": d["total"]}]
    inv = db.get_acct_doc(d["invoice_id"]) if d.get("invoice_id") else None
    return {"doc": d, "items": items, "seller": seller(), "title": title(doc), "status_th": STATUS_TH.get(d["status"], d["status"]),
            "source_th": SOURCE_TH.get(d.get("source") or "", d.get("source") or ""), "words": bahttext(d["total"]),
            "net_received": round(d["total"] - (d.get("wht") or 0), 2), "wht_suggest": wht_of(d["subtotal"]),
            "issued": th_date(d["created_at"]), "paid": th_date(d.get("paid_at")), "due": th_date(d.get("due_date")),
            "period": f"{th_date(d.get('period_start'))} – {th_date(d.get('period_end'))}" if d.get("period_start") else "",
            "invoice_number": inv["number"] if inv else "", "pdf": pdf, "base": _base()}


def render(doc, pdf: bool = False, extra: dict | None = None) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    env = Environment(loader=FileSystemLoader(str(Path(__file__).parent / "templates")), autoescape=select_autoescape(["html"]))
    return env.get_template("acct_doc.html").render({**context(doc, pdf), **(extra or {})})


def pdf_bytes(doc) -> bytes | None:
    try:
        if HOOKS["pdf"]:
            return HOOKS["pdf"](render(doc, pdf=True))
        from . import report
        return report.to_pdf(render(doc, pdf=True))
    except Exception:
        return None


def send(doc, subject: str | None = None, intro: str = "") -> str:
    """ส่งเอกสารทางอีเมล (แนบ PDF ถ้าสร้างได้) · คืนผลเป็นข้อความ"""
    to = (doc["buyer_email"] or "").strip()
    if not (to and mailer.is_deliverable(to) and mailer.config()):
        return "ไม่มีอีเมลที่ส่งได้ — ส่งลิงก์ให้ลูกค้าเอง"
    link = doc_url(doc)
    t = title(doc)
    lines = [f"• {t} เลขที่ {doc['number']}", f"• ยอด {doc['total']:,.2f} บาท"]
    if doc["kind"] == "invoice":
        lines.append(f"• ครบกำหนดชำระ {th_date(doc['due_date'])}")
    if doc["period_start"]:
        lines.append(f"• รอบบริการ {th_date(doc['period_start'])} – {th_date(doc['period_end'])}")
    body = (intro or (f"ขอบคุณที่ชำระค่าบริการครับ แนบ{t}มาให้" if doc["kind"] == "receipt" else "แจ้งค่าบริการรอบถัดไปครับ ชำระผ่าน PromptPay แล้วแนบสลิปในลิงก์ด้านล่าง"))
    text = f"สวัสดีครับ\n\n{body}\n\n" + "\n".join(lines) + f"\n\nดู/ดาวน์โหลด: {link}\n\nฝ่ายบัญชี เจอ.AI"
    html = mailer.wrap_html(t, f"<p>{_html.escape(body)}</p><p>" + "<br>".join(_html.escape(x) for x in lines) + f"</p><p><a href='{link}'>{'ชำระและแนบสลิป' if doc['kind'] == 'invoice' else 'ดู/ดาวน์โหลดเอกสาร'}</a></p>",
                            "ข้อความนี้ส่งจากฝ่ายบัญชีของเจอ.AI")
    pdf = pdf_bytes(doc)
    try:
        mailer.send(to, subject or f"{t} {doc['number']} จากเจอ.AI", html, text=text,
                    attachments=[(f"{doc['number']}.pdf", pdf, "application/pdf")] if pdf else ())
    except Exception as e:
        return f"ส่งอีเมลไม่สำเร็จ ({type(e).__name__})"
    db.update_acct_doc(doc["id"], sent_at=db.now())
    return f"ส่งอีเมลถึง {to}" + ("" if pdf else " (ไม่มี PDF แนบ — สร้างไม่ได้ตอนนี้)")


# ---------- ใบเสร็จ ----------
def issue_receipt(q, items: list[dict], source: str, period: tuple[str, str], stripe_ref: str | None = None,
                  invoice=None, wht: float = 0, total: float | None = None, send_mail: bool = True):
    """ออกใบเสร็จครั้งเดียวต่อการรับเงินหนึ่งครั้ง (Stripe ref / ใบแจ้งหนี้ / รอบเดียวกันซ้ำ = คืนใบเดิม)"""
    if stripe_ref:
        old = db.find_acct_doc("receipt", stripe_ref=stripe_ref)
    elif invoice is not None:
        old = db.find_acct_doc("receipt", invoice_id=invoice["id"])
    else:
        old = db.find_acct_doc("receipt", quote_id=q["id"], period_start=period[0])
    if old and old["status"] != "void":
        return old
    lead = db.get_lead(q["lead_id"])
    did = _create("receipt", q, lead, items, period, source, "issued", stripe_ref=stripe_ref,
                  invoice_id=invoice["id"] if invoice is not None else None, wht=wht, total=total)
    doc = db.get_acct_doc(did)
    res = send(doc) if send_mail else "ไม่ส่งอีเมล"
    _record(q["lead_id"], "acct_receipt", f"{doc['number']} {doc['total']:,.0f} บาท ({SOURCE_TH.get(source, source)}) · {res}")
    return db.get_acct_doc(did)


def first_receipt(q) -> None:
    """เรียกจาก pipeline.confirm_payment — เงินงวดแรกเข้าแล้ว (Stripe หรือ PromptPay)"""
    if not enabled():
        return
    start = _date(q["paid_at"]) or dt.date.today()
    issue_receipt(q, _items(q, first=True), "stripe" if q["paid_via"] == "stripe" else "promptpay", period_of(q, start),
                  total=float(q["first_total"]))


def stripe_receipt(q, inv: dict) -> None:
    """Stripe ต่ออายุสำเร็จ (invoice.paid ที่ไม่ใช่บิลแรก) → ใบเสร็จของเรา (ใบของ Stripe ไม่ใช่ใบกำกับภาษีไทย)"""
    if not enabled():
        return
    amt = round(int(inv.get("amount_paid") or 0) / 100, 2)
    if amt <= 0:
        return
    per = (((inv.get("lines") or {}).get("data") or [{}])[0].get("period")) or {}
    ts0, ts1 = per.get("start") or inv.get("period_start"), per.get("end") or inv.get("period_end")
    if ts0 and ts1:
        period = (dt.date.fromtimestamp(int(ts0)).isoformat(), (dt.date.fromtimestamp(int(ts1)) - dt.timedelta(days=1)).isoformat())
    else:
        period = period_of(q, dt.date.today())
    items = _items(q, first=False)
    items[0]["amount"] = amt
    issue_receipt(q, items, "stripe", period, stripe_ref=inv.get("id"), total=amt)


def capture_stripe_buyer(q, session: dict) -> None:
    """เก็บชื่อ/ที่อยู่/เลขผู้เสียภาษีที่ลูกค้ากรอกใน Stripe Checkout (ไม่ทับที่กรอกไว้แล้ว)"""
    cd = session.get("customer_details") or {}
    if not cd or (q["buyer_name"] if "buyer_name" in q.keys() else None):
        return
    a = cd.get("address") or {}
    addr = " ".join(x for x in (a.get("line1"), a.get("line2"), a.get("city"), a.get("state"), a.get("postal_code")) if x)
    tax = next((t.get("value") for t in (cd.get("tax_ids") or []) if t.get("value")), "")
    tax = tax if valid_tax_id(tax) else ""
    db.update_quote(q["id"], buyer_name=(cd.get("name") or "")[:200] or None, buyer_tax_id=re.sub(r"\D", "", tax) or None,
                    buyer_address=addr[:400] or None)


# ---------- ใบแจ้งหนี้รอบถัดไป (PromptPay) ----------
def customers() -> list:
    """ลูกค้าที่จ่ายด้วย PromptPay/โอน (ไม่ใช่ Stripe ซึ่งตัดบัตรเอง) และยังใช้บริการอยู่ — ใบล่าสุดต่อลีด"""
    out, seen = [], set()
    for q in db.list_quotes(status="paid", limit=5000):
        if q["lead_id"] in seen:
            continue
        seen.add(q["lead_id"])
        if q["paid_via"] == "stripe" or not q["paid_at"]:
            continue
        t = db.get_tenant(q["tenant_id"]) if q["tenant_id"] else None
        if not t or t["plan"] == "free":
            continue
        out.append(q)
    return out


def issue_invoice(q, start: dt.date, send_mail: bool = True):
    from . import quotes as Q
    lead = db.get_lead(q["lead_id"])
    did = _create("invoice", q, lead, _items(q, first=False), period_of(q, start), "promptpay", "open",
                  due=start.isoformat(), total=float(Q.period_total(q)))
    doc = db.get_acct_doc(did)
    res = send(doc) if send_mail else "ไม่ส่งอีเมล"
    _record(q["lead_id"], "acct_invoice", f"{doc['number']} {doc['total']:,.0f} บาท ครบกำหนด {th_date(doc['due_date'])} · {res}")
    return doc


def due_invoices(q, today: dt.date) -> list[dt.date]:
    """รอบที่ถึงเวลาออกใบแจ้งหนี้แล้วแต่ยังไม่มี"""
    from . import quotes as Q
    paid = _date(q["paid_at"])
    months, lead_days = _months(q), INVOICE_LEAD[Q.cycle_of(q)]
    out = []
    for n in range(1, 61):
        start = add_months(paid, months * n)
        if start - dt.timedelta(days=lead_days) > today:
            break
        if not db.find_acct_doc("invoice", quote_id=q["id"], period_start=start.isoformat()):
            out.append(start)
    return out


def _overdue_steps(inv, today: dt.date) -> list[str]:
    out = []
    due = _date(inv["due_date"])
    if not due or inv["status"] != "open":
        return out
    over = (today - due).days
    sent = int(inv["reminders"] or 0)
    for i, d in enumerate(REMIND_AFTER):
        if over >= d and sent <= i:
            intro = ("วันนี้ครบกำหนดชำระค่าบริการรอบถัดไปครับ" if d == 0 else
                     f"ค่าบริการรอบนี้เลยกำหนดมา {over} วันแล้วครับ ถ้าชำระแล้วแนบสลิปในลิงก์ได้เลย ถ้ามีปัญหาตอบอีเมลนี้ได้ครับ")
            res = send(inv, subject=f"{'ครบกำหนด' if d == 0 else 'เตือน'}ชำระ {inv['number']} · เจอ.AI", intro=intro)
            db.update_acct_doc(inv["id"], reminders=i + 1)
            _record(inv["lead_id"], "acct_reminder", f"{inv['number']} เตือนครั้งที่ {i + 1} (เลยกำหนด {max(over, 0)} วัน) · {res}")
            out.append(f"remind:{i + 1}")
            break
    done = [e["note"] or "" for e in db.list_lead_events(inv["lead_id"], 300) if e["kind"] == "acct_overdue"]
    dom = (db.get_lead(inv["lead_id"]) or {"domain": "?"})["domain"]
    from . import pipeline
    if over >= SUSPEND_ASK and not any(n.startswith(f"{inv['number']} 14") for n in done):
        _record(inv["lead_id"], "acct_overdue", f"{inv['number']} 14 วัน")
        pipeline.alert_admins(f"⛔ {dom} ค้างชำระ {inv['number']} {inv['total']:,.0f} บาท มา {over} วัน — พิจารณาระงับบริการที่หน้าบัญชี (ระบบไม่ระงับเอง)",
                              "/admin/accounting", "payment", subject=f"⛔ {dom} ค้างชำระ {over} วัน")
        out.append("ask_suspend")
    elif over >= OVERDUE_OWNER and not any(n.startswith(f"{inv['number']} 7") for n in done):
        _record(inv["lead_id"], "acct_overdue", f"{inv['number']} 7 วัน")
        pipeline.alert_admins(f"⚠️ {dom} ค้างชำระ {inv['number']} {inv['total']:,.0f} บาท มา {over} วัน — ควรโทรตาม", "/admin/accounting", "payment",
                              subject=f"⚠️ {dom} ค้างชำระ {over} วัน")
        out.append("owner_alert")
    return out


@office.track("acct", "บัญชี: ออกใบแจ้งหนี้ · ทวงชำระ")
def run(now: dt.datetime | None = None) -> dict:
    if not enabled():
        res = {"skipped": "ปิดอยู่", "at": db.now()}
        db.set_setting("acct_last", json.dumps(res, ensure_ascii=False))
        return res
    today = (now or dt.datetime.now()).date()
    res = {"at": db.now(), "invoiced": [], "actions": {}, "errors": 0}
    for q in customers():
        try:
            for start in due_invoices(q, today):
                res["invoiced"].append(issue_invoice(q, start)["number"])
        except Exception as e:
            res["errors"] += 1
            res["actions"][f"quote {q['id']}"] = [f"error {type(e).__name__}"]
    for inv in db.list_acct_docs("invoice", "open", limit=2000):
        try:
            acts = _overdue_steps(inv, today)
            if acts:
                res["actions"][inv["number"]] = acts
        except Exception as e:
            res["errors"] += 1
            res["actions"][inv["number"]] = [f"error {type(e).__name__}"]
    db.set_setting("acct_last", json.dumps(res, ensure_ascii=False))
    return res


# ---------- ลูกค้าแจ้งชำระ → เจ้าของยืนยัน → ใบเสร็จ ----------
def claim(inv, slip_path: str | None) -> int:
    from . import pipeline
    db.update_acct_doc(inv["id"], status="claimed", slip_path=slip_path or inv["slip_path"])
    lead = db.get_lead(inv["lead_id"])
    _record(inv["lead_id"], "payment_claimed", f"{inv['number']} ยอด {inv['total']:,.0f} บาท" + (" · แนบสลิป" if slip_path else ""))
    pl = {"acct_doc_id": inv["id"], "number": inv["number"], "amount": inv["total"], "slip": bool(slip_path)}
    did = db.add_decision("cfo", "payment", f"ยอดเข้า {inv['total']:,.0f} บาท — {lead['domain']} ({inv['number']})",
                          f"ลูกค้าแจ้งชำระใบแจ้งหนี้ {inv['number']} ยอด {inv['total']:,.2f} บาท\nเช็กบัญชีรับเงินแล้วกดอนุมัติ → ระบบออกใบเสร็จและส่งให้ลูกค้าเอง"
                          + ("" if slip_path else "\nลูกค้าไม่ได้แนบสลิป") + f"\nถ้าลูกค้าหักภาษี ณ ที่จ่าย {WHT_RATE}% ยืนยันที่หน้าบัญชีแทน", pl, lead_id=lead["id"])
    db.update_acct_doc(inv["id"], decision_id=did)
    pipeline.alert_admins(f"💳 {lead['domain']} แจ้งชำระ {inv['number']} {inv['total']:,.0f} บาท — เช็กบัญชีแล้วยืนยันยอด", "/admin/accounting", "payment",
                          subject=f"💳 {lead['domain']} แจ้งชำระ {inv['total']:,.0f} บาท")
    return did


def mark_paid(inv, who: str = "admin", wht: bool = False) -> str:
    if inv["kind"] != "invoice":
        return "ไม่ใช่ใบแจ้งหนี้"
    if inv["status"] == "paid":
        return "ใบนี้ชำระแล้ว"
    if inv["status"] == "void":
        return "ใบนี้ถูกยกเลิกแล้ว"
    q = db.get_quote(inv["quote_id"])
    items = json.loads(inv["items_json"] or "[]")
    w = wht_of(inv["subtotal"]) if wht else 0.0
    rc = issue_receipt(q, items, "promptpay", (inv["period_start"], inv["period_end"]), invoice=inv, wht=w, total=inv["total"])
    db.update_acct_doc(inv["id"], status="paid", paid_at=db.now())
    t = db.get_tenant(q["tenant_id"]) if q["tenant_id"] else None
    if t and t["plan"] != q["package"]:
        db.set_plan(q["tenant_id"], q["package"])                     # เคยระงับเพราะค้างชำระ → คืนแผน
    db.add_lead_event(inv["lead_id"], "paid", f"ต่ออายุ {inv['number']} {inv['total']:,.0f} บาท (PromptPay)" + (f" หัก ณ ที่จ่าย {w:,.2f}" if w else ""), who)
    for d in db.list_decisions("proposed", 300):
        if d["kind"] == "payment" and json.loads(d["payload_json"] or "{}").get("acct_doc_id") == inv["id"]:
            db.set_decision(d["id"], "executed", who, f"ออกใบเสร็จ {rc['number']}")
    return f"ยืนยันยอด {inv['number']} แล้ว · ออกใบเสร็จ {rc['number']}" + (f" (หัก ณ ที่จ่าย {w:,.2f} บาท — ขอหนังสือรับรองจากลูกค้า)" if w else "")


def void(doc, reason: str, who: str = "admin") -> str:
    if doc["status"] == "void":
        return "ยกเลิกไปแล้ว"
    if len((reason or "").strip()) < 3:
        return "ใส่เหตุผลการยกเลิก"
    db.update_acct_doc(doc["id"], status="void", void_reason=f"{reason.strip()[:200]} · โดย {who} {db.now()[:16]}")
    if doc["kind"] == "receipt" and doc["invoice_id"]:
        inv = db.get_acct_doc(doc["invoice_id"])
        if inv and inv["status"] == "paid":
            db.update_acct_doc(inv["id"], status="open")
    _record(doc["lead_id"], "note", f"ยกเลิก{KIND_TH[doc['kind']]} {doc['number']}: {reason.strip()[:200]}")
    return f"ยกเลิก {doc['number']} แล้ว (เลขที่นี้ไม่ถูกนำกลับมาใช้)"


def reissue(doc, who: str = "admin") -> str:
    """ยกเลิกใบเสร็จเดิมแล้วออกใหม่ด้วยข้อมูลผู้ซื้อปัจจุบัน (เช่น ลูกค้าขอแก้ชื่อ/เลขผู้เสียภาษี)"""
    if doc["kind"] != "receipt" or doc["status"] == "void":
        return "ออกใหม่ได้เฉพาะใบเสร็จที่ยังไม่ยกเลิก"
    q = db.get_quote(doc["quote_id"])
    db.update_acct_doc(doc["id"], status="void", void_reason=f"ออกใบใหม่แทน · โดย {who} {db.now()[:16]}")
    inv = db.get_acct_doc(doc["invoice_id"]) if doc["invoice_id"] else None
    did = _create("receipt", q, db.get_lead(q["lead_id"]), json.loads(doc["items_json"] or "[]"), (doc["period_start"], doc["period_end"]),
                  doc["source"], "issued", stripe_ref=None, invoice_id=inv["id"] if inv else None, wht=doc["wht"], total=doc["total"])
    new = db.get_acct_doc(did)
    res = send(new, intro=f"แนบใบเสร็จฉบับแก้ไขแทนเลขที่ {doc['number']} ครับ")
    _record(q["lead_id"], "acct_receipt", f"{new['number']} ออกแทน {doc['number']} · {res}")
    return f"ออก {new['number']} แทน {doc['number']} แล้ว · {res}"


def suspend(q, who: str = "admin") -> str:
    if q["tenant_id"]:
        db.set_plan(q["tenant_id"], "free")
    db.add_lead_event(q["lead_id"], "note", "ระงับบริการเพราะค้างชำระ (ลดแผนเป็น free) — ชำระแล้วระบบคืนแผนให้เอง", who)
    return "ระงับบริการแล้ว — ยืนยันยอดเมื่อไหร่ระบบคืนแผนให้เอง"


# ---------- สรุป · CSV ----------
def _month_range(month: str) -> tuple[str, str]:
    y, m = (int(x) for x in month.split("-"))
    nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
    return f"{y:04d}-{m:02d}-01", nxt.isoformat()


def summary(month: str | None = None) -> dict:
    month = month or dt.date.today().strftime("%Y-%m")
    a, b = _month_range(month)
    rcs = [r for r in db.list_acct_docs("receipt", since=a, until=b, limit=5000) if r["status"] != "void"]
    today = dt.date.today()
    open_inv = [i for i in db.list_acct_docs("invoice", limit=5000) if i["status"] in ("open", "claimed")]
    overdue = [i for i in open_inv if _date(i["due_date"]) and _date(i["due_date"]) < today]
    r2 = lambda v: round(v, 2)
    return {"month": month, "receipts": len(rcs), "total": r2(sum(r["total"] for r in rcs)), "vat": r2(sum(r["vat"] for r in rcs)),
            "subtotal": r2(sum(r["subtotal"] for r in rcs)), "wht": r2(sum(r["wht"] or 0 for r in rcs)),
            "received": r2(sum(r["total"] - (r["wht"] or 0) for r in rcs)),
            "outstanding": r2(sum(i["total"] for i in open_inv)), "open": len(open_inv),
            "overdue": len(overdue), "overdue_total": r2(sum(i["total"] for i in overdue))}


def csv_month(month: str) -> str:
    a, b = _month_range(month)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ประเภท", "เลขที่", "วันที่", "ผู้ซื้อ", "เลขผู้เสียภาษี", "สาขา", "รายการ", "มูลค่าก่อน VAT", "VAT", "รวม",
                "หัก ณ ที่จ่าย", "รับสุทธิ", "ช่องทาง", "สถานะ", "อ้างอิง"])
    for r in reversed(db.list_acct_docs(since=a, until=b, limit=10000)):
        w.writerow([title(r), r["number"], th_date(r["created_at"]), r["buyer_name"] or "", r["buyer_tax_id"] or "", r["buyer_branch"] or "",
                    r["description"] or "", f"{r['subtotal']:.2f}", f"{r['vat']:.2f}", f"{r['total']:.2f}", f"{(r['wht'] or 0):.2f}",
                    f"{r['total'] - (r['wht'] or 0):.2f}" if r["kind"] == "receipt" else "", SOURCE_TH.get(r["source"] or "", r["source"] or ""),
                    STATUS_TH.get(r["status"], r["status"]) + (f" ({r['void_reason']})" if r["void_reason"] else ""), r["stripe_ref"] or ""])
    return "﻿" + buf.getvalue()                       # BOM ให้ Excel อ่านภาษาไทยถูก


def status_text() -> str | None:
    s = summary()
    if not (s["receipts"] or s["open"]):
        return None
    t = f"บัญชี: เดือนนี้รับ {s['total']:,.0f} บาท ({s['receipts']} ใบเสร็จ)"
    if s["open"]:
        t += f" · ค้างรับ {s['outstanding']:,.0f} บาท ({s['open']} ใบ" + (f" เกินกำหนด {s['overdue']}" if s["overdue"] else "") + ")"
    return t
