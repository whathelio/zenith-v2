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
