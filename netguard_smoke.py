"""ทดสอบตัวกัน SSRF (app/netguard.py) และ SESSION_SECRET ที่ต้องตั้ง (app/config.py)
ไม่ยิงเครือข่ายจริง: DNS ถูกแทนด้วยตัวปลอม · HTTP ใช้ httpx.MockTransport"""
import os, subprocess, sys, tempfile
if not os.getenv("DATABASE_URL"):
    os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-smoke-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
os.environ.setdefault("GEO_DEEP_AUDIT", "0")
os.environ.setdefault("SESSION_SECRET", "smoke-only-not-a-real-secret")
import socket
import httpx
import app.netguard as ng
import app.config as config
import app.leads as L

# ---- literal_blocked: ดูจากตัวหนังสือ ไม่แตะเครือข่าย ----
for bad in ["127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1", "169.254.169.254", "0.0.0.0", "100.64.0.1",
            "::1", "[::1]", "fe80::1", "fd00::1", "::ffff:127.0.0.1",
            "127.1", "0x7f.0.0.1", "2130706433", "017700000001",
            "localhost", "no-dot", "host.docker.internal", "db.local", "printer.lan", "api.corp", "", None]:
    assert ng.literal_blocked(bad), bad
for good in ["example.co.th", "www.shop.com", "8.8.8.8", "1.1.1.1", "ร้านค้า.ไทย", "Example.COM.", "2606:4700:4700::1111"]:
    assert not ng.literal_blocked(good), good
print("literal_blocked OK")

# ---- host_public: ทุก IP ที่ชื่อชี้ไปต้องเป็น IP สาธารณะ ----
def dns(table):
    def resolve(host, port, proto=0):
        if host not in table:
            raise socket.gaierror("no such host")
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in table[host]]
    return resolve

fake = dns({"good.example.com": ["93.184.216.34"], "evil.example.com": ["127.0.0.1"], "www.evil.example.com": ["127.0.0.1"],
            "mixed.example.com": ["93.184.216.34", "10.0.0.5"], "meta.example.com": ["169.254.169.254"],
            "v6.example.com": ["::1"]})
assert ng.host_public("good.example.com", resolve=fake)
assert not ng.host_public("evil.example.com", resolve=fake)          # ชื่อสาธารณะที่ชี้เข้า loopback
assert not ng.host_public("mixed.example.com", resolve=fake)         # มี IP ภายในปนแม้ตัวเดียว = ไม่ผ่าน
assert not ng.host_public("meta.example.com", resolve=fake)          # cloud metadata
assert not ng.host_public("v6.example.com", resolve=fake)
assert ng.host_public("nxdomain.example.com", resolve=fake)          # resolve ไม่ได้ = ต่อไม่ติดอยู่แล้ว
assert not ng.host_public("127.0.0.1", resolve=fake)
print("host_public OK")

