"""llm_client 的 thinking 禁用分支回归守护（红线测试）。

为什么需要本文件 —— 两条红线此前**零测试**（`grep thinking tests/` = 0 命中）：

1. **流式** `_chat_stream_openai`（`llm_client.py:115-116`）**只对 glm 禁用** thinking。
   若有人"顺手"把 deepseek 也加进去 → provider 不再返回 `reasoning_content`
   → `yield {"type":"thinking"}` 永不触发 → `messages.thinking` 落空
   → **前端失去思考过程展示**。源码 `:355-356` 已用中文注释明确警告过这一点。

2. **非流式** `call_llm`（`llm_client.py:357-358`）**对 glm + deepseek 都禁用**。
   实测 2026-09-15：`deepseek-flash` + `max_tokens=2000` 时 `reasoning_tokens=2000`、
   `content` 为空、`finish_reason=length`；显式关闭思考后正文恢复正常。
   → 若有人"统一"两条路径，这一侧会复发 content 空事故。

本文件用假 httpx client 捕获真实发出的请求 payload，把两条分支钉死。
改动 `llm_client.py` 的 thinking 逻辑会让本文件失败 —— 这正是它存在的意义。
"""
import json

import httpx
import pytest

from backend import llm_client

OPENAI_MSG = [{"role": "user", "content": "hi"}]
CFG = {"temperature": 0.7, "max_tokens": 4096}
PROVIDER_BASE = {
    "name": "test-provider",
    "api_base": "https://api.test/v1",
    "type": "openai",
}


# ────────────────────────── 假 httpx client ──────────────────────────

def _sse_lines(*chunks: dict) -> list[str]:
    """把若干 chunk 拼成 OpenAI 兼容 SSE 行，末尾补 [DONE]。"""
    lines = [f"data: {json.dumps(c, ensure_ascii=False)}" for c in chunks]
    lines.append("data: [DONE]")
    return lines


class _FakeStreamResp:
    def __init__(self, lines):
        self._lines = lines

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln


class _FakeStreamCtx:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *exc):
        return False


class _FakePostResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeClient:
    """记录每次请求的 payload，供断言检查。"""

    def __init__(self, stream_lines=None, post_payloads=None):
        self.stream_lines = stream_lines if stream_lines is not None else ["data: [DONE]"]
        self.post_payloads = list(post_payloads or [])
        self.captured_stream = None
        self.captured_posts = []

    def stream(self, method, url, headers=None, json=None, timeout=None):
        self.captured_stream = {
            "method": method, "url": url, "headers": headers, "json": json,
        }
        return _FakeStreamCtx(_FakeStreamResp(self.stream_lines))

    async def post(self, url, headers=None, json=None, timeout=None):
        self.captured_posts.append({"url": url, "headers": headers, "json": json})
        payload = self.post_payloads.pop(0) if self.post_payloads else {
            "choices": [{"message": {"content": "ok"}}], "usage": {},
        }
        return _FakePostResp(payload)


@pytest.fixture(autouse=True)
def _silence_cache_stat(monkeypatch):
    """屏蔽 llm_client 的缓存命中率埋点。

    埋点在函数内 `from .database import cache_stat_add`，失败虽被 `except: pass`
    吞掉，但会产生无谓的异常与库写入。这里统一置空，让测试只测 payload 逻辑。
    """
    monkeypatch.setattr("backend.database.cache_stat_add", lambda **kw: None, raising=False)


async def _drain_stream(fake: FakeClient, model: str, tools=None):
    """跑完一次流式调用，返回全部事件。"""
    events = []
    async for ev in llm_client._chat_stream_openai(
        "test-key", model, "https://api.test/v1",
        OPENAI_MSG, tools, None, None, CFG, "test-provider",
    ):
        events.append(ev)
    return events


def _patch_nonstream(monkeypatch, model: str, fake: FakeClient):
    monkeypatch.setattr(
        llm_client, "get_provider", lambda *a, **k: {**PROVIDER_BASE, "model": model}
    )
    monkeypatch.setattr(llm_client, "get_background_provider",
                        lambda *a, **k: {**PROVIDER_BASE, "model": model})
    monkeypatch.setattr(llm_client, "get_provider_api_key", lambda *a, **k: "test-key")
    monkeypatch.setattr(llm_client, "get_client", lambda: fake)


