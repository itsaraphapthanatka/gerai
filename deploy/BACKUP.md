# สำรองข้อมูล กู้คืน และย้อนเวอร์ชัน

ใช้กับ production ที่รันด้วย `docker-compose.yml` (container `geo-postgres` + `geo-app`)
คำสั่งทั้งหมดรันจากโฟลเดอร์โปรเจกต์บนเซิร์ฟเวอร์

## ต้องกรอกเองก่อนใช้จริง

เอกสารนี้เขียนจากสิ่งที่อยู่ใน repo — สามเรื่องนี้ repo ไม่รู้ ต้องเติมให้ตรงกับเซิร์ฟเวอร์จริง:

| เรื่อง | ค่า |
|---|---|
| ปลายทางสำรองนอกเครื่อง (`BACKUP_REMOTE`) | _ยังไม่ได้กำหนด_ |
| วิธีหยุด deploy อัตโนมัติชั่วคราว | `crontab -e` ของ `aistudiofebc` แล้วใส่ `#` หน้าบรรทัด `/website/edge-configs/autodeploy/autodeploy.sh` (รันทุก 5 นาที) เอา `#` ออกเมื่อแก้เสร็จ |
| ผู้รับผิดชอบเมื่อระบบล่ม + ช่องทางติดต่อ | _ยังไม่ได้กำหนด_ |

## ข้อมูลที่ต้องรักษา

| อะไร | อยู่ที่ไหน | หายแล้วเป็นอย่างไร |
|---|---|---|
| ฐานข้อมูล `geo_platform` | Docker volume `geo-pg-data` | เสียลูกค้า แบรนด์ ผลตรวจ ใบเสนอราคา เอกสารบัญชีทั้งหมด |
| สลิปโอนเงิน | `uploads/` | เสียหลักฐานการชำระของลูกค้า |
| รูปบทความที่สร้าง | `app/static/gen/` | บทความที่เผยแพร่แล้วรูปแตก |
| `.env` | โฟลเดอร์โปรเจกต์ | `SESSION_SECRET` หาย = credential ของ WordPress และ token ที่เข้ารหัสไว้ในฐานถอดไม่ได้อีก |

`deploy/backup.sh` เก็บทั้งสี่อย่างลงชุดเดียว

## สำรอง

```bash
deploy/backup.sh
```

ได้โฟลเดอร์ `backups/<ปีเดือนวัน-เวลา>/` มี `db.dump`, `files.tar.gz`, `env` และ `MANIFEST` (commit ของโค้ด + sha256)
ชุดเก่าเกิน 14 วันถูกลบเอง (เปลี่ยนด้วย `KEEP_DAYS=30`)

`uploads/` และ `app/static/gen/` เป็นของผู้ใช้ใน container (uid 10001) ผู้ใช้บนเครื่องอ่านบางไฟล์และเขียนลงไม่ได้
สคริปต์ทั้งสองจึงทำงานกับไฟล์ผ่าน container: `backup.sh` อ่านผ่าน `geo-app` ที่รันอยู่ ส่วน `restore.sh` แตกไฟล์ผ่าน
container ชั่วคราวจาก image เดียวกัน (ใช้ได้แม้ `geo-app` หยุดอยู่ ขอแค่ยังไม่ถูกลบ)
ถ้าไม่มี container `geo-app` เลยและสคริปต์ฟ้อง `Permission denied` ให้รันด้วย `sudo`

ชุดสำรองมีข้อมูลลูกค้า password hash และ secret — โฟลเดอร์เป็นสิทธิ์ 700 อยู่แล้ว อย่าคัดลอกไปที่ที่คนอื่นอ่านได้

### ตั้งให้รันทุกคืน

ตั้งไว้แล้วบน production (24 พ.ย. 2569) ใน crontab ของ `aistudiofebc` รันทุกคืน 03:15:

```cron
15 3 * * * cd /website/geo.appreview.cloud/geo-platform && deploy/backup.sh >> logs/backup.log 2>&1
```

เมื่อตั้งปลายทางนอกเครื่องเสร็จ (หัวข้อถัดไป) ให้เติมตัวแปรปลายทางหน้า `deploy/backup.sh` ในบรรทัดนี้

สคริปต์ออกด้วยรหัสไม่ใช่ 0 เมื่อพัง และเขียนข้อผิดพลาดลง `logs/backup.log` ยังไม่มีตัวแจ้งเตือนเมื่อรอบสำรองล้มเหลวหรือไม่ได้รัน
จนกว่าจะมี ให้เช็กเองว่า `ls backups/` มีชุดของเมื่อคืน

