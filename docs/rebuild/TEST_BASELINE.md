# TEST_BASELINE — stage 00

## ผลการรัน (2026-11-14, หลังแก้ 00S)
- เครื่อง: Windows 11, Python 3.13.17 (ติดตั้งผ่าน winget ตามที่เจ้าของอนุญาต), `.venv` ในราก repo (git ignore อยู่แล้ว), `pip install -r requirements.txt` (fastapi 0.162.7, httpx 0.28.1)
- env: ไม่มี `.env`, ไม่ตั้ง `DATABASE_URL`, `GEO_DB_PATH` เป็นไฟล์ temp ใหม่ทุกไฟล์, `AI_BASE_URL=http://127.0.0.1:9`, `LITELLM_BASE_URL=""`, `GEO_AUTORUN=0`, `GEO_SEARCH_BACKEND=ddgs`, `PYTHONUTF8=1`
- **ผล: 63/63 ผ่าน, 0 ล้ม** = `security_00s_smoke.py` (ใหม่) + smoke เดิมทุกไฟล์ 62 ไฟล์ · ไฟล์ที่ช้าสุด 17 วินาที
- **ไม่ได้รัน (ตั้งใจ):** `smoke_test.py` (ค้น ddgs จริง + เขียน `geo_platform.db` ของ repo), `pg_smoke.py` (ต้องมี Postgres จริง)
- **ข้อควรรู้:** รันโดยไม่ได้ตัดเน็ต จึงยังไม่ได้พิสูจน์ว่าไม่มีเทสต์ไหนแอบออกเน็ต (เช่นค้น ddgs หรือดึงเว็บที่ไม่ได้ mock) · ไม่มีผลฐานจาก `main` ก่อนแก้ให้เทียบ แต่เมื่อทุกไฟล์ผ่านจึงไม่มี regression ที่ชุดเทสต์นี้จับได้
- คำสั่งที่ใช้: ดูหัวข้อ "สูตรรันอย่างปลอดภัย" ด้านล่าง (วนทุก `*_smoke.py` ยกเว้นสองไฟล์ข้างบน, timeout 300 วินาทีต่อไฟล์)

## รูปแบบเทสต์ที่มี
- ~70 ไฟล์ `*_smoke.py` ที่ราก repo + `smoke_test.py` · เป็นสคริปต์ assert ธรรมดา (ไม่ใช่ pytest) · รันจากราก repo: `python xxx_smoke.py`
- 46 ไฟล์ใช้ `TestClient(app)` แบบไม่มี `with` → startup event ไม่ทำงาน (ตั้งใจ, `app/main.py:128-129`) จึงไม่เปิด scheduler
- mock ด้วยการแทนค่าตรงๆ เช่น `crm_smoke.py:10-30`: `ai._chat`, `pipeline.alert_admins`, `mailer.send`, `line_oa._post`, `site_health.run_checks`, `pipeline.RUN_INLINE=True`

## การแยก DB
- `app/db.py:12-14`: `DATABASE_URL` ขึ้นต้น `postgres` → ใช้ Postgres · ไม่งั้น SQLite ที่ `GEO_DB_PATH` (ค่าเริ่มต้น `<repo>/geo_platform.db`)
- `app/main.py:13`, `run_monitors.py:31` โหลด `<repo>/.env` ตอน import → **ถ้ามี `.env` ที่ตั้ง `DATABASE_URL` เทสต์จะวิ่งเข้า Postgres นั้น** (ตอนนี้ไม่มี `.env` ใน repo)
- ไฟล์ส่วนใหญ่: `if not os.getenv("DATABASE_URL"): GEO_DB_PATH=<temp>` → ปลอดภัยเฉพาะเมื่อไม่ได้ตั้ง `DATABASE_URL`
- ตั้ง temp DB ทุกกรณี: theme, staff, self_site, salesbot, results, landing, i18n, grounding, email_i18n, customer_lead, agency_selfserve

## ความเสี่ยงผลข้างเคียงขาออก
| ทาง | สถานะเมื่อ DB ว่าง + ไม่มี .env |
|---|---|
| LINE / SMTP / SMS / Stripe | ส่งไม่ได้ (ไม่มี credential ใน DB settings) |
| **LLM `ai_client._chat`** | **ยิงได้** — POST ไป `AI_BASE_URL` ค่าเริ่มต้น `https://consoletoken.aunjai.org/api/v1` โดยไม่เช็ก `AI_API_KEY` (`app/ai_client.py:5-21`) |
| **ค้นเว็บ ddgs** | **ยิงได้** — backend ฟรีเป็นค่าเริ่มต้น (`app/geo_worker.py:111`) |
| PageSpeed | ยิงได้แม้ไม่มีคีย์ (`app/pagespeed.py:102`) |
| ดึงเว็บลูกค้า (site_health/deep_audit) | ยิงได้ถ้าไม่ mock |
| IndexNow | ปิด (`app/indexnow.py:19`, setting ไม่มี) |
| scheduler ในแอป | ปิด เว้นแต่ `GEO_AUTORUN=1` (`app/main.py:283`) |

## ไฟล์อันตราย — ห้ามรันใน baseline
- `smoke_test.py` — ค้น ddgs จริง และเขียน `<repo>/geo_platform.db`
- `pg_smoke.py` — ต้องมี Postgres จริง

## สูตรรันอย่างปลอดภัย (ใช้ใน stage ถัดไปเมื่อมี Python 3.13)
```bash
# จากราก repo, ไม่มีไฟล์ .env, ไม่ตั้ง DATABASE_URL
unset DATABASE_URL
export GEO_DB_PATH="$(mktemp -d)/smoke.db"
export AI_BASE_URL="http://127.0.0.1:9"     # ทำให้ LLM ที่หลุด mock ล้มทันที ไม่ออกเน็ต
export LITELLM_BASE_URL=""
export GEO_AUTORUN=0 GEO_SEARCH_BACKEND=ddgs
python -m venv .venv && .venv/bin/pip install -r requirements.txt   # Python 3.13 เท่านั้น
for f in aiserp speed report health gsc email autopilot auto_content image search bot_access \
         grounding i18n self_site theme trial admin billing llm wp connector; do
  .venv/bin/python ${f}_smoke.py && echo "PASS $f" || echo "FAIL $f"
done
```
ชุดแรกนี้ (ไม่มี DB หรือ temp DB, mock ครบตามที่อ่าน) ควรเป็น baseline · ชุดใหญ่ (deep, maps, marketing, prospect, sms, stripe, deliv, crm, sales_agency, phase2) mock หลายจุด ต้องรันแบบตัดเน็ต (firewall/ไม่มี network) ครั้งแรกเพื่อยืนยันว่าไม่หลุด
**ข้อเสนอสำหรับ stage 01:** เพิ่ม guard ใน harness ทดสอบ (เช่น ปฏิเสธการรันถ้ามี `DATABASE_URL` ที่ไม่ใช่ localhost/test) — ต้องให้เจ้าของอนุมัติก่อนเพราะแก้ไฟล์เทสต์

## สิ่งที่ยังไม่ได้ยืนยัน
- เทสต์ทุกไฟล์ผ่านหรือไม่ในโค้ดปัจจุบัน
- Postgres compatibility (`pg_smoke.py`)
- browser / accessibility / performance — ยังไม่ได้ตรวจเลย
