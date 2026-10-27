"""หน้า "ลูกค้า" ของเจ้าของ (โหมดเอเจนซี) — ดูลูกค้าที่จ่ายเงินทุกรายในที่เดียว: รายได้ประจำ ครบรอบบิล การชำระ ผลงาน และใครต้องดูแล
อ่านอย่างเดียว ไม่เปลี่ยนข้อมูล/ตรรกะธุรกิจใด ๆ · ตัวเลขทุกตัวมาจากฐานข้อมูล

ลูกค้า = บัญชีที่ไม่ใช่แอดมิน และอยู่แผนเสียเงิน หรือมีใบเสนอราคาที่ชำระแล้ว (บัญชีแผนฟรีที่เคยจ่าย = "กลับเป็นแผนฟรี" ยังโชว์ให้ตามต่อ แต่ไม่นับรายได้)
รายได้ประจำต่อเดือน (MRR, บาท ไม่รวม VAT) ต่อใบที่ใช้อยู่ (ใบชำระล่าสุดของแต่ละลีด — ใบใหม่ของลีดเดิม = อัปเกรด/เปลี่ยนรอบ แทนใบเก่า):
  รายเดือน = ยอดต่อเดือน · รายปี = ยอดทั้งปี ÷ 12 · ราคาตั้งเอง = ราคาที่เจ้าของตั้ง (รายปีใช้ยอดทั้งปีที่ใส่เองถ้ามี) · ค่า GEO Setup ครั้งเดียวไม่นับ
  บัญชีแผนเสียเงินที่ไม่มีใบเสนอราคา (เปิดแผนให้ด้วยมือ) ไม่นับใน MRR — แจ้งแยกพร้อมราคาแพ็กเกจไว้เทียบ
ครบรอบบิลถัดไป = วันชำระใบ + 1 เดือน (รายเดือน) / 1 ปี (รายปี) ทีละรอบ จนถึงวันแรกที่ยังไม่ผ่าน (แบบเดียวกับที่บัญชีออกใบแจ้งหนี้)
สัญญาณเสี่ยง = เงื่อนไขเดียวกับดูแลหลังการขาย (success.step) และผลลัพธ์ (results.visibility) คำนวณจากสถานะปัจจุบัน"""
from __future__ import annotations
import csv
import datetime as dt
import io

from . import accounting as A, billing, db, leads as L, quotes as Q, results as RS, success as S

RENEW_WINDOW = 30            # ครบรอบบิลภายในกี่วัน = "ใกล้ครบรอบ"
SOV_LOOKBACK = 28            # เทียบ Share of Voice กับเมื่อ 4 สัปดาห์ก่อน
INSTALL_DAYS, CONTENT_DAYS = 7, 10   # ตรงกับ success.step: ยังไม่ติดตั้งเมื่อครบ 7 วัน · ไม่มีคอนเทนต์เมื่อครบ 10 วัน
# สัญญาณเสี่ยง: ชื่อ · น้ำหนัก (เรียงคนที่ต้องดูแลก่อน)
FLAGS = {"overdue": ("ค้างชำระ", 50), "free": ("กลับเป็นแผนฟรี", 40), "install": ("ยังไม่ติดตั้งตัวเชื่อม", 30),
         "content": ("ยังไม่มีคอนเทนต์", 20), "flat": ("ผลไม่ขยับ 4 สัปดาห์", 20)}
CUSTOM = "custom"


def _ts(s) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(s)[:19]) if s else None
    except ValueError:
        return None


def _key(row, k):
    try:
        return row[k] if k in row.keys() else None
    except Exception:
        return row.get(k) if isinstance(row, dict) else None


# ---------- เงิน ----------
def period_amount(q) -> float:
    """ยอดต่อรอบบิลที่ตกลงไว้ (ไม่รวม VAT · ไม่รวม Setup ครั้งเดียว)"""
    if Q.is_custom(q):
        if Q.cycle_of(q) == "year":
            y = Q._f(q, "custom_year")
            return float(y if y is not None else Q.period_total(q))
        return float(Q._f(q, "custom_price") or 0)
    return float(Q.period_total(q))


