"""Today + Customer 360 + รายงานทดลองจับคู่ตัวตน (stage 09, flag customer360) — อ่านจากของเดิมทั้งหมด

แยกให้ชัด (ยังไม่มีตาราง account กลาง):
- **ลีด** (leads) = บริษัทที่ตรวจเว็บ/ทีมหาเอง · **ผู้ติดต่อ** (lead_contacts, plan_requests) = คนในบริษัท
- **บัญชีลูกค้า** (tenants) = บัญชีล็อกอิน · **แบรนด์** (brands) = เว็บที่บัญชีดูแล · **ดีล** = สถานะ/มูลค่าบนลีด + ใบเสนอราคา
- **พันธมิตร** (partners) = คนแนะนำ ไม่ใช่ลูกค้า
- ตอนนี้ทั้งหมดผูกกันด้วย **โดเมน** (เดาจากข้อมูล ไม่ใช่การยืนยัน) → `account_key` = โดเมนที่ทำให้เป็นรูปเดียวกันแล้ว
- **ไม่รวม/ไม่แก้ข้อมูลอัตโนมัติ** · `identity_report()` เป็นรายงานทดลอง (dry-run) ให้เจ้าของดูก่อนตัดสินใจสร้างตาราง account

การมองเห็น (ซ้อนบนสิทธิ์รายหมวดเดิมของ app/staff.py):
- บทบาท "ฝ่ายขาย" เห็นเฉพาะลีดที่ยังไม่มอบหมาย หรือมอบหมายให้ตัวเอง (`leads.owner_staff_id`) · บทบาทอื่นที่เห็นหมวดขายเห็นทั้งหมด
- ช่องทางติดต่อ/รายงาน/ดีล ต้องเห็นหมวดขาย · การเงินต้องเห็นหมวดบัญชี · บัญชีลูกค้าต้องเห็นหมวดลูกค้าหรือบัญชีผู้ใช้ · ข้อมูลพันธมิตรเกินชื่อต้องเห็นหมวดพันธมิตร
- ไม่แสดงรหัสผ่าน/โทเค็น/LINE user id ให้ใครเลย"""
from __future__ import annotations

import datetime as dt
import json

from . import db, flags, staff, leads as L

SCOPED_ROLES = ("sales",)
DAYS_RECENT = 14


def enabled() -> bool:
    try:
        return flags.enabled("customer360")
    except Exception:
        return False


def account_key(domain_or_url: str | None) -> str:
    from .geo_worker import norm_domain
    return norm_domain(L.domain_of(domain_or_url or "") if "://" in (domain_or_url or "") else (domain_or_url or ""))


# ---------- สิทธิ์ ----------
def can(t, area: str, level: str = "view") -> bool:
    return staff.can(t, area, level)


def scoped(t) -> bool:
    return staff.role_key(t) in SCOPED_ROLES


def lead_visible(t, lead) -> bool:
    """เห็นลีดนี้ได้ไหม: ต้องเห็นหมวดขาย · ฝ่ายขายเห็นเฉพาะที่ยังไม่มอบหมายหรือของตัวเอง"""
    if not can(t, "sales"):
        return False
    owner = lead["owner_staff_id"] if "owner_staff_id" in lead.keys() else None
    return not scoped(t) or owner in (None, t["id"])


def staff_names() -> dict:
    return {r["id"]: (r["name"] or r["email"]) for r in db.list_staff()}


def owner_label(lead, names: dict | None = None) -> str:
    oid = lead["owner_staff_id"] if "owner_staff_id" in lead.keys() else None
    if not oid:
        return "ยังไม่มอบหมาย (ฝ่ายขาย)"
    return (names or staff_names()).get(oid, f"ทีมงาน #{oid}")


def assign(lead_id: int, new_owner: int | None, expected: int | None, by: str) -> bool:
    """มอบหมายลีดแบบ compare-and-set: ต้องตรงกับเจ้าของที่ผู้กดเห็นอยู่ (มีคนเปลี่ยนก่อน = ไม่ทับ)"""
    with db.get_conn() as c:
        cur = c.execute(db.q("UPDATE leads SET owner_staff_id=? WHERE id=? AND COALESCE(owner_staff_id, 0)=?"),
                        (new_owner, lead_id, expected or 0))
        ok = (cur.rowcount or 0) == 1
    if ok:
        who = staff_names().get(new_owner, "—") if new_owner else "ไม่มีผู้รับผิดชอบ"
        db.add_lead_event(lead_id, "note", f"มอบหมายลีดให้ {who} (โดย {by})", "staff")
    return ok


