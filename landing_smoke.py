"""ทดสอบหน้าแรกแบบใหม่ (/): เปิดได้ + ไม่ noindex · ลิงก์ทดลองเดิมพาไปหน้าแรก · ผลจริงมาจากผลถาม AI ที่ดีขึ้นของลูกค้าที่อนุญาตใช้ชื่อเท่านั้น
· ไม่มีใครเข้าเกณฑ์ = ไม่โชว์ส่วนผลจริง · ราคารายปีจากตัวคิดราคาจริง · perks ไม่มีอีโมจินำหน้า · หน้าแรกเดิมยังเปิดได้"""
import os, tempfile, json
os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-lp-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
from fastapi.testclient import TestClient
import re
import app.db as db, app.main as M, app.marketing as MK, app.quotes as Q, app.billing as B, app.landing_i18n as L

db.init_db()
c = TestClient(M.app)

# ---- ไม่มีลูกค้าเข้าเกณฑ์ → ไม่มีส่วนผลจริง ----
M._PROOF.update(ts=0, val=None)
r = c.get("/")
assert r.status_code == 200 and 'name="robots"' not in r.text and 'class="cine"' in r.text
assert c.get("/preview/landing", follow_redirects=False).headers["location"] == "/"
assert 'id="proof"' not in r.text and 'href="#proof"' not in r.text
assert 'action="/audit"' in r.text and 'name="website"' in r.text          # ฟอร์มตรวจเว็บ + honeypot
print("หน้าแรก: แบบใหม่ · ไม่ noindex · ลิงก์ทดลองพาไปหน้าแรก · ไม่มีผลจริงก็ไม่โชว์ · ฟอร์มตรวจเว็บส่งไป /audit OK")

# ---- ราคา: รายปีจากตัวคิดราคาจริง · perks ไม่มีอีโมจิ ----
rows = {p["key"]: p for p in M._plan_rows(M._landing_ctx()["plans"])}
y = Q.price_for("pro", cycle="year")
assert rows["pro"]["year_net"] == y["net"] and rows["pro"]["year_total"] == y["period_total"]
assert "year_net" not in rows["unlimited"]
assert all(f[:1].isalnum() or "฀" <= f[:1] <= "๿" for p in rows.values() for f in p["perks"])
assert f"฿{y['net']:,}" in r.text and "฿9,900" in r.text
print("pricing: รายเดือน + รายปีจาก quotes.price_for · perks ไม่มีอีโมจินำหน้า OK")

# ---- ผลจริง: เฉพาะลูกค้าที่อนุญาตใช้ชื่อ และเลือกรายที่ดีขึ้นมากที่สุด ----
tid = db.create_tenant("o@x.test", "x", "Owner")
b1 = db.create_brand(tid, "ร้านตัวอย่างหนึ่ง", "a.example", "ร้านอาหาร")
b2 = db.create_brand(tid, "ร้านไม่อนุญาต", "b.example", "คลินิก")
db.set_brand_case_study(b1, True)
trend = {b1: {"asked": 12, "first": {"hits": 2, "at": "2026-10-01", "eng": {"chatgpt": [2, 6], "gemini": [0, 6]}},
              "last": {"hits": 6, "at": "2026-10-15", "eng": {"chatgpt": [3, 6], "gemini": [3, 6]}},
              "engines": ["ChatGPT", "Gemini"], "moved": ["Gemini จาก 0 เป็น 3 จาก 6"]},
         b2: {"asked": 12, "first": {"hits": 0, "at": "2026-10-01"}, "last": {"hits": 9, "at": "2026-10-15"},
              "engines": ["ChatGPT"], "moved": []}}
