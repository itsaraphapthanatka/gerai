"""ทดสอบหาลูกค้าจาก Google Maps (Serper /places): แปลงผล · กรองไม่มีเว็บ/เพจโซเชียล/รีวิวน้อย · เรียงรีวิวเยอะก่อน · เก็บเบอร์+รีวิวใน call_json
· รายไม่มีอีเมลแต่มีเบอร์ขึ้นรายการโทร (บทเปิด cold_phone) · Maps ไม่พอ → เติมจากค้นเว็บ · ปิดสวิตช์ได้ · ไม่ยิงเครือข่าย"""
import os, tempfile, json, datetime as dt
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-maps-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ.setdefault("GEO_DEEP_AUDIT", "0")
os.environ.setdefault("GEO_CS", "0")
os.environ.pop("SERPER_API_KEY", None)
import httpx
import app.db as db, app.prospecting as PR, app.pipeline as P, app.site_health as sh, app.ai_client as ai, app.mailer as mailer, app.geo_worker as gw, app.closer as C

def rep(*fails, ok=10):
    checks = [{"key": k, "label": f"ข้อ {k}", "status": "fail", "detail": ""} for k in fails]
    return {"ok": not checks, "n_fail": len(checks), "n_ok": ok, "checks": checks + [{"key": "x", "label": "ผ่าน", "status": "ok"}] * ok}
db.init_db()
sh.run_checks = lambda site, **kw: rep("csr", "indexed", "schema", "faq", "llms", "meta")
ai._read_setting = lambda k: None
ai._chat = lambda m, max_tokens=2048, timeout=120: (_ for _ in ()).throw(RuntimeError("no model"))
mailer.config = lambda: {"host": "x"}; mailer.send = lambda *a, **k: None
P.RUN_INLINE = True
C.FETCH = lambda u: ""                                    # ไม่ไต่เว็บจริงหาเบอร์

# ---- places_search: แปลงผล Serper ----
assert gw.places_search("x") == [] and not PR.maps_available() and not PR.maps_on()      # ไม่มีคีย์ = ไม่ยิง
db.set_setting("serper_key", "test-key")
assert PR.maps_available() and PR.maps_on()                                             # มีคีย์ = เปิดเป็นค่าเริ่มต้น
sent = {}
class R:
    def raise_for_status(self): pass
    def json(self): return {"places": [{"title": "คลินิก เอ ", "website": "https://clinic-a.co.th/", "phoneNumber": "053 000 111", "rating": 4.9,
                                        "ratingCount": 705, "category": "คลินิกทันตกรรม", "address": "ถนน 1", "cid": 123}, {"title": "ไม่มีเว็บ"}]}
def fake_post(url, headers=None, json=None, timeout=None):
    sent.update(url=url, key=headers["X-API-KEY"], body=json); return R()
orig = httpx.post; httpx.post = fake_post
rows = gw.places_search("คลินิกทันตกรรม เชียงใหม่", 5)
httpx.post = orig
assert sent["url"].endswith("/places") and sent["key"] == "test-key" and sent["body"]["gl"] == "th"
assert rows[0] == {"title": "คลินิก เอ", "url": "https://clinic-a.co.th/", "phone": "053 000 111", "rating": 4.9, "reviews": 705,
                   "category": "คลินิกทันตกรรม", "address": "ถนน 1", "cid": "123"}, rows[0]
assert rows[1]["url"] == "" and rows[1]["reviews"] == 0
print("places_search: ยิง /places ด้วยคีย์ · แปลงชื่อ/เว็บ/เบอร์/คะแนน/รีวิว · ไม่มีคีย์ไม่ยิง OK")

# ---- รอบหาลูกค้าจาก Maps ----
places = [
    {"title": "ร้านเล็ก", "url": "https://tiny.co.th", "phone": "021112222", "rating": 5, "reviews": 2, "category": "c", "address": "", "cid": "1"},
    {"title": "มีแค่เพจ", "url": "https://www.facebook.com/page", "phone": "0811112222", "rating": 4.5, "reviews": 90, "category": "c", "address": "", "cid": "2"},
    {"title": "ไม่มีเว็บ", "url": "", "phone": "0811113333", "rating": 4.5, "reviews": 50, "category": "c", "address": "", "cid": "3"},
    {"title": "คลินิกกลาง", "url": "https://mid-clinic.co.th/", "phone": "053 000 222", "rating": 4.7, "reviews": 120, "category": "คลินิกทันตกรรม", "address": "a", "cid": "4"},
    {"title": "คลินิกดัง", "url": "https://big-clinic.co.th/th/", "phone": "063 000 4444", "rating": 4.9, "reviews": 918, "category": "คลินิกทันตกรรม", "address": "b", "cid": "5"},
]
web_called = []
contact = lambda site: {"email": "info@mid-clinic.co.th", "line": "", "n": 1, "source": "เว็บ"} if "mid-clinic" in site else {"email": "", "line": "", "n": 0, "source": ""}
res = PR.run(limit=2, queries=["คลินิกทันตกรรม เชียงใหม่"], places=lambda q, n: places, contact=contact,
             search=lambda q, n: web_called.append(q) or [])
