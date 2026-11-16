"""ข้อมูลธุรกิจที่รายงานใช้ (stage 04B, flag context_v2) — DATA_CONTRACTS §1

แต่ละช่องมีที่มา: confirmed (ลูกค้ายืนยัน) · inferred (ระบบเดาจากเว็บ ติดป้ายเสมอ) · unknown (ยังไม่ทราบ ไม่เดาเพิ่ม)
- ระบบเดาเฉพาะที่มีหลักฐานจากรายงานเต็ม: บริการหลัก = คำค้นหลักข้อแรก · คู่แข่ง = โดเมนที่ติดผลค้นคำหลักบ่อยสุด
- พื้นที่และลูกค้าเป้าหมาย: ไม่เดา (ยังไม่ทราบ จนกว่าลูกค้าบอก)
- คำถามที่ระบบเสนอ = ตัวอย่าง ไม่ใช่ปริมาณการค้น (is_search_volume เป็น False เสมอ)
- บันทึกทุกครั้งเป็นเวอร์ชันใหม่ ไม่แก้ทับ · ผลทดสอบ AI จำว่าใช้บริบทเวอร์ชันไหน (เทียบกันได้หรือไม่)"""
from __future__ import annotations

import json
import re

from . import db, leads as L

MAX_VERSIONS = 50
MAX_QUESTIONS = 5
FIELDS = (("primary_service", "บริการหลักที่อยากให้ลูกค้าค้นเจอ"), ("region", "พื้นที่ให้บริการ"),
          ("audience", "ลูกค้าเป้าหมาย"), ("competitor", "คู่แข่งที่อยากเทียบ"))
LIMITS = {"primary_service": 80, "region": 80, "audience": 120, "competitor": 100}
_Q_TPL = ("{k} ที่ไหนดี", "แนะนำ {k} ที่น่าเชื่อถือ", "{k} ราคาเท่าไหร่", "วิธีเลือก {k}")


def _t1(lead) -> dict:
    from . import deep_audit
    t1 = deep_audit.tier1(lead)
    return t1 if t1.get("state") == "done" else {}


def inferred(lead) -> dict:
    """สิ่งที่ระบบเดาได้จากรายงานเต็ม พร้อมหลักฐาน · ไม่มีรายงานเต็ม = ทุกช่องยังไม่ทราบ"""
    t1 = _t1(lead)
    kws = [k for k in (t1.get("keywords") or []) if k]
    comps = [c.get("domain") for c in ((t1.get("ranking") or {}).get("competitors") or []) if c.get("domain")]
    own = L.domain_of(lead["site_url"] or "") if lead["site_url"] else ""
    comps = [c for c in comps if c != own]
    f = {k: {"value": None, "source": "unknown"} for k, _ in FIELDS}
    if kws:
        f["primary_service"] = {"value": kws[0][:LIMITS["primary_service"]], "source": "inferred",
                                "evidence": "คำค้นหลักข้อแรกที่ระบบเลือกจากหน้าเว็บของคุณ"}
    if comps:
        f["competitor"] = {"value": comps[0], "source": "inferred", "evidence": "โดเมนที่ติดผลค้นเว็บของคำหลักบ่อยที่สุด"}
    f["questions"] = [{"text": t.format(k=k), "origin": "suggested", "is_search_volume": False}
                      for k in kws[:2] for t in _Q_TPL][:MAX_QUESTIONS]
    return f


def current(lead) -> dict:
    """บริบทที่ใช้อยู่: เวอร์ชันล่าสุดที่บันทึก (ช่องที่ลูกค้าเว้นว่าง = ยังไม่ทราบ) · ยังไม่เคยบันทึก = ที่ระบบเดา (version 0)"""
    row = db.latest_business_context(lead["id"])
    if row:
        try:
            f = json.loads(row["fields_json"] or "{}")
        except ValueError:
            f = {}
        return {"version": row["version"], "updated_by": row["updated_by"], "at": row["created_at"], "fields": f, "confirmed": True}
    return {"version": 0, "updated_by": "system", "at": None, "fields": inferred(lead), "confirmed": False}


def view(lead) -> dict:
    """สำหรับเทมเพลต: รายการช่อง (label, value, source) + คำถาม + ค่าในฟอร์ม"""
    c = current(lead)
    f = c["fields"]
    rows = [{"key": k, "label": lb, "value": (f.get(k) or {}).get("value"), "source": (f.get(k) or {}).get("source", "unknown"),
             "evidence": (f.get(k) or {}).get("evidence")} for k, lb in FIELDS]
    qs = f.get("questions") or []
    return {**c, "rows": rows, "questions": qs, "n_confirmed": sum(1 for r in rows if r["source"] == "confirmed"),
            "form": {**{r["key"]: r["value"] or "" for r in rows}, "questions": "\n".join(q["text"] for q in qs)}}


