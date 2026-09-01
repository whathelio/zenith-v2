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

    @pytest.fixture(scope="session")
    def engine():
        """memory_engine 模块，其底层 DB_PATH 已指向快照库。"""
        import backend.database as db

        db.DB_PATH = SNAPSHOT_DB
        from backend import memory_engine

        return memory_engine

    @pytest.fixture(scope="session")
    def dataset():
        with open(PILOT_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
