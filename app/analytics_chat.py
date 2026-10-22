"""ถาม-ตอบจากตัวเลขของแบรนด์ — "เดือนนี้ดีขึ้นไหม", "หน้าไหนยังไม่ติด index", "ทำไม AI ไม่พูดถึงเรา"

ใช้โมเดลเดียวกับที่เขียนคอนเทนต์ (ai_client) แต่บังคับให้ตอบจาก digest ของข้อมูลจริงเท่านั้น — รายงาน
(report.compose) ย่อเป็นข้อความสั้น ๆ ใส่ไปใน system prompt ทุกครั้ง โมเดลจึงอ้างตัวเลข/วันที่ที่มีจริง
และต้องบอกว่า "ไม่มีข้อมูล" เมื่อยังไม่ได้รันตัววัดนั้น แทนการเดา
digest()/build_messages() เป็น pure — เทสต์ได้โดยไม่เรียกโมเดล
"""
from __future__ import annotations

import re

SYSTEM = (
    "คุณคือผู้ช่วยวิเคราะห์ของเจอ.AI (แพลตฟอร์ม GEO) ตอบคำถามเกี่ยวกับผลลัพธ์ของแบรนด์จาก 'ข้อมูล' ด้านล่างเท่านั้น "
    "ตอบภาษาไทย กระชับ เป็นข้อ ๆ เมื่อเหมาะ อ้างตัวเลขและวันที่ตามข้อมูลจริง ห้ามแต่งตัวเลข "
    "ถ้าข้อมูลไม่มีให้บอกว่ายังไม่มีและควรรันตัววัดไหนในระบบ (มอนิเตอร์ SoV / อันดับ Google / มองเห็นบน AI / Search Console / "
    "สุขภาพเว็บ / ความเร็วเว็บ / Google AI Overview) · ปิดท้ายด้วยสิ่งที่ควรทำต่อ 1–2 ข้อถ้าคำถามเกี่ยวกับการปรับปรุง\n"
    "กติกาเพิ่ม: "
    "(1) แต่ละตัวเลขมีวันที่ของตัวเอง — อ้างวันที่ของตัววัดนั้น ห้ามเอาวันที่ของตัววัดอื่นมาใช้แทน "
    "(2) ส่วน 'ข้อสรุปจากตัวเลข' ระบบคำนวณจากข้อมูลจริงให้แล้ว — ถ้าคำถามเกี่ยวกับ index / AI / Google AI Overview / ทำไมผลไม่ดี "
    "ส่วนสาเหตุของคำตอบต้องครอบคลุมทุกบรรทัดของ 'ข้อสรุปจากตัวเลข' บรรทัดละ 1 ข้อ และทุกส่วนย่อยในบรรทัดนั้น (เช่น index · ความสด · เทคนิค / ผู้ช่วย AI · Google AI Overview) "
    "ใช้ตัวเลขและหน่วย (คำตอบ/คำถาม/หน้า) ตามที่เขียนไว้ ห้ามขัดแย้งหรือข้าม "
    "(3) วิธีแก้ตามกลุ่ม index: 'เห็นแล้วยังไม่อ่าน' → ขอ index ราย URL ใน URL Inspection + ลิงก์ภายในจากหน้าแรก/เมนู · "
    "'Google ยังไม่รู้จัก' → เช็ค sitemap/ลิงก์ภายใน · 'อ่านแล้วไม่เก็บ' → ปรับเนื้อหาไม่ให้บาง/ซ้ำ · 'ถูกบล็อก' → แก้ robots/noindex"
)
HISTORY_TURNS = 6
MAX_TOKENS = 900
CHAT_TIMEOUT = 180   # คำตอบยาวขึ้นหลังเพิ่มข้อสรุป วัดจริง 55–115 วิ — 120 ของดีฟอลต์เคยหลุด
SUGGESTIONS = [
    "เดือนนี้แบรนด์ดีขึ้นหรือแย่ลง เพราะอะไร",
    "หน้าคอนเทนต์ไหนยังไม่อยู่ใน index ของ Google",
    "ทำไม AI ยังไม่พูดถึงแบรนด์ ควรทำอะไรก่อน",
    "คำค้นอะไรที่คนเจอเราบน Google",
    "สุขภาพเว็บมีอะไรต้องแก้ไหม",
]


