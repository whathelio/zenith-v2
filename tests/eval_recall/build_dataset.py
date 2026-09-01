"""C 阶段：构建召回评估 pilot 集（10-20 条）+ 快照库。

用法：
    python tests/eval_recall/build_dataset.py

产物（均在 D:\\dshs\\eval_recall\\，可用 ZENITH_EVAL_DATA 覆盖）：
    snapshot.db       生产 zenith.db 的一致性快照（含 WAL 回放）
    pilot.json        查询 + 机器初标候选（label_status=draft，待人工确认）
    PILOT_REVIEW.md   供人工勾选确认的审查表
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import sqlite3
import sys
from datetime import datetime

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# 生产库路径：默认按仓库根推导，可用 ZENITH_DB_PATH 覆盖（不硬编码绝对路径）
PROD_DB = pathlib.Path(os.environ.get("ZENITH_DB_PATH", str(REPO_ROOT / "data" / "zenith.db")))
EVAL_DATA = pathlib.Path(os.environ.get("ZENITH_EVAL_DATA", r"D:\dshs\eval_recall"))
SNAPSHOT_DB = pathlib.Path(os.environ.get("ZENITH_EVAL_DB", str(EVAL_DATA / "snapshot.db")))

TARGET_N = int(os.environ.get("ZENITH_EVAL_N", "32"))  # 正式集 30-50 区间取 32
HALF_N = TARGET_N // 2
MAX_CANDIDATES = 10    # 每条查询给人工审查的候选上限

FILLER_PREFIXES = ("继续", "好的", "嗯", "哦", "行", "收到", "谢谢", "再见", "你好", "在吗")

# 太通用的片段不计入特异性打分（仍可参与 FTS 标注）
GENERIC_SEGMENTS = {
    "回复", "总结", "记忆", "笔记", "删除", "合并", "关键词", "项目", "开始", "对话",
    "问题", "原因", "部分", "最近", "一个", "一些", "需要", "可以", "今天", "一下",
    "不能", "现在", "两个", "什么", "怎么", "为什么", "哪个", "哪些", "是否", "不要",
    "帮我", "zenith", "文件", "内容", "处理", "系统", "数据", "之前", "时候", "然后",
    "相关", "重新", "生成", "或者", "以及", "完整", "方案", "设计", "优化", "功能",
    "时间", "信息", "谢谢", "没有", "办法", "泄露", "建议", "决定",
}

INTERROGATIVE_RE = re.compile(r"[?？]|吗|呢|什么|怎么|为什么|哪些|哪个|如何|是否|之前|上次|决定|策略|计划|偏好|喜欢|优先级")


def make_snapshot() -> None:
    """一致性快照：主库 + WAL/SHM（若存在）。打开快照时 SQLite 自动回放 WAL。"""
    EVAL_DATA.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PROD_DB, SNAPSHOT_DB)
    for suffix in ("-wal", "-shm"):
        src = pathlib.Path(str(PROD_DB) + suffix)
        if src.exists():
            shutil.copy2(src, str(SNAPSHOT_DB) + suffix)
    print(f"[snapshot] {PROD_DB} -> {SNAPSHOT_DB} ({SNAPSHOT_DB.stat().st_size} bytes)")


def load_messages() -> list[dict]:
    """从快照读取候选用户消息（只读）。"""
    con = sqlite3.connect(f"file:{SNAPSHOT_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, conversation_id, content, created_at FROM messages "
        "WHERE role='user' AND archived=0 ORDER BY created_at"
    ).fetchall()
    mem_rows = con.execute("SELECT id, content, keywords FROM memories").fetchall()
    con.close()

    mems = [dict(r) for r in mem_rows]
    out = []
    for r in rows:
        d = dict(r)
        text = (d.get("content") or "").strip().replace("\n", " ")
        if not (6 <= len(text) <= 80):
            continue
        if not re.search(r"[\u4e00-\u9fff]", text):
            continue
        if re.search(r"https?://|\.py\b|\.json\b|SELECT |import |def ", text, re.I):
            continue
        if any(text.startswith(p) for p in FILLER_PREFIXES):
            continue
        # 排除“回复格式指令”类元对话与纯泛化总结指令
        if re.search(r'[\'"`]', text):
            continue
        if re.match(r"^(回复|请回复|总结一下|请总结|今天关键词)", text):
            continue
        # 正式集放宽：非问句但具主题特异性的指令也纳入（标注阶段由人工判定相关性）
        d["text"] = text
        d["hit_ids"] = _score_hits(text, mems)
        d["hit_count"] = len(d["hit_ids"])
        d["spec_count"] = len(d["hit_ids"])  # _score_hits 已排除通用片段
        out.append(d)
    return out


def _score_hits(text: str, mems: list[dict]) -> set[int]:
    """独立于被测召回系统的初筛：非通用字符片段 LIKE 命中（仅用于选题，不用于标注）。"""
    from backend.memory_engine import _extract_keywords

    hits: set[int] = set()
    segs = [s for s in _extract_keywords(text)[:6] if s.lower() not in GENERIC_SEGMENTS]
    if len(segs) < 1:
        return set()
    for seg in segs:
        for m in mems:
            c = (m.get("content") or "")[:400]
            k = (m.get("keywords") or "")[:200]
            if seg and (seg in c or seg in k):
                hits.add(m["id"])
    return hits


def select_queries(messages: list[dict]) -> list[dict]:
    """按月分桶、每桶按特异性命中数选一半，保证时间跨度的‘隔月’结构。

    特异性带：2 <= hit_count <= 60 优先（太泛的命中>60 说明查询是宽泛指令；
    命中<2 说明没有可标记忆，降级补充）。
    """
    created = [m["created_at"] for m in messages]
    created.sort()
    median = created[len(created) // 2]
    half1 = [m for m in messages if m["created_at"] < median]
    half2 = [m for m in messages if m["created_at"] >= median]

    def _bands(bucket):
        return {
            "band_2_60": len([m for m in bucket if 2 <= m["hit_count"] <= 60]),
            "band_1": len([m for m in bucket if m["hit_count"] == 1]),
            "band_60plus": len([m for m in bucket if m["hit_count"] > 60]),
            "band_0": len([m for m in bucket if m["hit_count"] == 0]),
        }

    print(f"[bands] half1={_bands(half1)} half2={_bands(half2)}")

    def pick(bucket: list[dict], n: int) -> list[dict]:
        picked, seen_conv, seen_text = [], set(), set()

        def take(cands):
            for m in sorted(cands, key=lambda x: (-x["hit_count"], x["created_at"])):
                if len(picked) >= n:
                    return
                key = re.sub(r"\W+", "", m["text"])[:12]
                if m["conversation_id"] in seen_conv or key in seen_text:
                    continue
                picked.append(m)
                seen_conv.add(m["conversation_id"])
                seen_text.add(key)

        take([m for m in bucket if 2 <= m["hit_count"] <= 60])   # 优选：主题特异性
        take([m for m in bucket if m["hit_count"] == 1])          # 次选：弱特异性
        take([m for m in bucket if m["hit_count"] > 60])          # 宽泛但有主题
        take([m for m in bucket if m["hit_count"] == 0])          # 硬对照（预期无相关）
        return picked

    chosen = pick(half1, HALF_N) + pick(half2, HALF_N)
    chosen.sort(key=lambda x: x["created_at"])
    return chosen


def build_candidates(query: dict) -> list[dict]:
    """机器初标：FTS（mem_search）∪ 现有 n-gram 联想（search_related_items）。"""
    import backend.database as db

    db.DB_PATH = SNAPSHOT_DB
    from backend.memory_engine import _extract_keywords, search_related_items

    text = query["text"]
    merged: dict[int, dict] = {}

    for kw in _extract_keywords(text)[:4]:
        for m in db.mem_search(kw, limit=8):
            _add(merged, m, "fts")

    ngram_mems, _notes = search_related_items(text, limit=15)
    for m in ngram_mems[:8]:
        _add(merged, m, "ngram")

    cands = list(merged.values())
    cands.sort(key=lambda c: (-len(c["sources"]), -c["importance"], c["memory_id"]))
    return cands[:MAX_CANDIDATES]


def _add(merged: dict, m: dict, source: str) -> None:
    mid = m["id"]
    if mid not in merged:
        merged[mid] = {
            "memory_id": mid,
            "type": m.get("type", ""),
            "importance": m.get("importance", 0),
            "content_preview": (m.get("content") or "").strip()[:100],
            "sources": [],
        }
    if source not in merged[mid]["sources"]:
        merged[mid]["sources"].append(source)


def main() -> int:
    make_snapshot()
    messages = load_messages()
    print(f"[extract] 候选用户消息 {len(messages)} 条")
    chosen = select_queries(messages)

    queries = []
    for i, q in enumerate(chosen, 1):
        cands = build_candidates(q)
        queries.append({
            "id": f"q{i:02d}",
            "conversation_id": q["conversation_id"],
            "message_id": q["id"],
            "query": q["text"],
            "created_at": q["created_at"],
            "period": q["created_at"][:7],
            "hit_count": q["hit_count"],
            "label_status": "draft",
            "confirmed_relevant_ids": None,
            "candidates": cands,
        })
        print(f"[query {i:02d}] hits={q['hit_count']:2d} cands={len(cands)}  {q['text'][:40]}")

    dataset = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "snapshot_source": str(PROD_DB),
        "target_n": TARGET_N,
        "queries": queries,
    }
    (EVAL_DATA / "pilot.json").write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_review_md(dataset)
    n_with_cands = sum(1 for q in queries if q["candidates"])
    print(f"[done] queries={len(queries)} with_candidates={n_with_cands} "
          f"rate={n_with_cands / len(queries):.0%}")
    return 0


def _write_review_md(dataset: dict) -> None:
    lines = [
        "# 召回评估 pilot 人工审查表（C 阶段）",
        "",
        f"- 生成时间：{dataset['created_at']}",
        f"- 快照来源：{dataset['snapshot_source']}",
        "- 用法：逐条阅读候选记忆，在 `[ ]` 里勾选与查询**确实相关**的记忆（相关=换一种问法时仍应被召回）；"
        "勾选后回填到 `pilot.json` 的 `confirmed_relevant_ids`（或直接把确认结果贴回给我）。",
        "- 状态说明：当前候选是机器初标（FTS ∪ n-gram），**不是**最终标注，允许全不选。",
        "",
    ]
    for q in dataset["queries"]:
        lines.append(f"## {q['id']}　{q['query']}")
        lines.append(f"- 会话 {q['conversation_id']} · 消息 {q['message_id']} · {q['created_at']} · 命中片段 {q['hit_count']}")
        if not q["candidates"]:
            lines.append("- （无候选，请标注：无相关）")
        for c in q["candidates"]:
            lines.append(
                f"- [ ] {c['memory_id']} ({c['type']}, imp={c['importance']}) "
                f"{c['content_preview']}  来源:{'+'.join(c['sources'])}"
            )
        lines.append("")
    (EVAL_DATA / "PILOT_REVIEW.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"[review] {EVAL_DATA / 'PILOT_REVIEW.md'}")


if __name__ == "__main__":
    raise SystemExit(main())
