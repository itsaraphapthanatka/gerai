"""สั่งงาน CEO — เจ้าของพิมพ์ภาษาคนทาง LINE (บัญชีที่ผูกเป็นเจ้าของ) หรือหน้าแชตในแอดมิน
CEO แปลงคำสั่งเป็นงานจากรายการที่กำหนดไว้เท่านั้น (ไม่มีคำสั่งอิสระ) แล้วลงมือด้วยฟังก์ชันจริงของระบบ
งานที่ติดต่อลูกค้า มีค่าใช้จ่าย หรือเรื่องราคา → ถามยืนยันก่อน ("ยืนยัน" ภายใน 30 นาที) · งานอ่านอย่างเดียวตอบทันที
คำสั่งของเจ้าของ = อนุมัติจากบอร์ด จึงทำได้แม้เรื่องนั้นยังเป็นโหมดเสนอ แต่ CFO ยังเตือนเมื่อเกินกรอบ"""
from __future__ import annotations
import datetime as dt
import json
import re
import threading

from . import db, execs, leads as L, office

LOG_KEY, PENDING_KEY = "ceo_chat_log", "ceo_chat_pending"
CONFIRM_WORDS = re.compile(r"^\s*(ยืนยัน|ใช่|ตกลง|โอเค|ok|okay|yes|y|จัดไป|เอา|ได้)\s*[.!✓]*\s*$", re.I)
CANCEL_WORDS = re.compile(r"^\s*(ยกเลิก|ไม่|ไม่เอา|no|cancel|stop)\s*$", re.I)
PENDING_MIN = 30
RUN_INLINE = False
LLM = None                    # เทสต์ฉีด

ACTIONS = {
    "status": ("สรุปสถานะวันนี้: ด่าน ตัวเลขดีล เรื่องรออนุมัติ ใครควรโทร", {}, False),
    "calls": ("รายชื่อที่ควรโทรวันนี้", {}, False),
    "lead_info": ("ดูสถานะลีดรายหนึ่ง", {"domain": "โดเมน"}, False),
    "find_leads": ("หาลูกค้าใหม่ตามกลุ่มธุรกิจ/พื้นที่", {"segment": "กลุ่มธุรกิจ", "area": "จังหวัด (ไม่บังคับ)", "n": "จำนวน 1-15"}, True),
    "audit": ("ตรวจเว็บฟรีให้เว็บหนึ่ง (เพิ่มเป็นลีด)", {"url": "ที่อยู่เว็บ"}, False),
    "contact_lead": ("ให้ CEO ทักลีดรายนี้ (ร่าง+ส่งข้อความ)", {"domain": "โดเมน"}, True),
    "quote": ("ทำและส่งใบเสนอราคา", {"domain": "โดเมน", "package": "pro|agency", "discount_pct": "ส่วนลด %"}, True),
    "followup": ("ส่งข้อความติดตามผลครั้งถัดไปทันที", {"domain": "โดเมน"}, True),
    "ai_report": ("ถาม ChatGPT 20 คำถามให้ลีด (≈฿40)", {"domain": "โดเมน"}, True),
    "post": ("โพสต์การตลาดทันที", {"kind": "insight|ai_test|tip"}, True),
    "note": ("จดบันทึกในไทม์ไลน์ลีด", {"domain": "โดเมน", "text": "ข้อความ"}, False),
    "pause": ("หยุดฉุกเฉิน — ทุกเรื่องกลับมารอคน", {}, True),
    "resume": ("กลับมาทำงานตามโหมดเดิม", {}, False),
    "plan": ("วางแผนวันนี้และแจกงานให้ทีม", {}, False),
    "tasks": ("ดูงานที่ CEO แจกวันนี้", {}, False),
    "explain": ("อธิบายว่าทำไมระบบทำแบบนั้นกับลีดรายหนึ่ง", {"domain": "โดเมน (ไม่บังคับ = รายล่าสุดที่คุยกัน)"}, False),
    "chat": ("ตอบคำถามทั่วไปเรื่องงาน (ไม่ลงมือทำอะไร)", {"question": "คำถาม"}, False),
    "help": ("บอกว่าสั่งอะไรได้บ้าง", {}, False),
}


