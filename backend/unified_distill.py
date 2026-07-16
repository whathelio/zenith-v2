"""Zenith v2 统一蒸馏模块 — 整合对话总结、日程提炼、记忆浓缩为单一模块，支持 txt 文本输出"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Optional

from .database import (
    conv_get, msg_list, sch_list, mem_list, mem_search, mem_add, mem_for_inject,
    conv_update_summary, conv_update_title,
    conv_list_by_date, mem_list_by_date, note_list_by_date,
)
from .llm_client import call_llm, _parse_json_response
from .memory_engine import _is_duplicate

logger = logging.getLogger("zenith.distill")

# ---------------------------------------------------------------------------
# 数据准备层：从数据库提取原始数据
# ---------------------------------------------------------------------------

def _gather_conversation(conv_id: str) -> dict:
    """从数据库提取单个对话的完整数据"""
    conv = conv_get(conv_id)
    if not conv:
        return {"success": False, "error": f"对话 {conv_id} 不存在"}

    messages = msg_list(conv_id)
    chat_lines = []
    for m in messages:
        if m["role"] == "system":
            continue
        role_label = "用户" if m["role"] == "user" else "AI"
        chat_lines.append(f"{role_label}：{m['content']}")

    return {
        "success": True,
        "conv_id": conv_id,
        "title": conv.get("title", ""),
        "created_at": conv.get("created_at", ""),
        "chat_text": "\n\n".join(chat_lines),
        "msg_count": len(chat_lines),
        "existing_summary": conv.get("summary", ""),
    }


def _gather_schedules(status: str = "", date_from: str = "", date_to: str = "") -> dict:
    """从数据库提取日程数据"""
    schedules = sch_list(status=status, date_from=date_from, date_to=date_to)
    if not schedules:
        return {"success": True, "schedules": [], "count": 0, "schedule_text": "（无日程数据）"}

    lines = []
    for s in schedules:
        imp_stars = "★" * min(s.get("importance", 3), 5)
        cat = s.get("category", "other")
        priority = s.get("priority", "normal")
        line = f"- [{s.get('status', '?')}] {s.get('start_time', '?')} | {s['title']} | 重要度:{imp_stars} | 类别:{cat} | 优先级:{priority}"
        if s.get("description"):
            line += f"\n  说明: {s['description']}"
        lines.append(line)

    return {
        "success": True,
        "schedules": schedules,
        "count": len(schedules),
        "schedule_text": "\n".join(lines),
    }


def _gather_memories(type_: str = "", search: str = "") -> dict:
    """从数据库提取记忆数据"""
    if search:
        memories = mem_search(search)
    else:
        memories = mem_list(type_=type_)

    if not memories:
        return {"success": True, "memories": [], "count": 0, "memory_text": "（无记忆数据）"}

    # 按类型分组
    by_type = {}
    for m in memories:
        t = m.get("type", "unknown")
        if t not in by_type:
            by_type[t] = []
        by_type[t].append(m)

    lines = []
    type_labels = {
        "personal_info": "个人信息",
        "preference": "偏好习惯",
        "event": "事件",
        "decision": "决策",
        "fact": "事实知识",
        "experience": "经验技巧",
    }

    for t, items in by_type.items():
        label = type_labels.get(t, t)
        lines.append(f"## {label} ({len(items)}条)")
        for item in items:
            imp = item.get("importance", 3)
            kw = item.get("keywords", "")
            lines.append(f"  [{imp}★] {item['content']}")
            if kw:
                lines.append(f"       关键词: {kw}")

    return {
        "success": True,
        "memories": memories,
        "by_type": by_type,
        "count": len(memories),
        "memory_text": "\n".join(lines),
    }


# ---------------------------------------------------------------------------
# 每日/每周数据聚合层
# ---------------------------------------------------------------------------

def _gather_daily(date: str) -> dict:
    """聚合指定日期的全部内容（对话+日程+笔记+记忆）"""
    date_start = f"{date} 00:00"
    date_end = f"{date} 23:59"

    # 对话：当天更新的
    convs = conv_list_by_date(date_from=date_start, date_to=date_end)
    chat_lines = []
    for conv in convs:
        msgs = msg_list(conv["id"])
        conv_title = conv.get("title", "New Chat")
        chat_lines.append(f"### 对话: {conv_title} ({conv.get('msg_count', len(msgs))}条)")
        for m in msgs:
            if m["role"] == "system":
                continue
            role_label = "用户" if m["role"] == "user" else "AI"
            # 截取每条消息避免过长
            content_preview = m["content"][:500] if len(m["content"]) > 500 else m["content"]
            chat_lines.append(f"  {role_label}: {content_preview}")

    chat_text = "\n".join(chat_lines) if chat_lines else "（当日无对话）"

    # 日程
    schedules = sch_list(date_from=date_start, date_to=date_end)
    sch_lines = []
    for s in schedules:
        sch_lines.append(f"- [{s.get('status', '?')}] {s.get('start_time', '?')[:16]} | {s['title']}")
    schedule_text = "\n".join(sch_lines) if sch_lines else "（当日无日程）"

    # 笔记
    notes = note_list_by_date(date_from=date_start, date_to=date_end)
    note_lines = []
    for n in notes:
        content_preview = n.get("content", "")[:300]
        note_lines.append(f"- {n['title']}: {content_preview}")
    note_text = "\n".join(note_lines) if note_lines else "（当日无笔记）"

    # 记忆
    memories = mem_list_by_date(date_from=date_start, date_to=date_end)
    mem_lines = []
    for m in memories:
        mem_lines.append(f"- [{m.get('type', '?')}] {m['content'][:200]} ({m.get('importance', 3)}/5)")
    memory_text = "\n".join(mem_lines) if mem_lines else "（当日无新增记忆）"

    return {
        "date": date,
        "chat_text": chat_text,
        "schedule_text": schedule_text,
        "note_text": note_text,
        "memory_text": memory_text,
        "conv_count": len(convs),
        "sch_count": len(schedules),
        "note_count": len(notes),
        "mem_count": len(memories),
    }


def _gather_weekly(week_start: str) -> dict:
    """聚合指定周的全部内容"""
    # 计算周末日期
    from datetime import datetime as dt, timedelta
    start = dt.strptime(week_start, "%Y-%m-%d")
    end = start + timedelta(days=6)
    week_end = end.strftime("%Y-%m-%d")

    date_start = f"{week_start} 00:00"
    date_end = f"{week_end} 23:59"

    # 对话
    convs = conv_list_by_date(date_from=date_start, date_to=date_end)
    chat_lines = []
    for conv in convs:
        msgs = msg_list(conv["id"])
        conv_title = conv.get("title", "New Chat")
        chat_lines.append(f"### 对话: {conv_title} ({conv.get('msg_count', len(msgs))}条)")
        # 周报每条截取更短
        for m in msgs[:20]:  # 限制消息数
            if m["role"] == "system":
                continue
            role_label = "用户" if m["role"] == "user" else "AI"
            content_preview = m["content"][:200]
            chat_lines.append(f"  {role_label}: {content_preview}")
    chat_text = "\n".join(chat_lines) if chat_lines else "（本周无对话）"

    # 日程
    schedules = sch_list(date_from=date_start, date_to=date_end)
    sch_lines = []
    for s in schedules:
        sch_lines.append(f"- [{s.get('status', '?')}] {s.get('start_time', '?')[:16]} | {s['title']}")
    schedule_text = "\n".join(sch_lines) if sch_lines else "（本周无日程）"

    # 笔记
    notes = note_list_by_date(date_from=date_start, date_to=date_end)
    note_lines = []
    for n in notes:
        content_preview = n.get("content", "")[:200]
        note_lines.append(f"- {n['title']}: {content_preview}")
    note_text = "\n".join(note_lines) if note_lines else "（本周无笔记）"

    # 记忆
    memories = mem_list_by_date(date_from=date_start, date_to=date_end)
    mem_lines = []
    for m in memories:
        mem_lines.append(f"- [{m.get('type', '?')}] {m['content'][:150]} ({m.get('importance', 3)}/5)")
    memory_text = "\n".join(mem_lines) if mem_lines else "（本周无新增记忆）"

    return {
        "week_start": week_start,
        "week_end": week_end,
        "chat_text": chat_text,
        "schedule_text": schedule_text,
        "note_text": note_text,
        "memory_text": memory_text,
        "conv_count": len(convs),
        "sch_count": len(schedules),
        "note_count": len(notes),
        "mem_count": len(memories),
    }


# ---------------------------------------------------------------------------
# LLM 蒸馏层：调用 LLM 生成蒸馏结果
# ---------------------------------------------------------------------------

_PROMPT_CONV = """请对以下对话进行深度总结和知识蒸馏。返回 JSON 格式：

