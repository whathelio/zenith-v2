"""Zenith v2 记忆引擎 — 自动提取 + 分类存储 + 去重 + 相关性注入 + 衰减合并"""
from __future__ import annotations

import asyncio
import logging
import re
import numpy as np
from collections import defaultdict
from datetime import datetime, timedelta
from .database import mem_add, mem_for_inject, mem_search, mem_list, mem_del, db, _now

logger = logging.getLogger("zenith.memory")

# 每个对话独立计数，避免跨对话污染
_conv_counters: dict[str, int] = {}          # conv_id → round count
_conv_text_buffer: dict[str, str] = {}       # conv_id → accumulated text
_pending_tasks: set = set()

# 关键词提取停用词
_STOPWORDS = frozenset(
    "的 了 是 在 我 你 他 她 它 有 没 就 都 和 与 或 也 还 不 没 把 被 让 给 从 到 向 "
    "这 那 这个 那个 什么 怎么 为什么 哪 哪里 谁 多少 几 可以 能 会 要 想 需要 应该 "
    "一个 一些 一下 一样 上 下 里 外 前 后 左 右 中 间 们 吧 吗 呢 啊 哦 嗯 呀 哈 "
    "the a an is are was were be been being have has had do does did will would "
    "can could should may might must to of in on at for with by from as it its"
    .split()
)


def _extract_keywords(text: str, max_k: int = 8) -> list[str]:
    """简易中文关键词提取 — 基于词频 + 停用词过滤"""
    # 提取 2-6 字的中文片段
    segments = re.findall(r'[\u4e00-\u9fff]{2,6}', text)
    # 英文单词
    segments += re.findall(r'[a-zA-Z]{3,}', text)
    # 数字
    segments += re.findall(r'\d+', text)

    freq: dict[str, int] = {}
    for seg in segments:
        seg_lower = seg.lower()
        if seg_lower in _STOPWORDS or len(seg_lower) < 2:
            continue
        freq[seg_lower] = freq.get(seg_lower, 0) + 1

    # 按频率排序，取 top N
    ranked = sorted(freq.items(), key=lambda x: -x[1])
    return [kw for kw, _ in ranked[:max_k]]


def build_memory_injection(current_query: str = "") -> str:
    """
    构建注入到 system prompt 的记忆摘要。
    如果提供了 current_query，优先注入相关性最高的记忆。
    """
    keywords = _extract_keywords(current_query) if current_query else []

    if keywords:
        # 相关性模式：按关键词搜索，再合并重要记忆补充
        relevant = []
        seen_ids = set()
        for kw in keywords[:4]:
            results = mem_search(kw)
            for r in results:
                if r["id"] not in seen_ids:
                    relevant.append(r)
                    seen_ids.add(r["id"])
            if len(relevant) >= 15:
                break
        # 补充高重要度记忆
        if len(relevant) < 10:
            for m in mem_for_inject(limit=20):
                if m["id"] not in seen_ids:
                    relevant.append(m)
                    seen_ids.add(m["id"])
                if len(relevant) >= 15:
                    break
        memories = relevant
    else:
        # 默认模式：按重要度取 top 20
        memories = mem_for_inject(limit=20)

    if not memories:
        return ""

    groups: dict[str, list] = {}
    for m in memories:
        groups.setdefault(m["type"], []).append(m)

    type_names = {
        "personal_info": "关于用户的信息",
        "preference": "用户的偏好",
        "event": "发生过的事件",
        "decision": "做过的决定",
        "fact": "知道的事实",
        "experience": "经验与技巧",
    }

    lines = ["## 记忆库（关于用户）"]
    for tp, items in groups.items():
        name = type_names.get(tp, tp)
        lines.append(f"\n**{name}**：")
        for item in items[:5]:
            lines.append(f"  - {item['content']}")

    return "\n".join(lines)


def reset_counter(conv_id: str = ""):
    """重置计数器 — 切换或删除对话时调用"""
    if conv_id:
        _conv_counters.pop(conv_id, None)
        _conv_text_buffer.pop(conv_id, None)
    else:
        _conv_counters.clear()
        _conv_text_buffer.clear()


