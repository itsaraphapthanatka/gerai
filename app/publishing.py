"""เผยแพร่แบบมีประตู (stage 16) — flag `publish_v2` (ปิด = ปุ่มเผยแพร่/auto-publish แบบเดิม)

สถานะ: draft → reviewed → approved → dispatching → published | failed | uncertain (ไม่รู้ผล) · limited (ปลายทางทำไม่ได้) · rolled_back
- อนุมัติผูกกับ เนื้อหา (hash) + ปลายทาง + เวอร์ชัน (jobs.grant/consume ใช้ครั้งเดียว) · แก้เนื้อหาหลังอนุมัติ = กลับเป็นร่าง
- "เผยแพร่แล้ว" ต้องมีการยืนยันจากปลายทาง · หมดเวลา/ไม่แน่ใจ = uncertain แล้ว reconcile ด้วยอ้างอิงเดิม ไม่ส่งซ้ำมั่ว
- ปลายทางทำไม่ได้ (แก้หน้าที่ทีมเว็บลูกค้าดูแล · คืนเวอร์ชันเดิมบนหน้า hosted) = บันทึกข้อจำกัด/สร้างงาน ไม่อ้างว่าสำเร็จ
- คืนเวอร์ชัน (rollback) กระทบเฉพาะชิ้นนั้น"""
from __future__ import annotations
import hashlib
import json
import re

from . import db, flags

STATES = {"draft": "ร่าง (ยังไม่ผ่านประตู)", "reviewed": "ตรวจแล้ว", "approved": "อนุมัติแล้ว", "dispatching": "กำลังส่ง",
          "published": "เผยแพร่แล้ว (ปลายทางยืนยัน)", "failed": "ไม่สำเร็จ", "uncertain": "ไม่แน่ใจผล — ต้องตรวจปลายทาง",
          "limited": "ปลายทางทำไม่ได้ — สร้างงานให้คนทำ", "rolled_back": "คืนเวอร์ชันแล้ว"}
CAPS = {"wordpress": {"create", "update", "unpublish", "restore"}, "connector": {"create", "update", "unpublish", "restore"},
        "hosted": {"create", "update", "unpublish"}, "existing_page": set()}
DEST_LABEL = {"wordpress": "WordPress", "connector": "WordPress (ตัวเชื่อม)", "hosted": "หน้า hosted ของเจอ.AI", "existing_page": "หน้าเดิมที่ทีมเว็บลูกค้าดูแล"}
HOOKS = {"send": None, "verify": None, "find": None}        # เทสต์ฉีด · None = ของจริง
BAD_LINK = re.compile(r"\]\((?!https?://|/|#)[^)]*\)|javascript:|\]\(https?://(localhost|127\.|example\.(com|org))|TODO|lorem ipsum", re.I)


def on() -> bool:
    try:
        return flags.enabled("publish_v2")
    except Exception:
        return False


