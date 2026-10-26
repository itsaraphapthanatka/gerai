"""ทดสอบใบเสนอราคา/สัญญาหลายภาษา: เปิดได้ทุกภาษา (หน้าเว็บ + HTML ที่ส่งไปทำ PDF) · <html lang> ถูก · ไม่มีไทยหลงในเนื้อหาที่เห็น
· สัญญาภาษาอื่นแจ้งว่าฉบับไทยมีผลบังคับ + ลิงก์ฉบับไทย · ภาษาไทยข้อความเดิมทุกตัวอักษร · เงื่อนไขคืนเงินทั้งแบบใหม่ (ไม่คืน) และใบเก่า (7 วัน)
· ข้อความผิดพลาดจากฟอร์มแปลตามภาษา · คลังคำแปล quote.json ครบ"""
import os, tempfile, json, re, glob
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke-qi18n-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["GEO_DEEP_AUDIT"] = "0"; os.environ["GEO_CS"] = "0"
from fastapi.testclient import TestClient
import app.db as db, app.quotes as Q, app.pipeline as P, app.mailer as mailer, app.i18n as I, app.stripe_pay as SP, app.report as R
import app.main as M
db.init_db()
I.load(force=True)
mailer.config = lambda: {"host": "x"}; mailer.send = lambda *a, **k: None; mailer.is_deliverable = lambda e: bool(e)
P.RUN_INLINE = True
OTHER = [l for l in I.LANGS if l != "th"]
THAI = re.compile(r"[฀-๿]")
CALL = re.compile(r"""_\(\s*(["'])((?:(?!\1).|\\\1)*?)\1""", re.S)

# ---- คลังคำแปลของกลุ่มนี้ ----
FIELD = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
cat = json.load(open("app/i18n/quote.json", encoding="utf-8"))
for k, v in cat.items():
    for lg in OTHER:
        assert (v.get(lg) or "").strip(), (k, lg)
        assert set(FIELD.findall(v[lg])) == set(FIELD.findall(k)), (k, lg)
        assert not THAI.search(re.sub(r"เจอ\.AI|฿", "", v[lg])), (k, lg)
pages = [x.strip() for x in open("app/i18n/quote.pages", encoding="utf-8") if x.strip() and not x.startswith("#")]
assert pages == ["quote.html", "contract.html"], pages
for name in pages:
    src = open(f"app/templates/{name}", encoding="utf-8").read()
    miss = [m for _q, m in CALL.findall(src) if THAI.search(m) and not I.has(m)]
    assert not miss, (name, miss)
    left = re.sub(r"\{#.*?#\}", "", src, flags=re.S)
    left = re.sub(r"\{\{.*?\}\}", lambda m: CALL.sub("", m.group(0)), left, flags=re.S)
    left = re.sub(r"\{%.*?%\}", lambda m: CALL.sub("", m.group(0)), left, flags=re.S)
    left = re.sub(r"เจอ(?=\.AI)", "", left)
    bad = [ln.strip()[:100] for ln in left.splitlines() if THAI.search(ln)]
    assert not bad, (name, bad)
print(f"catalog: quote.json {len(cat)} ข้อความ ครบ 4 ภาษา · ทุก _() ใน quote/contract มีคำแปล · ไม่มีไทยหลงนอก _() OK")

# ---- ข้อความที่โค้ดสร้าง: ภาษาไทยเหมือนเดิม · ภาษาอื่นแปลครบ ----
def all_variants():
    for cyc in ("month", "year"):
        for setup in (False, True):
            for created in ("2026-10-22T10:00:00", "2026-10-26T10:00:00"):          # ก่อน/หลังเลิกคืนเงิน 25 ต.ค. 2569
                for vat in (0, 7):
                    pr = Q.price_for("pro", 5 if setup else 0, setup, cyc)
                    yield {**pr, "number": "Q1", "status": "sent", "created_at": created, "vat_pct": vat, "valid_until": "2026-11-09", "setup": int(setup)}
