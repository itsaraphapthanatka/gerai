"""สร้าง/อัปเกรดบัญชี admin (operator).

ใช้:  python create_admin.py <email> <password> [ชื่อ]
ถ้ามีอีเมลนี้อยู่แล้ว จะอัปเกรดเป็น admin (ไม่เปลี่ยนรหัสผ่าน)
"""
import sys
from pathlib import Path
from dotenv import load_dotenv

# โหลด .env ก่อน import db เพื่อให้ใช้ฐานข้อมูลเดียวกับเซิร์ฟเวอร์
load_dotenv(Path(__file__).resolve().parent / ".env")

import bcrypt
from app import db


def main() -> None:
    if len(sys.argv) < 3:
        print("usage: python create_admin.py <email> <password> [name]")
        sys.exit(1)
    email, password = sys.argv[1], sys.argv[2]
    name = sys.argv[3] if len(sys.argv) > 3 else "Admin"
    db.init_db()
    pw = bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")
    existing = db.get_tenant_by_email(email)
    if existing:
        db.set_admin(existing["id"], True)
        db.set_password(existing["id"], pw)  # ใช้รีเซ็ตรหัสผ่านได้ด้วย
        print(f"อัปเดต {email} เป็น admin + ตั้งรหัสผ่านใหม่แล้ว (id={existing['id']})")
    else:
        tid = db.create_tenant(email, pw, name, is_admin=True)
        print(f"สร้าง admin {email} แล้ว (id={tid})")


if __name__ == "__main__":
    main()
