"""ล้างความลับออกจากข้อความ error ก่อนเก็บลงฐานข้อมูลหรือแสดงบนหน้าเว็บ

error ของ httpx มี URL เต็มติดมา — บริการที่รับคีย์ทาง query string (SerpApi `api_key=`, Google `key=`)
หรือใน path (Telegram `/bot<token>/`) จึงทำคีย์หลุดไปอยู่ในรายงานที่ลูกค้าเปิดดูได้
ทุกที่ที่เก็บ/แสดง str(e) ให้ผ่าน redact() ก่อน
"""
import re

_QUERY = re.compile(
    r"(?i)([?&;](?:api[_-]?key|apikey|key|access[_-]?token|token|secret|client[_-]?secret|password|passwd|sig|signature)=)"
    r"[^&\s'\"<>]+")
_TELEGRAM = re.compile(r"(/bot)\d+:[A-Za-z0-9_-]+")
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")
_STRIPE = re.compile(r"\b((?:sk|pk|rk|whsec)_(?:live|test)?_?)[A-Za-z0-9]{8,}")


def redact(x) -> str:
    """ข้อความเดิม (หรือ str ของ exception) โดยแทนค่าความลับด้วย *** — ตัดความยาวหลังเรียกได้ปลอดภัย"""
    s = str(x)
    s = _QUERY.sub(r"\1***", s)
    s = _TELEGRAM.sub(r"\1***", s)
    s = _BEARER.sub(r"\1***", s)
    s = _STRIPE.sub(r"\1***", s)
    return s
