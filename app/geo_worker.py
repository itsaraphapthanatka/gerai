"""GEO monitoring engine.

สำหรับแต่ละคำถามเป้าหมาย: ค้นเว็บ (ddgs) → ดูว่าโดเมนแบรนด์โผล่ไหม + ตำแหน่ง + คู่แข่ง
แล้วคำนวณ Share of Voice = (จำนวนคำถามที่แบรนด์โผล่) / (จำนวนคำถามทั้งหมด)

MVP ใช้ heuristic (จับคู่โดเมน) — ไม่ต้องมี LLM ก็รันได้
(เติม LLM summary ผ่าน LiteLLM ได้ภายหลัง ดู llm_summary())
"""
from __future__ import annotations
import os
import re
import json
import datetime
from urllib.parse import urlparse

from . import db

SEARCH_LIMIT = int(os.getenv("GEO_SEARCH_LIMIT", "8"))


def domain_of(url: str) -> str:
    try:
        net = urlparse(url).netloc.lower()
        return net[4:] if net.startswith("www.") else net
    except Exception:
        return ""


def _row(title, url, i: int) -> dict:
    u = str(url or "")
    return {"title": str(title or ""), "url": u, "domain": domain_of(u), "position": i + 1}


def _ddgs_search(query: str, limit: int) -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError as e:
        raise RuntimeError("ddgs ยังไม่ได้ติดตั้ง — pip install ddgs") from e
    out: list[dict] = []
    with DDGS() as ddg:
        for i, hit in enumerate(ddg.text(query, max_results=limit)):
            if i >= limit:
                break
            out.append(_row(hit.get("title"), hit.get("href") or hit.get("url"), i))
    return out


def _brave_search(query: str, limit: int) -> list[dict]:
    import httpx

    r = httpx.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={"X-Subscription-Token": _cfg("brave_key", "BRAVE_API_KEY"), "Accept": "application/json"},
        params={"q": query, "count": limit, "country": "th", "search_lang": "th"},
        timeout=20,
    )
    r.raise_for_status()
    results = ((r.json().get("web") or {}).get("results")) or []
    return [_row(x.get("title"), x.get("url"), i) for i, x in enumerate(results[:limit])]


def _serper_search(query: str, limit: int, page: int = 1) -> list[dict]:
    import httpx

    body = {"q": query, "gl": "th", "hl": "th", "num": limit}
    if page > 1:
        body["page"] = page
    r = httpx.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": _cfg("serper_key", "SERPER_API_KEY"), "Content-Type": "application/json"},
        json=body,
        timeout=20,
    )
    r.raise_for_status()
    results = r.json().get("organic") or []
    return [_row(x.get("title"), x.get("link"), i) for i, x in enumerate(results[:limit])]


def _cfg(db_key: str, env_key: str) -> str:
    """อ่านค่า config: DB settings ก่อน (ตั้งผ่านหน้า admin) ถ้าไม่มี fallback ไป .env"""
    try:
        v = db.get_setting(db_key)
        if v:
            return v
    except Exception:
        pass
    return os.getenv(env_key, "")


def active_backend() -> str:
    """backend ที่ใช้จริง — เลือก brave/serper เฉพาะเมื่อมี API key ไม่งั้น ddgs (ฟรี)."""
    b = (_cfg("search_backend", "GEO_SEARCH_BACKEND") or "ddgs").lower()
    if b == "brave" and _cfg("brave_key", "BRAVE_API_KEY"):
        return "brave"
    if b == "serper" and _cfg("serper_key", "SERPER_API_KEY"):
        return "serper"
    return "ddgs"


