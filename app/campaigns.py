"""อีเมลแคมเปญ — ส่งรายงาน/ข้อความให้ลูกค้าตามรอบ (ครั้งเดียว / รายสัปดาห์ / รายเดือน) พร้อมแนบ PDF ได้

กลุ่มผู้รับ: ทุกลูกค้าที่มีแบรนด์, ลูกค้ารายเดียว (tenant:<id>), หรือแบรนด์เดียว (brand:<id>) — ส่ง "ต่อแบรนด์"
เพราะเนื้อหาผูกกับตัวเลขของแบรนด์นั้น: เนื้อความเป็น Markdown ใส่ตัวแปรได้ ({brand}, {sov}, {clicks}, {next_steps} …)
เติมจาก report.collect() ของแบรนด์ตอนส่ง · ทุกฉบับลง email_log (สำเร็จ/ล้ม/เหตุผล) · รอบถัดไปคำนวณจาก next_run()
cron รายวัน (--email) เรียก send_due() — แคมเปญที่ถึงเวลา (next_at <= ตอนนี้) และ active เท่านั้น
render_body()/next_run()/recipients_of() เป็น pure/ใกล้ pure — เทสต์ได้ (email_smoke.py)
"""
from __future__ import annotations
import datetime

SCHEDULES = {"once": "ครั้งเดียว", "weekly": "ทุกสัปดาห์", "monthly": "ทุกเดือน"}
PLACEHOLDERS = {
    "{name}": "ชื่อลูกค้า", "{brand}": "ชื่อแบรนด์", "{domain}": "เว็บ", "{period}": "ช่วงรายงาน",
    "{sov}": "SoV ล่าสุด (%)", "{sov_delta}": "SoV เปลี่ยนจากรอบก่อนช่วง (จุด)", "{rank}": "ติดอันดับ Google x/y",
    "{ai_rate}": "ถูกพูดถึงบนผู้ช่วย AI (%)", "{clicks}": "คลิกจาก Google 28 วัน", "{impressions}": "impressions 28 วัน",
    "{indexed}": "หน้าคอนเทนต์อยู่ใน index x/y", "{published}": "คอนเทนต์เผยแพร่ในช่วงรายงาน", "{next_steps}": "สิ่งที่ควรทำต่อ (ลิสต์)",
    "{report_url}": "ลิงก์หน้ารายงาน",
}
# ชื่อเรียกแทนเมื่อบัญชีไม่มีชื่อ — ต่อท้ายคำทักในเนื้อหาเริ่มต้น ("สวัสดีคุณ{name}" / "Hi {name}," / "{name} 様")
# ไม่ใส่ในคลังคำแปลกลาง เพราะ "ลูกค้า" เป็นคำทั่วไปที่หน้าเว็บใช้ในความหมายอื่น (ป้าย "ลูกค้า")
NAME_FALLBACK = {"th": "ลูกค้า", "en": "there", "zh": "客户", "ja": "ご担当者", "ko": "고객"}
DEFAULT_SUBJECT = "รายงาน GEO ประจำเดือน — {brand}"
DEFAULT_BODY = """สวัสดีคุณ{name}

สรุปผล GEO ของ **{brand}** ({domain}) ช่วง {period}

- Share of Voice: **{sov}%** ({sov_delta} จุดจากรอบก่อน)
- ติดอันดับ Google: {rank}
- ถูกพูดถึงบนผู้ช่วย AI: {ai_rate}%
- คลิกจาก Google 28 วัน: {clicks} (impressions {impressions})
- หน้าคอนเทนต์อยู่ใน index: {indexed}
- คอนเทนต์เผยแพร่ใหม่: {published} ชิ้น

**สิ่งที่ควรทำต่อ**
{next_steps}

รายละเอียดทั้งหมด: {report_url}
"""


