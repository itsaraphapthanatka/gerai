"""ผลรายเดือนและการดูแลลูกค้าต่อ (stage 16) — ใช้เมื่อเปิด flag `publish_v2`

รายงานรายเดือนแยก 4 หมวด ไม่ปนกัน:
  1. งานที่ส่งมอบ (สิ่งที่เราทำ)      2. สิ่งที่เห็นบนเว็บ/การค้นหา
  3. AI ที่สังเกตได้ (เทียบเฉพาะชุดคำถามเดียวกัน)   4. ผลทางธุรกิจ (มีเมื่อลูกค้าให้ข้อมูลเท่านั้น)
ไม่สรุปว่างานไหน "ทำให้" ตัวเลขขยับ · ผลจาก AI มีระยะหน่วง · "ข้อมูลยังไม่พอสรุป" เป็นผลรายเดือนที่ถูกต้อง
ขยายบัญชีต้องมีเหตุผลเรื่องความเหมาะสม/กำลัง/ขอบเขต ไม่ใช่แค่ใช้เยอะ · ทุกข้อเสนอรอคนตรวจ ไม่เก็บเงิน/อัปเกรด/เผยแพร่เคสเอง"""
from __future__ import annotations
import datetime as dt
import hashlib
import json

from . import db

LAG_NOTE = "การถูก AI แนะนำมักเปลี่ยนช้ากว่างานที่ทำหลายสัปดาห์ ตัวเลขเดือนนี้จึงไม่ได้บอกว่างานไหนได้ผล"
BLOCKER_DAYS = 3


def on() -> bool:
    from . import publishing
    return publishing.on()


def _rows(rep) -> list:
    try:
        return (json.loads(rep) if isinstance(rep, str) else rep or {}).get("rows") or []
    except Exception:
        return []


def qset(av) -> str | None:
    """ลายนิ้วมือของชุดคำถาม+เครื่องยนต์ — ต่างกัน = เทียบกันไม่ได้"""
    if not av:
        return None
    rows = _rows(av["report"])
    keys = sorted({f"{r.get('engine', '')}|{(r.get('question') or '').strip()}" for r in rows})
    if not keys:
        return f"n{av['asked']}"
    return hashlib.sha1("\n".join(keys).encode("utf-8")).hexdigest()[:12]


def _hits(av) -> int:
    return (av["cited"] or 0) + (av["named"] or 0)


def review(bid: int, now: dt.datetime | None = None) -> dict:
    from . import delivery, publishing
    now = now or dt.datetime.now()
    ym = now.strftime("%Y-%m")
    ws = delivery.for_brand(bid) if delivery.on() else None
    delivered = []
    if ws:
        delivered += [f"{delivery.KINDS.get(i['kind'], i['kind'])}: {i['title']}" for i in delivery._items(ws["id"])
                      if i["status"] in ("delivered", "accepted") and (i["updated_at"] or "").startswith(ym)]
    delivered += [f"เผยแพร่: {(db.get_content(r['content_id']) or {'title': '#'})['title']}" for r in publishing.list_brand(bid)
                  if r["status"] == "published" and (r["published_at"] or "").startswith(ym)]
    hc = list(db.list_health_checks(bid, 2)) if hasattr(db, "list_health_checks") else ([db.last_health_check(bid)] if db.last_health_check(bid) else [])
    site = {"state": "not_tested", "text": "ยังไม่ได้ตรวจสุขภาพเว็บ"}
    if hc:
        site = {"state": "measured", "text": f"ตรวจล่าสุด {str(hc[0]['checked_at'])[:10]}: ต้องแก้ {hc[0]['n_fail']} ข้อ"}
        if len(hc) > 1:
            site["text"] += f" (ครั้งก่อน {hc[1]['n_fail']} ข้อ)"
    runs = list(db.list_ai_visibility(bid, 8))
    ai = {"state": "not_tested", "text": "ยังไม่ได้ถาม AI จริง (ไม่ใช่ 0)", "comparable": False}
    if runs:
        cur = runs[0]
        ai = {"state": "measured", "text": f"AI เอ่ยถึง/อ้างอิง {_hits(cur)} จาก {cur['asked']} คำถาม ({str(cur['checked_at'])[:10]})", "comparable": False}
        base_q = None
        if ws and ws.get("baseline_json"):
            base_q = (json.loads(ws["baseline_json"]).get("lanes") or {}).get("ai_mention", {})
        if base_q and base_q.get("state") in ("found", "absent"):
            if base_q.get("qset") and base_q.get("qset") == qset(cur):
                ai["comparable"] = True
                ai["text"] += f" · ตั้งต้น {base_q.get('value')}/{base_q.get('of')} (ชุดคำถามเดียวกัน)"
            else:
                ai["text"] += " · เทียบกับค่าตั้งต้นไม่ได้: ชุดคำถาม/เครื่องมือไม่เหมือนกัน"
        elif len(runs) > 1:
            prev = next((r for r in runs[1:] if qset(r) == qset(cur)), None)
            if prev:
                ai["comparable"] = True
                ai["text"] += f" · ครั้งก่อนที่ใช้ชุดคำถามเดียวกัน {_hits(prev)}/{prev['asked']}"
            else:
                ai["text"] += " · ยังไม่มีครั้งก่อนที่ใช้ชุดคำถามเดียวกัน"
    biz = {"state": "unknown", "text": "ยังไม่มีข้อมูลผลทางธุรกิจ (ยอดติดต่อ/ยอดขายต้องได้จากคุณ)"}
    sufficient = bool(delivered) or ai["comparable"]
    return {"month": ym, "delivered": delivered, "site": site, "ai": ai, "business": biz, "sufficient": sufficient,
            "verdict": "มีข้อมูลพอสรุปสิ่งที่ทำและสิ่งที่วัดได้" if sufficient else "ข้อมูลยังไม่พอสรุปในเดือนนี้", "lag": LAG_NOTE}


