"""Background task scheduler — memory maintenance, distillation, reminders"""
import json
import re
import asyncio
import logging
from datetime import datetime, timedelta

from . import database as db
from .config import load_config
from .schedule_reminder import check_reminders
from .memory_engine import mem_consolidate
from .llm_client import call_llm
from .unified_distill import distill_daily, distill_weekly, distill_monthly, distill_yearly

# ===== 日程完成 → 经验记忆 =====

_SCHEDULE_MEMORY_PROMPT = """你是一个经验提炼助手。根据以下日程信息，生成一条简洁的经验教训记忆。

输出 JSON 格式（不要额外文字）：
{"content": "一句话经验总结（含发生了什么+学到了什么）", "importance": 1-5, "keywords": "关键词1,关键词2"}

要求：
- 内容简练，10-30字为宜
- importance 根据事件价值评估（已完成任务3, 交易经验4-5, 重大事项4-5）
- keywords 提取2-4个关键词"""


async def extract_schedule_memory(sid: int, schedule: dict):
    """日程标记完成 → 提炼经验记忆（供 routers/schedules.py 调用）"""
    logger = logging.getLogger("zenith.memory")
    title = schedule.get("title", "")
    desc = schedule.get("description", "")
    text_parts = [f"标题: {title}"]
    if desc:
        text_parts.append(f"描述: {desc}")
    text_parts.append(f"地点: {schedule.get('location', '无')}")
    text_parts.append(f"分类: {schedule.get('category', 'other')}")

    source_text = "\n".join(text_parts)
    try:
        messages = [
            {"role": "system", "content": _SCHEDULE_MEMORY_PROMPT},
            {"role": "user", "content": source_text},
        ]
        result = await call_llm(messages, temperature=0.3, max_tokens=500,
                                response_format={"type": "json_object"})
        raw = result.get("content", "")
        m = re.search(r'\{[\s\S]*\}', raw)
        parsed = json.loads(m.group()) if m else json.loads(raw)
        content = parsed.get("content", "").strip()
        if not content:
            logger.debug("日程#%d 完成: LLM 未生成有效记忆", sid)
            return
        importance = int(parsed.get("importance", 3))
        keywords = parsed.get("keywords", "")

        db.mem_add(type_="experience", content=content, importance=importance,
                   keywords=keywords, source_conv_id=f"schedule_{sid}")
        logger.info("日程#%d 完成 → 已提炼经验记忆: %s", sid, content[:50])
    except Exception as e:
        logger.debug("日程#%d 自动提炼记忆失败: %s", sid, e)


# ===== 后台循环 =====

async def _reminder_loop():
    """每5分钟扫描 remind_before 到期提醒"""
    logger = logging.getLogger("zenith.schedule")
    while True:
        try:
            text = check_reminders()
            if text:
                logger.info("日程提醒扫描发现到期项:\n%s", text)
        except Exception as e:
            logger.warning("日程提醒扫描失败: %s", e)
        await asyncio.sleep(5 * 60)


async def _memory_maintenance_loop():
    """每6小时整理记忆：合并相似 + 衰减旧记忆"""
    logger = logging.getLogger("zenith.memory")
    while True:
        await asyncio.sleep(6 * 3600)
        try:
            result = mem_consolidate()
            if result.get("merged") or result.get("decayed"):
                logger.info("记忆整理完成: 合并 %d 条, 衰减 %d 条",
                            result["merged"], result["decayed"])
        except Exception as e:
            logger.warning("记忆整理失败: %s", e)


