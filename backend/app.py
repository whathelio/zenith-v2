"""Zenith v2 — FastAPI 主应用
整合所有模块：对话、记忆、日程、笔记、代码执行、确认流程
架构参考：Shinsekai (Python Bridge + React Frontend)
"""
from __future__ import annotations

import json
import re
import asyncio
import sys
import webbrowser
import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Body
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from . import database as db
from .database import conv_update_summary
from .config import load_config, save_config, ensure_dirs, DEFAULT_CONFIG, is_code_execution_enabled, is_auto_distill_enabled
from .tools import TOOLS_SCHEMA, execute_tool, detect_consolidate_intent, generate_consolidate_plan, apply_consolidate_plan, _format_consolidate_plan
from .llm_client import chat_stream, plan_time, call_llm
from .memory_engine import maybe_extract_memories, build_memory_injection, reset_counter, mem_consolidate
from .confirm_flow import get_pending_proposals, confirm_proposal, reject_proposal, modify_proposal
from .confirm_flow import TutorialFlow, list_active_tutorials
from .context_compressor import maybe_compress
from .schedule_reminder import check_reminders, get_due_reminders, get_upcoming_schedules, REMINDER_PRESETS
from .timezone import now_tz
from .recurrence import expand_recurring
from .file_analyzer import analyze_file_stream
from .unified_distill import distill_conversation, distill_schedules, distill_memories, distill_all, distill_daily, distill_weekly
from . import knowledge_service

PROJECT_DIR = Path(__file__).parent.parent
FRONTEND_DIST = PROJECT_DIR / "frontend" / "dist"
FRONTEND_PUBLIC = PROJECT_DIR / "frontend" / "public"
STANDALONE_HTML = PROJECT_DIR / "frontend" / "index-standalone.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    ensure_dirs()
    if not (PROJECT_DIR / "config" / "config.yaml").exists():
        save_config(DEFAULT_CONFIG)

    # 启动市场分析定时任务
    cfg = load_config()
    if cfg.get('market_analysis_enabled', True):
        from .market_analyzer import start_market_scheduler
        scheduler_task = asyncio.create_task(start_market_scheduler())
        # yield 时保持任务运行

    # 启动记忆整理定时任务（每6小时执行一次）
    asyncio.create_task(_memory_maintenance_loop())

    # 启动每日/每周蒸馏定时任务（受 auto_distill_enabled 控制）
    if is_auto_distill_enabled():
        asyncio.create_task(_daily_distill_loop())
        asyncio.create_task(_weekly_distill_loop())
    else:
        logger.info("auto_distill_enabled=false, 跳过每日/每周蒸馏定时任务")
    # 启动日程提醒后台扫描任务（每5分钟检查 remind_before 到期）
    asyncio.create_task(_reminder_loop())

    yield


async def _reminder_loop():
    """后台每5分钟扫描 remind_before 到期提醒，并记录已提醒状态"""
    logger = logging.getLogger("zenith.schedule")
    while True:
        try:
            result = get_due_reminders()
            due = result.get("due", [])
            overdue = result.get("overdue", [])
            if due or overdue:
                logger.info(
                    "日程提醒扫描: %d 个即将到期, %d 个已逾期",
                    len(due), len(overdue)
                )
        except Exception as e:
            logger.warning("日程提醒扫描失败: %s", e)
        await asyncio.sleep(5 * 60)


async def _memory_maintenance_loop():
    """每6小时自动整理记忆：合并相似 + 衰减旧记忆"""
    import asyncio
    while True:
        await asyncio.sleep(6 * 3600)
        try:
            result = mem_consolidate()
            if result.get("merged") or result.get("decayed"):
                import logging
                logging.getLogger("zenith.memory").info(
                    "记忆整理完成: 合并 %d 条, 衰减 %d 条",
                    result["merged"], result["decayed"]
                )
        except Exception as e:
            import logging
            logging.getLogger("zenith.memory").warning("记忆整理失败: %s", e)


async def _auto_distill_conv(conv_id: str):
    """后台自动蒸馏对话内容：提取经验/决策/知识 → 存入记忆库"""
    import logging
    logger = logging.getLogger("zenith.distill")
    try:
        result = await distill_conversation(conv_id)
        saved = result.get("saved_count", 0)
        if saved > 0:
            logger.info("自动蒸馏完成: 对话%s, 已保存%d条记忆", conv_id, saved)
        else:
            logger.debug("自动蒸馏完成: 对话%s, 无新记忆提取", conv_id)
    except Exception as e:
        logging.getLogger("zenith.distill").warning("自动蒸馏失败: %s", e)


# ===== 完成日程 → 自动提炼经验记忆 =====
_pending_schedule_tasks: set = set()

_SCHEDULE_MEMORY_PROMPT = """你是一个经验提炼助手。根据以下日程信息，生成一条简洁的经验教训记忆。

输出 JSON 格式（不要额外文字）：
{"content": "一句话经验总结（含发生了什么+学到了什么）", "importance": 1-5, "keywords": "关键词1,关键词2"}

要求：
- 内容简练，10-30字为宜
- importance 根据事件价值评估（已完成任务3, 交易经验4-5, 重大事项4-5）
- keywords 提取2-4个关键词"""


async def _auto_extract_schedule_memory(sid: int, schedule: dict):
    """后台任务：日程标记完成 → 提炼经验记忆"""
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
        logger.info("日程#%d「%s」完成 → 已提炼经验记忆: %s", sid, title, content[:50])
    except Exception as e:
        logger.debug("日程#%d 自动提炼记忆失败: %s", sid, e)


async def _daily_distill_loop():
    """每天 23:00 自动执行当日内容蒸馏"""
    import logging
    from datetime import timedelta
    logger = logging.getLogger("zenith.distill")
    while True:
        now = datetime.now()
        # 计算到下一个 23:00 的等待时间
        target = now.replace(hour=23, minute=0, second=0, microsecond=0)
        if now >= target:
            # 已过今天23点，等到明天23点
            target = target + timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        logger.info("每日蒸馏: 等待 %d 秒后执行（目标 %s）", int(wait_seconds), target.isoformat())
        await asyncio.sleep(wait_seconds)
        try:
            date_str = datetime.now().strftime("%Y-%m-%d")
            logger.info("每日蒸馏开始: %s", date_str)
            result = await distill_daily(date=date_str, save_txt=True)
            logger.info("每日蒸馏完成: %s, 对话%d 日程%d 笔记%d 记忆%d",
                        date_str,
                        result.get("conv_count", 0),
                        result.get("schedule_count", 0),
                        result.get("note_count", 0),
                        result.get("memory_count", 0))
        except Exception as e:
            logger.warning("每日蒸馏失败: %s", e)


async def _weekly_distill_loop():
    """每周日 23:00 自动执行当周内容蒸馏"""
    import logging
    from datetime import timedelta
    logger = logging.getLogger("zenith.distill")
    while True:
        now = datetime.now()
        # 计算到下一个周日 23:00 的等待时间
        days_until_sunday = (6 - now.weekday()) % 7  # 0=Monday, 6=Sunday
        if days_until_sunday == 0 and now.hour < 23:
            # 今天是周日但还没到23点
            target = now.replace(hour=23, minute=0, second=0, microsecond=0)
        else:
            if days_until_sunday == 0:
                days_until_sunday = 7  # 已过周日23点，等到下周日
            target = (now + timedelta(days=days_until_sunday)).replace(
                hour=23, minute=0, second=0, microsecond=0)
        wait_seconds = (target - now).total_seconds()
        logger.info("每周蒸馏: 等待 %d 秒后执行（目标 %s）", int(wait_seconds), target.isoformat())
        await asyncio.sleep(wait_seconds)
        try:
            # 计算本周周一日期
            today = datetime.now()
            monday = today - __import__('datetime').timedelta(days=today.weekday())
            week_start = monday.strftime("%Y-%m-%d")
            logger.info("每周蒸馏开始: %s", week_start)
            result = await distill_weekly(week_start=week_start, save_txt=True)
            logger.info("每周蒸馏完成: %s, 对话%d 日程%d 笔记%d 记忆%d",
                        week_start,
                        result.get("conv_count", 0),
                        result.get("schedule_count", 0),
                        result.get("note_count", 0),
                        result.get("memory_count", 0))
        except Exception as e:
            logger.warning("每周蒸馏失败: %s", e)


