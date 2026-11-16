"""ทดสอบ stage 05 (รายงาน 4 ช่อง, flag report_v2) + stage 06 (ขอแผน/นัดคุย, flag plan_request_v2 + marketing_v2)
ไม่ยิงเครือข่าย · DB ชั่วคราว · ไม่ส่งข้อความจริง (alert_admins ถูกแทนด้วยตัวนับ)"""
import os, re, json, tempfile
os.environ.pop("DATABASE_URL", None)
os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["AI_BASE_URL"] = "http://127.0.0.1:9"
os.environ["GEO_DEEP_AUDIT"] = "0"
for k in list(os.environ):
    if k.startswith("GEO_FLAG_"):
        os.environ.pop(k)
from fastapi.testclient import TestClient
import app.db as db
import app.leads as L
import app.pipeline as pipeline
import app.report_view as RV
import app.plan_request as PR
from app.main import app

db.init_db()
alerts = []
pipeline.alert_admins = lambda *a, **kw: alerts.append(a[0])
c = TestClient(app)
visible = lambda html: re.sub(r"(?s)<(style|script)\b.*?</\1>", "", html)

REPORT = {"ok": False, "checks": [{"key": "home", "label": "หน้าแรก", "status": "ok"},
                                  {"key": "robots", "label": "robots.txt กันบอท AI", "status": "fail", "detail": "Disallow: / สำหรับ GPTBot"},
                                  {"key": "schema", "label": "ข้อมูลโครงสร้าง", "status": "ok"}]}


def mk_lead(dom, **kw):
    lid = db.add_lead(f"https://{dom}", dom, source="audit", ip="1.2.3.4")
    tok = L.new_token()
    db.update_lead(lid, token=tok, audit_json=json.dumps(REPORT, ensure_ascii=False), audit_at=db.now(), **kw)
    return lid, tok


lid, tok = mk_lead("shop-a.co.th")

# ---------- 05: flag ปิด = หน้าเดิม ----------
r = c.get(f"/audit/{tok}")
assert r.status_code == 200 and "AI Visibility" in r.text and "ภาพรวม 4 ด้าน" not in r.text

# ---------- 05: flag เปิด · ยังไม่มีรายงานเต็ม/ผล AI ----------
os.environ["GEO_FLAG_REPORT_V2"] = "1"
r = c.get(f"/audit/{tok}")
t = visible(r.text)
assert r.status_code == 200 and "ภาพรวม 4 ด้าน" in t, t[:400]
for title in ("ความพร้อมของเว็บ", "การปรากฏในผลค้นเว็บ", "AI พูดถึงแบรนด์", "AI อ้างอิงเว็บของคุณ"):
    assert title in t, title
assert "noindex" in r.headers.get("x-robots-tag", "") and "no-store" in r.headers.get("cache-control", "")
assert "2/3" in t and "มีข้อที่ต้องแก้" in t                                        # หน้าแรกผ่าน 2 จาก 3
assert t.count("ยังไม่ได้ทดสอบ") >= 3 and "ตรวจแล้ว 1 จาก 4 ส่วน" in t
assert not re.search(r"0/\d", t)                                                    # ช่องที่ไม่ได้ทดสอบไม่มีตัวเลข 0
assert "robots.txt กันบอท AI" in t and "ไม่ใช่คำตอบ AI" in t                         # สิ่งที่ควรทำมาจากข้อที่ตกจริง · บอกว่าผลค้นเว็บไม่ใช่ AI
assert "?view=full" in t and "ข้อมูลจาก Google Search Console" in t                 # ขอบเขต: บอกสิ่งที่ไม่ได้ตรวจ
assert 'action="/audit/' + tok + '/contact"' in r.text                               # flag ขอแผนปิด = ฟอร์มเดิม
assert "AI Visibility" in c.get(f"/audit/{tok}?view=full").text                       # ตารางละเอียดเดิมยังเปิดได้
assert "ภาพรวม 4 ด้าน" not in c.get(f"/audit/{tok}?lang=en").text                     # ภาษาอื่น = หน้าเดิม (หน้าใหม่ยังเป็นไทย)
c.get("/audit/" + tok + "?lang=th")