def monthly_of(q) -> float:
    """MRR ของใบนี้: รายปี = ยอดทั้งปี ÷ 12"""
    amt = period_amount(q)
    return amt / 12 if Q.cycle_of(q) == "year" else amt


def next_renewal(q, today: dt.date) -> dt.date | None:
    paid = A._date(q["paid_at"])
    if not paid:
        return None
    months = 12 if Q.cycle_of(q) == "year" else 1
    for n in range(1, 1200):
        d = A.add_months(paid, months * n)
        if d >= today:
            return d
    return None


def _active_quotes() -> tuple[dict, dict]:
    """{tenant_id: [ใบที่ใช้อยู่ ใหม่→เก่า]} และ {tenant_id: วันจ่ายครั้งแรก}"""
    active, first, seen = {}, {}, set()
    for q in db.list_quotes(status="paid", limit=10000):            # id ใหม่ → เก่า
        tid = q["tenant_id"]
        if not tid:
            continue
        p = _ts(q["paid_at"])
        if p and (tid not in first or p < first[tid]):
            first[tid] = p
        if q["lead_id"] in seen:
            continue
        seen.add(q["lead_id"])
        active.setdefault(tid, []).append(q)
    return active, first


# ---------- การชำระ ----------
def _stripe_past_due(lead_id) -> bool:
    """Stripe แจ้ง past_due/unpaid หลังการชำระครั้งล่าสุด (บันทึกในไทม์ไลน์ลีดโดย stripe_pay.webhook)"""
    if not lead_id:
        return False
    for e in db.list_lead_events(lead_id, 300):                      # ใหม่ → เก่า
        note = e["note"] or ""
        if e["kind"] == "paid":
            return False
        if e["kind"] == "note" and note.startswith("Stripe:") and ("past_due" in note or "unpaid" in note):
            return True
    return False


def payment_status(tid: int, qs: list, today: dt.date) -> dict:
    invs = [i for i in db.list_acct_docs("invoice", tenant_id=tid, limit=200) if i["status"] in ("open", "claimed")]
    over = [i for i in invs if A._date(i["due_date"]) and A._date(i["due_date"]) < today]
    rc = db.list_acct_docs("receipt", tenant_id=tid, limit=1)
    out = {"invoice": dict(invs[0]) if invs else None, "receipt": dict(rc[0]) if rc else None, "overdue_days": 0, "amount": 0.0}
    if over:
        days = max((today - A._date(i["due_date"])).days for i in over)
        claimed = all(i["status"] == "claimed" for i in over)
        out.update(key="overdue", amount=round(sum(i["total"] for i in over), 2), overdue_days=days, invoice=dict(over[0]),
                   label=(f"แจ้งชำระแล้ว รอตรวจยอด · เลยกำหนด {days} วัน" if claimed else f"ค้างชำระ {days} วัน"))
    elif any(_key(q, "paid_via") == "stripe" and _stripe_past_due(q["lead_id"]) for q in qs):
        out.update(key="overdue", label="ตัดบัตรไม่ผ่าน (Stripe)")
    elif invs:
        i = invs[0]
        out.update(key="claimed" if i["status"] == "claimed" else "open", amount=round(sum(x["total"] for x in invs), 2),
                   label=("แจ้งชำระแล้ว รอตรวจยอด" if i["status"] == "claimed" else f"ใบแจ้งหนี้รอชำระ · ครบกำหนด {A.th_date(i['due_date'])}"))
    elif qs:
        via = "ตัดบัตรอัตโนมัติ" if any(_key(q, "paid_via") == "stripe" for q in qs) else "ชำระแล้ว"
        out.update(key="paid", label=via)
    else:
        out.update(key="none", label="ไม่มีบิลในระบบ")
    return out


def _payment(tid: int, qs: list, on: bool, today: dt.date) -> dict:
    """บัญชีที่กลับเป็นแผนฟรีและไม่มีบิลค้าง = ไม่มีบิลค้าง (ไม่ใช่ "ชำระแล้ว")"""
    out = payment_status(tid, qs, today)
    if not on and out["key"] == "paid":
        out.update(key="none", label="ไม่มีบิลค้าง")
    return out


