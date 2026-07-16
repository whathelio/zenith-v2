"""Zenith v2 文件分析器 — 读取 .txt 文件，AI 分析并生成规划"""
from __future__ import annotations

import json
from datetime import datetime
from typing import AsyncGenerator
from .llm_client import chat_stream
from .tools import TOOLS_SCHEMA, execute_tool
from . import database as db


# 分析流程专用工具集（仅允许 create_plan_schedule 和 list_schedule）
ANALYSIS_TOOLS = [
    t for t in TOOLS_SCHEMA
    if t["function"]["name"] in ("create_plan_schedule", "list_schedule")
]


ANALYSIS_SYSTEM_PROMPT = """你是 Zenith 的文件分析和规划助手。用户上传了一个文本文件，你需要进行全面分析并生成可执行的规划。

## 分析流程

请按以下顺序输出分析内容：

### 1. 文件概述
简要说明文件的主要内容和主题（2-3 句话）。

### 2. 要点摘要
提取文件中的关键信息，列出 3-7 个要点，每个要点一行，以 "- " 开头。

### 3. 任务分解
从文件内容中识别需要完成的任务，按优先级分类：
- **高优先级**：紧急或重要的任务
- **中优先级**：常规任务
- **低优先级**：可延后的任务

### 4. 时间规划
为识别出的任务创建日程安排。请为每个任务调用 create_plan_schedule 工具：
- 日程标题可加优先级前缀 [高]/[中]/[低]
- 时间格式：YYYY-MM-DD HH:MM
- 根据任务优先级和依赖关系合理安排时间
- 避免时间冲突

## 注意事项
- 今天是 {today}
- 如果文件内容不包含明确的任务，基于文件主题主动规划后续行动
- 日程时间从今天开始排，不要安排在过去
- 请用中文回复
- 先输出完整的分析文本（概述、要点、任务分解、时间规划说明），然后在最后调用 create_plan_schedule 工具创建所有日程
"""


def build_analysis_system_prompt() -> str:
    """构建分析专用的 system prompt"""
    today = datetime.now().strftime("%Y-%m-%d %A")
    return ANALYSIS_SYSTEM_PROMPT.format(today=today)


def build_export_document(filename: str, analysis_text: str, schedules: list) -> str:
    """生成导出的 .txt 文档内容（Windows 记事本可打开）"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        "=" * 60,
        "  Zenith v2 - 文件分析报告",
        "=" * 60,
        f"  原始文件: {filename}",
        f"  分析时间: {now}",
        "=" * 60,
        "",
        "",
        "## 文件分析",
        "",
        analysis_text,
        "",
        "",
    ]

    if schedules:
        lines.append("## 生成的日程规划")
        lines.append("")
        priority_names = {"high": "高", "normal": "中", "low": "低"}
        for s in schedules:
            p = priority_names.get(s.get("priority", "normal"), "中")
            lines.append(f"  [{p}] {s['title']}")
            lines.append(f"      时间: {s.get('start_time', '待定')}")
            if s.get("end_time"):
                lines.append(f"      结束: {s['end_time']}")
            if s.get("description"):
                lines.append(f"      备注: {s['description']}")
            lines.append("")
    else:
        lines.append("## 生成的日程规划")
        lines.append("")
        lines.append("  （本次分析未生成日程）")
        lines.append("")

    lines.extend([
        "=" * 60,
        f"  共生成 {len(schedules)} 项日程",
        "=" * 60,
    ])

    return "\n".join(lines)


async def analyze_file_stream(
    filename: str,
    file_content: str,
    max_tokens: int = 8192,
) -> AsyncGenerator[dict, None]:
    """
    流式分析文件。
    Yields:
        {"type":"text","content":"..."}          — 分析文本片段
        {"type":"schedule_created","data":{...}}  — 日程创建成功
        {"type":"tool_result","data":{...}}       — 其他工具结果
        {"type":"tool_error","error":"..."}       — 工具错误
        {"type":"analysis_complete","doc_id":N,"schedule_count":N}
    """
    system_prompt = build_analysis_system_prompt()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"请分析以下文件内容（文件名：{filename}）：\n\n---\n{file_content}\n---"},
    ]

    analysis_text = ""
    created_schedules = []

    async for chunk in chat_stream(messages, tools=ANALYSIS_TOOLS, max_tokens=max_tokens):
        if chunk["type"] == "text":
            analysis_text += chunk["content"]
            yield {"type": "text", "content": chunk["content"]}

        elif chunk["type"] == "tool_call":
            result = await execute_tool(chunk["name"], chunk["args"])

            if chunk["name"] == "create_plan_schedule" and result.get("success"):
                created_schedules.append({
                    "id": result["schedule_id"],
                    "title": result["title"],
                    "start_time": result["start_time"],
                    "end_time": result.get("end_time", ""),
                    "priority": result["priority"],
                    "description": chunk["args"].get("description", ""),
                })
                yield {"type": "schedule_created", "data": result}
            elif result.get("success"):
                yield {"type": "tool_result", "data": result}
            else:
                yield {"type": "tool_error", "error": result.get("result", "未知错误")}

    # 生成导出文档并存入数据库
    export_text = build_export_document(filename, analysis_text, created_schedules)
    schedule_ids = json.dumps([s["id"] for s in created_schedules])

    doc_id = db.analysis_add({
        "filename": filename,
        "original_content": file_content,
        "analysis_text": analysis_text,
        "schedule_ids": schedule_ids,
        "export_text": export_text,
    })

    yield {
        "type": "analysis_complete",
        "doc_id": doc_id,
        "schedule_count": len(created_schedules),
    }