# ---------- Today ----------
def _bizday_after(ts: str, days: int = 1) -> str:
    try:
        t = dt.datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return ts or ""
    n = 0
    while n < days:
        t += dt.timedelta(days=1)
        if t.weekday() < 5:
            n += 1
    return t.isoformat(timespec="seconds")


def _item(kind, reason, due, next_action, href, source, lead=None, names=None, area="sales") -> dict:
    return {"kind": kind, "reason": reason, "due": (due or "")[:16].replace("T", " "), "due_raw": due or "", "next": next_action,
            "href": href, "source": source, "owner": owner_label(lead, names) if lead is not None else AREA_OWNER.get(area, area),
            "domain": lead["domain"] if lead is not None else None, "area": area}


AREA_OWNER = {"agents": "ผู้ดูแลเอเจนต์ AI", "sales": "ฝ่ายขาย", "marketing": "ฝ่ายการตลาด"}
SECTIONS = [("requests", "คำขอแผนที่ยังไม่ได้ติดต่อ", "sales"), ("meetings", "นัดคุย", "sales"), ("overdue", "งานถัดไปที่ถึงกำหนด/เลยกำหนด", "sales"),
            ("context", "ข้อมูลธุรกิจที่ยังเป็นระบบเดา", "sales"), ("approvals", "รออนุมัติ", "agents"),
            ("blocked", "งานที่ติดขัด", "agents"), ("suppression", "คำขอหยุดติดต่อ (ตรวจว่าไม่มีอะไรค้างส่ง)", "sales")]


