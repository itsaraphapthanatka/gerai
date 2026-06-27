# เจอ.AI — GEO Platform

แพลตฟอร์มทำ **GEO/AEO** (ทำให้แบรนด์ถูกค้นเจอ/ถูกอ้างอิงบน AI Search — ChatGPT, Gemini, Perplexity, Google AI) ครบวงจร:

```
1) Monitor    วัด Share of Voice (SoV) — แบรนด์โผล่ในผลค้นกี่คำถาม + ใครครองพื้นที่
2) Generate   สร้างคอนเทนต์ ไทย/อังกฤษ + Schema JSON-LD + meta จากคำถามที่ SoV ต่ำ
3) Publish    ส่งเข้าเว็บลูกค้า — WP REST (บทความ) หรือ Connector plugin (บทความ+schema+meta)
              + ชุดติดตั้ง on-site: llms.txt / robots (AI-bot) / schema องค์กร
4) Measure    รัน Monitor ซ้ำ → หน้า Progress เทียบ SoV ก่อน/หลัง (พิสูจน์ผลเป็นตัวเลข)
```

## Stack
- Python 3.13 + FastAPI + SQLite (เริ่มเร็ว ไม่ต้องตั้ง DB; ย้าย Postgres ภายหลังแก้แค่ `app/db.py`)
- ค้นเว็บแบบเลือก backend ได้: `ddgs` (ฟรี) / Brave / Serper
- (ออปชัน) LLM เขียนคอนเทนต์ผ่าน LiteLLM — ไม่มีคีย์ก็ใช้ template ได้
- venv **แยกของตัวเอง** (อย่าใช้ venv ของ hermes)

## ติดตั้ง & รัน (Windows + uv)
```powershell
cd "C:\GEO PLATFORM\geo-platform"
& "C:\Users\User\AppData\Local\hermes\bin\uv.exe" venv --python 3.13 .venv
& "C:\Users\User\AppData\Local\hermes\bin\uv.exe" pip install --python .venv\Scripts\python.exe -r requirements.txt
copy .env.example .env          # แก้ SESSION_SECRET (+ search/LLM ถ้าต้องการ)
# สร้างบัญชีผู้ดูแลคนแรก (รันซ้ำอีเมลเดิม = รีเซ็ตรหัสผ่าน)
.\.venv\Scripts\python.exe create_admin.py admin@geo.local "รหัสผ่านของคุณ" "Operator"
# รัน dev server (--reload-dir app ให้ hot-reload เฝ้าโฟลเดอร์ app เสมอ)
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --reload-dir app --port 8099
```
เปิด http://127.0.0.1:8099 → ล็อกอิน admin → เด้งเข้า **/admin** (god-view)

> ⚠️ ต้องใช้ **Python 3.13** — 3.14 ทำ jinja2/starlette stack นี้พัง

## บทบาทผู้ใช้
- **admin (operator)** — เห็นลูกค้าทุกราย + แบรนด์ทุกอัน, เปิด/รันของใครก็ได้, สร้างบัญชีลูกค้าที่ `/admin`
- **ลูกค้า (tenant)** — เห็นเฉพาะแบรนด์ของตัวเอง
- **ปิดรับสมัครเอง** — `/register` เด้งไป `/login`; บัญชีลูกค้าสร้างผ่าน admin เท่านั้น

## ใช้งาน (ต่อ 1 แบรนด์)
1. **เพิ่มแบรนด์ + คำถามเป้าหมาย** (คำถามที่ลูกค้ามักถาม AI)
2. **รันมอนิเตอร์** → ดูรายงาน SoV + ใครครองพื้นที่
3. **สร้างคอนเทนต์ GEO** จากคำถาม (เลือกภาษา) → ได้บทความ + FAQPage JSON-LD + meta (คัดลอกได้)
4. **ชุดติดตั้ง on-site** (`/brands/{id}/assets`) → คัดลอก llms.txt / robots / schema องค์กรไปแปะ
5. **เชื่อมต่อ WordPress** → เผยแพร่คอนเทนต์เป็น **ร่าง** (REST) หรือ **ร่าง+schema** (Connector)
6. **ดูความคืบหน้า** (`/brands/{id}/progress`) → SoV ก่อน/หลัง + กราฟ + รายคำถามที่ขึ้นมาโผล่

## Automation (รันมอนิเตอร์อัตโนมัติ)
- **Batch job** (สำหรับ cron / Task Scheduler):
  ```powershell
  .\.venv\Scripts\python.exe run_monitors.py --due --days 7   # รันเฉพาะแบรนด์ที่ค้างเกิน 7 วัน
  .\.venv\Scripts\python.exe run_monitors.py --all            # รันทุกแบรนด์
  .\.venv\Scripts\python.exe run_monitors.py --brand 3        # แบรนด์เดียว
  ```
- **ในแอป** (ไม่ต้องพึ่ง scheduler ภายนอก): ตั้ง `GEO_AUTORUN=1` ใน `.env` → เซิร์ฟเวอร์รันแบรนด์ที่ค้างให้เองทุก `GEO_RUN_CHECK_HOURS` ชั่วโมง

