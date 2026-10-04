"""ทดสอบ gsc.py แบบไม่ยิงเน็ต — parse คีย์, จับคู่ property, ย่อผล URL Inspection, JWT, และ sync_brand ที่ mock API

รันใน image ของแอป (ต้องมี cryptography): docker run --rm -v "$PWD:/src" -w /src geo-platform:latest python gsc_smoke.py
"""
import json
import base64
import datetime
import app.gsc as g

# ---------- parse_key ----------
for bad, kw in (("not json", "ไม่ใช่ JSON"), ('{"type":"authorized_user"}', "service_account"),
                ('{"type":"service_account","client_email":"a@b"}', "private_key")):
    try:
        g.parse_key(bad); raise SystemExit("ควรปฏิเสธ: " + bad)
    except ValueError as e:
        assert kw in str(e), (bad, str(e))
print("parse_key: ปฏิเสธไฟล์ผิดพร้อมบอกเหตุผล OK")

# ---------- match_property ----------
SITES = [{"siteUrl": "https://other.com/", "permissionLevel": "siteOwner"},
         {"siteUrl": "https://www.petgo.asia/", "permissionLevel": "siteRestrictedUser"},
         {"siteUrl": "sc-domain:petgo.asia", "permissionLevel": "siteFullUser"}]
assert g.match_property(SITES, "https://petgo.asia") == ("sc-domain:petgo.asia", "siteFullUser")
assert g.match_property(SITES[:2], "https://petgo.asia") == ("https://www.petgo.asia/", "siteRestrictedUser")
assert g.match_property([{"siteUrl": "https://jkppropertyagency.com/th/", "permissionLevel": "siteFullUser"}],
                        "jkppropertyagency.com")[0] == "https://jkppropertyagency.com/th/"
assert g.match_property(SITES, "https://nobody.example") == (None, None)
assert g.candidate_props("https://www.Petgo.asia/")[0] == "sc-domain:petgo.asia"
print("match_property: domain property ก่อน → www → prefix ลึก → ไม่เจอ OK")

# ---------- classify ----------
def res(verdict="NEUTRAL", state="", **idx):
    return {"inspectionResult": {"inspectionResultLink": "https://search.google.com/x",
                                 "indexStatusResult": {"verdict": verdict, "coverageState": state, **idx}}}
assert g.classify(res("PASS", "Submitted and indexed"))["group"] == g.INDEXED
assert g.classify(res("NEUTRAL", "Crawled - currently not indexed", lastCrawlTime="2026-09-30T01:02:03Z"))["group"] == g.CRAWLED
assert g.classify(res("NEUTRAL", "Discovered - currently not indexed"))["group"] == g.DISCOVERED
assert g.classify(res("NEUTRAL", "URL is unknown to Google"))["group"] == g.UNKNOWN
assert g.classify(res("FAIL", "Excluded by 'noindex' tag", indexingState="BLOCKED_BY_META_TAG"))["group"] == g.BLOCKED
assert g.classify(res("NEUTRAL", "Blocked by robots.txt", robotsTxtState="DISALLOWED"))["group"] == g.BLOCKED
assert g.classify(res("NEUTRAL", "Page with redirect"))["group"] == g.OTHER
assert g.classify({})["group"] == g.OTHER
c = g.classify(res("PASS", "Submitted and indexed", googleCanonical="https://x/geo/1"))
assert c["link"].startswith("https://search.google.com") and c["google_canonical"] == "https://x/geo/1"
# Google แปล coverageState ตามภาษา — fallback ไทยต้องจำแนกถูกด้วย (รอบแรกของจริง 12 หน้าตกไป "อื่น ๆ")
assert g.classify(res("NEUTRAL", "Google ไม่รู้จัก URL"))["group"] == g.UNKNOWN
assert g.classify(res("NEUTRAL", "ค้นพบแล้ว - ยังไม่ได้จัดทำดัชนีในขณะนี้"))["group"] == g.DISCOVERED
assert g.classify(res("NEUTRAL", "รวบรวมข้อมูลแล้ว - ยังไม่ได้จัดทำดัชนี"))["group"] == g.CRAWLED
c = g.classify(res("NEUTRAL", "Discovered - currently not indexed", sitemap=["https://x/geo-sitemap.xml"], referringUrls=["https://x/geo"]))
assert c["in_sitemap"] and c["referrers"] == 1, c
assert g.classify(res("PASS", "ok"))["in_sitemap"] is False
print("classify: PASS→indexed / crawled / discovered / unknown / blocked (meta+robots) / other / fallback ไทย / in_sitemap OK")

