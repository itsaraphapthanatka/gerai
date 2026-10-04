"""ทดสอบ mailer/campaigns แบบไม่ส่งจริง — build_message, next_run, values_for/render_body, recipients_of"""
import datetime, email
import app.mailer as mail
import app.campaigns as cp

cfg = {"smtp_host": "smtp.example.com", "smtp_port": 587, "smtp_user": "u", "smtp_pass": "p", "smtp_from": "geo@example.com", "smtp_from_name": "เจอ.AI", "smtp_tls": "starttls"}
msg = mail.build_message(cfg, "lukkha@example.com", "รายงาน — JKP", "<h1>หัว</h1><p>บรรทัด 1</p><p>บรรทัด 2</p>", attachments=[("r.pdf", b"%PDF-1.4 x", "application/pdf")])
assert msg["To"] == "lukkha@example.com" and "JKP" in msg["Subject"] and "geo@example.com" in msg["From"] and msg["Message-ID"].endswith("@example.com>")
parts = {p.get_content_type(): p for p in msg.walk()}
assert "text/plain" in parts and "text/html" in parts and "application/pdf" in parts
assert "บรรทัด 1\nบรรทัด 2" in parts["text/plain"].get_content() and parts["application/pdf"].get_filename() == "r.pdf"
print("build_message: plain จาก html + html + แนบ PDF + From/Message-ID OK")

d = datetime.datetime(2026, 1, 31, 7, 30)
assert cp.next_run("weekly", d) == datetime.datetime(2026, 2, 7, 7, 30)
assert cp.next_run("monthly", d) == datetime.datetime(2026, 2, 28, 7, 30)        # เดือนสั้น ปัดวันสุดท้าย
assert cp.next_run("monthly", datetime.datetime(2026, 12, 15)) == datetime.datetime(2027, 1, 15)
assert cp.next_run("once", d) is None
print("next_run: weekly +7 · monthly วันเดิม/ปัดท้ายเดือน/ข้ามปี · once จบ OK")

rep = {"brand": {"id": 1, "name": "JKP"}, "site": "https://jkp.test", "period": {"from": "2026-09-04", "to": "2026-10-04"},
       "sov": {"latest": {"sov": 25}, "delta": 25}, "rank": {"latest": {"ranked": 3, "total": 8}}, "ai": {"asked": 16, "rate": 0},
       "gsc": {"linked": True, "clicks": 1234, "impressions": 56789, "indexed": 1, "inspected": 22}, "content": {"published_period": 2},
       "next_steps": ["ทำ A", "ทำ B"]}
v = cp.values_for(rep, "สมชาย", "https://geo.appreview.cloud")
assert v["{sov}"] == "25" and v["{sov_delta}"] == "+25" and v["{rank}"] == "3/8" and v["{clicks}"] == "1,234" and v["{indexed}"] == "1/22"
assert v["{next_steps}"] == "- ทำ A\n- ทำ B" and v["{report_url}"] == "https://geo.appreview.cloud/brands/1/report" and v["{name}"] == "สมชาย"
body = cp.render_body(cp.DEFAULT_BODY, v)
assert "สวัสดีคุณสมชาย" in body and "**JKP**" in body and "25%" in body and "- ทำ A" in body and "{" not in body.replace("{report_url}", "")
assert cp.render_body(cp.DEFAULT_SUBJECT, v) == "รายงาน GEO ประจำเดือน — JKP"
empty = cp.values_for({"brand": {"id": 2, "name": "X"}, "site": "", "period": {"from": "a", "to": "b"}, "sov": {}, "rank": {}, "ai": None, "gsc": None, "content": {}, "next_steps": []}, "", "https://x")
assert empty["{sov}"] == "-" and empty["{rank}"] == "ยังไม่เคยเช็ค" and empty["{indexed}"].startswith("ยังไม่ได้เชื่อม") and "ไม่มีข้อเร่งด่วน" in empty["{next_steps}"] and empty["{name}"] == "ลูกค้า"
print("values_for/render_body: เติมตัวแปรครบ · ข้อมูลว่างไม่พัง OK")

tenants = [{"id": 1, "email": "a@x", "name": "A"}, {"id": 2, "email": "", "name": "B"}, {"id": 3, "email": "c@x", "name": "C"}]
brands = [{"id": 10, "tenant_id": 1, "name": "A1"}, {"id": 11, "tenant_id": 1, "name": "A2"}, {"id": 20, "tenant_id": 2, "name": "B1"}, {"id": 30, "tenant_id": 3, "name": "C1"}]
assert [b["id"] for _, b in cp.recipients_of("all", tenants, brands)] == [10, 11, 30]          # B ไม่มีอีเมล → ข้าม
assert [b["id"] for _, b in cp.recipients_of("tenant:1", tenants, brands)] == [10, 11]
assert [(t["id"], b["id"]) for t, b in cp.recipients_of("brand:30", tenants, brands)] == [(3, 30)]
assert cp.recipients_of("brand:20", tenants, brands) == []
assert cp.audience_label("brand:30", tenants, brands) == "แบรนด์ C1" and cp.audience_label("tenant:1", tenants, brands) == "ลูกค้า A" and cp.audience_label("all", tenants, brands) == "ทุกลูกค้า"
print("recipients_of: all/tenant/brand · ข้ามลูกค้าไม่มีอีเมล OK")
print("ALL EMAIL TESTS OK")
