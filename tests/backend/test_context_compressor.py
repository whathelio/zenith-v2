"""context_compressor 单元测试 — 工具结果剪枝 / token 估算 / 触发判断 / 完整压缩"""
import pytest
from unittest.mock import AsyncMock

from backend.context_compressor import (
    prune_tool_result,
    estimate_tokens,
    _compress_triggered,
    _messages_tokens,
)


class TestPruneToolResult:
    def test_short_text_unchanged(self):
        assert prune_tool_result("短文本") == "短文本"

    def test_long_text_head_tail(self):
        s = "a" * 20000
        out = prune_tool_result(s, threshold_chars=8192, head_chars=4096, tail_chars=1024)
        assert out.startswith("a" * 4096)
        assert out.endswith("a" * 1024)
        assert "已省略" in out
        assert len(out) < len(s)

    def test_none_returns_empty(self):
        assert prune_tool_result(None) == ""

    def test_misconfig_no_inflation(self):
        s = "x" * 1000
        out = prune_tool_result(s, threshold_chars=500, head_chars=600, tail_chars=600)
        assert out == s  # head+tail >= len → 原样返回


class TestEstimateTokens:
    def test_empty(self):
        assert estimate_tokens("") == 0

    def test_int_and_monotonic(self):
        a = estimate_tokens("hello world")
        b = estimate_tokens("hello world " * 100)
        assert isinstance(a, int)
        assert a > 0
        assert b > a


class TestCompressTriggered:
    def _msgs(self, contents):
        return [{"role": "user" if i % 2 == 0 else "assistant", "content": c}
                for i, c in enumerate(contents)]

    def test_count_trigger(self):
        assert _compress_triggered(self._msgs(["hi"] * 20), threshold=20, token_budget=0)

    def test_no_trigger(self):
        assert not _compress_triggered(self._msgs(["hi"] * 5), threshold=20, token_budget=0)

    def test_token_trigger(self):
        msgs = self._msgs(["x" * 1000] * 10)  # 10000 字符 ≈ 3333 tokens > 3000
        assert _compress_triggered(msgs, threshold=100, token_budget=3000)

    def test_token_budget_zero_disabled(self):
        msgs = self._msgs(["x" * 1000] * 10)
        assert not _compress_triggered(msgs, threshold=100, token_budget=0)


class TestMessagesTokens:
    def test_only_user_assistant_counted(self):
        msgs = [
            {"role": "user", "content": "a" * 300},
            {"role": "assistant", "content": "b" * 300},
            {"role": "system", "content": "c" * 300},
        ]
        assert _messages_tokens(msgs) == 200  # 600 字符 / 3，system 不计入


class TestMaybeCompress:
    @pytest.mark.asyncio
    async def test_token_trigger_short_conversation(self, test_db, monkeypatch):
        """P1 回归：少量超长消息（<10 条）在 token 预算突破时也应压缩，不被 <4 条旧消息短路。"""
        from backend import context_compressor, database as db

        conv = db.conv_create()
        conv_id = conv["id"]
        # 8 条消息 × 2000 字符 = 16000 字符 ≈ 5333 tokens，远大于预算 100，但 < 阈值 100
        for i in range(8):
            db.msg_add(conv_id, "user" if i % 2 == 0 else "assistant", "x" * 2000)

        monkeypatch.setattr(
            context_compressor, "call_llm",
            AsyncMock(return_value={"content": '{"key_points":["a"],"context":"c"}'}),
        )
        monkeypatch.setattr(
            "backend.config.load_config",
            lambda: {"context_compress_threshold": 100, "context_token_budget": 100},
        )

        assert await context_compressor.maybe_compress(conv_id) is True

        msgs = db.msg_list(conv_id)
        assert any(m["role"] == "system" and m["content"].startswith("[历史摘要]") for m in msgs)
        non_sys = [m for m in msgs if m["role"] != "system"]
        assert len(non_sys) == 6  # keep_recent=6，其余被压缩为摘要

    @pytest.mark.asyncio
    async def test_compress_archives_not_deletes(self, test_db, monkeypatch):
        """D-C 回归：压缩后旧消息被归档（archived=1）而非物理删除，可恢复。"""
        from backend import context_compressor, database as db

        conv = db.conv_create()
        conv_id = conv["id"]
        for i in range(8):
            db.msg_add(conv_id, "user" if i % 2 == 0 else "assistant", "x" * 2000)

        monkeypatch.setattr(
            context_compressor, "call_llm",
            AsyncMock(return_value={"content": '{"key_points":["a"],"context":"c"}'}),
        )
        monkeypatch.setattr(
            "backend.config.load_config",
            lambda: {"context_compress_threshold": 100, "context_token_budget": 100},
        )

        assert await context_compressor.maybe_compress(conv_id) is True

        with db.db() as c:
            archived = c.execute(
                "SELECT COUNT(*) as cnt FROM messages WHERE conversation_id = ? AND archived = 1",
                (conv_id,),
            ).fetchone()
        assert archived["cnt"] == 2  # 2 条旧消息被归档而非删除
