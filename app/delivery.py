"""ส่งมอบงานตามขอบเขต + พื้นที่งานลูกค้า (stage 15) — flag `delivery_v2` (ปิด = แบบเดิมทุกอย่าง)

แยก 3 เรื่องที่เคยปนกัน:
  ข้อเสนอ (offer)       = ร่างการขายใน setting `offer_drafts` เท่านั้น ไม่มีราคาที่ระบบคิดเอง ไม่ย้ายลูกค้าเดิมเข้าแพ็กเกจใหม่
  สิทธิ์ (entitlement)   = สิ่งที่แพ็กเกจเดิมของลูกค้าให้ (billing.PLANS) — อ่านอย่างเดียว ไม่แก้
  งบการใช้ (usage)      = ที่ใช้ไปเดือนนี้ (billing.usage)
ขอบเขต (scope) มีเวอร์ชัน: สิ่งที่ส่งมอบ / ไม่รวม / ข้อมูลที่ลูกค้าต้องให้ / ใครทำ / รอบแก้ / งานที่ต้องเสร็จก่อน / เกณฑ์รับงาน / สมมติฐานเวลา

ลำดับ: ยืนยันยอด → เปิดพื้นที่งาน → ลูกค้ายืนยันข้อมูล + ผู้อนุมัติ → สิทธิ์เข้าถึง → วัดผลตั้งต้น → แผนที่อนุมัติ → ลงมือ
การจ่ายเงินไม่ได้แปลว่าพร้อม และไม่ได้แปลว่าอนุญาตเผยแพร่ (publish_allowed ลูกค้าต้องเปิดเอง)
ลูกค้าเดิมที่ไม่มีพื้นที่งาน = สัญญาเดิมทุกอย่าง (Autopilot เดิม ไม่แตะ)"""
from __future__ import annotations
import hashlib
import json

from . import db, flags

STATES = {"awaiting_customer": "รอข้อมูลจากคุณ", "awaiting_access": "รอสิทธิ์เข้าถึงเว็บ", "ready": "พร้อมวางแผน", "in_progress": "กำลังทำ",
          "review": "รอคุณตรวจ", "delivered": "ส่งมอบแล้ว", "accepted": "รับงานแล้ว", "blocked": "ติดปัญหา", "cancelled": "ยกเลิก"}
# ป้ายเดียวกันแต่พูดจากมุมทีม (หน้า /admin/delivery และการแจ้งเตือนภายใน) — STATES ข้างบนพูดกับลูกค้า
STAFF_STATES = {**STATES, "awaiting_customer": "รอข้อมูลจากลูกค้า", "review": "รอลูกค้าตรวจ"}
ITEM_STATES = {"planned": "ในแผน", "in_progress": "กำลังทำ", "review": "รอตรวจ", "delivered": "ส่งแล้ว", "accepted": "รับแล้ว",
               "blocked": "ติดปัญหา", "stale": "ข้อมูลเปลี่ยน ต้องร่างใหม่", "cancelled": "ยกเลิก"}
KINDS = {"investigate": "ตรวจหาสาเหตุ/วัดเพิ่ม", "fix_technical": "แก้ปัญหาทางเทคนิค", "confirm_facts": "ยืนยันข้อมูลธุรกิจ",
         "update_existing": "ปรับหน้าที่มีอยู่", "consolidate": "รวมหน้าที่ซ้ำกัน", "add_evidence": "เพิ่มหลักฐานความน่าเชื่อถือ",
         "create_page": "สร้างหน้าใหม่", "create_article": "เขียนบทความ", "internal_links": "เชื่อมลิงก์ภายใน", "wait": "รอวัดผลรอบถัดไป",
         "no_action": "ยังไม่ต้องทำอะไร"}
OWNERS = {"us": "ทีมเจอ.AI", "customer_team": "ทีมเว็บของคุณ", "customer": "คุณ"}
ACCESS = {"connector": "ติดตั้งตัวเชื่อม /geo", "wordpress": "เชื่อม WordPress", "existing_team": "ทีมเว็บเดิมของคุณเป็นคนแก้/เผยแพร่"}
OPEN_ITEM = ("planned", "in_progress", "review", "blocked")
DONE_ITEM = ("delivered", "accepted")
MAX_TECH = 5


def on() -> bool:
    try:
        return flags.enabled("delivery_v2")
    except Exception:
        return False


