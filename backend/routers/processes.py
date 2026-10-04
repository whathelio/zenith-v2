"""本机进程 / 端口监控 API。

⚠️ 采集是**阻塞调用**（PowerShell + netstat，实测约 1.75s）。
   app.py 与所有现有 routers 均无阻塞调用先例，因此这里必须走
   `asyncio.to_thread`——直接在 async 里跑会卡住事件循环、拖慢 8766 的对话流。
"""
import asyncio
import logging

from fastapi import APIRouter, Query

from ..process_monitor import TTL_SECONDS, invalidate_cache, snapshot

router = APIRouter(prefix="/api/processes", tags=["processes"])
logger = logging.getLogger("zenith.processes")


@router.get("/snapshot")
async def get_snapshot(
    force: bool = Query(False, description="强制重采，忽略 TTL 缓存"),
    ttl: int = Query(TTL_SECONDS, ge=0, le=600, description="缓存有效期（秒）"),
):
    """返回本机进程 / 端口快照（命令行**始终脱敏** + 15 秒缓存）。

    2026-09-11：移除原 `raw` 查询参数 —— 它允许任意本机进程通过
    `?raw=true` 拿到未脱敏的完整命令行（可能含 token / 密钥）。
    全项目无任何消费者（前端只读已脱敏的 cmd），属无用的调试后门。
    `process_monitor.snapshot()` 的 raw 形参保留（不删旧实现，便于回滚）。
    """
    try:
        return await asyncio.to_thread(snapshot, force=force, ttl=ttl)
    except Exception as e:                              # noqa: BLE001
        logger.warning("进程快照采集失败: %s", e)
        return {"error": str(e)}


@router.post("/invalidate")
async def post_invalidate():
    """清空快照缓存（下次请求重采）。"""
    invalidate_cache()
    return {"ok": True}
