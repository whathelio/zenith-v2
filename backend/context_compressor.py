"""Zenith v2 上下文压缩 — 长对话自动摘要减少 token 消耗
优化版：结构化 JSON 摘要 + 关键信息保留 + 递增合并
"""
from __future__ import annotations

import json
import logging
from .database import msg_list, db, _now
from .llm_client import call_llm

logger = logging.getLogger("zenith.compress")


async def maybe_compress(conv_id: str) -> bool:
    """
    检查对话是否需要压缩。
    超过阈值时将旧消息压缩为一条 system 摘要。
    """
    from .config import load_config
    cfg = load_config()
    threshold = cfg.get("context_compress_threshold", 20)
    messages = msg_list(conv_id)

    if len(messages) < threshold:
        return False

    keep_recent = 6
    old_messages = messages[:-keep_recent]

    if len(old_messages) < 4:
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

    content = result.get("content", "").strip()
    if not content:
        return False

    # 解析 JSON
    summary_data = _parse_summary(content)
    if not summary_data:
        # JSON 解析失败，回退到纯文本
        summary_text = content[:600]
    else:
        # 格式化为可读摘要
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

        # 删除被压缩的旧消息
        old_ids = [m["id"] for m in old_messages if m["role"] != "system"]
        if old_ids:
            placeholders = ",".join("?" * len(old_ids))
            c.execute(
                f"DELETE FROM messages WHERE id IN ({placeholders})",
                old_ids
            )

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
