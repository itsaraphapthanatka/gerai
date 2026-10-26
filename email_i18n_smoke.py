"""ทดสอบอีเมลตามภาษาลูกค้า (th en zh ja ko)
· ภาษาของลีดมาจากหน้าที่เขาใช้ (/audit?lang=ja) และไม่ถูกเขียนทับด้วยไทยจากคำขอที่ไม่มีคุกกี้ · /api/contact · พันธมิตรสมัคร · แชตบนเว็บ
· บัญชีลูกค้าได้ภาษาจากลีดตอนเปิดบัญชี และเปลี่ยนตาม ?lang= เมื่อล็อกอินอยู่
· อีเมลถึงลูกค้าทุกฉบับ (ทัก ติดตามผล ตอบอีเมล ใบเสนอราคา ต้อนรับ อัปเกรด แผน 30 วัน เตือนติดตั้ง ชวนตรวจ รายงานเดือน ต่ออายุ แคมเปญ
  Autopilot ข้อเสนอขยายบัญชี พันธมิตร ใบเสร็จ/ใบแจ้งหนี้/ทวงชำระ ลิงก์ชำระหลังโทร) เป็นภาษานั้น ไม่มีไทยหลง (ยกเว้น เจอ.AI ฿ และข้อมูลลูกค้า)
· ไทย = ข้อความเดิมทุกตัวอักษร · ใบเสร็จ/ใบกำกับภาษีที่แนบยังเป็นภาษาไทย (กฎสรรพากร) · อีเมลถึงเจ้าของยังเป็นไทย
ข้อมูลทดสอบเป็นของสมมติทั้งหมด (example.com) · ไม่ยิงเครือข่าย"""
import os, tempfile, re, json, datetime as dt
os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-email-i18n-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["GEO_DEEP_AUDIT"] = "0"; os.environ["GEO_CS"] = "0"
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.i18n as I, app.mailer as mailer, app.pipeline as P, app.success as S, app.quotes as Q
import app.site_health as sh, app.report as report, app.deliverability as D, app.ai_client as ai, app.line_oa as LO
import app.accounting as A, app.campaigns as CP, app.expansion as EX, app.partners as PT, app.followup as FU
import app.leads as LD, app.inbox as IB, app.salesbot as SB, app.execs as X, app.closer as CL, app.main as M
from app.main import app

db.init_db()
I.load(force=True)
OTHER = ["en", "zh", "ja", "ko"]
THAI = re.compile(r"[฀-๿]")

sh.run_checks = lambda site, **kw: {"ok": False, "n_fail": 2, "n_ok": 8, "checks": [
    {"key": "csr", "label": "Client-side rendering", "status": "fail", "detail": ""},
    {"key": "indexed", "label": "Indexed", "status": "fail", "detail": ""}] + [{"key": "x", "label": "ok", "status": "ok"}] * 8}
ai._read_setting = lambda k: None
ai._chat = lambda m, max_tokens=2048, timeout=120: (_ for _ in ()).throw(RuntimeError("no model"))
mails = []
mailer.config = lambda: {"host": "x", "smtp_from": "report@example.com"}
mailer.send = lambda to, subj, html, text="", attachments=(): mails.append({"to": to, "subj": subj, "html": html, "text": text or "", "att": list(attachments or ())})
mailer.is_deliverable = lambda e: bool(e) and "@" in e and not e.endswith(".local")
D.mx_ok = lambda e: True
LO._post = lambda path, payload: True
owner_alerts = []
_alert = P.alert_admins
P.RUN_INLINE = True
report.to_pdf = lambda html: html.encode("utf-8")            # "PDF" = HTML ที่จะเรนเดอร์ — ตรวจภาษาได้
A.HOOKS["pdf"] = lambda html: html.encode("utf-8")
S.HOOKS["autopilot"] = lambda bid: None
S.HOOKS["health"] = lambda bid: None
S.RUN_INLINE = True


