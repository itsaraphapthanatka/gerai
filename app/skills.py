"""ทักษะของเอเจนต์ (skills) — คู่มือความเชี่ยวชาญระดับมืออาชีพต่อบทบาท (Markdown ใน app/skills/)
ใช้เป็น system prompt ของงานที่เอเจนต์นั้นให้โมเดลทำ (ทัก/โพสต์/เคสศึกษา/คอนเทนต์) และเป็นเช็กลิสต์ที่คนอ่านได้
เจ้าของแก้ทับได้จากหน้าแอดมิน (เก็บใน settings skill_<name>) โดยไม่ต้อง deploy · กดคืนค่าเริ่มต้นได้"""
from __future__ import annotations
import hashlib
from pathlib import Path

from . import db

DIR = Path(__file__).parent / "skills"
AGENTS = {
    "ceo": "CEO — ขายและคัดลูกค้า",
    "cfo": "CFO — การเงินและกรอบความเสี่ยง",
    "cto": "CTO — ความน่าเชื่อถือของระบบ",
    "cmo": "CMO — การตลาดและคอนเทนต์",
    "prospecting": "Prospecting — หาลูกค้าเป้าหมาย",
    "delivery": "Delivery/QA — ติดตั้ง GEO และคุณภาพคอนเทนต์",
}
# งานของโมเดล → ทักษะที่แนบเป็น system prompt
TASK_SKILL = {"sales": "ceo", "marketing": "cmo", "content": "delivery"}


def default(name: str) -> str:
    p = DIR / f"{name}.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def load(name: str) -> str:
    """ข้อความทักษะที่ใช้งานจริง: ที่เจ้าของแก้ทับใน settings ก่อน ไม่งั้นไฟล์ค่าเริ่มต้น"""
    override = db.get_setting(f"skill_{name}")
    return override if override and override.strip() else default(name)


def is_overridden(name: str) -> bool:
    o = db.get_setting(f"skill_{name}")
    return bool(o and o.strip() and o.strip() != default(name).strip())


def save(name: str, text: str) -> None:
    if name not in AGENTS:
        raise ValueError("ไม่รู้จักทักษะนี้")
    text = (text or "").strip()
    db.set_setting(f"skill_{name}", "" if (not text or text == default(name).strip()) else text[:20000])


def reset(name: str) -> None:
    db.set_setting(f"skill_{name}", "")


def system_for(task: str) -> str:
    """system prompt สำหรับงานของโมเดล (ว่าง = ไม่มีทักษะผูก)"""
    name = TASK_SKILL.get(task)
    return load(name) if name else ""


def with_skill(task: str, messages: list[dict]) -> list[dict]:
    """แนบทักษะเป็นข้อความ system ไว้หน้าสุด (ถ้ายังไม่มี system)"""
    sk = system_for(task)
    if not sk or any(m.get("role") == "system" for m in messages):
        return messages
    return [{"role": "system", "content": sk}] + list(messages)


def summary() -> list[dict]:
    out = []
    for name, label in AGENTS.items():
        text = load(name)
        out.append({"name": name, "label": label, "chars": len(text), "overridden": is_overridden(name),
                    "version": hashlib.sha1(text.encode("utf-8")).hexdigest()[:8],
                    "tasks": [t for t, s in TASK_SKILL.items() if s == name]})
    return out
