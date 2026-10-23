"""SMS ถึงลีดที่มีแต่เบอร์มือถือ (ไม่มีอีเมล ไม่มี LINE) — ผู้ให้บริการ ThaiBulkSMS (api-v2)
ใช้เป็นช่องทางสุดท้ายของ pipeline._send_text: อีเมล → LINE → SMS
กติกา: ส่งเฉพาะมือถือไทย (06/08/09) · 08:00–20:00 เวลาไทยเท่านั้น (นอกเวลา → ค้างไว้ส่งรอบถัดไป) · ข้อความสั้น มีชื่อผู้ส่ง ลิงก์ และลิงก์ไม่รับ
· เพดานต่อวัน (sms_daily) · คีย์เข้ารหัสเก็บใน settings"""
from __future__ import annotations
import datetime as dt
import json
import re
from zoneinfo import ZoneInfo

from . import db, wp_client

API = "https://api-v2.thaibulksms.com"
BKK = ZoneInfo("Asia/Bangkok")
HOURS = (8, 20)                  # ส่งได้ 08:00–19:59 เวลาไทย
DAILY_DEFAULT = 30
MAX_CHARS = 200                  # ไทย UCS-2 ≈ 67 ตัว/ท่อน → ไม่เกิน 3 ท่อน
POST = None                      # เทสต์ฉีด post(url, data, auth) → (status, json)


class QuietHours(Exception):
    """นอกเวลาส่ง — ให้ข้อเสนอค้างไว้ แล้วรอบถัดไป (สรุปเช้า 08:00 / ติดตามผล 10:00) ส่งให้"""


def _get(k: str) -> str:
    v = db.get_setting(k) or ""
    try:
        return wp_client.decrypt(v) if v else ""
    except Exception:
        return ""


def config() -> dict | None:
    key, secret = _get("sms_api_key"), _get("sms_api_secret")
    if not (key and secret):
        return None
    return {"key": key, "secret": secret, "sender": (db.get_setting("sms_sender") or "").strip()}


def available() -> bool:
    return config() is not None and (db.get_setting("sms_on") or "1") == "1"


def save_config(key: str, secret: str, sender: str, daily: str, on: bool, prospects: bool = False) -> None:
    """เว้นว่าง = คงค่าเดิม"""
    if key.strip():
        db.set_setting("sms_api_key", wp_client.encrypt(key.strip()))
    if secret.strip():
        db.set_setting("sms_api_secret", wp_client.encrypt(secret.strip()))
    db.set_setting("sms_sender", re.sub(r"[^A-Za-z0-9 .\-]", "", sender or "")[:11])
    try:
        db.set_setting("sms_daily", str(max(0, min(500, int(daily or DAILY_DEFAULT)))))
    except ValueError:
        pass
    db.set_setting("sms_on", "1" if on else "0")
    db.set_setting("sms_prospects", "1" if prospects else "0")


def daily_cap() -> int:
    try:
        return int(db.get_setting("sms_daily") or DAILY_DEFAULT)
    except ValueError:
        return DAILY_DEFAULT


def sent_today() -> int:
    try:
        d = json.loads(db.get_setting("sms_count") or "{}")
    except Exception:
        d = {}
    return int(d.get("n", 0)) if d.get("day") == dt.datetime.now(BKK).date().isoformat() else 0


def _count_up() -> None:
    db.set_setting("sms_count", json.dumps({"day": dt.datetime.now(BKK).date().isoformat(), "n": sent_today() + 1}))


def in_hours(now: dt.datetime | None = None) -> bool:
    h = (now or dt.datetime.now(BKK)).astimezone(BKK).hour
    return HOURS[0] <= h < HOURS[1]


# ---------- เบอร์ ----------
def mobile(s: str) -> str:
    """เบอร์มือถือไทยตัวแรกในข้อความ → 0812345678 ('' = ไม่มี)"""
    for m in re.finditer(r"(?:\+?66[\s\-]?|0)[689]\d[\s\-]?\d{3}[\s\-]?\d{4}(?!\d)", s or ""):
        d = re.sub(r"\D", "", m.group(0))
        d = "0" + d[2:] if d.startswith("66") else d
        if re.fullmatch(r"0[689]\d{8}", d):
            return d
    return ""


def prospects_allowed() -> bool:
    """ส่งถึงลีดที่ระบบหาเอง (เบอร์จากเว็บ ไม่ได้ให้เราเอง) — ปิดไว้ก่อน: SMS โฆษณาถึงเบอร์ที่ไม่ได้ยินยอมเสี่ยงตามกฎ กสทช./PDPA"""
    return (db.get_setting("sms_prospects") or "0") == "1"


