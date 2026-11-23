"""กันแอปถูกใช้ยิงเข้าเครือข่ายภายใน (SSRF)

เราดึงเว็บตามที่อยู่ที่คนนอกกรอกเอง (หน้าตรวจเว็บฟรี /audit ไม่ต้องล็อกอิน) — ถ้าไม่กัน เขากรอก
127.0.0.1, 169.254.169.254 หรือ host.docker.internal:4000 แล้วอ่านผลจากรายงานได้

สองชั้น:
- literal_blocked(host)  ดูจากตัวหนังสืออย่างเดียว ไม่แตะเครือข่าย — ใช้ตอนรับค่าจากฟอร์ม
- host_public(host)      resolve ชื่อจริง ทุก IP ที่ได้ต้องเป็น IP สาธารณะ — ใช้ก่อนดึงทุกครั้ง
                         (รวมทุกจังหวะของ redirect ผ่าน client() / urlopen() ด้านล่าง)

ชื่อที่ resolve ไม่ได้ถือว่า "ผ่าน" เพราะต่อไม่ติดอยู่แล้ว และปล่อยให้โค้ดเดิมรายงานว่าเว็บล่มตามปกติ

ข้อจำกัดที่รู้: ตรวจ IP ตอน resolve แล้วไลบรารี HTTP resolve อีกรอบตอนต่อจริง — DNS ที่สลับคำตอบ
ระหว่างสองจังหวะ (DNS rebinding) ยังลอดได้ ต้องกันที่ชั้นเครือข่ายของเซิร์ฟเวอร์อีกชั้น
"""
import ipaddress
import socket
import urllib.request
from urllib.parse import urlparse

# ชื่อที่ใช้กันเฉพาะในเครือข่ายภายใน — ไม่มีเว็บลูกค้าจริงอยู่ใต้ชื่อเหล่านี้
_INTERNAL_SUFFIXES = (".internal", ".local", ".localhost", ".localdomain", ".lan", ".home", ".intranet", ".corp")


class BlockedHost(OSError):
    """ปลายทางไม่ใช่ที่อยู่สาธารณะ — เป็น OSError ให้โค้ดที่จับ "ต่อไม่ติด" อยู่แล้วจับได้เหมือนกัน"""


def _ip_public(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])          # ตัด zone id ของ IPv6 (fe80::1%eth0)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:                        # ::ffff:127.0.0.1 = 127.0.0.1
        ip = ip.ipv4_mapped
    return ip.is_global


def _clean(host: str | None) -> str:
    return (host or "").strip().strip("[]").rstrip(".").lower()


def literal_blocked(host: str | None) -> bool:
    """True = ห้ามดึง ดูจากตัวหนังสือ: IP ภายใน, เลขรูปแบบแปลก (127.1, 0x7f.0.0.1, 2130706433), ชื่อภายใน"""
    h = _clean(host)
    if not h:
        return True
    try:
        ipaddress.ip_address(h.split("%", 1)[0])
        return not _ip_public(h)
    except ValueError:
        pass
    try:
        socket.inet_aton(h)            # รับรูปย่อ/ฐานแปด/ฐานสิบหกที่ ip_address ไม่รับ แต่ระบบปฏิบัติการต่อให้ได้
        return True
    except (OSError, UnicodeError):
        pass
    return "." not in h or h.endswith(_INTERNAL_SUFFIXES)


def host_public(host: str | None, resolve=None) -> bool:
    """True = ดึงได้: ไม่โดน literal_blocked และทุก IP ที่ชื่อนี้ชี้ไปเป็น IP สาธารณะ"""
    h = _clean(host)
    if literal_blocked(h):
        return False
    try:
        infos = (resolve or socket.getaddrinfo)(h, None, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        return True                    # resolve ไม่ได้ = ต่อไม่ติดอยู่แล้ว
    return all(_ip_public(str(i[4][0])) for i in infos)


def url_public(url: str) -> bool:
    try:
        return host_public(urlparse(url if "://" in url else "https://" + url).hostname)
    except ValueError:
        return False


def check(url: str) -> None:
    if not url_public(url):
        raise BlockedHost(f"blocked non-public address: {urlparse(url).hostname or url[:80]}")


def _on_request(request) -> None:
    check(str(request.url))


def client(**kw):
    """httpx.Client สำหรับดึงเว็บของคนนอก — ตรวจปลายทางก่อนส่งทุกคำขอ รวมทุกจังหวะของ redirect"""
    import httpx
    return httpx.Client(event_hooks={"request": [_on_request]}, **kw)


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_CheckedRedirects)


def urlopen(req, timeout: float = 18):
    """urllib.request.urlopen สำหรับดึงเว็บของคนนอก — ตรวจปลายทางและทุก redirect"""
    check(req.full_url if isinstance(req, urllib.request.Request) else str(req))
    return _opener.open(req, timeout=timeout)
