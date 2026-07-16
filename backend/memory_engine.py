"""Zenith v2 记忆引擎 — 自动提取 + 分类存储 + 去重 + 相关性注入 + 衰减合并"""
from __future__ import annotations

import asyncio
import logging
import re
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
    """后台执行记忆提取 + 去重"""
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

            # 去重检查：搜索已有记忆中是否有相似的
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

    except Exception as e:
        logger.warning("记忆提取失败: %s", e, exc_info=True)


def _is_duplicate(content: str, threshold: float = 0.8) -> bool:
    """
    检查是否已有相似记忆。
    策略：取内容前 20 字做 LIKE 查询，找到候选后计算相似度。
    """
    if not content or len(content) < 4:
        return False

    # 用前几个字做模糊查询
    prefix = content[:20]
    candidates = mem_search(prefix[:8])

    if not candidates:
        return False

    for c in candidates:
        existing = c.get("content", "")
        if _similarity(content, existing) >= threshold:
            return True

    return False


def _similarity(a: str, b: str) -> float:
    """简易文本相似度 — 基于 Jaccard 系数（字符级）"""
    if not a or not b:
        return 0.0

    set_a = set(a[i:i+2] for i in range(len(a) - 1))
    set_b = set(b[i:i+2] for i in range(len(b) - 1))

    if not set_a or not set_b:
        return 0.0

    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


def mem_touch(memory_id: int):
    """记忆被引用时调用 — 提升重要度 + 更新访问时间"""
    with db() as c:
        c.execute(
            "UPDATE memories SET importance = MIN(importance + 1, 5) WHERE id = ?",
            (memory_id,)
        )


def mem_consolidate():
    """
    记忆合并 — 定期调用。
    1. 合并高度相似的记忆（保留重要度最高的）
    2. 降低长期未引用记忆的重要度
    """
    all_mems = mem_list()
    if len(all_mems) < 10:
        return {"merged": 0, "decayed": 0}

    merged = 0
    decayed = 0
    seen_ids = set()

    for i, m in enumerate(all_mems):
        if m["id"] in seen_ids:
            continue

        for j in range(i + 1, len(all_mems)):
            other = all_mems[j]
            if other["id"] in seen_ids:
                continue
            if other["type"] != m["type"]:
                continue

            sim = _similarity(m["content"], other["content"])
            if sim >= 0.7:
                # 合并：保留重要度更高的，删除较低的
                keeper = m if m["importance"] >= other["importance"] else other
                to_del = other if m["importance"] >= other["importance"] else m

                # 合并关键词
                merged_kw = set()
                for kw in (keeper.get("keywords", "") + "," + to_del.get("keywords", "")).split(","):
                    kw = kw.strip()
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
        for r in rows:
            c.execute(
                "UPDATE memories SET importance = importance - 1 WHERE id = ?",
                (r["id"],)
            )
            decayed += 1

    if merged or decayed:
        logger.info("记忆合并完成: 合并 %d 条, 衰减 %d 条", merged, decayed)

    return {"merged": merged, "decayed": decayed}