def _h(v) -> str:
    return hashlib.sha256(json.dumps(v, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def snapshot(item) -> dict:
    return {k: item[k] for k in ("title", "meta_title", "meta_desc", "body_md", "schema_json")}


def content_hash(item) -> str:
    return _h(snapshot(item))[:24]


def get(rid: int) -> dict | None:
    with db.get_conn() as c:
        r = c.execute(db.q("SELECT * FROM publish_requests WHERE id=?"), (rid,)).fetchone()
    return dict(r) if r else None


def for_content(cid: int) -> list[dict]:
    with db.get_conn() as c:
        return [dict(r) for r in c.execute(db.q("SELECT * FROM publish_requests WHERE content_id=? ORDER BY id DESC"), (cid,)).fetchall()]


def list_brand(bid: int) -> list[dict]:
    with db.get_conn() as c:
        return [dict(r) for r in c.execute(db.q("SELECT * FROM publish_requests WHERE brand_id=? ORDER BY id DESC"), (bid,)).fetchall()]


def _upd(rid: int, frm: tuple | None = None, **f) -> bool:
    f["updated_at"] = db.now()
    sql = f"UPDATE publish_requests SET {', '.join(k + '=?' for k in f)} WHERE id=?"
    args = tuple(f.values()) + (rid,)
    if frm:
        sql += f" AND status IN ({','.join('?' * len(frm))})"
        args += tuple(frm)
    with db.get_conn() as c:
        return (c.execute(db.q(sql), args).rowcount or 0) == 1


def destination(bid: int) -> str:
    from . import delivery
    ws = delivery.for_brand(bid) if delivery.on() else None
    if ws and delivery.access(ws).get("mode") == "existing_team":
        return "existing_page"
    conn = db.get_wp_connection(bid)
    if not conn:
        return "hosted"
    return "connector" if conn["mode"] == "connector" and conn["api_key"] else "wordpress"


def _published_version(cid: int) -> dict | None:
    return next((r for r in for_content(cid) if r["status"] == "published"), None)


# ---------- ประตูก่อนเผยแพร่ ----------
def _norm(s: str) -> str:
    return re.sub(r"[\s\W_]+", " ", (s or "").lower()).strip()


def _schema_ok(item, brand) -> tuple[bool, str]:
    raw = item["schema_json"]
    if not raw:
        return True, "ไม่มี schema"
    try:
        data = json.loads(raw)
    except ValueError:
        return False, "schema ไม่ใช่ JSON ที่อ่านได้"
    nodes = data if isinstance(data, list) else [data]
    body = _norm(item["body_md"])
    for n in nodes:
        if not isinstance(n, dict):
            continue
        for qa in n.get("mainEntity") or [] if n.get("@type") == "FAQPage" else []:
            q = _norm((qa or {}).get("name") or "")
            if q and q not in body:
                return False, f"คำถามใน schema ไม่มีในเนื้อหา: {(qa.get('name') or '')[:60]}"
        org = n.get("name") if n.get("@type") in ("Organization", "LocalBusiness") else None
        if org and _norm(org) != _norm(brand["name"]):
            return False, f"ชื่อองค์กรใน schema ({org}) ไม่ตรงกับแบรนด์ ({brand['name']})"
    return True, "schema ตรงกับข้อความที่เห็น"


def _shingles(s: str) -> set:
    w = _norm(s).split()
    return {" ".join(w[i:i + 3]) for i in range(max(0, len(w) - 2))}


def _dup(item) -> str | None:
    me = _shingles(item["body_md"])
    for o in db.list_content(item["brand_id"]):
        if o["id"] == item["id"] or o["status"] != "published":
            continue
        if _norm(o["title"]) == _norm(item["title"]):
            return f"ชื่อซ้ำกับชิ้น #{o['id']} ที่เผยแพร่แล้ว"
        ot = _shingles(o["body_md"])
        if me and ot and len(me & ot) / len(me | ot) >= 0.8:
            return f"เนื้อหาซ้ำกับชิ้น #{o['id']} เกิน 80%"
    return None


def checks(item, approver_role: str = "customer") -> list[dict]:
    from . import delivery
    brand = db.get_brand(item["brand_id"])
    ws = delivery.for_brand(item["brand_id"]) if delivery.on() else None
    dest = destination(item["brand_id"])
    out = []
    add = lambda k, ok, d: out.append({"key": k, "ok": bool(ok), "detail": d})
    if ws:
        delivery.sync_facts(ws["id"])
        ws = delivery.get(ws["id"])
        g = delivery.gates(ws)
        add("facts", g["facts"], "ข้อมูลธุรกิจยืนยันแล้ว" if g["facts"] else "ข้อมูลธุรกิจยังไม่ยืนยัน/เปลี่ยนหลังยืนยัน")
        add("rights", ws["publish_allowed"] or approver_role == "customer",
            "ลูกค้าอนุญาตให้เราเผยแพร่" if ws["publish_allowed"] else ("ลูกค้าอนุมัติเอง" if approver_role == "customer" else "ลูกค้ายังไม่อนุญาตให้เราเผยแพร่"))
        items = [i for i in delivery._items(ws["id"]) if i["kind"] in ("create_page", "update_existing", "create_article") and i["status"] in ("in_progress", "review", "delivered", "accepted")]
        add("scope", g["plan"] and bool(items), "อยู่ในแผนที่อนุมัติ" if (g["plan"] and items) else "ไม่มีงานเนื้อหาในแผนที่อนุมัติ")
        add("access", g["access"], "สิทธิ์เข้าถึงตรวจแล้ว" if g["access"] else "ยังไม่มีสิทธิ์เข้าถึง")
    else:
        add("facts", True, "ลูกค้าเดิม (ไม่มีพื้นที่งาน) — ลูกค้าเป็นผู้ตรวจข้อมูลเอง")
        add("rights", approver_role == "customer", "ลูกค้าเป็นผู้เผยแพร่เอง" if approver_role == "customer" else "ทีมเผยแพร่แทนลูกค้าเดิมไม่ได้")
        add("scope", True, "แพ็กเกจเดิม")
        add("access", True, "ตามการเชื่อมต่อเดิม")
    ok, d = _schema_ok(item, brand)
    add("schema", ok, d)
    dup = _dup(item)
    add("duplicate", not dup, dup or "ไม่ซ้ำกับที่เผยแพร่แล้ว")
    bad = BAD_LINK.search(item["body_md"] or "")
    add("links", not bad, f"ลิงก์ใช้ไม่ได้: {bad.group(0)[:60]}" if bad else "ลิงก์ปกติ")
    add("relevance", len(_norm(item["body_md"])) >= 200 and bool(_norm(item["title"])), "มีชื่อและเนื้อหาพอ" if len(_norm(item["body_md"])) >= 200 else "เนื้อหาสั้นเกิน")
    need = "update" if _published_version(item["id"]) else "create"
    add("capability", need in CAPS[dest], f"{DEST_LABEL[dest]} ทำ{'แก้ไข' if need == 'update' else 'สร้าง'}ได้" if need in CAPS[dest]
        else f"{DEST_LABEL[dest]} — ระบบเผยแพร่เองไม่ได้")
    return out


# ---------- ขั้นตอน ----------
def request(cid: int, by: str) -> int:
    item = db.get_content(cid)
    if not item:
        raise ValueError("ไม่พบคอนเทนต์")
    h, dest = content_hash(item), destination(item["brand_id"])
    base = _h({"c": cid, "h": h, "d": dest})[:32]
    prev = _published_version(cid)
    with db.get_conn() as c:
        same = [dict(x) for x in c.execute(db.q("SELECT id, status, idem_key FROM publish_requests WHERE idem_key=? OR idem_key LIKE ? ORDER BY id DESC"),
                                           (base, base + "-%")).fetchall()]
    # เวอร์ชันเดียวกันที่ยังเดินอยู่ = คำขอเดิม (กันกดซ้ำ) · ล้ม/คืนแล้ว = เริ่มคำขอใหม่ได้
    idem = base if not same or same[0]["status"] not in ("failed", "rolled_back") else f"{base}-{len(same)}"
    with db.get_conn() as c:
        row = c.execute(db.q("INSERT INTO publish_requests(content_id, brand_id, destination, content_hash, idem_key, status, prior_request_id, "
                             "requested_by, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING RETURNING id"),
                        (cid, item["brand_id"], dest, h, idem, "draft", prev["id"] if prev else None, by[:120], db.now(), db.now())).fetchone()
        if not row:
            row = c.execute(db.q("SELECT id FROM publish_requests WHERE idem_key=?"), (idem,)).fetchone()
    return row["id"]


def review(rid: int, by: str, role: str = "customer") -> tuple[bool, list]:
    r = get(rid)
    item = db.get_content(r["content_id"])
    if content_hash(item) != r["content_hash"]:
        return False, [{"key": "version", "ok": False, "detail": "เนื้อหาเปลี่ยนหลังสร้างคำขอ — สร้างคำขอใหม่"}]
    cs = checks(item, role)
    if r["destination"] == "existing_page" or not next(c for c in cs if c["key"] == "capability")["ok"]:
        _limit(rid, r, item)
        return False, cs
    ok = all(c["ok"] for c in cs)
    _upd(rid, ("draft", "reviewed"), status="reviewed" if ok else "draft", checks_json=json.dumps(cs, ensure_ascii=False), reviewed_by=by[:120])
    return ok, cs


def _limit(rid: int, r: dict, item) -> None:
    """ปลายทางทำไม่ได้ → ไม่อ้างว่าเผยแพร่ · สร้างงานให้ทีมเว็บลูกค้า (ถ้ามีพื้นที่งาน)"""
    from . import delivery
    note = f"ระบบเผยแพร่ที่ {DEST_LABEL[r['destination']]} เองไม่ได้ — ส่งเนื้อหาให้ทีมเว็บนำไปใส่"
    ws = delivery.for_brand(r["brand_id"]) if delivery.on() else None
    if ws:
        with db.get_conn() as c:
            delivery._add_item(c, ws["id"], "update_existing", f"นำเนื้อหา \"{(item['title'] or '')[:60]}\" ไปใส่เว็บ", target=f"content:{item['id']}",
                               evidence=[f"คำขอเผยแพร่ #{rid}"], owner="customer_team", acceptance="หน้าเว็บแสดงเนื้อหาเวอร์ชันนี้", fh=ws.get("facts_hash"))
    _upd(rid, status="limited", error=note)


def approve(rid: int, approver: str, role: str = "customer") -> tuple[bool, str]:
    from . import jobs
    r = get(rid)
    if r["status"] != "reviewed":
        return False, "ต้องผ่านการตรวจก่อน"
    ok, cs = review(rid, approver, role)                 # ตรวจซ้ำตอนอนุมัติ (ข้อมูล/สิทธิ์อาจเปลี่ยน)
    if not ok:
        return False, "ไม่ผ่าน: " + "; ".join(c["detail"] for c in cs if not c["ok"])
    aid = jobs.grant("publish", payload={"content": r["content_id"], "hash": r["content_hash"], "dest": r["destination"]}, policy="publish_gate",
                     approver=f"{role}:{approver}"[:120], channel=r["destination"], ttl_hours=72)
    _upd(rid, ("reviewed",), status="approved", approval_id=aid, approved_by=f"{role}:{approver}"[:120])
    return True, ""


def _send(dest, item, status, remote_id):
    if HOOKS["send"]:
        return HOOKS["send"](dest, item, status, remote_id)
    from . import wp_client
    if dest == "hosted":
        from . import geo_content
        brand = db.get_brand(item["brand_id"])
        if status == "publish":
            link = f"{geo_content._site_url(brand)}/geo/{item['id']}"
            db.mark_content_published(item["id"], None, link)
            return {"ok": True, "id": None, "link": link}
        with db.get_conn() as c:
            c.execute(db.q("UPDATE content_items SET status='draft' WHERE id=?"), (item["id"],))
        return {"ok": True, "id": None, "link": ""}
    conn = db.get_wp_connection(item["brand_id"])
    if dest == "connector":
        return wp_client.connector_publish(conn["site_url"], wp_client.decrypt(conn["api_key"]), item["title"], item["body_md"], schema_json=item["schema_json"],
                                           meta_title=item["meta_title"], meta_desc=item["meta_desc"], status=status, wp_post_id=remote_id)
    return wp_client.publish_post(conn["site_url"], conn["auth_user"], wp_client.decrypt(conn["auth_secret"]), item["title"], item["body_md"],
                                  status=status, wp_post_id=remote_id)


def _verify(dest, ref: dict):
    """True = ปลายทางมีชิ้นงาน · False = ไม่มี · None = ไม่แน่ใจ"""
    if HOOKS["verify"]:
        return HOOKS["verify"](dest, ref)
    if dest == "hosted":
        return True
    if not ref.get("link"):
        return None
    try:
        from . import netguard
        code = netguard.get(ref["link"], timeout=10).status_code
        return True if code == 200 else (False if code in (404, 410) else None)
    except Exception:
        return None


def _find(dest, r: dict):
    if HOOKS["find"]:
        return HOOKS["find"](dest, r)
    return None


def dispatch(rid: int) -> tuple[str, str]:
    """ส่งไปปลายทาง (ครั้งเดียวต่อคำขอ) · คืน (สถานะ, ข้อความ)"""
    from . import jobs, delivery
    r = get(rid)
    if r["status"] in ("dispatching", "uncertain", "published"):
        return r["status"], "ส่งไปแล้ว — ไม่ส่งซ้ำ (ใช้ตรวจสอบปลายทางแทน)"
    if r["status"] != "approved":
        return r["status"], "ยังไม่ได้อนุมัติ"
    item = db.get_content(r["content_id"])
    if content_hash(item) != r["content_hash"]:
        jobs.revoke(r["approval_id"])
        _upd(rid, status="draft", error="เนื้อหาเปลี่ยนหลังอนุมัติ — ต้องตรวจและอนุมัติใหม่", approval_id=None)
        return "draft", "เนื้อหาเปลี่ยนหลังอนุมัติ"
    # สิทธิ์เข้าถึงยังอยู่ไหม (ถูกถอน = ไม่ส่ง)
    ws = delivery.for_brand(r["brand_id"]) if delivery.on() else None
    revoked = (r["destination"] in ("wordpress", "connector") and not db.get_wp_connection(r["brand_id"])) or \
              (ws is not None and (not delivery.access(ws).get("verified") or ws["state"] == "cancelled")) or destination(r["brand_id"]) != r["destination"]
    if revoked:
        _upd(rid, status="failed", error="สิทธิ์เข้าถึงถูกถอนหรือปลายทางเปลี่ยน — ไม่ได้ส่ง")
        return "failed", "สิทธิ์เข้าถึงถูกถอน"
    ok, why = jobs.consume(r["approval_id"], action="publish", payload={"content": r["content_id"], "hash": r["content_hash"], "dest": r["destination"]},
                           job_id=rid, channel=r["destination"])
    if not ok:
        _upd(rid, status="failed", error=f"การอนุมัติใช้ไม่ได้ ({why})")
        return "failed", why
    if not _upd(rid, ("approved",), status="dispatching"):
        return get(rid)["status"], "มีอีกงานกำลังส่ง"
    prev = get(r["prior_request_id"]) if r["prior_request_id"] else None
    remote_id = (prev or {}).get("remote_id") or item["wp_post_id"]
    try:
        res = _send(r["destination"], item, "publish", remote_id)
    except TimeoutError:
        _upd(rid, status="uncertain", error="ปลายทางไม่ตอบในเวลา — ไม่รู้ว่าเผยแพร่หรือยัง")
        return "uncertain", "หมดเวลา"
    except Exception as e:
        _upd(rid, status="failed", error=f"{type(e).__name__}: {str(e)[:160]}")
        return "failed", str(e)[:160]
    if not res.get("ok"):
        _upd(rid, status="failed", error=(res.get("msg") or "ปลายทางปฏิเสธ")[:300])
        return "failed", res.get("msg") or ""
    ref = {"id": res.get("id") or remote_id, "link": res.get("link") or ""}
    _upd(rid, remote_id=ref["id"], remote_link=ref["link"])
    return _confirm(rid, ref, item)


def _confirm(rid: int, ref: dict, item) -> tuple[str, str]:
    v = _verify(get(rid)["destination"], ref)
    if v is True:
        _upd(rid, status="published", published_snapshot_json=json.dumps(snapshot(item), ensure_ascii=False), error=None, published_at=db.now())
        if ref.get("id") is not None or ref.get("link"):
            db.mark_content_published(item["id"], ref.get("id"), ref.get("link"))
        return "published", "ปลายทางยืนยันแล้ว"
    if v is False:
        _upd(rid, status="failed", error="ปลายทางตอบรับแต่ตรวจแล้วไม่พบชิ้นงาน")
        return "failed", "ตรวจไม่พบ"
    _upd(rid, status="uncertain", error="ส่งแล้วแต่ยังยืนยันจากปลายทางไม่ได้")
    return "uncertain", "ยืนยันไม่ได้"


def reconcile(rid: int) -> tuple[str, str]:
    """คำขอที่ไม่แน่ใจผล: ตามหาด้วยอ้างอิงเดิม (id ปลายทาง/คีย์กันซ้ำ) แทนการส่งซ้ำ"""
    r = get(rid)
    if r["status"] not in ("uncertain", "dispatching"):
        return r["status"], "ไม่ต้องตรวจ"
    ref = {"id": r["remote_id"], "link": r["remote_link"]} if (r["remote_id"] or r["remote_link"]) else _find(r["destination"], r)
    if not ref:
        return "uncertain", "ยังหาไม่เจอว่าปลายทางมีหรือไม่ — ตรวจเองที่ปลายทางก่อนสั่งใหม่"
    _upd(rid, remote_id=ref.get("id"), remote_link=ref.get("link") or "")
    return _confirm(rid, ref, db.get_content(r["content_id"]))


def rollback(rid: int, by: str) -> tuple[str, str]:
    """คืนเฉพาะชิ้นนี้: มีเวอร์ชันก่อน → ส่งเวอร์ชันเดิมกลับ (ต้องรองรับ restore) · ไม่มี → ถอนการเผยแพร่ (ต้องรองรับ unpublish)"""
    r = get(rid)
    if r["status"] != "published":
        return r["status"], "คืนได้เฉพาะชิ้นที่เผยแพร่แล้ว"
    prev = get(r["prior_request_id"]) if r["prior_request_id"] else None
    need = "restore" if prev else "unpublish"
    if need not in CAPS[r["destination"]]:
        _upd(rid, error=f"{DEST_LABEL[r['destination']]} คืนเวอร์ชันเดิมเองไม่ได้ — ต้องแก้ด้วยคน (ยังเผยแพร่เวอร์ชันนี้อยู่)")
        return "published", "ปลายทางคืนเวอร์ชันไม่ได้ — บันทึกเป็นงานให้คนทำ"
    item = dict(db.get_content(r["content_id"]))
    try:
        if prev:
            snap = json.loads(prev["published_snapshot_json"] or "{}")
            res = _send(r["destination"], {**item, **snap}, "publish", r["remote_id"])
        else:
            res = _send(r["destination"], item, "draft", r["remote_id"])
    except Exception as e:
        _upd(rid, error=f"คืนเวอร์ชันไม่สำเร็จ ({type(e).__name__}) — สถานะปลายทางไม่แน่ใจ")
        return "published", "คืนไม่สำเร็จ"
    if not res.get("ok"):
        _upd(rid, error="คืนเวอร์ชันไม่สำเร็จ: " + (res.get("msg") or "")[:200])
        return "published", "คืนไม่สำเร็จ"
    _upd(rid, ("published",), status="rolled_back", error=f"คืนเวอร์ชันโดย {by[:60]}")
    if prev:
        _upd(prev["id"], status="published")
    else:
        with db.get_conn() as c:
            c.execute(db.q("UPDATE content_items SET status='draft' WHERE id=?"), (r["content_id"],))
    return "rolled_back", "คืนแล้ว"


def publish_now(cid: int, who: str, role: str = "customer") -> tuple[str, str]:
    """ปุ่ม "เผยแพร่" ของลูกค้า: สร้างคำขอ → ตรวจ → อนุมัติ (ลูกค้าคือผู้อนุมัติ) → ส่ง · ไม่ผ่านประตู = บอกเหตุผล ไม่ส่ง"""
    rid = request(cid, who)
    r = get(rid)
    if r["status"] in ("published", "dispatching", "uncertain", "limited"):
        return r["status"], STATES[r["status"]]
    if r["status"] == "draft":
        ok, cs = review(rid, who, role)
        if not ok:
            r = get(rid)
            if r["status"] == "limited":
                return "limited", r["error"]
            return "draft", "ยังไม่ผ่าน: " + "; ".join(c["detail"] for c in cs if not c["ok"])
    if get(rid)["status"] == "reviewed":
        ok, msg = approve(rid, who, role)
        if not ok:
            return "draft", msg
    return dispatch(rid)