assert res["maps"] == 2 and res["maps_no_site"] == 2 and res["maps_few_reviews"] == 1 and res["added"] == 2, res
assert res["leads"] == ["big-clinic.co.th", "mid-clinic.co.th"], res["leads"]           # รีวิวเยอะก่อน
assert not web_called                                                                    # Maps พอแล้ว ไม่ต้องค้นเว็บ
assert res["with_phone"] == 2 and res["with_email"] == 1 and res["proposed"] == 1, res
big = db.find_lead_by_domain("big-clinic.co.th")
d = json.loads(big["call_json"])
assert big["src"] == "maps" and big["source"] == "prospect" and big["name"] == "คลินิกดัง" and "Google Maps" in big["notes"] and "918 รีวิว" in big["notes"]
assert d["phone"] == "0630004444" and d["phone_src"] == "maps" and d["maps"]["reviews"] == 918 and d["maps"]["rating"] == 4.9
assert any(e["kind"] == "contact_found" and "Google Maps" in e["note"] for e in db.list_lead_events(big["id"]))
print("run(maps): ข้ามไม่มีเว็บ/เพจโซเชียล/รีวิวน้อย · เรียงรีวิวเยอะก่อน · เก็บเบอร์+รีวิว · มีอีเมลเสนอทักตามเดิม OK")

# ---- รายการโทร: ไม่มีอีเมลแต่มีเบอร์ + เว็บอ่อน ----
big = db.get_lead(big["id"])
assert big["status"] == "new" and not big["email"] and (big["score"] or 0) >= 30, (big["status"], big["score"])
t = C.trigger(big, dt.datetime.now())
assert t and t["kind"] == "cold_phone" and "918 รีวิว" in t["why"] and "★4.9" in t["why"], t
assert any(x["lead"]["id"] == big["id"] and x["phone"] == "0630004444" for x in C.call_list())
sc = C.script(big)
assert "Google Maps" in sc["opener"] and "อีเมลหรือ LINE" in sc["close"]
mid = db.find_lead_by_domain("mid-clinic.co.th")
assert C.trigger(mid, dt.datetime.now()) is None or C.trigger(mid, dt.datetime.now())["kind"] != "cold_phone"   # มีอีเมล → ทักทางอีเมลก่อน
print("closer: รายไม่มีอีเมลแต่มีเบอร์ขึ้นรายการโทร · บทเปิดอ้าง Google Maps · รายมีอีเมลไม่ขึ้น OK")

# ---- Maps ไม่พอ → เติมจากค้นเว็บ · ซ้ำไม่เพิ่ม ----
res = PR.run(limit=3, queries=["q"], places=lambda q, n: places, contact=contact,
             search=lambda q, n: [{"url": "https://webonly.co.th", "title": "เว็บ"}])
assert res["maps"] == 0 and res["added"] == 1 and res["leads"] == ["webonly.co.th"], res
assert db.find_lead_by_domain("webonly.co.th")["src"] == "prospect"
# Places ล้ม → ไม่ล้มทั้งรอบ
res = PR.run(limit=1, queries=["q2"], places=lambda q, n: (_ for _ in ()).throw(RuntimeError("serper down")), contact=contact,
             search=lambda q, n: [{"url": "https://fallback.co.th", "title": "x"}])
assert res["added"] == 1 and res["leads"] == ["fallback.co.th"], res
print("fallback: Maps ไม่พอ/ล้ม → เติมจากค้นเว็บ · ไม่เพิ่มซ้ำ OK")

# ---- สวิตช์ ----
PR.save_config("", "10", True, maps=False); assert not PR.maps_on() and not PR.public_config()["maps"]
PR.save_config("", "10", True); assert not PR.maps_on()                              # ไม่ส่งค่า maps = คงเดิม
PR.save_config("", "10", True, maps=True); assert PR.maps_on()
db.set_setting("prospect_last", json.dumps({"at": "2026-10-24T06:30", "found": 3, "maps": 2, "added": 2, "audited": 2, "with_email": 1, "with_phone": 2, "proposed": 1}))
assert "Google Maps 2" in PR.status_text() and "มีเบอร์ 2" in PR.status_text()
print("config: เปิด/ปิด Maps · สรุปเช้าบอกจำนวนจาก Maps/มีเบอร์ OK")
print("MAPS SMOKE OK")
