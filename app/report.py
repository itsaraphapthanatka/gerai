"""รายงานสรุปรายแบรนด์ + ส่งออกไฟล์ — รวมตัวเลขจากทุกตัววัดที่มีอยู่แล้วเป็นหน้าเดียว (HTML/PDF) และ CSV

ลูกค้าถามคำถามเดียว "เดือนนี้ดีขึ้นไหม" แต่คำตอบกระจายอยู่ 8 หน้า (SoV, อันดับ, AI, Search Console, สุขภาพ,
ความเร็ว, AI ของ Google, คอนเทนต์) หน้านี้รวมให้พร้อม "สิ่งที่ควรทำต่อ" ที่สรุปจากตัวเลขเองด้วยกฎง่าย ๆ
PDF เรนเดอร์ด้วย Gotenberg (Chromium) ใน container `pdf` ของ compose — ฟอนต์ Sarabun mount ไว้ให้ ไม่งั้นไทยเป็นกล่อง
collect() อ่าน DB → compose() เป็น pure (เทสต์ได้จาก dict) → render_html()/to_pdf()/csv_bytes()
CSV ใส่ BOM เพราะ Excel เปิด UTF-8 ไม่มี BOM แล้วภาษาไทยเพี้ยนทุกครั้ง
"""
from __future__ import annotations
import os
import io
import csv
import json
import datetime
from pathlib import Path

PDF_URL = os.getenv("PDF_SERVICE_URL", "http://pdf:3000")
PERIOD_DAYS = 30
PDF_TIMEOUT = 90
A4 = {"paperWidth": "8.27", "paperHeight": "11.69", "marginTop": "0.5", "marginBottom": "0.5",
      "marginLeft": "0.5", "marginRight": "0.5", "printBackground": "true"}


def _rep(row):
    try:
        return json.loads(row["report"]) if row and row["report"] else None
    except Exception:
        return None


def _d(s) -> str:
    return (s or "")[:10]


