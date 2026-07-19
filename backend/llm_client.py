"""Zenith v2 LLM 客户端 — 支持流式对话 + Function Calling"""
from __future__ import annotations

import json
import logging
import httpx
from typing import AsyncGenerator, Optional
from .config import load_config, get_api_base, get_api_key, get_model

logger = logging.getLogger("zenith.llm")


async def chat_stream(
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> AsyncGenerator[dict, None]:
    """
    SSE 流式对话。
    Yields: {"type":"text","content":"..."} | {"type":"tool_call","name":"...","args":{...}}
    """
    cfg = load_config()
    base_url = get_api_base()
    api_key = get_api_key()
    model = get_model()

    if not api_key:
        yield {"type": "text", "content": "\n\n> ⚠ 请先配置 API Key：设置 → 填入 API Key"}
        return

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature if temperature is not None else cfg.get("temperature", 0.7),
        "max_tokens": max_tokens if max_tokens is not None else cfg.get("max_tokens", 4096),
        "stream": True,
    }
    # GLM-5.2 默认开启 thinking 会消耗大量 token 导致 content 为空，显式禁用
    if "glm" in model.lower():
        payload["thinking"] = {"type": "disabled"}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST", f"{base_url}/chat/completions",
                headers=headers, json=payload
            ) as resp:
                resp.raise_for_status()
                accumulated_tool = {}

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        delta = data.get("choices", [{}])[0].get("delta", {})

                        content = delta.get("content", "")
                        if content:
                            yield {"type": "text", "content": content}

                        tc = delta.get("tool_calls")
                        if tc:
                            for t in tc:
                                idx = t.get("index", 0)
                                if idx not in accumulated_tool:
                                    accumulated_tool[idx] = {
                                        "id": t.get("id", ""),
                                        "name": "",
                                        "args": ""
                                    }
                                fn = t.get("function", {})
                                if fn.get("name"):
                                    accumulated_tool[idx]["name"] = fn["name"]
                                if fn.get("arguments"):
                                    accumulated_tool[idx]["args"] += fn["arguments"]
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

                for tc in accumulated_tool.values():
                    try:
                        args = json.loads(tc["args"])
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    yield {
                        "type": "tool_call",
                        "name": tc["name"],
                        "args": args,
                        "id": tc["id"]
                    }

    except httpx.ConnectError:
        yield {"type": "text", "content": f"\n\n> ❌ 无法连接到 {base_url}"}
    except httpx.HTTPStatusError as e:
        yield {"type": "text", "content": f"\n\n> ❌ API 错误 ({e.response.status_code})"}
    except Exception as e:
        yield {"type": "text", "content": f"\n\n> ❌ 连接错误: {e}"}


async def call_llm(
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    temperature: float = 0.7,
    max_tokens: int = 2000,
    response_format: Optional[dict] = None,
) -> dict:
    """非流式 LLM 调用，用于记忆提取、日程分析等后台任务"""
    cfg = load_config()
    base_url = get_api_base()
    api_key = get_api_key()
    model = get_model()

    if not api_key:
        return {"role": "assistant", "content": "API Key 未配置"}

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    # GLM-5.2 禁用 thinking 避免 token 被 reasoning 耗尽
    if "glm" in model.lower():
        payload["thinking"] = {"type": "disabled"}
    if tools:
        payload["tools"] = tools
    if response_format:
        payload["response_format"] = response_format

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{base_url}/chat/completions",
                headers=headers, json=payload
            )
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]
    except Exception as e:
        return {"role": "assistant", "content": f"Error: {e}"}


# ---------------------------------------------------------------------------
# Functional Helpers
# ---------------------------------------------------------------------------

async def extract_memories(conversation_text: str) -> list[dict]:
    """从对话文本中提取结构化记忆"""
    prompt = f"""从以下对话中提取值得记住的信息。返回 JSON 数组，每条格式：
{{"type":"personal_info|preference|event|decision|fact|experience","content":"记忆内容","importance":1-5,"keywords":"逗号分隔关键词"}}

类型说明：
- personal_info: 用户的个人信息（姓名、年龄、职业、所在地等）
- preference: 用户的偏好习惯（喜欢什么、讨厌什么、工作方式等）
- event: 发生过的事件（计划了什么、完成了什么等）
- decision: 做过的决定（选了什么方案、定了什么方向等）
- fact: 值得记住的事实（知识点、数据、背景信息等）
- experience: 可复用的经验技巧（工作方法、踩坑教训、最佳实践等）

对话内容：
{conversation_text}

只返回 JSON 数组，不要其他内容。没有可提取的记忆则返回 []。"""

    msg = await call_llm(
        [{"role": "user", "content": prompt}],
        temperature=0.1, max_tokens=2000
    )
    content = msg.get("content", "[]")
    if content.startswith("Error:"):
        logger.warning("记忆提取 LLM 调用失败: %s", content[:200])
        return []
    result = _parse_json_response(content)
    logger.info("记忆提取 LLM 返回 %d 条 (raw_len=%d)", len(result), len(content))
    return result