# ---------- ผลงาน ----------
def sov(bid: int) -> dict | None:
    """Share of Voice ล่าสุด (%) และการเปลี่ยนแปลงเทียบรอบที่เก่ากว่า 4 สัปดาห์ (จุด %) · share_of_voice เก็บเป็นสัดส่วน 0–1"""
    runs = [r for r in db.list_runs(bid) if r["status"] == "done" and r["share_of_voice"] is not None and _ts(r["started_at"])]
    if not runs:
        return None
    last = runs[0]
    cut = _ts(last["started_at"]) - dt.timedelta(days=SOV_LOOKBACK)
    prev = next((r for r in runs[1:] if _ts(r["started_at"]) <= cut), None)
    pct = round(float(last["share_of_voice"]) * 100, 1)
    delta = round(pct - float(prev["share_of_voice"]) * 100, 1) if prev else None
    return {"pct": pct, "delta": delta, "at": str(last["started_at"])[:10]}


def ai_mentions(bid: int, vis: dict) -> dict | None:
    """AI เอ่ยถึง/อ้างอิง: รอบแรก → ล่าสุด จาก marketing.ai_trend (มีเมื่อดีขึ้น) ไม่มีก็ใช้รอบล่าสุดจาก results.visibility"""
    from . import marketing
    try:
        t = marketing.ai_trend(bid)
    except Exception:
        t = None
    if t:
        return {"first": t["first"]["hits"], "last": t["last"]["hits"], "asked": t["asked"], "up": True}
    s = vis.get("series") or []
    if not s:
        return None
    return {"first": s[0]["hits"] if len(s) > 1 else None, "last": s[-1]["hits"], "asked": s[-1]["asked"], "up": False}