def thai_left(s: str, allow=()) -> list[str]:
    """คำที่ยังเป็นภาษาไทย (ไม่นับชื่อแบรนด์ เจอ.AI และข้อมูลลูกค้าที่ระบุ)"""
    s = re.sub(r"เจอ\.?AI", "", s or "")
    for a in allow:
        s = s.replace(a, "")
    return sorted({m.group(0) for m in re.finditer(r"\S*[฀-๿]\S*", s)})


def no_thai(m: dict, lg: str, what: str) -> None:
    for part in ("subj", "text", "html"):
        left = thai_left(m[part])
        assert not left, f"{what} [{lg}] {part} ยังมีไทย: {left[:8]}"
    assert f'lang="{I.HTML_LANG[lg]}"' in m["html"], f"{what} [{lg}] ไม่มี lang ใน HTML"


def take(pred=lambda m: True) -> list[dict]:
    out = [m for m in mails if pred(m)]
    mails.clear()
    return out


# ================= 1) ภาษาของลีด =================
c = TestClient(app)
r = c.get("/audit?lang=ja")
assert c.cookies.get(I.COOKIE) == "ja"
r = c.post("/audit", data={"url": "lotus-ja.example.com"})
token = str(r.url).rstrip("/").rsplit("/", 1)[-1]
lead = db.get_lead_by_token(token)
assert lead and lead["lang"] == "ja", dict(lead or {})
anon = TestClient(app)                                             # ไม่มีคุกกี้ภาษา → ไม่เขียนทับเป็นไทย
r = anon.post(f"/audit/{token}/contact", data={"name": "Hana", "email": "hana@lotus-ja.example.com", "consent": "1"})
assert db.get_lead(lead["id"])["lang"] == "ja" and db.get_lead(lead["id"])["email"] == "hana@lotus-ja.example.com"
r = anon.post("/audit", data={"url": "lotus-ja.example.com"})      # ตรวจซ้ำแบบไม่มีคุกกี้ → คงภาษาเดิม
assert db.get_lead(lead["id"])["lang"] == "ja"
r = anon.post(f"/audit/{token}/contact?lang=en", data={"name": "Hana", "email": "hana@lotus-ja.example.com", "consent": "1"})
assert db.get_lead(lead["id"])["lang"] == "en"                    # เลือกภาษาเองชัด ๆ = เปลี่ยนได้
db.update_lead(lead["id"], lang="ja")
r = TestClient(app).post("/audit", data={"url": "plain-th.example.com"})
assert db.find_lead_by_domain("plain-th.example.com")["lang"] == "th"   # ไม่ได้เลือกภาษา = ไทย
lid_imp = db.add_lead("https://found.example.com", "found.example.com", source="prospect")
assert db.get_lead(lid_imp)["lang"] == "th"                        # ลีดที่หาเอง/นำเข้า = ไทย
# ข้อความทักที่ CEO ร่างให้ลีดญี่ปุ่น = ภาษาญี่ปุ่น (แม่แบบแบบคนเขียน)
d = next(x for x in db.list_decisions("proposed", 50) if x["lead_id"] == lead["id"] and x["kind"] == "outreach")
pl = json.loads(d["payload_json"])
assert not thai_left(pl["subject"] + "\n" + pl["body"]), (pl["subject"], pl["body"])
assert "lotus-ja.example.com" in pl["body"] and "/audit/" in pl["body"]
# /api/contact · พันธมิตรสมัคร · แชตบนเว็บ
kc = TestClient(app); kc.get("/?lang=ko")
assert kc.post("/api/contact", json={"name": "Min", "contact": "min@example.com"}).status_code == 200
with db.get_conn() as cn:
    assert cn.execute(db.q("SELECT lang FROM contacts ORDER BY id DESC LIMIT 1")).fetchone()["lang"] == "ko"
r = kc.post("/partners", data={"name": "Min Park", "company": "Park Web", "email": "min@park-web.example.com", "phone": "",
                                "website": "park-web.example.com", "clients": "5", "agree": "1"})
