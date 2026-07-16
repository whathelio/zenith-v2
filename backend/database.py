"""Zenith v2 SQLite 数据库 — WAL 模式 + 外键约束"""
from __future__ import annotations

import sqlite3
import uuid
import json
from datetime import datetime, timedelta
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "data" / "zenith.db"


def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


@contextmanager
def db():
    c = _conn()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def _migrate_memory_types():
    """迁移 memories 表 CHECK 约束 — 新增 experience 类型。
    SQLite 不支持 ALTER CHECK，通过重建表实现。
    直接连接数据库，不走 _conn() 避免递归。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    try:
        # 如果表尚不存在，无需迁移
        table_check = c.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='memories'"
        ).fetchone()
        if not table_check:
            return

        old_sql = table_check[0]
        if "'experience'" in old_sql:
            return  # 已迁移

        # executescript 会自动提交每条语句，不需要显式 BEGIN/COMMIT
        c.execute("PRAGMA foreign_keys=OFF")
        c.executescript("""
CREATE TABLE IF NOT EXISTS memories_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT CHECK(type IN ('personal_info','preference','event','decision','fact','experience')),
    content TEXT,
    importance INTEGER DEFAULT 3,
    keywords TEXT,
    source_conv_id TEXT,
    created_at TEXT
);
INSERT INTO memories_new SELECT * FROM memories;
DROP TABLE memories;
ALTER TABLE memories_new RENAME TO memories;
CREATE INDEX IF NOT EXISTS idx_mem_type ON memories(type);
""")
        c.execute("PRAGMA foreign_keys=ON")
    finally:
        c.close()


def _migrate_schedules():
    """迁移 schedules 表 — 新增 importance/category/impact/country/remind_before 字段。
    使用 ALTER TABLE ADD COLUMN（SQLite 支持逐字段添加）。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    try:
        info = c.execute("PRAGMA table_info(schedules)").fetchall()
        if not info:
            return  # 表不存在
        cols = {row[1] for row in info}
        new_cols = [
            ("importance", "INTEGER DEFAULT 3"),
            ("category", "TEXT DEFAULT 'other'"),
            ("impact", "TEXT DEFAULT ''"),
            ("country", "TEXT DEFAULT ''"),
            ("remind_before", "INTEGER DEFAULT 0"),
        ]
        for col_name, col_def in new_cols:
            if col_name not in cols:
                c.execute(f"ALTER TABLE schedules ADD COLUMN {col_name} {col_def}")
        c.commit()
    finally:
        c.close()


def _migrate_conversations():
    """迁移 conversations 表 — 新增 summary 列用于存储对话摘要。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    try:
        info = c.execute("PRAGMA table_info(conversations)").fetchall()
        if not info:
            return
        cols = {row[1] for row in info}
        if "summary" not in cols:
            c.execute("ALTER TABLE conversations ADD COLUMN summary TEXT DEFAULT ''")
            c.commit()
    finally:
        c.close()


def init_db():
    # 先迁移旧数据库的 CHECK 约束（新增 experience 类型）
    _migrate_memory_types()
    _migrate_schedules()
    _migrate_conversations()
    with db() as c:
        c.executescript("""
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT DEFAULT 'New Chat',
    summary TEXT DEFAULT '',
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT CHECK(role IN ('user','assistant','system')),
    content TEXT,
    created_at TEXT,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT CHECK(type IN ('personal_info','preference','event','decision','fact','experience')),
    content TEXT,
    importance INTEGER DEFAULT 3,
    keywords TEXT,
    source_conv_id TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    description TEXT DEFAULT '',
    start_time TEXT DEFAULT '',
    end_time TEXT DEFAULT '',
    location TEXT DEFAULT '',
    status TEXT DEFAULT 'confirmed' CHECK(status IN ('proposed','confirmed','done','cancelled')),
    priority TEXT DEFAULT 'normal' CHECK(priority IN ('low','normal','high')),
    importance INTEGER DEFAULT 3,
    category TEXT DEFAULT 'other' CHECK(category IN ('economic','market','reminder','personal','other')),
    impact TEXT DEFAULT '' CHECK(impact IN ('','bullish','bearish','neutral')),
    country TEXT DEFAULT '',
    remind_before INTEGER DEFAULT 0,
    source TEXT DEFAULT 'manual',
    confirmed_at TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    content TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    source TEXT DEFAULT 'manual',
    status TEXT DEFAULT 'confirmed' CHECK(status IN ('proposed','confirmed','cancelled')),
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    start_value REAL DEFAULT 0,
    target_value REAL DEFAULT 0,
    current_value REAL DEFAULT 0,
    daily_target REAL DEFAULT 5,
    strategy TEXT DEFAULT 'compound' CHECK(strategy IN ('compound','linear')),
    status TEXT DEFAULT 'active' CHECK(status IN ('active','completed','cancelled')),
    start_date TEXT,
    end_date TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);

CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_sch_start ON schedules(start_time);
CREATE INDEX IF NOT EXISTS idx_sch_status ON schedules(status);
CREATE INDEX IF NOT EXISTS idx_notes_status ON notes(status);
CREATE INDEX IF NOT EXISTS idx_notes_upd ON notes(updated_at);
CREATE INDEX IF NOT EXISTS idx_mem_type ON memories(type);

CREATE TABLE IF NOT EXISTS analysis_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    original_content TEXT DEFAULT '',
    analysis_text TEXT DEFAULT '',
    schedule_ids TEXT DEFAULT '[]',
    export_text TEXT DEFAULT '',
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_analysis_created ON analysis_documents(created_at);

-- CFTC 原始数据缓存
CREATE TABLE IF NOT EXISTS cftc_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL,
    contract_name TEXT NOT NULL,
    category TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_cftc_date ON cftc_cache(report_date);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cftc_uniq ON cftc_cache(report_date, contract_name, category);

-- 宏观指标每日快照
CREATE TABLE IF NOT EXISTS macro_indicators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    indicator TEXT NOT NULL,
    value TEXT NOT NULL,
    change_pct TEXT DEFAULT '',
    source TEXT DEFAULT '',
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_macro_indicator ON macro_indicators(indicator);

-- 每日市场分析报告
CREATE TABLE IF NOT EXISTS market_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL,
    gold_price TEXT DEFAULT '',
    factor_data TEXT DEFAULT '',
    events_overdue TEXT DEFAULT '',
    events_upcoming TEXT DEFAULT '',
    analysis_text TEXT NOT NULL,
    daily_advice TEXT DEFAULT '',
    weekly_advice TEXT DEFAULT '',
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_market_date ON market_reports(report_date);

CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    trigger_scene TEXT NOT NULL,
    steps TEXT DEFAULT '[]',
    tags TEXT DEFAULT '[]',
    usage_count INTEGER DEFAULT 0,
    confirmed_by_user INTEGER DEFAULT 0,
    source_conv_id TEXT DEFAULT '',
    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_skills_name ON skills(name);
CREATE INDEX IF NOT EXISTS idx_skills_confirmed ON skills(confirmed_by_user);

-- 预测追踪表
CREATE TABLE IF NOT EXISTS market_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL,
    event_name TEXT NOT NULL,
    predicted_direction TEXT NOT NULL,
    predicted_strength REAL DEFAULT 0,
    predicted_range TEXT DEFAULT '',
    actual_direction TEXT DEFAULT '',
    actual_change_pct TEXT DEFAULT '',
    actual_close TEXT DEFAULT '',
    verified TEXT DEFAULT 'pending',
    verified_at TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_pred_date ON market_predictions(report_date);