CO = {"name": "Acme Co", "address": "1 Main Road", "tax_id": "0105555123456"}
CU = {"name": "Owner Test", "domain": "shop.example", "email": "owner@shop.example"}
n = 0
for q in all_variants():
    assert Q.terms_for(q) == Q.terms_for(q, "th") and Q.contract_sections(CO, CU, q) == Q.contract_sections(CO, CU, q, "th")
    for lg in OTHER:
        txt = json.dumps([Q.terms_for(q, lg), Q.contract_sections(CO, CU, q, lg), Q.contract_sections({}, {}, q, lg)], ensure_ascii=False)
        assert not THAI.search(re.sub(r"เจอ\.AI", "", txt)), (lg, re.findall(r"\S*[฀-๿]+\S*", txt)[:5])
        assert "Acme Co" in txt and "0105555123456" in txt and "shop.example" in txt and "owner@shop.example" in txt   # ข้อมูลไม่แปล
    n += 1
old = next(q for q in all_variants() if q["cycle"] == "year" and q["created_at"] < "2026-10-25")
new = next(q for q in all_variants() if q["cycle"] == "year" and q["created_at"] > "2026-10-25")
assert "7 วัน" in Q.terms_for(old)[1] and "ไม่คืนเงิน" in Q.terms_for(new)[1]                                   # ไทยเดิม
assert "within 7 days" in Q.terms_for(old, "en")[1] and "full refund" in Q.terms_for(old, "en")[1]
assert "non-refundable" in Q.terms_for(new, "en")[1] and "refund" not in Q.terms_for(new, "en")[1].replace("non-refundable", "")
sec_old = json.dumps(Q.contract_sections(CO, CU, old, "en")); sec_new = json.dumps(Q.contract_sections(CO, CU, new, "en"))
assert "within 7 days" in sec_old and "non-refundable" not in sec_old and "non-refundable" in sec_new and "within 7 days" not in sec_new
mo = next(q for q in all_variants() if q["cycle"] == "month" and q["vat_pct"] == 7)
assert "excluding VAT" in json.dumps(Q.contract_sections(CO, CU, mo, "en")) and "7%" in Q.terms_for(mo, "en")[-1]
assert Q.contract_sections(CO, CU, mo, "ja")[0]["h"] == "1. 当事者" and Q.terms_for(mo, "ko")[0].startswith("서비스 요금은 월간")
print(f"text: เงื่อนไข/สัญญา {n} แบบ × 4 ภาษา — ไทยเหมือนเดิม · ภาษาอื่นไม่มีไทยหลง · ชื่อ/ที่อยู่/เลขภาษี/โดเมน/อีเมลไม่แปล · ไม่คืนเงิน vs ใบเก่า 7 วัน OK")

# ---- หน้าเว็บ ----
def visible(html: str) -> str:
    h = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html)
    attrs = " ".join(re.findall(r'(?:placeholder|title|alt|aria-label)="([^"]*)"', h))
    h = re.sub(r"<[^>]+>", " ", h) + " " + attrs
    return re.sub(r"เจอ\.AI|เจอ|฿|ไทย", "", h)                    # ชื่อแบรนด์ · ชื่อภาษา "ไทย" ในตัวเลือกภาษา

def no_thai(html, where):
    v = visible(html)
    assert not THAI.search(v), (where, re.findall(r"\S*[฀-๿]+\S*", v)[:8])

pdfs = []
R.to_pdf = lambda html: pdfs.append(html) or b"%PDF-1.4 test"
db.set_setting("promptpay_id", "0812345678"); db.set_setting("promptpay_name", "Acme Co")
SP.save_config("sk_test_abcdefghijklmnop", "whsec_x")
REPORT = {"checks": [{"key": "csr", "label": "เนื้อหาอยู่ใน HTML ไม่ต้องรอ JavaScript", "status": "fail", "detail": ""},
                     {"key": "noindex", "label": "ไม่ถูกสั่งห้าม index", "status": "fail", "detail": ""},
                     {"key": "ai_bots", "label": "เปิดทางบอท AI", "status": "fail", "detail": ""}]}