partner = db.find_partner(email="min@park-web.example.com")
assert partner and partner["lang"] == "ko", dict(partner or {})
SB.LLM = lambda m: '{"answer": "Pro costs 9,900 THB per month.", "needs_human": false}'
r = TestClient(app).post("/chat/ask?lang=en", json={"q": "How much is Pro?", "token": token})
assert r.json()["answer"] and db.get_lead(lead["id"])["lang"] == "en"
db.update_lead(lead["id"], lang="ja")
SB.LLM = None
print("lead lang: /audit?lang=ja บันทึก · คำขอไม่มีคุกกี้ไม่เขียนทับ · เลือกเองเปลี่ยนได้ · หาเอง = ไทย · /api/contact · พันธมิตร · แชต OK")


# ================= 2) ลูกค้าต่อภาษา: เปิดบัญชี → อีเมลทุกฉบับ =================
def make_customer(lg: str, dom: str):
    lid = db.add_lead(f"https://{dom}", dom, source="audit", name="Alex Example", email=f"owner@{dom}")
    db.update_lead(lid, lang=lg, consent=1)
    P.run_lead_audit(lid, f"https://{dom}")
    lead = db.get_lead(lid)
    q = P.create_quote(lead, "pro", setup=True, cycle="month")
    return db.get_lead(lid), q


