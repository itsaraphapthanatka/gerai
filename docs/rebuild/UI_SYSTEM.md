# UI_SYSTEM — หน้าเว็บสาธารณะชุดใหม่ (stage 01)

เจ้าของเลือก (ประตู 1, 2026-11-14): **ต่อยอดแบรนด์เดิม** — ไม่เปลี่ยนสี ฟอนต์ หรือทรงปุ่ม เพิ่มชิ้นส่วนแบบ "แฟ้มหลักฐาน"
ตัวอย่างภาพ: `docs/rebuild/previews/01-ui-kit.html` · หน้าในแอป: `/preview/ui-kit` (แอดมิน + คุกกี้ `jr_preview=marketing_v2`)

## ไฟล์
| ไฟล์ | หน้าที่ |
|---|---|
| `app/templates/_ds.html` (เดิม) | โทเคนแบรนด์ + ฟอนต์ Google — ใช้ร่วมกับหน้าเดิมทั้งหมด |
| `app/templates/mk/_styles.html` | สไตล์ชุดใหม่ ทุกกฎอยู่ใต้ `body.mk` ไม่กระทบหน้าแอป/หลังบ้าน/หน้า hosted |
| `app/templates/mk/base.html` | โครงหน้า: skip link, nav, main, footer, block `actbar` · `preview=True` → meta noindex |
| `app/templates/mk/_nav.html`, `_footer.html` | เมนูแยกทางธุรกิจ/พาร์ตเนอร์ · ลิงก์เฉพาะ route ที่มีจริง |
| `app/templates/mk/_ui.html` | macro ชิ้นส่วน (ด้านล่าง) |
| `app/marketing_web.py` | router หน้าใหม่ หลัง flag `marketing_v2` |

## โทเคน
| โทเคน | ค่า | ใช้กับ |
|---|---|---|
| `--mk-ink` (= `--primary`) | #0a0a0a | ข้อความ, ปุ่มหลัก |
| `--mk-coral` / `--mk-coral-deep` | #ff5530 / #c8341a | จุดเน้นเท่านั้น (โลโก้, eyebrow, เส้นอ้างอิง, focus) — **ห้ามใช้เป็นสีสถานะ** |
| `--mk-slate`, `--mk-hair`, `--mk-surface` | #45515e, #e5e7eb, #f7f8fa | ข้อความรอง, เส้น, พื้นรอง |
| `--mk-found` | #1c7a44 | พบแล้ว |
| `--mk-absent` | #8a5a12 | ไม่พบ (ทดสอบแล้ว), ได้บางส่วน |
| `--mk-untested` | #5f6670 | ยังไม่ได้ทดสอบ |
| `--mk-down` | #b42318 | ระบบไม่พร้อม, ข้อผิดพลาดฟอร์ม |
| ฟอนต์ | DM Sans + IBM Plex Sans Thai (จาก `_ds.html`) | ทุกข้อความ · ตัวเลข/URL ใช้ monospace ของระบบ |
| radius | ปุ่ม 9999px, การ์ด 14px, ช่องกรอก 12px | |

## ตัวอักษร
h1 `clamp(28px,5vw,44px)` 700 · h2 `clamp(22px,3vw,30px)` 700 · h3 17px 600 · เนื้อหา 16px line-height 1.65 · ย่อหน้ากว้างสุด 66ch · หัวเรื่องใช้ `text-wrap: balance`

