"""Stripe — ชำระงวดแรกด้วยบัตรผ่าน Stripe Checkout แบบสมัครสมาชิกรายเดือน (ต่ออายุอัตโนมัติ) + GEO Setup ครั้งเดียว
webhook ยืนยันยอดเอง → ระบบเปิดบัญชี+แบรนด์ทันทีโดยไม่ต้องรอคนเช็กบัญชี · ต่ออายุ/ค้างชำระ/ยกเลิก มาทาง webhook เช่นกัน
คีย์เก็บเข้ารหัส (Fernet) ใน settings · ยิง API ตรงด้วย httpx (ไม่เพิ่ม SDK) · PromptPay ของ Stripe ใช้กับรายเดือนไม่ได้ จึงคงช่องทางโอน+สลิปไว้เป็นทางเลือก"""
from __future__ import annotations
import hashlib
import hmac
import json
import time

import httpx

from . import billing, db, wp_client

API = "https://api.stripe.com/v1"
TIMEOUT = 20
TOLERANCE = 300                                    # วินาที — กัน replay ของ webhook เก่า
EVENTS = ("checkout.session.completed", "invoice.paid", "invoice.payment_failed", "customer.subscription.updated",
          "customer.subscription.deleted", "charge.refunded", "credit_note.created")
CURRENCY = "thb"
# แคตตาล็อกใน Stripe (สร้างให้เองครั้งแรกที่ใช้ ผูกด้วย lookup_key ที่มีราคาอยู่ในชื่อ — เปลี่ยนราคาในตาราง = ได้ price ใหม่อัตโนมัติ)
CATALOG = {"pro": ("geo_pro_month", "month"), "agency": ("geo_agency_month", "month"), "setup": ("geo_setup_once", None),
           "pro_year": ("geo_pro_year", "year"), "agency_year": ("geo_agency_year", "year")}   # รายปี = 12 เดือน − ส่วนลดรายปี


def _get(key: str) -> str:
    v = (db.get_setting(key) or "").strip()
    if not v:
        return ""
    try:
        return wp_client.decrypt(v)
    except Exception:
        return ""


def secret_key() -> str:
    return _get("stripe_secret_key")


def webhook_secret() -> str:
    return _get("stripe_webhook_secret")


def save_config(secret: str, whsec: str) -> None:
    """เว้นว่าง = คงค่าเดิม"""
    if secret.strip():
        db.set_setting("stripe_secret_key", wp_client.encrypt(secret.strip()))
    if whsec.strip():
        db.set_setting("stripe_webhook_secret", wp_client.encrypt(whsec.strip()))


def available() -> bool:
    return bool(secret_key())


def mode() -> str:
    sk = secret_key()
    return "live" if ("_live_" in sk) else ("test" if sk else "")


def status() -> str:
    if not secret_key():
        return "ยังไม่มีคีย์"
    if not webhook_secret():
        return f"มีคีย์ ({mode()}) แต่ยังไม่มี webhook secret — ลูกค้าจ่ายได้ แต่ระบบจะยืนยันยอดเองเฉพาะตอนลูกค้ากลับมาหน้าใบเสนอราคา"
    return f"พร้อม ({mode()})"


def _mask(v: str) -> str:
    return (v[:7] + "…" + v[-4:]) if len(v) > 14 else ("ตั้งแล้ว" if v else "")


def public_config() -> dict:
    sk, wh = secret_key(), webhook_secret()
    return {"secret_set": bool(sk), "whsec_set": bool(wh), "ready": bool(sk and wh), "mode": mode(), "status": status(),
            "key_mask": _mask(sk), "whsec_mask": _mask(wh), "webhook_path": "/stripe/webhook", "events": EVENTS}


# ---------- Stripe API ----------
def _post(path: str, data: dict, idem: str | None = None) -> dict:
    headers = {"Idempotency-Key": idem} if idem else {}
    r = httpx.post(f"{API}{path}", data=data, auth=(secret_key(), ""), timeout=TIMEOUT, headers=headers)
    body = r.json() if r.content else {}
    if r.status_code >= 400:
        raise RuntimeError((body.get("error") or {}).get("message") or f"Stripe HTTP {r.status_code}")
    return body


