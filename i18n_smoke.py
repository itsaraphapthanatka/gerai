"""ทดสอบการแปลทั้งเว็บ: คลังคำแปลครบ 4 ภาษา · ตัวแปร {x} ตรงกัน · ทุก _("…") ในหน้ามีคำแปล
· หน้าที่แปลแล้ว (CONVERTED) ไม่มีภาษาไทยหลงนอก _() · ?lang= จำลงคุกกี้ทั้งเว็บ · หน้าแรกตามภาษาที่เลือก · ข้อความที่มีตัวเลขแทรกแปลได้"""
import os, tempfile, re, json, glob
os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-i18n-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
from fastapi.testclient import TestClient
import app.db as db, app.main as M, app.i18n as I

db.init_db()
I.load(force=True)
OTHER = [l for l in I.LANGS if l != "th"]
THAI = re.compile(r"[฀-๿]")

# หน้าที่แปลครบแล้ว — เพิ่มชื่อไฟล์เมื่อแปลหน้าใหม่ (ตรวจว่าไม่มีไทยหลงนอก _())
CONVERTED = ["audit.html", "_lang.html", "_logo.html"]
for f in glob.glob("app/i18n/*.pages"):                                                   # แต่ละกลุ่มประกาศหน้าที่แปลแล้วในไฟล์ .pages
    CONVERTED += [x.strip() for x in open(f, encoding="utf-8") if x.strip() and not x.startswith("#")]

# ---- คลังคำแปล ----
FIELD = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
cats = {}
for f in sorted(glob.glob("app/i18n/*.json")):
    data = json.load(open(f, encoding="utf-8"))
    for k, v in data.items():
        assert isinstance(v, dict), (f, k)
        for lg in OTHER:
            assert (v.get(lg) or "").strip(), f"{f}: ขาดคำแปล {lg} ของ {k!r}"
            assert set(FIELD.findall(v[lg])) == set(FIELD.findall(k)), f"{f}: ตัวแปรไม่ตรง ({lg}) {k!r}"
            assert not THAI.search(re.sub(r"เจอ\.AI|฿", "", v[lg])), f"{f}: คำแปล {lg} มีภาษาไทย {k!r}"
        cats[k] = f
print(f"คลังคำแปล: {len(cats)} ข้อความ · ครบ 4 ภาษา · ตัวแปรตรงกัน OK")

# ---- ทุก _("…") ใน template มีคำแปล ----
CALL = re.compile(r"""_\(\s*(["'])((?:(?!\1).|\\\1)*?)\1""", re.S)
missing = {}
for path in glob.glob("app/templates/*.html"):
    src = open(path, encoding="utf-8").read()
    for _q, msg in CALL.findall(src):
        if THAI.search(msg) and not I.has(msg):
            missing.setdefault(os.path.basename(path), []).append(msg)
assert not missing, "ยังไม่มีคำแปล: " + json.dumps(missing, ensure_ascii=False, indent=1)[:3000]
print("template: ทุก _(\"…\") มีคำแปล OK")

# ---- หน้าที่แปลแล้วไม่มีภาษาไทยหลง ----
def leftover(src: str) -> list[str]:
    s = re.sub(r"\{#.*?#\}", "", src, flags=re.S)
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"(?m)^\s*//.*$|(?<=[;{}\s])//[^\n'\"]*$", "", s)
    s = CALL.sub("", s)
    s = re.sub(r"เจอ(?=<span class=\"ai\">|\.AI|<span)", "", s)          # ชื่อแบรนด์ เจอ.AI
    s = s.replace("฿", "")
    return [ln.strip()[:120] for ln in s.splitlines() if THAI.search(ln)]

bad = {}
for name in sorted(set(CONVERTED)):
    path = os.path.join("app/templates", name)
    if os.path.exists(path):
        left = leftover(open(path, encoding="utf-8").read())
        if left:
            bad[name] = left[:8]
assert not bad, "ยังมีภาษาไทยนอก _(): " + json.dumps(bad, ensure_ascii=False, indent=1)[:4000]
print(f"หน้าที่แปลแล้ว {len(set(CONVERTED))} ไฟล์: ไม่มีไทยหลง OK")

# ---- ข้อความที่มีตัวเลขแทรก ----
assert I.tr("วันนี้ตรวจครบ 5 เว็บแล้ว พรุ่งนี้ตรวจต่อได้", "en") == "You've checked 5 sites today. You can check more tomorrow."
assert I.tr("ข้อความที่ไม่มีในคลัง", "ja") == "ข้อความที่ไม่มีในคลัง" and I.tr("ตรวจเลย", "th") == "ตรวจเลย"
print("tr: แปลแบบมีตัวเลขแทรก · ไม่มีคำแปลคงไทยเดิม OK")

# ---- ?lang= จำทั้งเว็บ · หน้าแรกตามภาษาที่เลือก ----
c = TestClient(M.app)
r = c.get("/audit?lang=ja")
assert r.status_code == 200 and '<html lang="ja">' in r.text and "無料サイト診断" in r.text and c.cookies.get(I.COOKIE) == "ja"
r = c.get("/audit"); assert '<html lang="ja">' in r.text                       # หน้าถัดไปยังเป็นญี่ปุ่น
r = c.get("/", follow_redirects=False); assert r.status_code in (302, 303, 307) and r.headers["location"] == "/ja"
r = c.get("/?lang=th"); assert r.status_code == 200 and '<html lang="th">' in r.text and c.cookies.get(I.COOKIE) == "th"
r = c.get("/audit"); assert '<html lang="th">' in r.text and "ตรวจเว็บฟรี" in r.text
c.get("/en"); assert c.cookies.get(I.COOKIE) == "en" and '<html lang="en">' in c.get("/audit").text
print("ภาษา: ?lang= จำลงคุกกี้ · หน้าถัดไปตามภาษา · / พาไปหน้าแรกภาษาที่เลือก · เลือกไทยคืนได้ OK")

# ---- หน้าที่แปลแล้วเปิดได้ทุกภาษา และไม่มีไทยในเนื้อหาที่เห็น ----
PUBLIC = ["/audit"]
for f in glob.glob("app/i18n/*.urls"):
    PUBLIC += [x.strip() for x in open(f, encoding="utf-8") if x.strip() and not x.startswith("#")]
for url in PUBLIC:
    for lg in OTHER:
        r = c.get(url + ("&" if "?" in url else "?") + "lang=" + lg)
        assert r.status_code == 200, (url, lg, r.status_code)
        vis = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>", " ", r.text)
        vis = re.sub(r"เจอ\.AI|เจอ|฿|ไทย", "", vis)
        assert not THAI.search(vis), (url, lg, re.findall(r"\S*[฀-๿]+\S*", vis)[:6])
print(f"หน้าสาธารณะ {len(PUBLIC)} หน้า × 4 ภาษา: เปิดได้ ไม่มีไทยหลง OK")
print("ALL OK")