async def _daily_distill_loop():
    """每天 23:00 自动执行当日蒸馏"""
    logger = logging.getLogger("zenith.distill")
    while True:
        now = datetime.now()
        target = now.replace(hour=23, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        logger.info("每日蒸馏: 等待 %d 秒至 %s", int(wait_seconds), target.isoformat())
        await asyncio.sleep(wait_seconds)
        try:
            date_str = datetime.now().strftime("%Y-%m-%d")
            logger.info("每日蒸馏开始: %s", date_str)
            await distill_daily(date=date_str, save_txt=True)
            logger.info("每日蒸馏完成: %s", date_str)
        except Exception as e:
            logger.warning("每日蒸馏失败: %s", e)


async def _weekly_distill_loop():
    """每周日 23:00 自动执行当周蒸馏"""
    logger = logging.getLogger("zenith.distill")
    while True:
        now = datetime.now()
        days_until_sunday = (6 - now.weekday()) % 7
        if days_until_sunday == 0 and now.hour >= 23:
            days_until_sunday = 7
        target = (now + timedelta(days=days_until_sunday)).replace(hour=23, minute=0, second=0, microsecond=0)
        wait_seconds = (target - now).total_seconds()
        logger.info("每周蒸馏: 等待 %d 秒至 %s", int(wait_seconds), target.isoformat())
        await asyncio.sleep(wait_seconds)
        try:
            # 不传 week_start，让 distill_weekly 自己算本周周一（周日触发时=刚结束这周的周一）。
            # 旧代码传 datetime.now()（周日当天）会把 week_start 误当周一，聚合出「周日~下周六」的错误范围。
            logger.info("每周蒸馏开始")
            await distill_weekly(save_txt=True)
            logger.info("每周蒸馏完成")
        except Exception as e:
            logger.warning("每周蒸馏失败: %s", e)


async def _monthly_distill_loop():
    """每月 1 日 00:30 总结刚结束的月份"""
    logger = logging.getLogger("zenith.distill")
    while True:
        now = datetime.now()
        if now.month == 12:
            nxt = datetime(now.year + 1, 1, 1, 0, 30, 0)
        else:
            nxt = datetime(now.year, now.month + 1, 1, 0, 30, 0)
        wait_seconds = (nxt - now).total_seconds()
        logger.info("月度总结: 等待 %d 秒至 %s", int(wait_seconds), nxt.isoformat())
        await asyncio.sleep(wait_seconds)
        last_month = (nxt - timedelta(days=1)).strftime("%Y-%m")
        try:
            await distill_monthly(month=last_month, save_txt=True)
            logger.info("月度总结完成: %s", last_month)
        except Exception as e:
            logger.warning("月度总结失败: %s", e)


async def _yearly_distill_loop():
    """每年 1 月 1 日 00:45 总结刚结束的年份"""
    logger = logging.getLogger("zenith.distill")
    while True:
        now = datetime.now()
        nxt = datetime(now.year + 1, 1, 1, 0, 45, 0)
        wait_seconds = (nxt - now).total_seconds()
        logger.info("年度总结: 等待 %d 秒至 %s", int(wait_seconds), nxt.isoformat())
        await asyncio.sleep(wait_seconds)
        last_year = str(nxt.year - 1)
        try:
            await distill_yearly(year=last_year, save_txt=True)
            logger.info("年度总结完成: %s", last_year)
        except Exception as e:
            logger.warning("年度总结失败: %s", e)


async def _calendar_sync_loop():
    """每日自动同步外部财经日历（金十）到 schedule_events 缓存。

    配置节 config.yaml:
      calendar_sync: {enabled, hour, minute, days, min_star, run_on_start}
    run_on_start=true 时启动立即同步一次；异常仅 log 不退出循环。
    """
    logger = logging.getLogger("zenith.calendar_sync")
    from . import calendar_sync as cs

    cfg = load_config().get("calendar_sync", {})
    hour = int(cfg.get("hour", 8))
    minute = int(cfg.get("minute", 0))
    days = int(cfg.get("days", 7))
    min_star = int(cfg.get("min_star", 2))
    run_on_start = bool(cfg.get("run_on_start", True))

    if run_on_start:
        try:
            logger.info("财经日历启动即同步: days=%d min_star=%d", days, min_star)
            result = await cs.sync_calendar_events(days=days, min_star=min_star)
            logger.info("财经日历启动同步完成: %s", result)
        except Exception as e:
            logger.warning("财经日历启动同步失败: %s", e)

    while True:
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        logger.info("财经日历同步: 等待 %d 秒至 %s", int(wait_seconds), target.isoformat())
        await asyncio.sleep(wait_seconds)
        try:
            logger.info("财经日历同步开始: days=%d min_star=%d", days, min_star)
            result = await cs.sync_calendar_events(days=days, min_star=min_star)
            logger.info("财经日历同步完成: %s", result)
        except Exception as e:
            logger.warning("财经日历同步失败: %s", e)


_background_tasks: list = []


def start_all_background_tasks():
    """启动所有后台定时任务。在 lifespan 中调用。"""
    cfg = load_config()

    _background_tasks.append(asyncio.create_task(_memory_maintenance_loop()))
    _background_tasks.append(asyncio.create_task(_reminder_loop()))

    if cfg.get("auto_distill_enabled", True):
        _background_tasks.append(asyncio.create_task(_daily_distill_loop()))
        _background_tasks.append(asyncio.create_task(_weekly_distill_loop()))
        _background_tasks.append(asyncio.create_task(_monthly_distill_loop()))
        _background_tasks.append(asyncio.create_task(_yearly_distill_loop()))
    else:
        logging.getLogger("zenith").info("auto_distill_enabled=false, 跳过蒸馏定时任务")

    cs_cfg = cfg.get("calendar_sync", {}) or {}
    if cs_cfg.get("enabled", False):
        _background_tasks.append(asyncio.create_task(_calendar_sync_loop()))
        logging.getLogger("zenith").info("calendar_sync.enabled=true, 启动财经日历自动同步")
    else:
        logging.getLogger("zenith").info("calendar_sync.enabled=false, 跳过财经日历自动同步")


async def stop_all_background_tasks():
    """优雅停止所有后台定时任务。在 lifespan shutdown 调用。"""
    for t in _background_tasks:
        if not t.done():
            t.cancel()
    for t in _background_tasks:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
    _background_tasks.clear()
