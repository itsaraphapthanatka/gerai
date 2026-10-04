"""ถาม-ตอบจากตัวเลขของแบรนด์ — "เดือนนี้ดีขึ้นไหม", "หน้าไหนยังไม่ติด index", "ทำไม AI ไม่พูดถึงเรา"

ใช้โมเดลเดียวกับที่เขียนคอนเทนต์ (ai_client) แต่บังคับให้ตอบจาก digest ของข้อมูลจริงเท่านั้น — รายงาน
(report.compose) ย่อเป็นข้อความสั้น ๆ ใส่ไปใน system prompt ทุกครั้ง โมเดลจึงอ้างตัวเลข/วันที่ที่มีจริง
และต้องบอกว่า "ไม่มีข้อมูล" เมื่อยังไม่ได้รันตัววัดนั้น แทนการเดา
digest()/build_messages() เป็น pure — เทสต์ได้โดยไม่เรียกโมเดล
"""
from __future__ import annotations

SYSTEM = (
    "คุณคือผู้ช่วยวิเคราะห์ของเจอ.AI (แพลตฟอร์ม GEO) ตอบคำถามเกี่ยวกับผลลัพธ์ของแบรนด์จาก 'ข้อมูล' ด้านล่างเท่านั้น "
    "ตอบภาษาไทย กระชับ เป็นข้อ ๆ เมื่อเหมาะ อ้างตัวเลขและวันที่ตามข้อมูลจริง ห้ามแต่งตัวเลข "
    "ถ้าข้อมูลไม่มีให้บอกว่ายังไม่มีและควรรันตัววัดไหนในระบบ (มอนิเตอร์ SoV / อันดับ Google / มองเห็นบน AI / Search Console / "
    "สุขภาพเว็บ / ความเร็วเว็บ / Google AI Overview) · ปิดท้ายด้วยสิ่งที่ควรทำต่อ 1–2 ข้อถ้าคำถามเกี่ยวกับการปรับปรุง"
)
HISTORY_TURNS = 6
MAX_TOKENS = 700
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
    return "\n".join(L)


def build_messages(data: dict, history: list, question: str) -> list:
    """system (กติกา + digest) + ประวัติล่าสุด + คำถาม — ประวัติจำกัด HISTORY_TURNS ไม่ให้ prompt บวม"""
    msgs = [{"role": "system", "content": SYSTEM + "\n\n=== ข้อมูล ===\n" + digest(data)}]
    # กรองก่อนค่อยตัด — ไม่งั้นแถวขยะ (role แปลก/ว่าง) กินโควตาประวัติที่ควรเป็นบทสนทนาจริง
    turns = [m for m in (history or []) if m.get("role") in ("user", "assistant") and m.get("content")]
    for m in turns[-HISTORY_TURNS:]:
        msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role": "user", "content": question.strip()})
    return msgs


def answer(data: dict, history: list, question: str, llm=None) -> str:
    """ถามโมเดล — llm ฉีดได้สำหรับเทสต์ · ดีฟอลต์ ai_client._chat (OpenAI-compatible)"""
    if llm is None:
        from . import ai_client
        llm = ai_client._chat
    return (llm(build_messages(data, history, question), max_tokens=MAX_TOKENS) or "").strip()