# ---------- 05: มีรายงานเต็ม + ผล AI (บางคำถามล้ม = partial) ----------
deep = {"state": "done", "at": db.now(), "brand": "ShopA", "keywords": ["ร้านกาแฟ บางนา", "กาแฟดริป"],
        "crawl": {"n": 12, "truncated": False}, "health": {"score": 70, "errors": 2, "warnings": 1, "urls": 12},
        "issues": [{"key": "noindex", "label": "หน้าถูกตั้ง noindex", "severity": "Error", "fix": "เอา noindex ออก", "urls": ["https://shop-a.co.th/menu"], "n_urls": 1, "count": 1}],
        "ranking": {"rows": [{"keyword": "ร้านกาแฟ บางนา", "position": 4}, {"keyword": "กาแฟดริป", "error": "Timeout"}],
                    "competitors": [{"domain": "rival.co.th", "hits": 2}], "page1": 1, "n": 2, "ok": 1}}
ai = {"state": "done", "at": db.now(), "engine": "chatgpt", "asked": 3, "mentioned": 1, "cited": 0,
      "rows": [{"prompt": "a", "cited": False, "named": True}, {"prompt": "b", "cited": False, "named": False},
               {"prompt": "c", "cited": False, "named": False}, {"prompt": "d", "error": "timeout"}]}
db.update_lead(lid, deep_json=json.dumps(deep, ensure_ascii=False), deep_ai_json=json.dumps(ai, ensure_ascii=False))
vm = RV.build(db.get_lead(lid))
lanes = {x["key"]: x for x in vm["lanes"]}
assert lanes["web_search_presence"]["state"] == "partial" and lanes["web_search_presence"]["value"] == "1/1"
assert lanes["ai_brand_mention"]["state"] == "partial" and lanes["ai_brand_mention"]["value"] == "1/3"
assert lanes["ai_citation"]["value"] == "0/3"                                         # ทดสอบแล้วไม่พบ = 0 ได้ (ตัวหาร = ที่ถามสำเร็จ)
assert vm["tested"] == 4 and vm["actions"][0]["title"] == "หน้าถูกตั้ง noindex" and vm["actions"][0]["page"] == "/menu"
assert vm["actions"][1]["title"] == "robots.txt กันบอท AI"
t = visible(c.get(f"/audit/{tok}").text)
assert "ตรวจแล้ว 4 จาก 4 ส่วน" in t and "ค้นไม่สำเร็จ 1 คำ (ไม่นับ)" in t and "ถามไม่สำเร็จ 1 ข้อ (ไม่นับ)" in t

# ระบบไม่พร้อม = ไม่มีตัวเลข
db.update_lead(lid, deep_ai_json=json.dumps({"state": "error", "error": "x"}))
x = {l["key"]: l for l in RV.build(db.get_lead(lid))["lanes"]}["ai_citation"]
assert x["state"] == "unavailable" and x["value"] is None and x["denominator"] is None
rk0 = RV.search_presence({"state": "done", "ranking": {"rows": [{"error": "x"}], "n": 1, "ok": 0, "page1": 0}}, "done")
assert rk0["state"] == "unavailable" and rk0["value"] is None
# เปิดหน้าแรกไม่ได้ = ความพร้อมเว็บ "ระบบไม่พร้อม" ไม่ใช่ "ไม่ผ่าน"
hd = RV.readiness({"checks": [{"key": "home", "status": "fail"}]}, {}, {})
assert hd["state"] == "unavailable" and hd["value"] is None

# SoV เดิมไม่ถูกเรียกว่า AI อีก
for f in ("app/templates/rank.html", "app/templates/brand.html", "app/templates/brand_content.html"):
    s = open(f, encoding="utf-8").read()
    assert "SoV บน AI" not in s and "โผล่ใน AI" not in s, f