real_trend = MK.ai_trend
MK.ai_trend = lambda bid: trend.get(bid)
M._PROOF.update(ts=0, val=None)
p = M._landing_proof()
assert p and p["name"] == "ร้านตัวอย่างหนึ่ง" and (p["first"], p["last"], p["asked"]) == (2, 6, 12)   # รายไม่อนุญาตดีกว่าแต่ไม่ถูกเลือก
v = M._proof_view(p, "th")
assert v["first_at"] == "1 ต.ค. 2569" and v["last_at"] == "15 ต.ค. 2569" and v["who"] == "ร้านตัวอย่างหนึ่ง"
assert v["moved"] == ["ChatGPT จาก 2 เป็น 3 จาก 6", "Gemini จาก 0 เป็น 3 จาก 6"]
e = M._proof_view(p, "en")
assert e["who"] == "a.example" and e["first_at"] == "Oct 1, 2026" and e["moved"][1] == "Gemini: 0 → 3 of 6"
r = c.get("/")
assert 'id="proof"' in r.text and 'data-count="6"' in r.text and "ร้านไม่อนุญาต" not in r.text
print("proof: เลือกเฉพาะรายที่อนุญาตใช้ชื่อ · ตัวเลขจาก ai_trend · วันที่แบบไทย OK")

# ---- 5 ภาษา: ชุดข้อความครบเท่ากัน · hreflang · ไม่มีภาษาไทยหลงในหน้าภาษาอื่น · แชตเฉพาะหน้าไทย ----
keys = set(L.T["th"])
for lg in L.LANGS:
    assert set(L.T[lg]) == keys, (lg, keys ^ set(L.T[lg]))
    assert len(L.T[lg]["faq"]) == (5 if lg == "th" else 6)                # ภาษาอื่นมีข้อ "รายงานเป็นภาษาอะไร"
for plan in B.PLANS.values():                                              # จุดเด่นแพ็กเกจทุกบรรทัดต้องมีคำแปล
    for f in [plan.get("tagline", "")] + [re.sub(r"^[^\w\u0e00-\u0e7f]+", "", x).strip() for x in plan["perks"]]:
        assert all(L.PLAN_TEXT.get(f, {}).get(lg) for lg in L.LANGS if lg != "th"), f
for lg in L.LANGS:
    r = c.get(L.PATHS[lg])
    assert r.status_code == 200 and f'<html lang="{L.HTML_LANG[lg]}">' in r.text
    assert all(f'hreflang="{L.HTML_LANG[x]}" href="http://testserver{L.PATHS[x]}"' in r.text for x in L.LANGS)
    assert f'name="src" value="{"home" if lg == "th" else "home-" + lg}"' in r.text
    if lg != "th":
        txt = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<!--[\s\S]*?-->", "", r.text).replace("เจอ.AI", "").replace("฿", "")
        thai = re.findall(r"[\u0e00-\u0e7f]+", txt)
        assert set(thai) <= {"ไทย"}, (lg, thai[:5])          # "ไทย" = ชื่อภาษาในตัวเลือกภาษา
        assert "chat/ask" not in r.text and "ร้านไม่อนุญาต" not in r.text
        assert "a.example" in r.text                                       # ภาษาอื่นเรียกลูกค้าด้วยโดเมน
for lg in L.LANGS:                                                          # เจ้าของยกเลิกนโยบายคืนเงิน (25 ต.ค. 2569) — หน้าแรกไม่สัญญาคืนเงิน
    assert not re.search(r"คืนเงิน|refund|退款|返金|환불", c.get(L.PATHS[lg]).text, re.I), lg
print("5 ภาษา: ข้อความครบทุกภาษา · แพ็กเกจแปลครบ · hreflang · src ต่อภาษา · ไม่มีไทยหลง · แชตเฉพาะหน้าไทย OK")
assert L.date("2026-10-04", "en") == "Oct 4, 2026" and L.date("2026-10-04", "ja") == "2026年10月4日" and L.date("2026-10-04", "ko") == "2026년 10월 4일"
print("วันที่ตามภาษา OK")

# ---- หน้าแรกแบบเดิมเก็บไว้ย้อนกลับ ----
r = c.get("/preview/landing-old")
assert r.status_code == 200 and "noindex" in r.headers.get("x-robots-tag", "") and 'class="cine"' not in r.text
print("landing เดิม: ยังเปิดได้ที่ /preview/landing-old (noindex) OK")
MK.ai_trend = real_trend
print("ALL OK")
