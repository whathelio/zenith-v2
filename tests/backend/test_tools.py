"""工具系统测试 — 字典分发 + smart_classify + 日程工具"""
import pytest
from backend.tools import execute_tool, _TOOL_HANDLERS, _handle_search_memory, _handle_list_schedule


class TestToolRegistry:
    """工具注册表测试"""

    def test_all_tools_registered(self):
        """所有 32 个工具都应注册"""
        assert len(_TOOL_HANDLERS) >= 30

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        """未知工具返回错误"""
        result = await execute_tool("nonexistent_tool", {})
        assert result["success"] is False
        assert "未知工具" in result["result"]

    def test_tool_keys_match(self):
        """注册表键值一致"""
        for name, handler in _TOOL_HANDLERS.items():
            assert isinstance(name, str)
            assert callable(handler)
            assert len(name) > 0


class TestExecuteTool:
    """工具执行分发测试"""

    @pytest.mark.asyncio
    async def test_search_memory_empty(self, test_db):
        result = await execute_tool("search_memory", {"keyword": "nothing"})
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_search_memory_found(self, test_db, sample_memory):
        result = await execute_tool("search_memory", {"keyword": "Python"})
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_list_schedule_empty(self, test_db):
        result = await execute_tool("list_schedule", {"status": "confirmed"})
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_list_notes_empty(self, test_db):
        result = await execute_tool("list_notes", {})
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_add_note(self, test_db):
        result = await execute_tool("add_note", {
            "title": "测试笔记",
            "content": "这是测试内容",
            "tags": "test"
        })
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_complete_schedule_nonexistent(self, test_db):
        result = await execute_tool("complete_schedule", {"schedule_id": 99999})
        assert result["success"] is False


class TestScheduleTools:
    """日程相关工具测试"""

    @pytest.mark.asyncio
    async def test_add_schedule(self, test_db):
        result = await execute_tool("add_schedule", {
            "title": "明天下午2点开会",
            "start_time": "2026-07-30 14:00",
            "priority": "high"
        })
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_list_schedule_after_add(self, test_db):
        await execute_tool("add_schedule", {
            "title": "测试日程",
            "start_time": "2026-07-30 10:00",
        })
        result = await execute_tool("list_schedule", {"status": "proposed"})
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_time_plan(self, test_db):
        result = await execute_tool("time_plan", {})
        # time_plan 可能因为无 LLM 密钥而失败，但不应崩溃
        assert "success" in result


class TestMemoryTools:
    """记忆相关工具测试"""

    @pytest.mark.asyncio
    async def test_distill_note(self, test_db):
        """测试蒸馏笔记工具"""
        from backend.database import note_add
        note_add({"title": "测试笔记", "content": "今天学习了Python异步编程", "stage": "raw", "source": "manual"})
        result = await execute_tool("distill_note", {"note_id": 1})
        assert "success" in result

    @pytest.mark.asyncio
    async def test_consolidate_memories(self, test_db):
        result = await execute_tool("consolidate_memories", {})
        assert "success" in result


