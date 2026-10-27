"""CEO แจกงาน — ทุกเช้า (07:00) หรือเมื่อเจ้าของสั่ง "วางแผนวันนี้" CEO ดูตัวเลขด่านที่ยังขาดแล้วแจกงานให้เอเจนต์พร้อมเหตุผล:
  หาลูกค้า (Prospecting) — ตรวจฟรีสัปดาห์นี้ยังไม่ถึงเป้า → หาเพิ่มในกลุ่มที่ได้ผลดีที่สุด (หรือกลุ่มที่ยังไม่เคยลอง)
  หาลูกค้า — ลีดใหม่ที่ไม่มีอีเมล/เบอร์ → หาช่องทางติดต่อซ้ำ
  CTO — ลีดที่เปิดอ่านรายงาน/ตอบกลับแต่ยังไม่มีผลถาม AI → วัด 20 คำถาม (ใต้เพดานต่อวัน)
  CMO — โพสต์สัปดาห์นี้ยังไม่ครบโควตา → รอบการตลาด
  SDR — วันนี้ยังไม่ได้รันรอบติดตามผล (ช่วงเวลาที่ส่งได้) → รอบติดตามผล
  เจ้าของ — ลีดที่ต้องใช้คนโทร → ฝากงานโทรพร้อมลิงก์บท
ทุกงานผ่านฟังก์ชันจริงเดิม จึงอยู่ใต้เพดาน/กรอบอำนาจ/เวลาส่งเหมือนเดิม · หยุดฉุกเฉิน = แจกงานได้แต่พักไว้ (held) · งานซ้ำในวันเดียวกันไม่แจกซ้ำ"""
from __future__ import annotations
import datetime as dt
import json
import math
import re
import threading
from collections import defaultdict

from . import db, execs, leads as L, office

RUN_INLINE = False
NAME = {a["id"]: a["name"] for a in office.ACTORS}
MAX_PROSPECT = 10
MAX_REFIND = 5


def _today_start() -> str:
    return office.today_start_local()


def _ts(s):
    try:
        return dt.datetime.fromisoformat(str(s)[:19])
    except Exception:
        return None


# ---------- อ่านผลงานต่อกลุ่มลูกค้า ----------
def segment_stats() -> dict:
    """ต่อกลุ่มเป้าหมาย: จำนวนลีด ทักแล้ว เปิดอ่าน ตอบกลับ (จาก notes "พบจากคำค้น: <กลุ่ม> <จังหวัด>")"""
    from . import prospecting
    segs = prospecting.segments()
    st = {s: {"leads": 0, "contacted": 0, "opened": 0, "replied": 0, "areas": defaultdict(int)} for s in segs}
    for l in db.list_leads(None, 5000):
        m = re.search(r"พบจากคำค้น:\s*([^·]+)", l["notes"] or "")
        if not m:
            continue
        q = m.group(1).strip()
        seg = next((s for s in segs if q.startswith(s)), None)
        if not seg:
            continue
        r = st[seg]
        r["leads"] += 1
        area = q[len(seg):].strip()
        if area:
            r["areas"][area] += 1
        kinds = {e["kind"] for e in db.list_lead_events(l["id"], 200)}
        if "contacted" in kinds:
            r["contacted"] += 1
        if "opened" in kinds:
            r["opened"] += 1
        if "replied" in kinds or l["status"] in ("replied", "call", "met", "quoted", "won"):
            r["replied"] += 1
    return st


def pick_segment() -> tuple[str, str, str]:
    """(กลุ่ม, จังหวัด, เหตุผล) — กลุ่มที่เปิดอ่าน/ตอบดีที่สุด (ทักแล้ว ≥ 3) ไม่งั้นกลุ่มที่ลองน้อยที่สุด · จังหวัดที่ค้นน้อยที่สุดในกลุ่มนั้น"""
    from . import prospecting
    st = segment_stats()
    scored = [(s, (r["replied"] * 3 + r["opened"]) / r["contacted"], r) for s, r in st.items() if r["contacted"] >= 3]
    scored = [x for x in scored if x[1] > 0]
    if scored:
        seg, score, r = max(scored, key=lambda x: x[1])
        why = f"กลุ่ม \"{seg}\" ได้ผลดีที่สุด (ทัก {r['contacted']} เปิดอ่าน {r['opened']} ตอบ {r['replied']})"
    else:
        seg = min(st, key=lambda s: st[s]["leads"]) if st else prospecting.segments()[0]
        why = f"ยังไม่มีกลุ่มไหนได้ผลชัด — ลองกลุ่มที่ค้นน้อยที่สุด \"{seg}\" ({st.get(seg, {}).get('leads', 0)} ราย)"
    areas = st.get(seg, {}).get("areas", {})
    area = min(prospecting.AREAS, key=lambda a: areas.get(a, 0))
    return seg, area, why


