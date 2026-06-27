# คู่มือทดสอบ เจอ.AI Connector บน WordPress จริง

ทำบน **เว็บทดสอบ/staging** ก่อน (LocalWP, XAMPP, หรือ subdomain staging) — อย่าเพิ่งลงเว็บลูกค้า production
ต้องการ: WordPress 5.6+, สิทธิ์ admin, เปิด HTTPS (แนะนำ), ปลั๊กอิน Rank Math (ถ้าจะทดสอบ meta)

---

## 0) ติดตั้ง
1. ZIP โฟลเดอร์ `jor-ai-connector` (ให้ไฟล์ `.php` อยู่ในโฟลเดอร์ชั้นเดียว)
2. WP Admin → Plugins → Add New → Upload Plugin → เลือก zip → Install → **Activate**
   (หรือก๊อปโฟลเดอร์ไป `wp-content/plugins/` แล้ว Activate)
3. ✅ ผ่านถ้า: ไม่มี error, เห็นเมนู Settings → "เจอ.AI Connector"

## 1) ตั้งค่า
1. Settings → เจอ.AI Connector
2. **API Key**: ก๊อปค่าที่แนะนำ (หรือพิมพ์เอง ≥ 20 ตัว) → จำไว้ใช้ฝั่งแพลตฟอร์ม
3. **Schema องค์กร**: วาง JSON-LD จากแพลตฟอร์ม (หน้าแบรนด์ → ชุดติดตั้ง on-site → Schema องค์กร)
4. **llms.txt**: วางจากหน้าเดียวกัน
5. เปิด **AI crawler** → Save Changes
6. ✅ ผ่านถ้า: บันทึกแล้วค่ายังอยู่ครบ

## 2) /llms.txt
- เปิด `https://SITE/llms.txt`
- ✅ ผ่านถ้า: เห็นข้อความที่ตั้งไว้ (Content-Type: text/plain)
- ❌ ถ้า 404 → Settings → Permalinks → **Save Changes** (รีเฟรช rewrite) แล้วลองใหม่

## 3) robots.txt
- เปิด `https://SITE/robots.txt`
- ✅ ผ่านถ้า: เห็นบล็อก `User-agent: GPTBot ... Allow: /` และ ClaudeBot/PerplexityBot/Google-Extended
- หมายเหตุ: ใช้ได้กับ WP ที่เสิร์ฟ robots.txt แบบ virtual (ไม่มีไฟล์ robots.txt จริงบน disk)

## 4) Schema ใน <head>
- เปิดหน้าแรก → View Source (Ctrl+U) → ค้น `application/ld+json`
- ✅ ผ่านถ้า: เห็น `<script type="application/ld+json">` ที่มี schema องค์กร

## 5) REST — ping (ยืนยัน API key)
```bash
curl -s -H "X-JorAI-Key: ใส่คีย์ที่ตั้งไว้" https://SITE/wp-json/jor-ai/v1/ping
```
- ✅ ผ่านถ้า: ได้ `{"connected":true,"plugin":"jor-ai-connector","version":"0.1.0"}`
- ทดสอบคีย์ผิด → ควรถูกปฏิเสธ (401/403):
```bash
curl -s -o /dev/null -w "%{http_code}\n" -H "X-JorAI-Key: wrong" https://SITE/wp-json/jor-ai/v1/ping
```

## 6) REST — publish (สร้างร่าง + schema)
```bash
curl -s -X POST https://SITE/wp-json/jor-ai/v1/publish \
  -H "X-JorAI-Key: ใส่คีย์" -H "Content-Type: application/json" \
  -d '{"title":"ทดสอบ Connector","content_html":"<h2>หัวข้อ</h2><p>เนื้อหา</p>","status":"draft","schema_json":"{\"@context\":\"https://schema.org\",\"@type\":\"FAQPage\"}","meta_title":"t","meta_desc":"d"}'
```
- ✅ ผ่านถ้า: ได้ `{"ok":true,"id":NN,"link":"...","status":"draft"}`
- ตรวจใน WP Admin → Posts → เห็นร่างชื่อ "ทดสอบ Connector"
- เปิด Preview ร่าง → View Source → เห็น JSON-LD ของ FAQPage (มาจาก post meta `_jorai_schema`)
- (ถ้ามี Rank Math) ตรวจ SEO title/description ของโพสต์ = ค่าที่ส่งไป

## 7) จับคู่กับแพลตฟอร์ม (end-to-end)
1. แพลตฟอร์ม → หน้าแบรนด์ → เชื่อมต่อ WordPress → กรอก URL + user + app password + **Connector API Key เดียวกับใน WP**
2. ✅ ผ่านถ้า: หน้าแบรนด์ขึ้น "เชื่อมต่อแล้ว · โหมด: Connector"
3. สร้างคอนเทนต์ → เปิดหน้าคอนเทนต์ → "ส่งเข้า WordPress (เป็นร่าง)"
4. ✅ ผ่านถ้า: ขึ้น "เผยแพร่แล้ว" + ลิงก์ + ใน WP มีร่าง + view source มี schema ของหน้านั้น

## 8) ตรวจ schema ให้ถูกต้อง
- เอา URL ร่าง (preview) ไปเช็คที่ Google Rich Results Test หรือ validator.schema.org
- ✅ ผ่านถ้า: detect FAQPage/Organization ไม่มี error

---

## Troubleshooting
- **ping ได้ 401 ทั้งที่คีย์ถูก**: บาง host (Apache) ตัด header `Authorization`/custom — ลองเพิ่มใน `.htaccess`:
  `SetEnvIf Authorization "(.*)" HTTP_AUTHORIZATION=$1` หรือใช้ปลั๊กอินที่ส่ง header ผ่าน
- **/llms.txt 404**: Settings → Permalinks → Save (flush rewrite)
- **REST ถูกบล็อก**: ปลั๊กอิน security (Wordfence ฯลฯ) อาจบล็อก REST/REST ของ non-user — whitelist `jor-ai/v1`
- **schema ไม่ขึ้นใน head**: เช็คว่าธีมเรียก `wp_head()` (ทุกธีมมาตรฐานเรียก)
- **<script> หาย**: ปกติ — schema ถูกเก็บใน meta แล้ว render ผ่าน wp_head (ไม่ได้อยู่ใน content ที่ kses ตัด)

## เกณฑ์ผ่านรวม (checklist)
- [ ] Activate ได้ ไม่มี error
- [ ] /llms.txt + /robots.txt + schema ใน head ขึ้นครบ
- [ ] ping ok (คีย์ถูก) / ปฏิเสธ (คีย์ผิด)
- [ ] publish สร้างร่าง + schema ใน meta + (Rank Math) meta
- [ ] จับคู่แพลตฟอร์ม → publish ผ่าน Connector ได้
- [ ] schema validate ผ่าน