# ---------- 06: flag ขอแผนปิด = 404 ----------
assert c.post(f"/audit/{tok}/plan", data={"name": "x"}).status_code in (403, 404)
os.environ["GEO_FLAG_PLAN_REQUEST_V2"] = "1"
lid2, tok2 = mk_lead("shop-b.co.th")
r = c.get(f"/audit/{tok2}")
assert 'action="/audit/' + tok2 + '/plan#plan"' in r.text and "ส่งคำขอแผน" in r.text
csrf = re.search(r'name="csrf_token" value="([^"]+)"', r.text).group(1)
idem = re.search(r'name="idem_key" value="([0-9a-f]{32})"', r.text).group(1)
assert 'name="consent_marketing" value="1">' in r.text                                # การตลาดไม่ติ๊กไว้ก่อน
good = {"csrf_token": csrf, "idem_key": idem, "name": "สมชาย", "role": "เจ้าของ", "contact_channel": "phone",
        "contact_value": "081 234 5678", "desired_window": "1m", "budget": "", "web_readiness": "agency", "consent_fulfil": "1"}

# ไม่มี CSRF = 403
assert c.post(f"/audit/{tok2}/plan", data={k: v for k, v in good.items() if k != "csrf_token"}).status_code == 403
# ข้อมูลผิด = 422 + บอกวิธีแก้ + ไม่บันทึก
r = c.post(f"/audit/{tok2}/plan", data={**good, "contact_value": "abc", "consent_fulfil": ""})
assert r.status_code == 422 and "เบอร์โทรควรเป็นตัวเลข" in r.text and "ติ๊กยินยอมให้ติดต่อกลับก่อน" in r.text
assert 'value="สมชาย"' in r.text and not db.list_plan_requests(lid2)                    # ค่าที่กรอกไว้ไม่หาย
assert c.post(f"/audit/{tok2}/plan", data={**good, "contact_channel": "fax"}).status_code == 422
# บันทึกไม่สำเร็จ = ไม่บอกว่าสำเร็จ
_add = db.add_plan_request
db.add_plan_request = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("db down"))
r = c.post(f"/audit/{tok2}/plan", data=good)
db.add_plan_request = _add
assert r.status_code == 500 and "บันทึกคำขอไม่สำเร็จ" in r.text and "ได้รับคำขอแผนแล้ว" not in r.text and not alerts
# สำเร็จ
r = c.post(f"/audit/{tok2}/plan", data=good, follow_redirects=False)
assert r.status_code == 303 and r.headers["location"].endswith("#plan")
rows = db.list_plan_requests(lid2)
assert len(rows) == 1 and rows[0]["contact_channel"] == "phone" and rows[0]["desired_window"] == "1m" and rows[0]["budget"] is None
cons = {x["purpose"]: x for x in json.loads(rows[0]["consents_json"])}
assert cons["fulfil_request"]["granted"] and cons["marketing"]["granted"] is False and cons["marketing"]["notice_version"] == PR.NOTICE_VERSION
lead2 = db.get_lead(lid2)
assert lead2["consent"] == 1 and lead2["name"] == "สมชาย" and "081 234 5678" in lead2["contact"]
assert [e["kind"] for e in db.list_lead_events(lid2)].count("wants_call") == 1 and pipeline.wants_call_pending(lid2)
assert len(alerts) == 1 and "081" not in alerts[0]                                      # แจ้งทีม ไม่ใส่เบอร์ในข้อความแจ้ง
t = visible(c.get(f"/audit/{tok2}").text)
assert "ได้รับคำขอแผนแล้ว" in t and "โทรศัพท์" in t and "ยังไม่มีการนัดเวลา" in t
# กดส่งซ้ำ (idem_key เดิม) = ไม่สร้างซ้ำ ไม่แจ้งซ้ำ
r = c.post(f"/audit/{tok2}/plan", data=good, follow_redirects=False)
assert r.status_code == 303 and len(db.list_plan_requests(lid2)) == 1 and len(alerts) == 1
assert [e["kind"] for e in db.list_lead_events(lid2)].count("wants_call") == 1
# idem_key ของรายงานอื่นใช้ข้ามไม่ได้
lid3, tok3 = mk_lead("shop-c.co.th")
r = c.post(f"/audit/{tok3}/plan", data=good)
assert r.status_code == 500 and not db.list_plan_requests(lid3)
# ส่งใหม่ (key ใหม่) ภายใน 10 นาที = เก็บ แต่ไม่แจ้งทีมซ้ำ
r = c.post(f"/audit/{tok2}/plan", data={**good, "idem_key": "a" * 32, "contact_channel": "line", "contact_value": "@shopb"}, follow_redirects=False)
assert r.status_code == 303 and len(db.list_plan_requests(lid2)) == 2 and len(alerts) == 1
# honeypot
r = c.post(f"/audit/{tok3}/plan", data={**good, "idem_key": "b" * 32, "website": "spam"}, follow_redirects=False)
assert r.status_code == 303 and not db.list_plan_requests(lid3)