## Search backend
ตั้ง `GEO_SEARCH_BACKEND` ใน `.env` — ใส่คีย์แล้วผลค้นภาษาไทยแม่นขึ้น (ไม่ต้องแก้โค้ด):

| ค่า | ใช้ | ต้องมีคีย์ |
|---|---|---|
| `ddgs` (ดีฟอลต์) | DuckDuckGo ฟรี | — |
| `brave` | Brave Search API (country/lang=th) | `BRAVE_API_KEY` |
| `serper` | Google ผ่าน Serper (gl/hl=th) | `SERPER_API_KEY` |

ถ้า backend จ่ายเงินล่ม/คีย์ผิด → fallback กลับ ddgs อัตโนมัติ

## WordPress Connector plugin
ปลั๊กอินที่ `wordpress-plugin/jor-ai-connector/` — เติมสิ่งที่ WP REST แกนทำไม่ได้ (inject schema ทั้งเว็บ, `/llms.txt`, robots AI-bot, รับ schema+meta จากแพลตฟอร์ม). วิธีติดตั้ง + ทดสอบ: ดู `wordpress-plugin/jor-ai-connector/TESTING.md`

## Config (.env)
| ตัวแปร | ค่าเริ่มต้น | ความหมาย |
|---|---|---|
| `GEO_DB_PATH` | ./geo_platform.db | ที่อยู่ไฟล์ SQLite |
| `SESSION_SECRET` | — | คีย์ session + ใช้ derive คีย์เข้ารหัส WP credential (ตั้งให้ยาว) |
| `GEO_SEARCH_LIMIT` | 8 | จำนวนผลค้นต่อคำถาม |
| `GEO_SEARCH_BACKEND` | ddgs | ddgs / brave / serper |
| `BRAVE_API_KEY` / `SERPER_API_KEY` | — | คีย์ search backend |
| `GEO_AUTORUN` | 0 | 1=เปิด auto-run ในแอป |
| `GEO_RUN_INTERVAL_DAYS` | 7 | รันซ้ำเมื่อค้างเกินกี่วัน |
| `GEO_RUN_CHECK_HOURS` | 12 | auto-run เช็คทุกกี่ชั่วโมง |
| `LITELLM_BASE_URL` / `LITELLM_API_KEY` / `LITELLM_MODEL` | — | LLM เขียนคอนเทนต์ (เว้นว่าง = template) |

## โครงสร้างโปรเจกต์
```
app/db.py          SQLite: tenants, brands, target_questions, monitoring_runs,
                   run_results, content_items, wp_connections (+ migrations)
app/geo_worker.py  engine ค้น+วิเคราะห์ SoV · search backend (ddgs/brave/serper)
app/geo_content.py generator คอนเทนต์ ไทย/อังกฤษ + JSON-LD + llms.txt/robots/org schema
app/wp_client.py   WP REST + Connector publish · เข้ารหัส credential (Fernet)
app/main.py        FastAPI: auth, brand/question CRUD, run, report, admin,
                   content, publish, progress, auto-run scheduler
app/templates/     login, dashboard, brand, run, admin, content, assets, progress
create_admin.py    CLI สร้าง/รีเซ็ต admin
run_monitors.py    CLI batch monitoring (cron/scheduler)
wordpress-plugin/jor-ai-connector/   ปลั๊กอิน WP (Phase C) + TESTING.md
*_smoke.py         regression tests
```

## ทดสอบ (regression)
รันแต่ละไฟล์ด้วย venv python โดยชี้ DB ชั่วคราว (ไม่กระทบ db จริง):
```powershell
$env:PYTHONPATH="C:\GEO PLATFORM\geo-platform"; $env:GEO_DB_PATH="$PWD\_t.db"
.\.venv\Scripts\python.exe smoke_test.py       # monitor end-to-end
.\.venv\Scripts\python.exe admin_smoke.py      # admin god-view + ปิดสมัคร
.\.venv\Scripts\python.exe content_smoke.py    # generate คอนเทนต์ + schema + assets
.\.venv\Scripts\python.exe wp_smoke.py         # WP REST publish (mock)
.\.venv\Scripts\python.exe connector_smoke.py  # Connector mode + schema push (mock)
.\.venv\Scripts\python.exe progress_smoke.py   # หน้า progress ก่อน/หลัง
.\.venv\Scripts\python.exe search_smoke.py     # เลือก search backend + fallback
.\.venv\Scripts\python.exe run_smoke.py        # batch job selection
Remove-Item _t.db -ErrorAction SilentlyContinue
```

## Roadmap (เหลือ — งาน scale/go-live)
- [ ] ย้าย SQLite → Postgres + ผูกบิลลิ่งต่อ tenant
- [ ] เปิด public ผ่าน Caddy + โดเมน + security review
- [ ] ทดสอบปลั๊กอิน Connector บน WordPress จริง (ดู TESTING.md)