# ---------- วางแผน ----------
def _exists_today(agent: str, kind: str, key: str = "") -> bool:
    for t in db.list_tasks(_today_start(), None, 500):
        if t["agent"] == agent and t["kind"] == kind and (not key or (json.loads(t["args_json"] or "{}").get("key") == key)):
            return True
    return False


def plan(now: dt.datetime | None = None) -> list[int]:
    """สร้างงานของวันนี้ (ไม่ซ้ำกับที่แจกไปแล้ววันนี้) · คืน id งานที่สร้าง"""
    from . import closer, deep_audit, followup, marketing, prospecting, sms
    now = now or dt.datetime.now()
    made = []
    add = lambda agent, kind, title, reason, args: made.append(db.add_task(agent, kind, title, reason, args))

    # เจ้าของ: งานโทร
    for x in closer.call_list(now=now, limit=3):
        d = x["lead"]["domain"]
        if not _exists_today("owner", "call", d):
            add("owner", "call", f"โทรหา {d}", x["trigger"]["why"], {"key": d, "lead_id": x["lead"]["id"]})

    # Prospecting: ตรวจฟรียังไม่ถึงเป้า
    k = execs.kpis()
    row = next((r for r in k["rows"] if r["key"] == "audits_week"), None)
    if row and row["target"] and row["value"] < row["target"] and prospecting.enabled() and not _exists_today("prospecting", "find_leads"):
        days_left = max(1, 7 - now.weekday())
        need = row["target"] - row["value"]
        n = max(3, min(MAX_PROSPECT, math.ceil(need / days_left)))
        seg, area, why = pick_segment()
        add("prospecting", "find_leads", f"หาลูกค้า \"{seg} {area}\" {n} ราย",
            f"ตรวจฟรีสัปดาห์นี้ {row['value']}/{row['target']} ขาด {need} · {why}", {"segment": seg, "area": area, "n": n})

    # Prospecting: หาช่องทางติดต่อซ้ำ
    if not _exists_today("prospecting", "refind"):
        cut = (now - dt.timedelta(days=1)).isoformat(timespec="seconds")
        cands = []
        for l in db.list_leads("new", 2000):
            if l["email"] or not l["audit_json"] or (l["created_at"] or "") > cut:
                continue
            try:
                has_phone = bool(json.loads(l["call_json"] or "{}").get("phone")) if l["call_json"] else False
            except Exception:
                has_phone = False
            if has_phone or any("หาซ้ำ" in (e["note"] or "") for e in db.list_lead_events(l["id"], 50)):
                continue
            cands.append(l["id"])
        if cands:
            add("prospecting", "refind", f"หาช่องทางติดต่อซ้ำ {min(len(cands), MAX_REFIND)} ราย",
                f"ลีดใหม่ {len(cands)} รายยังไม่มีอีเมลหรือเบอร์ ทักไม่ได้", {"lead_ids": cands[:MAX_REFIND]})

    # CTO: ลีดที่สนใจจริงแต่ยังไม่มีผล AI
    room = max(0, deep_audit.ai_daily() - deep_audit.ai_today())
    if room:
        for l in db.list_leads(None, 5000):
            if room <= 0:
                break
            if l["status"] in L.NO_CONTACT or deep_audit.tier2(l).get("state") in ("done", "running"):
                continue
            kinds = {e["kind"] for e in db.list_lead_events(l["id"], 100)}
            if not ({"opened", "replied", "quote_viewed"} & kinds):
                continue
            if _exists_today("cto", "ai_report", l["domain"]):
                continue
            add("cto", "ai_report", f"ถาม ChatGPT 20 คำถามให้ {l['domain']}", "เปิดอ่านรายงาน/ตอบกลับแล้ว — ผลถาม AI ช่วยให้ตัดสินใจ", {"key": l["domain"], "lead_id": l["id"]})
            room -= 1

    # CMO: โพสต์สัปดาห์นี้ยังไม่ครบ
    if marketing.enabled() and marketing.channels() and not _exists_today("cmo", "marketing_round"):
        wk = (now - dt.timedelta(days=7)).isoformat(timespec="seconds")
        short = [ch for ch in marketing.channels() if db.count_posts_since(ch, wk) < marketing.weekly()]
        last = (marketing.last_run() or {}).get("at", "")
        if short and not last.startswith(now.date().isoformat()):
            add("cmo", "marketing_round", "รอบการตลาด: โพสต์ให้ครบโควตาสัปดาห์", f"โพสต์ 7 วันยังไม่ครบ {marketing.weekly()} ชิ้นใน {', '.join(short)}", {})

    # SDR: ยังไม่ได้รันรอบติดตามผลวันนี้ (เฉพาะช่วงที่ส่งได้)
    if followup.enabled() and sms.in_hours() and not _exists_today("sdr", "followup_round"):
        last = (followup.last_run() or {}).get("at", "")
        if not last.startswith(now.date().isoformat()):
            add("sdr", "followup_round", "รอบติดตามผลลีดที่เงียบ", "วันนี้ยังไม่ได้ติดตามผล", {})
    return made


