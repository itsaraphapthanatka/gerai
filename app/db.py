"""Data layer — SQLite (ดีฟอลต์) หรือ Postgres (เมื่อตั้ง DATABASE_URL).

ตั้ง  DATABASE_URL=postgresql://user:pass@host:5433/dbname  เพื่อใช้ Postgres
ไม่ตั้ง = ใช้ SQLite ไฟล์ GEO_DB_PATH เหมือนเดิม
รองรับทั้งสองด้วย: RETURNING id (SQLite 3.35+ รองรับ), placeholder ?→%s, row แบบ dict
"""
from __future__ import annotations
import os
import datetime
from pathlib import Path

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
IS_PG = DATABASE_URL.startswith("postgres")
DB_PATH = os.getenv("GEO_DB_PATH") or str(Path(__file__).resolve().parents[1] / "geo_platform.db")

if IS_PG:
    import psycopg
    from psycopg.rows import dict_row

_PK = "SERIAL PRIMARY KEY" if IS_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"


def _tables() -> list[str]:
    return [
        f"""CREATE TABLE IF NOT EXISTS tenants (
            id {_PK},
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT,
            is_admin INTEGER NOT NULL DEFAULT 0,
            plan TEXT NOT NULL DEFAULT 'free',
            created_at TEXT NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS brands (
            id {_PK},
            tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            domain TEXT NOT NULL,
            market TEXT,
            created_at TEXT NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS target_questions (
            id {_PK},
            brand_id INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
            question TEXT NOT NULL,
            lang TEXT DEFAULT 'th'
        )""",
        f"""CREATE TABLE IF NOT EXISTS monitoring_runs (
            id {_PK},
            brand_id INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            questions_total INTEGER DEFAULT 0,
            brand_hits INTEGER DEFAULT 0,
            share_of_voice REAL
        )""",
        f"""CREATE TABLE IF NOT EXISTS run_results (
            id {_PK},
            run_id INTEGER NOT NULL REFERENCES monitoring_runs(id) ON DELETE CASCADE,
            question_id INTEGER REFERENCES target_questions(id) ON DELETE SET NULL,
            question TEXT NOT NULL,
            brand_present INTEGER NOT NULL DEFAULT 0,
            position INTEGER,
            top_domains TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS health_checks (
            id {_PK},
            brand_id INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
            checked_at TEXT NOT NULL,
            ok INTEGER NOT NULL DEFAULT 0,
            n_fail INTEGER NOT NULL DEFAULT 0,
            n_warn INTEGER NOT NULL DEFAULT 0,
            report TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS ai_visibility (
            id {_PK},
            brand_id INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
            checked_at TEXT NOT NULL,
            asked INTEGER NOT NULL DEFAULT 0,
            cited INTEGER NOT NULL DEFAULT 0,
            named INTEGER NOT NULL DEFAULT 0,
            rate INTEGER,
            report TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS gsc_snapshots (
            id {_PK},
            brand_id INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
            synced_at TEXT NOT NULL,
            linked INTEGER NOT NULL DEFAULT 0,
            indexed INTEGER NOT NULL DEFAULT 0,
            total INTEGER NOT NULL DEFAULT 0,
            clicks INTEGER NOT NULL DEFAULT 0,
            impressions INTEGER NOT NULL DEFAULT 0,
            report TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS pagespeed_scans (
            id {_PK},
            brand_id INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
            checked_at TEXT NOT NULL,
            perf_mobile INTEGER,
            perf_desktop INTEGER,
            seo_mobile INTEGER,
            report TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS ai_serp_checks (
            id {_PK},
            brand_id INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
            checked_at TEXT NOT NULL,
            asked INTEGER NOT NULL DEFAULT 0,
            overview_shown INTEGER NOT NULL DEFAULT 0,
            overview_hit INTEGER NOT NULL DEFAULT 0,
            mode_hit INTEGER NOT NULL DEFAULT 0,
            searches INTEGER NOT NULL DEFAULT 0,
            report TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS chat_messages (
            id {_PK},
            brand_id INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS email_campaigns (
            id {_PK},
            name TEXT NOT NULL,
            subject TEXT NOT NULL,
            body_md TEXT NOT NULL,
            audience TEXT NOT NULL DEFAULT 'all',
            schedule TEXT NOT NULL DEFAULT 'monthly',
            attach_report INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'active',
            next_at TEXT,
            last_sent_at TEXT,
            created_at TEXT NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS email_log (
            id {_PK},
            campaign_id INTEGER REFERENCES email_campaigns(id) ON DELETE SET NULL,
            tenant_id INTEGER,
            to_email TEXT NOT NULL,
            brand_id INTEGER,
            sent_at TEXT NOT NULL,
            ok INTEGER NOT NULL DEFAULT 0,
            error TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS autopilot_runs (
            id {_PK},
            brand_id INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            ok INTEGER NOT NULL DEFAULT 0,
            summary TEXT,
            report TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS leads (
            id {_PK},
            created_at TEXT NOT NULL,
            updated_at TEXT,
            site_url TEXT NOT NULL,
            domain TEXT NOT NULL,
            name TEXT,
            email TEXT,
            contact TEXT,
            consent INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'audit',
            status TEXT NOT NULL DEFAULT 'new',
            score INTEGER,
            audit_json TEXT,
            audit_at TEXT,
            token TEXT,
            notes TEXT,
            ip TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS decisions (
            id {_PK},
            created_at TEXT NOT NULL,
            agent TEXT NOT NULL,
            kind TEXT NOT NULL,
            lead_id INTEGER,
            brand_id INTEGER,
            title TEXT NOT NULL,
            proposal TEXT,
            payload_json TEXT,
            status TEXT NOT NULL DEFAULT 'proposed',
            decided_at TEXT,
            decided_by TEXT,
            result TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS lead_events (
            id {_PK},
            lead_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            kind TEXT NOT NULL,
            note TEXT,
            actor TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS quotes (
            id {_PK},
            created_at TEXT NOT NULL,
            lead_id INTEGER NOT NULL,
            number TEXT NOT NULL,
            token TEXT NOT NULL,
            package TEXT NOT NULL,
            price INTEGER NOT NULL,
            discount_pct REAL NOT NULL DEFAULT 0,
            net INTEGER NOT NULL,
            setup INTEGER NOT NULL DEFAULT 0,
            setup_price INTEGER NOT NULL DEFAULT 0,
            first_total INTEGER NOT NULL,
            valid_until TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            sent_at TEXT,
            viewed_at TEXT,
            accepted_at TEXT,
            accepted_name TEXT,
            accepted_ip TEXT,
            claimed_at TEXT,
            slip_path TEXT,
            paid_at TEXT,
            tenant_id INTEGER,
            brand_id INTEGER,
            decision_id INTEGER,
            notes TEXT,
            created_by TEXT,
            stripe_session_id TEXT,
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            paid_via TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS stripe_invoices (
            id {_PK},
            created_at TEXT NOT NULL,
            quote_id INTEGER,
            tenant_id INTEGER,
            invoice_id TEXT UNIQUE,
            number TEXT,
            amount INTEGER,
            status TEXT,
            hosted_url TEXT,
            pdf_url TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS inbox_messages (
            id {_PK},
            received_at TEXT NOT NULL,
            message_id TEXT UNIQUE,
            from_addr TEXT,
            subject TEXT,
            body TEXT,
            lead_id INTEGER,
            category TEXT,
            action TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS partners (
            id {_PK},
            created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'prospect',
            kind TEXT,
            name TEXT,
            company TEXT,
            email TEXT,
            phone TEXT,
            website TEXT,
            domain TEXT,
            clients TEXT,
            code TEXT UNIQUE,
            token TEXT NOT NULL,
            source TEXT,
            touches INTEGER NOT NULL DEFAULT 0,
            last_contacted_at TEXT,
            replied_at TEXT,
            applied_at TEXT,
            activated_at TEXT,
            payout TEXT,
            notes TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS partner_commissions (
            id {_PK},
            created_at TEXT NOT NULL,
            partner_id INTEGER NOT NULL,
            lead_id INTEGER,
            quote_id INTEGER,
            receipt_id INTEGER UNIQUE,
            base REAL NOT NULL DEFAULT 0,
            pct REAL NOT NULL DEFAULT 0,
            amount REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            eligible_at TEXT,
            paid_at TEXT,
            note TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS acct_docs (
            id {_PK},
            created_at TEXT NOT NULL,
            kind TEXT NOT NULL,
            number TEXT NOT NULL,
            token TEXT NOT NULL,
            quote_id INTEGER,
            lead_id INTEGER,
            tenant_id INTEGER,
            invoice_id INTEGER,
            period_start TEXT,
            period_end TEXT,
            description TEXT,
            items_json TEXT,
            subtotal REAL NOT NULL DEFAULT 0,
            vat REAL NOT NULL DEFAULT 0,
            total REAL NOT NULL DEFAULT 0,
            wht REAL NOT NULL DEFAULT 0,
            vat_mode TEXT NOT NULL DEFAULT 'none',
            buyer_name TEXT,
            buyer_tax_id TEXT,
            buyer_branch TEXT,
            buyer_address TEXT,
            buyer_email TEXT,
            source TEXT,
            stripe_ref TEXT,
            status TEXT NOT NULL DEFAULT 'issued',
            due_date TEXT,
            paid_at TEXT,
            slip_path TEXT,
            sent_at TEXT,
            reminders INTEGER NOT NULL DEFAULT 0,
            void_reason TEXT,
            decision_id INTEGER,
            UNIQUE(kind, number)
        )""",
        f"""CREATE TABLE IF NOT EXISTS agent_tasks (
            id {_PK},
            created_at TEXT NOT NULL,
            agent TEXT NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            reason TEXT,
            args_json TEXT,
            status TEXT NOT NULL DEFAULT 'queued',
            started_at TEXT,
            finished_at TEXT,
            result TEXT,
            created_by TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS posts (
            id {_PK},
            created_at TEXT NOT NULL,
            channel TEXT NOT NULL,
            kind TEXT NOT NULL,
            content_id INTEGER,
            title TEXT,
            text TEXT,
            link TEXT,
            status TEXT NOT NULL DEFAULT 'proposed',
            decision_id INTEGER,
            published_at TEXT,
            external_id TEXT,
            result TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS rank_results (
            id {_PK},
            brand_id INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
            question_id INTEGER REFERENCES target_questions(id) ON DELETE SET NULL,
            question TEXT NOT NULL,
            position INTEGER,
            url TEXT,
            engine TEXT,
            checked_at TEXT NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS content_items (
            id {_PK},
            brand_id INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
            question_id INTEGER REFERENCES target_questions(id) ON DELETE SET NULL,
            lang TEXT NOT NULL DEFAULT 'th',
            title TEXT, meta_title TEXT, meta_desc TEXT,
            body_md TEXT, schema_json TEXT, source TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            wp_post_id INTEGER, wp_link TEXT,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            val TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS contacts (
            id {_PK},
            name TEXT NOT NULL,
            contact TEXT NOT NULL,
            plan TEXT,
            message TEXT,
            status TEXT NOT NULL DEFAULT 'new',
            created_at TEXT NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS notifications (
            id {_PK},
            tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            type TEXT,
            message TEXT NOT NULL,
            link TEXT,
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS payments (
            id {_PK},
            tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            plan TEXT NOT NULL,
            amount INTEGER NOT NULL,
            slip_path TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            confirmed_at TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS wp_connections (
            id {_PK},
            brand_id INTEGER NOT NULL UNIQUE REFERENCES brands(id) ON DELETE CASCADE,
            site_url TEXT NOT NULL,
            auth_user TEXT NOT NULL,
            auth_secret TEXT NOT NULL,
            status TEXT,
            mode TEXT NOT NULL DEFAULT 'rest',
            api_key TEXT,
            created_at TEXT NOT NULL
        )""",
    ]


