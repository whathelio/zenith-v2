"""Zenith v2 工具注册表 — OpenAI Function Calling Schema + 执行器"""
from __future__ import annotations

import re
import json
import logging
from .database import sch_add, sch_list, sch_update, note_add, note_list, mem_search
from .database import prediction_list, prediction_get_hit_rate
from .database import skill_add, skill_get, skill_increment_usage

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
    # --- 市场分析工具 ---
    {
        "type": "function",
        "function": {
            "name": "query_market",
            "description": "查询当前现货黄金市场状态，包括价格、CFTC持仓、宏观指标、今日事件等。用户问黄金/市场/持仓/CFTC时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "focus": {"type": "string", "description": "关注方向: gold/cftc/macro/all", "enum": ["gold", "cftc", "macro", "all"]},
                    "detail": {"type": "string", "description": "详细程度: summary/detailed", "enum": ["summary", "detailed"]}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cftc_positioning",
            "description": "获取最新的CFTC黄金持仓分析，包括净持仓、z-score、flow state、拥挤度等。用户问持仓/仓位/资金流向时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "contract": {"type": "string", "description": "合约名，默认gold（可选：wti/铜/白银/标普500等）"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_gold",
            "description": "生成现货黄金综合分析报告，包含影响因素、事件预测、操作建议。用户要求做市场分析或要看报告时调用。",
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
            "name": "track_predictions",
            "description": "查看昨日市场预测的验证结果和命中率统计。用户问预测准不准/命中率/验证时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "查看哪天的预测，默认昨天，格式YYYY-MM-DD"}
                },
                "required": []
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
    # --- 统一蒸馏工具 ---
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
]


async def execute_tool(name: str, args: dict) -> dict:
    """
    执行工具并返回结果。
    confirm 字段指示是否需要用户确认。
    """
    try:
        if name == "add_schedule":
            return _handle_add_schedule(args)
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
        elif name == "query_market":
            return await _handle_query_market(args)
        elif name == "cftc_positioning":
            return await _handle_cftc_positioning(args)
        elif name == "analyze_gold":
            return await _handle_analyze_gold()
        elif name == "track_predictions":
            return await _handle_track_predictions(args)
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
        elif name == "smart_classify":
            return await _handle_smart_classify(args)
        else:
            return {"success": False, "result": f"未知工具: {name}"}
    except Exception as e:
        return {"success": False, "result": str(e)}


