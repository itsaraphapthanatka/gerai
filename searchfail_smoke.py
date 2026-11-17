"""ค้นเว็บไม่สำเร็จ ≠ ไม่โผล่ · ข้อความ error ต้องไม่มีคีย์ — ไม่ยิงเน็ต"""
import os
import tempfile
os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-searchfail-"), "smoke.db")
os.environ["DATABASE_URL"] = ""

from app import db
from app.redact import redact
import app.geo_worker as gw

# ---- redact ----
url_err = ("HTTPStatusError: Client error '429 Too Many Requests' for url "
           "'https://serpapi.com/search.json?engine=google&q=x&hl=th&api_key=abcdef1234567890'")
assert "abcdef1234567890" not in redact(url_err) and "api_key=***" in redact(url_err), redact(url_err)
assert "AIzaSyFAKE" not in redact("https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url=a&key=AIzaSyFAKE123")
assert "SECRETTOKEN" not in redact("https://api.telegram.org/bot123456:SECRETTOKEN/sendMessage")
assert "abc.def.ghi" not in redact("Authorization: Bearer abc.def.ghijklmnop")
assert "4242424242" not in redact("Invalid API Key provided: " + "sk_" + "live_4242424242abcd")   # ประกอบตอนรัน กันตัวสแกนความลับเข้าใจผิด
assert redact("https://example.com/page?q=โกดัง&page=2") == "https://example.com/page?q=โกดัง&page=2"   # ไม่มีความลับ = ไม่แตะ
assert redact(ValueError("x?token=zzz9999")) == "x?token=***"
cut = redact(url_err)[:120]   # ตัดหลัง redact = ไม่เหลือคีย์ครึ่งท่อน
assert "abcdef" not in cut
print("redact: SerpApi / Google key / Telegram / Bearer / Stripe / URL ปกติ OK")

# ---- run_for_brand: ค้นพลาด/ว่าง ไม่นับเป็นไม่โผล่ ----
db.init_db()
tid = db.create_tenant("searchfail@x.test", "x")
bid = db.create_brand(tid, "Pocket Trip", "pockettrip.example", "แอปวางแผนเที่ยว")
for qtext in ("q-hit", "q-miss", "q-empty", "q-boom"):
    db.add_question(bid, qtext)

calls = {}
def fake_search(query, limit=10):
    calls[query] = calls.get(query, 0) + 1
    if query == "q-hit":
        return [{"title": "x", "url": "https://pockettrip.example/a", "domain": "pockettrip.example", "position": 1}]
    if query == "q-miss":
        return [{"title": "y", "url": "https://other.example/b", "domain": "other.example", "position": 1}]
    if query == "q-empty":
        return []
    raise RuntimeError("No results found.")

gw.search = fake_search
gw.time.sleep = lambda s: None
res = gw.run_for_brand(bid)
assert res["measured"] == 2 and res["failed"] == 2 and res["brand_hits"] == 1 and res["share_of_voice"] == 0.5, res
assert calls["q-empty"] == 3 and calls["q-boom"] == 3 and calls["q-hit"] == 1, calls   # ลองซ้ำเฉพาะข้อที่พลาด
run = db.get_run(res["run_id"])
assert run["status"] == "done" and run["questions_total"] == 2 and run["questions_failed"] == 2, dict(run)
rows = {r["question"]: r for r in db.get_results(res["run_id"])}
assert rows["q-boom"]["error"] == 1 and rows["q-empty"]["error"] == 1 and rows["q-miss"]["error"] == 0
gaps = db.get_content_gaps(bid)
lv = {g["question"]: g["level"] for g in gaps["gaps"]}
assert lv["q-boom"] == "unmeasured" and lv["q-empty"] == "unmeasured" and lv["q-miss"] == "critical" and "q-hit" not in lv, lv   # ค้นพลาด = รอวัดใหม่ ไม่ใช่ช่องว่างที่ต้องรีบเขียน
print("run_for_brand: ค้นพลาด/ว่าง = วัดไม่ได้ ไม่นับใน SoV · ลองซ้ำ 3 ครั้ง OK")

# ค้นไม่ได้ทั้งรอบ = รอบล้ม ไม่ใช่ 0%
gw.search = lambda q, limit=10: []
res = gw.run_for_brand(bid)
run = db.get_run(res["run_id"])
assert run["status"] == "failed" and run["share_of_voice"] is None and run["questions_failed"] == 4, dict(run)
last = [b for b in db.list_all_brands() if b["id"] == bid][0]
assert last["last_sov"] == 0.5, dict(last)   # หน้าแอดมินยังโชว์รอบล่าสุดที่วัดได้
print("รอบที่ค้นไม่ได้เลย = failed, SoV ว่าง, หน้าแอดมินใช้รอบที่วัดได้ล่าสุด OK")
print("ALL SEARCH-FAIL TESTS OK")
