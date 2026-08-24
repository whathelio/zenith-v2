"""Zenith v2 SQLite 数据库 — WAL 模式 + 外键约束"""
from __future__ import annotations

import os
import sqlite3
import uuid
import json
import logging
from datetime import datetime, timedelta
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "data" / "zenith.db"
_TESTING = os.environ.get("ZENITH_TESTING") == "1"
if _TESTING:
    # 测试隔离：重定向到临时文件，避免 init_db / 迁移 / 增删改污染生产库。
    # 所有迁移函数均通过 str(DB_PATH) 直连，故必须在此处（模块加载时）替换路径。
    import tempfile as _tempfile
    _test_fd, _test_tmp_path = _tempfile.mkstemp(suffix=".db", prefix="zenith_v2_test_")
    os.close(_test_fd)
    DB_PATH = Path(_test_tmp_path)
_test_conn = None


def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=5000")
    c.execute("PRAGMA synchronous=NORMAL")
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
        if "'experience'" in old_sql and "'skill'" in old_sql:
            return  # 已迁移

        # executescript 会自动提交每条语句，不需要显式 BEGIN/COMMIT
        c.execute("PRAGMA foreign_keys=OFF")
        c.executescript("""
CREATE TABLE IF NOT EXISTS memories_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT CHECK(type IN ('personal_info','preference','event','decision','fact','experience','skill')),
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
    """迁移 schedules 表 — 新增 importance/category/impact/country/remind_before/goal_id/recurrence/parent_id 字段。
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
            ("remind_before", "INTEGER DEFAULT 30"),
            ("goal_id", "INTEGER DEFAULT NULL"),
            ("recurrence", "TEXT DEFAULT ''"),
            ("parent_id", "INTEGER DEFAULT NULL"),
        ]
        for col_name, col_def in new_cols:
            if col_name not in cols:
                c.execute(f"ALTER TABLE schedules ADD COLUMN {col_name} {col_def}")
        c.commit()
    finally:
        c.close()


def _migrate_notes():
    """迁移 notes 表 — 新增 stage/recorded_at/distilled_at/distilled_into 字段。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    try:
        info = c.execute("PRAGMA table_info(notes)").fetchall()
        if not info:
            return
        cols = {row[1] for row in info}
        new_cols = [
            ("stage", "TEXT DEFAULT 'raw'"),
            ("recorded_at", "TEXT"),
            ("distilled_at", "TEXT"),
            ("distilled_into", "TEXT DEFAULT ''"),
        ]
        for col_name, col_def in new_cols:
            if col_name not in cols:
                c.execute(f"ALTER TABLE notes ADD COLUMN {col_name} {col_def}")
        # 旧数据初始化：stage 为 raw，recorded_at 用 created_at 回填
        if "stage" in cols:
            c.execute("UPDATE notes SET stage = 'raw' WHERE stage IS NULL OR stage = ''")
        c.commit()
    finally:
        c.close()


def _migrate_memories():
    """迁移 memories 表 — 新增 recorded_at/distilled_from 字段。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    try:
        info = c.execute("PRAGMA table_info(memories)").fetchall()
        if not info:
            return
        cols = {row[1] for row in info}
        new_cols = [
            ("recorded_at", "TEXT"),
            ("distilled_from", "INTEGER DEFAULT NULL"),
            ("user_edited", "INTEGER DEFAULT 0"),
            ("last_touched_at", "TEXT"),
        ]
        for col_name, col_def in new_cols:
            if col_name not in cols:
                c.execute(f"ALTER TABLE memories ADD COLUMN {col_name} {col_def}")
        # 旧数据回填：recorded_at 用 created_at
        if "recorded_at" in cols:
            c.execute("UPDATE memories SET recorded_at = created_at WHERE recorded_at IS NULL OR recorded_at = ''")
        c.commit()
    finally:
        c.close()


def _migrate_conversations():
    """迁移 conversations 表 — 新增 summary、persona_name、background、background_image、source_type、source_id 列。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    try:
        info = c.execute("PRAGMA table_info(conversations)").fetchall()
        if not info:
            return
        cols = {row[1] for row in info}
        if "summary" not in cols:
            c.execute("ALTER TABLE conversations ADD COLUMN summary TEXT DEFAULT ''")
        if "persona_name" not in cols:
            c.execute("ALTER TABLE conversations ADD COLUMN persona_name TEXT DEFAULT NULL")
        if "background" not in cols:
            c.execute("ALTER TABLE conversations ADD COLUMN background TEXT DEFAULT NULL")
        if "background_image" not in cols:
            c.execute("ALTER TABLE conversations ADD COLUMN background_image TEXT DEFAULT NULL")
        if "source_type" not in cols:
            c.execute("ALTER TABLE conversations ADD COLUMN source_type TEXT DEFAULT NULL")
        if "source_id" not in cols:
            c.execute("ALTER TABLE conversations ADD COLUMN source_id TEXT DEFAULT NULL")
        if "learning_progress" not in cols:
            c.execute("ALTER TABLE conversations ADD COLUMN learning_progress TEXT DEFAULT NULL")
        c.commit()
    finally:
        c.close()


def _migrate_market_reports():
    """迁移 market_reports 表 — 新增 markdown_text 列（纯文本分析报告）。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    try:
        info = c.execute("PRAGMA table_info(market_reports)").fetchall()
        if not info:
            return
        cols = {row[1] for row in info}
        if "markdown_text" not in cols:
            c.execute("ALTER TABLE market_reports ADD COLUMN markdown_text TEXT DEFAULT ''")
            c.commit()
    finally:
        c.close()


def _migrate_memories_fts():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    try:
        c.execute("PRAGMA foreign_keys=OFF")
        ft_exists = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memories_fts'"
        ).fetchone()
        if not ft_exists:
            c.executescript("""
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, keywords, content='memories', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, keywords) VALUES (new.id, new.content, new.keywords);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, keywords) VALUES ('delete', old.id, old.content, old.keywords);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, keywords) VALUES ('delete', old.id, old.content, old.keywords);
    INSERT INTO memories_fts(rowid, content, keywords) VALUES (new.id, new.content, new.keywords);
END;
""")
            c.execute("INSERT INTO memories_fts(rowid, content, keywords) SELECT id, content, keywords FROM memories")
            c.commit()
        c.execute("PRAGMA foreign_keys=ON")
    finally:
        c.close()


