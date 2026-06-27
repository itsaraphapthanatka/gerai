# Deploy GEO Platform บน VPS (Ubuntu + Nginx + systemd + Docker Postgres)

เป้าหมาย: `https://geo.appreview.cloud` → Nginx → FastAPI (127.0.0.1:**8095**) → Postgres (docker, 127.0.0.1:5434)
แอปเป็น **FastAPI ตัวเดียว** (ไม่ใช่ Next.js) — ใช้แค่พอร์ต 8095, ไม่มี API แยก/ ไม่ใช้ 8011

---

## 0) เช็คพอร์ต 8095 ว่างก่อน
```bash
sudo ss -tlnp | grep ':8095' ; docker ps
```
ถ้ามี container/แอปอื่นถือ 8095 อยู่ (เช่น Next.js เดิม) → หยุดก่อน: `docker stop <ชื่อ>` (หรือเปลี่ยนพอร์ตแอปเรา + แก้ nginx ให้ตรง)

## 1) อัปโหลดโค้ดขึ้น VPS
จากเครื่อง local (ที่มีไฟล์ `geo-platform-deploy.tar.gz` ที่ผมสร้างให้):
```bash
scp geo-platform-deploy.tar.gz <user>@119.59.102.32:/tmp/
```
บน VPS:
```bash
sudo mkdir -p /opt/geo-platform
sudo tar -xzf /tmp/geo-platform-deploy.tar.gz -C /opt/geo-platform --strip-components=1
cd /opt/geo-platform
```

## 2) Python venv + dependencies
```bash
sudo apt update && sudo apt install -y python3-venv python3-pip
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```
(รองรับ Python 3.11–3.13 — อย่าใช้ 3.14)

## 3) Postgres (docker) — bind localhost เท่านั้น
```bash
docker run -d --name geo-postgres --restart unless-stopped \
  -e POSTGRES_USER=geo -e POSTGRES_PASSWORD=<PG_PASSWORD> -e POSTGRES_DB=geo_platform \
  -p 127.0.0.1:5434:5432 -v geo-pg-data:/var/lib/postgresql/data postgres:16
```

## 4) สร้าง .env
```bash
cp deploy/.env.production.example .env
nano .env   # ใส่ DATABASE_URL (รหัส PG ข้างบน) + SESSION_SECRET
```
สร้างค่าสุ่ม: `openssl rand -base64 48`  (ใช้เป็น SESSION_SECRET)

## 5) ย้ายข้อมูลเดิม (SQLite → Postgres)
`geo_platform.db` (ข้อมูลจริง) อยู่ใน tarball แล้ว → ย้ายเข้า PG:
```bash
.venv/bin/python migrate_sqlite_to_pg.py     # อ่าน DATABASE_URL จาก .env
```

## 6) เปลี่ยนรหัส admin (สำคัญ — ของเดิมรู้กันแล้ว)
```bash
.venv/bin/python create_admin.py admin@geo.local "<รหัสใหม่ที่แข็งแรง>"
```

## 7) systemd service (รันถาวร + auto-restart)
```bash
sudo useradd -r -s /usr/sbin/nologin geo 2>/dev/null; sudo chown -R geo:geo /opt/geo-platform
sudo cp deploy/geo-platform.service /etc/systemd/system/
# ปรับ User=/Group= ใน service ให้ตรงกับผู้ใช้ที่ใช้ docker/ไฟล์ได้
sudo systemctl daemon-reload
sudo systemctl enable --now geo-platform
sudo systemctl status geo-platform        # ต้องเป็น active (running)
curl -I http://127.0.0.1:8095/login        # ต้องได้ HTTP 200
```

## 8) Nginx — ชี้ geo.appreview.cloud → 8095
แก้ server block ของ geo.appreview.cloud (ตัว 443 ที่ certbot จัดการ): **ลบ** location `/api/`, `/`, `/_next/static/` เดิม แล้วใส่ตาม `deploy/nginx-geo-location.conf` (เหลือแค่ `location /` → 8095). เก็บบรรทัด `listen 443 ssl` + `ssl_certificate*` ของ certbot ไว้
```bash
sudo nginx -t && sudo systemctl reload nginx
```

## 9) DNS
A record: `geo` → `119.59.102.32` (TTL ต่ำ ๆ ตอนแรกได้) — ตั้งในหน้า registrar

## 10) ทดสอบ
เปิด `https://geo.appreview.cloud/login` → ล็อกอิน admin → เห็น JKP + progress ครบ

---

## Security checklist (ก่อนประกาศใช้จริง)
- [ ] เปลี่ยนรหัส admin แล้ว (ข้อ 6)
- [ ] `SESSION_SECRET` เป็นค่าสุ่มยาว (ไม่ใช่ค่า dev)
- [ ] Postgres bind `127.0.0.1` เท่านั้น (ข้อ 3) — ไม่เปิดออกเน็ต
- [ ] uvicorn bind `127.0.0.1:8095` (ไม่ใช่ 0.0.0.0) — เข้าได้ผ่าน nginx เท่านั้น
- [ ] systemd ไม่มี `--reload`
- [ ] ufw: `sudo ufw allow 22,80,443/tcp && sudo ufw enable`

## ดูแล / debug
```bash
sudo journalctl -u geo-platform -f        # log แอป
sudo systemctl restart geo-platform        # รีสตาร์ทหลังแก้ .env/โค้ด
docker logs geo-postgres                    # log DB
```

## เปลี่ยน LLM / search backend
- LLM เขียนคอนเทนต์: ใส่ `LITELLM_BASE_URL`/`LITELLM_API_KEY` ใน .env (cloud OpenAI-compatible) แล้ว restart; เว้นว่าง = template
- Search: `GEO_SEARCH_BACKEND=brave` + `BRAVE_API_KEY=...` (หรือ serper) แล้ว restart

## อัปเดตโค้ดครั้งถัดไป
```bash
# scp tarball ใหม่ → extract ทับ → 
.venv/bin/pip install -r requirements.txt
sudo systemctl restart geo-platform
```