def _get_json(path: str) -> dict:
    r = httpx.get(f"{API}{path}", auth=(secret_key(), ""), timeout=TIMEOUT)
    body = r.json() if r.content else {}
    if r.status_code >= 400:
        raise RuntimeError((body.get("error") or {}).get("message") or f"Stripe HTTP {r.status_code}")
    return body


# ---------- แคตตาล็อก (Products/Prices/Coupons ใน Stripe) ----------
def _amount(kind: str) -> int:
    if kind == "setup":
        return int(next(a for a in billing.ADDONS if a["key"] == "setup")["price"])
    if kind.endswith("_year"):
        from . import quotes as Q
        return int(Q.price_for(kind[:-5], 0, False, "year")["period_total"])   # ราคาเปลี่ยน → lookup key ใหม่ ราคาเก่าไม่ถูกแก้
    return int(billing.PLANS[kind]["price"])


def _name(kind: str, company: str) -> str:
    if kind == "setup":
        return f"{company} GEO Setup (ครั้งเดียว)"
    if kind.endswith("_year"):
        return f"{company} แพ็กเกจ {billing.PLANS[kind[:-5]]['label']} รายปี"
    return f"{company} แพ็กเกจ {billing.PLANS[kind]['label']} รายเดือน"


def ensure_price(kind: str, company: str = "เจอ.AI") -> dict:
    """คืน {price, product} ของรายการนี้ใน Stripe — หาด้วย lookup_key ก่อน ไม่มีค่อยสร้าง (ผลแคชใน settings)"""
    base_key, interval = CATALOG[kind]
    amount = _amount(kind)
    key = f"{base_key}_{amount}"
    cached = db.get_setting(f"stripe_price_{key}")
    if cached and "|" in cached:
        pid, prod = cached.split("|", 1)
        return {"price": pid, "product": prod}
    found = _get_json(f"/prices?lookup_keys[]={key}&active=true&limit=1").get("data") or []
    if found:
        pid, prod = found[0]["id"], found[0]["product"] if isinstance(found[0].get("product"), str) else (found[0].get("product") or {}).get("id", "")
    else:
        product = _post("/products", {"name": _name(kind, company), "metadata[geo_kind]": kind}, idem=f"geo-product-{key}")
        data = {"product": product["id"], "currency": CURRENCY, "unit_amount": str(amount * 100), "lookup_key": key, "transfer_lookup_key": "true"}
        if interval:
            data["recurring[interval]"] = interval
        price = _post("/prices", data, idem=f"geo-price-{key}")
        pid, prod = price["id"], product["id"]
    db.set_setting(f"stripe_price_{key}", f"{pid}|{prod}")
    return {"price": pid, "product": prod}


def ensure_coupon(pct: float, product_id: str, package: str) -> str:
    """คูปองลด x% เฉพาะแพ็กเกจรายเดือน (ไม่ลด Setup) — ใช้ซ้ำได้ทุกดีลที่ลดเท่ากัน"""
    pct_i = int(round(pct))
    cid = f"geo-disc-{pct_i}-{package}"
    try:
        _get_json(f"/coupons/{cid}")
        return cid
    except RuntimeError:
        pass
    label = billing.PLANS[package.removesuffix("_year")]["label"] + (" รายปี" if package.endswith("_year") else "")
    _post("/coupons", {"id": cid, "percent_off": str(pct_i), "duration": "forever", "name": f"ส่วนลด {pct_i}% แพ็กเกจ {label}",
                       "applies_to[products][]": product_id}, idem=f"geo-coupon-{cid}")
    return cid


def _cycle(q) -> str:
    from . import quotes as Q
    return Q.cycle_of(q)