app = FastAPI(
    title="Zenith v2 — Local AI Assistant",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")
if FRONTEND_PUBLIC.exists():
    app.mount("/public", StaticFiles(directory=str(FRONTEND_PUBLIC)), name="public")


# ═══════════════════════════════════════════════════════
# Frontend SPA
# ═══════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def index():
    """Frontend SPA 入口"""
    # 优先使用 React 构建产物
    index_html = FRONTEND_DIST / "index.html"
    if index_html.exists():
        return HTMLResponse(
            content=index_html.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    # 回退到独立 HTML（无需 npm build）
    if STANDALONE_HTML.exists():
        return HTMLResponse(
            content=STANDALONE_HTML.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    # 最低回退
    return """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>Zenith v2</title>
<style>body{font-family:-apple-system,'Microsoft YaHei',sans-serif;background:#282c34;color:#ddd;display:flex;justify-content:center;align-items:center;height:100vh;margin:0}
.card{text-align:center;padding:40px}h1{color:#bd93f9;font-size:48px;margin:0}p{color:#717e95}
code{background:#1b1d23;padding:4px 12px;border-radius:4px;color:#ff79c6}a{color:#bd93f9}</style></head><body>
<div class="card"><h1>Zenith v2</h1><p>Local AI Assistant — Backend Running</p>
<p>API: <code>http://localhost:8766</code></p><a href="/api/health">Health Check</a></div></body></html>"""


# ═══════════════════════════════════════════════════════
# API: Health & Settings
# ═══════════════════════════════════════════════════════

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}


# ── 知识库薄代理（转发到外部 api_gateway） ──────────────────────
@app.get("/api/knowledge/health")
async def knowledge_health():
    try:
        return await knowledge_service.health()
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e), "code": "GATEWAY_DOWN"})


@app.post("/api/knowledge/search")
async def knowledge_search(data: dict = Body(default=None)):
    q = (data or {}).get("question", "").strip()
    if not q:
        raise HTTPException(400, "question is required")
    top_k = int((data or {}).get("top_k", 5))
    return await knowledge_service.search(q, top_k)


@app.post("/api/knowledge/wiki")
async def knowledge_wiki(data: dict = Body(default=None)):
    q = (data or {}).get("question", "").strip()
    if not q:
        raise HTTPException(400, "question is required")
    return await knowledge_service.wiki_query(q)


@app.post("/api/knowledge/tasks")
async def knowledge_create_task(data: dict = Body(default=None)):
    t = (data or {}).get("type")
    payload = (data or {}).get("payload", {})
    if t not in ("search", "wiki", "agent"):
        raise HTTPException(400, "type must be search|wiki|agent")
    return await knowledge_service.create_task(t, payload)


@app.get("/api/knowledge/tasks/{task_id}")
async def knowledge_get_task(task_id: str):
    return await knowledge_service.get_task(task_id)


@app.get("/api/knowledge/tasks")
async def knowledge_list_tasks(status: str | None = None, limit: int = 20):
    return await knowledge_service.list_tasks(status, limit)


@app.post("/api/knowledge/ingest")
async def knowledge_ingest(file: UploadFile = File(...)):
    """上传 PDF → 审查 → 入库（转发到 api_gateway）"""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "仅支持 PDF")
    try:
        content = await file.read()
        result = await knowledge_service.ingest_pdf(file.filename, content)
    except Exception as e:
        logger.exception("knowledge ingest failed")
        return JSONResponse(status_code=502, content={"error": str(e), "code": "INGEST_FAILED"})
    if result.get("code") in ("GATEWAY_DOWN", "GATEWAY_TIMEOUT"):
        return JSONResponse(status_code=502, content=result)
    if result.get("error"):
        return JSONResponse(status_code=502, content=result)
    return result


