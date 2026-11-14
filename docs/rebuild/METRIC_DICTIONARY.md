# METRIC_DICTIONARY — นิยามตัวชี้วัดชุดเดียวของทั้งระบบ

ทุกขั้น (05, 11, 16, 17 ฯลฯ) อ้างไฟล์นี้ ไม่นิยามใหม่ · ข้อความที่ลูกค้าเห็นต้องใช้ "ชื่อไทย" ในตาราง

## 1. หกตระกูลตัวชี้วัด (ห้ามรวมหรือสลับชื่อกัน)
| key | ชื่อไทยที่แสดง | วัดอะไร | แหล่งในโค้ดปัจจุบัน |
|---|---|---|---|
| `website_readiness` | ความพร้อมของเว็บ | หน้าเว็บอ่านได้ไหม บอกชัดไหมว่าทำอะไร ที่ไหน มี schema, robots, index ฯลฯ | `site_health`, `deep_audit` tier 1, `leads.score_audit` |
| `web_search_presence` | การปรากฏในผลค้นเว็บ | โดเมน/ชื่อแบรนด์โผล่ในผลค้น (ddgs/brave/serper) ต่อคำถาม | `geo_worker` SoV, `rank_results` |
| `ai_brand_mention` | AI พูดถึงแบรนด์ | คำตอบ AI เอ่ยชื่อแบรนด์ | `ai_visibility` (named), `ai_serp` |
| `ai_citation` | AI อ้างอิงเว็บของคุณ | คำตอบ AI ลิงก์/อ้างเว็บของแบรนด์ | `ai_visibility` (cited), `ai_serp` |
| `search_console_performance` | ข้อมูลจาก Google Search Console | index, clicks, impressions, position จาก GSC จริง | `gsc_snapshots` |
| `business_outcomes` | ผลทางธุรกิจ | lead, นัด, ดีล, รายได้ จากแหล่งที่เชื่อถือได้ | leads/quotes/acct_docs (stage 17) |

**กฎ:** SoV ปัจจุบัน = `web_search_presence` เท่านั้น ห้ามเรียก "SoV บน AI" / "โผล่ใน AI" (แก้ข้อความใน stage 05 — จุดที่พบ: `rank.html:84`, `brand.html:56,83`, `db.py:1402` docstring, `main.py:1451`)

## 2. สถานะของผลวัด (ใช้กับทุกตระกูล)
| state | ชื่อไทย | ความหมาย | แสดงเป็นตัวเลข? |
|---|---|---|---|
| `found` | พบแล้ว | ทดสอบสำเร็จและพบ | ✓ |
| `absent` | ไม่พบ (ทดสอบแล้ว) | ทดสอบสำเร็จแต่ไม่พบ | ✓ (นับ 0 ได้) |
| `not_tested` | ยังไม่ได้ทดสอบ | ไม่ได้อยู่ในขอบเขตรอบนี้ / ไม่ได้ตั้งคีย์ | **ห้าม** — แสดง `null` |
| `unavailable` | ระบบไม่พร้อม | ผู้ให้บริการล่ม / timeout / เว็บเปิดไม่ได้ | **ห้าม** — แสดง `null` และเหตุผล |
| `partial` | ได้บางส่วน | บางคำถาม/บางหน้าล้ม | ✓ พร้อมตัวหารที่ลดลง |

ห้าม `or 0` กับค่าที่เป็น `not_tested`/`unavailable` (จุดที่พบ: `report.py:59-61,77-80,100-102,111`, `geo_worker.py:287-290,309`)

## 3. ทุกตัวเลขต้องพกข้อมูลนี้
`protocol_id` + `protocol_version` · `provider` (เช่น ddgs, serper, openai, serpapi) · `method` · `measured_at` · `attempted` · `succeeded` · `failed` · `denominator` (= succeeded ที่นับได้) · `evidence_refs[]`

## 4. การเปรียบเทียบข้ามรอบ
- เทียบได้ (`comparable`) เมื่อ protocol_id, provider และชุดคำถามเหมือนกัน
- เปลี่ยน provider หรือคำถามบางส่วน → `qualified` (แสดงเชิงอรรถ)
- เปลี่ยนวิธีวัด → `non_comparable` (ห้ามต่อเส้นกราฟ)
- ข้อมูลเก่าที่เป็นผลค้นเว็บ (SoV ก่อน rebuild) → ติดป้าย `legacy_search_only` ห้ามแปลงเป็นตัวเลข AI

## 5. คำที่ห้ามใช้
"การันตี", "รับประกันอันดับ", "AI จะแนะนำคุณ", ตัวเลขยอดขายที่เสียไปโดยไม่มีหลักฐาน, % ความคืบหน้าที่ไม่ได้วัดจริง
