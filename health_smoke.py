"""ทดสอบ judge_* ของ site_health แบบไม่ยิงเน็ต — เคส CSR / ตัวเชื่อม redirect-vs-rewrite / WordPress ping

HTML จำลองถอดจากเว็บจริงที่เคยเจอ: hop-property (React CSR ส่งข้อความ 32 ตัวอักษร),
tanawat-lawyer (CSR + ลิงก์สำรองใน #root), twinveetech (SSR ที่มี <script type=module> แต่เนื้อหาเต็ม)
และ Hop ที่ตั้ง geo-sitemap.xml เป็น redirect ข้ามโดเมนซึ่งเช็คเดิมมองไม่เห็น
"""
import app.site_health as sh

# ---------- csr ----------
HOP = ('<!doctype html><html lang="th"><head><meta charset="utf-8"><title>HOP — ฐานข้อมูลทรัพย์ให้เช่า/ขาย</title>'
       '<script type="module" crossorigin src="/assets/index-Bx1.js"></script><link rel="stylesheet" href="/assets/index.css">'
       '<style>body{margin:0}</style></head><body><div id="root"></div></body></html>')
TANAWAT = HOP.replace('<div id="root"></div>', '<div id="root"><a href="/geo">บทความกฎหมาย</a></div>')
SSR = ('<html><head><title>Twin Vee Tech</title><script type="module" src="/_build/x.js"></script></head><body>'
       '<nav><a href="/">หน้าแรก</a><a href="/about">เกี่ยวกับเรา</a></nav><main><p>'
       + "บริการพัฒนาซอฟต์แวร์และที่ปรึกษาด้านไอที " * 120 + '</p></main></body></html>')
THIN_STATIC = '<html><body><p>Coming soon</p></body></html>'
PARTIAL = ('<html><head><script src="/bundle.js"></script></head><body><header><h1>ร้านของเรา</h1><p>'
           + "ข้อความสำรองสั้น ๆ " * 30 + '</p></header><div id="root"></div></body></html>')

t = sh.visible_text('<p>a &amp; b</p><!-- x --><script>var q="zzz"</script><style>.a{}</style>')
assert t == "a & b", t
print("visible_text strips script/style/comment + unescapes OK")

shp = sh.page_shape(HOP)
assert shp["text"] < sh.CSR_MIN_TEXT and shp["links"] == 0 and shp["empty_mount"] and not shp["h1"], shp
c = sh.judge_csr(shp)[0]
assert c["status"] == sh.FAIL and "JavaScript" in c["detail"], c
print("csr: React shell (hop) FAIL OK —", c["detail"][:60])

c = sh.judge_csr(sh.page_shape(TANAWAT))[0]
assert c["status"] == sh.FAIL and "ลิงก์ 1" in c["detail"], c
print("csr: CSR + ลิงก์สำรอง (tanawat) FAIL OK")

c = sh.judge_csr(sh.page_shape(SSR))[0]
assert c["status"] == sh.OK and "ไม่มี h1" in c["detail"], c
print("csr: SSR ที่มี type=module OK (ไม่ฟ้องผิด) —", c["detail"])

c = sh.judge_csr(sh.page_shape(THIN_STATIC))[0]
assert c["status"] == sh.WARN, c
c = sh.judge_csr(sh.page_shape(PARTIAL))[0]
assert c["status"] == sh.WARN and "สำรอง" in c["detail"], c
assert sh.judge_csr(None)[0]["status"] == sh.SKIP
print("csr: หน้าเปล่า WARN / เนื้อหาสำรองบางส่วน WARN / หน้าแรกเข้าไม่ได้ SKIP OK")

# ---------- connector ----------
APEX = "hop-property.vercel.app"
SITE = f"https://{APEX}"
CONTENT = {f"{SITE}/geo/101", f"{SITE}/geo/102"}
GEO_INDEX = (f'<html><head><meta name="generator" content="เจอ.AI GEO"></head><body><h1>บทความ — Hop</h1>'
             f'<ul><li><a href="{SITE}/geo/101">A</a></li><li><a href="{SITE}/geo/102">B</a></li></ul></body></html>')
