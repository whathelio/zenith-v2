"""Zenith v2 重复日程展开工具 — 轻量规则"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional
from .timezone import DEFAULT_TIMEZONE


SUPPORTED_RULES = {"daily", "weekly", "weekdays", "monthly"}


def _parse_iso(time_str: str) -> datetime:
    """解析 ISO 时间，失败时返回 epoch"""
    if not time_str:
        return datetime(1970, 1, 1, tzinfo=DEFAULT_TIMEZONE)
    try:
        dt = datetime.fromisoformat(time_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=DEFAULT_TIMEZONE)
        return dt
    except ValueError:
        try:
            dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M")
            return dt.replace(tzinfo=DEFAULT_TIMEZONE)
        except ValueError:
            return datetime(1970, 1, 1, tzinfo=DEFAULT_TIMEZONE)


def parse_recurrence(rule: str) -> dict:
    """解析简化规则字符串
    格式: daily|weekly|weekdays|monthly[,count=N|until=YYYY-MM-DD]
    返回: {"type": "daily", "count": int|None, "until": datetime|None}
    """
    if not rule:
        return {"type": "", "count": None, "until": None}
    parts = [p.strip() for p in rule.split(",")]
    rtype = parts[0].lower()
    count = None
    until = None
    for p in parts[1:]:
        if p.lower().startswith("count="):
            try:
                count = int(p.split("=", 1)[1])
            except ValueError:
                pass
        elif p.lower().startswith("until="):
            try:
                until = datetime.strptime(p.split("=", 1)[1], "%Y-%m-%d").replace(tzinfo=DEFAULT_TIMEZONE)
            except ValueError:
                pass
    return {"type": rtype if rtype in SUPPORTED_RULES else "", "count": count, "until": until}


def expand_recurring(schedule: dict, date_from: str, date_to: str) -> list[dict]:
    """在指定日期范围内展开重复日程实例"""
    rule = schedule.get("recurrence", "")
    parsed = parse_recurrence(rule)
    if not parsed["type"]:
        return [schedule]

    start = _parse_iso(schedule.get("start_time", ""))
    end = _parse_iso(schedule.get("end_time", ""))
    duration = end - start if end > start else timedelta()

    range_start = _parse_iso(date_from) if date_from else start
    range_end = _parse_iso(date_to) if date_to else range_start + timedelta(days=30)

    results = []
    current = start
    count = 0

    while True:
        # 检查结束条件
        if parsed["count"] and count >= parsed["count"]:
            break
        if parsed["until"] and current > parsed["until"]:
            break
        if current > range_end + timedelta(days=1):
            break

        # 只保留在查询范围内的实例
        if range_start.date() <= current.date() <= range_end.date():
            instance = dict(schedule)
            instance["start_time"] = current.isoformat()
            instance["end_time"] = (current + duration).isoformat() if duration else ""
            instance["is_recurring_instance"] = True
            instance["instance_date"] = current.strftime("%Y-%m-%d")
            results.append(instance)

        # 计算下一个日期
        next_date = _next_occurrence(current, parsed["type"])
        if next_date <= current:
            break  # 防止死循环
        current = next_date
        count += 1

    return results


def _next_occurrence(current: datetime, rtype: str) -> datetime:
    """计算下一个发生日期"""
    if rtype == "daily":
        return current + timedelta(days=1)
    if rtype == "weekly":
        return current + timedelta(weeks=1)
    if rtype == "weekdays":
        # 跳到下一个工作日（周一到周五）
        next_day = current + timedelta(days=1)
        while next_day.weekday() >= 5:  # 5=Sat, 6=Sun
            next_day += timedelta(days=1)
        return next_day
    if rtype == "monthly":
        # 简单月度：直接加一个月，尽量保持同一天
        year = current.year
        month = current.month + 1
        if month > 12:
            month = 1
            year += 1
        day = min(current.day, _days_in_month(year, month))
        return current.replace(year=year, month=month, day=day)
    return current


def _days_in_month(year: int, month: int) -> int:
    if month == 2:
        return 29 if (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0) else 28
    if month in {1, 3, 5, 7, 8, 10, 12}:
        return 31
    return 30


def build_recurrence_rule(rtype: str, count: Optional[int] = None, until: Optional[str] = None) -> str:
    """根据参数构建规则字符串"""
    if rtype not in SUPPORTED_RULES:
        return ""
    parts = [rtype]
    if count:
        parts.append(f"count={count}")
    if until:
        parts.append(f"until={until}")
    return ",".join(parts)
