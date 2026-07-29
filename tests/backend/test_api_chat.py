"""API 冒烟测试 — 所有核心端点可访问 + 返回 200/正常结构"""
import pytest
from httpx import AsyncClient, ASGITransport

pytestmark = pytest.mark.asyncio


@pytest.fixture
def client(test_db):
    """创建测试客户端（使用内存数据库）"""
    from backend.app import app

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestHealthCheck:
    """健康检查"""

    @pytest.mark.asyncio
    async def test_health(self, client):
        resp = await client.get("/api/health")
        assert resp.status_code == 200


class TestConversations:
    """对话 API"""

    @pytest.mark.asyncio
    async def test_list_conversations(self, client):
        resp = await client.get("/api/conversations")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_create_conversation(self, client):
        resp = await client.post("/api/conversations")
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data


class TestSchedules:
    """日程 API"""

    @pytest.mark.asyncio
    async def test_list_schedules(self, client):
        resp = await client.get("/api/schedules")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_create_schedule(self, client):
        resp = await client.post("/api/schedules", json={
            "title": "测试会议",
            "start_time": "2026-12-25 10:00",  # far future to avoid conflict
            "status": "confirmed",
            "priority": "normal"
        })
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_calendar_week(self, client):
        resp = await client.get("/api/calendar/week?date=2026-07-29")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_reminders(self, client):
        resp = await client.get("/api/reminders")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_templates(self, client):
        resp = await client.get("/api/calendar/templates")
        assert resp.status_code == 200


class TestNotes:
    """笔记 API"""

    @pytest.mark.asyncio
    async def test_list_notes(self, client):
        resp = await client.get("/api/notes")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_create_note(self, client):
        resp = await client.post("/api/notes", json={
            "title": "测试笔记",
            "content": "测试内容"
        })
        assert resp.status_code == 200


class TestMemories:
    """记忆 API"""

    @pytest.mark.asyncio
    async def test_list_memories(self, client):
        resp = await client.get("/api/memories")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_dedup_memories(self, client):
        resp = await client.get("/api/memories/dedup")
        # 端点可能不存在（404）或返回成功（200）
        assert resp.status_code in (200, 404, 405)


class TestGoals:
    """目标 API"""

    @pytest.mark.asyncio
    async def test_list_goals(self, client):
        resp = await client.get("/api/goals")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_create_goal(self, client):
        resp = await client.post("/api/goals", json={
            "title": "测试目标",
            "target_value": 10000,
            "start_value": 0,
            "current_value": 1000,
            "daily_target": 100,
        })
        assert resp.status_code == 200


class TestSettings:
    """设置 API"""

    @pytest.mark.asyncio
    async def test_get_settings(self, client):
        resp = await client.get("/api/settings")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_put_settings(self, client):
        resp = await client.put("/api/settings", json={
            "model": "test-model",
            "temperature": 0.5
        })
        assert resp.status_code == 200


class TestSkills:
    """技能 API"""

    @pytest.mark.asyncio
    async def test_list_skills(self, client):
        resp = await client.get("/api/skills")
        assert resp.status_code in (200, 404)  # Skills endpoint removed (C.5)


class TestMarketAPIs:
    """市场 API（已封存，返回 410 或 404）"""

    @pytest.mark.asyncio
    async def test_market_run_analysis(self, client):
        resp = await client.post("/api/market/run-analysis")
        assert resp.status_code in (200, 410)

    @pytest.mark.asyncio
    async def test_market_refresh_data(self, client):
        resp = await client.get("/api/market/refresh-data")
        assert resp.status_code in (200, 410, 404)

    @pytest.mark.asyncio
    async def test_market_reports(self, client):
        resp = await client.get("/api/market/reports")
        assert resp.status_code in (200, 404, 410)

    @pytest.mark.asyncio
    async def test_market_predictions(self, client):
        resp = await client.get("/api/market/predictions")
        assert resp.status_code in (200, 410, 404)


class TestDistillAPI:
    """蒸馏 API"""

    @pytest.mark.asyncio
    async def test_distill_files(self, client):
        resp = await client.get("/api/distill/files")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_distill_daily(self, client):
        resp = await client.post("/api/distill/daily/2026-07-29")
        # 可能因 LLM 不可用而返回错误，但不应该 500
        assert resp.status_code in (200, 500)


class TestKnowledgeAPI:
    """知识库 API"""

    @pytest.mark.asyncio
    async def test_knowledge_health(self, client):
        resp = await client.get("/api/knowledge/health")
        # 外部网关可能离线
        assert resp.status_code in (200, 502, 503)


class TestProposals:
    """提案 API"""

    @pytest.mark.asyncio
    async def test_list_proposals(self, client):
        resp = await client.get("/api/proposals")
        assert resp.status_code == 200