XML = '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>'

def probes(geo_status=200, geo_url=f"{SITE}/geo", geo_html=GEO_INDEX,
           sm_url=f"{SITE}/geo-sitemap.xml", llms_html="# Hop Agency\n", pages=None):
    return {"/geo": {"status": geo_status, "url": geo_url, "html": geo_html},
            "/llms.txt": {"status": 200, "url": f"{SITE}/llms.txt", "html": llms_html},
            "/geo-sitemap.xml": {"status": 200, "url": sm_url, "html": XML},
            "pages": pages or [{"url": f"{SITE}/geo/101", "final": f"{SITE}/geo/101"}]}

c = sh.judge_connector(probes(), CONTENT, APEX)[0]
assert c["status"] == sh.OK and "ลิงก์บทความ 2" in c["detail"], c
print("connector: rewrite ครบ OK —", c["detail"])

# Hop จริง: sitemap เป็น 308 ไป geo.appreview.cloud — ตามไปแล้วได้ 200 แต่ Google ตีตก
c = sh.judge_connector(probes(sm_url="https://geo.appreview.cloud/e/KEY/sitemap.xml"), CONTENT, APEX)[0]
assert c["status"] == sh.FAIL and "geo-sitemap.xml redirect ไป geo.appreview.cloud" in c["detail"], c
assert "/geo " not in c["detail"].split("—")[0] or "/geo ✓" not in c["detail"], c
print("connector: sitemap redirect ข้ามโดเมน (Hop) FAIL OK")

c = sh.judge_connector(probes(geo_html=HOP), CONTENT, APEX)[0]
assert c["status"] == sh.FAIL and "fallback" in c["detail"], c
print("connector: /geo ตอบ SPA shell FAIL OK")

c = sh.judge_connector(probes(geo_status=404, geo_html=""), CONTENT, APEX)[0]
assert c["status"] == sh.FAIL and "ยังไม่ได้ตั้ง rewrite" in c["detail"], c
c = sh.judge_connector(probes(geo_status=404, geo_html=""), CONTENT, APEX, wp=True)[0]
assert c["status"] == sh.OK and "WordPress" in c["detail"], c
print("connector: /geo 404 → FAIL (hosted) / OK (WordPress) OK")

# เว็บ dev ที่ render /geo เองจาก content.json — ไม่มี marker ของเรา แต่ลิงก์ไปบทความ → ต้องผ่าน
own = f'<html><body><a href="/geo/101">A</a><a href="/geo/102">B</a></body></html>'
c = sh.judge_connector(probes(geo_html=own), CONTENT, APEX)[0]
assert c["status"] == sh.OK, c
# ยังไม่มีคอนเทนต์เผยแพร่ → หน้า /geo ว่างได้ ไม่ฟ้อง
c = sh.judge_connector(probes(geo_html="<html><body>ยังไม่มีบทความ</body></html>"), set(), APEX)[0]
assert c["status"] == sh.OK, c
print("connector: เว็บ render /geo เอง OK / ยังไม่มีคอนเทนต์ OK")

c = sh.judge_connector(probes(pages=[{"url": f"{SITE}/geo/101", "final": "https://geo.appreview.cloud/e/K/a/101"}]), CONTENT, APEX)[0]
assert c["status"] == sh.FAIL and "บทความ 1 หน้า redirect" in c["detail"], c
c = sh.judge_connector(probes(llms_html="<!doctype html><html>…"), CONTENT, APEX)[0]
assert c["status"] == sh.FAIL and "llms.txt ตอบ HTML" in c["detail"], c
print("connector: บทความ redirect ออก FAIL / llms.txt เป็น HTML fallback FAIL OK")

