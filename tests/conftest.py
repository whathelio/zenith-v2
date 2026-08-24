"""pytest fixtures — 内存数据库 + 隔离测试环境"""
import os
import sys
import pytest
from pathlib import Path

# 确保 backend 可导入
PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))
sys.path.insert(0, str(PROJECT_DIR / "backend"))

# 全局设置测试模式
os.environ["ZENITH_TESTING"] = "1"


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_db():
    """会话结束后删除临时测试库（含 WAL/SHM 伴生文件），避免残留累积。"""
    yield
    try:
        from backend.database import _test_tmp_path
        for suffix in ("", "-wal", "-shm"):
            p = _test_tmp_path + suffix
            if os.path.exists(p):
                os.remove(p)
    except Exception:
        pass


@pytest.fixture(scope="function")
def test_db():
    """每个测试函数独立的内存数据库 — 完全隔离，跑完即销毁"""
    from backend.database import init_db, db as db_ctx

    init_db()

    # 返回一个可以直接用的连接（用于断言验证）
    with db_ctx() as conn:
        yield conn

    # 每个测试后重新 init（清空所有表）
    # 由于是 :memory:，无需清理，连接关闭即销毁


@pytest.fixture
def sample_memory(test_db):
    """预置一条测试记忆"""
    cur = test_db.cursor()
    cur.execute(
        "INSERT INTO memories (type, content, importance, keywords, created_at) "
        "VALUES ('fact', '用户使用 Python 编写自动化脚本', 4, 'python,自动化', datetime('now'))"
    )
    test_db.commit()
    return cur.lastrowid


@pytest.fixture
def sample_notes(test_db):
    """预置两条测试笔记"""
    cur = test_db.cursor()
    cur.executemany(
        "INSERT INTO notes (title, content, stage, status, created_at) VALUES (?, ?, ?, ?, datetime('now'))",
        [
            ("测试笔记1", "这是一条 raw 笔记", "raw", "confirmed"),
            ("测试笔记2", "这是一条 refined 笔记", "refined", "confirmed"),
        ]
    )
    test_db.commit()


@pytest.fixture
def sample_schedule(test_db):
    """预置一条测试日程"""
    cur = test_db.cursor()
    cur.execute(
        "INSERT INTO schedules (title, start_time, status, priority, created_at) "
        "VALUES ('项目评审会', '2026-07-30 14:00', 'confirmed', 'high', datetime('now'))"
    )
    test_db.commit()
    return cur.lastrowid
