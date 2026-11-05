"""ทดสอบข้อ "เปิดทางบอท AI": ยิงหน้าแรกด้วย UA จริงของบอท ไม่ใช่แค่อ่าน robots.txt
· robots.txt เปิด แต่ไฟร์วอลล์ตอบ 403 ใส่บอทฝึกโมเดล = เตือน (บอกทางแก้ใน Cloudflare) · บอทค้น/ตอบสดโดน = ไม่ผ่าน
· robots.txt บล็อกอยู่แล้ว = คงผลเดิม · ทุกข้อความแปลครบ 4 ภาษา"""
import os, re
os.environ["SERPER_API_KEY"] = ""
import httpx
import app.site_health as sh, app.i18n as I

I.load(force=True)
THAI = re.compile(r"[฀-๿]")
ROBOTS_OPEN = "User-agent: *\nAllow: /\nSitemap: https://acme.example/geo-sitemap.xml\n"


def site(blocked: set, server="cloudflare", robots=ROBOTS_OPEN):
    """เว็บปลอม: UA ที่มีชื่อบอทใน blocked ได้ 403 ทุก path"""
    def handler(req: httpx.Request):
        ua = req.headers.get("user-agent", "")
        if any(b + "/" in ua for b in blocked):
            return httpx.Response(403, html="<html>blocked</html>", headers={"server": server})
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=robots, headers={"server": server})
        if req.url.path == "/":
            return httpx.Response(200, html="<html><title>Acme</title><body>" + "ข้อความ " * 200 + "</body></html>", headers={"server": server})
        return httpx.Response(404, html="<html>404</html>", headers={"server": server})
    return handler


_Client = httpx.Client


def run(handler):
    httpx.Client = lambda *a, **kw: _Client(*a, transport=httpx.MockTransport(handler), **kw)
    try:
        return next(c for c in sh.run_checks("https://acme.example")["checks"] if c["key"] == "ai_bots")
    finally:
        httpx.Client = _Client


c = run(site(set()))
assert c["status"] == "ok", c
c = run(site({"GPTBot", "ClaudeBot", "CCBot"}))                       # ค่า Training = Disallow ของ Cloudflare
assert c["status"] == "warn" and "GPTBot, ClaudeBot, CCBot" in c["detail"] and "Training = Allow" in c["detail"], c
c = run(site({"GPTBot", "OAI-SearchBot"}, server="nginx"))             # บอทที่ ChatGPT ใช้ค้นสดโดนด้วย
assert c["status"] == "fail" and "OAI-SearchBot" in c["detail"] and "Cloudflare" not in c["detail"], c
c = run(site({"ClaudeBot"}, robots="User-agent: GPTBot\nDisallow: /\n"))   # robots บล็อกอยู่แล้ว → แก้ robots ก่อน
assert c["status"] == "fail" and c["detail"] == "ถูกบล็อก: GPTBot", c
print("ยิงหน้าแรกด้วย UA บอทจริง: เปิดครบ = ผ่าน · บล็อกบอทฝึกโมเดล = เตือน · บล็อกบอทค้นสด = ไม่ผ่าน · robots บล็อก = คงผลเดิม OK")

for blocked, server in (({"GPTBot"}, "cloudflare"), ({"GPTBot"}, "nginx"), ({"OAI-SearchBot"}, "cloudflare"), ({"OAI-SearchBot"}, "nginx")):
    d = sh.judge_bot_access([], {b: 403 for b in blocked}, server)[0]["detail"]
    for lang in ("en", "zh", "ja", "ko"):
        t = I.tr(d, lang)
        assert not THAI.search(t) and next(iter(blocked)) in t, (lang, d, t)
print("ข้อความไฟร์วอลล์บล็อกบอท แปลครบ en zh ja ko OK")
print("ALL OK")