def _migrate_goals():
    """迁移 goals 表 — 新增 active_days 字段（存储 JSON 数组）。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    try:
        info = c.execute("PRAGMA table_info(goals)").fetchall()
        if not info:
            return
        cols = {row[1] for row in info}
        if "active_days" not in cols:
            c.execute("ALTER TABLE goals ADD COLUMN active_days TEXT DEFAULT '[]'")
            c.commit()
    finally:
        c.close()


def _migrate_messages():
    """迁移 messages 表 — 新增 thinking 列（存储 AI 思考过程，与 WorkBuddy 对齐）。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    try:
        info = c.execute("PRAGMA table_info(messages)").fetchall()
        if not info:
            return
        cols = {row[1] for row in info}
        if "thinking" not in cols:
            c.execute("ALTER TABLE messages ADD COLUMN thinking TEXT")
            c.commit()
        if "archived" not in cols:
            c.execute("ALTER TABLE messages ADD COLUMN archived INTEGER DEFAULT 0")
            c.commit()
    finally:
        c.close()


def _migrate_cache_stats():
    """迁移 cache_stats 表 — 新增 prompt_cache_hit_tokens 列（P2 缓存命中率埋点）。
    旧库若已建无该列的表，补充列；新库由 CREATE TABLE IF NOT EXISTS 直接建全。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    try:
        info = c.execute("PRAGMA table_info(cache_stats)").fetchall()
        if not info:
            return
        cols = {row[1] for row in info}
        if "prompt_cache_hit_tokens" not in cols:
            c.execute("ALTER TABLE cache_stats ADD COLUMN prompt_cache_hit_tokens INTEGER DEFAULT 0")
            c.commit()
    finally:
        c.close()


def _migrate_academic_papers():
    """迁移 academic_papers 表 — 补充 arxiv_id / pdf_url / code_links 字段。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    try:
        info = c.execute("PRAGMA table_info(academic_papers)").fetchall()
        if not info:
            return
        cols = {row[1] for row in info}
        new_cols = [
            ("arxiv_id", "TEXT DEFAULT ''"),
            ("pdf_url", "TEXT DEFAULT ''"),
            ("code_links", "TEXT DEFAULT ''"),
        ]
        for col_name, col_def in new_cols:
            if col_name not in cols:
                c.execute(f"ALTER TABLE academic_papers ADD COLUMN {col_name} {col_def}")
        c.commit()
    finally:
        c.close()



def init_db():
    # 先迁移旧数据库的 CHECK 约束（新增 experience 类型）
    _migrate_memory_types()
    _migrate_schedules()
    _migrate_conversations()
    _migrate_market_reports()
    _migrate_notes()
    _migrate_memories()
    _migrate_goals()
    _migrate_messages()
    _migrate_cache_stats()
    _migrate_academic_papers()
    with db() as c:
        c.executescript("""
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT DEFAULT 'New Chat',
    summary TEXT DEFAULT '',
    persona_name TEXT DEFAULT NULL,
    background TEXT DEFAULT NULL,
    background_image TEXT DEFAULT NULL,
    source_type TEXT DEFAULT NULL,
    source_id TEXT DEFAULT NULL,
    learning_progress TEXT DEFAULT NULL,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT CHECK(role IN ('user','assistant','system')),
    content TEXT,
    thinking TEXT,
    archived INTEGER DEFAULT 0,
    created_at TEXT,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT CHECK(type IN ('personal_info','preference','event','decision','fact','experience','skill')),
    content TEXT,
    importance INTEGER DEFAULT 3,
    keywords TEXT,
    source_conv_id TEXT,
    recorded_at TEXT,
    distilled_from INTEGER DEFAULT NULL,
    user_edited INTEGER DEFAULT 0,
    last_touched_at TEXT,
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
    remind_before INTEGER DEFAULT 30,
    goal_id INTEGER DEFAULT NULL,
    recurrence TEXT DEFAULT '',
    parent_id INTEGER DEFAULT NULL,
    source TEXT DEFAULT 'manual',
    confirmed_at TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS schedule_reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id INTEGER NOT NULL,
    reminded_at TEXT NOT NULL,
    FOREIGN KEY(schedule_id) REFERENCES schedules(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sch_reminder_schedule ON schedule_reminders(schedule_id);

CREATE TABLE IF NOT EXISTS schedule_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    event_time TEXT NOT NULL,
    star INTEGER DEFAULT 1,
    previous TEXT DEFAULT '',
    consensus TEXT DEFAULT '',
    actual TEXT DEFAULT '',
    revised TEXT DEFAULT '',
    affect_txt TEXT DEFAULT '',
    impact TEXT DEFAULT '' CHECK(impact IN ('','bullish','bearish','neutral')),
    country TEXT DEFAULT '',
    category TEXT DEFAULT 'economic' CHECK(category IN ('economic','market','other')),
    source TEXT DEFAULT 'jin10',
    source_id TEXT DEFAULT '',
    fetched_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_schedule_events_time ON schedule_events(event_time);
CREATE INDEX IF NOT EXISTS idx_schedule_events_name ON schedule_events(name);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    content TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    source TEXT DEFAULT 'manual',
    status TEXT DEFAULT 'confirmed' CHECK(status IN ('proposed','confirmed','cancelled')),
    stage TEXT DEFAULT 'raw' CHECK(stage IN ('raw','refined','distilled')),
    recorded_at TEXT,
    distilled_at TEXT,
    distilled_into TEXT DEFAULT '',
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
    active_days TEXT DEFAULT '[]',
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS cache_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT,
    provider TEXT DEFAULT '',
    model TEXT DEFAULT '',
    kind TEXT DEFAULT 'chat' CHECK(kind IN ('chat','background','other')),
    prompt_tokens INTEGER DEFAULT 0,
    prompt_cache_hit_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_cache_stats_time ON cache_stats(created_at);

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
    markdown_text TEXT DEFAULT '',
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_market_date ON market_reports(report_date);

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

-- 执行追踪表（Phase 1: 记录 LLM 调用 & 工具调用）
CREATE TABLE IF NOT EXISTS conversation_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_id TEXT NOT NULL,
    message_id INTEGER,
    trace_type TEXT NOT NULL,   -- 'llm_call' | 'tool_call' | 'error' | 'validation'
    round_num INTEGER DEFAULT 0,
    data TEXT NOT NULL,         -- JSON blob: {name, args, result_summary, duration_ms, tokens, ...}
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_traces_conv ON conversation_traces(conv_id, created_at);

-- 周期总结表（日/周/月/年总结正文，可查可回溯）
CREATE TABLE IF NOT EXISTS periodic_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_type TEXT NOT NULL CHECK(period_type IN ('daily','weekly','monthly','yearly')),
    period_key TEXT NOT NULL,
    headline TEXT DEFAULT '',
    content TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(period_type, period_key)
);
CREATE INDEX IF NOT EXISTS idx_periodic_summaries ON periodic_summaries(period_type, period_key);

-- 学术论文缓存表（Nature/Science/OpenAlex/Crossref 检索结果本地化）
CREATE TABLE IF NOT EXISTS academic_papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doi TEXT UNIQUE,
    title TEXT NOT NULL,
    authors TEXT DEFAULT '',
    venue TEXT DEFAULT '',
    year INTEGER DEFAULT 0,
    date TEXT DEFAULT '',
    citations INTEGER DEFAULT 0,
    tier TEXT DEFAULT '',
    rankings TEXT DEFAULT '',
    abstract TEXT DEFAULT '',
    url TEXT DEFAULT '',
    source TEXT DEFAULT '',
    region TEXT DEFAULT '',
    venue_kind TEXT DEFAULT '',
    arxiv_id TEXT DEFAULT '',
    pdf_url TEXT DEFAULT '',
    code_links TEXT DEFAULT '',
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_academic_venue ON academic_papers(venue);
CREATE INDEX IF NOT EXISTS idx_academic_year ON academic_papers(year);

CREATE VIRTUAL TABLE IF NOT EXISTS academic_papers_fts USING fts5(
    title, authors, venue, abstract,
    content='academic_papers', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS academic_papers_ai AFTER INSERT ON academic_papers BEGIN
    INSERT INTO academic_papers_fts(rowid, title, authors, venue, abstract)
    VALUES (new.id, new.title, new.authors, new.venue, new.abstract);
END;
CREATE TRIGGER IF NOT EXISTS academic_papers_ad AFTER DELETE ON academic_papers BEGIN
    INSERT INTO academic_papers_fts(academic_papers_fts, rowid, title, authors, venue, abstract)
    VALUES ('delete', old.id, old.title, old.authors, old.venue, old.abstract);
END;
CREATE TRIGGER IF NOT EXISTS academic_papers_au AFTER UPDATE ON academic_papers BEGIN
    INSERT INTO academic_papers_fts(academic_papers_fts, rowid, title, authors, venue, abstract)
    VALUES ('delete', old.id, old.title, old.authors, old.venue, old.abstract);
    INSERT INTO academic_papers_fts(rowid, title, authors, venue, abstract)
    VALUES (new.id, new.title, new.authors, new.venue, new.abstract);
END;
INSERT INTO academic_papers_fts(academic_papers_fts) VALUES('rebuild');

""")


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