def digest(d: dict) -> str:
    """ย่อรายงาน (report.compose) เป็นข้อความสำหรับโมเดล — เฉพาะตัวเลขที่มี ไม่มี = บอกว่าไม่มี"""
    b, L = d.get("brand") or {}, []
    L.append(f"แบรนด์: {b.get('name')} · เว็บ {d.get('site')} · ตลาด: {b.get('market') or '-'} · คำถามเป้าหมาย {d.get('n_questions', 0)} ข้อ")
    L.append(f"ช่วงรายงาน: {d['period']['from']} ถึง {d['period']['to']} ({d['period']['days']} วัน) · สร้างเมื่อ {d.get('generated_at')}")
    s = d.get("sov") or {}
    if s.get("latest"):
        lt = s["latest"]
        line = f"SoV (แบรนด์โผล่ในผลค้นเว็บ): {lt['sov']}% ({lt['hits']}/{lt['total']} คำถาม) เมื่อ {lt['at']}"
        if s.get("previous"):
            line += f" · รอบก่อนช่วงรายงาน {s['previous']['sov']}% ({s['previous']['at']}) → เปลี่ยน {s['delta']:+d} จุด"
        if s.get("series"):
            line += " · ประวัติ: " + ", ".join(f"{a} {v}%" for a, v in s["series"])
        L.append(line)
    else:
        L.append("SoV: ยังไม่เคยรันมอนิเตอร์")
    r = d.get("rank") or {}
    if r.get("latest"):
        lt = r["latest"]
        line = f"อันดับ Google: ติดหน้าแรก-สอง {lt['ranked']}/{lt['total']} คำถาม" + (f" อันดับเฉลี่ย {lt['avg_pos']}" if lt.get("avg_pos") else "") + f" เมื่อ {lt['at']}"
        if r.get("previous"):
            line += f" · รอบก่อน {r['previous']['ranked']}/{r['previous']['total']}"
        if r.get("rows"):
            line += " · รายคำถาม: " + "; ".join(f"{x['question'][:40]} → {('#' + str(x['position'])) if x.get('position') else 'ไม่ติด'}" for x in r["rows"][:8])
        L.append(line)
    else:
        L.append("อันดับ Google: ยังไม่เคยเช็ค")
    a = d.get("ai")
    if a and a.get("asked"):
        L.append(f"ผู้ช่วย AI (ChatGPT/Perplexity/Gemini): ถูกพูดถึง {a['rate']}% จาก {a['asked']} คำตอบ — อ้างอิงเว็บเรา {a['cited']}, เอ่ยชื่อ {a['named']} เมื่อ {a['at']}"
                 + (" · " + ", ".join(f"{e['label']} {e['cited']}+{e['named']}/{e['asked']}" for e in a.get("engines") or []) if a.get("engines") else ""))
    else:
        L.append("ผู้ช่วย AI: ยังไม่มีผล (ยังไม่ได้ถามหรือยังไม่มีคีย์)")
    g = d.get("gsc")
    if g and g.get("linked"):
        L.append(f"Search Console ({g['at']}): หน้าคอนเทนต์อยู่ใน index {g['indexed']}/{g['inspected']} (Google ยังไม่รู้จัก {g['unknown']}, เห็นแล้วยังไม่อ่าน {g['discovered']}, อ่านแล้วไม่เก็บ {g['crawled']}, ถูกบล็อก {g['blocked']}) · "
                 f"28 วัน: {g['clicks']} คลิก / {g['impressions']} impressions ทั้งเว็บ (หน้าคอนเทนต์ {g['content_clicks']} คลิก)"
                 + (f" อันดับเฉลี่ย {g['position']}" if g.get("position") else "") + f" · sitemap {'ส่งแล้ว' if g.get('sitemap_submitted') else 'ยังไม่ได้ส่ง'}"
                 + (" · คำค้นที่คนเจอเรา: " + "; ".join(f"{q['query']} ({q['clicks']} คลิก/{q['impressions']} impr)" for q in g.get("top_queries") or []) if g.get("top_queries") else ""))
    elif g:
        L.append(f"Search Console: ยังไม่ได้เชื่อม — {g.get('reason') or ''}")
    else:
        L.append("Search Console: ยังไม่เคยซิงค์")
    h = d.get("health")
    if h:
        L.append(f"สุขภาพเว็บ ({h['at']}): " + ("ผ่านทุกข้อ" if h["ok"] else f"ไม่ผ่าน {h['n_fail']} ข้อ: " + ", ".join(h["fails"])) + (f" · ควรดู: {', '.join(h['warns'])}" if h.get("warns") else ""))
    else:
        L.append("สุขภาพเว็บ: ยังไม่เคยตรวจ")
    sp = d.get("speed")
    if sp:
        L.append(f"ความเร็ว ({sp['at']}): มือถือ {sp['mobile'] if sp['mobile'] is not None else 'ไม่มีผล'} · เดสก์ท็อป {sp['desktop'] if sp['desktop'] is not None else 'ไม่มีผล'}"
                 + (f" · ควรแก้: " + "; ".join(f"{t} ({ms} ms)" for t, ms in sp.get("top_fix") or []) if sp.get("top_fix") else "") + (f" · ผิดพลาด: {sp['error']}" if sp.get("error") else ""))
    else:
        L.append("ความเร็วเว็บ: ยังไม่เคยสแกน")
    se = d.get("serp")
    if se:
        L.append(f"Google AI Overview ({se['at']}): พูดถึงเรา {se['overview_hit']}/{se['overview_shown']} (Google ไม่แสดง {se['overview_none']} ข้อ) · AI Mode {se['mode_hit']}/{se['mode_shown']}")
    else:
        L.append("Google AI Overview / AI Mode: ยังไม่มีผล")
    c = d.get("content") or {}
    L.append(f"คอนเทนต์: ทั้งหมด {c.get('total', 0)} · เผยแพร่แล้ว {c.get('published', 0)} · เผยแพร่ในช่วงรายงาน {c.get('published_period', 0)} · ร่าง {c.get('drafts', 0)} · ล่าสุด {c.get('latest') or '-'}"
             + (" · ใหม่: " + "; ".join(f"{x['title'][:40]} ({x['at']})" for x in c.get("recent") or []) if c.get("recent") else ""))
    gp = d.get("gaps") or {}
    L.append(f"ช่องว่างคอนเทนต์: {gp.get('critical', 0)} คำถามยังไม่มีคอนเทนต์ จากช่องว่างทั้งหมด {gp.get('total', 0)}")
    if d.get("next_steps"):
        L.append("สิ่งที่ระบบแนะนำ: " + " | ".join(d["next_steps"]))
    # ข้อสรุปขึ้นก่อนตัวเลขดิบ — วางท้ายแล้วโมเดลข้ามบางข้อบ่อย
    F = findings(d)
    if F:
        L[2:2] = ["ข้อสรุปจากตัวเลข (ระบบคำนวณแล้ว — ใช้ตามนี้ ทุกข้อ):"] + ["- " + f for f in F] + ["ตัวเลขดิบ:"]
    return "\n".join(L)


