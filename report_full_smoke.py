"""ทดสอบ stage 05-full: รายงานเต็ม (หลักฐาน ตัวเลขดิบ เทียบคู่แข่ง ตัวอย่างร่าง ประวัติเวอร์ชัน ขั้นต่อไป)
fixture 7 กรณีตาม PROMPT 05: complete · partial · no AI · provider failure · search-only legacy · corrected context · changed protocol
ไม่ยิงเครือข่าย · DB ชั่วคราว"""
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
import app.business_context as BC
import app.plan_request as PR
import app.report_view as RV
from app.main import app

db.init_db()
c = TestClient(app)
XSS = "<script>alert(1)</script>"
REPORT = {"ok": True, "checks": [{"key": "home", "label": "หน้าแรก", "status": "ok"},
                                 {"key": "robots", "label": "robots.txt กันบอท", "status": "fail", "detail": f"Disallow: / {XSS}"},
                                 {"key": "llms", "label": "llms.txt", "status": "ok"}]}
PAGES = [{"url": "https://shop-a.co.th/", "status": 200, "title": "หน้าแรก", "desc": ""},
         {"url": "https://shop-a.co.th/about", "status": 200, "title": "เกี่ยวกับเรา", "desc": "x"},
         {"url": "https://shop-a.co.th/old", "status": 404, "title": "", "desc": ""}]
ISSUES = [{"key": "4xx", "group": "Crawl & Index", "severity": "Error", "label": "ลิงก์เสีย 4XX", "fix": "แก้ลิงก์",
           "urls": ["https://shop-a.co.th/old", "javascript:alert(1)", "https://evil.example/x"], "n_urls": 3, "count": 3}]
ROWS = [{"keyword": "ให้เช่าโกดัง บางนา", "top": ["rival.co.th", "x.com"], "position": None, "url": None, "depth": 20},
        {"keyword": "คลังสินค้าให้เช่า", "top": ["shop-a.co.th", "rival.co.th"], "position": 1, "url": "https://shop-a.co.th/", "depth": 20}]


def t1(provider="serper", pid="search-rank-v1", ver=1, rows=None, ok=None, proto=True):
    rows = rows if rows is not None else ROWS
    rk = {"rows": rows, "competitors": [{"domain": "rival.co.th", "hits": 2}], "n": len(rows),
          "ok": ok if ok is not None else sum(1 for r in rows if not r.get("error")),
          "page1": sum(1 for r in rows if r.get("position") and r["position"] <= 10), "depth": 20}
    if proto:
        rk["protocol"] = {"id": pid, "version": ver, "provider": provider}
    return {"state": "done", "at": db.now(), "brand": "ShopA", "keywords": [r["keyword"] for r in rows], "crawl": {"n": 3},
            "pages": PAGES, "issues": ISSUES, "health": {"score": 80, "errors": 1, "warnings": 0, "notices": 0, "urls": 3},
            "ranking": rk, **({"protocol": {"id": "crawl-v1", "version": 1, "provider": "httpx"}} if proto else {})}


AI_ROWS = [{"prompt": "โกดังบางนา เจ้าไหนดี", "status": "cited", "cited": True, "named": True, "competitors": {"rival.co.th": "named"},
            "sources": ["shop-a.co.th", "rival.co.th"], "excerpt": f"แนะนำ ShopA <img src=x onerror=alert(1)>"},
           {"prompt": "เช่าคลังสินค้าราคาเท่าไหร่", "status": "none", "cited": False, "named": False, "competitors": {"rival.co.th": ""},
            "sources": ["x.com"]}]


def t2(rows=None, state="done", ctx_version=0):
    rows = rows if rows is not None else AI_ROWS
    done = [r for r in rows if "status" in r]
    return {"state": state, "at": db.now(), "engine": "chatgpt", "rows": rows, "asked": len(done),
            "mentioned": sum(1 for r in done if r["cited"] or r["named"]), "cited": sum(1 for r in done if r["cited"]),
            "competitors": ["rival.co.th"], "comp_mentions": {"rival.co.th": 1}, "context_version": ctx_version,
            "protocol": {"id": "ai-ask-v1", "version": 1, "provider": "chatgpt"}} if state == "done" else {"state": state, "at": db.now(), "error": "x"}


