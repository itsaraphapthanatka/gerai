"""แปลหน้าที่ลูกค้าเห็นตามภาษาที่เลือก (ไทย อังกฤษ จีน ญี่ปุ่น เกาหลี)

· ภาษามาจาก ?lang=xx (จำลงคุกกี้ jr_lang) > คุกกี้ > ไทย · หน้าแรก /en /zh /ja /ko ตั้งคุกกี้เอง
· ข้อความต้นฉบับคือภาษาไทยตามเดิม (msgid) — คลังคำแปลอยู่ที่ app/i18n/*.json รูปแบบ {"ข้อความไทย": {"en": …, "zh": …, "ja": …, "ko": …}}
· ใน template: {{ _("ข้อความไทย") }} · มีตัวแปร {{ _("ส่ง {n} ฉบับ", n=x) }} · ข้อความที่โค้ดสร้าง (เช่นชื่อข้อตรวจ) {{ c.label|tr }}
· ข้อความที่มีตัวเลขแทรก (เก็บไว้ในฐานข้อมูลเป็นไทย) จับด้วย msgid ที่มี {ชื่อ} เช่น "รูปไม่มี alt {n} รูป"
· ไม่มีคำแปล = แสดงไทยเดิม (ไม่ล้ม) · i18n_smoke.py จับข้อความที่ยังไม่มีคำแปล
· ใบเสร็จ/ใบกำกับภาษีคงภาษาไทย (กฎสรรพากร) · สัญญาภาษาอื่นเป็นคำแปลอ้างอิง ฉบับไทยมีผลบังคับ
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .landing_i18n import LANGS, PATHS, HTML_LANG, NAMES, FONTS

COOKIE = "jr_lang"
DIR = Path(__file__).parent / "i18n"
_CAT: dict[str, dict] = {}
_GROUP: dict[str, dict[str, dict]] = {}                             # คลังต่อกลุ่ม (ชื่อไฟล์ json) — หน้าในกลุ่มใช้คำแปลของกลุ่มตัวเองก่อน
_PAGE_GROUP: dict[str, str] = {}                                    # template → กลุ่ม (จาก app/i18n/<กลุ่ม>.pages)
_PAT: list[tuple[re.Pattern, dict, list[str]]] = []
_LOADED = {"ok": False}
_FIELD = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
# ตัวแปรชื่อพวกนี้เป็นตัวเลขเสมอ → ตอนจับข้อความที่เก็บไว้ จับเฉพาะตัวเลข (กัน "{months} เดือน" ไปจับทั้งประโยคที่ลงท้ายด้วย "เดือน")
NUMERIC = {"n", "num", "count", "total", "days", "day", "months", "month", "years", "year", "hours", "minutes", "seconds",
           "pct", "percent", "amount", "price", "net", "score", "hits", "asked", "a", "b", "max", "min", "limit", "rank", "pages"}
_NUM_RX = r"[0-9][0-9,\.]*"


def valid(lang) -> str | None:
    lang = (lang or "").strip().lower()[:5]
    return lang if lang in LANGS else None


def lang_of(request) -> str:
    """ภาษาของคำขอนี้ · ?lang= ชนะคุกกี้ (middleware จำให้)"""
    if request is None:
        return "th"
    try:
        return valid(request.query_params.get("lang")) or valid(request.cookies.get(COOKIE)) or getattr(request.state, "lang", None) or "th"
    except Exception:
        return "th"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def load(force: bool = False) -> None:
    if _LOADED["ok"] and not force:
        return
    _CAT.clear()
    _PAT.clear()
    _GROUP.clear()
    _PAGE_GROUP.clear()
    for f in sorted(DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        g = _GROUP.setdefault(f.stem, {})
        for k, v in data.items():
            if isinstance(v, dict):
                _CAT[_norm(k)] = v
                g[_norm(k)] = v
    for f in sorted(DIR.glob("*.pages")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                _PAGE_GROUP[line.strip()] = f.stem
    for k, v in _CAT.items():
        names = _FIELD.findall(k)
        if names:
            parts = re.split(r"(\{[a-z_][a-z0-9_]*\})", k)
            rx = "".join((f"(?P<{p[1:-1]}>{_NUM_RX})" if p[1:-1] in NUMERIC else f"(?P<{p[1:-1]}>.+?)") if _FIELD.fullmatch(p) else re.escape(p)
                         for p in parts)
            if not re.sub(r"\{[a-z_][a-z0-9_]*\}", "", k).strip(" ·:—-()"):
                continue                                         # มีแต่ตัวแปร ไม่มีข้อความคงที่ = ไม่ใช้เป็นแบบจับ
            try:
                _PAT.append((re.compile("^" + rx + "$", re.S), v, names))
            except re.error:
                pass
    _PAT.sort(key=lambda x: -len(x[0].pattern))                     # แบบยาวกว่า (เจาะจงกว่า) ก่อน
    _LOADED["ok"] = True


def has(s: str) -> bool:
    load()
    return _norm(s) in _CAT


def tr(s, lang: str, /, **kw) -> str:
    """แปลข้อความไทยเป็นภาษา lang · ไม่มีคำแปล = ไทยเดิม · kw = ค่าแทน {ชื่อ}
    · _ns="<กลุ่ม>" = ใช้คำแปลของกลุ่มนั้นก่อน (คำสั้นที่แปลต่างกันตามหน้า เช่น "ชื่อ" = Name/Title)"""
    ns = kw.pop("_ns", None)
    if s is None:
        return ""
    s = str(s)
    if not s.strip() or lang == "th" or lang not in LANGS:
        return s.format(**kw) if kw else s
    load()
    key = _norm(s)
    hit = (_GROUP.get(ns) or {}).get(key) if ns else None
    hit = hit if hit and hit.get(lang) else _CAT.get(key)
    if hit and hit.get(lang):
        out = hit[lang]
        return out.format(**kw) if kw else out
    if not kw:
        for rx, v, names in _PAT:
            m = rx.match(key)
            if m and v.get(lang):
                vals = {n: tr(m.group(n), lang) for n in names}     # ส่วนที่แทรกเป็นไทยก็แปลต่อได้ (เช่นชื่อข้อตรวจ)
                try:
                    return v[lang].format(**vals)
                except (KeyError, IndexError):
                    break
    return s.format(**kw) if kw else s


# ---------- Jinja ----------
def _ctx_lang(ctx) -> str:
    lg = ctx.get("lang")
    if lg in LANGS:
        return lg
    return lang_of(ctx.get("request"))


def install(env) -> None:
    from jinja2 import pass_context

    def _ns(ctx, kw):
        if "_ns" not in kw:
            g = _PAGE_GROUP.get(ctx.name or "")
            if g:
                kw["_ns"] = g
        return kw

    @pass_context
    def _(ctx, s, /, **kw):
        load()
        return tr(s, _ctx_lang(ctx), **_ns(ctx, kw))

    @pass_context
    def _tr_filter(ctx, s, /, **kw):
        load()
        return tr(s, _ctx_lang(ctx), **_ns(ctx, kw))

    @pass_context
    def ui_lang(ctx):
        return _ctx_lang(ctx)

    @pass_context
    def html_lang(ctx):
        return HTML_LANG[_ctx_lang(ctx)]

    @pass_context
    def home_href(ctx):
        return PATHS[_ctx_lang(ctx)]

    @pass_context
    def lang_links(ctx):
        """ลิงก์เปลี่ยนภาษาของหน้าปัจจุบัน (หน้าเดิม + ?lang=xx)"""
        req = ctx.get("request")
        path = req.url.path if req is not None else "/"
        q = [(k, v) for k, v in (req.query_params.multi_items() if req is not None else []) if k != "lang"]
        from urllib.parse import urlencode
        cur = _ctx_lang(ctx)
        return [{"code": c, "name": NAMES[c], "hreflang": HTML_LANG[c], "current": c == cur,
                 "href": path + "?" + urlencode(q + [("lang", c)])} for c in LANGS]

    @pass_context
    def font_css(ctx):
        """ฟอนต์ของภาษา (Google Fonts) — ไทย/อังกฤษใช้ชุดเดิม"""
        return FONTS[_ctx_lang(ctx)][1] if _ctx_lang(ctx) in ("zh", "ja", "ko") else None

    @pass_context
    def font_family(ctx):
        return FONTS[_ctx_lang(ctx)][0]

    env.globals.update({"_": _, "ui_lang": ui_lang, "html_lang": html_lang, "home_href": home_href,
                        "lang_links": lang_links, "font_css": font_css, "font_family": font_family})
    env.filters["tr"] = _tr_filter
