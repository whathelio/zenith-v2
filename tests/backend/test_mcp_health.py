"""MCP 真实健康检查单元测试（backlog Item 6）

核心命题：配置里的 ``enabled`` 只代表开关，不代表服务真的能连上。
本测试锁死「开关开着但连不上 → 必须报 error，绝不能报 ok」这条底线。

所有测试均为**离线**测试：用 fake client 替换 ``build_client``，
不拉起任何真实子进程、不访问任何端口，因此可在 CI/沙箱内稳定运行。
"""
import asyncio

import pytest

from backend import mcp_health


@pytest.fixture(autouse=True)
def _clean_cache():
    """每个用例前后清空模块级 TTL 缓存，避免用例间相互污染。"""
    mcp_health.invalidate()
    yield
    mcp_health.invalidate()


# ---------------------------------------------------------------- fake client


class FakeClient:
    """可编排行为的假 MCP 客户端。

    ``fail_on`` 决定在哪一步炸：
    - None：正常返回工具列表
    - "connect"：连接阶段抛异常
    - "list"：list_tools 阶段抛异常
    - "timeout"：连接阶段抛 asyncio.TimeoutError
    """

    def __init__(self, cfg, tools=None, fail_on=None, record=None):
        self.cfg = cfg
        self.tools = tools if tools is not None else [
            {"name": "verify_claim"}, {"name": "scan_contradictions"},
        ]
        self.fail_on = fail_on
        self.record = record if record is not None else []
        self.closed = False

    async def connect(self):
        self.record.append(("connect", self.cfg.get("name")))
        if self.fail_on == "connect":
            raise RuntimeError("spawn failed: No such file or directory")
        if self.fail_on == "timeout":
            raise asyncio.TimeoutError()

    async def list_tools(self):
        self.record.append(("list_tools", self.cfg.get("name")))
        if self.fail_on == "list":
            raise RuntimeError("broken pipe")
        return {"tools": self.tools}

    async def close(self):
        self.closed = True


def _patch(monkeypatch, **kwargs):
    """把 mcp_health.build_client 换成生成 FakeClient 的工厂。

    返回 record 列表，可用于断言「到底有没有真的发起握手」。
    """
    record: list = []

    def factory(cfg, trust_env=True):
        record.append(("build", cfg.get("name"), trust_env))
        return FakeClient(cfg, record=record, **kwargs)

    monkeypatch.setattr(mcp_health, "build_client", factory)
    return record


# ------------------------------------------------------------------ 纯函数


class TestHelpers:
    def test_local_target_recognizes_loopback(self):
        for url in ("http://localhost:8080/mcp", "http://127.0.0.1:22346/mcp",
                    "http://[::1]:9000", "http://0.0.0.0:1234"):
            assert mcp_health._local_target({"serverUrl": url}) is True, url

    def test_local_target_recognizes_remote(self):
        for url in ("https://mcp.example.com/sse", "http://192.168.1.50:8000"):
            assert mcp_health._local_target({"serverUrl": url}) is False, url

    def test_local_target_stdio_defaults_true(self):
        """stdio 不走 HTTP，无 URL 时视为本机，trust_env=False 无副作用。"""
        assert mcp_health._local_target({"command": "python"}) is True

    def test_extract_tools_accepts_dict_payload(self):
        assert len(mcp_health._extract_tools({"tools": [{"name": "a"}, {"name": "b"}]})) == 2

    def test_extract_tools_accepts_bare_list(self):
        assert len(mcp_health._extract_tools([{"name": "a"}])) == 1

    def test_extract_tools_filters_garbage(self):
        assert mcp_health._extract_tools({"tools": [{"name": "a"}, "oops", None]}) == [{"name": "a"}]

    def test_extract_tools_handles_nonsense(self):
        assert mcp_health._extract_tools(None) == []
        assert mcp_health._extract_tools("boom") == []


# ------------------------------------------------------------ 单服务检查


