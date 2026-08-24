"""共享 httpx 客户端 — 连接复用，避免每次请求新建/销毁 client。

llm_client / knowledge_service 等模块共用同一个 AsyncClient，
TCP 连接按 host 复用（httpx 内置连接池），显著降低高频调用的握手开销。
生命周期：懒加载创建，app lifespan 关闭时统一 close_client() 释放。
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger("zenith.http")

_shared_client: Optional[httpx.AsyncClient] = None


def get_client() -> httpx.AsyncClient:
    """获取共享 AsyncClient（首次调用懒加载）。

    默认 timeout 较宽松（read 120s / connect 10s），
    各调用点可用 `timeout=` 参数按需覆盖（如 health 检查用 5s）。
    """
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(max_connections=30, max_keepalive_connections=15),
        )
        logger.debug("共享 httpx 客户端已创建")
    return _shared_client


async def close_client() -> None:
    """关闭共享客户端（在 app lifespan 的 shutdown 阶段调用）。"""
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
        logger.debug("共享 httpx 客户端已关闭")
    _shared_client = None
