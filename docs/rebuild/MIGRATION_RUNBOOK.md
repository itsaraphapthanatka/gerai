# คู่มือย้ายฐานข้อมูล (Migration Runbook) — stage 18

## 1. หลักการ

- **เพิ่มอย่างเดียว:** `CREATE TABLE IF NOT EXISTS` และ `_ensure_column` (เพิ่มคอลัมน์ที่ไม่มีค่าบังคับ) ไม่ลบ ไม่เปลี่ยนชื่อ และไม่แก้ค่าเดิม
- ย้ายตอนแอปเริ่ม (`startup` → `db.init_db()`) และรันซ้ำได้ (idempotent)
- ระบบบันทึกเวอร์ชันลงตาราง `schema_migrations` (ดูได้ท้ายหน้า `/admin/jobs`)
- **ไม่มีการรวม/แก้ข้อมูลเดิมอัตโนมัติ** การจับคู่ตัวตน (stage 09) เป็นรายงานแบบ dry-run ที่นับอย่างเดียว

| เวอร์ชัน | เพิ่ม |
|---|---|
| 04a / 06 / 04b / 06a1 / 05 / 08b | snapshot, plan_requests, business_contexts, line_plan, report_versions, analytics_events |
| 10 / 06a2 / 09 / 11 / 04c | jobs, job_pauses, approvals, budget_reservations, outbox · appointments · leads.owner_staff_id · leads.opp_override/lost_reason · ai_answer_cache |
| 14 / 15 / 16 / 17 / 18 | agent_actions + agent_tasks.approval_id · delivery_* · publish_requests · finance_adjustments, cost_entries, leads.self_reported_source · scheduler_leases |

## 2. ผลการซ้อม (วัดจริง ข้อมูลสมมติ)

สคริปต์ `scripts/migration_rehearsal.py` · ผลเต็มอยู่ที่ `docs/rebuild/rehearsal/sqlite_2000.json` (วันที่ 2026-11-16 บนเครื่องพัฒนา Windows ระหว่างที่เทสต์อื่นรันอยู่ จึงเป็นค่าที่ช้ากว่าปกติได้)

| ขั้น | ผล |
|---|---|
| ข้อมูลตั้งต้น | schema ของ commit `a35405f` (ก่อน rebuild) · ลีด 2,000 (ซ้ำโดเมนราว 1%) · แบรนด์ 80 · 37 ตาราง · 4,161 แถว · ไฟล์ 0.76 MB |
| สำรอง (sqlite backup API) | 0.02 วินาที |
| ย้าย (init_db โค้ดปัจจุบัน) | 0.11 วินาที · ตารางใหม่ 23 · **แถวเดิมหาย 0** · integrity_check = ok · foreign key ผิด 0 · บันทึกเวอร์ชัน 15+ |
| ย้ายซ้ำ | 0.03 วินาที · ไม่มีแถวเปลี่ยน |
| โค้ดเก่าบน DB ที่ย้ายแล้ว (ถอยโค้ด) | เริ่มได้และอ่านลีดครบ 2,000 · แบรนด์ 80 |
| dry-run จับคู่ตัวตน | 0.09 วินาที · ลีดซ้ำ 20 กลุ่ม · เขียน 0 แถว · รันซ้ำได้ผลเท่าเดิม |
| กู้คืนจากไฟล์สำรอง | 0.01 วินาที · จำนวนแถวตรงกับก่อนย้ายทุกตาราง · integrity ok |

**ยังไม่ได้ทดสอบ:**
- **Postgres** (เครื่องนี้ไม่มี Postgres และ Docker ไม่ได้รัน) ต้องรัน `pg_smoke.py` และสคริปต์ซ้อมกับ Postgres บน staging ก่อน
- ข้อมูลขนาดจริงของ production
- ข้อมูลจริงที่ปกปิดตัวตนแล้ว (ต้องได้รับอนุมัติเฉพาะก่อน)

## 3. ขั้นตอนบน staging / production (ผู้ดูแลเซิร์ฟเวอร์ทำ)

1. ประกาศเวลาปิดปรับปรุงสั้น ๆ แล้ว drain ตาม OPERATIONS_RUNBOOK ข้อ 4
2. **สำรอง:**
    - SQLite: `sqlite3 <db> ".backup '<backup-file>'"` (หรือใช้ backup API แบบในสคริปต์) ห้ามใช้การ copy ไฟล์ขณะแอปเขียน
    - Postgres: `pg_dump -Fc` ไปที่เก็บที่ไม่ใช่เครื่องเดียวกัน
3. ตรวจไฟล์สำรอง: เปิดแล้วนับแถวตารางหลัก (leads, quotes, acct_docs, tenants, brands)
4. deploy โค้ด → แอปเริ่ม → `init_db()` เพิ่มตาราง/คอลัมน์ให้เอง
5. ตรวจ:
    - `PRAGMA integrity_check` (SQLite)
    - เทียบจำนวนแถวตารางหลักกับก่อน deploy
    - ดูรายการเวอร์ชันท้ายหน้า `/admin/jobs`
6. ยังไม่เปิด flag ใด ๆ ใช้ระบบเดิมสักพักก่อน แล้วค่อยเปิดทีละกลุ่มตาม OPERATIONS_RUNBOOK ข้อ 2

## 4. backfill

- ไม่มี backfill ที่เขียนข้อมูลในรอบนี้
- รายงานจับคู่ตัวตน `/admin/staff/identity` เป็น dry-run: รันกี่ครั้งก็ได้ หยุดกลางคันได้ และนับใหม่ทั้งหมดทุกครั้ง การรวมบัญชีต้องให้เจ้าของตัดสินใจและทำเป็นงานแยก

## 5. กู้คืน (ถ้าย้ายแล้วมีปัญหา)

1. kill switch → หยุดแอป
2. SQLite: เอาไฟล์สำรองกลับมาแทนไฟล์ DB · Postgres: `pg_restore` ลงฐานใหม่แล้วสลับ
3. ถอยโค้ดเป็นเวอร์ชันก่อน deploy ได้แม้ไม่กู้ DB เพราะการซ้อมยืนยันแล้วว่าโค้ดเก่าอ่าน DB ที่ย้ายแล้วได้ (ตารางใหม่จะถูกปล่อยไว้เฉย ๆ)
4. ข้อมูลที่เกิดหลังสำรองจะหายถ้ากู้จากไฟล์สำรอง ต้องเลือกระหว่าง "ถอยโค้ดอย่างเดียว" กับ "กู้ DB" ตามความเสียหาย