@app.post("/api/open-url")
async def open_url(request: Request):
    """通过系统默认浏览器打开 URL"""
    body = await request.json()
    url = body.get("url", "").strip()
    if not url:
        raise HTTPException(400, "URL is required")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(400, "Only http/https URLs are allowed")
    try:
        webbrowser.open(url)
        return {"success": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/settings")
async def get_settings():
    """返回配置，API Key 做掩码处理（安全）"""
    cfg = load_config()
    key = cfg.get("api_key", "")
    if key:
        cfg["api_key"] = key[:6] + "•" * (len(key) - 10) + key[-4:] if len(key) > 10 else "•" * len(key)
    # 首次运行标识：api_key 为空则未配置
    cfg["is_first_run"] = not bool(cfg.get("api_key", "").strip())
    return cfg


@app.put("/api/settings")
async def update_settings(data: dict = Body(default=None)):
    """更新配置 — 合并到现有配置，不覆盖未提供的字段"""
    existing = load_config()
    # 如果前端传回的是掩码 key，保留原有 key
    if "api_key" in data and "•" in data.get("api_key", ""):
        data["api_key"] = existing.get("api_key", "")
    merged = {**existing, **data}
    save_config(merged)
    return {"success": True}


# ═══════════════════════════════════════════════════════
# API: Conversations
# ═══════════════════════════════════════════════════════

@app.post("/api/conversations")
async def create_conversation(data: dict = Body(default=None)):
    title = (data or {}).get("title", "New Chat") if data else "New Chat"
    return db.conv_create(title)


@app.get("/api/conversations")
async def list_conversations():
    return db.conv_list()


@app.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    conv = db.conv_get(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")
    conv["messages"] = db.msg_list(conv_id)
    return conv


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    db.conv_del(conv_id)
    reset_counter(conv_id)
    return {"success": True}


@app.put("/api/conversations/{conv_id}")
async def rename_conversation(conv_id: str, data: dict = Body(default=None)):
    """重命名对话"""
    title = data.get("title", "").strip()
    if not title:
        raise HTTPException(400, "title 不能为空")
    db.conv_update_title(conv_id, title)
    return {"success": True, "title": title}


@app.post("/api/conversations/{conv_id}/summarize")
async def summarize_conversation(conv_id: str):
    """深度总结对话 — 蒸馏关键决策、经验与知识"""
    conv = db.conv_get(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")

    messages = db.msg_list(conv_id)
    if not messages:
        raise HTTPException(400, "对话无消息")

    # 过滤 system 角色，构建对话文本
    chat_lines = []
    for m in messages:
        if m["role"] == "system":
            continue
        role_label = "用户" if m["role"] == "user" else "AI"
        chat_lines.append(f"{role_label}：{m['content']}")

    conversation_text = "\n\n".join(chat_lines)

    # 3段式总结 prompt — 先蒸馏经验再判断重要度
    summarize_prompt = f"""请对以下对话进行深度总结和知识蒸馏。返回 JSON 格式：

{{
  "title": "对话标题（≤15字）",
  "summary": "3-5句话的全貌总结",
  "key_decisions": ["决策1", "决策2"],
  "experiences": [
    {{"content": "可复用的经验/技巧/踩坑教训", "importance": 1-5, "keywords": "逗号分隔关键词"}}
  ],
  "knowledge": ["知识点1", "知识点2"],
  "action_items": ["后续行动1", "后续行动2"],
  "tags": ["标签1", "标签2"]
}}

对话内容：
{conversation_text}

只返回 JSON，不要其他内容。"""

    msg = await call_llm(
        [{"role": "user", "content": summarize_prompt}],
        temperature=0.3,
        max_tokens=2000,
    )

    content = msg.get("content", "{}")
    result = _parse_json_response_single(content)

    # 自动存储提炼的经验到记忆库（带去重）
    experiences = result.get("experiences", [])
    saved_memories = []
    from .memory_engine import _is_duplicate
    for exp in experiences:
        content = exp.get("content", "").strip()
        if not content or _is_duplicate(content):
            continue
        mid = db.mem_add(
            type_="experience",
            content=content,
            importance=exp.get("importance", 3),
            keywords=exp.get("keywords", ""),
            source_conv_id=conv_id,
        )
        saved_memories.append({"id": mid, "content": content})

    # 自动存储决策（带去重）
    key_decisions = result.get("key_decisions", [])
    tags_str = ",".join(result.get("tags", [])) if isinstance(result.get("tags", []), list) else ""
    for dec in key_decisions:
        if not dec.strip() or _is_duplicate(dec):
            continue
        db.mem_add(
            type_="decision",
            content=dec,
            importance=4,
            keywords=tags_str,
            source_conv_id=conv_id,
        )

    # 自动存储知识点（带去重）
    knowledge_items = result.get("knowledge", [])
    for kn in knowledge_items:
        if not kn.strip() or _is_duplicate(kn):
            continue
        db.mem_add(
            type_="fact",
            content=kn,
            importance=3,
            keywords=tags_str,
            source_conv_id=conv_id,
        )

    # 自动更新对话标题
    title = result.get("title", "").strip()
    if title:
        db.conv_update_title(conv_id, title)

    # 持久化摘要到 conversations 表
    summary_text = result.get("summary", "")
    action_items = result.get("action_items", [])
    if action_items:
        summary_text += "\n[待办] " + " | ".join(action_items[:5])
    if summary_text:
        conv_update_summary(conv_id, summary_text)

    return {
        "conversation_id": conv_id,
        "message_count": len(chat_lines),
        "summary": summary_text,
        "dedup_skipped": len(experiences) + len(key_decisions) + len(knowledge_items) - len(saved_memories) - sum(1 for d in key_decisions if not _is_duplicate(d)) - sum(1 for k in knowledge_items if not _is_duplicate(k)),
        **result,
        "experiences_saved": len(saved_memories),
    }


# ===========================================================================
# 统一蒸馏 API
# ===========================================================================

@app.post("/api/distill/conversation/{conv_id}")
async def api_distill_conv(conv_id: str, save_txt: bool = True):
    """对话蒸馏 — 总结 + 知识提取 + 记忆存储 + txt 输出"""
    result = await distill_conversation(conv_id, save_txt=save_txt)
    if not result.get("success", True) and "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.post("/api/distill/schedules")
async def api_distill_schedules(
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    save_txt: bool = True,
):
    """日程蒸馏 — 规律/遗漏/优化 + txt 输出"""
    return await distill_schedules(status=status, date_from=date_from, date_to=date_to, save_txt=save_txt)


@app.post("/api/distill/memories")
async def api_distill_memories(
    type_: str = "",
    search: str = "",
    save_txt: bool = True,
):
    """记忆蒸馏 — 精华/合并/过时 + txt 输出"""
    return await distill_memories(type_=type_, search=search, save_txt=save_txt)


@app.post("/api/distill/all")
async def api_distill_all(
    conv_id: str = "",
    schedule_status: str = "confirmed",
    memory_type: str = "",
    save_txt: bool = True,
):
    """全维度综合蒸馏 — 交叉关联对话/日程/记忆 + txt 输出"""
    return await distill_all(
        conv_id=conv_id,
        schedule_status=schedule_status,
        memory_type=memory_type,
        save_txt=save_txt,
    )


@app.post("/api/distill/daily/{date}")
async def api_distill_daily(date: str, save_txt: bool = True, save_md: bool = True):
    """每日蒸馏 — 聚合指定日期的对话/日程/笔记/记忆 → 生成每日总结"""
    result = await distill_daily(date=date, save_txt=save_txt, save_md=save_md)
    if not result.get("success", True) and "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.post("/api/distill/weekly/{week_start}")
async def api_distill_weekly(week_start: str, save_txt: bool = True):
    """每周蒸馏 — 聚合指定周（从周一开始）的对话/日程/笔记/记忆 → 生成周总结"""
    result = await distill_weekly(week_start=week_start, save_txt=save_txt)
    if not result.get("success", True) and "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.get("/api/distill/files")
async def api_distill_list_files():
    """列出已保存的蒸馏 txt 文件"""
    from .unified_distill import _OUTPUT_DIR
    import os
    if not os.path.exists(_OUTPUT_DIR):
        return {"files": []}
    files = []
    for f in sorted(os.listdir(_OUTPUT_DIR)):
        if f.endswith(".txt"):
            filepath = os.path.join(_OUTPUT_DIR, f)
            stat = os.stat(filepath)
            files.append({
                "name": f,
                "path": filepath,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
    return {"files": files, "count": len(files)}


@app.get("/api/distill/file/{filename}")
async def api_distill_get_file(filename: str):
    """下载指定蒸馏 txt 文件"""
    from .unified_distill import _OUTPUT_DIR
    import os
    filepath = os.path.join(_OUTPUT_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(404, "文件不存在")
    return FileResponse(filepath, media_type="text/plain", filename=filename)


def _parse_json_response_single(content: str) -> dict:
    """解析 LLM 返回的单体 JSON"""
    text = content.strip()
    if "```" in text:
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            if text and text[0].isalpha():
                first_line_end = text.find("\n")
                if first_line_end > 0:
                    lang = text[:first_line_end].strip()
                    if lang.isalpha():
                        text = text[first_line_end + 1:]
    text = text.strip()
    try:
        import json
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except (json.JSONDecodeError, ValueError):
                pass
    return {}


# ═══════════════════════════════════════════════════════
# API: Chat (SSE Streaming) — 后台任务 + Queue 架构
# 客户端断连时后台任务继续处理，保证对话始终完成
# ═══════════════════════════════════════════════════════

_active_streams: dict[str, asyncio.Task] = {}  # conv_id → 后台处理任务

# 对话中的待确认记忆整理计划：conv_id → {plan, message, created_at}
_pending_consolidate: dict[str, dict] = {}


async def _handle_consolidate_chat(
    conv_id: str,
    user_message: str,
    event_queue: asyncio.Queue,
):
    """处理记忆整理意图：返回计划或执行结果，不走普通 LLM 流程。"""
    # 1. 先检查是否有待执行计划 + 用户确认
    pending = _pending_consolidate.get(conv_id)
    confirm_words = ["确认执行", "执行", "确认", "删除", "开始整理"]
    skip_words = ["跳过", "取消", "不", "不要", "算了"]
    is_confirm = any(w in user_message for w in confirm_words)
    is_skip = any(w in user_message for w in skip_words)

    if pending:
        if is_confirm and not is_skip:
            try:
                result = await apply_consolidate_plan(pending["plan"])
                _pending_consolidate.pop(conv_id, None)
                reply = (
                    f"🧹 记忆整理已执行。\n"
                    f"共删除 {result['deleted_count']} 条记忆（重复/过时）。\n"
                    f"失败 {len(result['failed'])} 条。"
                )
                if result["deleted_ids"]:
                    reply += f"\n删除 ID: {', '.join(str(x) for x in result['deleted_ids'])}"
                if result["failed"]:
                    reply += f"\n失败项: {result['failed'][:3]}"
            except Exception as e:
                reply = f"❌ 记忆整理执行失败: {e}"
        elif is_skip:
            _pending_consolidate.pop(conv_id, None)
            reply = "已取消记忆整理。"
        else:
            # 用户没给明确指令，再次展示计划
            reply = (
                "我还在等你确认上次的记忆整理计划。\n\n"
                + _format_consolidate_plan(pending["plan"])
            )
        event_queue.put_nowait(json.dumps({'type': 'text', 'content': reply}, ensure_ascii=False))
        event_queue.put_nowait(json.dumps({'type': 'full_text', 'content': reply, 'conversation_id': conv_id}, ensure_ascii=False))
        db.msg_add(conv_id, "assistant", reply)
        event_queue.put_nowait(json.dumps({'type': 'done'}))
        event_queue.put_nowait(None)
        return

    # 2. 检测新意图
    intent = await detect_consolidate_intent(user_message)
    if not intent.get("trigger"):
        # 不是整理意图，走正常流程：返回 False 让调用方继续
        event_queue.put_nowait(None)
        return

    # 3. 生成整理计划
    scope = intent.get("scope", "all")
    type_ = ""
    search = ""
    if scope in ("personal_info", "preference", "event", "decision", "fact", "experience"):
        type_ = scope
    elif scope and scope != "all":
        search = scope

    plan = await generate_consolidate_plan(type_=type_, search=search)
    formatted = _format_consolidate_plan(plan)

    # 4. 如果没有任何候选，直接提示
    if not plan.get("merge_groups") and not plan.get("outdated"):
        reply = "🧹 我检查了记忆库，没有发现明显的重复或过时记忆，无需整理。"
        event_queue.put_nowait(json.dumps({'type': 'text', 'content': reply}, ensure_ascii=False))
        event_queue.put_nowait(json.dumps({'type': 'full_text', 'content': reply, 'conversation_id': conv_id}, ensure_ascii=False))
        db.msg_add(conv_id, "assistant", reply)
        event_queue.put_nowait(json.dumps({'type': 'done'}))
        event_queue.put_nowait(None)
        return

    # 5. 保存待确认计划，并返回计划给用户
    _pending_consolidate[conv_id] = {
        "plan": plan,
        "message": user_message,
        "created_at": datetime.now().isoformat(),
    }
    event_queue.put_nowait(json.dumps({'type': 'text', 'content': formatted}, ensure_ascii=False))
    event_queue.put_nowait(json.dumps({'type': 'full_text', 'content': formatted, 'conversation_id': conv_id}, ensure_ascii=False))
    db.msg_add(conv_id, "assistant", formatted)
    event_queue.put_nowait(json.dumps({'type': 'done'}))
    event_queue.put_nowait(None)


async def _process_conv(
    conv_id: str, user_message: str, messages: list, cfg: dict,
    event_queue: asyncio.Queue,
):
    """后台任务：LLM 调用 + 工具执行 + 消息保存，不受客户端断连影响"""
    logger = logging.getLogger("zenith.chat")
    try:
        reminder = check_reminders()
        if reminder:
            event_queue.put_nowait(json.dumps({'type': 'reminder', 'content': reminder}, ensure_ascii=False))

        assistant_text = ""
        tool_results = []
        MAX_TOOL_ROUNDS = 6

        for round_num in range(MAX_TOOL_ROUNDS):
            round_text = ""
            round_tool_calls = []

            async for chunk in chat_stream(messages, tools=TOOLS_SCHEMA):
                if chunk["type"] == "text":
                    round_text += chunk["content"]
                    assistant_text += chunk["content"]
                    event_queue.put_nowait(json.dumps({'type': 'text', 'content': chunk["content"]}, ensure_ascii=False))
                elif chunk["type"] == "tool_call":
                    round_tool_calls.append(chunk)

            if not round_tool_calls:
                break

            assistant_msg = {
                "role": "assistant",
                "content": round_text if round_text else None,
                "tool_calls": [
                    {
                        "id": tc.get("id") or f"call_{round_num}_{i}",
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["args"], ensure_ascii=False),
                        },
                    }
                    for i, tc in enumerate(round_tool_calls)
                ],
            }
            messages.append(assistant_msg)

            for i, tc in enumerate(round_tool_calls):
                result = await execute_tool(tc["name"], tc["args"])
                tool_results.append(result)
                tool_id = tc.get("id") or f"call_{round_num}_{i}"

                if result.get("confirm"):
                    proposal_data = dict(result)
                    if "confirm_type" in proposal_data and "type" not in proposal_data:
                        proposal_data["type"] = proposal_data["confirm_type"]
                    if "confirm_id" in proposal_data and "id" not in proposal_data:
                        proposal_data["id"] = proposal_data["confirm_id"]
                    event_queue.put_nowait(json.dumps({'type': 'proposal', 'data': proposal_data}, ensure_ascii=False))
                else:
                    tool_info = f"\n\n[{tc['name']}]: {result.get('result', '')}"
                    assistant_text += tool_info
                    event_queue.put_nowait(json.dumps({'type': 'text', 'content': tool_info}, ensure_ascii=False))

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": str(result.get("result", "")),
                })

        if assistant_text:
            db.msg_add(conv_id, "assistant", assistant_text)

        event_queue.put_nowait(json.dumps({'type': 'full_text', 'content': assistant_text, 'conversation_id': conv_id}, ensure_ascii=False))

        if tool_results:
            event_queue.put_nowait(json.dumps({'type': 'tool_results', 'results': tool_results}, ensure_ascii=False))

        proposals = get_pending_proposals()
        if proposals:
            event_queue.put_nowait(json.dumps({'type': 'proposals', 'proposals': proposals}, ensure_ascii=False))

        combined = user_message + "\n" + assistant_text
        await maybe_extract_memories(combined, conv_id, interval=cfg.get("memory_extract_interval", 3))

        # 自动蒸馏：后台触发对话蒸馏
        if cfg.get("auto_distill_enabled", True):
            asyncio.create_task(_auto_distill_conv(conv_id))

        event_queue.put_nowait(json.dumps({'type': 'done'}))

    except Exception as e:
        logger.error("后台对话处理异常: %s", e, exc_info=True)
        event_queue.put_nowait(json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False))
    finally:
        # 信号：处理完成，generate() 读到 None 后退出循环
        event_queue.put_nowait(None)
        _active_streams.pop(conv_id, None)


def _build_skill_injection(current_query: str) -> str:
    """根据当前查询匹配已确认技能，返回注入到 system prompt 的文本"""
    if not current_query or len(current_query.strip()) < 2:
        return ""
    try:
        matched = db.skill_find_by_scene(current_query.strip())
        if not matched:
            return ""
        # 只取已确认的技能，最多 3 条
        confirmed = [s for s in matched if s.get("confirmed_by_user")]
        if not confirmed:
            return ""
        parts = ["【已记录技能参考】"]
        for skill in confirmed[:3]:
            steps = skill.get("steps", [])
            if isinstance(steps, str):
                try:
                    steps = json.loads(steps)
                except Exception:
                    steps = [steps]
            scene = skill.get("trigger_scene", "")
            parts.append(f"## {skill.get('name', '未命名技能')}")
            if scene:
                parts.append(f"触发场景：{scene}")
            if steps:
                parts.append("步骤：")
                for i, step in enumerate(steps, 1):
                    parts.append(f"  {i}. {step}")
            parts.append("")
        return "\n".join(parts).strip()
    except Exception:
        return ""


@app.post("/api/chat")
async def chat(request: Request):
    """SSE 流式对话 — 后台任务处理，客户端断连不影响对话完成"""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "无效的 JSON 请求"}, status_code=400)

    user_message = data.get("message", "")
    conv_id = data.get("conversation_id", "")

    if not conv_id:
        conv = db.conv_create()
        conv_id = conv["id"]

    if not user_message.strip():
        return JSONResponse({"error": "消息不能为空"}, status_code=400)

    # 如有同对话的旧后台任务仍在运行，取消它
    old_task = _active_streams.get(conv_id)
    if old_task and not old_task.done():
        old_task.cancel()
        _active_streams.pop(conv_id, None)

    db.msg_add(conv_id, "user", user_message)
    await maybe_compress(conv_id)

    # 检测/处理记忆整理意图。若命中，直接返回计划或结果，不走普通 LLM 流程。
    event_queue = asyncio.Queue()
    await _handle_consolidate_chat(conv_id, user_message, event_queue)
    if event_queue.qsize() > 1:
        # _handle_consolidate_chat 已放入内容，说明命中了 consolidate 流程
        async def generate_consolidate():
            while True:
                event = await event_queue.get()
                if event is None:
                    break
                yield f"data: {event}\n\n"
        return StreamingResponse(
            generate_consolidate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    cfg = load_config()
    system_parts = [cfg["system_prompt"]]
    memory_injection = build_memory_injection(current_query=user_message)
    if memory_injection:
        system_parts.append(memory_injection)
    skill_injection = _build_skill_injection(current_query=user_message)
    if skill_injection:
        system_parts.append(skill_injection)

    messages = [{"role": "system", "content": "\n\n".join(system_parts)}]
    for m in db.msg_list(conv_id):
        if m["role"] != "system":
            messages.append({"role": m["role"], "content": m["content"]})

    # 创建事件队列 + 启动后台处理任务
    event_queue = asyncio.Queue()
    process_task = asyncio.create_task(
        _process_conv(conv_id, user_message, messages, cfg, event_queue)
    )
    _active_streams[conv_id] = process_task

    async def generate():
        """SSE 生成器：只从队列读事件并 yield，客户端断连不影响后台任务"""
        try:
            while True:
                event = await event_queue.get()
                if event is None:  # 后台任务完成信号
                    break
                yield f"data: {event}\n\n"
        finally:
            # 无论客户端断连还是正常完成，后台任务继续处理
            _chat_logger = logging.getLogger("zenith.chat")
            _chat_logger.info("SSE 流结束 (对话%s)", conv_id)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ═══════════════════════════════════════════════════════
# API: Schedules
# ═══════════════════════════════════════════════════════

@app.get("/api/schedules")
async def get_schedules(status: str = "", date_from: str = "", date_to: str = "", overdue: str = ""):
    items = db.sch_list(status=status, date_from=date_from, date_to=date_to)
    if overdue:
        from .schedule_reminder import _parse_time
        now = now_tz()
        filtered = []
        for s in items:
            st = s.get("start_time", "")
            start = _parse_time(st) if st else None
            if start is None:
                continue
            is_overdue = start < now and s.get("status") not in ("done", "cancelled")
            if overdue == "true" and is_overdue:
                filtered.append(s)
            elif overdue == "false" and not is_overdue:
                filtered.append(s)
        return filtered
    return items


@app.post("/api/schedules")
async def create_schedule(request: Request):
    data = await request.json() or {}
    if not data.get("title"):
        raise HTTPException(400, "title 为必填字段")
    data["source"] = data.get("source", "manual")
    sid = db.sch_add(data)
    return {"id": sid, **data}


@app.put("/api/schedules/{sid}")
async def update_schedule(sid: int, data: dict = Body(default=None)):
    old = db.sch_get(sid)
    if not old:
        raise HTTPException(404, "日程不存在")

    # 重复日程仅修改本次实例
    if data.get("apply_to") == "instance" and old.get("recurrence"):
        instance = dict(old)
        instance.pop("id", None)
        instance["parent_id"] = old["id"]
        instance["recurrence"] = ""
        for k in ["title", "description", "start_time", "end_time", "location", "status", "priority", "importance", "category", "impact", "country", "remind_before", "goal_id"]:
            if k in data:
                instance[k] = data[k]
        new_id = db.sch_add(instance)
        return {"success": True, "instance_id": new_id, "message": "已创建独立实例"}

    db.sch_update(sid, data)
    # 目标关联：完成日程时推进目标进度
    if data.get("status") == "done":
        goal_id = data.get("goal_id") or old.get("goal_id")
        if goal_id:
            g = db.goal_get(goal_id)
            if g:
                strategy = g.get("strategy", "compound")
                current = float(g.get("current_value", 0))
                target = float(g.get("target_value", 1))
                daily = float(g.get("daily_target", 5))
                if strategy == "linear":
                    new_value = current + 1
                else:
                    new_value = current * (1 + daily / 100)
                new_value = min(new_value, target)
                db.goal_update(goal_id, {"current_value": new_value})
    # 自动记忆：标记 done → 后台提炼经验记忆
    if data.get("status") == "done":
        task = asyncio.create_task(_auto_extract_schedule_memory(sid, old))
        _pending_schedule_tasks.add(task)
        task.add_done_callback(_pending_schedule_tasks.discard)
    return {"success": True}


@app.delete("/api/schedules/{sid}")
async def delete_schedule(sid: int, cascade: str = ""):
    """删除日程。若 cascade=true 且为重复母日程，同时删除所有实例"""
    if cascade == "true":
        with db() as c:
            c.execute("DELETE FROM schedules WHERE parent_id = ?", (sid,))
    db.sch_del(sid)
    return {"success": True}


@app.post("/api/schedules/ai-plan")
async def ai_plan(data: dict = Body(default=None)):
    schedules = data.get("schedules", db.sch_list(status="confirmed")[:20])
    advice = await plan_time(schedules)
    return {"advice": advice}


@app.get("/api/reminders")
async def get_reminders():
    """获取当前到期提醒与已逾期日程"""
    result = get_due_reminders()
    return {
        "due": result.get("due", []),
        "overdue": result.get("overdue", []),
        "upcoming": get_upcoming_schedules(limit=5),
    }


@app.get("/api/reminders/presets")
async def get_reminder_presets():
    """返回可用的 remind_before 预设选项"""
    return REMINDER_PRESETS


# ═══════════════════════════════════════════════════════
# API: Calendar (周视图 / 月度查询 / 快捷模板)
# ═══════════════════════════════════════════════════════

_QUICK_TEMPLATES = [
    {"label": "非农就业", "title": "非农就业数据发布", "category": "economic", "importance": 5, "remind_before": 30, "default_time": "20:30"},
    {"label": "CPI数据", "title": "CPI消费者物价指数发布", "category": "economic", "importance": 4, "remind_before": 30, "default_time": "20:30"},
    {"label": "FOMC决议", "title": "FOMC利率决议", "category": "economic", "importance": 5, "remind_before": 60, "default_time": "02:00"},
    {"label": "PMI数据", "title": "PMI制造业指数发布", "category": "economic", "importance": 3, "remind_before": 15, "default_time": ""},
    {"label": "EIA原油", "title": "EIA原油库存数据", "category": "economic", "importance": 3, "remind_before": 15, "default_time": "22:30"},
    {"label": "PCE物价", "title": "PCE物价指数发布", "category": "economic", "importance": 4, "remind_before": 30, "default_time": "20:30"},
    {"label": "零售销售", "title": "零售销售数据发布", "category": "economic", "importance": 3, "remind_before": 15, "default_time": "20:30"},
    {"label": "ADP就业", "title": "ADP就业数据发布", "category": "economic", "importance": 3, "remind_before": 15, "default_time": "20:15"},
    # --- 交易时段模板 ---
    {"label": "亚盘开盘", "title": "亚洲交易时段开盘", "category": "market", "importance": 2, "remind_before": 5, "default_time": "08:00"},
    {"label": "欧盘开盘", "title": "欧洲交易时段开盘", "category": "market", "importance": 3, "remind_before": 10, "default_time": "15:00"},
    {"label": "美盘开盘", "title": "美国交易时段开盘", "category": "market", "importance": 4, "remind_before": 15, "default_time": "21:30"},
    {"label": "美盘收盘", "title": "美国交易时段收盘", "category": "market", "importance": 2, "remind_before": 5, "default_time": "05:00"},
]


@app.get("/api/calendar/templates")
async def get_calendar_templates():
    return _QUICK_TEMPLATES


@app.get("/api/calendar/week")
async def get_calendar_week(date: str = ""):
    """返回指定日期所在周的所有日程（含重复展开）"""
    from datetime import datetime as _dt, timedelta as _td
    try:
        ref = _dt.strptime(date, "%Y-%m-%d") if date else _dt.now()
    except ValueError:
        ref = _dt.now()
    dow = ref.weekday()  # 周一=0
    monday = (ref - _td(days=dow)).strftime("%Y-%m-%d")
    sunday = (ref + _td(days=6 - dow)).strftime("%Y-%m-%d 23:59:59")
    # 普通日程按时间范围查询；重复日程母记录单独拉取
    normal = db.sch_list(date_from=monday, date_to=sunday)
    recurring = [s for s in db.sch_list() if s.get("recurrence")]
    expanded = []
    seen_ids = set()
    for s in normal + recurring:
        if s["id"] in seen_ids:
            continue
        seen_ids.add(s["id"])
        if s.get("recurrence"):
            instances = expand_recurring(s, monday, sunday)
            expanded.extend(instances)
        else:
            expanded.append(s)
    return {"monday": monday, "sunday": sunday, "events": expanded}


@app.get("/api/calendar/month")
async def get_calendar_month(month: str = ""):
    """返回指定月份所有有事件的日期（含重复展开）"""
    from datetime import datetime as _dt
    try:
        ref = _dt.strptime(month, "%Y-%m") if month else _dt.now()
    except ValueError:
        ref = _dt.now()
    first = ref.strftime("%Y-%m-01")
    last_day = (ref.replace(day=28) + __import__('datetime').timedelta(days=4)).replace(day=1) - __import__('datetime').timedelta(days=1)
    last = last_day.strftime("%Y-%m-%d 23:59:59")
    schedules = db.sch_list(date_from=first, date_to=last)
    # 展开重复日程并返回每个日期的事件数
    from collections import Counter
    date_counts = Counter()
    for s in schedules:
        if s.get("recurrence"):
            for inst in expand_recurring(s, first, last):
                st = inst.get("start_time", "")
                if st:
                    date_counts[st[:10]] += 1
        else:
            st = s.get("start_time", "")
            if st:
                date_counts[st[:10]] += 1
    return {"month": month or ref.strftime("%Y-%m"), "date_counts": {k: v for k, v in date_counts.items()}}


# ═══════════════════════════════════════════════════════
# API: Goals（目标追踪）
# ═══════════════════════════════════════════════════════

@app.get("/api/goals")
async def get_goals(status: str = ""):
    return db.goal_list(status=status)


@app.post("/api/goals")
async def create_goal(data: dict = Body(default=None)):
    if not data:
        data = {}
    if not data.get("title"):
        raise HTTPException(400, "title 为必填字段")
    gid = db.goal_add(data)
    return {"id": gid, **data}


@app.get("/api/goals/{gid}")
async def get_goal(gid: int):
    g = db.goal_get(gid)
    if not g:
        raise HTTPException(404, "目标不存在")
    return g


@app.put("/api/goals/{gid}")
async def update_goal(gid: int, data: dict = Body(default=None)):
    db.goal_update(gid, data)
    return {"success": True}


@app.delete("/api/goals/{gid}")
async def delete_goal(gid: int):
    db.goal_del(gid)
    return {"success": True}


@app.get("/api/goals/{gid}/stats")
async def get_goal_stats(gid: int):
    stats = db.goal_get_stats(gid)
    if not stats:
        raise HTTPException(404, "目标不存在")
    return stats


@app.get("/api/goals/{gid}/schedules")
async def get_goal_schedules(gid: int, status: str = ""):
    """获取与目标关联的日程列表"""
    g = db.goal_get(gid)
    if not g:
        raise HTTPException(404, "目标不存在")
    items = db.sch_list()
    related = [s for s in items if s.get("goal_id") == gid]
    if status:
        related = [s for s in related if s.get("status") == status]
    return related


# ═══════════════════════════════════════════════════════
# API: Notes
# ═══════════════════════════════════════════════════════

@app.get("/api/notes")
async def get_notes(search: str = ""):
    return db.note_list(search=search)


@app.post("/api/notes")
async def create_note(data: dict = Body(...)):
    if not data:
        data = {}
    if not data.get("title"):
        raise HTTPException(400, "title 为必填字段")
    nid = db.note_add(data)
    return {"id": nid, **data}


@app.put("/api/notes/{nid}")
async def update_note(nid: int, data: dict = Body(default=None)):
    db.note_update(nid, data)
    return {"success": True}


@app.delete("/api/notes/{nid}")
async def delete_note(nid: int):
    db.note_del(nid)
    return {"success": True}


@app.post("/api/notes/{nid}/distill")
async def distill_note_endpoint(nid: int):
    """手动蒸馏一条 raw note：根据记忆偏好/方法分流为笔记/日程/记忆"""
    from .tools import _handle_distill_note
    result = await _handle_distill_note({"note_id": nid})
    return result


# ═══════════════════════════════════════════════════════
# API: Proposals (Confirm Flow)
# ═══════════════════════════════════════════════════════

@app.get("/api/proposals")
async def proposals():
    return get_pending_proposals()


@app.post("/api/proposals/confirm")
async def proposal_confirm(data: dict = Body(default=None)):
    ptype = data.get("type") or data.get("confirm_type")
    pid = data.get("id") or data.get("confirm_id")
    if not ptype or not pid:
        raise HTTPException(status_code=400, detail="缺少 type 和 id 字段")
    return confirm_proposal(ptype, pid)


@app.post("/api/proposals/reject")
async def proposal_reject(data: dict = Body(default=None)):
    ptype = data.get("type") or data.get("confirm_type")
    pid = data.get("id") or data.get("confirm_id")
    if not ptype or not pid:
        raise HTTPException(status_code=400, detail="缺少 type 和 id 字段")
    return reject_proposal(ptype, pid)


@app.post("/api/proposals/modify")
async def proposal_modify(data: dict = Body(default=None)):
    ptype = data.get("type") or data.get("confirm_type")
    pid = data.get("id") or data.get("confirm_id")
    if not ptype or not pid:
        raise HTTPException(status_code=400, detail="缺少 type 和 id 字段")
    return modify_proposal(ptype, pid, data.get("changes", {}))


# ═══════════════════════════════════════════════════════
# API: Tutorial Flow（分步教程模式）
# ═══════════════════════════════════════════════════════

@app.post("/api/tutorial/create")
async def tutorial_create(data: dict = Body(default=None)):
    """创建分步教程会话

    Body: {"title": "安装MT5指标", "steps": [{"action": "...", "verify": "..."}, ...]}
    """
    title = data.get("title", "")
    steps = data.get("steps", [])
    if not title or not steps:
        raise HTTPException(status_code=400, detail="title 和 steps 不能为空")
    flow = TutorialFlow.create(title, steps)
    return {"success": True, "tutorial": flow.current_step()}


@app.get("/api/tutorial/{session_id}")
async def tutorial_get(session_id: str):
    """获取教程会话当前状态"""
    flow = TutorialFlow.get(session_id)
    if not flow:
        raise HTTPException(status_code=404, detail="教程会话不存在或已完成")
    return {"success": True, "tutorial": flow.current_step()}


@app.post("/api/tutorial/{session_id}/confirm")
async def tutorial_confirm(session_id: str):
    """确认当前步骤完成，进入下一步"""
    flow = TutorialFlow.get(session_id)
    if not flow:
        raise HTTPException(status_code=404, detail="教程会话不存在或已完成")
    return flow.confirm_step()


@app.post("/api/tutorial/{session_id}/fail")
async def tutorial_fail(session_id: str, data: dict = Body(default=None)):
    """标记当前步骤失败"""
    flow = TutorialFlow.get(session_id)
    if not flow:
        raise HTTPException(status_code=404, detail="教程会话不存在或已完成")
    reason = (data or {}).get("reason", "")
    return flow.fail_step(reason)


@app.post("/api/tutorial/{session_id}/skip")
async def tutorial_skip(session_id: str):
    """跳过当前步骤"""
    flow = TutorialFlow.get(session_id)
    if not flow:
        raise HTTPException(status_code=404, detail="教程会话不存在或已完成")
    return flow.skip_step()


@app.post("/api/tutorial/{session_id}/retry")
async def tutorial_retry(session_id: str):
    """重试当前步骤"""
    flow = TutorialFlow.get(session_id)
    if not flow:
        raise HTTPException(status_code=404, detail="教程会话不存在或已完成")
    return flow.retry_step()


@app.get("/api/tutorials/active")
async def tutorials_active():
    """列出所有活跃的教程会话"""
    return {"success": True, "tutorials": list_active_tutorials()}


# ═══════════════════════════════════════════════════════
# API: Memories
# ═══════════════════════════════════════════════════════

@app.get("/api/memories")
async def get_memories(type_: str = "", search: str = ""):
    if search:
        return db.mem_search(search)
    return db.mem_list(type_=type_)


@app.delete("/api/memories/{mid}")
async def delete_memory(mid: int):
    db.mem_del(mid)
    return {"success": True}


# ═══════════════════════════════════════════════════════
# Transform API — 记忆/笔记/行程 互转
# ═══════════════════════════════════════════════════════

_TRANSFORM_PROMPTS = {
    "schedule": {
        "memory": (
            "将以下日程信息提炼为一条长期记忆。提取其中的关键经验、决定或事实。\n"
            "输出JSON: {\"type\": \"experience|decision|event|fact\", \"content\": \"记忆内容(简洁陈述句)\", "
            "\"importance\": 1-5, \"keywords\": \"关键词1,关键词2\"}\n"
            "规则: 已完成的日程→experience(经验); 含决策的→decision; 纯事件→event; 纯信息→fact"
        ),
        "note": (
            "将以下日程扩展为一份结构化笔记。包含背景、要点、行动建议。\n"
            "输出JSON: {\"title\": \"笔记标题\", \"content\": \"笔记正文(可含换行)\", \"tags\": \"标签1,标签2\"}"
        ),
    },
    "note": {
        "memory": (
            "将以下笔记的核心内容提炼为一条长期记忆。\n"
            "输出JSON: {\"type\": \"fact|experience|decision|preference\", \"content\": \"记忆内容(简洁陈述句)\", "
            "\"importance\": 1-5, \"keywords\": \"关键词1,关键词2\"}\n"
            "规则: 知识/信息→fact; 经验/技巧→experience; 决定/结论→decision; 偏好→preference"
        ),
        "schedule": (
            "从以下笔记中提取需要执行的事项，创建一个日程。如果笔记中提到具体时间/日期，填入对应字段；否则留空。\n"
            "输出JSON: {\"title\": \"日程标题\", \"description\": \"描述\", \"start_time\": \"YYYY-MM-DDTHH:MM或空\", "
            "\"end_time\": \"同格式或空\", \"location\": \"地点或空\", \"priority\": \"low|normal|high\", "
            "\"category\": \"economic|market|reminder|personal|other\"}"
        ),
    },
    "memory": {
        "schedule": (
            "根据以下记忆内容，创建一个相关的行动日程。如果是定期事件(如非农/CPI)，设定合理时间。\n"
            "输出JSON: {\"title\": \"日程标题\", \"description\": \"描述\", \"start_time\": \"YYYY-MM-DDTHH:MM或空\", "
            "\"end_time\": \"同格式或空\", \"location\": \"地点或空\", \"priority\": \"low|normal|high\", "
            "\"category\": \"economic|market|reminder|personal|other\"}"
        ),
        "note": (
            "将以下记忆扩展为一份详细笔记，补充背景和上下文。\n"
            "输出JSON: {\"title\": \"笔记标题\", \"content\": \"笔记正文(可含换行)\", \"tags\": \"标签1,标签2\"}"
        ),
    },
}


@app.post("/api/transform")
async def transform_item(data: dict = Body(default=None)):
    """记忆/笔记/行程 互转 — LLM 生成目标数据，创建 proposed 状态新条目"""
    source_type = data.get("source_type", "")  # memory | note | schedule
    source_id = data.get("source_id", 0)
    target_type = data.get("target_type", "")  # memory | note | schedule

    if source_type not in ("memory", "note", "schedule"):
        raise HTTPException(400, f"Invalid source_type: {source_type}")
    if target_type not in ("memory", "note", "schedule"):
        raise HTTPException(400, f"Invalid target_type: {target_type}")
    if source_type == target_type:
        raise HTTPException(400, "Source and target type cannot be the same")

    # 1. 读取源数据
    if source_type == "memory":
        memories = db.mem_list()
        src = next((m for m in memories if m["id"] == source_id), None)
        if not src:
            raise HTTPException(404, "Memory not found")
        source_text = f"类型: {src['type']}\n内容: {src['content']}\n重要度: {src['importance']}/5\n关键词: {src.get('keywords', '')}"
    elif source_type == "note":
        src = db.note_get(source_id)
        if not src:
            raise HTTPException(404, "Note not found")
        source_text = f"标题: {src['title']}\n内容: {src.get('content', '')}\n标签: {src.get('tags', '')}"
    else:  # schedule
        src = db.sch_get(source_id)
        if not src:
            raise HTTPException(404, "Schedule not found")
        source_text = (
            f"标题: {src['title']}\n描述: {src.get('description', '')}\n"
            f"开始: {src.get('start_time', '')}\n结束: {src.get('end_time', '')}\n"
            f"地点: {src.get('location', '')}\n状态: {src.get('status', '')}\n"
            f"优先级: {src.get('priority', '')}\n分类: {src.get('category', '')}"
        )

    # 2. LLM 生成目标数据
    prompt_key = (source_type, target_type)
    system_prompt = _TRANSFORM_PROMPTS.get(source_type, {}).get(target_type, "")
    if not system_prompt:
        raise HTTPException(400, f"Transform {source_type}->{target_type} not supported")

    today = datetime.now().strftime("%Y-%m-%d")
    messages = [
        {"role": "system", "content": f"{system_prompt}\n当前日期: {today}\n只输出JSON，不要额外文字。"},
        {"role": "user", "content": source_text},
    ]

    resp = await call_llm(messages, temperature=0.3, max_tokens=800,
                          response_format={"type": "json_object"})

    raw = resp.get("content", "")
    try:
        import re
        m = re.search(r'\{[\s\S]*\}', raw)
        result = json.loads(m.group()) if m else json.loads(raw)
    except Exception:
        raise HTTPException(500, "LLM transform parse failed")

    # 3. 创建目标条目 (proposed 状态)
    created_id = None
    created_item = None
    source_tag = f"transform_from_{source_type}"

    if target_type == "schedule":
        result.setdefault("status", "proposed")
        result["source"] = source_tag
        result.setdefault("priority", "normal")
        result.setdefault("category", "other")
        created_id = db.sch_add(result)
        created_item = db.sch_get(created_id)
    elif target_type == "note":
        result.setdefault("status", "proposed")
        result["source"] = source_tag
        result.setdefault("content", "")
        result.setdefault("tags", "")
        created_id = db.note_add(result)
        created_item = db.note_get(created_id)
    else:  # memory
        mem_type = result.get("type", "fact")
        if mem_type not in ("personal_info", "preference", "event", "decision", "fact", "experience"):
            mem_type = "fact"
        created_id = db.mem_add(
            type_=mem_type,
            content=result.get("content", ""),
            importance=int(result.get("importance", 3)),
            keywords=result.get("keywords", ""),
            source_conv_id=source_tag,
        )
        all_mems = db.mem_list()
        created_item = next((m for m in all_mems if m["id"] == created_id), None)

    # 4. 标记源条目为已转化
    if source_type == "schedule":
        db.sch_update(source_id, {"status": "converted"})

    return {
        "success": True,
        "source_type": source_type,
        "source_id": source_id,
        "target_type": target_type,
        "created_id": created_id,
        "created_item": created_item,
        "preview": result,
    }


# Skills API
# ═══════════════════════════════════════════════════════

@app.get("/api/skills")
async def list_skills(search: str = "", confirmed: int = -1):
    return db.skill_list(search=search, confirmed=confirmed)


@app.post("/api/skills")
async def add_skill(data: dict = Body(default=None)):
    sid = db.skill_add(data)
    skill = db.skill_get(sid)
    return {"success": True, "id": sid, **skill}


@app.get("/api/skills/{sid}")
async def get_skill(sid: int):
    skill = db.skill_get(sid)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill


@app.put("/api/skills/{sid}")
async def update_skill(sid: int, data: dict = Body(default=None)):
    db.skill_update(sid, data)
    skill = db.skill_get(sid)
    return {"success": True, **skill}


@app.delete("/api/skills/{sid}")
async def delete_skill(sid: int):
    db.skill_del(sid)
    return {"success": True}


@app.post("/api/skills/{sid}/confirm")
async def confirm_skill(sid: int):
    """用户确认技能卡片"""
    db.skill_update(sid, {"confirmed_by_user": 1})
    skill = db.skill_get(sid)
    return {"success": True, **skill}


@app.post("/api/skills/{sid}/use")
async def use_skill(sid: int):
    """标记技能被使用，递增 usage_count"""
    db.skill_increment_usage(sid)
    skill = db.skill_get(sid)
    return {"success": True, **skill}


@app.get("/api/skills/match")
async def match_skills(scene: str):
    """根据触发场景查找匹配的已确认技能"""
    skills = db.skill_find_by_scene(scene)
    return skills


# ===== 技能反馈迭代机制 =====

_SKILL_FEEDBACK_PROMPT = """分析以下技能及其反馈，生成改进建议。

技能信息：
名称: {name}
触发场景: {trigger_scene}
当前步骤:
{steps}

使用反馈（来自实际使用经验）:
{feedback}

诊断问题并提出改进建议。输出 JSON（只输出 JSON）：
{{"analysis": "问题诊断（2-3句话）", "improved_steps": ["新步骤1", "新步骤2", ...], "reason": "改进理由"}}"""


@app.post("/api/skills/{sid}/feedback")
async def submit_skill_feedback(sid: int, data: dict = Body(default=None)):
    """提交技能使用反馈 → 存储为 experience 记忆"""
    skill = db.skill_get(sid)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    content = data.get("content", "").strip()
    if not content:
        raise HTTPException(400, "反馈内容不能为空")
    rating = int(data.get("rating", 3))
    keywords = data.get("keywords", "") or f"技能反馈,{skill.get('name', '')}"
    mem_id = db.mem_add(
        type_="experience",
        content=f"[技能反馈] {skill['name']}: {content}",
        importance=min(rating, 5),
        keywords=keywords,
        source_conv_id=f"skill_feedback_{sid}",
    )
    db.skill_increment_usage(sid)
    return {"success": True, "memory_id": mem_id}


@app.get("/api/skills/{sid}/suggestions")
async def get_skill_suggestions(sid: int):
    """聚合技能反馈 → LLM 生成改进建议"""
    skill = db.skill_get(sid)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    # 查询该技能的所有反馈记忆
    all_mems = db.mem_list()
    feedback_mems = [m for m in all_mems if m.get("source_conv_id") == f"skill_feedback_{sid}"]
    if len(feedback_mems) < 2:
        return {"ready": False, "feedback_count": len(feedback_mems), "min_required": 2}

    # 格式化步骤和反馈
    steps_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(skill.get("steps", [])))
    feedback_text = "\n".join(f"- {m['content']}" for m in feedback_mems[-10:])

    prompt = _SKILL_FEEDBACK_PROMPT.format(
        name=skill.get("name", ""),
        trigger_scene=skill.get("trigger_scene", ""),
        steps=steps_text,
        feedback=feedback_text,
    )
    messages = [{"role": "user", "content": prompt}]
    try:
        resp = await call_llm(messages, temperature=0.3, max_tokens=1200,
                              response_format={"type": "json_object"})
        raw = resp.get("content", "")
        m = re.search(r'\{[\s\S]*\}', raw)
        parsed = json.loads(m.group()) if m else json.loads(raw)
    except Exception as e:
        raise HTTPException(500, f"LLM 建议生成失败: {e}")

    return {
        "ready": True,
        "feedback_count": len(feedback_mems),
        "analysis": parsed.get("analysis", ""),
        "current_steps": skill.get("steps", []),
        "improved_steps": parsed.get("improved_steps", []),
        "reason": parsed.get("reason", ""),
    }


