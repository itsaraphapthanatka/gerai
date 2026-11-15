"""เหตุการณ์ funnel ภายใน (stage 08B, flag analytics) — สัญญาข้อมูลใน docs/rebuild/ANALYTICS_CONTRACT.md

หลักการ:
- ไม่มี tracker ภายนอก ไม่มีคุกกี้ ไม่มี fingerprint · เก็บในตาราง analytics_events ของเราเท่านั้น (ไม่ส่งออก)
- flag ปิด = ไม่เก็บอะไรเลย (โหมดปิด) · ทีมงานที่ล็อกอิน/แอดมินที่ preview ไม่ถูกนับ
- เหตุการณ์ที่ "สำเร็จ" บันทึกฝั่งเซิร์ฟเวอร์ตอนที่ข้อมูลถูกบันทึกจริง · ฝั่งเบราว์เซอร์มีแค่การกระทำ (เปิดหลักฐาน) ซึ่งติดป้าย origin=browser
- ไม่เก็บ: ชื่อ เบอร์ อีเมล IP user-agent token URL เต็ม ข้อความคำถาม/หลักฐาน · props รับเฉพาะคีย์และค่าที่อยู่ในรายการ
- ผูกกับลีดด้วย lead_id ภายในเท่านั้น (ไม่ออกไปนอกระบบ)
- meeting_booked บันทึกเมื่อทีมกดยืนยันนัด (appointments.confirm, stage 06A-2) เท่านั้น — ห้ามนับคำขอแผน/การเลือกเวลาเป็นการนัด"""
from __future__ import annotations

import datetime as dt
import json
from urllib.parse import urlparse

from . import db, flags

# name -> (ใครบันทึก, {prop: ค่าที่อนุญาต | type})
EVENTS: dict[str, tuple[str, dict]] = {
    "audit_started": ("server", {"reused": bool}),
    "audit_completed": ("server", {"status": ("completed", "partial", "failed")}),
    "business_context_confirmed": ("server", {"version": int}),
    "evidence_opened": ("browser", {"section": ("lane", "action", "ai_test"),
                                    "lane": ("website_readiness", "web_search_presence", "ai_brand_mention", "ai_citation")}),
    "plan_requested": ("server", {"channel": ("line", "phone", "email", "line_oa")}),
    "booking_link_clicked": ("server", {"target": ("line",)}),
    "meeting_booked": ("server", {}),
}
BROWSER_EVENTS = tuple(k for k, (o, _) in EVENTS.items() if o == "browser")
FUNNEL = ("audit_started", "audit_completed", "business_context_confirmed", "evidence_opened", "plan_requested",
          "booking_link_clicked", "meeting_booked")

# ---------- ที่มา (attribution) แบบ allowlist ----------
SOURCES = ("partner", "organic", "line", "email", "social", "referral", "direct", "unknown")
SOURCE_LABEL = {"partner": "พันธมิตร", "organic": "ค้นหา", "line": "LINE", "email": "อีเมล", "social": "โซเชียล",
                "referral": "เว็บอื่นแนะนำ", "direct": "เข้าตรง", "unknown": "ไม่ทราบ"}
UTM_ALLOW = ("google", "bing", "facebook", "instagram", "line", "linkedin", "youtube", "tiktok", "email", "newsletter", "partner")
UTM_SOURCE = {"google": "organic", "bing": "organic", "facebook": "social", "instagram": "social", "linkedin": "social",
              "youtube": "social", "tiktok": "social", "line": "line", "email": "email", "newsletter": "email", "partner": "partner"}
SRC_SOURCE = {"partner": "partner", "line": "line", "line_chat": "line", "email": "email", "follow": "email", "invite": "email"}
_HOSTS = (("organic", ("google.", "bing.com", "duckduckgo.com", "search.yahoo.", "yandex.", "baidu.com", "naver.com")),
          ("line", ("line.me", "liff.line.me")),
          ("email", ("mail.google.com", "outlook.live.com", "outlook.office.com", "mail.yahoo.com")),
          ("social", ("facebook.com", "instagram.com", "linkedin.com", "lnkd.in", "t.co", "x.com", "twitter.com",
                      "youtube.com", "tiktok.com", "pantip.com")))
ENTRIES = ("organic", "line", "email", "social", "referral")


def enabled() -> bool:
    try:
        return flags.enabled("analytics")
    except Exception:
        return False


def entry_from_referer(referer: str | None, own_host: str | None) -> str | None:
    """ประเภทของหน้าที่พามา จาก Referer — เก็บแค่ประเภท ไม่เก็บ URL · เว็บเราเอง/ไม่มี = None"""
    if not referer:
        return None
    try:
        h = (urlparse(referer).hostname or "").lower()
    except ValueError:
        return None
    if not h or (own_host and (h == own_host.lower() or h.endswith("." + own_host.lower()))):
        return None
    for kind, pats in _HOSTS:
        if any(h == p or h.endswith("." + p) or (p.endswith(".") and (h.startswith(p) or ("." + p) in h)) for p in pats):
            return kind
    return "referral"


def clean_utm(v: str | None) -> str | None:
    v = (v or "").strip().lower()
    return v if v in UTM_ALLOW else None