# ---------- บันทึกบทสนทนา ----------
def log() -> list[dict]:
    try:
        return json.loads(db.get_setting(LOG_KEY) or "[]")
    except Exception:
        return []


def _log(role: str, text: str) -> None:
    lg = log()[-59:]
    lg.append({"at": db.now(), "role": role, "text": text[:2000]})
    db.set_setting(LOG_KEY, json.dumps(lg, ensure_ascii=False))


def _pending():
    try:
        p = json.loads(db.get_setting(PENDING_KEY) or "null")
    except Exception:
        return None
    if not p:
        return None
    try:
        if (dt.datetime.now() - dt.datetime.fromisoformat(p["at"])).total_seconds() > PENDING_MIN * 60:
            return None
    except Exception:
        return None
    return p


def _set_pending(p) -> None:
    db.set_setting(PENDING_KEY, json.dumps(p, ensure_ascii=False) if p else "null")


# ---------- เข้าใจคำสั่ง ----------
def _last_domain() -> str:
    """โดเมนล่าสุดที่พูดถึงในบทสนทนา — ใช้เมื่อเจ้าของพิมพ์สั้น ๆ เช่น "ทักเลย" "ปิดทำไม" """
    for m in reversed(log()[-12:]):
        u = L.first_url(m["text"])
        if u and L.normalize_url(u):
            return L.domain_of(L.normalize_url(u))
    return ""


def _find_lead(q: str):
    q = (q or "").strip().lower()
    q = L.domain_of(L.normalize_url(q)) if L.normalize_url(q) else q
    if not q:
        return None
    rows = db.list_leads(None, 5000)
    exact = [r for r in rows if r["domain"] == q]
    if exact:
        return exact[0]
    part = [r for r in rows if q in r["domain"]]
    return part[0] if len(part) == 1 else None


def _rules(text: str) -> dict | None:
    """คำสั่งที่พบบ่อยไม่ต้องรอโมเดล"""
    t = text.strip()
    url = L.first_url(t)
    if url and re.search(r"(สถานะ|เป็นไง|ถึงไหน|status|ดูลีด|ข้อมูล)", t, re.I):
        return {"action": "lead_info", "args": {"domain": url}}
    if not url and re.search(r"^(สรุป|สถานะ|status|วันนี้เป็นไง|เป็นไงบ้าง)", t, re.I):
        return {"action": "status", "args": {}}
    if re.search(r"(ใคร.*โทร|โทรใคร|ควรโทร|รายชื่อโทร)", t):
        return {"action": "calls", "args": {}}
    if re.search(r"^(หยุด|หยุดฉุกเฉิน|pause)\b", t, re.I):
        return {"action": "pause", "args": {}}
    if re.search(r"^(ทำงานต่อ|เริ่มใหม่|resume|กลับมาทำงาน)", t, re.I):
        return {"action": "resume", "args": {}}
    if re.search(r"(วางแผน|แจกงาน|สั่งทีม|มอบหมาย)", t):
        return {"action": "plan", "args": {}}
    if re.search(r"(งานวันนี้|ใครทำอะไร|ทีมทำอะไร|งานที่แจก)", t):
        return {"action": "tasks", "args": {}}
    if re.search(r"(ทำไม|เพราะอะไร|why|อธิบาย|เหตุผล)", t, re.I):
        return {"action": "explain", "args": {"domain": url or ""}}
    if re.search(r"^(ช่วย|help|สั่งอะไรได้|ทำอะไรได้)", t, re.I):
        return {"action": "help", "args": {}}
    return None