# ---------- รวม ----------
def rows(today: dt.date | None = None, now: dt.datetime | None = None) -> list[dict]:
    now = now or dt.datetime.now()
    today = today or now.date()
    active, first = _active_quotes()
    paid_plans = {k for k in billing.PLANS if k != "free"}
    out = []
    for t in db.list_all_tenants():
        if t["is_admin"]:
            continue
        tid, plan = t["id"], (t["plan"] or "free")
        qs = active.get(tid, [])
        if plan not in paid_plans and not qs:
            continue                                                  # แผนฟรีที่ไม่เคยจ่าย = ยังไม่ใช่ลูกค้า
        brands = list(db.list_brands(tid))
        q0 = qs[0] if qs else None
        brand = next((b for b in brands if q0 and b["id"] == q0["brand_id"]), None) or (brands[0] if brands else None)
        on = plan in paid_plans
        mrr = sum(monthly_of(q) for q in qs) if on else 0.0          # ไม่ปัดต่อแถว (รายปี ÷ 12 มีเศษ) — ปัดตอนแสดง
        vat = Q.vat_pct(q0) if q0 else 0.0
        renewals = sorted((d, q) for q in qs if (d := next_renewal(q, today))) if on else []
        nxt, nq = renewals[0] if renewals else (None, None)
        since = first.get(tid) or _ts(t["created_at"])
        days = (now - since).days if since else 0
        lead = db.get_lead(q0["lead_id"]) if q0 else None
        dom = L.domain_of(brand["domain"]) if brand and brand["domain"] else (lead["domain"] if lead else "")
        if not lead and dom:
            lead = db.find_lead_by_domain(dom)
        pay = _payment(tid, qs, on, today)
        vis = RS.visibility(brand["id"]) if brand else {}
        rv = db.last_result_review(brand["id"]) if brand else None
        hc = db.last_health_check(brand["id"]) if brand else None
        flags = []
        if pay["key"] == "overdue":
            flags.append(("overdue", pay["label"]))
        if qs and not on:
            flags.append(("free", "บัญชีกลับเป็นแผนฟรี (ยกเลิกหรือจ่ายไม่ผ่าน)"))
        if not brand:
            flags.append(("install", "ยังไม่มีแบรนด์ในบัญชี"))
        else:
            ok, det = S.installed(brand)
            if days >= INSTALL_DAYS and not ok:
                flags.append(("install", f"ครบ {days} วันแล้วยังไม่ติดตั้ง ({det})"))
            if days >= CONTENT_DAYS and not S.content_counts(brand["id"])["total"]:
                flags.append(("content", f"ครบ {days} วันแล้วยังไม่มีคอนเทนต์ชิ้นแรก"))
            if vis.get("stalled") or (rv and rv["state"] in ("stalled", "blocked")):
                flags.append(("flat", f"AI ยังไม่เอ่ยถึงมา {RS.STALL_WEEKS} สัปดาห์"))
        custom = bool(q0 and Q.is_custom(q0))
        out.append({
            "tenant_id": tid, "name": t["name"] or (brand["name"] if brand else "") or dom or f"บัญชี #{tid}", "email": t["email"],
            "brand": dict(brand) if brand else None, "n_brands": len(brands), "domain": dom,
            "plan": plan, "plan_key": CUSTOM if custom else plan, "active": on,
            "plan_label": ("ราคาตั้งเอง" if custom else billing.PLANS.get(plan, {}).get("label", plan)),
            "custom_label": (Q._f(q0, "custom_label") or "") if custom else "",
            "cycle": Q.cycle_of(q0) if q0 else None, "cycle_label": Q.CYCLES.get(Q.cycle_of(q0), "") if q0 else "",
            "n_quotes": len(qs), "has_quote": bool(qs), "mrr": mrr, "vat_pct": vat,
            "period_amount": round(sum(period_amount(q) for q in qs), 2) if qs else 0.0,
            "list_price": float(billing.PLANS.get(plan, {}).get("price") or 0),
            "since": since.date() if since else None, "days": days,
            "next_date": nxt, "next_in": (nxt - today).days if nxt else None,
            "next_amount": period_amount(nq) if nq else 0.0, "next_vat": round(period_amount(nq) * Q.vat_pct(nq) / 100, 2) if nq else 0.0,
            "pay": pay, "sov": sov(brand["id"]) if brand else None, "ai": ai_mentions(brand["id"], vis) if brand else None,
            "health": ({"ok": bool(hc["ok"]), "fail": hc["n_fail"], "warn": hc["n_warn"], "at": str(hc["checked_at"])[:10]} if hc else None),
            "self_serve": db.tenant_self_serve(t),
            "flags": [{"key": k, "label": FLAGS[k][0], "detail": d} for k, d in flags],
            "score": sum(FLAGS[k][1] for k, _ in flags),
            "lead_id": lead["id"] if lead else None, "quote_token": q0["token"] if q0 else None, "quote_number": q0["number"] if q0 else None,
        })
    out.sort(key=lambda r: (-r["score"], r["next_in"] if r["next_in"] is not None else 99999, -r["mrr"], r["name"].lower()))
    return out


