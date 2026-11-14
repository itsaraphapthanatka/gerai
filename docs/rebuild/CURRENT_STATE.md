# CURRENT_STATE — สิ่งที่มีจริงในโค้ด

ตรวจจาก `main` @ a35405f (2026-11-14) โดยอ่านโค้ดอย่างเดียว · **ยังไม่ได้รันอะไร** (ดู TEST_BASELINE.md)
ป้ายความมั่นใจ: **[code]** = เห็นในโค้ด · **[test]** = มี smoke test ครอบ (ยังไม่ได้รัน) · **[doc]** = อ้างในเอกสาร/README แต่ไม่พบในโค้ด · **[missing]** = ไม่มี

## 1. โครงสร้าง
- `app/` 56 โมดูล · `app/main.py` 4,892 บรรทัด (~232 route) · `APIRouter` แยก 2 ตัว (`crm_web.py`, `staff_web.py`) · templates 74 ไฟล์ (49 ใช้ `base.html`) · CSS เขียนเองแบบ inline ทั้งหมด ไม่มี framework · JS แบบ vanilla (three.js r128 จาก cdnjs ใช้ใน `admin_office.html` หน้าเดียว)
- dependencies: fastapi, uvicorn, jinja2, python-multipart, itsdangerous, bcrypt, cryptography, httpx, psycopg, ddgs, pillow, qrcode
- deploy: Docker Compose (`geo-postgres` 127.0.0.1:5434, `geo-app` 127.0.0.1:8095, `pdf` gotenberg) หรือ systemd (`deploy/geo-platform.service`) หลัง nginx · Dockerfile ใช้ python:3.12-slim แต่ README บังคับ 3.13 → **ไม่ตรงกัน**

## 2. Auth / สิทธิ์ [code]
- Session: Starlette `SessionMiddleware` (cookie ลงนาม) คีย์จาก `SESSION_SECRET` ค่าเริ่มต้น `"dev-secret-change-me"` (`main.py:81`) · bcrypt (`main.py:28-37`)
- `_tid` 336, `_is_admin` 340, `_brand_for` 389 (ตรวจเจ้าของแบรนด์), `_managed`/`_managed_block` 350–364 (ลูกค้า agency mode อ่านอย่างเดียว)
- `_staff_guard` (45–77) เป็น dependency ทั้งแอป → `staff.area_for`/`staff.allowed` (`staff.py:38-65`) · บทบาท owner/manager/sales/marketing/finance/viewer · บันทึกการแก้ลง `staff_log` ผ่าน `staff_audit`
- **CSRF [missing]**: ไม่มีเลยทั้ง repo · มีแค่ honeypot
- **Rate limit**: ไม่มี middleware · มี quota ตรวจเว็บฟรี 5/IP/วัน, 20/บัญชี/วัน (`leads.py:36-38`) และแชต `DAILY_PER_USER`
- **ลิงก์ token สาธารณะไม่มีวันหมดอายุ** (ยกเว้น quote `valid_until`) — รายละเอียดใน ROUTE_MAP.md §2

## 3. ฐานข้อมูล [code]
- เลือก engine: `DATABASE_URL` ขึ้นต้น postgres → Postgres, ไม่งั้น SQLite `GEO_DB_PATH` (`db.py:12-14`)
- ตารางทั้งหมดใน `db.py:23-457` (`_tables()`): tenants, brands, target_questions, monitoring_runs, run_results, health_checks, ai_visibility, gsc_snapshots, pagespeed_scans, ai_serp_checks, rank_results, chat_messages, content_items, wp_connections, autopilot_runs, email_campaigns, email_log, leads, **decisions** (คิวอนุมัติ, `:175`), **lead_events** (timeline, `:190`), quotes, sales_chats, contacts, lead_contacts (`:551`), stripe_invoices, acct_docs, payments, inbox_messages, partners, partner_commissions, result_tasks, result_reviews, agent_tasks, posts, settings, notifications, staff_log (`:557`)
- Migration: **ไม่มีตารางเวอร์ชัน** · `init_db()` (`:489-567`) = `CREATE TABLE IF NOT EXISTS` + ~60 `_ensure_column` (เพิ่มคอลัมน์อย่างเดียว) รันทุกครั้งที่แอปเริ่มและทุกครั้งที่รัน `run_monitors.py`
- `migrate_sqlite_to_pg.py` ย้ายแค่ 7 ตาราง (tenants, brands, target_questions, monitoring_runs, run_results, content_items, wp_connections) — **leads, quotes, decisions, acct_docs ฯลฯ ไม่ถูกย้าย**
- ยังไม่มีแนวคิด **account (บริษัท)** แยกจาก lead/tenant · contact เป็นฟิลด์ใน lead + ตาราง `lead_contacts`