def parse(text: str, llm=None) -> dict:
    r = _rules(text)
    if r:
        return r
    llm = llm or LLM
    if llm is None:
        from . import ai_client
        llm = lambda m: ai_client.chat_for("sales", m, max_tokens=1500, json_mode=True)
    catalog = "\n".join(f"- {k}: {v[0]} · args: {json.dumps(v[1], ensure_ascii=False)}" for k, v in ACTIONS.items())
    prompt = (f"คุณคือ CEO เอเจนต์ของเจอ.AI เจ้าของสั่งงานว่า: \"{text}\"\nเลือกงานหนึ่งอย่างจากรายการนี้เท่านั้น:\n{catalog}\n"
              "ถ้าไม่ตรงงานไหนเลย ใช้ help · ห้ามคิดงานใหม่เอง · ตอบ JSON เท่านั้น: {\"action\": \"...\", \"args\": {...}}")
    try:
        raw = llm([{"role": "user", "content": prompt}])
        m = re.search(r"\{[\s\S]*\}", raw or "")
        d = json.loads(m.group(0)) if m else {}
        if d.get("action") in ACTIONS:
            return {"action": d["action"], "args": d.get("args") or {}}
    except Exception:
        pass
    url = L.first_url(text)
    if url and re.search(r"(ทัก|ติดต่อ|ส่งข้อความ|อีเมลหา|contact)", text):
        return {"action": "contact_lead", "args": {"domain": url}}
    if url:
        return {"action": "audit", "args": {"url": url}}
    return {"action": "chat", "args": {"question": text}}


# ---------- งานแต่ละอย่าง ----------
def _bg(fn) -> None:
    if RUN_INLINE:
        fn()
    else:
        threading.Thread(target=fn, daemon=False).start()


def _need_lead(args) -> tuple:
    q = args.get("domain") or args.get("url") or _last_domain()
    args["domain"] = q
    lead = _find_lead(q)
    if not lead:
        return None, f"ไม่เจอลีด \"{args.get('domain') or ''}\" — พิมพ์โดเมนให้ตรง เช่น example.co.th"
    return lead, ""


def plan(action: str, args: dict) -> tuple[str, str | None]:
    """ข้อความยืนยันก่อนทำ (None = ทำได้เลย ไม่ต้องยืนยัน หรือทำไม่ได้)"""
    from . import pipeline, quotes as Q
    if action == "contact_lead":
        lead, err = _need_lead(args)
        if err:
            site = L.normalize_url(args.get("domain") or "")
            if not site:
                return err, None
            return f"ยังไม่มีลีด {L.domain_of(site)} — จะตรวจเว็บ หาอีเมล/เบอร์จากเว็บ แล้วร่างและส่งข้อความทักให้", "ok"
        if lead["status"] == "unsubscribed":
            return f"{lead['domain']} ขอไม่รับการติดต่อไว้แล้ว ทักไม่ได้", None
        ch = pipeline.send_channel(lead)
        reopen = " (ลีดนี้ถูกปิดไว้ จะเปิดใหม่ตามคำสั่งคุณ)" if lead["status"] == "lost" else ""
        return (f"จะร่างและส่งข้อความทัก {lead['domain']} " + (f"ทาง {ch}" if ch else "— ยังไม่มีอีเมล/LINE จะลองหาจากเว็บก่อน ถ้าไม่เจอจะส่งร่างให้คุณคัดลอกไปส่งเอง") + reopen), "ok"
    if action in ("quote", "followup", "ai_report"):
        lead, err = _need_lead(args)
        if err:
            return err, None
        ch = pipeline.send_channel(lead) or "ไม่มีช่องทางส่งอัตโนมัติ"
        if action == "followup":
            return f"จะส่งข้อความติดตามผลครั้งถัดไปถึง {lead['domain']} ทาง {ch} ทันที (ไม่รอวันครบ)", "ok"
        if action == "ai_report":
            return f"จะถาม ChatGPT 20 คำถามเรื่อง {lead['domain']} (ค่าใช้จ่ายราว ฿40)", "ok"
        pkg = (args.get("package") or "pro").lower()
        pkg = pkg if pkg in Q.PACKAGES else "pro"
        try:
            disc = max(0.0, min(50.0, float(str(args.get("discount_pct") or 0).strip("% "))))
        except ValueError:
            disc = 0.0
        args.update(package=pkg, discount_pct=disc)
        pr = Q.price_for(pkg, disc, Q.needs_setup(json.loads(lead["audit_json"] or "{}") if lead["audit_json"] else {}))
        warn = execs.cfo_check({"price": pr["price"], "discount_pct": disc})
        return (f"จะส่งใบเสนอราคา {pkg.capitalize()} {pr['net']:,} บาท/เดือน" + (f" (ลด {disc:.0f}%)" if disc else "")
                + (f" + GEO Setup {pr['setup_price']:,}" if pr["setup"] else "") + f" งวดแรก {pr['first_total']:,} บาท ถึง {lead['domain']} ทาง {ch}"
                + ("" if warn["ok"] else f"\n⚠️ CFO: {warn['note']} — คุณเป็นบอร์ด สั่งได้ แต่เกินกรอบที่ตั้งไว้")), "ok"
    if action == "find_leads":
        seg = (args.get("segment") or "").strip()
        if not seg:
            return "บอกกลุ่มธุรกิจด้วย เช่น \"หาคลินิกทันตกรรม เชียงใหม่ 5 ราย\"", None
        n = max(1, min(15, int(re.sub(r"\D", "", str(args.get("n") or 5)) or 5)))
        args["n"] = n
        return f"จะหาลูกค้า \"{seg}{' ' + args['area'] if args.get('area') else ''}\" {n} ราย → ตรวจเว็บ → หาช่องทาง → ทักตามโหมดข้อความทัก (ใช้ Serper ราว {n * 2} เครดิต)", "ok"
    if action == "post":
        k = args.get("kind") if args.get("kind") in ("insight", "ai_test", "tip") else "insight"
        args["kind"] = k
        return f"จะโพสต์ {k} ทาง LINE OA และ Facebook ทันที (นอกโควตารายสัปดาห์)", "ok"
    if action == "pause":
        return "จะหยุดฉุกเฉิน — ทุกเรื่องที่ทำเองจะกลับมารอคุณอนุมัติ", "ok"
    return "", None