@app.post("/api/skills/{sid}/improve")
async def apply_skill_improvement(sid: int, data: dict = Body(default=None)):
    """应用改进建议到技能"""
    new_steps = data.get("steps", [])
    if not new_steps:
        raise HTTPException(400, "steps 不能为空")

    skill = db.skill_get(sid)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    # 存储旧版本（作为反馈记忆保留历史）
    old_steps = skill.get("steps", [])
    db.mem_add(
        type_="fact",
        content=f"[技能版本记录] {skill['name']} v{skill.get('usage_count', 0)}: {' → '.join(old_steps)}",
        importance=2,
        keywords=f"技能版本,{skill.get('name', '')}",
        source_conv_id=f"skill_version_{sid}",
    )

    db.skill_update(sid, {"steps": new_steps})
    updated = db.skill_get(sid)
    return {"success": True, **updated}


# API: Code Execution
# ═══════════════════════════════════════════════════════

@app.post("/api/code/run")
async def run_code(data: dict = Body(default=None)):
    if not is_code_execution_enabled():
        return JSONResponse(
            status_code=403,
            content={
                "success": False,
                "output": "代码执行已禁用。在 config.yaml 设 code_execution_enabled: true 启用（仅限本地单用户，多用户部署见 SECURITY.md）",
            },
        )
    from .code_runner import run
    return await run(data.get("code", ""), timeout=data.get("timeout", 30))


