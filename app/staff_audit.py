"""ประวัติการแก้ไขของทีมงาน: ทุกคำขอที่เปลี่ยนข้อมูล (ไม่ใช่ GET) ของบัญชีทีมงาน → แถว staff_log
เก็บ: ทำอะไร (ชื่ออ่านง่าย) · กับอะไร (ตาราง:id จาก URL) · ค่าก่อน → หลังของแถวนั้น + ค่าตั้งระบบที่เปลี่ยน · ฟอร์มที่ส่ง (ซ่อนรหัส/คีย์)
· ผล ok / error / denied — main._staff_guard เรียก begin() ก่อน route และ finish() หลัง route ทำงานเสร็จ"""
from __future__ import annotations
import json
import re

from . import db

# URL → แถวที่ถูกแก้ (ตาราง, ชื่อเรียก) — ตัวแรกที่ตรงชนะ
TARGETS = [
    (r"/admin/deals/requests/(\d+)", "contacts", "คำขอติดต่อ"),
    (r"/admin/(?:leads|deals|calls)/(\d+)", "leads", "ลีด"),
    (r"/admin/(?:tenants|staff)/(\d+)", "tenants", "บัญชี"),
    (r"/admin/quotes/(\d+)", "quotes", "ใบเสนอราคา"),
    (r"/admin/contacts/(\d+)", "contacts", "คำขอติดต่อ"),
    (r"/admin/payments/(\d+)", "payments", "การชำระเงิน"),
    (r"/admin/email/campaigns/(\d+)", "email_campaigns", "แคมเปญอีเมล"),
    (r"/admin/decisions/(\d+)", "decisions", "การตัดสินใจ"),
    (r"/admin/acct/(\d+)", "acct_docs", "เอกสารบัญชี"),
    (r"/admin/results/task/(\d+)", "result_tasks", "งานผลลัพธ์"),
    (r"/admin/marketing/brand/(\d+)", "brands", "แบรนด์"),
    (r"/brands/(\d+)", "brands", "แบรนด์"),
    (r"/content/(\d+)", "content_items", "คอนเทนต์"),
    (r"/questions/(\d+)", "target_questions", "คำถาม"),
]
_TARGETS = [(re.compile(p), t, l) for p, t, l in TARGETS]