# inspect ต้องขอ en-US — classify จำแนกจากข้อความอังกฤษ
captured = {}
_orig_req = g._req
g._req = lambda key, method, url, **kw: captured.update(kw.get("json") or {}) or res("PASS", "Submitted and indexed")
row = g.inspect({"client_email": "x"}, "sc-domain:x.com", "https://x.com/geo/1")
assert captured == {"inspectionUrl": "https://x.com/geo/1", "siteUrl": "sc-domain:x.com", "languageCode": "en-US"}, captured
assert row["group"] == g.INDEXED and row["url"] == "https://x.com/geo/1"
g._req = _orig_req
print("inspect: ส่ง inspectionUrl/siteUrl/languageCode=en-US ตามสเปก OK")

# ---------- JWT ----------
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                         serialization.NoEncryption()).decode()
KEY = {"type": "service_account", "client_email": "geo@proj.iam.gserviceaccount.com",
       "private_key": pem, "private_key_id": "kid1", "token_uri": "https://oauth2.googleapis.com/token"}
assert g.parse_key(json.dumps(KEY))["client_email"] == KEY["client_email"]
tok = g.make_jwt(KEY, now=1_800_000_000)
h, c, sig = tok.split(".")
pad = lambda s: s + "=" * (-len(s) % 4)
header = json.loads(base64.urlsafe_b64decode(pad(h)))
claims = json.loads(base64.urlsafe_b64decode(pad(c)))
assert header == {"alg": "RS256", "typ": "JWT", "kid": "kid1"}, header
assert claims == {"iss": KEY["client_email"], "scope": g.SCOPE, "aud": g.TOKEN_URL,
                  "iat": 1_800_000_000, "exp": 1_800_003_600}, claims
priv.public_key().verify(base64.urlsafe_b64decode(pad(sig)), f"{h}.{c}".encode(), padding.PKCS1v15(), hashes.SHA256())
print("make_jwt: header/claims ตามสเปก service-account flow + ลายเซ็น RS256 ตรวจผ่านด้วย public key OK")

# ---------- sync_brand (mock API) ----------
SITE = "https://petgo.asia"
PAGES = [{"title": f"บทความ {i}", "url": f"{SITE}/geo/{i}"} for i in (1, 2, 3)]
calls = {"submit": 0, "inspect": [], "sitemap_get": 0}
sm_store = {}
g.list_sites = lambda key: SITES
def fake_status(key, prop, url):
    calls["sitemap_get"] += 1
    return sm_store.get(url)
def fake_submit(key, prop, url):
    calls["submit"] += 1
    sm_store[url] = {"path": url, "lastSubmitted": "2026-10-04T10:00:00Z", "isPending": True,
                     "warnings": "0", "errors": "0", "contents": [{"type": "web", "submitted": "4"}]}
g.sitemap_status, g.submit_sitemap = fake_status, fake_submit
STATES = {1: ("PASS", "Submitted and indexed"), 2: ("NEUTRAL", "Crawled - currently not indexed"),
          3: ("NEUTRAL", "URL is unknown to Google"), 4: ("FAIL", "Excluded by 'noindex' tag"),
          5: ("FAIL", "Excluded by 'noindex' tag")}
# หน้า 4 แก้ noindex ไปแล้ว (เช็คสดไม่เจอ) · หน้า 5 ยังติดอยู่จริง
g._live_noindex = lambda url: None if url.endswith("/4") else "meta robots: noindex"
def fake_inspect(key, prop, url):
    calls["inspect"].append((prop, url))
    v, s = STATES[int(url.rsplit("/", 1)[1])]
    extra = {"indexingState": "BLOCKED_BY_META_TAG"} if v == "FAIL" else {}
    return {"url": url, **g.classify(res(v, s, lastCrawlTime="2026-09-29T00:00:00Z", **extra))}
