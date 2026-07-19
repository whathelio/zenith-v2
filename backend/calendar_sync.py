"""Zenith v2 — 外部财经日历同步模块

仅同步「财经事件时间」到本地缓存 (schedule_events)，不获取行情价格，
与市场分析模块保持隔离。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from .timezone import now_tz
from . import database as db
from .jin10_service import get_jin10_service

logger = logging.getLogger("zenith.calendar_sync")


# 关键词 → 通用事件别名，用于匹配用户输入与缓存事件
EVENT_KEYWORDS = {
    "非农": ["非农就业", "非农", "NFP", "Non-Farm Payrolls"],
    "CPI": ["CPI", "消费者物价指数"],
    "PPI": ["PPI", "生产者物价指数"],
    "FOMC": ["FOMC", "美联储利率决议", "美联储"],
    "GDP": ["GDP", "国内生产总值"],
    "失业金": ["初请失业金", "续请失业金", "失业金"],
    "非农": ["非农就业", "非农", "NFP"],
    "零售销售": ["零售销售", "Retail Sales"],
    "制造业PMI": ["制造业PMI", "ISM制造业"],
    "服务业PMI": ["服务业PMI", "ISM服务业"],
}


def _event_match_score(name: str, keyword: str) -> float:
    """简单匹配：关键词出现在事件名中即高匹配。"""
    name_lower = name.lower()
    keyword_lower = keyword.lower()
    if keyword_lower in name_lower:
        return 1.0
    # 中文字符逐字匹配（仅当关键词含中文时）
    chinese_chars = [ch for ch in keyword if "\u4e00" <= ch <= "\u9fff"]
    if chinese_chars and all(ch in name for ch in chinese_chars):
        return 0.8
    return 0.0


def find_event_by_keyword(keyword: str, date_from: str = "", date_to: str = "", min_star: int = 1) -> Optional[dict]:
    """根据关键词查找未来/指定区间内的外部财经事件，返回最佳匹配项。"""
    if not date_from:
        date_from = now_tz().isoformat()
    if not date_to:
        date_to = (now_tz() + timedelta(days=14)).isoformat()

    events = db.event_list(date_from=date_from, date_to=date_to, min_star=min_star, limit=200)
    if not events:
        return None

    # 1. 直接用 keyword 匹配
    candidates = []
    for ev in events:
        score = _event_match_score(ev.get("name", ""), keyword)
        if score > 0:
            candidates.append((score, ev))

    # 2. 用别名扩展匹配
    aliases = EVENT_KEYWORDS.get(keyword.upper(), []) + EVENT_KEYWORDS.get(keyword, [])
    for alias in aliases:
        for ev in events:
            score = _event_match_score(ev.get("name", ""), alias)
            if score > 0:
                candidates.append((score * 0.95, ev))

    if not candidates:
        return None

    # 取匹配度最高、时间最近的事件
    candidates.sort(key=lambda x: (-x[0], x[1].get("event_time", "")))
    return candidates[0][1]


def _parse_event_time(pub_time: str) -> str:
    """把金十 pub_time 转成 ISO 格式（+08:00）。"""
    if not pub_time:
        return ""
    try:
        # 金十常见格式：2026-07-17 20:30
        dt = datetime.strptime(pub_time[:16], "%Y-%m-%d %H:%M")
        return dt.replace(tzinfo=now_tz().tzinfo).isoformat()
    except Exception:
        return pub_time


async def sync_calendar_events(days: int = 7, min_star: int = 2) -> dict:
    """从外部数据源同步未来 N 天财经事件到本地缓存。

    Returns: {synced: int, errors: list, next_sync: str}
    """
    svc = get_jin10_service()
    errors = []
    synced = 0

    try:
        raw_events = await svc.list_calendar()
        if raw_events is None:
            errors.append("无法获取外部财经日历（返回 None）")
            return {"synced": 0, "errors": errors, "next_sync": ""}

        now = now_tz()
        cutoff = now + timedelta(days=days)

        for ev in raw_events:
            try:
                event_time = _parse_event_time(ev.get("pub_time", ""))
                if not event_time:
                    continue
                dt = datetime.fromisoformat(event_time)
                if dt < now or dt > cutoff:
                    continue

                star = ev.get("star", 1) or 1
                try:
                    star = int(star)
                except Exception:
                    star = 1
                if star < min_star:
                    continue

                db.event_add_or_update({
                    "name": ev.get("title", ""),
                    "event_time": event_time,
                    "star": star,
                    "previous": str(ev.get("previous", "") or ""),
                    "consensus": str(ev.get("consensus", "") or ""),
                    "actual": str(ev.get("actual", "") or ""),
                    "revised": str(ev.get("revised", "") or ""),
                    "affect_txt": ev.get("affect_txt", ""),
                    "impact": _affect_to_impact(ev.get("affect_txt", "")),
                    "country": "",
                    "category": "economic",
                    "source": "jin10",
                    "source_id": f"{ev.get('title','')}_{event_time}",
                })
                synced += 1
            except Exception as e:
                logger.debug("同步单条财经事件失败: %s", e)

    except Exception as e:
        errors.append(str(e))
        logger.warning("财经日历同步失败: %s", e)
    finally:
        try:
            await svc.close()
        except Exception:
            pass

    return {
        "synced": synced,
        "errors": errors,
        "next_sync": (now + timedelta(days=1)).isoformat(),
    }


def _affect_to_impact(affect_txt: str) -> str:
    """把金十 affect_txt 映射到 impact 字段。"""
    if "利多" in affect_txt:
        return "bullish"
    if "利空" in affect_txt:
        return "bearish"
    return "neutral"


def get_external_event_time(title: str, date_from: str = "", date_to: str = "") -> Optional[dict]:
    """根据日程标题查找外部事件的真实时间。

    Returns: {event_time, source, name, star, impact} 或 None
    """
    # 1. 提取标题中的关键词
    keywords = []
    for canonical, aliases in EVENT_KEYWORDS.items():
        for alias in aliases:
            if alias.lower() in title.lower() or alias in title:
                keywords.append(canonical)
                break

    # 2. 依次匹配
    for kw in keywords:
        ev = find_event_by_keyword(kw, date_from=date_from, date_to=date_to, min_star=1)
        if ev:
            return {
                "event_time": ev.get("event_time"),
                "source": ev.get("source"),
                "name": ev.get("name"),
                "star": ev.get("star"),
                "impact": ev.get("impact"),
                "affect_txt": ev.get("affect_txt"),
            }

    return None