def now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def q(sql: str) -> str:
    """แปลง placeholder ? -> %s เมื่อใช้ Postgres (SQL เราไม่มี ? ที่เป็น literal)."""
    return sql.replace("?", "%s") if IS_PG else sql


def get_conn():
    if IS_PG:
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)
    import sqlite3

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_column(c, table: str, col: str, ddl: str) -> None:
    if IS_PG:
        c.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {ddl}")
    else:
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})")]
        if col not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


def init_db() -> None:
    with get_conn() as c:
        for stmt in _tables():
            c.execute(stmt)
        # migrations สำหรับ DB เก่า (DB ใหม่มีคอลัมน์ครบตั้งแต่ CREATE แล้ว)
        _ensure_column(c, "tenants", "is_admin", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(c, "tenants", "plan", "TEXT NOT NULL DEFAULT 'free'")
        _ensure_column(c, "tenants", "requested_plan", "TEXT")
        _ensure_column(c, "content_items", "wp_link", "TEXT")
        _ensure_column(c, "content_items", "published_at", "TEXT")
        _ensure_column(c, "wp_connections", "mode", "TEXT NOT NULL DEFAULT 'rest'")
        _ensure_column(c, "wp_connections", "api_key", "TEXT")
        _ensure_column(c, "brands", "embed_key", "TEXT")
        _ensure_column(c, "brands", "auto_run_days", "INTEGER DEFAULT 7")
        _ensure_column(c, "brands", "auto_run_time", "TEXT DEFAULT '08:00'")
        _ensure_column(c, "brands", "auto_content_weekly", "INTEGER DEFAULT 0")
        _ensure_column(c, "brands", "last_auto_content_at", "TEXT")
        _ensure_column(c, "brands", "auto_publish_days", "INTEGER DEFAULT -1")
        _ensure_column(c, "brands", "auto_image", "INTEGER DEFAULT 0")
        _ensure_column(c, "brands", "facts", "TEXT")           # ข้อมูลจริงของแบรนด์ (ลูกค้ากรอกเอง)
        _ensure_column(c, "brands", "site_context", "TEXT")    # เนื้อหาจากเว็บจริง (cache สำหรับ grounding)
        _ensure_column(c, "brands", "schema_type", "TEXT")     # schema.org @type ขององค์กร (ว่าง = เดาจากชื่อ/ตลาด)
        _ensure_column(c, "ai_visibility", "cost_usd", "REAL")  # ค่าใช้จ่ายจริงต่อรอบ จาก usage ที่ API ส่งกลับ
        _ensure_column(c, "brands", "autopilot", "INTEGER DEFAULT 0")            # เปิด Autopilot รายสัปดาห์
        _ensure_column(c, "brands", "autopilot_mode", "TEXT DEFAULT 'draft'")    # draft | publish
        for col, ddl in (("line_user_id", "TEXT"), ("package", "TEXT"), ("monthly_thb", "INTEGER"),
                         ("last_contacted_at", "TEXT"), ("unsub_at", "TEXT")):
            _ensure_column(c, "leads", col, ddl)      # เฟส 2: ช่องทาง LINE · ดีล · ขอไม่รับการติดต่อ
        for col in ("stripe_session_id", "stripe_customer_id", "stripe_subscription_id", "paid_via"):
            _ensure_column(c, "quotes", col, "TEXT")  # Stripe: session/ลูกค้า/การสมัคร และช่องทางที่จ่าย
        _ensure_column(c, "tenants", "stripe_customer_id", "TEXT")    # ลูกค้าเปิด Customer Portal ของ Stripe จากในแอป
        _ensure_column(c, "leads", "src", "TEXT")                      # ช่องทางที่ลีดมา (line, fb, article, direct, line_chat, prospect)
        _ensure_column(c, "leads", "deep_json", "TEXT")                # รายงานเต็มชั้น 1: ไต่เว็บ 100 หน้า + อันดับ Google
        _ensure_column(c, "leads", "deep_ai_json", "TEXT")             # รายงานเต็มชั้น 2: 20 คำถาม AI
        for col in ("buyer_name", "buyer_tax_id", "buyer_branch", "buyer_address"):   # ข้อมูลออกใบเสร็จ/ใบกำกับภาษีของผู้ซื้อ
            _ensure_column(c, "quotes", col, "TEXT")
        _ensure_column(c, "leads", "exp_arm", "TEXT")
        _ensure_column(c, "leads", "partner_id", "INTEGER")                  # ลีดที่พันธมิตรแนะนำมา (ลิงก์ /p/<code> หรือส่งผ่านแดชบอร์ดพันธมิตร)                     # การทดลองข้อความทัก "<exp>:<A|B>" ตอนส่งจริง
        _ensure_column(c, "quotes", "cycle", "TEXT DEFAULT 'month'")     # รอบบิล month | year
        _ensure_column(c, "quotes", "period_total", "INTEGER")           # ยอดต่อรอบบิล (รายปี = ทั้งปี)
        _ensure_column(c, "posts", "image", "TEXT")                    # การ์ดรูปของโพสต์ (URL ใต้ /static/gen/posts)
        _ensure_column(c, "leads", "call_json", "TEXT")                # บทโทรปิดดีล: เบอร์ที่หาได้ + ผลโทรแต่ละครั้ง
        _ensure_column(c, "brands", "case_study_ok", "INTEGER DEFAULT 0")   # อนุญาตให้ใช้ชื่อแบรนด์ในเคสศึกษาของเจอ.AI
        _ensure_column(c, "brands", "autopilot_content", "INTEGER DEFAULT 1")    # เขียนคอนเทนต์กี่ชิ้นต่อรอบ (0 = วัดอย่างเดียว)
        _ensure_column(c, "brands", "aiserp_auto", "INTEGER DEFAULT 0")          # เช็ค Google AI Overview/Mode อัตโนมัติรายสัปดาห์ (กินโควตา SerpApi — เลือกเป็นรายแบรนด์)
        _ensure_column(c, "target_questions", "source", "TEXT DEFAULT 'manual'")   # manual | auto (ระบบคิดเพิ่มเองเมื่อปิดครบทุกข้อ)
        _ensure_column(c, "target_questions", "created_at", "TEXT")                # ของเดิมก่อนมีคอลัมน์นี้ = NULL
        _ensure_column(c, "content_items", "image_note", "TEXT")                   # ผลการหารูป: ได้จากไหน / ไม่ได้เพราะอะไร (ไม่เงียบ)
        # สร้าง embed_key ให้แบรนด์เก่าที่ยังไม่มี
        import secrets as _s
        rows = c.execute(q("SELECT id FROM brands WHERE embed_key IS NULL")).fetchall()
        for row in rows:
            c.execute(q("UPDATE brands SET embed_key=? WHERE id=?"),
                      (_s.token_urlsafe(16), row["id"]))


# ---- tenants ----
def create_tenant(email: str, password_hash: str, name: str = "", is_admin: bool = False) -> int:
    with get_conn() as c:
        cur = c.execute(
            q("INSERT INTO tenants(email,password_hash,name,is_admin,created_at) VALUES(?,?,?,?,?) RETURNING id"),
            (email.lower().strip(), password_hash, name, 1 if is_admin else 0, now()),
        )
        return cur.fetchone()["id"]


def get_tenant_by_email(email: str):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM tenants WHERE email=?"), (email.lower().strip(),)).fetchone()


def set_admin(tenant_id: int, is_admin: bool = True) -> None:
    with get_conn() as c:
        c.execute(q("UPDATE tenants SET is_admin=? WHERE id=?"), (1 if is_admin else 0, tenant_id))


def set_email(tenant_id: int, email: str) -> bool:
    """เปลี่ยนอีเมลบัญชี — False ถ้าซ้ำกับบัญชีอื่น (email เป็น UNIQUE) · บัญชีตัวอย่าง/ทดสอบใช้ @…local ซึ่งส่งอีเมลไม่ถึงใคร"""
    with get_conn() as c:
        dup = c.execute(q("SELECT id FROM tenants WHERE email=? AND id<>?"), (email, tenant_id)).fetchone()
        if dup:
            return False
        c.execute(q("UPDATE tenants SET email=? WHERE id=?"), (email, tenant_id))
    return True


def set_password(tenant_id: int, password_hash: str) -> None:
    with get_conn() as c:
        c.execute(q("UPDATE tenants SET password_hash=? WHERE id=?"), (password_hash, tenant_id))


def count_admins() -> int:
    with get_conn() as c:
        return c.execute("SELECT COUNT(*) AS n FROM tenants WHERE is_admin=1").fetchone()["n"]


def get_tenant(tenant_id: int):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM tenants WHERE id=?"), (tenant_id,)).fetchone()


def set_plan(tenant_id: int, plan: str) -> None:
    with get_conn() as c:
        # ตั้งแผน = เคลียร์คำขออัปเกรด (admin จัดการแล้ว)
        c.execute(q("UPDATE tenants SET plan=?, requested_plan=NULL WHERE id=?"), (plan, tenant_id))


def set_requested_plan(tenant_id: int, plan) -> None:
    with get_conn() as c:
        c.execute(q("UPDATE tenants SET requested_plan=? WHERE id=?"), (plan, tenant_id))


# ---- settings (key-value) ----
def get_setting(key: str, default: str = None):
    with get_conn() as c:
        r = c.execute(q("SELECT val FROM settings WHERE key=?"), (key,)).fetchone()
    return r["val"] if (r and r["val"] is not None) else default


def set_setting(key: str, val: str) -> None:
    with get_conn() as c:
        exists = c.execute(q("SELECT 1 FROM settings WHERE key=?"), (key,)).fetchone()
        if exists:
            c.execute(q("UPDATE settings SET val=? WHERE key=?"), (val, key))
        else:
            c.execute(q("INSERT INTO settings(key,val) VALUES(?,?)"), (key, val))


# ---- contacts (leads จากหน้า landing) ----
def add_contact(name: str, contact: str, plan: str = "", message: str = "") -> int:
    with get_conn() as c:
        row = c.execute(
            q("INSERT INTO contacts(name,contact,plan,message,status,created_at) "
              "VALUES(?,?,?,?,'new',?) RETURNING id"),
            (name, contact, plan, message, now()),
        ).fetchone()
        return row["id"]


def list_contacts(limit: int = 100):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM contacts ORDER BY id DESC LIMIT ?"), (limit,)).fetchall()


def count_new_contacts() -> int:
    with get_conn() as c:
        return c.execute("SELECT COUNT(*) AS n FROM contacts WHERE status='new'").fetchone()["n"]


def set_contact_status(cid: int, status: str) -> None:
    with get_conn() as c:
        c.execute(q("UPDATE contacts SET status=? WHERE id=?"), (status, cid))


# ---- notifications ----
def add_notification(tenant_id: int, message: str, link: str = None, type_: str = "info") -> None:
    with get_conn() as c:
        c.execute(
            q("INSERT INTO notifications(tenant_id,type,message,link,is_read,created_at) VALUES(?,?,?,?,0,?)"),
            (tenant_id, type_, message, link, now()),
        )


def notify_admins(message: str, link: str = None, type_: str = "info") -> None:
    with get_conn() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM tenants WHERE is_admin=1").fetchall()]
        for tid in ids:
            c.execute(
                q("INSERT INTO notifications(tenant_id,type,message,link,is_read,created_at) VALUES(?,?,?,?,0,?)"),
                (tid, type_, message, link, now()),
            )


def count_unread(tenant_id: int) -> int:
    with get_conn() as c:
        return c.execute(q("SELECT COUNT(*) AS n FROM notifications WHERE tenant_id=? AND is_read=0"),
                         (tenant_id,)).fetchone()["n"]


def list_notifications(tenant_id: int, limit: int = 40):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM notifications WHERE tenant_id=? ORDER BY id DESC LIMIT ?"),
                         (tenant_id, limit)).fetchall()


