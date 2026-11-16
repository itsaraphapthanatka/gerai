"""รายได้ ที่มา และต้นทุน (stage 17) — flag `revenue_v2` (ปิด = ไม่มีหน้า/ไม่บันทึกอะไรเพิ่ม)

นิยามชุดเดียวมีเวอร์ชัน (DEF_VERSION) — ทุกตัวเลขในหน้าบอกว่าใช้นิยามเวอร์ชันไหน
เงิน: นับจากเอกสารบัญชีที่ออกจริง (ใบเสร็จที่ไม่ถูกยกเลิก) + รายการคืนเงิน/ลดหนี้ — ไม่ใช่จากหน้า "ขอบคุณ" หรือใบเสนอราคา
  · ยอดก่อน VAT · แยกครั้งเดียว (Setup) กับรายรอบ · MRR = รายรอบที่ยังไม่หมดรอบ แปลงเป็นต่อเดือน (รายปี ÷ 12) ไม่รวมครั้งเดียว
  · หนึ่งใบเสร็จนับครั้งเดียว ผูกกับบัญชีเดียว (หลายผู้ติดต่อ/หลายลีดของโดเมนเดียวกัน = บัญชีเดียว)
ที่มา: แสดงหลายมุมคู่กัน (แรกที่รู้ · ล่าสุดที่มีความหมาย · พันธมิตรแนะนำ · ลูกค้าบอกเอง) — แต่ละมุมให้เครดิตเต็มกับที่มาเดียว ไม่บวกข้ามมุม
  · เข้าตรง ≠ AI · การส่งต่อส่วนตัว (LINE/แชต) และข้ามอุปกรณ์มองไม่เห็น → อาจอยู่ใน "เข้าตรง/ไม่ทราบ"
ต้นทุน: ค่า API จริง · งบที่จองแล้วยังไม่ปิด (แยก) · ค่าแรง/เครื่องมือที่กรอกเอง (จริงหรือประมาณ ติดป้าย) · แยกหาลูกค้า vs ส่งมอบ
  · ข้อมูลไม่ครบ = ไม่คำนวณกำไร บอกว่าขาดอะไร"""
from __future__ import annotations
import datetime as dt
import json
import re

from . import db, flags

DEF_VERSION = "rev-v1"
STAGES = (("audit_complete", "ตรวจเว็บเสร็จ"), ("context_confirmed", "ยืนยันข้อมูลธุรกิจ"), ("qualified", "โอกาสที่คัดแล้ว"),
          ("booked", "นัดคุยแล้ว"), ("held", "คุยแล้ว"), ("proposal", "ส่งใบเสนอราคา"), ("won", "ปิดได้ (จ่ายแล้ว)"), ("lost", "ไม่ได้ดีล"))
DEFINITIONS = {
    "audit_complete": "ลีดมีผลตรวจเว็บ (audit_at) หรือเหตุการณ์ audit_completed",
    "context_confirmed": "มีข้อมูลธุรกิจที่ลูกค้า/ทีมบันทึกอย่างน้อย 1 เวอร์ชัน",
    "qualified": "ขั้นโอกาสขาย (stage 11) เป็น qualified ขึ้นไป",
    "booked": "มีนัดที่ทีมยืนยัน หรือเหตุการณ์ meeting_booked",
    "held": "นัดถูกบันทึกว่าคุยแล้ว หรือสถานะเดิม met ขึ้นไป",
    "proposal": "มีใบเสนอราคาที่ส่งแล้ว (ไม่นับร่าง)",
    "won": "มีใบเสร็จที่ออกจริงและไม่ถูกยกเลิก",
    "lost": "สถานะลีด lost (ขอไม่รับการติดต่อแยกนับ ไม่ใช่แพ้ดีล)",
    "cash": "ยอดใบเสร็จก่อน VAT ที่ไม่ถูกยกเลิก − คืนเงิน/ลดหนี้",
    "one_time": "รายการที่เป็น Setup / ครั้งเดียว",
    "mrr": "รายรอบก่อน VAT ที่รอบบริการยังไม่หมด แปลงต่อเดือน (รายปี ÷ 12) · ไม่รวมครั้งเดียว · ใบที่ถูกคืนเงินเต็มไม่นับ",
}
ONE_TIME_RE = re.compile(r"setup|ครั้งเดียว|one[- ]time", re.I)
COST_CATEGORIES = {"api": "ค่า API (AI/ค้นหา)", "processing": "ประมวลผล/เซิร์ฟเวอร์", "labor": "ค่าแรง", "tools": "เครื่องมือ/บริการภายนอก",
                   "delivery_other": "ส่งมอบอื่น ๆ"}
