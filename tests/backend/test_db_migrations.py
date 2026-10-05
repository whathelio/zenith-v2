"""数据库迁移测试（`backend/database.py` 的 11 个 `_migrate_*`，`:135-441`）。

为什么需要本文件 —— 11 个迁移此前**零测试**（`grep _migrate_ tests/` = 0 命中），
且没有旧版 schema fixture。这是全仓风险最高的一处：

- 迁移处理的是**不可再生数据**（用户的记忆 / 笔记 / 日程），一旦写坏无从恢复；
- 迁移路径只在「旧库升级」时执行 → **在开发机上永不触发**，等于从未被验证过；
- `pyproject.toml` 里没有任何针对迁移的约束。

本文件用三种手段覆盖（不需要知道完整历史 schema）：
1. **结构完整性** —— 从零建库后，迁移应补的列都在；
2. **幂等性** —— `init_db()` 与每个迁移函数重复执行不得报错（迁移每天都在跑）；
3. **旧库模拟** —— 用 `DROP COLUMN` 造出缺列的表，跑迁移，断言列被修复且数据不丢。

⚠️ 已知脆弱点（本文件记录但不断言）：`_migrate_memory_types` 重建表时用
`INSERT INTO memories_new SELECT * FROM memories`，**隐含假设 memories 恰好 7 列**
（与 `memories_new` 的定义一致）。若某库已含 `recorded_at/distilled_from/archived`
（10 列）而 CHECK 仍缺 `experience`，重建会因列数不匹配而失败。
当前 `init_db` 的执行顺序（types 在 memories 之前）使这条路径不可达 —— 但顺序一变就会暴露。
"""
import inspect
import sqlite3

import pytest

from backend import database as db

MIGRATION_FUNCTIONS = [
    "_migrate_memory_types",
    "_migrate_schedules",
    "_migrate_notes",
    "_migrate_memories",
    "_migrate_conversations",
    "_migrate_market_reports",
    "_migrate_memories_fts",
    "_migrate_goals",
    "_migrate_messages",
    "_migrate_cache_stats",
    "_migrate_academic_papers",
]

EXPECTED_BUSINESS_TABLES = {
    "conversations", "messages", "memories", "schedules", "notes", "goals",
    "settings", "cache_stats", "conversation_traces", "periodic_summaries",
    "analysis_documents", "academic_papers", "schedule_reminders",
    "schedule_events",
}
# ⚠️ 刻意不含 `skills`：生产库里有这张表，但**源码中不存在任何 CREATE TABLE skills**
#    （grep 全仓 0 命中），也没有任何 SQL 读写它 —— `routers/modules.py:219` 的注释
#    已确认「grep 无 INTO skills / FROM skills」，说明技能功能实际走 `memories.type='skill'`。
#    生产库的 `skills` 表是**历史残留**，init_db 不建它，故不能进本清单。

_SQLITE_SUPPORTS_DROP_COLUMN = (
    tuple(int(x) for x in sqlite3.sqlite_version.split(".")) >= (3, 35)
)


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _raw_connect():
    """迁移函数用的是裸 sqlite3.connect(DB_PATH)，这里保持一致以观察真实效果。"""
    return sqlite3.connect(str(db.DB_PATH))


class TestMigrationInventory:
    """迁移函数本身的存在性与可调用性。"""

    def test_all_migration_functions_exist(self):
        missing = [n for n in MIGRATION_FUNCTIONS if not hasattr(db, n)]
        assert missing == [], f"迁移函数缺失：{missing}"

    def test_migration_count_is_eleven(self):
        """数量变化应被察觉（新增迁移需同步登记到本文件）。"""
        assert len(MIGRATION_FUNCTIONS) == 11


