"""ทดสอบ auto สร้างร่าง แบบไม่แตะ DB/เครือข่าย — ด่านเวลา, สถานะคิว (เขียน/เติมคำถาม/รอวัด/โควตา/เพดาน), กรองคำถามซ้ำ, prompt เลี่ยงของเดิม"""
import datetime
import app.auto_content as ac
import app.ai_client as ai
import app.autopilot as ap

now = datetime.datetime(2026, 10, 5, 10, 0)

# ---- ด่านเวลา ----
assert ac.time_gate(0, 0, None, now)["action"] == "off"
assert ac.time_gate(3, 3, None, now)["action"] == "cap"                       # สัปดาห์นี้ครบแล้ว
g = ac.time_gate(3, 1, "2026-10-04T10:00:00", now)
assert g["action"] == "spacing" and g["next_at"] == datetime.datetime(2026, 10, 6, 10, 0)   # 3 ชิ้น → เว้น 2 วัน
assert ac.time_gate(3, 1, "2026-10-03T09:59:00", now) is None               # ครบ 2 วันแล้ว → ผ่าน
assert ac.time_gate(2, 0, "2026-10-03T10:00:00", now)["action"] == "spacing"  # 2 ชิ้น → เว้น 3 วัน
assert ac.time_gate(3, 0, "garbage", now) is None                             # ค่าเสีย = ไม่ขวาง
print("time_gate: ปิด/ครบสัปดาห์/เว้นระยะตามจำนวนชิ้น OK")

# ---- สถานะคิว ----
def gaps(levels, has_run=True, q_total=None):
    gs = [{"question_id": i, "question": f"คำถาม {i} {lv}", "lang": "th", "level": lv} for i, lv in enumerate(levels)]
    return {"has_run": has_run, "gaps": gs, "critical": levels.count("critical"), "total_gaps": len(gs),
            "q_total": q_total if q_total is not None else len(levels) + 2}

assert ac.queue_state(gaps([], has_run=False), True)["action"] == "no_run"
w = ac.queue_state(gaps(["stale", "critical", "critical", "draft"]), True)
assert w["action"] == "write" and w["gap"]["question_id"] == 1 and "อีก 2 ข้อ" in w["text"]   # ตัว critical แรก
assert ac.queue_state(gaps(["critical"]), False)["action"] == "quota"                            # มีงานแต่โควตาเต็ม
a = ac.queue_state(gaps(["stale", "unmeasured", "unmeasured"]), True, next_run_at=datetime.datetime(2026, 10, 11, 8, 0))
assert a["action"] == "await_measure" and a["n"] == 2 and "2026-10-11 08:00" in a["text"]      # มีคำถามใหม่รอวัด → ไม่เติมซ้ำ
assert ac.queue_state(gaps(["stale"] * 15), False)["action"] == "quota"
assert ac.queue_state(gaps(["stale"] * 15, q_total=ac.TOPUP_MAX_Q), True)["action"] == "max_q"
t = ac.queue_state(gaps(["stale"] * 15, q_total=16), True)
assert t["action"] == "topup" and t["n"] == ac.TOPUP_N and "16 ข้อ" in t["text"]                # เคส twinveetech
assert ac.queue_state(gaps([], q_total=0), True)["action"] == "topup"                            # ไม่มีคำถามเลยก็คิดให้
print("queue_state: ไม่เคยรัน/เขียน/โควตา/รอวัด/เพดาน/เติมคำถาม OK")

# decide = ด่านเวลาก่อน แล้วค่อยคิว · status แยกสองบรรทัดและไม่ให้ด่านเวลาบังสถานะคิว
assert ac.decide(3, 3, None, gaps(["critical"]), True, now)["action"] == "cap"
assert ac.decide(3, 0, "2026-08-05T01:19:16", gaps(["stale"] * 15, q_total=16), True, now)["action"] == "topup"
st = ac.status(3, 1, "2026-10-04T10:00:00", gaps(["stale"] * 15, q_total=16), True, now, last_item_at="2026-10-04T10:00:00")
assert st["progress"].startswith("สัปดาห์นี้สร้างแล้ว 1/3 ชิ้น") and "ชิ้นล่าสุด 2026-10-04 10:00" in st["progress"] and "รอบถัดไป 2026-10-06 10:00" in st["progress"]
assert st["queue"]["action"] == "topup"
print("decide/status: ด่านเวลาก่อนคิว · หน้าเว็บเห็นทั้งความคืบหน้าและคิว OK")

# ---- รอบมอนิเตอร์ถัดไป (กติกาเดียวกับตัวตั้งเวลา) ----
assert ac.next_monitor_at("2026-10-04T08:00:12", 7, "08:00", now) == datetime.datetime(2026, 10, 11, 8, 0)
assert ac.next_monitor_at("2026-10-04T15:59:00", 7, "09:30", now) == datetime.datetime(2026, 10, 11, 9, 30)
assert ac.next_monitor_at(None, 7, "08:00", now) == now and ac.next_monitor_at("2026-10-04T08:00:00", 0, "08:00", now) is None
print("next_monitor_at: วันล่าสุด+interval เวลา HH:MM OK")

