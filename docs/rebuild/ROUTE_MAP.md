# ROUTE_MAP — เจอ.AI (stage 00)

สถานะ: ตรวจจากโค้ด `main` @ a35405f (2026-11-14) · อ่านอย่างเดียว ไม่มีการแก้ route
เลขบรรทัดหมายถึง `app/main.py` ถ้าไม่ระบุไฟล์อื่น

## ภาพรวม
- ~250 route: `main.py` ~232, `crm_web.py` (APIRouter 10 route, mount ที่ 4888), `staff_web.py` (7 route, mount ที่ 4892), `/static` mount ที่ 82
- แยกตาม prefix: `/admin*` 118 · tenant (`/brands`, `/content`, `/questions`, `/runs`) 54 · อื่นๆ 77

## 1. Public marketing
| Method | Path | Handler / บรรทัด | หมายเหตุ |
|---|---|---|---|
| GET | `/` | `landing` 554 | redirect ไป `/en` ฯลฯ ถ้า cookie `jr_lang` ไม่ใช่ th |
| GET | `/en` `/zh` `/ja` `/ko` | 563–578 → `_landing_page` 531 | render `landing_v2.html` |
| GET | `/preview/landing-old` | 584 | header `X-Robots-Tag: noindex` |
| GET | `/preview/landing` | 633 | redirect `/` |
| POST | `/api/contact` | 638 | JSON + honeypot |
| GET/POST | `/audit` | 696 / 701 | ตรวจเว็บฟรี · quota 5/IP/วัน, 20/บัญชี/วัน (`leads.py:36-38`, เช็กที่ 715) |
| GET/POST | `/partners` | 4163 / 4168 | หน้าพาร์ตเนอร์ |
| GET/POST | `/login` | 482 / 487 | |
| GET/POST | `/register` | 504 / 509 | redirect `/login` (ปิดสมัครเอง) |
| GET | `/logout` | 514 | |
| GET | `/manifest.webmanifest`, `/sw.js` | 1446, 1503 | PWA / service worker |
| GET | `/plugin/jor-ai-connector.zip` | 3465 | |
| POST | `/chat/ask`, `/chat/contact` | 4232, 4254 | web salesbot (JSON) |

## 2. Public token links — **สัญญาที่ต้องรักษา (ส่งทางอีเมล/พิมพ์ลง PDF แล้ว)**
| กลุ่ม | Routes | บรรทัด |
|---|---|---|
| Audit | GET `/audit/{token}` · GET `/audit/{token}/progress` (JSON) · POST `/audit/{token}/contact` · POST `/audit/{token}/reply` | 749, 762, 773, 999 |
| Unsubscribe | GET/POST `/u/{token}` | 1015 / 1023 |
| Quote | GET `/q/{token}` · `.pdf` · `/contract` · `/contract.pdf` · POST `/cycle` `/stripe` `/accept` `/pay` (slip) | 1057, 1033, 1049, 1041, 1071, 1083, 1121, 1141 |
| Receipt | GET `/doc/{token}` · `.pdf` | 4541, 4535 |
| Invoice | GET `/bill/{token}` · `.pdf` · POST `/bill/{token}/pay` | 4553, 4547, 4559 |
| Partner | GET `/p/{code}` (ตั้ง cookie `jr_ref` 60 วัน → `/audit?src=partner&ref=`) · GET `/partner/{token}` · POST `/partner/{token}/client` · GET/POST `/partner/{token}/unsubscribe` | 684, 4191, 4199, 4211/4222 |

Token: lead/audit/unsubscribe/quote ใช้ `secrets.token_urlsafe(9)` (`leads.py:229`, `pipeline.py:36,103,393`) · เอกสารบัญชี `token_urlsafe(9)` (`accounting.py:200`) · partner token `token_urlsafe(12)` (`db.py:1931`) · partner code `token_hex(4)` (`partners.py:78`) · `embed_key` `token_urlsafe(16)` (`db.py:567,889`)
**ไม่มีวันหมดอายุ** ยกเว้นใบเสนอราคา (`quotes.is_expired`, `quotes.py:235`, ดู `valid_until`) · quote draft ซ่อนจากคนที่ไม่ใช่ admin (1060) · pay/stripe ต้องสถานะ accepted/claimed · `?paid=1` เรียก `stripe_pay.sync_session`