### ต้องมีสำเนานอกเครื่อง

ชุดใน `backups/` อยู่บนดิสก์เดียวกับฐานข้อมูล ดิสก์พังหรือเซิร์ฟเวอร์หาย = หายพร้อมกัน
สคริปต์ส่งชุดใหม่ออกไปทุกครั้งเมื่อตั้งปลายทางอย่างใดอย่างหนึ่ง:

| ตัวแปร | ปลายทาง | เครื่องมือ |
|---|---|---|
| `BACKUP_RCLONE_REMOTE=r2:<bucket>/geo-platform` | ที่เก็บไฟล์บนคลาวด์ (Cloudflare R2, S3, B2 ...) | `rclone` |
| `BACKUP_REMOTE=user@backup-host:/srv/geo-backups` | เซิร์ฟเวอร์อีกเครื่องผ่าน SSH | `rsync` |

แบบ rclone: ส่งเสร็จแล้วเทียบขนาดและ hash ของทุกไฟล์กับปลายทาง ไม่ตรง = สคริปต์ออกด้วยรหัสผิดพลาด
และลบชุดที่ปลายทางเมื่อเก่าเกิน `REMOTE_KEEP_DAYS` วัน (ดีฟอลต์ 30, `0` = ไม่ลบ) เฉพาะโฟลเดอร์ชื่อรูปแบบวันเวลา
แบบ rsync ไม่ลบชุดเก่าที่ปลายทาง ต้องตั้งการลบที่เครื่องนั้นเอง

ตั้ง Cloudflare R2 ครั้งแรก:

1. หน้า Cloudflare → R2 → สร้าง bucket (ไม่เปิด public access)
2. R2 → Manage API Tokens → สร้าง token สิทธิ์ Object Read & Write จำกัดเฉพาะ bucket นั้น
3. บนเซิร์ฟเวอร์ ใส่ค่าใน `~/.config/rclone/rclone.conf` (สิทธิ์ 600) ของผู้ใช้ที่รัน cron:

   ```ini
   [r2]
   type = s3
   provider = Cloudflare
   access_key_id = <จาก token>
   secret_access_key = <จาก token>
   endpoint = https://<account id>.r2.cloudflarestorage.com
   acl = private
   no_check_bucket = true
   ```

4. ลองส่ง: `BACKUP_RCLONE_REMOTE=r2:<bucket>/geo-platform deploy/backup.sh` แล้ว `rclone lsf r2:<bucket>/geo-platform/`

`rclone` ต้องอยู่ใน `PATH` ของ cron (ดีฟอลต์ `/usr/bin:/bin`) — ถ้าติดตั้งไว้ที่อื่น ให้ตั้ง `PATH` ในบรรทัด cron ด้วย

ชุดสำรองถูกส่งไปแบบไม่เข้ารหัสเพิ่ม ใครถือ token ของ bucket หรือเข้าบัญชี Cloudflare ได้ก็อ่านข้อมูลลูกค้าและ `.env` ได้
เก็บ token ไว้เฉพาะบนเซิร์ฟเวอร์ และอย่าเปิด bucket เป็น public

กู้จากสำเนานอกเครื่อง (เซิร์ฟเวอร์ใหม่ หรือชุดในเครื่องหาย):

```bash
rclone lsf r2:<bucket>/geo-platform/                                          # เลือกชุด
rclone copy r2:<bucket>/geo-platform/20261123-031500 backups/20261123-031500
deploy/restore.sh backups/20261123-031500
```

## กู้คืน

```bash
ls backups/                                   # เลือกชุด
cat backups/20261123-031500/MANIFEST          # ดูเวลาและ commit ของชุดนั้น
deploy/restore.sh backups/20261123-031500     # ถามให้พิมพ์ชื่อฐานเพื่อยืนยัน
```

สิ่งที่สคริปต์ทำ ตามลำดับ:

1. ตรวจ sha256 ของชุดกับ `MANIFEST` — ไม่ตรงหยุดทันที ยังไม่แตะอะไร
2. หยุด `geo-app`
3. สำรองฐานปัจจุบันไว้ที่ `backups/pre-restore-<เวลา>/db.dump`
4. ล้าง schema แล้วกู้ฐานจากชุด ในทรานแซกชันเดียว — พังกลางทางฐานเดิมยังอยู่
5. แตก `uploads/` และ `app/static/gen/` ทับ (ไฟล์ที่ใหม่กว่าชุดสำรองไม่ถูกลบ ไฟล์ที่ได้เป็นของผู้ใช้เดียวกับที่แอปรัน)
6. เริ่ม `geo-app` (พังขั้นไหนก็เริ่มคืนให้)