def compose(brand: dict, today: datetime.date, runs: list, rank: list, ai, gsc, health, speed, serp,
            content: list, gaps: dict, n_questions: int, site: str, rank_rows: list | None = None) -> dict:
    """รวมเป็นรายงาน — ทุกอินพุตเป็น dict/list ธรรมดา (ไม่แตะ DB) จะได้เทสต์กฎ 'สิ่งที่ควรทำต่อ' ได้"""
    since = today - datetime.timedelta(days=PERIOD_DAYS)
    out = {"brand": brand, "site": site, "generated_at": datetime.datetime.now().isoformat(timespec="minutes"),
           "period": {"from": since.isoformat(), "to": today.isoformat(), "days": PERIOD_DAYS}, "n_questions": n_questions}

    # SoV — รอบล่าสุด เทียบรอบสุดท้ายก่อนช่วงรายงาน
    done = [r for r in runs if (r.get("status") or "done") == "done" and r.get("share_of_voice") is not None]
    latest = done[0] if done else None
    prev = next((r for r in done if _d(r.get("started_at")) < since.isoformat()), None)
    out["sov"] = {
        "latest": {"sov": round(latest["share_of_voice"]), "hits": latest.get("brand_hits"), "total": latest.get("questions_total"), "at": _d(latest.get("started_at"))} if latest else None,
        "previous": {"sov": round(prev["share_of_voice"]), "at": _d(prev.get("started_at"))} if prev else None,
        "delta": (round(latest["share_of_voice"]) - round(prev["share_of_voice"])) if (latest and prev) else None,
        "series": [(_d(r.get("started_at")), round(r["share_of_voice"])) for r in reversed(done[:8])],
    }
    # อันดับ Google — รอบล่าสุด vs รอบก่อน
    rk = rank[0] if rank else None
    rk_prev = rank[1] if len(rank) > 1 else None
    out["rank"] = {
        "latest": {"ranked": int(rk["ranked"] or 0), "total": int(rk["total"] or 0),
                   "avg_pos": round(float(rk["avg_pos"]), 1) if rk.get("avg_pos") else None, "at": _d(rk.get("checked_at"))} if rk else None,
        "previous": {"ranked": int(rk_prev["ranked"] or 0), "total": int(rk_prev["total"] or 0)} if rk_prev else None,
        "rows": [{"question": r.get("question"), "position": r.get("position"), "url": r.get("url")} for r in (rank_rows or [])][:12],
    }
    # ผู้ช่วย AI
    out["ai"] = None
    if ai:
        out["ai"] = {"rate": ai.get("rate"), "cited": ai.get("cited"), "named": ai.get("named"), "asked": ai.get("asked"),
                     "at": _d(ai.get("checked_at")), "cost_usd": ai.get("cost_usd"),
                     "engines": [{"label": v.get("label"), "asked": v.get("asked"), "cited": v.get("cited"), "named": v.get("named")}
                                 for v in (ai.get("by_engine") or {}).values() if v.get("asked")]}
    # Search Console
    out["gsc"] = None
    if gsc:
        idx, an = gsc.get("index") or {}, (gsc.get("analytics") or {})
        sm = gsc.get("sitemap") or {}
        out["gsc"] = {"linked": bool(gsc.get("linked")), "reason": gsc.get("reason"), "at": _d(gsc.get("synced_at")),
                      "indexed": idx.get("indexed", 0), "inspected": idx.get("inspected", 0), "unknown": idx.get("unknown", 0),
                      "discovered": idx.get("discovered", 0), "crawled": idx.get("crawled", 0), "blocked": idx.get("blocked", 0),
                      "clicks": (an.get("site") or {}).get("clicks", 0), "impressions": (an.get("site") or {}).get("impressions", 0),
                      "position": (an.get("site") or {}).get("position"), "content_clicks": (an.get("content") or {}).get("clicks", 0),
                      "top_queries": (an.get("top_queries") or [])[:5], "sitemap_submitted": bool(sm.get("submitted"))}
    # สุขภาพเว็บ
    out["health"] = None
    if health:
        fails = [c.get("label") for c in health.get("checks") or [] if c.get("status") == "fail"]
        warns = [c.get("label") for c in health.get("checks") or [] if c.get("status") == "warn"]
        out["health"] = {"ok": not fails, "n_fail": len(fails), "fails": fails, "warns": warns, "at": _d(health.get("checked_at"))}
    # ความเร็ว
    out["speed"] = None
    if speed:
        m, d = speed.get("mobile") or {}, speed.get("desktop") or {}
        out["speed"] = {"mobile": (m.get("scores") or {}).get("performance"), "desktop": (d.get("scores") or {}).get("performance"),
                        "seo": (m.get("scores") or {}).get("seo"), "error": m.get("error") or d.get("error"), "at": _d(speed.get("checked_at")),
                        "top_fix": [(o.get("title"), o.get("savings_ms")) for o in (m.get("opportunities") or [])[:3]]}
    # Google AI Overview / AI Mode
    out["serp"] = None
    if serp and not serp.get("skip"):
        bk = serp.get("by_kind") or {}
        ov, md = bk.get("overview") or {}, bk.get("mode") or {}
        out["serp"] = {"overview_hit": (ov.get("cited") or 0) + (ov.get("named") or 0), "overview_shown": ov.get("shown") or 0,
                       "overview_none": ov.get("none") or 0, "mode_hit": (md.get("cited") or 0) + (md.get("named") or 0),
                       "mode_shown": md.get("shown") or 0, "at": _d(serp.get("checked_at"))}
    # คอนเทนต์
    pub = [c for c in content if c.get("status") == "published"]
    pub_period = [c for c in pub if _d(c.get("published_at")) >= since.isoformat()]
    out["content"] = {"total": len(content), "published": len(pub), "published_period": len(pub_period),
                      "drafts": len(content) - len(pub), "latest": _d(max((c.get("published_at") or "" for c in pub), default="")),
                      "recent": [{"title": c.get("title"), "at": _d(c.get("published_at"))} for c in sorted(pub_period, key=lambda c: c.get("published_at") or "", reverse=True)[:8]]}
    out["gaps"] = {"critical": (gaps or {}).get("critical", 0), "total": (gaps or {}).get("total_gaps", 0)}
    out["next_steps"] = next_steps(out)
    return out