# ชื่อการกระทำ: ท้าย URL → คำอ่านง่าย (ไม่ตรง = ใช้ path)
ACTIONS = [
    (r"/audit", "ตรวจเว็บฟรี (หน้าสาธารณะ)"),
    (r"/admin/staff", "เพิ่มทีมงาน"), (r"/admin/staff/\d+/perms", "แก้สิทธิ์ทีมงาน"), (r"/admin/staff/\d+/active", "เปิด/ปิดบัญชีทีมงาน"),
    (r"/admin/staff/\d+/password", "ตั้งรหัสผ่านทีมงาน"), (r"/admin/staff/\d+/email", "แก้อีเมลทีมงาน"),
    (r"/admin/tenants", "สร้างบัญชีลูกค้า"), (r"/admin/tenants/\d+/email", "แก้อีเมลบัญชี"), (r"/admin/tenants/\d+/plan", "เปลี่ยนแผน"),
    (r"/admin/tenants/\d+/trial", "ต่อเวลาทดลองใช้"), (r"/admin/tenants/\d+/self-serve", "เปลี่ยนการใช้งานเอง/ทีมดูแล"),
    (r"/admin/deals/\d+/stage", "ย้ายขั้นดีล"), (r"/admin/deals/\d+/value", "แก้มูลค่าดีล"), (r"/admin/deals/\d+/next", "ตั้งงานถัดไป"),
    (r"/admin/deals/\d+/activity", "บันทึกกิจกรรม"), (r"/admin/deals/\d+/contacts", "เพิ่มผู้ติดต่อ"),
    (r"/admin/deals/\d+/contacts/\d+", "แก้ผู้ติดต่อ"), (r"/admin/deals/\d+/contacts/\d+/delete", "ลบผู้ติดต่อ"),
    (r"/admin/deals/requests/\d+/link", "ผูกคำขอนัดคุยกับดีล"), (r"/admin/deals/requests/\d+/dismiss", "ปิดคำขอนัดคุย"),
    (r"/admin/leads/import", "นำเข้าลีด"), (r"/admin/leads/\d+/status", "เปลี่ยนสถานะลีด"), (r"/admin/leads/\d+/note", "เพิ่มโน้ตลีด"),
    (r"/admin/leads/\d+/quote", "สร้างใบเสนอราคา"), (r"/admin/leads/\d+/propose", "ร่างข้อเสนอ"), (r"/admin/leads/\d+/contact", "แก้ช่องทางติดต่อลีด"),
    (r"/admin/leads/\d+/buyer", "แก้ข้อมูลผู้ซื้อ"), (r"/admin/leads/\d+/mark-contacted", "ทำเครื่องหมายว่าทักแล้ว"),
    (r"/admin/leads/\d+/unsubscribe", "เลิกส่งอีเมลถึงลีด"), (r"/admin/leads/\d+/(audit|deep|find-email)", "สั่งตรวจ/หาข้อมูลลีด"),
    (r"/admin/calls/\d+/log", "บันทึกผลการโทร"), (r"/admin/contacts/\d+/handled", "ทำเครื่องหมายติดต่อแล้ว"),
    (r"/admin/quotes/\d+/send", "ส่งใบเสนอราคา"), (r"/admin/quotes/\d+/cancel", "ยกเลิกใบเสนอราคา"), (r"/admin/quotes/\d+/confirm", "ยืนยันรับเงินใบเสนอราคา"),
    (r"/admin/payments/\d+/confirm", "ยืนยันการชำระเงิน"), (r"/admin/acct/\d+/\w+", "จัดการเอกสารบัญชี"),
    (r"/admin/decisions/\d+/approve", "อนุมัติงานเอเจนต์"), (r"/admin/decisions/\d+/reject", "ปฏิเสธงานเอเจนต์"), (r"/admin/decisions/auto", "ตั้งโหมดอัตโนมัติ"),
    (r"/admin/agents/pause", "หยุด/เดินเอเจนต์"), (r"/admin/ceo(/send\.json)?", "สั่งงาน CEO"), (r"/admin/tasks/plan", "ให้ CEO วางแผนงาน"),
    (r"/admin/skills/mandate", "แก้ขอบเขตอำนาจเอเจนต์"), (r"/admin/skills/[^/]+/reset", "รีเซ็ตทักษะเอเจนต์"), (r"/admin/skills/[^/]+", "แก้ทักษะเอเจนต์"),
    (r"/admin/email/campaigns", "สร้างแคมเปญอีเมล"), (r"/admin/email/campaigns/\d+/send", "ส่งแคมเปญอีเมล"),
    (r"/admin/email/campaigns/\d+/(pause|resume)", "หยุด/เดินแคมเปญ"), (r"/admin/email/campaigns/\d+/delete", "ลบแคมเปญ"),
    (r"/admin/free-plan", "เปิด/ปิดแผน Free"), (r"/admin/agency-mode", "เปิด/ปิดโหมดเอเจนซี"),
    (r"/admin/settings(/[\w/]+)?", "แก้ตั้งค่าระบบ"), (r"/admin/(email/smtp|inbox/settings|salesbot/settings|marketing/settings|results/settings)", "แก้ตั้งค่า"),
    (r"/admin/deliverability/[\w-]+", "ตั้งค่าการส่งอีเมล"), (r"/admin/partners/[\w-]+", "จัดการพันธมิตร"),
    (r"/admin/marketing/brand/\d+/case-study", "อนุญาต/ยกเลิกใช้เป็นผลงาน"), (r"/admin/\w+/run|/admin/inbox/poll|/admin/digest/run", "สั่งรันงาน"),
    (r"/admin/results/task(/\d+/\w+)?", "จัดการงานผลลัพธ์"), (r"/admin/accounting/toggle", "เปิด/ปิดระบบบัญชีอัตโนมัติ"),
    (r"/admin/email/test|/admin/marketing/fb-test|/admin/settings/sms/test", "ทดสอบการเชื่อมต่อ"),
    (r"/brands/\d+/content", "เขียนคอนเทนต์"), (r"/brands/\d+/delete", "ลบแบรนด์"), (r"/brands/\d+/questions(/generate)?", "แก้คำถามของแบรนด์"),
    (r"/brands/\d+/[\w/-]+/run|/brands/\d+/run", "สั่งตรวจแบรนด์"), (r"/brands/\d+/[\w/-]+", "แก้ตั้งค่าแบรนด์"), (r"/brands", "เพิ่มแบรนด์"),
    (r"/content/\d+/publish", "เผยแพร่คอนเทนต์"), (r"/content/\d+/regenerate", "เขียนคอนเทนต์ใหม่"), (r"/content/\d+/delete", "ลบคอนเทนต์"),
    (r"/questions/\d+/delete", "ลบคำถาม"), (r"/account/password", "เปลี่ยนรหัสผ่านตัวเอง"),
]
_ACTIONS = [(re.compile(p + r"\Z"), l) for p, l in ACTIONS]