def execute(action: str, args: dict) -> str:
    from . import pipeline, closer, followup, deep_audit, marketing, prospecting
    if action == "status":
        k = execs.kpis()
        f = L.funnel(db.list_leads(None, 5000))
        pend = len(db.list_decisions("proposed", 500))
        lines = [f"สถานะ {office.today_bkk()} · {execs.mode_label()}",
                 f"สายดีล: ใหม่ {f['n'].get('new', 0)} · ทักแล้ว {f['n'].get('contacted', 0)} · ตอบกลับ {f['n'].get('replied', 0)} · เสนอราคา {f['n'].get('quoted', 0)} · ปิดดีล {f['n'].get('won', 0)}",
                 f"ด่านเฟส {k['phase']}: ผ่าน {k['n_pass']}/{k['n_rows']} · " + " · ".join(f"{r['label']} {r['value']}/{r['target']}" for r in k["rows"] if not r["ok"]),
                 f"รออนุมัติ {pend} เรื่อง"]
        c = closer.status_text()
        if c:
            lines.append(c)
        return "\n".join(lines)
    if action == "calls":
        lst = closer.call_list(limit=8)
        if not lst:
            return "วันนี้ยังไม่มีลีดที่ควรโทร"
        return "ควรโทรวันนี้:\n" + "\n".join(f"{i + 1}. {x['lead']['domain']} — {x['trigger']['why']}" + (f" · {closer.fmt_phone(x['phone'])}" if x["phone"] else "")
                                               for i, x in enumerate(lst)) + f"\nบทโทร: {pipeline.public_base()}/admin/calls"
    if action == "plan":
        from . import orchestrator
        res = orchestrator.plan_and_run()
        return orchestrator.summary(res["tasks"]) + ("\n(พักไว้ เพราะหยุดฉุกเฉินอยู่)" if res["held"] else "\nกำลังทำ เสร็จแล้วแจ้งใน LINE")
    if action == "tasks":
        from . import orchestrator
        return orchestrator.summary()
    if action == "explain":
        return explain(args)
    if action == "chat":
        return answer(args.get("question") or "")
    if action == "help":
        return ("สั่งได้เช่น\n• สรุปวันนี้\n• วางแผนวันนี้ (CEO แจกงานให้ทีม)\n• งานวันนี้\n• วันนี้ควรโทรใคร\n• หาคลินิกทันตกรรม เชียงใหม่ 5 ราย\n• ตรวจเว็บ example.co.th\n• ทัก example.co.th\n"
                "• ส่งใบเสนอราคา example.co.th ลด 5%\n• ติดตามผล example.co.th\n• ถาม AI ให้ example.co.th\n• โพสต์สถิติ / โพสต์เคล็ดลับ\n"
                "• จดว่า example.co.th ขอคุยสัปดาห์หน้า\n• หยุด / ทำงานต่อ")
    if action == "resume":
        execs.set_paused(False)
        return "กลับมาทำงานแล้ว · " + execs.mode_label()
    if action == "pause":
        execs.set_paused(True)
        return "หยุดฉุกเฉินแล้ว — ทุกเรื่องรอคุณอนุมัติ พิมพ์ \"ทำงานต่อ\" เมื่อพร้อม"
    if action == "audit":
        site = L.normalize_url(args.get("url") or "")
        if not site:
            return "อ่านที่อยู่เว็บไม่ออก ลองพิมพ์แบบ example.co.th"
        dom = L.domain_of(site)
        lead = db.find_lead_by_domain(dom)
        lid = lead["id"] if lead else db.add_lead(site, dom, source="manual")
        token = pipeline.run_lead_audit(lid, site)
        deep_audit.start(lid)
        sc = L.score_audit(json.loads(db.get_lead(lid)["audit_json"]))
        return (f"ตรวจ {dom} แล้ว: ตก {sc['n_fail']} ข้อ · โอกาส {sc['level']} {sc['score']}/100\nรายงาน (รายงานเต็มเสร็จในราว 1 นาที): "
                f"{pipeline.public_base()}/audit/{token}\nจะให้ทักไหม พิมพ์ \"ทัก {dom}\"")
    if action == "note":
        lead, err = _need_lead(args)
        if err:
            return err
        db.add_lead_event(lead["id"], "note", (args.get("text") or "")[:500], "owner")
        return f"จดในไทม์ไลน์ {lead['domain']} แล้ว"
    if action == "lead_info":
        lead, err = _need_lead(args)
        if err:
            return err
        ev = db.list_lead_events(lead["id"], 5)
        qs = db.list_quotes(lead["id"])
        return (f"{lead['domain']} · {L.STATUS_LABEL.get(lead['status'], lead['status'])} · โอกาส {lead['score'] or '—'}"
                + (f"\nใบเสนอราคา {qs[0]['number']}: {qs[0]['status']}" if qs else "")
                + "\nล่าสุด: " + " · ".join(f"{e['created_at'][5:16].replace('T', ' ')} {L.EVENT_LABEL.get(e['kind'], e['kind'])}" for e in ev)
                + f"\n{pipeline.public_base()}/admin/leads/{lead['id']}")
    if action == "contact_lead":
        return owner_contact(args)
    if action == "quote":
        lead, err = _need_lead(args)
        if err:
            return err
        from . import quotes as Q
        if any(q["status"] in Q.OPEN for q in db.list_quotes(lead["id"])):
            return f"{lead['domain']} มีใบเสนอราคาที่ยังเปิดอยู่แล้ว — ดูที่ {pipeline.public_base()}/admin/leads/{lead['id']}"
        q = pipeline.create_quote(lead, args.get("package") or "pro", float(args.get("discount_pct") or 0), created_by="owner")
        res = pipeline.send_quote(q, "owner (สั่งผ่าน CEO)")
        return f"{res}\n{pipeline.quote_url(q)}"
    if action == "followup":
        lead, err = _need_lead(args)
        if err:
            return err
        why = followup.eligible(lead)
        if why:
            return f"ติดตาม {lead['domain']} ไม่ได้ — {why}"
        h = followup.history(lead["id"])
        if h["replied"] or h["n"] >= len(followup.STEPS):
            return f"{lead['domain']} {'ตอบกลับแล้ว' if h['replied'] else 'ติดตามครบ 3 ครั้งแล้ว'}"
        day, kind = followup.STEPS[h["n"]]
        did = followup.propose(lead, {"step": h["n"] + 1, "kind": kind, "day": day})
        d = db.get_decision(did)
        if d["status"] == "proposed":
            result = pipeline.execute_decision(d, "owner")
            db.set_decision(did, "executed", "owner (สั่งผ่าน CEO)", result)
            return result
        return d["result"] or "ส่งแล้ว"
    if action == "ai_report":
        lead, err = _need_lead(args)
        if err:
            return err
        deep_audit.start(lead["id"], ai=True, force_ai=True)
        return f"เริ่มถาม ChatGPT 20 คำถามเรื่อง {lead['domain']} แล้ว ราว 3 นาที ดูผลที่ {pipeline.result_url(lead)}#ai"
    if action == "find_leads":
        seg, area, n = args["segment"].strip(), (args.get("area") or "").strip(), int(args["n"])
        queries = [f"{seg} {area}".strip()] if area else [f"{seg} {a}" for a in prospecting.AREAS[:2]]

        def job():
            res = prospecting.run(limit=n, queries=queries, force=True)
            try:
                from . import line_oa
                line_oa.notify_owner(f"หาลูกค้า \"{seg}\" เสร็จ: เพิ่ม {res.get('added', 0)} ราย · ตรวจ {res.get('audited', 0)} · ทัก {res.get('proposed', 0)}"
                                     + (f"\n{', '.join(res.get('leads', [])[:8])}" if res.get("leads") else ""))
            except Exception:
                pass
        _bg(job)
        return f"เริ่มหา \"{' '.join(queries)}\" {n} ราย แล้ว ราว {n // 2 + 1} นาที เสร็จแล้วจะแจ้งใน LINE"
    if action == "post":
        from . import post_studio as PS
        out = []
        for ch in marketing.channels():
            c = PS.make(args["kind"], ch, marketing.audit_link(marketing.SRC.get(ch, ch)), marketing._base())
            if not c:
                out.append(f"{ch}: ยังไม่มีข้อมูลพอสำหรับโพสต์แบบนี้")
                continue
            pid = db.add_post(ch, args["kind"], c.get("content_id"), c["title"], c["text"], c.get("link"))
            if c.get("image"):
                db.update_post(pid, image=c["image"])
            try:
                out.append(marketing.publish_post(db.get_post(pid)))
            except Exception as e:
                out.append(f"{ch}: โพสต์ไม่สำเร็จ ({str(e)[:80]})")
        return "\n".join(out) or "ยังไม่มีช่องทางโพสต์"
    return "ยังทำงานนี้ไม่ได้"


