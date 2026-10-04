"""召回评估集 conftest —— 把 backend 指向评估快照库，不碰生产 zenith.db。"""
import json
import os
import pathlib
import sys

try:
    import pytest
except ModuleNotFoundError:  # 无 pytest 环境由 run_eval.py 回退执行
    pytest = None

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

EVAL_DATA = pathlib.Path(os.environ.get("ZENITH_EVAL_DATA", r"D:\dshs\eval_recall"))
SNAPSHOT_DB = pathlib.Path(os.environ.get("ZENITH_EVAL_DB", str(EVAL_DATA / "snapshot.db")))
PILOT_JSON = EVAL_DATA / "pilot.json"

pytestmark = (
    pytest.mark.skipif(
        not SNAPSHOT_DB.exists() or not PILOT_JSON.exists(),
        reason="评估快照缺失：先运行 build_dataset.py",
    )
    if pytest is not None
    else None
)


if pytest is not None:

    def _ensure_schema(db_path: pathlib.Path) -> None:
        """把冻结快照库对齐到当前 schema（仅做纯增量加列，幂等）。

        2026-09-11：快照库构建于 2026-09-01，不带之后新增的 `memories.archived` 列，
        导致检索 SQL 报 `no such column: archived` —— 与被测代码无关，是夹具产物陈旧。

        刻意**不**调用 `db.init_db()`：那会连带跑 created_at 回填等**数据**修复，
        改变评估快照的内容 → 污染 recall 基线。这里只加列，不动数据。
        """
        import sqlite3

        if not db_path.exists():
            return
        conn = sqlite3.connect(str(db_path))
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
            if "archived" not in cols:
                conn.execute("ALTER TABLE memories ADD COLUMN archived INTEGER DEFAULT 0")
                conn.commit()
        finally:
            conn.close()

    def _ensure_not_wal(db_path: pathlib.Path) -> None:
        """确保评估快照**不是 WAL 模式**（2026-09-12）。

        背景：本机装了火绒，它把**所有删除操作**都重定向到回收站 ——
        实测连 `bash rm` 删一个普通文件都会进回收站。

        WAL 模式下 SQLite **每次打开连接都要创建 `-wal`/`-shm`**（哪怕只是
        只读查询），最后一个连接关闭时再把它们删掉 —— 于是这些文件被逐次
        灌进回收站。`test_baseline_draft.py` 在循环里逐条 `db.mem_get(cid)`，
        单次运行约 750 次连接开关 → **一次产出 1506 条**回收站记录，
        这就是「回收站堆积到清空要等很久」的真凶。

        这里先把**库头**从 WAL 摘掉（`journal_mode` 里只有 WAL 是持久属性，
        写掉即永久生效），免得每个新连接还要付一次「退出 WAL」的代价。
        连接级的模式由 `backend.database._JOURNAL_MODE` 统一控制 ——
        测试用 MEMORY（回滚日志放内存，不落任何磁盘临时文件），
        生产仍是 WAL（行为不变）。

        实测：修复后单次运行回收站增量 **0**（修复前 1506）。
        """
        import sqlite3

        if not db_path.exists():
            return
        conn = sqlite3.connect(str(db_path))
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            if str(mode).lower() == "wal":
                new = conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
                print(
                    f"[eval_recall] journal_mode: {mode} -> {new}"
                    "（退出 WAL，避免 -wal/-shm 灌爆回收站）"
                )
        finally:
            conn.close()

    @pytest.fixture(scope="session")
    def engine():
        """memory_engine 模块，其底层 DB_PATH 已指向快照库。"""
        import backend.database as db

        db.DB_PATH = SNAPSHOT_DB
        _ensure_not_wal(SNAPSHOT_DB)
        _ensure_schema(SNAPSHOT_DB)
        from backend import memory_engine

        return memory_engine

    @pytest.fixture(scope="session")
    def dataset():
        with open(PILOT_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