_SECRET = re.compile(r"pass|secret|token|key|hash|service_account|credential", re.I)
# ค่าตั้งระบบที่งานเบื้องหลังเขียนเองตลอด — ไม่นับว่าทีมงานแก้
_NOISE = re.compile(r"^(uptime:|results_stall_alert|outreach_route_|outreach_from_(ok|checked)|ceo_chat_|office_live|deliv_(counts|tests|seeds|dns|last|released_at)|fb_token_)|_(last|cursor)$")
FIELD_LABEL = {"status": "สถานะ", "deal_value": "มูลค่าดีล", "next_action": "งานถัดไป", "next_at": "เวลานัด", "next_kind": "ชนิดงาน",
               "email": "อีเมล", "plan": "แผน", "name": "ชื่อ", "staff_role": "บทบาท", "staff_perms": "สิทธิ์", "disabled": "ปิดบัญชี",
               "note": "โน้ต", "notes": "โน้ต", "self_serve": "ใช้เอง", "trial_until": "ทดลองถึง", "case_study": "ใช้เป็นผลงาน",
               "title": "หัวข้อ", "body_md": "เนื้อหา", "market": "ธุรกิจ", "domain": "โดเมน", "phone": "โทร", "is_admin": "ทีมงาน"}
MAX_VAL = 160


def action_label(path: str) -> str:
    for rx, label in _ACTIONS:
        if rx.match(path):
            return label
    return path


def target_of(path: str):
    """(table, id, ชื่ออ่านง่าย) ของแถวที่ URL นี้แก้ · None = ไม่ผูกกับแถวเดียว"""
    for rx, table, label in _TARGETS:
        m = rx.match(path)
        if m:
            return table, int(m.group(1)), f"{label} #{m.group(1)}"
    return None


def _show(field: str, v):
    if v is None or v == "":
        return "—"
    if _SECRET.search(field):
        return "•••"
    s = str(v)
    return s if len(s) <= MAX_VAL else f"{s[:MAX_VAL]}… ({len(s):,} ตัวอักษร)"


def _row(table: str, rid: int):
    with db.get_conn() as c:
        r = c.execute(db.q(f"SELECT * FROM {table} WHERE id=?"), (rid,)).fetchone()
    return dict(r) if r else None


def _settings() -> dict:
    with db.get_conn() as c:
        return {r["key"]: r["val"] for r in c.execute("SELECT key, val FROM settings").fetchall() if not _NOISE.search(r["key"])}


