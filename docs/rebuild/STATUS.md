# STATUS — เจอ.AI rebuild

| ขั้น | สถานะ | วันที่ | หมายเหตุ |
|---|---|---|---|
| 00 ตรวจระบบ + เอกสาร | **เสร็จ รอเจ้าของตรวจ** | 2026-11-14 | branch `docs/rebuild-stage-00` · แก้เฉพาะ `docs/rebuild/` · ไม่แตะโค้ด/config/schema |
| 00S ปิดช่องเสี่ยง (ข้อ 1–3) | **เสร็จ เทสต์ผ่าน 63/63 รอเจ้าของตรวจ** | 2026-11-14 | เจ้าของอนุมัติ · branch เดียวกัน · ข้อ 4–5 (ตรวจ config production) ยังรอผู้ดูแล server |
| 01-lite ฐาน (flags + CSRF) | **เสร็จ เทสต์ผ่าน 64/64** | 2026-11-14 | ไม่เปลี่ยนพฤติกรรมเดิม: ทุก flag ปิด, CSRF ใช้กับ route ใหม่เท่านั้น |
| 01-lite หน้าตา | **รอเจ้าของเลือกแนว (ประตู 1)** | 2026-11-14 | ตัวอย่าง: `docs/rebuild/previews/01-ui-kit.html` |
| 02–19 | ยังไม่เริ่ม | | |

