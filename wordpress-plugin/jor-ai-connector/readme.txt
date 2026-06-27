=== เจอ.AI Connector ===
Requires at least: 5.6
Tested up to: 6.x
Stable tag: 0.1.0
License: GPLv2 or later

เชื่อมเว็บ WordPress กับแพลตฟอร์ม เจอ.AI (GEO) — เติมส่วนที่ WP REST แกนทำไม่ได้

== ทำอะไร ==
* Inject Schema JSON-LD ทั้งเว็บ (Organization) ใน <head> + per-post schema (เก็บใน meta `_jorai_schema` → render ผ่าน wp_head เลี่ยง kses ที่ตัด <script> ในเนื้อหา)
* เสิร์ฟ /llms.txt
* เปิดทาง AI crawler ใน robots.txt (GPTBot/ClaudeBot/PerplexityBot/Google-Extended ฯลฯ)
* REST endpoints ให้แพลตฟอร์ม push คอนเทนต์ + schema + Rank Math meta:
  - GET  /wp-json/jor-ai/v1/ping     (auth: header X-JorAI-Key)
  - POST /wp-json/jor-ai/v1/publish  (title, content_html, status, schema_json, meta_title, meta_desc, post_id?)

== ติดตั้ง ==
1. ZIP โฟลเดอร์ jor-ai-connector แล้วอัปโหลดที่ Plugins → Add New → Upload (หรือก๊อปไปที่ wp-content/plugins/)
2. Activate
3. Settings → เจอ.AI Connector → ใส่ API Key (ใช้ค่าที่แนะนำได้) + (ออปชัน) Schema องค์กร / llms.txt
4. กรอก API Key เดียวกันในแพลตฟอร์ม เจอ.AI (หน้าแบรนด์ → เชื่อมต่อ WordPress → ช่อง Connector API Key)
5. ถ้า /llms.txt ขึ้น 404 ให้ไป Settings → Permalinks กด Save (รีเฟรช rewrite)

== หมายเหตุ ==
* publish ส่งเป็น "draft" โดยค่าเริ่มต้น ให้รีวิวก่อนเผยแพร่จริง
* บาง host ต้องตั้งให้ส่ง header Authorization/Custom ผ่าน (ถ้า REST ถูกบล็อก)
