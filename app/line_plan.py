"""คุยต่อใน LINE จากรายงาน (stage 06A-1, flag line_appointments)

เส้นทาง: ปุ่ม "คุยต่อใน LINE" ในรายงาน → เปิดแชต OA พร้อมข้อความที่พิมพ์ไว้ให้ "ขอแผนจากรายงาน <โดเมน> · รหัส K7P2Q9"
→ ลูกค้ากดส่ง → webhook จับรหัส → ผูกบัญชี LINE นี้กับรายงาน + บันทึกคำขอแผน (plan_requests ตัวเดียวกับฟอร์ม)
→ บอทส่ง 3 เรื่องที่ควรแก้ + ลิงก์รายงาน → ถาม 2 ข้อด้วยปุ่มตอบเร็ว → ถามเรื่องรับข่าวสาร (แยกจากคำขอ ค่าเริ่มต้น = ไม่รับ)
→ "ทีมจะทักกลับในแชตนี้" + [ขอให้โทรหาแทน] → ขอเบอร์ในแชตเฉพาะคนที่กดเท่านั้น

หลัก:
- ไม่ส่ง token รายงานเข้า LINE · ใช้รหัสสั้นใช้ครั้งเดียว มีวันหมดอายุ · เดารหัสผิดหลายครั้ง = พักรับรหัสจาก LINE นั้น
- ไม่ขอ LINE ID / อีเมล · ขอเบอร์เฉพาะคนที่เลือกให้โทร
- ทีมพักบอทได้ทีละห้องแชต (คนในทีมตอบเอง บอทเงียบ)
- ข้อความแจ้งทีมไม่มีช่องทางติดต่อของลูกค้า (ดูในแอป)
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
import secrets
from urllib.parse import quote

from . import db, line_oa, plan_request as PR

CODE_TTL_HOURS = 72
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"          # ไม่มี 0 O 1 I L กันอ่านผิด
CODE_LEN = 6
MAX_FAILS = 5                                               # รหัสผิดเกินนี้ใน 1 ชม. = ไม่รับรหัสจาก LINE นั้นชั่วคราว
FAIL_WINDOW_MIN = 60
PAUSE_HOURS = 24
_CODE_RE = re.compile(r"รหัส\s*[:：]?\s*([A-Za-z0-9]{6})\b")
WEB_CHOICES = ("own_team", "agency", "none")
WIN_CHOICES = ("asap", "1m", "3m", "explore")
NOTICE = ("ข้อมูลที่ใช้: บัญชี LINE นี้และคำตอบในแชตนี้ ใช้ตอบคำขอแผนนี้เท่านั้น "
          "ถ้าไม่ต้องการให้ติดต่อแล้ว พิมพ์ \"หยุดติดต่อ\" ได้ทุกเมื่อ")          # ร่าง — ตรวจด้านกฎหมายพร้อมข้อความในฟอร์ม (NOTICE_VERSION)
STOP_WORDS = ("หยุดติดต่อ", "ไม่ต้องติดต่อ", "ยกเลิกคำขอ")


def _now() -> dt.datetime:
    return dt.datetime.now()


def _iso(t: dt.datetime) -> str:
    return t.isoformat(timespec="seconds")


def ready() -> bool:
    """มีของพอสร้างลิงก์จริง: คีย์ Messaging API + LINE ID ของ OA"""
    return line_oa.available() and bool(line_oa.oa_id())


def active(request=None) -> bool:
    from . import flags
    return flags.active("line_appointments", request) and ready()


# ---------- รหัสใช้ครั้งเดียว ----------
def _new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))


def code_for(lead) -> str:
    """รหัสที่ยังไม่หมดอายุของรายงานนี้ (ใช้ซ้ำ ไม่สร้างใหม่ทุกครั้งที่กด) · ไม่มี = สร้างใหม่"""
    now = _now()
    row = db.active_line_code(lead["id"], _iso(now + dt.timedelta(hours=1)))   # เหลือไม่ถึง 1 ชม. = ออกใหม่ ไม่ให้หมดระหว่างเปิด LINE
    if row:
        return row["code"]
    for _ in range(5):
        code = _new_code()
        try:
            db.add_line_code(code, lead["id"], _iso(now + dt.timedelta(hours=CODE_TTL_HOURS)))
            return code
        except Exception:
            continue                                         # ชนรหัสเดิม (โอกาสน้อยมาก) → สุ่มใหม่
    raise RuntimeError("สร้างรหัสไม่สำเร็จ")


def prefill_text(lead, code: str) -> str:
    return f"ขอแผนจากรายงาน {lead['domain']} · รหัส {code}"


def oa_message_url(text: str) -> str:
    """ลิงก์มาตรฐานของ LINE: เปิดแชต OA พร้อมข้อความที่พิมพ์ไว้ (ลูกค้ายังต้องกดส่งเอง) · ต้องทดสอบกับ OA จริงทั้งมือถือและคอมพิวเตอร์"""
    return f"https://line.me/R/oaMessage/{quote(line_oa.oa_id(), safe='')}/?{quote(text, safe='')}"


def add_friend_url() -> str:
    oid = line_oa.oa_id()
    return f"https://line.me/R/ti/p/{quote(oid, safe='')}" if oid else ""


# ---------- ข้อความ + ปุ่มตอบเร็ว ----------
def _text(t: str, quick: list[tuple[str, str]] | None = None) -> dict:
    m = {"type": "text", "text": t[:5000]}
    if quick:
        m["quickReply"] = {"items": [{"type": "action", "action": {"type": "postback", "label": lb[:20], "data": data, "displayText": lb[:300]}}
                                     for lb, data in quick[:13]]}
    return m


def _q_window() -> dict:
    return _text("ข้อ 1/2 · อยากเริ่มปรับเว็บเมื่อไร", [(PR.WINDOWS[k], f"jp:win:{k}") for k in WIN_CHOICES])


def _q_web() -> dict:
    return _text("ข้อ 2/2 · ตอนนี้ใครดูแลเว็บไซต์", [(PR.READINESS[k], f"jp:web:{k}") for k in WEB_CHOICES])


def _q_marketing() -> dict:
    return _text("อีกเรื่องหนึ่ง (ไม่บังคับ) อยากรับข่าวสารและบทความจากเจอ.AI ทาง LINE นี้ไหม ยกเลิกได้ทุกเมื่อ",
                 [("รับข่าวสาร", "jp:mkt:1"), ("ไม่รับ", "jp:mkt:0")])


def _done_msg() -> dict:
    return _text("ขอบคุณครับ ทีมเจอ.AI จะทักกลับในแชตนี้ภายใน 1 วันทำการ เพื่อพาดูรายงานและสรุปว่าควรแก้อะไรก่อน "
                 "ถ้าสะดวกให้โทรคุยมากกว่า กดปุ่มด้านล่างได้", [("ขอให้โทรหาแทน", "jp:call:1")])


def _summary(lead) -> str:
    from . import pipeline, report_view, snapshot
    try:
        acts = report_view.build(lead, snapshot.view(lead))["actions"]
    except Exception:
        acts = []
    lines = [f"ได้รับคำขอแผนของ {lead['domain']} แล้วครับ ✓"]
    if acts:
        lines.append("\nเรื่องที่ควรแก้ก่อน (จากผลตรวจจริง):")
        lines += [f"{i}. {a['title']}" for i, a in enumerate(acts, 1)]
    lines.append(f"\nดูรายงาน: {pipeline.result_url(lead, 'line')}")
    return "\n".join(lines)


# ---------- webhook ----------
def find_code(text: str) -> str | None:
    m = _CODE_RE.search(text or "")
    return m.group(1).upper() if m else None


def paused(user_id: str) -> bool:
    s = db.get_line_session(user_id) if user_id else None
    return bool(s and s["paused_until"] and s["paused_until"] > _iso(_now()))


def set_pause(user_id: str, on: bool) -> None:
    db.save_line_session(user_id, paused_until=_iso(_now() + dt.timedelta(hours=PAUSE_HOURS)) if on else None)


def lead_bot(lead_id: int) -> dict:
    """สถานะบอทของห้องแชตที่ผูกกับลีดนี้ (ไว้แสดงในหน้าแอดมิน)"""
    users = db.line_users_of_lead(lead_id)
    until = [s["paused_until"] for s in (db.get_line_session(u) for u in users) if s and s["paused_until"] and s["paused_until"] > _iso(_now())]
    return {"linked": bool(users), "paused_until": max(until) if until else None, "hours": PAUSE_HOURS}


def set_lead_pause(lead_id: int, on: bool) -> int:
    users = db.line_users_of_lead(lead_id)
    for u in users:
        set_pause(u, on)
    return len(users)


def _too_many_fails(s) -> bool:
    if not s or not s["fail_at"]:
        return False
    recent = s["fail_at"] >= _iso(_now() - dt.timedelta(minutes=FAIL_WINDOW_MIN))
    return recent and (s["fails"] or 0) >= MAX_FAILS


def _fail(user_id: str, s) -> None:
    recent = bool(s and s["fail_at"] and s["fail_at"] >= _iso(_now() - dt.timedelta(minutes=FAIL_WINDOW_MIN)))
    db.save_line_session(user_id, fails=((s["fails"] or 0) + 1) if recent else 1, fail_at=_iso(_now()))


def redeem(user_id: str, code: str, reply_token: str | None) -> str:
    """ลูกค้าส่งรหัสจากรายงานมา · คืนชื่อกิ่ง (ไว้ log/ทดสอบ)"""
    s = db.get_line_session(user_id)
    if _too_many_fails(s):
        line_oa.reply(reply_token, ["ส่งรหัสไม่ถูกหลายครั้ง ลองใหม่อีกครั้งในอีก 1 ชั่วโมง หรือกดปุ่ม LINE จากหน้ารายงานอีกครั้ง"])
        return "code_locked"
    row = db.get_line_code(code)
    if row and row["used_by"] == user_id:                      # ส่งรหัสเดิมซ้ำจากบัญชีเดิม → ส่งสรุปให้อีกครั้ง ไม่สร้างคำขอซ้ำ
        lead = db.get_lead(row["lead_id"])
        if lead:
            line_oa.reply_messages(reply_token, [_text(_summary(lead)), _done_msg()])
            return "code_again"
    lead_id = db.redeem_line_code(code, user_id, _iso(_now()))
    if not lead_id:
        _fail(user_id, s)
        line_oa.reply(reply_token, ["รหัสนี้หมดอายุหรือถูกใช้ไปแล้ว กลับไปที่หน้ารายงานแล้วกดปุ่ม \"คุยต่อใน LINE\" อีกครั้งได้เลยครับ"])
        return "code_invalid"
    lead = db.get_lead(lead_id)
    if not lead:
        return "code_invalid"
    fill = {}
    if not lead["line_user_id"]:
        fill["line_user_id"] = user_id                        # ผูกเฉพาะลีดที่ยังไม่มี LINE · มีของคนอื่นแล้ว = ไม่เขียนทับ (คุยต่อได้ด้วยสถานะแชต)
    if fill:
        db.update_lead(lead_id, **fill)
    existing = PR.latest(lead_id)
    if existing:                                               # มีคำขอจากฟอร์มอยู่แล้ว → ผูกแชตกับคำขอเดิม ไม่สร้างใหม่
        pid, created = existing["id"], False
        db.add_lead_event(lead_id, "line", "ลูกค้าคุยต่อใน LINE OA (ผูกกับคำขอแผนเดิม)", "customer")
    else:
        v = {"name": lead["name"] or "ลูกค้าจาก LINE", "role": "", "contact_channel": "line", "contact_value": "แชต LINE OA (ผูกแล้ว)",
             "desired_window": "", "web_readiness": "", "budget": "", "message": "",
             "idem_key": hashlib.md5(f"line:{code}".encode()).hexdigest(), "marketing": False}
        pid, created = PR.submit(lead, v, source="line_oa")
    db.save_line_session(user_id, lead_id=lead_id, plan_id=pid, step="win", fails=0, fail_at=None)
    line_oa.reply_messages(reply_token, [_text(_summary(db.get_lead(lead_id))), _text(NOTICE), _q_window()])
    PR.after_submit(lead, pid, created, "LINE OA")
    return "code_linked" if created else "code_linked_existing"


def postback(user_id: str, data: str, reply_token: str | None) -> str:
    """ปุ่มตอบเร็ว · ยอมรับเฉพาะผู้ใช้ที่มีสถานะแชตจากรหัสแล้ว และค่าที่อยู่ในรายการเท่านั้น"""
    m = re.fullmatch(r"jp:(win|web|mkt|call):([a-z0-9_]{1,12})", data or "")
    s = db.get_line_session(user_id) if user_id else None
    if not m or not s or not s["plan_id"] or not db.get_plan_request(s["plan_id"]):
        return "postback_ignored"
    if paused(user_id):
        return "paused"
    kind, val = m.groups()
    pid = s["plan_id"]
    if kind == "win" and val in WIN_CHOICES:
        db.update_plan_request(pid, desired_window=val)
        db.save_line_session(user_id, step="web")
        line_oa.reply_messages(reply_token, [_q_web()])
        return "answered_win"
    if kind == "web" and val in WEB_CHOICES:
        db.update_plan_request(pid, web_readiness=val)
        db.save_line_session(user_id, step="mkt")
        line_oa.reply_messages(reply_token, [_q_marketing()])
        return "answered_web"
    if kind == "mkt" and val in ("0", "1"):
        PR.set_marketing(pid, val == "1", "line_oa")
        db.save_line_session(user_id, step="done")
        line_oa.reply_messages(reply_token, [_done_msg()])
        return "answered_mkt"
    if kind == "call" and val == "1":
        db.save_line_session(user_id, step="phone")
        line_oa.reply_messages(reply_token, [_text("พิมพ์เบอร์โทรที่สะดวกได้เลยครับ (เช่น 081 234 5678) ทีมจะโทรหาภายใน 1 วันทำการ",
                                                   [("คุยในแชตต่อ", "jp:call:0")])])
        return "ask_phone"
    if kind == "call" and val == "0":
        db.save_line_session(user_id, step="done")
        line_oa.reply(reply_token, ["ได้ครับ ทีมจะทักกลับในแชตนี้"])
        return "call_cancelled"
    return "postback_ignored"


def text(user_id: str, t: str, reply_token: str | None) -> str | None:
    """ข้อความธรรมดาที่เกี่ยวกับเส้นทางนี้ · คืน None = ไม่เกี่ยว ให้ระบบเดิมจัดการต่อ"""
    code = find_code(t)
    if code:
        return redeem(user_id, code, reply_token)
    s = db.get_line_session(user_id) if user_id else None
    if not s or not s["plan_id"]:
        return None
    if any(w in t for w in STOP_WORDS):
        return stop(user_id, s, reply_token)
    if s["step"] == "phone":
        digits = re.sub(r"\D", "", t)
        if PR._PHONE.match(t.strip()) and 9 <= len(digits) <= 12:
            pid = s["plan_id"]
            db.update_plan_request(pid, contact_channel="phone", contact_value=t.strip()[:40])
            lead = db.get_lead(s["lead_id"]) if s["lead_id"] else None
            if lead:
                if not lead["contact"] or lead["contact"] == "LINE" or lead["contact"].startswith("LINE:"):
                    db.update_lead(lead["id"], contact=f"โทรศัพท์: {t.strip()[:40]}")
                db.add_lead_event(lead["id"], "wants_call", "ลูกค้าขอให้โทรหาแทนคุยใน LINE (เบอร์อยู่ใน Sales Brief)", "customer")
                from . import pipeline
                pipeline.alert_admins(f"📞 ลูกค้าขอให้โทรหา: {lead['domain']} (ขอผ่าน LINE)", f"/admin/leads/{lead['id']}#brief", "lead")
            db.save_line_session(user_id, step="done")
            line_oa.reply(reply_token, ["รับเบอร์แล้วครับ ทีมจะโทรหาภายใน 1 วันทำการ"])
            return "phone_saved"
        if len(digits) >= 4:
            line_oa.reply_messages(reply_token, [_text("เบอร์ดูไม่ครบ ลองพิมพ์ใหม่แบบ 081 234 5678 ครับ", [("คุยในแชตต่อ", "jp:call:0")])])
            return "phone_invalid"
        db.save_line_session(user_id, step="done")             # พิมพ์เรื่องอื่น = เลิกรอเบอร์ ให้ระบบเดิมตอบตามปกติ
    return None


def stop(user_id: str, s, reply_token: str | None) -> str:
    """ลูกค้าขอให้หยุดติดต่อ: ปิดคำขอ · ถอนการรับข่าวสาร · บอกทีม · บอทหยุดถามต่อ"""
    pid = s["plan_id"]
    db.update_plan_request(pid, status="withdrawn")
    PR.set_marketing(pid, False, "line_oa_stop")
    db.save_line_session(user_id, step="stopped")
    if s["lead_id"]:
        db.add_lead_event(s["lead_id"], "line", "ลูกค้าขอให้หยุดติดต่อเรื่องคำขอแผน (ผ่าน LINE)", "customer")
        from . import pipeline
        pipeline.alert_admins("⛔ ลูกค้าขอให้หยุดติดต่อเรื่องคำขอแผน (ผ่าน LINE)", f"/admin/leads/{s['lead_id']}#brief", "lead")
    line_oa.reply(reply_token, ["รับทราบครับ เราจะไม่ติดต่อเรื่องคำขอนี้อีก ถ้าเปลี่ยนใจ ทักมาในแชตนี้ได้ทุกเมื่อ"])
    return "stopped"
