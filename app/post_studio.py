"""สตูดิโอโพสต์ของ CMO — โพสต์ที่คนหยุดอ่าน: เริ่มด้วยตัวเลขจริงหรือคำถามที่เจ็บ มีการ์ดรูปทุกโพสต์ จบด้วยคำถามชวนคอมเมนต์ + ลิงก์ตรวจฟรี
ประเภท:
  insight  สถิติจากเว็บธุรกิจไทยที่เราตรวจจริง (ไม่ระบุชื่อ) — "ตรวจ 28 เว็บ: 22 เว็บ (79%) ไม่มี llms.txt"
  ai_test  ลองถาม ChatGPT จริงด้วยคำถามที่ลูกค้าถาม แล้วดูว่า AI อ้างอิงเว็บของใคร (ไม่ระบุชื่อลูกค้า)
  tip      เคล็ดลับเช็กเองได้ใน 5 นาที (ชุดที่เขียนไว้ หมุนเวียน)
  article  บทความของเราเอง — เขียนโพสต์ด้วยโมเดลเท่านั้น ถ้าโมเดลล้มไม่โพสต์ (กันข้อความแม่แบบเพี้ยน)
ทุกตัวเลขในโพสต์ต้องมาจากข้อมูลจริง (ตรวจในโค้ด) · ห้ามการันตี · การ์ดรูปเรนเดอร์ผ่าน Gotenberg (Chromium) → /static/gen/posts/"""
from __future__ import annotations
import datetime as dt
import html as _html
import json
import os
import re
from collections import Counter
from pathlib import Path

from . import db, execs, leads as L

GEN_DIR = Path(__file__).parent / "static" / "gen" / "posts"
PDF_URL = os.getenv("PDF_SERVICE_URL", "http://pdf:3000")
RENDER = None                 # เทสต์ฉีด render(html) → bytes
MIN_SITES = 10                # สถิติต้องมีเว็บที่ตรวจอย่างน้อยเท่านี้ ไม่งั้นไม่โพสต์
KINDS = {"insight": "สถิติจากการตรวจจริง", "ai_test": "ลองถาม AI จริง", "tip": "เคล็ดลับเช็กเอง"}
# ข้อที่ความหมายตรงกับเว็บทั่วไป (ไม่ใช่ข้อที่วัดตัวเชื่อมของเราเอง) → ใช้ทำสถิติสาธารณะได้
PUBLIC_STAT = {
    "llms": "ไม่มีไฟล์ llms.txt ที่ช่วยให้ AI อ่านสรุปเว็บ",
    "www": "เว็บแบบมี www กับไม่มี www ไม่ชี้ที่เดียวกัน คะแนนจาก Google แยกเป็นสองเว็บ",
    "csr": "เนื้อหาโหลดด้วย JavaScript ล้วน บอท AI อ่านไม่เห็น",
    "ai_bots": "robots.txt กันบอทของ ChatGPT/Claude/Perplexity ไว้",
    "robots_sitemap": "robots.txt ไม่บอกที่อยู่ sitemap",
    "noindex": "ตั้ง noindex ไว้ Google ไม่เก็บหน้าเว็บ",
    "canonical": "canonical ชี้ผิด Google มองว่าเป็นหน้าซ้ำ",
}
TIPS = [
    {"hook": "เช็ก 10 วินาที: ChatGPT เข้าเว็บคุณได้ไหม", "steps": ["เปิด เว็บคุณ/robots.txt", "หาบรรทัด GPTBot หรือ User-agent: * ตามด้วย Disallow: /", "ถ้าเจอ แปลว่า AI ถูกกันไว้ ลบบรรทัดนั้นหรือเปลี่ยนเป็น Allow"],
     "q": "ลองเช็กแล้วเจออะไร บอกได้ในคอมเมนต์"},
    {"hook": "llms.txt คืออะไร ทำไมเว็บธุรกิจควรมี", "steps": ["ไฟล์ข้อความสั้น ๆ ที่สรุปว่าเว็บคุณขายอะไร หน้าไหนสำคัญ", "AI ใช้อ่านแทนการไล่อ่านทั้งเว็บ", "วางไว้ที่ เว็บคุณ/llms.txt ทำครั้งเดียว"],
     "q": "เว็บคุณมี llms.txt แล้วหรือยัง"},
    {"hook": "เปิดเว็บแบบปิด JavaScript — เห็นเนื้อหาไหม", "steps": ["Chrome: กด Ctrl+U ดู source ของหน้า", "ค้นหาประโยคจากหน้าเว็บ ถ้าไม่เจอ แปลว่าเนื้อหาโหลดด้วย JavaScript", "บอท AI ส่วนใหญ่ไม่รัน JavaScript จึงไม่เห็นอะไรเลย"],
     "q": "เว็บคุณทำด้วยอะไร WordPress, Wix หรือเขียนเอง"},
    {"hook": "คนถาม AI ว่า \"ร้านไหนดี\" — AI หยิบคำตอบจากไหน", "steps": ["หน้าที่ตอบคำถามตรง ๆ ในย่อหน้าแรก", "มีตัวเลข ราคา พื้นที่ให้บริการชัดเจน", "มีคำถามที่พบบ่อย (FAQ) อยู่ในหน้า"],
     "q": "ลองถาม ChatGPT เรื่องธุรกิจคุณแล้ว เจอชื่อตัวเองไหม"},
    {"hook": "www กับไม่มี www ต้องไปที่เดียวกัน", "steps": ["พิมพ์เว็บคุณทั้งแบบมีและไม่มี www", "ทั้งสองแบบต้องเด้งไปที่อยู่เดียว (301)", "ไม่งั้น Google นับเป็นสองเว็บ คะแนนแตก"],
     "q": "ลองแล้วได้ผลแบบไหน"},
    {"hook": "3 ประโยคแรกของหน้าบริการ สำคัญกว่าที่คิด", "steps": ["AI และ Google อ่านย่อหน้าแรกก่อน", "บอกให้จบ: ทำอะไร ให้ใคร ที่ไหน ราคาเริ่มเท่าไร", "อย่าเปิดด้วยคำทักทายหรือประวัติบริษัท"],
     "q": "ย่อหน้าแรกของเว็บคุณตอนนี้เขียนว่าอะไร"},
]