## 4. งานเบื้องหลัง [code] — จุดเสี่ยงสูง
- startup (`main.py:180-187`): `init_db` + **ตั้ง `indexnow_on=1` และ `agency_mode=1` ทุกครั้งที่เริ่มแอป** + `_maybe_start_scheduler`
- `GEO_AUTORUN=1` (production example เปิดไว้, `deploy/.env.production.example:19`) → thread `geo-autorun` วนทุก 60 วินาที: รัน SoV ตามกำหนด, `_auto_content_tick` (สร้างร่างด้วย AI), `_auto_publish_tick` (เผยแพร่ร่าง auto หลัง `auto_publish_days`, ค่าเริ่มต้นปิด)
- `GEO_RUN_CHECK_HOURS` อยู่ใน README/env แต่ **ไม่มีโค้ดอ่าน** [doc]
- thread อื่น: `pipeline._spawn` (`pipeline.py:629`), `deep_audit._bg` (Semaphore 2), `orchestrator.dispatch`, `ceo_chat._bg`, `success.py:226`, `indexnow.py:70`, worker ต่อแบรนด์ใน `main.py:2275-2939` — **ทั้งหมดอยู่ในหน่วยความจำ รีสตาร์ตแล้วหาย**
- `run_monitors.py` มี ~25 โหมด (`--due`, `--autopilot`, `--crm`, `--inbox`, `--plan`, `--followup` ฯลฯ) · ตาราง cron อยู่แค่ใน docstring ไม่มี crontab ใน repo
- **ไม่มี lock/lease** → autorun ในแอป + cron `--due` อาจรัน SoV ซ้ำ · `_auto_content_tick` กับ autopilot สร้างคอนเทนต์ซ้ำได้ (กันด้วย weekly cap อย่างเดียว) · ถ้ารันทั้ง systemd และ container พร้อม `GEO_AUTORUN=1` จะมีสองลูป
- คิว: `decisions` (คนอนุมัติที่ `/admin/decisions` หรือ auto ผ่าน `pipeline._maybe_auto`), `agent_tasks` (ไม่มี poller เก็บงานค้าง), ไม่มี outbox

## 5. การวัดผล — ความหมายของตัวเลข
| ตัวเลขในระบบ | วัดจากอะไรจริง | ปัญหา |
|---|---|---|
| SoV (`geo_worker.py:266-325`) | **ผลค้นเว็บ** ddgs/brave/serper: โดเมนหรือชื่อแบรนด์โผล่ในผล | **ถูกเรียกว่า AI** ใน `rank.html:84` ("SoV บน AI"), `db.py:1402` + `brand.html:56,83` ("ยังไม่โผล่ใน AI"), `main.py:1451` · ค้นล้ม → นับเป็นไม่โผล่ (`:287-290`) · ไม่มีคำถาม → SoV 0.0 status done (`:309`) |
| ai_visibility (`ai_visibility.py`) | ถาม ChatGPT/Claude/Perplexity/Gemini (เปิด web search) แล้วตัดสิน cited/named/absent | ✓ ตัด unsearched/no_answer ออกจากตัวหาร (`:23-26`) · สูงสุด 8 คำถาม |
| ai_serp (`ai_serp.py`) | Google AI Overview/AI Mode ผ่าน SerpApi | ✓ แยก "Google ไม่แสดง overview" ออกจากตัวหาร |
| health / deep_audit | ดึงหน้าเว็บมาตรวจ | timeout (status 0) รายงานเป็น "5XX หรือเปิดไม่ได้" (`deep_audit.py:229`) · ค้นบางคำล้มแต่ยังนับรวมใน "ทั้ง N คำยังไม่ติด" (`:660-665`) |
| report.py | รวมทุกตัว | **ไม่มีข้อมูล → 0**: rank (`:59-61`), GSC indexed/clicks/impressions (`:77-80`), serp hits/shown (`:100-102`), gaps (`:111`) |

ข้อดีที่มีอยู่แล้ว: `analytics_chat.py:45,158` เรียกถูกว่า "ผลค้นเว็บ" · `campaigns.py:63,66` ใช้ "-" เมื่อไม่มีข้อมูล · `leads.ai_part` คืน None ถ้าถามไม่ถึง 5 (`leads.py:133-137`)

