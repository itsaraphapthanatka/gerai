"""auto สร้างร่างคอนเทนต์ — กติกาว่ารอบนี้ "เขียน / เติมคำถาม / รอ" และข้อความสถานะให้หน้าเว็บ

หลักการ: เขียนให้เฉพาะคำถามที่ *วัดแล้ว* ว่าแบรนด์ยังไม่โผล่และยังไม่มีคอนเทนต์ (gap ระดับ critical)
เมื่อปิดครบทุกข้อแล้ว ระบบไม่หยุดเงียบ — ให้ AI คิดคำถามใหม่เพิ่ม (TOPUP_N ข้อ/รอบ ไม่ซ้ำของเดิม)
รอมอนิเตอร์รอบหน้าวัดก่อน แล้วค่อยเขียนให้เฉพาะข้อที่ยังไม่โผล่ — ฟังก์ชันในนี้ไม่แตะ DB/เครือข่าย เพื่อให้ทดสอบได้
"""
from __future__ import annotations
import datetime
import re

TOPUP_N = 3        # เติมคำถามใหม่กี่ข้อต่อรอบ (= จังหวะ 3 ชิ้น/สัปดาห์พอดี)
TOPUP_MAX_Q = 40   # เพดานคำถามต่อแบรนด์ — มอนิเตอร์ทุกรอบถามทุกข้อกับทุก engine ยิ่งเยอะยิ่งแพง
SIM_THRESHOLD = 0.5  # trigram Jaccard ≥ นี้ = ถือว่าซ้ำ (ไทยไม่มีช่องว่างคั่นคำ ใช้ตัวอักษรแทนคำ)
MIN_LEN, MAX_LEN = 8, 200

WAIT, OK, WARN = "wait", "ok", "warn"


def _fmt(dt: datetime.datetime | None) -> str:
    return dt.isoformat(timespec="minutes").replace("T", " ") if dt else ""