# ---------- ข้อมูลจริง ----------
def dataset(days: int = 60) -> dict:
    """สถิติจากเว็บที่ตรวจ (ไม่นับลีดทดสอบที่ปิดเป็น lost + notes ตัวอย่าง)"""
    since = (dt.datetime.now() - dt.timedelta(days=days)).isoformat(timespec="seconds")
    rows = [l for l in db.list_leads(None, 5000) if l["audit_json"] and (l["audit_at"] or "") >= since
            and not (l["notes"] or "").startswith("ตัวอย่างรายงาน")]
    c, n = Counter(), 0
    for l in rows:
        try:
            rep = json.loads(l["audit_json"])
        except Exception:
            continue
        keys = {ch.get("key") for ch in rep.get("checks", []) if ch.get("status") == "fail"}
        if "home" in keys:
            continue
        n += 1
        for k in keys & set(PUBLIC_STAT):
            c[k] += 1
    stats = [{"key": k, "n": v, "pct": round(v * 100 / n), "text": PUBLIC_STAT[k]} for k, v in c.most_common() if n]
    return {"n": n, "stats": stats}


def ai_sample() -> dict | None:
    """ผลถาม ChatGPT จริงล่าสุดหนึ่งชุด (จากรายงานเต็มของลีด) — ใช้แค่คำถามกับโดเมนที่ AI อ้าง ไม่ใช้ชื่อลีด"""
    best = None
    for l in db.list_leads(None, 5000):
        try:
            d = json.loads(l["deep_ai_json"] or "{}") if l["deep_ai_json"] else {}
        except Exception:
            continue
        if d.get("state") == "done" and d.get("asked") and d.get("top_sources"):
            if not best or (d.get("at") or "") > (best[1].get("at") or ""):
                best = (l, d)
    if not best:
        return None
    l, d = best
    own = L.domain_of(l["site_url"])
    sources = [s for s in d["top_sources"] if s["domain"] != own][:3]
    prompt = next((r["prompt"] for r in d["rows"] if r.get("sources")), d["rows"][0]["prompt"])
    return {"lead_id": l["id"], "asked": d["asked"], "mentioned": d["mentioned"], "prompt": prompt[:120], "sources": sources}