def make(dom, cycle, setup, disc=0):
    lid = db.add_lead(f"https://{dom}", dom, source="prospect", email=f"owner@{dom}")
    db.update_lead(lid, audit_json=json.dumps(REPORT), score=60, name="Owner Test", audit_at="2026-10-20T09:00:00")
    q = P.create_quote(db.get_lead(lid), "pro", disc, setup, created_by="admin", cycle=cycle)
    P.send_quote(q, "admin")
    return db.get_quote(q["id"])

c = TestClient(M.app)
th_pages = {}
def check_all(q, tag):
    t = q["token"]
    for lg in I.LANGS:
        for url in (f"/q/{t}", f"/q/{t}/contract"):
            r = c.get(f"{url}?lang={lg}")
            assert r.status_code == 200, (tag, url, lg, r.status_code)
            assert f'<html lang="{I.HTML_LANG[lg]}">' in r.text, (tag, url, lg)
            is_contract = url.endswith("/contract")
            notice = I.tr("นี่คือคำแปลเพื่อใช้อ้างอิงเท่านั้น สัญญาฉบับภาษาไทยเป็นฉบับที่มีผลผูกพันตามกฎหมาย หากข้อความไม่ตรงกัน ให้ถือตามฉบับภาษาไทย", lg)
            if lg == "th":
                th_pages[(tag, url)] = r.text
                assert "นี่คือคำแปล" not in r.text and "ใบเสนอราคา " + q["number"] in r.text
            else:
                no_thai(r.text, (tag, url, lg))
                if is_contract:
                    assert notice in r.text and f"/q/{t}/contract?lang=th" in r.text, (tag, lg)
                    if lg == "en":
                        assert "the Thai version prevails" in r.text
                else:
                    assert notice not in r.text and f"/q/{t}/contract?lang={lg}" in r.text       # ลิงก์สัญญา/PDF พาภาษาไปด้วย
        for url in (f"/q/{t}.pdf", f"/q/{t}/contract.pdf"):                                         # HTML ที่ส่งไปทำ PDF
            pdfs.clear()
            r = c.get(f"{url}?lang={lg}")
            assert r.status_code == 200 and r.content.startswith(b"%PDF") and len(pdfs) == 1, (tag, url, lg)
            html = pdfs[0]
            assert f'<html lang="{I.HTML_LANG[lg]}">' in html and 'class="ds-lang"' not in html, (tag, url, lg)
            if lg != "th":
                no_thai(html, (tag, url, lg, "pdf"))
                if url.endswith("contract.pdf"):
                    assert I.tr("ดูฉบับภาษาไทย", lg) in html
            if lg in ("zh", "ja", "ko"):
                assert {"zh": "Noto Sans CJK SC", "ja": "Noto Sans CJK JP", "ko": "Noto Sans CJK KR"}[lg] in html

qm = make("monthly.example", "month", True, 5)
qy = make("yearly.example", "year", True)
for q, tag in ((qm, "month"), (qy, "year")):
    check_all(q, tag + "-sent")
    for st in ("accepted", "claimed", "paid", "cancelled"):
        db.update_quote(q["id"], status=st, accepted_name="Owner Test", accepted_at="2026-10-26T10:00:00", accepted_ip="203.0.113.5")
        check_all(db.get_quote(q["id"]), f"{tag}-{st}")
    db.update_quote(q["id"], status="sent", valid_until="2020-01-01", accepted_at=None)
    check_all(db.get_quote(q["id"]), f"{tag}-expired")
print("pages: ใบเสนอราคา + สัญญา (รายเดือน/รายปี × ส่ง/ยืนยัน/แจ้งชำระ/ชำระ/ยกเลิก/หมดอายุ) × 5 ภาษา + HTML ของ PDF — 200 · <html lang> · ไม่มีไทยหลง · ประกาศฉบับไทยมีผลบังคับ OK")