async def maybe_extract_memories(
    conversation_text: str,
    conv_id: str = "",
    interval: int = 3
):
    """每 N 轮对话触发一次记忆提取（按对话独立计数）"""
    # 累积当前对话的文本
    _conv_text_buffer[conv_id] = _conv_text_buffer.get(conv_id, "") + "\n" + conversation_text
    _conv_counters[conv_id] = _conv_counters.get(conv_id, 0) + 1

    if _conv_counters[conv_id] >= interval:
        _conv_counters[conv_id] = 0
        text = _conv_text_buffer.pop(conv_id, "")
        task = asyncio.create_task(_do_extract(text, conv_id))
        _pending_tasks.add(task)
        task.add_done_callback(_pending_tasks.discard)
        logger.info("记忆提取任务已启动 (conv=%s, text_len=%d)", conv_id, len(text))


async def _do_extract(text: str, conv_id: str):
    """后台执行记忆提取 + 去重。同时被 periodic 和 final 两种触发时机共用。"""
    try:
        from .llm_client import extract_memories
        items = await extract_memories(text)
        logger.info("记忆提取完成: %d 条 (conv=%s)", len(items), conv_id)

        new_count = 0
        skip_count = 0
        for item in items:
            content = item.get("content", "").strip()
            if not content:
                continue

            if _is_duplicate(content):
                skip_count += 1
                continue

            mem_add(
                type_=item.get("type", "fact"),
                content=content,
                importance=item.get("importance", 3),
                keywords=item.get("keywords", ""),
                source_conv_id=conv_id,
            )
            new_count += 1

        if skip_count:
            logger.info("记忆去重: 跳过 %d 条相似记忆", skip_count)
        return {"new": new_count, "skipped": skip_count}

    except Exception as e:
        logger.warning("记忆提取失败: %s", e, exc_info=True)
        return {"new": 0, "skipped": 0}


# 公开别名 — 供 app.py _auto_distill_conv 调用
extract_memories_from_text = _do_extract


def _is_duplicate(content: str, threshold: float = 0.75) -> bool:
    """
    检查是否已有相似记忆。
    策略：提取关键词后用 LIKE 搜索候选（FTS5 CJK tokenizer 对中文分词受限），计算语义相似度。
    """
    if not content or len(content) < 4:
        return False

    # 用前 15 个字符做 LIKE 候选搜索（更可靠）
    candidates = mem_search(content[:15], limit=10)
    if not candidates:
        # 补充：关键词搜索
        keywords = _extract_keywords(content)
        if keywords:
            candidates = mem_search(keywords[0], limit=10)

    if not candidates:
        return False

    for c in candidates:
        existing = c.get("content", "")
        sim = _similarity(content, existing)
        if sim >= threshold:
            return True

    return False


def _ngrams(text: str, n: int) -> set:
    """提取 n-gram 字符片段"""
    return set(text[i:i+n] for i in range(len(text) - n + 1))


def _text_vector(text: str, idf_weights: dict = None) -> np.ndarray:
    """将文本转为稀疏加权向量（2-4 gram + IDF 加权）"""
    grams = set()
    # 2-4 字符级 n-gram（对中文特别有效）
    for n in [2, 3, 4]:
        grams |= _ngrams(text, n)
    # 单词级（英文/数字）
    words = set(re.findall(r'[a-zA-Z0-9]+', text.lower()))
    all_features = list(grams | words)
    if not all_features:
        return np.zeros(0)
    vec = np.zeros(len(all_features))
    for i, f in enumerate(all_features):
        tf = text.count(f) / max(len(text), 1)
        idf = idf_weights.get(f, 1.0) if idf_weights else 1.0
        vec[i] = tf * idf
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


# 全局 IDF 权重缓存
_idf_cache: dict = {}
_idf_doc_count: int = 0


def _update_idf_weights():
    """从所有记忆中更新 IDF 权重"""
    global _idf_cache, _idf_doc_count
    try:
        all_mems = mem_list()
        _idf_doc_count = len(all_mems)
        if _idf_doc_count < 5:
            return
        df = defaultdict(int)
        for m in all_mems:
            seen = set()
            for n in [2, 3, 4]:
                for gram in _ngrams(m.get("content", ""), n):
                    if gram not in seen:
                        df[gram] += 1
                        seen.add(gram)
        _idf_cache = {
            gram: np.log((_idf_doc_count + 1) / (count + 1)) + 1
            for gram, count in df.items()
        }
    except Exception as e:
        logger.warning("更新 IDF 权重失败: %s", e)