# ---------- ข้อเท็จจริงต่อประเภท ----------
def facts_for(kind: str, idx: int = 0) -> dict | None:
    if kind == "insight":
        ds = dataset()
        if ds["n"] < MIN_SITES or not ds["stats"]:
            return None
        top = ds["stats"][:3]
        return {"kind": kind, "n": ds["n"], "card_title": f"เราตรวจเว็บธุรกิจไทย {ds['n']} เว็บ", "lines": [f"{s['n']} จาก {ds['n']} เว็บ ({s['pct']}%) {s['text']}" for s in top],
                "big": f"{top[0]['pct']}%", "big_label": top[0]["text"], "allowed": _nums([ds["n"]] + [s["n"] for s in top] + [s["pct"] for s in top])}
    if kind == "ai_test":
        s = ai_sample()
        if not s:
            return None
        src = ", ".join(x["domain"] for x in s["sources"])
        q = s["prompt"]
        q = q.split(" ")[0] if 12 <= len(q.split(" ")[0]) <= 70 else (q[:60].rstrip() + "…" if len(q) > 60 else q)
        return {"kind": kind, "prompt": q, "card_title": f"เราถาม ChatGPT ว่า “{q}”", "lines": [f"ถาม ChatGPT {s['asked']} คำถามที่ลูกค้าถามจริง", f"AI อ้างอิง {src} บ่อยที่สุด",
                f"เว็บธุรกิจที่เราตรวจถูกเอ่ยถึง {s['mentioned']} จาก {s['asked']} ครั้ง"], "big": f"{s['mentioned']}/{s['asked']}",
                "big_label": "ครั้งที่ AI เอ่ยถึงเว็บธุรกิจไทยที่เราตรวจ", "content_id": 2_000_000 + s["lead_id"],
                "allowed": _nums([s["asked"], s["mentioned"]]) | _nums_in(src) | _nums_in(s["prompt"])}
    if kind == "tip":
        t = TIPS[idx % len(TIPS)]
        return {"kind": kind, "lines": t["steps"], "hook": t["hook"], "q": t["q"], "big": "", "big_label": "", "content_id": 1000 + idx % len(TIPS),
                "allowed": _nums_in(" ".join([t["hook"]] + t["steps"] + [t["q"]]))}
    return None


def _nums(xs) -> set:
    return {str(x) for x in xs}


def _nums_in(s: str) -> set:
    return set(re.findall(r"\d+", s or ""))


# ---------- เขียน ----------
def template(f: dict) -> dict:
    if f["kind"] == "insight":
        return {"hook": f"ตรวจเว็บธุรกิจไทย {f['n']} เว็บ — {f['big']} {f['big_label']}"[:90], "bullets": f["lines"],
                "question": "เว็บของคุณอยู่ในกลุ่มไหน ลองตรวจดูแล้วมาเล่าให้ฟัง", "hashtags": ["#SEO", "#AI", "#ธุรกิจออนไลน์"]}
    if f["kind"] == "ai_test":
        return {"hook": f"เราถาม ChatGPT ว่า \"{f['prompt'][:60]}\" — AI ไปหยิบคำตอบจากใคร", "bullets": f["lines"],
                "question": "ถ้าลูกค้าถาม AI เรื่องธุรกิจคุณ คิดว่าจะเจอชื่อคุณไหม", "hashtags": ["#ChatGPT", "#GEO", "#AEO"]}
    if f["kind"] == "article":
        return {"hook": f["lines"][0][:90], "bullets": [], "question": "คุณเคยลองแบบนี้หรือยัง", "hashtags": ["#SEO", "#AI"]}
    return {"hook": f["hook"], "bullets": f["lines"], "question": f["q"], "hashtags": ["#SEO", "#AI", "#ทำเว็บ"]}