## 3. Customer-domain / embed (GET+HEAD) — ห้ามเปลี่ยน (ลูกค้าตั้ง rewrite ไว้แล้ว)
- ต่อแบรนด์ด้วย `embed_key`: `/e/{key}.js` 3194 · `/e/{key}/llms.txt` 3222 · `/robots.txt` 3235 · `/sitemap.xml` 3246 · `/head.html` 3259 · `/a/indexnow.txt` 3284 · `/a/` 3311 · `/a/{cid}` 3328 · `/content.json` 3446
- โดเมนตัวเอง (setting `self_brand_id`): `/geo` 3376 · `/geo/{cid}` 3382 · `/geo/indexnow.txt` 3293 · `/llms.txt` 3411 · `/geo-sitemap.xml` + `/sitemap.xml` 3426–3427 · `/robots.txt` 3436
- CORS `*` บน sitemap.xml 3253, head.html 3266, hosted 3325/3364, content.json 3462

## 4. Webhooks (URL ลงทะเบียนกับระบบภายนอกแล้ว)
| Path | บรรทัด | ตรวจ |
|---|---|---|
| POST `/stripe/webhook` | 1100 | `stripe_pay.verify` |
| POST `/line/webhook` | 1402 | `line_oa.verify` (HMAC) · ยังไม่กัน event ซ้ำ (`webhookEventId`) |
| POST `/resend/webhook` | 4425 | Svix signature |
| POST `/inbox/hook?secret=` | 4872 | `hmac.compare_digest` |

## 5. Customer (tenant)
`/app` 1510 · `/notifications` 1526 · `/account/password` 1419/1427 · `/account/billing` 1547 · `/upgrade` 1562/1643 · `/upgrade/pay/{item}` 1589/1606 · `/api/classify-market` 1659 · `/seed-demo` 1696 · ~54 route `/brands/{id}/...` และ `/content/{id}` (1682–3483: monitor, ai, gsc, speed, ai-serp, report(.pdf), export csv, chat, autopilot, rank, wp, theme, assets, impact)

## 6. Admin / staff
118 route (803–4859) + `crm_web.py:53-180` (`/admin/deals*`) + `staff_web.py:31-131` (`/admin/staff*`) · หน้าหลัก `/admin` 3554, `/admin/leads*`, `/admin/quotes/{qid}/*` 1311–1364, `/admin/settings*` 3679–3961, `/admin/accounting*` 4606–4664, `/admin/partners` 4368/4387, `/admin/ceo*`, `/admin/office*`, `/admin/inbox*`, `/admin/deliverability*`, `/admin/email*` · JSON: `/admin/office/pending.json` 4112, `/admin/office/state.json` 4131, `/admin/ceo/send.json` 4783, `/admin/ceo/log.json` 4799

## 7. Locale / canonical / index
- ภาษา th/en/zh/ja/ko (`landing_i18n.py:8`) · มี URL prefix เฉพาะหน้า landing (`PATHS` บรรทัด 9)
- หน้าอื่น: `?lang=xx` → cookie `jr_lang` 365 วัน ผ่าน middleware `_remember_lang` (98) · `i18n.lang_of` (`i18n.py:37`) ลำดับ query → cookie → request.state → th
- `landing_v2.html:9-11` มี canonical + hreflang + x-default · sitemap ใส่ hreflang (`_self_pages` 3403, `geo_content.py:509-525`)
- `robots.txt` disallow `SELF_DISALLOW` (3389) ครอบ `/admin /app /brands /account /upgrade /q/ /bill/ /doc/ /audit/ /u/ /p/ /partner/ /e/ /chat/ /preview/` ฯลฯ
- meta noindex มีใน `acct_doc, admin_office, audit_result, contract, partner_dash, quote, unsubscribe` · **`base.html` และ `login.html` ไม่มี** · middleware `_no_cache_html` (86) ใส่ no-store ให้ HTML ที่ไม่ได้ตั้ง Cache-Control

## 8. ข้อสังเกตสำหรับขั้นถัดไป
- route ใหม่ที่เอกสารแผนเสนอ (`/how-it-works`, `/methodology`, `/services/*`, `/solutions/*`, `/sample-report`, `/clinics`, `/book`, `/learn`) **ยังไม่มี** — ไม่ชนกับ route เดิม แต่ `/geo/*` ถูกใช้แล้วเป็นหน้าคอนเทนต์ของโดเมนตัวเอง → หน้าความรู้ใหม่ห้ามใช้ `/geo`
- `/partners` มีอยู่แล้ว (evolve ได้ ห้ามทำลาย `/p/{code}` และ `/partner/{token}`)
- `/audit` คือทางเข้าตรวจเว็บปัจจุบัน → stage 02/04 ควรต่อยอด ไม่สร้าง route คู่ขนาน
- ไม่มี CSRF ทุก POST (ดู CURRENT_STATE.md)
