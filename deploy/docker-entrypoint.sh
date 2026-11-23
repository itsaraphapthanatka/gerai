#!/bin/sh
# จุดเริ่มของ container geo-app: เริ่มเป็น root → จัดสิทธิ์โฟลเดอร์ข้อมูล → ลดสิทธิ์ → รันคำสั่งจริง
#
# รันเป็นใคร:
#   - โฟลเดอร์ทำงานเป็นของ root (ปกติ: /app ใน image)        → ผู้ใช้ app (uid 10001)
#   - โฟลเดอร์ทำงานเป็นของผู้ใช้อื่น (mount โค้ดเข้ามา เช่น CI: -v "$PWD:/src" -w /src)
#                                                             → ผู้ใช้เจ้าของโฟลเดอร์นั้น เพื่อให้เขียนไฟล์ลงที่ mount ได้
#   - container ถูกสั่งให้เริ่มเป็นผู้ใช้อื่นอยู่แล้ว (--user)  → ไม่แตะอะไร รันต่อเลย
# ทุกกรณีโปรเซสของแอปไม่ได้เป็น root
set -eu

[ "$(id -u)" = "0" ] || exec "$@"

uid="$(stat -c %u .)"
gid="$(stat -c %g .)"
if [ "$uid" = "0" ]; then
  uid="$(id -u app)"
  gid="$(id -g app)"
  export HOME=/home/app
  # โฟลเดอร์ที่แอปเขียนตอนรัน (bind mount จากเครื่อง — ของเดิมเป็นของ root จากตอนที่ container ยังรันเป็น root)
  # โอนให้ app ครั้งแรกครั้งเดียว: รอบถัดไปโฟลเดอร์เป็นของ app แล้วก็ข้าม
  for d in /app/uploads /app/app/static/gen; do
    mkdir -p "$d"
    [ "$(stat -c %u "$d")" = "$uid" ] || chown -R "$uid:$gid" "$d"
  done
else
  export HOME=/tmp
fi

exec setpriv --reuid="$uid" --regid="$gid" --clear-groups "$@"
