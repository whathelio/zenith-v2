"""News API — 金十快讯代理（不落库，直接透传）

端点：
  GET /api/news/flash?cursor=     最新快讯流（TTL 30s 缓存）
  GET /api/news/search?keyword=   关键词搜索快讯（TTL 60s 缓存）

设计约束：
- 快讯不落库（用户裁定），仅代理查询 + 进程内 TTL 缓存
- jin10 token 缺失或调用失败 → 502 + 明确文案，不回退空 200
- 金十快讯条目原始字段为 {content, time, url}，无独立 title，
  标题从 content 的【】前缀提取，无则截断前 40 字
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["news"])
logger = logging.getLogger("zenith.news")

# 进程内 TTL 缓存: {cache_key: (expire_ts, payload)}
# 上限 100 条，防不同 keyword 无限累积（过期条目仅在访问或超限时清理）
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_MAX = 100
_FLASH_TTL = 30.0
_SEARCH_TTL = 60.0

_TITLE_PREFIX_RE = re.compile(r"^【([^】]{2,30})】")


def _get_cache(key: str) -> Optional[dict]:
    entry = _CACHE.get(key)
    if not entry:
        return None
    expire, payload = entry
    if time.monotonic() > expire:
        _CACHE.pop(key, None)
        return None
    return payload


def _set_cache(key: str, payload: dict, ttl: float) -> None:
    _CACHE[key] = (time.monotonic() + ttl, payload)
    if len(_CACHE) > _CACHE_MAX:
        expired = [k for k, (exp, _) in _CACHE.items() if time.monotonic() > exp]
        for k in expired:
            _CACHE.pop(k, None)
    if len(_CACHE) > _CACHE_MAX:
        oldest_key = min(_CACHE, key=lambda k: _CACHE[k][0])
        _CACHE.pop(oldest_key, None)


def _make_svc():
    """惰性导入金十服务（_archived 封存模块）。"""
    try:
        from .._archived.jin10_service import Jin10Service
        return Jin10Service()
    except Exception as e:  # noqa: BLE001
        logger.warning("金十服务初始化失败: %s", e)
        return None


def _normalize_flash_items(items: list, keyword: str = "") -> list[dict]:
    """规范化快讯条目: {id, title, content, time, url, keyword, source}"""
    out = []
    for i in items or []:
        content = (i.get("content") or "").strip()
        time_ = i.get("time") or ""
        url = i.get("url") or ""
        # id 从 url 提取，兜底用 content 哈希前缀
        m = re.search(r"/detail/([0-9a-zA-Z]+)", url)
        item_id = m.group(1) if m else str(abs(hash(content)) % 10 ** 10)
        # 标题：优先【】前缀，否则截断
        m2 = _TITLE_PREFIX_RE.match(content)
        title = m2.group(1) if m2 else (content[:40] + ("…" if len(content) > 40 else ""))
        out.append({
            "id": item_id,
            "title": title,
            "content": content,
            "time": time_,
            "url": url,
            "keyword": keyword,
            "source": "jin10_flash",
        })
    return out


@router.get("/api/news/flash")
async def get_flash(cursor: str = ""):
    """获取最新快讯流，支持游标分页。"""
    cache_key = f"flash:{cursor}"
    cached = _get_cache(cache_key)
    if cached is not None:
        return cached

    svc = _make_svc()
    if svc is None:
        raise HTTPException(502, "jin10 service unavailable: 服务初始化失败")
    try:
        if not svc._token:
            raise HTTPException(502, "jin10 service unavailable: ZENITH_JIN10_API_TOKEN 未配置")
        data = await svc.list_flash(cursor or None)
        if not isinstance(data, dict):
            raise HTTPException(502, "jin10 service unavailable: 快讯接口返回空")
        payload = {
            "items": _normalize_flash_items(data.get("items") or []),
            "next_cursor": data.get("next_cursor") or "",
            "has_more": bool(data.get("has_more")),
            "cached": False,
        }
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("快讯列表失败: %s", e)
        raise HTTPException(502, f"jin10 service unavailable: {type(e).__name__}")
    finally:
        try:
            await svc.close()
        except Exception:  # noqa: BLE001
            pass

    _set_cache(cache_key, payload, _FLASH_TTL)
    payload["cached"] = True
    return payload


@router.get("/api/news/search")
async def search_flash(keyword: str):
    """按关键词搜索快讯。"""
    keyword = (keyword or "").strip()
    if not keyword:
        raise HTTPException(400, "keyword 为必填参数")
    cache_key = f"search:{keyword}"
    cached = _get_cache(cache_key)
    if cached is not None:
        return cached

    svc = _make_svc()
    if svc is None:
        raise HTTPException(502, "jin10 service unavailable: 服务初始化失败")
    try:
        if not svc._token:
            raise HTTPException(502, "jin10 service unavailable: ZENITH_JIN10_API_TOKEN 未配置")
        data = await svc.search_flash(keyword)
        if not isinstance(data, dict):
            raise HTTPException(502, "jin10 service unavailable: 搜索接口返回空")
        payload = {
            "items": _normalize_flash_items(data.get("items") or [], keyword=keyword),
            "next_cursor": data.get("next_cursor") or "",
            "has_more": bool(data.get("has_more")),
            "keyword": keyword,
            "cached": False,
        }
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("快讯搜索失败: %s", e)
        raise HTTPException(502, f"jin10 service unavailable: {type(e).__name__}")
    finally:
        try:
            await svc.close()
        except Exception:  # noqa: BLE001
            pass

    _set_cache(cache_key, payload, _SEARCH_TTL)
    payload["cached"] = True
    return payload
