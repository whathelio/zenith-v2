"""Zenith v2 — MCP 服务真实健康检查（backlog Item 6）

配置里的 ``enabled`` 标志只回答「用户有没有打开开关」，回答不了
「这个服务现在到底能不能用」——脚本被删、解释器路径失效、依赖缺失、
端口没监听，配置里统统看不出来，而 UI 此前照样显示绿灯。

本模块通过**真实握手**回答后者：
- stdio：拉起子进程 → ``initialize`` → ``tools/list``
- http ：POST ``initialize`` → ``tools/list``（本机地址自动绕开系统代理）

设计约定：
- **临时客户端**：每次检查新建并 close，不进 ``mcp_client._POOL``，
  因此不会干扰正在使用的长连接会话。
- **TTL 缓存**：避免每次打开面板都拉起全部子进程；配置变更时由路由
  调 :func:`invalidate` 主动失效（事件驱动，不做后台轮询）。
- **永不抛异常**：所有失败收敛到返回值的 ``state`` / ``error`` 字段。
"""
from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlparse

from .mcp_client import build_client

logger = logging.getLogger("zenith.mcp_health")

__all__ = [
    "DEFAULT_TIMEOUT",
    "DEFAULT_TTL",
    "check_server",
    "check_all",
    "health_snapshot",
    "invalidate",
]

# stdio 需要拉起 Python 进程并 import mcp 库，冷启动可能数秒；HTTP 通常 <1s
DEFAULT_TIMEOUT = 15.0
# TTL 缓存秒数
DEFAULT_TTL = 60.0
# 清理临时客户端的兜底超时
_CLOSE_TIMEOUT = 5.0

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}

# name -> {"result": dict, "ts": monotonic 时刻}
_CACHE: dict[str, dict] = {}


def _local_target(cfg: dict) -> bool:
    """目标是否本机地址。

    本机地址必须让 httpx 跳过环境变量代理（``trust_env=False``）,
    否则请求会被系统代理拦截 —— 本机实测经代理访问 localhost 返回 502。
    stdio 不经过 HTTP，返回 True 无副作用。
    """
    url = cfg.get("serverUrl") or ""
    if not url:
        return True
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host in _LOCAL_HOSTS


def _extract_tools(payload) -> list[dict]:
    """从 ``tools/list`` 的返回值里取工具列表，兼容 dict 与 list 两种形态。"""
    if isinstance(payload, dict):
        tools = payload.get("tools", [])
    elif isinstance(payload, list):
        tools = payload
    else:
        tools = []
    return [t for t in tools if isinstance(t, dict)]


def _blank(name: str, enabled: bool, error: str, state: str = "error") -> dict:
    return {
        "name": name,
        "transport": "unknown",
        "enabled": enabled,
        "ok": False,
        "state": state,
        "latency_ms": 0,
        "tool_count": 0,
        "tools": [],
        "error": error,
        "cached": False,
    }


async def check_server(cfg: dict, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """对单个 MCP 服务做真实握手检查，永不抛异常。"""
    name = cfg.get("name") or ""
    if cfg.get("command"):
        transport = "stdio"
    elif cfg.get("serverUrl"):
        transport = "http"
    else:
        transport = "unknown"
    enabled = not cfg.get("disabled", False)

    out = {
        "name": name,
        "transport": transport,
        "enabled": enabled,
        "ok": False,
        "state": "unknown",
        "latency_ms": 0,
        "tool_count": 0,
        "tools": [],
        "error": "",
        "cached": False,
    }

    # 开关关着就不握手：既省一次子进程，也避免「被禁用的服务反而被拉起来」
    if not enabled:
        out["state"] = "disabled"
        out["error"] = "开关已关闭，未做连接检查"
        return out
    if transport == "unknown":
        out["state"] = "unknown"
        out["error"] = "配置缺少 command 与 serverUrl"
        return out

    started = time.monotonic()
    client = None
    try:
        client = build_client(cfg, trust_env=not _local_target(cfg))
        await asyncio.wait_for(client.connect(), timeout=timeout)
        payload = await asyncio.wait_for(client.list_tools(), timeout=timeout)
        names = [t.get("name") for t in _extract_tools(payload) if t.get("name")]
        out["ok"] = True
        out["state"] = "ok"
        out["tools"] = names[:50]
        out["tool_count"] = len(names)
    except asyncio.TimeoutError:
        out["state"] = "error"
        out["error"] = f"握手超时（>{timeout:.0f}s）"
    except Exception as e:  # MCPClientError / OSError / 子进程启动失败等
        out["state"] = "error"
        out["error"] = f"{type(e).__name__}: {e}"[:300]
    finally:
        out["latency_ms"] = round((time.monotonic() - started) * 1000)
        # 成败都要收掉临时客户端，否则超时残留的子进程会变成野进程
        if client is not None:
            try:
                await asyncio.wait_for(client.close(), timeout=_CLOSE_TIMEOUT)
            except Exception:
                pass

    if out["state"] == "error":
        logger.warning("MCP 健康检查失败 %s: %s", name, out["error"])
    return out


async def check_all(servers: list[dict], timeout: float = DEFAULT_TIMEOUT) -> list[dict]:
    """并发检查全部服务；单个服务卡住不拖累整体（各自独立超时）。"""
    results = await asyncio.gather(
        *(check_server(s, timeout=timeout) for s in servers),
        return_exceptions=True,
    )
    out: list[dict] = []
    for s, r in zip(servers, results):
        if isinstance(r, BaseException):
            out.append(_blank(s.get("name") or "", not s.get("disabled", False),
                              f"{type(r).__name__}: {r}"[:300]))
        else:
            out.append(r)
    return out


async def health_snapshot(
    servers: list[dict],
    timeout: float = DEFAULT_TIMEOUT,
    ttl: float = DEFAULT_TTL,
    refresh: bool = False,
) -> dict:
    """带 TTL 缓存的全量健康快照。

    ``refresh=True`` 时忽略缓存全部重测。未过期的条目直接复用，
    因此混合场景下只有真正需要重测的服务才会被拉起。
    """
    now = time.monotonic()
    results: list[dict | None] = [None] * len(servers)
    todo: list[tuple[int, dict]] = []
    from_cache = 0

    for i, s in enumerate(servers):
        name = s.get("name") or ""
        hit = None if refresh else _CACHE.get(name)
        if hit and (now - hit["ts"]) < ttl:
            cached = dict(hit["result"])
            cached["cached"] = True
            results[i] = cached
            from_cache += 1
        else:
            todo.append((i, s))

    if todo:
        fresh = await check_all([s for _, s in todo], timeout=timeout)
        for (i, s), r in zip(todo, fresh):
            _CACHE[s.get("name") or ""] = {"result": r, "ts": time.monotonic()}
            results[i] = r

    final = [r for r in results if r is not None]
    return {
        "servers": final,
        "count": len(final),
        "ok": sum(1 for r in final if r.get("ok")),
        "error": sum(1 for r in final if r.get("state") == "error"),
        "disabled": sum(1 for r in final if r.get("state") == "disabled"),
        "cached_count": from_cache,
        "timeout": timeout,
        "ttl": ttl,
    }


def invalidate(name: str | None = None) -> None:
    """清除健康检查结果缓存。

    配置变更（新增/删除/启停/导入）后由路由调用，避免展示过期结论。
    传 ``name`` 只失效单个服务，不传则全清。
    """
    if name is None:
        _CACHE.clear()
    else:
        _CACHE.pop(name, None)