results = {}
for lg in ["th"] + OTHER:
    dom = f"shop-{lg}.example.com"
    lead, q = make_customer(lg, dom)
    mails.clear()
    got = {}
    # --- ทักครั้งแรก (แม่แบบแบบคนเขียน) → ส่งจริงทาง send_plain ---
    tpl = X.outreach_template(lead, [{"key": "csr", "label": "csr", "why": LD.EXPLAIN["csr"]}], P.result_url(lead))
    did = db.add_decision("ceo", "outreach", "t", "t", {"subject": tpl["subject"], "body": tpl["body"]}, lead_id=lead["id"])
    P.execute_decision(db.get_decision(did), "test")
    got["outreach"] = take()
    # --- ใบเสนอราคา (ใหม่ + ปรับ) ---
    P.send_quote(q, "test"); got["quote"] = take()
    P.send_quote(q, "test", revised=True); got["quote_revised"] = take()
    # --- ยืนยันยอด → บัญชีใหม่ + ใบเสร็จ ---
    db.update_quote(q["id"], status="accepted")
    P.confirm_payment(db.get_quote(q["id"]), "test")
    tenant = db.get_tenant_by_email(f"owner@{dom}")
    assert tenant["lang"] == lg, (lg, tenant["lang"])                 # ภาษาบัญชีคัดลอกจากลีด
    ms = take()
    got["welcome"] = [m for m in ms if not m["att"]]
    got["receipt"] = [m for m in ms if m["att"]]
    assert got["welcome"] and got["receipt"], [m["subj"] for m in ms]
    # --- ดูแลหลังการขาย ---
    cust = next(x for x in S.customers() if x["lead"]["id"] == lead["id"])
    for name, fn in (("kickoff", lambda: S.kickoff_message(cust)), ("install", lambda: S.install_message(cust, S.installed(cust["brand"])[1])),
                     ("review", lambda: S.review_message(cust, 2)), ("report", lambda: S.report_message(cust, 1)),
                     ("renewal", lambda: S.renewal_message(cust, 1, dt.date(2027, 10, 25)))):
        s_, h_, t_, l_ = fn()
        S._send(cust, s_, h_, t_, l_)
        got[name] = take()
    # --- แคมเปญรายเดือน (แม่แบบมาตรฐาน + แนบ PDF รายงาน) ---
    cid = db.create_campaign(f"monthly-{lg}", CP.DEFAULT_SUBJECT, CP.DEFAULT_BODY, f"tenant:{tenant['id']}", "monthly", 1, db.now())
    camp = next(dict(x) for x in db.due_campaigns("9999") if x["id"] == cid)
    CP.send_campaign(camp, "https://geo.example.com")
    got["campaign"] = take()
    # --- Autopilot ---
    brand = dict(db.get_brand(cust["brand"]["id"]))
    log = {"steps": [{"step": "sov", "label": "มอนิเตอร์ SoV", "status": "ok", "detail": "SoV 3/10 คำถาม"},
                     {"step": "health", "label": "สุขภาพเว็บ", "status": "ok", "detail": "ผ่านทุกข้อ"}]}
    M._autopilot_report(brand, log); got["autopilot"] = take()
    # --- ข้อเสนอขยายบัญชี (ร่าง + ส่งรายปีพร้อมใบเสนอราคาใหม่) ---
    sig = EX.signals(cust)
    sig.update(days=60, next_bill="2026-11-30", published=4, brands=1, brands_cap=1, content_month=10, content_cap=10, runs_month=5, runs_cap=5)
    drafts = {opp: EX.draft(cust, opp, sig) for opp in ("yearly", "agency", "content", "advocate")}
    did = EX.propose(cust, "yearly", sig)
    EX.execute(db.get_decision(did), "test"); got["expansion"] = take()
    got["expansion_drafts"] = [{"subj": d_["subject"], "text": d_["body"], "html": f'<div lang="{I.HTML_LANG[lg]}"></div>'} for d_ in drafts.values()]
    # --- ใบแจ้งหนี้ + ทวงชำระ ---
    q_paid = db.get_quote(q["id"])
    inv = A.issue_invoice(q_paid, dt.date(2026, 11, 26)); got["invoice"] = take()
    A._overdue_steps(db.get_acct_doc(inv["id"]), dt.date(2026, 12, 1)); got["overdue"] = take()
    # --- ติดตามผล / ตอบอีเมล / ตัวตอบแชต (อีเมล) / ลิงก์ชำระหลังโทร ---
    f = {"summary_new": [], "issues": [{"label": "csr", "count": 3}], "health": 62, "pages": 40,
         "competitors": [{"domain": "rival.example.com", "hits": 4}], "keywords": ["coffee bangkok"], "page1": 1, "n_kw": 10, "ai": {"asked": 20, "mentioned": 2}}
    link = P.result_url(lead, "mail")
    fus = [FU.template(lead, k, f, link) for k in ("new_issue", "competitor", "breakup")]
    thai_llm = lambda m: json.dumps({"subject": "เรื่องเว็บ", "body": "สวัสดีครับ ผมดูเว็บแล้ว " * 6 + link}, ensure_ascii=False)
    fus.append(FU.draft(lead, "new_issue", f, link, llm=thai_llm))
    if lg != "th":
        assert fus[-1]["source"].startswith("template"), fus[-1]["source"]          # โมเดลตอบไทยให้ลีดภาษาอื่น = ใช้แม่แบบที่แปลแล้ว
    got["followup"] = [{"subj": x["subject"], "text": x["body"], "html": f'<div lang="{I.HTML_LANG[lg]}"></div>'} for x in fus]
    ans = IB.draft_answer(lead, "How much does it cost?", llm=thai_llm)
    sb = SB.answer("Can I get a refund?", lead, "email", llm=thai_llm)               # เรื่องคืนเงิน = ส่งต่อเจ้าของ (ข้อความรับเรื่องตามภาษา)
    sb2 = SB.answer("Do you work with WordPress?", lead, "email", llm=thai_llm)       # โมเดลตอบผิดภาษา = ไม่ใช้
    got["replies"] = [{"subj": "", "text": x, "html": f'<div lang="{I.HTML_LANG[lg]}"></div>'} for x in (ans, sb["answer"], sb2["answer"])]
    assert sb["escalate"] and (lg == "th" or sb2["source"] == "fallback"), (sb, sb2)
    did = db.add_decision("salesbot", "reply", "t", "t", {"subject": "Re: question", "body": ans, "to": f"owner@{dom}"}, lead_id=lead["id"])
    P.execute_decision(db.get_decision(did), "test"); got["reply_sent"] = take()
    q2 = P.create_quote(db.get_lead(lead["id"]), "agency", cycle="year")
    CL.log_call(db.get_lead(lead["id"]), "will_pay"); got["closer"] = take()
    # --- อัปเกรด (ลูกค้าเดิมจ่ายใบใหม่) ---
    db.update_quote(q2["id"], status="accepted")
    P.confirm_payment(db.get_quote(q2["id"]), "test")
    ms = take(); got["upgrade"] = [m for m in ms if not m["att"]]
    assert got["upgrade"], [m["subj"] for m in ms]
    results[lg] = (lead, tenant, got)

