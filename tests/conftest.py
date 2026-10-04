"""pytest fixtures — 内存数据库 + 隔离测试环境"""
import os
import shutil
import sys
import tempfile
import pytest
from pathlib import Path

# 确保 backend 可导入
PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))
sys.path.insert(0, str(PROJECT_DIR / "backend"))

# 全局设置测试模式
os.environ["ZENITH_TESTING"] = "1"

# 2026-09-16: 重定向配置目录到临时区（此前只重定向了 DB，漏了这一项）。
# 症状：test_api_chat 里 `PUT /api/settings {"model":"test-model","temperature":0.5}`
# 会**直接写进生产配置** config/config.yaml，实测该文件 mtime 随每次 pytest 变化，
# 把用户的 model 改成 "test-model"、temperature 改成 0.5。
# 做法：把生产 config.yaml 拷一份到临时目录 —— 测试读写的仍是同一份内容，但落点在临时区。
_TEST_CONF_DIR = Path(tempfile.gettempdir()) / "zenith_test_config"
try:
    _TEST_CONF_DIR.mkdir(parents=True, exist_ok=True)
    _prod_conf = PROJECT_DIR / "config" / "config.yaml"
    if _prod_conf.exists():
        shutil.copyfile(_prod_conf, _TEST_CONF_DIR / "config.yaml")
except Exception:
    pass
os.environ["ZENITH_CONFIG_DIR"] = str(_TEST_CONF_DIR)


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
    # 2026-09-15: memory_engine 的缓冲落盘文件也落在同一临时目录（= DB_PATH.parent），
    # 此前只清 .db / -wal / -shm，会在 %TEMP% 累积 memory_buffer.json 残留。
    try:
        from backend.memory_engine import _BUFFER_PATH as _mb
        for p in (str(_mb), str(_mb) + ".tmp"):
            if os.path.exists(p):
                os.remove(p)
    except Exception:
        pass
    # 2026-09-28（D16）：配置副本目录（见本文件 :22）此前**未清** —— 它是本会话自建的
    # 夹具，却不随会话结束消失。虽**不累积**（固定目录 + 每次 copyfile 覆盖写），
    # 但留着就是 %TEMP% 里一处恒定残留。下次 pytest 会用 mkdir + copyfile 原样重建，
    # 故删除无副作用。
    #
    # ⚠️ 安全前置（必留）：先断言目标**确实是「临时目录下的 zenith_test_config」**。
    #    宁可不清，也绝不能误伤生产配置目录 `config/` —— 那会把用户设置删掉。
    #
    # 权衡：本机装了火绒，它把 `os.remove` 重定向到回收站 → 本段会让每次 pytest 多
    # 1~2 条回收站记录（与上面几段既有清理同性质）。换来的是 %TEMP% 真正零残留。
    # 若要回退：删掉本段即可（不影响前面几段的清理）。
    try:
        _tmp_root = Path(tempfile.gettempdir()).resolve()
        _conf_target = Path(_TEST_CONF_DIR).resolve()
        if (_conf_target.parent == _tmp_root
                and _conf_target.name.startswith("zenith_test_config")
                and _conf_target.is_dir()):
            for _p in _conf_target.iterdir():
                if _p.is_file():
                    os.remove(_p)
            os.rmdir(_conf_target)
    except Exception:
        pass


@pytest.fixture(scope="function")
def test_db():
    """每个测试函数独立的内存数据库 — 完全隔离，跑完即销毁"""
    from backend.database import init_db, db as db_ctx

    init_db()

    # 2026-09-11 补上真正的隔离（此前 docstring 声称「完全隔离」，实际没有）：
    #   DB_PATH 并非 :memory:，而是**会话级共享的临时文件**（模块导入时 mkstemp 一次），
    #   init_db() 只建表、不清数据 → 上一个测试写入的记忆会残留到下一个测试。
    #   此前无感，是因为没有任何写入路径会因「库中已存在近似内容」而拒绝写入；
    #   自 mem_add 内置去重门禁后，残留会直接让后续 mem_add 返回 -2（未写入），
    #   表现为 mem_get(mid) 返回 None 这种与代码本身无关的诡异失败。
    with db_ctx() as conn:
        for tbl in ("memories", "notes"):
            try:
                conn.execute(f"DELETE FROM {tbl}")
            except Exception:
                pass

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