def mark_all_read(tenant_id: int) -> None:
    with get_conn() as c:
        c.execute(q("UPDATE notifications SET is_read=1 WHERE tenant_id=? AND is_read=0"), (tenant_id,))


# ---- payments (PromptPay) ----
def create_payment(tenant_id: int, plan: str, amount: int, slip_path) -> int:
    with get_conn() as c:
        cur = c.execute(
            q("INSERT INTO payments(tenant_id,plan,amount,slip_path,status,created_at) VALUES(?,?,?,?,?,?) RETURNING id"),
            (tenant_id, plan, amount, slip_path, "pending", now()),
        )
        return cur.fetchone()["id"]


def get_payment(pid: int):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM payments WHERE id=?"), (pid,)).fetchone()


def list_pending_payments():
    with get_conn() as c:
        return c.execute(
            "SELECT p.*, t.email AS tenant_email FROM payments p JOIN tenants t ON t.id=p.tenant_id "
            "WHERE p.status='pending' ORDER BY p.id DESC"
        ).fetchall()


def confirm_payment(pid: int) -> None:
    with get_conn() as c:
        c.execute(q("UPDATE payments SET status='confirmed', confirmed_at=? WHERE id=?"), (now(), pid))


def list_confirmed_payments():
    with get_conn() as c:
        return c.execute("SELECT * FROM payments WHERE status='confirmed' ORDER BY id").fetchall()


# ---- usage counters (billing) ----
def count_brands(tenant_id: int) -> int:
    with get_conn() as c:
        return c.execute(q("SELECT COUNT(*) AS n FROM brands WHERE tenant_id=?"), (tenant_id,)).fetchone()["n"]


def count_runs_month(tenant_id: int, ym: str) -> int:
    with get_conn() as c:
        return c.execute(
            q("SELECT COUNT(*) AS n FROM monitoring_runs r JOIN brands b ON b.id=r.brand_id "
              "WHERE b.tenant_id=? AND r.started_at LIKE ?"),
            (tenant_id, ym + "%"),
        ).fetchone()["n"]


def count_content_month(tenant_id: int, ym: str) -> int:
    with get_conn() as c:
        return c.execute(
            q("SELECT COUNT(*) AS n FROM content_items ci JOIN brands b ON b.id=ci.brand_id "
              "WHERE b.tenant_id=? AND ci.created_at LIKE ?"),
            (tenant_id, ym + "%"),
        ).fetchone()["n"]


def list_all_tenants():
    with get_conn() as c:
        return c.execute(
            "SELECT t.*, (SELECT COUNT(*) FROM brands b WHERE b.tenant_id=t.id) AS brand_count "
            "FROM tenants t ORDER BY t.is_admin DESC, t.id"
        ).fetchall()


def list_all_brands():
    with get_conn() as c:
        return c.execute(
            "SELECT b.*, t.email AS owner_email, "
            "(SELECT brand_hits FROM monitoring_runs r WHERE r.brand_id=b.id ORDER BY r.id DESC LIMIT 1) AS last_hits, "
            "(SELECT questions_total FROM monitoring_runs r WHERE r.brand_id=b.id ORDER BY r.id DESC LIMIT 1) AS last_total, "
            "(SELECT share_of_voice FROM monitoring_runs r WHERE r.brand_id=b.id ORDER BY r.id DESC LIMIT 1) AS last_sov, "
            "(SELECT started_at FROM monitoring_runs r WHERE r.brand_id=b.id ORDER BY r.id DESC LIMIT 1) AS last_run_at "
            "FROM brands b JOIN tenants t ON t.id=b.tenant_id ORDER BY b.id DESC"
        ).fetchall()


# ---- brands ----
def create_brand(tenant_id: int, name: str, domain: str, market: str = "") -> int:
    import secrets as _s
    with get_conn() as c:
        cur = c.execute(
            q("INSERT INTO brands(tenant_id,name,domain,market,embed_key,created_at) VALUES(?,?,?,?,?,?) RETURNING id"),
            (tenant_id, name, domain.lower().strip(), market, _s.token_urlsafe(16), now()),
        )
        return cur.fetchone()["id"]


def get_brand_by_embed_key(key: str):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM brands WHERE embed_key=?"), (key,)).fetchone()


def list_brands(tenant_id: int):
    with get_conn() as c:
        return c.execute(q(
            "SELECT b.*, "
            "(SELECT share_of_voice FROM monitoring_runs r WHERE r.brand_id=b.id AND r.status='done' ORDER BY r.id DESC LIMIT 1) AS last_sov, "
            "(SELECT started_at FROM monitoring_runs r WHERE r.brand_id=b.id AND r.status='done' ORDER BY r.id DESC LIMIT 1) AS last_run_at "
            "FROM brands b WHERE b.tenant_id=? ORDER BY b.id DESC"
        ), (tenant_id,)).fetchall()


def get_brand(brand_id: int, tenant_id: int | None = None):
    with get_conn() as c:
        if tenant_id is None:
            return c.execute(q("SELECT * FROM brands WHERE id=?"), (brand_id,)).fetchone()
        return c.execute(q("SELECT * FROM brands WHERE id=? AND tenant_id=?"), (brand_id, tenant_id)).fetchone()


def delete_brand(brand_id: int) -> None:
    with get_conn() as c:
        c.execute(q("DELETE FROM brands WHERE id=?"), (brand_id,))


