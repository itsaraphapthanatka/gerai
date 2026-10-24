"""ฝ่ายขยายบัญชีลูกค้า (Account Expansion) — ลูกค้าที่จ่ายแล้วและเห็นผล ได้ข้อเสนอที่คุ้มกว่าในจังหวะที่ใช่
โอกาส (แต่ละอย่างเสนอครั้งเดียวต่อลูกค้า บันทึกเป็น lead_events exp_*):
  yearly   — รายเดือนมาแล้ว ≥ 55 วัน ติดตั้งแล้ว และรอบบิลถัดไปอีก ≤ 10 วัน → ชวนเปลี่ยนเป็นรายปี (ประหยัดเท่าไรบอกเป็นตัวเลข)
  agency   — แพ็กเกจ Pro ใช้ใกล้เต็ม (แบรนด์ ≥ 80% หรือคอนเทนต์/มอนิเตอร์เดือนนี้ ≥ 80%) → ชวนขึ้น Agency
  content  — คอนเทนต์เดือนนี้เต็มโควตาแล้ว (ยังไม่เหมาะขึ้นแพ็กเกจ) → แนะนำแพ็กคอนเทนต์เพิ่ม 20 ชิ้น (ซื้อเองที่หน้าอัปเกรด)
  advocate — ใช้มา ≥ 45 วัน ติดตั้งแล้ว เผยแพร่ ≥ 3 ชิ้น → ขออนุญาตทำเคสศึกษา + ชวนแนะนำเพื่อนผ่านโปรแกรมพันธมิตร
ทุกข้อเสนอเป็น decision kind "expansion" (agent expand) รอเจ้าของอนุมัติ · yearly/agency อนุมัติแล้วออกใบเสนอราคาใหม่ (ลิงก์ให้ลูกค้ากดยืนยัน/จ่าย)
ลูกค้าจ่ายใบใหม่ → ยกเลิกการสมัคร Stripe เดิมทันที และยกเลิกใบแจ้งหนี้ PromptPay ของแผนเดิมที่ยังค้าง (ไม่เก็บซ้ำ)"""
from __future__ import annotations
import datetime as dt
import html as _html
import json

from . import db, office

OPPS = {"yearly": "ชวนเปลี่ยนเป็นรายปี", "agency": "ชวนขึ้นแพ็กเกจ Agency", "content": "แนะนำแพ็กคอนเทนต์เพิ่ม", "advocate": "ขอเคสศึกษา + ชวนแนะนำเพื่อน"}
YEARLY_AFTER_DAYS, YEARLY_WINDOW_DAYS = 55, 10
USAGE_PCT = 0.8
ADVOCATE_AFTER_DAYS, ADVOCATE_MIN_PUBLISHED = 45, 3


def enabled() -> bool:
    return (db.get_setting("expand_on") or "1") == "1"


def _base() -> str:
    from . import pipeline
    return pipeline.public_base()


def done(lead_id: int) -> set:
    return {(e["note"] or "").split(" ", 1)[0] for e in db.list_lead_events(lead_id, 500) if e["kind"] == "exp_offer"}


def _pending(lead_id: int, opp: str) -> bool:
    for d in db.list_decisions("proposed", 300):
        if d["kind"] == "expansion" and d["lead_id"] == lead_id and json.loads(d["payload_json"] or "{}").get("opp") == opp:
            return True
    return False


def signals(c: dict, now: dt.datetime | None = None) -> dict:
    """ข้อมูลจริงของลูกค้ารายนี้ที่ใช้ตัดสิน"""
    from . import billing, success, quotes as Q, accounting
    now = now or dt.datetime.now()
    q, t, b = c["quote"], c["tenant"], c["brand"]
    first = min((x["paid_at"] for x in db.list_quotes(c["lead"]["id"]) if x["status"] == "paid" and x["paid_at"]), default=q["paid_at"])
    days = (now - dt.datetime.fromisoformat(str(first)[:19])).days if first else 0
    plan = billing.PLANS.get(q["package"], billing.PLANS["pro"])
    use = billing.usage(t["id"])
    ok, _ = success.installed(b)
    cc = success.content_counts(b["id"])
    paid_at = dt.date.fromisoformat(str(q["paid_at"])[:10])
    k = 1
    while accounting.add_months(paid_at, k) <= now.date():
        k += 1
    next_bill = accounting.add_months(paid_at, k) if Q.cycle_of(q) == "month" else None
    return {"days": days, "cycle": Q.cycle_of(q), "package": q["package"], "installed": ok, "published": cc["published"], "content_total": cc["total"],
            "brands": use["brands"], "brands_cap": plan["brands"], "content_month": use["content"], "content_cap": plan["content_month"],
            "runs_month": use["runs"], "runs_cap": plan["runs_month"], "next_bill": next_bill.isoformat() if next_bill else None,
            "days_to_bill": (next_bill - now.date()).days if next_bill else None, "net": q["net"], "discount_pct": q["discount_pct"]}