# ---------- ดูแลลูกค้าตามขั้น ไม่ใช่ตามวันอย่างเดียว ----------
def cs_step(c: dict, ws: dict, now: dt.datetime, done: dict, record, send_report) -> list[str]:
    """ลูกค้าที่มีพื้นที่งาน: เตือนเมื่อติดขั้นเกิน 3 วัน (ภายใน) · รายงานรายเดือนเมื่อมีผลวัดตั้งต้นแล้ว"""
    from . import delivery
    out = []
    st = delivery.refresh(ws["id"])
    try:
        since = dt.datetime.fromisoformat(str(ws["updated_at"])[:19])
    except Exception:
        since = now
    if st in ("awaiting_customer", "awaiting_access", "blocked") and (now - since).days >= BLOCKER_DAYS:
        key = f"blocker:{st}"
        if not any(n.startswith(key) for n in done.get("cs_risk", [])):
            record(c, "cs_risk", f"{key} ค้างที่ \"{delivery.STATES[st]}\" {(now - since).days} วัน")
            try:
                from . import pipeline
                pipeline.alert_admins(f"⏳ {c['lead']['domain']} ค้างที่ {delivery.STATES[st]} {(now - since).days} วัน", "/admin/delivery", "lead")
            except Exception:
                pass
            out.append(key)
    paid = c.get("paid_at")
    days = (now - paid).days if paid else 0
    month = days // 30 + 1
    if days % 30 >= 25 and not any(n.startswith(f"เดือน {month}") for n in done.get("cs_report", [])):
        if delivery.gates(delivery.get(ws["id"]))["baseline"]:
            out.append(send_report(month))
        elif not any(n.startswith(f"รอวัดตั้งต้น {month}") for n in done.get("cs_risk", [])):
            record(c, "cs_risk", f"รอวัดตั้งต้น {month}: ยังไม่ส่งรายงานเดือน {month} เพราะยังไม่มีผลวัดตั้งต้น")
            out.append(f"report_waiting:{month}")
    return out


# ---------- ขยายบัญชี: ต้องเหมาะ ไม่ใช่แค่ใช้เยอะ ----------
def fit_reason(c: dict, s: dict, opp: str) -> str | None:
    """คืนเหตุผลที่เหมาะ (ใช้แสดงให้คนตรวจ) · None = ไม่เสนอ"""
    from . import delivery
    lead = c["lead"]
    if lead["unsub_at"] or lead["status"] == "unsubscribed":
        return None                                                   # ไม่ติดต่อคนที่ขอไม่รับ
    ws = delivery.for_brand(c["brand"]["id"]) if delivery.on() else None
    if ws and ws["state"] not in ("delivered", "accepted", "in_progress", "review"):
        return None                                                   # ยังส่งมอบไม่ได้/ติดปัญหา = ไม่ขายเพิ่ม
    if ws and ws["state"] in ("in_progress", "review") and opp != "yearly":
        return None                                                   # กำลังทำขอบเขตเดิม ยังไม่มีกำลังรับงานเพิ่ม
    if opp in ("agency", "content"):
        if s["published"] < 3 or not s["installed"]:
            return None                                               # ใช้เยอะอย่างเดียวไม่พอ ต้องเห็นว่างานเดิมเดินได้
        return f"ใช้ใกล้เต็มสิทธิ์ และงานเดิมเดินได้ (ติดตั้งแล้ว · เผยแพร่ {s['published']} ชิ้น)" + (f" · พื้นที่งาน: {delivery.STATES[ws['state']]}" if ws else "")
    if opp == "yearly":
        return f"ใช้มา {s['days']} วัน ติดตั้งแล้ว ใกล้รอบบิล — ให้ลูกค้าเลือกเอง ไม่ตัดเงินอัตโนมัติ"
    if opp == "advocate":
        return "ขออนุญาตทำเคสศึกษาเท่านั้น — ไม่เผยแพร่จนลูกค้าอนุมัติเนื้อหาเป็นลายลักษณ์อักษร (แยกจากการต่ออายุ/ซื้อเพิ่ม)"
    return None