def set_auto_schedule(brand_id: int, days: int, time_str: str) -> None:
    """ตั้งตาราง auto-run ต่อแบรนด์: days (0=ปิด, >0=ทุก N วัน) + time_str 'HH:MM' (เวลาไทย)"""
    with get_conn() as c:
        c.execute(q("UPDATE brands SET auto_run_days=?, auto_run_time=? WHERE id=?"), (days, time_str, brand_id))


def set_auto_content(brand_id: int, weekly: int) -> None:
    """ตั้งจำนวนร่างคอนเทนต์ที่ระบบสร้างอัตโนมัติต่อสัปดาห์ (0 = ปิด)"""
    with get_conn() as c:
        c.execute(q("UPDATE brands SET auto_content_weekly=? WHERE id=?"), (weekly, brand_id))


def touch_auto_content(brand_id: int, ts: str) -> None:
    with get_conn() as c:
        c.execute(q("UPDATE brands SET last_auto_content_at=? WHERE id=?"), (ts, brand_id))


def count_content_since(brand_id: int, ts: str) -> int:
    with get_conn() as c:
        return c.execute(
            q("SELECT COUNT(*) AS n FROM content_items WHERE brand_id=? AND created_at >= ?"), (brand_id, ts)
        ).fetchone()["n"]


def set_auto_image(brand_id: int, on: int) -> None:
    """เปิด/ปิด แนบรูปอัตโนมัติเมื่อสร้างคอนเทนต์ (0/1)"""
    with get_conn() as c:
        c.execute(q("UPDATE brands SET auto_image=? WHERE id=?"), (1 if on else 0, brand_id))


def set_brand_facts(brand_id: int, facts: str) -> None:
    with get_conn() as c:
        c.execute(q("UPDATE brands SET facts=? WHERE id=?"), (facts, brand_id))


def set_schema_type(brand_id: int, t) -> None:
    with get_conn() as c:
        c.execute(q("UPDATE brands SET schema_type=? WHERE id=?"), (t or None, brand_id))


def set_brand_site_context(brand_id: int, ctx: str) -> None:
    with get_conn() as c:
        c.execute(q("UPDATE brands SET site_context=? WHERE id=?"), (ctx, brand_id))


def update_content_body(content_id: int, body_md: str) -> None:
    with get_conn() as c:
        c.execute(q("UPDATE content_items SET body_md=? WHERE id=?"), (body_md, content_id))


def set_image_note(content_id: int, note: str) -> None:
    """บันทึกผลการหารูปประกอบของชิ้นงาน — หน้าคอนเทนต์โชว์ให้คนรู้ว่าทำไมไม่มีรูป"""
    with get_conn() as c:
        c.execute(q("UPDATE content_items SET image_note=? WHERE id=?"), ((note or "")[:300], content_id))


def update_content(content_id: int, title, meta_title, meta_desc, body_md, schema_json, source) -> None:
    """เขียนทับคอนเทนต์ทั้งชิ้น (ใช้ตอน regenerate)"""
    with get_conn() as c:
        c.execute(
            q("UPDATE content_items SET title=?, meta_title=?, meta_desc=?, body_md=?, schema_json=?, source=? WHERE id=?"),
            (title, meta_title, meta_desc, body_md, schema_json, source, content_id),
        )


def set_auto_publish(brand_id: int, days: int) -> None:
    """โหมดเผยแพร่ร่าง auto: -1 = ร่างเท่านั้น, 0 = ทันที, N = หลัง N วัน (review window)"""
    with get_conn() as c:
        c.execute(q("UPDATE brands SET auto_publish_days=? WHERE id=?"), (days, brand_id))


def due_auto_drafts(brand_id: int, cutoff_ts: str):
    """ร่างที่ระบบสร้าง (source='auto') ยังเป็น draft และถึงกำหนดเผยแพร่ (created_at <= cutoff)"""
    with get_conn() as c:
        return c.execute(
            q("SELECT * FROM content_items WHERE brand_id=? AND status='draft' AND source='auto' AND created_at <= ? ORDER BY id"),
            (brand_id, cutoff_ts),
        ).fetchall()


# ---- questions ----
def add_question(brand_id: int, question: str, lang: str = "th", source: str = "manual") -> int:
    """source: manual (คนเพิ่ม/กดปุ่มสร้าง) | auto (ระบบคิดเพิ่มเองเมื่อปิดครบทุกข้อ — หน้าเว็บติดป้ายให้ลบได้)"""
    import datetime
    with get_conn() as c:
        cur = c.execute(
            q("INSERT INTO target_questions(brand_id,question,lang,source,created_at) VALUES(?,?,?,?,?) RETURNING id"),
            (brand_id, question.strip(), lang, source, datetime.datetime.now().isoformat(timespec="seconds")),
        )
        return cur.fetchone()["id"]


def list_questions(brand_id: int):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM target_questions WHERE brand_id=? ORDER BY id"), (brand_id,)).fetchall()


def delete_question(qid: int) -> None:
    with get_conn() as c:
        c.execute(q("DELETE FROM target_questions WHERE id=?"), (qid,))


# ---- runs / results ----
def list_runs(brand_id: int):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM monitoring_runs WHERE brand_id=? ORDER BY id DESC"), (brand_id,)).fetchall()


def get_run(run_id: int):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM monitoring_runs WHERE id=?"), (run_id,)).fetchone()


def get_results(run_id: int):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM run_results WHERE run_id=? ORDER BY id"), (run_id,)).fetchall()


# ---- site health ----
def add_health_check(brand_id: int, res: dict) -> None:
    import json as _j
    with get_conn() as c:
        c.execute(
            q("INSERT INTO health_checks(brand_id,checked_at,ok,n_fail,n_warn,report) VALUES(?,?,?,?,?,?)"),
            (brand_id, res["checked_at"], 1 if res["ok"] else 0,
             res["n_fail"], res["n_warn"], _j.dumps(res, ensure_ascii=False)),
        )


def add_ai_visibility(brand_id: int, res: dict) -> None:
    import json as _j
    with get_conn() as c:
        c.execute(
            q("INSERT INTO ai_visibility(brand_id,checked_at,asked,cited,named,rate,cost_usd,report)"
              " VALUES(?,?,?,?,?,?,?,?)"),
            (brand_id, res["checked_at"], res["asked"], res["cited"], res["named"],
             res["rate"], res.get("cost_usd") or 0.0, _j.dumps(res, ensure_ascii=False)),
        )


def ai_cost_30d(brand_id: int) -> float:
    """ค่าใช้จ่ายรวม 30 วันล่าสุดของแบรนด์ (USD) — ตอบคำถาม "แพงไหม" ด้วยของจริงแทนการประเมิน"""
    since = (datetime.datetime.now() - datetime.timedelta(days=30)).isoformat(timespec="seconds")
    with get_conn() as c:
        r = c.execute(q("SELECT COALESCE(SUM(cost_usd),0) AS s FROM ai_visibility WHERE brand_id=? AND checked_at>=?"),
                      (brand_id, since)).fetchone()
    return float(r["s"] or 0)


def last_ai_visibility(brand_id: int):
    with get_conn() as c:
        return c.execute(
            q("SELECT * FROM ai_visibility WHERE brand_id=? ORDER BY checked_at DESC LIMIT 1"),
            (brand_id,),
        ).fetchone()


# ---- Google Search Console ----
def add_gsc_snapshot(brand_id: int, res: dict) -> None:
    """เก็บผลซิงค์ทั้งก้อน (JSON) + ตัวเลขหัว ๆ แยกคอลัมน์ให้หน้าแบรนด์อ่านได้โดยไม่ต้องแกะ JSON"""
    import json as _j
    idx, an = res.get("index") or {}, (res.get("analytics") or {}).get("site") or {}
    with get_conn() as c:
        c.execute(
            q("INSERT INTO gsc_snapshots(brand_id,synced_at,linked,indexed,total,clicks,impressions,report)"
              " VALUES(?,?,?,?,?,?,?,?)"),
            (brand_id, res["synced_at"], 1 if res.get("linked") else 0,
             int(idx.get("indexed") or 0), int(idx.get("total") or 0),
             int(an.get("clicks") or 0), int(an.get("impressions") or 0),
             _j.dumps(res, ensure_ascii=False)),
        )


def last_gsc(brand_id: int):
    with get_conn() as c:
        return c.execute(
            q("SELECT * FROM gsc_snapshots WHERE brand_id=? ORDER BY synced_at DESC LIMIT 1"),
            (brand_id,),
        ).fetchone()


# ---- Page speed ----
def add_pagespeed(brand_id: int, res: dict) -> None:
    import json as _j
    m, d = res.get("mobile") or {}, res.get("desktop") or {}
    with get_conn() as c:
        c.execute(
            q("INSERT INTO pagespeed_scans(brand_id,checked_at,perf_mobile,perf_desktop,seo_mobile,report) VALUES(?,?,?,?,?,?)"),
            (brand_id, res["checked_at"], (m.get("scores") or {}).get("performance"),
             (d.get("scores") or {}).get("performance"), (m.get("scores") or {}).get("seo"),
             _j.dumps(res, ensure_ascii=False)),
        )


def last_pagespeed(brand_id: int):
    with get_conn() as c:
        return c.execute(
            q("SELECT * FROM pagespeed_scans WHERE brand_id=? ORDER BY checked_at DESC LIMIT 1"), (brand_id,)
        ).fetchone()


def pagespeed_history(brand_id: int, limit: int = 8):
    with get_conn() as c:
        return c.execute(
            q("SELECT checked_at, perf_mobile, perf_desktop, seo_mobile FROM pagespeed_scans "
              "WHERE brand_id=? ORDER BY checked_at DESC LIMIT ?"), (brand_id, limit)
        ).fetchall()


