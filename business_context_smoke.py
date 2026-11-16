"""ทดสอบ stage 04B: ยืนยันข้อมูลธุรกิจในรายงาน (flag context_v2 + report_v2)
ไม่ยิงเครือข่าย (ฉีดตัวถาม AI ปลอม) · DB ชั่วคราว"""
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
import app.flags as F
import app.deep_audit as D
import app.business_context as BC
import app.plan_request as PR
import app.report_view as RV
from app.main import app

db.init_db()
c = TestClient(app)
REPORT = {"ok": True, "checks": [{"key": "home", "label": "หน้าแรก", "status": "ok"}]}
T1 = {"state": "done", "at": db.now(), "brand": "ShopA", "keywords": ["ให้เช่าโกดัง บางนา", "คลังสินค้าให้เช่า"],
      "crawl": {"n": 3}, "pages": [], "issues": [], "health": {"score": 90, "errors": 0, "warnings": 0, "notices": 0, "urls": 3},
      "ranking": {"rows": [], "competitors": [{"domain": "shop-a.co.th", "hits": 3}, {"domain": "rival.co.th", "hits": 2}], "n": 2, "ok": 2, "page1": 0, "depth": 20}}


def mk_lead(dom, t1=T1):
    lid = db.add_lead(f"https://{dom}", dom, source="audit", ip="1.2.3.4")
    tok = L.new_token()
    db.update_lead(lid, token=tok, audit_json=json.dumps(REPORT), audit_at=db.now(), deep_json=json.dumps(t1, ensure_ascii=False) if t1 else None)
    return lid, tok


def csrf_of(html):
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


lid, tok = mk_lead("shop-a.co.th")
lead = db.get_lead(lid)

# ---------- ที่ระบบเดา: มีหลักฐาน · ไม่เดาพื้นที่/ลูกค้า · ไม่เอาเว็บตัวเองเป็นคู่แข่ง ----------
inf = BC.inferred(lead)
assert inf["primary_service"] == {"value": "ให้เช่าโกดัง บางนา", "source": "inferred", "evidence": inf["primary_service"]["evidence"]}
assert inf["region"]["source"] == "unknown" and inf["audience"]["source"] == "unknown"
assert inf["competitor"]["value"] == "rival.co.th"
assert inf["questions"] and all(q["is_search_volume"] is False and q["origin"] == "suggested" for q in inf["questions"]) and len(inf["questions"]) <= 5
lid0, tok0 = mk_lead("empty.co.th", t1=None)
e = BC.inferred(db.get_lead(lid0))
assert all(e[k]["source"] == "unknown" for k, _ in BC.FIELDS) and e["questions"] == []                      # ไม่มีรายงานเต็ม = ไม่เดา

# ---------- flag ปิด = ไม่มีส่วนนี้ · route 404 ----------
F.set_enabled("report_v2", True); F.set_enabled("plan_request_v2", True)   # ฟอร์มขอแผนมี csrf_token ให้ใช้ตอน flag บริบทยังปิด
html = c.get(f"/audit/{tok}").text
assert 'id="context"' not in html
assert c.post(f"/audit/{tok}/context", data={"csrf_token": csrf_of(html), "primary_service": "x"}).status_code == 404
F.set_enabled("context_v2", True)

# ---------- แสดงในรายงาน: ป้ายระบบเดา / ยังไม่ทราบ ----------
html = c.get(f"/audit/{tok}").text
sec = html.split('id="context"')[1].split("</section>")[0]
assert "ระบบเดา" in sec and "ยังไม่ทราบ" in sec and "ไม่ใช่ข้อมูลปริมาณการค้น" in sec and "rival.co.th" in sec
assert "<details>" in sec and 'value="ให้เช่าโกดัง บางนา"' in sec                                          # ฟอร์มเติมค่าที่เดาไว้ให้แก้
csrf = csrf_of(html)

