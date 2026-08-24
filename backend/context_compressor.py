"""Zenith v2 上下文压缩 — 长对话自动摘要减少 token 消耗
优化版：结构化 JSON 摘要 + 关键信息保留 + 递增合并
"""
from __future__ import annotations

import json
import logging
from .database import msg_list, msg_archive, db, _now
from .llm_client import call_llm

logger = logging.getLogger("zenith.compress")


def prune_tool_result(content: str, threshold_chars: int = 8192,
                      head_chars: int = 4096, tail_chars: int = 1024) -> str:
    """模型无关的工具结果头尾剪枝（借鉴 dsh compaction-tool-result-pruner）。

    超过 threshold_chars 时保留 head_chars 头部 + tail_chars 尾部，中段用占位符替换；
    短文本原样返回。仅作用于发送给 LLM 的 tool content，不影响前端 SSE 展示。
    """
    if content is None:
        return ""
    text = str(content)
    if len(text) <= threshold_chars:
        return text
    head = max(0, int(head_chars))
    tail = max(0, int(tail_chars))
    if head + tail >= len(text):
        return text  # 参数不合理时原样返回，避免比原文更长
    omitted = len(text) - head - tail
    suffix = text[-tail:] if tail > 0 else ""
    return f"{text[:head]}\n…[中间 {omitted} 字符已省略]…\n{suffix}"


def estimate_tokens(text: str, chars_per_token: int = 3) -> int:
    """轻量 token 估算（字符启发式，不引入 tokenizer 依赖）。

    中英混合文本的保守近似：约 chars_per_token 字符 ≈ 1 token。
    仅用于压缩触发判断，不做精确计量；provider 返回的 usage 才是精确值。
    """
    if not text:
        return 0
    n = max(1, int(chars_per_token))
    return (len(str(text)) + n - 1) // n


def _messages_tokens(messages: list) -> int:
    """估算 user/assistant 消息的 token 总量。"""
    return sum(
        estimate_tokens(m.get("content") or "")
        for m in messages if m.get("role") in ("user", "assistant")
    )


def _compress_triggered(messages: list, threshold: int, token_budget: int) -> bool:
    """压缩触发判断：消息数 >= threshold，或估算 token 总量 >= token_budget（>0 时启用）。"""
    if len(messages) >= threshold:
        return True
    if token_budget > 0 and _messages_tokens(messages) >= token_budget:
        return True
    return False