def lead_mobile(lead) -> str:
    """มือถือของลีดที่ส่ง SMS ได้: ช่องติดต่อก่อน → เบอร์ที่บทโทรหาได้จากเว็บ
    ลีดที่ไม่ได้ให้ช่องทางเอง (หาเอง/นำเข้า ไม่ได้ติ๊กยินยอม) ส่งได้เฉพาะเมื่อเจ้าของเปิด sms_prospects"""
    if not lead["consent"] and (lead["source"] or "") in ("prospect", "import", "manual") and not prospects_allowed():
        return ""
    m = mobile(lead["contact"] or "")
    if m:
        return m
    try:
        d = json.loads(lead["call_json"] or "{}") if "call_json" in lead.keys() and lead["call_json"] else {}
    except Exception:
        d = {}
    return mobile(d.get("phone") or "")


def to_msisdn(p: str) -> str:
    return "66" + p[1:] if p.startswith("0") else p


# ---------- ข้อความ ----------
def compact(subject: str, body: str, unsub: str) -> str:
    """อีเมล/ข้อความยาว → SMS สั้น: หัวข้อ + ลิงก์แรกในเนื้อหา + ลิงก์ไม่รับ"""
    links = re.findall(r"https?://\S+", body or "")
    link = next((l for l in links if "/u/" not in l), "")
    head = re.sub(r"\s+", " ", subject or "").strip()
    room = MAX_CHARS - len("เจอ.AI: ") - len(link) - len(" ไม่รับ: ") - len(unsub) - 2
    if len(head) > room:
        head = head[:max(10, room - 1)] + "…"
    return f"เจอ.AI: {head} {link} ไม่รับ: {unsub}".replace("  ", " ").strip()


# ---------- ส่ง ----------
def _post(url: str, data: dict, auth: tuple):
    if POST:
        return POST(url, data, auth)
    import httpx
    r = httpx.post(url, data=data, auth=auth, timeout=20)
    try:
        js = r.json()
    except Exception:
        js = {"raw": r.text[:200]}
    return r.status_code, js


def send(phone: str, text: str) -> str:
    """ส่ง 1 ข้อความ · คืนข้อความผลสั้น ๆ · โยน QuietHours นอกเวลา · โยน RuntimeError เมื่อส่งไม่ได้"""
    cfg = config()
    if not cfg:
        raise RuntimeError("ยังไม่ได้ตั้งค่า SMS")
    if not in_hours():
        raise QuietHours("นอกเวลาส่ง SMS (08:00–20:00)")
    if sent_today() >= daily_cap():
        raise RuntimeError(f"ครบเพดาน SMS วันนี้ {daily_cap()} ข้อความ")
    p = mobile(phone)
    if not p:
        raise RuntimeError("ไม่ใช่เบอร์มือถือไทย")
    data = {"msisdn": to_msisdn(p), "message": text[:MAX_CHARS + 60]}
    if cfg["sender"]:
        data["sender"] = cfg["sender"]
    status, js = _post(f"{API}/sms", data, (cfg["key"], cfg["secret"]))
    bad = js.get("bad_phone_number_list") if isinstance(js, dict) else None
    if isinstance(js, dict):
        ok = 200 <= status < 300 and not bad and bool(js.get("phone_number_list") or js.get("status") in ("success", "ok")
                                                     or js.get("message_id") or (js.get("data") or {}).get("message_id"))
    else:
        ok = False
    if not ok:
        err = (js.get("error") or {}) if isinstance(js, dict) else {}
        why = (err.get("description") or err.get("name") or (bad and "เบอร์ไม่ถูกต้อง") or js.get("message") or js.get("raw") or status) if isinstance(js, dict) else status
        raise RuntimeError(f"ThaiBulkSMS ปฏิเสธ: {str(why)[:140]}")
    _count_up()
    return f"ส่ง SMS ถึง {p[:3]}-{p[3:6]}-{p[6:]} แล้ว"


def balance() -> str:
    cfg = config()
    if not cfg:
        return ""
    try:
        import httpx
        r = httpx.get(f"{API}/credit", auth=(cfg["key"], cfg["secret"]), timeout=15)
        js = r.json()
        if r.status_code >= 300:
            return f"เช็กเครดิตไม่ได้ ({(js.get('error') or {}).get('description') or r.status_code})"
        bal = js.get("remaining_credit") if "remaining_credit" in js else (js.get("credit") or js.get("data"))
        return f"เครดิตคงเหลือ {bal}"
    except Exception as e:
        return f"เช็กเครดิตไม่ได้ ({type(e).__name__})"


def public_config() -> dict:
    cfg = config()
    return {"ready": bool(cfg), "on": (db.get_setting("sms_on") or "1") == "1", "sender": (cfg or {}).get("sender", "") or (db.get_setting("sms_sender") or ""),
            "key_preview": ("••••" + cfg["key"][-4:]) if cfg else "", "daily": daily_cap(), "today": sent_today(), "prospects": prospects_allowed()}