ข้อมูลที่เข้ามาหลังเวลาของชุดสำรองจะหายจากฐาน — ถ้ากู้ผิดชุด กู้กลับด้วย `deploy/restore.sh backups/pre-restore-<เวลา>` ได้

`.env` ไม่ถูกเขียนทับเอง ถ้าเซิร์ฟเวอร์ใหม่ไม่มี: `cp backups/<ชุด>/env .env` ก่อนเริ่มแอป

ตัวเลือก: `--yes` ไม่ถามยืนยัน · `--db-only` ไม่แตะไฟล์ · รันแอปด้วย systemd ให้ตั้ง
`STOP_CMD="sudo systemctl stop geo-platform" START_CMD="sudo systemctl start geo-platform"`

### กู้ขึ้นเซิร์ฟเวอร์ใหม่

```bash
git clone <repo> /opt/geo-platform && cd /opt/geo-platform
rsync -a user@backup-host:/srv/geo-backups/<ชุด> backups/
cp backups/<ชุด>/env .env
docker compose up -d --wait geo-postgres
STOP_CMD=true START_CMD=true deploy/restore.sh backups/<ชุด>
docker compose up -d --build
```

## ซ้อมกู้

ชุดสำรองที่ไม่เคยลองกู้ = ยังไม่รู้ว่าใช้ได้ ซ้อมอย่างน้อยเดือนละครั้งบนเครื่องที่ไม่ใช่ production
ตามขั้น "กู้ขึ้นเซิร์ฟเวอร์ใหม่" แล้วล็อกอินดูว่าแบรนด์และสลิปล่าสุดอยู่ครบ

CI ซ้อมกลไกทุก push (`deploy/backup_restore_test.sh`: สำรอง → ทำข้อมูลพัง → กู้ → เทียบ กับ Postgres 16 จริง)
และซ้อมสำรอง/กู้ไฟล์กับ image จริงที่โฟลเดอร์ข้อมูลเป็นของ uid 10001 (ขั้น "Postgres and container checks")
แต่ CI ไม่ได้พิสูจน์ว่าชุดของ production กู้ได้ — ต้องซ้อมกับชุดจริงเอง

## ย้อนเวอร์ชันโค้ด

Deploy เป็นแบบดึง `main` อัตโนมัติเมื่อ CI ผ่าน ดังนั้น:

**ทางปกติ — revert ใน git** (ช้ากว่าไม่กี่นาที แต่เซิร์ฟเวอร์กับ `main` ตรงกันเสมอ)

```bash
git revert <commit ที่มีปัญหา> && git push origin main      # CI ผ่านแล้วเซิร์ฟเวอร์ดึงเอง
```

**ฉุกเฉิน — ย้อนบนเซิร์ฟเวอร์ตรง ๆ**

```bash
# 1) หยุด deploy อัตโนมัติก่อน ไม่งั้นรอบถัดไปดึง main กลับมาทับ (ดูตาราง "ต้องกรอกเอง")
git log --oneline -10                       # หา commit ก่อนหน้าที่ยังดี
git checkout <commit ที่ดี>
docker compose up -d --build geo-app
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8095/login    # ต้องได้ 200
# 2) แก้ที่ main ให้เรียบร้อย (revert) แล้วค่อย git checkout main และเปิด deploy อัตโนมัติคืน
```

ย้อนโค้ดไม่ต้องกู้ฐาน: `db.init_db()` เพิ่มตารางและคอลัมน์อย่างเดียว ไม่ลบไม่เปลี่ยนชนิด โค้ดเก่าจึงรันบนฐานที่ใหม่กว่าได้
ถ้า release ไหนแก้ข้อมูลแบบย้อนไม่ได้ (ลบคอลัมน์ ย้ายข้อมูล) ให้รัน `deploy/backup.sh` ก่อน deploy และเขียนไว้ใน PR

## ก่อน deploy ที่เสี่ยง

```bash
deploy/backup.sh          # ได้จุดย้อนของข้อมูล
git rev-parse HEAD        # จด commit ปัจจุบัน = จุดย้อนของโค้ด
```