def _handle_add_schedule(args: dict) -> dict:
    title = args["title"]
    priority = "normal"

    m = re.match(r'^\[(高|中|低)\]\s*', title)
    if m:
        pmap = {"高": "high", "中": "normal", "低": "low"}
        priority = pmap[m.group(1)]
        title = title[m.end():]

    sid = sch_add({
        "title": title,
        "start_time": args.get("time", ""),
        "description": args.get("note", ""),
        "source": "ai_detect",
        "status": "proposed",
        "priority": priority,
    })
    return {
        "success": True,
        "result": f"日程提议已生成 (ID:{sid})：[{priority}] {title} @ {args.get('time','')}",
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
# Market Analysis Handlers
# ---------------------------------------------------------------------------

async def _handle_query_market(args: dict) -> dict:
    """查询市场状态"""
    from .macro_data import get_macro_service
    from .database import macro_indicator_list_latest, market_report_get_latest

    focus = args.get("focus", "all")
    detail = args.get("detail", "summary")

    result_parts = []

    # 宏观指标
    if focus in ("all", "macro", "gold"):
        indicators = macro_indicator_list_latest(limit=15)
        if indicators:
            result_parts.append("📊 当前市场指标：")
            for ind in indicators:
                arrow = "↑" if ind.get('change_pct') and float(ind.get('change_pct', 0)) > 0 else "↓"
                result_parts.append(f"  {ind['indicator']}: {ind['value']} {arrow}{ind.get('change_pct', '')}%")
        else:
            result_parts.append("（宏观数据暂未更新，请刷新数据）")

    # 最新分析报告
    if focus in ("all", "gold"):
        latest = market_report_get_latest()
        if latest:
            result_parts.append(f"\n📋 最新报告 ({latest['report_date']}):")
            result_parts.append(f"  黄金价格: {latest.get('gold_price', 'N/A')}")
            if detail == "detailed" and latest.get('analysis_text'):
                # 截取前500字
                text = latest['analysis_text'][:500]
                result_parts.append(f"  分析摘要: {text}...")
            else:
                if latest.get('daily_advice'):
                    result_parts.append(f"  日内建议: {latest['daily_advice'][:200]}")

    return {"success": True, "result": "\n".join(result_parts)}


async def _handle_cftc_positioning(args: dict) -> dict:
    """获取 CFTC 持仓分析"""
    from .cftc_service import get_cftc_service

    contract = args.get("contract", "gold")
    svc = get_cftc_service()

    try:
        await svc.fetch_incremental()
        all_data = await svc.get_positioning_json()
    except Exception as e:
        logger.error(f"CFTC fetch failed: {e}")
        return {"success": False, "result": f"CFTC数据获取失败: {e}"}

    # 按合约筛选
    if contract == "gold":
        filtered = [d for d in all_data if 'GOLD' in d.get('contract', '').upper() or d.get('contract') == '黄金']
    elif contract == "wti":
        filtered = [d for d in all_data if 'WTI' in d.get('contract', '').upper() or d.get('contract') == 'WTI原油']
    else:
        filtered = [d for d in all_data if contract.lower() in d.get('contract', '').lower()]

    if not filtered:
        filtered = all_data  # fallback: show all

    lines = ["📈 CFTC持仓分析："]
    lines.append(f"数据截止: {svc._report_date or 'N/A'} | 新鲜度: {svc.check_freshness()}")
    for d in filtered[:8]:
        direction_arrow = "↗" if d.get('net', 0) > 0 else "↘"
        lines.append(
            f"  [{d['category']}] {d['contract']}: "
            f"净{d.get('net', 'N/A')}{direction_arrow} | z={d.get('net_z', 'N/A')} | "
            f"flow={d.get('flow_state', '')} | 拥挤={d.get('crowding', '正常')} | "
            f"周变化={d.get('net_ww', 'N/A')} | 价格={d.get('price_chg', 'N/A')}%"
        )

    return {"success": True, "result": "\n".join(lines)}


async def _handle_analyze_gold() -> dict:
    """触发市场分析"""
    from .market_analyzer import get_market_analyzer

    analyzer = get_market_analyzer()
    try:
        result = await analyzer.run_daily_analysis()
        lines = [
            f"✅ 分析完成！报告ID: {result['report_id']}",
            f"报告日期: {result['report_date']}",
            f"黄金价格: {result.get('gold_price', 'N/A')}",
            f"预测条数: {result.get('predictions_count', 0)}",
        ]
        analysis = result.get('analysis', {})
        if analysis.get('daily_advice'):
            lines.append(f"日内建议: {analysis['daily_advice'][:200]}")
        if analysis.get('weekly_advice'):
            lines.append(f"周度建议: {analysis['weekly_advice'][:200]}")
        if analysis.get('summary'):
            lines.append(f"核心结论: {analysis['summary'][:200]}")
        return {"success": True, "result": "\n".join(lines)}
    except Exception as e:
        logger.error(f"Market analysis failed: {e}")
        return {"success": False, "result": f"分析失败: {e}"}


async def _handle_track_predictions(args: dict) -> dict:
    """查看预测追踪"""
    from .market_analyzer import get_market_analyzer
    from datetime import datetime, timedelta

    date = args.get("date", "")
    if not date:
        date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

    # 获取命中率统计
    hit_rate = prediction_get_hit_rate(days=30)

    # 获取指定日期的预测
    preds = prediction_list(date=date)

    lines = [f"🎯 预测追踪 ({date})："]
    lines.append(f"近30天命中率: {hit_rate['hit']}/{hit_rate['total']} = {hit_rate['hit_rate']}%")

    if preds:
        lines.append("\n预测详情：")
        for p in preds:
            status_icon = "✅" if p.get('verified') == 'verified' and p.get('predicted_direction') == p.get('actual_direction') else "❌" if p.get('verified') == 'verified' else "⏳"
            lines.append(
                f"  {status_icon} {p['event_name']}: "
                f"预测={p['predicted_direction']} 实际={p.get('actual_direction', '待验证')} "
                f"力度={p.get('predicted_strength', 0)}⭐"
            )
    else:
        lines.append("（该日无预测记录）")

    return {"success": True, "result": "\n".join(lines)}


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
你是一个智能分类器，请对用户输入进行一次分析，输出以下结构化JSON（不要加markdown代码块，直接输出JSON）：

{
  "classification": {
    "is_plan": false,
    "is_thought": false,
    "is_skill": false,
    "keywords": ["关键词1", "关键词2"]
  },
  "skill_card": null,
  "summary": "一句话摘要"
}

分类规则：
- is_plan: 用户在安排/确认一个日程、会议、提醒等时间相关事件 → true
- is_thought: 用户在表达想法、观点、偏好、感受 → true
- is_skill: 用户描述了一个可重复的操作流程、方法论、步骤化的技巧 → true
- keywords: 提取2-5个核心关键词

如果 is_skill=true，必须同时输出 skill_card：
{
  "classification": {..., "is_skill": true},
  "skill_card": {
    "name": "技能名称（简短）",
    "trigger_scene": "什么时候应该用这个技能",
    "steps": ["步骤1", "步骤2", ...],
    "tags": ["标签1", "标签2"]
  },
  "summary": "一句话摘要"
}

如果不是技能，skill_card 必须为 null。

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


async def _handle_smart_classify(args: dict) -> dict:
    """智能分类：一次 LLM 调用完成分类+技能提取+摘要，自动创建对应数据"""
    from .llm_client import call_llm

    text = args.get("text", "").strip()
    conv_id = args.get("conv_id", "")
    if not text:
        return {"success": False, "result": "text 不能为空"}

    messages = [
        {"role": "system", "content": SMART_CLASSIFY_PROMPT},
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
    summary = parsed.get("summary", "")

    # 根据分类结果自动创建数据
    actions = []
    created_ids = {}

    # is_plan → 创建日程
    if classification.get("is_plan"):
        sid = sch_add({
            "title": summary or text[:50],
            "start_time": "",
            "description": text,
            "source": "smart_classify",
            "status": "proposed",
        })
        actions.append(f"日程提议 (ID:{sid})")
        created_ids["schedule_id"] = sid

    # is_thought → 创建笔记
    if classification.get("is_thought"):
        nid = note_add({
            "title": summary or text[:30],
            "content": text,
            "tags": ",".join(classification.get("keywords", [])),
            "source": "smart_classify",
            "status": "proposed",
        })
        actions.append(f"笔记提议 (ID:{nid})")
        created_ids["note_id"] = nid

    # is_skill → 创建技能卡片 + 经验记忆
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

        # 同时存一条 experience 类型的记忆
        from .database import mem_add
        mem_id = mem_add(
            type_="experience",
            content=f"技能: {skill_card.get('name', '')} — {skill_card.get('trigger_scene', '')}",
            importance=4,
            keywords=",".join(skill_card.get("tags", []) + classification.get("keywords", [])),
            source_conv_id=conv_id,
        )
        actions.append(f"经验记忆 (ID:{mem_id})")

    # 如果没有任何分类命中，至少存一条记忆
    if not actions and summary:
        from .database import mem_add
        mem_id = mem_add(
            type_="fact",
            content=summary,
            importance=2,
            keywords=",".join(classification.get("keywords", [])),
            source_conv_id=conv_id,
        )
        actions.append(f"事实记忆 (ID:{mem_id})")

    # 构建返回结果
    result_lines = [
        "🧠 智能分类结果:",
        f"  分类: plan={classification.get('is_plan')} | thought={classification.get('is_thought')} | skill={classification.get('is_skill')}",
        f"  关键词: {', '.join(classification.get('keywords', []))}",
        f"  摘要: {summary}",
    ]
    if skill_card:
        result_lines.extend([
            f"  技能卡片: {skill_card.get('name', '')}",
            f"  触发场景: {skill_card.get('trigger_scene', '')}",
            f"  步骤数: {len(skill_card.get('steps', []))}",
        ])
    if actions:
        result_lines.append(f"\n  已自动创建: {' | '.join(actions)}")

    return {
        "success": True,
        "result": "\n".join(result_lines),
        "classification": classification,
        "skill_card": skill_card,
        "summary": summary,
        "actions": actions,
        "created_ids": created_ids,
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