def mk(dom, deep=None, ai=None, report=REPORT):
    lid = db.add_lead(f"https://{dom}", dom, source="audit", ip="1.2.3.4")
    tok = L.new_token()
    db.update_lead(lid, token=tok, audit_json=json.dumps(report, ensure_ascii=False), audit_at=db.now(),
                   deep_json=json.dumps(deep, ensure_ascii=False) if deep else None, deep_ai_json=json.dumps(ai, ensure_ascii=False) if ai else None)
    return lid, tok


def lane(vm, key):
    return next(x for x in vm["lanes"] if x["key"] == key)


F.set_enabled("report_v2", True)

# ---------- 1) complete ----------
lid, tok = mk("shop-a.co.th", t1(), t2())
vm = RV.build(db.get_lead(lid))
assert vm["summary"]["status"] == "completed", vm["summary"]
s = lane(vm, "web_search_presence")
assert s["state"] == "found" and s["m"]["provider"] == "serper" and s["m"]["protocol_id"] == "search-rank-v1"
assert (s["m"]["attempted"], s["m"]["succeeded"], s["m"]["failed"], s["m"]["denominator"]) == (2, 2, 0, 2) and s["m"]["measured_at"]
a = lane(vm, "ai_brand_mention")
assert a["m"]["provider"] == "chatgpt" and a["evidence_refs"] and lane(vm, "ai_citation")["evidence_refs"]
assert all(vm["evidence"][r]["kind"] == "ai_answer" for r in a["evidence_refs"])
act = vm["actions"][0]
assert act["confidence"] == "high" and act["effort"] and act["owner"] in ("either", "customer_web_team") and act["evidence_refs"]
urls = vm["evidence"][act["evidence_refs"][0]]["urls"]
assert urls[0]["href"] == "https://shop-a.co.th/old" and urls[1]["href"] is None and urls[2]["href"] is None   # ลิงก์ออกเฉพาะโดเมนลูกค้า http(s)
assert RV.safe_url("javascript:alert(1)", "shop-a.co.th") is None and RV.safe_url("https://shop-a.co.th.evil.net/", "shop-a.co.th") is None
assert RV.safe_url("https://blog.shop-a.co.th/a", "shop-a.co.th")
assert vm["competitor"]["domain"] == "rival.co.th" and vm["competitor"]["gaps"] == ["ให้เช่าโกดัง บางนา"]
assert any("พูดถึง rival.co.th ใน 1 จาก 2" in f for f in vm["competitor"]["facts"])
assert vm["draft"] is None                                                                  # ไม่มีข้อมูลธุรกิจ = ไม่สร้างตัวอย่าง
assert vm["ai_tests"][0]["result"] == "cited" and vm["ai_tests"][1]["result"] == "none"
r = c.get(f"/audit/{tok}")
html = r.text
assert r.headers["cache-control"] == "private, no-store" and "noindex" in r.headers["x-robots-tag"] and r.headers["referrer-policy"] == "no-referrer"
assert XSS not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html                # ข้อความดิบถูก escape
assert "<img src=x" not in html and "&lt;img src=x" in html
assert 'href="javascript:' not in html and 'href="https://evil.example' not in html
assert 'href="https://shop-a.co.th/old"' in html
for sec in ('id="summary"', 'id="next-step"', 'id="ai-tests"', 'id="competitor"', 'id="methodology"', "ตัวเลขดิบและหลักฐาน", "ดูหลักฐาน"):
    assert sec in html, sec
assert 'data-step="request_plan"' in html
assert "ยอดขายที่เสีย" not in html and "การันตีอันดับ" not in html.replace("ไม่การันตีอันดับ", "")
assert len(db.list_report_versions(lid)) == 1
c.get(f"/audit/{tok}")
assert len(db.list_report_versions(lid)) == 1                                             # ผลวัดไม่เปลี่ยน = ไม่เพิ่มเวอร์ชัน

