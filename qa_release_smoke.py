"""ทดสอบ stage 08A: QA/SEO ของเว็บสาธารณะชุดใหม่ + ความเป็นส่วนตัวของหน้าลิงก์ลับ + เส้นทางลูกค้าแบบต้นจนจบ
- หน้าสาธารณะ (flag เปิด): title/description ไม่ซ้ำ · canonical ชี้ตัวเอง · h1 เดียว · ไม่ noindex · ฟอร์มทุกช่องมี label · ฟอร์มส่งได้โดยไม่มี JS
  · รูปมี alt · ลิงก์ภายในไม่เสีย · ข้อมูลโครงสร้างตรงกับที่แสดง
- sitemap มีเฉพาะหน้าสาธารณะ และทุก URL เปิดได้ไม่ noindex · robots กันเส้นทางส่วนตัว
- หน้าลิงก์ลับ/หลังล็อกอิน (รวม PDF): noindex + private/no-store + no-referrer · service worker แคชแค่ /static
- เส้นทาง: หน้าแรก → รายงาน → ยืนยันข้อมูลธุรกิจ → หลักฐาน → ขอแผน → ทีมเห็น Sales Brief · flag ปิด = หน้าเดิมทำงาน
ไม่ยิงเครือข่าย · DB ชั่วคราว · ไม่ส่งข้อความจริง"""
import os, re, json, tempfile
from html.parser import HTMLParser
from urllib.parse import urlparse
os.environ.pop("DATABASE_URL", None)
os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-qa-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ["AI_BASE_URL"] = "http://127.0.0.1:9"
os.environ["GEO_DEEP_AUDIT"] = "0"
for k in list(os.environ):
    if k.startswith("GEO_FLAG_"):
        os.environ.pop(k)
import bcrypt
from fastapi.testclient import TestClient
import app.db as db
import app.leads as L
import app.flags as F
import app.pipeline as pipeline
from app.main import app, _is_private_path

pipeline.alert_admins = lambda *a, **kw: None
db.init_db()
SITE = "https://ours.example"
tid = db.create_tenant("admin@qa.test", bcrypt.hashpw(b"pw123456", bcrypt.gensalt()).decode(), "Admin", is_admin=True)
bid = db.create_brand(tid, "เจอ.AI", SITE, "แพลตฟอร์ม")
db.set_setting("self_brand_id", str(bid))
c = TestClient(app)


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.title, self.metas, self.links, self.hrefs, self.h1, self.imgs = "", {}, [], [], 0, []
        self.inputs, self.labels_for, self.forms, self.ld, self._t, self._ld, self._lbl = [], set(), [], [], False, None, 0
        self.feed(html)

    def handle_starttag(self, tag, a):
        a = dict(a)
        if tag == "title":
            self._t = True
        elif tag == "meta" and a.get("name"):
            self.metas[a["name"]] = a.get("content") or ""
        elif tag == "link" and a.get("rel") in ("canonical", "alternate"):
            self.links.append((a["rel"], a.get("hreflang"), a.get("href")))
        elif tag == "a" and a.get("href"):
            self.hrefs.append(a["href"])
        elif tag == "h1":
            self.h1 += 1
        elif tag == "img":
            self.imgs.append(a)
        elif tag == "label":
            self._lbl += 1
            if a.get("for"):
                self.labels_for.add(a["for"])
        elif tag in ("input", "select", "textarea"):
            if a.get("type") not in ("hidden", "submit", "button") and "mk-hp" not in (a.get("class") or ""):
                self.inputs.append({**a, "_wrapped": self._lbl > 0})
        elif tag == "form":
            self.forms.append(a)
        elif tag == "script" and a.get("type") == "application/ld+json":
            self._ld = ""

    def handle_endtag(self, tag):
        if tag == "title":
            self._t = False
        elif tag == "label":
            self._lbl -= 1
        elif tag == "script" and self._ld is not None:
            self.ld.append(json.loads(self._ld))
            self._ld = None

    def handle_data(self, d):
        if self._t:
            self.title += d
        if self._ld is not None:
            self._ld += d