## ชิ้นส่วน (`{% import "mk/_ui.html" as ui %}`)
| macro | ใช้ | กติกา |
|---|---|---|
| `ui.button(label, href, kind, type, disabled_reason, id)` | kind = primary / secondary / text | หนึ่งการตัดสินใจ มีปุ่ม primary ปุ่มเดียว · ปุ่มกดไม่ได้ต้องมี `disabled_reason` แสดงข้าง ๆ |
| `ui.status(state, text)` | state ตาม METRIC_DICTIONARY | สถานะที่ไม่รู้จัก → "ยังไม่ได้ทดสอบ" (ไม่ใช่ "ไม่พบ") · แยกด้วยรูปทรง+คำ ไม่ใช่สีอย่างเดียว |
| `ui.lane(title, state, detail)` | การ์ดตัวชี้วัด 1 ช่อง | not_tested/unavailable ห้ามมีตัวเลข |
| `ui.coverage(tested, total)` | แถบความครอบคลุม | ไม่ใช่คะแนน |
| `ui.evidence(excerpt, url, captured_at, finding_state, finding, index, of)` | หลักฐาน | ข้อความจากเว็บที่ crawl ถูก escape เสมอ |
| `ui.action(n, title, page, confidence, effort, owner, evidence_href, draft_href)` | สิ่งที่ควรทำ | |
| `ui.fact(label, value, source)` | ข้อมูลธุรกิจ | source = confirmed / inferred · ไม่มีค่า = "ยังไม่ทราบ" |
| `ui.field(id, label, ...)` | ช่องกรอก | label ผูก id เสมอ · error ใช้ `aria-invalid` + `aria-describedby` |
| `ui.empty(title, body, cta_label, cta_href)` | หน้าว่างที่บอกความจริง | ห้ามใส่ของตัวอย่างแทนของจริง |
| `ui.media(alt, src, poster, video, captions, title, duration)` | ภาพ/วิดีโอ | ไม่มีวิดีโอที่อนุมัติ = ข้อความ/ภาพนิ่ง ไม่มีปุ่มเล่นปลอม · วิดีโอไม่เล่นเอง `preload="none"` |
| `ui.faq(items)` | คำถามพบบ่อย | `<details>` ใช้ได้ไม่ต้องมี JS |
| block `actbar` | แถบปุ่มล่างจอ | แสดงเฉพาะจอกว้าง ≤720px · sticky ไม่ลอยทับเนื้อหา |

## ข้อห้ามทางภาพ
ตัวเลขวิ่ง · โลโก้/รีวิวที่ไม่ได้รับอนุญาต · % ความคืบหน้าที่ไม่ได้วัด · ปุ่ม play ปลอม · หุ่นยนต์/นีออน · glass/gradient หนัก · animation ที่ไม่จำเป็น (และหยุดทั้งหมดเมื่อ `prefers-reduced-motion`)

## การใช้ร่วมกับ `_ds.html`
`_ds.html` ทาสีปุ่ม `<button>` ทุกปุ่มเป็นสีดำ ยกเว้นที่มีคลาส ghost/outline/alt/danger → macro ใส่ `outline` ให้ปุ่มรอง และ `ghost` ให้ปุ่มข้อความแล้ว · ลิงก์ใน `body.mk` ใช้ `:where()` เพื่อไม่ทับสีของปุ่มที่เป็นลิงก์

## สิ่งที่ตรวจแล้ว (2026-11-14)
- `mk_smoke.py`: flag ปิด → 404 สำหรับทุกคนยกเว้นแอดมินที่ขอ preview · มี noindex ทั้ง header และ meta · ทุก input มี label · ลิงก์ในหน้าเปิดได้จริง · escape ข้อมูลภายนอก · หน้าเดิม `/ /audit /partners /login` ไม่เปลี่ยน
- เบราว์เซอร์ (หน้า preview ที่ render จากแอปจริง): ไม่มีเลื่อนแนวนอนที่ 360 / 390 / 768 / 1280 px · แถบปุ่มล่างจอแสดงเฉพาะจอแคบ · พบและแก้บั๊กปุ่มลิงก์สีดำมองไม่เห็นข้อความ
- **ยังไม่ได้ตรวจ:** screen reader จริง, การไล่ Tab ทีละจุดด้วยคีย์บอร์ด (มี focus ring ใน CSS แต่ยังไม่ได้กดทดสอบ), คอนทราสต์ด้วยเครื่องมือวัด, ความเร็ว/Lighthouse, Safari/iOS