# Sales brief: แยกยืนยัน/เดา/ไม่ทราบ
db.update_lead(lid2, deep_json=json.dumps(deep, ensure_ascii=False))
b = PR.brief(db.get_lead(lid2), RV.build(db.get_lead(lid2)))
conf = dict(b["confirmed"]); inf = dict(b["inferred"])
assert conf["ติดต่อทาง"] == "LINE: @shopb" and "ชื่อแบรนด์" not in conf and inf["ชื่อแบรนด์"] == "ShopA"
assert inf["คู่แข่งบน Google"] == "rival.co.th" and "งบประมาณ" in b["unknown"] and b["marketing"] is False
assert b["findings"][0]["title"] == "หน้าถูกตั้ง noindex"
assert PR.brief(db.get_lead(lid3)) is None
from app.main import _lead_ctx
assert _lead_ctx(db.get_lead(lid2))["brief"]["request"]["id"] == db.list_plan_requests(lid2)[0]["id"]

# ---------- 06: นัดคุย 30 นาที (หน้าแรกใหม่) ----------
assert c.get("/callback").status_code == 404                                          # marketing_v2 ปิด = ไม่มีหน้า
os.environ["GEO_FLAG_MARKETING_V2"] = "1"
assert 'href="/callback"' in c.get("/").text                                          # ปุ่มนัดคุยกลับมาที่หน้าแรกใหม่
r = c.get("/callback")
assert r.status_code == 200 and "ขอให้ทีมติดต่อกลับ" in r.text
csrf = re.search(r'name="csrf_token" value="([^"]+)"', r.text).group(1)
idem = re.search(r'name="idem_key" value="([0-9a-f]{32})"', r.text).group(1)
cb = {"csrf_token": csrf, "idem_key": idem, "name": "วิภา", "contact_channel": "email", "contact_value": "Wipa@Example.co.th",
      "message": "อยากคุยเรื่องเว็บโรงงาน", "consent_fulfil": "1", "consent_marketing": "1"}
assert c.post("/callback", data={**cb, "contact_value": "nope"}).status_code == 422
n0 = len(db.list_contacts())
r = c.post("/callback", data=cb, follow_redirects=False)
assert r.status_code == 303 and r.headers["location"] == "/callback?done=1"
row = db.list_contacts()[0]
assert len(db.list_contacts()) == n0 + 1 and row["plan"] == "นัดคุย" and row["contact"] == "อีเมล: wipa@example.co.th"
assert "รับข่าวสาร: ยินยอม" in row["message"] and len(alerts) == 2
assert c.post("/callback", data=cb, follow_redirects=False).status_code == 303 and len(db.list_contacts()) == n0 + 1   # กดซ้ำ
assert "ทีมจะติดต่อกลับเพื่อนัดเวลา" in c.get("/callback?done=1").text

print("report_plan_smoke OK")