def checkout_params(q, lead, base: str, prices: dict, coupon: str | None = None) -> dict:
    """Checkout แบบ subscription จากแคตตาล็อก: แพ็กเกจรายเดือน (+คูปองส่วนลด) + GEO Setup ครั้งเดียวในบิลแรก
    เก็บเลขผู้เสียภาษีและที่อยู่ออกบิล (ธุรกิจไทยต้องการใบกำกับ) · Stripe เลือกวิธีจ่ายให้เอง (Link, บัตร)"""
    p = {
        "mode": "subscription",
        "client_reference_id": f"quote-{q['id']}",
        "metadata[quote_id]": str(q["id"]), "metadata[lead_id]": str(q["lead_id"]), "metadata[domain]": lead["domain"],
        "subscription_data[metadata][quote_id]": str(q["id"]), "subscription_data[metadata][domain]": lead["domain"],
        "subscription_data[billing_mode][type]": "flexible",            # โหมดเรียกเก็บแบบยืดหยุ่น (ค่าแนะนำของ Stripe)
        "success_url": f"{base}/q/{q['token']}?paid=1", "cancel_url": f"{base}/q/{q['token']}", "locale": "th",
        "line_items[0][price]": prices["plan"], "line_items[0][quantity]": "1",
        "tax_id_collection[enabled]": "true", "billing_address_collection": "required",
    }
    if q["setup"] and prices.get("setup") and _cycle(q) != "year":      # รายปี Setup ฟรี
        p["line_items[1][price]"] = prices["setup"]
        p["line_items[1][quantity]"] = "1"
    if coupon:
        p["discounts[0][coupon]"] = coupon
    if lead["email"] and "@" in lead["email"] and not lead["email"].endswith((".local", ".invalid", ".test")):
        p["customer_email"] = lead["email"]
    return p


def create_checkout(q, lead, base: str, company: str = "เจอ.AI") -> str:
    """สร้าง Checkout Session → คืน URL ให้ลูกค้าไปจ่าย (เก็บ session id ไว้เช็กย้อนหลัง) · idempotent ต่อใบเสนอราคาเป็นช่วง 10 นาที"""
    if not available():
        raise RuntimeError("ยังไม่ได้ตั้งค่า Stripe")
    kind = q["package"] + ("_year" if _cycle(q) == "year" else "")
    plan = ensure_price(kind, company)
    prices = {"plan": plan["price"], "setup": ensure_price("setup", company)["price"] if q["setup"] and _cycle(q) != "year" else None}
    coupon = ensure_coupon(q["discount_pct"], plan["product"], kind) if q["discount_pct"] else None
    resp = _post("/checkout/sessions", checkout_params(q, lead, base, prices, coupon), idem=f"geo-checkout-{q['id']}-{int(time.time()) // 600}")
    db.update_quote(q["id"], stripe_session_id=resp.get("id"))
    return resp["url"]


def _delete(path: str) -> None:
    r = httpx.delete(f"{API}{path}", auth=(secret_key(), ""), timeout=TIMEOUT)
    if r.status_code >= 400:
        body = r.json() if r.content else {}
        raise RuntimeError((body.get("error") or {}).get("message") or f"Stripe HTTP {r.status_code}")


def ensure_webhook(base: str) -> str:
    """ตั้ง webhook endpoint ให้เองด้วย secret key ของเจ้าของ — Stripe คืน signing secret ครั้งเดียวตอนสร้าง ระบบเก็บเข้ารหัสทันที
    (ไม่ต้องคัดลอกผ่านแชต) · มี endpoint ของ URL นี้อยู่แล้วแต่ไม่มี secret → สร้างใหม่แทนอันเก่า"""
    if not available():
        raise RuntimeError("ยังไม่ได้ใส่ secret key")
    url = f"{base}/stripe/webhook"
    mine = [e for e in (_get_json("/webhook_endpoints?limit=100").get("data") or []) if e.get("url") == url]
    if mine and webhook_secret():
        e = mine[0]
        if set(e.get("enabled_events") or []) != set(EVENTS):
            _post(f"/webhook_endpoints/{e['id']}", {"enabled_events[]": list(EVENTS)})
            return "มี webhook อยู่แล้ว — อัปเดตรายการ event ให้ครบ"
        return "มี webhook อยู่แล้วและพร้อมใช้"
    for e in mine:                                    # มีแต่เราไม่มี secret (สร้างจากที่อื่น) → แทนที่
        _delete(f"/webhook_endpoints/{e['id']}")
    created = _post("/webhook_endpoints", {"url": url, "description": "geo.appreview.cloud (เจอ.AI) — quotes, subscriptions, invoices",
                                           "enabled_events[]": list(EVENTS)})
    if not created.get("secret"):
        raise RuntimeError("Stripe ไม่คืน signing secret")
    db.set_setting("stripe_webhook_secret", wp_client.encrypt(created["secret"]))
    return "สร้าง webhook และเก็บ signing secret แล้ว" + (" (แทนอันเก่าที่ไม่มี secret)" if mine else "")