def today(t, now: dt.datetime | None = None) -> list[dict]:
    """รายการงานวันนี้ตามสิทธิ์ · ทุกข้อมี เหตุผล · ผู้รับผิดชอบ · กำหนด · สิ่งที่ควรทำ (ลิงก์) · ที่มา"""
    now = now or dt.datetime.now()
    nowi = now.isoformat(timespec="seconds")
    since = (now - dt.timedelta(days=DAYS_RECENT)).isoformat(timespec="seconds")
    names = staff_names()
    out = {k: [] for k, _, _ in SECTIONS}
    sales = can(t, "sales")
    if sales:
        with db.get_conn() as c:
            prs = c.execute(db.q("SELECT p.id AS pid, p.created_at AS p_at, p.contact_channel, l.* FROM plan_requests p JOIN leads l ON l.id=p.lead_id "
                                 "WHERE p.status='stored' AND p.created_at>=? ORDER BY p.created_at"), (since,)).fetchall()
            ctx_leads = {r["lead_id"] for r in c.execute(db.q("SELECT DISTINCT lead_id FROM business_contexts")).fetchall()}
            apps = c.execute(db.q("SELECT a.*, l.domain, l.owner_staff_id FROM appointments a JOIN leads l ON l.id=a.lead_id "
                                  "WHERE a.status IN ('pending_confirmation','confirmed','needs_review') ORDER BY a.slot_start")).fetchall()
            due = c.execute(db.q("SELECT * FROM leads WHERE next_action IS NOT NULL AND next_at IS NOT NULL AND next_at<=? AND status!='unsubscribed' "
                                 "ORDER BY next_at LIMIT 100"), ((now + dt.timedelta(hours=12)).isoformat(timespec="seconds"),)).fetchall()
            unsub = c.execute(db.q("SELECT * FROM leads WHERE unsub_at IS NOT NULL AND unsub_at>=? ORDER BY unsub_at DESC LIMIT 50"), (since,)).fetchall()
            withdrawn = c.execute(db.q("SELECT p.id AS pid, p.created_at AS p_at, l.* FROM plan_requests p JOIN leads l ON l.id=p.lead_id "
                                       "WHERE p.status='withdrawn' AND p.created_at>=?"), (since,)).fetchall()
        from . import plan_request as PR
        seen_ctx = set()
        for r in prs:
            if not lead_visible(t, r):
                continue
            contacted = r["last_contacted_at"] and r["last_contacted_at"] >= r["p_at"]
            if not contacted and r["status"] not in ("won", "lost", "unsubscribed"):
                out["requests"].append(_item("request", f"ขอแผนจากรายงาน · ให้ติดต่อทาง {PR.CHANNELS.get(r['contact_channel'], r['contact_channel'])}",
                                             _bizday_after(r["p_at"]), "เปิด Sales Brief แล้วติดต่อตามช่องทางที่ลูกค้าเลือก",
                                             f"/admin/leads/{r['id']}#brief", f"plan_requests:{r['pid']}", r, names))
            if r["id"] not in ctx_leads and r["id"] not in seen_ctx:
                seen_ctx.add(r["id"])
                out["context"].append(_item("context", "ลูกค้ายังไม่ได้ยืนยันบริการหลัก/คู่แข่ง (ยังเป็นที่ระบบเดา)", _bizday_after(r["p_at"]),
                                            "ถามบริการหลัก พื้นที่ และคู่แข่งตอนติดต่อ แล้วบันทึกในโน้ต", f"/admin/leads/{r['id']}#brief",
                                            f"plan_requests:{r['pid']}", r, names))
        from . import appointments as AP
        for a in apps:
            if not lead_visible(t, a):
                continue
            w = AP.label(a["slot_start"])
            if a["status"] == "pending_confirmation":
                out["meetings"].append(_item("meeting", f"ลูกค้าขอนัด {w} (รอยืนยัน · ยังไม่ใช่นัด)", a["updated_at"], "เช็กว่าทีมว่างแล้วกดยืนยัน หรือยกเลิกพร้อมแจ้งลูกค้า",
                                             f"/admin/leads/{a['lead_id']}#appointment", f"appointments:{a['id']}", a, names))
            elif a["status"] == "confirmed" and (a["slot_start"] or "") <= AP._iso(AP.now() + dt.timedelta(hours=36)):
                out["meetings"].append(_item("meeting", f"นัดคุย {w} (ยืนยันแล้ว)", a["slot_start"], "เตรียม Sales Brief ก่อนคุย",
                                             f"/admin/leads/{a['lead_id']}#appointment", f"appointments:{a['id']}", a, names))
            elif a["status"] == "needs_review":
                out["blocked"].append(_item("meeting", f"นัด {w} ต้องตรวจ", a["updated_at"], "เปิดนัดแล้วตัดสินใจ",
                                            f"/admin/leads/{a['lead_id']}#appointment", f"appointments:{a['id']}", a, names))
        for l in due:
            if not lead_visible(t, l):
                continue
            late = l["next_at"] < nowi
            out["overdue"].append(_item("next", ("เลยกำหนด: " if late else "ใกล้ถึงกำหนด: ") + (l["next_action"] or ""), l["next_at"],
                                        "ทำตามงานถัดไป แล้วตั้งงานถัดไปใหม่ในหน้าลีด", f"/admin/leads/{l['id']}", f"leads:{l['id']}", l, names))
    if sales or can(t, "marketing"):
        if not sales:
            unsub, withdrawn = [], []
            with db.get_conn() as c:
                unsub = c.execute(db.q("SELECT * FROM leads WHERE unsub_at IS NOT NULL AND unsub_at>=? ORDER BY unsub_at DESC LIMIT 50"), (since,)).fetchall()
        for l in unsub:
            if sales and not lead_visible(t, l):
                continue
            out["suppression"].append(_item("suppression", "ขอไม่รับการติดต่อ", l["unsub_at"], "ตรวจว่าไม่มีข้อความ/ข้อเสนอค้างส่งถึงรายนี้ (รออนุมัติ · คิวงาน · แคมเปญ)",
                                            f"/admin/leads/{l['id']}" if sales else "/admin/marketing", f"leads:{l['id']}", l if sales else None, names,
                                            "sales" if sales else "marketing"))
        for r in withdrawn:
            if not lead_visible(t, r):
                continue
            out["suppression"].append(_item("suppression", "ขอให้หยุดติดต่อเรื่องคำขอแผน (ผ่าน LINE)", r["p_at"],
                                            "ตรวจว่าไม่มีนัด/ข้อความค้าง และไม่โทรตาม", f"/admin/leads/{r['id']}#brief", f"plan_requests:{r['pid']}", r, names))
    if can(t, "agents"):
        for d in db.list_decisions("proposed", 50):
            out["approvals"].append(_item("approval", f"{d['agent']}: {d['title']}", d["created_at"], "ตรวจเนื้อหาแล้วอนุมัติหรือปฏิเสธ",
                                          "/admin/decisions", f"decisions:{d['id']}", area="agents"))
        try:
            from . import jobs
            for j in jobs.list_jobs("dead", 30):
                out["blocked"].append(_item("job", f"งาน {jobs.KIND_LABEL.get(j['kind'], j['kind'])} ล้ม: {(j['last_error'] or '')[:120]}", j["updated_at"],
                                            "ดูสาเหตุ แล้วสั่งใหม่หรือยกเลิก", "/admin/jobs?status=dead", f"jobs:{j['id']}", area="agents"))
        except Exception:
            pass
    res = []
    for k, title, area in SECTIONS:
        visible = can(t, area) or (k == "suppression" and can(t, "marketing"))
        if not visible:
            continue
        items = sorted(out[k], key=lambda x: x["due_raw"])
        res.append({"key": k, "title": title, "items": items, "overdue": sum(1 for i in items if i["due_raw"] and i["due_raw"] < nowi)})
    return res