def search(query: str, limit: int = SEARCH_LIMIT) -> list[dict]:
    """ค้นเว็บคืน list ของ {title,url,domain,position} ตาม backend ที่ตั้งไว้.

    GEO_SEARCH_BACKEND = ddgs (ฟรี, ดีฟอลต์) | brave | serper
    ถ้า backend จ่ายเงินล่ม/คีย์ผิด → fallback กลับไป ddgs อัตโนมัติ
    """
    backend = active_backend()
    try:
        if backend == "brave":
            return _brave_search(query, limit)
        if backend == "serper":
            return _serper_search(query, limit)
    except Exception:
        pass  # backend จริงล่ม → ใช้ ddgs แทน
    return _ddgs_search(query, limit)


def analyze(brand_domain: str, results: list[dict], brand_name: str = ""):
    """คืน (present, position, competitor_domains).
    นับว่า 'โผล่' ถ้า: โดเมนแบรนด์ติดผล หรือ ชื่อแบรนด์ปรากฏใน title ของผลใดผลหนึ่ง
    (ครอบคลุม mention บนไดเรกทอรี/บทความ ซึ่งเป็น share of voice จริง)."""
    # core ของชื่อแบรนด์ (ตัดคำต่อท้ายทั่วไป) → จับ "JKP PROPERTY CO.,LTD" ได้จาก "JKP Property Agency"
    _STOP = {"co", "co.", "ltd", "ltd.", "inc", "inc.", "group", "agency", "company",
             "จำกัด", "บริษัท", "the", "and", "&", "(ตัวอย่าง)"}
    _toks = [t for t in " ".join((brand_name or "").lower().split()).split() if t not in _STOP]
    core = " ".join(_toks[:2]) if _toks else ""
    name_key = core if len(core) >= 5 else ""   # ชื่อสั้น/กว้างเกิน ไม่ใช้ (กัน false positive)
    present, position = False, None
    competitors = []
    for r in results:
        dom = (r.get("domain") or "").lower()
        title = (r.get("title") or "").lower()
        own = domain_matches(brand_domain, dom)   # ตรงตัว/ซับโดเมนเท่านั้น ไม่ใช่ substring
        named = bool(name_key and name_key in title)
        if own or named:
            if not present:
                present, position = True, r.get("position")
        elif dom:
            competitors.append(dom)
    return present, position, competitors


# ---- Google rank tracking ----
# SoV ถามว่า "แบรนด์ถูกพูดถึงไหม" (นับ mention บนเว็บใครก็ได้)
# rank ถามว่า "เว็บเราอยู่อันดับเท่าไหร่" → นับเฉพาะโดเมนตัวเอง และต้องมองลึกกว่า top 8
# Serper เมิน num ทั้งดุ้น — ส่ง 10/20/100 ก็คืนหน้าแรก ~8-10 ผลเท่ากัน
# ต้องไล่ด้วย page แทน และ position ของแต่ละหน้าเริ่มนับ 1 ใหม่ จึงต้อง offset เอง
RANK_PAGES = int(os.getenv("GEO_RANK_PAGES", "2"))
_PER_PAGE = 10
RANK_LIMIT = RANK_PAGES * _PER_PAGE   # ความลึกสูงสุดที่วัดได้ (ใช้เป็น label ใน UI)


def rank_backend() -> str:
    """อันดับต้องมาจาก Google จริง = serper. ไม่มีคีย์ก็ยังเช็คได้แต่เป็นของ engine อื่น (บอกตามตรงใน UI)"""
    return "serper" if _cfg("serper_key", "SERPER_API_KEY") else active_backend()


def norm_domain(value: str) -> str:
    """โดเมนเปล่าสำหรับเทียบ — รับได้ทั้ง 'https://x.com/path', 'www.x.com', 'x.com:8080'
    (brands.domain ของลูกค้ากรอกมาหลายรูปแบบ ส่วน domain ในผลค้นผ่าน domain_of() มาแล้ว)"""
    d = (value or "").strip().lower()
    d = re.sub(r"^[a-z][a-z0-9+.\-]*://", "", d)       # ตัด scheme
    d = d.split("/")[0].split("?")[0].split("#")[0]     # ตัด path/query/fragment
    d = d.split("@")[-1].split(":")[0]                  # ตัด userinfo / port
    if d.startswith("www."):
        d = d[4:]
    return d.strip(".")


