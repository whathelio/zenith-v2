"""WAL 保活连接的回归守卫（2026-09-23）。

为什么需要这个测试：本机存在**环境层**的「删除改道回收站」（D: 卷内任意目录，
与进程环境变量无关）。WAL 模式下 SQLite 会在最后一个连接关闭时删除
`-wal`/`-shm`，于是「每次 db 调用 = 回收站 +2 条」——2026-09-22 一次运行实测
留下 490 条、同路径重复 245 次。修法是 `database._keep_wal_alive()` 持有常驻连接。

本测试只断言**机制在位**（不依赖本机回收站行为，任何机器都能跑）：
若有人把保活逻辑删掉，这里会失败。
"""
from pathlib import Path



def test_keeper_installed_on_conn(tmp_path, monkeypatch):
    """_conn() 首次调用后必须存在保活连接（WAL 模式）。"""
    import backend.database as database

    monkeypatch.setattr(database, "_TESTING", False)
    monkeypatch.setattr(database, "_JOURNAL_MODE", "WAL")
    monkeypatch.setattr(database, "DB_PATH", Path(tmp_path) / "keeper_guard.db")
    database._release_keeper()

    with database.db() as c:
        c.execute("SELECT 1").fetchone()

    try:
        assert database._KEEPER is not None, "WAL 保活连接未建立 —— 回收站会被 -wal/-shm 灌满"
        assert database._KEEPER_PATH == str(database.DB_PATH)
        # 保活连接必须真的在跑 SQL，而不是个空壳
        assert database._KEEPER.execute("SELECT 1").fetchone()[0] == 1
    finally:
        database._release_keeper()


def test_keeper_follows_db_path_switch(tmp_path, monkeypatch):
    """DB_PATH 被改写（如 eval_recall 指向快照库）后，保活连接必须跟着换。"""
    import backend.database as database

    monkeypatch.setattr(database, "_TESTING", False)
    monkeypatch.setattr(database, "_JOURNAL_MODE", "WAL")
    p1 = Path(tmp_path) / "a.db"
    p2 = Path(tmp_path) / "b.db"
    database._release_keeper()

    try:
        monkeypatch.setattr(database, "DB_PATH", p1)
        with database.db() as c:
            c.execute("SELECT 1").fetchone()
        assert database._KEEPER_PATH == str(p1)

        monkeypatch.setattr(database, "DB_PATH", p2)
        with database.db() as c:
            c.execute("SELECT 1").fetchone()
        assert database._KEEPER_PATH == str(p2), "换库后保活连接未跟随 —— 会保活错对象"
    finally:
        database._release_keeper()


def test_keeper_skipped_in_testing_mode(monkeypatch):
    """测试模式（MEMORY journal）不应建立保活连接，避免误持文件句柄。"""
    import backend.database as database

    monkeypatch.setattr(database, "_TESTING", True)
    monkeypatch.setattr(database, "_JOURNAL_MODE", "MEMORY")
    database._release_keeper()

    database._keep_wal_alive()
    assert database._KEEPER is None