def text_of(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


# ================= flag ปิด: หน้าเดิมยังทำงาน =================
for p in ("/", "/audit", "/partners", "/login", "/robots.txt", "/sitemap.xml"):
    assert c.get(p).status_code == 200, p
for p in ("/how-it-works", "/services"):
    assert c.get(p).status_code == 404, p                                            # หน้าใหม่ยังไม่เปิด
assert "/how-it-works" not in c.get("/sitemap.xml").text

# ================= flag เปิด: หน้าสาธารณะ =================
for f in ("marketing_v2", "report_v2", "snapshot_v2", "plan_request_v2", "context_v2"):
    F.set_enabled(f, True)
PUBLIC_MK = ["/", "/how-it-works", "/services", "/services/foundation-sprint", "/solutions/industrial-property"]
titles, descs, internal = {}, {}, set()
for p in PUBLIC_MK:
    r = c.get(p)
    assert r.status_code == 200, p
    pg = Page(r.text)
    assert "noindex" not in r.headers.get("x-robots-tag", "") and "noindex" not in pg.metas.get("robots", ""), p
    assert pg.title.strip() and pg.title not in titles.values(), (p, pg.title)
    d = pg.metas.get("description", "")
    assert 50 <= len(d) <= 200 and d not in descs.values(), (p, d)
    titles[p], descs[p] = pg.title, d
    can = [h for rel, _, h in pg.links if rel == "canonical"]
    assert can == [SITE + p], (p, can)
    assert pg.h1 == 1, (p, pg.h1)
    assert re.search(r'<html lang="th"', r.text), p
    for i in pg.inputs:                                                               # ทุกช่องกรอกมีป้าย
        assert i.get("id") in pg.labels_for or i["_wrapped"] or i.get("aria-label"), (p, i)
    for f in pg.forms:                                                                # ส่งได้โดยไม่ต้องมี JS (ยกเว้นแชตเสริม _chat_widget ที่ใช้ JS อยู่แล้ว ไม่ใช่เส้นทางหลัก)
        if (f.get("id") or "").startswith("jc-"):
            continue
        assert f.get("action") and (f.get("method") or "get").lower() in ("post", "get"), (p, f)
    for im in pg.imgs:
        assert "alt" in im, (p, im)
    for h in pg.hrefs:
        u = urlparse(h)
        if not u.scheme and h.startswith("/") and not h.startswith("//"):
            internal.add(h.split("#")[0])
        elif u.scheme in ("http", "https") and u.netloc == "ours.example":
            internal.add(u.path or "/")
        assert not h.lower().startswith("javascript:"), (p, h)
    for ld in pg.ld:                                                                  # ข้อมูลโครงสร้างตรงกับที่เห็น
        assert ld["@context"] == "https://schema.org"
        if ld["@type"] == "FAQPage":
            body = text_of(r.text)
            for q in ld["mainEntity"]:
                assert q["name"] in body and q["acceptedAnswer"]["text"][:40] in body, q["name"]
        assert "aggregateRating" not in json.dumps(ld) and "review" not in json.dumps(ld).lower()
home = Page(c.get("/").text)
assert {x["@type"] for x in home.ld} == {"Organization", "FAQPage"}
assert any(rel == "alternate" and hl == "x-default" for rel, hl, _ in home.links)

# ลิงก์ภายในจากทุกหน้าไม่เสีย (3xx ได้ เช่น /login พาไปหน้าอื่น)
bad = []
for h in sorted(internal):
    if not h or h.startswith(("/static/", "/audit/")):
        continue
    st = c.get(h, follow_redirects=False).status_code
    if st >= 400:
        bad.append((h, st))
assert not bad, bad
assert c.get("/static/favicon-32.png").status_code == 200

# sitemap: เฉพาะหน้าสาธารณะ เปิดได้ทุกหน้า ไม่ noindex · ไม่มีเส้นทางส่วนตัว
sm = c.get("/sitemap.xml").text
locs = re.findall(r"<loc>([^<]+)</loc>", sm)
assert locs and all(l.startswith(SITE) for l in locs)
for p in PUBLIC_MK[1:]:
    assert SITE + p in locs, p
for l in locs:
    path = urlparse(l).path or "/"
    assert not _is_private_path(path), l
    r = c.get(path)
    assert r.status_code == 200 and "noindex" not in r.headers.get("x-robots-tag", ""), (path, r.status_code)
rb = c.get("/robots.txt").text
for p in ("/audit/", "/q/", "/bill/", "/doc/", "/admin", "/u/", "/partner/"):
    assert f"Disallow: {p}" in rb, p
assert "Sitemap:" in rb

# ================= หน้าลิงก์ลับ/หลังล็อกอิน =================
assert _is_private_path("/audit/abc") and _is_private_path("/q/x.pdf") and _is_private_path("/app") and _is_private_path("/app/brands")
assert not _is_private_path("/audit") and not _is_private_path("/apple") and not _is_private_path("/partners") and not _is_private_path("/")
lid = db.add_lead("https://shop-qa.co.th", "shop-qa.co.th", source="audit", ip="1.2.3.4")
tok = L.new_token()
T1 = {"state": "done", "at": db.now(), "brand": "ShopQA", "keywords": ["โกดังให้เช่า บางนา"], "crawl": {"n": 2},
      "pages": [{"url": "https://shop-qa.co.th/", "status": 200, "title": "ShopQA", "desc": ""}], "issues": [],
      "health": {"score": 90, "errors": 0, "warnings": 0, "notices": 0, "urls": 2},
      "ranking": {"rows": [{"keyword": "โกดังให้เช่า บางนา", "top": ["rival.co.th"], "position": None, "url": None, "depth": 20}],
                  "competitors": [{"domain": "rival.co.th", "hits": 1}], "n": 1, "ok": 1, "page1": 0, "depth": 20,
                  "protocol": {"id": "search-rank-v1", "version": 1, "provider": "serper"}}}
db.update_lead(lid, token=tok, audit_at=db.now(), deep_json=json.dumps(T1, ensure_ascii=False),
               audit_json=json.dumps({"ok": True, "checks": [{"key": "home", "label": "หน้าแรก", "status": "ok"},
                                                            {"key": "robots", "label": "robots.txt", "status": "fail", "detail": "Disallow: /"}]}))
qid = db.add_quote(lid, "Q-QA", "qatok", "pro", 1000, 0, 1000, False, 0, 1000, "2026-12-31")
db.update_quote(qid, status="sent")
for p in (f"/audit/{tok}", f"/audit/{tok}?view=full", f"/audit/{tok}/progress", "/q/qatok", "/q/qatok.pdf", "/q/qatok/contract",
          "/bill/nope", "/bill/nope.pdf", "/doc/nope.pdf", "/u/nope", "/partner/nope", "/app", "/admin"):
    r = c.get(p, follow_redirects=False)
    assert "noindex" in r.headers.get("x-robots-tag", ""), (p, r.status_code, dict(r.headers))
    assert "no-store" in r.headers.get("cache-control", "") and "public" not in r.headers.get("cache-control", ""), (p, r.headers.get("cache-control"))
    assert r.headers.get("referrer-policy") == "no-referrer", p
for p in ("/", "/how-it-works"):
    assert "x-robots-tag" not in c.get(p).headers, p

# service worker: แคชเฉพาะ /static · หน้าเว็บ network-first ไม่เก็บลงแคช · มีเวอร์ชันแคช
sw = c.get("/sw.js")
assert sw.headers.get("cache-control") == "no-cache" and re.search(r"const CACHE = 'geo-v\d+'", sw.text)
assert sw.text.count("c.put(") == 1 and sw.text.index("c.put(") > sw.text.index("startsWith('/static/')")
assert "req.method !== 'GET'" in sw.text

# ================= เส้นทางลูกค้าต้นจนจบ =================
cu = TestClient(app)                                                                     # ผู้เยี่ยมชมใหม่ (c เคยเปิด /ko แล้วจำภาษาเกาหลีไว้)
db.update_quote(qid, status="draft")                                                    # ยังไม่มีใบเสนอราคาที่ส่งแล้ว
html = cu.get(f"/audit/{tok}").text
assert 'data-step="confirm_context"' in html and 'id="context"' in html
csrf = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
r = cu.post(f"/audit/{tok}/context", data={"csrf_token": csrf, "primary_service": "ให้เช่าโกดัง", "region": "บางนา", "competitor": "rival.co.th"},
           follow_redirects=False)
assert r.status_code in (302, 303)
html = cu.get(f"/audit/{tok}").text
assert "ตัวเลขดิบและหลักฐาน" in html and "ดูหลักฐาน" in html and 'id="draft"' in html
assert 'data-step="request_plan"' in html                                                # ยืนยันแล้ว → ขอแผน
r = cu.post(f"/audit/{tok}/plan", data={"csrf_token": csrf, "idem_key": "qa-1", "name": "สมชาย", "contact_channel": "phone",
                                       "contact_value": "0812345678", "consent_fulfil": "1"}, follow_redirects=False)
assert r.status_code in (302, 303)
html = cu.get(f"/audit/{tok}").text
assert "ได้รับคำขอแผนแล้ว" in html and 'data-step="request_meeting"' in html
db.update_quote(qid, status="sent")
assert 'data-step="view_quote"' in cu.get(f"/audit/{tok}").text                            # ทีมส่งใบเสนอราคาแล้ว
adm = TestClient(app)
assert adm.post("/login", data={"email": "admin@qa.test", "password": "pw123456"}).status_code == 200
brief = adm.get(f"/admin/leads/{lid}").text
assert "Sales Brief" in brief and "ให้เช่าโกดัง" in brief and "ลูกค้ายืนยัน" in brief                   # ทีมเห็นบริบทที่ลูกค้ายืนยัน

# flag ปิดอีกครั้ง = กลับเป็นหน้าเดิม รายงานเปิดได้
for f in ("marketing_v2", "report_v2", "snapshot_v2", "plan_request_v2", "context_v2"):
    F.set_enabled(f, False)
assert c.get(f"/audit/{tok}").status_code == 200 and 'id="summary"' not in c.get(f"/audit/{tok}").text
assert c.get("/how-it-works").status_code == 404 and c.get("/").status_code == 200
print("qa_release_smoke OK")