g.inspect = fake_inspect
def fake_analytics(key, prop, body):
    dims = body.get("dimensions") or []
    if not dims:
        return [{"clicks": 120, "impressions": 3400, "ctr": 0.035, "position": 18.4}]
    if dims == ["page"]:
        return [{"keys": [f"{SITE}/geo/1/"], "clicks": 9, "impressions": 300, "ctr": 0.03, "position": 7.2},
                {"keys": [f"{SITE}/about"], "clicks": 50, "impressions": 900, "ctr": 0.05, "position": 3.1}]
    if dims == ["query"]:
        return [{"keys": ["รับส่งสัตว์เลี้ยง"], "clicks": 40, "impressions": 600, "ctr": 0.06, "position": 2.3}]
    if dims == ["query", "page"]:
        return [{"keys": ["ส่งแมวไปต่างจังหวัด", f"{SITE}/geo/1"], "clicks": 6, "impressions": 200, "position": 8.0},
                {"keys": ["ส่งแมวไปต่างจังหวัด", f"{SITE}/geo/2"], "clicks": 0, "impressions": 100, "position": 20.0},
                {"keys": ["อื่น", f"{SITE}/about"], "clicks": 3, "impressions": 10, "position": 1.0}]
    raise AssertionError(dims)
g.analytics = fake_analytics

rep = g.sync_brand(SITE, PAGES, key=KEY, today=datetime.date(2026, 10, 4))
assert rep["linked"] and rep["property"] == "sc-domain:petgo.asia" and rep["permission"] == "siteFullUser", rep
assert calls["submit"] == 1 and rep["sitemap"]["just_submitted"] and rep["sitemap"]["submitted"] and rep["sitemap"]["n_urls"] == 4, rep["sitemap"]
assert [u for _, u in calls["inspect"]] == [p["url"] for p in PAGES] and all(pr == "sc-domain:petgo.asia" for pr, _ in calls["inspect"])
idx = rep["index"]
assert (idx["total"], idx["inspected"], idx["indexed"], idx["crawled"], idx["unknown"]) == (3, 3, 1, 1, 1), idx
an = rep["analytics"]
assert an["start"] == "2026-09-04" and an["end"] == "2026-10-01", an      # ช้ากว่าวันนี้ 3 วัน ย้อน 28 วัน
assert an["site"] == {"clicks": 120, "impressions": 3400, "ctr": 0.035, "position": 18.4}, an["site"]
assert rep["pages"][0]["clicks"] == 9 and rep["pages"][0]["position"] == 7.2        # join ทน / ท้าย URL
assert rep["pages"][1]["clicks"] is None                                            # ไม่มีข้อมูล = ไม่ใช่ 0
assert an["content"] == {"clicks": 9, "impressions": 300}, an["content"]
assert an["top_queries"][0]["query"] == "รับส่งสัตว์เลี้ยง"
cq = an["content_queries"]
assert len(cq) == 1 and cq[0]["clicks"] == 6 and cq[0]["impressions"] == 300 and cq[0]["position"] == 12.0, cq  # เฉลี่ยถ่วง impressions
assert rep["errors"] == []
print("sync_brand: จับคู่ property · ส่ง sitemap ที่ยังไม่มี · ตรวจ 3 หน้า · join คลิกรายหน้า · คำค้นหน้าคอนเทนต์ OK")

hs = g.health_summary(rep)
assert hs["inspected"] == 3 and hs["indexed"] == 1 and hs["unknown"] == 1 and hs["age_days"] >= 0 and hs["sitemap_ok"], hs
assert g.health_summary({"linked": False}) is None and g.health_summary(None) is None
print("health_summary: สรุปให้ site_health / None เมื่อยังไม่เชื่อม OK")

# sitemap ส่งแล้วแต่ Google ไม่อ่านมา 14 วัน → ส่งซ้ำ · อ่านเมื่อ 2 วันก่อน → ไม่ยุ่ง
for ago, expect in ((14, True), (2, False)):
    calls["submit"] = 0
    sm_store[f"{SITE}/geo-sitemap.xml"] = {"path": "x", "lastSubmitted": "2026-09-01T00:00:00Z", "isPending": False,
        "lastDownloaded": (datetime.date(2026, 10, 4) - datetime.timedelta(days=ago)).isoformat() + "T01:00:00Z",
        "warnings": "0", "errors": "0", "contents": [{"type": "web", "submitted": "17"}]}
    r5 = g.sync_brand(SITE, PAGES[:1], key=KEY, today=datetime.date(2026, 10, 4))
    assert (calls["submit"] == 1) is expect and r5["sitemap"]["re_submitted"] is expect and not r5["sitemap"]["just_submitted"], (ago, r5["sitemap"])
    if expect: assert "14 วัน" in r5["sitemap"]["note"], r5["sitemap"]["note"]
