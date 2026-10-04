"""ทดสอบ report.compose / next_steps / csv_bytes และ analytics_chat.digest / build_messages แบบไม่แตะ DB"""
import datetime
import app.report as rp
import app.analytics_chat as ac

today = datetime.date(2026, 10, 4)
brand = {"id": 1, "name": "JKP", "domain": "https://jkppropertyagency.com", "market": "โกดังให้เช่า"}
runs = [{"id": 9, "started_at": "2026-10-01T08:00:00", "status": "done", "share_of_voice": 25.0, "brand_hits": 2, "questions_total": 8},
        {"id": 8, "started_at": "2026-09-20T08:00:00", "status": "done", "share_of_voice": 12.5, "brand_hits": 1, "questions_total": 8},
        {"id": 7, "started_at": "2026-08-30T08:00:00", "status": "done", "share_of_voice": 0.0, "brand_hits": 0, "questions_total": 8},
        {"id": 6, "started_at": "2026-08-01T08:00:00", "status": "running", "share_of_voice": None}]
rank = [{"checked_at": "2026-10-01T09:00:00", "total": 8, "ranked": 3, "avg_pos": 6.33}, {"checked_at": "2026-09-24T09:00:00", "total": 8, "ranked": 1, "avg_pos": 9.0}]
rank_rows = [{"question": "โกดังให้เช่า บางนา", "position": 4, "url": "https://jkppropertyagency.com/x"}, {"question": "โรงงานให้เช่า", "position": None, "url": None}]
ai = {"checked_at": "2026-10-04T12:00:00", "asked": 16, "cited": 0, "named": 0, "rate": 0, "cost_usd": 0.41,
      "by_engine": {"chatgpt": {"label": "ChatGPT", "asked": 8, "cited": 0, "named": 0}, "claude": {"label": "Claude", "asked": 0, "cited": 0, "named": 0}}}
gsc = {"linked": True, "synced_at": "2026-10-04T12:40:00", "index": {"indexed": 1, "inspected": 22, "unknown": 3, "discovered": 18, "crawled": 0, "blocked": 0},
       "analytics": {"site": {"clicks": 8, "impressions": 168, "position": 6.6}, "content": {"clicks": 0}, "top_queries": [{"query": "jkp property", "clicks": 2, "impressions": 8, "position": 1.0}]},
       "sitemap": {"submitted": True}}
health = {"checked_at": "2026-10-04T13:00:00", "checks": [{"label": "เนื้อหาอยู่ใน HTML ไม่ต้องรอ JavaScript", "status": "fail"}, {"label": "Google เก็บเข้า index แล้ว", "status": "warn"}, {"label": "หน้าแรกเข้าถึงได้", "status": "ok"}]}
speed = {"checked_at": "2026-10-04T14:00:00", "mobile": {"scores": {"performance": 42, "seo": 91}, "opportunities": [{"title": "ลด JS", "savings_ms": 1200}]}, "desktop": {"scores": {"performance": 80}}}
serp = {"checked_at": "2026-10-04T15:00:00", "by_kind": {"overview": {"shown": 5, "cited": 1, "named": 0, "none": 3}, "mode": {"shown": 8, "cited": 0, "named": 2}}}
content = [{"id": 1, "title": "บทความใหม่", "status": "published", "published_at": "2026-09-28T10:00:00"},
           {"id": 2, "title": "บทความเก่า", "status": "published", "published_at": "2026-07-01T10:00:00"},
           {"id": 3, "title": "ร่าง", "status": "draft", "published_at": None}]
gaps = {"critical": 2, "total_gaps": 5}

d = rp.compose(brand, today, runs, rank, ai, gsc, health, speed, serp, content, gaps, 8, "https://jkppropertyagency.com", rank_rows)
assert d["sov"]["latest"]["sov"] == 25 and d["sov"]["previous"]["sov"] == 0 and d["sov"]["delta"] == 25, d["sov"]   # รอบก่อนช่วง = 30 ส.ค. (ก่อน 4 ก.ย.)
assert d["sov"]["series"][0] == ("2026-08-30", 0) and len(d["sov"]["series"]) == 3
assert d["rank"]["latest"] == {"ranked": 3, "total": 8, "avg_pos": 6.3, "at": "2026-10-01"} and d["rank"]["previous"]["ranked"] == 1 and len(d["rank"]["rows"]) == 2
assert d["ai"]["rate"] == 0 and [e["label"] for e in d["ai"]["engines"]] == ["ChatGPT"]           # เจ้าที่ asked=0 ไม่โชว์
assert d["gsc"]["indexed"] == 1 and d["gsc"]["clicks"] == 8 and d["gsc"]["top_queries"][0]["query"] == "jkp property"
assert d["health"] == {"ok": False, "n_fail": 1, "fails": ["เนื้อหาอยู่ใน HTML ไม่ต้องรอ JavaScript"], "warns": ["Google เก็บเข้า index แล้ว"], "at": "2026-10-04"}
assert d["speed"]["mobile"] == 42 and d["speed"]["desktop"] == 80 and d["speed"]["top_fix"] == [("ลด JS", 1200)]
assert d["serp"] == {"overview_hit": 1, "overview_shown": 5, "overview_none": 3, "mode_hit": 2, "mode_shown": 8, "at": "2026-10-04"}
assert d["content"] == {"total": 3, "published": 2, "published_period": 1, "drafts": 1, "latest": "2026-09-28", "recent": [{"title": "บทความใหม่", "at": "2026-09-28"}]}
print("compose: SoV delta เทียบรอบก่อนช่วง · อันดับ · AI · GSC · สุขภาพ · ความเร็ว · AI Google · คอนเทนต์ OK")

