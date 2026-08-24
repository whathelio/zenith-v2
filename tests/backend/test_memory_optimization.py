"""对话→记忆模块优化回归测试 — 覆盖 flush / 检索 / mem_touch / 衰减 / 相似度（K1–K5）"""
import pytest

from backend.memory_engine import (
    _conv_text_buffer, _conv_counters,
    flush_all_pending_memories, _retrieve_related_memories,
    mem_touch, mem_consolidate, _is_duplicate, _similarity,
)
from backend.database import mem_add, mem_get, db


class TestFlushAllPending:
    """K1 — 优雅关闭 flush 各对话残余 buffer"""

    @pytest.mark.asyncio
    async def test_flush_clears_buffer(self, test_db):
        _conv_text_buffer.clear()
        _conv_counters.clear()
        _conv_text_buffer["conv-k1"] = "用户偏好深色主题"
        flushed = await flush_all_pending_memories()
        # buffer 已消费（LLM 无 key 时返回空，但不会崩溃，且残余文本被 pop）
        assert "conv-k1" not in _conv_text_buffer
        assert flushed >= 1


class TestRetrieveRelated:
    """K2 — 检索与对话文本相关的已有记忆"""

    def test_retrieve_returns_related(self, test_db):
        mem_add(
            type_="preference",
            content="用户偏好使用 FastAPI 构建后端",
            importance=5,
            keywords="FastAPI,后端",
        )
        r = _retrieve_related_memories("FastAPI 后端相关问题", limit=10)
        assert isinstance(r, list)
        assert any("FastAPI" in x for x in r)


class TestMemTouch:
    """K3 — 引用提升重要度 + 刷新 last_touched_at"""

    def test_touch_bumps_importance_and_timestamp(self, test_db):
        mid = mem_add(type_="fact", content="测试记忆 touch", importance=3)
        mem_touch(mid)
        m = mem_get(mid)
        assert m["importance"] == 4
        assert m["last_touched_at"]  # 非空


class TestConsolidateDecay:
    """K4 — 衰减跳过 user_edited，按 last_touched_at 判断"""

    def test_decay_skips_user_edited(self, test_db):
        old = "2026-01-01T00:00:00"
        # 填充记忆，满足 mem_consolidate 的 >=10 条阈值（否则 early return 不衰减）
        for i in range(8):
            mem_add(type_="fact", content=f"填充记忆{i}", importance=2)
        mid_normal = mem_add(type_="fact", content="普通旧记忆", importance=2)
        mid_edited = mem_add(type_="fact", content="手工编辑旧记忆", importance=2)
        with db() as c:
            c.execute(
                "UPDATE memories SET created_at=?, recorded_at=NULL, last_touched_at=NULL WHERE id=?",
                (old, mid_normal),
            )
            c.execute(
                "UPDATE memories SET created_at=?, recorded_at=NULL, last_touched_at=NULL, user_edited=1 WHERE id=?",
                (old, mid_edited),
            )
        mem_consolidate()
        assert mem_get(mid_edited)["importance"] == 2  # 手工记忆不衰减
        assert mem_get(mid_normal)["importance"] == 1  # 普通记忆衰减


class TestSimilarityThreshold:
    """K5 — 相似度重写后，近重复与不同语义可被正确区分"""

    def test_near_duplicate_vs_different(self, test_db):
        mem_add(
            type_="experience",
            content="早晨复盘交易时关注黄金走势和成交量变化",
            importance=3,
            keywords="复盘,黄金,成交量",
        )
        sim_dup = _similarity(
            "早晨复盘交易时关注黄金走势和成交量变化",
            "早晨复盘交易时关注黄金走势变化",
        )
        sim_diff = _similarity(
            "早晨复盘交易时关注黄金走势和成交量变化",
            "今天天气很好适合出门",
        )
        assert sim_dup >= 0.7, f"近重复应 >=0.7，实际 {sim_dup}"
        assert sim_diff < 0.4, f"不同语义应 <0.4，实际 {sim_diff}"

    def test_default_threshold_detects_near_duplicate(self, test_db):
        mem_add(
            type_="experience",
            content="早晨复盘交易时关注黄金走势和成交量变化",
            importance=3,
            keywords="复盘,黄金,成交量",
        )
        # 近重复句在默认 0.75 阈值下应被判定为重复
        assert _is_duplicate("早晨复盘交易时关注黄金走势变化") is True
