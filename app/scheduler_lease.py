"""เจ้าของตัวตั้งเวลา (stage 18) — flag `scheduler_lease` (ปิด = แบบเดิม: ทุกโปรเซสที่ GEO_AUTORUN=1 วิ่งรอบของตัวเอง)

ปัญหาเดิม: รัน uvicorn หลาย worker หรือหลายเครื่องพร้อม GEO_AUTORUN=1 → ทุกโปรเซสวิ่งรอบมอนิเตอร์/คอนเทนต์/เผยแพร่ซ้ำกัน
เปิด flag: ใครถือ lease ("autorun") อยู่ถึงวิ่งรอบแบรนด์ได้ · lease หมดอายุ (เจ้าของตาย) → โปรเซสอื่นรับต่อเอง
คิวงานถาวร (jobs) ไม่ต้องพึ่ง lease เพราะ claim เป็น CAS อยู่แล้ว"""
from __future__ import annotations
import datetime as dt
import os
import socket
import uuid

from . import db, flags

TTL_S = 180
OWNER = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def on() -> bool:
    try:
        return flags.enabled("scheduler_lease")
    except Exception:
        return False


def _iso(d: dt.datetime) -> str:
    return d.isoformat(timespec="seconds")


def acquire(name: str = "autorun", owner: str = OWNER, ttl: int = TTL_S, now: dt.datetime | None = None) -> bool:
    """ถือหรือต่ออายุ lease · คืน True เมื่อเป็นเจ้าของ · flag ปิด = True เสมอ (แบบเดิม)"""
    if not on():
        return True
    now = now or dt.datetime.now()
    exp = _iso(now + dt.timedelta(seconds=ttl))
    with db.get_conn() as c:
        c.execute(db.q("INSERT INTO scheduler_leases(name, owner, expires_at, updated_at) VALUES(?,?,?,?) ON CONFLICT DO NOTHING"),
                  (name, owner, exp, _iso(now)))
        cur = c.execute(db.q("UPDATE scheduler_leases SET owner=?, expires_at=?, updated_at=? WHERE name=? AND (owner=? OR expires_at<?)"),
                        (owner, exp, _iso(now), name, owner, _iso(now)))
        return (cur.rowcount or 0) == 1


def release(name: str = "autorun", owner: str = OWNER) -> None:
    with db.get_conn() as c:
        c.execute(db.q("UPDATE scheduler_leases SET expires_at=? WHERE name=? AND owner=?"), ("1970-01-01T00:00:00", name, owner))


def holder(name: str = "autorun") -> dict | None:
    with db.get_conn() as c:
        r = c.execute(db.q("SELECT * FROM scheduler_leases WHERE name=?"), (name,)).fetchone()
    return dict(r) if r else None