def classify(*, partner: bool = False, utm_source: str | None = None, src: str | None = None, entry: str | None = None) -> dict:
    """ลำดับความสำคัญ: พันธมิตร > utm_source ที่อยู่ในรายการ > รหัส src ของลิงก์เรา > ประเภทหน้าที่พามา > เข้าตรง"""
    utm = clean_utm(utm_source)
    if partner:
        source = "partner"
    elif utm:
        source = UTM_SOURCE[utm]
    elif (src or "") in SRC_SOURCE:
        source = SRC_SOURCE[src]
    elif entry in ENTRIES:
        source = entry
    else:
        source = "direct"
    return {"source": source, "utm_source": utm}


# ---------- บันทึก ----------
def _clean_props(name: str, props: dict | None) -> dict:
    spec = EVENTS[name][1]
    out = {}
    for k, v in (props or {}).items():
        rule = spec.get(k)
        if rule is bool:
            out[k] = bool(v)
        elif rule is int:
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            if 0 <= n <= 10000:
                out[k] = n
        elif isinstance(rule, tuple) and v in rule:
            out[k] = v
    return out


def _excluded(request) -> bool:
    """ไม่นับทีมงานที่ล็อกอิน และแอดมินที่กำลัง preview"""
    if request is None:
        return False
    try:
        if getattr(request.state, "staff", None) is not None or (request.session or {}).get("is_admin"):
            return True
        return bool(request.cookies.get(flags.PREVIEW_COOKIE))
    except Exception:
        return False


def emit(name: str, lead_id: int | None = None, props: dict | None = None, *, request=None, source: str | None = None,
         utm_source: str | None = None, origin: str | None = None) -> bool:
    """บันทึกเหตุการณ์ · ไม่เคยทำให้งานหลักล้ม · คืน True ถ้าบันทึก"""
    if name not in EVENTS or not enabled() or _excluded(request):
        return False
    try:
        if source not in SOURCES:
            source = (db.analytics_source_of(lead_id) if lead_id else None) or "unknown"
        db.add_analytics_event(name, origin or EVENTS[name][0], lead_id, source, clean_utm(utm_source),
                               json.dumps(_clean_props(name, props), sort_keys=True))
        return True
    except Exception:
        return False


DEDUPE_MIN = 10
DAILY_CAP = 100


def emit_browser(name: str, lead_id: int, props: dict | None, request=None) -> str:
    """การกระทำจากเบราว์เซอร์: ชื่อต้องอยู่ใน BROWSER_EVENTS · กดซ้ำแบบเดิมใน 10 นาทีไม่นับ · ต่อลีดไม่เกิน 100/วัน"""
    if name not in BROWSER_EVENTS:
        return "unknown_event"
    if not enabled() or _excluded(request):
        return "off"
    p = json.dumps(_clean_props(name, props), sort_keys=True)
    now = dt.datetime.now()
    try:
        if db.analytics_recent(name, lead_id, p, (now - dt.timedelta(minutes=DEDUPE_MIN)).isoformat(timespec="seconds")):
            return "dup"
        if db.analytics_count_since(lead_id, (now - dt.timedelta(days=1)).isoformat(timespec="seconds")) >= DAILY_CAP:
            return "capped"
    except Exception:
        return "error"
    return "ok" if emit(name, lead_id, json.loads(p), request=request, origin="browser") else "error"


# ---------- รายงานภายใน ----------
def funnel(days: int = 30) -> dict:
    """นับลีด (ไม่ซ้ำ) ที่ผ่านแต่ละขั้น แยกตามที่มา · เหตุการณ์ที่ไม่มีลีด (ไม่ควรเกิด) นับเป็นครั้ง"""
    since = (dt.datetime.now() - dt.timedelta(days=days)).isoformat(timespec="seconds")
    rows = db.analytics_summary(since)
    by = {(r["name"], r["source"]): dict(r) for r in rows}
    srcs = [s for s in SOURCES if any(k[1] == s for k in by)]
    table = []
    for name in FUNNEL:
        cells = {s: (by.get((name, s)) or {}).get("leads") or 0 for s in srcs}
        table.append({"name": name, "origin": EVENTS[name][0], "cells": cells, "total": sum(cells.values()),
                      "events": sum((by.get((name, s)) or {}).get("n") or 0 for s in srcs)})
    return {"days": days, "sources": [(s, SOURCE_LABEL[s]) for s in srcs], "rows": table}



def att_fields(request) -> str:
    """ช่องซ่อนในฟอร์มตรวจเว็บ: utm_source (เฉพาะที่อยู่ในรายการ) + ประเภทหน้าที่พามา — ส่งต่อที่มาโดยไม่ใช้คุกกี้
    flag ปิด = ไม่มีอะไร · ค่าที่ส่งกลับมาถูกตรวจซ้ำกับรายการตอนรับ (ไม่เชื่อค่าจากฟอร์ม)"""
    from markupsafe import Markup
    if request is None or not enabled():
        return Markup("")
    utm = clean_utm(request.query_params.get("utm_source")) or ""
    ent = entry_from_referer(request.headers.get("referer"), request.url.hostname) or ""
    return Markup(f'<input type="hidden" name="utm_source" value="{utm}"><input type="hidden" name="ent" value="{ent}">')


def install(env) -> None:
    env.globals["att_fields"] = att_fields