# ---------- CSRF / ข้อมูลผิด / เว็บตัวเองเป็นคู่แข่ง ----------
assert c.post(f"/audit/{tok}/context", data={"primary_service": "x"}).status_code == 403
r = c.post(f"/audit/{tok}/context", data={"csrf_token": csrf, "primary_service": "", "competitor": "www.shop-a.co.th",
                                          "questions": "\n".join(f"q{i}" for i in range(7)), "region": "ก" * 81})
assert r.status_code == 422
sec = r.text.split('id="context"')[1].split("</section>")[0]
assert "<details open>" in sec and 'role="alert"' in sec
for k in ("primary_service", "competitor", "questions", "region"):
    assert f'href="#c-{k}"' in sec, k
assert "q6" in sec                                                                                       # ค่าที่กรอกไว้ไม่หาย
assert db.count_business_contexts(lid) == 0
assert c.post(f"/audit/{tok}/context", data={"csrf_token": csrf, "primary_service": "x", "competitor": "not a url"}).status_code == 422
assert c.post(f"/audit/{tok}/context", data={"csrf_token": csrf, "primary_service": "x", "website": "bot"}, follow_redirects=False).status_code in (302, 303)
assert db.count_business_contexts(lid) == 0                                                             # honeypot ไม่บันทึก

# ---------- บันทึก = เวอร์ชันใหม่ · เว้นว่าง = ยังไม่ทราบ ----------
qs = "ให้เช่าโกดัง บางนา ที่ไหนดี\nโกดังใกล้สุวรรณภูมิ ขนาด 1,000 ตร.ม.\n\nโกดังใกล้สุวรรณภูมิ ขนาด 1,000 ตร.ม."
r = c.post(f"/audit/{tok}/context", data={"csrf_token": csrf, "primary_service": "  ให้เช่าคลังสินค้า  ", "region": "บางนา",
                                          "audience": "", "competitor": "https://www.Rival2.co.th/about", "questions": qs}, follow_redirects=False)
assert r.status_code in (302, 303) and r.headers["location"].endswith("?ctx=1#context")
cur = BC.current(db.get_lead(lid))
assert cur["version"] == 1 and cur["confirmed"] and cur["updated_by"] == "customer"
f = cur["fields"]
assert f["primary_service"] == {"value": "ให้เช่าคลังสินค้า", "source": "confirmed"} and f["audience"]["source"] == "unknown"
assert f["competitor"]["value"] == "rival2.co.th"
assert [q["text"] for q in f["questions"]] == ["ให้เช่าโกดัง บางนา ที่ไหนดี", "โกดังใกล้สุวรรณภูมิ ขนาด 1,000 ตร.ม."]          # ตัดซ้ำ/บรรทัดว่าง
assert [q["origin"] for q in f["questions"]] == ["suggested", "customer"] and all(q["is_search_volume"] is False for q in f["questions"])
html = c.get(f"/audit/{tok}?ctx=1").text
sec = html.split('id="context"')[1].split("</section>")[0]
assert "บันทึกแล้ว (เวอร์ชัน 1)" in sec and "ลูกค้ายืนยัน" in sec and "ระบบเดา" not in sec
assert any("ยืนยันข้อมูลธุรกิจ (เวอร์ชัน 1)" in (e["note"] or "") for e in db.list_lead_events(lid))
c.post(f"/audit/{tok}/context", data={"csrf_token": csrf, "primary_service": "ให้เช่าคลังสินค้า", "region": "บางนา"})
assert BC.current(db.get_lead(lid))["version"] == 2 and db.count_business_contexts(lid) == 2               # เก็บทุกเวอร์ชัน ไม่แก้ทับ
c.post(f"/audit/{tok}/context", data={"csrf_token": csrf, "primary_service": "ให้เช่าคลังสินค้า", "region": "บางนา", "competitor": "rival2.co.th",
                                      "questions": "ให้เช่าโกดัง บางนา ที่ไหนดี\nโกดังใกล้สุวรรณภูมิ ขนาด 1,000 ตร.ม.\nShopA ดีไหม"})
assert BC.current(db.get_lead(lid))["version"] == 3

