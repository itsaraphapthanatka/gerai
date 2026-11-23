#!/usr/bin/env bash
# ซ้อมสำรอง → ทำข้อมูลพัง → กู้ กับ Postgres จริง แล้วเช็กว่าข้อมูลกลับมาครบ
# ต้องมี container geo-postgres รันอยู่ (docker compose up -d geo-postgres) — ใช้ฐานแยกชื่อ geo_backup_test
# และโฟลเดอร์ชั่วคราว ไม่แตะฐาน geo_platform, uploads/ หรือ backups/ ของจริง
#
#   deploy/backup_restore_test.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
export PG_CONTAINER="${PG_CONTAINER:-geo-postgres}" PG_USER="${PG_USER:-geo}" PG_DB="geo_backup_test"

T="$(mktemp -d)"
trap 'rm -rf "$T"; docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d postgres -q -c "DROP DATABASE IF EXISTS geo_backup_test" > /dev/null 2>&1 || true' EXIT
mkdir -p "$T/deploy" "$T/uploads/slips" "$T/app/static/gen"
cp "$HERE/backup.sh" "$HERE/restore.sh" "$T/deploy/"       # สคริปต์หา ROOT จากที่ตั้งตัวเอง → ทำงานในโฟลเดอร์ชั่วคราว
export BACKUP_DIR="$T/backups" STOP_CMD="echo stop >> '$T/calls'" START_CMD="echo start >> '$T/calls'"

sql() { docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "${2:-$PG_DB}" -v ON_ERROR_STOP=1 -qAt -c "$1"; }
fail() { echo "FAIL: $*" >&2; exit 1; }
latest() { find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name '20*' | sort | tail -1; }

sql "DROP DATABASE IF EXISTS geo_backup_test" postgres
sql "CREATE DATABASE geo_backup_test" postgres
sql "CREATE TABLE tenants (id serial PRIMARY KEY, email text UNIQUE, name text)"
sql "INSERT INTO tenants(email, name) VALUES ('a@example.com', 'ร้านกาแฟ'), ('b@example.com', 'B')"
printf 'slip-bytes' > "$T/uploads/slips/q1-abc.jpg"
printf 'img-bytes' > "$T/app/static/gen/c1.png"
printf 'SESSION_SECRET=test-only\n' > "$T/.env"

# ---- สำรอง ----
bash "$T/deploy/backup.sh" > "$T/backup.log" 2>&1 || { cat "$T/backup.log"; fail "backup.sh ออกด้วยรหัสผิดพลาด"; }
B="$(latest)"
for f in db.dump files.tar.gz env MANIFEST; do [ -s "$B/$f" ] || fail "ชุดสำรองไม่มี $f"; done
grep -q '^commit=' "$B/MANIFEST" || fail "MANIFEST ไม่มี commit"
[ "$(grep -cE '  (db.dump|files.tar.gz|env)$' "$B/MANIFEST")" = "3" ] || fail "MANIFEST ไม่มี sha256 ครบ 3 ไฟล์"
[ -z "$(find "$BACKUP_DIR" -name '.partial-*')" ] || fail "เหลือโฟลเดอร์ .partial"
perm="$(stat -c %a "$B" 2> /dev/null || stat -f %Lp "$B")"
[ "$perm" = "700" ] || fail "โฟลเดอร์ชุดสำรองควรเป็น 700 แต่เป็น $perm"
echo "backup OK"

# ---- ทำข้อมูลพัง ----
sql "DELETE FROM tenants WHERE email = 'a@example.com'"
sql "UPDATE tenants SET name = 'เปลี่ยนแล้ว' WHERE email = 'b@example.com'"
sql "CREATE TABLE junk (x int)"
rm "$T/uploads/slips/q1-abc.jpg"
printf 'newer' > "$T/uploads/slips/q2-new.jpg"

# ---- ไม่ยืนยัน = ไม่ทำอะไร ----
if echo "no" | bash "$T/deploy/restore.sh" "$B" > /dev/null 2>&1; then fail "ตอบผิดควรยกเลิก"; fi
[ "$(sql "SELECT count(*) FROM tenants")" = "1" ] || fail "ยกเลิกแล้วฐานไม่ควรเปลี่ยน"
[ ! -f "$T/calls" ] || fail "ยกเลิกแล้วไม่ควรหยุดแอป"
echo "ยกเลิกเมื่อไม่ยืนยัน OK"