def diff(before: dict | None, after: dict | None, prefix: str = "") -> list[dict]:
    """[{f: ชื่อช่อง, a: ก่อน, b: หลัง}] เฉพาะช่องที่เปลี่ยน · ค่าลับแสดง ••• (บอกแค่ว่าเปลี่ยน)"""
    if before is None and after is None:
        return []
    if after is None:
        return [{"f": prefix or "แถว", "a": "มีอยู่", "b": "ลบแล้ว"}]
    if before is None:
        return [{"f": prefix or "แถว", "a": "—", "b": "สร้างใหม่"}]
    out = []
    for k in sorted(set(before) | set(after)):
        a, b = before.get(k), after.get(k)
        if a == b or k in ("updated_at",):
            continue
        name = prefix + FIELD_LABEL.get(k, k)
        if _SECRET.search(k):
            out.append({"f": name, "a": "•••" if a else "—", "b": "••• (เปลี่ยนแล้ว)" if b else "— (ลบแล้ว)"})
        else:
            out.append({"f": name, "a": _show(k, a), "b": _show(k, b)})
    return out


async def _form(request) -> dict:
    """ฟอร์ม/JSON ที่ส่งมา (ซ่อนรหัส/คีย์ · ตัดยาว · ไฟล์ = ชื่อไฟล์) — Starlette เก็บ cache ไว้ route อ่านซ้ำได้"""
    out = {}
    try:
        ct = request.headers.get("content-type", "")
        if "application/json" in ct:
            data = await request.json()
            items = data.items() if isinstance(data, dict) else []
        elif "form" in ct:
            items = (await request.form()).multi_items()
        else:
            items = []
        for k, v in items:
            if hasattr(v, "filename"):
                v = f"[ไฟล์] {v.filename}"
            elif not isinstance(v, str):
                v = json.dumps(v, ensure_ascii=False)
            out[k] = "•••" if (_SECRET.search(k) and v) else (v if len(v) <= MAX_VAL else v[:MAX_VAL] + "…")
    except Exception:
        pass
    return out


async def begin(request, tenant_id: int) -> dict:
    path = request.url.path
    tgt = target_of(path)
    entry = {"tid": tenant_id, "method": request.method, "path": path, "action": action_label(path),
             "target": tgt, "form": await _form(request)}
    try:
        entry["row"] = _row(tgt[0], tgt[1]) if tgt else None
        entry["settings"] = _settings()
    except Exception:
        entry["row"], entry["settings"] = None, None
    return entry


def finish(entry: dict, status: str = "ok") -> None:
    tgt, changes = entry["target"], []
    try:
        if tgt:
            changes += diff(entry["row"], _row(tgt[0], tgt[1]))
        if entry.get("settings") is not None:
            changes += diff(entry["settings"], _settings(), prefix="ตั้งค่า: ")
    except Exception:
        pass
    db.log_staff(entry["tid"], entry["method"], entry["path"], action=entry["action"],
                 target=f"{tgt[0]}:{tgt[1]}" if tgt else None, target_label=tgt[2] if tgt else None,
                 changes=changes, form=entry["form"], status=status)


def denied(tenant_id: int, request) -> None:
    db.log_staff(tenant_id, request.method, request.url.path, action=action_label(request.url.path) if request.method not in ("GET", "HEAD")
                 else "เปิดหน้า", status="denied")


def view(row) -> dict:
    """แถว staff_log → dict สำหรับเทมเพลต (แปลง JSON)"""
    d = dict(row)
    for k in ("changes", "form"):
        try:
            d[k] = json.loads(d.get(k) or ("[]" if k == "changes" else "{}"))
        except ValueError:
            d[k] = [] if k == "changes" else {}
    d["action"] = d.get("action") or ("เข้าสู่ระบบ" if d.get("method") == "LOGIN" else action_label(d.get("path") or ""))
    d["status"] = d.get("status") or "ok"
    return d