# ═══════════════════════════════════════════════════════
# API: File Analysis
# ═══════════════════════════════════════════════════════

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


@app.post("/api/analyze-file")
async def analyze_file(file: UploadFile = File(...)):
    """上传 .txt 文件，SSE 流式分析"""
    if not file.filename or not file.filename.lower().endswith('.txt'):
        return JSONResponse({"error": "仅支持 .txt 文件"}, status_code=400)

    content_bytes = await file.read()
    if len(content_bytes) > MAX_FILE_SIZE:
        return JSONResponse({"error": "文件大小超过 10MB 限制"}, status_code=413)

    # 尝试 UTF-8，回退 GBK（Windows 记事本常见编码）
    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        content = content_bytes.decode("gbk", errors="replace")

    if not content.strip():
        return JSONResponse({"error": "文件内容为空"}, status_code=400)

    filename = file.filename

    async def generate():
        try:
            async for event in analyze_file_stream(filename, content):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type':'error','message':str(e)}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type':'done'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/analysis-documents")
async def list_analysis_documents():
    """获取所有分析文档列表"""
    return db.analysis_list()


@app.get("/api/analysis-documents/{doc_id}")
async def get_analysis_document(doc_id: int):
    """获取单个分析文档详情"""
    doc = db.analysis_get(doc_id)
    if not doc:
        raise HTTPException(404, "分析文档不存在")
    return doc


