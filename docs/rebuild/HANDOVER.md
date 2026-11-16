# ส่งมอบงาน rebuild เจอ.AI (stage 19)

> เอกสารนี้สรุปงานพร้อมหลักฐาน **ไม่ใช่ใบรับรองว่าปลอดภัยทั้งหมด**
> - ทุกอย่างทดสอบบน SQLite ในเครื่องพัฒนาเท่านั้น
> - ยังไม่มี staging
> - ยังไม่ได้ทดสอบ Postgres หรือบริการภายนอกจริง (LINE OA, Stripe, WordPress, SMTP, AI แบบเสียเงิน)
> - **production ต้องได้รับอนุมัติแยกเป็นรายครั้ง** ดู PRODUCTION_CHANGE_REQUEST.md (ยังไม่ได้ทำ)

## 1. สถานะรวม

| สถานะ | รายการ |
|---|---|
| โค้ดเสร็จ + ทดสอบในเครื่องแล้ว | stage 00–19 (ตาราง traceability ด้านล่าง) |
| ติดขัด (รอเจ้าของ/ผู้ดูแลเซิร์ฟเวอร์) | staging · Postgres · บัญชีทดสอบ LINE/Stripe/WordPress · ข้อความกฎหมาย · ราคา/ข้อเสนอ · ของจริงในเนื้อหา (CONTENT_READINESS.md) |
| ตรวจบน staging แล้ว | ไม่มี |
| อนุญาต production | ไม่มี |
| commit ที่ยังไม่ push | ทั้งหมดตั้งแต่ stage 00 (push ไม่ผ่านด้วย 403 ของบัญชี GitHub ที่ใช้อยู่ — รอเจ้าของแก้สิทธิ์) |

## 2. Requirement → โค้ด → เทสต์ → flag

| ขั้น | requirement หลัก | โค้ด | เทสต์ | flag |
|---|---|---|---|---|
| 00 | ตรวจระบบจริง + เอกสาร | `docs/rebuild/*` | `TEST_BASELINE.md` | — |
| 01–03 | design system · หน้าแรก · หน้าบริการ/กลุ่มธุรกิจ | `app/templates/mk/*`, `app/marketing_web.py`, `app/service_pages.py` | `marketing_*_smoke.py` | `marketing_v2` |
| 04A | ตรวจเว็บเร็ว จำกัดหน้า กัน SSRF | `app/snapshot.py`, `app/netguard.py` | `snapshot_smoke.py` | `snapshot_v2` |
| 04B | ยืนยันข้อมูลธุรกิจ | `app/business_context.py` | `business_context_smoke.py` | `context_v2` |
| 04C | ทดสอบ AI เสียเงิน: สวิตช์/สิทธิ์/งบ/ไม่จ่ายซ้ำ | `app/ai_tests.py`, `app/jobs.py` | `ai_tests_smoke.py` | `durable_jobs` + `ai_live_tests` |
| 05 | รายงาน 4 ช่อง หลักฐาน เทียบได้/ไม่ได้ | `app/report_view.py` | `report_*_smoke.py` | `report_v2` |
| 06 / 06A | ขอแผน ความยินยอม brief · LINE · นัด | `app/plan_request.py`, `app/line_plan.py`, `app/appointments.py` | `plan_request_smoke.py`, `appointments_smoke.py` | `plan_request_v2`, `line_appointments` |
| 08A / 08B | QA/SEO/หัวส่วนตัว · analytics ภายใน | `app/main.py` (_private_headers), `app/analytics.py` | `qa_release_smoke.py`, `analytics_smoke.py` | หัวส่วนตัว = เปิดเสมอ · `analytics` |
| 09 | Today · Customer 360 · dry-run ตัวตน | `app/c360.py` | `c360_smoke.py` | `customer360` |
| 10 | คิวงาน lease/retry/อนุมัติ/งบ/outbox | `app/jobs.py` | `jobs_smoke.py` | `durable_jobs` |
| 11 | ช่องทางที่อนุญาต · ขั้นโอกาส · OOO | `app/sales.py` | `sales_smoke.py` | `sales_v2` (contact_block เปิดเสมอ) |
| 14 | อำนาจเอเจนต์ A–D ในโค้ด · injection | `app/agent_policy.py` | `agent_policy_smoke.py` | `agent_policy` |
| 15 | ขอบเขต · พื้นที่งาน · ประตูเริ่มงาน | `app/delivery.py` | `delivery_smoke.py` | `delivery_v2` |
| 16 | ประตูเผยแพร่ · ยืนยันปลายทาง · คืนเวอร์ชัน · ผลรายเดือน | `app/publishing.py`, `app/retention.py` | `publishing_smoke.py` | `publish_v2` |
| 17 | รายได้จากใบเสร็จ · ที่มาหลายมุม · ต้นทุน | `app/revenue.py` | `revenue_smoke.py` | `revenue_v2` |
| 18 | regression · ซ้อมย้าย · runbook · lease · origin | `app/scheduler_lease.py`, `scripts/migration_rehearsal.py` | `release_regression_smoke.py`, `rehearsal/sqlite_2000.json` | `scheduler_lease`, `origin_check` |
| 19 | UAT · ส่งมอบ · คำขอเปลี่ยนแปลง | `scripts/uat_seed.py`, เอกสารนี้ | `uat_fixture_smoke.py` | — |