# ---- Autopilot ----
def set_autopilot(brand_id: int, on: bool, mode: str, content_n: int) -> None:
    with get_conn() as c:
        c.execute(q("UPDATE brands SET autopilot=?, autopilot_mode=?, autopilot_content=? WHERE id=?"),
                  (1 if on else 0, mode, content_n, brand_id))


def autopilot_brands():
    with get_conn() as c:
        return c.execute("SELECT * FROM brands WHERE autopilot=1 ORDER BY id").fetchall()


def add_autopilot_run(brand_id: int, log: dict) -> None:
    import json as _j
    with get_conn() as c:
        c.execute(q("INSERT INTO autopilot_runs(brand_id,started_at,finished_at,ok,summary,report) VALUES(?,?,?,?,?,?)"),
                  (brand_id, log["started_at"], log.get("finished_at"), 1 if log.get("ok") else 0,
                   (log.get("summary") or "")[:600], _j.dumps(log, ensure_ascii=False)))


def list_autopilot_runs(brand_id: int, limit: int = 10):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM autopilot_runs WHERE brand_id=? ORDER BY id DESC LIMIT ?"), (brand_id, limit)).fetchall()


def last_autopilot_run(brand_id: int):
    rows = list_autopilot_runs(brand_id, 1)
    return rows[0] if rows else None


# ---- Email campaigns ----
def create_campaign(name, subject, body_md, audience, schedule, attach_report, next_at) -> int:
    with get_conn() as c:
        cur = c.execute(
            q("INSERT INTO email_campaigns(name,subject,body_md,audience,schedule,attach_report,status,next_at,created_at)"
              " VALUES(?,?,?,?,?,?,'active',?,?) RETURNING id"),
            (name, subject, body_md, audience, schedule, 1 if attach_report else 0, next_at, now()))
        return cur.fetchone()["id"]


def list_campaigns():
    with get_conn() as c:
        return c.execute(
            "SELECT ec.*, (SELECT COUNT(*) FROM email_log l WHERE l.campaign_id=ec.id AND l.ok=1) AS sent_ok, "
            "(SELECT COUNT(*) FROM email_log l WHERE l.campaign_id=ec.id AND l.ok=0) AS sent_fail "
            "FROM email_campaigns ec ORDER BY ec.id DESC").fetchall()


def get_campaign(cid: int):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM email_campaigns WHERE id=?"), (cid,)).fetchone()


def due_campaigns(now_iso: str):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM email_campaigns WHERE status='active' AND next_at IS NOT NULL AND next_at<=? ORDER BY id"),
                         (now_iso,)).fetchall()


def touch_campaign(cid: int, last_sent_at: str, next_at, status: str) -> None:
    with get_conn() as c:
        c.execute(q("UPDATE email_campaigns SET last_sent_at=?, next_at=?, status=? WHERE id=?"), (last_sent_at, next_at, status, cid))


def set_campaign_status(cid: int, status: str) -> None:
    with get_conn() as c:
        c.execute(q("UPDATE email_campaigns SET status=? WHERE id=?"), (status, cid))


def delete_campaign(cid: int) -> None:
    with get_conn() as c:
        c.execute(q("DELETE FROM email_campaigns WHERE id=?"), (cid,))


def add_email_log(campaign_id, tenant_id, to_email, brand_id, ok: bool, error: str) -> None:
    with get_conn() as c:
        c.execute(q("INSERT INTO email_log(campaign_id,tenant_id,to_email,brand_id,sent_at,ok,error) VALUES(?,?,?,?,?,?,?)"),
                  (campaign_id, tenant_id, to_email, brand_id, now(), 1 if ok else 0, error or None))


def list_email_log(limit: int = 30):
    with get_conn() as c:
        return c.execute(
            "SELECT l.*, ec.name AS campaign_name, b.name AS brand_name FROM email_log l "
            "LEFT JOIN email_campaigns ec ON ec.id=l.campaign_id LEFT JOIN brands b ON b.id=l.brand_id "
            + q("ORDER BY l.id DESC LIMIT ?"), (limit,)).fetchall()


# ---- Analytics chat ----
def add_chat_message(brand_id: int, role: str, content: str) -> None:
    with get_conn() as c:
        c.execute(q("INSERT INTO chat_messages(brand_id,role,content,created_at) VALUES(?,?,?,?)"),
                  (brand_id, role, content, now()))


def list_chat_messages(brand_id: int, limit: int = 40):
    with get_conn() as c:
        rows = c.execute(q("SELECT * FROM chat_messages WHERE brand_id=? ORDER BY id DESC LIMIT ?"), (brand_id, limit)).fetchall()
    return list(reversed(rows))


def clear_chat(brand_id: int) -> None:
    with get_conn() as c:
        c.execute(q("DELETE FROM chat_messages WHERE brand_id=?"), (brand_id,))


# ---- Google AI Overview / AI Mode ----
def set_aiserp_auto(brand_id: int, on: bool) -> None:
    with get_conn() as c:
        c.execute(q("UPDATE brands SET aiserp_auto=? WHERE id=?"), (1 if on else 0, brand_id))


def aiserp_auto_brands():
    """แบรนด์ที่เปิดเช็ค Google AI อัตโนมัติ พร้อมจำนวนคำถาม — ไว้ประเมินโควตา SerpApi ต่อรอบ"""
    with get_conn() as c:
        return c.execute(
            "SELECT b.id, b.name, (SELECT COUNT(*) FROM target_questions t WHERE t.brand_id=b.id) AS n_questions "
            "FROM brands b WHERE b.aiserp_auto=1 ORDER BY b.id").fetchall()


def add_ai_serp(brand_id: int, res: dict) -> None:
    import json as _j
    bk = res.get("by_kind") or {}
    ov, md = bk.get("overview") or {}, bk.get("mode") or {}
    with get_conn() as c:
        c.execute(
            q("INSERT INTO ai_serp_checks(brand_id,checked_at,asked,overview_shown,overview_hit,mode_hit,searches,report)"
              " VALUES(?,?,?,?,?,?,?,?)"),
            (brand_id, res["checked_at"], res.get("asked") or 0, ov.get("shown") or 0,
             (ov.get("cited") or 0) + (ov.get("named") or 0), (md.get("cited") or 0) + (md.get("named") or 0),
             res.get("searches") or 0, _j.dumps(res, ensure_ascii=False)),
        )


def last_ai_serp(brand_id: int):
    with get_conn() as c:
        return c.execute(
            q("SELECT * FROM ai_serp_checks WHERE brand_id=? ORDER BY checked_at DESC LIMIT 1"), (brand_id,)
        ).fetchone()


def last_health_check(brand_id: int):
    with get_conn() as c:
        return c.execute(
            q("SELECT * FROM health_checks WHERE brand_id=? ORDER BY checked_at DESC LIMIT 1"), (brand_id,)
        ).fetchone()


def last_published_at(brand_id: int):
    with get_conn() as c:
        r = c.execute(
            q("SELECT MAX(published_at) AS d FROM content_items WHERE brand_id=? AND status='published'"),
            (brand_id,),
        ).fetchone()
        return r["d"] if r else None


# ---- Google rank tracking ----
# 1 รอบเช็ค = หลายแถวที่ใช้ checked_at เดียวกัน (ไม่มีตาราง run แยก — group ด้วย checked_at)
def add_rank_result(brand_id, question_id, question, position, url, engine, checked_at) -> None:
    with get_conn() as c:
        c.execute(
            q("INSERT INTO rank_results(brand_id,question_id,question,position,url,engine,checked_at) "
              "VALUES(?,?,?,?,?,?,?)"),
            (brand_id, question_id, question, position, url, engine, checked_at),
        )


def rank_batches(brand_id: int, limit: int = 12):
    """สรุปรายรอบ (ใหม่→เก่า): กี่คำถาม, ติดกี่คำถาม, อันดับเฉลี่ยเฉพาะที่ติด"""
    with get_conn() as c:
        return c.execute(
            q("SELECT checked_at, COUNT(*) AS total, "
              "SUM(CASE WHEN position IS NOT NULL THEN 1 ELSE 0 END) AS ranked, "
              "AVG(position) AS avg_pos "
              "FROM rank_results WHERE brand_id=? GROUP BY checked_at ORDER BY checked_at DESC LIMIT ?"),
            (brand_id, limit),
        ).fetchall()


def rank_results_at(brand_id: int, checked_at: str):
    with get_conn() as c:
        return c.execute(
            q("SELECT * FROM rank_results WHERE brand_id=? AND checked_at=? ORDER BY "
              "CASE WHEN position IS NULL THEN 1 ELSE 0 END, position, id"),
            (brand_id, checked_at),
        ).fetchall()


def last_rank_check(brand_id: int):
    with get_conn() as c:
        row = c.execute(
            q("SELECT MAX(checked_at) AS ts FROM rank_results WHERE brand_id=?"), (brand_id,)
        ).fetchone()
        return row["ts"] if row else None


# ---- content items ----
def create_content_item(brand_id, question_id, lang, title, meta_title, meta_desc, body_md, schema_json, source) -> int:
    with get_conn() as c:
        cur = c.execute(
            q("INSERT INTO content_items(brand_id,question_id,lang,title,meta_title,meta_desc,body_md,schema_json,source,status,created_at) "
              "VALUES(?,?,?,?,?,?,?,?,?,?,?) RETURNING id"),
            (brand_id, question_id, lang, title, meta_title, meta_desc, body_md, schema_json, source, "draft", now()),
        )
        return cur.fetchone()["id"]


def list_content(brand_id):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM content_items WHERE brand_id=? ORDER BY id DESC"), (brand_id,)).fetchall()


def get_content(content_id):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM content_items WHERE id=?"), (content_id,)).fetchone()