def opportunities(c: dict, now: dt.datetime | None = None) -> list[str]:
    s = signals(c, now)
    out = []
    if s["cycle"] == "month" and s["days"] >= YEARLY_AFTER_DAYS and s["installed"] and s["days_to_bill"] is not None and 0 <= s["days_to_bill"] <= YEARLY_WINDOW_DAYS:
        out.append("yearly")
    near = (s["brands"] >= s["brands_cap"] * USAGE_PCT and s["brands_cap"] > 1) or s["content_month"] >= s["content_cap"] * USAGE_PCT or s["runs_month"] >= s["runs_cap"] * USAGE_PCT
    if s["package"] == "pro" and near and s["days"] >= 14:
        out.append("agency")
    elif s["content_month"] >= s["content_cap"] and s["package"] != "unlimited":
        out.append("content")
    if s["days"] >= ADVOCATE_AFTER_DAYS and s["installed"] and s["published"] >= ADVOCATE_MIN_PUBLISHED:
        out.append("advocate")
    return out


def draft(c: dict, opp: str, s: dict) -> dict:
    """ข้อความถึงลูกค้า — ตัวเลขจากระบบจริงเท่านั้น สุภาพ หนึ่งข้อเสนอต่อฉบับ"""
    from . import billing, quotes as Q, partners
    dom, base = c["lead"]["domain"], _base()
    vat = Q.vat_rate_now()
    vt = f" (ยังไม่รวม VAT {vat:g}%)" if vat else ""
    if opp == "yearly":
        y = Q.price_for(s["package"], s["discount_pct"], False, "year")
        save = s["net"] * 12 - y["period_total"]
        subj = f"{dom}: เปลี่ยนเป็นรายปีประหยัด {save:,} บาท"
        body = (f"สวัสดีครับ\n\nใช้เจอ.AI ดูแล {dom} มา {s['days']} วันแล้ว ขอบคุณครับ รอบบิลถัดไปคือ {s['next_bill']}\n\n"
                f"ถ้าเปลี่ยนเป็นรายปีตอนนี้ จ่าย {y['period_total']:,} บาทต่อปี{vt} แทน {s['net']:,} บาท × 12 = {s['net'] * 12:,} บาท ประหยัด {save:,} บาท "
                f"(ลด {Q.yearly_discount():g}%) และคืนเงินได้เต็มจำนวนภายใน 7 วันแรก\n\n"
                "ถ้าสนใจ กดยืนยันในใบเสนอราคาที่แนบลิงก์ไว้ด้านล่าง เมื่อชำระแล้วเราจะยกเลิกการเก็บเงินรายเดือนเดิมให้ทันที\n\nทีมดูแลลูกค้า เจอ.AI")
    elif opp == "agency":
        a = billing.PLANS["agency"]
        used = [f"แบรนด์ {s['brands']}/{s['brands_cap']}", f"คอนเทนต์เดือนนี้ {s['content_month']}/{s['content_cap']}", f"มอนิเตอร์เดือนนี้ {s['runs_month']}/{s['runs_cap']}"]
        subj = f"{dom}: ใช้แพ็กเกจ Pro ใกล้เต็มแล้ว"
        body = (f"สวัสดีครับ\n\nเดือนนี้ {dom} ใช้งานใกล้เพดานแพ็กเกจ Pro แล้ว: {' · '.join(used)}\n\n"
                f"แพ็กเกจ Agency {a['price']:,} บาท/เดือน{vt} ดูแลได้ {a['brands']} แบรนด์ คอนเทนต์ {a['content_month']} ชิ้น และมอนิเตอร์ {a['runs_month']} ครั้งต่อเดือน "
                "เหมาะถ้าคุณดูแลหลายเว็บหรืออยากเพิ่มคอนเทนต์ให้ทันคำถามของลูกค้า\n\n"
                "ถ้าสนใจ กดยืนยันในใบเสนอราคาที่แนบลิงก์ไว้ด้านล่าง เมื่อชำระแล้วเราจะยกเลิกการเก็บเงินแพ็กเกจเดิมให้ทันที ไม่จ่ายซ้อน\n\nทีมดูแลลูกค้า เจอ.AI")
    elif opp == "content":
        ad = next(x for x in billing.ADDONS if x["key"] == "content20")
        subj = f"{dom}: คอนเทนต์เดือนนี้ครบโควตาแล้ว"
        body = (f"สวัสดีครับ\n\nเดือนนี้ {dom} สร้างคอนเทนต์ครบ {s['content_cap']} ชิ้นตามแพ็กเกจแล้ว ระบบจะเริ่มสร้างต่อเมื่อขึ้นเดือนใหม่\n\n"
                f"ถ้าอยากได้ต่อเลย มีแพ็กคอนเทนต์เพิ่ม 20 ชิ้น {ad['price']:,} บาท{vt} ครั้งเดียว ซื้อได้ที่ {base}/upgrade\n\nทีมดูแลลูกค้า เจอ.AI")
    else:
        subj = f"{dom}: ขอเล่าผลงานของคุณเป็นเคสศึกษาได้ไหม"
        body = (f"สวัสดีครับ\n\nตอนนี้ {dom} เผยแพร่คอนเทนต์ที่ตอบคำถามลูกค้าแล้ว {s['published']} ชิ้น ขอบคุณที่ไว้ใจเจอ.AI ครับ\n\n"
                "ถ้ายินดี เราขออนุญาตเล่าผลงานของคุณเป็นเคสศึกษา (ใช้ชื่อแบรนด์ได้หรือไม่ใช้ก็ได้) ตอบอีเมลนี้ว่า \"ยินดี\" หรือ \"ไม่ใช้ชื่อ\" ได้เลย\n\n"
                f"และถ้ามีเพื่อนธุรกิจที่อยากให้ลูกค้าหาเจอใน Google และ AI แนะนำผ่านโปรแกรมพันธมิตรของเราได้ รับค่าแนะนำ {partners.pct():g}% "
                f"ของค่าบริการ {partners.months()} เดือนแรก: {base}/partners?src=customer\n\nทีมดูแลลูกค้า เจอ.AI")
    return {"subject": subj, "body": body}