dead = {p: {"status": 0, "url": "", "html": ""} for p in sh.CONNECTOR_PATHS}
assert sh.judge_connector(dead, CONTENT, APEX)[0]["status"] == sh.SKIP
print("connector: เข้าเว็บไม่ได้ SKIP OK")

# ---------- wp ----------
assert sh.judge_wp(None) == []
c = sh.judge_wp({"mode": "connector", "ok": True, "msg": "Connector เชื่อมต่อแล้ว (v0.2.2)"})[0]
assert c["status"] == sh.OK and c["detail"].startswith("ปลั๊กอิน:"), c
c = sh.judge_wp({"mode": "rest", "ok": False, "msg": "ปฏิเสธ (HTTP 401)"})[0]
assert c["status"] == sh.FAIL and "REST API" in c["detail"] and "ไปไม่ถึงเว็บ" in c["detail"], c
print("wp: ไม่มี WordPress → ไม่มีข้อ / ปลั๊กอิน OK / REST ล้ม FAIL OK")

s = sh.summarize(sh.judge_csr(sh.page_shape(HOP)) + sh.judge_connector(probes(), CONTENT, APEX) + sh.judge_wp(None))
assert (s["n_fail"], s["n_ok"]) == (1, 1), s
print("ALL HEALTH TESTS OK")

# ---------- indexed จาก Search Console ----------
def gs(**k):
    base = {"synced_at": "2026-10-04T09:15:00", "age_days": 0, "total": 10, "inspected": 10,
            "indexed": 0, "crawled": 0, "discovered": 0, "unknown": 0, "blocked": 0, "other": 0, "error": 0}
    return {**base, **k}
c = sh.judge_indexed(None, None, "", 10, gsc=gs(indexed=10))[0]
assert c["status"] == sh.OK and "10/10" in c["detail"] and "Search Console" in c["detail"], c
c = sh.judge_indexed(None, None, "", 10, gsc=gs(unknown=7, crawled=3))[0]
assert c["status"] == sh.FAIL and "0/10" in c["detail"] and "ยังไม่รู้จัก URL" in c["detail"], c
c = sh.judge_indexed(None, None, "", 10, gsc=gs(unknown=10, sitemap_ok=True))[0]
assert c["status"] == sh.FAIL and "ทั้งที่ sitemap ส่งแล้ว" in c["detail"], c
c = sh.judge_indexed(None, None, "", 10, gsc=gs(indexed=3, crawled=7))[0]
assert c["status"] == sh.WARN and "อ่านแล้วแต่เลือกไม่เก็บ" in c["detail"], c
c = sh.judge_indexed(None, None, "", 10, gsc=gs(indexed=9, blocked=1))[0]
assert c["status"] == sh.FAIL and "noindex" in c["detail"], c
c = sh.judge_indexed(None, None, "", 10, gsc=gs(indexed=6, blocked=0, blocked_stale=4))[0]
assert c["status"] == sh.OK and "เคยติด noindex" in c["detail"], c
c = sh.judge_indexed(None, None, "", 10, gsc=gs(indexed=8, other=2, age_days=20))[0]
assert c["status"] == sh.OK and "20 วันก่อน" in c["detail"], c
c = sh.judge_indexed(None, None, "", 5, gsc=gs(total=5, inspected=0))[0]
assert c["status"] == sh.WARN and "ตรวจรายหน้าไม่ได้" in c["detail"], c
c = sh.judge_indexed(None, None, "", 0, gsc=gs(total=0, inspected=0))[0]
assert c["status"] == sh.OK, c
# ไม่มี Search Console → ทางเดิม (Serper) ยังทำงานเหมือนเดิม
assert sh.judge_indexed(None, None, "", 3)[0]["status"] == sh.SKIP
assert sh.judge_indexed(0, None, "", 3)[0]["status"] == sh.FAIL
print("indexed: ใช้ Search Console (ครบ/0/บางส่วน/บล็อก/เก่า/ตรวจไม่ได้) + ทางเดิมยังอยู่ OK")
print("ALL HEALTH TESTS OK (incl. gsc)")