# ---- ภาษาอื่น: ไม่มีไทยหลง ----
for lg in OTHER:
    lead, tenant, got = results[lg]
    for name, ms in got.items():
        assert ms, (lg, name)
        for m in ms:
            if name == "receipt":
                for part in ("subj", "text"):
                    assert not thai_left(m[part]), f"receipt [{lg}] {part}: {thai_left(m[part])[:8]}"
                assert f'lang="{I.HTML_LANG[lg]}"' in m["html"]
                pdf = m["att"][0][1].decode("utf-8")
                assert "ใบเสร็จรับเงิน" in pdf, "ใบเสร็จที่แนบต้องเป็นภาษาไทย"                 # กฎสรรพากร
                continue
            if name in ("invoice", "overdue"):
                assert "ใบแจ้งหนี้" in m["att"][0][1].decode("utf-8")                           # เอกสารแนบภาษาไทยเหมือนกัน
            no_thai(m, lg, name)
            if name in ("campaign", "autopilot"):
                rep = m["att"][0][1].decode("utf-8")
                assert f'lang="{I.HTML_LANG[lg]}"' in rep, f"{name} [{lg}] PDF รายงานไม่ตามภาษา"
    o = got["outreach"][0]
    assert f'<div dir="auto" lang="{I.HTML_LANG[lg]}">' in o["html"] and "/u/" in o["text"]           # แบบคนพิมพ์ + ลิงก์ไม่รับ
print(f"emails: {len(results['en'][2])} ประเภท × {len(OTHER)} ภาษา ไม่มีไทยหลง · ใบเสร็จ/ใบแจ้งหนี้ที่แนบยังเป็นไทย · PDF รายงานตามภาษา OK")