# ---- กรองคำถามซ้ำ ----
existing = ["โกดังให้เช่าแถวบางนา ที่ไหนถูกสุด", "จ้างทำแอพร้านอาหาร ราคาประมาณเท่าไหร่", "Best software house in Bangkok for AI chatbot?"]
assert ac.similar("โกดังให้เช่าแถวบางนา ที่ไหนถูกสุด", "โกดังให้เช่า แถวบางนา ที่ไหนถูกสุด?")        # ต่างแค่ช่องว่าง/เครื่องหมาย
assert ac.similar("โกดังให้เช่าบางนา ที่ไหนถูกสุด", "โกดังให้เช่าแถวบางนา ที่ไหนถูกสุด")            # หายไปคำเดียว
assert ac.similar("best software house in bangkok for ai chatbot", "Best software house in Bangkok for AI chatbot?")
assert not ac.similar("โกดังให้เช่าแถวบางนา ที่ไหนถูกสุด", "ทนายรับทำคดีแรงงานที่ไหนดี ค่าจ้างแพงมั้ย")
assert not ac.similar("จ้างทำแอพร้านอาหาร ราคาประมาณเท่าไหร่", "บริษัทรับทำ AI chatbot ให้ร้านค้าออนไลน์ ที่ไหนดี")
cands = [
    {"question": "โกดังให้เช่า แถวบางนา ที่ไหนถูกสุด?", "lang": "th"},                 # ซ้ำของเดิม
    {"question": "สั้น", "lang": "th"},                                                  # สั้นเกิน
    {"question": "เพิ่งเปิดร้านออนไลน์ หาโกดังเล็กๆ แถวสมุทรปราการ งบไม่เกิน 15,000", "lang": "th"},
    {"question": "หาโกดังเล็กๆ แถวสมุทรปราการ เพิ่งเปิดร้านออนไลน์ งบไม่เกิน 15,000", "lang": "th"},  # ซ้ำกันเอง (สลับประโยค)
    {"question": "Warehouse for rent near Suvarnabhumi airport, how much per sqm?", "lang": "xx"},   # lang เพี้ยน → เดาจากตัวอักษร
    {"question": "โกดังพร้อมออฟฟิศ ให้เช่าแถวบางพลี มีมั้ย", "lang": "th"},
    {"question": "โรงงานให้เช่า พื้นที่สีม่วง สมุทรสาคร ราคาเท่าไหร่"},
]
picked = ac.pick_new(cands, existing, 3)
assert [p["question"] for p in picked] == [cands[2]["question"], cands[4]["question"], cands[5]["question"]], picked
assert picked[1]["lang"] == "en" and picked[0]["lang"] == "th"
assert len(ac.pick_new(cands, existing, 10)) == 4                                          # ไม่จำกัด → เหลือ 4 ที่ไม่ซ้ำ
print("pick_new: ตัดซ้ำของเดิม/ซ้ำกันเอง/สั้นเกิน · เดา lang · ไม่เกิน n OK")

# ---- prompt เลี่ยงของเดิม + สัดส่วนภาษาแปรตาม n (ไม่ยิงจริง) ----
seen = {}
def fake_chat(messages, **kw):
    seen["prompt"] = messages[0]["content"]
    return '[{"question": "โกดังพร้อมออฟฟิศ ให้เช่าแถวบางพลี มีมั้ย", "lang": "th"}, {"question": "Warehouse for rent Bangna, price?", "lang": "en"}]'
ai._chat = fake_chat
items = ai.generate_questions("JKP", "jkp.co.th", "โกดังให้เช่า", n=6, existing=existing)
assert len(items) == 2 and items[1]["lang"] == "en"
assert "คำถามที่มีอยู่แล้ว 3 ข้อ" in seen["prompt"] and "- " + existing[0] in seen["prompt"] and "เขียนคำถาม 6 ข้อ" in seen["prompt"]
assert "ภาษาไทยอย่างน้อย 5 ข้อ, อังกฤษ 1 ข้อ" in seen["prompt"]
ai.generate_questions("JKP", "jkp.co.th", "โกดังให้เช่า")   # แบบเดิม n=8 ไม่มี existing
assert "คำถามที่มีอยู่แล้ว" not in seen["prompt"] and "ภาษาไทยอย่างน้อย 6 ข้อ, อังกฤษ 2 ข้อ" in seen["prompt"]
print("generate_questions: แนบของเดิมให้เลี่ยง · สัดส่วนภาษาตาม n OK")

# ---- Autopilot สรุปขั้น content เมื่อเติมคำถาม / ติดอะไร ----
assert ap.summarize_step("content", [{"status": "topup", "questions": ["ก", "ข", "ค"]}]) == "ปิดครบทุกคำถามแล้ว — เพิ่มคำถามใหม่ 3 ข้อ รอวัดรอบหน้า"
assert ap.summarize_step("content", [{"status": "await_measure", "msg": "คำถามใหม่ 3 ข้อรอวัด"}]) == "คำถามใหม่ 3 ข้อรอวัด"
assert ap.summarize_step("content", []) == "ไม่มีช่องว่างที่ยังไม่มีคอนเทนต์"
assert ap.summarize_step("content", [{"id": 9, "title": "x", "status": "draft"}, {"status": "quota", "msg": "เต็ม", "question": "q"}]) == "สร้าง 1 ชิ้น (ร่าง) · โควตาแพ็กเกจเต็ม"
print("autopilot summarize content: เติมคำถาม/รอวัด/ของเดิม OK")
print("\nauto_content_smoke: ผ่านทั้งหมด")