{
  "title": "对话标题（≤15字）",
  "summary": "3-5句话的全貌总结",
  "key_decisions": ["决策1", "决策2"],
  "experiences": [
    {"content": "可复用的经验/技巧/踩坑教训", "importance": 1-5, "keywords": "逗号分隔关键词"}
  ],
  "knowledge": ["知识点1", "知识点2"],
  "action_items": ["后续行动1", "后续行动2"],
  "tags": ["标签1", "标签2"]
}

对话内容：
{chat_text}

只返回 JSON，不要其他内容。"""


_PROMPT_SCHEDULE = """请对以下日程数据进行提炼蒸馏。找出规律、遗漏和优化建议。返回 JSON 格式：

{
  "schedule_summary": "日程整体概述（3-5句话）",
  "patterns": ["发现的规律/模式1", "规律2"],
  "gaps": ["遗漏/缺失的安排1", "遗漏2"],
  "suggestions": ["优化建议1", "建议2"],
  "categories": {"类别名": 数量},
  "priority_distribution": {"high": 数量, "normal": 数量, "low": 数量},
  "important_upcoming": ["即将到来的重要事项1", "事项2"]
}

日程数据：
{schedule_text}

只返回 JSON，不要其他内容。"""


_PROMPT_MEMORY = """请对以下记忆数据进行精华蒸馏。合并相似条目、提炼核心洞察、标记过时信息。返回 JSON 格式：

{
  "memory_summary": "记忆库整体概述（3-5句话）",
  "core_insights": ["核心洞察1", "洞察2"],
  "merged_items": [
    {"original_ids": [1,2], "merged_content": "合并后的内容", "type": "类型", "importance": 1-5}
  ],
  "outdated": ["可能过时的记忆1", "过时2"],
  "growth_stats": {"total": 数量, "by_type": {"类型": 数量}},
  "suggestions": ["记忆管理建议1", "建议2"]
}