# ---------- Customer 360 ----------
def leads_for(key: str):
    if not key:
        return []
    with db.get_conn() as c:
        rows = c.execute(db.q("SELECT * FROM leads WHERE domain=? OR domain=? ORDER BY id DESC"), (key, "www." + key)).fetchall()
    return [r for r in rows if account_key(r["domain"]) == key]


def brands_for(key: str):
    return [b for b in db.list_all_brands() if account_key(L.domain_of(b["domain"] or "") if "://" in (b["domain"] or "") else b["domain"]) == key]


def _mask(v: str | None) -> str:
    v = (v or "").strip()
    return (v[:2] + "•" * max(3, len(v) - 4) + v[-2:]) if len(v) > 4 else "•••"


def view(key: str, t) -> dict | None:
    """ภาพรวมของ 1 บัญชี (ตามโดเมน) · คืน None = ไม่มีอะไรที่ผู้ดูนี้เห็นได้ (กันการเดาโดเมนของลีดที่ไม่ใช่ของตัวเอง)"""
    from . import plan_request as PR, business_context as BC, appointments as AP, report_view as RV
    all_leads = leads_for(key)
    vis_leads = [l for l in all_leads if lead_visible(t, l)]
    brands = brands_for(key)
    see_cust = can(t, "customers") or can(t, "accounts")
    if not vis_leads and not (brands and see_cust):
        return None
    names = staff_names()
    out = {"key": key, "hidden_leads": len(all_leads) - len(vis_leads), "leads": [], "brands": [], "partner": None,
           "can": {"sales": can(t, "sales"), "sales_edit": can(t, "sales", "edit"), "finance": can(t, "finance"), "customers": see_cust,
                   "partners": can(t, "partners")}, "matched_by": "domain"}
    for b in brands if see_cust else []:
        tn = db.get_tenant(b["tenant_id"])
        out["brands"].append({"id": b["id"], "name": b["name"], "domain": b["domain"], "tenant_id": b["tenant_id"],
                              "plan": (tn["plan"] if tn else None), "tenant_name": (tn["name"] if tn else None),
                              "is_internal": bool(tn and tn["is_admin"])})
    for l in vis_leads:
        pr = PR.latest(l["id"])
        try:
            bc = BC.view(l)
        except Exception:
            bc = None
        people = []
        for p in db.list_lead_contacts(l["id"]):
            people.append({"name": p["name"], "role": p["role"], "source": "ทีมบันทึก",
                           "channels": [x for x in (p["phone"], p["email"], p["line_id"]) if x]})
        if pr:
            people.append({"name": pr["name"], "role": pr["role"], "source": "ลูกค้ากรอกในคำขอแผน (ยืนยันเอง)",
                           "channels": [f"{PR.CHANNELS.get(pr['contact_channel'], pr['contact_channel'])}: {pr['contact_value']}"]})
        consents = []
        if pr:
            try:
                consents = json.loads(pr["consents_json"] or "[]")
            except ValueError:
                consents = []
        mkt = next((x for x in reversed(consents) if x.get("purpose") == "marketing"), None)
        try:
            from . import line_plan
            lb = line_plan.lead_bot(l["id"])
        except Exception:
            lb = {}
        try:
            vm = RV.build(l, None, bc)
            evidence = {"status": vm["summary"]["status_label"], "lines": vm["summary"]["lines"], "uncertainty": vm["summary"]["uncertainty"],
                        "actions": [a["title"] for a in vm["actions"]], "tested": vm["tested"], "total": vm["total"]}
        except Exception:
            evidence = None
        quotes = [dict(q) for q in db.list_quotes(lead_id=l["id"], limit=10)] if can(t, "sales") else []
        docs = [dict(d) for d in db.list_acct_docs(lead_id=l["id"], limit=20)] if can(t, "finance") else []
        with db.get_conn() as c:
            versions = c.execute(db.q("SELECT version, created_at FROM report_versions WHERE lead_id=? ORDER BY version DESC LIMIT 5"), (l["id"],)).fetchall()
            jobs_ = c.execute(db.q("SELECT id, kind, status, updated_at FROM jobs WHERE lead_id=? ORDER BY id DESC LIMIT 10"), (l["id"],)).fetchall()
        partner = None
        if l["partner_id"]:
            p = db.get_partner(l["partner_id"])
            if p:
                partner = {"name": p["name"] or p["company"], "id": p["id"] if can(t, "partners") else None,
                           "status": p["status"] if can(t, "partners") else None}
                out["partner"] = out["partner"] or partner
        out["leads"].append({
            "lead": l, "owner": owner_label(l, names), "owner_id": l["owner_staff_id"], "status": L.STATUS_LABEL.get(l["status"], l["status"]),
            "people": [{**p, "channels": p["channels"] if can(t, "sales") else [_mask(x) for x in p["channels"]]} for p in people],
            "context": bc, "plan": pr, "evidence": evidence, "versions": [dict(v) for v in versions],
            "events": [dict(e) for e in db.list_lead_events(l["id"], 30)],
            "appointments": [a for a in [AP.latest_for_lead(l["id"])] if a], "jobs": [dict(j) for j in jobs_],
            "quotes": [{"number": q["number"], "package": q["package"], "status": q["status"], "net": q["net"] if can(t, "finance") or can(t, "sales") else None,
                        "token": q["token"]} for q in quotes],
            "docs": [{"kind": d.get("kind"), "number": d.get("number"), "status": d.get("status"), "total": d.get("total")} for d in docs],
            "prefs": {"unsub_at": l["unsub_at"], "consent": bool(l["consent"]), "marketing": (mkt or {}).get("granted"),
                      "marketing_at": (mkt or {}).get("at"), "line_linked": bool(l["line_user_id"]), "line_paused": (lb or {}).get("paused_until"),
                      "plan_status": pr["status"] if pr else None},
            "partner": partner})
    return out


