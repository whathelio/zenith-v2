"""Cache Stats API — LLM 前缀缓存命中率统计（P2）"""
import logging
from fastapi import APIRouter
from ..database import cache_stats_summary

router = APIRouter(prefix="/api/cache", tags=["cache"])
logger = logging.getLogger("zenith.cache")


@router.get("/stats")
async def get_cache_stats(hours: int = 24):
    """聚合最近 N 小时 LLM 调用 token / 前缀缓存命中统计。"""
    try:
        return cache_stats_summary(hours=min(max(hours, 1), 720))
    except Exception as e:
        logger.warning("cache stats 查询失败: %s", e)
        return {"error": str(e)}