# ---------- 2) partial: ค้นล้ม 1 คำ · ถาม AI ล้ม 1 ข้อ ----------
rows = ROWS + [{"keyword": "โกดังเย็น", "error": "Timeout", "top": [], "position": None, "url": None}]
lid2, tok2 = mk("partial.co.th", t1(rows=rows), t2(rows=AI_ROWS + [{"prompt": "q3", "error": "timeout"}]))
vm = RV.build(db.get_lead(lid2))
s = lane(vm, "web_search_presence")
assert s["state"] == "partial" and (s["m"]["attempted"], s["m"]["succeeded"], s["m"]["failed"]) == (3, 2, 1) and s["denominator"] == 2
assert lane(vm, "ai_brand_mention")["state"] == "partial" and lane(vm, "ai_brand_mention")["m"]["failed"] == 1
assert vm["summary"]["status"] == "partial" and any("ตัวหารจึงเล็กลง" in u for u in vm["summary"]["uncertainty"])
assert any(t["result"] == "error" and "ไม่นับ" in t["result_label"] for t in vm["ai_tests"])
html = c.get(f"/audit/{tok2}").text
assert "ล้ม 1" in html

# ---------- 3) no AI ----------
lid3, tok3 = mk("noai.co.th", t1(), None)
vm = RV.build(db.get_lead(lid3))
for k in ("ai_brand_mention", "ai_citation"):
    x = lane(vm, k)
    assert x["state"] == "not_tested" and x["value"] is None and x["m"] is None and x["evidence_refs"] == []
assert vm["ai_tests"] == [] and vm["summary"]["status"] == "partial"
assert any("ยังไม่ได้ทดสอบ" in u for u in vm["summary"]["uncertainty"])
html = c.get(f"/audit/{tok3}").text
card = html.split('id="lane-ai_brand_mention"')[1].split('id="lane-ai_citation"')[0]
assert "rp-num" not in card and "0/" not in card                                         # ไม่ได้ทดสอบ = ไม่มีตัวเลข ไม่ใช่ 0
assert 'id="ai-tests"' not in html

# ---------- 4) provider failure ----------
lid4, tok4 = mk("down.co.th", t1(rows=[dict(ROWS[0], error="Timeout")], ok=0), t2(state="error"))
vm = RV.build(db.get_lead(lid4))
s = lane(vm, "web_search_presence")
assert s["state"] == "unavailable" and s["value"] is None and s["m"]["failed"] == 1
assert lane(vm, "ai_brand_mention")["state"] == "unavailable" and lane(vm, "ai_brand_mention")["value"] is None
assert vm["summary"]["status"] == "provider_unavailable" and any("ไม่ใช่ผลว่าไม่ผ่าน" in u for u in vm["summary"]["uncertainty"])
assert c.get(f"/audit/{tok4}").status_code == 200

# ---------- 5) search-only legacy (รอบเก่าไม่มี protocol) ----------
lid5, tok5 = mk("legacy.co.th", t1(proto=False), None)
vm = RV.build(db.get_lead(lid5))
s = lane(vm, "web_search_presence")
assert s["m"]["protocol_id"] is None and s["m"]["provider"] is None and s["title"] == "การปรากฏในผลค้นเว็บ"
assert lane(vm, "ai_brand_mention")["state"] == "not_tested"                              # ผลค้นเว็บเก่าไม่กลายเป็นตัวเลข AI
assert RV.record(db.get_lead(lid5), vm) == 1
d = t1(proto=False, rows=ROWS[:1])
db.update_lead(lid5, deep_json=json.dumps(d, ensure_ascii=False))
html = c.get(f"/audit/{tok5}").text
assert "ไม่ได้บันทึก (รอบเก่า)" in html
h = RV.history(db.get_lead(lid5))
assert len(h) == 2 and h[0]["cells"][1]["cmp"] == "legacy_search_only" and h[1]["cells"][1]["cmp"] is None
assert 'id="history"' in html and 'data-cmp="legacy_search_only"' in html