def write(f: dict, llm=None) -> dict:
    """โมเดลเขียนตามทักษะ CMO เป็นโครง {hook, bullets, question, hashtags} · ตัวเลขนอกข้อมูลจริง/คำต้องห้าม/ยาวเกิน → แม่แบบ"""
    base = template(f)
    if llm is None:
        from . import ai_client
        llm = lambda m: ai_client.chat_for("marketing", m, max_tokens=2500, json_mode=True)   # Gemini คิดก่อนตอบ ใช้ token ไปส่วนหนึ่ง
    prompt = ("เขียนโพสต์โซเชียลภาษาไทยของ เจอ.AI (ช่วยให้เว็บธุรกิจไทยโผล่ใน Google และคำตอบ AI) ให้คนเลื่อนผ่านแล้วต้องหยุดอ่าน\n"
              f"ประเภท: {KINDS.get(f['kind'], f['kind'])}\nข้อมูลจริงที่ใช้ได้ (ห้ามเพิ่มตัวเลขอื่น):\n- " + "\n- ".join(f["lines"])
              + (f"\nคำถามที่ใช้ทดสอบ: {f['prompt']}" if f.get("prompt") else "")
              + "\nกติกา: hook บรรทัดเดียวไม่เกิน 70 ตัวอักษร เริ่มด้วยตัวเลขหรือคำถามที่เจ้าของธุรกิจรู้สึก · bullets 3 ข้อ ข้อละไม่เกิน 70 ตัวอักษร ภาษาคน ไม่ใช้ศัพท์เทคนิคถ้าไม่จำเป็น"
              " · question ชวนคอมเมนต์ 1 ประโยค · hashtags 2–3 อัน · ห้ามคำว่า การันตี/รับประกัน/อันดับ 1 · ห้ามขายตรง\n"
              'ตอบ JSON เท่านั้น: {"hook": "...", "bullets": ["...","...","..."], "question": "...", "hashtags": ["#..."]}')
    try:
        raw = llm([{"role": "user", "content": prompt}])
        m = re.search(r"\{[\s\S]*\}", raw or "")
        d = json.loads(m.group(0)) if m else {}
        hook = str(d.get("hook") or "").strip()
        bullets = [str(b).strip(" •-") for b in (d.get("bullets") or []) if str(b).strip()][:3]
        q = str(d.get("question") or "").strip()
        tags = [t if t.startswith("#") else "#" + t for t in (str(x).strip() for x in (d.get("hashtags") or [])) if t][:3]
        allt = " ".join([hook, q] + bullets)
        if not hook or len(hook) > 90 or len(bullets) < 2 or any(len(b) > 110 for b in bullets):
            raise ValueError("โครงไม่ครบ/ยาวเกิน")
        if execs.FORBIDDEN.search(allt):
            raise ValueError("คำต้องห้าม")
        extra = _nums_in(allt) - f["allowed"] - {"1", "2", "3", "5", "10"}
        if extra:
            raise ValueError(f"ตัวเลขนอกข้อมูลจริง {extra}")
        return {"hook": hook, "bullets": bullets, "question": q or base["question"], "hashtags": tags or base["hashtags"], "source": "llm"}
    except Exception as e:
        return {**base, "source": f"template ({type(e).__name__})"}


def assemble(copy: dict, cta_link: str, extra_link: str | None = None) -> str:
    lines = [copy["hook"], ""] + [f"• {b}" for b in copy["bullets"]] + ["", copy["question"], ""]
    if extra_link:
        lines.append(f"อ่านต่อ: {extra_link}")
    lines.append(f"👉 ตรวจเว็บของคุณฟรี ได้ผลใน 30 วินาที: {cta_link}")
    lines += ["", " ".join(copy["hashtags"])]
    return "\n".join(lines).strip()


# ---------- การ์ดรูป ----------
def card_html(copy: dict, f: dict) -> str:
    e = _html.escape
    big = f.get("big") or ""
    title = f.get("card_title") if big else copy["hook"]            # มีตัวเลขใหญ่ = หัวสั้น ๆ บอกที่มา ไม่ซ้ำกับตัวเลข
    items = (f["lines"][1:] if f["kind"] == "insight" else f["lines"]) if big else copy["bullets"]
    bullets = "".join(f"<li>{e(b)}</li>" for b in items[:3])
    big_html = (f"<div class='big'>{e(big)}</div><div class='bl'>{e(f.get('big_label') or '')}</div>") if big else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:1080px;height:1080px;font-family:'Sarabun',sans-serif;background:linear-gradient(155deg,#1b1340 0%,#3b1d8f 55%,#7132f5 100%);color:#fff;
  padding:84px 80px;display:flex;flex-direction:column}}