def propose(c: dict, opp: str, s: dict, key: str | None = None) -> int:
    d = draft(c, opp, s)
    facts = (f"ใช้มา {s['days']} วัน · {s['package']} {('รายปี' if s['cycle'] == 'year' else 'รายเดือน')} · ติดตั้ง {'แล้ว' if s['installed'] else 'ยัง'} · "
             f"เผยแพร่ {s['published']} ชิ้น · แบรนด์ {s['brands']}/{s['brands_cap']} · คอนเทนต์เดือนนี้ {s['content_month']}/{s['content_cap']}")
    did = db.add_decision("expand", "expansion", f"{OPPS[opp]} — {c['lead']['domain']}",
                          f"เหตุผล: {facts}\n" + ("อนุมัติ = ออกใบเสนอราคาใหม่และส่งพร้อมข้อความนี้\n" if opp in ("yearly", "agency") else "อนุมัติ = ส่งข้อความนี้\n")
                          + f"\nถึง {c['tenant']['email']}\nหัวข้อ: {d['subject']}\n\n{d['body']}",
                          {"opp": opp, "quote_id": c["quote"]["id"], "tenant_id": c["tenant"]["id"], **d}, lead_id=c["lead"]["id"])
    db.add_lead_event(c["lead"]["id"], "exp_offer", f"{key or opp} เสนอ #{did}", "expand")     # รายปีเสนอได้ครั้งเดียวต่อรอบบิล · อื่น ๆ ครั้งเดียว
    return did


@office.track("expand", "ขยายบัญชี: รายปี · Agency · แพ็กเสริม · เคสศึกษา")
def run(now: dt.datetime | None = None) -> dict:
    from . import success
    res = {"at": db.now(), "customers": 0, "proposed": []}
    if not enabled():
        res["skipped"] = "ปิดอยู่"
        db.set_setting("expand_last", json.dumps(res, ensure_ascii=False))
        return res
    for c in success.customers():
        if c["tenant"]["plan"] == "free":
            continue
        res["customers"] += 1
        try:
            s = signals(c, now)
            seen = done(c["lead"]["id"])
            for opp in opportunities(c, now):
                key = f"{opp}:{s['next_bill']}" if opp == "yearly" else opp
                if key in seen or _pending(c["lead"]["id"], opp):
                    continue
                propose(c, opp, s, key)
                res["proposed"].append(f"{c['lead']['domain']}:{opp}")
        except Exception as e:
            res["proposed"].append(f"{c['lead']['domain']}:error {type(e).__name__}")
    db.set_setting("expand_last", json.dumps(res, ensure_ascii=False))
    return res