def delete_content(content_id) -> None:
    with get_conn() as c:
        c.execute(q("DELETE FROM content_items WHERE id=?"), (content_id,))


def mark_content_published(content_id, wp_post_id, wp_link) -> None:
    with get_conn() as c:
        c.execute(
            q("UPDATE content_items SET status='published', wp_post_id=?, wp_link=?, published_at=? WHERE id=?"),
            (wp_post_id, wp_link, now(), content_id),
        )


def get_sov_impact(brand_id: int):
    """คืน dict: runs ทั้งหมด + จุด publish + per-question before/after"""
    with get_conn() as c:
        runs = c.execute(
            q("SELECT id, started_at, share_of_voice, brand_hits, questions_total "
              "FROM monitoring_runs WHERE brand_id=? AND status='done' ORDER BY id"),
            (brand_id,),
        ).fetchall()
        publishes = c.execute(
            q("SELECT id, title, published_at, wp_link, question_id "
              "FROM content_items WHERE brand_id=? AND status='published' AND published_at IS NOT NULL ORDER BY published_at"),
            (brand_id,),
        ).fetchall()
        # per-question SoV: นับ brand_present ต่อ run
        q_rows = c.execute(
            q("SELECT rr.run_id, rr.question_id, rr.question, rr.brand_present, rr.position "
              "FROM run_results rr "
              "JOIN monitoring_runs mr ON mr.id=rr.run_id "
              "WHERE mr.brand_id=? AND mr.status='done' ORDER BY rr.run_id"),
            (brand_id,),
        ).fetchall()
    return {
        "runs": [dict(r) for r in runs],
        "publishes": [dict(p) for p in publishes],
        "question_results": [dict(r) for r in q_rows],
    }


def get_content_gaps(brand_id: int):
    """หา 'ช่องว่าง' คอนเทนต์: คำถามที่แบรนด์ยังไม่โผล่ใน AI จาก run ล่าสุด
    จัดระดับ: critical (ไม่มีคอนเทนต์) > draft (มีร่าง) > stale (เผยแพร่แล้วยังไม่โผล่) > unmeasured
    คืน dict: has_run, last_run_at, gaps[], critical, total_gaps, q_total
    """
    with get_conn() as c:
        run = c.execute(
            q("SELECT id, started_at FROM monitoring_runs WHERE brand_id=? AND status='done' ORDER BY id DESC LIMIT 1"),
            (brand_id,),
        ).fetchone()
        questions = c.execute(
            q("SELECT id, question, lang FROM target_questions WHERE brand_id=? ORDER BY id"), (brand_id,)
        ).fetchall()
        content_rows = c.execute(
            q("SELECT question_id, status FROM content_items WHERE brand_id=?"), (brand_id,)
        ).fetchall()
        present_map = {}
        if run:
            for r in c.execute(q("SELECT question_id, brand_present FROM run_results WHERE run_id=?"), (run["id"],)).fetchall():
                if r["question_id"] is not None:
                    present_map[r["question_id"]] = bool(r["brand_present"])
    has_content, has_published = set(), set()
    for r in content_rows:
        if r["question_id"] is not None:
            has_content.add(r["question_id"])
            if r["status"] == "published":
                has_published.add(r["question_id"])
    gaps = []
    if run:
        for qq in questions:
            qid = qq["id"]
            if present_map.get(qid):
                continue  # โผล่แล้ว ไม่ใช่ gap
            if qid not in present_map:
                level, reason = "unmeasured", "ยังไม่เคยวัด — รัน monitor"
            elif qid not in has_content:
                level, reason = "critical", "ยังไม่โผล่ + ยังไม่มีคอนเทนต์"
            elif qid not in has_published:
                level, reason = "draft", "มีร่างแล้ว ยังไม่เผยแพร่"
            else:
                level, reason = "stale", "เผยแพร่แล้วแต่ยังไม่โผล่"
            gaps.append({"question_id": qid, "question": qq["question"], "lang": qq["lang"],
                         "level": level, "reason": reason})
        order = {"critical": 0, "draft": 1, "stale": 2, "unmeasured": 3}
        gaps.sort(key=lambda g: order.get(g["level"], 9))
    return {
        "has_run": bool(run),
        "last_run_at": run["started_at"] if run else None,
        "gaps": gaps,
        "critical": sum(1 for g in gaps if g["level"] == "critical"),
        "total_gaps": len(gaps),
        "q_total": len(questions),
    }


# ---- ลูกค้าเป้าหมาย (leads) + การตัดสินใจของผู้บริหารเอเจนต์ ----
LEAD_FIELDS = {"name", "email", "contact", "consent", "status", "score", "audit_json", "audit_at", "token", "notes", "ip", "site_url", "domain",
               "line_user_id", "package", "monthly_thb", "last_contacted_at", "unsub_at", "src", "deep_json", "deep_ai_json", "call_json", "exp_arm", "partner_id"}


def add_lead(site_url: str, domain: str, source: str = "audit", name: str = "", email: str = "", contact: str = "",
             ip: str = "", token: str = None, audit_json: str = None, score: int = None) -> int:
    with get_conn() as c:
        cur = c.execute(
            q("INSERT INTO leads(created_at,updated_at,site_url,domain,name,email,contact,source,status,score,audit_json,audit_at,token,ip) "
              "VALUES(?,?,?,?,?,?,?,?,'new',?,?,?,?,?) RETURNING id"),
            (now(), now(), site_url, domain, name or None, email or None, contact or None, source, score, audit_json,
             now() if audit_json else None, token, ip or None),
        )
        return cur.fetchone()["id"]


def get_lead(lead_id: int):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM leads WHERE id=?"), (lead_id,)).fetchone()


def get_lead_by_token(token: str):
    if not token:
        return None
    with get_conn() as c:
        return c.execute(q("SELECT * FROM leads WHERE token=?"), (token,)).fetchone()


def find_lead_by_domain(domain: str):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM leads WHERE domain=? ORDER BY id DESC LIMIT 1"), (domain,)).fetchone()


def list_leads(status: str = None, limit: int = 200):
    with get_conn() as c:
        if status:
            return c.execute(q("SELECT * FROM leads WHERE status=? ORDER BY id DESC LIMIT ?"), (status, limit)).fetchall()
        return c.execute(q("SELECT * FROM leads ORDER BY id DESC LIMIT ?"), (limit,)).fetchall()


def update_lead(lead_id: int, **fields) -> None:
    """อัปเดตเฉพาะคอลัมน์ที่อนุญาต (กันชื่อคอลัมน์หลุดมาจากฟอร์ม)"""
    cols = [k for k in fields if k in LEAD_FIELDS]
    if not cols:
        return
    sets = ", ".join(f"{k}=?" for k in cols) + ", updated_at=?"
    with get_conn() as c:
        c.execute(q(f"UPDATE leads SET {sets} WHERE id=?"), tuple(fields[k] for k in cols) + (now(), lead_id))


def count_audits_since(ip: str, since_ts: str) -> int:
    with get_conn() as c:
        return c.execute(q("SELECT COUNT(*) AS n FROM leads WHERE ip=? AND audit_at >= ?"), (ip, since_ts)).fetchone()["n"]


def count_leads_since(since_ts: str) -> int:
    with get_conn() as c:
        return c.execute(q("SELECT COUNT(*) AS n FROM leads WHERE created_at >= ?"), (since_ts,)).fetchone()["n"]


def add_decision(agent: str, kind: str, title: str, proposal: str, payload: dict, lead_id: int = None,
                 brand_id: int = None, status: str = "proposed") -> int:
    import json as _json
    with get_conn() as c:
        cur = c.execute(
            q("INSERT INTO decisions(created_at,agent,kind,lead_id,brand_id,title,proposal,payload_json,status) "
              "VALUES(?,?,?,?,?,?,?,?,?) RETURNING id"),
            (now(), agent, kind, lead_id, brand_id, title[:200], proposal, _json.dumps(payload or {}, ensure_ascii=False), status),
        )
        return cur.fetchone()["id"]


def get_decision(did: int):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM decisions WHERE id=?"), (did,)).fetchone()


def list_decisions(status: str = None, limit: int = 100):
    with get_conn() as c:
        if status:
            return c.execute(q("SELECT * FROM decisions WHERE status=? ORDER BY id DESC LIMIT ?"), (status, limit)).fetchall()
        return c.execute(q("SELECT * FROM decisions ORDER BY id DESC LIMIT ?"), (limit,)).fetchall()


def count_decisions_for_lead(lead_id: int, status: str = "proposed") -> int:
    with get_conn() as c:
        return c.execute(q("SELECT COUNT(*) AS n FROM decisions WHERE lead_id=? AND status=?"), (lead_id, status)).fetchone()["n"]


def set_decision(did: int, status: str, decided_by: str = None, result: str = None) -> None:
    with get_conn() as c:
        c.execute(q("UPDATE decisions SET status=?, decided_at=?, decided_by=?, result=? WHERE id=?"),
                  (status, now(), decided_by, result, did))


def decision_stats(kind: str = None) -> dict:
    """ตรงกับเจ้าของ = อนุมัติ (approved/executed) ÷ ที่ตัดสินแล้วทั้งหมด — ตัวเลขด่าน (ต้อง ≥ 95%) · kind = นับเฉพาะเรื่องนั้น"""
    with get_conn() as c:
        if kind:
            rows = c.execute(q("SELECT status, COUNT(*) AS n FROM decisions WHERE kind=? GROUP BY status"), (kind,)).fetchall()
        else:
            rows = c.execute("SELECT status, COUNT(*) AS n FROM decisions WHERE kind != 'digest' GROUP BY status").fetchall()
    by = {r["status"]: r["n"] for r in rows}
    agreed = by.get("approved", 0) + by.get("executed", 0)
    decided = agreed + by.get("rejected", 0)
    return {"proposed": by.get("proposed", 0), "agreed": agreed, "rejected": by.get("rejected", 0), "decided": decided,
            "agreement_pct": round(agreed / decided * 100) if decided else None}



