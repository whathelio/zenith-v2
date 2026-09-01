"""pilot 数据集完整性检查（C 阶段）。

可行性门槛（与终版计划一致）：>=60% 的查询拥有至少 1 条可标注候选。
"""
from __future__ import annotations


def test_dataset_shape(dataset):
    n = len(dataset["queries"])
    assert 10 <= n <= 50, f"pilot/正式集应在 10-50 条，当前 {n}"


def test_queries_unique(dataset):
    texts = [q["query"] for q in dataset["queries"]]
    assert len(texts) == len(set(texts)), "查询文本重复"


def test_feasibility_proxy(dataset):
    with_cands = sum(1 for q in dataset["queries"] if q["candidates"])
    rate = with_cands / len(dataset["queries"])
    assert rate >= 0.6, f"候选率 {rate:.0%} 低于 60% 可行性门槛"


def test_candidate_ids_exist_in_snapshot(dataset, engine):
    import backend.database as db

    missing = []
    for q in dataset["queries"]:
        for c in q["candidates"]:
            if not db.mem_get(c["memory_id"]):
                missing.append((q["id"], c["memory_id"]))
    assert not missing, f"快照库中不存在的候选记忆: {missing[:5]}"