def execute(d, who: str) -> str:
    """อนุมัติข้อเสนอ → yearly/agency ออกใบเสนอราคาใหม่ + ส่ง · content/advocate ส่งข้อความ"""
    from . import mailer, pipeline, quotes as Q, deliverability
    pl = json.loads(d["payload_json"] or "{}")
    lead, t = db.get_lead(d["lead_id"]), db.get_tenant(pl.get("tenant_id") or 0)
    old = db.get_quote(pl.get("quote_id") or 0)
    if not (lead and t and old):
        return "ไม่พบลูกค้า"
    to = t["email"]
    if not (mailer.is_deliverable(to) and mailer.config()):
        return f"ส่งไม่ได้ — อีเมลบัญชี {to} ใช้ส่งไม่ได้"
    body, note = pl.get("body", ""), ""
    if pl.get("opp") in ("yearly", "agency"):
        pkg = "agency" if pl["opp"] == "agency" else old["package"]
        cyc = "year" if pl["opp"] == "yearly" else Q.cycle_of(old)
        q = pipeline.create_quote(lead, pkg, old["discount_pct"] if pl["opp"] == "yearly" else 0, False, created_by="expand", cycle=cyc,
                                  notes=f"ขยายบัญชีจาก {old['number']}")
        db.update_quote(q["id"], status="sent", sent_at=db.now())
        db.update_lead(lead["id"], status="won", package=old["package"], monthly_thb=old["net"])       # ยังเป็นลูกค้าแผนเดิมจนกว่าจะจ่ายใบใหม่
        body += f"\n\nใบเสนอราคา {q['number']}: {Q.summary_text(q, lead['domain'])}\nยืนยันและชำระ: {pipeline.quote_url(q)}"
        note = f" · ใบเสนอราคา {q['number']}"
    fa, fn = deliverability.active_from()
    mailer.send_plain(to, pl.get("subject") or "ข้อเสนอจากเจอ.AI", body, "", fa, fn)
    db.add_notification(t["id"], pl.get("subject") or "ข้อเสนอจากเจอ.AI", "/upgrade", "info")
    db.add_lead_event(lead["id"], "exp_sent", f"{pl.get('opp')} ส่งถึง {to}{note}", who)
    return f"ส่ง{OPPS.get(pl.get('opp'), 'ข้อเสนอ')}ถึง {to} แล้ว{note}"


def on_upgrade(q, prev: list) -> str:
    """ลูกค้าเดิมจ่ายใบใหม่ (รายปี/Agency) → หยุดเก็บเงินแผนเดิม: ยกเลิกการสมัคร Stripe เดิม · ยกเลิกใบแจ้งหนี้ PromptPay ของใบเดิมที่ค้าง"""
    from . import stripe_pay, accounting
    out = []
    for p in prev:
        sub = p["stripe_subscription_id"] if "stripe_subscription_id" in p.keys() else None
        if sub and sub != (q["stripe_subscription_id"] if "stripe_subscription_id" in q.keys() else None):
            try:
                stripe_pay._delete(f"/subscriptions/{sub}")
                out.append(f"ยกเลิกการสมัคร Stripe เดิมของ {p['number']}")
            except Exception as e:
                out.append(f"ยกเลิก Stripe เดิม ({p['number']}) ไม่สำเร็จ: {type(e).__name__} — ยกเลิกเองใน Stripe")
        for inv in db.list_acct_docs("invoice", limit=500):
            if inv["quote_id"] == p["id"] and inv["status"] in ("open", "claimed"):
                accounting.void(inv, f"ลูกค้าเปลี่ยนเป็น {q['number']}", "expand")
                out.append(f"ยกเลิกใบแจ้งหนี้ {inv['number']}")
    db.add_lead_event(q["lead_id"], "exp_upgraded", f"{q['number']} · " + (" · ".join(out) or "ไม่มีการเก็บเงินเดิมที่ต้องหยุด"), "expand")
    return " · ".join(out)


def summary() -> dict:
    ev = [e for e in db.events_since(("exp_offer", "exp_sent", "exp_upgraded"), (dt.datetime.now() - dt.timedelta(days=90)).isoformat())]
    pend = [d for d in db.list_decisions("proposed", 300) if d["kind"] == "expansion"]
    return {"pending": len(pend), "sent_90d": sum(1 for e in ev if e["kind"] == "exp_sent"), "upgraded_90d": sum(1 for e in ev if e["kind"] == "exp_upgraded")}


def status_text() -> str | None:
    s = summary()
    if not any(s.values()):
        return None
    return f"ขยายบัญชี: รออนุมัติ {s['pending']} · ส่งข้อเสนอ 90 วัน {s['sent_90d']} · อัปเกรดแล้ว {s['upgraded_90d']}"