def spacing_seconds(weekly: int) -> int:
    """เว้นระยะระหว่างร่าง auto เพื่อกระจายให้ทั่วสัปดาห์ — 3 ชิ้น → ทุก 2 วัน, 2 ชิ้น → ทุก 3 วัน"""
    return max(1, 7 // max(1, weekly)) * 86400


def time_gate(weekly: int, n_week: int, last_auto: str | None, now: datetime.datetime) -> dict | None:
    """ด่านเวลา — คืน None ถ้าถึงเวลาทำอะไรสักอย่าง ไม่งั้นคืน dict บอกว่ารออะไร"""
    if weekly <= 0:
        return {"action": "off", "tone": WAIT, "text": "ปิดอยู่"}
    if n_week >= weekly:
        return {"action": "cap", "tone": OK, "text": f"สัปดาห์นี้สร้างครบ {weekly} ชิ้นแล้ว"}
    if last_auto:
        try:
            nxt = datetime.datetime.fromisoformat(last_auto) + datetime.timedelta(seconds=spacing_seconds(weekly))
            if now < nxt:
                return {"action": "spacing", "tone": OK, "next_at": nxt, "text": f"รอบถัดไป {_fmt(nxt)}"}
        except Exception:
            pass
    return None


def queue_state(gaps: dict, quota_ok: bool, next_run_at: datetime.datetime | None = None) -> dict:
    """สถานะคิว (ไม่สนเวลา): รอบที่ถึงเวลาแล้วจะ write / topup / หรือติดอะไร — gaps = db.get_content_gaps()"""
    q_total = gaps.get("q_total", 0)
    if not gaps.get("has_run"):
        return {"action": "no_run", "tone": WARN,
                "text": "ยังไม่เคยมอนิเตอร์ — ระบบต้องวัดก่อนว่าแบรนด์ยังไม่โผล่ข้อไหน แล้วค่อยเขียน (กด “รันมอนิเตอร์”)"}
    crit = [g for g in gaps.get("gaps", []) if g["level"] == "critical"]
    if crit:
        if not quota_ok:
            return {"action": "quota", "tone": WARN,
                    "text": f"มีคำถามรอเขียน {len(crit)} ข้อ แต่โควตาคอนเทนต์เดือนนี้เต็ม — เริ่มใหม่เดือนหน้า"}
        g = crit[0]
        return {"action": "write", "tone": OK, "gap": g,
                "text": f"คิวถัดไป «{(g['question'] or '')[:70]}» · ยังไม่โผล่และยังไม่มีคอนเทนต์อีก {len(crit)} ข้อ"}
    unmeasured = [g for g in gaps.get("gaps", []) if g["level"] == "unmeasured"]
    if unmeasured:
        when = f" ({_fmt(next_run_at)})" if next_run_at else ""
        return {"action": "await_measure", "tone": OK, "n": len(unmeasured),
                "text": f"ปิดครบทุกคำถามที่วัดแล้ว · คำถามใหม่ {len(unmeasured)} ข้อรอมอนิเตอร์รอบหน้า{when} วัดก่อน "
                        f"— จะเขียนให้เฉพาะข้อที่ยังไม่โผล่"}
    if not quota_ok:
        return {"action": "quota", "tone": WARN,
                "text": f"ปิดครบทุกคำถามแล้ว ({q_total} ข้อ) แต่โควตาคอนเทนต์เดือนนี้เต็ม — เริ่มใหม่เดือนหน้า"}
    if q_total >= TOPUP_MAX_Q:
        return {"action": "max_q", "tone": WARN,
                "text": f"ปิดครบทุกคำถามแล้ว ({q_total} ข้อ) — ถึงเพดาน {TOPUP_MAX_Q} คำถาม ระบบไม่คิดเพิ่ม "
                        f"ลบคำถามที่ไม่ต้องการออกถ้าอยากให้คิดใหม่"}
    return {"action": "topup", "tone": OK, "n": TOPUP_N,
            "text": f"ปิดครบทุกคำถามแล้ว ({q_total} ข้อ) — รอบถัดไประบบจะคิดคำถามใหม่เพิ่ม {TOPUP_N} ข้อ "
                    f"ให้มอนิเตอร์วัดก่อน แล้วเขียนต่อ"}


def decide(weekly: int, n_week: int, last_auto: str | None, gaps: dict, quota_ok: bool,
           now: datetime.datetime, next_run_at: datetime.datetime | None = None) -> dict:
    """รอบนี้ทำอะไร — ด่านเวลาก่อน แล้วค่อยดูคิว"""
    return time_gate(weekly, n_week, last_auto, now) or queue_state(gaps, quota_ok, next_run_at)


def status(weekly: int, n_week: int, last_auto: str | None, gaps: dict, quota_ok: bool,
           now: datetime.datetime, next_run_at: datetime.datetime | None = None,
           last_item_at: str | None = None) -> dict:
    """ข้อความให้หน้าคอนเทนต์: ความคืบหน้าสัปดาห์นี้ + สถานะคิว (แยกกัน เพราะด่านเวลาไม่ควรบังว่า ‘ปิดครบแล้ว’)"""
    bits = [f"สัปดาห์นี้สร้างแล้ว {n_week}/{weekly} ชิ้น"]
    if last_item_at:
        bits.append(f"ชิ้นล่าสุด {str(last_item_at)[:16].replace('T', ' ')}")
    gate = time_gate(weekly, n_week, last_auto, now)
    if gate and gate["action"] == "spacing":
        bits.append(gate["text"])
    return {"progress": " · ".join(bits), "queue": queue_state(gaps, quota_ok, next_run_at)}


def next_monitor_at(last_run_at: str | None, interval_days: int | None, time_str: str | None,
                    now: datetime.datetime) -> datetime.datetime | None:
    """รอบมอนิเตอร์ถัดไปของแบรนด์ — กติกาเดียวกับตัวตั้งเวลาในแอป (วันล่าสุด + interval เวลา HH:MM)"""
    if not interval_days or interval_days <= 0:
        return None
    if not last_run_at:
        return now  # ไม่เคยรัน → ตัวตั้งเวลารันทันที
    try:
        last = datetime.datetime.fromisoformat(last_run_at)
        hh, mm = (time_str or "08:00").split(":")
        hh, mm = max(0, min(23, int(hh))), max(0, min(59, int(mm)))
    except Exception:
        return None
    return (last + datetime.timedelta(days=interval_days)).replace(hour=hh, minute=mm, second=0, microsecond=0)


# ---- กรองคำถามใหม่ไม่ให้ซ้ำของเดิม ----
_PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)


def _norm(s: str) -> str:
    # ตัดช่องว่าง/เครื่องหมาย เหลือตัวอักษร-ตัวเลข (ไทยยังอยู่ครบ) แล้ว lower
    return _PUNCT.sub("", (s or "")).lower()


def _grams(s: str, k: int = 3) -> set:
    return {s[i:i + k] for i in range(len(s) - k + 1)} if len(s) >= k else ({s} if s else set())


def similar(a: str, b: str, threshold: float = SIM_THRESHOLD) -> bool:
    """ซ้ำ/ใกล้เคียง? — อันหนึ่งซ้อนในอีกอัน หรือ trigram Jaccard ≥ threshold"""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    ga, gb = _grams(na), _grams(nb)
    return len(ga & gb) / max(1, len(ga | gb)) >= threshold


def pick_new(candidates: list[dict], existing: list[str], n: int) -> list[dict]:
    """คัดคำถามที่ AI เสนอ — ตัดที่สั้น/ยาวเกิน, ซ้ำของเดิม, ซ้ำกันเอง · คืนไม่เกิน n (คงลำดับที่ AI ให้)"""
    out: list[dict] = []
    seen = list(existing)
    for c in candidates:
        text = str(c.get("question") or "").strip()
        if not (MIN_LEN <= len(text) <= MAX_LEN):
            continue
        if any(similar(text, s) for s in seen):
            continue
        lang = c.get("lang") if c.get("lang") in ("th", "en") else ("th" if re.search(r"[฀-๿]", text) else "en")
        out.append({"question": text, "lang": lang})
        seen.append(text)
        if len(out) >= n:
            break
    return out