sm_store.clear()
print("sync_brand: sitemap ค้าง 14 วัน → ส่งซ้ำ · 2 วัน → ไม่ส่ง OK")

# ไม่เจอ property → ไม่ล้ม บอกเหตุผล + รายชื่อที่ลองหา และไม่ยิง inspect
calls["inspect"].clear()
rep2 = g.sync_brand("https://nobody.example", PAGES, key=KEY)
assert not rep2["linked"] and "nobody.example" in rep2["tried"][0] and KEY["client_email"] in rep2["reason"] and not calls["inspect"], rep2
# Restricted → ไม่ส่ง sitemap แต่ยังตรวจ index ได้
calls["submit"] = 0; sm_store.clear()
g.list_sites = lambda key: [{"siteUrl": "https://www.petgo.asia/", "permissionLevel": "siteRestrictedUser"}]
rep3 = g.sync_brand(SITE, PAGES[:1], key=KEY, today=datetime.date(2026, 10, 4))
assert rep3["linked"] and calls["submit"] == 0 and not rep3["sitemap"]["submitted"] and "Restricted" in rep3["sitemap"]["note"], rep3["sitemap"]
assert rep3["index"]["inspected"] == 1
# API ล้มบางขั้น → เก็บใน errors ไม่ล้มทั้งรอบ
def boom(*a, **k): raise RuntimeError("quota")
g.analytics = boom
rep4 = g.sync_brand(SITE, PAGES[:1], key=KEY)
assert rep4["linked"] and rep4["errors"] and rep4["errors"][0]["step"] == "analytics" and "quota" in rep4["errors"][0]["msg"], rep4["errors"]
print("sync_brand: ไม่เจอ property / Restricted / analytics ล้ม — ไม่ล้มทั้งรอบ OK")

# noindex ที่ Google จำ vs ที่ยังติดจริง
g.analytics = fake_analytics
P45 = [{"title": "เก่า", "url": f"{SITE}/geo/4"}, {"title": "ยังติด", "url": f"{SITE}/geo/5"}]
r6 = g.sync_brand(SITE, P45, key=KEY, today=datetime.date(2026, 10, 4))
assert r6["index"]["blocked"] == 2 and r6["index"]["blocked_stale"] == 1, r6["index"]
assert r6["pages"][0]["stale"] is True and r6["pages"][1]["stale"] is False and r6["pages"][1]["live_noindex"] == "meta robots: noindex"
hs6 = g.health_summary(r6)
assert hs6["blocked"] == 1 and hs6["blocked_stale"] == 1, hs6
print("sync_brand: แยก noindex ที่ Google จำของเก่า (แก้แล้ว) ออกจากที่ยังติดจริง OK")

# Google ช้าครั้งเดียว → ลองซ้ำแล้วได้ผล · ช้าสองครั้ง → ตรวจไม่ได้ (ไม่ค้าง ไม่ล้มทั้งรอบ)
import httpx
flaky = {"n": 0}
def flaky_inspect(key, prop, url):
    flaky["n"] += 1
    if flaky["n"] == 1: raise httpx.ReadTimeout("slow")
    return {"url": url, **g.classify(res("PASS", "Submitted and indexed"))}
g.inspect = flaky_inspect
r7 = g.sync_brand(SITE, PAGES[:1], key=KEY, today=datetime.date(2026, 10, 4))
assert flaky["n"] == 2 and r7["index"]["indexed"] == 1 and r7["index"]["error"] == 0, (flaky, r7["index"])
def always_slow(key, prop, url): raise httpx.ReadTimeout("slow")
g.inspect = always_slow
r8 = g.sync_brand(SITE, PAGES[:1], key=KEY, today=datetime.date(2026, 10, 4))
assert r8["index"]["error"] == 1 and "ReadTimeout" in r8["pages"][0]["state"], r8["pages"][0]["state"]
print("sync_brand: timeout ครั้งเดียวลองซ้ำสำเร็จ · timeout ซ้ำบันทึกว่าตรวจไม่ได้ OK")
print("ALL GSC TESTS OK")
