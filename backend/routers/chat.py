"""Chat API — SSE 流式对话 + 6 轮工具循环 + 执行追踪"""
import json
import asyncio
import time
import logging
from fastapi import APIRouter, Body, Request
from fastapi.responses import StreamingResponse, JSONResponse

from .. import database as db
from ..config import load_config
from ..tools import TOOLS_SCHEMA, execute_tool
from ..llm_client import chat_stream
from ..memory_engine import (
    maybe_extract_memories, build_memory_injection,
    extract_memories_from_text,
)
from ..context_compressor import maybe_compress
from ..schedule_reminder import check_reminders
from ..confirm_flow import get_pending_proposals
from ..validators.auditor_skill import AUDITOR_SKILL_PROMPT
from ..validators.output_validator import validate_output
from ..validators.input_validator import validate_input
from ..validators.execution_validator import validate_tool_result
from ..audit.audit_log import log_event

router = APIRouter(prefix="/api/chat", tags=["chat"])

# 活跃的 SSE 流任务，按对话 ID 管理
_active_streams: dict[str, asyncio.Task] = {}

# 苏格拉底追问法注入 — 在分析/决策/学习类问题中引导用户自己思考
SOCRATIC_INJECTION = """## 苏格拉底追问原则
在回答用户关于分析、决策、学习、理解类问题时，优先使用苏格拉底式追问：
- 不直接给答案，而是提出一个深刻的问题引导用户自己推理
- 在给出结论前先问："你为什么这么想？"或"你试过从 X 角度考虑吗？"
- 每次回答最多追问 1-2 个核心问题，避免问题轰炸
- 当用户明确要求直接答案、或问题属于事实性查询时，切换到直接回答模式

这些原则仅用于引导思维方式，不影响你调用工具和执行实际任务。"""


def _auto_title(text: str, max_len: int = 24) -> str:
    """从首条用户消息生成简洁对话标题（永久优化：替代 New Chat 占位）。

    - 去除 URL、空白、控制字符
    - 纯链接消息按域名归类（B站/YouTube/其他），避免退回「新对话」占位
    - 截取前 max_len 个字符
    - 空消息 → 兜底「新对话」
    """
    import re
    if not text or not text.strip():
        return "新对话"
    urls = re.findall(r"https?://([^/\s]+)", text)
    host = urls[0].lower() if urls else ""
    is_bili = "bilibili" in host or "b23" in host
    is_yt = "youtube" in host or "youtu.be" in host
    t = re.sub(r"https?://\S+", "", text)
    t = re.sub(r"\s+", "", t)
    t = re.sub(r"[。！？!?；;，,]+$", "", t)
    # 纯链接（无文字）→ 按域名归类
    if not t:
        if is_bili:
            return "B站视频提取与总结"
        if is_yt:
            return "YouTube视频提取与总结"
        if "zhihu" in host:
            return "知乎文章"
        return "链接内容提取"
    # 文字过短且带视频链接 → 补全域名语义（如「提取与总结」→「B站视频提取与总结」）
    if len(t) < 6:
        if is_bili:
            return f"B站视频{t}"
        if is_yt:
            return f"YouTube视频{t}"
    return t[:max_len]


def _maybe_auto_title(conv_id: str, user_message: str):
    """若对话标题仍是 New Chat（首条消息），用首条消息生成简洁标题。
    仅当消息数 == 1（刚刚写入第一条 user 消息）时触发，避免覆盖用户自定义标题。
    """
    try:
        conv = db.conv_get(conv_id)
        if not conv:
            return
        title = (conv.get("title") or "").strip()
        if title not in ("", "New Chat"):
            return  # 已有标题（含用户自定义），不覆盖
        title = _auto_title(user_message)
        if title != "新对话":
            db.conv_update_title(conv_id, title)
    except Exception as e:
        logging.getLogger("zenith.chat").warning("自动标题生成失败: %s", e)


