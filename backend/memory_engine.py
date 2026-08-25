"""Zenith v2 记忆引擎 — 自动提取 + 分类存储 + 去重 + 相关性注入 + 衰减合并"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .database import mem_add, mem_for_inject, mem_search, mem_list, note_list, db, _now

if TYPE_CHECKING:
    import numpy as np  # 仅类型注解用（np.ndarray），运行时走函数内延迟导入

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


# 记忆整理/合并的相似度阈值。mem_consolidate（自动后台合并）与
# generate_consolidate_plan（LLM 辅助整理计划）曾各用 0.7 / 0.85 不一致，
# 统一为 0.85（更保守，宁少合不误删）。
MERGE_SIM_THRESHOLD = 0.85


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


def search_related_memories(keywords: list[str], limit: int = 15, min_keyword_results: int = 10) -> list[dict]:
    """按关键词检索相关记忆（去重），不足时补充高重要度记忆。返回记忆对象列表。

    收敛 build_memory_injection 与 _retrieve_related_memories 的重复检索逻辑
    （此前两处各自实现「关键词→mem_search→去重→mem_for_inject 补充」）。
    """
    relevant: list[dict] = []
    seen_ids: set[int] = set()
    for kw in keywords[:4]:
        for r in mem_search(kw, limit=10):
            if r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                relevant.append(r)
        if len(relevant) >= limit:
            break
    # 关键词命中不足时，补充高重要度记忆
    if len(relevant) < min_keyword_results:
        for m in mem_for_inject(limit=limit):
            if m["id"] not in seen_ids:
                seen_ids.add(m["id"])
                relevant.append(m)
            if len(relevant) >= limit:
                break
    return relevant


def build_memory_injection(current_query: str = "") -> str:
    """
    构建注入到 system prompt 的记忆摘要（记忆 + 笔记）。
    若提供 current_query，走语义联想召回（字符 n-gram 重叠打分，不依赖精确关键词）；
    否则按重要度取 top 记忆。
    """
    memories: list[dict] = []
    notes: list[dict] = []

    if current_query:
        memories, notes = search_related_items(current_query, limit=15, include_notes=True)
    else:
        # 默认模式：按重要度取 top 20
        memories = mem_for_inject(limit=20)

    if not memories and not notes:
        return ""

    lines = ["## 记忆库（关于用户）"]

    # 分组（排除 skill：技能由 _build_skill_injection 单独注入，避免重复 + 超长 content 浪费 token）
    groups: dict[str, list] = {}
    for m in memories:
        if m["type"] == "skill":
            continue
        groups.setdefault(m["type"], []).append(m)

    type_names = {
        "personal_info": "关于用户的信息",
        "preference": "用户的偏好",
        "event": "发生过的事件",
        "decision": "做过的决定",
        "fact": "知道的事实",
        "experience": "经验与技巧",
    }
    for tp, items in groups.items():
        name = type_names.get(tp, tp)
        lines.append(f"\n**{name}**：")
        for item in items[:5]:
            lines.append(f"  - {item['content']}")

    if notes:
        lines.append("\n## 相关笔记")
        for nt in notes[:5]:
            title = nt.get("title", "") or "未命名"
            content = (nt.get("content", "") or "").strip().replace("\n", " ")
            snippet = content[:120]
            lines.append(f"  - 【{title}】{snippet}")

    # 无有效记忆分组也无笔记时返回空
    if not groups and not notes:
        return ""

    return "\n".join(lines)


def reset_counter(conv_id: str = ""):
    """重置计数器 — 切换或删除对话时调用"""
    if conv_id:
        _conv_counters.pop(conv_id, None)
        _conv_text_buffer.pop(conv_id, None)
    else:
        _conv_counters.clear()
        _conv_text_buffer.clear()


def flush_conversation_memories(conv_id: str):
    """对话收尾（删除/关闭）时调用：把该对话 buffer 中尚未提取的残余文本提取完。

    与 reset_counter 的区别：reset_counter 直接丢弃 buffer；flush 先消费残余再清理，
    确保最后 1~2 轮（不足 interval 触发阈值）的内容不会丢失。
    """
    text = _conv_text_buffer.pop(conv_id, "")
    _conv_counters.pop(conv_id, None)
    if not text.strip():
        return
    task = asyncio.create_task(_do_extract(text, conv_id))
    _pending_tasks.add(task)
    task.add_done_callback(_pending_tasks.discard)
    logger.info("对话收尾记忆提取已启动 (conv=%s, text_len=%d)", conv_id, len(text))


async def flush_all_pending_memories():
    """优雅关闭时调用：遍历所有对话 buffer 并同步等待残余文本提取完成。

    设计为 async，由调用方在独立 event loop 中 run_until_complete（本函数在
    信号处理/finally 块中使用，uvicorn 已退出但进程尚存）。LLM 调用可能耗时数秒，
    关闭时一次性等待完成，避免数据丢失。
    """
    conv_ids = list(_conv_text_buffer.keys())
    flushed = 0
    for cid in conv_ids:
        text = _conv_text_buffer.pop(cid, "")
        _conv_counters.pop(cid, None)
        if not text.strip():
            continue
        try:
            await _do_extract(text, cid)
            flushed += 1
        except Exception as e:
            logger.warning("flush 对话 %s 失败: %s", cid, e)
    # 等待已在跑的后台提取任务完成（_pending_tasks 由 done_callback 自动清理，
    # 此处 gather 使之成为有意义的生命周期状态，而非仅防 GC 的幽灵集合）
    pending = list(_pending_tasks)
    if pending:
        try:
            await asyncio.gather(*pending, return_exceptions=True)
        except Exception as e:
            logger.warning("等待后台提取任务失败: %s", e)
    logger.info("优雅关闭 flush 完成: 提取 %d 个对话残余, 等待 %d 个后台任务", flushed, len(pending))
    return flushed


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


def _retrieve_related_memories(text: str, limit: int = 15) -> list[str]:
    """检索与当前对话文本相关的已有记忆（content 列表），供 LLM 去重参考。

    复用 search_related_memories（关键词 FTS 检索 + 高重要度补充）。
    """
    mems = search_related_memories(_extract_keywords(text, max_k=6), limit=limit)
    return [m.get("content", "") for m in mems]


async def _do_extract(text: str, conv_id: str):
    """后台执行记忆提取 + 去重。同时被 periodic 和 final 两种触发时机共用。"""
    try:
        from .llm_client import extract_memories
        # 提取前惰性刷新 IDF 权重，让去重的 TF-IDF 余弦真正用上 IDF 加权
        _maybe_refresh_idf()
        existing = _retrieve_related_memories(text)
        items = await extract_memories(text, existing_memories=existing)
        logger.info("记忆提取完成: %d 条 (conv=%s, 已有参考%d条)", len(items), conv_id, len(existing))

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


def _shared_text_vectors(a: str, b: str, idf_weights: dict = None) -> tuple[np.ndarray, np.ndarray]:
    """两条文本映射到「同一共享词表」的加权向量（2-4 gram + 单词 + IDF）。

    关键修复：旧 _text_vector 对每条文本各自提取词表，导致两条文本向量维度
    不一致，_semantic_similarity 里 `len(va) != len(vb)` 恒成立、永远降级到
    Jaccard——精心编写的 TF-IDF 余弦相似度实为死代码。这里用「IDF 词表 ∪ 两文本
    各自特征」作为统一词表，使余弦相似度真正可用。
    """
    import numpy as np  # 延迟导入：本函数是唯一用 numpy 处，避免启动即吃导入开销

    grams_a, grams_b = set(), set()
    for n in (2, 3, 4):
        grams_a |= _ngrams(a, n)
        grams_b |= _ngrams(b, n)
    words_a = set(re.findall(r'[a-zA-Z0-9]+', a.lower()))
    words_b = set(re.findall(r'[a-zA-Z0-9]+', b.lower()))

    # 关键修复（2026-08-25）：vocab 只取两文本的特征并集。
    # 旧实现 `vocab = set(idf_weights)` 把全量 IDF 词表（几万个 n-gram）塞进循环，
    # 每次相似度比较 O(几万 × 文本长度)，consolidate_memories 的 500 条两两比较
    # （12.5 万次）直接 CPU 空转数分钟 → 假死 → watchdog 强杀。
    # 两文本向量维度一致只需「两文本 gram/word 并集」，IDF 权重对 vocab 内 gram 查表即可。
    vocab = grams_a | grams_b | words_a | words_b
    vocab = sorted(vocab)
    if not vocab:
        return np.zeros(0), np.zeros(0)

    la, lb = a.lower(), b.lower()
    va = np.zeros(len(vocab))
    vb = np.zeros(len(vocab))
    for i, f in enumerate(vocab):
        idf = idf_weights.get(f, 1.0) if idf_weights else 1.0
        if f in grams_a or f in words_a:
            va[i] = (la.count(f) / max(len(a), 1)) * idf
        if f in grams_b or f in words_b:
            vb[i] = (lb.count(f) / max(len(b), 1)) * idf
    return va, vb


# 全局 IDF 权重缓存
_idf_cache: dict = {}
_idf_doc_count: int = 0


def _update_idf_weights():
    """从所有记忆中更新 IDF 权重"""
    global _idf_cache, _idf_doc_count
    import numpy as np  # 延迟导入：与其他 np 使用点一致，避免启动开销
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


# IDF 上次刷新时的记忆总数（惰性刷新基准）
_idf_last_count: int = 0


def _maybe_refresh_idf():
    """惰性刷新 IDF 权重（在记忆提取前调用，避免相似度热路径全表扫描）。

    记忆数相对上次刷新变化超过 20% 或 20 条时重建；否则跳过。
    """
    global _idf_last_count
    try:
        with db() as c:
            count = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    except Exception:
        return
    if _idf_cache and abs(count - _idf_last_count) < max(20, int(count * 0.2)):
        return  # 变化不大，跳过
    _update_idf_weights()
    _idf_last_count = count


def _semantic_similarity(a: str, b: str) -> float:
    """
    增强版文本相似度 — 共享词表 TF-IDF 余弦 + 字符级 Jaccard 混合。
    对中文文本区分度远超 Jaccard bigram。
    """
    import numpy as np  # 延迟导入：与 _shared_text_vectors 一致，避免启动即导入

    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    # 短文本额外检查精确包含
    if len(a) < 10 and len(b) < 10:
        if a in b or b in a:
            return 0.9

    va, vb = _shared_text_vectors(a, b, _idf_cache if _idf_cache else None)
    if va.size == 0 or vb.size == 0:
        return _legacy_jaccard(a, b)

    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return _legacy_jaccard(a, b)

    cos = float(np.dot(va, vb) / (na * nb))
    jac = _legacy_jaccard(a, b)
    # 混合：取余弦与（Jaccard×0.9）的较大者，兼顾语义区分度与字符重叠召回
    return max(cos, jac * 0.9)


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


def find_similar_memory_groups(mems: list[dict], threshold: float = None, id_key: str = "id", type_key: str = "type", content_key: str = "content") -> list[dict]:
    """记忆相似度合并分组（供 consolidate 等写侧复用）。

    用 3-gram 倒排索引做候选召回，替代 O(n²) 全量两两比较；只对「共享 3-gram」
    的候选对算精确相似度。返回 [{keep_id, delete_ids, merged_content}]。

    设计要点（2026-08-25 性能优化）：
    - 3-gram 剪枝是安全的：_semantic_similarity 与 _legacy_jaccard 都以 n-gram 交集
      为相似度非零的必要条件，两条文本无共享 3-gram 时相似度必为 0。
    - 2-gram 太密集（中文常用字组合）剪枝无效，4-gram 对短文本有漏检风险，故取 3-gram。
    - <3 字符短文本无 3-gram，单独全量比较兜底（数量极少）。
    - 不设固定上限，支持全量；调用方如需限制可自行切片。
    """
    if threshold is None:
        threshold = MERGE_SIM_THRESHOLD
    if not mems:
        return []

    # 按 type 分组（跨 type 不比较），组内做 3-gram 倒排索引
    by_type: dict = {}
    for idx, m in enumerate(mems):
        by_type.setdefault(m.get(type_key), []).append(idx)

    merge_groups = []
    seen_ids = set()
    for tp, idxs in by_type.items():
        if len(idxs) < 2:
            continue
        # <3 字符短文本兜底全量；其余走 3-gram 候选召回
        short_idxs = {i for i in idxs if len(mems[i].get(content_key, "") or "") < 3}
        gram_to_idxs: dict = {}
        mem_grams: dict = {}
        for idx in idxs:
            grams = _ngrams(mems[idx].get(content_key, "") or "", 3)
            mem_grams[idx] = grams
            for g in grams:
                gram_to_idxs.setdefault(g, []).append(idx)

        for i in idxs:
            if mems[i].get(id_key) in seen_ids:
                continue
            candidate_set = {j for j in idxs if j > i} if i in short_idxs else set()
            if i not in short_idxs:
                for g in mem_grams[i]:
                    for j in gram_to_idxs.get(g, ()):
                        if j > i:
                            candidate_set.add(j)

            group_delete = []
            for j in sorted(candidate_set):
                other = mems[j]
                if other.get(id_key) in seen_ids:
                    continue
                sim = _similarity(mems[i].get(content_key, ""), other.get(content_key, ""))
                if sim >= threshold:
                    group_delete.append(other)
                    seen_ids.add(other.get(id_key))

            if group_delete:
                seen_ids.add(mems[i].get(id_key))
                keeper = mems[i]
                for o in group_delete:
                    if o.get("importance", 3) > keeper.get("importance", 3):
                        keeper = o
                merge_groups.append({
                    "keep_id": keeper.get(id_key),
                    "delete_ids": [o.get(id_key) for o in group_delete if o.get(id_key) != keeper.get(id_key)],
                    "merged_content": keeper.get(content_key, ""),
                })

    return merge_groups


# ---------------------------------------------------------------------------
# 语义联想召回（对话读侧）：字符 n-gram 重叠打分，不依赖精确关键词
# ---------------------------------------------------------------------------

_gram_cache: list[dict] = []      # [{mem: dict, grams: set}]
_note_gram_cache: list[dict] = [] # [{note: dict, grams: set}]
_gram_cache_count: int = 0


def _score_query_overlap(q_grams: set, m_grams: set) -> float:
    """query 与记忆的字符 n-gram 重叠打分（IDF 加权：稀有 gram 权重高、常见 gram 权重低）。"""
    overlap = q_grams & m_grams
    if not overlap:
        return 0.0
    if _idf_cache:
        return sum(_idf_cache.get(g, 1.0) for g in overlap)
    return float(len(overlap))


def _build_gram_cache():
    """惰性构建全量记忆 + 笔记的字符 n-gram 索引（供联想召回打分）"""
    global _gram_cache, _note_gram_cache, _gram_cache_count
    try:
        all_mems = mem_list()
        cache = []
        for m in all_mems:
            if m.get("type") == "skill":
                continue  # 技能由 _build_skill_injection 单独注入，不参与记忆联想
            grams: set = set()
            content = m.get("content", "") or ""
            for n in (2, 3, 4):
                grams |= _ngrams(content, n)
            cache.append({"mem": m, "grams": grams})
        _gram_cache = cache
        _gram_cache_count = len(all_mems)

        notes_cache = []
        for nt in note_list():
            text = f"{nt.get('title', '')} {nt.get('content', '')}"
            grams = set()
            for n in (2, 3, 4):
                grams |= _ngrams(text, n)
            notes_cache.append({"note": nt, "grams": grams})
        _note_gram_cache = notes_cache
    except Exception as e:
        logger.warning("构建 gram 索引失败: %s", e)


def _maybe_refresh_gram_cache():
    """记忆数量变化时重建 gram 索引（用 COUNT 避免全量 SELECT）"""
    global _gram_cache_count
    try:
        with db() as c:
            count = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    except Exception:
        return
    if _gram_cache and count == _gram_cache_count:
        return
    _build_gram_cache()


def search_related_items(query: str, limit: int = 15, include_notes: bool = True):
    """语义联想召回：记忆（全量字符 n-gram 重叠打分）+ 笔记（TF-IDF 余弦）。

    返回 (memories: list[dict], notes: list[dict])，均已按相关性降序。
    记忆召回覆盖「字符重叠/子序列相关」（如「跑步」↔「每周跑步三次」），
    比 mem_search 的精确子串 LIKE 更宽，实现「联想」；笔记用 _semantic_similarity 打分。
    """
    _maybe_refresh_idf()
    _maybe_refresh_gram_cache()

    q_grams: set = set()
    for n in (2, 3, 4):
        q_grams |= _ngrams(query, n)

    memories: list[dict] = []
    scored = []
    if q_grams:
        for item in _gram_cache:
            s = _score_query_overlap(q_grams, item["grams"])
            if s > 0:
                scored.append((s, item["mem"]))
        scored.sort(key=lambda x: -x[0])
        memories = [m for _, m in scored[:limit]]

    # 联想无强命中（top 分数 < 1.0，即不足一个默认 IDF 权重的 gram 重叠）时，按重要度兜底
    if not memories or (scored and scored[0][0] < 1.0):
        memories = mem_for_inject(limit=limit)

    notes: list[dict] = []
    if include_notes and q_grams:
        try:
            scored_notes = []
            for item in _note_gram_cache:
                s = _score_query_overlap(q_grams, item["grams"])
                if s > 0:
                    scored_notes.append((s, item["note"]))
            scored_notes.sort(key=lambda x: -x[0])
            notes = [nt for _, nt in scored_notes[:5]]
        except Exception as e:
            logger.warning("笔记联想召回失败: %s", e)

    return memories, notes


def mem_touch(memory_id: int):
    """记忆被引用时调用 — 提升重要度 + 更新访问时间（last_touched_at）。

    供 search_memory 命中时调用，使「引用提升重要度」机制真正生效，
    并作为 mem_consolidate 衰减判断的时间基准（而非 created_at）。
    """
    with db() as c:
        c.execute(
            "UPDATE memories SET importance = MIN(importance + 1, 5), last_touched_at = ? WHERE id = ?",
            (_now(), memory_id)
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
            if sim >= MERGE_SIM_THRESHOLD:
                keeper = m if m["importance"] >= other["importance"] else other
                to_del = other if m["importance"] >= other["importance"] else m

                merged_kw = set()
                for kw_str in (keeper.get("keywords", "") + "," + to_del.get("keywords", "")).split(","):
                    kw = kw_str.strip()
                    if kw:
                        merged_kw.add(kw)

                with db() as c:
                    c.execute(
                        "UPDATE memories SET keywords = ?, distilled_from = ? WHERE id = ?",
                        (",".join(merged_kw), to_del["id"], keeper["id"])
                    )
                    c.execute("DELETE FROM memories WHERE id = ?", (to_del["id"],))

                seen_ids.add(to_del["id"])
                merged += 1
                logger.info("合并记忆 #%d → #%d (sim=%.2f)", to_del["id"], keeper["id"], sim)

    # 衰减：30天以上未被引用的记忆，重要度 -1。
    # 时间基准用 last_touched_at（无则退回 recorded_at，再退回 created_at），
    # 人工编辑过的记忆（user_edited=1）不自动衰减。
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    with db() as c:
        rows = c.execute(
            "SELECT id, importance, user_edited FROM memories "
            "WHERE importance > 1 AND COALESCE(last_touched_at, recorded_at, created_at) < ?",
            (cutoff,)
        ).fetchall()
        decayed = 0
        for r in rows:
            if r["user_edited"]:
                continue
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