def conv_create(title: str = "New Chat", persona_name: str = "", source_type: str = "", source_id: str = "") -> dict:
    cid = uuid.uuid4().hex[:8]
    now = _now()
    with db() as c:
        c.execute(
            "INSERT INTO conversations (id, title, summary, persona_name, source_type, source_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (cid, title, "",
             persona_name if persona_name else None,
             source_type if source_type else None,
             source_id if source_id else None,
             now, now)
        )
    return {
        "id": cid, "title": title, "summary": "",
        "persona_name": persona_name or None,
        "source_type": source_type or None, "source_id": source_id or None,
        "created_at": now, "updated_at": now,
    }


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
        q += " AND date(c.updated_at) >= date(?)"
        ps.append(date_from)
    if date_to:
        q += " AND date(c.updated_at) <= date(?)"
        ps.append(date_to)
    q += " GROUP BY c.id ORDER BY c.updated_at DESC"
    with db() as c:
        rs = c.execute(q, ps).fetchall()
    return [dict(r) for r in rs]


def conv_get(cid: str) -> Optional[dict]:
    with db() as c:
        r = c.execute("SELECT * FROM conversations WHERE id = ?", (cid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    # learning_progress 以 JSON 字符串存储，读时解析为 dict
    if d.get("learning_progress"):
        try:
            import json as _json
            d["learning_progress"] = _json.loads(d["learning_progress"])
        except (ValueError, TypeError):
            pass
    return d


def conv_del(cid: str):
    with db() as c:
        c.execute("DELETE FROM conversations WHERE id = ?", (cid,))


def conv_update_title(cid: str, title: str):
    """更新对话标题（不改 updated_at — 元数据操作不应影响按活动时间的排序）"""
    with db() as c:
        c.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, cid))


def conv_update_summary(cid: str, summary: str):
    """存储对话摘要"""
    now = _now()
    with db() as c:
        c.execute("UPDATE conversations SET summary = ?, updated_at = ? WHERE id = ?", (summary, now, cid))


def conv_update_persona(cid: str, persona_name: str | None):
    """更新对话的 Persona"""
    now = _now()
    with db() as c:
        c.execute("UPDATE conversations SET persona_name = ?, updated_at = ? WHERE id = ?", (persona_name, now, cid))


def conv_update_background(cid: str, background: str | None):
    """更新对话的世界观背景（可为 None 清除）"""
    now = _now()
    with db() as c:
        c.execute("UPDATE conversations SET background = ?, updated_at = ? WHERE id = ?", (background, now, cid))


def conv_update_background_image(cid: str, image_file: str | None):
    """更新对话的背景图片文件名（可为 None 清除）"""
    now = _now()
    with db() as c:
        c.execute("UPDATE conversations SET background_image = ?, updated_at = ? WHERE id = ?", (image_file, now, cid))


def conv_update_learning_progress(cid: str, progress: dict | None):
    """更新对话的学习进度（JSON：{doc_id, chunk_index, total_chunks, title}）"""
    import json as _json
    now = _now()
    data = _json.dumps(progress, ensure_ascii=False) if progress else None
    with db() as c:
        c.execute("UPDATE conversations SET learning_progress = ?, updated_at = ? WHERE id = ?", (data, now, cid))


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def msg_add(cid: str, role: str, content: str, thinking: str = "") -> int:
    now = _now()
    with db() as c:
        cur = c.execute(
            "INSERT INTO messages (conversation_id, role, content, thinking, created_at) VALUES (?,?,?,?,?)",
            (cid, role, content, thinking or None, now)
        )
        c.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, cid))
        return cur.lastrowid


def msg_list(cid: str) -> list:
    with db() as c:
        rs = c.execute(
            "SELECT * FROM messages WHERE conversation_id = ? AND archived = 0 ORDER BY id", (cid,)
        ).fetchall()
    return [dict(r) for r in rs]