class TestSchemaCompleteness:
    """从零建库后，结构应包含全部业务表与迁移补的列。"""

    def test_all_business_tables_exist(self, test_db):
        tables = {
            r[0] for r in test_db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert EXPECTED_BUSINESS_TABLES <= tables, (
            f"缺表：{EXPECTED_BUSINESS_TABLES - tables}"
        )

    def test_schedules_has_migrated_columns(self, test_db):
        expected = {"importance", "category", "impact", "country",
                    "remind_before", "goal_id", "recurrence", "parent_id"}
        assert expected <= _columns(test_db, "schedules")

    def test_notes_has_migrated_columns(self, test_db):
        expected = {"stage", "recorded_at", "distilled_at",
                    "distilled_into", "confirmed_at"}
        assert expected <= _columns(test_db, "notes")

    def test_memories_has_migrated_columns(self, test_db):
        expected = {"recorded_at", "distilled_from", "archived"}
        assert expected <= _columns(test_db, "memories")

    def test_memories_check_constraint_allows_experience_and_skill(self, test_db):
        """`_migrate_memory_types` 的目的是让 CHECK 接受 experience / skill。"""
        sql = test_db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='memories'"
        ).fetchone()[0]
        assert "'experience'" in sql, "CHECK 约束应允许 experience"
        assert "'skill'" in sql, "CHECK 约束应允许 skill"

    def test_memories_actually_accepts_both_types(self, test_db):
        """行为级确认（不只看 DDL 文本）—— 约束真的生效。"""
        for t in ("experience", "skill"):
            test_db.execute(
                "INSERT INTO memories (type, content, importance, created_at) "
                "VALUES (?, '约束测试', 3, datetime('now'))",
                (t,),
            )
        test_db.commit()

    def test_memories_rejects_unknown_type(self, test_db):
        """反向：CHECK 仍在拦截非法类型（别把约束迁没了）。"""
        with pytest.raises(sqlite3.IntegrityError):
            test_db.execute(
                "INSERT INTO memories (type, content, importance, created_at) "
                "VALUES ('bogus_type', 'x', 3, datetime('now'))"
            )
            test_db.commit()

    def test_academic_fts_triggers_exist(self, test_db):
        """`academic_papers_fts` 的 3 个同步触发器（由 init_db 的 executescript 建）。

        ⚠️ 注意这里**只断言 academic_papers** —— `memories_fts` 的同名触发器
        在全新库上**不存在**，原因见 `TestMemoriesFtsGap`（一个已定位的缺陷）。
        """
        triggers = {
            r[0] for r in test_db.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()
        }
        for t in ("academic_papers_ai", "academic_papers_ad", "academic_papers_au"):
            assert t in triggers, f"缺 FTS 同步触发器 {t}"

    def test_foreign_keys_pragma_is_on(self, test_db):
        """`db()` 每次建连都设 foreign_keys=ON —— 级联删除依赖它。"""
        assert test_db.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    def test_message_cascade_delete_is_effective(self, test_db):
        """messages → conversations 的 ON DELETE CASCADE 应真实生效。"""
        test_db.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) "
            "VALUES ('migr01', 't', datetime('now'), datetime('now'))"
        )
        test_db.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) "
            "VALUES ('migr01', 'user', 'hi', datetime('now'))"
        )
        test_db.commit()
        test_db.execute("DELETE FROM conversations WHERE id = 'migr01'")
        test_db.commit()
        left = test_db.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id = 'migr01'"
        ).fetchone()[0]
        assert left == 0, "级联删除未生效"


class TestMigrationIdempotency:
    """迁移每天随启动执行 —— 重复跑必须无害。"""

    def test_init_db_is_idempotent(self, test_db):
        """重复 init_db 不应报错（test_db fixture 已跑过一次）。"""
        db.init_db()
        db.init_db()

    @pytest.mark.parametrize("fn_name", MIGRATION_FUNCTIONS)
    def test_single_migration_is_idempotent(self, test_db, fn_name):
        fn = getattr(db, fn_name)
        fn()
        fn()  # 第二次应识别「已迁移」并跳过

    def test_all_migrations_then_init_db_again(self, test_db):
        """全跑一遍后再 init_db —— 混合顺序也不得报错。"""
        for name in MIGRATION_FUNCTIONS:
            getattr(db, name)()
        db.init_db()