@app.get("/api/analysis-documents/{doc_id}/download")
async def download_analysis_document(doc_id: int):
    """下载分析报告为 .txt 文件"""
    doc = db.analysis_get(doc_id)
    if not doc:
        raise HTTPException(404, "分析文档不存在")

    safe_name = doc["filename"].rsplit(".txt", 1)[0]
    download_name = f"analysis_report_{safe_name}.txt"

    # 添加 UTF-8 BOM 以确保 Windows 记事本正确显示中文
    content = "\ufeff" + doc["export_text"]

    # 使用 ASCII 安全的 filename + RFC 5987 filename* 支持中文
    from urllib.parse import quote
    download_name_encoded = quote(f"分析报告_{safe_name}.txt")

    return Response(
        content=content.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=\"{download_name}\"; filename*=UTF-8''{download_name_encoded}"
        }
    )


@app.delete("/api/analysis-documents/{doc_id}")
async def delete_analysis_document(doc_id: int):
    """删除分析文档"""
    doc = db.analysis_get(doc_id)
    if not doc:
        raise HTTPException(404, "分析文档不存在")
    db.analysis_del(doc_id)
    return {"success": True}


# ═══════════════════════════════════════════════════════
# API: Calendar Dashboard (聚合数据)
# ═══════════════════════════════════════════════════════