def _build_skill_injection(current_query: str) -> str:
    """从记忆库检索 type='skill' 匹配当前查询，作为硬性指令注入 system prompt。

    A 级实现：命中后注入完整技能定义（不再截断 300 字），并明确指令模型
    "若用户请求适用该技能，必须严格按其步骤执行"。这保证技能在每次对话中
    被可靠遵循（非 LLM 自觉），为后续 B 级后端真实执行打基础。
    """
    if not current_query or len(current_query.strip()) < 2:
        return ""
    try:
        results = db.mem_search(current_query.strip()[:30], limit=5)
        skill_mems = [m for m in results if m.get("type") == "skill"]
        if not skill_mems:
            return ""
        parts = [
            "【已启用技能 · 必须遵循】",
            "以下技能与本次请求相关。若用户请求适用其中某个技能，你必须严格按其定义的「触发」与「步骤」执行，"
            "不要跳过步骤，也不要仅作为参考。技能内容：",
        ]
        for m in skill_mems[:3]:
            c = m.get("content", "").strip()
            if not c:
                continue
            name = c[3:].split("\n")[0].strip() if c.startswith("技能：") else "(未命名)"
            parts.append(f"\n### 技能：{name}\n{c}")
        return "\n".join(parts).strip()
    except Exception:
        return ""


async def _auto_extract_memory(conv_id: str):
    """后台自动提取对话记忆（共用 _do_extract 内核，与 periodic 同路径）"""
    logger = logging.getLogger("zenith.distill")
    try:
        msgs = db.msg_list(conv_id)
        text = "\n".join(m.get("content", "") for m in msgs if m.get("role") in ("user", "assistant"))
        if not text.strip():
            return
        result = await extract_memories_from_text(text, conv_id)
        new_count = result.get("new", 0)
        if new_count > 0:
            logger.info("对话结束记忆提取: conv=%s, 新增%d条", conv_id, new_count)
    except Exception as e:
        logging.getLogger("zenith.distill").warning("对话结束记忆提取失败: %s", e)