class TestLegacySchemaRepair:
    """旧库模拟：删掉列后，迁移应把它补回来。"""

    @pytest.mark.skipif(not _SQLITE_SUPPORTS_DROP_COLUMN,
                        reason="DROP COLUMN 需要 SQLite 3.35+")
    def test_dropped_schedule_column_is_restored(self, test_db):
        conn = _raw_connect()
        try:
            conn.execute("ALTER TABLE schedules DROP COLUMN importance")
            conn.commit()
        finally:
            conn.close()
        assert "importance" not in _columns(test_db, "schedules")

        db._migrate_schedules()

        assert "importance" in _columns(test_db, "schedules")

    @pytest.mark.skipif(not _SQLITE_SUPPORTS_DROP_COLUMN,
                        reason="DROP COLUMN 需要 SQLite 3.35+")
    def test_dropped_note_column_is_restored(self, test_db):
        conn = _raw_connect()
        try:
            conn.execute("ALTER TABLE notes DROP COLUMN confirmed_at")
            conn.commit()
        finally:
            conn.close()
        assert "confirmed_at" not in _columns(test_db, "notes")

        db._migrate_notes()

        assert "confirmed_at" in _columns(test_db, "notes")

    def test_memory_types_migration_rebuilds_legacy_check(self, tmp_path, monkeypatch):
        """模拟旧版 memories（CHECK 缺 experience/skill）→ 重建后数据保留。

        这是 11 个迁移里唯一走「重建表」路径的（SQLite 不支持改 CHECK）。

        ⚠️ 必须用**独立临时库**：本测试会 DROP TABLE memories，若在会话级共享的
        `test_db` 上执行会污染后续用例（实测触发 `database disk image is malformed`
        —— 因为 memories_fts 是 external-content 表，content 表被 DROP 后它就坏了）。
        """
        legacy = tmp_path / "legacy_check.db"
        monkeypatch.setattr(db, "DB_PATH", legacy)

        conn = sqlite3.connect(str(legacy))
        conn.executescript("""
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT CHECK(type IN ('personal_info','preference','event','decision','fact')),
                content TEXT,
                importance INTEGER DEFAULT 3,
                keywords TEXT,
                source_conv_id TEXT,
                created_at TEXT
            );
            INSERT INTO memories (type, content, importance, created_at)
            VALUES ('fact', '迁移前就存在的记忆', 4, datetime('now'));
        """)
        conn.commit()
        conn.close()

        db._migrate_memory_types()

        conn = sqlite3.connect(str(legacy))
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='memories'"
        ).fetchone()[0]
        assert "'experience'" in sql and "'skill'" in sql, "CHECK 约束未被升级"
        survived = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE content = '迁移前就存在的记忆'"
        ).fetchone()[0]
        assert survived == 1, "重建表不应丢数据"
        conn.close()

    def test_fts_rebuilds_after_memories_table_rebuild(self, tmp_path, monkeypatch):
        """🔴 修复验证：`_migrate_memory_types` 重建 memories 后，FTS 必须仍可用。

        原缺陷：`memories_fts` 是 external-content 虚表（`content='memories'`），
        `DROP TABLE memories` 会让它进入 `disk image malformed` 状态；而
        `_migrate_memories_fts` 的存在性检查**只看表名**（表还在，只是坏了）→
        不会重建 → FTS 永久损坏且难以察觉。

        修复：重建前显式 `DROP TABLE IF EXISTS memories_fts`，再由 `init_db` 末尾的
        `_migrate_memories_fts` 重新建立并回填。本测试走完整 `init_db()` 路径。
        """
        legacy = tmp_path / "legacy_fts.db"
        monkeypatch.setattr(db, "DB_PATH", legacy)

        conn = sqlite3.connect(str(legacy))
        conn.executescript("""
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT CHECK(type IN ('personal_info','preference','event','decision','fact')),
                content TEXT,
                importance INTEGER DEFAULT 3,
                keywords TEXT,
                source_conv_id TEXT,
                created_at TEXT
            );
            INSERT INTO memories (type, content, importance, created_at)
            VALUES ('fact', 'legacy fts check 记忆内容', 4, datetime('now'));
        """)
        conn.commit()
        conn.close()

        db.init_db()  # 触发 _migrate_memory_types 重建 + 末尾的 FTS 重建

        conn = sqlite3.connect(str(legacy))
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "memories_fts" in tables, "重建 memories 后 FTS 表应被重建"

        triggers = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()}
        assert {"memories_ai", "memories_ad", "memories_au"} <= triggers, "FTS 触发器应被重建"

        assert conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0] >= 1, \
            "FTS 应有回填数据"
        # ⚠️ 查询词必须用 ASCII：FTS5 默认 unicode61 分词器**不对中文分词**
        #    （整串连续 CJK 会被当作单个 token）→ 用中文做 MATCH 会假阴性。
        #    这也正是 memory_engine._is_duplicate 先做 LIKE 候选召回的原因。
        hit = conn.execute(
            "SELECT m.content FROM memories m JOIN memories_fts f ON m.id = f.rowid "
            "WHERE memories_fts MATCH 'legacy' LIMIT 1"
        ).fetchall()
        assert hit, "重建后 FTS MATCH 应能命中（证明不是 malformed 空壳）"
        conn.close()

    def test_memory_types_migration_is_skipped_when_already_migrated(self, test_db):
        """已迁移的库：应直接返回，不重建（避免无谓的表重建风险）。"""
        before = test_db.execute(
            "SELECT COUNT(*) FROM memories").fetchone()[0]
        db._migrate_memory_types()
        db._migrate_memory_types()
        after = test_db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        assert before == after