def kpis(rs: list[dict], today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    act = [r for r in rs if r["active"]]
    mrr = sum(r["mrr"] for r in act)
    ren = [r for r in act if r["next_in"] is not None and r["next_in"] <= RENEW_WINDOW]
    noq = [r for r in act if not r["has_quote"]]
    inv = [i for i in db.list_acct_docs("invoice", limit=10000) if i["status"] in ("open", "claimed")]
    over = [i for i in inv if A._date(i["due_date"]) and A._date(i["due_date"]) < today]      # นิยามเดียวกับหน้าบัญชี (accounting.summary)
    return {"active": len(act), "mrr": round(mrr, 2), "arr": round(mrr * 12, 2),
            "mrr_vat": round(sum(r["mrr"] * r["vat_pct"] / 100 for r in act), 2),
            "renew_n": len(ren), "renew_amount": round(sum(r["next_amount"] for r in ren), 2),
            "renew_vat": round(sum(r["next_vat"] for r in ren), 2), "renew_year": sum(1 for r in ren if r["cycle"] == "year"),
            "overdue_n": len(over), "overdue_amount": round(sum(i["total"] for i in over), 2),
            "stripe_overdue": sum(1 for r in rs if r["pay"]["key"] == "overdue" and not r["pay"]["amount"]),
            "noquote_n": len(noq), "noquote_list": round(sum(r["list_price"] for r in noq), 2),
            "attention": sum(1 for r in rs if r["flags"]), "inactive": len(rs) - len(act)}


def apply_filters(rs: list[dict], q: str = "", plan: str = "", attention: bool = False, renewing: bool = False) -> list[dict]:
    s = (q or "").strip().lower()
    out = []
    for r in rs:
        if s and not any(s in (v or "").lower() for v in (r["name"], r["email"], r["domain"], (r["brand"] or {}).get("name"), r["custom_label"])):
            continue
        if plan and r["plan_key"] != plan:
            continue
        if attention and not r["flags"]:
            continue
        if renewing and not (r["next_in"] is not None and r["next_in"] <= RENEW_WINDOW):
            continue
        out.append(r)
    return out


def plan_options(rs: list[dict]) -> list[tuple[str, str]]:
    keys = []
    for r in rs:
        if r["plan_key"] not in keys:
            keys.append(r["plan_key"])
    return [(k, "ราคาตั้งเอง" if k == CUSTOM else billing.PLANS.get(k, {}).get("label", k)) for k in keys]


def context(q: str = "", plan: str = "", attention: bool = False, renewing: bool = False, today: dt.date | None = None) -> dict:
    now = dt.datetime.now() if today is None else dt.datetime.combine(today, dt.time(12))
    rs = rows(now.date(), now)
    shown = apply_filters(rs, q, plan, attention, renewing)
    return {"rows": shown, "total": len(rs), "k": kpis(rs, now.date()), "plans": plan_options(rs), "flags": FLAGS,
            "f": {"q": q, "plan": plan, "attention": attention, "renewing": renewing}, "window": RENEW_WINDOW,
            "today": now.date(), "th_date": A.th_date, "baht": Q.baht, "vat_now": Q.vat_rate_now()}


# ---------- CSV ----------
CSV_HEAD = ["บัญชี", "อีเมลบัญชี", "แบรนด์", "โดเมน", "แผน", "รอบบิล", "MRR (บาท ไม่รวม VAT)", "VAT %", "ยอดต่อรอบ (บาท ไม่รวม VAT)",
            "ลูกค้าตั้งแต่", "ครบรอบถัดไป", "อีก (วัน)", "สถานะชำระ", "ยอดค้าง (บาท)", "Share of Voice %", "SoV เทียบ 4 สัปดาห์ (จุด)",
            "AI เอ่ยถึง (รอบแรก→ล่าสุด)", "สุขภาพเว็บ", "ดูแลโดย", "สัญญาณเสี่ยง"]


def csv_text(rs: list[dict]) -> str:
    """คอลัมน์เดียวกับหน้าเว็บ · ไม่มีลิงก์ที่มีโทเค็น (ใบเสนอราคา/ใบเสร็จ) หรือข้อมูลลับ"""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_HEAD)
    for r in rs:
        ai = r["ai"]
        h = r["health"]
        w.writerow([r["name"], r["email"], (r["brand"] or {}).get("name", ""), r["domain"],
                    r["plan_label"] + (f" ({r['custom_label']})" if r["custom_label"] else ""), r["cycle_label"] or "",
                    f"{r['mrr']:.2f}" if r["has_quote"] else "", f"{r['vat_pct']:g}" if r["has_quote"] else "",
                    f"{r['period_amount']:.2f}" if r["has_quote"] else "",
                    r["since"].isoformat() if r["since"] else "", r["next_date"].isoformat() if r["next_date"] else "",
                    "" if r["next_in"] is None else r["next_in"], r["pay"]["label"], f"{r['pay']['amount']:.2f}" if r["pay"]["amount"] else "",
                    f"{r['sov']['pct']:g}" if r["sov"] else "", "" if not r["sov"] or r["sov"]["delta"] is None else f"{r['sov']['delta']:+g}",
                    (f"{ai['first']}→{ai['last']} จาก {ai['asked']}" if ai and ai["first"] is not None else f"{ai['last']} จาก {ai['asked']}") if ai else "",
                    ("ผ่าน" if h["ok"] else f"ต้องแก้ {h['fail']} ข้อ") if h else "",
                    "ใช้เอง" if r["self_serve"] else "ทีมดูแล", " · ".join(f["label"] for f in r["flags"])])
    return "﻿" + buf.getvalue()                                  # BOM ให้ Excel อ่านภาษาไทยถูก