async def _process_conv(
    conv_id: str, user_message: str, messages: list, cfg: dict,
    event_queue: asyncio.Queue, provider_name: str = "", persona_name: str = "",
):
    """后台任务：LLM 调用 + 工具执行 + 消息保存，不受客户端断连影响"""
    logger = logging.getLogger("zenith.chat")
    try:
        reminder = check_reminders()
        if reminder:
            event_queue.put_nowait(json.dumps({'type': 'reminder', 'content': reminder}, ensure_ascii=False))

        assistant_text = ""
        assistant_thinking = ""  # 收集思考过程（与 WorkBuddy 对齐持久化）
        tool_results = []
        MAX_TOOL_ROUNDS = 6

        for round_num in range(MAX_TOOL_ROUNDS):
            round_text = ""
            round_tool_calls = []

            async for chunk in chat_stream(messages, tools=TOOLS_SCHEMA, provider_name=provider_name):
                if chunk["type"] == "text":
                    round_text += chunk["content"]
                    assistant_text += chunk["content"]
                    event_queue.put_nowait(json.dumps({'type': 'text', 'content': chunk["content"]}, ensure_ascii=False))
                elif chunk["type"] == "thinking":
                    # 思考过程 — DeepSeek reasoning_content / Anthropic thinking
                    assistant_thinking += chunk["content"]
                    event_queue.put_nowait(json.dumps({'type': 'thinking', 'content': chunk["content"]}, ensure_ascii=False))
                elif chunk["type"] == "tool_call":
                    round_tool_calls.append(chunk)

            if not round_tool_calls:
                break

            assistant_msg = {
                "role": "assistant",
                "content": round_text if round_text else None,
                "tool_calls": [
                    {
                        "id": tc.get("id") or f"call_{round_num}_{i}",
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["args"], ensure_ascii=False),
                        },
                    }
                    for i, tc in enumerate(round_tool_calls)
                ],
            }
            messages.append(assistant_msg)

            for i, tc in enumerate(round_tool_calls):
                tool_id = tc.get("id") or f"call_{round_num}_{i}"
                tool_name = tc["name"]
                tool_args = tc.get("args", {})

                # Phase 1: 工具调用开始事件
                trace_cfg = cfg.get("trace", {})
                trace_enabled = trace_cfg.get("enabled", True) if isinstance(trace_cfg, dict) else True
                show_bubbles = trace_cfg.get("show_tool_bubbles", True) if isinstance(trace_cfg, dict) else True

                if show_bubbles:
                    event_queue.put_nowait(json.dumps({
                        'type': 'tool_call_start',
                        'id': tool_id,
                        'name': tool_name,
                        'args': tool_args,
                        'round': round_num,
                    }, ensure_ascii=False))

                t_start = time.time()
                try:
                    result = await execute_tool(tool_name, tool_args, conv_id=conv_id)
                    success = not result.get("error")
                except Exception as exc:
                    result = {"error": str(exc), "result": f"工具执行异常: {exc}"}
                    success = False
                    log_event("tool_error", {
                        "tool": tool_name,
                        "args": tool_args,
                        "error": str(exc),
                    }, conv_id)

                duration_ms = int((time.time() - t_start) * 1000)
                tool_results.append(result)

                # Phase 1: 工具调用结束事件
                result_text = str(result.get("result", ""))
                if show_bubbles:
                    event_queue.put_nowait(json.dumps({
                        'type': 'tool_call_end',
                        'id': tool_id,
                        'name': tool_name,
                        'args': tool_args,
                        'result_summary': result_text[:500],
                        'round': round_num,
                        'duration_ms': duration_ms,
                        'success': success,
                        # 结构化代码痕迹（execute_code 专用）
                        'stdout': result.get("stdout"),
                        'stderr': result.get("stderr"),
                        'exit_code': result.get("exit_code"),
                        'lang': result.get("lang"),
                    }, ensure_ascii=False))

                # Phase 1: 写入执行追踪
                if trace_enabled:
                    try:
                        db.trace_add(
                            conv_id, "tool_call",
                            data={
                                "name": tool_name,
                                "args": tool_args,
                                "result_summary": result_text[:500],
                                "success": success,
                                "duration_ms": duration_ms,
                            },
                            round_num=round_num,
                        )
                    except Exception:
                        pass

                # Phase 2: 工具结果验证（移植自 WorkBuddy MCP 逻辑）
                if cfg.get("validators", {}).get("execution", {}).get("enabled", True):
                    try:
                        v_warnings = validate_tool_result(tool_name, tool_args, result_text)
                        for w in v_warnings:
                            event_queue.put_nowait(json.dumps({
                                'type': 'warning',
                                'level': w.get('level', 'warning'),
                                'content': w.get('message', ''),
                                'tool': tool_name,
                            }, ensure_ascii=False))
                            # 写入 traces
                            if trace_enabled:
                                try:
                                    db.trace_add(conv_id, "validation",
                                                 data={"type": w.get("type"), "message": w.get("message"),
                                                       "tool": tool_name}, round_num=round_num)
                                except Exception:
                                    pass
                    except Exception:
                        pass

                if result.get("confirm"):
                    proposal_data = dict(result)
                    if "confirm_type" in proposal_data and "type" not in proposal_data:
                        proposal_data["type"] = proposal_data["confirm_type"]
                    if "confirm_id" in proposal_data and "id" not in proposal_data:
                        proposal_data["id"] = proposal_data["confirm_id"]
                    event_queue.put_nowait(json.dumps({'type': 'proposal', 'data': proposal_data}, ensure_ascii=False))
                else:
                    # 兼容模式：保留文本拼接（若前端不支持 tool_call_start/end 气泡）
                    if not show_bubbles:
                        tool_info = f"\n\n[{tool_name}]: {result_text}"
                        assistant_text += tool_info
                        event_queue.put_nowait(json.dumps({'type': 'text', 'content': tool_info}, ensure_ascii=False))

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": str(result.get("result", "")),
                })

        if assistant_text:
            db.msg_add(conv_id, "assistant", assistant_text, thinking=assistant_thinking)
            audit_cfg = cfg.get("audit", {})
            if audit_cfg.get("enabled", True):
                log_event("conversation_complete", {
                    "conv_id": conv_id,
                    "message_len": len(assistant_text),
                    "tool_count": len(tool_results),
                    "rounds": round_num + 1,
                }, conv_id)

        event_queue.put_nowait(json.dumps({'type': 'full_text', 'content': assistant_text, 'conversation_id': conv_id}, ensure_ascii=False))

        if tool_results:
            event_queue.put_nowait(json.dumps({'type': 'tool_results', 'results': tool_results}, ensure_ascii=False))

        proposals = get_pending_proposals()
        if proposals:
            event_queue.put_nowait(json.dumps({'type': 'proposals', 'proposals': proposals}, ensure_ascii=False))

        combined = user_message + "\n" + assistant_text
        await maybe_extract_memories(combined, conv_id, interval=cfg.get("memory_extract_interval", 3))

        if cfg.get("auto_distill_enabled", True):
            asyncio.create_task(_auto_extract_memory(conv_id))

        # L3: 输出验证 — 绝对化表述/高风险领域/记忆矛盾检测
        if cfg.get("validators", {}).get("output", {}).get("enabled", True):
            warnings = validate_output(assistant_text, conv_id)
            for w in warnings:
                event_queue.put_nowait(json.dumps({'type': 'warning', **w}, ensure_ascii=False))

        event_queue.put_nowait(json.dumps({'type': 'done'}))

    except Exception as e:
        logger.error("后台对话处理异常: %s", e, exc_info=True)
        # Phase 1: 异常追踪
        try:
            db.trace_add(conv_id, "error", {
                "error": str(e),
                "stage": "process_conv",
            })
        except Exception:
            pass
        event_queue.put_nowait(json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False))
    finally:
        event_queue.put_nowait(None)
        _active_streams.pop(conv_id, None)