@app.get("/api/calendar")
async def calendar_data(year: int = 0, month: int = 0):
    """返回指定月份的日历聚合数据：日程、笔记、对话、记忆、文件分析按日期分组"""
    from datetime import datetime, timedelta
    import calendar as cal_module

    now = datetime.now()
    y = year or now.year
    m = month or now.month

    # 月份范围
    first_day = datetime(y, m, 1)
    if m == 12:
        last_day = datetime(y + 1, 1, 1) - timedelta(seconds=1)
    else:
        last_day = datetime(y, m + 1, 1) - timedelta(seconds=1)

    date_from = first_day.strftime("%Y-%m-%d")
    date_to = last_day.strftime("%Y-%m-%d 23:59:59")

    result = {"year": y, "month": m, "days": {}}

    # 日程 — 按 start_time 日期分组（含重复展开）
    schedules = db.sch_list(date_from=date_from, date_to=date_to)
    expanded_schedules = []
    for s in schedules:
        if s.get("recurrence"):
            expanded_schedules.extend(expand_recurring(s, date_from, date_to))
        else:
            expanded_schedules.append(s)

    for s in expanded_schedules:
        st = s.get("start_time", "")
        if st:
            day_key = st[:10]  # YYYY-MM-DD
            result["days"].setdefault(day_key, {"schedules": [], "notes": [], "conversations": [], "memories": [], "analyses": []})
            result["days"][day_key]["schedules"].append({
                "id": s["id"], "title": s["title"], "start_time": st,
                "end_time": s.get("end_time", ""), "status": s["status"],
                "priority": s["priority"], "location": s.get("location", ""),
                "is_recurring_instance": s.get("is_recurring_instance", False),
            })

    # 笔记 — 按 created_at 日期分组
    all_notes = db.note_list()
    for n in all_notes:
        ca = n.get("created_at", "")
        if ca and ca[:7] == f"{y:04d}-{m:02d}":
            day_key = ca[:10]
            result["days"].setdefault(day_key, {"schedules": [], "notes": [], "conversations": [], "memories": [], "analyses": []})
            result["days"][day_key]["notes"].append({
                "id": n["id"], "title": n["title"], "tags": n.get("tags", ""),
            })

    # 对话 — 按 created_at 日期分组
    all_convs = db.conv_list()
    for c in all_convs:
        ca = c.get("created_at", "")
        if ca and ca[:7] == f"{y:04d}-{m:02d}":
            day_key = ca[:10]
            result["days"].setdefault(day_key, {"schedules": [], "notes": [], "conversations": [], "memories": [], "analyses": []})
            result["days"][day_key]["conversations"].append({
                "id": c["id"], "title": c["title"], "msg_count": c.get("msg_count", 0),
            })

    # 记忆 — 按 created_at 日期分组
    all_mems = db.mem_list()
    for m_ in all_mems:
        ca = m_.get("created_at", "")
        if ca and ca[:7] == f"{y:04d}-{m:02d}":
            day_key = ca[:10]
            result["days"].setdefault(day_key, {"schedules": [], "notes": [], "conversations": [], "memories": [], "analyses": []})
            result["days"][day_key]["memories"].append({
                "id": m_["id"], "type": m_["type"], "content": m_["content"][:60],
                "importance": m_.get("importance", 3),
            })

    # 文件分析 — 按 created_at 日期分组
    all_analyses = db.analysis_list()
    for a in all_analyses:
        ca = a.get("created_at", "")
        if ca and ca[:7] == f"{y:04d}-{m:02d}":
            day_key = ca[:10]
            result["days"].setdefault(day_key, {"schedules": [], "notes": [], "conversations": [], "memories": [], "analyses": []})
            result["days"][day_key]["analyses"].append({
                "id": a["id"], "filename": a["filename"],
            })

    # 月统计
    result["summary"] = {
        "schedules": len(expanded_schedules),
        "notes": sum(len(d["notes"]) for d in result["days"].values()),
        "conversations": sum(len(d["conversations"]) for d in result["days"].values()),
        "memories": sum(len(d["memories"]) for d in result["days"].values()),
        "analyses": sum(len(d["analyses"]) for d in result["days"].values()),
    }

    return result