def list_decisions_since(since_ts: str):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM decisions WHERE created_at >= ? AND kind != 'digest' ORDER BY id DESC"), (since_ts,)).fetchall()


# ---- ไทม์ไลน์ลูกค้าเป้าหมาย (เฟส 2) ----
def add_lead_event(lead_id: int, kind: str, note: str = "", actor: str = "system") -> int:
    with get_conn() as c:
        cur = c.execute(q("INSERT INTO lead_events(lead_id,created_at,kind,note,actor) VALUES(?,?,?,?,?) RETURNING id"),
                        (lead_id, now(), kind, (note or "")[:1000], actor))
        return cur.fetchone()["id"]


def list_lead_events(lead_id: int, limit: int = 100):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM lead_events WHERE lead_id=? ORDER BY id DESC LIMIT ?"), (lead_id, limit)).fetchall()


def leads_with_event(kind: str, since_ts: str) -> set:
    with get_conn() as c:
        rows = c.execute(q("SELECT DISTINCT lead_id FROM lead_events WHERE kind=? AND created_at >= ?"), (kind, since_ts)).fetchall()
    return {r["lead_id"] for r in rows}


def count_events(kind: str, since_ts: str) -> int:
    with get_conn() as c:
        return c.execute(q("SELECT COUNT(*) AS n FROM lead_events WHERE kind=? AND created_at >= ?"), (kind, since_ts)).fetchone()["n"]


def get_lead_by_line_user(uid: str):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM leads WHERE line_user_id=? ORDER BY id DESC LIMIT 1"), (uid,)).fetchone()


def count_line_audits_since(uid: str, since_ts: str) -> int:
    with get_conn() as c:
        return c.execute(q("SELECT COUNT(*) AS n FROM leads WHERE line_user_id=? AND audit_at >= ?"), (uid, since_ts)).fetchone()["n"]


# ---- ใบเสนอราคา (เฟส 2) ----
QUOTE_FIELDS = {"status", "sent_at", "viewed_at", "accepted_at", "accepted_name", "accepted_ip", "claimed_at", "slip_path",
                "paid_at", "tenant_id", "brand_id", "decision_id", "notes", "valid_until",
                "stripe_session_id", "stripe_customer_id", "stripe_subscription_id", "paid_via",
                "buyer_name", "buyer_tax_id", "buyer_branch", "buyer_address",
                "cycle", "period_total", "net", "setup_price", "first_total",        # เปลี่ยนรอบบิลก่อนยืนยัน
                "package", "price", "discount_pct", "setup"}                          # เจ้าของสั่งปรับใบเดิม


def add_quote(lead_id: int, number: str, token: str, package: str, price: int, discount_pct: float, net: int,
              setup: bool, setup_price: int, first_total: int, valid_until: str, created_by: str = "ceo", notes: str = "",
              cycle: str = "month", period_total: int | None = None) -> int:
    with get_conn() as c:
        cur = c.execute(q("INSERT INTO quotes(created_at,lead_id,number,token,package,price,discount_pct,net,setup,setup_price,"
                          "first_total,valid_until,status,created_by,notes,cycle,period_total) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'draft',?,?,?,?) RETURNING id"),
                        (now(), lead_id, number, token, package, price, discount_pct, net, 1 if setup else 0, setup_price,
                         first_total, valid_until, created_by, notes or None, cycle, period_total if period_total is not None else net))
        return cur.fetchone()["id"]


def get_quote(qid: int):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM quotes WHERE id=?"), (qid,)).fetchone()


def get_quote_by_token(token: str):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM quotes WHERE token=?"), (token,)).fetchone()


def get_quote_by_customer(customer_id: str):
    if not customer_id:
        return None
    with get_conn() as c:
        return c.execute(q("SELECT * FROM quotes WHERE stripe_customer_id=? ORDER BY id DESC LIMIT 1"), (customer_id,)).fetchone()


def set_tenant_stripe_customer(tenant_id: int, customer_id: str) -> None:
    with get_conn() as c:
        c.execute(q("UPDATE tenants SET stripe_customer_id=? WHERE id=?"), (customer_id, tenant_id))


def add_stripe_invoice(quote_id, tenant_id, invoice_id: str, amount: int, status: str, hosted_url, pdf_url, number=None) -> None:
    """เก็บใบแจ้งหนี้/ใบเสร็จของ Stripe ไว้โชว์ให้ลูกค้า (ส่งซ้ำไม่ซ้ำ)"""
    if not invoice_id:
        return
    with get_conn() as c:
        if c.execute(q("SELECT id FROM stripe_invoices WHERE invoice_id=?"), (invoice_id,)).fetchone():
            c.execute(q("UPDATE stripe_invoices SET status=?, hosted_url=?, pdf_url=?, amount=? WHERE invoice_id=?"),
                      (status, hosted_url, pdf_url, amount, invoice_id))
            return
        c.execute(q("INSERT INTO stripe_invoices(created_at,quote_id,tenant_id,invoice_id,number,amount,status,hosted_url,pdf_url) VALUES(?,?,?,?,?,?,?,?,?)"),
                  (now(), quote_id, tenant_id, invoice_id, number, amount, status, hosted_url, pdf_url))


def list_stripe_invoices(tenant_id: int, limit: int = 50):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM stripe_invoices WHERE tenant_id=? ORDER BY id DESC LIMIT ?"), (tenant_id, limit)).fetchall()


def get_quote_by_subscription(sub_id: str):
    if not sub_id:
        return None
    with get_conn() as c:
        return c.execute(q("SELECT * FROM quotes WHERE stripe_subscription_id=? ORDER BY id DESC LIMIT 1"), (sub_id,)).fetchone()


def list_quotes(lead_id: int = None, status: str = None, limit: int = 100):
    sql, args = "SELECT * FROM quotes WHERE 1=1", []
    if lead_id is not None:
        sql += " AND lead_id=?"
        args.append(lead_id)
    if status:
        sql += " AND status=?"
        args.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with get_conn() as c:
        return c.execute(q(sql), tuple(args)).fetchall()


def update_quote(qid: int, **fields) -> None:
    cols = [k for k in fields if k in QUOTE_FIELDS]
    if not cols:
        return
    with get_conn() as c:
        c.execute(q(f"UPDATE quotes SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?"), tuple(fields[k] for k in cols) + (qid,))


def count_quotes_month(ym: str) -> int:
    with get_conn() as c:
        return c.execute(q("SELECT COUNT(*) AS n FROM quotes WHERE created_at LIKE ?"), (ym + "%",)).fetchone()["n"]


def count_audits_by_src(since_ts: str) -> dict:
    """ลีดที่ตรวจเว็บในช่วงนี้ แยกตามช่องทางที่มา (ไม่ระบุ = direct)"""
    with get_conn() as c:
        rows = c.execute(q("SELECT COALESCE(src, 'direct') AS s, COUNT(*) AS n FROM leads WHERE audit_at >= ? GROUP BY COALESCE(src, 'direct') ORDER BY n DESC"),
                         (since_ts,)).fetchall()
    return {r["s"]: r["n"] for r in rows}


def set_brand_case_study(brand_id: int, ok: bool) -> None:
    with get_conn() as c:
        c.execute(q("UPDATE brands SET case_study_ok=? WHERE id=?"), (1 if ok else 0, brand_id))


# ---- โพสต์การตลาด (CMO) ----
POST_FIELDS = {"status", "decision_id", "published_at", "external_id", "result", "text", "title", "link", "image"}


def add_post(channel: str, kind: str, content_id, title: str, text: str, link: str | None, status: str = "proposed") -> int:
    with get_conn() as c:
        cur = c.execute(q("INSERT INTO posts(created_at,channel,kind,content_id,title,text,link,status) VALUES(?,?,?,?,?,?,?,?) RETURNING id"),
                        (now(), channel, kind, content_id, (title or "")[:200], text, link, status))
        return cur.fetchone()["id"]


def get_post(pid: int):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM posts WHERE id=?"), (pid,)).fetchone()


def list_posts(limit: int = 100, channel: str = None):
    with get_conn() as c:
        if channel:
            return c.execute(q("SELECT * FROM posts WHERE channel=? ORDER BY id DESC LIMIT ?"), (channel, limit)).fetchall()
        return c.execute(q("SELECT * FROM posts ORDER BY id DESC LIMIT ?"), (limit,)).fetchall()


def update_post(pid: int, **fields) -> None:
    cols = [k for k in fields if k in POST_FIELDS]
    if not cols:
        return
    with get_conn() as c:
        c.execute(q(f"UPDATE posts SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?"), tuple(fields[k] for k in cols) + (pid,))


def count_posts_since(channel: str, since_ts: str, kind: str = None) -> int:
    """โพสต์ที่ยังเดินอยู่ (ไม่นับที่ถูกปฏิเสธ/ล้ม) ในช่วงเวลา — ใช้คุมโควตาต่อสัปดาห์"""
    sql, args = "SELECT COUNT(*) AS n FROM posts WHERE channel=? AND created_at >= ? AND status NOT IN ('rejected','failed','cancelled')", [channel, since_ts]
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    with get_conn() as c:
        return c.execute(q(sql), tuple(args)).fetchone()["n"]


def posted_keys() -> set:
    """(channel, kind, content_id) ที่เคยเสนอ/โพสต์แล้ว (ยกเว้นที่ล้ม) — กันโพสต์บทความเดิมซ้ำ"""
    with get_conn() as c:
        rows = c.execute("SELECT channel, kind, content_id FROM posts WHERE status NOT IN ('failed','cancelled')").fetchall()
    return {(r["channel"], r["kind"], r["content_id"]) for r in rows}


