"""Zenith v2 日程提醒 — 按距开始时间分层提醒"""
from __future__ import annotations

from datetime import datetime, timedelta
from .database import sch_list


def check_reminders() -> str:
    """
    扫描所有 confirmed 日程，按距开始时间分层生成提醒。
    每次对话开始时调用。
    """
    items = sch_list(status="confirmed")
    if not items:
        return ""

    now = datetime.now()
    reminders = {"urgent": [], "today": [], "week": [], "month": []}

    for s in items:
        st = s.get("start_time", "")
        if not st:
            continue
        try:
            start = _parse_time(st)
            if start is None:
                continue
        except Exception:
            continue

        if start < now:
            continue

        delta = start - now

        if delta <= timedelta(hours=1):
            reminders["urgent"].append(f"🟡 {s['title']} @ {st}")
        elif delta <= timedelta(days=1):
            reminders["today"].append(f"🔵 {s['title']} @ {st}")
        elif delta <= timedelta(days=7):
            reminders["week"].append(f"🟢 {s['title']} @ {st}")
        elif delta <= timedelta(days=30):
            reminders["month"].append(f"⚪ {s['title']} @ {st}")

    parts = []
    if reminders["urgent"]:
        parts.append("🟡 **1小时内：**\n" + "\n".join(reminders["urgent"]))
    if reminders["today"]:
        parts.append("🔵 **今天：**\n" + "\n".join(reminders["today"]))
    if reminders["week"]:
        parts.append("🟢 **本周：**\n" + "\n".join(reminders["week"]))
    if reminders["month"]:
        parts.append("⚪ **本月：**\n" + "\n".join(reminders["month"]))

    return "\n\n".join(parts) if parts else ""


def _parse_time(time_str: str) -> datetime:
    """尝试多种格式解析时间"""
    formats = [
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d",
        "%m月%d日 %H:%M",
        "%Y/%m/%d %H:%M",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(time_str.strip(), fmt)
        except ValueError:
            continue
    return None
