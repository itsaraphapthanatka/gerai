"""เกราะระดับคำขอ: IP จริงของผู้ใช้หลัง proxy · จำกัดการลองล็อกอิน · กันคำขอข้ามเว็บ (CSRF) · ตรวจไฟล์สลิป

ทุกอย่างในไฟล์นี้ไม่แตะฐานข้อมูลและไม่ผูกกับ FastAPI app — main.py เป็นคนเรียก
ตัวนับการลองล็อกอินอยู่ในหน่วยความจำของโปรเซส: แอปรัน uvicorn โปรเซสเดียว (ดู Dockerfile)
ถ้าวันหนึ่งรันหลาย worker ต้องย้ายไปเก็บในฐาน ไม่งั้นแต่ละ worker นับแยกกัน
"""
from __future__ import annotations
import os
import threading
import time
from collections import deque
from urllib.parse import urlsplit


# ---------- IP ของผู้ใช้ ----------
def _hops() -> int:
    try:
        return max(0, int(os.getenv("TRUSTED_PROXY_HOPS", "1")))
    except ValueError:
        return 1


def client_ip(request) -> str:
    """IP ที่ proxy ของเราเห็น ไม่ใช่ค่าที่ผู้ใช้พิมพ์มาเองใน X-Forwarded-For

    proxy แต่ละชั้น *ต่อท้าย* IP ที่มันเห็น ดังนั้นค่าขวาสุดมาจาก proxy ชั้นในสุดของเรา เชื่อได้
    ส่วนค่าซ้ายสุดคือสิ่งที่ผู้ใช้ส่งมา ปลอมได้ (เดิมใช้ค่าซ้ายสุด → เลี่ยงโควตาตรวจเว็บต่อ IP ได้ด้วย header เดียว)
    TRUSTED_PROXY_HOPS = จำนวน proxy ที่อยู่หน้าแอป (ดีฟอลต์ 1 = nginx หรือ cloudflared อย่างเดียว ·
    Cloudflare → nginx → แอป = 2 · 0 = ไม่มี proxy ไม่อ่าน header เลย)
    """
    peer = (request.client.host if request.client else "") or ""
    hops = _hops()
    parts = [p.strip() for p in (request.headers.get("x-forwarded-for") or "").split(",") if p.strip()]
    if hops == 0 or not parts:
        return peer[:64]
    return (parts[-hops] if len(parts) >= hops else parts[0])[:64]