# ---------- 6) corrected context ----------
F.set_enabled("context_v2", True); F.set_enabled("plan_request_v2", True)
lid6, tok6 = mk("ctx.co.th", t1(), t2(ctx_version=0))
lead6 = db.get_lead(lid6)
assert 'data-step="confirm_context"' in c.get(f"/audit/{tok6}").text                     # ยังไม่ยืนยัน → ขั้นต่อไปคือยืนยันข้อมูล
v, err = BC.validate({"primary_service": "ให้เช่าคลังสินค้า", "region": "บางนา", "competitor": "rival2.co.th",
                      "questions": "โกดังบางนา เจ้าไหนดี"}, lead6)
assert not err
BC.save(lead6, v)
bc = BC.view(db.get_lead(lid6))
vm = RV.build(db.get_lead(lid6), None, bc)
assert vm["competitor"]["domain"] == "rival2.co.th" and vm["competitor"]["source"] == "confirmed"
assert any("ยังไม่ได้นับการพูดถึง rival2.co.th" in f for f in vm["competitor"]["facts"])
dr = vm["draft"]
assert dr["after"]["title"] == "ให้เช่าคลังสินค้า บางนา | ShopA" and dr["requires_fact_confirmation"] and dr["guessed"] == []
assert dr["before"]["title"] == "หน้าแรก" and dr["before"]["desc"] is None
assert vm["ai_tests"][0]["origin"] == "customer" and vm["ai_tests"][1]["origin"] == "system"
html = c.get(f"/audit/{tok6}").text
assert 'id="draft"' in html and "ตัวอย่างประกอบเท่านั้น" in html and "ลูกค้าเลือก" in html
assert "จึงเทียบกับข้อมูลชุดใหม่ตรง ๆ ไม่ได้" in html and 'data-step="request_plan"' in html
pv, perr = PR.validate({"name": "สมชาย", "contact_channel": "phone", "contact_value": "0812345678", "consent_fulfil": "1", "idem_key": PR.new_key()})
assert not perr
PR.submit(db.get_lead(lid6), pv, "1.2.3.4")
assert 'data-step="request_meeting"' in c.get(f"/audit/{tok6}").text
qid = db.add_quote(lid6, "Q-1", "qtok123", "pro", 1000, 0, 1000, False, 0, 1000, "2026-12-31")
assert 'data-step="request_meeting"' in c.get(f"/audit/{tok6}").text                    # ใบเสนอราคาฉบับร่าง = ไม่แสดง
db.update_quote(qid, status="sent")
html = c.get(f"/audit/{tok6}").text
assert 'data-step="view_quote"' in html and 'href="/q/qtok123"' in html

# ---------- 7) changed protocol ----------
lid7, tok7 = mk("proto.co.th", t1(provider="serper"), t2())
c.get(f"/audit/{tok7}")
db.update_lead(lid7, deep_json=json.dumps(t1(provider="ddgs"), ensure_ascii=False))
c.get(f"/audit/{tok7}")
db.update_lead(lid7, deep_json=json.dumps(t1(provider="ddgs", ver=2), ensure_ascii=False))
html = c.get(f"/audit/{tok7}").text
h = RV.history(db.get_lead(lid7))
assert [x["version"] for x in h] == [3, 2, 1]
assert h[1]["cells"][1]["cmp"] == "qualified" and "serper → ddgs" in h[1]["cells"][1]["why"]
assert h[0]["cells"][1]["cmp"] == "non_comparable"
assert h[0]["cells"][2]["cmp"] == "comparable"                                            # AI ไม่เปลี่ยน = เทียบได้
assert 'data-cmp="non_comparable"' in html and 'data-cmp="qualified"' in html
assert RV.compare({"state": "found", "protocol_id": "a", "protocol_version": 1, "provider": "p", "qset": "1"},
                  {"state": "not_tested"}, "ai_citation") == (None, "")

# ---------- flag ปิด = หน้าเดิม · ไม่บันทึกเวอร์ชัน ----------
F.set_enabled("report_v2", False)
lid8, tok8 = mk("off.co.th", t1(), t2())
assert 'id="summary"' not in c.get(f"/audit/{tok8}").text and db.list_report_versions(lid8) == []
print("report_full_smoke OK")
