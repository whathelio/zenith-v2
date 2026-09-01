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


def _normalize(s: str) -> str:
    import re

    return re.sub(r"\s+", "", s)


def _probe_included(injection: str, memory: dict) -> tuple[bool, str]:
    """截断探针：优先 [ID:n] 锚点；注入未带锚点时退化为归一化内容前缀匹配。"""
    mid = memory.get("id")
    anchor = f"[ID:{mid}]"
    if anchor in injection:
        return True, "anchor"
    content = _normalize(memory.get("content") or "")
    probe = content[:60]
    if probe and probe in _normalize(injection):
        return True, "normalized-fallback"
    return False, "normalized-fallback"


def test_baseline_draft(engine, dataset):
    import backend.database as db

    per_query = []
    anchor_supported = False
    for q in dataset["queries"]:
        relevant = [c["memory_id"] for c in q["candidates"]]
        ngram_only = [c["memory_id"] for c in q["candidates"] if c["sources"] == ["ngram"]]
        fts_only = [c["memory_id"] for c in q["candidates"] if c["sources"] == ["fts"]]
        both = [c["memory_id"] for c in q["candidates"] if len(c["sources"]) >= 2]

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
            # 偏置边界：被测系统本身是 n-gram，ngram_only 集对其天然偏乐观；
            # fts_only 集相对独立，是更可信的 draft 下界。
            "recall_ngram_only": {str(k): _recall_at_k(retrieved, ngram_only, k) for k in (1, 3, 5, 10)},
            "recall_fts_only": {str(k): _recall_at_k(retrieved, fts_only, k) for k in (1, 3, 5, 10)},
            "recall_both": {str(k): _recall_at_k(retrieved, both, k) for k in (1, 3, 5, 10)},
        }

        injection = engine.build_memory_injection(q["query"])
        if "[ID:" in injection:
            anchor_supported = True
        missing_ids = []
        for cid in relevant:
            m = db.mem_get(cid)
            if not m:
                continue
            ok, _mode = _probe_included(injection, m)
            if not ok:
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
        "avg_recall_ngram_only": {
            str(k): round(sum(r["recall_ngram_only"][str(k)] for r in per_query) / n, 4)
            for k in (1, 3, 5, 10)
        },
        "avg_recall_fts_only": {
            str(k): round(sum(r["recall_fts_only"][str(k)] for r in per_query) / n, 4)
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
        "probe_mode": "anchor" if anchor_supported else "normalized-fallback",
        "label_status": "draft（机器初标，未人工确认；recall 存在系统性乐观偏差，正式基线以人工确认为准）",
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
        assert 0.0 <= metrics["avg_recall_ngram_only"][str(k)] <= 1.0
        assert 0.0 <= metrics["avg_recall_fts_only"][str(k)] <= 1.0
    assert 0.0 <= metrics["query_level_truncation_rate"] <= 1.0