class TestCheckServer:
    @pytest.mark.asyncio
    async def test_disabled_skips_handshake(self, monkeypatch):
        """开关关着 → 不握手。既省子进程，也避免「禁用的服务被偷偷拉起来」。"""
        record = _patch(monkeypatch)
        out = await mcp_health.check_server(
            {"name": "x", "command": "python", "disabled": True}
        )
        assert out["state"] == "disabled"
        assert out["ok"] is False
        assert out["enabled"] is False
        assert record == [], "禁用服务不应发起任何连接"

    @pytest.mark.asyncio
    async def test_missing_transport_is_unknown(self, monkeypatch):
        record = _patch(monkeypatch)
        out = await mcp_health.check_server({"name": "y"})
        assert out["state"] == "unknown"
        assert out["transport"] == "unknown"
        assert "command" in out["error"] and "serverUrl" in out["error"]
        assert record == []

    @pytest.mark.asyncio
    async def test_successful_handshake(self, monkeypatch):
        _patch(monkeypatch, tools=[{"name": "a"}, {"name": "b"}, {"name": "c"}])
        out = await mcp_health.check_server({"name": "ok-svc", "command": "python"})
        assert out["state"] == "ok"
        assert out["ok"] is True
        assert out["tool_count"] == 3
        assert out["tools"] == ["a", "b", "c"]
        assert out["error"] == ""
        assert out["latency_ms"] >= 0

    @pytest.mark.asyncio
    async def test_connect_failure_is_not_ok(self, monkeypatch):
        """底线用例：开关开着但连不上，必须报 error，不能报 ok。"""
        _patch(monkeypatch, fail_on="connect")
        out = await mcp_health.check_server(
            {"name": "dead-svc", "command": "/nonexistent/python"}
        )
        assert out["enabled"] is True
        assert out["ok"] is False, "连不上却报成功 = 假绿灯，正是本模块要消灭的问题"
        assert out["state"] == "error"
        assert "RuntimeError" in out["error"]
        assert "spawn failed" in out["error"]

    @pytest.mark.asyncio
    async def test_list_tools_failure_is_error(self, monkeypatch):
        _patch(monkeypatch, fail_on="list")
        out = await mcp_health.check_server({"name": "half-dead", "command": "python"})
        assert out["state"] == "error"
        assert "broken pipe" in out["error"]

    @pytest.mark.asyncio
    async def test_timeout_reports_timeout(self, monkeypatch):
        _patch(monkeypatch, fail_on="timeout")
        out = await mcp_health.check_server({"name": "slow", "command": "python"})
        assert out["state"] == "error"
        assert "超时" in out["error"]

    @pytest.mark.asyncio
    async def test_local_target_disables_env_proxy(self, monkeypatch):
        """本机地址必须 trust_env=False，否则会被系统代理拦截（实测 502）。"""
        record = _patch(monkeypatch)
        await mcp_health.check_server(
            {"name": "local", "serverUrl": "http://127.0.0.1:22346/mcp"}
        )
        assert record[0][0] == "build"
        assert record[0][2] is False, "本机目标必须关闭 env 代理"

    @pytest.mark.asyncio
    async def test_remote_target_keeps_env_proxy(self, monkeypatch):
        record = _patch(monkeypatch)
        await mcp_health.check_server(
            {"name": "remote", "serverUrl": "https://mcp.example.com/sse"}
        )
        assert record[0][2] is True, "外网目标应保留 env 代理（可能需要走梯子）"

    @pytest.mark.asyncio
    async def test_base_exception_still_propagates(self, monkeypatch):
        """check_server 捕获 Exception，但**故意不吞** BaseException。

        KeyboardInterrupt / SystemExit 必须能穿透体检逻辑——否则用户在体检
        期间按 Ctrl-C 会被静默吃掉，服务也停不下来。这条用例锁死这个边界。
        """

        def boom(cfg, trust_env=True):
            raise KeyboardInterrupt("ctrl-c during check")

        monkeypatch.setattr(mcp_health, "build_client", boom)
        with pytest.raises(KeyboardInterrupt):
            await mcp_health.check_server({"name": "boom", "command": "python"})

    @pytest.mark.asyncio
    async def test_client_always_closed_on_failure(self, monkeypatch):
        """失败的客户端必须被回收，否则超时残留的子进程会变成野进程。"""
        record: list = []
        made: list = []

        def factory(cfg, trust_env=True):
            c = FakeClient(cfg, fail_on="connect", record=record)
            made.append(c)
            return c

        monkeypatch.setattr(mcp_health, "build_client", factory)
        await mcp_health.check_server({"name": "leaky", "command": "python"})
        assert made and made[0].closed is True


# ------------------------------------------------------------ 并发与快照