def count_quotes_paid_since(since_ts: str) -> int:
    with get_conn() as c:
        return c.execute(q("SELECT COUNT(*) AS n FROM quotes WHERE status='paid' AND paid_at >= ?"), (since_ts,)).fetchone()["n"]

# ---- WordPress connections ----
def upsert_wp_connection(brand_id, site_url, auth_user, auth_secret, status, mode="rest", api_key=None) -> None:
    with get_conn() as c:
        exists = c.execute(q("SELECT id FROM wp_connections WHERE brand_id=?"), (brand_id,)).fetchone()
        if exists:
            c.execute(
                q("UPDATE wp_connections SET site_url=?,auth_user=?,auth_secret=?,status=?,mode=?,api_key=? WHERE brand_id=?"),
                (site_url, auth_user, auth_secret, status, mode, api_key, brand_id),
            )
        else:
            c.execute(
                q("INSERT INTO wp_connections(brand_id,site_url,auth_user,auth_secret,status,mode,api_key,created_at) VALUES(?,?,?,?,?,?,?,?)"),
                (brand_id, site_url, auth_user, auth_secret, status, mode, api_key, now()),
            )


def get_wp_connection(brand_id):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM wp_connections WHERE brand_id=?"), (brand_id,)).fetchone()


def delete_wp_connection(brand_id) -> None:
    with get_conn() as c:
        c.execute(q("DELETE FROM wp_connections WHERE brand_id=?"), (brand_id,))


if __name__ == "__main__":
    init_db()
    print(f"DB initialised ({'Postgres' if IS_PG else 'SQLite: ' + DB_PATH})")


# ---- งานที่ CEO แจกให้เอเจนต์ (orchestrator) ----
TASK_FIELDS = {"status", "started_at", "finished_at", "result"}


def add_task(agent: str, kind: str, title: str, reason: str, args: dict, created_by: str = "ceo") -> int:
    import json as _json
    with get_conn() as c:
        cur = c.execute(q("INSERT INTO agent_tasks(created_at,agent,kind,title,reason,args_json,status,created_by) VALUES(?,?,?,?,?,?,?,?) RETURNING id"),
                        (now(), agent, kind, title[:200], (reason or "")[:500], _json.dumps(args or {}, ensure_ascii=False), "queued", created_by))
        return cur.fetchone()["id"]


def update_task(tid: int, **fields) -> None:
    cols = [k for k in fields if k in TASK_FIELDS]
    if not cols:
        return
    with get_conn() as c:
        c.execute(q(f"UPDATE agent_tasks SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?"), tuple(fields[k] for k in cols) + (tid,))


def get_task(tid: int):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM agent_tasks WHERE id=?"), (tid,)).fetchone()


def list_tasks(since_ts: str = None, status: str = None, limit: int = 200):
    sql, args = "SELECT * FROM agent_tasks WHERE 1=1", []
    if since_ts:
        sql += " AND created_at >= ?"
        args.append(since_ts)
    if status:
        sql += " AND status=?"
        args.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with get_conn() as c:
        return c.execute(q(sql), tuple(args)).fetchall()


# ---------- ฝ่ายบัญชี: ใบแจ้งหนี้ · ใบเสร็จรับเงิน/ใบกำกับภาษี ----------
ACCT_FIELDS = {"status", "paid_at", "slip_path", "sent_at", "reminders", "void_reason", "decision_id", "wht", "invoice_id",
               "buyer_name", "buyer_tax_id", "buyer_branch", "buyer_address", "buyer_email"}


def add_acct_doc(**f) -> int:
    cols = ["created_at"] + [k for k in f]
    vals = [now()] + [f[k] for k in f]
    with get_conn() as c:
        cur = c.execute(q(f"INSERT INTO acct_docs({','.join(cols)}) VALUES({','.join('?' * len(cols))}) RETURNING id"), tuple(vals))
        return cur.fetchone()["id"]


def update_acct_doc(did: int, **fields) -> None:
    cols = [k for k in fields if k in ACCT_FIELDS]
    if not cols:
        return
    with get_conn() as c:
        c.execute(q(f"UPDATE acct_docs SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?"), tuple(fields[k] for k in cols) + (did,))


def get_acct_doc(did: int):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM acct_docs WHERE id=?"), (did,)).fetchone()


def get_acct_doc_by_token(token: str):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM acct_docs WHERE token=?"), (token,)).fetchone()


def find_acct_doc(kind: str, **where):
    sql, args = "SELECT * FROM acct_docs WHERE kind=?", [kind]
    for k, v in where.items():
        sql += f" AND {k}=?"
        args.append(v)
    with get_conn() as c:
        return c.execute(q(sql + " ORDER BY id DESC LIMIT 1"), tuple(args)).fetchone()


def list_acct_docs(kind: str = None, status: str = None, lead_id: int = None, tenant_id: int = None,
                   since: str = None, until: str = None, limit: int = 500):
    sql, args = "SELECT * FROM acct_docs WHERE 1=1", []
    for col, v in (("kind", kind), ("status", status), ("lead_id", lead_id), ("tenant_id", tenant_id)):
        if v is not None:
            sql += f" AND {col}=?"
            args.append(v)
    if since:
        sql += " AND created_at >= ?"
        args.append(since)
    if until:
        sql += " AND created_at < ?"
        args.append(until)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with get_conn() as c:
        return c.execute(q(sql), tuple(args)).fetchall()


def last_acct_number(kind: str, prefix: str):
    with get_conn() as c:
        r = c.execute(q("SELECT number FROM acct_docs WHERE kind=? AND number LIKE ? ORDER BY number DESC LIMIT 1"), (kind, prefix + "%")).fetchone()
        return r["number"] if r else None


def events_since(kinds, since_ts: str):
    kinds = list(kinds)
    with get_conn() as c:
        return c.execute(q(f"SELECT lead_id, kind, note, created_at FROM lead_events WHERE kind IN ({','.join('?' * len(kinds))}) AND created_at >= ? ORDER BY id"),
                         tuple(kinds) + (since_ts,)).fetchall()


def get_lead_by_email(email: str):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM leads WHERE LOWER(email)=? ORDER BY id DESC LIMIT 1"), ((email or "").strip().lower(),)).fetchone()


def leads_with_arm(prefix: str):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM leads WHERE exp_arm LIKE ? ORDER BY id"), (prefix + "%",)).fetchall()


# ---------- ฝ่ายพันธมิตร ----------
PARTNER_FIELDS = {"status", "kind", "name", "company", "email", "phone", "website", "domain", "clients", "code", "touches",
                  "last_contacted_at", "replied_at", "applied_at", "activated_at", "payout", "notes", "source"}


def add_partner(**f) -> int:
    import secrets as _s
    f.setdefault("token", _s.token_urlsafe(12))
    cols = ["created_at"] + list(f)
    with get_conn() as c:
        cur = c.execute(q(f"INSERT INTO partners({','.join(cols)}) VALUES({','.join('?' * len(cols))}) RETURNING id"), tuple([now()] + list(f.values())))
        return cur.fetchone()["id"]


def update_partner(pid: int, **fields) -> None:
    cols = [k for k in fields if k in PARTNER_FIELDS]
    if cols:
        with get_conn() as c:
            c.execute(q(f"UPDATE partners SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?"), tuple(fields[k] for k in cols) + (pid,))


def get_partner(pid: int):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM partners WHERE id=?"), (pid,)).fetchone()


def find_partner(**where):
    k, v = next(iter(where.items()))
    with get_conn() as c:
        if k in ("email", "domain", "code"):
            return c.execute(q(f"SELECT * FROM partners WHERE LOWER({k})=? ORDER BY id LIMIT 1"), ((v or "").lower(),)).fetchone()
        return c.execute(q(f"SELECT * FROM partners WHERE {k}=? ORDER BY id LIMIT 1"), (v,)).fetchone()


def list_partners(status: str = None, limit: int = 1000):
    with get_conn() as c:
        if status:
            return c.execute(q("SELECT * FROM partners WHERE status=? ORDER BY id DESC LIMIT ?"), (status, limit)).fetchall()
        return c.execute(q("SELECT * FROM partners ORDER BY id DESC LIMIT ?"), (limit,)).fetchall()


def add_commission(**f) -> int:
    cols = ["created_at"] + list(f)
    with get_conn() as c:
        cur = c.execute(q(f"INSERT INTO partner_commissions({','.join(cols)}) VALUES({','.join('?' * len(cols))}) RETURNING id"), tuple([now()] + list(f.values())))
        return cur.fetchone()["id"]


def update_commission(cid: int, **fields) -> None:
    cols = [k for k in fields if k in ("status", "paid_at", "note", "eligible_at")]
    if cols:
        with get_conn() as c:
            c.execute(q(f"UPDATE partner_commissions SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?"), tuple(fields[k] for k in cols) + (cid,))


def list_commissions(partner_id: int = None, status: str = None, limit: int = 2000):
    sql, args = "SELECT * FROM partner_commissions WHERE 1=1", []
    if partner_id is not None:
        sql += " AND partner_id=?"
        args.append(partner_id)
    if status:
        sql += " AND status=?"
        args.append(status)
    with get_conn() as c:
        return c.execute(q(sql + " ORDER BY id DESC LIMIT ?"), tuple(args) + (limit,)).fetchall()


def commission_for_receipt(receipt_id: int):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM partner_commissions WHERE receipt_id=?"), (receipt_id,)).fetchone()


def leads_of_partner(pid: int):
    with get_conn() as c:
        return c.execute(q("SELECT * FROM leads WHERE partner_id=? ORDER BY id DESC"), (pid,)).fetchall()