# ---------- งานที่ต้องคิดเพิ่ม ----------
def owner_contact(args) -> str:
    """เจ้าของสั่งทัก = ทักแม้ CEO จะคัดว่าโอกาสต่ำ (เจ้าของรู้บริบทมากกว่า) · ไม่มีลีด → ตรวจเว็บก่อน · ไม่มีช่องทาง → หาจากเว็บ ไม่เจอให้ร่างไปส่งเอง"""
    from . import pipeline, prospecting, closer
    lead = _find_lead(args.get("domain") or "")
    if not lead:
        site = L.normalize_url(args.get("domain") or "")
        if not site:
            return "อ่านโดเมนไม่ออก ลองพิมพ์แบบ example.co.th"
        lid = db.add_lead(site, L.domain_of(site), source="manual")
        lead = db.get_lead(lid)
    if lead["status"] == "unsubscribed":
        return f"{lead['domain']} ขอไม่รับการติดต่อไว้แล้ว ทักไม่ได้"
    if lead["status"] == "lost":
        db.update_lead(lead["id"], status="new")
        pipeline.record(lead["id"], "note", "เจ้าของสั่งเปิดลีดใหม่และให้ทัก", "owner")
    if not lead["audit_json"]:
        pipeline.run_lead_audit(lead["id"], lead["site_url"])
    lead = db.get_lead(lead["id"])
    found = ""
    if not pipeline.send_channel(lead):
        try:
            c = prospecting.find_contact(lead["site_url"])
            if c.get("email"):
                db.update_lead(lead["id"], email=c["email"])
                found = f"เจออีเมล {c['email']} บนเว็บ · "
            ph = closer.find_phone(db.get_lead(lead["id"]))
            if ph:
                d = closer._data(db.get_lead(lead["id"])); d["phone"] = ph; closer._save(lead["id"], d)
                found += f"เบอร์ {closer.fmt_phone(ph)} · "
        except Exception:
            pass
        lead = db.get_lead(lead["id"])
    report = json.loads(lead["audit_json"] or "{}")
    draft = execs.draft_outreach(lead, L.public_summary(report), pipeline.result_url(lead, "mail"))
    ch = pipeline.send_channel(lead)
    pl = {"subject": draft["subject"], "body": draft["body"], "channel": ch, "by_owner": True}
    did = db.add_decision("ceo", "outreach", f"ทัก {lead['domain']} (เจ้าของสั่ง)", f"หัวข้อ: {draft['subject']}\n\n{draft['body']}", pl, lead_id=lead["id"])
    sc = L.score_audit(report)
    note = f" · หมายเหตุ: เว็บนี้ผ่านทุกข้อที่ตรวจ ข้อความจึงเน้นเรื่องทำให้ AI เอ่ยถึง" if not sc["n_fail"] and sc["level"] != "เข้าเว็บไม่ได้" else ""
    if ch:
        result = pipeline.execute_decision(db.get_decision(did), "owner")
        db.set_decision(did, "executed", "owner (สั่งผ่าน CEO)", result)
        return found + result + note
    db.set_decision(did, "executed", "owner (สั่งผ่าน CEO)", "ไม่มีช่องทางส่งอัตโนมัติ — ส่งร่างให้เจ้าของ")
    phone = closer._data(lead).get("phone")
    return (found + f"ยังไม่มีอีเมล/LINE ของ {lead['domain']} ที่ระบบส่งเองได้" + (f" · โทร {closer.fmt_phone(phone)}" if phone else "")
            + f"\nร่างไว้ให้คัดลอกไปส่งเอง:\n\nหัวข้อ: {draft['subject']}\n{draft['body']}\n\nเติมอีเมลที่ {pipeline.public_base()}/admin/leads/{lead['id']} แล้วสั่ง \"ทัก {lead['domain']}\" อีกครั้งได้" + note)