记忆数据：
{memory_text}

只返回 JSON，不要其他内容。"""


_PROMPT_ALL = """请对以下对话、日程和记忆数据进行全维度综合蒸馏。交叉关联、发现跨维度洞察。返回 JSON 格式：

{
  "overall_summary": "全维度综合概述（5-8句话）",
  "cross_insights": ["跨维度洞察1（如：某个决策影响了日程安排）", "洞察2"],
  "conv_distill": {
    "title": "对话标题",
    "summary": "对话总结",
    "key_decisions": ["决策"],
    "experiences": [{"content": "...", "importance": 1-5, "keywords": "..."}],
    "knowledge": ["知识点"],
    "action_items": ["行动项"]
  },
  "schedule_distill": {
    "patterns": ["日程规律"],
    "gaps": ["遗漏"],
    "suggestions": ["建议"]
  },
  "memory_distill": {
    "core_insights": ["记忆核心洞察"],
    "merged_items": [{"merged_content": "...", "type": "...", "importance": 1-5}],
    "outdated": ["过时信息"]
  },
  "tags": ["标签1", "标签2"]
}

对话内容：
{chat_text}

日程数据：
{schedule_text}

记忆数据：
{memory_text}

只返回 JSON，不要其他内容。"""


_PROMPT_DAILY = """请对以下「当日全部内容」进行综合蒸馏总结。这是 {date} 这一天的全部活动记录。
生成一份结构化的每日总结报告，包括：当日全貌、重要事项、完成与遗漏、经验教训、明日建议。

返回 JSON 格式：
{
  "date": "{date}",
  "headline": "当日一句话概要（≤30字）",
  "daily_summary": "3-5句话的当日全貌描述",
  "key_events": ["当日最重要的事1", "事2"],
  "completed": ["已完成事项1", "2"],
  "missed": ["遗漏/未完成1", "2"],
  "insights": ["当日核心洞察/经验1", "2"],
  "emotions": "当日情绪/状态一句话描述",
  "next_day_suggestions": ["明日建议1", "2"],
  "tags": ["标签1", "标签2"]
}

当日对话：
{chat_text}

当日日程：
{schedule_text}

当日笔记：
{note_text}

当日新增记忆：
{memory_text}

只返回 JSON，不要其他内容。"""


_PROMPT_WEEKLY = """请对以下「本周全部内容」进行综合蒸馏总结。这是 {week_start} ~ {week_end} 这一周的全部活动记录。
生成一份结构化的周度总结报告，包括：周度全貌、重要事项、规律模式、趋势变化、经验总结、下周规划。

返回 JSON 格式：
{
  "week_range": "{week_start} ~ {week_end}",
  "headline": "本周一句话概要（≤30字）",
  "weekly_summary": "5-8句话的本周全貌描述",
  "major_events": ["本周最重要的事1", "事2"],
  "patterns": ["发现的规律/习惯1", "2"],
  "trends": ["趋势/变化1（如：目标进度加快/放缓）", "2"],
  "achievements": ["本周成就1", "2"],
  "lessons": ["经验教训1", "2"],
  "goal_progress": "目标追踪进展一句话描述",
  "next_week_plan": ["下周重点1", "2"],
  "tags": ["标签1", "标签2"]
}

本周对话：
{chat_text}

本周日程：
{schedule_text}

本周笔记：
{note_text}

本周新增记忆：
{memory_text}

