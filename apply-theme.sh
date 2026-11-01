#!/usr/bin/env bash
# ตั้งธีมหน้าบทความ (/geo) ของแบรนด์หนึ่งจากไฟล์ JSON ใน app/themes/
#   ./apply-theme.sh <brand_id> <app/themes/ไฟล์.json>
# ธีมอ่านตอนเรนเดอร์ทุกครั้ง — แก้สีแล้วไม่ต้อง build ใหม่ รอแค่แคช 10 นาทีหมดอายุ
set -euo pipefail
cd "$(dirname "$0")"

BRAND="${1:?ใส่ brand id เช่น 1}"
FILE="${2:?ใส่ไฟล์ธีม เช่น app/themes/jkp-property.json}"
[ -f "$FILE" ] || { echo "ไม่พบไฟล์ $FILE"; exit 1; }

# ตรวจก่อนว่าทุกคีย์ผ่านตัวกรองของ hosted_theme.py — ค่าที่ไม่ผ่านจะถูกทิ้งเงียบ ๆ ตอนเรนเดอร์
docker run --rm -v "$PWD:/src:ro" -w /src -e PYTHONDONTWRITEBYTECODE=1 geo-platform:latest \
  python -c "
import json, sys
sys.path.insert(0, '/src')
from app.hosted_theme import DEFAULT, _ok
raw = json.load(open('/src/$FILE'))
bad = [k for k, v in raw.items() if k not in DEFAULT or not isinstance(v, str) or not _ok(k, v.strip())]
print('ธีม:', len(raw), 'คีย์ · ไม่ผ่าน:', bad or 'ไม่มี')
sys.exit(1 if bad else 0)
"

docker exec -i geo-postgres psql -U geo -d geo_platform -v ON_ERROR_STOP=1 \
  -c "UPDATE brands SET theme = \$th\$$(cat "$FILE")\$th\$ WHERE id = $BRAND" \
  -c "SELECT id, name, length(theme) AS theme_bytes FROM brands WHERE id = $BRAND"