def facts(d: dict) -> list:
    """ข้อเท็จจริงที่คำนวณจากตัวเลขให้เลย — [(กลุ่ม, regex ที่คำตอบต้องมีถ้าพูดถึงเรื่องนี้, ข้อความ)]
    โมเดลแค่ยกไปใช้ ไม่ต้องอนุมานเอง · regex ใช้ตรวจหลังตอบว่าข้อไหนหล่น (ดู answer)"""
    F = []
    g, c, h = d.get("gsc") or {}, d.get("content") or {}, d.get("health")
    if g.get("linked") and g.get("inspected") and g["indexed"] < g["inspected"]:
        groups = [(g.get("discovered", 0), "เห็นแล้วยังไม่อ่าน (Google รู้ว่ามีหน้าแต่ยังไม่ crawl)"), (g.get("unknown", 0), "Google ยังไม่รู้จัก (ยังไม่เจอหน้าเลย)"),
                  (g.get("crawled", 0), "อ่านแล้วไม่เก็บ (มองว่าเนื้อหาบาง/ซ้ำ)"), (g.get("blocked", 0), "ถูกบล็อก (robots/noindex)")]
        groups = sorted([x for x in groups if x[0]], key=lambda x: -x[0])
        line = f"ยังไม่อยู่ใน index {g['inspected'] - g['indexed']} จาก {g['inspected']} หน้า"
        if groups:
            main = groups[0][1].split(" (")[0]
            line += " — แยกเป็น " + " · ".join(f"{t} {n} หน้า" for n, t in groups) + f" → กลุ่มหลักคือ '{main}'"
        F.append(("index", groups[0][1].split(" (")[0] if groups else "index", line))
        if c.get("published_fresh"):
            F.append(("index", "สัปดาห์", f"ความสด: {c['published_fresh']} หน้าเพิ่งเผยแพร่ภายใน 14 วันล่าสุด หน้าใหม่รอ index 1–4 สัปดาห์เป็นเรื่องปกติ"))
        if not g.get("blocked") and h and h.get("ok"):
            F.append(("index", "เทคนิค", "เทคนิค: ไม่มีหน้าถูกบล็อกและสุขภาพเว็บผ่านทุกข้อ จึงไม่ใช่ปัญหาเทคนิค"))
    a = d.get("ai")
    if a and a.get("asked"):
        eng = a.get("engines") or []
        unit = f" ({eng[0]['asked']} คำถาม × {len(eng)} ผู้ช่วย)" if eng and len({e.get("asked") for e in eng}) == 1 else ""
        F.append(("ai", "คำตอบ", f"ผู้ช่วย AI ({a['at']}): อ้างอิง/เอ่ยชื่อเรา {(a.get('cited') or 0) + (a.get('named') or 0)} จาก {a['asked']} คำตอบ{unit}"))
    se = d.get("serp")
    if se:
        asked = (se.get("overview_shown") or 0) + (se.get("overview_none") or 0)
        if se.get("overview_shown") and not se.get("overview_hit"):
            F.append(("ai", "Overview", f"Google AI Overview ({se['at']}): Google แสดง Overview {se['overview_shown']}/{asked} คำถาม แต่ไม่มีข้อไหนพูดถึงเรา (Overview มีอยู่ แต่เลือกอ้างเว็บอื่น)"))
        elif se.get("overview_hit"):
            F.append(("ai", "Overview", f"Google AI Overview ({se['at']}): พูดถึงเรา {se['overview_hit']} จาก {se['overview_shown']} ข้อที่ Google แสดง Overview"))
        if se.get("overview_none"):
            F.append(("ai", "ไม่แสดง", f"Google ไม่แสดง Overview เลย {se['overview_none']}/{asked} คำถาม (ไม่มีพื้นที่ให้ถูกอ้าง)"))
    if a and a.get("asked") and not (a.get("cited") or a.get("named")):
        # เขียนเป็นข้อเท็จจริงตรง ๆ — เคยใช้ "สอดคล้องกับ" แล้วโมเดลกลับเป็น "ไม่สอดคล้อง" ความหมายพลิก
        rk = d.get("rank") or {}
        if rk.get("latest"):
            lt = rk["latest"]
            hit = [r for r in rk.get("rows") or [] if r.get("position")]
            name = ((d.get("brand") or {}).get("name") or "").lower()
            only_brand = bool(hit) and bool(name) and all(name in (r.get("question") or "").lower() for r in hit)
            F.append(("out", re.escape(f"{lt['ranked']}/{lt['total']}"), f"อันดับ Google ({lt['at']}): ติดหน้าแรก-สองแค่ {lt['ranked']}/{lt['total']} คำถาม"
                      + (" และที่ติดมีแต่คำถามที่มีชื่อแบรนด์ — คำถามเชิงบริการยังไม่ติดเลย" if only_brand else "")))
        s = d.get("sov") or {}
        sv = s.get("latest")
        if sv:
            # แยกเป็นข้อของตัวเอง — ฝังในข้อ "นอกเว็บ" แล้วโมเดลข้าม SoV ได้โดยที่ regex "ภายนอก" ยังผ่าน
            ser = s.get("series") or []
            flat = len(ser) > 1 and all(v == sv["sov"] for _, v in ser)
            F.append(("out", r"SoV|Share of Voice|ส่วนแบ่ง", f"SoV ({sv['at']}): แบรนด์โผล่ในผลค้นเว็บ {sv['sov']}% ({sv['hits']}/{sv['total']} คำถาม)"
                      + (f" — เท่าเดิมทุกรอบตั้งแต่ {ser[0][0]} ({len(ser)} รอบ)" if flat else "")))
        parts = []
        if g.get("linked"):
            parts.append(f"หน้าคอนเทนต์ได้ {g.get('content_clicks', 0)} คลิก จาก {g.get('clicks', 0)} คลิกทั้งเว็บใน 28 วัน"
                         + (" (คลิกมาจากหน้าหลัก/การค้นชื่อแบรนด์ ไม่ใช่คอนเทนต์)" if g.get("clicks") and not g.get("content_clicks") else ""))
        F.append(("out", "ภายนอก|Google Business Profile", "นอกเว็บ: " + (" · ".join(parts) + " — ตัวเลขเหล่านี้ต่ำเหมือนกับผลของ AI แปลว่า" if parts else "")
                  + "แบรนด์ยังแทบไม่ถูกพูดถึงที่ไหนนอกจากการค้นชื่อตรง ๆ · ติด index อย่างเดียวไม่พอ AI มักอ้างแหล่งภายนอก (เว็บลิสติ้ง/ไดเรกทอรี/Google Business Profile) แบรนด์ต้องถูกพูดถึงนอกเว็บตัวเองด้วย"))
    return F