# ---- ไทย: ข้อความเดิมทุกตัวอักษร (ตัวอย่างหลัก) ----
lead, tenant, got = results["th"]
dom = "shop-th.example.com"
o = got["outreach"][0]
assert o["text"].endswith(f"\n\n— เจอ.AI (ข้อความนี้ส่งจากระบบอัตโนมัติ) · ไม่อยากรับการติดต่อจากเรา กดที่ {P.unsubscribe_url(lead)} เราจะไม่ติดต่ออีก")
assert o["html"].startswith('<div dir="auto">สวัสดีครับ<br><br>ผมลองตรวจเว็บ shop-th.example.com แล้วเจอว่า')
qq = got["quote"][0]
assert qq["subj"] == f"ใบเสนอราคา {db.list_quotes(lead['id'])[-1]['number']} สำหรับ {dom}" and got["quote_revised"][0]["subj"].startswith("ปรับใบเสนอราคา ")
assert qq["text"].startswith("สวัสดีครับ คุณAlex Example\n\nตามที่สนใจ ผมส่งใบเสนอราคาสำหรับเว็บ shop-th.example.com มาให้ครับ\n\nใบเสนอราคา ")
assert "\n\nเปิดดูรายละเอียด เงื่อนไข และยืนยันรับข้อเสนอได้ที่ " in qq["text"] and "\nมีคำถามตอบอีเมลนี้ได้เลยครับ\n\n— เจอ.AI (ข้อความนี้ส่งจากระบบอัตโนมัติ)" in qq["text"]
assert '<html lang="th">' in qq["html"] and "<b>ขอบคุณที่ใช้บริการ</b><br>จาก ทีมงาน เจอ.AI" in qq["html"] and 'alt="เจอ.AI — ให้ลูกค้าหาคุณเจอใน Google และ AI"' in qq["html"]
assert ">ดูใบเสนอราคาและยืนยัน</a>" in qq["html"] and "ไม่ต้องการรับอีเมลจากเรา <a href=" in qq["html"]
w = got["welcome"][0]
assert w["subj"] == f"บัญชีเจอ.AI ของ {dom} พร้อมแล้ว" and "<p>รหัสผ่านชั่วคราว: <code>" in w["html"] and "หน้า \"ติดตั้ง\" เพื่อเชื่อม /geo" in w["html"]
k = got["kickoff"][0]
base, bid = S._base(), next(x for x in S.customers() if x["lead"]["id"] == lead["id"])["brand"]["id"]
assert k["subj"] == f"แผน 30 วันแรกของ {dom} กับเจอ.AI"
assert k["text"] == (f"สวัสดีครับ\n\nขอบคุณที่เลือกเจอ.AI ดูแล {dom} ครับ นี่คือสิ่งที่จะเกิดขึ้นใน 30 วันแรก\n\n"
                     "• วันนี้: ระบบตรวจเว็บ วัดว่า AI เอ่ยถึงแบรนด์คุณหรือยัง และเริ่มร่างคอนเทนต์ชิ้นแรกให้แล้ว\n"
                     f"• ภายใน 3 วัน: ติดตั้งตัวเชื่อม /geo · llms.txt · sitemap บนเว็บของคุณ (ทำครั้งเดียว ใช้เวลาราว 15 นาที) ตามชุดติดตั้ง: {base}/brands/{bid}/assets\n"
                     "• ภายใน 7 วัน: คอนเทนต์ชิ้นแรกพร้อมให้ตรวจในหน้า คอนเทนต์ — ตรวจแล้วกดเผยแพร่ได้เลย\n"
                     "• วันที่ 25: รายงานผลจริงเดือนแรก: สุขภาพเว็บ งานที่ทำ และการมองเห็นใน AI ก่อนรอบบิลถัดไป"
                     f"\n\nเข้าแบรนด์ของคุณ: {base}/brands/{bid}\nติดขัดตรงไหน ตอบอีเมลนี้ได้เลยครับ\n\nทีมดูแลลูกค้า เจอ.AI"), k["text"]
assert got["install"][0]["text"].startswith(f"สวัสดีครับ\n\nระบบเช็กเว็บ {dom} แล้ว ยังไม่พบตัวเชื่อม /geo · llms.txt · sitemap สำหรับ AI (")
rn = got["renewal"][0]
assert rn["subj"] == f"แจ้งล่วงหน้า: บริการรายปีของ {dom} จะต่ออายุวันที่ 25/10/2570"              # ไทยยังเป็นปี พ.ศ.
rp = got["receipt"][0]
assert rp["text"].startswith("สวัสดีครับ\n\nขอบคุณที่ชำระค่าบริการครับ แนบใบเสร็จรับเงินมาให้\n\n• ใบเสร็จรับเงิน เลขที่ ") and rp["text"].endswith("\n\nฝ่ายบัญชี เจอ.AI")
assert "ภาษาไทย ซึ่งเป็นฉบับ" not in rp["text"]                                                     # หมายเหตุเอกสารภาษาไทยเฉพาะลูกค้าภาษาอื่น
cm = got["campaign"][0]
assert cm["subj"] == "รายงาน GEO ประจำเดือน — Alex Example" or cm["subj"].startswith("รายงาน GEO ประจำเดือน — ")
assert cm["text"].startswith("สวัสดีคุณ") and "**สิ่งที่ควรทำต่อ**" in cm["text"]
assert got["autopilot"][0]["subj"].startswith("Autopilot รายสัปดาห์ — ") and "**Autopilot รอบนี้ทำ**\n- มอนิเตอร์ SoV: SoV 3/10 คำถาม" in got["autopilot"][0]["text"]
fu = got["followup"]
assert fu[0]["subj"] == f"เจอเพิ่มอีกเรื่องบนเว็บ {dom}" and fu[2]["text"].startswith(f"สวัสดีครับ\n\nผมส่งผลตรวจเว็บ {dom} ไปสองสามครั้งแล้ว")
assert got["replies"][1]["text"] == SB.HOLD
assert got["closer"][0]["text"].startswith("สวัสดีครับ ตามที่คุยทางโทรศัพท์ ลิงก์ใบเสนอราคาและชำระเงินอยู่ที่นี่ครับ\n")
assert got["upgrade"][0]["subj"] == f"อัปเกรด {dom} เรียบร้อยแล้ว"
assert got["expansion_drafts"][0]["subj"].startswith(f"{dom}: เปลี่ยนเป็นรายปีประหยัด ") and got["expansion_drafts"][0]["text"].endswith("ทีมดูแลลูกค้า เจอ.AI")
print("thai: ข้อความไทยเหมือนเดิม (ทัก · ใบเสนอราคา · กรอบอีเมล · ต้อนรับ · แผน 30 วัน · ต่ออายุ พ.ศ. · ใบเสร็จ · แคมเปญ · Autopilot · ติดตาม · ขยายบัญชี) OK")

