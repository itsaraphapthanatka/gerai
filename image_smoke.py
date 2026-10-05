"""ทดสอบตัวหารูปแบบไม่ยิงเครือข่าย — แนบรูปเป็น base64 (ย่อรูปใหญ่), เลขลำดับถูกแม้ข้ามบางรูป,
เหตุผลกลับมาทุกขั้น, ข้ามการสร้างรูปเมื่อ proxy ไม่มีโมเดล, ข้อความสถานะ"""
import io, base64
import app.image_finder as imf
import app.ai_client as ai
from PIL import Image

def png_bytes(w, h, color=(200, 30, 30)):
    buf = io.BytesIO(); Image.new("RGB", (w, h), color).save(buf, "PNG"); return buf.getvalue()

# ---- ชนิดไฟล์จาก magic bytes ----
assert imf._mime(png_bytes(2, 2)) == "image/png"
jpg = io.BytesIO(); Image.new("RGB", (2, 2)).save(jpg, "JPEG"); assert imf._mime(jpg.getvalue()) == "image/jpeg"
assert imf._mime(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp" and imf._mime(b"<html>") == ""
print("_mime: png/jpeg/webp/ไม่ใช่รูป OK")

# ---- inline: เล็กผ่านตรง ๆ · ใหญ่ถูกย่อเป็น JPEG · ไม่ใช่รูป/ดึงไม่ได้ บอกเหตุผล ----
small = png_bytes(40, 40)
durl, why = imf.inline_image("http://x/a.png", fetch=lambda u: small)
assert durl.startswith("data:image/png;base64,") and why == "ok"
big = png_bytes(3000, 2000)                       # ~ใหญ่กว่า SHRINK_OVER_BYTES แน่นอน? (PNG สีเดียวบีบดีมาก → บังคับผ่านเกณฑ์ด้วย gif แทน)
assert len(big) < imf.SHRINK_OVER_BYTES           # ยืนยันสมมติฐาน: PNG สีเดียวเล็ก จึงทดสอบการย่อผ่านทาง size เกณฑ์ด้วยรูป noise
import random
noise = Image.new("RGB", (2200, 1600)); noise.putdata([(random.randrange(256), random.randrange(256), random.randrange(256)) for _ in range(2200 * 1600)])
nb = io.BytesIO(); noise.save(nb, "PNG"); noise_png = nb.getvalue()
assert len(noise_png) > imf.SHRINK_OVER_BYTES
durl, why = imf.inline_image("http://x/big.png", fetch=lambda u: noise_png)
assert durl.startswith("data:image/jpeg;base64,") and why == "ok"
shr = Image.open(io.BytesIO(base64.b64decode(durl.split(",", 1)[1]))); assert max(shr.size) <= imf.INLINE_MAX_PX, shr.size
assert imf.inline_image("http://x/t.html", fetch=lambda u: b"<html>hi</html>") == (None, "ไฟล์ไม่ใช่รูป")
assert imf.inline_image("http://x/404", fetch=lambda u: None) == (None, "ดึงรูปไม่ได้")
print("inline_image: ส่ง base64 · ย่อรูปใหญ่เหลือ ≤%dpx JPEG · เหตุผลเมื่อใช้ไม่ได้ OK" % imf.INLINE_MAX_PX)

# ---- pick_relevant: รูปที่ 2 ดึงไม่ได้ → โมเดลเห็นแค่ 2 รูป และ "2|..." ต้องชี้ไปรูปที่ 3 ของจริง ----
cands = [{"url": "http://x/1.png", "alt": "หนึ่ง"}, {"url": "http://x/2.png", "alt": "สอง"}, {"url": "http://x/3.png", "alt": "สาม"}]
fetch = lambda u: None if u.endswith("2.png") else small
seen = {}
def fake_chat(messages, **kw):
    seen["parts"] = messages[0]["content"]; return seen.get("answer", "2|คลังสินค้าชั้นวางสูง")
ai._chat = fake_chat
pick, why = imf.pick_relevant(cands, "เช็คสต็อก", "Twinveetech", fetch=fetch)
imgs = [p for p in seen["parts"] if p["type"] == "image_url"]
assert len(imgs) == 2 and all(p["image_url"]["url"].startswith("data:image/png;base64,") for p in imgs)   # ไม่มี URL ภายนอกหลุดไป
assert "ลำดับ 1-2" in seen["parts"][0]["text"]
assert pick == {"url": "http://x/3.png", "alt": "คลังสินค้าชั้นวางสูง"} and why.startswith("เลือกรูปจากเว็บ"), (pick, why)
seen["answer"] = "0"; assert imf.pick_relevant(cands, "t", "b", fetch=fetch) == (None, "โมเดลบอกว่าไม่มีรูปบนเว็บที่เกี่ยวข้อง")
seen["answer"] = "9|x"; assert imf.pick_relevant(cands, "t", "b", fetch=fetch)[1] == "โมเดลตอบเลขรูปที่ไม่มี"
seen["answer"] = "รูปสวยดี"; assert imf.pick_relevant(cands, "t", "b", fetch=fetch)[1] == "อ่านคำตอบโมเดลไม่ออก"
def boom(messages, **kw): raise RuntimeError("HTTP Error 500")
ai._chat = boom
assert imf.pick_relevant(cands, "t", "b", fetch=fetch)[1].startswith("โมเดล vision ตอบไม่ได้ (RuntimeError")
assert imf.pick_relevant(cands, "t", "b", fetch=lambda u: None) == (None, "รูปบนเว็บใช้ไม่ได้ (ดึงรูปไม่ได้)")
assert imf.pick_relevant([], "t", "b") == (None, "เว็บแบรนด์ไม่มีรูปให้เลือก")
print("pick_relevant: ส่งเฉพาะ base64 · เลขลำดับชี้ถูกรูปแม้ข้ามบางรูป · เหตุผลครบทุกทาง OK")

# ---- capabilities/describe ----
imf.list_models = lambda timeout=8: ["qwen3.8", "gemma4-12b-fast"]
cap = imf.capabilities(fresh=True); assert cap["generate"] is False and "ไม่มีโมเดลรูป" in imf.describe(cap) and cap["image_model"] in imf.describe(cap)
imf.list_models = lambda timeout=8: ["qwen3.8", imf.image_model()]
assert imf.capabilities()["generate"] is False                      # ยังอยู่ใน cache 1 ชม.
cap = imf.capabilities(fresh=True); assert cap["generate"] is True and imf.describe(cap).startswith("✓")
def down(timeout=8): raise ConnectionError("refused")
imf.list_models = down
cap = imf.capabilities(fresh=True); assert cap["generate"] is None and "ตรวจโมเดลสร้างรูปไม่ได้" in imf.describe(cap) and "ConnectionError" in cap["error"]
print("capabilities: มี/ไม่มี/ตรวจไม่ได้ · cache · ข้อความสถานะ OK")

# ---- _generate_no_people หยุดทันทีเมื่อ proxy ไม่มีโมเดล · image_for_article ให้ note ครบ ----
calls = []
imf._scene_prompt = lambda brand, topic: "scene"
imf.generate_image = lambda p, size="768x512", negative_prompt=None: calls.append(p) or (None, "proxy ไม่มีโมเดลรูป “dreamshaper”")
png, why = imf._generate_no_people({"market": "x"}, "t"); assert png is None and len(calls) == 1 and "ไม่มีโมเดลรูป" in why
calls.clear()
imf.generate_image = lambda p, size="768x512", negative_prompt=None: calls.append(p) or (None, "proxy ตอบ HTTP 503 ตอนสร้างรูป")
assert imf._generate_no_people({"market": "x"}, "t") == (None, "proxy ตอบ HTTP 503 ตอนสร้างรูป") and len(calls) == 2   # 5xx ลองครบ 2 รอบ แต่เหตุผลไม่ซ้ำ

brand = {"name": "B", "market": "x"}
imf.scrape_site_images = lambda site, limit=8: cands
imf.pick_relevant = lambda c, t, n, fetch=None: (None, "โมเดล vision ตอบไม่ได้ (HTTPError: 500)")
imf.capabilities = lambda fresh=False, ttl=3600: {"image_model": "dreamshaper", "generate": False}
imf._generate_no_people = lambda b, t: (_ for _ in ()).throw(AssertionError("ต้องไม่ถูกเรียกเมื่อ proxy ไม่มีโมเดล"))
r = imf.image_for_article(brand, "t", "https://b.example")
assert r["mode"] == "none" and "vision ตอบไม่ได้" in r["note"] and "ข้ามการสร้างรูป" in r["note"], r
imf.capabilities = lambda fresh=False, ttl=3600: {"image_model": "sdxl", "generate": True}
imf._generate_no_people = lambda b, t: (b"PNGBYTES", "สร้างรูปใหม่")
r = imf.image_for_article(brand, "t", "https://b.example"); assert r["mode"] == "generated" and r["png"] == b"PNGBYTES" and r["note"] == "สร้างรูปใหม่"
imf.pick_relevant = lambda c, t, n, fetch=None: ({"url": "http://x/3.png", "alt": "สาม"}, "เลือกรูปจากเว็บ: สาม")
r = imf.image_for_article(brand, "t", "https://b.example"); assert r == {"mode": "scraped", "url": "http://x/3.png", "alt": "สาม", "note": "เลือกรูปจากเว็บ: สาม"}
imf.scrape_site_images = lambda site, limit=8: []
imf.capabilities = lambda fresh=False, ttl=3600: {"image_model": "dreamshaper", "generate": False}
r = imf.image_for_article(brand, "t", "https://b.example"); assert r["note"].startswith("เว็บแบรนด์ไม่มีรูปให้เลือก · ข้ามการสร้างรูป")
print("image_for_article: scraped/generated/none พร้อม note · ไม่เสียเวลาเรียกสร้างรูปเมื่อ proxy ไม่มีโมเดล OK")
print("\nimage_smoke: ผ่านทั้งหมด")