def msg_recent(cid: str, n: int = 10) -> list:
    with db() as c:
        rs = c.execute(
            "SELECT * FROM messages WHERE conversation_id = ? AND archived = 0 ORDER BY id DESC LIMIT ?", (cid, n)
        ).fetchall()
    return [dict(r) for r in reversed(rs)]


def msg_count(cid: str) -> int:
    with db() as c:
        r = c.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE conversation_id = ? AND role != 'system' AND archived = 0", (cid,)
        ).fetchone()
    return r["cnt"] if r else 0


def msg_get(msg_id: int) -> Optional[dict]:
    """按全局消息 ID 取单条消息"""
    with db() as c:
        r = c.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
    return dict(r) if r else None


def msg_update(msg_id: int, content: str) -> bool:
    """更新消息内容（编辑重发用），并刷新会话 updated_at"""
    now = _now()
    with db() as c:
        cur = c.execute(
            "UPDATE messages SET content = ?, created_at = ? WHERE id = ?",
            (content, now, msg_id),
        )
        if cur.rowcount:
            c.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = "
                "(SELECT conversation_id FROM messages WHERE id = ?)",
                (now, msg_id),
            )
            return True
        return False


def msg_del_from(msg_id: int) -> int:
    """删除 id >= msg_id 的所有消息（含自身及后续），返回删除数量"""
    with db() as c:
        # 先定位所属会话以刷新 updated_at
        row = c.execute("SELECT conversation_id FROM messages WHERE id = ?", (msg_id,)).fetchone()
        cur = c.execute("DELETE FROM messages WHERE id >= ?", (msg_id,))
        if row:
            c.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (_now(), row["conversation_id"]))
        return cur.rowcount


def msg_del_one(msg_id: int) -> int:
    """删除单条消息（不影响前后消息），返回删除数量"""
    with db() as c:
        row = c.execute("SELECT conversation_id FROM messages WHERE id = ?", (msg_id,)).fetchone()
        cur = c.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
        if row:
            c.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (_now(), row["conversation_id"]))
        return cur.rowcount


def msg_archive(cid: str, msg_ids: list[int]) -> int:
    """归档消息（压缩后保留原文，避免破坏 regenerate/edit 语义）。返回归档数量。"""
    ids = [int(i) for i in msg_ids if int(i) > 0]
    if not ids:
        return 0
    with db() as c:
        placeholders = ",".join("?" * len(ids))
        cur = c.execute(
            f"UPDATE messages SET archived = 1 WHERE conversation_id = ? AND id IN ({placeholders})",
            [cid] + ids,
        )
        if cur.rowcount:
            c.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (_now(), cid))
        return cur.rowcount


# ---------------------------------------------------------------------------
# Conversation Traces (执行追踪)
# ---------------------------------------------------------------------------

def trace_add(conv_id: str, trace_type: str, data: dict,
              message_id: int = None, round_num: int = 0) -> int:
    with db() as c:
        cur = c.execute(
            "INSERT INTO conversation_traces (conv_id, message_id, trace_type, round_num, data, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (conv_id, message_id, trace_type, round_num, json.dumps(data, ensure_ascii=False), _now()),
        )
        return cur.lastrowid


def trace_list(conv_id: str = "", trace_type: str = "", limit: int = 100) -> list:
    q = "SELECT * FROM conversation_traces WHERE 1=1"
    ps = []
    if conv_id:
        q += " AND conv_id = ?"
        ps.append(conv_id)
    if trace_type:
        q += " AND trace_type = ?"
        ps.append(trace_type)
    q += " ORDER BY id DESC LIMIT ?"
    ps.append(limit)
    with db() as c:
        rs = c.execute(q, ps).fetchall()
    return [dict(r) for r in rs]


def trace_query(keyword: str = "", trace_type: str = "", conv_id: str = "",
                limit: int = 50, offset: int = 0) -> list:
    """跨对话查询执行痕迹，支持按工具名/关键词过滤（data JSON 内 name/args/result_summary）。

    keyword 为空时按时间倒序返回；非空时做 LIKE 匹配（名称 + 内容片段）。
    """
    q = "SELECT * FROM conversation_traces WHERE 1=1"
    ps = []
    if conv_id:
        q += " AND conv_id = ?"
        ps.append(conv_id)
    if trace_type:
        q += " AND trace_type = ?"
        ps.append(trace_type)
    if keyword:
        kw = f"%{keyword}%"
        q += " AND (data LIKE ? OR trace_type LIKE ?)"
        ps.extend([kw, kw])
    q += " ORDER BY id DESC LIMIT ? OFFSET ?"
    ps.extend([limit, offset])
    with db() as c:
        rs = c.execute(q, ps).fetchall()
    return [dict(r) for r in rs]


def trace_stats(conv_id: str = "") -> dict:
    """统计概览：总调用次数、类型分布、平均耗时、错误数"""
    q = "SELECT trace_type, COUNT(*) as cnt FROM conversation_traces"
    ps = []
    if conv_id:
        q += " WHERE conv_id = ?"
        ps.append(conv_id)
    q += " GROUP BY trace_type"
    with db() as c:
        rs = c.execute(q, ps).fetchall()
    type_counts = {r["trace_type"]: r["cnt"] for r in rs}
    total = sum(type_counts.values())
    errors = type_counts.get("error", 0)
    return {
        "total": total,
        "by_type": type_counts,
        "error_count": errors,
        "error_rate": round(errors / total, 3) if total > 0 else 0,
    }


# ---------------------------------------------------------------------------
# Memories
# ---------------------------------------------------------------------------

def mem_list_by_date(date_from: str = "", date_to: str = "") -> list:
    """按创建日期范围筛选记忆"""
    q = "SELECT * FROM memories WHERE 1=1"
    ps = []
    if date_from:
        q += " AND date(created_at) >= date(?)"
        ps.append(date_from)
    if date_to:
        q += " AND date(created_at) <= date(?)"
        ps.append(date_to)
    q += " ORDER BY importance DESC, created_at DESC"
    with db() as c:
        rs = c.execute(q, ps).fetchall()
    return [dict(r) for r in rs]


def mem_add(type_: str, content: str, importance: int = 3,
            keywords: str = "", source_conv_id: str = "",
            recorded_at: str = "", distilled_from: Optional[int] = None) -> int:
    # 落库守卫：拒绝明文密钥写入记忆库（防蒸馏/对话把明文提进记忆）
    from .validators.sanitize_guard import guard_store
    risk = guard_store(content, field="memory")
    if risk:
        logging.getLogger("zenith.db").warning(
            "拒绝写入记忆（含明文密钥）: type=%s source=%s names=%s",
            type_, source_conv_id, risk["names"]
        )
        return -1
    now = _now()
    with db() as c:
        cur = c.execute(
            "INSERT INTO memories (type, content, importance, keywords, source_conv_id, recorded_at, distilled_from, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (type_, content, importance, keywords, source_conv_id, recorded_at or now, distilled_from, now)
        )
        return cur.lastrowid


