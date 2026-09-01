"""基线草案指标（C 阶段）——用机器初标候选当 draft 相关集，测现有 n-gram 召回。

重要：draft 标签（FTS ∪ n-gram 候选）尚未人工确认，本测试只输出参考数字并落盘
baseline_draft.json；不对 recall 设硬阈值。人工确认标签后，同口径重跑即可得正式基线。
"""
from __future__ import annotations

import json

import conftest


def _recall_at_k(retrieved: list[int], relevant: list[int], k: int) -> float:
    if not relevant:
        return 1.0
    hits = set(relevant) & set(retrieved[:k])
    return len(hits) / len(relevant)


def _precision_at_k(retrieved: list[int], relevant: list[int], k: int) -> float:
    top = retrieved[:k]
    if not top:
        return 0.0
    return len(set(top) & set(relevant)) / len(top)


def test_baseline_draft(engine, dataset):
    import backend.database as db

    per_query = []
    for q in dataset["queries"]:
        relevant = [c["memory_id"] for c in q["candidates"]]
        memories, _notes = engine.search_related_items(
            q["query"], limit=15, include_notes=False
        )
        retrieved = [m["id"] for m in memories if m.get("type") != "skill"]

        row = {
            "id": q["id"],
            "query": q["query"],
            "hit_count": q.get("hit_count"),
            "n_relevant": len(relevant),
            "n_retrieved": len(retrieved),
            "recall": {str(k): _recall_at_k(retrieved, relevant, k) for k in (1, 3, 5, 10)},
            "precision": {str(k): _precision_at_k(retrieved, relevant, k) for k in (1, 3, 5, 10)},
        }

        # 1500 字符注入截断口径（draft 版）
        injection = engine.build_memory_injection(q["query"])
        missing_ids = []
        for cid in relevant:
            m = db.mem_get(cid)
            if not m:
                continue
            probe = (m.get("content") or "").strip()[:60]
            if probe and probe not in injection:
                missing_ids.append(cid)
        row["injection_chars"] = len(injection)
        row["candidates_missing_from_injection"] = missing_ids
        row["truncated"] = bool(missing_ids) and bool(injection)
        per_query.append(row)

    n = len(per_query)
    metrics = {
        "n_queries": n,
        "avg_recall": {
            str(k): round(sum(r["recall"][str(k)] for r in per_query) / n, 4)
            for k in (1, 3, 5, 10)
        },
        "avg_precision": {
            str(k): round(sum(r["precision"][str(k)] for r in per_query) / n, 4)
            for k in (1, 3, 5, 10)
        },
        "query_level_truncation_rate": round(
            sum(1 for r in per_query if r["truncated"]) / n, 4
        ),
        "item_level_truncation_rate": round(
            sum(len(r["candidates_missing_from_injection"]) for r in per_query)
            / max(1, sum(r["n_relevant"] for r in per_query)),
            4,
        ),
        "label_status": "draft（机器初标，未人工确认）",
    }

    out = conftest.EVAL_DATA / "baseline_draft.json"
    out.write_text(
        json.dumps({"metrics": metrics, "per_query": per_query}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(metrics, ensure_ascii=False, indent=2))

    # 只做数值合法性断言，不设 recall 阈值（draft 标签）
    for k in (1, 3, 5, 10):
        assert 0.0 <= metrics["avg_recall"][str(k)] <= 1.0
        assert 0.0 <= metrics["avg_precision"][str(k)] <= 1.0
    assert 0.0 <= metrics["query_level_truncation_rate"] <= 1.0
