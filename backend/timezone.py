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
    """将常见中文数字替换为阿拉伯数字，便于正则提取

    ⚠️ 这是**逐字替换**，只适用于「时/分已经用阿拉伯数字」或"非时间的数字"场景。
    解析「X点」时**不能**用它 —— "十一点" 会被替换成 "101点"，再被 `\\d{1,2}点`
    在位置 1 匹配成 "01" → 01:00（2026-10-17 审计 A4-R4）。时间解析请走
    `_cn_hour_to_int()` 的整体解析。
    """
    result = []
    for ch in text:
        result.append(CN_NUMBERS.get(ch, ch))
    return "".join(result)


_CN_HOUR_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
    "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}


def _cn_hour_to_int(token: str) -> Optional[int]:
    """把「11 / 十一 / 二十三 / 廿三 / 两」整体解析成整数；无法解析返回 None。

    2026-10-17 审计 A4-R4：中文数字必须整体解析。「十一点」= 11，而不是
    逐字替换后的 "101" 被截成 "01"。
    """
    s = (token or "").strip()
    if not s:
        return None
    if re.fullmatch(r"\d{1,3}", s):
        return int(s)
    if s in _CN_HOUR_DIGITS:
        return _CN_HOUR_DIGITS[s]
    m = re.fullmatch(r"廿([一二三四五六七八九])", s)
    if m:
        return 20 + _CN_HOUR_DIGITS[m.group(1)]
    if s == "卅":
        return 30
    m = re.fullmatch(r"([一二三四五六七八九两])?十([一二三四五六七八九])?", s)
    if m:
        tens = _CN_HOUR_DIGITS[m.group(1)] if m.group(1) else 1
        ones = _CN_HOUR_DIGITS[m.group(2)] if m.group(2) else 0
        return tens * 10 + ones
    return None


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
        # 2026-10-17 审计 A4-R5：越界值必须在这里拦住，否则 replace() 抛 ValueError
        # 会一路冒到工具层（"hour must be in 0..23" 直接被回给模型），
        # 而且这条分支在"规则优先"路径上，会绕过 LLM 兜底。
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return ""
        try:
            dt = ref.replace(hour=hour, minute=minute, second=0, microsecond=0)
        except ValueError:
            return ""
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
    found_time = False
    # "15:30" / "3:30"（冒号形式；s 已逐字转换，这里的数字一定是阿拉伯数字）
    hm_match = re.search(r"(\d{1,2}):(\d{2})", s)
    if hm_match:
        hour = int(hm_match.group(1))
        minute = int(hm_match.group(2))
        found_time = True
    else:
        # 「X点[半|一刻|三刻|N分]」：X 可能是中文数字 → 必须对**原文**整体解析。
        # 2026-10-17 审计 A4-R4：走 s（已被逐字替换）会把"十一点"读成 01:00。
        point_match = re.search(
            r"(?P<h>[0-9〇零一二三四五六七八九十两廿卅]{1,3})\s*点\s*"
            r"(?P<sfx>半|一刻|三刻|(?P<m>[0-9]{1,2})\s*分)?",
            time_str,
        )
        if point_match:
            parsed_hour = _cn_hour_to_int(point_match.group("h"))
            if parsed_hour is not None:
                hour = parsed_hour
                sfx = point_match.group("sfx") or ""
                if sfx == "半":
                    minute = 30
                elif sfx == "一刻":
                    minute = 15
                elif sfx == "三刻":
                    minute = 45
                elif point_match.group("m"):
                    minute = int(point_match.group("m"))
                found_time = True

    # 上午/下午判断
    if "下午" in s and hour < 12 and hour > 0:
        hour += 12
    elif "晚上" in s and hour < 12 and hour >= 6:
        hour += 12
    elif "上午" in s and hour == 12:
        hour = 0

    # 如果没有任何时间信息，且没有日期偏移，说明没有提取到时间
    if not found_time and date_offset == 0 and not re.search(r"\d", s):
        return ""

    # 2026-10-17 审计 A4-R5：上午/下午调整后仍可能越界（"下午23点"+12、"99点"），
    # 必须在构造 datetime 前拦掉，否则 ValueError 会冒到工具层。
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return ""

    try:
        base = ref.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except ValueError:
        return ""
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