# ใบเก่าก่อนเลิกคืนเงิน + เปิด VAT
qo = make("legacy.example", "year", True)
db.update_quote(qo["id"], vat_pct=7)
with db.get_conn() as cc: cc.execute(db.q("UPDATE quotes SET created_at=? WHERE id=?"), ("2026-10-22T10:00:00", qo["id"]))
qo = db.get_quote(qo["id"])
r = c.get(f"/q/{qo['token']}?lang=en"); no_thai(r.text, "legacy-en")
assert "within 7 days of payment" in r.text and "VAT 7%" in r.text and "(incl. VAT)" in r.text and "full refund available within the first 7 days" not in r.text
r = c.get(f"/q/{qo['token']}/contract?lang=en")
assert "within 7 days of paying the annual fee" in r.text and "7-day refund" in r.text and "excluding VAT" in r.text
r = c.get(f"/q/{qo['token']}/contract?lang=th"); assert "คืนเงิน 7 วัน" in r.text and "ภายใน 7 วันนับจากชำระค่าบริการรายปี" in r.text
r = c.get(f"/q/{qy['token']}/contract?lang=ja"); assert "返金なし" in r.text
print("refund: ใบเก่าคืนเงิน 7 วัน (สัญญา/เงื่อนไข/ป้ายร่าง) · ใบใหม่ไม่คืนเงิน · VAT ทุกภาษา OK")

# ---- ฟอร์ม: ข้อความผิดพลาดตามภาษา · ยืนยันได้ · สลับรอบบิล ----
qf = make("form.example", "month", False)
c.get("/audit?lang=ko")                                                    # จำภาษาเกาหลีลงคุกกี้
r = c.post(f"/q/{qf['token']}/accept", data={"name": "Owner Test"})
assert r.status_code == 200 and '<html lang="ko">' in r.text and "먼저 조건 및 계약 동의란에 체크해 주십시오." in r.text; no_thai(r.text, "accept-err-ko")
r = c.post(f"/q/{qf['token']}/accept", data={"name": "Owner Test", "agree": "1", "buyer_tax_id": "123"})
assert "납세자 번호가 올바르지 않습니다(13자리)." in r.text; no_thai(r.text, "taxid-err-ko")
r = c.post(f"/q/{qf['token']}/cycle", data={"cycle": "year"}, follow_redirects=True)
assert r.status_code == 200 and '<html lang="ko">' in r.text and db.get_quote(qf["id"])["cycle"] == "year"; no_thai(r.text, "cycle-ko")
r = c.post(f"/q/{qf['token']}/accept", data={"name": "Owner Test", "agree": "1"}, follow_redirects=True)
assert db.get_quote(qf["id"])["status"] == "accepted" and "Owner Test 님이" in r.text and "Stripe로 결제" in r.text and "(테스트 모드)" in r.text; no_thai(r.text, "accepted-ko")
c.get("/audit?lang=th")
print("forms: ข้อความผิดพลาด/ยืนยัน/สลับรอบบิล ตามภาษาที่จำไว้ OK")

# ---- ภาษาไทย: ข้อความสำคัญอยู่ครบ (เหมือนเดิม) ----
r = c.get(f"/q/{qy['token']}?lang=th"); h = r.text
for s in ("ส่วนลดจ่ายรายปี 17%", "118,800", "ยอดชำระครั้งเดียวทั้งปี", "ใบเสนอราคานี้หมดอายุเมื่อ 2020-01-01 — ตอบกลับอีเมลหรือทัก LINE เพื่อขอใบใหม่ได้ครับ",
          "GEO Setup — เสริมบล็อกเก่า", "ธุรกิจเดียว ครบทุกฟีเจอร์", "เนื้อหาอยู่ใน HTML ไม่ต้องรอ JavaScript", "หน้าเว็บสร้างด้วย JavaScript ล้วน"):
    assert s in h, s
for t_ in Q.terms_for(db.get_quote(qy["id"])):
    assert t_ in h, t_
r = c.get(f"/q/{qy['token']}/contract?lang=th")
for s in Q.contract_sections(M._company(), {"name": "Owner Test", "domain": "yearly.example", "email": "owner@yearly.example"}, db.get_quote(qy["id"])):
    assert s["h"] in r.text and all(p.replace("&", "&amp;") in r.text for p in s["p"]), s["h"]
assert "ไม่คืนเงิน ยกเลิกได้ทุกเมื่อ ไม่รับประกันผล" in r.text
print("thai: หน้าไทยข้อความเดิม (เงื่อนไข/สัญญา/ป้ายร่าง/ผลตรวจ) OK")
print("ALL OK")