# ---- ชุดที่ถูกแก้ = ไม่กู้ ----
cp -R "$B" "$T/tampered"; printf 'x' >> "$T/tampered/db.dump"
if bash "$T/deploy/restore.sh" "$T/tampered" --yes > /dev/null 2>&1; then fail "ชุดที่ sha256 ไม่ตรงควรถูกปฏิเสธ"; fi
[ "$(sql "SELECT count(*) FROM tenants")" = "1" ] || fail "ชุดเสียแล้วฐานไม่ควรเปลี่ยน"
[ ! -f "$T/calls" ] || fail "ชุดเสียแล้วไม่ควรหยุดแอป"
echo "ปฏิเสธชุดที่ถูกแก้ OK"

# ---- กู้ ----
bash "$T/deploy/restore.sh" "$B" --yes > "$T/restore.log" 2>&1 || { cat "$T/restore.log"; fail "restore.sh ออกด้วยรหัสผิดพลาด"; }
[ "$(sql "SELECT email || '|' || name FROM tenants ORDER BY id" | paste -sd, -)" = "a@example.com|ร้านกาแฟ,b@example.com|B" ] || fail "ข้อมูล tenants ไม่กลับมาเหมือนเดิม"
[ "$(sql "SELECT count(*) FROM information_schema.tables WHERE table_name = 'junk'")" = "0" ] || fail "ตารางที่สร้างหลังสำรองควรหายไป (ฐานต้องตรงกับชุดสำรอง)"
sql "INSERT INTO tenants(email) VALUES ('c@example.com')"
[ "$(sql "SELECT id FROM tenants WHERE email = 'c@example.com'")" = "3" ] || fail "sequence ของ id ไม่ถูกกู้"
sql "DELETE FROM tenants WHERE email = 'c@example.com'"
[ "$(cat "$T/uploads/slips/q1-abc.jpg")" = "slip-bytes" ] || fail "สลิปที่ถูกลบไม่กลับมา"
[ "$(cat "$T/uploads/slips/q2-new.jpg")" = "newer" ] || fail "ไฟล์ที่ใหม่กว่าชุดสำรองไม่ควรหาย"
[ "$(paste -sd, - < "$T/calls")" = "stop,start" ] || fail "ควรหยุดแล้วเริ่มแอปอย่างละครั้ง: $(cat "$T/calls")"
P="$(find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name 'pre-restore-*' | head -1)"
[ -s "$P/db.dump" ] || fail "ไม่มีชุด pre-restore"
grep -q 'SESSION_SECRET=test-only' "$T/.env" || fail ".env ไม่ควรถูกแตะ"
echo "restore OK"

# ---- กู้พังกลางทาง = เริ่มแอปคืน และฐานเดิมยังอยู่ ----
rm "$T/calls"
if PG_DB=geo_no_such_db bash "$T/deploy/restore.sh" "$B" --yes --db-only > /dev/null 2>&1; then fail "ฐานปลายทางไม่มีควรล้มเหลว"; fi
[ "$(paste -sd, - < "$T/calls")" = "stop,start" ] || fail "ล้มเหลวกลางทางต้องเริ่มแอปคืน: $(cat "$T/calls")"
[ "$(sql "SELECT count(*) FROM tenants")" = "2" ] || fail "ล้มเหลวแล้วฐานไม่ควรเปลี่ยน"
echo "ล้มเหลวกลางทางแล้วเริ่มแอปคืน OK"

# ---- ลบชุดเก่า: เฉพาะโฟลเดอร์ชื่อรูปแบบวันเวลา ----
mkdir -p "$BACKUP_DIR/20200101-000000" "$BACKUP_DIR/keep-me"
touch -t 202001010000 "$BACKUP_DIR/20200101-000000" "$BACKUP_DIR/keep-me"
sleep 1
KEEP_DAYS=7 bash "$T/deploy/backup.sh" > /dev/null 2>&1 || fail "backup รอบสองล้มเหลว"
[ ! -d "$BACKUP_DIR/20200101-000000" ] || fail "ชุดเก่าเกินกำหนดควรถูกลบ"
[ -d "$BACKUP_DIR/keep-me" ] && [ -d "$B" ] && [ -d "$P" ] || fail "ลบเกิน: โฟลเดอร์อื่นหรือชุดใหม่หายไป"
echo "ลบชุดเก่า OK"

echo "ALL BACKUP/RESTORE TESTS OK"
