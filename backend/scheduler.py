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
from .unified_distill import distill_daily, distill_weekly

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
            result = await distill_daily(date=date_str, save_txt=True)
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
            week_start = datetime.now().strftime("%Y-%m-%d")
            logger.info("每周蒸馏开始: %s", week_start)
            result = await distill_weekly(week_start=week_start, save_txt=True)
            logger.info("每周蒸馏完成: %s", week_start)
        except Exception as e:
            logger.warning("每周蒸馏失败: %s", e)


def start_all_background_tasks():
    """启动所有后台定时任务。在 lifespan 中调用。"""
    cfg = load_config()

    asyncio.create_task(_memory_maintenance_loop())
    asyncio.create_task(_reminder_loop())

    if cfg.get("auto_distill_enabled", True):
        asyncio.create_task(_daily_distill_loop())
        asyncio.create_task(_weekly_distill_loop())
    else:
        logging.getLogger("zenith").info("auto_distill_enabled=false, 跳过蒸馏定时任务")