def _semantic_similarity(a: str, b: str) -> float:
    """
    增强版文本相似度 — n-gram TF-IDF + 余弦相似度。
    对中文文本区分度远超 Jaccard bigram。
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    # 短文本额外检查精确包含
    if len(a) < 10 and len(b) < 10:
        if a in b or b in a:
            return 0.9

    va = _text_vector(a, _idf_cache if _idf_cache else None)
    vb = _text_vector(b, _idf_cache if _idf_cache else None)
    if len(va) == 0 or len(vb) == 0:
        return 0.0

    # 确保向量维度一致（取交集）
    if len(va) != len(vb):
        return _legacy_jaccard(a, b)

    return float(np.dot(va, vb))


def _legacy_jaccard(a: str, b: str) -> float:
    """降级：字符级 Jaccard bigram 相似度"""
    if not a or not b:
        return 0.0
    set_a = set(a[i:i+2] for i in range(len(a) - 1))
    set_b = set(b[i:i+2] for i in range(len(b) - 1))
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def _similarity(a: str, b: str) -> float:
    """统一相似度入口 — 优先使用语义相似度，降级到 Jaccard"""
    try:
        return _semantic_similarity(a, b)
    except Exception:
        return _legacy_jaccard(a, b)


def mem_touch(memory_id: int):
    """记忆被引用时调用 — 提升重要度 + 更新访问时间"""
    with db() as c:
        c.execute(
            "UPDATE memories SET importance = MIN(importance + 1, 5) WHERE id = ?",
            (memory_id,)
        )


def mem_consolidate():
    """
    记忆增量合并 — 定期调用。
    1. 从最近更新的记忆中查找相似对（非 O(n²) 全量比对）
    2. 合并高度相似的记忆（保留重要度最高的）
    3. 降低长期未引用记忆的重要度
    """
    all_mems = mem_list()
    if len(all_mems) < 10:
        return {"merged": 0, "decayed": 0}

    # 按 created_at 排序，找最近 N 条
    recent = sorted(all_mems, key=lambda m: m.get("created_at") or "", reverse=True)[:50]

    merged = 0
    seen_ids = set()

    for i, m in enumerate(recent):
        if m["id"] in seen_ids:
            continue
        content = m.get("content") or ""
        if not content:
            continue

        # 用关键词搜索候选（非全量比对）
        keywords = m.get("keywords", "")
        if keywords:
            candidates = mem_search(keywords.split(",")[0], limit=10)
        else:
            candidates = mem_search(content[:20], limit=10)

        for other in candidates:
            if other["id"] == m["id"] or other["id"] in seen_ids:
                continue
            if other.get("type") != m.get("type"):
                continue

            other_content = other.get("content") or ""
            if not other_content:
                continue
            sim = _similarity(content, other_content)
            if sim >= 0.7:
                keeper = m if m["importance"] >= other["importance"] else other
                to_del = other if m["importance"] >= other["importance"] else m

                merged_kw = set()
                for kw_str in (keeper.get("keywords", "") + "," + to_del.get("keywords", "")).split(","):
                    kw = kw_str.strip()
                    if kw:
                        merged_kw.add(kw)

                with db() as c:
                    c.execute(
                        "UPDATE memories SET keywords = ? WHERE id = ?",
                        (",".join(merged_kw), keeper["id"])
                    )
                    c.execute("DELETE FROM memories WHERE id = ?", (to_del["id"],))

                seen_ids.add(to_del["id"])
                merged += 1
                logger.info("合并记忆 #%d → #%d (sim=%.2f)", to_del["id"], keeper["id"], sim)

    # 衰减：30天以上未被引用的记忆，重要度 -1
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    with db() as c:
        rows = c.execute(
            "SELECT id, importance FROM memories "
            "WHERE created_at < ? AND importance > 1",
            (cutoff,)
        ).fetchall()
        decayed = 0
        for r in rows:
            c.execute(
                "UPDATE memories SET importance = importance - 1 WHERE id = ?",
                (r["id"],)
            )
            decayed += 1

    # 定期更新 IDF 权重（低频操作）
    if merged > 0:
        _update_idf_weights()

    if merged or decayed:
        logger.info("记忆合并完成: 合并 %d 条, 衰减 %d 条", merged, decayed)

    return {"merged": merged, "decayed": decayed}