def findings(d: dict) -> list:
    """facts รวมเป็นบรรทัดตามกลุ่ม (index / AI / นอกเว็บ) — โมเดล 12B ตอบสาเหตุราว 4 ข้อ ถ้าแยกบรรทัดย่อยจะหล่นบางข้อ"""
    label = {"index": "index: ", "ai": "AI: ", "out": ""}
    out = {}
    for grp, _, text in facts(d):
        out.setdefault(grp, []).append(text)
    return [label[k] + " · ".join(v) for k, v in out.items()]


def build_messages(data: dict, history: list, question: str) -> list:
    """system (กติกา + digest) + ประวัติล่าสุด + คำถาม — ประวัติจำกัด HISTORY_TURNS ไม่ให้ prompt บวม"""
    msgs = [{"role": "system", "content": SYSTEM + "\n\n=== ข้อมูล ===\n" + digest(data)}]
    # กรองก่อนค่อยตัด — ไม่งั้นแถวขยะ (role แปลก/ว่าง) กินโควตาประวัติที่ควรเป็นบทสนทนาจริง
    turns = [m for m in (history or []) if m.get("role") in ("user", "assistant") and m.get("content")]
    for m in turns[-HISTORY_TURNS:]:
        msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role": "user", "content": question.strip()})
    return msgs