class TestCheckAllAndSnapshot:
    @pytest.mark.asyncio
    async def test_check_all_mixed_states(self, monkeypatch):
        """一个服务卡住不应拖累其他服务（各自独立超时、并发执行）。"""

        def factory(cfg, trust_env=True):
            fail = "connect" if cfg["name"] == "bad" else None
            return FakeClient(cfg, fail_on=fail, tools=[{"name": "t"}])

        monkeypatch.setattr(mcp_health, "build_client", factory)
        servers = [
            {"name": "good", "command": "python"},
            {"name": "bad", "command": "python"},
            {"name": "off", "command": "python", "disabled": True},
        ]
        out = await mcp_health.check_all(servers)
        by = {r["name"]: r for r in out}
        assert by["good"]["state"] == "ok"
        assert by["bad"]["state"] == "error"
        assert by["off"]["state"] == "disabled"

    @pytest.mark.asyncio
    async def test_check_all_survives_escaped_exception(self, monkeypatch):
        """gather 兜底：即使有异常逸出，也要补一条 error 记录而不是整体崩掉。"""

        def boom(cfg, trust_env=True):
            if cfg["name"] == "evil":
                raise BaseException("unexpected")
            return FakeClient(cfg, tools=[{"name": "t"}])

        monkeypatch.setattr(mcp_health, "build_client", boom)
        out = await mcp_health.check_all(
            [{"name": "evil", "command": "x"}, {"name": "fine", "command": "y"}]
        )
        assert len(out) == 2
        by = {r["name"]: r for r in out}
        assert by["evil"]["state"] == "error"
        assert by["fine"]["state"] == "ok"

    @pytest.mark.asyncio
    async def test_snapshot_counts(self, monkeypatch):
        def factory(cfg, trust_env=True):
            fail = "connect" if cfg["name"] == "bad" else None
            return FakeClient(cfg, fail_on=fail, tools=[{"name": "t"}])

        monkeypatch.setattr(mcp_health, "build_client", factory)
        servers = [
            {"name": "a", "command": "p"},
            {"name": "bad", "command": "p"},
            {"name": "off", "command": "p", "disabled": True},
        ]
        snap = await mcp_health.health_snapshot(servers)
        assert snap["count"] == 3
        assert snap["ok"] == 1
        assert snap["error"] == 1
        assert snap["disabled"] == 1
        assert snap["cached_count"] == 0

    @pytest.mark.asyncio
    async def test_snapshot_uses_ttl_cache(self, monkeypatch):
        calls: list = []

        def factory(cfg, trust_env=True):
            calls.append(cfg["name"])
            return FakeClient(cfg, tools=[{"name": "t"}])

        monkeypatch.setattr(mcp_health, "build_client", factory)
        servers = [{"name": "a", "command": "p"}, {"name": "b", "command": "p"}]

        await mcp_health.health_snapshot(servers)
        assert len(calls) == 2

        snap2 = await mcp_health.health_snapshot(servers)
        assert len(calls) == 2, "TTL 内不应重复握手"
        assert snap2["cached_count"] == 2
        assert all(r["cached"] for r in snap2["servers"])

    @pytest.mark.asyncio
    async def test_refresh_bypasses_cache(self, monkeypatch):
        calls: list = []

        def factory(cfg, trust_env=True):
            calls.append(cfg["name"])
            return FakeClient(cfg, tools=[{"name": "t"}])

        monkeypatch.setattr(mcp_health, "build_client", factory)
        servers = [{"name": "a", "command": "p"}]

        await mcp_health.health_snapshot(servers)
        snap = await mcp_health.health_snapshot(servers, refresh=True)
        assert len(calls) == 2
        assert snap["cached_count"] == 0

    @pytest.mark.asyncio
    async def test_expired_ttl_triggers_recheck(self, monkeypatch):
        calls: list = []

        def factory(cfg, trust_env=True):
            calls.append(cfg["name"])
            return FakeClient(cfg, tools=[{"name": "t"}])

        monkeypatch.setattr(mcp_health, "build_client", factory)
        servers = [{"name": "a", "command": "p"}]

        await mcp_health.health_snapshot(servers, ttl=60)
        await mcp_health.health_snapshot(servers, ttl=0)
        assert len(calls) == 2, "TTL=0 应视为立即过期"


class TestInvalidate:
    @pytest.mark.asyncio
    async def test_invalidate_one(self, monkeypatch):
        calls: list = []

        def factory(cfg, trust_env=True):
            calls.append(cfg["name"])
            return FakeClient(cfg, tools=[{"name": "t"}])

        monkeypatch.setattr(mcp_health, "build_client", factory)
        servers = [{"name": "a", "command": "p"}, {"name": "b", "command": "p"}]

        await mcp_health.health_snapshot(servers)
        mcp_health.invalidate("a")
        snap = await mcp_health.health_snapshot(servers)
        # a 重测、b 命中缓存
        assert calls.count("a") == 2
        assert calls.count("b") == 1
        assert snap["cached_count"] == 1

    @pytest.mark.asyncio
    async def test_invalidate_all(self, monkeypatch):
        calls: list = []

        def factory(cfg, trust_env=True):
            calls.append(cfg["name"])
            return FakeClient(cfg, tools=[{"name": "t"}])

        monkeypatch.setattr(mcp_health, "build_client", factory)
        servers = [{"name": "a", "command": "p"}]

        await mcp_health.health_snapshot(servers)
        mcp_health.invalidate()
        await mcp_health.health_snapshot(servers)
        assert len(calls) == 2