@router.post("")
async def chat(request: Request):
    """SSE 流式对话 — 后台任务处理，客户端断连不影响对话完成"""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "无效的 JSON 请求"}, status_code=400)

    user_message = data.get("message", "")
    conv_id = data.get("conversation_id", "")
    provider_name = data.get("provider_name", "")  # 多 Provider 支持
    persona_name = data.get("persona_name", "")    # Persona 支持

    if not conv_id:
        conv = db.conv_create()
        conv_id = conv["id"]
    else:
        # 兜底：conv_id 非空但对话不存在（如旧标签页/URL 残留）→ 自动创建新对话，
        # 避免 msg_add 触发 FOREIGN KEY 约束崩溃（500）
        try:
            if db.conv_get(conv_id) is None:
                conv = db.conv_create()
                conv_id = conv["id"]
        except Exception:
            conv = db.conv_create()
            conv_id = conv["id"]

    # 如果请求未指定 persona，从对话记录中读取
    if not persona_name and conv_id:
        try:
            conv_data = db.conv_get(conv_id)
            persona_name = (conv_data or {}).get("persona_name", "") or ""
        except Exception:
            pass

    if not user_message.strip():
        return JSONResponse({"error": "消息不能为空"}, status_code=400)

    # L1: 输入验证 — 高危指令拦截 / 模糊请求追问
    cfg = load_config()
    if cfg.get("validators", {}).get("input", {}).get("enabled", True):
        input_check = validate_input(user_message)
        if not input_check["passed"]:
            return JSONResponse(
                {"error": input_check["warning"],
                 "validation": {"blocked": True}},
                status_code=400,
            )
        if input_check["warning"] and not input_check["block"]:
            pass  # soft warning — 继续但前端可显示提示

    old_task = _active_streams.get(conv_id)
    if old_task and not old_task.done():
        old_task.cancel()
        _active_streams.pop(conv_id, None)

    db.msg_add(conv_id, "user", user_message)
    # 永久优化：首条消息后自动生成简洁标题（替代 New Chat 占位）
    _maybe_auto_title(conv_id, user_message)
    await maybe_compress(conv_id)

    return _start_sse(conv_id, user_message, cfg, provider_name, persona_name, persist_user=False)