# ---------- จำกัดจำนวนครั้ง ----------
class Limiter:
    """นับเหตุการณ์ต่อ key ในช่วงเวลาเลื่อน (sliding window) — เกิน limit = ต้องรอ"""

    def __init__(self, limit: int, window: int, max_keys: int = 20000):
        self.limit, self.window, self.max_keys = limit, window, max_keys
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float):
        dq = self._hits.get(key)
        if dq is None:
            return None
        while dq and dq[0] <= now - self.window:
            dq.popleft()
        if not dq:
            del self._hits[key]
            return None
        return dq

    def retry_after(self, key: str, now: float | None = None) -> int:
        """วินาทีที่ต้องรอ · 0 = ยังทำได้"""
        now = time.time() if now is None else now
        with self._lock:
            dq = self._prune(key, now)
            if dq is None or len(dq) < self.limit:
                return 0
            return max(1, int(dq[0] + self.window - now) + 1)

    def hit(self, key: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            if len(self._hits) >= self.max_keys:          # กันหน่วยความจำบวมจาก key ปลอมจำนวนมาก
                for k in list(self._hits):
                    self._prune(k, now)
                if len(self._hits) >= self.max_keys:
                    self._hits.clear()
            self._hits.setdefault(key, deque()).append(now)

    def clear(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


# ล็อกอินผิด: นับต่อ (IP + อีเมล) ให้แน่น และต่อ IP ให้หลวมกว่า
# ไม่นับต่ออีเมลอย่างเดียว — ไม่งั้นใครก็ล็อกบัญชีเจ้าของระบบได้ด้วยการเดารหัสผิดรัว ๆ จากที่ไหนก็ได้
LOGIN_WINDOW = 15 * 60
LOGIN_PAIR = Limiter(8, LOGIN_WINDOW)
LOGIN_IP = Limiter(40, LOGIN_WINDOW)


def login_wait(ip: str, email: str) -> int:
    return max(LOGIN_IP.retry_after(ip), LOGIN_PAIR.retry_after(f"{ip}|{email}"))


def login_failed(ip: str, email: str) -> None:
    LOGIN_IP.hit(ip)
    LOGIN_PAIR.hit(f"{ip}|{email}")


def login_ok(ip: str, email: str) -> None:
    LOGIN_PAIR.clear(f"{ip}|{email}")


# ---------- CSRF ----------
SESSION_COOKIE = "session"
_SAFE = {"GET", "HEAD", "OPTIONS"}


def _host(v: str) -> str:
    v = (v or "").strip().lower()
    if "//" not in v:
        v = "//" + v
    try:
        return urlsplit(v).hostname or ""
    except ValueError:
        return ""


def _own_hosts(request) -> set[str]:
    own = {_host(request.headers.get("host", "")), _host(request.headers.get("x-forwarded-host", ""))}
    for name in ("PUBLIC_BASE_URL", "GEO_BASE_URL"):
        own.add(_host(os.getenv(name, "")))
    for o in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(","):
        own.add(_host(o))
    own.discard("")
    return own


def cross_site_write(request) -> bool:
    """True = คำขอที่เปลี่ยนข้อมูล มาพร้อมคุกกี้ล็อกอิน แต่เบราว์เซอร์บอกว่าส่งมาจากเว็บอื่น → ต้องปฏิเสธ

    คุกกี้ session เป็น SameSite=Lax อยู่แล้ว (เบราว์เซอร์ปัจจุบันไม่แนบไปกับ POST ข้ามเว็บ) — นี่คือชั้นที่สอง
    สำหรับเบราว์เซอร์เก่าและเว็บอื่นใต้โดเมนแม่เดียวกัน (same-site แต่คนละ origin) ซึ่ง Lax ไม่กัน
    ไม่มีคุกกี้ล็อกอิน = ไม่มีอะไรให้ขโมยสิทธิ์ ปล่อยผ่าน (webhook, ฟอร์มสาธารณะ, WordPress plugin)
    ไม่มีทั้ง Sec-Fetch-Site และ Origin = ไม่ใช่เบราว์เซอร์ (curl, สคริปต์) ปล่อยผ่านเช่นกัน
    """
    if request.method in _SAFE or SESSION_COOKIE not in request.cookies:
        return False
    site = (request.headers.get("sec-fetch-site") or "").lower()
    if site in ("same-origin", "none"):
        return False
    origin = (request.headers.get("origin") or "").strip()
    if origin and origin.lower() != "null":
        return _host(origin) not in _own_hosts(request)
    return site == "cross-site"


# ---------- ไฟล์สลิป ----------
SLIP_MAX_BYTES = 8_000_000
SLIP_TYPES = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".heic": "image/heic", ".pdf": "application/pdf"}


def sniff_slip(data: bytes) -> str | None:
    """นามสกุลจากเนื้อไฟล์จริง (ไม่เชื่อชื่อไฟล์ที่ผู้ใช้ส่งมา) · None = ไม่ใช่รูป/PDF ที่รับ"""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"heif", b"mif1", b"msf1", b"hevc"):
        return ".heic"                                     # รูปจาก iPhone ที่ไม่ได้แปลงเป็น JPEG
    if data[:5] == b"%PDF-":
        return ".pdf"
    return None


async def read_slip(upload) -> tuple[bytes | None, str | None, str]:
    """อ่านไฟล์สลิปที่อัปโหลด → (ข้อมูล, นามสกุล, ข้อความผิดพลาด) · ไม่ได้แนบไฟล์ = (None, None, "")

    อ่านไม่เกิน SLIP_MAX_BYTES+1 ไบต์เข้าหน่วยความจำ — ไฟล์ใหญ่กว่านั้นถูกปฏิเสธ ไม่ใช่ตัดท้ายทิ้งเงียบ ๆ
    (เดิมอ่านทั้งไฟล์ก่อนแล้วค่อยตัด: ไฟล์ที่ถูกตัดเปิดไม่ได้ แต่ระบบบันทึกว่าลูกค้าแนบสลิปแล้ว)
    """
    if upload is None or not getattr(upload, "filename", ""):
        return None, None, ""
    data = await upload.read(SLIP_MAX_BYTES + 1)
    if not data:
        return None, None, ""
    if len(data) > SLIP_MAX_BYTES:
        return None, None, "ไฟล์สลิปใหญ่เกิน 8 MB ลองถ่ายภาพหน้าจอหรือย่อรูปแล้วส่งใหม่"
    ext = sniff_slip(data)
    if not ext:
        return None, None, "ไฟล์สลิปต้องเป็นรูป (JPG, PNG, WEBP, HEIC) หรือ PDF"
    return data, ext, ""