async def maybe_compress(conv_id: str) -> bool:
    """
    检查对话是否需要压缩。
    超过阈值时将旧消息压缩为一条 system 摘要。
    """
    from .config import load_config
    cfg = load_config()
    threshold = cfg.get("context_compress_threshold", 20)
    token_budget = int(cfg.get("context_token_budget", 0) or 0)
    messages = msg_list(conv_id)

    # 计数触发 OR token 预算触发（预算 > 0 时启用）
    if not _compress_triggered(messages, threshold, token_budget):
        return False

    keep_recent = 6
    old_messages = messages[:-keep_recent]

    # P1 修复：token 预算突破时，即使旧消息 < 4 条（少量超长消息场景）也应允许压缩；
    # 「≥4 条旧消息才压」护栏仅约束计数触发路径
    token_triggered = token_budget > 0 and _messages_tokens(messages) >= token_budget
    if len(old_messages) < 4 and not token_triggered:
        return False

    # 构建要压缩的对话文本
    conv_text = "\n".join(
        f"{'用户' if m['role'] == 'user' else 'AI'}: {m['content']}"
        for m in old_messages
        if m['role'] in ('user', 'assistant')
    )

    if not conv_text.strip():
        return False

    # 检查是否已有摘要（递增合并）
    existing_summary = ""
    with db() as c:
        row = c.execute(
            "SELECT content FROM messages WHERE conversation_id = ? AND role = 'system' "
            "AND content LIKE '[历史摘要]%' ORDER BY id DESC LIMIT 1",
            (conv_id,)
        ).fetchone()
        if row:
            existing_summary = row["content"].replace("[历史摘要] ", "")

    # 结构化压缩 prompt
    merge_hint = ""
    if existing_summary:
        merge_hint = f"\n\n已有摘要（请合并新内容）：\n{existing_summary}"

    prompt = f"""请将以下对话历史总结为结构化摘要。严格返回 JSON 格式：

{{
  "key_points": ["关键信息要点1", "关键信息要点2"],
  "decisions": ["已做决定1", "已做决定2"],
  "pending": ["待处理事项1"],
  "context": "用户当前关注的核心话题（1句话）",
  "entities": ["人名/项目名/技术名等关键实体"]
}}

要求：
- key_points: 保留所有重要事实和数据，不超过 8 条
- decisions: 明确的决定和方向选择
- pending: 尚未完成的任务或承诺
- context: 当前对话的核心上下文
- entities: 后续可能被引用的名称

对话内容：
{conv_text}{merge_hint}

只返回 JSON，不要其他内容。"""

    result = await call_llm(
        [{"role": "user", "content": prompt}],
        temperature=0.2, max_tokens=800
    )

    content = (result.get("content") or "").strip()
    if not content:
        return False

    # C2: 有效性校验 — LLM 失败/错误文本绝不写入库，更不删除原文
    _ERR_MARKERS = ("Error:", "Traceback (most recent call last)", "Exception", "❌")
    if any(m in content for m in _ERR_MARKERS):
        logger.warning("对话 %s 压缩 LLM 返回错误文本，跳过压缩（保护原文）", conv_id)
        return False

    # 解析 JSON
    summary_data = _parse_summary(content)
    if not summary_data:
        # C2: JSON 解析失败 → 跳过压缩（不写库不删文），宁可不压缩不可删错
        logger.warning("对话 %s 压缩摘要解析失败，跳过压缩（保护原文）", conv_id)
        return False
    summary_text = _format_summary(summary_data)
    if not summary_text:
        return False

    summary_content = f"[历史摘要] {summary_text}"

    # 更新或插入摘要
    with db() as c:
        existing = c.execute(
            "SELECT id FROM messages WHERE conversation_id = ? AND role = 'system' AND content LIKE ?",
            (conv_id, "[历史摘要]%")
        ).fetchone()

        if existing:
            c.execute(
                "UPDATE messages SET content = ? WHERE id = ?",
                (summary_content, existing["id"])
            )
        else:
            c.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, 'system', ?, ?)",
                (conv_id, summary_content, _now())
            )

    # D-C: 归档被压缩的旧消息（替代物理 DELETE，保留原文供 regenerate/edit/审计）
    old_ids = [m["id"] for m in old_messages if m["role"] != "system"]
    if old_ids:
        msg_archive(conv_id, old_ids)

    logger.info("对话 %s 压缩完成: %d 条旧消息 → 摘要 (%d 字)",
                conv_id, len(old_ids), len(summary_text))
    return True


def _parse_summary(content: str) -> dict | None:
    """解析 LLM 返回的 JSON 摘要"""
    text = content.strip()
    if "```" in text:
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            if text and text[0].isalpha():
                first_line_end = text.find("\n")
                if first_line_end > 0:
                    lang = text[:first_line_end].strip()
                    if lang.isalpha():
                        text = text[first_line_end + 1:]
    text = text.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    # 尝试提取 JSON 对象
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def _format_summary(data: dict) -> str:
    """将结构化摘要格式化为紧凑的可读文本"""
    parts = []

    ctx = data.get("context", "")
    if ctx:
        parts.append(f"[话题] {ctx}")

    entities = data.get("entities", [])
    if entities:
        parts.append(f"[实体] {', '.join(entities[:8])}")

    key_points = data.get("key_points", [])
    if key_points:
        parts.append("[要点] " + " | ".join(key_points[:8]))

    decisions = data.get("decisions", [])
    if decisions:
        parts.append("[决定] " + " | ".join(decisions[:5]))

    pending = data.get("pending", [])
    if pending:
        parts.append("[待办] " + " | ".join(pending[:5]))

    return "\n".join(parts) if parts else ""