def next_steps(d: dict) -> list:
    """กฎง่าย ๆ จากตัวเลขที่มี — เรียงจากเรื่องที่ขวางผลมากสุด (เว็บมองไม่เห็น > ยังไม่ติด index > ไม่มีคอนเทนต์ > AI ไม่พูดถึง)"""
    steps = []
    h = d.get("health") or {}
    fails = h.get("fails") or []
    if any("ไม่ต้องรอ JavaScript" in f for f in fails):
        steps.append("เว็บเรนเดอร์ฝั่ง client — บอท AI อ่านไม่เห็นอะไร ต้องทำ prerender/SSR (ทำกับ tanawat-lawyer.com แล้ว ใช้สูตรเดียวกัน)")
    if any("rewrite" in f for f in fails):
        steps.append("ตัวเชื่อม /geo · llms.txt · sitemap ยังเป็น redirect/ไม่ครบ — ตั้ง rewrite ให้ตอบบนโดเมนเอง")
    g = d.get("gsc")
    if g is None or not g.get("linked"):
        steps.append("เชื่อม Search Console (เพิ่มบัญชีบริการเป็นผู้ใช้ Full) เพื่อให้เห็น index จริงและส่ง sitemap อัตโนมัติ")
    elif g.get("inspected") and not g.get("indexed"):
        steps.append(f"หน้าคอนเทนต์ยังไม่อยู่ใน index เลย (0/{g['inspected']}) — เพิ่มลิงก์ภายในจากหน้าแรก/เมนู แล้วรอ Google crawl")
    elif g.get("inspected") and g["indexed"] * 2 < g["inspected"]:
        steps.append(f"อยู่ใน index แค่ {g['indexed']}/{g['inspected']} หน้า — ดูสถานะรายหน้าในหน้า Search Console")
    gaps = d.get("gaps") or {}
    if gaps.get("critical"):
        steps.append(f"{gaps['critical']} คำถามยังไม่มีคอนเทนต์รองรับ — สร้างจากหน้า คอนเทนต์ (ระบบเขียนให้)")
    c = d.get("content") or {}
    if c.get("total") and not c.get("published_period"):
        steps.append(f"ไม่มีคอนเทนต์เผยแพร่ใหม่ใน {PERIOD_DAYS} วัน — GEO ต้องเติมต่อเนื่อง ตั้ง auto สร้าง/เผยแพร่ได้")
    ai = d.get("ai")
    if ai and ai.get("asked") and not (ai.get("cited") or ai.get("named")):
        steps.append("ผู้ช่วย AI ยังไม่พูดถึงแบรนด์เลย — ดูว่า AI อ้างหน้าแบบไหนของคู่แข่ง (หน้า มองเห็นบน AI) แล้วทำหน้าแบบนั้น")
    sp = d.get("speed") or {}
    if sp.get("mobile") is not None and sp["mobile"] < 50:
        steps.append(f"ความเร็วมือถือ {sp['mobile']}/100 — แก้ตามรายการในหน้า ความเร็วเว็บ (เริ่มจากที่ประหยัดเวลาได้มากสุด)")
    other = [f for f in fails if "JavaScript" not in f and "rewrite" not in f and "index" not in f and "ต่อเนื่อง" not in f]
    if other:
        steps.append("สุขภาพเว็บยังตก: " + ", ".join(other[:3]))
    return steps[:6]


def collect(brand_id: int) -> dict:
    from . import db, geo_content
    brand = db.get_brand(brand_id)
    rank = [dict(r) for r in db.rank_batches(brand_id, 2)]
    rank_rows = [dict(r) for r in db.rank_results_at(brand_id, rank[0]["checked_at"])] if rank else []
    return compose(
        brand=dict(brand), today=datetime.date.today(),
        runs=[dict(r) for r in db.list_runs(brand_id)], rank=rank, rank_rows=rank_rows,
        ai=_rep(db.last_ai_visibility(brand_id)), gsc=_rep(db.last_gsc(brand_id)), health=_rep(db.last_health_check(brand_id)),
        speed=_rep(db.last_pagespeed(brand_id)), serp=_rep(db.last_ai_serp(brand_id)),
        content=[dict(c) for c in db.list_content(brand_id)], gaps=db.get_content_gaps(brand_id),
        n_questions=len(db.list_questions(brand_id)), site=geo_content._site_url(brand),
    )


_env = None


def render_html(data: dict, pdf: bool = False) -> str:
    """เรนเดอร์ report.html (เทมเพลตเดี่ยว ไม่ใช้ base) — pdf=True ซ่อนปุ่มและใช้ฟอนต์จากระบบของ Gotenberg"""
    global _env
    if _env is None:
        import jinja2
        _env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(Path(__file__).parent / "templates")), autoescape=True)
    return _env.get_template("report.html").render(d=data, pdf=pdf)