**ยังไม่ได้ทำ** จากแผนเดิม: stage **07** (วิดีโอ/เคส/คลินิก/หน้าตัวอย่างรายงาน) · **12** (Growth Studio) · **13** (พื้นที่พันธมิตรเต็มรูปแบบ) ทั้งสามรอของจริงจากเจ้าของ ดู §8

## 3. Route ใหม่ (ทั้งหมดหลัง flag เว้นแต่ระบุ)

- **ลูกค้า:**
    - `/app/delivery` (+ `/facts`, `/access`, `/approve-plan`, `/items/<id>/accept`)
    - `/brands/<id>/publishing` (+ `/reconcile`, `/rollback`)
- **หลังบ้าน:**
    - `/admin/today`, `/admin/c360/*`
    - `/admin/jobs/*`, `/admin/tasks/<id>/approve|retry`, `/admin/tasks/rules`
    - `/admin/delivery/*`, `/admin/revenue/*`
    - `/admin/appointments/*`, `/admin/analytics`, `/admin/sales/policy`, `/admin/staff/identity`
- **สาธารณะ:**
    - หน้าเว็บชุดใหม่ (`marketing_v2`)
    - `POST /audit/<token>/event` (`analytics`)
- **ทุก POST ใหม่มีโทเคน CSRF** (เทสต์บังคับ)
- **POST เดิม 131 route ยังไม่มีโทเคน** ดู §7 ความเสี่ยง

## 4. Schema และความเข้ากันได้

- เพิ่มอย่างเดียว ทั้งหมด 15+ เวอร์ชันในตาราง `schema_migrations` (ดู MIGRATION_RUNBOOK.md)
- ซ้อมแล้วว่าโค้ดเก่าใช้ DB ใหม่ได้ ปิด flag แล้วข้อมูลไม่หาย และลูกค้าเดิมที่ไม่มีพื้นที่งานใช้สัญญาและ Autopilot เดิม

## 5. งานตามเวลา / worker

- `run_monitors.py --jobs|--plan|--success|--autopilot|…` หรือ GEO_AUTORUN=1 ในแอป ต้องมีตัวตั้งเวลาเจ้าของเดียว (`scheduler_lease`)
- รายละเอียดใน OPERATIONS_RUNBOOK.md §3

## 6. อำนาจของเอเจนต์ และหน้าที่ของคน

| ระดับ | เอเจนต์ทำเองได้ | ต้องมีคน |
|---|---|---|
| A อ่าน/ร่าง | ✓ | — |
| B ใช้เงินในเพดาน | ✓ ถ้างบเหลือ | ตั้งงบ |
| C ติดต่อคนนอก/เผยแพร่ | เฉพาะเมื่อเปิดกฎประจำ | อนุมัติทีละงาน หรือเปิดกฎประจำ |
| D ราคา/สิทธิ์/คืนอำนาจ | ✗ | อนุมัติทุกครั้ง |