# ────────────────────────── 红线 1：流式 thinking ──────────────────────────

class TestStreamThinkingDisabled:
    """流式路径：**只有 glm** 允许被禁用 thinking。"""

    @pytest.mark.parametrize("model", ["glm-4", "glm-4.5", "GLM-4-Plus", "glm-4-flash"])
    async def test_glm_stream_disables_thinking(self, monkeypatch, model):
        fake = FakeClient()
        monkeypatch.setattr(llm_client, "get_client", lambda: fake)
        await _drain_stream(fake, model)
        assert fake.captured_stream["json"]["thinking"] == {"type": "disabled"}

    @pytest.mark.parametrize("model", ["deepseek-chat", "deepseek-flash", "deepseek-reasoner"])
    async def test_deepseek_stream_MUST_NOT_disable_thinking(self, monkeypatch, model):
        """🔴 红线：流式路径绝不能对 deepseek 禁用 thinking。

        一旦禁用，`delta.reasoning_content` 不再出现 → `yield {"type":"thinking"}`
        永不触发 → 前端失去思考展示。见 `llm_client.py:355-356` 的警告注释。
        """
        fake = FakeClient()
        monkeypatch.setattr(llm_client, "get_client", lambda: fake)
        await _drain_stream(fake, model)
        assert "thinking" not in fake.captured_stream["json"], (
            f"流式请求对 {model} 带了 thinking 禁用 —— 会让思考过程消失。"
            "见 llm_client.py:355-356 的警告。"
        )

    @pytest.mark.parametrize("model", ["gpt-4o", "qwen-max", "claude-3-5-sonnet"])
    async def test_other_models_stream_have_no_thinking_key(self, monkeypatch, model):
        fake = FakeClient()
        monkeypatch.setattr(llm_client, "get_client", lambda: fake)
        await _drain_stream(fake, model)
        assert "thinking" not in fake.captured_stream["json"]


# ────────────────────────── 红线 2：非流式 thinking ──────────────────────────

class TestNonStreamThinkingDisabled:
    """非流式路径：**glm + deepseek 都**禁用 thinking（防 reasoning 吃光 max_tokens）。"""

    @pytest.mark.parametrize("model", ["glm-4", "GLM-4-Plus", "deepseek-chat",
                                      "deepseek-flash", "deepseek-reasoner"])
    async def test_glm_and_deepseek_disable_thinking(self, monkeypatch, model):
        fake = FakeClient()
        _patch_nonstream(monkeypatch, model, fake)
        await llm_client.call_llm(OPENAI_MSG)
        assert fake.captured_posts[0]["json"]["thinking"] == {"type": "disabled"}

    @pytest.mark.parametrize("model", ["gpt-4o", "qwen-max", "claude-3-5-sonnet"])
    async def test_other_models_do_not_disable_thinking(self, monkeypatch, model):
        fake = FakeClient()
        _patch_nonstream(monkeypatch, model, fake)
        await llm_client.call_llm(OPENAI_MSG)
        assert "thinking" not in fake.captured_posts[0]["json"]

    async def test_nonstream_uses_background_provider_when_requested(self, monkeypatch):
        """use_background=True 走 background_provider（便宜模型）。"""
        fake = FakeClient()
        _patch_nonstream(monkeypatch, "deepseek-flash", fake)
        monkeypatch.setattr(
            llm_client, "get_background_provider",
            lambda *a, **k: {**PROVIDER_BASE, "model": "deepseek-flash-bg"},
        )
        await llm_client.call_llm(OPENAI_MSG, use_background=True)
        assert fake.captured_posts[0]["json"]["model"] == "deepseek-flash-bg"


# ────────────────────────── 空正文兜底（:379-398）──────────────────────────