def domain_matches(brand_domain: str, result_domain: str) -> bool:
    """ตรงตัว หรือเป็นซับโดเมนของแบรนด์ (blog.x.com นับเป็นของ x.com).

    เทียบแบบ substring ไม่ได้ — 'go.asia' จะ match 'petgo.asia' และ
    'x.com' จะ match 'x.com.evil.net' ทำให้รายงานว่าติดอันดับทั้งที่เป็นเว็บคนอื่น
    """
    bd, rd = norm_domain(brand_domain), norm_domain(result_domain)
    if not bd or not rd:
        return False
    return rd == bd or rd.endswith("." + bd)


def find_position(brand_domain: str, results: list[dict]):
    """คืน (position, url) ของผลแรกที่เป็นโดเมนแบรนด์ — ไม่เจอคืน (None, None)"""
    for r in results:
        if domain_matches(brand_domain, r.get("domain") or ""):
            return r.get("position"), r.get("url")
    return None, None


def rank_lookup(query: str, brand_domain: str):
    """หาอันดับของ brand_domain โดยไล่ทีละหน้า — เจอแล้วหยุดทันที (ประหยัด credit:
    ติดหน้าแรก = 1 credit, ต้องดูหน้า 2 = 2 credits). คืน (position, url)."""
    if rank_backend() != "serper":
        return find_position(brand_domain, search(query, RANK_LIMIT))
    seen = 0
    for page in range(1, RANK_PAGES + 1):
        rows = _serper_search(query, _PER_PAGE, page=page)
        if not rows:
            break
        for r in rows:                      # position ของหน้าถัดไปเริ่มที่ 1 → บวก offset
            r["position"] += seen
        seen += len(rows)
        pos, url = find_position(brand_domain, rows)
        if pos:
            return pos, url
    return None, None


def check_rank_for_brand(brand_id: int) -> dict:
    """เช็คอันดับ Google ของทุกคำถามเป้าหมาย 1 รอบ — ทุกแถวใช้ checked_at เดียวกัน"""
    conn = db.get_conn()
    try:
        brand = conn.execute(db.q("SELECT * FROM brands WHERE id=?"), (brand_id,)).fetchone()
        if not brand:
            raise ValueError(f"ไม่พบ brand id={brand_id}")
        questions = conn.execute(
            db.q("SELECT * FROM target_questions WHERE brand_id=? ORDER BY id"), (brand_id,)
        ).fetchall()
    finally:
        conn.close()

    engine = rank_backend()
    # ละเอียดระดับไมโครวินาที — checked_at คือคีย์ของ "รอบ" ถ้าใช้แค่วินาที
    # การเช็คสองรอบในวินาทีเดียวกันจะถูกยุบเป็นรอบเดียว (แถวซ้ำ/เทียบ before-after เพี้ยน)
    checked_at = datetime.datetime.now().isoformat(timespec="microseconds")
    positions, errors = [], 0
    for q in questions:
        try:
            pos, url = rank_lookup(q["question"], brand["domain"])
        except Exception:
            errors += 1   # ค้นไม่สำเร็จ ≠ ไม่ติดอันดับ — ข้ามไป ไม่บันทึกเป็น "หลุด"
            continue
        if pos:
            positions.append(pos)
        db.add_rank_result(brand_id, q["id"], q["question"], pos, url, engine, checked_at)

    return {
        "checked_at": checked_at,
        "engine": engine,
        "questions": len(questions),
        "checked": len(questions) - errors,
        "errors": errors,
        "ranked": len(positions),
        "avg_position": (sum(positions) / len(positions)) if positions else None,
    }