# ═══════════════════════════════════════════════════════
# API: Market Analysis (黄金市场分析)
# ═══════════════════════════════════════════════════════

@app.get("/api/market/status")
async def market_status():
    """当前市场状态（黄金价格+宏观指标+最新报告）"""
    indicators = db.macro_indicator_list_latest(limit=15)
    latest_report = db.market_report_get_latest()
    return {
        "indicators": indicators,
        "latest_report": {
            "id": latest_report["id"] if latest_report else None,
            "report_date": latest_report["report_date"] if latest_report else None,
            "gold_price": latest_report.get("gold_price", "") if latest_report else "",
            "daily_advice": latest_report.get("daily_advice", "") if latest_report else "",
        } if latest_report else None,
    }


@app.get("/api/market/cftc")
async def market_cftc():
    """CFTC 持仓数据（JSON）"""
    from .cftc_service import get_cftc_service
    svc = get_cftc_service()
    try:
        await svc.fetch_incremental()
        data = await svc.get_positioning_json()
    except Exception as e:
        return {"error": str(e), "data": []}
    return {"data": data, "report_date": svc._report_date, "freshness": svc.check_freshness()}


@app.get("/api/market/cftc/gold")
async def market_cftc_gold():
    """CFTC 黄金专项分析"""
    from .cftc_service import get_cftc_service
    svc = get_cftc_service()
    try:
        await svc.fetch_incremental()
        data = await svc.gold_focus()
    except Exception as e:
        return {"error": str(e)}
    return data


@app.get("/api/market/reports")
async def market_reports(limit: int = 30):
    """分析报告列表"""
    return db.market_report_list(limit=limit)


@app.get("/api/market/reports/latest")
async def market_reports_latest():
    """最新分析报告"""
    report = db.market_report_get_latest()
    if not report:
        return JSONResponse(content={"id": None, "report_date": None, "gold_price": "", "daily_advice": "", "weekly_advice": "", "analysis_text": ""}, status_code=200)
    return report


@app.get("/api/market/reports/{report_id}")
async def market_report_detail(report_id: int):
    """单份报告详情"""
    report = db.market_report_get(report_id)
    if not report:
        raise HTTPException(404, "报告不存在")
    return report


@app.post("/api/market/run-analysis")
async def run_market_analysis():
    """手动触发市场分析（异步）"""
    from .market_analyzer import get_market_analyzer
    analyzer = get_market_analyzer()
    try:
        result = await analyzer.run_daily_analysis()
        return {"success": True, **result}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/market/refresh-data")
async def refresh_market_data():
    """手动刷新 CFTC + 宏观数据"""
    from .cftc_service import get_cftc_service
    from .macro_data import get_macro_service

    cftc_svc = get_cftc_service()
    macro_svc = get_macro_service()

    try:
        cftc_result = await cftc_svc.fetch_incremental()
        macro_result = await macro_svc.fetch_all_indicators()
    except Exception as e:
        return {"success": False, "error": str(e)}

    return {
        "success": True,
        "cftc": {"status": cftc_result.get("status"), "report_date": cftc_result.get("report_date")},
        "macro_count": len(macro_result.get("indicators", [])),
    }


@app.get("/api/market/predictions")
async def market_predictions(date: str = "", verified: str = ""):
    """预测列表"""
    return db.prediction_list(date=date, verified=verified)


@app.get("/api/market/predictions/hit-rate")
async def market_predictions_hit_rate(days: int = 30):
    """命中率统计"""
    return db.prediction_get_hit_rate(days=days)


@app.post("/api/market/predictions/verify")
async def verify_predictions():
    """手动触发预测验证"""
    from .market_analyzer import get_market_analyzer
    analyzer = get_market_analyzer()
    try:
        result = await analyzer.verify_yesterday_predictions()
        return {"success": True, **result}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════
# API: MT5 (MetaTrader 5 桥接)
# ═══════════════════════════════════════════════════════

@app.get("/api/mt5/status")
async def mt5_status():
    """MT5 连接状态"""
    from .mt5_service import get_connection_status
    return get_connection_status()


@app.get("/api/mt5/tick")
async def mt5_tick(symbol: str = "XAUUSD"):
    """获取最新 Tick 报价"""
    from .mt5_service import get_tick
    return get_tick(symbol)


@app.get("/api/mt5/rates")
async def mt5_rates(symbol: str = "XAUUSD", timeframe: str = "M5", count: int = 100):
    """获取历史 K 线数据"""
    from .mt5_service import get_rates
    count = min(max(count, 1), 1000)  # 限制 1-1000
    return get_rates(symbol, timeframe, count)


@app.get("/api/mt5/volume-profile")
async def mt5_volume_profile(symbol: str = "XAUUSD", timeframe: str = "M5", count: int = 200):
    """获取成交量分布 (Volume Profile)"""
    from .mt5_service import get_volume_profile
    count = min(max(count, 10), 500)
    return get_volume_profile(symbol, timeframe, count)


@app.get("/api/mt5/positions")
async def mt5_positions():
    """获取当前持仓"""
    from .mt5_service import get_positions
    return get_positions()


@app.get("/api/mt5/tick-stats")
async def mt5_tick_stats(symbol: str = "XAUUSD", seconds: int = 60):
    """获取 Tick 成交统计"""
    from .mt5_service import get_tick_stats
    seconds = min(max(seconds, 1), 3600)
    return get_tick_stats(symbol, seconds)


@app.get("/{full_path:path}")
async def catch_all(full_path: str):
    """SPA fallback — API 路径返回 404 JSON，其他路径返回 index.html"""
    if full_path.startswith("api/"):
        raise HTTPException(404, f"API 路径不存在: /{full_path}")
    index_html = FRONTEND_DIST / "index.html"
    if index_html.exists():
        return FileResponse(
            index_html,
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    raise HTTPException(404, "Page not found. Build the frontend first.")


# ═══════════════════════════════════════════════════════
# Main Entry
# ═══════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"=> Zenith v2 backend starting on http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
