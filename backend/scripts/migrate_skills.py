"""C.5: 技能表迁移到记忆表 — 一次性执行脚本"""
import json, sqlite3, sys
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent.parent
DB_PATH = PROJECT_DIR / "data" / "zenith.db"

if not DB_PATH.exists():
    print("Database not found:", DB_PATH)
    sys.exit(1)

conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA foreign_keys=OFF")

# Step 1: Rebuild memories table with 'skill' type in CHECK constraint
print("1. Rebuilding memories table with skill type...")
conn.executescript("""
CREATE TABLE IF NOT EXISTS memories_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT CHECK(type IN ('personal_info','preference','event','decision','fact','experience','skill')),
    content TEXT,
    importance INTEGER DEFAULT 3,
    keywords TEXT,
    source_conv_id TEXT,
    recorded_at TEXT,
    distilled_from INTEGER DEFAULT NULL,
    created_at TEXT
);
INSERT INTO memories_new SELECT * FROM memories;
DROP TABLE memories;
ALTER TABLE memories_new RENAME TO memories;
CREATE INDEX IF NOT EXISTS idx_mem_type ON memories(type);
""")

# Step 2: Rebuild FTS5
print("2. Rebuilding FTS5 index...")
conn.executescript("""
DROP TABLE IF EXISTS memories_fts;
CREATE VIRTUAL TABLE memories_fts USING fts5(
    content, keywords, content='memories', content_rowid='id'
);
INSERT INTO memories_fts(rowid, content, keywords) SELECT id, content, keywords FROM memories;
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

# Step 3: Migrate skills to memories
print("3. Migrating skills to memories...")
skills = conn.execute("SELECT * FROM skills").fetchall()
moved = 0
for s in skills:
    tags = json.loads(s["tags"]) if s["tags"] else []
    tags.append("skill")
    content = f"Skill: {s['name']}\nTrigger: {s['trigger_scene']}\nSteps: {s['steps']}"
    importance = 3 if s["confirmed_by_user"] else 1
    conn.execute(
        "INSERT INTO memories (type, content, importance, keywords, source_conv_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("skill", content, importance, ",".join(tags), f"skill_{s['id']}", s["created_at"])
    )
    moved += 1
print(f"   Migrated {moved} skills")

# Step 4: Drop skills table
print("4. Dropping skills table...")
conn.execute("DROP TABLE skills")

conn.execute("PRAGMA foreign_keys=ON")
conn.commit()

# Step 5: Verify
print("5. Verification...")
cnt = conn.execute("SELECT COUNT(*) FROM memories WHERE type='skill'").fetchone()[0]
print(f"   Skill memories: {cnt}")
print("Migration complete.")
conn.close()