def to_pdf(html: str) -> bytes:
    """HTML → PDF ผ่าน Gotenberg (POST multipart ไฟล์ชื่อ index.html เท่านั้น ตามสเปก)"""
    import httpx
    r = httpx.post(f"{PDF_URL.rstrip('/')}/forms/chromium/convert/html",
                   files={"files": ("index.html", html.encode("utf-8"), "text/html")}, data=A4, timeout=PDF_TIMEOUT)
    r.raise_for_status()
    return r.content


def pdf_available() -> bool:
    import httpx
    try:
        return httpx.get(f"{PDF_URL.rstrip('/')}/health", timeout=5).status_code == 200
    except Exception:
        return False


# ---------- CSV ----------
EXPORTS = {
    "content": "คอนเทนต์ทั้งหมด", "questions": "คำถามเป้าหมาย", "sov": "ประวัติ SoV รายรอบ", "rank": "อันดับ Google รอบล่าสุด",
    "ai": "ผลถามผู้ช่วย AI รอบล่าสุด", "gsc": "สถานะ index รายหน้า (Search Console)", "health": "ผลตรวจสุขภาพเว็บล่าสุด",
}


def csv_bytes(header: list, rows: list) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    return ("﻿" + buf.getvalue()).encode("utf-8")      # BOM: Excel ถึงจะอ่าน UTF-8 ไทยถูก


def export(brand_id: int, kind: str):
    """คืน (ชื่อไฟล์, header, rows) — ไม่รู้จัก kind คืน None"""
    from . import db, geo_content
    brand = db.get_brand(brand_id)
    slug = (brand["name"] or "brand").replace(" ", "_")[:30]
    today = datetime.date.today().isoformat()
    if kind == "content":
        qmap = {qq["id"]: qq["question"] for qq in db.list_questions(brand_id)}
        rows = [(c["id"], c["title"], c["status"], c["lang"], _d(c["published_at"]), geo_content.content_url(brand, c), qmap.get(c["question_id"], ""))
                for c in db.list_content(brand_id)]
        return f"{slug}_content_{today}.csv", ["id", "title", "status", "lang", "published_at", "url", "question"], rows
    if kind == "questions":
        return f"{slug}_questions_{today}.csv", ["id", "question", "lang"], [(qq["id"], qq["question"], qq["lang"]) for qq in db.list_questions(brand_id)]
    if kind == "sov":
        rows = [(r["id"], r["started_at"], r["share_of_voice"], r["brand_hits"], r["questions_total"], r["status"]) for r in db.list_runs(brand_id)]
        return f"{slug}_sov_{today}.csv", ["run_id", "started_at", "share_of_voice", "brand_hits", "questions_total", "status"], rows
    if kind == "rank":
        batches = db.rank_batches(brand_id, 1)
        rows = [(r["checked_at"], r["question"], r["position"], r["url"], r["engine"]) for r in db.rank_results_at(brand_id, batches[0]["checked_at"])] if batches else []
        return f"{slug}_rank_{today}.csv", ["checked_at", "question", "position", "url", "engine"], rows
    if kind == "ai":
        rep = _rep(db.last_ai_visibility(brand_id)) or {}
        rows = [(_d(r.get("checked_at")) or _d(rep.get("checked_at")), r.get("engine"), r.get("question"), r.get("status"),
                 "; ".join(r.get("citations") or []), "; ".join(r.get("others") or [])) for r in rep.get("rows") or []]
        return f"{slug}_ai_{today}.csv", ["checked_at", "engine", "question", "status", "citations", "other_domains"], rows
    if kind == "gsc":
        rep = _rep(db.last_gsc(brand_id)) or {}
        rows = [(p.get("title"), p.get("url"), p.get("group"), p.get("state"), _d(p.get("last_crawl")), p.get("clicks"), p.get("impressions"), p.get("position"))
                for p in rep.get("pages") or []]
        return f"{slug}_search_console_{today}.csv", ["title", "url", "group", "google_state", "last_crawl", "clicks_28d", "impressions_28d", "position"], rows
    if kind == "health":
        rep = _rep(db.last_health_check(brand_id)) or {}
        rows = [(_d(rep.get("checked_at")), c.get("key"), c.get("label"), c.get("status"), c.get("detail")) for c in rep.get("checks") or []]
        return f"{slug}_health_{today}.csv", ["checked_at", "key", "label", "status", "detail"], rows
    return None