CREATE INDEX IF NOT EXISTS idx_pred_verified ON market_predictions(verified);
""")


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

def conv_create(title: str = "New Chat") -> dict:
    cid = uuid.uuid4().hex[:8]
    now = _now()
    with db() as c:
        c.execute("INSERT INTO conversations (id, title, summary, created_at, updated_at) VALUES (?,?,?,?,?)", (cid, title, "", now, now))
    return {"id": cid, "title": title, "summary": "", "created_at": now, "updated_at": now}


def conv_list() -> list:
    with db() as c:
        rs = c.execute("""
            SELECT c.*, COUNT(m.id) AS msg_count
            FROM conversations c
            LEFT JOIN messages m ON c.id = m.conversation_id
            GROUP BY c.id
            ORDER BY c.updated_at DESC
        """).fetchall()
    return [dict(r) for r in rs]


def conv_list_by_date(date_from: str = "", date_to: str = "") -> list:
    """按日期范围筛选对话（基于 updated_at）"""
    q = """
        SELECT c.*, COUNT(m.id) AS msg_count
        FROM conversations c
        LEFT JOIN messages m ON c.id = m.conversation_id
        WHERE 1=1
    """
    ps = []
    if date_from:
        q += " AND c.updated_at >= ?"
        ps.append(date_from)
    if date_to:
        q += " AND c.updated_at <= ?"
        ps.append(date_to)
    q += " GROUP BY c.id ORDER BY c.updated_at DESC"
    with db() as c:
        rs = c.execute(q, ps).fetchall()
    return [dict(r) for r in rs]


def conv_get(cid: str) -> Optional[dict]:
    with db() as c:
        r = c.execute("SELECT * FROM conversations WHERE id = ?", (cid,)).fetchone()
    return dict(r) if r else None


def conv_del(cid: str):
    with db() as c:
        c.execute("DELETE FROM conversations WHERE id = ?", (cid,))


def conv_update_title(cid: str, title: str):
    now = _now()
    with db() as c:
        c.execute("UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?", (title, now, cid))


def conv_update_summary(cid: str, summary: str):
    """存储对话摘要"""
    now = _now()
    with db() as c:
        c.execute("UPDATE conversations SET summary = ?, updated_at = ? WHERE id = ?", (summary, now, cid))


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def msg_add(cid: str, role: str, content: str) -> int:
    now = _now()
    with db() as c:
        cur = c.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?,?,?,?)",
            (cid, role, content, now)
        )
        c.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, cid))
        return cur.lastrowid


def msg_list(cid: str) -> list:
    with db() as c:
        rs = c.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id", (cid,)
        ).fetchall()
    return [dict(r) for r in rs]


def msg_recent(cid: str, n: int = 10) -> list:
    with db() as c:
        rs = c.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?", (cid, n)
        ).fetchall()
    return [dict(r) for r in reversed(rs)]


def msg_count(cid: str) -> int:
    with db() as c:
        r = c.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE conversation_id = ? AND role != 'system'", (cid,)
        ).fetchone()
    return r["cnt"] if r else 0


# ---------------------------------------------------------------------------
# Memories
# ---------------------------------------------------------------------------

def mem_list_by_date(date_from: str = "", date_to: str = "") -> list:
    """按创建日期范围筛选记忆"""
    q = "SELECT * FROM memories WHERE 1=1"
    ps = []
    if date_from:
        q += " AND created_at >= ?"
        ps.append(date_from)
    if date_to:
        q += " AND created_at <= ?"
        ps.append(date_to)
    q += " ORDER BY importance DESC, created_at DESC"
    with db() as c:
        rs = c.execute(q, ps).fetchall()
    return [dict(r) for r in rs]


def mem_add(type_: str, content: str, importance: int = 3,
            keywords: str = "", source_conv_id: str = "") -> int:
    now = _now()
    with db() as c:
        cur = c.execute(
            "INSERT INTO memories (type, content, importance, keywords, source_conv_id, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (type_, content, importance, keywords, source_conv_id, now)
        )
        return cur.lastrowid


def mem_list(type_: str = "") -> list:
    with db() as c:
        q = "SELECT * FROM memories"
        ps = []
        if type_:
            q += " WHERE type = ?"
            ps.append(type_)
        q += " ORDER BY importance DESC, created_at DESC"
        rs = c.execute(q, ps).fetchall()
    return [dict(r) for r in rs]


def mem_search(keyword: str = "") -> list:
    with db() as c:
        rs = c.execute(
            "SELECT * FROM memories WHERE content LIKE ? OR keywords LIKE ? "
            "ORDER BY importance DESC",
            (f"%{keyword}%", f"%{keyword}%")
        ).fetchall()
    return [dict(r) for r in rs]


def mem_del(mid: int):
    with db() as c:
        c.execute("DELETE FROM memories WHERE id = ?", (mid,))


def mem_for_inject(limit: int = 20) -> list:
    """获取需要注入到对话上下文的重要记忆"""
    with db() as c:
        rs = c.execute(
            "SELECT * FROM memories ORDER BY importance DESC, created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rs]


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------

def sch_add(data: dict) -> int:
    now = _now()
    status = data.get("status", "confirmed")
    with db() as c:
        cur = c.execute(
            """INSERT INTO schedules
               (title, description, start_time, end_time, location, status, priority,
                importance, category, impact, country, remind_before, source, confirmed_at, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data["title"],
                data.get("description", ""),
                data.get("start_time", ""),
                data.get("end_time", ""),
                data.get("location", ""),
                status,
                data.get("priority", "normal"),
                data.get("importance", 3),
                data.get("category", "other"),
                data.get("impact", ""),
                data.get("country", ""),
                data.get("remind_before", 0),
                data.get("source", "manual"),
                now if status == "confirmed" else None,
                now,
            )
        )
        return cur.lastrowid