# ---- client(): ตรวจทุกคำขอ รวม redirect ที่พาเข้าเครือข่ายภายใน ----
_orig_resolve = socket.getaddrinfo
socket.getaddrinfo = lambda host, *a, **k: fake(host, None)
try:
    seen = []
    def handler(req):
        seen.append(str(req.url))
        if req.url.host == "good.example.com" and req.url.path == "/hop":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
        return httpx.Response(200, text="ok")
    with ng.client(transport=httpx.MockTransport(handler), follow_redirects=True) as c:
        assert c.get("https://good.example.com/").status_code == 200
        for url in ["http://127.0.0.1:4000/", "https://evil.example.com/", "http://host.docker.internal:4000/v1/models"]:
            try:
                c.get(url)
                raise AssertionError(f"ควรถูกกัน: {url}")
            except ng.BlockedHost:
                pass
        try:
            c.get("https://good.example.com/hop")
            raise AssertionError("redirect เข้า IP ภายในควรถูกกัน")
        except ng.BlockedHost:
            pass
    assert seen == ["https://good.example.com/", "https://good.example.com/hop"], seen   # ไม่มีคำขอไหนไปถึงปลายทางภายใน
    assert isinstance(ng.BlockedHost("x"), OSError)
    print("client() blocks direct + redirect OK")

    # ---- ตัวตรวจสุขภาพเว็บ: ปลายทางภายใน = เหมือนเว็บล่ม ไม่มีคำขอหลุดออกไป ----
    import app.site_health as sh
    hits = []
    _Client = httpx.Client
    httpx.Client = lambda *a, **kw: _Client(*a, transport=httpx.MockTransport(lambda r: (hits.append(str(r.url)), httpx.Response(200, text="secret"))[1]), **kw)
    try:
        rep = sh.run_checks("https://evil.example.com")
    finally:
        httpx.Client = _Client
    assert hits == [], hits
    assert not rep["ok"] and "secret" not in str(rep)
    print("site_health.run_checks ไม่ดึงปลายทางภายใน OK")

    # ---- urlopen(): ตรวจปลายทางก่อนเปิด ----
    import urllib.request
    for url in ["http://127.0.0.1:8095/admin", "https://evil.example.com/"]:
        try:
            ng.urlopen(urllib.request.Request(url), timeout=1)
            raise AssertionError(f"ควรถูกกัน: {url}")
        except ng.BlockedHost:
            pass
    print("urlopen() OK")

    # ---- ฟอร์ม: normalize_url และหน้า /audit ----
    for raw in ["127.0.0.1", "http://169.254.169.254/latest", "host.docker.internal:4000", "http://[::1]:8095", "10.0.0.5:5434", "localhost"]:
        assert L.normalize_url(raw) is None, raw
    assert L.normalize_url("http://www.shop.com:8080/a") == "http://www.shop.com:8080"

    from fastapi.testclient import TestClient
    import app.db as db
    import app.main as M
    db.init_db()
    ran = []
    M._run_lead_audit = lambda lid, site, ip="": (ran.append(site) or "tok")
    M.deep_audit.start = lambda lid: None
    web = TestClient(M.app)
    for raw in ["127.0.0.1", "host.docker.internal:4000", "evil.example.com", "meta.example.com"]:
        r = web.post("/audit", data={"url": raw}, follow_redirects=False)
        assert r.status_code == 200 and "กรอกที่อยู่เว็บให้ถูกต้อง" in r.text, (raw, r.status_code)
    assert ran == [], ran
    r = web.post("/audit", data={"url": "good.example.com"}, follow_redirects=False)
    assert r.status_code in (302, 303) and ran == ["https://good.example.com"], (r.status_code, ran)
    print("/audit ปฏิเสธปลายทางภายใน และยังตรวจเว็บจริงได้ OK")
finally:
    socket.getaddrinfo = _orig_resolve

# ---- SESSION_SECRET: ไม่ได้ตั้ง / ค่าตัวอย่าง = ไม่เริ่มแอป ----
_keep = os.environ["SESSION_SECRET"]
for bad in ["", "   ", "dev-secret-change-me", "CHANGE_ME_LONG_RANDOM", "เปลี่ยนเป็นข้อความสุ่มยาวๆ"]:
    os.environ["SESSION_SECRET"] = bad
    try:
        config.session_secret()
        raise AssertionError(f"ควรปฏิเสธ: {bad!r}")
    except RuntimeError as e:
        assert "SESSION_SECRET" in str(e)
os.environ["SESSION_SECRET"] = _keep
assert config.session_secret() == _keep

env = {k: v for k, v in os.environ.items() if k != "SESSION_SECRET"}
env["SESSION_SECRET"] = ""                     # ค่าว่างชนะ .env (load_dotenv ไม่เขียนทับ) — จำลองเครื่องที่ลืมตั้ง
p = subprocess.run([sys.executable, "-c", "import app.main"], env=env, capture_output=True, text=True, timeout=120)
assert p.returncode != 0 and "SESSION_SECRET" in p.stderr, p.stderr[-400:]
print("SESSION_SECRET fail-fast OK")
print("ALL NETGUARD TESTS OK")
