"""สร้าง PromptPay QR (มาตรฐาน EMVCo) ในโค้ดเอง — ไม่ต้องใช้ payment gateway
รองรับ proxy แบบเบอร์มือถือ (10 หลัก) และเลขประจำตัว/เลขภาษี (13 หลัก)
"""
from __future__ import annotations
import io
import base64

import qrcode


def _tlv(tag: str, val: str) -> str:
    return f"{tag}{len(val):02d}{val}"


def _crc16(s: str) -> str:
    crc = 0xFFFF
    for ch in s.encode("ascii"):
        crc ^= ch << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def _proxy(ppid: str) -> str:
    d = "".join(c for c in (ppid or "") if c.isdigit())
    if len(d) == 13:  # เลขประจำตัวประชาชน / เลขภาษี
        return _tlv("02", d)
    # เบอร์มือถือ -> 13 หลัก (0066 + 9 หลักท้าย)
    if d.startswith("0"):
        d = d[1:]
    if not d.startswith("66"):
        d = "66" + d
    return _tlv("01", ("0000000000000" + d)[-13:])


def payload(ppid: str, amount=None) -> str:
    merchant = _tlv("00", "A000000677010111") + _proxy(ppid)
    parts = [_tlv("00", "01"), _tlv("01", "11"), _tlv("29", merchant), _tlv("53", "764")]
    if amount is not None:
        parts.append(_tlv("54", f"{float(amount):.2f}"))
    parts.append(_tlv("58", "TH"))
    s = "".join(parts) + "6304"
    return s + _crc16(s)


def qr_data_uri(ppid: str, amount=None) -> str:
    """คืน data: URI ของ QR PNG (ฝังใน <img> ได้เลย)"""
    img = qrcode.make(payload(ppid, amount))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