只返回 JSON，不要其他内容。"""


async def _call_distill_llm(prompt_template: str, **kwargs) -> dict:
    """调用 LLM 进行蒸馏，返回解析后的 JSON"""
    # 使用字符串替换而非 str.format()，避免 JSON 花括号被误认为占位符
    prompt = prompt_template
    for k, v in kwargs.items():
        prompt = prompt.replace("{" + k + "}", str(v))
    msg = await call_llm(
        [{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=3000,
    )
    content = msg.get("content", "{}")
    result = _parse_json_response(content)
    if isinstance(result, list) and len(result) > 0:
        result = result[0]
    if not isinstance(result, dict):
        result = {"raw": content}
    return result


# ---------------------------------------------------------------------------
# txt 格式输出层：将蒸馏结果转为格式化文本
# ---------------------------------------------------------------------------

_TYPE_LABELS = {
    "personal_info": "个人信息",
    "preference": "偏好习惯",
    "event": "事件",
    "decision": "决策",
    "fact": "事实知识",
    "experience": "经验技巧",
}


def _to_txt_conv(result: dict, conv_id: str, msg_count: int) -> str:
    """对话蒸馏 → txt 格式"""
    lines = [
        "=" * 60,
        "对话总结蒸馏报告",
        "=" * 60,
        f"对话ID: {conv_id}",
        f"标题: {result.get('title', '(无标题)')}",
        f"消息数: {msg_count}",
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "--- 总览 ---",
        result.get("summary", "(无总结)"),
        "",
    ]

    decisions = result.get("key_decisions", [])
    if decisions:
        lines.append("--- 关键决策 ---")
        for i, d in enumerate(decisions, 1):
            lines.append(f"  {i}. {d}")
        lines.append("")

    experiences = result.get("experiences", [])
    if experiences:
        lines.append("--- 经验蒸馏 ---")
        for i, exp in enumerate(experiences, 1):
            imp = exp.get("importance", 3)
            kw = exp.get("keywords", "")
            lines.append(f"  {i}. {exp.get('content', '')}")
            lines.append(f"     重要度: {imp}/5  关键词: {kw}")
        lines.append("")

    knowledge = result.get("knowledge", [])
    if knowledge:
        lines.append("--- 知识点 ---")
        for i, k in enumerate(knowledge, 1):
            lines.append(f"  {i}. {k}")
        lines.append("")

    actions = result.get("action_items", [])
    if actions:
        lines.append("--- 待办行动 ---")
        for i, a in enumerate(actions, 1):
            lines.append(f"  {i}. {a}")
        lines.append("")

    tags = result.get("tags", [])
    if tags:
        lines.append(f"标签: {', '.join(tags)}")

    lines.append("=" * 60)
    return "\n".join(lines)


def _to_txt_schedule(result: dict, count: int) -> str:
    """日程蒸馏 → txt 格式"""
    lines = [
        "=" * 60,
        "日程提炼蒸馏报告",
        "=" * 60,
        f"日程总数: {count}",
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "--- 总览 ---",
        result.get("schedule_summary", "(无总结)"),
        "",
    ]

    patterns = result.get("patterns", [])
    if patterns:
        lines.append("--- 规律模式 ---")
        for i, p in enumerate(patterns, 1):
            lines.append(f"  {i}. {p}")
        lines.append("")

    gaps = result.get("gaps", [])
    if gaps:
        lines.append("--- 遗漏/缺失 ---")
        for i, g in enumerate(gaps, 1):
            lines.append(f"  {i}. {g}")
        lines.append("")

    suggestions = result.get("suggestions", [])
    if suggestions:
        lines.append("--- 优化建议 ---")
        for i, s in enumerate(suggestions, 1):
            lines.append(f"  {i}. {s}")
        lines.append("")

    upcoming = result.get("important_upcoming", [])
    if upcoming:
        lines.append("--- 即将到来 ---")
        for i, u in enumerate(upcoming, 1):
            lines.append(f"  {i}. {u}")
        lines.append("")

    categories = result.get("categories", {})
    if categories:
        lines.append("--- 类别分布 ---")
        for cat, num in categories.items():
            lines.append(f"  {cat}: {num}条")
        lines.append("")

    priority = result.get("priority_distribution", {})
    if priority:
        lines.append("--- 优先级分布 ---")
        for pri, num in priority.items():
            lines.append(f"  {pri}: {num}条")

    lines.append("=" * 60)
    return "\n".join(lines)


def _to_txt_memory(result: dict, count: int) -> str:
    """记忆蒸馏 → txt 格式"""
    lines = [
        "=" * 60,
        "记忆精华蒸馏报告",
        "=" * 60,
        f"记忆总数: {count}",
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "--- 总览 ---",
        result.get("memory_summary", "(无总结)"),
        "",
    ]

    insights = result.get("core_insights", [])
    if insights:
        lines.append("--- 核心洞察 ---")
        for i, ins in enumerate(insights, 1):
            lines.append(f"  {i}. {ins}")
        lines.append("")

    merged = result.get("merged_items", [])
    if merged:
        lines.append("--- 合并建议 ---")
        for i, m in enumerate(merged, 1):
            orig_ids = m.get("original_ids", [])
            lines.append(f"  {i}. 原 ID {orig_ids} → 合并为:")
            lines.append(f"     {m.get('merged_content', '')}")
            lines.append(f"     类型: {_TYPE_LABELS.get(m.get('type', ''), m.get('type', ''))}  重要度: {m.get('importance', 3)}/5")
        lines.append("")

    outdated = result.get("outdated", [])
    if outdated:
        lines.append("--- 可能过时 ---")
        for i, o in enumerate(outdated, 1):
            lines.append(f"  {i}. {o}")
        lines.append("")

    stats = result.get("growth_stats", {})
    if stats:
        lines.append("--- 增长统计 ---")
        lines.append(f"  总计: {stats.get('total', 0)}条")
        by_type = stats.get("by_type", {})
        for t, num in by_type.items():
            label = _TYPE_LABELS.get(t, t)
            lines.append(f"  {label}: {num}条")
        lines.append("")

    suggestions = result.get("suggestions", [])
    if suggestions:
        lines.append("--- 管理建议 ---")
        for i, s in enumerate(suggestions, 1):
            lines.append(f"  {i}. {s}")

    lines.append("=" * 60)
    return "\n".join(lines)


def _to_txt_all(result: dict, conv_msg_count: int, sch_count: int, mem_count: int) -> str:
    """全维度蒸馏 → txt 格式"""
    lines = [
        "=" * 60,
        "全维度综合蒸馏报告",
        "=" * 60,
        f"对话消息数: {conv_msg_count} | 日程数: {sch_count} | 记忆数: {mem_count}",
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "--- 全维度总览 ---",
        result.get("overall_summary", "(无总结)"),
        "",
    ]

    cross = result.get("cross_insights", [])
    if cross:
        lines.append("--- 跨维度洞察 ---")
        for i, c in enumerate(cross, 1):
            lines.append(f"  {i}. {c}")
        lines.append("")

    conv_d = result.get("conv_distill", {})
    if conv_d:
        lines.append("--- 对话维度 ---")
        lines.append(f"  标题: {conv_d.get('title', '')}")
        lines.append(f"  总结: {conv_d.get('summary', '')}")
        for i, d in enumerate(conv_d.get("key_decisions", []), 1):
            lines.append(f"  决策{i}: {d}")
        for i, exp in enumerate(conv_d.get("experiences", []), 1):
            lines.append(f"  经验{i}: {exp.get('content', '')} (重要度:{exp.get('importance', 3)})")
        for i, a in enumerate(conv_d.get("action_items", []), 1):
            lines.append(f"  待办{i}: {a}")
        lines.append("")

    sch_d = result.get("schedule_distill", {})
    if sch_d:
        lines.append("--- 日程维度 ---")
        for i, p in enumerate(sch_d.get("patterns", []), 1):
            lines.append(f"  规律{i}: {p}")
        for i, g in enumerate(sch_d.get("gaps", []), 1):
            lines.append(f"  遗漏{i}: {g}")
        for i, s in enumerate(sch_d.get("suggestions", []), 1):
            lines.append(f"  建议{i}: {s}")
        lines.append("")

    mem_d = result.get("memory_distill", {})
    if mem_d:
        lines.append("--- 记忆维度 ---")
        for i, ins in enumerate(mem_d.get("core_insights", []), 1):
            lines.append(f"  洞察{i}: {ins}")
        for i, m in enumerate(mem_d.get("merged_items", []), 1):
            lines.append(f"  合并{i}: {m.get('merged_content', '')} (类型:{m.get('type', '')} 重要度:{m.get('importance', 3)})")
        for i, o in enumerate(mem_d.get("outdated", []), 1):
            lines.append(f"  过时{i}: {o}")
        lines.append("")

    tags = result.get("tags", [])
    if tags:
        lines.append(f"标签: {', '.join(tags)}")

    lines.append("=" * 60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 核心蒸馏函数：对外接口
# ---------------------------------------------------------------------------

_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "distill")
_PLAN_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "daily_plans")  # 每日/每周蒸馏输出目录


async def distill_conversation(conv_id: str, save_txt: bool = True) -> dict:
    """蒸馏单个对话 — 总结 + 知识提取 + 记忆存储
    
    Args:
        conv_id: 对话ID
        save_txt: 是否保存为 txt 文件
    
    Returns:
        dict: 蒸馏结果 (含 txt_content 字段)
    """
    # 1. 数据准备
    conv_data = _gather_conversation(conv_id)
    if not conv_data["success"]:
        return conv_data

    # 2. LLM 蒸馏
    result = await _call_distill_llm(
        _PROMPT_CONV,
        chat_text=conv_data["chat_text"],
    )

    # 3. 自动存储到记忆库（去重）
    saved_count = 0
    skip_count = 0

    for exp in result.get("experiences", []):
        content = exp.get("content", "").strip()
        if not content:
            continue
        if _is_duplicate(content):
            skip_count += 1
            continue
        mem_add(
            type_="experience",
            content=content,
            importance=exp.get("importance", 3),
            keywords=exp.get("keywords", ""),
            source_conv_id=conv_id,
        )
        saved_count += 1

    tags_str = ",".join(result.get("tags", [])) if isinstance(result.get("tags", []), list) else ""
    for dec in result.get("key_decisions", []):
        if not dec.strip() or _is_duplicate(dec):
            skip_count += 1
            continue
        mem_add(type_="decision", content=dec, importance=4, keywords=tags_str, source_conv_id=conv_id)
        saved_count += 1

    for kn in result.get("knowledge", []):
        if not kn.strip() or _is_duplicate(kn):
            skip_count += 1
            continue
        mem_add(type_="fact", content=kn, importance=3, keywords=tags_str, source_conv_id=conv_id)
        saved_count += 1

    # 4. 更新对话标题和摘要
    title = result.get("title", "").strip()
    if title:
        conv_update_title(conv_id, title)

    summary_text = result.get("summary", "")
    action_items = result.get("action_items", [])
    if action_items:
        summary_text += "\n[待办] " + " | ".join(action_items[:5])
    if summary_text:
        conv_update_summary(conv_id, summary_text)

    # 5. txt 格式化
    txt_content = _to_txt_conv(result, conv_id, conv_data["msg_count"])
    result["txt_content"] = txt_content
    result["saved_count"] = saved_count
    result["skip_count"] = skip_count

    # 6. 保存 txt 文件
    if save_txt:
        result["txt_path"] = _save_txt(txt_content, f"conv_{conv_id}")

    return result


async def distill_schedules(
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    save_txt: bool = True,
) -> dict:
    """蒸馏日程数据 — 规律/遗漏/优化
    
    Args:
        status: 过滤状态 (confirmed/proposed/done/cancelled)
        date_from: 起始日期
        date_to: 结束日期
        save_txt: 是否保存为 txt 文件
    
    Returns:
        dict: 蒸馏结果 (含 txt_content 字段)
    """
    # 1. 数据准备
    sch_data = _gather_schedules(status=status, date_from=date_from, date_to=date_to)

    # 2. LLM 蒸馏
    result = await _call_distill_llm(
        _PROMPT_SCHEDULE,
        schedule_text=sch_data["schedule_text"],
    )

    # 3. txt 格式化
    txt_content = _to_txt_schedule(result, sch_data["count"])
    result["txt_content"] = txt_content
    result["schedule_count"] = sch_data["count"]

    # 4. 保存 txt 文件
    if save_txt:
        result["txt_path"] = _save_txt(txt_content, "schedule_distill")

    return result


async def distill_memories(
    type_: str = "",
    search: str = "",
    save_txt: bool = True,
) -> dict:
    """蒸馏记忆数据 — 精华/合并/过时
    
    Args:
        type_: 过滤类型 (personal_info/preference/event/decision/fact/experience)
        search: 搜索关键词
        save_txt: 是否保存为 txt 文件
    
    Returns:
        dict: 蒸馏结果 (含 txt_content 字段)
    """
    # 1. 数据准备
    mem_data = _gather_memories(type_=type_, search=search)

    # 2. LLM 蒸馏
    result = await _call_distill_llm(
        _PROMPT_MEMORY,
        memory_text=mem_data["memory_text"],
    )

    # 3. txt 格式化
    txt_content = _to_txt_memory(result, mem_data["count"])
    result["txt_content"] = txt_content
    result["memory_count"] = mem_data["count"]

    # 4. 保存 txt 文件
    if save_txt:
        result["txt_path"] = _save_txt(txt_content, "memory_distill")

    return result


async def distill_all(
    conv_id: str = "",
    schedule_status: str = "confirmed",
    memory_type: str = "",
    save_txt: bool = True,
) -> dict:
    """全维度综合蒸馏 — 交叉关联对话/日程/记忆
    
    Args:
        conv_id: 对话ID（可选，空则不蒸馏对话维度）
        schedule_status: 日程过滤状态
        memory_type: 记忆过滤类型
        save_txt: 是否保存为 txt 文件
    
    Returns:
        dict: 蒸馏结果 (含 txt_content 字段)
    """
    # 1. 数据准备
    conv_data = _gather_conversation(conv_id) if conv_id else {"chat_text": "（未指定对话）", "msg_count": 0}
    sch_data = _gather_schedules(status=schedule_status)
    mem_data = _gather_memories(type_=memory_type)

    # 2. LLM 全维度蒸馏
    result = await _call_distill_llm(
        _PROMPT_ALL,
        chat_text=conv_data.get("chat_text", "（未指定对话）"),
        schedule_text=sch_data["schedule_text"],
        memory_text=mem_data["memory_text"],
    )

    # 3. 对话维度的记忆存储（如果有）
    saved_count = 0
    skip_count = 0

    conv_d = result.get("conv_distill", {})
    if conv_d and conv_id and conv_data.get("success"):
        tags_str = ",".join(result.get("tags", [])) if isinstance(result.get("tags", []), list) else ""

        for exp in conv_d.get("experiences", []):
            content = exp.get("content", "").strip()
            if not content or _is_duplicate(content):
                skip_count += 1
                continue
            mem_add(type_="experience", content=content,
                    importance=exp.get("importance", 3),
                    keywords=exp.get("keywords", ""), source_conv_id=conv_id)
            saved_count += 1

        for dec in conv_d.get("key_decisions", []):
            if not dec.strip() or _is_duplicate(dec):
                skip_count += 1
                continue
            mem_add(type_="decision", content=dec, importance=4,
                    keywords=tags_str, source_conv_id=conv_id)
            saved_count += 1

        title = conv_d.get("title", "").strip()
        if title:
            conv_update_title(conv_id, title)

        summary = conv_d.get("summary", "")
        actions = conv_d.get("action_items", [])
        if actions:
            summary += "\n[待办] " + " | ".join(actions[:5])
        if summary:
            conv_update_summary(conv_id, summary)

    # 4. txt 格式化
    txt_content = _to_txt_all(
        result,
        conv_data.get("msg_count", 0),
        sch_data.get("count", 0),
        mem_data.get("count", 0),
    )
    result["txt_content"] = txt_content
    result["saved_count"] = saved_count
    result["skip_count"] = skip_count

    # 5. 保存 txt 文件
    if save_txt:
        result["txt_path"] = _save_txt(txt_content, "all_distill")

    return result


# ---------------------------------------------------------------------------
# txt 文件保存
# ---------------------------------------------------------------------------

def _save_txt(content: str, prefix: str) -> str:
    """将 txt 内容保存到文件
    
    Args:
        content: txt 文本内容
        prefix: 文件名前缀
    
    Returns:
        str: 保存的文件绝对路径
    """
    os.makedirs(_OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{prefix}_{timestamp}.txt"
    filepath = os.path.join(_OUTPUT_DIR, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    logger.info(f"蒸馏 txt 已保存: {filepath}")
    return filepath


def _save_txt_plan(content: str, filename: str) -> str:
    """将 txt 内容保存到每日/每周计划目录

    Args:
        content: txt 文本内容
        filename: 完整文件名（含前缀和日期）

    Returns:
        str: 保存的文件绝对路径
    """
    os.makedirs(_PLAN_DIR, exist_ok=True)
    filepath = os.path.join(_PLAN_DIR, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    logger.info(f"计划和 txt 已保存: {filepath}")
    return filepath


def get_txt_content(result: dict) -> str:
    """从蒸馏结果中获取 txt 文本内容（不保存文件）
    
    适用于只需要文本内容、不想写文件的场景。
    """
    return result.get("txt_content", "")


# ---------------------------------------------------------------------------
# 每日/每周蒸馏主入口
# ---------------------------------------------------------------------------

async def distill_daily(date: str = "", save_txt: bool = True) -> dict:
    """每日内容综合蒸馏
    
    Args:
        date: 目标日期，格式 YYYY-MM-DD，空则取今天
        save_txt: 是否保存为 txt 文件
    
    Returns:
        dict: 蒸馏结果 (含 txt_content 字段)
    """
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    logger.info(f"开始每日蒸馏: {date}")

    # 1. 聚合当日数据
    daily_data = _gather_daily(date)

    # 如果当日没有任何内容，跳过蒸馏
    total = daily_data["conv_count"] + daily_data["sch_count"] + daily_data["note_count"] + daily_data["mem_count"]
    if total == 0:
        logger.info(f"每日蒸馏跳过: {date} 无内容")
        return {
            "date": date,
            "headline": f"{date} 无活动记录",
            "daily_summary": "当日无对话、日程、笔记或记忆数据",
            "key_events": [],
            "completed": [],
            "missed": [],
            "insights": [],
            "emotions": "",
            "next_day_suggestions": [],
            "tags": [],
            "txt_content": "",
            "saved_count": 0,
        }

    # 2. LLM 每日蒸馏
    result = await _call_distill_llm(
        _PROMPT_DAILY,
        date=date,
        chat_text=daily_data["chat_text"],
        schedule_text=daily_data["schedule_text"],
        note_text=daily_data["note_text"],
        memory_text=daily_data["memory_text"],
    )

    # 3. 自动存入记忆库（核心洞察作为 experience）
    saved_count = 0
    for insight in result.get("insights", []):
        if isinstance(insight, str) and insight.strip():
            if not _is_duplicate(insight.strip()):
                mem_add(
                    type_="experience",
                    content=f"[每日总结 {date}] {insight.strip()}",
                    importance=4,
                    keywords=f"每日总结,{date}",
                )
                saved_count += 1

    # 4. txt 格式化
    txt_content = _to_txt_daily(result, daily_data)
    result["txt_content"] = txt_content
    result["saved_count"] = saved_count

    # 5. 保存 txt 文件到每日计划目录
    if save_txt:
        result["txt_path"] = _save_txt_plan(txt_content, f"daily_{date}.txt")

    logger.info(f"每日蒸馏完成: {date}, 已保存{saved_count}条记忆")
    return result


async def distill_weekly(week_start: str = "", save_txt: bool = True) -> dict:
    """每周内容综合蒸馏
    
    Args:
        week_start: 周起始日期(周一)，格式 YYYY-MM-DD，空则取本周
        save_txt: 是否保存为 txt 文件
    
    Returns:
        dict: 蒸馏结果 (含 txt_content 字段)
    """
    if not week_start:
        from datetime import datetime as dt, timedelta
        today = dt.now()
        # 计算本周周一
        monday = today - timedelta(days=(today.weekday()))
        week_start = monday.strftime("%Y-%m-%d")

    logger.info(f"开始每周蒸馏: {week_start}")

    # 1. 聚合本周数据
    weekly_data = _gather_weekly(week_start)

    total = weekly_data["conv_count"] + weekly_data["sch_count"] + weekly_data["note_count"] + weekly_data["mem_count"]
    if total == 0:
        logger.info(f"每周蒸馏跳过: {week_start} 无内容")
        return {
            "week_range": f"{week_start} ~ {weekly_data.get('week_end', week_start)}",
            "headline": "本周无活动记录",
            "weekly_summary": "本周无对话、日程、笔记或记忆数据",
            "major_events": [],
            "patterns": [],
            "trends": [],
            "achievements": [],
            "lessons": [],
            "goal_progress": "",
            "next_week_plan": [],
            "tags": [],
            "txt_content": "",
            "saved_count": 0,
        }

    # 2. LLM 每周蒸馏
    result = await _call_distill_llm(
        _PROMPT_WEEKLY,
        week_start=week_start,
        week_end=weekly_data["week_end"],
        chat_text=weekly_data["chat_text"],
        schedule_text=weekly_data["schedule_text"],
        note_text=weekly_data["note_text"],
        memory_text=weekly_data["memory_text"],
    )

    # 3. 自动存入记忆库
    saved_count = 0
    for lesson in result.get("lessons", []):
        if isinstance(lesson, str) and lesson.strip():
            if not _is_duplicate(lesson.strip()):
                mem_add(
                    type_="experience",
                    content=f"[每周总结 {week_start}] {lesson.strip()}",
                    importance=4,
                    keywords=f"每周总结,{week_start}",
                )
                saved_count += 1
    for pattern in result.get("patterns", []):
        if isinstance(pattern, str) and pattern.strip():
            if not _is_duplicate(pattern.strip()):
                mem_add(
                    type_="fact",
                    content=f"[每周规律 {week_start}] {pattern.strip()}",
                    importance=3,
                    keywords=f"每周规律,{week_start}",
                )
                saved_count += 1

    # 4. txt 格式化
    txt_content = _to_txt_weekly(result, weekly_data)
    result["txt_content"] = txt_content
    result["saved_count"] = saved_count

    # 5. 保存 txt 文件到每日计划目录
    if save_txt:
        result["txt_path"] = _save_txt_plan(txt_content, f"weekly_{week_start}.txt")

    logger.info(f"每周蒸馏完成: {week_start}, 已保存{saved_count}条记忆")
    return result


# ---------------------------------------------------------------------------
# 每日/每周 txt 格式化层
# ---------------------------------------------------------------------------

def _to_txt_daily(result: dict, daily_data: dict) -> str:
    """每日蒸馏 → txt 格式"""
    date = daily_data.get("date", "")
    lines = [
        "=" * 60,
        f"每日总结蒸馏报告 — {date}",
        "=" * 60,
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"当日数据: 对话{daily_data['conv_count']}条 | 日程{daily_data['sch_count']}条 | 笔记{daily_data['note_count']}条 | 记忆{daily_data['mem_count']}条",
        "",
        "--- 一句话概要 ---",
        result.get("headline", ""),
        "",
        "--- 当日全貌 ---",
        result.get("daily_summary", ""),
        "",
    ]

    key_events = result.get("key_events", [])
    if key_events:
        lines.append("--- 重要事项 ---")
        for i, e in enumerate(key_events, 1):
            lines.append(f"  {i}. {e}")
        lines.append("")

    completed = result.get("completed", [])
    if completed:
        lines.append("--- 已完成 ---")
        for i, c in enumerate(completed, 1):
            lines.append(f"  {i}. {c}")
        lines.append("")

    missed = result.get("missed", [])
    if missed:
        lines.append("--- 遗漏/未完成 ---")
        for i, m in enumerate(missed, 1):
            lines.append(f"  {i}. {m}")
        lines.append("")

    insights = result.get("insights", [])
    if insights:
        lines.append("--- 核心洞察 ---")
        for i, ins in enumerate(insights, 1):
            lines.append(f"  {i}. {ins}")
        lines.append("")

    emotions = result.get("emotions", "")
    if emotions:
        lines.append("--- 当日状态 ---")
        lines.append(f"  {emotions}")
        lines.append("")

    suggestions = result.get("next_day_suggestions", [])
    if suggestions:
        lines.append("--- 明日建议 ---")
        for i, s in enumerate(suggestions, 1):
            lines.append(f"  {i}. {s}")
        lines.append("")

    tags = result.get("tags", [])
    if tags:
        lines.append("--- 标签 ---")
        lines.append(f"  {', '.join(tags)}")

    lines.append("=" * 60)
    return "\n".join(lines)


def _to_txt_weekly(result: dict, weekly_data: dict) -> str:
    """每周蒸馏 → txt 格式"""
    ws = weekly_data.get("week_start", "")
    we = weekly_data.get("week_end", "")
    lines = [
        "=" * 60,
        f"每周总结蒸馏报告 — {ws} ~ {we}",
        "=" * 60,
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"本周数据: 对话{weekly_data['conv_count']}条 | 日程{weekly_data['sch_count']}条 | 笔记{weekly_data['note_count']}条 | 记忆{weekly_data['mem_count']}条",
        "",
        "--- 一句话概要 ---",
        result.get("headline", ""),
        "",
        "--- 本周全貌 ---",
        result.get("weekly_summary", ""),
        "",
    ]

    major_events = result.get("major_events", [])
    if major_events:
        lines.append("--- 重要事项 ---")
        for i, e in enumerate(major_events, 1):
            lines.append(f"  {i}. {e}")
        lines.append("")

    patterns = result.get("patterns", [])
    if patterns:
        lines.append("--- 规律模式 ---")
        for i, p in enumerate(patterns, 1):
            lines.append(f"  {i}. {p}")
        lines.append("")

    trends = result.get("trends", [])
    if trends:
        lines.append("--- 趋势变化 ---")
        for i, t in enumerate(trends, 1):
            lines.append(f"  {i}. {t}")
        lines.append("")

    achievements = result.get("achievements", [])
    if achievements:
        lines.append("--- 本周成就 ---")
        for i, a in enumerate(achievements, 1):
            lines.append(f"  {i}. {a}")
        lines.append("")

    lessons = result.get("lessons", [])
    if lessons:
        lines.append("--- 经验教训 ---")
        for i, l in enumerate(lessons, 1):
            lines.append(f"  {i}. {l}")
        lines.append("")

    gp = result.get("goal_progress", "")
    if gp:
        lines.append("--- 目标进展 ---")
        lines.append(f"  {gp}")
        lines.append("")

    next_plan = result.get("next_week_plan", [])
    if next_plan:
        lines.append("--- 下周规划 ---")
        for i, p in enumerate(next_plan, 1):
            lines.append(f"  {i}. {p}")
        lines.append("")

    tags = result.get("tags", [])
    if tags:
        lines.append("--- 标签 ---")
        lines.append(f"  {', '.join(tags)}")

    lines.append("=" * 60)
    return "\n".join(lines)