**คนต้องทำเสมอ:**
- ยืนยันนัด
- ใบเสนอราคา
- ยืนยันยอดเงิน
- อนุมัติเผยแพร่ (ลูกค้า หรือทีมเมื่อลูกค้าอนุญาต)
- รับงาน
- ตรวจรายงานจับคู่ตัวตน
- ตั้งราคา/ข้อเสนอ
- ข้อความกฎหมาย

## 7. ความเสี่ยงที่ยังเปิดอยู่

| ความเสี่ยง | ระดับ | สิ่งที่มีแล้ว | ต้องทำ |
|---|---|---|---|
| POST เดิม 131 route ไม่มีโทเคน CSRF | กลาง | SameSite=Lax · flag `origin_check` · เทสต์กัน route ใหม่ | เปิด `origin_check` · เติมโทเคนให้ route เดิม (งานแยก) |
| ยังไม่ได้ทดสอบ Postgres | สูงถ้า production ใช้ Postgres | ซ้อมบน SQLite | รัน `pg_smoke.py` + ซ้อมย้ายบน staging |
| `SESSION_SECRET` ค่าเริ่มต้นเป็นค่าพัฒนา | สูงถ้าไม่ได้ตั้ง | — | ผู้ดูแลเซิร์ฟเวอร์ตรวจ env |
| production ทางเดิมยังถาม AI เมื่อลูกค้าตอบกลับ (เพดาน 5/วัน) | ต้นทุน | นโยบาย 04C เมื่อเปิด `durable_jobs` | ตัดสินใจเปิด |
| ปิด `publish_v2` / `delivery_v2` แล้ว auto-publish / Autopilot เดิมกลับมา | กลาง | ระบุไว้ใน ROLLBACK_RUNBOOK | ปิดรายแบรนด์ก่อนปิด flag |
| `/preview/landing-old` หน้าเว็บเก่าเปิดสาธารณะ (noindex) อาจมีคำโฆษณาเดิม | ต่ำ | noindex | ลบหลังเปิด `marketing_v2` |
| หา WordPress คำขอที่ไม่แน่ใจผลด้วยคีย์กันซ้ำไม่ได้ | ต่ำ | ใช้ id/ลิงก์ที่ปลายทางตอบ | เพิ่มเมื่อมีปลั๊กอินรองรับ |
| ยังไม่มีอีเมลต้อนรับลูกค้าใหม่ที่ตรงกับขั้นตอน `delivery_v2` | กลาง | ไม่ส่งอีเมลเดิมที่สัญญาผิด | เขียนข้อความ + ตรวจกฎหมาย |

## 8. แยก "เลื่อนได้" กับ "ยังไม่เสร็จที่ต้องมี"

**ต้องมีก่อนขึ้น production** (ยังไม่เสร็จ):
- staging + เกณฑ์ผ่าน (STAGING_ACCEPTANCE.md)
- Postgres
- `SESSION_SECRET`
- ข้อความความยินยอม/นโยบายความเป็นส่วนตัวที่ผ่านการตรวจ
- ราคาที่ใช้จริง
- ช่องเวลานัดจริง (ถ้าเปิด `line_appointments`)
- UAT ผ่าน
- อีเมลต้อนรับแบบใหม่ (ถ้าเปิด `delivery_v2`)

**เลื่อนได้** (ไม่ขวาง):
- stage 07 / 12 / 13 (ซ่อนส่วนที่ไม่มีของจริงได้)
- เติมโทเคน CSRF ให้ route เดิม (ระหว่างนี้เปิด `origin_check`)
- ค่า API ของงานอื่นในหน้าต้นทุน
- การแปลหน้าใหม่หลังบ้าน (ตอนนี้ภาษาไทยอย่างเดียว)