.tag{{display:inline-block;align-self:flex-start;background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.28);border-radius:999px;padding:10px 26px;font-size:30px;font-weight:600}}
h1{{font-size:{62 if len(title or '') < 46 else 50}px;line-height:1.28;font-weight:800;margin-top:44px}}
.big{{font-size:190px;font-weight:800;line-height:1;margin-top:34px;color:#ffd54a}}
.bl{{font-size:34px;line-height:1.35;color:#e9e2ff;margin-top:8px;max-width:880px}}
ul{{list-style:none;margin-top:38px}}
li{{font-size:{36 if big else 40}px;line-height:1.4;padding-left:48px;position:relative;margin-bottom:{16 if big else 24}px;color:#f3efff}}
li:before{{content:"";position:absolute;left:0;top:18px;width:22px;height:22px;border-radius:6px;background:#ffd54a}}
.foot{{margin-top:auto;display:flex;justify-content:space-between;align-items:flex-end;font-size:32px}}
.foot b{{font-size:46px;font-weight:800}}
.foot span{{color:#e9e2ff}}
</style></head><body>
<div class="tag">{e(KINDS.get(f['kind'], 'เจอ.AI'))}</div>
<h1>{e(title or '')}</h1>{big_html}<ul>{bullets}</ul>
<div class="foot"><b>เจอ.AI</b><span>ตรวจเว็บฟรี · geo.appreview.cloud/audit</span></div>
</body></html>"""


def render_png(html: str) -> bytes:
    if RENDER:
        return RENDER(html)
    import httpx
    r = httpx.post(f"{PDF_URL.rstrip('/')}/forms/chromium/screenshot/html",
                   files={"files": ("index.html", html.encode("utf-8"), "text/html")},
                   data={"width": "1080", "height": "1080", "format": "png", "clip": "true"}, timeout=60)
    r.raise_for_status()
    return r.content


def save_card(name: str, png: bytes, base_url: str) -> str:
    GEN_DIR.mkdir(parents=True, exist_ok=True)
    p = GEN_DIR / f"{name}.png"
    p.write_bytes(png)
    return f"{base_url.rstrip('/')}/static/gen/posts/{p.name}"


def make(kind: str, channel: str, cta_link: str, base_url: str, idx: int = 0, llm=None, article=None) -> dict | None:
    """ทั้งโพสต์: ข้อเท็จจริง → ข้อความ → การ์ดรูป · None = ไม่มีข้อมูลพอ (ไม่โพสต์ดีกว่าโพสต์ของแย่)"""
    if kind == "article":
        if article is None:
            return None
        title = re.sub(r"\s*[|·–-]\s*เจอ\.?AI\s*$", "", article["title"] or "").strip()
        body = re.sub(r"\s+", " ", re.sub(r"[#>*_`|\[\]()!]", " ", article["body_md"] or ""))[:600]
        f = {"kind": "article", "lines": [title, body], "allowed": _nums_in(title + " " + body), "big": "", "big_label": "", "content_id": article["id"]}
        copy = write(f, llm)
        if not copy["source"].startswith("llm"):
            return None                                   # บทความไม่มีแม่แบบ — โมเดลล้มก็ข้าม
        f["kind_label"] = "บทความใหม่"
    else:
        f = facts_for(kind, idx)
        if not f:
            return None
        copy = write(f, llm)
    text = assemble(copy, cta_link, (f"{base_url.rstrip('/')}/geo/{article['id']}" if article is not None else None))
    image = None
    try:
        stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
        image = save_card(f"{kind}-{channel}-{stamp}", render_png(card_html(copy, {**f, "kind": kind if kind != "article" else "tip"})), base_url)
    except Exception:
        image = None                                      # รูปล้ม → โพสต์ข้อความ+ลิงก์ตามเดิม
    return {"title": copy["hook"][:120], "text": text, "link": cta_link, "image": image, "content_id": f.get("content_id"), "source": copy["source"]}