## 6. Audit / crawler [code] — จุดเสี่ยงสูง
- `POST /audit` (`main.py:701-746`): honeypot → `normalize_url` → quota → ใช้ผลเดิมถ้าโดเมนเดียวกันใน 24 ชม. → `run_lead_audit` (sync) → `deep_audit.start` tier 1 เบื้องหลัง · `/audit/{token}/contact` เปิด tier 2 (AI ~฿40/ครั้ง, cap `deep_ai_daily` 5/วัน, `deep_audit.py:29`)
- **ไม่มีการกัน SSRF เลย** (ตรวจซ้ำแล้ว: ไม่มี `ipaddress`/`is_private`/`is_loopback` ใน `app/`) · `normalize_url` (`leads.py:71-88`) ยอม IP ภายในอย่าง `10.0.0.1`, `169.254.169.254` และพอร์ตใดก็ได้ · ทุก client ใช้ `follow_redirects=True` โดยไม่ตรวจปลายทาง (`site_health.py:498,515,524`, `deep_audit.py:131`, `results.py:139,148,250`, `prospecting.py:161`) · `deep_audit.crawl` ใช้ scheme+พอร์ตที่เก็บไว้
- ขีดจำกัด: deep_audit MAX_PAGES 100, 4 workers, 110 วินาที, timeout 12 วินาที, cap 800KB เฉพาะ HTML (robots/sitemap ไม่ cap) · site_health timeout 20 วินาที, MAX_CRAWL 5, ไม่ cap byte
- งาน AI ที่เกิน cap ถูกเก็บเป็น "queued" ใน `leads.deep_ai_json` ไม่มี poller มาหยิบต่อ

## 7. การค้าและราคา [code] — ห้ามแตะโดยไม่อนุมัติ
- ราคาเขียนตายใน `billing.PLANS` (`billing.py:10-38`): free ฿0 (ทดลอง 14 วัน), pro ฿9,900, agency ฿29,000, unlimited (ซ่อน) · ADDONS (`:41-48`) · `landing.html:463` มี "฿9,900"
- Mandate (`execs.py:12-21`): ลดสูงสุด 10%, ดีลสูงสุด ฿50,000, ส่วนลดรายปี 17%, คอมมิชชันพาร์ตเนอร์ 20% 12 เดือน
- quote → `billing.PLANS` (`quotes.py:45-76`) · จ่ายแล้ว `pipeline.provision_customer` ตั้ง plan + สร้าง brand + เปิด autopilot แบบ draft (`pipeline.py:531-561`) → **quote ผูกกับ entitlement โดยตรง**
- Stripe webhook เปลี่ยน plan ขึ้น/ลงอัตโนมัติ (`stripe_pay.py:391-439`) · PromptPay สร้าง QR อย่างเดียว คนเป็นผู้ยืนยันการจ่าย · คอมมิชชันพาร์ตเนอร์อนุมัติอัตโนมัติเมื่อพ้น refund_days (`partners.py:360-410`) การจ่ายจริงบันทึกด้วยมือ

## 8. CRM / ช่องทางขาออก [code]
- สถานะ lead (`leads.py:10-15`): new, contacted, replied, call, met, quoted, won, lost, unsubscribed · กระดาน CRM (`crm.py:19-25`) · สถานะ quote (`quotes.py:16-18`)
- ลำดับช่องทาง `pipeline.send_channel` (`:177-219`): email (ตรวจ MX) → LINE push → SMS (มี quiet hours) → **ขัดกฎใหม่ "ไม่ไล่ข้ามช่องทางเอง"** ต้องทบทวนใน stage 11
- ต้องผ่าน `decisions`: outreach, followup, reply, quote, payment, post, partner_outreach, partner_apply, expansion · auto ได้เฉพาะ {lead_review, outreach, post, followup, reply} หลัง ≥20 ครั้งที่เห็นตรงกัน ≥95% หรือเจ้าของสั่ง (`execs.py:74,283-301`) · มี kill switch `agents_paused`
- **ส่งทันทีไม่ผ่านอนุมัติ**: salesbot ตอบ LINE/เว็บ, อีเมลตอบรับ inbox "interested" (`inbox.py:212-252`), ใบแจ้งหนี้/ใบเสร็จ/ทวงถาม (`accounting.py:274-298,390-429`), อีเมล success (`success.py:105-113`), แคมเปญอีเมล (`campaigns.py:144`), อีเมลรับพาร์ตเนอร์ (`partners.py:268`), แจ้งเตือนแอดมิน, ลิงก์ชำระเงินเมื่อเจ้าของบันทึก will_pay (`closer.py:299-307`)