def sch_list(status: str = "", date_from: str = "", date_to: str = "") -> list:
    q = "SELECT * FROM schedules WHERE 1=1"
    ps = []
    if status:
        q += " AND status = ?"
        ps.append(status)
    if date_from:
        q += " AND start_time >= ?"
        ps.append(date_from)
    if date_to:
        q += " AND start_time <= ?"
        ps.append(date_to)
    q += " ORDER BY start_time ASC"
    with db() as c:
        rs = c.execute(q, ps).fetchall()
    return [dict(r) for r in rs]


def sch_get(sid: int) -> Optional[dict]:
    with db() as c:
        r = c.execute("SELECT * FROM schedules WHERE id = ?", (sid,)).fetchone()
    return dict(r) if r else None


_SCHEDULE_COLUMNS = {"title", "description", "start_time", "end_time", "location", "status", "priority", "importance", "category", "impact", "country", "remind_before", "source", "confirmed_at"}


def sch_update(sid: int, data: dict):
    fs = []
    ps = []
    for k, v in data.items():
        if k not in _SCHEDULE_COLUMNS:
            continue
        if v is not None:
            fs.append(f"{k} = ?")
            ps.append(v)
    if not fs:
        return
    ps.append(sid)
    with db() as c:
        c.execute(f"UPDATE schedules SET {', '.join(fs)} WHERE id = ?", ps)


def sch_del(sid: int):
    with db() as c:
        c.execute("DELETE FROM schedules WHERE id = ?", (sid,))


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def note_add(data: dict) -> int:
    now = _now()
    with db() as c:
        cur = c.execute(
            "INSERT INTO notes (title, content, tags, source, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                data["title"],
                data.get("content", ""),
                data.get("tags", ""),
                data.get("source", "manual"),
                data.get("status", "confirmed"),
                now,
                now,
            )
        )
        return cur.lastrowid


def note_list(search: str = "") -> list:
    q = "SELECT * FROM notes WHERE status != 'cancelled'"
    ps = []
    if search:
        q += " AND (title LIKE ? OR content LIKE ?)"
        ps.extend([f"%{search}%", f"%{search}%"])
    q += " ORDER BY updated_at DESC"
    with db() as c:
        rs = c.execute(q, ps).fetchall()
    return [dict(r) for r in rs]


def note_list_by_date(date_from: str = "", date_to: str = "") -> list:
    """按创建日期范围筛选笔记"""
    q = "SELECT * FROM notes WHERE status != 'cancelled'"
    ps = []
    if date_from:
        q += " AND created_at >= ?"
        ps.append(date_from)
    if date_to:
        q += " AND created_at <= ?"
        ps.append(date_to)
    q += " ORDER BY updated_at DESC"
    with db() as c:
        rs = c.execute(q, ps).fetchall()
    return [dict(r) for r in rs]


def note_get(nid: int) -> Optional[dict]:
    with db() as c:
        r = c.execute("SELECT * FROM notes WHERE id = ?", (nid,)).fetchone()
    return dict(r) if r else None


_NOTE_COLUMNS = {"title", "content", "tags", "source", "status"}


def note_update(nid: int, data: dict):
    fs = []
    ps = []
    for k, v in data.items():
        if k not in _NOTE_COLUMNS:
            continue
        if v is not None:
            fs.append(f"{k} = ?")
            ps.append(v)
    fs.append("updated_at = ?")
    ps.append(_now())
    ps.append(nid)
    with db() as c:
        c.execute(f"UPDATE notes SET {', '.join(fs)} WHERE id = ?", ps)


def note_del(nid: int):
    with db() as c:
        c.execute("DELETE FROM notes WHERE id = ?", (nid,))


# ---------------------------------------------------------------------------
# Goals (目标追踪)
# ---------------------------------------------------------------------------

