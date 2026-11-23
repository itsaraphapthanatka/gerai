#!/usr/bin/env bash
# สำรองข้อมูล production ทั้งชุดลงโฟลเดอร์เดียว: ฐานข้อมูล + ไฟล์ที่แอปเขียนตอนรัน + .env
#
#   deploy/backup.sh                 # สำรองลง ./backups/<วันเวลา>/
#   KEEP_DAYS=30 deploy/backup.sh    # เก็บย้อนหลัง 30 วัน (ดีฟอลต์ 14)
#   BACKUP_REMOTE=user@host:/path deploy/backup.sh   # สำรองเสร็จแล้ว rsync ออกนอกเครื่องด้วย
#
# ได้อะไรในหนึ่งชุด:
#   db.dump        pg_dump รูปแบบ custom ของฐาน geo_platform (กู้ด้วย deploy/restore.sh)
#   files.tar.gz   uploads/ (สลิปลูกค้า) + app/static/gen/ (รูปบทความที่สร้าง)
#   env            สำเนา .env — ต้องมี: SESSION_SECRET ใช้ถอดรหัส credential ของ WordPress ที่เก็บในฐาน
#                  หายแล้วข้อมูลในฐานส่วนนั้นถอดไม่ได้อีก (ปิดด้วย BACKUP_ENV=0)
#   MANIFEST       เวลา, commit ของโค้ดตอนสำรอง, sha256 ของทุกไฟล์
#
# ชุดสำรองมีข้อมูลลูกค้า, password hash และ secret — โฟลเดอร์ถูกสร้างแบบเจ้าของอ่านได้คนเดียว (umask 077)
# ออกด้วยรหัสไม่ใช่ 0 เมื่อขั้นไหนพัง ให้ cron/ตัวเฝ้าแจ้งเตือนได้
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
PG_CONTAINER="${PG_CONTAINER:-geo-postgres}"
PG_USER="${PG_USER:-geo}"
PG_DB="${PG_DB:-geo_platform}"
BACKUP_ENV="${BACKUP_ENV:-1}"
BACKUP_REMOTE="${BACKUP_REMOTE:-}"
# container ของแอป — ถ้ารันอยู่ จะอ่านไฟล์ผ่านมัน (ดูเหตุผลตรงส่วนไฟล์) · ตั้งเป็นค่าว่างเพื่ออ่านจากเครื่องตรง ๆ
APP_CONTAINER="${APP_CONTAINER-geo-app}"

umask 077
stamp="$(date +%Y%m%d-%H%M%S)"
out="$BACKUP_DIR/$stamp"
tmp="$BACKUP_DIR/.partial-$stamp"
mkdir -p "$tmp"
trap 'rm -rf "$tmp"' EXIT          # พังกลางทาง = ไม่ทิ้งชุดครึ่ง ๆ กลาง ๆ ให้เข้าใจผิดว่าใช้ได้

echo "[backup] ฐานข้อมูล $PG_DB จาก container $PG_CONTAINER"
docker exec "$PG_CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" --format=custom --no-owner --no-privileges > "$tmp/db.dump"
# dump ที่เปิดอ่านสารบัญไม่ได้ = ใช้กู้ไม่ได้ — เช็กตอนนี้ดีกว่ารู้ตอนต้องใช้
docker exec -i "$PG_CONTAINER" pg_restore --list < "$tmp/db.dump" > /dev/null
[ -s "$tmp/db.dump" ] || { echo "[backup] db.dump ว่างเปล่า" >&2; exit 1; }

paths=""
for p in uploads app/static/gen; do
  [ -d "$ROOT/$p" ] && paths="$paths $p"
done
if [ -n "$paths" ]; then
  echo "[backup] ไฟล์:$paths"
  # ไฟล์ที่แอปเขียนเป็นของผู้ใช้ใน container และบางไฟล์สิทธิ์ 600 (เช่น uploads/archive/) — ผู้ใช้บนเครื่องที่รัน
  # สคริปต์นี้อ่านไม่ได้ tar จากเครื่องจึงล้ม ให้ container ของแอปเป็นคนอ่านแทน (โฟลเดอร์เดียวกัน mount อยู่ที่ /app)
  # container ไม่ได้รัน (ติดตั้งแบบ systemd หรือแอปล่มอยู่) → อ่านจากเครื่องตรง ๆ ถ้าอ่านไม่ได้ให้รันด้วย sudo
  # shellcheck disable=SC2086  # ตั้งใจแยกคำ: รายชื่อโฟลเดอร์คงที่ด้านบน ไม่มีช่องว่างในชื่อ
  if [ -n "$APP_CONTAINER" ] && docker exec "$APP_CONTAINER" true 2> /dev/null; then
    docker exec "$APP_CONTAINER" tar -czf - -C /app $paths > "$tmp/files.tar.gz"
  else
    tar -czf "$tmp/files.tar.gz" -C "$ROOT" $paths
  fi
  tar -tzf "$tmp/files.tar.gz" > /dev/null          # เปิดอ่านไม่ได้ = ใช้กู้ไม่ได้
else
  echo "[backup] ยังไม่มี uploads/ หรือ app/static/gen/ — ข้ามส่วนไฟล์"
fi

if [ "$BACKUP_ENV" = "1" ] && [ -f "$ROOT/.env" ]; then
  cp "$ROOT/.env" "$tmp/env"
fi

{
  echo "created=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "commit=$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "pg_container=$PG_CONTAINER db=$PG_DB"
  (cd "$tmp" && for f in db.dump files.tar.gz env; do
     [ -f "$f" ] && { if command -v sha256sum > /dev/null; then sha256sum "$f"; else shasum -a 256 "$f"; fi; }
   done; true)
} > "$tmp/MANIFEST"

mv "$tmp" "$out"
trap - EXIT
echo "[backup] เสร็จ: $out ($(du -sh "$out" | cut -f1))"

if [ -n "$BACKUP_REMOTE" ]; then
  echo "[backup] ส่งออกนอกเครื่อง → $BACKUP_REMOTE"
  rsync -a "$out" "$BACKUP_REMOTE/"
fi

# ลบชุดเก่าเกิน KEEP_DAYS วัน — เฉพาะโฟลเดอร์ชื่อรูปแบบวันเวลาที่สคริปต์นี้สร้าง
if [ "$KEEP_DAYS" -gt 0 ] 2> /dev/null; then
  find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name '20[0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9]' \
    -mtime +"$KEEP_DAYS" -print -exec rm -rf {} + | sed 's/^/[backup] ลบชุดเก่า: /'
fi