# ---- ตัวอย่างภาษาอื่น: อ่านแล้วเป็นภาษานั้นจริง ----
en = results["en"][2]
assert en["quote"][0]["subj"].startswith("Quote ") and en["welcome"][0]["subj"].startswith("Your เจอ.AI account for shop-en.example.com")
assert "Temporary password: <code>" in en["welcome"][0]["html"] and "THB/month" in en["quote"][0]["text"]
assert "The attached document is in Thai" in en["receipt"][0]["text"] and en["renewal"][0]["subj"].endswith("2027-10-25")
assert en["outreach"][0]["text"].startswith("Hi,\n\nI took a look at shop-en.example.com and found this: ")
assert "If you'd rather not hear from us, click" in en["outreach"][0]["text"]
assert "見積書" in results["ja"][2]["quote"][0]["subj"] and "报价单" in results["zh"][2]["quote"][0]["subj"] and "견적서" in results["ko"][2]["quote"][0]["subj"]
assert results["ja"][2]["kickoff"][0]["text"].startswith("こんにちは。") and "合作伙伴" not in results["zh"][2]["kickoff"][0]["text"]
print("samples: อังกฤษ/จีน/ญี่ปุ่น/เกาหลี อ่านได้เป็นภาษานั้น · ต่ออายุใช้ปี ค.ศ. OK")


# ================= 3) ภาษาบัญชีเปลี่ยนตาม ?lang= ตอนล็อกอิน =================
lead_ja, tenant_ja, got_ja = results["ja"]
pw = re.search(r"<code>([^<]+)</code>", got_ja["welcome"][0]["html"]).group(1)
u = TestClient(app)
r = u.post("/login", data={"email": tenant_ja["email"], "password": pw})
assert db.get_tenant(tenant_ja["id"])["lang"] == "ja"
u.get("/app?lang=ko")
assert db.get_tenant(tenant_ja["id"])["lang"] == "ko", db.get_tenant(tenant_ja["id"])["lang"]
u.get("/app")                                                       # ไม่มี ?lang= → ไม่เปลี่ยน
assert db.get_tenant(tenant_ja["id"])["lang"] == "ko"
TestClient(app).get("/?lang=zh")                                    # ไม่ได้ล็อกอิน → ไม่แตะบัญชีใคร
assert db.get_tenant(tenant_ja["id"])["lang"] == "ko"
cust = next(x for x in S.customers() if x["tenant"]["id"] == tenant_ja["id"])
s_, h_, t_, l_ = S.review_message(cust, 1)
assert "콘텐츠" in s_ and not thai_left(s_ + t_ + h_)                # อีเมลถัดไปตามภาษาใหม่ของบัญชี
print("tenant lang: คัดลอกจากลีดตอนเปิดบัญชี · ?lang= ตอนล็อกอินเปลี่ยนภาษาบัญชี · ไม่ล็อกอินไม่แตะ OK")