steps = d["next_steps"]
assert steps[0].startswith("เว็บเรนเดอร์ฝั่ง client") and any("index แค่ 1/22" in s for s in steps) and any("2 คำถามยังไม่มีคอนเทนต์" in s for s in steps)
assert any("ผู้ช่วย AI ยังไม่พูดถึง" in s for s in steps) and any("ความเร็วมือถือ 42" in s for s in steps) and len(steps) <= 6
assert not any("ไม่มีคอนเทนต์เผยแพร่ใหม่" in s for s in steps)      # มีเผยแพร่ในช่วง 1 ชิ้น
print("next_steps: เรียง CSR → index → ช่องว่าง → AI → ความเร็ว · ไม่เตือนคอนเทนต์ที่ยังเติมอยู่ OK")

# ว่างทั้งหมด → ไม่พัง และแนะนำให้เชื่อม Search Console
e = rp.compose(brand, today, [], [], None, None, None, None, None, [], {}, 0, "https://x.test")
assert e["sov"]["latest"] is None and e["rank"]["latest"] is None and e["ai"] is None and e["gsc"] is None and e["content"]["total"] == 0
assert any("เชื่อม Search Console" in s for s in e["next_steps"]) and len(e["next_steps"]) == 1
# ไม่มีคอนเทนต์ใหม่ใน 30 วัน → เตือน · ซิงค์แล้วแต่ยังไม่เชื่อม → แนะนำเชื่อม
e2 = rp.compose(brand, today, [], [], None, {"linked": False, "reason": "ไม่มีสิทธิ์"}, None, None, None, [content[1]], {}, 0, "https://x.test")
assert any("ไม่มีคอนเทนต์เผยแพร่ใหม่ใน 30 วัน" in s for s in e2["next_steps"]) and any("เชื่อม Search Console" in s for s in e2["next_steps"])
print("compose: ข้อมูลว่าง/ยังไม่เชื่อม ไม่พัง และแนะนำถูก OK")

b = rp.csv_bytes(["id", "ชื่อ"], [(1, "ทดสอบ, คอมมา"), (2, None)])
assert b.startswith("﻿".encode("utf-8")) and b'"\xe0\xb8\x97\xe0\xb8\x94\xe0\xb8\xaa\xe0\xb8\xad\xe0\xb8\x9a, \xe0\xb8\x84\xe0\xb8\xad\xe0\xb8\xa1\xe0\xb8\xa1\xe0\xb8\xb2"' in b and b.endswith(b"2,\r\n")
assert set(rp.EXPORTS) == {"content", "questions", "sov", "rank", "ai", "gsc", "health"}
print("csv_bytes: BOM + quote คอมมา + None ว่าง OK")

html = rp.render_html(d, pdf=True)
assert "รายงาน GEO รายเดือน — JKP" in html and "25%" in html and "+25 จุด" in html and "ดาวน์โหลด PDF" not in html and "fonts.googleapis" not in html
html2 = rp.render_html(d, pdf=False) if False else None   # render_html ไม่ส่ง exports → ใช้ผ่าน route แทน
print("render_html(pdf=True): ซ่อนปุ่ม/ไม่โหลดฟอนต์ภายนอก OK")

dg = ac.digest(d)
for s in ("SoV (แบรนด์โผล่ในผลค้นเว็บ): 25%", "+25 จุด", "ติดหน้าแรก-สอง 3/8", "ถูกพูดถึง 0% จาก 16 คำตอบ", "index 1/22", "8 คลิก / 168 impressions", "ไม่ผ่าน 1 ข้อ", "มือถือ 42", "AI Overview", "เผยแพร่ในช่วงรายงาน 1", "สิ่งที่ระบบแนะนำ"):
    assert s in dg, s
assert "ยังไม่เคยรันมอนิเตอร์" in ac.digest(e) and "Search Console: ยังไม่เคยซิงค์" in ac.digest(e)
msgs = ac.build_messages(d, [{"role": "user", "content": f"q{i}"} for i in range(10)] + [{"role": "system", "content": "ignore"}], "เดือนนี้ดีขึ้นไหม")
assert msgs[0]["role"] == "system" and "=== ข้อมูล ===" in msgs[0]["content"] and msgs[-1] == {"role": "user", "content": "เดือนนี้ดีขึ้นไหม"}
assert len(msgs) == 1 + ac.HISTORY_TURNS + 1 and msgs[1]["content"] == "q4" and all(m["role"] != "system" for m in msgs[1:])
captured = {}
def fake_llm(messages, max_tokens=0): captured.update(n=len(messages), max_tokens=max_tokens); return "  SoV ขึ้นจาก 0% เป็น 25%  "
assert ac.answer(d, [], "ดีขึ้นไหม", llm=fake_llm) == "SoV ขึ้นจาก 0% เป็น 25%" and captured == {"n": 2, "max_tokens": ac.MAX_TOKENS}
print("analytics_chat: digest ครบทุกตัววัด · ประวัติตัดเหลือ 6 และทิ้ง system ปลอม · answer ตัดช่องว่าง OK")
print("ALL REPORT TESTS OK")