def validate(form: dict, lead) -> tuple[dict, dict]:
    v = {k: re.sub(r"\s+", " ", (form.get(k) or "")).strip() for k, _ in FIELDS}
    err = {}
    for k, n in LIMITS.items():
        if len(v[k]) > n:
            err[k] = f"ยาวเกิน {n} ตัวอักษร"
    if not v["primary_service"]:
        err["primary_service"] = "บอกบริการหลักอย่างน้อย 1 อย่าง เช่น ให้เช่าคลังสินค้า"
    if v["competitor"]:
        site = L.normalize_url(v["competitor"])
        if not site:
            err["competitor"] = "ใส่เป็นที่อยู่เว็บ เช่น competitor.co.th หรือเว้นว่างไว้"
        else:
            dom = L.domain_of(site)
            if lead["site_url"] and dom == L.domain_of(lead["site_url"]):
                err["competitor"] = "นี่คือเว็บของคุณเอง ใส่เว็บของคู่แข่ง หรือเว้นว่างไว้"
            v["competitor"] = dom
    lines = [re.sub(r"\s+", " ", x).strip() for x in (form.get("questions") or "").splitlines()]
    lines = [x for x in lines if x]
    if len(lines) > MAX_QUESTIONS:
        err["questions"] = f"ใส่ได้ไม่เกิน {MAX_QUESTIONS} คำถาม (บรรทัดละ 1 คำถาม)"
    elif any(len(x) > 150 for x in lines):
        err["questions"] = "คำถามแต่ละข้อยาวไม่เกิน 150 ตัวอักษร"
    v["questions"] = list(dict.fromkeys(lines))[:MAX_QUESTIONS]
    return v, err


def save(lead, v: dict, updated_by: str = "customer") -> int:
    """บันทึกเป็นเวอร์ชันใหม่ · ช่องที่กรอก = ยืนยันแล้ว · เว้นว่าง = ยังไม่ทราบ (ไม่เก็บค่าที่ระบบเดาไว้แทน)"""
    if db.count_business_contexts(lead["id"]) >= MAX_VERSIONS:
        raise ValueError(f"แก้ข้อมูลธุรกิจของรายงานนี้ครบ {MAX_VERSIONS} ครั้งแล้ว ติดต่อทีมเพื่อแก้ต่อ")
    suggested = {q["text"] for q in inferred(lead).get("questions") or []}
    fields = {k: ({"value": v[k], "source": "confirmed"} if v[k] else {"value": None, "source": "unknown"}) for k, _ in FIELDS}
    fields["questions"] = [{"text": q, "origin": "suggested" if q in suggested else "customer", "is_search_volume": False} for q in v["questions"]]
    ver = db.add_business_context(lead["id"], updated_by, json.dumps(fields, ensure_ascii=False))
    who = {"customer": "ลูกค้า", "staff": "ทีม"}.get(updated_by, updated_by)
    db.add_lead_event(lead["id"], "note", f"{who}ยืนยันข้อมูลธุรกิจ (เวอร์ชัน {ver}): บริการหลัก {v['primary_service']}"
                      + (f" · พื้นที่ {v['region']}" if v["region"] else "") + f" · คำถาม {len(v['questions'])} ข้อ", updated_by)
    return ver


def for_ai(lead) -> dict | None:
    """ให้ชั้นทดสอบ AI ใช้: คำถามและคู่แข่งที่ลูกค้ายืนยัน · ยังไม่เคยยืนยัน = None (ใช้วิธีเดิม)"""
    c = current(lead)
    if not c["confirmed"]:
        return None
    f = c["fields"]
    comp = (f.get("competitor") or {}).get("value")
    return {"version": c["version"], "questions": [q["text"] for q in f.get("questions") or []], "competitors": [comp] if comp else [],
            "primary_service": (f.get("primary_service") or {}).get("value")}


def ai_comparability(lead, t2: dict) -> str | None:
    """ผลทดสอบ AI ใช้บริบทเวอร์ชันไหน เทียบกับปัจจุบัน · คืนข้อความเตือน หรือ None"""
    if (t2 or {}).get("state") != "done":
        return None
    c = current(lead)
    used = t2.get("context_version") or 0
    if c["version"] and used != c["version"]:
        return ("ผลทดสอบ AI ด้านล่างใช้คำถามชุดที่ระบบเลือกเอง ก่อนคุณยืนยันข้อมูลธุรกิจ" if not used
                else f"ผลทดสอบ AI ใช้ข้อมูลธุรกิจเวอร์ชัน {used} ซึ่งคุณแก้ไขแล้ว (ปัจจุบันเวอร์ชัน {c['version']})") + \
               " จึงเทียบกับข้อมูลชุดใหม่ตรง ๆ ไม่ได้ การทดสอบรอบถัดไปจะใช้ข้อมูลล่าสุด"
    return None
