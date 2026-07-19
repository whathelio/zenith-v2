"""Zenith v2 时区工具模块

统一使用 Asia/Shanghai（CST）作为默认时区，所有日程、提醒、蒸馏相关
时间操作都应通过本模块获取当前时间，避免 datetime.now() 在不同环境下产生偏差。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

DEFAULT_TIMEZONE = timezone(timedelta(hours=8))  # Asia/Shanghai CST


def now_tz(tz: Optional[timezone] = None) -> datetime:
    """获取当前时区时间（默认 CST）"""
    return datetime.now(tz or DEFAULT_TIMEZONE)


CN_NUMBERS = {
    "零": "0", "一": "1", "二": "2", "三": "3", "四": "4",
    "五": "5", "六": "6", "七": "7", "八": "8", "九": "9",
    "十": "10", "两": "2", "廿": "20", "卅": "30",
}


def _cn_to_arabic(text: str) -> str:
    """将常见中文数字替换为阿拉伯数字，便于正则提取"""
    result = []
    for ch in text:
        result.append(CN_NUMBERS.get(ch, ch))
    return "".join(result)


def parse_time_to_iso(time_str: str, reference: Optional[datetime] = None) -> str:
    """解析自然语言或格式时间字符串，返回 ISO 格式时间字符串。

    支持格式：
    - YYYY-MM-DD HH:MM
    - YYYY-MM-DDTHH:MM
    - YYYY-MM-DD
    - MM-DD HH:MM
    - HH:MM
    - 相对表达：明天、后天、今天、下午3点、3点、3:00

    无法解析时返回空字符串。
    """
    if not time_str or not isinstance(time_str, str):
        return ""

    time_str = time_str.strip()
    ref = reference or now_tz()

    # 1. 已经是 ISO 格式
    if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", time_str):
        return time_str[:16]

    # 2. 标准格式：YYYY-MM-DD HH:MM 或 YYYY-MM-DD
    m = re.match(r"^(\d{4}-\d{2}-\d{2})(?:\s+T?\s*(\d{1,2}):(\d{2}))?$", time_str)
    if m:
        date_part = m.group(1)
        hour = int(m.group(2)) if m.group(2) else 0
        minute = int(m.group(3)) if m.group(3) else 0
        try:
            dt = datetime.strptime(date_part, "%Y-%m-%d").replace(
                hour=hour, minute=minute, tzinfo=DEFAULT_TIMEZONE
            )
            return dt.isoformat()
        except ValueError:
            return ""

    # 3. MM-DD HH:MM / MM-DD
    m = re.match(r"^(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?$", time_str)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        hour = int(m.group(3)) if m.group(3) else 0
        minute = int(m.group(4)) if m.group(4) else 0
        try:
            dt = ref.replace(month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
            return dt.isoformat()
        except ValueError:
            return ""

    # 4. 仅时间 HH:MM
    m = re.match(r"^(\d{1,2}):(\d{2})$", time_str)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        dt = ref.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return dt.isoformat()

    # 5. 自然语言相对表达（轻量规则）
    s = _cn_to_arabic(time_str).lower()

    # 日期偏移
    date_offset = 0
    if "后天" in s:
        date_offset = 2
    elif "明天" in s or "明日" in s:
        date_offset = 1
    elif "今天" in s or "今" in s:
        date_offset = 0
    elif "昨天" in s or "昨日" in s:
        date_offset = -1

    # 提取小时和分钟
    hour, minute = 0, 0
    # 匹配 "15:30" / "3:30" / "下午3点" / "3点" / "15点"
    hm_match = re.search(r"(\d{1,2}):(\d{2})", s)
    if hm_match:
        hour = int(hm_match.group(1))
        minute = int(hm_match.group(2))
    else:
        h_match = re.search(r"(\d{1,2})\s*点", s)
        if h_match:
            hour = int(h_match.group(1))
            minute = 0

    # 上午/下午判断
    if "下午" in s and hour < 12 and hour > 0:
        hour += 12
    elif "晚上" in s and hour < 12 and hour >= 6:
        hour += 12
    elif "上午" in s and hour == 12:
        hour = 0

    # 如果没有任何时间信息，且没有日期偏移，说明没有提取到时间
    if hour == 0 and minute == 0 and date_offset == 0 and not re.search(r"\d", s):
        return ""

    base = ref.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if date_offset:
        base = base + timedelta(days=date_offset)
    return base.isoformat()


def iso_to_display(iso_str: str, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """将 ISO 时间字符串格式化为展示字符串"""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime(fmt)
    except (ValueError, TypeError):
        return iso_str
