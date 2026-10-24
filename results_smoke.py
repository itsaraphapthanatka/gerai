"""ทดสอบฝ่ายผลลัพธ์ลูกค้า: แนวโน้มผลวัด · ใครถูกอ้างแทน + ชนิดหน้า · เอารูปเสีย/โลโก้ออก · ลิงก์ตาย · งานทีมเว็บตรวจเอง → วัดผลซ้ำ
· ค้างเกิน 14 วันร่างข้อความตาม · ไม่ขยับ 4 สัปดาห์เตือนครั้งเดียว · ขยับขึ้นแจ้ง + CMO ได้เรื่องเคสศึกษาก่อน · หน้าแอดมิน · ไม่ยิงเครือข่าย"""
import os, tempfile, json, datetime as dt
os.environ["GEO_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="geo-results-"), "smoke.db")
os.environ["LITELLM_BASE_URL"] = ""
import bcrypt
from fastapi.testclient import TestClient
import app.db as db, app.results as RS, app.ai_client as ai

db.init_db()
tid = db.create_tenant("own@test.local", bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode(), "Own", is_admin=True)
bid = db.create_brand(tid, "Shop", "shop.co.th")
db.set_brand_site_context(bid, "Title: ร้านโกดัง\nContent: " + "ก" * 800)
ai.site_context = lambda url, **kw: (_ for _ in ()).throw(AssertionError("บริบทดีอยู่แล้ว ไม่ควรดึงซ้ำ"))

def vis(day, hits, asked=8):
    rows = [{"engine": "chatgpt", "question": f"q{i}", "status": "cited" if i < hits else "absent", "cited": i < hits, "named": False,
             "others": ["bigportal.co.th", "bigagency.co.th"], "citations": ["https://www.bigportal.co.th/en/warehouse-factory-for-rent/in-samut-prakan-th11?utm_source=openai",
                                                                     "https://www.bigagency.co.th/th/property/abc", "https://blog.x.com/blog/tips-1"]} for i in range(asked)]
    with db.get_conn() as c:
        c.execute(db.q("INSERT INTO ai_visibility(brand_id,checked_at,asked,cited,named,rate,report,cost_usd) VALUES(?,?,?,?,?,?,?,?)"),
                  (bid, day, asked, hits, 0, hits / asked, json.dumps({"rows": rows}), 0.4))
for d in ("2026-09-28", "2026-10-05", "2026-10-12", "2026-10-19"):
    vis(d + "T03:30:00", 0)
v = RS.visibility(bid)
assert [s["hits"] for s in v["series"]] == [0, 0, 0, 0] and v["stalled"] and not v["improved"], v
w = RS.who_wins(db.get_brand(bid))
assert w["domains"][0][0] == "bigportal.co.th" and dict(w["kinds"]) == {"listing": 8, "detail": 8, "article": 8} and w["top_kind"] == "listing", w
assert "utm_" not in w["examples"]["listing"][0]
assert RS.page_kind("https://www.smallwh.com/index.php?id=813&option=property_detail") == "detail"
assert RS.page_kind("https://www.bigportal.co.th/%E0%B9%80%E0%B8%8A%E0%B9%88%E0%B8%B2%E0%B9%82%E0%B8%81%E0%B8%94%E0%B8%B1%E0%B8%87/in-x") == "listing"
assert RS.page_kind("https://x.com/") == "home" and RS.page_kind("https://x.com/blog/a") == "article"
print("visibility/who_wins: แนวโน้ม 4 สัปดาห์ = ไม่ขยับ · โดเมน + ชนิดหน้าที่ถูกอ้าง · ตัด utm OK")

# ---- คอนเทนต์: รูปเสีย / โลโก้ / ลิงก์ตาย ----
def mk(title, body):
    cid = db.create_content_item(bid, None, "th", title, title, "d", body, "[]", "auto")
    db.mark_content_published(cid, None, f"https://shop.co.th/geo/{cid}")
    return cid
c1 = mk("a", "> **สรุป:** x\n\n![โกดัง](https://shop.co.th/img/dead.png)\n\n## h\nดู [ทรัพย์](https://shop.co.th/th/property/1) และ [อื่น](https://other.com/x)")
c2 = mk("b", "> **สรุป:** y\n\n![โลโก้กรมพัฒนาธุรกิจการค้า](https://www.dbd.go.th/a.png)\n\n## h")
c3 = mk("c", "> **สรุป:** z\n\n![โกดังจริง](https://shop.co.th/img/ok.png)\n\n## h")
codes = {"https://shop.co.th/img/dead.png": 404, "https://shop.co.th/img/ok.png": 200, "https://shop.co.th/th/property/1": 404}
asked = []
http = lambda u: asked.append(u) or codes.get(u, 200)
site = {"https://shop.co.th": '<a href="/geo">บทความ</a><a href="/th/warehouse-rent">โกดัง</a><a href="/logo.png">x</a>',
        "https://shop.co.th/th/warehouse-rent": '<a href="/geo/12">บทความที่เกี่ยวข้อง</a>'}
page = lambda u: site.get(u.rstrip("/"), "")
RS._page_html = page
h = RS.content_health(db.get_brand(bid), http=http)
assert h["links"] == {"hub": True, "articles_on_inner": True, "inner": "https://shop.co.th/th/warehouse-rent", "checked": 1}, h["links"]
site2 = {"https://shop.co.th": '<link rel="manifest" href="/m.webmanifest"><a href="/geo">บทความ</a><a href="/api/media/x/raw?w=1">i</a><a href="/feed/">f</a>'
         '<a href="/th/about">a</a><a href="/th/warehouse-rent">w</a>', "https://shop.co.th/th/about": "<p>เรา</p>",
         "https://shop.co.th/th/warehouse-rent": '<a href="/geo/3">x</a>', "https://shop.co.th/m.webmanifest": "{}", "https://shop.co.th/feed": "<rss/>"}
lk = RS.site_links_to_articles(db.get_brand(bid), page=lambda u: site2.get(u.rstrip("/"), ""))
assert lk["articles_on_inner"] is True and lk["inner"] == "https://shop.co.th/th/warehouse-rent" and lk["checked"] == 1, lk   # หน้าหมวดก่อนหน้าเกี่ยวกับเรา
assert sorted(h["img_fixed"]) == sorted([c1, c2]) and h["img_ok"] == 1 and h["published"] == 3, h
assert "dbd.go.th" not in "".join(asked) and "https://other.com/x" not in asked            # โลโก้ไม่ต้องยิงเช็ก · ลิงก์นอกโดเมนไม่ตรวจ
assert h["dead_links"] == [{"content_id": c1, "url": "https://shop.co.th/th/property/1"}]
assert "![" not in db.get_content(c1)["body_md"] and "![" not in db.get_content(c2)["body_md"] and "![" in db.get_content(c3)["body_md"]
assert db.get_content(c1)["body_md"].startswith("> **สรุป:**")
h2 = dict(h, index_total=27, indexed=10, discovered=11, unknown=6)
a = RS.actions(db.get_brand(bid), RS.visibility(bid), RS.who_wins(db.get_brand(bid)), h2, {"open": [], "done": []})
assert any("ลิงก์ไปบทความครบแล้ว" in x and "Search Console" in x for x in a) and not any("เพิ่มกล่องบทความ" in x for x in a), a
h3 = dict(h2, links={"hub": False, "articles_on_inner": False, "inner": None})
assert any("เพิ่มลิงก์ \"บทความ\" ในเมนู" in x for x in RS.actions(db.get_brand(bid), RS.visibility(bid), RS.who_wins(db.get_brand(bid)), h3, {"open": [], "done": []}))
print("content_health: รูป 404/โลโก้ราชการถูกเอาออก รูปดีอยู่ต่อ · ลิงก์ภายในตายถูกรายงาน · เช็กลิงก์บนเว็บจริงก่อนแนะนำ OK")

# ---- งานทีมเว็บ ----
now = dt.datetime(2026, 10, 24, 11, 15)
t1 = db.add_result_task(bid, "หน้าทำเล × ประเภท 22 เขต", check_kind="exists", check_url="https://shop.co.th/th/rent/warehouse/x", created_at="2026-10-01T09:00:00")
t2 = db.add_result_task(bid, "ลิงก์ไปบทความ", check_kind="contains", check_url="https://shop.co.th/th/property/2", check_text="/geo/", created_at="2026-10-20T09:00:00")
t3 = db.add_result_task(bid, "คุยเรื่อง schema", check_kind="manual")
pages = {"https://shop.co.th/th/rent/warehouse/x": (404, "nf"), "https://shop.co.th/th/property/2": (200, '<a href="/geo/5">บทความ</a>')}
fetch = lambda u: pages[u]
remeasured, alerts = [], []
rep = RS.review(db.get_brand(bid), now, http=http, fetch=fetch, remeasure=remeasured.append, alert=lambda m, l: alerts.append(m))
st = {t["id"]: t["status"] for t in db.list_result_tasks(bid)}
assert st == {t1: "open", t2: "done", t3: "open"} and remeasured == [bid], (st, remeasured)
assert rep["state"] == "blocked" and len(rep["tasks"]["nudges"]) == 1 and rep["tasks"]["nudges"][0]["id"] == t1
assert "23 วัน" in rep["tasks"]["nudges"][0]["nudge"] or "ส่งไปเมื่อ 2026-10-01" in rep["tasks"]["nudges"][0]["nudge"]
assert "ยังขึ้น 404" in db.get_result_task(t1)["last_check_note"]
assert any("ค้าง 23 วัน" in a for a in rep["actions"]) and any("แล้ว" in a and "ลิงก์ไปบทความ" in a for a in rep["actions"])
assert any("ไม่แนะนำเลย 4 สัปดาห์" in a for a in alerts) and any("ร่างข้อความตามทีมเว็บ" in a for a in alerts), alerts
n_alert = len(alerts)
# ทีมเว็บเพิ่งทำเสร็จ → รอผล ไม่เตือนว่าไม่ขยับ
b2 = db.create_brand(tid, "Two", "two.co.th")
with db.get_conn() as c:
    for d in ("2026-09-28", "2026-10-05", "2026-10-12", "2026-10-19"):
        c.execute(db.q("INSERT INTO ai_visibility(brand_id,checked_at,asked,cited,named,rate,report,cost_usd) VALUES(?,?,?,?,?,?,?,?)"),
                  (b2, d + "T03:30:00", 8, 0, 0, 0, json.dumps({"rows": [{"status": "absent", "cited": False, "named": False}] * 8}), 0.4))
db.add_result_task(b2, "หน้าใหม่", check_kind="exists", check_url="https://two.co.th/new")
al2 = []
r2 = RS.review(db.get_brand(b2), now, http=http, fetch=lambda u: (200, "ok"), remeasure=lambda b: None, alert=lambda m, l: al2.append(m))
assert r2["state"] == "waiting" and not any("ไม่แนะนำเลย" in a for a in al2), (r2["state"], al2)
rep2 = RS.review(db.get_brand(bid), now + dt.timedelta(days=7), http=http, fetch=fetch, remeasure=remeasured.append, alert=lambda m, l: alerts.append(m))
assert not any("ไม่แนะนำเลย" in a for a in alerts[n_alert:]) and not rep2["tasks"]["nudges"]     # เตือนซ้ำไม่ถี่ · ตามงานทุก 14 วัน
print("tasks: ตรวจเอง → เสร็จแล้ววัดผลซ้ำ · ค้าง 14 วันร่างข้อความตาม (ไม่ส่งเอง) · manual รอเจ้าของ · ไม่ขยับเตือน 28 วันครั้ง OK")

# ---- ขยับขึ้น → แจ้ง + เคสศึกษา ----
vis("2026-10-26T03:30:00", 2)
rep3 = RS.review(db.get_brand(bid), dt.datetime(2026, 10, 26, 11, 15), http=http, fetch=fetch, remeasure=remeasured.append, alert=lambda m, l: alerts.append(m))
assert rep3["state"] == "win" and any("ถูก AI แนะนำแล้ว 2 จาก 8" in a for a in alerts)
import app.marketing as MK
assert db.last_result_review(bid)["state"] == "win"
print("win: ขยับขึ้นแจ้งเจ้าของ · CMO ให้น้ำหนักเคสศึกษา OK")

# ---- run / due / summary / status ----
assert not RS.due(db.get_brand(bid), dt.datetime(2026, 10, 27, 11, 15))                          # เพิ่งรีวิว ไม่มีผลวัดใหม่
assert RS.due(db.get_brand(bid), dt.datetime(2026, 11, 2, 11, 15))
out = RS.run(now=dt.datetime(2026, 10, 27, 11, 15), http=http, fetch=fetch, remeasure=remeasured.append, alert=lambda m, l: None)
assert out["reviewed"] == 0
s = RS.status_text(); assert "ผลลัพธ์ลูกค้า: 2 แบรนด์" in s and "รอผลหลังทีมเว็บทำงานเสร็จ 1" in s and "ขยับขึ้น 1" in s and "งานทีมเว็บค้าง 2" in s, s
print("run/status: ไม่รีวิวซ้ำถ้าไม่มีผลวัดใหม่ · สรุปเช้า OK")

# ---- หน้าแอดมิน ----
from app.main import app
cl = TestClient(app)
cl.post("/login", data={"email": "own@test.local", "password": "pw"})
r = cl.get("/admin/results"); assert r.status_code == 200 and "Shop" in r.text and "ขยับขึ้น" in r.text and "หน้าทำเล × ประเภท 22 เขต" in r.text, r.text[:400]
r = cl.post("/admin/results/task", data={"bid": str(bid), "title": "เพิ่ม FAQ schema", "check_kind": "contains", "check_url": "shop.co.th/th/faq", "check_text": "FAQPage"}, follow_redirects=False)
assert r.status_code in (302, 303)
nt = [t for t in db.list_result_tasks(bid) if t["title"] == "เพิ่ม FAQ schema"][0]
assert nt["check_url"] == "https://shop.co.th/th/faq" and nt["check_kind"] == "contains"
cl.post(f"/admin/results/task/{nt['id']}/delete", follow_redirects=False); assert not db.get_result_task(nt["id"])
cl.post(f"/admin/results/task/{t3}/done", follow_redirects=False); assert db.get_result_task(t3)["status"] == "done"
print("admin: หน้า /admin/results · เพิ่ม/ลบ/เสร็จงาน OK")
print("RESULTS SMOKE OK")