def next_run(schedule: str, after: datetime.datetime):
    """รอบถัดไปหลังส่งเสร็จ — รายเดือนใช้วันเดิมของเดือน (ปัดเป็นวันสุดท้ายถ้าเดือนสั้นกว่า) · once = None (จบ)"""
    if schedule == "weekly":
        return after + datetime.timedelta(days=7)
    if schedule == "monthly":
        y, m = (after.year + (after.month // 12), after.month % 12 + 1)
        import calendar
        day = min(after.day, calendar.monthrange(y, m)[1])
        return after.replace(year=y, month=m, day=day)
    return None


def values_for(report: dict, tenant_name: str, base_url: str, lang: str = "th") -> dict:
    """ตัวแปรสำหรับเติมใน subject/body จากรายงานของแบรนด์ · lang = ภาษาของลูกค้า (ข้อความที่ระบบเติมแปลตามนั้น)"""
    from . import i18n
    t = lambda x: i18n.tr(x, lang)
    sov, rk, ai, g, c = report.get("sov") or {}, report.get("rank") or {}, report.get("ai"), report.get("gsc"), report.get("content") or {}
    steps = [t(x) for x in (report.get("next_steps") or [])]
    return {
        "{name}": tenant_name or (NAME_FALLBACK.get(lang) or "ลูกค้า"), "{brand}": report["brand"]["name"], "{domain}": report.get("site", ""),
        "{period}": f"{report['period']['from']} – {report['period']['to']}",
        "{sov}": str(sov["latest"]["sov"]) if sov.get("latest") else "-",
        "{sov_delta}": (f"{sov['delta']:+d}" if sov.get("delta") is not None else "±0"),
        "{rank}": f"{rk['latest']['ranked']}/{rk['latest']['total']}" if rk.get("latest") else t("ยังไม่เคยเช็ค"),
        "{ai_rate}": str(ai["rate"]) if ai and ai.get("asked") else "-",
        "{clicks}": f"{g['clicks']:,}" if g and g.get("linked") else "-",
        "{impressions}": f"{g['impressions']:,}" if g and g.get("linked") else "-",
        "{indexed}": f"{g['indexed']}/{g['inspected']}" if g and g.get("linked") else t("ยังไม่ได้เชื่อม Search Console"),
        "{published}": str(c.get("published_period", 0)),
        "{next_steps}": "\n".join(f"- {s}" for s in steps) if steps else "- " + t("ไม่มีข้อเร่งด่วน ทำต่อเนื่องตามแผน"),
        "{report_url}": f"{base_url}/brands/{report['brand']['id']}/report",
    }


def localized(template: str, default: str, lang: str) -> str:
    """แม่แบบมาตรฐาน (หัวข้อ/เนื้อหาเริ่มต้น) → ฉบับแปลตามภาษาลูกค้า · แม่แบบที่เจ้าของเขียนเองส่งตามที่เขียน (ไม่แปลข้อความของคน)"""
    from . import i18n
    norm = lambda x: " ".join((x or "").split())
    return i18n.tr(default, lang) if lang != "th" and norm(template) == norm(default) else template


def render_body(template: str, values: dict) -> str:
    out = template or ""
    for k, v in values.items():
        out = out.replace(k, str(v))
    return out


def recipients_of(audience: str, tenants: list, brands: list) -> list:
    """คู่ (tenant, brand) ที่จะได้รับ — all: ทุกแบรนด์ของทุกลูกค้า (ยกเว้นลูกค้าไม่มีอีเมล) · tenant:<id> · brand:<id>"""
    tmap = {t["id"]: t for t in tenants}
    if audience.startswith("brand:"):
        bid = int(audience.split(":", 1)[1])
        pairs = [(tmap.get(b["tenant_id"]), b) for b in brands if b["id"] == bid]
    elif audience.startswith("tenant:"):
        tid = int(audience.split(":", 1)[1])
        pairs = [(tmap.get(tid), b) for b in brands if b["tenant_id"] == tid]
    else:
        pairs = [(tmap.get(b["tenant_id"]), b) for b in brands]
    from .mailer import is_deliverable
    return [(t, b) for t, b in pairs if t and is_deliverable(t.get("email") or "")]


def audience_label(audience: str, tenants: list, brands: list) -> str:
    if audience.startswith("brand:"):
        b = next((x for x in brands if x["id"] == int(audience.split(":")[1])), None)
        return f"แบรนด์ {b['name']}" if b else audience
    if audience.startswith("tenant:"):
        t = next((x for x in tenants if x["id"] == int(audience.split(":")[1])), None)
        return f"ลูกค้า {t['name'] or t['email']}" if t else audience
    return "ทุกลูกค้า"


def send_campaign(camp, base_url: str, now: datetime.datetime | None = None, dry: bool = False) -> dict:
    """ส่งแคมเปญ 1 รายการให้ผู้รับทุกคู่ (tenant, brand) → อัปเดต next_at/status และ email_log · คืนสรุป"""
    from . import db, mailer, report, wp_client
    now = now or datetime.datetime.now()
    tenants = [dict(t) for t in db.list_all_tenants()]
    brands = [dict(b) for b in db.list_all_brands()]
    pairs = recipients_of(camp["audience"], tenants, brands)
    sent = failed = 0
    errors = []
    for tenant, brand in pairs:
        try:
            from . import i18n
            lang = db.row_lang(tenant)
            rep = report.collect(brand["id"])
            vals = values_for(rep, tenant.get("name") or "", base_url, lang)
            subject = render_body(localized(camp["subject"], DEFAULT_SUBJECT, lang), vals)
            body_md = render_body(localized(camp["body_md"], DEFAULT_BODY, lang), vals)
            html = mailer.wrap_html(subject, wp_client.md_to_html(body_md),
                                    i18n.tr("ส่งจาก เจอ.AI ถึง {email} · ดูรายงานฉบับเต็มได้ที่ {url}", lang, email=tenant['email'], url=vals['{report_url}']),
                                    lang=lang)
            attachments = []
            if camp.get("attach_report"):
                try:
                    attachments.append((f"geo-report-{brand['name'][:30].replace(' ', '_')}-{rep['period']['to']}.pdf",
                                        report.to_pdf(report.render_html(rep, pdf=True, lang=lang)), "application/pdf"))
                except Exception as e:
                    # แนบ PDF ไม่ได้ (บริการ pdf ล่ม) → ส่งข้อความต่อ แต่บอกไว้ใน log
                    errors.append(f"{brand['name']}: แนบ PDF ไม่ได้ ({type(e).__name__})")
            if not dry:
                mailer.send(tenant["email"], subject, html, body_md, attachments)
            db.add_email_log(camp["id"], tenant["id"], tenant["email"], brand["id"], True, "")
            sent += 1
        except Exception as e:
            failed += 1
            err = f"{type(e).__name__}: {str(e)[:200]}"
            errors.append(f"{brand['name']}: {err}")
            db.add_email_log(camp["id"], tenant["id"], tenant["email"], brand["id"], False, err)
    nxt = next_run(camp["schedule"], now)
    db.touch_campaign(camp["id"], now.isoformat(timespec="seconds"), nxt.isoformat(timespec="seconds") if nxt else None,
                      "active" if nxt else "done")
    return {"campaign": camp["name"], "recipients": len(pairs), "sent": sent, "failed": failed, "errors": errors[:5],
            "next_at": nxt.isoformat(timespec="minutes") if nxt else None}


def send_due(base_url: str, now: datetime.datetime | None = None) -> list:
    from . import db
    now = now or datetime.datetime.now()
    out = []
    for camp in db.due_campaigns(now.isoformat(timespec="seconds")):
        out.append(send_campaign(dict(camp), base_url, now))
    return out
