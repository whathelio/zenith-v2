"""Zenith v2 日程提醒 — 按 remind_before 与逾期状态提醒"""
from __future__ import annotations

from datetime import datetime, timedelta
from .database import sch_list, db
from .timezone import now_tz, DEFAULT_TIMEZONE


REMINDER_PRESETS = {
    0: "不提醒",
    15: "15分钟前",
    30: "30分钟前",
    60: "1小时前",
    120: "2小时前",
    1440: "1天前",
}


def _parse_time(time_str: str) -> datetime | None:
    """尝试多种格式解析时间，统一使用 CST 时区"""
    if not time_str:
        return None
    formats = [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S+%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%m月%d日 %H:%M",
        "%Y/%m/%d %H:%M",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(time_str.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=DEFAULT_TIMEZONE)
            return dt
        except ValueError:
            continue

    # 尝试 ISO 解析（如 2026-07-18T15:00:00+08:00）
    try:
        dt = datetime.fromisoformat(time_str.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=DEFAULT_TIMEZONE)
        return dt
    except ValueError:
        return None


def _already_reminded(sid: int) -> bool:
    """检查当天是否已经提醒过该日程"""
    today = now_tz().strftime("%Y-%m-%d")
    with db() as c:
        r = c.execute(
            "SELECT 1 FROM schedule_reminders WHERE schedule_id = ? AND DATE(reminded_at) = ?",
            (sid, today)
        ).fetchone()
    return r is not None


def _record_reminder(sid: int):
    """记录本次提醒"""
    with db() as c:
        c.execute(
            "INSERT INTO schedule_reminders (schedule_id, reminded_at) VALUES (?, ?)",
            (sid, now_tz().isoformat())
        )


def get_due_reminders() -> list[dict]:
    """获取所有到期的提醒（包括 remind_before 到期和已逾期）"""
    items = sch_list(status="confirmed")
    now = now_tz()
    due = []
    overdue = []

    for s in items:
        st = s.get("start_time", "")
        if not st:
            continue
        start = _parse_time(st)
        if start is None:
            continue

        remind_before = int(s.get("remind_before") or 0)

        # 已逾期
        if start < now:
            if s.get("status") not in ("done", "cancelled"):
                overdue.append(s)
            continue

        # remind_before 提醒
        if remind_before > 0:
            trigger_at = start - timedelta(minutes=remind_before)
            if now >= trigger_at and not _already_reminded(s["id"]):
                due.append(s)

    return {"due": due, "overdue": overdue}


def check_reminders() -> str:
    """
    扫描所有 confirmed 日程，生成按 remind_before 触发的提醒文本。
    每次对话开始时调用。
    """
    result = get_due_reminders()
    due = result["due"]
    overdue = result["overdue"]

    parts = []
    if overdue:
        parts.append("🔴 **已逾期：**\n" + "\n".join(
            f"  ⚠ {s['title']} @ {s.get('start_time', '待定')}"
            for s in overdue
        ))

    if due:
        # 记录提醒，避免重复
        for s in due:
            _record_reminder(s["id"])
        parts.append("🟡 **即将开始：**\n" + "\n".join(
            f"  ⏰ {s['title']} @ {s.get('start_time', '待定')}"
            for s in due
        ))

    return "\n\n".join(parts) if parts else ""


def get_upcoming_schedules(limit: int = 5) -> list[dict]:
    """获取最近的未逾期/未完成的日程（用于前端展示）"""
    items = sch_list(status="confirmed")
    now = now_tz()
    upcoming = []
    for s in items:
        st = s.get("start_time", "")
        start = _parse_time(st) if st else None
        if start is None or start >= now:
            upcoming.append(s)

    upcoming.sort(key=lambda x: x.get("start_time") or "")
    return upcoming[:limit]
