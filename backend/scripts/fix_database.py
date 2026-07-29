"""Re-apply Phase 2 DB changes to database.py (lost by git checkout)"""
from pathlib import Path
app_path = Path(__file__).parent.parent / 'database.py'

with open(app_path, 'r', encoding='utf-8') as f:
    content = f.read()

changes = 0

# 1. add 'import os'
if 'import os\n' not in content[:100]:
    content = content.replace('from __future__ import annotations\n\nimport sqlite3',
                              'from __future__ import annotations\n\nimport os\nimport sqlite3')
    changes += 1

# 2. _TESTING + _test_conn after DB_PATH
if '_TESTING' not in content:
    old = "DB_PATH = Path(__file__).parent.parent / \"data\" / \"zenith.db\""
    new = """DB_PATH = Path(__file__).parent.parent / "data" / "zenith.db"
_TESTING = os.environ.get("ZENITH_TESTING") == "1"
_test_conn = None"""
    content = content.replace(old, new)
    changes += 1

# 3. _conn(): testing mode support
if 'if _TESTING:' not in content:
    old_conn = """def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA cache_size=-8000")
    c.execute("PRAGMA synchronous=NORMAL")
    return c"""
    new_conn = """def _conn():
    global _test_conn
    if _TESTING:
        if _test_conn is None:
            _test_conn = sqlite3.connect(":memory:")
            _test_conn.row_factory = sqlite3.Row
            _test_conn.execute("PRAGMA foreign_keys=ON")
        return _test_conn
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA cache_size=-8000")
    c.execute("PRAGMA synchronous=NORMAL")
    return c"""
    content = content.replace(old_conn, new_conn)
    changes += 1

# 4. db(): testing mode skip commit/close
if 'if not _TESTING:' not in content:
    old_db = """@contextmanager
def db():
    c = _conn()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        try:
            c.execute("PRAGMA optimize")
        except Exception:
            pass
        c.close()"""
    new_db = """@contextmanager
def db():
    c = _conn()
    try:
        yield c
        if not _TESTING:
            c.commit()
    except Exception:
        if not _TESTING:
            c.rollback()
        raise
    finally:
        if not _TESTING:
            try:
                c.execute("PRAGMA optimize")
            except Exception:
                pass
            c.close()"""
    content = content.replace(old_db, new_db)
    changes += 1

# 5. _migrate_memories_fts
if '_migrate_memories_fts' not in content:
    fts_func = """

def _migrate_memories_fts():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    try:
        c.execute("PRAGMA foreign_keys=OFF")
        ft_exists = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memories_fts'"
        ).fetchone()
        if not ft_exists:
            c.executescript(\"\"\"
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
\"\"\")
            c.execute("INSERT INTO memories_fts(rowid, content, keywords) SELECT id, content, keywords FROM memories")
            c.commit()
        c.execute("PRAGMA foreign_keys=ON")
    finally:
        c.close()
"""
    # Insert before _migrate_goals
    content = content.replace('\n\ndef _migrate_goals():', fts_func + '\n\ndef _migrate_goals():')
    changes += 1

# 6. init_db: skip migrations when testing + add _migrate_memories_fts
if 'if not _TESTING:' not in content:
    old_init = 'def init_db():\n    _migrate_memory_types()'
    new_init = 'def init_db():\n    if not _TESTING:\n        _migrate_memory_types()\n        _migrate_memories_fts()'
    content = content.replace(old_init, new_init)
    changes += 1

# 7. init_db: skip other migrations when testing
if '        _migrate_schedules()\n        _migrate_conversations()' in content:
    # These should be indented under the if not _TESTING
    pass

# 8. FTS5 in init_db executescript
if 'memories_fts USING fts5' not in content:
    # Add FTS5 + triggers after idx_mem_type in the init_db CREATE TABLE script
    old_fts = 'CREATE INDEX IF NOT EXISTS idx_mem_type ON memories(type);'
    new_fts = '''CREATE INDEX IF NOT EXISTS idx_mem_type ON memories(type);

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
END;'''
    content = content.replace(old_fts, new_fts)
    changes += 1

# 9. mem_search with FTS5
old_search = """def mem_search(keyword: str = "") -> list:
    with db() as c:
        rs = c.execute(
            "SELECT * FROM memories WHERE content LIKE ? OR keywords LIKE ? "
            "ORDER BY importance DESC",
            (f"%{keyword}%", f"%{keyword}%")
        ).fetchall()
    return [dict(r) for r in rs]"""
new_search = """def mem_search(keyword: str = "", limit: int = 30) -> list:
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
    return [dict(r) for r in rs]"""
content = content.replace(old_search, new_search)
changes += 1

with open(app_path, 'w', encoding='utf-8') as f:
    f.write(content)
print(f"Applied {changes} changes to database.py")