class TestEmptyContentFallback:
    """空正文兜底：摘掉 thinking + max_tokens 翻倍重试一次。"""

    async def test_empty_content_retries_without_thinking_and_doubles_tokens(
        self, monkeypatch
    ):
        fake = FakeClient(post_payloads=[
            {"choices": [{"message": {"content": ""}}], "usage": {}},          # 首次空
            {"choices": [{"message": {"content": "正常正文"}}], "usage": {}},  # 重试成功
        ])
        _patch_nonstream(monkeypatch, "deepseek-flash", fake)
        msg = await llm_client.call_llm(OPENAI_MSG, max_tokens=2000)

        assert msg["content"] == "正常正文"
        assert len(fake.captured_posts) == 2, "空正文应触发一次重试"
        first, retry = fake.captured_posts
        assert first["json"]["thinking"] == {"type": "disabled"}
        assert "thinking" not in retry["json"], "重试必须摘掉 thinking"
        assert retry["json"]["max_tokens"] == 4096, "max(2000*2, 4096) == 4096"

    async def test_max_tokens_doubling_respects_floor(self, monkeypatch):
        """额度翻倍有 4096 下限 —— 传入较小的 max_tokens 时以 4096 为准。"""
        fake = FakeClient(post_payloads=[
            {"choices": [{"message": {"content": ""}}], "usage": {}},
            {"choices": [{"message": {"content": "x"}}], "usage": {}},
        ])
        _patch_nonstream(monkeypatch, "deepseek-flash", fake)
        await llm_client.call_llm(OPENAI_MSG, max_tokens=500)
        assert fake.captured_posts[1]["json"]["max_tokens"] == 4096

    async def test_retry_still_empty_returns_error_text(self, monkeypatch):
        """重试后仍空 → 不抛异常，返回 Error 文本（供上游判断）。"""
        fake = FakeClient(post_payloads=[
            {"choices": [{"message": {"content": ""}}], "usage": {}},
            {"choices": [{"message": {"content": ""}}], "usage": {}},
        ])
        _patch_nonstream(monkeypatch, "deepseek-flash", fake)
        msg = await llm_client.call_llm(OPENAI_MSG)
        assert msg["role"] == "assistant"
        assert "Error" in msg["content"]

    async def test_empty_choices_does_not_raise(self, monkeypatch):
        """choices 为空列表 → 走 ValueError → 被兜底为 Error 文本。"""
        fake = FakeClient(post_payloads=[{"choices": [], "usage": {}}])
        _patch_nonstream(monkeypatch, "gpt-4o", fake)
        msg = await llm_client.call_llm(OPENAI_MSG)
        assert "Error" in msg["content"]

    async def test_missing_api_key_short_circuits_without_request(self, monkeypatch):
        fake = FakeClient()
        monkeypatch.setattr(
            llm_client, "get_provider", lambda *a, **k: {**PROVIDER_BASE, "model": "x"}
        )
        monkeypatch.setattr(llm_client, "get_provider_api_key", lambda *a, **k: "")
        monkeypatch.setattr(llm_client, "get_client", lambda: fake)
        msg = await llm_client.call_llm(OPENAI_MSG)
        assert "未配置 API Key" in msg["content"]
        assert fake.captured_posts == [], "无 key 时不应发出请求"


# ────────────────────────── 流式事件产出 ──────────────────────────