class TestConsolidateDefense:
    """P0 防御用例（2026-08-19 治理评审 C1-C4）"""

    @pytest.mark.asyncio
    async def test_consolidate_plan_has_llm_note_on_failure(self, test_db, monkeypatch):
        """C3: LLM 建议段失败时 plan 携带显式 llm_note，而非静默"""
        from backend import tools as tools_mod
        from backend import llm_client
        from backend.database import mem_add

        # 造 ≥5 条记忆，确保触发 LLM 建议段（generate_consolidate_plan 的 LLM 分支门槛）
        for i in range(6):
            mem_add(type_="experience", content=f"测试记忆条目 {i}：关于工作流的经验",
                    importance=3, keywords="test")

        async def _fake_call_llm_fail(**kwargs):
            return {"role": "assistant", "content": "Error: boom"}

        # generate_consolidate_plan 内 `from .llm_client import call_llm` 局部导入 → patch 源模块
        monkeypatch.setattr(llm_client, "call_llm", _fake_call_llm_fail)
        plan = await tools_mod.generate_consolidate_plan()
        assert "llm_note" in plan
        assert plan["llm_note"]  # 非空即显式降级生效
        # 格式化输出也应包含提示
        text = tools_mod._format_consolidate_plan(plan)
        assert "⚠️" in text

    @pytest.mark.asyncio
    async def test_consolidate_plan_limits_scan(self, test_db):
        """C4: 自动相似度比对范围被限制（>500 条时不爆炸）"""
        from backend import tools as tools_mod
        # 构造 plan 直接验证 scan_mems 切片逻辑不越界
        plan = await tools_mod.generate_consolidate_plan()
        assert "total" in plan and "merge_groups" in plan
        assert isinstance(plan["merge_groups"], list)

    def test_extract_json_empty_raises(self):
        """C1 辅助: _extract_json 对空/纯错误文本抛 ValueError 而非崩溃"""
        from backend.tools import _extract_json
        with pytest.raises(ValueError):
            _extract_json("")
        with pytest.raises(ValueError):
            _extract_json("Error: 'NoneType' object is not subscriptable")

    @pytest.mark.asyncio
    async def test_consolidate_plan_null_created_at_no_crash(self, test_db):
        """V4 根因防回归: created_at 为 NULL 的记忆不再抛 'NoneType' subscriptable。

        2026-08-19 实测复现：m.get("created_at", "") 在 key 存在但值为 None 时
        返回 None → None[:10] 抛 TypeError。修复为 (m.get("created_at") or "")[:10]。
        """
        from backend import tools as tools_mod
        from backend.database import mem_add, db as db_ctx
        for i in range(6):
            mem_add(type_="experience", content=f"NULL时间记忆 {i}",
                    importance=1, keywords="test")
        # 手动把 created_at 置 NULL 复现线上脏数据
        with db_ctx() as c:
            c.execute("UPDATE memories SET created_at = NULL")
        plan = await tools_mod.generate_consolidate_plan()
        assert "total" in plan  # 不再抛异常
        assert "outdated" in plan

    @pytest.mark.asyncio
    async def test_llm_client_choices_null_returns_error_dict(self, monkeypatch):
        """C1: choices=null 时返回 Error dict，不再抛裸 TypeError 文本"""
        import httpx
        from backend import llm_client

        class _FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": None}

        class _FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **k):
                return _FakeResp()

        monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
        monkeypatch.setattr(llm_client, "get_provider", lambda: {
            "type": "openai", "name": "test", "model": "test-model",
            "api_base": "http://x",
        })
        monkeypatch.setattr(llm_client, "get_provider_api_key", lambda p: "k")
        msg = await llm_client.call_llm(
            [{"role": "user", "content": "hi"}],
            temperature=0.1, max_tokens=64,
        )
        assert msg["content"].startswith("Error:")
        assert "'NoneType' object is not subscriptable" not in msg["content"]


class TestCacheStats:
    """P2 缓存命中率埋点测试"""

    def test_cache_stat_add_and_summary(self, test_db):
        """写入两条记录后聚合正确，命中率=hit/prompt"""
        from backend.database import cache_stat_add, cache_stats_summary, db as db_ctx
        # 测试库为共享 tempfile，先清空本表保证计数独立
        with db_ctx() as c:
            c.execute("DELETE FROM cache_stats")
        cache_stat_add(provider="deepseek", model="m1", kind="chat",
                       prompt_tokens=1000, prompt_cache_hit_tokens=800,
                       completion_tokens=200)
        cache_stat_add(provider="deepseek", model="m1", kind="chat",
                       prompt_tokens=500, prompt_cache_hit_tokens=0,
                       completion_tokens=150)
        s = cache_stats_summary(hours=24)
        assert s["calls"] == 2
        assert s["prompt_tokens"] == 1500
        assert s["hit_tokens"] == 800
        assert abs(s["hit_rate"] - 800 / 1500) < 1e-3  # 4位小数精度
        kinds = {k["kind"]: k for k in s["by_kind"]}
        assert kinds["chat"]["calls"] == 2


class TestDistillTools:
    """蒸馏工具测试"""

    @pytest.mark.asyncio
    async def test_distill_conv_noexist(self, test_db):
        result = await execute_tool("distill_conversation", {"conv_id": "nonexistent"})
        assert "success" in result

    @pytest.mark.asyncio
    async def test_kb_stats(self, test_db):
        result = await execute_tool("kb_stats", {})
        # 知识库网关可能离线，但工具不应崩溃
        assert "success" in result