# ------------------------------------------------------------------ 路由


class TestRoutes:
    @pytest.fixture
    def client(self, test_db, monkeypatch):
        from httpx import AsyncClient, ASGITransport
        from backend.app import app

        calls: list = []

        def factory(cfg, trust_env=True):
            calls.append(cfg.get("name"))
            fail = "connect" if cfg.get("name") == "dead" else None
            return FakeClient(cfg, fail_on=fail, tools=[{"name": "t1"}, {"name": "t2"}])

        monkeypatch.setattr(mcp_health, "build_client", factory)
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    @pytest.mark.asyncio
    async def test_health_route_precedes_name_route(self, client):
        """/mcp/health 必须排在 /mcp/{name} 之前，否则 "health" 会被当服务名吃掉。"""
        resp = await client.get("/api/modules/mcp/health?refresh=1")
        assert resp.status_code == 200
        body = resp.json()
        assert "servers" in body
        assert set(["count", "ok", "error", "disabled"]) <= set(body)

    @pytest.mark.asyncio
    async def test_health_route_accepts_refresh_and_timeout(self, client):
        resp = await client.get("/api/modules/mcp/health?refresh=1&timeout=5&ttl=10")
        assert resp.status_code == 200
        body = resp.json()
        assert body["timeout"] == 5
        assert body["ttl"] == 10

    @pytest.mark.asyncio
    async def test_health_route_rejects_out_of_range_timeout(self, client):
        assert (await client.get("/api/modules/mcp/health?timeout=999")).status_code == 422

    @pytest.mark.asyncio
    async def test_single_health_route(self, client):
        """按名字查单个；未体检过的服务名应 404 而不是编造结果。"""
        allsnap = await client.get("/api/modules/mcp/health?refresh=1")
        names = [s["name"] for s in allsnap.json()["servers"]]
        if names:
            resp = await client.get(f"/api/modules/mcp/health/{names[0]}?refresh=1")
            assert resp.status_code == 200
            assert resp.json()["name"] == names[0]

    @pytest.mark.asyncio
    async def test_unknown_name_returns_404(self, client):
        resp = await client.get("/api/modules/mcp/health/__no_such_server__")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_never_reports_ok_for_unreachable(self, client):
        """端到端底线：连不上的服务在 API 里必须是 error，不能是 ok。"""
        body = (await client.get("/api/modules/mcp/health?refresh=1")).json()
        for s in body["servers"]:
            if s["state"] == "error":
                assert s["ok"] is False
                assert s["error"], "error 状态必须带可读原因"
            if s["ok"]:
                assert s["state"] == "ok"


class TestMutationRoutes:
    """回归防护：路由里的延迟 import 写错包层级会静默变成 500。

    `modules.py` 位于 ``backend/routers/``，所以 ``from .mcp_config import``
    解析的是 ``backend.routers.mcp_config``（不存在），正确写法是 ``..mcp_config``。
    这个错误只会在**运行时**暴露，且被 ``except`` 包成 500，静态检查抓不到。
    以下用例用 monkeypatch 拦截落盘，在不动真实配置的前提下锁死这条路径。
    """

    @pytest.fixture
    def client(self, test_db, monkeypatch):
        from httpx import AsyncClient, ASGITransport
        from backend import mcp_config
        from backend.app import app

        calls: list = []

        def fake_save(name, disabled):
            calls.append(("save", name, disabled))

        def fake_clear(name):
            calls.append(("clear", name))

        monkeypatch.setattr(mcp_config, "save_mcp_override", fake_save)
        monkeypatch.setattr(mcp_config, "clear_mcp_override", fake_clear)
        c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        c._calls = calls  # type: ignore[attr-defined]
        return c

    @pytest.mark.asyncio
    async def test_toggle_enable_does_not_500(self, client):
        """启停开关不能因为 import 写错而变成 500。"""
        resp = await client.put(
            "/api/modules/mcp/some-service", json={"enabled": False}
        )
        assert resp.status_code == 200, resp.text
        assert ("save", "some-service", True) in client._calls

    @pytest.mark.asyncio
    async def test_toggle_enable_true(self, client):
        resp = await client.put(
            "/api/modules/mcp/some-service", json={"enabled": True}
        )
        assert resp.status_code == 200, resp.text
        assert ("save", "some-service", False) in client._calls

    @pytest.mark.asyncio
    async def test_delete_clears_override(self, client):
        resp = await client.delete("/api/modules/mcp/some-service")
        assert resp.status_code == 200, resp.text
        assert ("clear", "some-service") in client._calls

    @pytest.mark.asyncio
    async def test_toggle_requires_enabled_or_disabled(self, client):
        resp = await client.put("/api/modules/mcp/some-service", json={})
        assert resp.status_code == 400
