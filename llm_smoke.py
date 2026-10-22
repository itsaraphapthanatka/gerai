"""ทดสอบตัวเลือกโมเดลต่องาน (ไม่ยิงเครือข่าย): ค่าเริ่มต้น local · เลือก Gemini แล้วใช้จริง · Gemini ล้ม/ไม่มีคีย์ → ถอยกลับ local
และจำเหตุผล · แปลง messages → generateContent · อ่านคำตอบ Gemini · ตัวคิดคำถามวิ่งผ่าน router · การ์ดในหน้าตั้งค่า render ได้"""
import os, tempfile
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke-"), "smoke.db")
import app.ai_client as ai

settings = {}
calls = []
ai._read_setting = lambda k: settings.get(k)
ai._chat = lambda m, max_tokens=2048, timeout=120: calls.append("local") or '[{"question": "โกดังให้เช่าบางนา ที่ไหนถูกสุด", "lang": "th"}]'
msgs = [{"role": "user", "content": "คิดคำถาม"}]

# 1) ค่าเริ่มต้น / ค่าเพี้ยน → local
assert ai.task_setting("questions") == "local"
settings["llm_questions"] = "openai-something"; assert ai.task_setting("questions") == "local"
assert ai.chat_for("questions", msgs).startswith("[") and calls == ["local"]
print("router: ค่าเริ่มต้นและค่าเพี้ยน → โมเดลในเครื่อง OK")

# 2) เลือก Gemini + มีคีย์ → ใช้ Gemini ไม่แตะ local
settings["llm_questions"] = "gemini"; calls.clear()
ai._gemini_conf = lambda: ("k3y", "gemini-test", "https://gemini.example")
seen = {}
def fake_gemini(messages, max_tokens=2048, timeout=120, json_mode=False):
    seen.update(json_mode=json_mode, max_tokens=max_tokens); return '[{"question": "หาโกดังเช่าแถวสมุทรปราการ งบไม่เกิน 3 หมื่น", "lang": "th"}]'
ai.gemini_chat = fake_gemini
out = ai.chat_for("questions", msgs, json_mode=True)
assert "สมุทรปราการ" in out and calls == [] and seen == {"json_mode": True, "max_tokens": 2048}
assert ai.task_status("questions") == {"choice": "gemini", "gemini_ready": True, "gemini_model": "gemini-test", "local_model": ai.MODEL, "fallback": None}
print("router: เลือก Gemini → ใช้ Gemini (JSON mode) ไม่แตะโมเดลในเครื่อง OK")

# 3) Gemini ล้ม → ถอยกลับ local + จำเหตุผล · สำเร็จครั้งถัดไป → ล้างเหตุผล
def boom(*a, **k): raise RuntimeError("HTTP Error 429: quota")
ai.gemini_chat = boom; calls.clear()
assert ai.chat_for("questions", msgs).startswith("[") and calls == ["local"]
assert ai.task_status("questions")["fallback"] == "RuntimeError: HTTP Error 429: quota"
ai.gemini_chat = fake_gemini
ai.chat_for("questions", msgs); assert ai.task_status("questions")["fallback"] is None
print("router: Gemini ล้ม → โมเดลในเครื่อง + จำเหตุผล · หายเมื่อกลับมาใช้ได้ OK")

# 4) เลือก Gemini แต่ไม่มีคีย์ → gemini_chat ของจริงโยน "ไม่มีคีย์" → ถอยกลับ · สถานะบอก gemini_ready=False
import importlib; real_gemini_chat = importlib.reload(ai).gemini_chat   # เอาตัวจริงกลับมาเทสต์เงื่อนไขคีย์
ai._read_setting = lambda k: settings.get(k); ai._chat = lambda m, max_tokens=2048, timeout=120: calls.append("local") or "[]"
ai._gemini_conf = lambda: (None, "gemini-test", "https://gemini.example"); calls.clear()
assert ai.chat_for("questions", msgs) == "[]" and calls == ["local"]
st = ai.task_status("questions"); assert st["gemini_ready"] is False and "ไม่มีคีย์" in st["fallback"]
print("router: เลือก Gemini แต่ไม่มีคีย์ → โมเดลในเครื่อง + บอกเหตุผล OK")

# 5) แปลง messages → body และอ่านคำตอบ
body = ai.gemini_body([{"role": "system", "content": "เป็นคนไทย"}, {"role": "user", "content": "ถาม A"}, {"role": "user", "content": "ถาม B"}], 500, True)
assert body["systemInstruction"]["parts"][0]["text"] == "เป็นคนไทย" and body["contents"][0]["parts"][0]["text"] == "ถาม A\n\nถาม B"
assert body["generationConfig"] == {"maxOutputTokens": 500, "responseMimeType": "application/json"}
assert "systemInstruction" not in ai.gemini_body([{"role": "user", "content": "x"}], 10, False) and "responseMimeType" not in ai.gemini_body([{"role": "user", "content": "x"}], 10, False)["generationConfig"]
assert ai.gemini_text({"candidates": [{"content": {"parts": [{"text": "ก"}, {"text": "ข"}]}}]}) == "กข" and ai.gemini_text({}) == ""
print("gemini: แปลง body (system/json mode) + อ่านคำตอบ OK")

# 6) ตัวคิดคำถามวิ่งผ่าน router: เลือก Gemini → ได้คำถามจาก Gemini
ai._gemini_conf = lambda: ("k3y", "gemini-test", "https://gemini.example"); ai.gemini_chat = fake_gemini
items = ai.generate_questions("HOP", "hopai.online", "อสังหาฯ เชิงพาณิชย์", n=3)
assert len(items) == 1 and "สมุทรปราการ" in items[0]["question"] and items[0]["lang"] == "th"
print("generate_questions: ใช้โมเดลตามที่ตั้ง OK")

# 7) การ์ดในหน้าตั้งค่า render ได้ (DB ชั่วคราว, stub globals ของ base.html)
from starlette.requests import Request
import app.db as db, app.main as m
db.init_db(); db.set_setting("llm_questions", "gemini")
m.templates.env.globals.update({"sidebar_brands": lambda r: [], "unread_count": lambda r: 0, "new_contacts_count": lambda r: 0})
req = Request({"type": "http", "method": "GET", "path": "/admin/settings", "headers": [], "query_string": b"", "session": {"tenant_id": 1, "is_admin": True}, "router": None, "app": m.app, "scheme": "https", "server": ("x", 443)})
m.ai_client._read_setting = lambda k: db.get_setting(k)
m.ai_client._gemini_conf = lambda: (None, "gemini-3.8-flash", "https://generativelanguage.googleapis.com")
html = m.templates.TemplateResponse(req, "admin_settings.html", m._settings_ctx(req)).body.decode()
assert "โมเดลต่องาน — ตัวคิดคำถาม" in html and 'value="gemini" selected' in html and "ยังไม่มีคีย์ Gemini" in html
print("admin_settings.html: การ์ดเลือกโมเดล + สถานะไม่มีคีย์ OK")
print("\nllm_smoke: ผ่านทั้งหมด")