def _build_chat_messages(conv_id: str, cfg: dict, persona_name: str, current_query: str = "") -> list:
    """构建 system + 历史消息（背景 / 学习进度 / Persona / 苏格拉底 / 审计 / 记忆 / 技能注入链）"""
    system_parts = [cfg["system_prompt"]]

    # 对话背景注入（world background）— 在 system_prompt 之后、Persona 之前
    background = None
    conv_data = None
    try:
        conv_data = db.conv_get(conv_id)
        background = (conv_data or {}).get("background", "") or ""
    except Exception:
        pass
    if background:
        # 时间/情景自动感知：注入当前日期与时段，让 AI 的回答贴合当下情境
        from datetime import datetime as _dt
        _now = _dt.now()
        _hour = _now.hour
        _period = "清晨" if _hour < 7 else ("上午" if _hour < 12 else ("下午" if _hour < 18 else ("晚上" if _hour < 23 else "深夜")))
        _weekday = "星期" + "一二三四五六日"[_now.weekday()]
        system_parts.append(
            f"## 对话背景\n{background.strip()}\n\n"
            f"【当前时间感知】今天是 {_now.strftime('%Y-%m-%d')} {_weekday}，现在{_period}"
            f"（{_now.strftime('%H:%M')}）。回答时自然贴合当前时段（如早晨问候、晚间收尾、深夜简短）"
        )

    # 学习进度注入（逐段学习模式续学）— 告知 AI 当前学到第几段
    if conv_data:
        lp = conv_data.get("learning_progress")
        if isinstance(lp, dict) and lp.get("doc_id"):
            system_parts.append(
                f"## 学习进度\n"
                f"当前学习: {lp.get('title', '')}（共 {lp.get('total_chunks', 0)} 段）\n"
                f"进度: 第 {lp.get('chunk_index', 0)}/{lp.get('total_chunks', 0)} 段（下一段是第 {lp.get('chunk_index', 0) + 1} 段）\n"
                f"规则: 用户要求继续学习时，调用 read_document_chunk(item_id={lp.get('doc_id')}, "
                f"chunk_index={lp.get('chunk_index', 0) + 1}) 读取下一段开始讲解。"
            )

    # Persona 注入 — 在 system_prompt 之后、审计之前（per-conversation 绑定）
    if persona_name:
        personas: list[dict] = cfg.get("personas", [])
        persona = next((p for p in personas if p.get("name") == persona_name), None)
        if persona:
            system_parts.append(
                f"## 当前工作模式: {persona['name']}\n"
                f"{persona.get('system_prompt', '')}"
            )
        else:
            logging.getLogger("zenith.chat").warning(
                "Persona '%s' 不存在，回退默认模式", persona_name
            )

    # 苏格拉底追问模式（默认启用，可在 config 关闭）
    if cfg.get("socratic_mode", True):
        system_parts.append(SOCRATIC_INJECTION)

    # 审计员 Skill — 从源头减少幻觉（可配置关闭）
    if cfg.get("auditor_skill", {}).get("enabled", True):
        system_parts.append(AUDITOR_SKILL_PROMPT)

    # 编辑能力声明 — 告知 AI 具备删除/修改笔记、编辑文件的能力（需用户确认后执行）
    system_parts.append(
        "## 你的编辑能力\n"
        "你具备修改 Zenith 内容的工具，可以执行以下操作（全部需要用户确认后才会真正执行）：\n"
        "- delete_note(note_id): 删除一条笔记\n"
        "- edit_note(note_id, title/content/tags): 修改一条笔记的内容/标题/标签\n"
        "- edit_file(path, content): 编辑项目内的代码/配置文件（限项目目录，自动备份）\n"
        "- update_background(new_background): 更新当前对话的背景设定（世界观/情境）\n"
        "- delete_message(content_fragment): 删除当前对话历史中的某条消息（清理隐私明文，如密钥/密码/助记词）\n"
        "- read_document_chunk(item_id, chunk_index): 逐段学习——读取已入库文档第 N 段文本（自动更新学习进度）\n"
        "当用户要求删除/修改笔记、编辑代码、修改对话背景设定、清理对话中的隐私消息、或逐段学习文档时，调用对应工具，"
        "不要声称你没有这些能力。修改类工具调用后前端会弹出确认卡片，用户点「执行」才会生效；read_document_chunk 是只读工具，直接返回段落内容。"
    )

    memory_injection = build_memory_injection(current_query=current_query)
    if memory_injection:
        system_parts.append(memory_injection)
    skill_injection = _build_skill_injection(current_query=current_query)
    if skill_injection:
        system_parts.append(skill_injection)

    messages = [{"role": "system", "content": "\n\n".join(system_parts)}]
    for m in db.msg_list(conv_id):
        if m["role"] != "system":
            messages.append({"role": m["role"], "content": m["content"]})
    return messages