class TestStreamEventProtocol:
    """流式事件协议：thinking / text / tool_call 的产出与累积。"""

    async def test_reasoning_content_and_reasoning_both_yield_thinking(
        self, monkeypatch
    ):
        """`reasoning_content` 与 `reasoning` 双字段兼容（:157-160）。"""
        fake = FakeClient(stream_lines=_sse_lines(
            {"choices": [{"delta": {"reasoning_content": "思考甲"}}]},
            {"choices": [{"delta": {"reasoning": "思考乙"}}]},
            {"choices": [{"delta": {"content": "回答"}}]},
        ))
        monkeypatch.setattr(llm_client, "get_client", lambda: fake)
        events = await _drain_stream(fake, "deepseek-reasoner")

        pairs = [(e["type"], e.get("content")) for e in events]
        assert ("thinking", "思考甲") in pairs
        assert ("thinking", "思考乙") in pairs
        assert ("text", "回答") in pairs

    async def test_tool_call_arguments_accumulated_across_chunks(self, monkeypatch):
        """arguments 分片累积后整体 yield（:162-190）。"""
        fake = FakeClient(stream_lines=_sse_lines(
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_1",
                 "function": {"name": "add_note", "arguments": '{"ti'}}
            ]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": 'tle":"x"}'}}
            ]}}]},
        ))
        monkeypatch.setattr(llm_client, "get_client", lambda: fake)
        events = await _drain_stream(fake, "gpt-4o", tools=[{"type": "function"}])

        calls = [e for e in events if e["type"] == "tool_call"]
        assert len(calls) == 1
        assert calls[0]["name"] == "add_note"
        assert calls[0]["id"] == "call_1"
        assert calls[0]["args"] == {"title": "x"}, "分片应被拼接并解析为 dict"

    async def test_malformed_tool_arguments_degrade_to_empty_dict(self, monkeypatch):
        """arguments 非法 JSON → 退化为 {}，不抛异常（:181-184）。"""
        fake = FakeClient(stream_lines=_sse_lines(
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "c", "function": {"name": "f", "arguments": "{bad json"}}
            ]}}]},
        ))
        monkeypatch.setattr(llm_client, "get_client", lambda: fake)
        events = await _drain_stream(fake, "gpt-4o", tools=[{"type": "function"}])

        calls = [e for e in events if e["type"] == "tool_call"]
        assert len(calls) == 1
        assert calls[0]["args"] == {}

    async def test_tools_not_sent_when_none(self, monkeypatch):
        fake = FakeClient()
        monkeypatch.setattr(llm_client, "get_client", lambda: fake)
        await _drain_stream(fake, "gpt-4o", tools=None)
        assert "tools" not in fake.captured_stream["json"]

    async def test_malformed_sse_lines_are_skipped(self, monkeypatch):
        """非 `data: ` 前缀行、坏 JSON 都应被静默跳过（:131/:177）。"""
        fake = FakeClient(stream_lines=[
            "event: ping",
            ": keepalive",
            "data: {not json}",
            'data: {"choices":[{"delta":{"content":"活下来"}}]}',
            "data: [DONE]",
        ])
        monkeypatch.setattr(llm_client, "get_client", lambda: fake)
        events = await _drain_stream(fake, "gpt-4o")
        assert [e.get("content") for e in events if e["type"] == "text"] == ["活下来"]


# ────────────────────────── 错误路径：不抛异常，转文本 ──────────────────────────

class TestStreamErrorPaths:
    """错误被转成 text 事件（含 ❌ 前缀），不向上抛（:192-197）。

    ⚠️ 这个「错误伪装成正文」的设计是已知的脆弱点：
    `context_compressor.py:146` 靠 `"❌" in content` 判断是否跳过压缩。
    本组测试固化当前行为；若将来改为抛异常，需同步改该处判断。
    """

    async def test_connect_error_becomes_text_event(self, monkeypatch):
        class _BoomClient:
            def stream(self, *a, **k):
                raise httpx.ConnectError("boom")

        monkeypatch.setattr(llm_client, "get_client", lambda: _BoomClient())
        events = await _drain_stream(fake := FakeClient(), "gpt-4o")
        assert len(events) == 1
        assert events[0]["type"] == "text"
        assert "❌" in events[0]["content"]
        assert "无法连接" in events[0]["content"]
        assert fake.captured_stream is None

    async def test_http_status_error_becomes_text_event(self, monkeypatch):
        class _StatusClient:
            def stream(self, *a, **k):
                req = httpx.Request("POST", "https://api.test/v1/chat/completions")
                resp = httpx.Response(401, request=req, text='{"error":"bad key"}')
                raise httpx.HTTPStatusError("401", request=req, response=resp)

        monkeypatch.setattr(llm_client, "get_client", lambda: _StatusClient())
        events = await _drain_stream(FakeClient(), "gpt-4o")
        assert events[0]["type"] == "text"
        assert "❌" in events[0]["content"]

    async def test_generic_error_becomes_text_event(self, monkeypatch):
        class _BoomClient:
            def stream(self, *a, **k):
                raise RuntimeError("unexpected")

        monkeypatch.setattr(llm_client, "get_client", lambda: _BoomClient())
        events = await _drain_stream(FakeClient(), "gpt-4o")
        assert events[0]["type"] == "text"
        assert "连接错误" in events[0]["content"]