ALLOC = {"acquisition": "หาลูกค้า", "delivery": "ส่งมอบ"}
REQUIRED_DELIVERY = ("api", "labor")        # ต้องมีข้อมูลก่อนจะบอกกำไรขั้นต้นจากการส่งมอบ
PUBLIC_KEYS = ("def_version", "period", "won_accounts", "by_first_source")


def on() -> bool:
    try:
        return flags.enabled("revenue_v2")
    except Exception:
        return False


def _iso(d) -> str:
    return d.isoformat(timespec="seconds") if isinstance(d, dt.datetime) else str(d)


def _date(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def account_of(lead) -> str:
    from . import c360
    return c360.account_key(lead["site_url"] or lead["domain"]) or f"lead:{lead['id']}"


# ---------- เงิน (จากเอกสารบัญชีจริง) ----------
def adjustments(since: str | None = None, until: str | None = None) -> list[dict]:
    sql, a = "SELECT * FROM finance_adjustments WHERE 1=1", []
    if since:
        sql += " AND created_at>=?"; a.append(since)
    if until:
        sql += " AND created_at<?"; a.append(until)
    with db.get_conn() as c:
        return [dict(r) for r in c.execute(db.q(sql + " ORDER BY id"), tuple(a)).fetchall()]


def record_adjustment(kind: str, amount: float, *, ref: str, lead_id: int | None = None, quote_id: int | None = None, receipt_id: int | None = None,
                      note: str = "", by: str = "system") -> int | None:
    """คืนเงิน/ลดหนี้/ยกเลิก — กันซ้ำด้วย ref (เช่น id ของ Stripe) · ยอดบวกเสมอ (หักออกตอนคำนวณ) · ยอดรวม VAT"""
    if kind not in ("refund", "credit", "cancellation") or amount < 0:
        raise ValueError("รายการไม่ถูกต้อง")
    with db.get_conn() as c:
        r = c.execute(db.q("INSERT INTO finance_adjustments(kind, amount, ref, lead_id, quote_id, receipt_id, note, created_by, created_at) "
                           "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING RETURNING id"),
                      (kind, float(amount), ref[:120], lead_id, quote_id, receipt_id, note[:300], by[:120], db.now())).fetchone()
    return r["id"] if r else None


def _ex_vat(doc, amount: float) -> float:
    tot = float(doc["total"] or 0)
    return round(amount * float(doc["subtotal"] or 0) / tot, 2) if tot else 0.0


def ledger(since: str | None = None, until: str | None = None) -> list[dict]:
    """แถวเงินต่อใบเสร็จ (ก่อน VAT): ครั้งเดียว / รายรอบ / ช่วงบริการ · ใบที่ยกเลิกไม่นับ"""
    out = []
    for d in db.list_acct_docs(kind="receipt", since=since, until=until, limit=100000):
        if d["status"] == "void":
            continue
        items = json.loads(d["items_json"] or "[]") or [{"desc": d["description"] or "", "amount": d["total"]}]
        gross_items = sum(float(i.get("amount") or 0) for i in items) or float(d["total"] or 0)
        scale = float(d["total"] or 0) / gross_items if gross_items else 0
        one = sum(float(i.get("amount") or 0) for i in items if ONE_TIME_RE.search(i.get("desc") or "")) * scale
        rec = float(d["total"] or 0) - one
        lead = db.get_lead(d["lead_id"]) if d["lead_id"] else None
        out.append({"id": d["id"], "number": d["number"], "lead_id": d["lead_id"], "account": account_of(lead) if lead else f"doc:{d['id']}",
                    "quote_id": d["quote_id"], "at": d["created_at"], "net": float(d["subtotal"] or 0), "one_time": _ex_vat(d, one),
                    "recurring": _ex_vat(d, rec), "period_start": d["period_start"], "period_end": d["period_end"],
                    "months": _months(d["period_start"], d["period_end"])})
    return out


def _months(a, b) -> int:
    da, dbb = _date(a), _date(b)
    if not (da and dbb):
        return 1
    e1 = dbb + dt.timedelta(days=1)                        # ช่วงบริการรวมวันสุดท้าย: 1 พ.ย.–30 พ.ย. = 1 เดือน
    n = (e1.year - da.year) * 12 + (e1.month - da.month) + (1 if e1.day > da.day else 0)
    return max(1, min(12, n))


def finance(period_start: dt.date, period_end: dt.date, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    s, e = period_start.isoformat(), (period_end + dt.timedelta(days=1)).isoformat()
    rows = ledger(s, e)
    adj = adjustments(s, e)
    refunds = sum(_adj_ex_vat(a) for a in adj)
    one = round(sum(r["one_time"] for r in rows), 2)
    rec = round(sum(r["recurring"] for r in rows), 2)
    # MRR: รายรอบทั้งหมด (ไม่จำกัดช่วง) ที่รอบบริการครอบวันนี้ · ใบที่คืนเงินเต็มไม่นับ
    full_refunded = {a["receipt_id"] for a in adjustments() if a["kind"] in ("refund", "cancellation") and a["receipt_id"]
                     and _adj_ex_vat(a) >= _receipt_net(a["receipt_id"]) - 0.01}
    mrr = 0.0
    for r in ledger():
        ps, pe = _date(r["period_start"]), _date(r["period_end"])
        if r["id"] in full_refunded or not ps or not pe or not (ps <= today <= pe):
            continue
        mrr += r["recurring"] / r["months"]
    accts = {r["account"] for r in rows}
    return {"def_version": DEF_VERSION, "receipts": len(rows), "accounts": len(accts), "one_time": one, "recurring": rec,
            "refunds": round(refunds, 2), "cash_net": round(one + rec - refunds, 2), "mrr": round(mrr, 2), "rows": rows, "adjustments": adj}


def _receipt_net(rid: int) -> float:
    d = db.get_acct_doc(rid)
    return float(d["subtotal"] or 0) if d else 0.0


def _adj_ex_vat(a: dict) -> float:
    """คืนเงินผูกใบเสร็จ = แยก VAT ตามสัดส่วนใบนั้น · ไม่ผูก = ถือว่าเป็นยอดก่อน VAT ตามที่กรอก"""
    if a.get("receipt_id"):
        d = db.get_acct_doc(a["receipt_id"])
        if d:
            return _ex_vat(d, float(a["amount"]))
    return float(a["amount"])


# ---------- ขั้น funnel (นิยามเดียว) ----------
def stages_for(lead) -> dict:
    from . import sales
    lid = lead["id"]
    with db.get_conn() as c:
        ev = {r["name"] for r in c.execute(db.q("SELECT DISTINCT name FROM analytics_events WHERE lead_id=?"), (lid,)).fetchall()}
        ctx = c.execute(db.q("SELECT 1 FROM business_contexts WHERE lead_id=? LIMIT 1"), (lid,)).fetchone()
        ap = {r["status"] for r in c.execute(db.q("SELECT status FROM appointments WHERE lead_id=?"), (lid,)).fetchall()}
    quotes = list(db.list_quotes(lid))
    won = any(d["status"] != "void" for d in db.list_acct_docs(kind="receipt", lead_id=lid, limit=50))
    opp = sales.opportunity(lead)["stage"]
    st = lead["status"] or "new"
    return {"audit_complete": bool(lead["audit_at"]) or "audit_completed" in ev,
            "context_confirmed": bool(ctx),
            "qualified": opp in ("qualified", "meeting_booked", "meeting_held", "proposal", "won") or st in ("met", "quoted", "won"),
            "booked": "confirmed" in ap or "completed" in ap or "meeting_booked" in ev,
            "held": "completed" in ap or st in ("met", "quoted", "won"),
            "proposal": any(q["status"] not in ("draft",) and q["sent_at"] for q in quotes),
            "won": won, "lost": st == "lost"}


def funnel(leads=None) -> dict:
    """นับต่อบัญชี (หลายลีดของบัญชีเดียวกัน = 1)"""
    acc: dict[str, dict] = {}
    for l in leads if leads is not None else db.list_leads(None, 100000):
        k = account_of(l)
        s = stages_for(l)
        cur = acc.setdefault(k, {x: False for x, _ in STAGES})
        for x in cur:
            cur[x] = cur[x] or s[x]
    return {"def_version": DEF_VERSION, "accounts": len(acc), "counts": {x: sum(1 for v in acc.values() if v[x]) for x, _ in STAGES}}


# ---------- ที่มา ----------
MEANINGFUL = ("plan_requested", "booking_link_clicked", "meeting_booked", "business_context_confirmed")


def touches(lead) -> list[dict]:
    with db.get_conn() as c:
        return [dict(r) for r in c.execute(db.q("SELECT name, source, utm_source, created_at FROM analytics_events WHERE lead_id=? ORDER BY created_at, id"),
                                           (lead["id"],)).fetchall()]


def attribution(lead) -> dict:
    """มุมต่าง ๆ ของที่มาลีดหนึ่งราย — ไม่ใช่การบอกว่าอะไรทำให้ปิดได้"""
    from . import analytics
    t = touches(lead)
    src = (lead["src"] or "") if "src" in lead.keys() else ""
    fallback = analytics.classify(partner=bool(lead["partner_id"]), src=src)["source"] if (lead["partner_id"] or src) else "unknown"
    first = t[0]["source"] if t else fallback
    meaningful = [x for x in t if x["name"] in MEANINGFUL]
    last = meaningful[-1]["source"] if meaningful else (t[-1]["source"] if t else fallback)
    sr = lead["self_reported_source"] if "self_reported_source" in lead.keys() else None
    return {"first": first or "unknown", "last_meaningful": last or "unknown", "partner": bool(lead["partner_id"]),
            "campaigns": sorted({x["utm_source"] for x in t if x["utm_source"]}), "self_reported": sr or None, "touch_count": len(t)}


def attribution_report(period_start: dt.date, period_end: dt.date) -> dict:
    """บัญชีที่ปิดได้ในช่วง (จากใบเสร็จ) × มุมที่มา · เครดิต 1 ต่อบัญชีต่อมุม (รวมแต่ละมุม = จำนวนบัญชี ไม่บวกข้ามมุม)"""
    fin = finance(period_start, period_end)
    by_acct: dict[str, dict] = {}
    for r in fin["rows"]:
        a = by_acct.setdefault(r["account"], {"net": 0.0, "lead_ids": set()})
        a["net"] += r["net"]
        if r["lead_id"]:
            a["lead_ids"].add(r["lead_id"])
    views = {"first": {}, "last_meaningful": {}, "partner": {}, "self_reported": {}}
    rows = []
    idx: dict[str, list] = {}
    for l in db.list_leads(None, 100000):                      # ผู้ติดต่อทุกคนของบัญชี (ไม่ใช่แค่คนที่มีใบเสร็จ)
        idx.setdefault(account_of(l), []).append(l)
    for k, a in by_acct.items():
        leads = sorted(idx.get(k) or [db.get_lead(i) for i in a["lead_ids"]], key=lambda l: l["id"])
        lead = leads[0] if leads else None                     # ลีดแรกของบัญชี = ผู้ติดต่อที่รู้จักก่อน
        at = attribution(lead) if lead else {"first": "unknown", "last_meaningful": "unknown", "partner": False, "self_reported": None, "campaigns": []}
        if len(leads) > 1:                                     # หลายผู้ติดต่อ: ล่าสุดที่มีความหมายดูจากทุกคนในบัญชี
            ts = sorted((x for l in leads for x in touches(l) if x["name"] in MEANINGFUL), key=lambda x: x["created_at"])
            if ts:
                at["last_meaningful"] = ts[-1]["source"]
            at["partner"] = any(l["partner_id"] for l in leads)
        for view, key in (("first", at["first"]), ("last_meaningful", at["last_meaningful"]),
                          ("partner", "partner" if at["partner"] else "not_partner"), ("self_reported", at["self_reported"] or "not_asked")):
            v = views[view].setdefault(key, {"accounts": 0, "net": 0.0})
            v["accounts"] += 1
            v["net"] = round(v["net"] + a["net"], 2)
        rows.append({"account": k, "net": round(a["net"], 2), **{x: at[x] for x in ("first", "last_meaningful", "partner", "self_reported", "campaigns")}})
    return {"def_version": DEF_VERSION, "won_accounts": len(by_acct), "views": views, "rows": rows,
            "gaps": ["การส่งต่อส่วนตัว (LINE/แชต) และการเปลี่ยนอุปกรณ์มองไม่เห็น → อาจถูกนับเป็น \"เข้าตรง\" หรือ \"ไม่ทราบ\"",
                     "\"เข้าตรง\" ไม่ได้แปลว่ามาจาก AI · นับเป็นผู้ช่วย AI เฉพาะเมื่อเว็บที่พามาเป็นผู้ช่วย AI",
                     "แต่ละมุมให้เครดิตเต็มกับที่มาเดียว ไม่ได้บอกว่าอะไรทำให้ปิดการขาย"]}


def public_summary(period_start: dt.date, period_end: dt.date) -> dict:
    """ส่งออกได้: จำนวนเท่านั้น ไม่มีเงินรายบัญชี ไม่มีโดเมน/อีเมล/ id · ผ่าน allowlist"""
    rep = attribution_report(period_start, period_end)
    out = {"def_version": DEF_VERSION, "period": f"{period_start.isoformat()}..{period_end.isoformat()}", "won_accounts": rep["won_accounts"],
           "by_first_source": {k: v["accounts"] for k, v in rep["views"]["first"].items()}}
    return {k: out[k] for k in PUBLIC_KEYS}


# ---------- ต้นทุน ----------
def add_cost(category: str, alloc: str, amount_thb: float, *, period: str, basis: str = "actual", note: str = "", lead_id: int | None = None,
             by: str = "owner") -> int:
    if category not in COST_CATEGORIES or alloc not in ALLOC or basis not in ("actual", "estimate") or amount_thb < 0:
        raise ValueError("ข้อมูลต้นทุนไม่ถูกต้อง")
    if not re.fullmatch(r"\d{4}-\d{2}", period or ""):
        raise ValueError("งวดต้องเป็น YYYY-MM")
    with db.get_conn() as c:
        return c.execute(db.q("INSERT INTO cost_entries(category, alloc, amount_thb, period, basis, note, lead_id, created_by, created_at) "
                              "VALUES(?,?,?,?,?,?,?,?,?) RETURNING id"),
                         (category, alloc, float(amount_thb), period, basis, note[:300], lead_id, by[:120], db.now())).fetchone()["id"]


def fx() -> float | None:
    try:
        v = float(db.get_setting("fx_usd_thb") or "")
        return v if v > 0 else None
    except ValueError:
        return None


def _paying_brands() -> set:
    with db.get_conn() as c:
        return {r["brand_id"] for r in c.execute(db.q("SELECT DISTINCT q.brand_id FROM quotes q JOIN acct_docs d ON d.quote_id=q.id "
                                                      "WHERE d.kind='receipt' AND d.status<>'void' AND q.brand_id IS NOT NULL")).fetchall()}


def costs(period: str) -> dict:
    """period = YYYY-MM · ค่า API จริงแยกหาลูกค้า/ส่งมอบ · งบที่จองยังไม่ปิดแยกไว้ (ไม่ใช่ค่าใช้จ่ายจริง) · กรอกเอง (จริง/ประมาณ)"""
    rate = fx()
    paying = _paying_brands()
    with db.get_conn() as c:
        av = c.execute(db.q("SELECT brand_id, COALESCE(SUM(cost_usd),0) AS usd FROM ai_visibility WHERE checked_at LIKE ? GROUP BY brand_id"),
                       (period + "%",)).fetchall()
        jobs_usd = c.execute(db.q("SELECT COALESCE(SUM(actual),0) AS a FROM budget_reservations WHERE status='committed' AND day LIKE ?"), (period + "%",)).fetchone()["a"]
        reserved = c.execute(db.q("SELECT COALESCE(SUM(amount),0) AS a FROM budget_reservations WHERE status='reserved' AND day LIKE ?"), (period + "%",)).fetchone()["a"]
        entries = [dict(r) for r in c.execute(db.q("SELECT * FROM cost_entries WHERE period=? ORDER BY id"), (period,)).fetchall()]
    api_usd = {"delivery": sum(r["usd"] for r in av if r["brand_id"] in paying), "acquisition": sum(r["usd"] for r in av if r["brand_id"] not in paying) + float(jobs_usd)}
    out = {a: {"api_usd": round(api_usd[a], 4), "api_thb": round(api_usd[a] * rate, 2) if rate else None, "entries": {}, "estimate_thb": 0.0, "actual_thb": 0.0}
           for a in ALLOC}
    for e in entries:
        b = out[e["alloc"]]
        b["entries"][e["category"]] = round(b["entries"].get(e["category"], 0) + e["amount_thb"], 2)
        b["estimate_thb" if e["basis"] == "estimate" else "actual_thb"] = round(b["estimate_thb" if e["basis"] == "estimate" else "actual_thb"] + e["amount_thb"], 2)
    missing = []
    if not rate:
        missing.append("อัตราแลกเปลี่ยน (setting fx_usd_thb) — ค่า API แสดงเป็น USD")
    for cat in REQUIRED_DELIVERY:
        if cat != "api" and cat not in out["delivery"]["entries"]:
            missing.append(f"ต้นทุนส่งมอบหมวด \"{COST_CATEGORIES[cat]}\" ของงวด {period}")
    return {"def_version": DEF_VERSION, "period": period, "by_alloc": out, "reserved_usd": round(float(reserved), 4), "missing": missing,
            "has_estimates": any(e["basis"] == "estimate" for e in entries), "entries": entries}


def margin(period: str) -> dict:
    """กำไรขั้นต้นจากการส่งมอบ = รายได้งวดนี้ − ต้นทุนส่งมอบ · ข้อมูลไม่ครบ = ไม่คำนวณ"""
    y, m = map(int, period.split("-"))
    start = dt.date(y, m, 1)
    end = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))
    fin, cst = finance(start, end), costs(period)
    if cst["missing"]:
        return {"available": False, "why": cst["missing"], "revenue": fin["cash_net"]}
    d = cst["by_alloc"]["delivery"]
    cost = (d["api_thb"] or 0) + d["actual_thb"] + d["estimate_thb"]
    return {"available": True, "revenue": fin["cash_net"], "delivery_cost": round(cost, 2), "gross": round(fin["cash_net"] - cost, 2),
            "includes_estimates": d["estimate_thb"] > 0, "acquisition_cost_separate": cst["by_alloc"]["acquisition"]}
