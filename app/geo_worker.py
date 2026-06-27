"""GEO monitoring engine.

สำหรับแต่ละคำถามเป้าหมาย: ค้นเว็บ (ddgs) → ดูว่าโดเมนแบรนด์โผล่ไหม + ตำแหน่ง + คู่แข่ง
แล้วคำนวณ Share of Voice = (จำนวนคำถามที่แบรนด์โผล่) / (จำนวนคำถามทั้งหมด)

MVP ใช้ heuristic (จับคู่โดเมน) — ไม่ต้องมี LLM ก็รันได้
(เติม LLM summary ผ่าน LiteLLM ได้ภายหลัง ดู llm_summary())
"""
from __future__ import annotations
import os
import json
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
        headers={"X-Subscription-Token": os.getenv("BRAVE_API_KEY", ""), "Accept": "application/json"},
        params={"q": query, "count": limit, "country": "th", "search_lang": "th"},
        timeout=20,
    )
    r.raise_for_status()
    results = ((r.json().get("web") or {}).get("results")) or []
    return [_row(x.get("title"), x.get("url"), i) for i, x in enumerate(results[:limit])]


def _serper_search(query: str, limit: int) -> list[dict]:
    import httpx

    r = httpx.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": os.getenv("SERPER_API_KEY", ""), "Content-Type": "application/json"},
        json={"q": query, "gl": "th", "hl": "th", "num": limit},
        timeout=20,
    )
    r.raise_for_status()
    results = r.json().get("organic") or []
    return [_row(x.get("title"), x.get("link"), i) for i, x in enumerate(results[:limit])]


def active_backend() -> str:
    """backend ที่ใช้จริง — เลือก brave/serper เฉพาะเมื่อมี API key ไม่งั้น ddgs (ฟรี)."""
    b = os.getenv("GEO_SEARCH_BACKEND", "ddgs").lower()
    if b == "brave" and os.getenv("BRAVE_API_KEY"):
        return "brave"
    if b == "serper" and os.getenv("SERPER_API_KEY"):
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


def analyze(brand_domain: str, results: list[dict]):
    """คืน (present, position, competitor_domains)."""
    bd = (brand_domain or "").lower()
    if bd.startswith("www."):
        bd = bd[4:]
    present, position = False, None
    competitors = []
    for r in results:
        if bd and (bd in r["domain"] or r["domain"] in bd) and r["domain"]:
            if not present:
                present, position = True, r["position"]
        elif r["domain"]:
            competitors.append(r["domain"])
    return present, position, competitors


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
            present, position, comps = analyze(brand["domain"], results)
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