# ---------- ทดสอบ AI ใช้คำถาม/คู่แข่งที่ยืนยัน + จำเวอร์ชัน ----------
asked = []
D.HOOKS.update(ask=lambda q: asked.append(q) or {"ok": True, "text": "แนะนำ rival2 และ shopa", "citations": [], "cost_usd": 0},
               llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")), engine="chatgpt")
d = D.run_ai(lid, force=True)
assert d["state"] == "done" and d["context_version"] == 3, d
assert asked[:2] == ["ให้เช่าโกดัง บางนา ที่ไหนดี", "โกดังใกล้สุวรรณภูมิ ขนาด 1,000 ตร.ม."]
assert "ShopA ดีไหม" not in asked                                                                        # คำถามที่มีชื่อแบรนด์ตัวเองไม่ใช้วัด
assert any("ให้เช่าคลังสินค้า" in q for q in asked) and len(asked) <= D.N_PROMPTS                        # บริการหลักที่ยืนยันใช้สร้างคำถามเพิ่ม
assert d["competitors"][0] == "rival2.co.th"
assert BC.ai_comparability(db.get_lead(lid), D.tier2(db.get_lead(lid))) is None
c.post(f"/audit/{tok}/context", data={"csrf_token": csrf, "primary_service": "ให้เช่าโรงงาน"})
note = BC.ai_comparability(db.get_lead(lid), D.tier2(db.get_lead(lid)))
assert note and "เวอร์ชัน 3" in note and "เวอร์ชัน 4" in note
assert note in c.get(f"/audit/{tok}").text.replace("&#39;", "'")
lid2, tok2 = mk_lead("shop-b.co.th")                                                                      # ทดสอบก่อนยืนยัน → ใช้วิธีเดิม · version 0
asked.clear()
assert D.run_ai(lid2, force=True)["context_version"] == 0 and BC.for_ai(db.get_lead(lid2)) is None
BC.save(db.get_lead(lid2), {"primary_service": "x", "region": "", "audience": "", "competitor": "", "questions": []})
assert "ก่อนคุณยืนยันข้อมูลธุรกิจ" in BC.ai_comparability(db.get_lead(lid2), D.tier2(db.get_lead(lid2)))

# ---------- Sales Brief แยกที่มา ----------
b = None
v, err = PR.validate({"name": "สมชาย", "contact_channel": "phone", "contact_value": "081 234 5678", "consent_fulfil": "1"})
PR.submit(db.get_lead(lid), v)
b = PR.brief(db.get_lead(lid), RV.build(db.get_lead(lid)))
conf = dict(b["confirmed"])
assert conf["บริการหลักที่อยากให้ลูกค้าค้นเจอ"] == "ให้เช่าโรงงาน" and conf["ข้อมูลธุรกิจเวอร์ชัน"] == "4"
assert "พื้นที่ให้บริการ" in b["unknown"] and "ลูกค้าเป้าหมาย" in b["unknown"]
PR.submit(db.get_lead(lid0), {**v, "idem_key": PR.new_key()})
b0 = PR.brief(db.get_lead(lid0), RV.build(db.get_lead(lid0)))
assert "ข้อมูลธุรกิจ" in b0["unknown"]                                                                  # ยังไม่ยืนยัน = ทีมรู้ว่าต้องถาม

# ---------- เพดานเวอร์ชัน ----------
BC.MAX_VERSIONS = 4
r = c.post(f"/audit/{tok}/context", data={"csrf_token": csrf, "primary_service": "y"})
assert r.status_code == 409 and "ครบ 4 ครั้ง" in r.text and BC.current(db.get_lead(lid))["version"] == 4

# ---------- ภาษาอื่น / ?view=full ยังเป็นหน้าเดิม ----------
assert 'id="context"' not in c.get(f"/audit/{tok}?view=full").text
F.set_enabled("context_v2", False)
assert 'id="context"' not in c.get(f"/audit/{tok}").text

print("business_context_smoke OK")
