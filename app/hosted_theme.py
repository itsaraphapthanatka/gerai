"""ธีมของหน้าบทความที่เสิร์ฟใต้โดเมนลูกค้า (/geo และ /geo/<id>)

หน้าพวกนี้ขึ้นบนโดเมนของลูกค้า ไม่ใช่ของเจอ.AI — คนอ่านเข้ามาจากเมนูเว็บเขา
และกดกลับเข้าเว็บเขาต่อ ถ้าหน้ากลางทางยังเป็นสีของแพลตฟอร์ม คนอ่านจะรู้สึกว่า
หลุดไปเว็บอื่น (และบอทก็อ่านว่าเป็นคนละแหล่ง) ธีมจึงเก็บเป็นรายแบรนด์ที่
brands.theme เป็น JSON · แบรนด์ที่ยังไม่ตั้ง = ชุดสีเดิมของเจอ.AI ทุกค่า

ค่าทุกตัวถูกเขียนลง <style> หรือ attribute ตรง ๆ จึงต้องผ่านตัวกรองก่อน
ค่าที่ไม่ผ่าน (พิมพ์ผิด หรือมีคนแก้ฐานข้อมูลมั่ว) จะตกกลับเป็นค่า default
ไม่ใช่หลุดเข้าไปในหน้าเว็บ
"""
from __future__ import annotations

import json
import re

# ชุดเริ่มต้น = หน้าตาเดิมของหน้า /geo ก่อนมีธีม (สีม่วงของเจอ.AI บนพื้นขาว)
DEFAULT: dict[str, str] = {
    # ตัวอักษร
    "font": "'IBM Plex Sans Thai', system-ui, -apple-system, sans-serif",
    "font_url": "",
    # พื้นและตัวหนังสือ
    "page": "#ffffff",
    "surface": "#ffffff",
    "tint": "#f7f8fa",
    "text": "#1c1c2b",
    "muted": "#6b6b7b",
    "faint": "#9b9bab",
    "border": "#ececf1",
    # สีประจำแบรนด์ — ลิงก์ หัวข้อย่อย แถบชวนติดต่อ
    "accent": "#7132f5",
    "accent_ink": "#ffffff",
    # ปุ่มเม็ดยาบนแถบบนและในแถบชวนติดต่อ
    "cta": "#7132f5",
    "cta_ink": "#ffffff",
    # ท้ายหน้า
    "foot": "#ffffff",
    "foot_text": "#6b6b7b",
    "foot_head": "#1c1c2b",
    "foot_border": "#ececf1",
    "foot_round": "0px",
    # รูปทรง
    "radius": "14px",
    # โลโก้ (ว่าง = ใช้ชื่อแบรนด์เป็นตัวหนังสือแทน) · logo_foot ใช้บนพื้นเข้มท้ายหน้า
    "logo": "",
    "logo_foot": "",
    "logo_h": "34px",
    # แถบชวนติดต่อท้ายบทความ — ขึ้นเฉพาะแบรนด์ที่ตั้ง cta_url กับ cta_label ไว้
    "cta_url": "",
    "cta_label": "",
    "cta_title": "",
    "cta_text": "",
}

_COLOR = re.compile(r"^#[0-9a-fA-F]{3,8}$|^rgba?\([\d\s.,%]{3,40}\)$")
_LEN = re.compile(r"^\d{1,3}(px|rem)$")
_FONT = re.compile(r"^[\w\s,'\"฀-๿-]{1,160}$")
# รูป: path ในโดเมนเดียวกัน หรือ https เต็ม ๆ เท่านั้น
_IMG = re.compile(r"^/[\w\-./%]{0,200}$|^https://[\w\-.]{1,80}/[\w\-./%]{0,200}$")
# ลิงก์: อนุญาต query string ด้วย (เช่น /contact?src=geo)
_LINK = re.compile(r"^/[\w\-./%?=&+]{0,200}$|^https://[\w\-.]{1,80}(/[\w\-./%?=&+]{0,200})?$")
# ฟอนต์โหลดจาก Google Fonts ทางเดียว — ไม่ให้ค่าที่กรอกผิดกลายเป็นการเรียกเว็บอื่นจากหน้าลูกค้า
_FONT_URL = re.compile(r"^https://fonts\.googleapis\.com/[\w\-./%?=&+:;,@]{1,300}$")