async def detect_schedule(user_text: str) -> list[dict]:
    """从用户输入中检测日程意图"""
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d %A")

    prompt = f"""从用户输入中检测日程安排。今天是 {today}。
返回 JSON 数组格式：
[{{"title":"事项","start_time":"YYYY-MM-DD HH:MM","end_time":"YYYY-MM-DD HH:MM","location":"地点","description":"描述","priority":"low|normal|high"}}]

没有日程则返回 []。只返回 JSON。"""

    msg = await call_llm(
        [{"role": "user", "content": f"用户说：{user_text}\n{prompt}"}],
        temperature=0.1, max_tokens=1000
    )
    content = msg.get("content", "[]")
    return _parse_json_response(content)


async def extract_datetime(user_text: str, reference_iso: Optional[str] = None) -> str:
    """从用户输入中抽取日程时间，返回 ISO 格式字符串（失败返回空字符串）"""
    from datetime import datetime
    from .timezone import now_tz, parse_time_to_iso, DEFAULT_TIMEZONE

    # 先尝试规则解析（快速、省钱）
    parsed = parse_time_to_iso(user_text, reference=now_tz())
    if parsed:
        return parsed

    # 规则解析失败时，再用 LLM 兜底
    today = now_tz().strftime("%Y-%m-%d %A %H:%M")
    prompt = f"""从用户输入中抽取日程时间。今天是 {today}，默认时区 CST (+08:00)。
如果输入中包含时间，只返回一个 ISO 格式时间字符串（如 2026-07-18T15:00:00+08:00）。
如果没有时间、只有日期，返回日期零时（如 2026-07-18T00:00:00+08:00）。
如果完全无法判断，只返回空字符串 "" 。

只返回时间字符串，不要加任何解释。"""

    msg = await call_llm(
        [{"role": "user", "content": f"用户说：{user_text}\n{prompt}"}],
        temperature=0.1, max_tokens=200
    )
    content = msg.get("content", "").strip()
    if content.startswith("Error:"):
        return ""

    # 清理可能的代码块或多余字符
    content = content.strip('"').strip()
    if not content:
        return ""

    try:
        dt = datetime.fromisoformat(content)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=DEFAULT_TIMEZONE)
        return dt.isoformat()
    except ValueError:
        # 如果 LLM 返回的是格式时间，尝试用 parse_time_to_iso 再解析
        return parse_time_to_iso(content, reference=now_tz())

    """检测值得记录的想法/观点"""
    prompt = f"""判断用户输入中是否有值得记录的思考/想法/观点。
返回 JSON 数组：[{{"title":"标题","content":"内容","tags":"逗号分隔标签"}}]
只是闲聊没有想法则返回 []。只返回 JSON。"""

    msg = await call_llm(
        [{"role": "user", "content": f"用户说：{user_text}\n{prompt}"}],
        temperature=0.1, max_tokens=1000
    )
    content = msg.get("content", "[]")
    return _parse_json_response(content)


async def plan_time(schedules: list[dict]) -> str:
    """基于现有日程分析时间安排"""
    schedule_text = "\n".join(
        f"- [{s.get('start_time','?')}] {s['title']}"
        for s in schedules
    )
    prompt = f"分析以下日程安排，指出时间冲突或优化建议：\n{schedule_text}\n\n用简洁中文回复。"

    msg = await call_llm(
        [{"role": "user", "content": prompt}],
        temperature=0.7, max_tokens=800
    )
    return msg.get("content", "")


def _parse_json_response(content: str) -> list:
    """解析 LLM 返回的 JSON"""
    text = content.strip()
    if "```" in text:
        # 提取代码块内容
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            # 去除语言前缀（json, python, etc.）
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

    # 尝试从文本中提取 JSON 数组
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except (json.JSONDecodeError, ValueError):
            pass

    # 尝试从文本中提取 JSON 对象（蒸馏等场景返回 dict）
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            if isinstance(obj, dict):
                return [obj]
            return obj  # 可能是嵌套结构
        except (json.JSONDecodeError, ValueError):
            pass

    return []
