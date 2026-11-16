"""กันการดึง URL ที่ชี้เข้าเครือข่ายภายใน (SSRF) — ใช้กับทุกที่ที่ระบบไปเปิดเว็บที่คนนอกส่งมา

ใช้งาน:
    from . import netguard
    with netguard.client(follow_redirects=True, timeout=20, headers=...) as c:
        c.get(url)            # ทุก request รวมทุกจุด redirect ถูกตรวจปลายทางก่อนส่ง
    netguard.get(url, ...)    # แทน httpx.get

กติกา:
- รับเฉพาะ http/https
- host ที่เป็น IP ต้องเป็น IP สาธารณะ (ipaddress.is_global) — กัน 127.x, 10.x, 172.16-31.x, 192.168.x,
  169.254.x (metadata ของ cloud), ::1, fc00::/7, fe80::/10, IPv4-mapped IPv6 ฯลฯ
- host ที่เป็นชื่อ: resolve ก่อน ทุก address ที่ได้ต้องเป็นสาธารณะ
- resolve ไม่ได้ → ปล่อยผ่าน (เชื่อมต่อจริงก็จะล้มเอง) — ทำให้เทสต์ที่ใช้ MockTransport กับโดเมนสมมติยังทำงาน

ข้อจำกัดที่รู้ (ดู docs/rebuild/STATUS.md):
- DNS rebinding: ตรวจตอน resolve แล้ว httpx resolve อีกรอบตอนต่อจริง ผู้ควบคุม DNS ที่ตอบสลับเร็วมากอาจหลุดได้
- ปิดทั้งหมดได้ด้วย env GEO_NETGUARD=0 (สำหรับเหตุฉุกเฉินเท่านั้น)
"""
from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit


class BlockedURL(Exception):
    """ปลายทางไม่อนุญาต — ผู้เรียกจับเป็นข้อผิดพลาดเครือข่ายแบบเดียวกับเว็บเปิดไม่ได้"""


def enabled() -> bool:
    return (os.getenv("GEO_NETGUARD") or "1").strip().lower() not in ("0", "false", "no", "off")


def _ip_ok(ip: ipaddress._BaseAddress) -> bool:
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return bool(ip.is_global) and not ip.is_multicast


def _parse_ip(host: str):
    h = host.strip("[]")
    try:
        return ipaddress.ip_address(h)
    except ValueError:
        pass
    # รูปแบบ IPv4 แปลก ๆ ที่ระบบปฏิบัติการยอม เช่น 2130706433, 0x7f.1, 017700000001
    try:
        return ipaddress.ip_address(socket.inet_aton(h))
    except (OSError, ValueError):
        return None


def check_host(host: str) -> None:
    """ยก BlockedURL ถ้า host ชี้ไปที่อยู่ที่ไม่ใช่สาธารณะ"""
    host = (host or "").strip().rstrip(".").lower()
    if not host:
        raise BlockedURL("ไม่มีชื่อโฮสต์")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local") or host.endswith(".internal"):
        raise BlockedURL(f"โฮสต์ภายใน: {host}")
    ip = _parse_ip(host)
    if ip is not None:
        if not _ip_ok(ip):
            raise BlockedURL(f"IP ไม่ใช่สาธารณะ: {ip}")
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return                                   # resolve ไม่ได้ → การเชื่อมต่อจริงก็จะล้ม
    for info in infos:
        addr = info[4][0].split("%")[0]
        try:
            a = ipaddress.ip_address(addr)
        except ValueError:
            raise BlockedURL(f"ที่อยู่อ่านไม่ออก: {addr}")
        if not _ip_ok(a):
            raise BlockedURL(f"{host} ชี้ไปที่อยู่ภายใน: {a}")


def literal_ok(host: str) -> bool:
    """ตรวจเร็วแบบไม่ resolve DNS: False ถ้า host เป็นชื่อภายใน หรือเป็น IP ที่ไม่ใช่สาธารณะ"""
    if not enabled():
        return True
    h = (host or "").strip().rstrip(".").lower()
    if h == "localhost" or h.endswith((".localhost", ".local", ".internal")):
        return False
    ip = _parse_ip(h)
    return ip is None or _ip_ok(ip)


def check_url(url: str) -> None:
    """ยก BlockedURL ถ้า URL ใช้ scheme อื่น หรือชี้เข้าเครือข่ายภายใน · ไม่ทำอะไรถ้าปิด guard"""
    if not enabled():
        return
    p = urlsplit(str(url))
    if p.scheme.lower() not in ("http", "https"):
        raise BlockedURL(f"scheme ไม่อนุญาต: {p.scheme}")
    check_host(p.hostname or "")


def is_public_url(url: str) -> bool:
    try:
        check_url(url)
        return True
    except BlockedURL:
        return False


def _on_request(request) -> None:
    check_url(str(request.url))


def client(**kw):
    """httpx.Client ที่ตรวจปลายทางทุก request (รวม redirect) · รับ kwargs เหมือน httpx.Client"""
    import httpx
    hooks = dict(kw.pop("event_hooks", None) or {})
    hooks["request"] = [_on_request] + list(hooks.get("request") or [])
    return httpx.Client(event_hooks=hooks, **kw)


def get(url: str, *, follow_redirects: bool = False, timeout=10, headers=None, **kw):
    """แทน httpx.get สำหรับ URL จากภายนอก"""
    with client(follow_redirects=follow_redirects, timeout=timeout, headers=headers) as c:
        return c.get(url, **kw)
