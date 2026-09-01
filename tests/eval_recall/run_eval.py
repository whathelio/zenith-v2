"""无 pytest 环境下的评估运行器（C 阶段）。

等价于 `pytest tests/eval_recall`，直接调用同目录测试函数；
pytest 可用时请优先用 pytest 跑，本文件仅作沙箱/最小环境回退。
"""
from __future__ import annotations

import json
import sys
import traceback

import conftest
import test_baseline_draft
import test_dataset_integrity


def _fixtures():
    import backend.database as db

    db.DB_PATH = conftest.SNAPSHOT_DB
    from backend import memory_engine

    with open(conftest.PILOT_JSON, "r", encoding="utf-8") as f:
        dataset = json.load(f)
    return memory_engine, dataset


def main() -> int:
    engine, dataset = _fixtures()
    tests = [
        ("dataset_shape", test_dataset_integrity.test_dataset_shape, (dataset,)),
        ("queries_unique", test_dataset_integrity.test_queries_unique, (dataset,)),
        ("feasibility_proxy", test_dataset_integrity.test_feasibility_proxy, (dataset,)),
        ("candidate_ids_exist", test_dataset_integrity.test_candidate_ids_exist_in_snapshot, (dataset, engine)),
        ("baseline_draft", test_baseline_draft.test_baseline_draft, (engine, dataset)),
    ]
    failed = []
    for name, fn, args in tests:
        try:
            fn(*args)
            print(f"PASS  {name}")
        except AssertionError as e:
            failed.append(name)
            print(f"FAIL  {name}: {e}")
        except Exception:
            failed.append(name)
            print(f"ERROR {name}:\n{traceback.format_exc()}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
