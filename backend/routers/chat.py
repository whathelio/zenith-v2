"""Chat API — SSE 流式对话 + 6 轮工具循环"""
import json
import asyncio
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

router = APIRouter(prefix="/api/chat", tags=["chat"])

# 活跃的 SSE 流任务，按对话 ID 管理
_active_streams: dict[str, asyncio.Task] = {}


def _build_skill_injection(current_query: str) -> str:
    """从记忆库检索 type='skill' 匹配当前查询，注入 system prompt"""
    if not current_query or len(current_query.strip()) < 2:
        return ""
    try:
        results = db.mem_search(current_query.strip()[:30], limit=5)
        skill_mems = [m for m in results if m.get("type") == "skill"]
        if not skill_mems:
            return ""
        parts = ["【已记录技能参考】"]
        for m in skill_mems[:3]:
            c = m.get("content", "")
            parts.append(f"- {c[:300]}")
        return "\n".join(parts).strip()
    except Exception:
        return ""


async def _auto_distill_conv(conv_id: str):
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
    event_queue: asyncio.Queue,
):
    """后台任务：LLM 调用 + 工具执行 + 消息保存，不受客户端断连影响"""
    logger = logging.getLogger("zenith.chat")
    try:
        reminder = check_reminders()
        if reminder:
            event_queue.put_nowait(json.dumps({'type': 'reminder', 'content': reminder}, ensure_ascii=False))

        assistant_text = ""
        tool_results = []
        MAX_TOOL_ROUNDS = 6

        for round_num in range(MAX_TOOL_ROUNDS):
            round_text = ""
            round_tool_calls = []

            async for chunk in chat_stream(messages, tools=TOOLS_SCHEMA):
                if chunk["type"] == "text":
                    round_text += chunk["content"]
                    assistant_text += chunk["content"]
                    event_queue.put_nowait(json.dumps({'type': 'text', 'content': chunk["content"]}, ensure_ascii=False))
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
                result = await execute_tool(tc["name"], tc["args"])
                tool_results.append(result)
                tool_id = tc.get("id") or f"call_{round_num}_{i}"

                if result.get("confirm"):
                    proposal_data = dict(result)
                    if "confirm_type" in proposal_data and "type" not in proposal_data:
                        proposal_data["type"] = proposal_data["confirm_type"]
                    if "confirm_id" in proposal_data and "id" not in proposal_data:
                        proposal_data["id"] = proposal_data["confirm_id"]
                    event_queue.put_nowait(json.dumps({'type': 'proposal', 'data': proposal_data}, ensure_ascii=False))
                else:
                    tool_info = f"\n\n[{tc['name']}]: {result.get('result', '')}"
                    assistant_text += tool_info
                    event_queue.put_nowait(json.dumps({'type': 'text', 'content': tool_info}, ensure_ascii=False))

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": str(result.get("result", "")),
                })

        if assistant_text:
            db.msg_add(conv_id, "assistant", assistant_text)

        event_queue.put_nowait(json.dumps({'type': 'full_text', 'content': assistant_text, 'conversation_id': conv_id}, ensure_ascii=False))

        if tool_results:
            event_queue.put_nowait(json.dumps({'type': 'tool_results', 'results': tool_results}, ensure_ascii=False))

        proposals = get_pending_proposals()
        if proposals:
            event_queue.put_nowait(json.dumps({'type': 'proposals', 'proposals': proposals}, ensure_ascii=False))

        combined = user_message + "\n" + assistant_text
        await maybe_extract_memories(combined, conv_id, interval=cfg.get("memory_extract_interval", 3))

        if cfg.get("auto_distill_enabled", True):
            asyncio.create_task(_auto_distill_conv(conv_id))

        event_queue.put_nowait(json.dumps({'type': 'done'}))

    except Exception as e:
        logger.error("后台对话处理异常: %s", e, exc_info=True)
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

    if not conv_id:
        conv = db.conv_create()
        conv_id = conv["id"]

    if not user_message.strip():
        return JSONResponse({"error": "消息不能为空"}, status_code=400)

    old_task = _active_streams.get(conv_id)
    if old_task and not old_task.done():
        old_task.cancel()
        _active_streams.pop(conv_id, None)

    db.msg_add(conv_id, "user", user_message)
    await maybe_compress(conv_id)

    event_queue = asyncio.Queue()

    cfg = load_config()
    system_parts = [cfg["system_prompt"]]
    memory_injection = build_memory_injection(current_query=user_message)
    if memory_injection:
        system_parts.append(memory_injection)
    skill_injection = _build_skill_injection(current_query=user_message)
    if skill_injection:
        system_parts.append(skill_injection)

    messages = [{"role": "system", "content": "\n\n".join(system_parts)}]
    for m in db.msg_list(conv_id):
        if m["role"] != "system":
            messages.append({"role": m["role"], "content": m["content"]})

    process_task = asyncio.create_task(
        _process_conv(conv_id, user_message, messages, cfg, event_queue)
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


# 来自 app.py 的重定向端点
@router.post("/stream")
async def chat_stream_compat(request: Request):
    """兼容旧 /api/chat/stream 路径"""
    return await chat(request)