def explain(args) -> str:
    """ทำไมระบบทำแบบนั้น: สถานะลีด + การตัดสินใจล่าสุดพร้อมเหตุผลจริง"""
    lead, err = _need_lead(args)
    if err:
        return "อธิบายเรื่องไหนครับ — พิมพ์โดเมนด้วย เช่น \"ทำไมปิด example.co.th\""
    out = [f"{lead['domain']} ตอนนี้: {L.STATUS_LABEL.get(lead['status'], lead['status'])} · คะแนนโอกาส {lead['score'] if lead['score'] is not None else '—'}/100"]
    for d in [x for x in db.list_decisions(None, 300) if x["lead_id"] == lead["id"]][:3]:
        try:
            pl = json.loads(d["payload_json"] or "{}")
        except Exception:
            pl = {}
        why = pl.get("reason") or (pl.get("cfo") or {}).get("note") or ""
        out.append(f"• {d['created_at'][5:16].replace('T', ' ')} {d['title']} → {d['status']}" + (f" · เหตุผล: {why}" if why else "") + (f" · ผล: {d['result']}" if d["result"] else ""))
    if lead["status"] == "lost":
        out.append(f"ถ้าอยากให้ทักอยู่ดี พิมพ์ \"ทัก {lead['domain']}\" — คำสั่งคุณชนะการคัดอัตโนมัติ")
    return "\n".join(out)