## 9. LINE [code]
- `/line/webhook` ตรวจ HMAC (`main.py:1402`, `line_oa.py:83-89`) · คีย์เข้ารหัส Fernet (`line_oa.py:34`) · **ไม่กัน event ซ้ำ** (ไม่อ่าน `webhookEventId`/`isRedelivery`)
- **ผูก LINE user กับ lead จากโดเมนที่พิมพ์** แล้วตอบลิงก์รายงานของ lead นั้น (`pipeline.py:723-731`, ข้อความจาก `line_result_message` `:636-644`) → คนนอกที่รู้ชื่อโดเมนได้ลิงก์รายงานของบริษัทอื่น
- salesbot ส่งต่อเจ้าของเมื่อขอคุยกับคน แต่ **ไม่มีการพัก AI ต่อบทสนทนา** · ขอนัด = ข้อความ "ทีมจะติดต่อกลับเพื่อนัดคุย 30 นาที" + event `wants_call` (`pipeline.py:315-329`) · **ไม่มีปฏิทิน/LIFF/การจอง**

## 10. AI agents [code]
- `orchestrator.plan_and_run` (cron `--plan`) สร้าง `agent_tasks`: prospecting, หา contact, `ai_report` (~฿40/ครั้ง), `marketing.run`, `followup.run`
- **`marketing.run` เผยแพร่ร่างของบริษัทเองขึ้น /geo อัตโนมัติเมื่อผ่าน QA โดยไม่ต้องอนุมัติ** (`marketing.py:305-318`) · social post เป็น decision
- `ceo_chat` สั่งงานผ่าน LINE ได้ ต้องพิมพ์ "ยืนยัน" ภายใน 30 นาทีสำหรับ action สำคัญ (`:24-44,385-401`) — การยืนยันนั้นนับเป็นการอนุมัติส่ง quote
- Autopilot โหมด publish ส่งคอนเทนต์ขึ้น WordPress/หน้า hosted ของลูกค้าโดยไม่ตรวจ (`autopilot.py:18`, `main.py:2722-2743`) · ลูกค้าใหม่ตั้งเป็น draft
- `skills/*.md` 14 ไฟล์เป็น system prompt แก้ทับได้ผ่าน settings ไม่ให้สิทธิ์ action เอง
- ไม่มีเส้นทางใดโอนเงินเอง การยืนยันชำระต้องเป็นคน (`execs.py:298`)

## 11. ความต่างจากเอกสาร
| เอกสารบอก | โค้ดจริง |
|---|---|
| โน้ต 08 Architecture: GEO Dashboard/Worker "สร้างใหม่", LiteLLM :4003, Postgres :5433, geo.aunjai.org | แอป FastAPI ตัวเดียว, Postgres 127.0.0.1:5434, deploy เป้า geo.appreview.cloud (DEPLOY.md) · LLM ค่าเริ่มต้น `https://consoletoken.aunjai.org/api/v1` (`ai_client.py`) |
| README: SQLite แล้วย้าย Postgres ภายหลัง | รองรับ Postgres แล้ว แต่ script ย้ายข้อมูลย้ายแค่ 7 ตาราง |
| README: `GEO_RUN_CHECK_HOURS` | ไม่มีโค้ดอ่าน |
| MASTER PROMPT: "existing PDF infrastructure" | Gotenberg service `pdf` (compose) ✓ |
| MASTER PROMPT: ใช้ CSRF/permission conventions เดิม | permission มี (staff areas) แต่ CSRF ไม่มี |
| MASTER PROMPT: durable jobs | ไม่มี — ทุกงานเป็น thread ในหน่วยความจำ |
| ภาพรวม: "AI Share of Voice" | เป็นผลค้นเว็บ (เฉพาะ ai_visibility กับ ai_serp ที่วัด AI จริง) |

## 12. สิ่งที่ยังไม่ได้ตรวจ
- การรันเทสต์/แอปจริง, Postgres, หน้าตาในเบราว์เซอร์, accessibility, ความเร็ว
- เนื้อหาเต็มของ templates (`landing_v2.html` 615 บรรทัด) ว่ามีคำกล่าวอ้างเกินจริงตรงไหน — ทำใน stage 02
- `wordpress-plugin/` และ `app/skills/*.md` รายไฟล์