def mem_list(type_: str = "", limit: int = 0) -> list:
    with db() as c:
        q = "SELECT * FROM memories"
        ps = []
        if type_:
            q += " WHERE type = ?"
            ps.append(type_)
        q += " ORDER BY importance DESC, created_at DESC"
        if limit > 0:
            q += " LIMIT ?"
            ps.append(limit)
        rs = c.execute(q, ps).fetchall()
    return [dict(r) for r in rs]


def mem_search(keyword: str = "", limit: int = 30) -> list:
    with db() as c:
        kw = keyword.strip()
        if not kw:
            return []
        try:
            tokens = []
            for t in kw.split():
                if t.isascii() and len(t) > 1:
                    tokens.append(f"{t}*")
                else:
                    tokens.append(t)
            fts_query = " OR ".join(tokens) if tokens else kw
            rs = c.execute(
                "SELECT m.* FROM memories m "
                "JOIN memories_fts fts ON m.id = fts.rowid "
                "WHERE memories_fts MATCH ? "
                "ORDER BY rank "
                "LIMIT ?",
                (fts_query, limit)
            ).fetchall()
            if rs:
                return [dict(r) for r in rs]
        except Exception:
            pass
        rs = c.execute(
            "SELECT * FROM memories WHERE content LIKE ? OR keywords LIKE ? "
            "ORDER BY importance DESC LIMIT ?",
            (f"%{kw}%", f"%{kw}%", limit)
        ).fetchall()
    return [dict(r) for r in rs]


MEMORY_TYPES = ("personal_info", "preference", "event", "decision", "fact", "experience", "skill")


def mem_update(mid: int, content: str = "", type_: str = "", importance: int = 0,
               keywords: str = "") -> bool:
    """更新记忆（内容/类型/重要性/关键词）。返回是否成功。

    - 只更新传入的非空字段；全部为空时直接返回 True（无改动）
    - 与 mem_add 一致走明文密钥守卫，命中则拒绝
    - FTS 由 memories_au 触发器自动同步，无需手动维护
    - 更新后置 user_edited = 1，标记人工修改
    """
    existing = mem_get(mid)
    if not existing:
        return False
    new_content = content if content else existing.get("content", "")
    if not new_content:
        return False
    from .validators.sanitize_guard import guard_store
    risk = guard_store(new_content, field="memory")
    if risk:
        logging.getLogger("zenith.db").warning(
            "拒绝更新记忆（含明文密钥）: mid=%s names=%s", mid, risk["names"]
        )
        return False
    fields, params = [], []
    if content:
        fields.append("content = ?"); params.append(content)
    if type_ and type_ in MEMORY_TYPES:
        fields.append("type = ?"); params.append(type_)
    if importance:
        fields.append("importance = ?"); params.append(int(importance))
    if keywords:
        fields.append("keywords = ?"); params.append(keywords)
    if not fields:
        return True
    fields.append("user_edited = 1")
    params.append(mid)
    with db() as c:
        c.execute(f"UPDATE memories SET {', '.join(fields)} WHERE id = ?", params)
    return True


def mem_del(mid: int):
    with db() as c:
        c.execute("DELETE FROM memories WHERE id = ?", (mid,))


def mem_get(mid: int) -> dict | None:
    with db() as c:
        r = c.execute("SELECT * FROM memories WHERE id = ?", (mid,)).fetchone()
    return dict(r) if r else None


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
                importance, category, impact, country, remind_before, goal_id, recurrence, parent_id, source, confirmed_at, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                data.get("remind_before", 30),
                data.get("goal_id", None),
                data.get("recurrence", ""),
                data.get("parent_id", None),
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
        q += " AND date(start_time) >= date(?)"
        ps.append(date_from)
    if date_to:
        q += " AND date(start_time) <= date(?)"
        ps.append(date_to)
    q += " ORDER BY start_time ASC"
    with db() as c:
        rs = c.execute(q, ps).fetchall()
    return [dict(r) for r in rs]


def sch_get(sid: int) -> Optional[dict]:
    with db() as c:
        r = c.execute("SELECT * FROM schedules WHERE id = ?", (sid,)).fetchone()
    return dict(r) if r else None


_SCHEDULE_COLUMNS = {"title", "description", "start_time", "end_time", "location", "status", "priority", "importance", "category", "impact", "country", "remind_before", "goal_id", "recurrence", "parent_id", "source", "confirmed_at"}


def sch_update(sid: int, data: dict):
    """更新日程。值为 None 的字段会被显式清空（设为 NULL）。"""
    fs = []
    ps = []
    for k, v in data.items():
        if k not in _SCHEDULE_COLUMNS:
            continue
        # None 也写入（用于清空 goal_id 等字段），不再跳过
        fs.append(f"{k} = ?")
        ps.append(v)
    if not fs:
        return
    ps.append(sid)
    with db() as c:
        c.execute(f"UPDATE schedules SET {', '.join(fs)} WHERE id = ?", ps)
        # P1-2: status 变为 confirmed 时自动更新 confirmed_at
        if data.get("status") == "confirmed":
            c.execute("UPDATE schedules SET confirmed_at = ? WHERE id = ?", (_now(), sid))


def sch_del(sid: int):
    with db() as c:
        c.execute("DELETE FROM schedules WHERE id = ?", (sid,))


# ---------------------------------------------------------------------------
# Periodic Summaries (日/周/月/年总结)
# ---------------------------------------------------------------------------

def psum_upsert(period_type: str, period_key: str, headline: str = "", content: str = "", summary: str = "") -> int:
    """upsert 一条周期总结，返回其 id。"""
    now = _now()
    with db() as c:
        c.execute(
            """INSERT INTO periodic_summaries (period_type, period_key, headline, content, summary, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(period_type, period_key) DO UPDATE SET
                 headline=excluded.headline, content=excluded.content,
                 summary=excluded.summary, updated_at=excluded.updated_at""",
            (period_type, period_key, headline, content, summary, now, now)
        )
        row = c.execute(
            "SELECT id FROM periodic_summaries WHERE period_type=? AND period_key=?",
            (period_type, period_key)
        ).fetchone()
        return row["id"]