# ---------- รายงานทดลองจับคู่ตัวตน (ไม่เขียนข้อมูล) ----------
def identity_report() -> dict:
    """จับกลุ่มตามโดเมนแล้วหาความขัดแย้ง · อ่านอย่างเดียว · ไม่สร้าง/รวม/แก้แถวใด"""
    with db.get_conn() as c:
        leads_ = c.execute(db.q("SELECT id, domain, email, line_user_id, status, partner_id, created_at FROM leads")).fetchall()
        contacts = c.execute(db.q("SELECT lead_id, phone, email, line_id FROM lead_contacts")).fetchall()
        partners = c.execute(db.q("SELECT id, domain, website, email, status FROM partners")).fetchall()
        prs = c.execute(db.q("SELECT lead_id, contact_channel, contact_value FROM plan_requests")).fetchall()
    brands = db.list_all_brands()
    groups: dict[str, dict] = {}

    def g(k):
        return groups.setdefault(k, {"key": k, "leads": [], "brands": [], "partners": [], "raw": set()})
    for l in leads_:
        k = account_key(l["domain"])
        if k:
            g(k)["leads"].append(l["id"]); g(k)["raw"].add(l["domain"])
    tenants_by_key: dict[str, set] = {}
    for b in brands:
        raw = b["domain"] or ""
        k = account_key(L.domain_of(raw) if "://" in raw else raw)
        if k:
            g(k)["brands"].append(b["id"]); g(k)["raw"].add(raw)
            tenants_by_key.setdefault(k, set()).add(b["tenant_id"])
    for p in partners:
        k = account_key(p["domain"] or L.domain_of(p["website"] or ""))
        if k:
            g(k)["partners"].append(p["id"])
    conflicts = []

    def add(kind, key, detail, ids):
        conflicts.append({"kind": kind, "label": CONFLICT_LABEL[kind], "key": key, "detail": detail, "ids": ids})
    for k, x in groups.items():
        if len(x["leads"]) > 1:
            add("dup_leads", k, f"{len(x['leads'])} ลีดใช้โดเมนเดียวกัน", [f"leads:{i}" for i in x["leads"]])
        if len(tenants_by_key.get(k, ())) > 1:
            add("multi_tenant", k, f"แบรนด์โดเมนนี้อยู่ใน {len(tenants_by_key[k])} บัญชีลูกค้า", [f"tenants:{i}" for i in sorted(tenants_by_key[k])])
        if x["partners"] and (x["leads"] or x["brands"]):
            add("partner_is_customer", k, "โดเมนของพันธมิตรตรงกับลีด/ลูกค้า (คนแนะนำอาจเป็นลูกค้าเองด้วย)", [f"partners:{i}" for i in x["partners"]])
        if len(x["raw"]) > 1:
            add("domain_variants", k, "โดเมนเขียนหลายแบบ: " + ", ".join(sorted(x["raw"]))[:200], [])

    def shared(field_rows, kind, norm):
        seen: dict[str, set] = {}
        for lead_id, val in field_rows:
            v = norm(val)
            if v:
                seen.setdefault(v, set()).add(lead_id)
        for v, ids in seen.items():
            keys = {account_key(next((l["domain"] for l in leads_ if l["id"] == i), "")) for i in ids}
            if len(keys) > 1:
                add(kind, ", ".join(sorted(k for k in keys if k))[:120], f"ช่องทางเดียวกันอยู่กับ {len(keys)} โดเมน", [f"leads:{i}" for i in sorted(ids)])
    em = lambda v: (v or "").strip().lower() if "@" in (v or "") else ""
    ph = lambda v: "".join(ch for ch in (v or "") if ch.isdigit())[-9:] if len("".join(ch for ch in (v or "") if ch.isdigit())) >= 9 else ""
    shared([(l["id"], l["email"]) for l in leads_] + [(x["lead_id"], x["email"]) for x in contacts]
           + [(p["lead_id"], p["contact_value"]) for p in prs if p["contact_channel"] == "email"], "shared_email", em)
    shared([(x["lead_id"], x["phone"]) for x in contacts] + [(p["lead_id"], p["contact_value"]) for p in prs if p["contact_channel"] == "phone"],
           "shared_phone", ph)
    shared([(l["id"], l["line_user_id"]) for l in leads_], "shared_line", lambda v: (v or "").strip())
    counts = {"groups": len(groups), "leads": len(leads_), "brands": len(brands), "partners": len(partners),
              "with_customer": sum(1 for x in groups.values() if x["leads"] and x["brands"])}
    by_kind = {}
    for cf in conflicts:
        by_kind[cf["kind"]] = by_kind.get(cf["kind"], 0) + 1
    return {"counts": counts, "conflicts": conflicts[:500], "by_kind": by_kind, "writes": 0}


CONFLICT_LABEL = {"dup_leads": "ลีดซ้ำโดเมนเดียวกัน", "multi_tenant": "โดเมนเดียวอยู่หลายบัญชีลูกค้า", "partner_is_customer": "พันธมิตรเป็นลีด/ลูกค้าด้วย",
                  "domain_variants": "โดเมนเขียนต่างกัน", "shared_email": "อีเมลเดียวกันหลายโดเมน", "shared_phone": "เบอร์เดียวกันหลายโดเมน",
                  "shared_line": "LINE เดียวกันหลายลีด"}
