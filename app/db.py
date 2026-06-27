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
        c.execute(q("UPDATE tenants SET plan=? WHERE id=?"), (plan, tenant_id))


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
def add_question(brand_id: int, question: str, lang: str = "th") -> int:
    with get_conn() as c:
        cur = c.execute(
            q("INSERT INTO target_questions(brand_id,question,lang) VALUES(?,?,?) RETURNING id"),
            (brand_id, question.strip(), lang),
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