_COLOR_KEYS = ("page", "surface", "tint", "text", "muted", "faint", "border",
               "accent", "accent_ink", "cta", "cta_ink",
               "foot", "foot_text", "foot_head", "foot_border")
_LEN_KEYS = ("radius", "foot_round", "logo_h")
_TEXT_KEYS = ("cta_label", "cta_title", "cta_text")


def _ok(key: str, val: str) -> bool:
    if key in _COLOR_KEYS:
        return bool(_COLOR.match(val))
    if key in _LEN_KEYS:
        return bool(_LEN.match(val))
    if key in ("logo", "logo_foot"):
        return bool(_IMG.match(val))
    if key == "cta_url":
        return bool(_LINK.match(val))
    if key == "font":
        return bool(_FONT.match(val))
    if key == "font_url":
        return bool(_FONT_URL.match(val))
    if key in _TEXT_KEYS:
        # ข้อความล้วน — Jinja escape ให้อยู่แล้ว กันแค่ความยาวกับอักขระควบคุม
        return len(val) <= 160 and not any(ord(ch) < 32 for ch in val)
    return False


def theme_for(brand) -> dict[str, str]:
    """ธีมของแบรนด์ทับค่าเริ่มต้นทีละคีย์ — คีย์ที่ไม่รู้จักหรือค่าที่ไม่ผ่านตัวกรองถูกทิ้ง"""
    out = dict(DEFAULT)
    try:
        raw = json.loads(brand["theme"] or "{}")
    except Exception:
        raw = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in DEFAULT and isinstance(v, str) and _ok(k, v.strip()):
                out[k] = v.strip()
    return out


def css_vars(th: dict[str, str]) -> str:
    """ตัวแปร CSS สำหรับ :root — ทุกค่าผ่าน _ok มาแล้ว"""
    keys = ("font", "page", "surface", "tint", "text", "muted", "faint", "border",
            "accent", "accent_ink", "cta", "cta_ink",
            "foot", "foot_text", "foot_head", "foot_border", "foot_round", "radius", "logo_h")
    return "".join(f"--{k.replace('_', '-')}:{th[k]};" for k in keys)


# ป้ายบนหน้า — บทความมีได้หลายภาษา ภาษาที่ไม่ใช่ไทยใช้อังกฤษ (เหมือนเดิมก่อนมีธีม)
LABELS = {
    "th": {"eyebrow": "บทความ", "all": "บทความทั้งหมด", "home": "หน้าแรก", "index_h1": "บทความและคำแนะนำ",
           "more": "บทความอื่น", "more_all": "ดูบทความทั้งหมด →", "read": "อ่านต่อ",
           "by": "เนื้อหาโดย", "empty": "ยังไม่มีบทความที่เผยแพร่",
           "count": "{n} บทความ", "lede": "รวมบทความและคำแนะนำจาก {brand}"},
    "en": {"eyebrow": "Articles", "all": "All articles", "home": "Home", "index_h1": "Guides & articles",
           "more": "More articles", "more_all": "All articles →", "read": "Read",
           "by": "Written by", "empty": "No articles published yet",
           "count": "{n} articles", "lede": "Guides and answers from {brand}"},
}


def labels(lang: str | None) -> dict[str, str]:
    return LABELS["th"] if (lang or "th")[:2] == "th" else LABELS["en"]


