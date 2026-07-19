"""Zenith v2 工具注册表 — OpenAI Function Calling Schema + 执行器"""
from __future__ import annotations

import re
import json
import logging
from .database import sch_add, sch_list, sch_update, note_add, note_list, note_update, note_get, mem_search, mem_add, mem_del, mem_list
from .database import prediction_list, prediction_get_hit_rate
from .database import skill_add, skill_get, skill_increment_usage
from . import knowledge_service
from .config import is_code_execution_enabled

logger = logging.getLogger("zenith.tools")


TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "add_schedule",
            "description": "记录新日程。标题可选加优先级前缀：[高]/[中]/[低]。记录后会让用户确认。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "日程标题，可在前面加 [高]/[中]/[低] 标记优先级"
                    },
                    "time": {
                        "type": "string",
                        "description": "时间，如 2026-07-01 15:00"
                    },
                    "note": {
                        "type": "string",
                        "description": "备注（可选）"
                    }
                },
                "required": ["title", "time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_note",
            "description": "记录值得保存的想法、观点或信息。记录后会让用户确认。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "笔记标题"},
                    "content": {"type": "string", "description": "笔记内容"},
                    "tags": {"type": "string", "description": "标签，逗号分隔（可选）"}
                },
                "required": ["title", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_schedule",
            "description": "查询日程列表。用户问有什么安排时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "string",
                        "enum": ["all", "pending", "done"],
                        "description": "过滤条件，默认 pending"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_notes",
            "description": "查询笔记列表或搜索笔记。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "搜索关键词（可选）"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_code",
            "description": "在本地 Python 安全沙箱中执行代码并返回结果。用户要让跑代码时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "要执行的 Python 代码"}
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "搜索已存储的记忆。需要回忆用户之前说过的重要信息时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "搜索关键词"}
                },
                "required": ["keyword"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "complete_schedule",
            "description": "将日程标记为已完成。",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "number", "description": "日程 ID"}
                },
                "required": ["id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "time_plan",
            "description": "分析现有日程的时间安排，检测冲突并给出优化建议。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_plan_schedule",
            "description": "在文件分析流程中创建已确认的日程（无需用户确认）。标题可选加优先级前缀：[高]/[中]/[低]。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "日程标题，可在前面加 [高]/[中]/[低] 标记优先级"
                    },
                    "start_time": {
                        "type": "string",
                        "description": "开始时间，格式 YYYY-MM-DD HH:MM"
                    },
                    "end_time": {
                        "type": "string",
                        "description": "结束时间（可选），格式 YYYY-MM-DD HH:MM"
                    },
                    "description": {
                        "type": "string",
                        "description": "日程描述/备注（可选）"
                    }
                },
                "required": ["title", "start_time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "抓取并读取一个外部网页链接的内容（http/https）。用户发来链接、让你看某个网页、或需要读取在线文档时调用。返回网页正文文本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要访问的完整网页链接，如 https://example.com/article"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "联网搜索网络信息（免 API Key，使用 Bing）。当用户问的最新信息超出你的知识范围、或需要查网络资料时调用。返回标题、链接、摘要。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "max_results": {"type": "integer", "description": "返回结果数量，默认 5（可选）"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_content",
            "description": "分析并总结任意链接内容。自动识别文章或视频，提取文字后生成结构化摘要（核心摘要+关键要点+标签）。用户让你「看看这个链接」「帮我总结这篇文章/视频」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要分析的网页或视频链接"},
                    "language": {"type": "string", "description": "摘要语言，默认 zh-CN（可选）"}
                },
                "required": ["url"]
            }
        }
    },
    # --- 教程模式工具 ---
    {
        "type": "function",
        "function": {
            "name": "create_tutorial",
            "description": "创建分步教程，逐步指导用户完成多步骤任务。每步包含操作说明和验证方法。适用于安装配置、环境搭建、工具使用等需要逐步验证的场景。创建后会返回第一步，用户确认完成后再进入下一步。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "教程标题，如「安装MT5成交量分布指标」"},
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "action": {"type": "string", "description": "用户需要执行的操作"},
                                "verify": {"type": "string", "description": "如何验证这一步是否成功"}
                            },
                            "required": ["action", "verify"]
                        },
                        "description": "步骤列表，每个步骤包含 action(操作) 和 verify(验证方法)"
                    }
                },
                "required": ["title", "steps"]
            }
        }
    },
    # --- 文件安全扫描工具 ---
    {
        "type": "function",
        "function": {
            "name": "scan_file_safety",
            "description": "扫描本地文件或下载的文件的安全性。检测恶意脚本、混淆代码、可疑文件组合等安全风险。用户下载文件后、分析GitHub仓库内容时、或遇到可疑文件时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "要扫描的文件路径"},
                    "file_list": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "多个文件路径列表（可选，扫描文件夹时使用）"
                    }
                },
                "required": ["file_path"]
            }
        }
    },
    # --- MT5 行情工具 ---
    {
        "type": "function",
        "function": {
            "name": "mt5_tick",
            "description": "获取MT5实时Tick报价。用户问黄金实时价格/最新报价/买卖价时调用。需要MT5终端在本机运行。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "交易品种，默认XAUUSD（黄金）"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mt5_rates",
            "description": "获取MT5历史K线数据。用户要看K线/历史行情/价格走势时调用。支持M1/M5/M15/M30/H1/H4/D1等周期。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "交易品种，默认XAUUSD"},
                    "timeframe": {"type": "string", "description": "时间周期: M1/M5/M15/M30/H1/H4/D1/W1/MN1", "enum": ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"]},
                    "count": {"type": "integer", "description": "K线数量，默认100，最大1000"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mt5_volume_profile",
            "description": "计算MT5成交量分布(Volume Profile)，找出POC(最大成交量价位)和价值区域。用户问成交量分布/POC/主力成本区时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "交易品种，默认XAUUSD"},
                    "timeframe": {"type": "string", "description": "时间周期，默认M5"},
                    "count": {"type": "integer", "description": "K线数量，默认200"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mt5_positions",
            "description": "获取MT5当前持仓信息，包括品种/方向/开仓价/当前价/盈亏。用户问持仓/仓位/盈亏时调用。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "distill_note",
            "description": "蒸馏一条 raw 便签：根据记忆模块的偏好/方法，将其分流为整理后的笔记、日程或记忆。",
            "parameters": {
                "type": "object",
                "properties": {
                    "note_id": {"type": "integer", "description": "要蒸馏的便签/笔记 ID"}
                },
                "required": ["note_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "distill_conversation",
            "description": "蒸馏对话：深度总结对话内容，提取经验/决策/知识点，自动存入记忆库。可输出txt文本。用户要求总结对话/蒸馏经验/回顾聊天时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "conv_id": {"type": "string", "description": "对话ID"},
                    "save_txt": {"type": "boolean", "description": "是否保存为txt文件，默认true"}
                },
                "required": ["conv_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "distill_schedules",
            "description": "蒸馏日程：从日程数据中提炼规律/遗漏/优化建议。可输出txt文本。用户要求回顾日程/分析日程规律时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "过滤状态: confirmed/proposed/done/cancelled"},
                    "date_from": {"type": "string", "description": "起始日期 YYYY-MM-DD"},
                    "date_to": {"type": "string", "description": "结束日期 YYYY-MM-DD"},
                    "save_txt": {"type": "boolean", "description": "是否保存为txt文件，默认true"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "distill_memories",
            "description": "蒸馏记忆：从记忆库中浓缩精华洞察，合并相似条目，标记过时信息。可输出txt文本。用户要求回顾记忆/整理记忆库时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "type_": {"type": "string", "description": "过滤类型: personal_info/preference/event/decision/fact/experience"},
                    "search": {"type": "string", "description": "搜索关键词"},
                    "save_txt": {"type": "boolean", "description": "是否保存为txt文件，默认true"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "distill_all",
            "description": "全维度蒸馏：交叉关联对话/日程/记忆数据，发现跨维度洞察。可输出txt文本。用户要求全面回顾/综合蒸馏时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "conv_id": {"type": "string", "description": "对话ID（可选）"},
                    "schedule_status": {"type": "string", "description": "日程过滤状态，默认confirmed"},
                    "memory_type": {"type": "string", "description": "记忆过滤类型"},
                    "save_txt": {"type": "boolean", "description": "是否保存为txt文件，默认true"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "distill_daily",
            "description": "每日蒸馏：聚合指定日期的所有对话/日程/笔记/记忆，生成每日总结报告（标题、要点、完成/遗漏事项、洞察、情绪、明日建议）。可输出txt文本。用户要求总结今天/回顾当日/每日蒸馏时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "日期，格式YYYY-MM-DD，默认今天"},
                    "save_txt": {"type": "boolean", "description": "是否保存为txt文件，默认true"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "distill_weekly",
            "description": "每周蒸馏：聚合指定周（从周一开始）的所有对话/日程/笔记/记忆，生成周总结报告（重大事件、规律/趋势、成就、教训、目标进展、下周计划）。可输出txt文本。用户要求周总结/回顾本周/每周蒸馏时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "week_start": {"type": "string", "description": "周起始日期（周一），格式YYYY-MM-DD，默认本周周一"},
                    "save_txt": {"type": "boolean", "description": "是否保存为txt文件，默认true"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "consolidate_memories",
            "description": "整理/合并/去重记忆库：分析记忆库中的重复或相似条目，生成待执行的合并/删除建议清单。需要先经过用户确认，不会自动删除。当用户提到'合并记忆'、'去重记忆'、'整理记忆库'等意图时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "type_": {
                        "type": "string",
                        "description": "过滤类型: personal_info/preference/event/decision/fact/experience，空表示全部"
                    },
                    "search": {
                        "type": "string",
                        "description": "搜索关键词，限定只整理相关记忆"
                    },
                    "auto_apply": {
                        "type": "boolean",
                        "description": "是否直接执行（默认 false，需用户确认）"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sync_calendar",
            "description": "同步外部财经日历到本地缓存。拉取未来一段时间的财经事件（如非农、CPI、FOMC等）及其真实公布时间，用于后续创建日程时自动校准。",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "同步未来多少天，默认 7 天"
                    },
                    "min_star": {
                        "type": "integer",
                        "description": "最小事件星级，默认 2（1-3）"
                    }
                },
                "required": []
            }
        }
    },
    # --- 智能分类工具 (P0) ---
    {
        "type": "function",
        "function": {
            "name": "smart_classify",
            "description": "智能分类用户输入：一次调用完成意图分类 + 技能卡片提取 + 摘要生成。替代原先多轮分别调用 add_schedule/add_note/add_memory。返回结构化JSON，系统自动根据分类结果创建日程/笔记/技能。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "用户原始输入文本"
                    },
                    "conv_id": {
                        "type": "string",
                        "description": "当前对话ID，用于关联来源（可选）"
                    }
                },
                "required": ["text"]
            }
        }
    },
    # ── 知识库工具（转发到外部 api_gateway） ──
    {
        "type": "function",
        "function": {
            "name": "retrieve_docs",
            "description": "从本地知识库检索与问题相关的文献片段。适用：用户问论文/书籍/文献内容、要求总结资料、查原文出处。不用于记录日程或笔记。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "检索问题"},
                    "top_k": {"type": "integer", "description": "返回片段数，默认5"}
                },
                "required": ["question"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_wiki",
            "description": "查询 LLM Wiki 专题知识库，返回带引用的综述回答。适用：用户问已编译的主题/概念/综述。不用于原始文献片段检索。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Wiki 查询问题"}
                },
                "required": ["question"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "kb_stats",
            "description": "返回本地知识库统计：向量片段数、已处理文献数、OCR 回退数。用于用户问知识库状态。",
            "parameters": {"type": "object", "properties": {}}
        }
    },
]


async def execute_tool(name: str, args: dict) -> dict:
    """
    执行工具并返回结果。
    confirm 字段指示是否需要用户确认。
    """
    try:
        if name == "add_schedule":
            return await _handle_add_schedule(args)
        elif name == "add_note":
            return _handle_add_note(args)
        elif name == "list_schedule":
            return _handle_list_schedule(args)
        elif name == "list_notes":
            return _handle_list_notes(args)
        elif name == "execute_code":
            return await _handle_execute_code(args)
        elif name == "search_memory":
            return _handle_search_memory(args)
        elif name == "complete_schedule":
            return _handle_complete_schedule(args)
        elif name == "time_plan":
            return await _handle_time_plan()
        elif name == "create_plan_schedule":
            return _handle_create_plan_schedule(args)
        elif name == "web_fetch":
            return await _handle_web_fetch(args)
        elif name == "web_search":
            return await _handle_web_search(args)
        elif name == "analyze_content":
            return await _handle_analyze_content(args)
        elif name == "create_tutorial":
            return _handle_create_tutorial(args)
        elif name == "scan_file_safety":
            return _handle_scan_file_safety(args)
        elif name == "mt5_tick":
            return _handle_mt5_tick(args)
        elif name == "mt5_rates":
            return _handle_mt5_rates(args)
        elif name == "mt5_volume_profile":
            return _handle_mt5_volume_profile(args)
        elif name == "mt5_positions":
            return _handle_mt5_positions()
        elif name == "distill_conversation":
            return await _handle_distill_conv(args)
        elif name == "distill_schedules":
            return await _handle_distill_schedules(args)
        elif name == "distill_memories":
            return await _handle_distill_memories(args)
        elif name == "distill_all":
            return await _handle_distill_all(args)
        elif name == "distill_daily":
            return await _handle_distill_daily(args)
        elif name == "distill_weekly":
            return await _handle_distill_weekly(args)
        elif name == "consolidate_memories":
            return await _handle_consolidate_memories(args)
        elif name == "sync_calendar":
            return await _handle_sync_calendar(args)
        elif name == "smart_classify":
            return await _handle_smart_classify(args)
        elif name == "distill_note":
            return await _handle_distill_note(args)
        elif name == "retrieve_docs":
            return await _handle_retrieve_docs(args)
        elif name == "query_wiki":
            return await _handle_query_wiki(args)
        elif name == "kb_stats":
            return await _handle_kb_stats(args)
        else:
            return {"success": False, "result": f"未知工具: {name}"}
    except Exception as e:
        return {"success": False, "result": str(e)}


async def _handle_add_schedule(args: dict) -> dict:
    title = args["title"]
    priority = "normal"

    m = re.match(r'^\[(高|中|低)\]\s*', title)
    if m:
        pmap = {"高": "high", "中": "normal", "低": "low"}
        priority = pmap[m.group(1)]
        title = title[m.end():]

    # 解析自然语言时间
    raw_time = args.get("time", "")
    from .llm_client import extract_datetime
    start_time = await extract_datetime(raw_time) if raw_time else ""

    # 外部财经事件时间校准
    try:
        from .calendar_sync import get_external_event_time
        external = get_external_event_time(title)
        if external and external.get("event_time"):
            start_time = external["event_time"]
    except Exception as e:
        logger.debug("add_schedule external calibration failed: %s", e)

    # 冲突检测与自动错峰
    conflict = _find_time_conflict(start_time)
    if conflict:
        original_time = start_time
        start_time = _suggest_alternative_time(start_time)
        logger.info(
            "add_schedule conflict resolved: conflict_with=%s, original=%s, new=%s",
            conflict.get("id"), original_time, start_time,
        )

    sid = sch_add({
        "title": title,
        "start_time": start_time,
        "description": args.get("note", ""),
        "source": "ai_detect",
        "status": "proposed",
        "priority": priority,
    })
    return {
        "success": True,
        "result": f"日程提议已生成 (ID:{sid})：[{priority}] {title} @ {start_time or raw_time}",
        "confirm": True,
        "confirm_type": "schedule",
        "confirm_id": sid,
    }


def _handle_add_note(args: dict) -> dict:
    nid = note_add({
        "title": args["title"],
        "content": args.get("content", ""),
        "tags": args.get("tags", ""),
        "source": "ai_detect",
        "status": "proposed",
    })
    return {
        "success": True,
        "result": f"笔记提议已生成 (ID:{nid})：{args['title']}",
        "confirm": True,
        "confirm_type": "note",
        "confirm_id": nid,
    }


def _handle_list_schedule(args: dict) -> dict:
    flt = args.get("filter", "pending")
    if flt == "pending":
        items = sch_list(status="confirmed") + sch_list(status="proposed")
    elif flt == "done":
        items = sch_list(status="done")
    else:
        items = sch_list()

    if not items:
        return {"success": True, "result": "暂无日程。"}

    lines = [f"📅 日程列表（{flt}）："]
    icons = {"confirmed": "⏳", "done": "✅", "proposed": "🟡", "cancelled": "❌"}
    for s in items:
        icon = icons.get(s["status"], "⏳")
        lines.append(f"  {icon} [ID:{s['id']}] {s['title']} @ {s.get('start_time','待定')}")
    return {"success": True, "result": "\n".join(lines)}


def _handle_list_notes(args: dict) -> dict:
    kw = args.get("keyword", "")
    items = note_list(search=kw)
    if not items:
        return {"success": True, "result": "暂无笔记。"}
    lines = ["📝 笔记："]
    for n in items:
        lines.append(f"  [ID:{n['id']}] {n['title']}")
    return {"success": True, "result": "\n".join(lines)}


async def _handle_execute_code(args: dict) -> dict:
    if not is_code_execution_enabled():
        return {
            "success": False,
            "result": "代码执行已禁用。在 config.yaml 设 code_execution_enabled: true 启用（仅限本地单用户，多用户部署见 SECURITY.md）",
        }
    from .code_runner import run
    r = await run(args.get("code", ""))
    return {"success": r["success"], "result": r["output"]}


def _handle_search_memory(args: dict) -> dict:
    items = mem_search(keyword=args.get("keyword", ""))
    if not items:
        return {"success": True, "result": "未找到相关记忆。"}
    lines = ["🧠 相关记忆："]
    for m in items:
        lines.append(f"  [{m['type']}] {m['content']}")
    return {"success": True, "result": "\n".join(lines)}


def _handle_complete_schedule(args: dict) -> dict:
    sch_update(args["id"], {"status": "done"})
    return {"success": True, "result": f"日程 (ID:{args['id']}) 已标记完成。"}


async def _handle_time_plan() -> dict:
    from .llm_client import plan_time
    items = sch_list(status="confirmed")
    if not items:
        return {"success": True, "result": "暂无已确认的日程可供分析。"}
    advice = await plan_time(items)
    return {"success": True, "result": advice}


def _handle_create_plan_schedule(args: dict) -> dict:
    """文件分析专用：创建 confirmed 状态日程（跳过 proposal 确认流程）"""
    title = args["title"]
    priority = "normal"

    m = re.match(r'^\[(高|中|低)\]\s*', title)
    if m:
        pmap = {"高": "high", "中": "normal", "低": "low"}
        priority = pmap[m.group(1)]
        title = title[m.end():]

    sid = sch_add({
        "title": title,
        "start_time": args.get("start_time", ""),
        "end_time": args.get("end_time", ""),
        "description": args.get("description", ""),
        "source": "file_analysis",
        "status": "confirmed",
        "priority": priority,
    })
    return {
        "success": True,
        "result": f"日程已创建 (ID:{sid})：[{priority}] {title} @ {args.get('start_time','')}",
        "schedule_id": sid,
        "title": title,
        "start_time": args.get("start_time", ""),
        "end_time": args.get("end_time", ""),
        "priority": priority,
    }


async def _handle_web_fetch(args: dict) -> dict:
    """抓取网页链接内容"""
    from .web_tools import fetch_url
    url = args.get("url", "").strip()
    if not url:
        return {"success": False, "result": "url 不能为空"}
    return await fetch_url(url)


async def _handle_web_search(args: dict) -> dict:
    """联网搜索（Bing，免 Key）"""
    from .web_tools import web_search
    query = args.get("query", "").strip()
    max_results = args.get("max_results", 5)
    return await web_search(query, max_results=max_results)


async def _handle_analyze_content(args: dict) -> dict:
    """分析链接内容: 文章或视频 → 提取 → 总结

    注意: analyze_content 返回 {success, summary, source_text, ...}，
    但 app.py 工具回传取 result['result'] 字段，必须组装 result 字段，
    否则模型拿到空字符串，看不到总结结果。
    """
    from .content_tools import analyze_content
    url = args.get("url", "").strip()
    language = args.get("language", "zh-CN")
    result = await analyze_content(url, language=language)

    # 错误时 analyze_content 已含 result 字段，直接返回
    if not result.get("success"):
        return result

    # 组装模型可读的 result 字段
    parts = []
    if result.get("title"):
        parts.append(f"标题: {result['title']}")
    if result.get("author"):
        parts.append(f"作者: {result['author']}")
    if result.get("method"):
        method_map = {
            "bilibili_subtitles": "B站字幕", "bilibili_description": "B站简介",
            "bilibili_title_only": "B站标题", "github_readme": "GitHub README",
            "github_description": "GitHub描述", "github_title_only": "仓库名",
            "subtitles": "内嵌字幕", "stt": "语音转文字", "metadata": "元数据",
        }
        parts.append(f"提取方式: {method_map.get(result['method'], result['method'])}")
    parts.append(f"\n{result.get('summary', '(无摘要)')}")
    result["result"] = "\n".join(parts)
    return result


# ---------------------------------------------------------------------------
# Tutorial Flow Handler
# ---------------------------------------------------------------------------

def _handle_create_tutorial(args: dict) -> dict:
    """创建分步教程会话"""
    from .confirm_flow import TutorialFlow

    title = args.get("title", "")
    steps = args.get("steps", [])
    if not title or not steps:
        return {"success": False, "result": "title 和 steps 不能为空"}

    flow = TutorialFlow.create(title, steps)
    current = flow.current_step()

    lines = [
        f"📋 教程已创建: {title}",
        f"共 {current['total_steps']} 步，当前第 {current['step_index']} 步",
        f"\n步骤 {current['step_index']}: {current['action']}",
        f"验证: {current['verify']}",
        f"\n完成后请回复「确认」进入下一步，或回复「失败」+原因",
    ]
    return {
        "success": True,
        "result": "\n".join(lines),
        "tutorial_session_id": flow.session_id,
        "tutorial_step": current,
    }


# ---------------------------------------------------------------------------
# File Safety Scanner Handler
# ---------------------------------------------------------------------------

def _handle_scan_file_safety(args: dict) -> dict:
    """扫描文件安全性"""
    from .content_tools import scan_file_safety

    file_path = args.get("file_path", "")
    file_list = args.get("file_list", [])

    if file_list:
        # 批量扫描
        results = []
        for fp in file_list:
            r = scan_file_safety(fp)
            results.append(r)
        high_risk = [r for r in results if r.get("risk_level") == "high"]
        overall_level = "high" if high_risk else ("medium" if any(r.get("risk_level") == "medium" for r in results) else "low")
        return {
            "success": True,
            "result": f"扫描完成: {len(results)} 个文件, 风险等级={overall_level}, 高风险={len(high_risk)}",
            "risk_level": overall_level,
            "details": results,
        }
    elif file_path:
        result = scan_file_safety(file_path)
        lines = [
            f"🔍 文件安全扫描: {file_path}",
            f"风险等级: {result['risk_level']}",
        ]
        if result.get("risks"):
            lines.append("发现风险:")
            for r in result["risks"]:
                lines.append(f"  - [{r['severity']}] {r['description']}")
        lines.append(f"\n建议: {result['recommendation']}")
        return {
            "success": True,
            "result": "\n".join(lines),
            "risk_level": result["risk_level"],
            "risks": result.get("risks", []),
            "recommendation": result["recommendation"],
        }
    else:
        return {"success": False, "result": "file_path 不能为空"}


# ---------------------------------------------------------------------------
# MT5 Handler
# ---------------------------------------------------------------------------

def _handle_mt5_tick(args: dict) -> dict:
    """获取 MT5 实时 Tick 报价"""
    from .mt5_service import get_tick

    symbol = args.get("symbol", "XAUUSD")
    result = get_tick(symbol)

    if not result.get("success"):
        return result

    lines = [
        f"📊 {symbol} 实时报价:",
        f"  买价(Bid): {result['bid']}",
        f"  卖价(Ask): {result['ask']}",
        f"  最新价: {result.get('last', 'N/A')}",
        f"  点差: {result['spread']}",
        f"  时间: {result['time']}",
    ]
    return {"success": True, "result": "\n".join(lines), **result}


def _handle_mt5_rates(args: dict) -> dict:
    """获取 MT5 历史 K 线"""
    from .mt5_service import get_rates

    symbol = args.get("symbol", "XAUUSD")
    timeframe = args.get("timeframe", "M5")
    count = args.get("count", 100)

    result = get_rates(symbol, timeframe, count)
    if not result.get("success"):
        return result

    stats = result.get("stats", {})
    lines = [
        f"📈 {symbol} {timeframe} K线 ({result['count']}根):",
        f"  开盘: {stats.get('open', 'N/A')}",
        f"  最高: {stats.get('high', 'N/A')}",
        f"  最低: {stats.get('low', 'N/A')}",
        f"  收盘: {stats.get('close', 'N/A')}",
        f"  涨跌: {stats.get('change', 'N/A')} ({stats.get('change_pct', 'N/A')}%)",
        f"  均量: {stats.get('avg_volume', 'N/A')}",
    ]
    return {"success": True, "result": "\n".join(lines), **result}


def _handle_mt5_volume_profile(args: dict) -> dict:
    """获取 MT5 成交量分布"""
    from .mt5_service import get_volume_profile

    symbol = args.get("symbol", "XAUUSD")
    timeframe = args.get("timeframe", "M5")
    count = args.get("count", 200)

    result = get_volume_profile(symbol, timeframe, count)
    if not result.get("success"):
        return result

    lines = [
        f"📊 {symbol} 成交量分布 ({timeframe}, {result['count']}根K线):",
        f"  POC(最大成交量价位): {result['poc_price']}",
        f"  价值区域上沿: {result['value_area_high']}",
        f"  价值区域下沿: {result['value_area_low']}",
        f"  总成交量: {result['total_volume']}",
        f"  分箱大小: {result['bin_size']}",
        f"\n  POC是主力成本最密集的区域，通常作为关键支撑/阻力位",
    ]

    # 显示前5个最大成交量的箱
    top_bins = sorted(result.get("bins", []), key=lambda x: x["volume"], reverse=True)[:5]
    if top_bins:
        lines.append("\n  成交量最大的5个价位:")
        for i, b in enumerate(top_bins):
            lines.append(f"    {i+1}. {b['price_low']:.2f}-{b['price_high']:.2f} ({b['pct']}%)")

    return {"success": True, "result": "\n".join(lines), **result}


def _handle_mt5_positions() -> dict:
    """获取 MT5 当前持仓"""
    from .mt5_service import get_positions

    result = get_positions()
    if not result.get("success"):
        return result

    if result["count"] == 0:
        return {"success": True, "result": "当前无持仓"}

    lines = [f"📋 当前持仓 ({result['count']}个):"]
    lines.append(f"  总盈亏: {result['total_profit']}")
    for p in result["positions"]:
        icon = "🟢" if p["type"] == "buy" else "🔴"
        lines.append(
            f"  {icon} {p['symbol']} {p['type']} {p['volume']}手 "
            f"@ {p['price_open']} -> {p['price_current']} "
            f"盈亏={p['profit']}"
        )
    return {"success": True, "result": "\n".join(lines), **result}


# ---------------------------------------------------------------------------
# 统一蒸馏工具处理
# ---------------------------------------------------------------------------

async def _handle_distill_conv(args: dict) -> dict:
    """处理对话蒸馏工具调用"""
    from .unified_distill import distill_conversation

    conv_id = args.get("conv_id", "").strip()
    if not conv_id:
        return {"success": False, "result": "请指定对话ID"}

    save_txt = args.get("save_txt", True)
    result = await distill_conversation(conv_id, save_txt=save_txt)

    if not result.get("success", True) and "error" in result:
        return {"success": False, "result": result["error"]}

    # 格式化输出给对话
    txt = result.get("txt_content", "")
    txt_path = result.get("txt_path", "")
    lines = [
        "对话蒸馏完成！",
        f"  保存记忆数: {result.get('saved_count', 0)}",
        f"  去重跳过数: {result.get('skip_count', 0)}",
        f"  标题: {result.get('title', '')}",
    ]
    if txt_path:
        lines.append(f"  txt文件: {txt_path}")

    return {"success": True, "result": "\n".join(lines), "txt_content": txt, "txt_path": txt_path, **result}


async def _handle_distill_schedules(args: dict) -> dict:
    """处理日程蒸馏工具调用"""
    from .unified_distill import distill_schedules

    result = await distill_schedules(
        status=args.get("status", ""),
        date_from=args.get("date_from", ""),
        date_to=args.get("date_to", ""),
        save_txt=args.get("save_txt", True),
    )

    txt = result.get("txt_content", "")
    txt_path = result.get("txt_path", "")
    lines = [
        "日程蒸馏完成！",
        f"  日程数: {result.get('schedule_count', 0)}",
    ]
    if txt_path:
        lines.append(f"  txt文件: {txt_path}")

    return {"success": True, "result": "\n".join(lines), "txt_content": txt, "txt_path": txt_path, **result}


async def _handle_distill_memories(args: dict) -> dict:
    """处理记忆蒸馏工具调用"""
    from .unified_distill import distill_memories

    result = await distill_memories(
        type_=args.get("type_", ""),
        search=args.get("search", ""),
        save_txt=args.get("save_txt", True),
    )

    txt = result.get("txt_content", "")
    txt_path = result.get("txt_path", "")
    lines = [
        "记忆蒸馏完成！",
        f"  记忆数: {result.get('memory_count', 0)}",
    ]
    if txt_path:
        lines.append(f"  txt文件: {txt_path}")

    return {"success": True, "result": "\n".join(lines), "txt_content": txt, "txt_path": txt_path, **result}


async def _handle_distill_all(args: dict) -> dict:
    """处理全维度蒸馏工具调用"""
    from .unified_distill import distill_all

    result = await distill_all(
        conv_id=args.get("conv_id", ""),
        schedule_status=args.get("schedule_status", "confirmed"),
        memory_type=args.get("memory_type", ""),
        save_txt=args.get("save_txt", True),
    )

    txt = result.get("txt_content", "")
    txt_path = result.get("txt_path", "")
    lines = [
        "全维度蒸馏完成！",
        f"  保存记忆数: {result.get('saved_count', 0)}",
        f"  去重跳过数: {result.get('skip_count', 0)}",
    ]
    if txt_path:
        lines.append(f"  txt文件: {txt_path}")

    return {"success": True, "result": "\n".join(lines), "txt_content": txt, "txt_path": txt_path, **result}


async def _handle_distill_daily(args: dict) -> dict:
    """处理每日蒸馏工具调用"""
    from .unified_distill import distill_daily

    date = args.get("date", "").strip()
    if not date:
        from datetime import datetime
        date = datetime.now().strftime("%Y-%m-%d")

    save_txt = args.get("save_txt", True)
    result = await distill_daily(date=date, save_txt=save_txt)

    if not result.get("success", True) and "error" in result:
        return {"success": False, "result": result["error"]}

    txt = result.get("txt_content", "")
    txt_path = result.get("txt_path", "")
    lines = [
        f"每日蒸馏完成（{date}）！",
        f"  对话数: {result.get('conv_count', 0)}",
        f"  日程数: {result.get('schedule_count', 0)}",
        f"  笔记数: {result.get('note_count', 0)}",
        f"  记忆数: {result.get('memory_count', 0)}",
        f"  标题: {result.get('headline', '')}",
    ]
    if txt_path:
        lines.append(f"  txt文件: {txt_path}")

    return {"success": True, "result": "\n".join(lines), "txt_content": txt, "txt_path": txt_path, **result}


# ---------------------------------------------------------------------------
# Smart Classify (P0) — 合并分类+技能+摘要到单次 API 调用
# ---------------------------------------------------------------------------

SMART_CLASSIFY_PROMPT = """\
你是一个智能分类器，请对用户输入进行一次分析，输出以下结构化JSON（不要加markdown代码块，直接输出JSON）。

当前时间：{current_datetime}

{
  "classification": {
    "is_plan": false,
    "is_thought": false,
    "is_skill": false,
    "is_memory": false,
    "keywords": ["关键词1", "关键词2"]
  },
  "plan": {
    "start_time": "YYYY-MM-DDTHH:MM:SS+08:00 或空字符串",
    "end_time": "YYYY-MM-DDTHH:MM:SS+08:00 或空字符串",
    "location": "地点（可选）",
    "priority": "low|normal|high"
  },
  "memory_info": null,
  "skill_card": null,
  "summary": "一句话摘要"
}

分类规则（四个字段互相独立，可同时为 true）：
- is_plan: 用户在安排/确认一个日程、会议、提醒、任务等时间相关事件 → true
- is_thought: 用户在表达想法、观点、待整理的信息、感受 → true
- is_skill: 用户描述了一个可重复的操作流程、方法论、步骤化的技巧 → true
- is_memory: 用户陈述了应当长期记住的客观信息（个人信息/偏好/事实/决定/经验） → true
- keywords: 提取2-5个核心关键词

is_memory 判定细则（命中任一即 true）：
- personal_info: 姓名/住址/生日/联系方式等个人档案信息
- preference: 明确的喜好或习惯（"我喜欢…"、"我习惯…"、"我不吃辣"）
- fact: 客观事实陈述（"比特币减半在2024年4月"）
- decision: 已做的决定（"我决定每周日复盘"、"我选方案B"）
- experience: 可复用的经验/教训（注意：如果是步骤化方法论，应同时 is_skill=true）

如果 is_plan=true，必须同时输出 plan 字段，并基于"当前时间"推算 start_time。
示例（假设当前时间为 2026-07-18 00:22:52）：
  用户输入："明天下午三点开会" → start_time 应填 "2026-07-19T15:00:00+08:00"
  用户输入："提醒我周末买菜" → start_time 可填 "2026-07-19T09:00:00+08:00"
  用户输入："下周一汇报" → start_time 应填 "2026-07-21T09:00:00+08:00"
  用户输入没有时间信息 → start_time 为空字符串

如果 is_memory=true，必须同时输出 memory_info：
{
  "classification": {..., "is_memory": true},
  "plan": {...},
  "memory_info": {
    "type": "personal_info|preference|fact|decision|experience",
    "content": "精炼后的记忆内容（去掉口语化冗余）",
    "importance": 1-5,
    "keywords": "关键词,逗号分隔"
  },
  "skill_card": null,
  "summary": "一句话摘要"
}

如果 is_skill=true，必须同时输出 skill_card：
{
  "classification": {..., "is_skill": true},
  "plan": {...},
  "memory_info": null,
  "skill_card": {
    "name": "技能名称（简短）",
    "trigger_scene": "什么时候应该用这个技能",
    "steps": ["步骤1", "步骤2", ...],
    "tags": ["标签1", "标签2"]
  },
  "summary": "一句话摘要"
}

混合示例："我决定每周日做复盘，步骤是先看日程再看笔记最后写总结"
→ is_plan=false, is_thought=false, is_skill=true, is_memory=true (decision)
→ memory_info.type=decision, content="决定每周日做复盘"
→ skill_card 描述复盘步骤

非技能、非记忆时，skill_card 和 memory_info 必须为 null。

只输出JSON，不要加任何解释文字。"""


def _extract_json(text: str) -> dict:
    """从文本中提取 JSON 对象，支持多种格式"""
    import re as _re
    text = text.strip()
    if not text:
        raise ValueError("empty response")

    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试 markdown code block
    m = _re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试找到第一个 { ... } 块
    m = _re.search(r'\{[\s\S]*\}', text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass

    raise ValueError(f"无法从响应中提取 JSON: {text[:200]}")


# 录入时去重辅助 — 用 SequenceMatcher 比对现有记忆，相似度>阈值返回已有记忆 ID（不删除）
def _find_duplicate_memory(
    content: str,
    keywords: str = "",
    mem_type: str = "",
    threshold: float = 0.7,
) -> int | None:
    """检查 memory 内容是否已存在相似条目。

    Args:
        content: 待检查的记忆内容
        keywords: 关键词字符串（逗号分隔），用于搜索候选
        mem_type: 记忆类型，相同类型优先匹配
        threshold: 相似度阈值 (0-1)，>=threshold 视为重复

    Returns:
        已存在的记忆 ID（若有重复），否则 None
    """
    if not content or not content.strip():
        return None
    from difflib import SequenceMatcher
    from .database import mem_search as _mem_search, mem_list as _mem_list

    # 1. 收集候选：按关键词搜索 + 按类型列出最近 30 条
    candidates: list[dict] = []
    seen_ids: set[int] = set()
    kws = [k.strip() for k in (keywords or "").split(",") if k.strip()]
    for kw in kws[:3]:
        for m in _mem_search(kw)[:8]:
            mid = m.get("id")
            if mid not in seen_ids:
                seen_ids.add(mid)
                candidates.append(m)
    # 按类型补充
    if mem_type:
        for m in _mem_list(type_=mem_type)[:30]:
            mid = m.get("id")
            if mid not in seen_ids:
                seen_ids.add(mid)
                candidates.append(m)
    else:
        for m in _mem_list()[:30]:
            mid = m.get("id")
            if mid not in seen_ids:
                seen_ids.add(mid)
                candidates.append(m)

    # 2. 比对相似度
    content_norm = content.strip().lower()
    best_id: int | None = None
    best_ratio: float = 0.0
    for m in candidates:
        existing = (m.get("content") or "").strip().lower()
        if not existing:
            continue
        # 同类型加权（让同类型更容易命中）
        ratio = SequenceMatcher(None, content_norm, existing).ratio()
        if m.get("type") == mem_type and mem_type:
            ratio = min(1.0, ratio + 0.05)
        if ratio > best_ratio:
            best_ratio = ratio
            best_id = m.get("id")

    if best_ratio >= threshold and best_id is not None:
        logger.debug("memory dedup hit: ratio=%.3f, existing_id=%s", best_ratio, best_id)
        return best_id
    return None


def _find_time_conflict(start_time: str, end_time: str = "", exclude_id: int | None = None, buffer_minutes: int = 5) -> dict | None:
    """检查给定时间段是否与已有日程冲突。

    返回冲突日程的 dict，无冲突返回 None。
    """
    if not start_time:
        return None
    try:
        from datetime import datetime, timedelta
        from .timezone import now_tz
        dt = datetime.fromisoformat(start_time)
        # 简单默认 duration = 30min
        end_dt = datetime.fromisoformat(end_time) if end_time else dt + timedelta(minutes=30)
        # 前后各留 buffer
        window_start = dt - timedelta(minutes=buffer_minutes)
        window_end = end_dt + timedelta(minutes=buffer_minutes)

        # 只检查未来 14 天内的日程，避免全表扫描
        range_end = (now_tz() + timedelta(days=14)).isoformat()
        candidates = sch_list(date_from=window_start.isoformat(), date_to=range_end, status="confirmed")
        for s in candidates:
            sid = s.get("id")
            if exclude_id is not None and sid == exclude_id:
                continue
            s_start = s.get("start_time", "")
            if not s_start:
                continue
            try:
                s_dt = datetime.fromisoformat(s_start)
                s_end = s_dt + timedelta(minutes=30)
            except Exception:
                continue
            # 重叠判断
            if s_dt < window_end and s_end > window_start:
                return dict(s)
    except Exception as e:
        logger.debug("time conflict check failed: %s", e)
    return None


def _suggest_alternative_time(start_time: str, duration_minutes: int = 30, step_minutes: int = 30, max_try: int = 5) -> str:
    """当检测到时间冲突时，向后顺延寻找可用时间段。"""
    from datetime import datetime, timedelta
    try:
        dt = datetime.fromisoformat(start_time)
    except Exception:
        return start_time
    for _ in range(max_try):
        dt += timedelta(minutes=step_minutes)
        end = dt + timedelta(minutes=duration_minutes)
        if _find_time_conflict(dt.isoformat(), end.isoformat()) is None:
            return dt.isoformat()
    # 都冲突则返回原时间（由上层决定）
    return start_time


async def _distill_raw_note(
    note_id: int,
    text: str,
    classification: dict,
    plan_info: dict,
    conv_id: str = "",
    recorded_at: str = "",
) -> dict:
    """第二阶段：根据记忆模块的偏好/方法，对 raw note 进行蒸馏分流。
    输出：整理后的笔记、日程、或记忆。
    """
    from .llm_client import call_llm, extract_datetime
    from .database import mem_search, mem_list, sch_add, note_update, note_get, mem_add

    actions = []
    created_ids = {}

    # 1. 检索相关记忆：偏好 + 方法（experience）
    keywords = classification.get("keywords", [])
    related_mems = []
    for kw in keywords[:3]:
        related_mems.extend(mem_search(kw)[:5])
    # 去重并优先取 preference / experience
    seen = set()
    filtered_mems = []
    for m in related_mems:
        mid = m.get("id")
        if mid in seen:
            continue
        seen.add(mid)
        if m.get("type") in ("preference", "experience"):
            filtered_mems.append(m)
    # 补充最近的 preference / experience
    for m in mem_list(type_="preference")[:10]:
        if m.get("id") not in seen:
            filtered_mems.append(m)
    for m in mem_list(type_="experience")[:10]:
        if m.get("id") not in seen:
            filtered_mems.append(m)

    memory_context = []
    for i, m in enumerate(filtered_mems[:20], 1):
        t = m.get("type", "")
        c = m.get("content", "")[:200]
        mid = m.get("id")
        memory_context.append(f"{i}. [ID:{mid}][{t}] {c}")
    memory_text = "\n".join(memory_context) if memory_context else "（暂无相关偏好或方法记忆）"

    prompt = f"""你是一个个人知识管理助手。请根据以下用户原始便签内容和相关记忆偏好/方法，判断该便签最适合被整理成哪种形式。

规则：
- 如果包含时间、会议、提醒、任务等可执行安排 → 输出为 schedule（日程）
- 如果是想法、观点、待整理的信息 → 输出为 note（已整理笔记）
- 如果是个人偏好、重要事实、可复用经验 → 输出为 memory（记忆）
- 同一内容可能同时产生多种类型，请在 JSON 中列出

【去重关键规则】
- 仔细对照下方"相关记忆偏好/方法"，若已有记忆在语义上覆盖了便签内容（表述可不同但含义相同），memory.should_create 必须设为 false
- 例如：已有"我喜欢喝咖啡"，新便签"我每天必须喝咖啡" → should_create=false
- 例如：已有"比特币减半在2024年4月"，新便签"BTC 减半日期是 2024-04-20" → should_create=false
- 仅当便签带来新的、未覆盖的信息时才 should_create=true

输出 JSON 格式（不要加 markdown 代码块）：
{{
  "decision": {{
    "note": {{ "should_refine": true/false, "title": "整理后的标题", "content": "整理后的内容（可保留原文）", "tags": ["标签1"] }},
    "schedule": {{ "should_create": true/false, "title": "日程标题", "start_time": "YYYY-MM-DDTHH:MM:SS+08:00", "priority": "low|normal|high", "description": "描述" }},
    "memory": {{ "should_create": true/false, "type": "preference|experience|fact|decision", "content": "记忆内容", "importance": 1-5, "keywords": "关键词,逗号分隔", "duplicate_of": null }}
  }},
  "reason": "分流理由（一句话）"
}}

若 memory.should_create=false 且原因是已存在相似记忆，可在 memory.duplicate_of 填入已有记忆的真实 ID（如上方 [ID:12] 的 12），否则为 null。

原始便签：
{text}

相关记忆偏好/方法：
{memory_text}

只输出 JSON，不要其他内容。"""

    try:
        response = await call_llm(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=1500,
            response_format={"type": "json_object"},
        )
        result_text = response.get("content", "") if isinstance(response, dict) else str(response)
        parsed = _extract_json(result_text)
    except Exception as e:
        logger.error(f"_distill_raw_note LLM failed: {e}")
        # LLM 失败时，保守处理：保持 raw note 不变
        return {"actions": [], "created_ids": {}}

    decision = parsed.get("decision", {})
    from .timezone import now_tz
    now_iso = recorded_at or now_tz().isoformat()

    # 2. 创建整理后的笔记
    note_part = decision.get("note", {})
    if note_part.get("should_refine"):
        note_update(note_id, {
            "title": note_part.get("title", text[:30]),
            "content": note_part.get("content", text),
            "tags": ",".join(note_part.get("tags", [])),
            "status": "proposed",
            "stage": "refined",
            "distilled_at": now_iso,
        })
        actions.append(f"整理笔记 (ID:{note_id})")

    # 3. 创建日程
    schedule_part = decision.get("schedule", {})
    if schedule_part.get("should_create"):
        start_time = schedule_part.get("start_time", "")
        if not start_time and plan_info.get("start_time"):
            start_time = plan_info.get("start_time")
        if not start_time:
            start_time = await extract_datetime(text)

        title = schedule_part.get("title", text[:50])

        # 3a. 外部财经事件时间校准（仅当标题匹配金融事件时）
        try:
            from .calendar_sync import get_external_event_time
            external = get_external_event_time(title)
            if external and external.get("event_time"):
                logger.info(
                    "schedule time calibrated by external calendar: title=%s, old=%s, new=%s",
                    title, start_time, external["event_time"],
                )
                start_time = external["event_time"]
        except Exception as e:
            logger.debug("external calendar calibration failed: %s", e)

        # 3b. 时间冲突检测与自动错峰
        conflict = _find_time_conflict(start_time)
        if conflict:
            original_time = start_time
            start_time = _suggest_alternative_time(start_time)
            logger.info(
                "schedule conflict resolved: title=%s, conflict_with=%s, original=%s, new=%s",
                title, conflict.get("id"), original_time, start_time,
            )

        priority = schedule_part.get("priority", plan_info.get("priority", "normal"))
        if priority not in ("low", "normal", "high"):
            priority = "normal"
        sid = sch_add({
            "title": title,
            "start_time": start_time,
            "description": schedule_part.get("description", text),
            "source": "smart_classify_distill",
            "status": "proposed",
            "priority": priority,
        })
        actions.append(f"日程提议 (ID:{sid})")
        created_ids["schedule_id"] = sid

        # 把日程 ID 关联回原 note
        distilled_into = []
        import json as _json
        try:
            distilled_into = _json.loads(note_get(note_id).get("distilled_into", "[]") or "[]")
        except Exception:
            distilled_into = []
        if isinstance(distilled_into, list):
            distilled_into.append({"type": "schedule", "id": sid})
        else:
            distilled_into = [{"type": "schedule", "id": sid}]
        note_update(note_id, {
            "stage": "distilled",
            "distilled_at": now_iso,
            "distilled_into": _json.dumps(distilled_into, ensure_ascii=False),
        })

    # 4. 创建记忆（带录入时去重检查 — 不删除已有）
    memory_part = decision.get("memory", {})
    if memory_part.get("should_create"):
        mem_type = memory_part.get("type", "fact")
        if mem_type not in ("personal_info", "preference", "event", "decision", "fact", "experience"):
            mem_type = "fact"
        mem_content = memory_part.get("content", text[:200])
        mem_keywords = memory_part.get("keywords", ",".join(keywords))
        mem_importance = memory_part.get("importance", 3)
        try:
            mem_importance = int(mem_importance)
        except (TypeError, ValueError):
            mem_importance = 3
        mem_importance = max(1, min(5, mem_importance))

        # 4a. 先看 LLM 是否标注了 duplicate_of（直接跳过创建）
        dup_of = memory_part.get("duplicate_of")
        dup_id: int | None = None
        if dup_of is not None:
            try:
                dup_id = int(dup_of)
            except (TypeError, ValueError):
                dup_id = None

        # 4b. LLM 没标或标错时，用 SequenceMatcher 兜底查重（相似度>0.7 跳过创建）
        if dup_id is None:
            dup_id = _find_duplicate_memory(mem_content, mem_keywords, mem_type)

        if dup_id is not None:
            actions.append(f"记忆已存在 (ID:{dup_id})，跳过创建")
            created_ids["memory_id"] = dup_id
            # 关联回原 note（即使没创建新记忆，也记录"应映射到这条已有记忆"）
            import json as _json
            try:
                distilled_into = _json.loads(note_get(note_id).get("distilled_into", "[]") or "[]")
            except Exception:
                distilled_into = []
            if isinstance(distilled_into, list):
                distilled_into.append({"type": "memory", "id": dup_id, "deduped": True})
            else:
                distilled_into = [{"type": "memory", "id": dup_id, "deduped": True}]
            note_update(note_id, {
                "stage": "distilled",
                "distilled_at": now_iso,
                "distilled_into": _json.dumps(distilled_into, ensure_ascii=False),
            })
        else:
            mem_id = mem_add(
                type_=mem_type,
                content=mem_content,
                importance=mem_importance,
                keywords=mem_keywords,
                source_conv_id=conv_id,
                recorded_at=recorded_at or now_iso,
                distilled_from=note_id,
            )
            actions.append(f"记忆 (ID:{mem_id})")
            created_ids["memory_id"] = mem_id

            # 把记忆 ID 关联回原 note
            import json as _json
            try:
                distilled_into = _json.loads(note_get(note_id).get("distilled_into", "[]") or "[]")
            except Exception:
                distilled_into = []
            if isinstance(distilled_into, list):
                distilled_into.append({"type": "memory", "id": mem_id})
            else:
                distilled_into = [{"type": "memory", "id": mem_id}]
            note_update(note_id, {
                "stage": "distilled",
                "distilled_at": now_iso,
                "distilled_into": _json.dumps(distilled_into, ensure_ascii=False),
            })

    return {"actions": actions, "created_ids": created_ids}


async def _handle_smart_classify(args: dict) -> dict:
    """智能分类：第一阶段，所有输入先作为 raw note 初次记录。
    同时给出分类意图，为后续蒸馏分流做准备。"""
    from .llm_client import call_llm
    from .timezone import now_tz

    text = args.get("text", "").strip()
    conv_id = args.get("conv_id", "")
    if not text:
        return {"success": False, "result": "text 不能为空"}

    # 注入当前时间到 prompt（用 str.replace 避免 JSON 花括号冲突）
    current_dt = now_tz().strftime("%Y-%m-%d %H:%M:%S %A")
    prompt = SMART_CLASSIFY_PROMPT.replace("{current_datetime}", current_dt)
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": text},
    ]

    try:
        response = await call_llm(
            messages=messages,
            temperature=0.1,
            max_tokens=1024,
            response_format={"type": "json_object"},
        )
        result_text = response.get("content", "") if isinstance(response, dict) else str(response)
        logger.debug("smart_classify raw response: %s", result_text[:500])
    except Exception as e:
        logger.error(f"smart_classify LLM call failed: {e}")
        return {"success": False, "result": f"分类请求失败: {e}"}

    try:
        parsed = _extract_json(result_text)
    except Exception as e:
        logger.error(f"smart_classify JSON parse failed: {e}, raw={result_text[:500]}")
        return {"success": False, "result": f"分类结果解析失败: {e}"}

    classification = parsed.get("classification", {})
    skill_card = parsed.get("skill_card")
    memory_info = parsed.get("memory_info")
    summary = parsed.get("summary", "")
    plan_info = parsed.get("plan", {}) or {}

    # 1. 所有输入先作为 raw note（便签）记录
    recorded_at = now_tz().isoformat()
    keywords = ",".join(classification.get("keywords", []))
    nid = note_add({
        "title": summary or text[:30],
        "content": text,
        "tags": keywords,
        "source": "smart_classify",
        "status": "proposed",
        "stage": "raw",
        "recorded_at": recorded_at,
    })

    actions = [f"便签记录 (ID:{nid})"]
    created_ids = {"note_id": nid}

    # 2. 如果是技能，直接创建技能卡片（技能需要立即被确认/使用）
    if classification.get("is_skill") and skill_card:
        sk_id = skill_add({
            "name": skill_card.get("name", summary),
            "trigger_scene": skill_card.get("trigger_scene", ""),
            "steps": skill_card.get("steps", []),
            "tags": skill_card.get("tags", []),
            "source_conv_id": conv_id,
        })
        actions.append(f"技能卡片 (ID:{sk_id})")
        created_ids["skill_id"] = sk_id

        # 同时生成一条经验记忆（来源于技能）
        mem_id = mem_add(
            type_="experience",
            content=f"技能: {skill_card.get('name', '')} — {skill_card.get('trigger_scene', '')}",
            importance=4,
            keywords=",".join(skill_card.get("tags", []) + classification.get("keywords", [])),
            source_conv_id=conv_id,
            recorded_at=recorded_at,
            distilled_from=nid,
        )
        actions.append(f"经验记忆 (ID:{mem_id})")
        created_ids["memory_id"] = mem_id

    # 2.5 is_memory 快路径：立即入记忆库（跳过 raw 蒸馏阶段）
    #     若 is_skill=true 且 memory_info.type=experience，跳过（避免和技能经验记忆重复）
    elif classification.get("is_memory") and memory_info:
        mem_type = memory_info.get("type", "fact")
        if mem_type not in ("personal_info", "preference", "event", "decision", "fact", "experience"):
            mem_type = "fact"
        mem_content = memory_info.get("content", text[:200])
        mem_keywords = memory_info.get("keywords", keywords)
        mem_importance = memory_info.get("importance", 3)
        try:
            mem_importance = int(mem_importance)
        except (TypeError, ValueError):
            mem_importance = 3
        mem_importance = max(1, min(5, mem_importance))

        # 录入时去重检查（不删除已有记忆）：相似度>0.7 跳过创建
        dup_id = _find_duplicate_memory(mem_content, mem_keywords, mem_type)
        if dup_id is not None:
            actions.append(f"记忆已存在 (ID:{dup_id})，跳过创建")
            created_ids["memory_id"] = dup_id
        else:
            mem_id = mem_add(
                type_=mem_type,
                content=mem_content,
                importance=mem_importance,
                keywords=mem_keywords,
                source_conv_id=conv_id,
                recorded_at=recorded_at,
                distilled_from=nid,
            )
            actions.append(f"记忆 (ID:{mem_id})")
            created_ids["memory_id"] = mem_id

        # 把记忆 ID 关联回原 note
        import json as _json
        try:
            distilled_into = _json.loads(note_get(nid).get("distilled_into", "[]") or "[]")
        except Exception:
            distilled_into = []
        mid_for_link = created_ids.get("memory_id")
        if isinstance(distilled_into, list):
            distilled_into.append({"type": "memory", "id": mid_for_link})
        else:
            distilled_into = [{"type": "memory", "id": mid_for_link}]
        note_update(nid, {
            "stage": "distilled",
            "distilled_at": recorded_at,
            "distilled_into": _json.dumps(distilled_into, ensure_ascii=False),
        })

    # 3. 对 plan 类型立即进行蒸馏分流（时间敏感）
    if classification.get("is_plan"):
        distill_result = await _distill_raw_note(
            nid,
            text,
            classification,
            plan_info,
            conv_id=conv_id,
            recorded_at=recorded_at,
        )
        created_ids.update(distill_result.get("created_ids", {}))
        actions.extend(distill_result.get("actions", []))

    # 4. is_thought 自动蒸馏（不再停留 raw 阶段等待手动）
    #    若同时 is_plan=true，plan 蒸馏已经覆盖了内容，跳过避免重复
    if classification.get("is_thought") and not classification.get("is_plan"):
        distill_result = await _distill_raw_note(
            nid,
            text,
            classification,
            plan_info,
            conv_id=conv_id,
            recorded_at=recorded_at,
        )
        created_ids.update(distill_result.get("created_ids", {}))
        actions.extend(distill_result.get("actions", []))

    # 构建返回结果
    result_lines = [
        "🧠 智能分类结果:",
        f"  分类: plan={classification.get('is_plan')} | thought={classification.get('is_thought')} | skill={classification.get('is_skill')} | memory={classification.get('is_memory')}",
        f"  关键词: {keywords}",
        f"  摘要: {summary}",
    ]
    if skill_card:
        result_lines.extend([
            f"  技能卡片: {skill_card.get('name', '')}",
            f"  触发场景: {skill_card.get('trigger_scene', '')}",
            f"  步骤数: {len(skill_card.get('steps', []))}",
        ])
    if memory_info:
        result_lines.append(f"  记忆类型: {memory_info.get('type', 'fact')}")
    if actions:
        result_lines.append(f"\n  已自动创建: {' | '.join(actions)}")

    return {
        "success": True,
        "result": "\n".join(result_lines),
        "classification": classification,
        "skill_card": skill_card,
        "memory_info": memory_info,
        "summary": summary,
        "actions": actions,
        "created_ids": created_ids,
    }


async def _handle_distill_note(args: dict) -> dict:
    """工具入口：手动蒸馏一条 raw note。"""
    note_id = args.get("note_id")
    if not note_id:
        return {"success": False, "result": "note_id 不能为空"}

    from .database import note_get
    note = note_get(note_id)
    if not note:
        return {"success": False, "result": f"笔记 ID:{note_id} 不存在"}

    text = note.get("content", "")
    if not text:
        return {"success": False, "result": "笔记内容为空，无法蒸馏"}

    # 构造一个基础分类用于蒸馏
    classification = {"keywords": note.get("tags", "").split(",") if note.get("tags") else []}
    plan_info = {}

    result = await _distill_raw_note(
        note_id=note_id,
        text=text,
        classification=classification,
        plan_info=plan_info,
        recorded_at=note.get("recorded_at", ""),
    )

    actions = result.get("actions", [])
    if not actions:
        return {"success": True, "result": "未产生新的整理/日程/记忆。", "created_ids": result.get("created_ids", {})}

    return {
        "success": True,
        "result": f"蒸馏完成：{' | '.join(actions)}",
        "created_ids": result.get("created_ids", {}),
    }


async def _handle_distill_weekly(args: dict) -> dict:
    """处理每周蒸馏工具调用"""
    from .unified_distill import distill_weekly

    week_start = args.get("week_start", "").strip()
    if not week_start:
        from datetime import datetime, timedelta
        today = datetime.now()
        monday = today - timedelta(days=today.weekday())
        week_start = monday.strftime("%Y-%m-%d")

    save_txt = args.get("save_txt", True)
    result = await distill_weekly(week_start=week_start, save_txt=save_txt)

    if not result.get("success", True) and "error" in result:
        return {"success": False, "result": result["error"]}

    txt = result.get("txt_content", "")
    txt_path = result.get("txt_path", "")
    lines = [
        f"每周蒸馏完成（{week_start} 开始）！",
        f"  对话数: {result.get('conv_count', 0)}",
        f"  日程数: {result.get('schedule_count', 0)}",
        f"  笔记数: {result.get('note_count', 0)}",
        f"  记忆数: {result.get('memory_count', 0)}",
        f"  标题: {result.get('headline', '')}",
    ]
    if txt_path:
        lines.append(f"  txt文件: {txt_path}")

    return {"success": True, "result": "\n".join(lines), "txt_content": txt, "txt_path": txt_path, **result}


# ═══════════════════════════════════════════════════════
# 记忆整理 / 合并 / 去重（用户确认后执行）
# ═══════════════════════════════════════════════════════

_CONSOLIDATE_INTENT_PROMPT = """\
判断以下用户消息是否想要"整理/合并/去重记忆库"。只输出 JSON，不要解释。

{
  "trigger": false,
  "type": "none",
  "scope": "all",
  "reason": "一句话判断理由"
}

字段说明：
- trigger: true 仅当用户明确想整理/合并/去重记忆库（如：合并记忆、去重记忆、整理记忆库、清理重复记忆）
- trigger: false 当"合并"指其他事物（合并文件/表格/单元格/项目计划等）
- type: "merge" | "dedup" | "organize" | "none" 之一
- scope: "all" | 记忆类型（personal_info/preference/event/decision/fact/experience）| 关键词

只输出 JSON。"""


_CONSOLIDATE_PLAN_PROMPT = """\
请对以下记忆库数据进行分析，找出明显的重复、相似和可能过时的条目，输出待执行的整理计划 JSON。

{
  "total": 253,
  "merge_groups": [
    {"keep_id": 12, "delete_ids": [34, 56], "reason": "内容高度相似", "merged_content": "合并后的内容"}
  ],
  "outdated": [
    {"id": 78, "reason": "信息已过时"}
  ],
  "notes": "补充说明"
}

merge_groups 规则：
- keep_id: 保留的记忆 ID（重要度更高或创建时间更早的）
- delete_ids: 需要删除的重复记忆 ID 列表
- 只列出相似度 >= 0.85 的强重复组
- 不要猜测不存在的 ID；如果无法确定，宁可不列

outdated 规则：
- 只列出明显过时的客观信息（如旧价格、已取消的计划、临时信息）
- 必须带真实记忆 ID，没有 ID 的不要列

记忆数据格式：
[ID:1][type] 内容摘要
[ID:2][type] 内容摘要

{memory_text}

只输出 JSON，不要其他内容。"""


async def detect_consolidate_intent(user_message: str) -> dict:
    """判断用户是否想整理记忆库。"""
    from .llm_client import call_llm
    try:
        response = await call_llm(
            messages=[
                {"role": "system", "content": _CONSOLIDATE_INTENT_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.1,
            max_tokens=256,
            response_format={"type": "json_object"},
        )
        text = response.get("content", "") if isinstance(response, dict) else str(response)
        return _extract_json(text)
    except Exception as e:
        logger.warning("consolidate intent detection failed: %s", e)
        return {"trigger": False, "type": "none", "scope": "all", "reason": str(e)}


async def generate_consolidate_plan(type_: str = "", search: str = "") -> dict:
    """生成记忆整理计划（真实 ID）。结合自动相似度 + LLM 建议。"""
    from .memory_engine import _similarity
    from datetime import datetime, timedelta
    from .llm_client import call_llm

    all_mems = mem_list()
    if type_:
        all_mems = [m for m in all_mems if m.get("type") == type_]
    if search:
        all_mems = [m for m in all_mems if search in (m.get("content", "") + m.get("keywords", ""))]

    # 1. 自动相似度合并候选（>= 0.85 强相似）
    merge_groups = []
    seen_ids = set()
    for i, m in enumerate(all_mems):
        if m["id"] in seen_ids:
            continue
        group_delete = []
        for j in range(i + 1, len(all_mems)):
            other = all_mems[j]
            if other["id"] in seen_ids:
                continue
            if other.get("type") != m.get("type"):
                continue
            sim = _similarity(m.get("content", ""), other.get("content", ""))
            if sim >= 0.85:
                group_delete.append(other)
                seen_ids.add(other["id"])
        if group_delete:
            seen_ids.add(m["id"])
            keeper = m
            for o in group_delete:
                if o.get("importance", 3) > keeper.get("importance", 3):
                    keeper = o
            merge_groups.append({
                "keep_id": keeper["id"],
                "delete_ids": [o["id"] for o in group_delete if o["id"] != keeper["id"]],
                "reason": "强相似（内容相似度>=0.85），保留重要度更高/更早的",
                "merged_content": keeper.get("content", ""),
            })

    # 2. 30天前创建且重要度=1 的过时候选
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    outdated_candidates = [
        {"id": m["id"], "reason": "长期未引用且重要度最低，建议清理"}
        for m in all_mems
        if m.get("importance", 3) == 1 and m.get("created_at", "")[:10] < cutoff and m["id"] not in seen_ids
    ]

    # 3. LLM 辅助建议（补充可能漏掉的相似项）
    memory_text = "\n".join([
        f"[ID:{m['id']}][{m.get('type','')}] {m.get('content','')[:120]}"
        for m in all_mems[:100]
    ])

    llm_merge = []
    llm_outdated = []
    if all_mems and len(all_mems) >= 5:
        try:
            prompt = _CONSOLIDATE_PLAN_PROMPT.replace("{memory_text}", memory_text)
            response = await call_llm(
                messages=[{"role": "system", "content": prompt}],
                temperature=0.1,
                max_tokens=1024,
                response_format={"type": "json_object"},
            )
            text = response.get("content", "") if isinstance(response, dict) else str(response)
            parsed = _extract_json(text)
            llm_merge = parsed.get("merge_groups", [])
            llm_outdated = parsed.get("outdated", [])
        except Exception as e:
            logger.warning("LLM consolidate plan failed: %s", e)

    # 4. 合并自动结果和 LLM 结果（去重，以真实存在的 ID 为准）
    existing_ids = {m["id"] for m in all_mems}
    merged_map = {}

    for g in merge_groups:
        if g["keep_id"] not in existing_ids:
            continue
        delete_ids = [d for d in g["delete_ids"] if d in existing_ids and d != g["keep_id"]]
        if delete_ids:
            merged_map[g["keep_id"]] = merged_map.get(g["keep_id"], {"keep_id": g["keep_id"], "delete_ids": []})
            merged_map[g["keep_id"]]["delete_ids"].extend(delete_ids)
            merged_map[g["keep_id"]]["delete_ids"] = list(set(merged_map[g["keep_id"]]["delete_ids"]))

    for g in llm_merge:
        keep_id = g.get("keep_id")
        if not keep_id or keep_id not in existing_ids:
            continue
        delete_ids = [d for d in g.get("delete_ids", []) if d in existing_ids and d != keep_id]
        if delete_ids:
            merged_map[keep_id] = merged_map.get(keep_id, {"keep_id": keep_id, "delete_ids": []})
            merged_map[keep_id]["delete_ids"].extend(delete_ids)
            merged_map[keep_id]["delete_ids"] = list(set(merged_map[keep_id]["delete_ids"]))

    merged_groups = []
    for g in merged_map.values():
        g["reason"] = "内容相似或重复，合并保留一条"
        g["merged_content"] = next((m.get("content", "") for m in all_mems if m["id"] == g["keep_id"]), "")
        merged_groups.append(g)

    outdated_ids = {o["id"] for o in outdated_candidates if o["id"] in existing_ids}
    for o in llm_outdated:
        if o.get("id") in existing_ids:
            outdated_ids.add(o["id"])

    final_outdated = [o for o in outdated_candidates if o["id"] in outdated_ids]
    for o in llm_outdated:
        if o.get("id") in outdated_ids and not any(x["id"] == o["id"] for x in final_outdated):
            final_outdated.append(o)

    total_after = len(all_mems) - sum(len(g["delete_ids"]) for g in merged_groups) - len(final_outdated)

    return {
        "total": len(all_mems),
        "total_after": total_after,
        "merge_groups": merged_groups,
        "outdated": final_outdated,
        "scope": type_ or search or "all",
    }


async def apply_consolidate_plan(plan: dict) -> dict:
    """执行整理计划。删除重复/过时记忆，保留 keeper。"""
    deleted_ids = []
    failed = []

    for g in plan.get("merge_groups", []):
        keep_id = g.get("keep_id")
        delete_ids = g.get("delete_ids", [])
        if not keep_id or not delete_ids:
            continue
        for did in delete_ids:
            try:
                mem_del(did)
                deleted_ids.append(did)
            except Exception as e:
                failed.append({"id": did, "reason": str(e)})

    for o in plan.get("outdated", []):
        oid = o.get("id")
        if not oid:
            continue
        try:
            mem_del(oid)
            deleted_ids.append(oid)
        except Exception as e:
            failed.append({"id": oid, "reason": str(e)})

    return {
        "success": True,
        "deleted_count": len(deleted_ids),
        "deleted_ids": deleted_ids,
        "failed": failed,
    }


def _format_consolidate_plan(plan: dict) -> str:
    """把计划格式化成用户可读的文本。"""
    lines = [
        "🧹 记忆整理计划",
        f"当前记忆数: {plan.get('total', 0)}",
        f"整理后预计: {plan.get('total_after', 0)}",
        "",
    ]
    merge_groups = plan.get("merge_groups", [])
    if merge_groups:
        lines.append(f"合并去重（{len(merge_groups)} 组）：")
        for i, g in enumerate(merge_groups, 1):
            lines.append(f"  {i}. 保留 ID:{g['keep_id']}，删除 ID:{', '.join(str(x) for x in g['delete_ids'])}")
            lines.append(f"     理由：{g.get('reason', '')}")
            lines.append(f"     内容：{g.get('merged_content', '')[:80]}")
    else:
        lines.append("未发现需要合并的重复记忆。")

    outdated = plan.get("outdated", [])
    if outdated:
        lines.append("")
        lines.append(f"过时清理（{len(outdated)} 条）：")
        for i, o in enumerate(outdated, 1):
            lines.append(f"  {i}. ID:{o['id']} — {o.get('reason', '')}")
    else:
        lines.append("")
        lines.append("未发现明显过时的记忆。")

    lines.extend([
        "",
        "回复 **确认执行** 开始整理，或回复 **跳过** 取消。",
    ])
    return "\n".join(lines)


async def _handle_sync_calendar(args: dict) -> dict:
    """工具入口：同步外部财经日历到本地缓存。"""
    days = args.get("days", 7)
    min_star = args.get("min_star", 2)
    try:
        from .calendar_sync import sync_calendar_events
        result = await sync_calendar_events(days=days, min_star=min_star)
        if result.get("errors"):
            return {
                "success": False,
                "result": f"财经日历同步失败: {result['errors'][0]}",
                "errors": result["errors"],
            }
        return {
            "success": True,
            "result": f"已同步 {result['synced']} 条外部财经事件（未来 {days} 天，星级≥{min_star}）。",
            "synced": result["synced"],
            "next_sync": result.get("next_sync", ""),
        }
    except Exception as e:
        logger.error("sync_calendar tool failed: %s", e)
        return {"success": False, "result": f"同步失败: {e}"}


async def _handle_consolidate_memories(args: dict) -> dict:
    """工具入口：生成或执行记忆整理计划。"""
    type_ = args.get("type_", "")
    search = args.get("search", "")
    auto_apply = args.get("auto_apply", False)

    if auto_apply:
        # 直接执行模式：基于默认策略生成计划并执行
        plan = await generate_consolidate_plan(type_=type_, search=search)
        result = await apply_consolidate_plan(plan)
        return {
            "success": True,
            "result": f"已执行记忆整理：删除 {result['deleted_count']} 条记忆。",
            "plan": plan,
            **result,
        }

    # 默认：只生成计划，等待用户确认
    plan = await generate_consolidate_plan(type_=type_, search=search)
    formatted = _format_consolidate_plan(plan)

    return {
        "success": True,
        "result": formatted,
        "confirm": True,
        "confirm_type": "consolidate_memories",
        "plan": plan,
    }


# ── 知识库工具处理函数 ──────────────────────────────
async def _handle_retrieve_docs(args: dict) -> dict:
    """RAG 检索：转发到 knowledge_service.search"""
    question = args.get("question", "").strip()
    if not question:
        return {"success": False, "result": "question 不能为空"}
    top_k = int(args.get("top_k", 5))
    try:
        r = await knowledge_service.search(question, top_k)
        if "error" in r:
            return {"success": False, "result": r["error"]}
        return {"success": True, "result": r.get("answer", "")}
    except Exception as e:
        return {"success": False, "result": f"知识库检索失败: {e}"}


async def _handle_query_wiki(args: dict) -> dict:
    """LLM Wiki 查询：转发到 knowledge_service.wiki_query"""
    question = args.get("question", "").strip()
    if not question:
        return {"success": False, "result": "question 不能为空"}
    try:
        r = await knowledge_service.wiki_query(question)
        if "error" in r:
            return {"success": False, "result": r["error"]}
        return {"success": True, "result": r.get("answer", "")}
    except Exception as e:
        return {"success": False, "result": f"Wiki 查询失败: {e}"}


async def _handle_kb_stats(args: dict) -> dict:
    """知识库统计：通过 /tasks 间接不可用，这里返回健康与提示"""
    try:
        h = await knowledge_service.health()
        return {"success": True, "result": f"知识库状态: {h.get('status', '未知')}。详细统计请运行 zotero_parse_rag_core.py --stats"}
    except Exception as e:
        return {"success": False, "result": f"知识库离线: {e}"}
