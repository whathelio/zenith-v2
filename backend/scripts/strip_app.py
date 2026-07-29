"""Strip background tasks from app.py — move to scheduler.py"""
from pathlib import Path

app_path = Path(__file__).parent.parent / "app.py"
content = app_path.read_text(encoding="utf-8")

# 1. Add scheduler import
content = content.replace(
    "from . import knowledge_service",
    "from . import knowledge_service\nfrom . import scheduler"
)

# 2. Replace lifespan body
old_life = """    # 启动市场分析定时任务
    cfg = load_config()
    if cfg.get('market_analysis_enabled', True):
        from .market_analyzer import start_market_scheduler
        scheduler_task = asyncio.create_task(start_market_scheduler())

    # 启动记忆整理定时任务（每6小时执行一次）
    asyncio.create_task(_memory_maintenance_loop())

    # 启动每日/每周蒸馏定时任务（受 auto_distill_enabled 控制）
    if is_auto_distill_enabled():
        asyncio.create_task(_daily_distill_loop())
        asyncio.create_task(_weekly_distill_loop())
    else:
        logger.info("auto_distill_enabled=false, 跳过每日/每周蒸馏定时任务")
    # 启动日程提醒后台扫描任务（每5分钟检查 remind_before 到期）
    asyncio.create_task(_reminder_loop())"""

new_life = """    # 启动所有后台定时任务
    scheduler.start_all_background_tasks()"""

content = content.replace(old_life, new_life)

# 3. Remove background functions
for func_name in [
    "async def _reminder_loop",
    "async def _memory_maintenance_loop",
    "async def _auto_distill_conv",
    "async def _daily_distill_loop",
    "async def _weekly_distill_loop",
]:
    start = content.find(func_name)
    if start > 0:
        end_async = content.find("\nasync def ", start + len(func_name))
        end_def = content.find("\ndef ", start + len(func_name))
        end = min(end_async if end_async > 0 else 99999, end_def if end_def > 0 else 99999)
        content = content[:start] + content[end:]

# 4. Remove _SCHEDULE_MEMORY_PROMPT + _auto_extract_schedule_memory + _pending
start = content.find("_SCHEDULE_MEMORY_PROMPT")
if start > 0:
    end = content.find("async def _daily_distill_loop", start)
    if end < 0:
        end = content.find("async def", start + 500)
    content = content[:start] + content[end:]

content = content.replace("_pending_schedule_tasks: set = set()\n\n", "")

# 5. Clean unused imports
for imp in [
    "from .timezone import now_tz\n",
    "from .recurrence import expand_recurring\n",
    "from .schedule_reminder import check_reminders, get_due_reminders, get_upcoming_schedules, REMINDER_PRESETS\n",
    "from .unified_distill import distill_conversation, distill_schedules, distill_memories, distill_all, distill_daily, distill_weekly\n",
    "from .memory_engine import maybe_extract_memories, build_memory_injection, reset_counter, mem_consolidate, extract_memories_from_text\n",
]:
    content = content.replace(imp, "")

# Fix config import
content = content.replace(
    "from .config import load_config, save_config, ensure_dirs, DEFAULT_CONFIG, is_code_execution_enabled, is_auto_distill_enabled\n",
    "from .config import load_config, save_config, ensure_dirs, DEFAULT_CONFIG, is_code_execution_enabled\n"
)

# Clean whitespace
while "\n\n\n" in content:
    content = content.replace("\n\n\n", "\n\n")

app_path.write_text(content, encoding="utf-8")
print("app.py stripped of background tasks")