def answer(question: str, llm=None) -> str:
    """คำถามทั่วไป → ตอบจากข้อมูลจริงในระบบ ไม่ลงมือทำอะไร · โมเดลใช้ไม่ได้ → บอกตรง ๆ ว่าไม่เข้าใจ"""
    llm = llm or LLM
    k = execs.kpis()
    f = L.funnel(db.list_leads(None, 5000))
    ctx = (f"สายดีล: {json.dumps(f['n'], ensure_ascii=False)} · ด่าน: " + ", ".join(f"{r['label']} {r['value']}/{r['target']}" for r in k["rows"])
           + "\nบทสนทนาล่าสุด:\n" + "\n".join(f"{m['role']}: {m['text'][:200]}" for m in log()[-8:]))
    try:
        if llm is None:
            from . import ai_client
            llm = lambda m: ai_client.chat_for("sales", m, max_tokens=1200, json_mode=True)
        raw = llm([{"role": "user", "content": f"คุณคือ CEO เอเจนต์ของเจอ.AI คุยกับเจ้าของ ตอบสั้น ตรง ภาษาไทย ไม่เกิน 80 คำ ใช้เฉพาะข้อมูลนี้:\n{ctx}\n\nเจ้าของถาม: {question}\n"
                    "ถ้าเป็นคำสั่งที่ต้องลงมือ ให้บอกว่าพิมพ์คำสั่งอะไร (เช่น \"ทัก example.co.th\") อย่าบอกว่าทำแล้ว ตอบ JSON {\"reply\": \"...\"}"}])
        mm = re.search(r"\{[\s\S]*\}", raw or "")
        r = str((json.loads(mm.group(0)) if mm else {}).get("reply") or "").strip()
        if r:
            return r
    except Exception:
        pass
    return "ยังไม่แน่ใจว่าหมายถึงอะไรครับ ลองพิมพ์สั้น ๆ เช่น \"สรุปวันนี้\" \"ทัก example.co.th\" หรือ \"ช่วย\" เพื่อดูคำสั่งทั้งหมด"