def portal_url(customer_id: str, return_url: str) -> str:
    """Customer Portal ของ Stripe: ลูกค้าเปลี่ยนบัตร ดูใบแจ้งหนี้ ยกเลิก ได้เอง (เปิดใช้ครั้งเดียวใน Dashboard → Settings → Customer portal)"""
    return _post("/billing_portal/sessions", {"customer": customer_id, "return_url": return_url, "locale": "th"})["url"]


# ---------- webhook ----------
def verify(payload: bytes, sig_header: str, now: float | None = None) -> bool:
    """Stripe-Signature: t=<ts>,v1=<hex HMAC-SHA256(whsec, '<ts>.<payload>')> — ไม่ตรงหรือเก่ากว่า 5 นาที = ทิ้ง"""
    wh = webhook_secret()
    if not wh or not sig_header:
        return False
    parts = dict(kv.split("=", 1) for kv in sig_header.split(",") if "=" in kv)
    ts = parts.get("t")
    sigs = [v.split("=", 1)[1] for v in sig_header.split(",") if v.startswith("v1=")]
    if not ts or not sigs:
        return False
    try:
        if abs((now or time.time()) - int(ts)) > TOLERANCE:
            return False
    except ValueError:
        return False
    expected = hmac.new(wh.encode("utf-8"), f"{ts}.".encode("utf-8") + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, s) for s in sigs)