# ---------- ลงมือ ----------
def run_task(tid: int) -> str:
    from . import ceo_chat, prospecting, followup, marketing, deep_audit, closer, pipeline, line_oa
    t = db.get_task(tid)
    if not t or t["status"] not in ("queued", "held"):
        return t["result"] if t else ""
    args = json.loads(t["args_json"] or "{}")
    db.update_task(tid, status="running", started_at=db.now())
    try:
        with office.working(t["agent"] if t["agent"] != "owner" else "ceo", t["title"]):
            if t["kind"] == "call":
                lead = db.get_lead(args["lead_id"])
                closer.prepare(lead)
                res = f"ฝากเจ้าของโทร · บท: {pipeline.public_base()}/admin/calls/{lead['id']}"
            elif t["kind"] == "find_leads":
                r = prospecting.run(limit=int(args["n"]), queries=[f"{args['segment']} {args['area']}"], force=True)
                res = r.get("skipped") or f"เพิ่ม {r['added']} ราย · ตรวจ {r['audited']} · มีอีเมล {r['with_email']} · ทัก {r['proposed']}"
            elif t["kind"] == "refind":
                found = 0
                for lid in args.get("lead_ids", []):
                    lead = db.get_lead(lid)
                    if lead and not lead["email"]:
                        c = prospecting.refind_contact(lead)
                        found += 1 if c.get("email") else 0
                        try:
                            ph = closer.find_phone(db.get_lead(lid))
                            if ph:
                                d = closer._data(db.get_lead(lid)); d["phone"] = ph; closer._save(lid, d)
                        except Exception:
                            pass
                        pipeline.record(lid, "note", "หาช่องทางติดต่อซ้ำ (CEO แจกงาน)", "prospecting")
                res = f"หาซ้ำ {len(args.get('lead_ids', []))} ราย เจออีเมลเพิ่ม {found}"
            elif t["kind"] == "ai_report":
                lead = db.get_lead(args["lead_id"])
                d = deep_audit.run_ai(lead["id"]) if deep_audit.tier1(lead).get("state") == "done" else (deep_audit.run_tier1(lead["id"]) and deep_audit.run_ai(lead["id"]))
                res = f"{d.get('state')} · เอ่ยถึง {d.get('mentioned', '—')}/{d.get('asked', '—')}" if isinstance(d, dict) else str(d)
            elif t["kind"] == "marketing_round":
                r = marketing.run()
                res = r.get("skipped") or f"เสนอ/โพสต์ {r.get('proposed', 0)} ชิ้น"
            elif t["kind"] == "followup_round":
                r = followup.run()
                res = followup.status_text() or str(r)
            else:
                res = "ไม่รู้จักงานนี้"
        db.update_task(tid, status="done", finished_at=db.now(), result=res[:500])
        return res
    except Exception as e:
        db.update_task(tid, status="failed", finished_at=db.now(), result=f"{type(e).__name__}: {str(e)[:200]}")
        return f"ล้ม: {type(e).__name__}"


def dispatch(ids: list[int], notify: bool = True) -> None:
    def job():
        lines = []
        for tid in ids:
            t = db.get_task(tid)
            res = run_task(tid)
            lines.append(f"• {NAME.get(t['agent'], t['agent'])}: {t['title']} — {res[:120]}")
        if notify and lines:
            try:
                from . import line_oa
                line_oa.notify_owner("CEO แจกงานวันนี้\n" + "\n".join(lines))
            except Exception:
                pass
    if RUN_INLINE:
        job()
    else:
        threading.Thread(target=job, daemon=False).start()


@office.track("ceo", "วางแผนและแจกงานให้ทีม")
def plan_and_run(notify: bool = True) -> dict:
    ids = plan()
    if execs.paused():
        for tid in ids:
            db.update_task(tid, status="held", result="พักไว้ — หยุดฉุกเฉินอยู่")
        return {"tasks": ids, "held": True}
    dispatch(ids, notify)
    return {"tasks": ids, "held": False}


def summary(ids: list[int] | None = None) -> str:
    rows = [db.get_task(i) for i in ids] if ids is not None else list(db.list_tasks(_today_start(), None, 50))
    rows = [r for r in rows if r]
    if not rows:
        return "วันนี้ยังไม่มีงานที่ต้องแจก — ตัวเลขที่ทำได้ตอนนี้ทีมทำครบแล้ว"
    return "CEO แจกงาน:\n" + "\n".join(f"• {NAME.get(r['agent'], r['agent'])}: {r['title']} — {r['reason']}" + (f" [{r['status']}]" if r["status"] != "queued" else "") for r in rows)


def status_text() -> str:
    rows = list(db.list_tasks(_today_start(), None, 50))
    if not rows:
        return ""
    done = sum(1 for r in rows if r["status"] == "done")
    return f"CEO แจกงานวันนี้ {len(rows)} งาน (เสร็จ {done}): " + " · ".join(f"{NAME.get(r['agent'], r['agent'])} {r['title']}" for r in rows[:4])