# ฟอนต์สำเร็จรูปในฟอร์มตั้งค่า — (คีย์, ชื่อที่โชว์, font stack, URL ของ Google Fonts)
# ค่าเริ่มต้นของแพลตฟอร์มไม่โหลดฟอนต์เลย (font_url ว่าง) เครื่องที่ไม่มี IBM Plex Sans Thai
# จึงตกไปใช้ฟอนต์ระบบ — เลือกชุดใดชุดหนึ่งที่นี่แล้วหน้าถึงจะโหลดฟอนต์จริง
_G = "https://fonts.googleapis.com/css2?family="
FONTS = [
    ("plex", "IBM Plex Sans Thai", "'IBM Plex Sans Thai', system-ui, -apple-system, sans-serif",
     _G + "IBM+Plex+Sans+Thai:wght@400;500;600;700&display=swap"),
    ("noto", "Noto Sans Thai", "'Noto Sans Thai','Inter',system-ui,sans-serif",
     _G + "Noto+Sans+Thai:wght@400;500;600;700&display=swap"),
    ("sarabun", "Sarabun", "'Sarabun',system-ui,sans-serif",
     _G + "Sarabun:wght@400;500;600;700&display=swap"),
    ("prompt", "Prompt", "'Prompt',system-ui,sans-serif",
     _G + "Prompt:wght@400;500;600;700&display=swap"),
    ("kanit", "Kanit", "'Kanit',system-ui,sans-serif",
     _G + "Kanit:wght@400;500;600;700&display=swap"),
    ("anuphan", "Anuphan", "'Anuphan',system-ui,sans-serif",
     _G + "Anuphan:wght@400..700&display=swap"),
]

# ช่องในฟอร์มตั้งค่า (เรียงตามกลุ่มบนหน้า) — font/font_url มาจาก font_preset แยกต่างหาก
# foot_border ไม่มีในฟอร์ม เพราะคิดเองได้จากสีฟุตเตอร์ (ดู from_form)
FORM_KEYS = ("page", "surface", "tint", "text", "muted", "faint", "border",
             "accent", "accent_ink", "cta", "cta_ink",
             "foot", "foot_text", "foot_head", "foot_round", "radius",
             "logo", "logo_foot", "logo_h",
             "cta_url", "cta_label", "cta_title", "cta_text")


def font_preset_of(th: dict) -> str:
    """ชุดฟอนต์ที่ตรงกับธีมนี้ — ไม่ตรงชุดไหนเลย = กรอกเอง"""
    for key, _label, stack, url in FONTS:
        if th["font"] == stack and th["font_url"] == url:
            return key
    return "custom"


def px(v: str) -> str:
    """'34px' → '34' สำหรับช่องตัวเลขในฟอร์ม"""
    return re.sub(r"\D", "", v or "") or "0"


def from_form(form) -> str | None:
    """ฟอร์มตั้งค่า → JSON ที่เก็บลง brands.theme

    เก็บเฉพาะค่าที่ต่างจากค่าเริ่มต้น — แบรนด์ที่แก้แค่สีแบรนด์จะได้ JSON สองบรรทัด
    และค่าที่ไม่ได้ตั้งจะเดินตามค่าเริ่มต้นของแพลตฟอร์มต่อไปเมื่อมันเปลี่ยน
    คืน None เมื่อไม่ต่างสักช่อง (= ล้างธีม กลับไปใช้ชุดของเจอ.AI)
    """
    out: dict[str, str] = {}
    preset = (form.get("font_preset") or "").strip()
    pick = next((f for f in FONTS if f[0] == preset), None)
    if pick:
        out["font"], out["font_url"] = pick[2], pick[3]
    elif preset == "custom":
        for k in ("font", "font_url"):
            v = (form.get(k) or "").strip()
            if v and _ok(k, v):
                out[k] = v
    for k in FORM_KEYS:
        v = (form.get(k) or "").strip()
        if k in _LEN_KEYS and v.isdigit():      # ช่องตัวเลขส่งมาเป็นเลขล้วน
            v = v + "px"
        if v and _ok(k, v):
            out[k] = v
    # เส้นคั่นท้ายหน้า: ฟุตเตอร์สีเดียวกับพื้นหน้าต้องมีเส้นถึงจะเห็นว่าแยกส่วน
    # ฟุตเตอร์คนละสี (เช่น พื้นดำ) มีเส้นแล้วกลายเป็นขีดสว่างพาดขอบบน
    foot = out.get("foot", DEFAULT["foot"]).lower()
    page = out.get("page", DEFAULT["page"]).lower()
    out["foot_border"] = out.get("border", DEFAULT["border"]) if foot == page else "#00000000"
    out = {k: v for k, v in out.items() if v != DEFAULT[k]}
    return json.dumps(out, ensure_ascii=False, indent=2) if out else None
