# DELIVERY_PLAN — ลำดับงาน 01–19 (ฉบับเจ้าของอนุมัติ)

แผนภาพ/ตัวอย่างประกอบ: `jorai-plan.html` (ราก repo, ยังไม่ commit) · ทุกขั้นที่มีหน้าจอต้องทำ "ตัวอย่างให้ดูก่อน" แล้วรอเจ้าของอนุมัติก่อนลงมือ

## ลำดับ (5 ระลอก, 4 ประตู)
| ระลอก | ขั้น | dependency | schema ที่เพิ่ม (ขั้นต่ำ) | flag |
|---|---|---|---|---|
| 0 | **00** ตรวจระบบ + เอกสาร | — | ไม่มี | — |
| ◆ ประตู 1 | เจ้าของยืนยันภาพระบบ + แนวดีไซน์ A/B | | | |
| **0.5 (ใหม่)** | **00S** ปิดช่องเสี่ยงที่พบ (ดูด้านล่าง) | 00 | ไม่มี | `snapshot_v2` บางส่วน |
| 1 | 01-lite ชิ้นส่วนดีไซน์ + `app/flags.py` + กลไก CSRF | 00 | ไม่มี | `marketing_v2` |
| 1 | 02-lite หน้าแรก (ต่อยอด `landing_v2.html`, `/audit`) | 01 | ไม่มี | `marketing_v2` |
| 1 | 04A snapshot ≤5 หน้า + กัน SSRF (ต่อยอด `site_health`/`deep_audit`) | 01 | อาจเพิ่มคอลัมน์สถานะใน `leads` | `snapshot_v2` |
| 1 | 05-core รายงาน 4 ช่อง (view-model adapter) + แก้ชื่อ SoV | 04A | ไม่มี (adapter อ่านของเดิม) | `report_v2` |
| 1 | 06-core ขอแผน + consent + Sales Brief (ใช้ `leads`/`lead_events`/`decisions`) | 05 | `consents`, `plan_requests` | `plan_request_v2` |
| ◆ ประตู 2 | เจ้าของลองบนมือถือครบเส้น | | | |
| 2 | 06A-1 LINE: คุยจากรายงาน + ลิงก์ผูกบัญชี + พัก AI + คำขอนัด `requested` | 06 | `line_identity_links`, `appointments` (สถานะ requested เท่านั้น) | `line_appointments` |
| 2 | 01-full, 02-full, 04B บริบท, 05-full, 03 (3 หน้า + /partners), 07 (เฉพาะที่มีของ), 08A QA/SEO, 08B analytics | ตามลำดับ | `business_contexts`, `evidence` | ตามขั้น |
| ◆ ประตู 3 | checklist "หลัง 08" + staging | | | |
| 3 | 10 คิวงานถาวร/อนุมัติ/งบ (ต่อยอด `decisions`, `agent_tasks`) | 08 | `jobs` (lease, idempotency), `budget_reservations`, `schema_migrations` | `durable_jobs` |
| 3 | 06A-2 LINE เลือกเวลา/เตือน/เลื่อน/ยกเลิก | 10 | ขยาย `appointments`, `reminders` | `line_appointments` |
| 3 | 09 Today/Customer 360 (เริ่มด้วยรายงานทดลองรวมข้อมูล ไม่แก้จริง) | 10 | `accounts`, `account_contacts` | `customer360` |
| 3 | 11 ขาย/ติดตาม (ทบทวนลำดับช่องทาง `send_channel`) | 09, 10 | ไม่มีหรือเล็กมาก | |
| ◆ ประตู 4 | checklist "หลัง 10–14" | | | |
| 4 | 04C ทดสอบ AI จริง (ใช้ระบบงบจาก 10), 14, 15, 16, 12, 13, 17 | 10 | ตามขั้น | ตามขั้น |
| 5 | 18 regression + ซ้อม migration, 19 UAT | ทั้งหมด | — | — |

## 00S — ขั้นเร่งด่วนที่เสนอเพิ่ม (ต้องให้เจ้าของอนุมัติก่อน เพราะเปลี่ยนพฤติกรรม)
| # | เรื่อง | ไฟล์ | ทำไมด่วน |
|---|---|---|---|
| 1 | กัน SSRF ใน `normalize_url` + ตรวจ IP หลัง resolve + ตรวจทุก redirect | `leads.py:71-88`, `site_health.py`, `deep_audit.py`, `results.py`, `prospecting.py` | `/audit` เปิดสาธารณะ ยอม `169.254.169.254` |
| 2 | เลิกผูก LINE user กับ lead จากโดเมนที่พิมพ์ (ให้ตรวจใหม่แทนการส่งลิงก์รายงานเดิม) | `pipeline.py:723-731` | คนนอกได้ลิงก์รายงานของบริษัทอื่น |
| 3 | กัน LINE event ซ้ำด้วย `webhookEventId` | `main.py:1402`, `pipeline.handle_line_webhook` | LINE ส่งซ้ำ → ตอบ/ตรวจซ้ำ |
| 4 | เช็กว่า production ไม่ได้ใช้ `SESSION_SECRET` ค่าเริ่มต้น | `main.py:81` | ถ้าใช้ คนอื่นปลอม session admin ได้ |
| 5 | ยืนยันว่ามี scheduler ตัวเดียว (autorun ในแอป หรือ cron ไม่ใช่ทั้งคู่) | `main.py:281-333`, crontab บน server | งานรันซ้ำ เสียเงิน API ซ้ำ |
ข้อ 4–5 เป็นการตรวจ production config → เจ้าของหรือผู้ดูแล server ต้องเป็นคนดู Claude ห้ามเข้าเอง

## การตัดสินใจที่ต้องได้จากเจ้าของ
1. อนุมัติ 00S ข้อ 1–3 (เปลี่ยนพฤติกรรมเดิม)
2. แนวดีไซน์ A/B
3. ติดตั้ง Python 3.13 บนเครื่องพัฒนา (เพื่อรันเทสต์) หรือใช้เครื่องอื่น
4. กลุ่มธุรกิจที่ 2
5. LINE OA/LIFF, ปฏิทินที่ใช้จริง, นโยบายเตือน (06A)
6. ข้อความ SoV ในหน้าลูกค้าเดิม: เปลี่ยนชื่อทันที หรือรอ `report_v2`
7. `marketing.run` เผยแพร่ /geo อัตโนมัติ และ autopilot โหมด publish — คงไว้หรือบังคับผ่านอนุมัติ

## Rollback ทั่วไป
ทุกขั้น: ปิด flag = กลับพฤติกรรมเดิม · ตารางใหม่ไม่ถูกอ่านเมื่อ flag ปิด · ห้ามลบคอลัมน์/ตารางเดิม