def _j(s, d=None):
    try:
        return json.loads(s) if s else (d if d is not None else {})
    except ValueError:
        return d if d is not None else {}


def _h(v) -> str:
    return hashlib.sha256(json.dumps(v, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


# ---------- ข้อเสนอ / สิทธิ์ / การใช้ ----------
def offer_drafts() -> list[dict]:
    """ร่างข้อเสนอใหม่ (setting offer_drafts) — สถานะร่างเสมอ · ไม่มีราคา = แสดงว่า "ยังไม่ตั้งราคา" ไม่เติมเอง"""
    out = []
    for o in _j(db.get_setting("offer_drafts"), []) if isinstance(_j(db.get_setting("offer_drafts"), []), list) else []:
        if isinstance(o, dict) and o.get("key"):
            out.append({"key": str(o["key"])[:40], "label": str(o.get("label") or o["key"])[:80], "status": "draft",
                        "price_thb": o.get("price_thb") if isinstance(o.get("price_thb"), (int, float)) else None,
                        "deliverables": [str(x)[:120] for x in (o.get("deliverables") or [])][:12]})
    return out


def entitlements(tenant) -> dict:
    """สิทธิ์ตามแพ็กเกจเดิมของลูกค้า (ไม่แก้ ไม่ย้าย) · auto_publish ในแพ็กเกจ = ทำได้ทางเทคนิค ไม่ใช่การอนุญาตเผยแพร่"""
    from . import billing
    p = billing.plan_of(tenant)
    return {"plan": billing.plan_key(tenant), "label": p["label"], "brands": p["brands"], "runs_month": p["runs_month"],
            "content_month": p["content_month"], "images": p["images"], "auto_publish_feature": p["auto_publish"]}


def usage(tenant_id: int) -> dict:
    from . import billing
    return billing.usage(tenant_id)


# ---------- ขอบเขต (มีเวอร์ชัน) ----------
def scope_template(ent: dict) -> dict:
    n = ent.get("content_month") or 0
    return {
        "deliverables": ["ตรวจเว็บและรายการแก้ทางเทคนิคพร้อมหลักฐาน", "ยืนยันข้อมูลธุรกิจที่ใช้วางแผน", "วัดผลตั้งต้นก่อนเริ่มงาน",
                         "แผนงานที่คุณอนุมัติ", f"ร่าง/ปรับหน้าตามแผน ไม่เกิน {n} ชิ้นต่อเดือนตามแพ็กเกจเดิม", "รายงานผลรายเดือน (แยกตัวเลขที่วัดได้/ยังไม่ได้วัด)"],
        "exclusions": ["ไม่รับประกันอันดับหรือการถูก AI แนะนำ", "ไม่เผยแพร่บนเว็บของคุณโดยไม่ได้รับอนุญาต", "ไม่รวมออกแบบเว็บใหม่ โฮสติ้ง หรือค่าโฆษณา"],
        "customer_inputs": ["ยืนยันข้อมูลธุรกิจ (บริการหลัก พื้นที่ ลูกค้าเป้าหมาย)", "ผู้อนุมัติงาน 1 คน", "สิทธิ์เข้าถึงเว็บ หรือผู้ติดต่อทีมเว็บเดิม",
                            "ตรวจร่างภายใน 5 วันทำการ"],
        "owners": {"plan": "us", "technical_fix": "ตามสิทธิ์เข้าถึง", "publish": "ผู้ที่ได้รับอนุญาตเผยแพร่", "approval": "customer"},
        "review_rounds": 2,
        "dependencies": ["ข้อมูลธุรกิจยืนยันแล้ว", "สิทธิ์เข้าถึงหรือทีมเว็บรับงาน", "วัดผลตั้งต้นแล้ว"],
        "acceptance": "ลูกค้ากดรับงานแต่ละชิ้น หรือไม่มีความเห็นภายใน 5 วันทำการหลังส่ง (ทีมต้องบันทึกการรับงานเอง ระบบไม่ถือว่ารับอัตโนมัติ)",
        "timing": "เริ่มนับเวลาเมื่อได้ข้อมูล สิทธิ์ และวัดผลตั้งต้นครบ · งานรอลูกค้าไม่นับเป็นความล่าช้าของทีม",
    }


def _scopes(wid: int) -> list:
    with db.get_conn() as c:
        return [dict(r) for r in c.execute(db.q("SELECT * FROM delivery_scopes WHERE workspace_id=? ORDER BY version DESC"), (wid,)).fetchall()]


def scope(wid: int) -> dict | None:
    rows = _scopes(wid)
    acc = next((r for r in rows if r["status"] == "accepted"), None)
    r = acc or (rows[0] if rows else None)
    return {**r, "scope": _j(r["scope_json"])} if r else None


def draft_scope(wid: int, body: dict) -> int:
    with db.get_conn() as c:
        ver = (c.execute(db.q("SELECT COALESCE(MAX(version),0) AS v FROM delivery_scopes WHERE workspace_id=?"), (wid,)).fetchone()["v"] or 0) + 1
        c.execute(db.q("INSERT INTO delivery_scopes(workspace_id, version, status, scope_json, created_at) VALUES(?,?,?,?,?)"),
                  (wid, ver, "draft", json.dumps(body, ensure_ascii=False), db.now()))
    return ver


def accept_scope(wid: int, version: int, by: str) -> bool:
    with db.get_conn() as c:
        cur = c.execute(db.q("UPDATE delivery_scopes SET status='accepted', accepted_at=?, accepted_by=? WHERE workspace_id=? AND version=? AND status='draft'"),
                        (db.now(), by[:120], wid, version))
        if (cur.rowcount or 0) != 1:
            return False
        c.execute(db.q("UPDATE delivery_scopes SET status='superseded' WHERE workspace_id=? AND status='accepted' AND version<>?"), (wid, version))
    return True


# ---------- พื้นที่งาน ----------
def get(wid: int) -> dict | None:
    with db.get_conn() as c:
        r = c.execute(db.q("SELECT * FROM delivery_workspaces WHERE id=?"), (wid,)).fetchone()
    return dict(r) if r else None


def for_brand(bid: int) -> dict | None:
    with db.get_conn() as c:
        r = c.execute(db.q("SELECT * FROM delivery_workspaces WHERE brand_id=?"), (bid,)).fetchone()
    return dict(r) if r else None


def list_all(tenant_id: int | None = None) -> list[dict]:
    sql, a = "SELECT * FROM delivery_workspaces", ()
    if tenant_id is not None:
        sql, a = sql + " WHERE tenant_id=?", (tenant_id,)
    with db.get_conn() as c:
        return [dict(r) for r in c.execute(db.q(sql + " ORDER BY id DESC"), a).fetchall()]


def _upd(wid: int, **f) -> None:
    f["updated_at"] = db.now()
    with db.get_conn() as c:
        c.execute(db.q(f"UPDATE delivery_workspaces SET {', '.join(k + '=?' for k in f)} WHERE id=?"), tuple(f.values()) + (wid,))


def open_for_quote(q) -> int | None:
    """ยืนยันยอดแล้ว → เปิดพื้นที่งาน (ครั้งเดียวต่อแบรนด์) · สถานะเริ่ม = รอข้อมูลจากลูกค้า ไม่ใช่ "พร้อม" """
    q = db.get_quote(q["id"])
    if not q or q["status"] != "paid" or not q["brand_id"]:
        return None
    ex = for_brand(q["brand_id"])
    if ex:
        return ex["id"]
    now = db.now()
    with db.get_conn() as c:
        cur = c.execute(db.q("INSERT INTO delivery_workspaces(brand_id, tenant_id, lead_id, quote_id, state, publish_allowed, created_at, updated_at) "
                             "VALUES(?,?,?,?,?,0,?,?) ON CONFLICT DO NOTHING RETURNING id"),
                        (q["brand_id"], q["tenant_id"], q["lead_id"], q["id"], "awaiting_customer", now, now))
        row = cur.fetchone()
    if not row:
        return for_brand(q["brand_id"])["id"]
    wid = row["id"]
    tenant = db.get_tenant(q["tenant_id"]) if q["tenant_id"] else None
    draft_scope(wid, scope_template(entitlements(tenant)))
    db.add_lead_event(q["lead_id"], "note", "เปิดพื้นที่งานส่งมอบ — รอลูกค้ายืนยันข้อมูลและสิทธิ์เข้าถึง (ยังไม่เริ่มงานอัตโนมัติ)", "delivery")
    return wid


def _lead(ws):
    return db.get_lead(ws["lead_id"]) if ws.get("lead_id") else None


def facts(ws: dict) -> dict:
    from . import business_context as BC
    ld = _lead(ws)
    cur = BC.current(ld) if ld else {"version": 0, "fields": {}, "confirmed": False}
    vals = {k: (v.get("value") if isinstance(v, dict) else v) for k, v in (cur.get("fields") or {}).items() if k != "questions"}
    return {"version": cur["version"], "confirmed": bool(cur.get("confirmed")), "fields": vals, "hash": _h(vals)}


def confirm_facts(wid: int, approver: str) -> tuple[bool, str]:
    ws = get(wid)
    f = facts(ws)
    if not f["confirmed"] or not f["fields"].get("primary_service"):
        return False, "ต้องยืนยันบริการหลักก่อน"
    if not (approver or "").strip():
        return False, "ต้องระบุผู้อนุมัติงาน"
    _upd(wid, facts_version=f["version"], facts_hash=f["hash"], approver=approver.strip()[:120])
    _invalidate_if_changed(wid, f["hash"])
    refresh(wid)
    return True, ""


def _invalidate_if_changed(wid: int, new_hash: str) -> int:
    """ข้อมูลธุรกิจเปลี่ยน → ร่าง/งานที่ยังไม่ส่งมอบซึ่งอิงข้อมูลเดิมใช้ไม่ได้ · แผนต้องอนุมัติใหม่"""
    with db.get_conn() as c:
        cur = c.execute(db.q("UPDATE delivery_items SET status='stale', note='ข้อมูลธุรกิจเปลี่ยนหลังร่าง', updated_at=? "
                             "WHERE workspace_id=? AND status IN ('planned','in_progress','review','blocked') AND facts_hash IS NOT NULL AND facts_hash<>?"),
                        (db.now(), wid, new_hash))
        n = cur.rowcount or 0
    if n:
        _upd(wid, plan_approved_at=None, plan_approved_by=None)
    return n


def sync_facts(wid: int) -> int:
    """เรียกก่อนแสดง/ลงมือ: ลูกค้าแก้ข้อมูลธุรกิจที่อื่น (เวอร์ชันใหม่) → ต้องยืนยันใหม่ งานที่อิงข้อมูลเดิม = ต้องร่างใหม่"""
    ws = get(wid)
    f = facts(ws)
    if ws.get("facts_version") and f["version"] != ws["facts_version"]:
        _upd(wid, facts_version=None, facts_hash=None)
        n = _invalidate_if_changed(wid, f["hash"])
        refresh(wid)
        return n
    return 0


def set_access(wid: int, mode: str, team_contact: str = "", publish_allowed: bool = False) -> tuple[bool, str]:
    from . import success
    ws = get(wid)
    if mode not in ACCESS:
        return False, "ไม่รู้จักวิธีเข้าถึงนี้"
    brand = db.get_brand(ws["brand_id"])
    if mode == "existing_team":
        if not team_contact.strip():
            return False, "ระบุผู้ติดต่อทีมเว็บเดิม"
        ok, note = True, f"ทีมเว็บเดิมรับงาน: {team_contact.strip()[:120]}"
    elif mode == "wordpress":
        ok, note = bool(db.get_wp_connection(ws["brand_id"])), "เชื่อม WordPress แล้ว"
    else:
        ok, note = success.installed(brand)
    if not ok:
        _upd(wid, access_json=json.dumps({"mode": mode, "verified": False, "note": note or "ยังตรวจไม่ผ่าน"}, ensure_ascii=False))
        refresh(wid)
        return False, f"ยังตรวจสิทธิ์เข้าถึงไม่ผ่าน ({note or 'ยังไม่มีผลตรวจ'})"
    _upd(wid, access_json=json.dumps({"mode": mode, "verified": True, "note": note, "team_contact": team_contact.strip()[:120]}, ensure_ascii=False),
         publish_allowed=1 if (publish_allowed and mode != "existing_team") else 0)
    refresh(wid)
    return True, ""


def access(ws: dict) -> dict:
    return _j(ws.get("access_json"))


# ---------- วัดผลตั้งต้น (เฉพาะที่วัดจริง) ----------
def take_baseline(wid: int) -> dict:
    ws = get(wid)
    bid = ws["brand_id"]
    lanes = {}
    av = db.last_ai_visibility(bid)
    if av and (av["asked"] or 0) > 0:
        hits = (av["cited"] or 0) + (av["named"] or 0)
        from . import retention
        lanes["ai_mention"] = {"state": "found" if hits else "absent", "value": hits, "of": av["asked"], "at": str(av["checked_at"])[:16],
                               "qset": retention.qset(av)}                      # stage 16: เทียบได้เฉพาะชุดคำถามเดียวกัน
    else:
        lanes["ai_mention"] = {"state": "not_tested", "value": None, "at": None}
    hc = db.last_health_check(bid)
    if hc:
        lanes["site_health"] = {"state": "found" if hc["ok"] else "partial", "value": hc["n_fail"], "label": "ข้อที่ไม่ผ่าน", "at": str(hc["checked_at"])[:16]}
    else:
        lanes["site_health"] = {"state": "not_tested", "value": None, "at": None}
    gsc = db.last_gsc(bid) if hasattr(db, "last_gsc") else None
    lanes["search_console"] = ({"state": "found", "value": None, "at": str(dict(gsc).get("synced_at") or "")[:16]} if gsc
                               else {"state": "not_tested", "value": None, "at": None})
    base = {"at": db.now(), "lanes": lanes}
    _upd(wid, baseline_json=json.dumps(base, ensure_ascii=False), baseline_at=base["at"])
    refresh(wid)
    return base


# ---------- วางแผน (ใช้ผลตรวจเดิม ไม่เขียนบทความต่อคำถามที่ AI ไม่เอ่ยถึง) ----------
def _items(wid: int) -> list[dict]:
    with db.get_conn() as c:
        return [dict(r) for r in c.execute(db.q("SELECT * FROM delivery_items WHERE workspace_id=? ORDER BY id"), (wid,)).fetchall()]


def _add_item(c, wid, kind, title, *, target="", evidence=None, owner="us", acceptance="", depends_on=None, fh=None, ver=None) -> int:
    return c.execute(db.q("INSERT INTO delivery_items(workspace_id, kind, title, target, evidence_json, owner, acceptance, status, depends_on, facts_hash, "
                          "scope_version, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id"),
                     (wid, kind, title[:200], target[:500], json.dumps(evidence or [], ensure_ascii=False), owner, acceptance[:300], "planned",
                      depends_on, fh, ver, db.now(), db.now())).fetchone()["id"]


def plan(wid: int) -> tuple[bool, str]:
    """ร่างแผนจากผลตรวจจริง · แทนที่ร่างเดิมที่ยังไม่เริ่ม · ต้องมีข้อมูลยืนยัน + สิทธิ์ + วัดผลตั้งต้นก่อน"""
    sync_facts(wid)
    ws = get(wid)
    g = gates(ws)
    missing = [k for k in ("facts", "access", "baseline") if not g[k]]
    if missing:
        return False, "ยังวางแผนไม่ได้ — ขาด: " + ", ".join(GATE_LABEL[k] for k in missing)
    ld = _lead(ws)
    f = facts(ws)
    acc = access(ws)
    team = acc.get("mode") == "existing_team"
    tech_owner = "customer_team" if team else "us"
    sc = scope(wid) or {}
    ver = sc.get("version")
    deep = _j(ld["deep_json"]) if ld and ld["deep_json"] else {}
    base = _j(ws["baseline_json"])
    svc, region = f["fields"].get("primary_service") or "", f["fields"].get("region") or ""
    with db.get_conn() as c:
        c.execute(db.q("UPDATE delivery_items SET status='cancelled', note='แทนด้วยแผนใหม่', updated_at=? WHERE workspace_id=? AND status IN ('planned','stale')"),
                  (db.now(), wid))
        n = 0
        issues = [i for i in deep.get("issues") or [] if i.get("severity") in ("Error", "Warning")]
        dup = [i for i in issues if "duplicate" in (i.get("key") or "")]
        for i in [x for x in issues if x not in dup][:MAX_TECH]:
            _add_item(c, wid, "fix_technical", i.get("label") or i.get("key"), target=(i.get("urls") or [""])[0], evidence=(i.get("urls") or [])[:5],
                      owner=tech_owner, acceptance=i.get("fix") or "ตรวจซ้ำแล้วไม่พบปัญหานี้", fh=f["hash"], ver=ver)
            n += 1
        if dup:
            _add_item(c, wid, "consolidate", "รวมหน้าที่เนื้อหา/ชื่อซ้ำกัน", evidence=sum(((d.get("urls") or [])[:3] for d in dup), [])[:6],
                      owner=tech_owner, acceptance="เหลือหน้าหลักหน้าเดียวต่อหัวข้อ หน้าอื่นชี้มาที่หน้าหลัก", fh=f["hash"], ver=ver)
            n += 1
        ai = (base.get("lanes") or {}).get("ai_mention", {})
        if ai.get("state") == "not_tested":
            _add_item(c, wid, "investigate", "วัดการถูก AI แนะนำครั้งแรกด้วยคำถามที่ยืนยันแล้ว", evidence=[], owner="us",
                      acceptance="มีผลวัดจริงพร้อมแหล่งอ้างอิง (ยังไม่ทดสอบ ≠ 0)", fh=f["hash"], ver=ver)
            n += 1
        elif ai.get("state") == "absent":
            # คำถามที่ AI ไม่เอ่ยถึงทั้งหมด → งานเดียวที่หน้าบริการหลัก ไม่ใช่บทความต่อคำถาม
            page = next((p for p in deep.get("pages") or [] if svc and svc.split()[0] in (p.get("title") or "")), None)
            if page:
                pid = _add_item(c, wid, "update_existing", f"ปรับหน้า \"{(page.get('title') or '')[:60]}\" ให้ตอบเรื่อง {svc} {region}".strip(),
                                target=page.get("url") or "", evidence=[f"AI ไม่เอ่ยถึง {ai.get('of')} คำถาม (วัด {ai.get('at')})"], owner=tech_owner if team else "us",
                                acceptance="หน้ามีบริการ พื้นที่ และข้อเท็จจริงที่ยืนยันแล้ว · ลูกค้าอนุมัติข้อความ", fh=f["hash"], ver=ver)
            else:
                pid = _add_item(c, wid, "create_page", f"สร้างหน้าบริการ {svc} {region}".strip(), evidence=[f"AI ไม่เอ่ยถึง {ai.get('of')} คำถาม (วัด {ai.get('at')})", "ไม่พบหน้าที่ตรงบริการหลัก"],
                                owner="us", acceptance="ลูกค้าอนุมัติข้อความ · เผยแพร่โดยผู้ที่ได้รับอนุญาต", fh=f["hash"], ver=ver)
            _add_item(c, wid, "internal_links", "เชื่อมลิงก์จากหน้าแรก/เมนูไปหน้าบริการหลัก", owner=tech_owner, depends_on=pid,
                      acceptance="หน้าแรกมีลิงก์ไปหน้าบริการหลัก", fh=f["hash"], ver=ver)
            n += 2
        elif ai.get("state") == "found":
            _add_item(c, wid, "wait", "AI เอ่ยถึงแล้ว — รอวัดผลรอบถัดไปก่อนเพิ่มงาน", evidence=[f"เอ่ยถึง {ai.get('value')}/{ai.get('of')} ({ai.get('at')})"],
                      owner="us", acceptance="วัดซ้ำแล้วแนวโน้มไม่ลดลง", fh=f["hash"], ver=ver)
            n += 1
        if not n:
            _add_item(c, wid, "no_action", "ยังไม่มีงานที่คุ้มทำตอนนี้จากผลตรวจล่าสุด", owner="us", acceptance="—", fh=f["hash"], ver=ver)
    _upd(wid, plan_approved_at=None, plan_approved_by=None)
    refresh(wid)
    return True, ""


def approve_plan(wid: int, by: str) -> tuple[bool, str]:
    sync_facts(wid)
    ws = get(wid)
    if not [i for i in _items(wid) if i["status"] == "planned"]:
        return False, "ยังไม่มีแผนให้อนุมัติ"
    if not gates(ws)["facts"]:
        return False, "ข้อมูลธุรกิจเปลี่ยน ต้องยืนยันใหม่ก่อน"
    _upd(wid, plan_approved_at=db.now(), plan_approved_by=by[:120])
    refresh(wid)
    return True, ""


# ---------- งานแต่ละชิ้น ----------
def item(iid: int) -> dict | None:
    with db.get_conn() as c:
        r = c.execute(db.q("SELECT * FROM delivery_items WHERE id=?"), (iid,)).fetchone()
    return dict(r) if r else None


def _set_item(iid: int, frm: tuple, to: str, note: str = "") -> bool:
    with db.get_conn() as c:
        cur = c.execute(db.q(f"UPDATE delivery_items SET status=?, note=?, updated_at=? WHERE id=? AND status IN ({','.join('?' * len(frm))})"),
                        (to, note[:300], db.now(), iid) + tuple(frm))
        return (cur.rowcount or 0) == 1


def move(iid: int, action: str, note: str = "") -> tuple[bool, str]:
    it = item(iid)
    if not it:
        return False, "ไม่พบงาน"
    sync_facts(it["workspace_id"])
    it = item(iid)
    ws = get(it["workspace_id"])
    if ws["state"] == "cancelled":
        return False, "พื้นที่งานถูกยกเลิก"
    if action == "start":
        g = gates(ws)
        if not g["plan"]:
            return False, "แผนยังไม่ได้รับอนุมัติ"
        if it["status"] == "stale":
            return False, "ข้อมูลธุรกิจเปลี่ยน ต้องร่างใหม่"
        if it["depends_on"]:
            dep = item(it["depends_on"])
            if not dep or dep["status"] not in DONE_ITEM:
                _set_item(iid, ("planned",), "blocked", f"รองาน #{it['depends_on']} ส่งมอบก่อน")
                refresh(ws["id"])
                return False, f"ติดงานที่ต้องเสร็จก่อน (#{it['depends_on']})"
        ok = _set_item(iid, ("planned", "blocked"), "in_progress", note)
    elif action == "review":
        ok = _set_item(iid, ("in_progress",), "review", note)
    elif action == "deliver":
        ok = _set_item(iid, ("in_progress", "review"), "delivered", note)
    elif action == "accept":
        ok = _set_item(iid, ("delivered", "review"), "accepted", note)
    elif action == "block":
        ok = _set_item(iid, OPEN_ITEM, "blocked", note or "ติดปัญหา")
    elif action == "cancel":
        ok = _set_item(iid, OPEN_ITEM + ("stale",), "cancelled", note or "ยกเลิก")
    else:
        return False, "ไม่รู้จักคำสั่ง"
    refresh(ws["id"])
    return (True, "") if ok else (False, f"เปลี่ยนจากสถานะ \"{ITEM_STATES.get(it['status'], it['status'])}\" ไม่ได้")


# ---------- สถานะรวม ----------
GATE_LABEL = {"paid": "ยืนยันยอดแล้ว", "facts": "ข้อมูลธุรกิจ + ผู้อนุมัติ", "access": "สิทธิ์เข้าถึงเว็บ", "baseline": "วัดผลตั้งต้น", "plan": "แผนที่อนุมัติ"}


def gates(ws: dict) -> dict:
    q = db.get_quote(ws["quote_id"]) if ws.get("quote_id") else None
    return {"paid": bool(q and q["status"] == "paid"), "facts": bool(ws.get("facts_version") and ws.get("approver")),
            "access": bool(access(ws).get("verified")), "baseline": bool(ws.get("baseline_at")), "plan": bool(ws.get("plan_approved_at"))}


def compute_state(ws: dict, items: list[dict]) -> str:
    if ws.get("cancelled_at"):
        return "cancelled"
    if ws.get("blocked_reason"):
        return "blocked"
    g = gates(ws)
    if not g["facts"]:
        return "awaiting_customer"
    if not g["access"]:
        return "awaiting_access"
    if not (g["baseline"] and g["plan"]):
        return "ready"
    live = [i for i in items if i["status"] not in ("cancelled", "stale")]
    if not live:
        return "ready"
    if any(i["status"] == "review" for i in live):
        return "review"
    if all(i["status"] == "accepted" for i in live):
        return "accepted"
    if all(i["status"] in DONE_ITEM for i in live):
        return "delivered"
    return "in_progress"


def refresh(wid: int) -> str:
    ws = get(wid)
    st = compute_state(ws, _items(wid))
    if st != ws["state"]:
        _upd(wid, state=st)
    return st


def set_blocked(wid: int, reason: str) -> None:
    _upd(wid, blocked_reason=(reason or "").strip()[:300] or None)
    refresh(wid)


def cancel(wid: int, reason: str) -> None:
    _upd(wid, cancelled_at=db.now(), blocked_reason=(reason or "ยกเลิก")[:300])
    with db.get_conn() as c:
        c.execute(db.q("UPDATE delivery_items SET status='cancelled', updated_at=? WHERE workspace_id=? AND status IN ('planned','in_progress','review','blocked','stale')"),
                  (db.now(), wid))
    refresh(wid)


# ---------- Autopilot เดิม: ห้ามวิ่งเองก่อนพร้อม ----------
def autopilot_gate(bid: int) -> tuple[bool, str | None, str]:
    """(วิ่งได้ไหม, โหมดที่บังคับ, เหตุผล) · ไม่มีพื้นที่งาน = ลูกค้าเดิม → ตามเดิมทุกอย่าง"""
    if not on():
        return True, None, ""
    ws = for_brand(bid)
    if not ws:
        return True, None, ""
    st = refresh(ws["id"])
    if st not in ("in_progress", "review", "delivered", "accepted"):
        return False, None, f"พื้นที่งานยัง{STATES[st]} — ยังไม่เริ่มงานอัตโนมัติ"
    return True, ("publish" if ws["publish_allowed"] else "draft"), ""


# ---------- หน้าลูกค้า ----------
def _waiting_on_customer(ws, items, g) -> list[str]:
    out = []
    if not g["facts"]:
        out.append("ยืนยันข้อมูลธุรกิจและระบุผู้อนุมัติงาน")
    if not g["access"]:
        out.append("ให้สิทธิ์เข้าถึงเว็บ หรือระบุผู้ติดต่อทีมเว็บเดิม")
    if g["facts"] and g["access"] and g["baseline"] and not g["plan"] and any(i["status"] == "planned" for i in items):
        out.append("ตรวจและอนุมัติแผนงาน")
    out += [f"ตรวจงาน: {i['title']}" for i in items if i["status"] in ("review", "delivered")]
    out += [f"ทีมเว็บของคุณ: {i['title']}" for i in items if i["owner"] in ("customer_team", "customer") and i["status"] in ("planned", "in_progress")]
    return out


def _staff_wording(lines: list[str]) -> list[str]:
    return [s.replace("ทีมเว็บของคุณ", "ทีมเว็บของลูกค้า") for s in lines]


def view(wid: int) -> dict:
    sync_facts(wid)
    ws = get(wid)
    items = _items(wid)
    g = gates(ws)
    nxt = next((GATE_LABEL[k] for k in ("facts", "access", "baseline", "plan") if not g[k]), None)
    if not nxt:
        op = next((i for i in items if i["status"] in ("in_progress", "review", "planned", "blocked")), None)
        nxt = f"{KINDS.get(op['kind'], op['kind'])}: {op['title']}" if op else ("รับงานที่ส่งแล้ว" if ws["state"] == "delivered" else "วัดผลรอบถัดไป")
    live = [{**i, "evidence": _j(i["evidence_json"], [])} for i in items if i["status"] not in ("cancelled",)]
    tenant = db.get_tenant(ws["tenant_id"]) if ws.get("tenant_id") else None
    return {"ws": ws, "brand": db.get_brand(ws["brand_id"]), "state_label": STATES.get(ws["state"], ws["state"]),
            "staff_label": STAFF_STATES.get(ws["state"], ws["state"]), "gates": g, "scope": scope(wid),
            "facts": facts(ws), "access": access(ws), "baseline": _j(ws.get("baseline_json")),
            "done": [i for i in live if i["status"] in DONE_ITEM], "ongoing": [i for i in live if i["status"] in OPEN_ITEM or i["status"] == "stale"],
            "waiting": (w := _waiting_on_customer(ws, live, g)), "waiting_staff": _staff_wording(w), "next": nxt, "work": live, "ent": entitlements(tenant) if tenant else None,
            "usage": usage(ws["tenant_id"]) if ws.get("tenant_id") else None}


def partner_rows(partner_id: int) -> list[dict]:
    """พันธมิตรเห็นเฉพาะลูกค้าที่ตัวเองแนะนำ: ชื่อเว็บ สถานะ และขั้นถัดไป — ไม่เห็นข้อมูลธุรกิจ สิทธิ์เข้าถึง หรือผลวัด"""
    ids = {l["id"]: l["domain"] for l in db.leads_of_partner(partner_id)}
    out = []
    for ws in list_all():
        if ws.get("lead_id") in ids:
            g = gates(ws)
            nxt = next((GATE_LABEL[k] for k in ("facts", "access", "baseline", "plan") if not g[k]), "กำลังดำเนินงานตามแผน")
            out.append({"domain": ids[ws["lead_id"]], "state": STAFF_STATES.get(ws["state"], ws["state"]), "next": nxt})
    return out