def psum_get(period_type: str, period_key: str) -> Optional[dict]:
    with db() as c:
        row = c.execute(
            "SELECT * FROM periodic_summaries WHERE period_type=? AND period_key=?",
            (period_type, period_key)
        ).fetchone()
    return dict(row) if row else None


def psum_list(period_type: str = "") -> list:
    q = "SELECT * FROM periodic_summaries"
    ps = []
    if period_type:
        q += " WHERE period_type = ?"
        ps.append(period_type)
    q += " ORDER BY period_key DESC"
    with db() as c:
        rs = c.execute(q, ps).fetchall()
    return [dict(r) for r in rs]


def psum_delete(period_type: str, period_key: str) -> bool:
    with db() as c:
        cur = c.execute(
            "DELETE FROM periodic_summaries WHERE period_type=? AND period_key=?",
            (period_type, period_key)
        )
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def note_add(data: dict) -> int:
    now = _now()
    recorded = data.get("recorded_at") or now
    stage = data.get("stage", "raw")
    if stage not in ("raw", "refined", "distilled"):
        stage = "raw"
    with db() as c:
        cur = c.execute(
            "INSERT INTO notes (title, content, tags, source, status, stage, recorded_at, distilled_at, distilled_into, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                data["title"],
                data.get("content", ""),
                data.get("tags", ""),
                data.get("source", "manual"),
                data.get("status", "confirmed"),
                stage,
                recorded,
                data.get("distilled_at", ""),
                data.get("distilled_into", ""),
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
        q += " AND date(created_at) >= date(?)"
        ps.append(date_from)
    if date_to:
        q += " AND date(created_at) <= date(?)"
        ps.append(date_to)
    q += " ORDER BY updated_at DESC"
    with db() as c:
        rs = c.execute(q, ps).fetchall()
    return [dict(r) for r in rs]


def note_get(nid: int) -> Optional[dict]:
    with db() as c:
        r = c.execute("SELECT * FROM notes WHERE id = ?", (nid,)).fetchone()
    return dict(r) if r else None


_NOTE_COLUMNS = {"title", "content", "tags", "source", "status", "stage", "recorded_at", "distilled_at", "distilled_into"}


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

    # 解析 active_days（默认空数组 JSON）
    raw_active_days = data.get("active_days", [])
    if isinstance(raw_active_days, (list, tuple)):
        active_days = json.dumps(list(raw_active_days), ensure_ascii=False)
    else:
        active_days = str(raw_active_days) if raw_active_days else '[]'

    with db() as c:
        cur = c.execute(
            """INSERT INTO goals
               (title, start_value, target_value, current_value, daily_target, strategy,
                status, start_date, end_date, active_days, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data["title"],
                sv, tv, sv,
                daily,
                data.get("strategy", "compound"),
                "active",
                start_date, end_date, active_days,
                now, now,
            )
        )
        return cur.lastrowid


def _parse_active_days(value):
    """将 active_days 字符串解析为 list；失败返回空数组。"""
    if isinstance(value, (list, tuple)):
        return list(value)
    if not value:
        return []
    try:
        return json.loads(str(value))
    except (json.JSONDecodeError, TypeError):
        return []


def goal_list(status: str = "") -> list:
    q = "SELECT * FROM goals WHERE 1=1"
    ps = []
    if status:
        q += " AND status = ?"
        ps.append(status)
    q += " ORDER BY created_at DESC"
    with db() as c:
        rs = c.execute(q, ps).fetchall()
    items = [dict(r) for r in rs]
    for item in items:
        item["active_days"] = _parse_active_days(item.get("active_days"))
    return items


def goal_get(gid: int) -> Optional[dict]:
    with db() as c:
        r = c.execute("SELECT * FROM goals WHERE id = ?", (gid,)).fetchone()
    if not r:
        return None
    item = dict(r)
    item["active_days"] = _parse_active_days(item.get("active_days"))
    return item


_GOAL_COLUMNS = {"title", "start_value", "target_value", "current_value", "daily_target", "strategy", "status", "start_date", "end_date", "active_days"}


def goal_update(gid: int, data: dict):
    import math
    fs = []
    ps = []
    for k, v in data.items():
        if k not in _GOAL_COLUMNS:
            continue
        if v is None:
            continue
        # active_days 以 JSON 字符串存储
        if k == "active_days" and isinstance(v, (list, tuple)):
            v = json.dumps(list(v), ensure_ascii=False)
        fs.append(f"{k} = ?")
        ps.append(v)
    # 变更 start/target/daily/current 任一数值时，重算 end_date：
    # 以「当前值」为基准、从「今天」起算剩余天数，得出真实 ETA（与前端"需 N 天"一致）
    if any(k in data for k in ("start_value", "target_value", "daily_target", "current_value")):
        g = goal_get(gid)
        if g:
            daily = float(data.get("daily_target", g.get("daily_target", 5)))
            sv = float(data.get("start_value", g.get("start_value", 0)))
            tv = float(data.get("target_value", g.get("target_value", 1)))
            cv = float(data.get("current_value", g.get("current_value", 0)))
            base = cv if cv > 0 else sv
            if base > 0 and tv > base and daily > 0:
                days = math.ceil(math.log(tv / base) / math.log(1 + daily / 100))
                from datetime import timedelta as _td
                from datetime import datetime as _dt
                try:
                    ed = (_dt.strptime(_now()[:10], "%Y-%m-%d") + _td(days=days)).strftime("%Y-%m-%d")
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
    from .timezone import now_tz, DEFAULT_TIMEZONE
    now = now_tz()
    try:
        start = _dt.fromisoformat(g.get("start_date") or now.isoformat())
        if start.tzinfo is None:
            start = start.replace(tzinfo=DEFAULT_TIMEZONE)
    except (ValueError, TypeError):
        start = now
    try:
        end = _dt.fromisoformat(g.get("end_date") or now.isoformat())
        if end.tzinfo is None:
            end = end.replace(tzinfo=DEFAULT_TIMEZONE)
    except (ValueError, TypeError):
        end = now
    days_passed = max((now - start).days, 1)
    daily_return = 0.0
    if sv > 0:
        daily_return = round((pow(cv / sv, 1 / days_passed) - 1) * 100, 2) if days_passed > 0 else 0

    # 关联日程统计
    related_schedules = [s for s in sch_list() if s.get("goal_id") == gid]
    completed_schedules = [s for s in related_schedules if s.get("status") == "done"]

    return {
        "progress": min(progress, 100),
        "days_total": (end - start).days,
        "days_passed": days_passed,
        "daily_return": daily_return,
        "remaining": max(tv - cv, 0),
        "on_track": daily_return >= float(g.get("daily_target", 5)),
        "schedule_count": len(related_schedules),
        "completed_schedule_count": len(completed_schedules),
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


def analysis_list_by_date(date_from: str = "", date_to: str = "") -> list:
    """按日期范围筛选内容分析文档"""
    with db() as c:
        q = "SELECT id, filename, analysis_text, created_at FROM analysis_documents WHERE 1=1"
        ps = []
        if date_from:
            q += " AND date(created_at) >= date(?)"
            ps.append(date_from)
        if date_to:
            q += " AND date(created_at) <= date(?)"
            ps.append(date_to)
        q += " ORDER BY created_at DESC"
        rs = c.execute(q, ps).fetchall()
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
                analysis_text, daily_advice, weekly_advice, markdown_text, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                data["report_date"],
                data.get("gold_price", ""),
                data.get("factor_data", ""),
                data.get("events_overdue", ""),
                data.get("events_upcoming", ""),
                data["analysis_text"],
                data.get("daily_advice", ""),
                data.get("weekly_advice", ""),
                data.get("markdown_text", ""),
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
# Schedule Events Cache (外部财经日历事件缓存)
# ---------------------------------------------------------------------------

def event_add_or_update(data: dict) -> int:
    """添加或更新外部财经事件缓存。根据 source + source_id 去重，否则按 name + event_time 去重。"""
    now = _now()
    name = data.get("name", "").strip()
    event_time = data.get("event_time", "").strip()
    source = data.get("source", "jin10")
    source_id = data.get("source_id", "").strip()

    with db() as c:
        if source_id:
            existing = c.execute(
                "SELECT id FROM schedule_events WHERE source = ? AND source_id = ?",
                (source, source_id),
            ).fetchone()
        else:
            existing = c.execute(
                "SELECT id FROM schedule_events WHERE name = ? AND event_time = ?",
                (name, event_time),
            ).fetchone()

        if existing:
            eid = existing["id"]
            c.execute(
                """UPDATE schedule_events SET
                    name = ?, event_time = ?, star = ?, previous = ?, consensus = ?,
                    actual = ?, revised = ?, affect_txt = ?, impact = ?, country = ?,
                    category = ?, updated_at = ?
                WHERE id = ?""",
                (
                    name, event_time, data.get("star", 1), data.get("previous", ""),
                    data.get("consensus", ""), data.get("actual", ""), data.get("revised", ""),
                    data.get("affect_txt", ""), data.get("impact", ""), data.get("country", ""),
                    data.get("category", "economic"), now, eid,
                ),
            )
            return eid

        cur = c.execute(
            """INSERT INTO schedule_events
                (name, event_time, star, previous, consensus, actual, revised,
                 affect_txt, impact, country, category, source, source_id, fetched_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                name, event_time, data.get("star", 1), data.get("previous", ""),
                data.get("consensus", ""), data.get("actual", ""), data.get("revised", ""),
                data.get("affect_txt", ""), data.get("impact", ""), data.get("country", ""),
                data.get("category", "economic"), source, source_id, now, now,
            ),
        )
        return cur.lastrowid


def event_list(date_from: str = "", date_to: str = "", name_search: str = "", min_star: int = 1, limit: int = 200) -> list:
    """列出缓存的财经事件。"""
    q = "SELECT * FROM schedule_events WHERE 1=1"
    ps = []
    if date_from:
        q += " AND event_time >= ?"
        ps.append(date_from)
    if date_to:
        q += " AND event_time <= ?"
        ps.append(date_to)
    if name_search:
        q += " AND name LIKE ?"
        ps.append(f"%{name_search}%")
    if min_star:
        q += " AND star >= ?"
        ps.append(min_star)
    q += " ORDER BY event_time ASC LIMIT ?"
    ps.append(limit)
    with db() as c:
        rs = c.execute(q, ps).fetchall()
    return [dict(r) for r in rs]


def event_find_by_name(name: str, date_from: str = "", date_to: str = "") -> list:
    """按事件名称模糊匹配查找。"""
    q = "SELECT * FROM schedule_events WHERE name LIKE ?"
    ps = [f"%{name}%"]
    if date_from:
        q += " AND event_time >= ?"
        ps.append(date_from)
    if date_to:
        q += " AND event_time <= ?"
        ps.append(date_to)
    q += " ORDER BY event_time ASC LIMIT 20"
    with db() as c:
        rs = c.execute(q, ps).fetchall()
    return [dict(r) for r in rs]


def event_delete(eid: int):
    """删除缓存事件。"""
    with db() as c:
        c.execute("DELETE FROM schedule_events WHERE id = ?", (eid,))


# ---------------------------------------------------------------------------
# Cache stats（P2 缓存命中率埋点）
# ---------------------------------------------------------------------------

def cache_stat_add(provider: str = "", model: str = "", kind: str = "chat",
                   prompt_tokens: int = 0, prompt_cache_hit_tokens: int = 0,
                   completion_tokens: int = 0):
    """记录一次 LLM 调用的 token / 前缀缓存命中数据。"""
    try:
        with db() as c:
            c.execute(
                "INSERT INTO cache_stats (created_at, provider, model, kind, prompt_tokens, "
                "prompt_cache_hit_tokens, completion_tokens) VALUES (?,?,?,?,?,?,?)",
                (_now(), provider[:64], model[:64], kind,
                 int(prompt_tokens or 0), int(prompt_cache_hit_tokens or 0),
                 int(completion_tokens or 0))
            )
    except Exception:
        # 埋点失败不影响主流程
        pass


def cache_stats_summary(hours: int = 24) -> dict:
    """聚合最近 N 小时的缓存命中统计。"""
    import datetime as _dt
    cutoff = (_dt.datetime.now(_dt.timezone.utc).astimezone() - _dt.timedelta(hours=hours)).isoformat()
    with db() as c:
        rows = c.execute(
            "SELECT COUNT(*) AS calls, "
            "COALESCE(SUM(prompt_tokens),0) AS prompt_tokens, "
            "COALESCE(SUM(prompt_cache_hit_tokens),0) AS hit_tokens, "
            "COALESCE(SUM(completion_tokens),0) AS completion_tokens "
            "FROM cache_stats WHERE created_at >= ?", (cutoff,)
        ).fetchone()
        by_kind = c.execute(
            "SELECT kind, COUNT(*) AS calls, COALESCE(SUM(prompt_cache_hit_tokens),0) AS hit, "
            "COALESCE(SUM(prompt_tokens),0) AS prompt FROM cache_stats "
            "WHERE created_at >= ? GROUP BY kind", (cutoff,)
        ).fetchall()
    total = dict(rows) if rows else {}
    hit = total.get("hit_tokens", 0) or 0
    prompt = total.get("prompt_tokens", 0) or 0
    total["hit_rate"] = round(hit / prompt, 4) if prompt else 0.0
    total["by_kind"] = [dict(r) for r in by_kind]
    return total



# ---------------------------------------------------------------------------
# Academic papers cache（Nature/Science/OpenAlex/Crossref 本地缓存）
# ---------------------------------------------------------------------------

def academic_paper_upsert(paper: dict) -> int:
    """按 DOI upsert 一篇论文；无 DOI 时退化为 title+year 去重。返回论文 ID。"""
    doi = (paper.get("doi") or "").strip()
    title = (paper.get("title") or "").strip()
    if not title:
        return 0
    now = _now()
    with db() as c:
        existing = None
        if doi:
            existing = c.execute("SELECT id FROM academic_papers WHERE doi = ?", (doi,)).fetchone()
        if existing is None:
            existing = c.execute(
                "SELECT id FROM academic_papers WHERE title = ? AND year = ? LIMIT 1",
                (title, int(paper.get("year") or 0)),
            ).fetchone()

        if existing:
            pid = existing["id"]
            c.execute(
                """UPDATE academic_papers SET
                    title=?, authors=?, venue=?, year=?, date=?, citations=?, tier=?,
                    rankings=?, abstract=?, url=?, source=?, region=?, venue_kind=?,
                    arxiv_id=?, pdf_url=?, code_links=?, updated_at=?
                WHERE id=?""",
                (
                    title,
                    (paper.get("authors") or ""),
                    (paper.get("venue") or ""),
                    int(paper.get("year") or 0),
                    (paper.get("date") or ""),
                    int(paper.get("citations") or 0),
                    (paper.get("tier") or ""),
                    (paper.get("rankings") or ""),
                    (paper.get("abstract") or ""),
                    (paper.get("url") or ""),
                    (paper.get("source") or ""),
                    (paper.get("region") or ""),
                    (paper.get("venue_kind") or ""),
                    (paper.get("arxiv_id") or ""),
                    (paper.get("pdf_url") or ""),
                    json.dumps(paper.get("code_links") or [], ensure_ascii=False),
                    now,
                    pid,
                ),
            )
            return pid

        cur = c.execute(
            """INSERT INTO academic_papers
                (doi, title, authors, venue, year, date, citations, tier, rankings,
                 abstract, url, source, region, venue_kind, arxiv_id, pdf_url, code_links,
                 created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                doi or None,
                title,
                (paper.get("authors") or ""),
                (paper.get("venue") or ""),
                int(paper.get("year") or 0),
                (paper.get("date") or ""),
                int(paper.get("citations") or 0),
                (paper.get("tier") or ""),
                (paper.get("rankings") or ""),
                (paper.get("abstract") or ""),
                (paper.get("url") or ""),
                (paper.get("source") or ""),
                (paper.get("region") or ""),
                (paper.get("venue_kind") or ""),
                (paper.get("arxiv_id") or ""),
                (paper.get("pdf_url") or ""),
                json.dumps(paper.get("code_links") or [], ensure_ascii=False),
                now,
                now,
            ),
        )
        return cur.lastrowid


def _paper_from_row(r) -> dict:
    """把 SQLite 行转为 dict，并解析 code_links JSON。"""
    d = dict(r)
    try:
        raw = d.get("code_links") or ""
        d["code_links"] = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        d["code_links"] = []
    return d



def academic_paper_get(paper_id: int) -> dict | None:
    with db() as c:
        r = c.execute("SELECT * FROM academic_papers WHERE id = ?", (paper_id,)).fetchone()
    return _paper_from_row(r) if r else None


def academic_paper_get_by_doi(doi: str) -> dict | None:
    with db() as c:
        r = c.execute("SELECT * FROM academic_papers WHERE doi = ?", (doi,)).fetchone()
    return _paper_from_row(r) if r else None


def academic_paper_search(query: str = "", venue: str = "", year_from: int = 0,
                          year_to: int = 0, limit: int = 20, offset: int = 0) -> list:
    """本地学术论文检索（优先 FTS，失败/为空时回退 LIKE）。"""
    with db() as c:
        if query and query.strip():
            q = """
                SELECT p.* FROM academic_papers p
                JOIN academic_papers_fts f ON p.id = f.rowid
                WHERE academic_papers_fts MATCH ?
            """
            ps = [query.strip()]
        else:
            q = "SELECT p.* FROM academic_papers p WHERE 1=1"
            ps = []
        if venue:
            q += " AND p.venue = ?"
            ps.append(venue)
        if year_from:
            q += " AND p.year >= ?"
            ps.append(int(year_from))
        if year_to:
            q += " AND p.year <= ?"
            ps.append(int(year_to))
        q += " ORDER BY p.citations DESC, p.year DESC LIMIT ? OFFSET ?"
        ps.extend([int(limit), int(offset)])
        try:
            rs = c.execute(q, ps).fetchall()
        except Exception:
            rs = []
        if query and query.strip() and not rs:
            # FTS 查询语法错误/中文分词无命中时回退 LIKE
            like = f"%{query.strip()}%"
            q2 = ("SELECT * FROM academic_papers WHERE title LIKE ? OR abstract LIKE ? "
                  "OR authors LIKE ? OR venue LIKE ? ORDER BY citations DESC LIMIT ? OFFSET ?")
            rs = c.execute(q2, (like, like, like, like, int(limit), int(offset))).fetchall()
    return [dict(r) for r in rs]


def academic_paper_stats() -> dict:
    with db() as c:
        total = c.execute("SELECT COUNT(*) AS cnt FROM academic_papers").fetchone()["cnt"]
        by_venue = c.execute(
            "SELECT venue, COUNT(*) AS cnt FROM academic_papers GROUP BY venue ORDER BY cnt DESC LIMIT 20"
        ).fetchall()
        by_year = c.execute(
            "SELECT year, COUNT(*) AS cnt FROM academic_papers GROUP BY year ORDER BY year DESC LIMIT 10"
        ).fetchall()
    return {
        "total": total,
        "by_venue": [dict(r) for r in by_venue],
        "by_year": [dict(r) for r in by_year],
    }


# ---------------------------------------------------------------------------
# Utils
# ---------------------------------------------------------------------------

def _now() -> str:
    from .timezone import now_tz
    return now_tz().isoformat()
