#!/usr/bin/env bash
# กู้ข้อมูลจากชุดที่ deploy/backup.sh สร้าง — เขียนทับฐานข้อมูลปัจจุบันทั้งฐาน
#
#   deploy/restore.sh backups/20261123-030000            # ถามยืนยันก่อน
#   deploy/restore.sh backups/20261123-030000 --yes      # ไม่ถาม (ใช้ในสคริปต์/ซ้อมกู้)
#   deploy/restore.sh backups/20261123-030000 --db-only  # ไม่แตะ uploads/ และรูปที่สร้าง
#
# ลำดับ: ตรวจ sha256 ของชุด → หยุดแอป → สำรองฐานปัจจุบันไว้ก่อน (pre-restore) → กู้ฐานในทรานแซกชันเดียว
#        → กู้ไฟล์ → เริ่มแอป
# กู้ฐานพังกลางทาง = ทรานแซกชัน rollback ฐานเดิมยังอยู่ และยังมีชุด pre-restore ให้ย้อนอีกชั้น
#
# .env ไม่ถูกเขียนทับเอง — ถ้าต้องใช้ ให้คัดลอกจาก <ชุด>/env เอง (SESSION_SECRET ต้องตรงกับตอนสำรอง
# ไม่งั้น credential ของ WordPress ในฐานถอดรหัสไม่ได้)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
PG_CONTAINER="${PG_CONTAINER:-geo-postgres}"
PG_USER="${PG_USER:-geo}"
PG_DB="${PG_DB:-geo_platform}"
# รันแอปด้วย systemd แทน compose: STOP_CMD="sudo systemctl stop geo-platform" START_CMD="sudo systemctl start geo-platform"
STOP_CMD="${STOP_CMD:-docker compose -f $ROOT/docker-compose.yml stop geo-app}"
START_CMD="${START_CMD:-docker compose -f $ROOT/docker-compose.yml start geo-app}"

src=""; yes=0; db_only=0
for a in "$@"; do
  case "$a" in
    --yes) yes=1 ;;
    --db-only) db_only=1 ;;
    -*) echo "ไม่รู้จักตัวเลือก: $a" >&2; exit 2 ;;
    *) src="$a" ;;
  esac
done
[ -n "$src" ] || { echo "ใช้: deploy/restore.sh <โฟลเดอร์ชุดสำรอง> [--yes] [--db-only]" >&2; exit 2; }
src="$(cd "$src" 2> /dev/null && pwd)" || { echo "ไม่พบโฟลเดอร์ชุดสำรอง" >&2; exit 2; }
[ -s "$src/db.dump" ] || { echo "ไม่พบ $src/db.dump" >&2; exit 2; }

sha() { if command -v sha256sum > /dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }

echo "[restore] ตรวจความครบถ้วนของชุด $src"
[ -f "$src/MANIFEST" ] || { echo "ไม่พบ MANIFEST — ชุดนี้ไม่ได้มาจาก backup.sh หรือสำรองไม่จบ" >&2; exit 1; }
for f in db.dump files.tar.gz env; do
  [ -f "$src/$f" ] || continue
  want="$(awk -v f="$f" '$2 == f {print $1}' "$src/MANIFEST")"
  [ -n "$want" ] && [ "$want" = "$(sha "$src/$f")" ] || { echo "sha256 ของ $f ไม่ตรงกับ MANIFEST — ไฟล์เสียหรือถูกแก้" >&2; exit 1; }
done
docker exec -i "$PG_CONTAINER" pg_restore --list < "$src/db.dump" > /dev/null

grep -E '^(created|commit)=' "$src/MANIFEST" | sed 's/^/[restore]   /'
if [ "$yes" != "1" ]; then
  printf '[restore] จะเขียนทับฐาน %s ทั้งฐานด้วยชุดนี้ พิมพ์ชื่อฐานเพื่อยืนยัน: ' "$PG_DB"
  read -r answer
  [ "$answer" = "$PG_DB" ] || { echo "ยกเลิก"; exit 1; }
fi

echo "[restore] หยุดแอป"
sh -c "$STOP_CMD"
started=0
start_app() { [ "$started" = "1" ] || { started=1; echo "[restore] เริ่มแอป"; sh -c "$START_CMD"; }; }
trap start_app EXIT            # พังขั้นไหนก็เริ่มแอปคืน — ฐานยังเป็นของเดิมเพราะกู้ในทรานแซกชันเดียว

umask 077
pre="$BACKUP_DIR/pre-restore-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$pre"
echo "[restore] สำรองฐานปัจจุบันไว้ก่อน → $pre/db.dump"
docker exec "$PG_CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" --format=custom --no-owner --no-privileges > "$pre/db.dump"

echo "[restore] กู้ฐานข้อมูล"
# แปลง dump เป็น SQL ให้จบก่อน แล้วค่อยรันทั้งก้อนในทรานแซกชันเดียว: ล้าง schema public แล้วสร้างใหม่จากชุดสำรอง
# (pg_restore --clean ลบเฉพาะของที่อยู่ใน dump — ตารางที่เพิ่งสร้างหลังสำรองจะค้างอยู่ ฐานไม่ตรงกับชุดสำรองจริง)
docker exec -i "$PG_CONTAINER" pg_restore --no-owner --no-privileges -f - < "$src/db.dump" > "$pre/restore.sql"
{ echo 'DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;'; cat "$pre/restore.sql"; } \
  | docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" --single-transaction -v ON_ERROR_STOP=1 -q > /dev/null
rm -f "$pre/restore.sql"

if [ "$db_only" != "1" ] && [ -f "$src/files.tar.gz" ]; then
  echo "[restore] กู้ไฟล์ (uploads/, app/static/gen/) — ไฟล์ชื่อซ้ำถูกเขียนทับ ไฟล์ที่ใหม่กว่าชุดสำรองยังอยู่"
  tar -xzf "$src/files.tar.gz" -C "$ROOT"
fi

start_app
trap - EXIT
echo "[restore] เสร็จ · ฐานก่อนกู้เก็บไว้ที่ $pre (ลบเองเมื่อแน่ใจแล้ว)"
[ -f "$src/env" ] && echo "[restore] .env ไม่ได้ถูกแตะ — ถ้าต้องใช้ของชุดนี้: cp $src/env $ROOT/.env แล้วเริ่มแอปใหม่"
exit 0