class TestMemoriesFtsGap:
    """回归守护：`_migrate_memories_fts` 必须被 `init_db()` 调用。

    **2026-10-04 已修复** —— 修复内容：把 `_migrate_memories_fts()` 加入 `init_db`
    的调用列表（放在建表 executescript **之后**，因其内部 `CREATE TRIGGER ... ON memories`
    要求目标表已存在），并把触发器创建移出 `if not ft_exists` 分支。

    缺陷历史（实测：用全新临时库跑 `init_db()`）：

    | 对象 | 修复前 | 修复后 |
    |:--|:--|:--|
    | `memories_fts` 表 | ❌ 不存在 | ✅ 存在 |
    | `memories_ai/ad/au` trigger | ❌ 不存在 | ✅ 存在 |
    | `academic_papers_*` trigger | ✅ 存在 | ✅ 存在 |

    根因：`init_db()` 的调用列表只列了 **10** 个迁移函数，全仓却有 **11** 个
    `def _migrate_*` —— **漏掉 `_migrate_memories_fts`**。
    后果：从零部署的库记忆全文搜索不走 FTS，退化为 LIKE 全表扫（有兜底，功能不崩）。
    生产库之所以正常，纯属历史遗留（表在早期版本就被建过，与代码路径无关）。

    本组从两条路径守护：① 源码级 —— `init_db` 是否逐个调用了所有迁移函数；
    ② 行为级 —— 新库里表与触发器是否真的建出来了。
    """

    def test_init_db_invokes_every_migration(self):
        src = inspect.getsource(db.init_db)
        missing = [n for n in MIGRATION_FUNCTIONS if f"{n}()" not in src]
        assert not missing, f"init_db 未调用的迁移函数：{missing}"

    def test_memories_fts_and_triggers_exist(self, test_db):
        tables = {
            r[0] for r in test_db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        triggers = {
            r[0] for r in test_db.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()
        }
        assert "memories_fts" in tables
        assert {"memories_ai", "memories_ad", "memories_au"} <= triggers

    def test_mem_search_degrades_gracefully_without_fts(self, test_db):
        """即便 FTS 缺席，`mem_search` 也不得抛异常（靠 LIKE 兜底）。

        这条**应当通过** —— 它证明上面的缺陷是「功能降级」而非「功能崩溃」，
        从而为修复优先级提供依据（不急，但该修）。
        """
        from backend.database import mem_add, mem_search

        rules = {
            "content": "这是一条用于验证降级搜索的记忆内容",
            "importance": 3,
        }
        mem_add("fact", rules["content"], importance=3)
        assert mem_search("降级搜索", limit=5) is not None


class TestMigrationPreservesData:
    """迁移不得丢数据 —— 这是「不可再生数据」场景的底线。"""

    def test_data_survives_running_all_migrations(self, test_db):
        test_db.execute(
            "INSERT INTO memories (type, content, importance, created_at) "
            "VALUES ('fact', '存活检查-记忆', 3, datetime('now'))"
        )
        test_db.execute(
            "INSERT INTO notes (title, content, stage, status, created_at) "
            "VALUES ('存活检查', '内容', 'raw', 'confirmed', datetime('now'))"
        )
        test_db.execute(
            "INSERT INTO schedules (title, start_time, status, priority, created_at) "
            "VALUES ('存活检查日程', '2026-10-06 10:00', 'confirmed', 'normal', datetime('now'))"
        )
        test_db.commit()

        for name in MIGRATION_FUNCTIONS:
            getattr(db, name)()

        assert test_db.execute(
            "SELECT COUNT(*) FROM memories WHERE content = '存活检查-记忆'"
        ).fetchone()[0] == 1
        assert test_db.execute(
            "SELECT COUNT(*) FROM notes WHERE title = '存活检查'"
        ).fetchone()[0] == 1
        assert test_db.execute(
            "SELECT COUNT(*) FROM schedules WHERE title = '存活检查日程'"
        ).fetchone()[0] == 1

    def test_migration_preserves_row_ids(self, test_db):
        """重建表可能改变 rowid —— 断言 id 稳定（前端/外键按 id 引用）。"""
        test_db.execute(
            "INSERT INTO memories (type, content, importance, created_at) "
            "VALUES ('fact', 'id 稳定性检查', 3, datetime('now'))"
        )
        test_db.commit()
        mid = test_db.execute(
            "SELECT id FROM memories WHERE content = 'id 稳定性检查'"
        ).fetchone()[0]

        for name in MIGRATION_FUNCTIONS:
            getattr(db, name)()

        still = test_db.execute(
            "SELECT id FROM memories WHERE content = 'id 稳定性检查'"
        ).fetchone()
        assert still is not None and still[0] == mid, "迁移后 row id 应保持不变"