def goal_add(data: dict) -> int:
    now = _now()
    # 计算预计完成日期（按日化复利）
    daily = data.get("daily_target", 5)
    sv = float(data.get("start_value", 0))
    tv = float(data.get("target_value", 1))
    import math
    days = 30
    if sv > 0 and tv > sv and daily > 0:
        days = math.ceil(math.log(tv / sv) / math.log(1 + daily / 100))
    start_date = data.get("start_date", now[:10])
    from datetime import timedelta as _td
    from datetime import datetime as _dt
    try:
        end_date = (_dt.strptime(start_date, "%Y-%m-%d") + _td(days=days)).strftime("%Y-%m-%d")
    except ValueError:
        end_date = (_dt.now() + _td(days=days)).strftime("%Y-%m-%d")

    with db() as c:
        cur = c.execute(
            """INSERT INTO goals
               (title, start_value, target_value, current_value, daily_target, strategy,
                status, start_date, end_date, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data["title"],
                sv, tv, sv,
                daily,
                data.get("strategy", "compound"),
                "active",
                start_date, end_date,
                now, now,
            )
        )
        return cur.lastrowid


def goal_list(status: str = "") -> list:
    q = "SELECT * FROM goals WHERE 1=1"
    ps = []
    if status:
        q += " AND status = ?"
        ps.append(status)
    q += " ORDER BY created_at DESC"
    with db() as c:
        rs = c.execute(q, ps).fetchall()
    return [dict(r) for r in rs]


def goal_get(gid: int) -> Optional[dict]:
    with db() as c:
        r = c.execute("SELECT * FROM goals WHERE id = ?", (gid,)).fetchone()
    return dict(r) if r else None


_GOAL_COLUMNS = {"title", "start_value", "target_value", "current_value", "daily_target", "strategy", "status", "start_date", "end_date"}


def goal_update(gid: int, data: dict):
    import math
    fs = []
    ps = []
    for k, v in data.items():
        if k not in _GOAL_COLUMNS:
            continue
        if v is not None:
            fs.append(f"{k} = ?")
            ps.append(v)
    # 如果变更了 start_value/target_value/daily_target，重新计算 end_date
    if "start_value" in data or "target_value" in data or "daily_target" in data:
        g = goal_get(gid)
        if g:
            daily = float(data.get("daily_target", g.get("daily_target", 5)))
            sv = float(data.get("start_value", g.get("start_value", 0)))
            tv = float(data.get("target_value", g.get("target_value", 1)))
            if sv > 0 and tv > sv and daily > 0:
                days = math.ceil(math.log(tv / sv) / math.log(1 + daily / 100))
                st = data.get("start_date", g.get("start_date", _now()[:10]))
                from datetime import timedelta as _td
                from datetime import datetime as _dt
                try:
                    ed = (_dt.strptime(st, "%Y-%m-%d") + _td(days=days)).strftime("%Y-%m-%d")
                    fs.append("end_date = ?")
                    ps.append(ed)
                except (ValueError, TypeError):
                    pass
    if not fs:
        return
    fs.append("updated_at = ?")
    ps.append(_now())
    ps.append(gid)
    with db() as c:
        c.execute(f"UPDATE goals SET {', '.join(fs)} WHERE id = ?", ps)


def goal_del(gid: int):
    with db() as c:
        c.execute("DELETE FROM goals WHERE id = ?", (gid,))


def goal_get_stats(gid: int) -> Optional[dict]:
    """计算目标统计数据"""
    g = goal_get(gid)
    if not g:
        return None
    sv = float(g.get("start_value", 0))
    tv = float(g.get("target_value", 0))
    cv = float(g.get("current_value", 0))
    rng = tv - sv
    progress = round((cv - sv) / rng * 100, 1) if rng > 0 else 0
    from datetime import datetime as _dt
    days_passed = max((_dt.now() - _dt.fromisoformat(g.get("start_date", _now()))).days, 1)
    daily_return = 0.0
    if sv > 0:
        daily_return = round((pow(cv / sv, 1 / days_passed) - 1) * 100, 2) if days_passed > 0 else 0
    return {
        "progress": min(progress, 100),
        "days_total": (_dt.fromisoformat(g.get("end_date", _now())) - _dt.fromisoformat(g.get("start_date", _now()))).days,
        "days_passed": days_passed,
        "daily_return": daily_return,
        "remaining": max(tv - cv, 0),
        "on_track": daily_return >= float(g.get("daily_target", 5)),
    }


# ---------------------------------------------------------------------------
# Analysis Documents
# ---------------------------------------------------------------------------

def analysis_add(data: dict) -> int:
    now = _now()
    with db() as c:
        cur = c.execute(
            "INSERT INTO analysis_documents "
            "(filename, original_content, analysis_text, schedule_ids, export_text, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                data["filename"],
                data.get("original_content", ""),
                data.get("analysis_text", ""),
                data.get("schedule_ids", "[]"),
                data.get("export_text", ""),
                now,
            )
        )
        return cur.lastrowid


def analysis_list() -> list:
    with db() as c:
        rs = c.execute(
            "SELECT id, filename, created_at, "
            "length(analysis_text) as analysis_len, "
            "schedule_ids FROM analysis_documents ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rs]


def analysis_get(doc_id: int) -> Optional[dict]:
    with db() as c:
        r = c.execute(
            "SELECT * FROM analysis_documents WHERE id = ?", (doc_id,)
        ).fetchone()
    return dict(r) if r else None


_ANALYSIS_COLUMNS = {"filename", "original_content", "analysis_text", "schedule_ids", "export_text"}


def analysis_update(doc_id: int, data: dict):
    fs = []
    ps = []
    for k, v in data.items():
        if k not in _ANALYSIS_COLUMNS:
            continue
        if v is not None:
            fs.append(f"{k} = ?")
            ps.append(v)
    if not fs:
        return
    ps.append(doc_id)
    with db() as c:
        c.execute(f"UPDATE analysis_documents SET {', '.join(fs)} WHERE id = ?", ps)


def analysis_del(doc_id: int):
    with db() as c:
        c.execute("DELETE FROM analysis_documents WHERE id = ?", (doc_id,))


# ---------------------------------------------------------------------------
# CFTC Cache
# ---------------------------------------------------------------------------

def cftc_cache_add(report_date: str, contract_name: str, category: str, raw_json: str) -> int:
    now = _now()
    with db() as c:
        cur = c.execute(
            "INSERT OR REPLACE INTO cftc_cache (report_date, contract_name, category, raw_json, created_at) "
            "VALUES (?,?,?,?,?)",
            (report_date, contract_name, category, raw_json, now)
        )
        return cur.lastrowid


def cftc_cache_get_latest(category: str = "") -> list:
    q = "SELECT * FROM cftc_cache WHERE 1=1"
    ps = []
    if category:
        q += " AND category = ?"
        ps.append(category)
    q += " ORDER BY report_date DESC"
    with db() as c:
        rs = c.execute(q, ps).fetchall()
    return [dict(r) for r in rs]


def cftc_cache_check_exists(report_date: str, contract_name: str, category: str) -> bool:
    with db() as c:
        r = c.execute(
            "SELECT id FROM cftc_cache WHERE report_date=? AND contract_name=? AND category=?",
            (report_date, contract_name, category)
        ).fetchone()
    return r is not None


def cftc_cache_clear():
    with db() as c:
        c.execute("DELETE FROM cftc_cache")


# ---------------------------------------------------------------------------
# Macro Indicators
# ---------------------------------------------------------------------------

def macro_indicator_add(indicator: str, value: str, change_pct: str = "", source: str = "") -> int:
    now = _now()
    with db() as c:
        cur = c.execute(
            "INSERT INTO macro_indicators (indicator, value, change_pct, source, created_at) "
            "VALUES (?,?,?,?,?)",
            (indicator, value, change_pct, source, now)
        )
        return cur.lastrowid


def macro_indicator_list_latest(limit: int = 50) -> list:
    """获取最近一批指标快照（按 created_at 降序）"""
    with db() as c:
        rs = c.execute(
            "SELECT * FROM macro_indicators ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rs]


def macro_indicator_get_by_name(name: str) -> Optional[dict]:
    """获取指定指标的最新值"""
    with db() as c:
        r = c.execute(
            "SELECT * FROM macro_indicators WHERE indicator = ? ORDER BY created_at DESC LIMIT 1",
            (name,)
        ).fetchone()
    return dict(r) if r else None


# ---------------------------------------------------------------------------
# Market Reports
# ---------------------------------------------------------------------------

def market_report_add(data: dict) -> int:
    now = _now()
    with db() as c:
        cur = c.execute(
            """INSERT INTO market_reports
               (report_date, gold_price, factor_data, events_overdue, events_upcoming,
                analysis_text, daily_advice, weekly_advice, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                data["report_date"],
                data.get("gold_price", ""),
                data.get("factor_data", ""),
                data.get("events_overdue", ""),
                data.get("events_upcoming", ""),
                data["analysis_text"],
                data.get("daily_advice", ""),
                data.get("weekly_advice", ""),
                now,
            )
        )
        return cur.lastrowid