# คำถามแบบไหนที่ต้องมีข้อเท็จจริงครบ — ถามเรื่องอื่น (เช่นคำค้น/ความเร็ว) ไม่ต้องเติม
_NEEDS_FACTS = re.compile(r"index|อินเด็กซ์|AI|เอไอ|Overview|ไม่พูดถึง|ไม่ติด|ทำไม|แย่|ปรับปรุง|ควรทำ", re.I)


def missing_facts(d: dict, question: str, reply: str) -> list:
    """ข้อเท็จจริงที่โมเดลตอบหล่น (regex ของข้อนั้นไม่โผล่ในคำตอบ) — เฉพาะคำถามที่เกี่ยว"""
    if not _NEEDS_FACTS.search(question or ""):
        return []
    return [text for _, rx, text in facts(d) if not re.search(rx, reply or "")]


def answer(data: dict, history: list, question: str, llm=None) -> str:
    """ถามโมเดล — llm ฉีดได้สำหรับเทสต์ · ดีฟอลต์ ai_client._chat (OpenAI-compatible)
    โมเดลเล็กทำตามกติกา 'ยกข้อสรุปให้ครบ' ไม่สม่ำเสมอ → ข้อที่หล่นเติมท้ายคำตอบตรง ๆ จากตัวเลข"""
    if llm is None:
        from . import ai_client
        llm = lambda m, max_tokens: ai_client._chat(m, max_tokens=max_tokens, timeout=CHAT_TIMEOUT)
    reply = (llm(build_messages(data, history, question), max_tokens=MAX_TOKENS) or "").strip()
    miss = missing_facts(data, question, reply)
    if reply and miss:
        reply += "\n\n**ข้อมูลเพิ่มเติมจากระบบ:**\n" + "\n".join("- " + m for m in miss)
    return reply