# ---------- ทางเข้า ----------
def handle(text: str, who: str = "owner", llm=None) -> str:
    """ข้อความจากเจ้าของ → คำตอบของ CEO (ทำงานจริงตามคำสั่ง)"""
    text = (text or "").strip()
    if not text:
        return ""
    _log("owner", text)
    with office.working("ceo", f"รับคำสั่งเจ้าของ: {text[:40]}"):
        p = _pending()
        if p and CONFIRM_WORDS.match(text):
            _set_pending(None)
            try:
                reply = execute(p["action"], p["args"])
            except Exception as e:
                reply = f"ทำไม่สำเร็จ: {type(e).__name__}: {str(e)[:120]}"
        elif p and CANCEL_WORDS.match(text):
            _set_pending(None)
            reply = "ยกเลิกแล้ว"
        else:
            cmd = parse(text, llm)
            msg, need = plan(cmd["action"], cmd["args"])
            needs_confirm = ACTIONS[cmd["action"]][2]
            if needs_confirm and need:
                _set_pending({"action": cmd["action"], "args": cmd["args"], "at": db.now()})
                reply = msg + "\nพิมพ์ \"ยืนยัน\" เพื่อทำ หรือ \"ยกเลิก\""
            elif needs_confirm and not need:
                reply = msg or "ยังทำงานนี้ไม่ได้"
            else:
                try:
                    reply = msg if (msg and need is None and cmd["action"] in ("contact_lead", "quote", "followup", "ai_report")) else execute(cmd["action"], cmd["args"])
                except Exception as e:
                    reply = f"ทำไม่สำเร็จ: {type(e).__name__}: {str(e)[:120]}"
    _log("ceo", reply)
    return reply