def market_report_list(limit: int = 30) -> list:
    with db() as c:
        rs = c.execute(
            "SELECT id, report_date, gold_price, created_at FROM market_reports "
            "ORDER BY report_date DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rs]


def market_report_get_latest() -> Optional[dict]:
    with db() as c:
        r = c.execute(
            "SELECT * FROM market_reports ORDER BY report_date DESC LIMIT 1"
        ).fetchone()
    return dict(r) if r else None


def market_report_get(report_id: int) -> Optional[dict]:
    with db() as c:
        r = c.execute("SELECT * FROM market_reports WHERE id = ?", (report_id,)).fetchone()
    return dict(r) if r else None


def market_report_get_by_date(date: str) -> Optional[dict]:
    with db() as c:
        r = c.execute("SELECT * FROM market_reports WHERE report_date = ?", (date,)).fetchone()
    return dict(r) if r else None


# ---------------------------------------------------------------------------
# Market Predictions
# ---------------------------------------------------------------------------

def prediction_add(data: dict) -> int:
    now = _now()
    with db() as c:
        cur = c.execute(
            """INSERT INTO market_predictions
               (report_date, event_name, predicted_direction, predicted_strength,
                predicted_range, actual_direction, actual_change_pct, actual_close,
                verified, verified_at, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data["report_date"],
                data["event_name"],
                data["predicted_direction"],
                data.get("predicted_strength", 0),
                data.get("predicted_range", ""),
                data.get("actual_direction", ""),
                data.get("actual_change_pct", ""),
                data.get("actual_close", ""),
                data.get("verified", "pending"),
                data.get("verified_at"),
                now,
            )
        )
        return cur.lastrowid


def prediction_batch_add(items: list[dict]) -> list[int]:
    """批量添加预测记录"""
    ids = []
    for item in items:
        ids.append(prediction_add(item))
    return ids


def prediction_list(date: str = "", verified: str = "") -> list:
    q = "SELECT * FROM market_predictions WHERE 1=1"
    ps = []
    if date:
        q += " AND report_date = ?"
        ps.append(date)
    if verified:
        q += " AND verified = ?"
        ps.append(verified)
    q += " ORDER BY report_date DESC, id ASC"
    with db() as c:
        rs = c.execute(q, ps).fetchall()
    return [dict(r) for r in rs]


def prediction_get_pending(date: str = "") -> list:
    """获取待验证的预测"""
    q = "SELECT * FROM market_predictions WHERE verified = 'pending'"
    ps: list = []
    if date:
        q += " AND report_date = ?"
        ps.append(date)
    q += " ORDER BY report_date ASC"
    with db() as c:
        rs = c.execute(q, ps).fetchall()
    return [dict(r) for r in rs]


def prediction_verify(pred_id: int, actual_direction: str, actual_change_pct: str = "",
                      actual_close: str = "") -> None:
    now = _now()
    with db() as c:
        c.execute(
            "UPDATE market_predictions SET actual_direction=?, actual_change_pct=?, "
            "actual_close=?, verified='verified', verified_at=? WHERE id=?",
            (actual_direction, actual_change_pct, actual_close, now, pred_id)
        )


def prediction_get_hit_rate(days: int = 30) -> dict:
    """计算最近N天的预测命中率"""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    with db() as c:
        total = c.execute(
            "SELECT COUNT(*) as cnt FROM market_predictions WHERE verified='verified' AND created_at >= ?",
            (cutoff,)
        ).fetchone()["cnt"]
        hit = c.execute(
            "SELECT COUNT(*) as cnt FROM market_predictions "
            "WHERE verified='verified' AND predicted_direction=actual_direction AND created_at >= ?",
            (cutoff,)
        ).fetchone()["cnt"]
    return {"total": total, "hit": hit, "miss": total - hit,
            "hit_rate": round(hit / total * 100, 1) if total > 0 else 0}


# ---------------------------------------------------------------------------
# Skills (技能卡片)
# ---------------------------------------------------------------------------

def skill_add(data: dict) -> int:
    now = _now()
    steps = data.get("steps", [])
    tags = data.get("tags", [])
    with db() as c:
        cur = c.execute(
            "INSERT INTO skills (name, trigger_scene, steps, tags, usage_count, "
            "confirmed_by_user, source_conv_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                data["name"],
                data.get("trigger_scene", ""),
                json.dumps(steps) if isinstance(steps, list) else steps,
                json.dumps(tags) if isinstance(tags, list) else tags,
                0,
                0,
                data.get("source_conv_id", ""),
                now,
            )
        )
        return cur.lastrowid


def skill_list(search: str = "", confirmed: int = -1) -> list:
    """列出技能卡片。confirmed=-1表示全部，0=未确认，1=已确认"""
    import json as _json
    q = "SELECT * FROM skills WHERE 1=1"
    ps = []
    if search:
        q += " AND (name LIKE ? OR trigger_scene LIKE ? OR tags LIKE ?)"
        ps.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
    if confirmed >= 0:
        q += " AND confirmed_by_user = ?"
        ps.append(confirmed)
    q += " ORDER BY usage_count DESC, created_at DESC"
    with db() as c:
        rs = c.execute(q, ps).fetchall()
    results = []
    for r in rs:
        d = dict(r)
        # 解析 JSON 字段
        try:
            d["steps"] = _json.loads(d.get("steps", "[]"))
        except (ValueError, TypeError):
            d["steps"] = []
        try:
            d["tags"] = _json.loads(d.get("tags", "[]"))
        except (ValueError, TypeError):
            d["tags"] = []
        results.append(d)
    return results


def skill_get(sid: int) -> Optional[dict]:
    import json as _json
    with db() as c:
        r = c.execute("SELECT * FROM skills WHERE id = ?", (sid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    try:
        d["steps"] = _json.loads(d.get("steps", "[]"))
    except (ValueError, TypeError):
        d["steps"] = []
    try:
        d["tags"] = _json.loads(d.get("tags", "[]"))
    except (ValueError, TypeError):
        d["tags"] = []
    return d


_SKILL_COLUMNS = {"name", "trigger_scene", "steps", "tags", "confirmed_by_user"}


def skill_update(sid: int, data: dict):
    import json as _json
    fs = []
    ps = []
    for k, v in data.items():
        if k not in _SKILL_COLUMNS:
            continue
        if v is not None:
            # steps 和 tags 需要转 JSON 存储
            if k == "steps" and isinstance(v, list):
                v = _json.dumps(v)
            elif k == "tags" and isinstance(v, list):
                v = _json.dumps(v)
            fs.append(f"{k} = ?")
            ps.append(v)
    if not fs:
        return
    ps.append(sid)
    with db() as c:
        c.execute(f"UPDATE skills SET {', '.join(fs)} WHERE id = ?", ps)


def skill_del(sid: int):
    with db() as c:
        c.execute("DELETE FROM skills WHERE id = ?", (sid,))


def skill_increment_usage(sid: int):
    """技能被调用时递增 usage_count"""
    with db() as c:
        c.execute("UPDATE skills SET usage_count = usage_count + 1 WHERE id = ?", (sid,))


def skill_find_by_scene(scene: str) -> list:
    """根据触发场景查找匹配的技能"""
    import json as _json
    with db() as c:
        rs = c.execute(
            "SELECT * FROM skills WHERE trigger_scene LIKE ? AND confirmed_by_user = 1 "
            "ORDER BY usage_count DESC",
            (f"%{scene}%",)
        ).fetchall()
    results = []
    for r in rs:
        d = dict(r)
        try:
            d["steps"] = _json.loads(d.get("steps", "[]"))
        except (ValueError, TypeError):
            d["steps"] = []
        try:
            d["tags"] = _json.loads(d.get("tags", "[]"))
        except (ValueError, TypeError):
            d["tags"] = []
        results.append(d)
    return results


# ---------------------------------------------------------------------------
# Settings (key-value store)
# ---------------------------------------------------------------------------

def settings_get(key: str) -> Optional[str]:
    with db() as c:
        r = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return r["value"] if r else None


def settings_put(key: str, value: str):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))


# ---------------------------------------------------------------------------
# Utils
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now().isoformat()