def run_for_brand(brand_id: int) -> dict:
    """รันมอนิเตอร์ 1 รอบให้แบรนด์: บันทึก run + ผลรายคำถาม + คำนวณ SoV."""
    conn = db.get_conn()
    try:
        brand = conn.execute(db.q("SELECT * FROM brands WHERE id=?"), (brand_id,)).fetchone()
        if not brand:
            raise ValueError(f"ไม่พบ brand id={brand_id}")
        questions = conn.execute(
            db.q("SELECT * FROM target_questions WHERE brand_id=? ORDER BY id"), (brand_id,)
        ).fetchall()

        cur = conn.execute(
            db.q("INSERT INTO monitoring_runs(brand_id,started_at,status,questions_total) VALUES(?,?,?,?) RETURNING id"),
            (brand_id, db.now(), "running", len(questions)),
        )
        run_id = cur.fetchone()["id"]
        conn.commit()

        hits = 0
        comp_counter: dict[str, int] = {}
        for q in questions:
            try:
                results = search(q["question"])
            except Exception:
                results = []
            present, position, comps = analyze(brand["domain"], results, brand["name"])
            if present:
                hits += 1
            for c in comps[:5]:
                comp_counter[c] = comp_counter.get(c, 0) + 1
            conn.execute(
                db.q("INSERT INTO run_results(run_id,question_id,question,brand_present,position,top_domains) "
                     "VALUES(?,?,?,?,?,?)"),
                (
                    run_id,
                    q["id"],
                    q["question"],
                    1 if present else 0,
                    position,
                    json.dumps([r["domain"] for r in results[:5]], ensure_ascii=False),
                ),
            )

        sov = (hits / len(questions)) if questions else 0.0
        conn.execute(
            db.q("UPDATE monitoring_runs SET finished_at=?,status=?,brand_hits=?,share_of_voice=? WHERE id=?"),
            (db.now(), "done", hits, sov, run_id),
        )
        conn.commit()

        top_comp = sorted(comp_counter.items(), key=lambda kv: -kv[1])[:8]
        return {
            "run_id": run_id,
            "questions": len(questions),
            "brand_hits": hits,
            "share_of_voice": sov,
            "top_competitors": top_comp,
        }
    finally:
        conn.close()


def llm_summary(brand_name: str, summary: dict) -> str:
    """(ออปชัน) สรุปเชิงบรรยายด้วย LLM ผ่าน LiteLLM — คืน '' ถ้าไม่ได้ตั้งค่า."""
    key = os.getenv("LITELLM_API_KEY", "").strip()
    base = os.getenv("LITELLM_BASE_URL", "").strip()
    if not key or not base:
        return ""
    try:
        from openai import OpenAI

        client = OpenAI(base_url=base, api_key=key)
        prompt = (
            f"แบรนด์ '{brand_name}' มี Share of Voice {summary['share_of_voice']*100:.0f}% "
            f"({summary['brand_hits']}/{summary['questions']} คำถาม) "
            f"คู่แข่งที่โผล่บ่อย: {summary['top_competitors']}. "
            "สรุปสั้นๆ เป็นภาษาไทยธรรมชาติว่าแบรนด์อยู่ตรงไหนและควรทำอะไรต่อ 2-3 ประโยค"
        )
        resp = client.chat.completions.create(
            model=os.getenv("LITELLM_MODEL", "claude-haiku-4-5"),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
        )
        return resp.choices[0].message.content or ""
    except Exception as e:
        return f"(LLM summary ไม่สำเร็จ: {e})"


if __name__ == "__main__":
    # ทดสอบ engine แบบเร็วๆ: python -m app.geo_worker "โกดังให้เช่า สมุทรปราการ"
    import sys

    q = sys.argv[1] if len(sys.argv) > 1 else "โกดังให้เช่า สมุทรปราการ"
    print(f"ค้นหา: {q}")
    for r in search(q):
        print(f"  {r['position']}. {r['domain']}  — {r['title'][:60]}")
