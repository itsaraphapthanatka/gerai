"""ทดสอบหน้าแรกแบบใหม่ (/): เปิดได้ + ไม่ noindex · ลิงก์ทดลองเดิมพาไปหน้าแรก · ผลจริงมาจากผลถาม AI ที่ดีขึ้นของลูกค้าที่อนุญาตใช้ชื่อเท่านั้น
· ไม่มีใครเข้าเกณฑ์ = ไม่โชว์ส่วนผลจริง · โหมดเอเจนซี: ไม่มีตารางแพ็กเกจ/สลับรอบบิล มีราคาเริ่มต้นบรรทัดเดียวจาก billing.PLANS
· ปุ่ม "นัดคุย 30 นาที" ทุกภาษา · หน้าแรกเดิมยังเปิดได้"""
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

# ---- โหมดเอเจนซี: ไม่มีตารางแพ็กเกจ · ราคาเริ่มต้นบรรทัดเดียวจากระบบ · ขั้นตอนทำงานกับเรา 4 ขั้น ----
price = f"{B.PLANS['pro']['price']:,}"
vat = f"{Q.VAT_RATE:g}"
assert not re.search(r'class="plans?\b|class="cycle"|data-cycle|เลือกแผนนี้|ทดลองฟรี 14 วัน|/upgrade', r.text)
assert not hasattr(M, "_plan_rows") and not hasattr(L, "PLAN_TEXT")
line = re.search(r'<p class="pl" id="start-price">(.*?)</p>', r.text).group(1)
assert re.sub(r"<[^>]+>", "", line).replace("\xa0", " ") == f"เริ่มต้น ฿{price}/เดือน (ยังไม่รวม VAT {vat}%) · ราคาจริงตามขอบเขตงานที่ตกลงกัน", line
assert r.text.count(f"฿{price}") == 2                                      # บรรทัดราคา + คำตอบ FAQ "ราคาเท่าไร?" เท่านั้น
assert r.text.count('class="wstep rv"') == 4 and r.text.count(f'href="/audit?src=home"') >= 3
assert "สัญญารายเดือนยกเลิกได้ทุกเมื่อ มีผลเมื่อครบรอบที่ชำระแล้ว" in r.text
B.PLANS["pro"]["price"], old = 12345, B.PLANS["pro"]["price"]             # เปลี่ยนราคาในระบบ → หน้าแรกเปลี่ยนตาม (ไม่ได้เขียนตัวเลขตายตัว)
try:
    assert "฿12,345" in c.get("/").text
finally:
    B.PLANS["pro"]["price"] = old
print(f"agency: ไม่มีการ์ดแพ็กเกจ/สลับรอบบิล · เริ่มต้น ฿{price}/เดือน จาก billing.PLANS · 4 ขั้นทำงานกับเรา OK")

# ---- ปุ่มนัดคุย 30 นาที: เปิดฟอร์มติดต่อเดิม · ส่ง plan = "นัดคุย" เข้า /api/contact ----
assert r.text.count('onclick="openCall()"') == 5                             # nav · hero · ขั้นที่ 2 · แถบราคา · final CTA
assert "นัดคุย 30 นาที" in r.text and "ทิ้งช่องทางติดต่อ เราจะโทรหรือทัก LINE เพื่อนัดเวลา" in r.text
assert json.loads(re.search(r"window\.LP=(\{.*?\});", r.text).group(1))["call"] == L.CALL_PLAN == "นัดคุย"
assert c.post("/api/contact", json={"name": "ทดสอบ", "contact": "0000", "plan": L.CALL_PLAN, "message": ""}).json()["ok"]
print("นัดคุย: ปุ่มครบทุกจุด · ฟอร์มติดต่อหัวข้อนัดคุย · plan = นัดคุย ส่งเข้า /api/contact ได้ OK")

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
    assert len(L.T[lg]["faq"]) == (6 if lg == "th" else 7)                # ภาษาอื่นมีข้อ "รายงานเป็นภาษาอะไร"
    assert sum("{price}" in a for _, a in L.T[lg]["faq"]) == 1 and "{price}" in L.T[lg]["price_line"]
for lg in L.LANGS:
    r = c.get(L.PATHS[lg])
    assert r.status_code == 200 and f'<html lang="{L.HTML_LANG[lg]}">' in r.text
    assert r.text.count(L.T[lg]["btn_call"]) >= 5 and r.text.count('onclick="openCall()"') == 5   # ปุ่มนัดคุยทุกภาษา
    assert f"฿{price}" in r.text and "{price}" not in r.text and "{vat}" not in r.text
    assert 'class="cycle"' not in r.text and 'class="plan ' not in r.text
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
print("5 ภาษา: ข้อความครบทุกภาษา · ปุ่มนัดคุย + ราคาเริ่มต้นทุกภาษา · hreflang · src ต่อภาษา · ไม่มีไทยหลง · แชตเฉพาะหน้าไทย OK")
assert L.date("2026-10-04", "en") == "Oct 4, 2026" and L.date("2026-10-04", "ja") == "2026年10月4日" and L.date("2026-10-04", "ko") == "2026년 10월 4일"
print("วันที่ตามภาษา OK")

# ---- หน้าแรกแบบเดิมเก็บไว้ย้อนกลับ ----
r = c.get("/preview/landing-old")
assert r.status_code == 200 and "noindex" in r.headers.get("x-robots-tag", "") and 'class="cine"' not in r.text
print("landing เดิม: ยังเปิดได้ที่ /preview/landing-old (noindex) OK")
MK.ai_trend = real_trend
print("ALL OK")
