"""ทดสอบ ai_serp แบบไม่ยิงเน็ต — ถอด text_blocks/references ของ SerpApi และตัดสินการถูกพูดถึง"""
import app.ai_serp as a

OV = {"ai_overview": {
    "text_blocks": [
        {"type": "paragraph", "snippet": "โกดังให้เช่าในสมุทรปราการมีหลายผู้ให้บริการ เช่น JKP Property Agency และ DDproperty"},
        {"type": "list", "list": [{"title": "JKP Property", "snippet": "นายหน้าโกดัง"}, {"title": "WHA", "snippet": "นิคม"}]},
        {"type": "heading", "snippet": "ข้อควรรู้", "text_blocks": [{"type": "paragraph", "snippet": "เช็คสัญญาเช่า"}]},
    ],
    "references": [{"title": "JKP", "link": "https://jkppropertyagency.com/geo/73", "source": "jkp"},
                   {"title": "DD", "link": "https://www.ddproperty.com/x"}, {"title": "no link"}]}}
ov = a.extract_overview(OV)
assert ov["present"] and "JKP Property Agency" in ov["text"] and "เช็คสัญญาเช่า" in ov["text"] and "นายหน้าโกดัง" in ov["text"], ov
assert ov["sources"] == ["https://jkppropertyagency.com/geo/73", "https://www.ddproperty.com/x"], ov["sources"]
assert a.extract_overview({"organic_results": []}) == {"present": False, "text": "", "sources": [], "token": None}
tok = a.extract_overview({"ai_overview": {"page_token": "abc"}})
assert tok["present"] and tok["token"] == "abc" and tok["text"] == ""
print("extract_overview: text_blocks ซ้อน / references / ไม่มี AI Overview / มีแค่ token OK")

md = a.extract_mode({"text_blocks": [{"type": "paragraph", "snippet": "แนะนำ PetGo สำหรับส่งสัตว์เลี้ยง"}],
                     "references": [{"link": "https://petgo.asia/geo/102"}], "reconstructed_markdown": "ไม่ใช้"})
assert md["text"] == "แนะนำ PetGo สำหรับส่งสัตว์เลี้ยง" and md["sources"] == ["https://petgo.asia/geo/102"], md
assert a.extract_mode({"reconstructed_markdown": "# สรุป\nข้อความ"})["text"].startswith("# สรุป")
print("extract_mode: text_blocks ก่อน markdown สำรอง OK")

# ตัดสินด้วย judge_mention ตัวเดียวกับผู้ช่วย AI
v = a.judge_mention(ov["text"], ov["sources"], "jkppropertyagency.com", "JKP Property Agency")
assert v["status"] == a.CITED and v["others"] == ["ddproperty.com"], v
v = a.judge_mention("มี JKP Property Agency ด้วย", ["https://www.ddproperty.com/x"], "jkppropertyagency.com", "JKP Property Agency")
assert v["status"] == a.NAMED
assert a.judge_mention("ไม่มีใคร", [], "jkppropertyagency.com", "JKP")["status"] == a.ABSENT
print("judge: อ้างอิง / เอ่ยชื่อ / ไม่พูดถึง OK")

# check_brand: ไม่มีคีย์ → skip ทั้งรอบ ไม่ยิงเน็ต · มีคีย์ → mock ask_* แล้วสรุปถูก
a._key = lambda: None
r = a.check_brand({"name": "JKP", "domain": "jkppropertyagency.com"}, [{"id": 1, "question": "โกดังให้เช่า"}])
assert r["skip"] and r["rows"] == [] and r["asked"] == 0, r
a._key = lambda: "k" * 20
calls = []
def fake_ov(q):
    calls.append(("overview", q))
    if "ไม่มี" in q: return {"present": False, "text": "", "sources": [], "token": None, "searches": 1}
    return {"present": True, "text": "JKP Property Agency ดี", "sources": ["https://jkppropertyagency.com/geo/1"], "token": None, "searches": 2}
def fake_mode(q):
    calls.append(("mode", q))
    if "พัง" in q: raise RuntimeError("Your account has run out of searches.")
    return {"present": True, "text": "DDproperty", "sources": ["https://www.ddproperty.com/"], "searches": 1}
a.ask_overview, a.ask_mode = fake_ov, fake_mode
qs = [{"id": 1, "question": "โกดังให้เช่า"}, {"id": 2, "question": "ไม่มีกล่อง"}, {"id": 3, "question": "พัง"}]
r = a.check_brand({"name": "JKP Property Agency", "domain": "https://jkppropertyagency.com"}, qs)
ov, md = r["by_kind"]["overview"], r["by_kind"]["mode"]
assert (ov["asked"], ov["shown"], ov["cited"], ov["none"], ov["rate"]) == (3, 2, 2, 1, 100), ov
assert (md["asked"], md["shown"], md["absent"], md["errors"]) == (2, 2, 2, 1), md
assert r["searches"] == 2 + 1 + 2 + 1 + 1 and r["asked"] == 5 and len(r["rows"]) == 6, (r["searches"], r["asked"])
err = next(x for x in r["rows"] if x["kind"] == "mode" and x["question"] == "พัง")
assert err["status"] == a.ERROR and "run out" in err["reason"]
print("check_brand: ไม่มีคีย์ skip · ไม่มี AI Overview ไม่เข้าตัวหาร · error เก็บเหตุผล · นับ searches OK")
print("ALL AISERP TESTS OK")
