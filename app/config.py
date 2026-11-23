"""ค่าตั้งที่แอป "ต้องมี" — ไม่มีแล้วหยุดทันที ดีกว่ารันต่อด้วยค่าที่ใครก็รู้"""
import os

# ค่าตัวอย่างในไฟล์ .env.example / deploy/.env.production.example และค่าเริ่มต้นเดิมในโค้ด — ทุกคนที่เห็น repo รู้ค่าเหล่านี้
_PLACEHOLDER_SECRETS = {"dev-secret-change-me", "CHANGE_ME_LONG_RANDOM", "เปลี่ยนเป็นข้อความสุ่มยาวๆ"}


def session_secret() -> str:
    """คีย์เซ็น session cookie และ derive คีย์เข้ารหัส credential ของ WordPress

    ไม่ได้ตั้ง หรือยังเป็นค่าตัวอย่าง = ใครก็ปลอม cookie แอดมินได้ → ไม่ยอมเริ่มแอป"""
    s = os.getenv("SESSION_SECRET", "").strip()
    if not s or s in _PLACEHOLDER_SECRETS:
        raise RuntimeError(
            "SESSION_SECRET ยังไม่ได้ตั้ง หรือยังเป็นค่าตัวอย่าง — ใส่ค่าสุ่มยาวใน .env "
            "(สร้างด้วย: openssl rand -base64 48) แล้วเริ่มแอปใหม่"
        )
    return s