## stage 01-lite — สิ่งที่ทำแล้ว
- `app/flags.py`: flag กลาง 5 ตัว (marketing_v2, snapshot_v2, report_v2, plan_request_v2, line_appointments) ค่าเริ่มต้นปิด · อ่าน env `GEO_FLAG_<NAME>` ก่อน แล้ว setting `flag_<name>` · แอดมินดูตัวอย่างได้ผ่านคุกกี้ `jr_preview` · ใน Jinja: `flag_active(name, request)`
- `app/csrf.py`: โทเคนต่อ session + dependency `require_csrf` (อ่าน header `X-CSRF-Token` หรือฟิลด์ `csrf_token`) · ใน Jinja: `csrf_input(request)` · **ยังไม่บังคับกับ route เดิม**
- `app/main.py`: ลงทะเบียนตัวช่วย Jinja 3 ตัว (ไม่มี route ใหม่)
- เทสต์ `flags_csrf_smoke.py`
- พบระหว่างทำ: เว็บมีระบบดีไซน์อยู่แล้ว (`app/templates/_ds.html`: ดำ #0a0a0a, ส้มแดง #ff5530 เป็นจุดเน้น, DM Sans + IBM Plex Sans Thai, ปุ่มแคปซูล) → ตัวอย่าง 01 ต่อยอดของเดิม ส่วนแนว A/B ในหน้าแผนหมายถึงการเปลี่ยนแบรนด์
- เทสต์รอบนี้รวม `smoke_test.py` ด้วยโดยไม่ตั้งใจ: มีการค้น DuckDuckGo จริง 1 ชุด (อ่านอย่างเดียว) และเขียนลง DB ชั่วคราว ไม่ใช่ DB ของ repo

## stage 00S — สิ่งที่เปลี่ยน
| เรื่อง | ไฟล์ | พฤติกรรมใหม่ |
|---|---|---|
| กัน URL ภายใน (SSRF) | `app/netguard.py` (ใหม่) | ตรวจทุก request รวมทุกจุด redirect: รับแค่ http/https, IP ต้องเป็นสาธารณะ, ชื่อโฮสต์ resolve แล้วทุก address ต้องสาธารณะ · ปิดฉุกเฉินด้วย `GEO_NETGUARD=0` |
| ใช้ตัวกันในทุกจุดที่เปิดเว็บที่คนนอกส่งมา | `site_health.py`, `deep_audit.py:131`, `results.py`, `prospecting.py:_fetch`, `gsc.py:_live_noindex`, `image_finder.py` | `httpx.Client`/`httpx.get`/`urlopen` → `netguard.client`/`netguard.get` |
| ปฏิเสธ IP ภายในตั้งแต่ทางเข้า | `leads.normalize_url` | `169.254.169.254`, `10.x`, `127.x`, `localhost` ฯลฯ → None (ข้อความ "กรอกที่อยู่เว็บให้ถูกต้อง") |
| ไม่ส่งรายงานของลีดคนอื่น — LINE | `pipeline.line_site_request` + `leads.reusable_for` | ใช้ลีดเดิมได้เฉพาะ LINE user เดียวกัน · ไม่ผูก LINE เข้าลีดคนอื่นอีก · คนอื่นได้ลีดและการตรวจใหม่ของตัวเอง |
| ไม่ส่งรายงานของลีดคนอื่น — เว็บ `/audit` (พบเพิ่มระหว่างแก้ ช่องเดียวกัน) | `main.py:audit_run` | ใช้ผลเดิมใน 24 ชม. ได้เฉพาะ IP เดิม + ลีดยังนิรนาม (ยังไม่ให้ชื่อ/อีเมล/ช่องทาง/ยินยอม) · ไม่งั้นสร้างลีดใหม่ |
| กัน LINE event ซ้ำ | `db.py` (ตาราง `line_events_seen` + `claim_line_event`), `line_oa.parse_events`, `pipeline.handle_line_webhook` | event ที่มี `webhookEventId` ซ้ำ → ข้าม (คืน "duplicate") |
| เทสต์ใหม่ | `security_00s_smoke.py` | SSRF (IP/IPv6/mapped/ตัวเลขแปลก/redirect/DNS ปลอม), reuse เว็บ+LINE, event ซ้ำ |

### Migration / rollback
- ตารางใหม่ 1 ตาราง `line_events_seen(event_id PK, seen_at)` สร้างผ่าน `CREATE TABLE IF NOT EXISTS` ใน `init_db` (เพิ่มอย่างเดียว) · rollback: revert โค้ด ตารางค้างไว้ได้ไม่กระทบ
- ปิดตัวกัน SSRF ชั่วคราว: `GEO_NETGUARD=0` · ส่วนการไม่ reuse ลีดไม่มี flag (เป็นการแก้ช่องรั่ว)

### ผลข้างเคียงที่ตั้งใจ
- ลีดซ้ำโดเมนเพิ่มขึ้น (คนละคนตรวจโดเมนเดียวกัน = คนละลีด) → ทีมขายอาจเห็นโดเมนเดียวกันหลายแถว · รวมลีดเป็นงานของ stage 09
- คนที่ตรวจโดเมนเดียวกันจากคนละ IP จะทำให้ระบบไต่เว็บซ้ำ (จำกัดด้วย quota 5/IP/วัน เดิม)
- LINE event ถูกจองก่อนประมวลผล → ถ้าประมวลผลล้มกลางทาง LINE ส่งซ้ำจะไม่ถูกทำอีก (เลือกไม่ตอบซ้ำมากกว่าตอบสองครั้ง)

### ข้อจำกัดที่ยังเหลือ
- DNS rebinding: ตรวจตอน resolve แล้ว httpx resolve อีกรอบตอนต่อจริง
- ชื่อโฮสต์ที่ resolve ไม่ได้ตอนตรวจ ถูกปล่อยผ่าน
- `_client_ip` เชื่อ `X-Forwarded-For` ตัวแรกที่ client ส่งมา → ผู้ไม่หวังดีที่รู้ IP ของเหยื่อปลอม IP ได้ (ต้องให้ nginx เขียนทับ header นี้)
- `wp_client.py` (เชื่อม WordPress ของลูกค้าที่ล็อกอินแล้ว) ยังไม่ผ่านตัวกัน
- ตาราง `line_events_seen` ไม่มีการลบของเก่า (เพิ่มใน stage 10)

## stage 00 — สิ่งที่ทำ
- อ่านโค้ด `main` @ a35405f: routes, auth, DB/migration, งานเบื้องหลัง, audit/crawler, การวัด, การค้า, CRM, LINE, AI agents, เทสต์
- สร้าง: IMPLEMENTATION_CONTRACT, CURRENT_STATE, ROUTE_MAP, DATA_CONTRACTS, TEST_BASELINE, DELIVERY_PLAN, METRIC_DICTIONARY, OWNER_ASSET_CHECKLIST, STATUS

## เทสต์
รันแล้ว 2026-11-14: **63/63 ผ่าน** (ดู TEST_BASELINE.md) · ไม่ได้รัน `smoke_test.py`, `pg_smoke.py` · ยังไม่ได้ทดสอบบน Postgres และยังไม่ได้รันแบบตัดเน็ต
ไฟล์ที่เกี่ยวกับ 00S โดยตรง (ผ่านทั้งหมด): `security_00s_smoke.py`, `leads_smoke.py`, `email_i18n_smoke.py`, `bot_access_smoke.py`, `health_smoke.py`, `deep_smoke.py`, `results_smoke.py`, `image_smoke.py`, `gsc_smoke.py`, `prospect_smoke.py`, `marketing_smoke.py`, `office_smoke.py`, `partners_smoke.py`, `phase2_smoke.py`

## Blockers
1. ~~ไม่มี Python 3.13~~ ติดตั้งแล้ว 2026-11-14
2. ~~ต้องได้อนุมัติ 00S~~ อนุมัติและเทสต์ผ่านแล้ว · ยังไม่ได้ทดสอบบน Postgres (ตาราง `line_events_seen` ใช้ `ON CONFLICT` ซึ่ง Postgres รองรับ แต่ยังไม่ได้รันจริง)
3. ยังไม่ได้ตรวจ production config (`SESSION_SECRET`, scheduler ซ้ำ) — ต้องให้ผู้ดูแล server ตรวจ

## จุดเสี่ยงสูงที่พบ (สรุป)
1. ~~ไม่กัน SSRF ใน `/audit` (สาธารณะ)~~ แก้ใน 00S (เทสต์ผ่าน)
2. ~~LINE (และเว็บ `/audit`) ส่งรายงานของลีดคนอื่น~~ แก้ใน 00S (เทสต์ผ่าน)
3. ไม่มี CSRF ทั้ง repo
4. งานเบื้องหลังทั้งหมดเป็น thread ในหน่วยความจำ ไม่มี lock → งานซ้ำ/หาย
5. SoV จากผลค้นเว็บถูกเรียกว่า AI ในหลายหน้า และค่าที่ไม่มีกลายเป็น 0 ในรายงาน
6. `migrate_sqlite_to_pg.py` ย้ายแค่ 7 ตาราง
7. ส่งอีเมล/ข้อความหลายประเภททันทีไม่ผ่านอนุมัติ และ `marketing.run` เผยแพร่ /geo อัตโนมัติ