def _start_sse(conv_id: str, user_message: str, cfg: dict,
               provider_name: str = "", persona_name: str = "",
               persist_user: bool = True) -> StreamingResponse:
    """启动 SSE 后台对话任务。

    persist_user=True：先插入用户消息再启动（chat 主路径）；
    persist_user=False：用户消息已存在（regenerate/edit 复用）。
    """
    old_task = _active_streams.get(conv_id)
    if old_task and not old_task.done():
        old_task.cancel()
        _active_streams.pop(conv_id, None)

    if persist_user:
        db.msg_add(conv_id, "user", user_message)

    messages = _build_chat_messages(conv_id, cfg, persona_name, user_message)

    event_queue = asyncio.Queue()
    process_task = asyncio.create_task(
        _process_conv(conv_id, user_message, messages, cfg, event_queue, provider_name, persona_name)
    )
    _active_streams[conv_id] = process_task

    async def generate():
        try:
            while True:
                event = await event_queue.get()
                if event is None:
                    break
                yield f"data: {event}\n\n"
        finally:
            logging.getLogger("zenith.chat").info("SSE 流结束 (对话%s)", conv_id)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _conv_persona(conv_id: str) -> str:
    """读取会话绑定的 Persona 名称"""
    try:
        conv_data = db.conv_get(conv_id)
        return (conv_data or {}).get("persona_name", "") or ""
    except Exception:
        return ""


@router.post("/regenerate")
async def regenerate(request: Request):
    """重新生成最后一条 AI 回复 — 删除最后 assistant 消息后以最后用户消息重跑（SSE）"""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "无效的 JSON 请求"}, status_code=400)

    conv_id = data.get("conversation_id", "")
    provider_name = data.get("provider_name", "")
    if not conv_id:
        return JSONResponse({"error": "缺少 conversation_id"}, status_code=400)

    msgs = db.msg_list(conv_id)
    if not msgs:
        return JSONResponse({"error": "对话为空，无法重新生成"}, status_code=400)
    if msgs[-1]["role"] != "assistant":
        return JSONResponse({"error": "没有可重新生成的上一条 AI 回复"}, status_code=400)

    # 删除最后一条 assistant 消息
    db.msg_del_from(msgs[-1]["id"])

    # 找最后一条用户消息作为重跑输入
    user_message = ""
    for m in reversed(msgs[:-1]):
        if m["role"] == "user":
            user_message = m["content"]
            break
    if not user_message:
        return JSONResponse({"error": "没有可用的用户消息"}, status_code=400)

    cfg = load_config()
    return _start_sse(conv_id, user_message, cfg, provider_name, _conv_persona(conv_id), persist_user=False)


@router.post("/edit")
async def edit_message(request: Request):
    """编辑消息并重新生成：更新消息内容，删除其后所有消息。

    user 消息 → 返回 SSE 流重新生成；assistant 消息 → 仅保存修改（JSON）。
    """
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "无效的 JSON 请求"}, status_code=400)

    conv_id = data.get("conversation_id", "")
    msg_id = data.get("msg_id")
    content = data.get("content", "")
    provider_name = data.get("provider_name", "")
    if not conv_id or not msg_id or not content.strip():
        return JSONResponse({"error": "缺少 conversation_id / msg_id / content"}, status_code=400)

    msg = db.msg_get(msg_id)
    if not msg or msg["conversation_id"] != conv_id:
        return JSONResponse({"error": "消息不存在"}, status_code=404)
    if not db.msg_update(msg_id, content):
        return JSONResponse({"error": "更新失败"}, status_code=500)
    db.msg_del_from(msg_id + 1)

    cfg = load_config()
    if msg["role"] != "user":
        # assistant 消息编辑 — 仅保存修改（前端可再触发 regenerate）
        return {"success": True, "regenerate": False}

    return _start_sse(conv_id, content, cfg, provider_name, _conv_persona(conv_id), persist_user=False)


@router.delete("/messages/{msg_id}")
async def delete_message(msg_id: int, mode: str = "tail"):
    """删除消息。mode=tail（默认）：删除该条及之后；mode=single：仅删除该条"""
    if mode == "single":
        deleted = db.msg_del_one(msg_id)
    else:
        deleted = db.msg_del_from(msg_id)
    if deleted == 0:
        return JSONResponse({"error": "消息不存在"}, status_code=404)
    return {"success": True, "deleted": deleted}


@router.post("/stop")
async def stop_chat(request: Request):
    """停止当前对话的后台生成任务（SSE 流正常结束，不保存半成品）"""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "无效的 JSON 请求"}, status_code=400)
    conv_id = data.get("conversation_id", "")
    task = _active_streams.get(conv_id) if conv_id else None
    if task and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        _active_streams.pop(conv_id, None)
        return {"success": True, "stopped": True}
    return {"success": True, "stopped": False}


# 来自 app.py 的重定向端点
@router.post("/stream")
async def chat_stream_compat(request: Request):
    """兼容旧 /api/chat/stream 路径"""
    return await chat(request)