# ================= 4) พันธมิตร: อีเมลต้อนรับตามภาษาที่สมัคร =================
mails.clear()
PT.activate(db.find_partner(email="min@park-web.example.com"), "test")
m = take()[0]
no_thai(m, "ko", "partner welcome")
assert "파트너" in m["subj"]
print("partner: สมัครจากหน้าเกาหลี → อีเมลต้อนรับภาษาเกาหลี OK")


# ================= 5) โมเดลเขียนเป็นภาษาของลีด + ตัวกรองคำต้องห้ามภาษาอื่น =================
lead_en = results["en"][0]
prompts = []
def en_llm(msgs):
    prompts.append(msgs[0]["content"])
    return json.dumps({"subject": "Quick note about your site",
                       "body": "Hi,\n\nI had a look at shop-en.example.com and AI assistants can't read most of the pages yet, so they skip the business when people ask. "
                               "The full report is here: " + P.result_url(lead_en) + "\n\nWould it help if I sent a short fix plan?"})
d = X.draft_outreach(lead_en, [{"key": "csr", "label": "csr", "why": LD.EXPLAIN["csr"]}], P.result_url(lead_en), llm=en_llm)
assert d["source"] == "llm" and "IMPORTANT: write the subject and body entirely in English" in prompts[-1], d["source"]
bad = lambda msgs: json.dumps({"subject": "Rank #1 guaranteed", "body": "We guarantee you will be first on Google. " * 3 + P.result_url(lead_en)})
assert X.draft_outreach(lead_en, [], P.result_url(lead_en), llm=bad)["source"].startswith("template")
assert X.FORBIDDEN.search("We guarantee first page") and not X.FORBIDDEN.search("We don't guarantee rankings, but we measure every month")
assert X.FORBIDDEN.search("保证排名第一") and not X.FORBIDDEN.search("我们不保证排名")
assert X.FORBIDDEN.search("上位表示を保証します") and not X.FORBIDDEN.search("順位を保証するものではありません")
assert X.FORBIDDEN.search("1위를 보장합니다") and not X.FORBIDDEN.search("순위를 보장하지 않습니다")
assert D.personal_problems("Hi,\n\nWe ran a 16-point check on your site.") and D.personal_problems("Free 15 minutes call?")
assert D.personal_problems("サイトを16項目でチェックしました") and D.personal_problems("15분 통화 가능하실까요?")
assert D.spam_flags("Guaranteed results", "x") and not D.spam_flags("Quick note", "Hi, short note about your site.")
assert SB.escalation("返金できますか") and SB.escalation("환불 가능한가요") and SB.escalation("能打折吗")
print("llm: prompt สั่งเขียนเป็นภาษาของลีด · ตอบผิดภาษา/คำสัญญาผล (อังกฤษ จีน ญี่ปุ่น เกาหลี) ไม่ใช้ · ปฏิเสธได้ · แบบส่งหมู่ภาษาอื่นจับได้ OK")


# ================= 6) อีเมลถึงเจ้าของยังเป็นไทย =================
mails.clear()
admin = "owner@ops.example.com"
db.create_tenant(admin, bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Ops", is_admin=True)
db.set_tenant_lang(db.get_tenant_by_email(admin)["id"], "en")
P.alert_admins("ทดสอบแจ้งเจ้าของ", "/admin", "lead", subject="แจ้งเจ้าของ")
m = take()[0]
assert m["subj"] == "แจ้งเจ้าของ" and '<html lang="th">' in m["html"] and "ขอบคุณที่ใช้บริการ" in m["html"]
print("owner: แจ้งเตือนเจ้าของยังเป็นไทย OK")

print("\nemail_i18n_smoke: ผ่านทั้งหมด")