def sign(payload: bytes, whsec: str, ts: int | None = None) -> str:
    """สร้างหัว Stripe-Signature (ใช้ในเทสต์)"""
    ts = ts or int(time.time())
    mac = hmac.new(whsec.encode("utf-8"), f"{ts}.".encode("utf-8") + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def _invoice_subscription(inv: dict) -> str:
    """id การสมัครของใบแจ้งหนี้ — รองรับทั้ง API เก่า (invoice.subscription) และใหม่ (invoice.parent.subscription_details.subscription)"""
    sub = inv.get("subscription")
    if not sub:
        sub = ((inv.get("parent") or {}).get("subscription_details") or {}).get("subscription")
    if isinstance(sub, dict):
        sub = sub.get("id")
    return sub or ""


def _quote_of_session(obj: dict):
    qid = (obj.get("metadata") or {}).get("quote_id")
    if not qid and str(obj.get("client_reference_id") or "").startswith("quote-"):
        qid = obj["client_reference_id"][6:]
    try:
        return db.get_quote(int(qid)) if qid else None
    except ValueError:
        return None


def _paid_session(obj: dict) -> str:
    """ชำระสำเร็จ → เปิดบัญชี (ครั้งเดียว ส่งซ้ำไม่เป็นไร)"""
    from . import pipeline
    q = _quote_of_session(obj)
    if not q:
        return "no-quote"
    if obj.get("payment_status") not in ("paid", "no_payment_required"):
        return "unpaid"
    db.update_quote(q["id"], stripe_session_id=obj.get("id"), stripe_customer_id=obj.get("customer"),
                    stripe_subscription_id=obj.get("subscription"), paid_via="stripe")
    if q["status"] == "paid":
        return "already-paid"
    q = db.get_quote(q["id"])
    note = pipeline.confirm_payment(q, "stripe")
    q = db.get_quote(q["id"])
    if q["tenant_id"] and obj.get("customer"):
        db.set_tenant_stripe_customer(q["tenant_id"], obj["customer"])      # ให้ลูกค้าเปิด Customer Portal ได้จากในแอป
    for d in db.list_decisions("proposed", 300):
        if d["kind"] == "payment" and json.loads(d["payload_json"] or "{}").get("quote_id") == q["id"]:
            db.set_decision(d["id"], "executed", "stripe", note)
    lead = db.get_lead(q["lead_id"])
    pipeline.alert_admins(f"💳 {lead['domain']} ชำระ {q['first_total']:,} บาทผ่าน Stripe แล้ว — {note}", f"/admin/leads/{lead['id']}", "payment",
                          subject=f"💳 {lead['domain']} ชำระผ่าน Stripe {q['first_total']:,} บาท เปิดบัญชีแล้ว")
    return "paid"


def handle_event(evt: dict) -> str:
    from . import pipeline
    typ = evt.get("type") or ""
    obj = ((evt.get("data") or {}).get("object")) or {}
    if typ == "checkout.session.completed":
        return _paid_session(obj)
    if typ.startswith("invoice."):
        q = db.get_quote_by_subscription(_invoice_subscription(obj))
    elif typ.startswith("customer.subscription."):
        q = db.get_quote_by_subscription(obj.get("id"))
    elif typ in ("charge.refunded", "credit_note.created"):
        q = db.get_quote_by_customer(obj.get("customer")) if obj.get("customer") else None
    else:
        q = None
    if not q:
        return "ignored" if typ not in EVENTS else "no-quote"
    lead = db.get_lead(q["lead_id"])
    if typ == "invoice.paid":
        amt = int(obj.get("amount_paid") or 0) // 100
        db.add_stripe_invoice(q["id"], q["tenant_id"], obj.get("id"), amt, "paid", obj.get("hosted_invoice_url"), obj.get("invoice_pdf"),
                              obj.get("number"))
        if obj.get("billing_reason") == "subscription_create":
            return "first-invoice"                                   # บิลแรกจัดการที่ checkout.session.completed แล้ว
        pipeline.record(q["lead_id"], "paid", f"ต่ออายุรายเดือนผ่าน Stripe {amt:,} บาท", "stripe")
        if q["tenant_id"] and db.get_tenant(q["tenant_id"]) and db.get_tenant(q["tenant_id"])["plan"] != q["package"]:
            db.set_plan(q["tenant_id"], q["package"])                 # เคยถูกลดเป็น free เพราะค้างชำระ → คืนแผน
        db.notify_admins(f"🔁 {lead['domain']} ต่ออายุผ่าน Stripe {amt:,} บาท", f"/admin/leads/{lead['id']}", "payment")
        return "renewed"
    if typ == "customer.subscription.updated":
        st = obj.get("status") or ""
        if st == "unpaid":                                          # Stripe หมดความพยายามแล้วทำเครื่องหมายว่าไม่ได้ชำระ → ระงับบริการ
            if q["tenant_id"]:
                db.set_plan(q["tenant_id"], "free")
            pipeline.record(q["lead_id"], "note", "Stripe: การสมัครเป็น unpaid (ค้างชำระจนหมดความพยายาม) — ระงับบริการ", "stripe")
            pipeline.alert_admins(f"⛔ {lead['domain']} ค้างชำระจนหมดความพยายาม — ระงับบริการแล้ว", f"/admin/leads/{lead['id']}", "payment",
                                  subject=f"⛔ {lead['domain']} ค้างชำระ ระงับบริการแล้ว")
            return "unpaid"
        if st == "past_due":
            pipeline.record(q["lead_id"], "note", "Stripe: การสมัครอยู่ในสถานะ past_due (ค้างชำระ) — Smart Retries กำลังลองเก็บเงินใหม่", "stripe")
            pipeline.alert_admins(f"⚠️ {lead['domain']} ค้างชำระรายเดือน (Stripe past_due)", f"/admin/leads/{lead['id']}", "payment",
                                  subject=f"⚠️ {lead['domain']} ค้างชำระรายเดือน")
            return "past_due"
        if st == "active" and q["tenant_id"] and db.get_tenant(q["tenant_id"]) and db.get_tenant(q["tenant_id"])["plan"] != q["package"]:
            db.set_plan(q["tenant_id"], q["package"])
            pipeline.record(q["lead_id"], "note", "Stripe: การสมัครกลับมา active — คืนแผน", "stripe")
            return "reactivated"
        return "updated"
    if typ == "charge.refunded":
        amt = int(obj.get("amount_refunded") or 0) // 100
        pipeline.record(q["lead_id"], "note", f"Stripe คืนเงิน {amt:,} บาท", "stripe")
        pipeline.alert_admins(f"↩️ {lead['domain']} ได้รับเงินคืน {amt:,} บาท (Stripe) — เช็กว่าต้องลดแผนไหม", f"/admin/leads/{lead['id']}", "payment",
                              subject=f"↩️ คืนเงิน {amt:,} บาท ให้ {lead['domain']}")
        return "refunded"
    if typ == "credit_note.created":
        amt = int(obj.get("amount") or 0) // 100
        pipeline.record(q["lead_id"], "note", f"Stripe ออกใบลดหนี้ {amt:,} บาท", "stripe")
        db.notify_admins(f"🧾 {lead['domain']} มีใบลดหนี้ {amt:,} บาท (Stripe)", f"/admin/leads/{lead['id']}", "payment")
        return "credit_note"
    if typ == "invoice.payment_failed":
        final = "next_payment_attempt" in obj and obj["next_payment_attempt"] is None and obj.get("billing_reason") != "subscription_create"
        if final:                                                   # Smart Retries ลองครบแล้วยังไม่ผ่าน → ระงับบริการตามสัญญา (ไม่ต้องพึ่งค่าใน Dashboard)
            if q["tenant_id"]:
                db.set_plan(q["tenant_id"], "free")
            pipeline.record(q["lead_id"], "note", "Stripe เก็บเงินรายเดือนไม่สำเร็จครบทุกครั้ง — ระงับบริการ (ลดแผนเป็น free) จนกว่าจะชำระ", "stripe")
            pipeline.alert_admins(f"⛔ {lead['domain']} เก็บเงินรายเดือนไม่สำเร็จครบทุกครั้ง — ระงับบริการแล้ว", f"/admin/leads/{lead['id']}", "payment",
                                  subject=f"⛔ {lead['domain']} ค้างชำระ ระงับบริการแล้ว")
            return "suspended"
        pipeline.record(q["lead_id"], "note", "Stripe เก็บเงินรอบเดือนไม่สำเร็จ — Smart Retries จะลองใหม่เอง ถ้าไม่ผ่านครบทุกครั้งระบบจะระงับบริการ", "stripe")
        pipeline.alert_admins(f"⚠️ {lead['domain']} เก็บเงินรายเดือนผ่าน Stripe ไม่สำเร็จ (กำลังลองใหม่)", f"/admin/leads/{lead['id']}", "payment",
                              subject=f"⚠️ {lead['domain']} เก็บเงินรายเดือนไม่สำเร็จ")
        return "failed"
    if typ == "customer.subscription.deleted":
        if q["tenant_id"]:
            db.set_plan(q["tenant_id"], "free")                       # ตามสัญญา: ค้างชำระ/ยกเลิก → ระงับบริการ (ลดเป็น free)
        pipeline.record(q["lead_id"], "note", "การสมัครรายเดือนใน Stripe ถูกยกเลิก — ลดแผนเป็น free", "stripe")
        pipeline.alert_admins(f"⛔ {lead['domain']} ยกเลิกการสมัครรายเดือน (Stripe) — ลดแผนเป็น free แล้ว", f"/admin/leads/{lead['id']}", "payment",
                              subject=f"⛔ {lead['domain']} ยกเลิกการสมัครรายเดือน")
        return "cancelled"
    return "ignored"


def session_paid(q) -> bool | None:
    """ใบนี้มี Stripe Checkout: True = Stripe รับเงินแล้ว · False = ยังไม่จ่าย · None = ไม่มี session/ถาม Stripe ไม่ได้"""
    if not available() or not q["stripe_session_id"]:
        return None
    try:
        obj = _get_json(f"/checkout/sessions/{q['stripe_session_id']}")
    except Exception:
        return None
    return obj.get("payment_status") in ("paid", "no_payment_required")


def sync_session(q) -> str:
    """ลูกค้ากลับมาหน้าใบเสนอราคาหลังจ่าย (?paid=1) แต่ webhook ยังไม่มา/ยังไม่ตั้ง → ถาม Stripe ตรง ๆ แล้วยืนยันให้เลย"""
    if not available() or not q["stripe_session_id"] or q["status"] == "paid":
        return "skip"
    try:
        obj = _get_json(f"/checkout/sessions/{q['stripe_session_id']}")
    except Exception as e:
        return f"error:{type(e).__name__}"
    return _paid_session(obj)
