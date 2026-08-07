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
from .routers import memories, notes, goals, schedules, distill, knowledge, settings, chat, audit, modules, news
from .database import conv_update_summary
from .config import load_config, save_config, ensure_dirs, DEFAULT_CONFIG, is_code_execution_enabled, is_auto_distill_enabled
from .tools import TOOLS_SCHEMA, execute_tool
from .llm_client import chat_stream, plan_time, call_llm
from .memory_engine import maybe_extract_memories, build_memory_injection, reset_counter, mem_consolidate, extract_memories_from_text
from .confirm_flow import get_pending_proposals, confirm_proposal, reject_proposal, modify_proposal
from .confirm_flow import get_pending_proposals_merged, confirm_action, reject_action
from .confirm_flow import TutorialFlow, list_active_tutorials
from .context_compressor import maybe_compress
from .schedule_reminder import check_reminders, get_due_reminders, get_upcoming_schedules, REMINDER_PRESETS
from .timezone import now_tz
from .recurrence import expand_recurring
from .file_analyzer import analyze_file_stream
from .unified_distill import distill_conversation, distill_schedules, distill_memories, distill_all, distill_daily, distill_weekly
from . import knowledge_service
from . import scheduler

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

    # 启动所有后台定时任务（scheduler.py 统一管理）
    scheduler.start_all_background_tasks()

    yield

    # 关闭时清理 MCP stdio 子进程连接池，避免残留进程
    try:
        from .mcp_client import close_all
        import logging
        await close_all()
        logging.getLogger("zenith.app").info("MCP 连接池已清理")
    except Exception as e:
        import logging
        logging.getLogger("zenith.app").warning("MCP 连接池清理失败: %s", e)


async def _reminder_loop():
    """后台每5分钟扫描 remind_before 到期提醒，记录到 schedule_reminders 表"""
    logger = logging.getLogger("zenith.schedule")
    while True:
        try:
            # check_reminders 会调 get_due_reminders + _record_reminder 写表
            text = check_reminders()
            if text:
                logger.info("日程提醒扫描发现到期项:\n%s", text)
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
    """后台自动提取对话记忆（共用 _do_extract 内核，与 periodic 同路径）"""
    import logging
    logger = logging.getLogger("zenith.distill")
    try:
        msgs = db.msg_list(conv_id)
        text = "\n".join(m.get("content", "") for m in msgs if m.get("role") in ("user", "assistant"))
        if not text.strip():
            return
        result = await extract_memories_from_text(text, conv_id)
        new_count = result.get("new", 0)
        if new_count > 0:
            logger.info("对话结束记忆提取: conv=%s, 新增%d条", conv_id, new_count)
    except Exception as e:
        logging.getLogger("zenith.distill").warning("对话结束记忆提取失败: %s", e)


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
    allow_origins=["http://localhost:8766", "http://127.0.0.1:8766", "http://localhost:5173"],
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


# Knowledge - migrated to routers/knowledge.py


# ═══════════════════════════════════════════════════════
# API: Conversations
# ═══════════════════════════════════════════════════════

@app.post("/api/conversations")
async def create_conversation(data: dict = Body(default=None)):
    title = (data or {}).get("title", "New Chat") if data else "New Chat"
    persona_name = (data or {}).get("persona_name", "") if data else ""
    return db.conv_create(title, persona_name)


# ═══════════════════════════════════════════════════════
# API: 学习对话工厂 — 从笔记/记忆一键创建学习对话
# ═══════════════════════════════════════════════════════
_MAX_BG_LEN = 2000  # 背景截断上限（中文约 1.5k tokens）


@app.post("/api/learning/start")
async def start_learning(data: dict = Body(default=None)):
    """从笔记/记忆/文档创建学习对话。
    入参: {source_type: "note"|"memory"|"document", source_id: int}
    流程: 读内容 → 截断 → 按类型生成苏格拉底引导背景 → 建对话 → 返回 conv_id
    """
    if not data:
        raise HTTPException(400, "缺少参数")
    source_type = data.get("source_type", "")
    source_id = data.get("source_id")
    if source_type not in ("note", "memory", "document"):
        raise HTTPException(400, "source_type 仅支持 note/memory/document")
    if not source_id:
        raise HTTPException(400, "source_id 不能为空")

    # 1. 读取内容
    if source_type == "note":
        item = db.note_get(int(source_id))
        if not item:
            raise HTTPException(404, f"笔记 #{source_id} 不存在")
        title = item.get("title") or f"笔记 #{source_id}"
        content = item.get("content") or ""
        source_type_label = "笔记"
    elif source_type == "memory":
        item = db.mem_get(int(source_id))
        if not item:
            raise HTTPException(404, f"记忆 #{source_id} 不存在")
        title = (item.get("content") or "")[:40] or f"记忆 #{source_id}"
        content = item.get("content") or ""
        source_type_label = "记忆"
    else:  # document — 知识库文档（整本逐段学习）
        try:
            from .knowledge_service import get_doc_chunks
            doc = await get_doc_chunks(int(source_id))
        except Exception as e:
            raise HTTPException(503, f"知识库服务不可用: {e}")
        if doc.get("error") or not doc.get("chunks"):
            raise HTTPException(404, doc.get("error", f"文档 #{source_id} 未入库或不存在"))
        chunks = doc.get("chunks", [])
        total = doc.get("total", len(chunks))
        title = (chunks[0].get("title") or "")[:40] or f"文档 #{source_id}"
        # 背景只放开头摘要 + 教学引导，正文由 read_document_chunk 逐段拉取
        first_text = (chunks[0].get("text") or "")[:_MAX_BG_LEN]
        content = first_text
        source_type_label = "文档"

    if not content.strip():
        raise HTTPException(400, "内容为空，无法创建学习对话")

    # 2. 苏格拉底引导分层（按 source_type / 内容类型）
    content_preview = content.strip()
    truncated = False
    if len(content_preview) > _MAX_BG_LEN:
        content_preview = content_preview[:_MAX_BG_LEN]
        truncated = True

    mem_type = item.get("type", "") if source_type == "memory" else ""
    if source_type == "memory" and mem_type == "experience":
        guide = (
            "这是你的经验记忆。请用苏格拉底式追问（最多3个）引导我反思这段经历："
            "当时发生了什么、为什么有效/无效、下次如何复用。"
        )
    elif source_type == "document":
        guide = (
            "【逐段学习模式】这份文档已整本入库并切成段落。教学规则：\n"
            "1. 从第 1 段开始，先讲解当前段的要点（用自己的话，不要照抄）\n"
            "2. 讲完提一个具体问题确认我理解（一次一个）\n"
            "3. 我回答后：判断我是否掌握——掌握则调用 read_document_chunk 工具读取下一段继续；"
            "没掌握则换个角度再讲，直到我掌握\n"
            "4. 每段都这样推进，直到读完最后一段后总结全书"
        )
    else:
        guide = (
            "请你扮演苏格拉底式的学习伙伴，用提问引导我理解以下内容："
            "每次只提一个核心问题，等我想清楚再进入下一个，逐步推导，不要一次性灌输。"
            "内容来自我的{label}「{title}」。"
        ).format(label=source_type_label, title=title)

    bg = f"{content_preview}\n\n【学习模式】{guide}"
    if truncated:
        bg += "\n\n（内容较长已截断，完整内容请逐段学习或使用检索工具获取）"

    # 3. 创建学习对话
    conv = db.conv_create(
        title=f"学习：{title[:30]}",
        source_type=source_type,
        source_id=str(source_id),
    )
    db.conv_update_background(conv["id"], bg)
    # 文档学习：初始化进度
    if source_type == "document":
        db.conv_update_learning_progress(conv["id"], {
            "doc_id": int(source_id),
            "title": title,
            "chunk_index": 0,
            "total_chunks": total,
        })
    return {"success": True, "conversation_id": conv["id"], "title": conv["title"]}


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
    """重命名对话 / 更新 persona_name / 更新 background"""
    title = data.get("title", "").strip()
    persona_name = data.get("persona_name", "")
    if not title:
        raise HTTPException(400, "title 不能为空")
    db.conv_update_title(conv_id, title)
    if "persona_name" in data:
        db.conv_update_persona(conv_id, persona_name if persona_name else None)
    if "background" in data:
        bg = data.get("background", "").strip()
        db.conv_update_background(conv_id, bg if bg else None)
    return {"success": True, "title": title}


# ═══════════════════════════════════════════════════════
# API: 对话背景图片（仅本机，不影响对话内容）
# ═══════════════════════════════════════════════════════
BG_DIR = Path(__file__).parent.parent / "data" / "backgrounds"
BG_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


@app.post("/api/conversations/{conv_id}/background-image")
async def upload_conversation_bg(conv_id: str, file: UploadFile = File(...)):
    """上传对话背景图片 — 保存到 data/backgrounds/{conv_id}{ext}"""
    conv = db.conv_get(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")
    # 校验扩展名
    ext = Path(file.filename or "").suffix.lower()
    if ext not in BG_ALLOWED_EXT:
        raise HTTPException(400, f"不支持的图片格式: {ext}，仅支持 png/jpg/jpeg/webp/gif")
    BG_DIR.mkdir(parents=True, exist_ok=True)
    # 清理旧的背景图
    for old in BG_DIR.glob(f"{conv_id}.*"):
        try:
            old.unlink()
        except OSError:
            pass
    target = BG_DIR / f"{conv_id}{ext}"
    content = await file.read()
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(400, "图片不能超过 15MB")
    target.write_bytes(content)
    db.conv_update_background_image(conv_id, f"{conv_id}{ext}")
    return {"success": True, "image": f"/api/conversations/{conv_id}/background-image"}


@app.get("/api/conversations/{conv_id}/background-image")
async def get_conversation_bg(conv_id: str):
    """返回对话背景图片（二进制）"""
    conv = db.conv_get(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")
    img = (conv or {}).get("background_image", "") or ""
    if not img:
        raise HTTPException(404, "未设置背景图片")
    path = BG_DIR / img
    if not path.exists():
        raise HTTPException(404, "背景图片文件不存在")
    media = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".gif": "image/gif",
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(str(path), media_type=media)


@app.delete("/api/conversations/{conv_id}/background-image")
async def clear_conversation_bg(conv_id: str):
    """清除对话背景图片"""
    conv = db.conv_get(conv_id)
    if not conv:
        raise HTTPException(404, "对话不存在")
    img = (conv or {}).get("background_image", "") or ""
    if img:
        path = BG_DIR / img
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
    db.conv_update_background_image(conv_id, None)
    return {"success": True}


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
    from .memory_engine import _is_duplicat, extract_memories_from_text
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
    from pathlib import Path as _P
    # 路径穿越防护：仅允许 _OUTPUT_DIR 内的 .txt 文件
    base = _P(_OUTPUT_DIR).resolve()
    filepath = (base / filename).resolve()
    if not str(filepath).startswith(str(base)):
        raise HTTPException(400, "非法文件路径")
    if filepath.suffix.lower() != ".txt":
        raise HTTPException(400, "仅支持 .txt 文件")
    if not filepath.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(filepath, media_type="text/plain", filename=filepath.name)


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

# Chat API - migrated to routers/chat.py

# Schedules/Calendar/Reminders - migrated to routers/schedules.py

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
    """返回指定日期所在周的所有日程（含重复展开）+ 外部财经事件（只读）"""
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
    # 合并外部财经事件（schedule_events 缓存，只读，含前值/预期/实际三值）
    # 注意：event_time 存 ISO 格式（如 2026-08-09T20:30:00+08:00），date_to 必须用 T 分隔，
    # 不能用 sch_list 的空格格式（'2026-08-09 23:59:59'）——字符串比较会漏掉周日当天事件
    try:
        events = db.event_list(
            date_from=monday,
            date_to=f"{sunday[:10]}T23:59:59",
            min_star=0,
        )
        for ev in events:
            et = (ev.get("event_time") or "")[:10]
            if not et or et < monday or et > sunday[:10]:
                continue
            expanded.append({
                "id": f"evt_{ev.get('id', '')}",
                "title": ev.get("name", ""),
                "description": "",
                "start_time": ev.get("event_time", ""),
                "end_time": ev.get("event_time", ""),
                "location": "",
                "status": "",
                "priority": "",
                "importance": int(ev.get("star", 1) or 1),
                "category": ev.get("category", "economic"),
                "impact": ev.get("impact", "neutral"),
                "country": ev.get("country", ""),
                "remind_before": 0,
                "goal_id": None,
                "recurrence": "",
                "parent_id": None,
                "source": ev.get("source", "jin10"),
                "confirmed_at": None,
                "created_at": ev.get("created_at", ""),
                "is_event": True,
                "is_external": True,
                "finance": {
                    "previous": ev.get("previous", ""),
                    "consensus": ev.get("consensus", ""),
                    "actual": ev.get("actual", ""),
                    "revised": ev.get("revised", ""),
                    "affect_txt": ev.get("affect_txt", ""),
                },
            })
    except Exception as e:
        logger.warning("合并财经事件失败: %s", e)
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
# API: Goals - migrated to routers/goals.py

# API: Proposals (Confirm Flow)
# ═══════════════════════════════════════════════════════

@app.get("/api/proposals")
async def proposals():
    return get_pending_proposals_merged()


@app.post("/api/proposals/confirm")
async def proposal_confirm(data: dict = Body(default=None)):
    ptype = data.get("type") or data.get("confirm_type")
    pid = data.get("id") or data.get("confirm_id")
    if not ptype or not pid:
        raise HTTPException(status_code=400, detail="缺少 type 和 id 字段")
    if ptype == "action":
        return confirm_action(int(pid))
    return confirm_proposal(ptype, pid)


@app.post("/api/proposals/reject")
async def proposal_reject(data: dict = Body(default=None)):
    ptype = data.get("type") or data.get("confirm_type")
    pid = data.get("id") or data.get("confirm_id")
    if not ptype or not pid:
        raise HTTPException(status_code=400, detail="缺少 type 和 id 字段")
    if ptype == "action":
        return reject_action(int(pid))
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
# API: Memories — migrated to routers/memories.py
# Router registration at bottom of file


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

# [C.5] Skills merged into memories — /api/skills endpoints removed


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
    return {"success": False, "error": "市场分析模块已封存"}


@app.get("/api/market/refresh-data")
async def refresh_market_data():
    return {"success": False, "error": "市场分析模块已封存"}


@app.get("/api/market/predictions")
async def market_predictions(date: str = "", verified: str = ""):
    """预测列表 — 模块已封存"""
    return []


@app.get("/api/market/predictions/hit-rate")
async def market_predictions_hit_rate(days: int = 30):
    return {"hit_rate": 0, "total": 0}


@app.post("/api/market/predictions/verify")
async def verify_predictions():
    return {"success": False, "error": "市场分析模块已封存"}


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


# Router registration (must be before catch-all)
app.include_router(memories.router)
app.include_router(notes.router)
app.include_router(goals.router)
app.include_router(schedules.router)
app.include_router(distill.router)
app.include_router(knowledge.router)
app.include_router(settings.router)
app.include_router(chat.router)
app.include_router(audit.router)
app.include_router(modules.router)
app.include_router(news.router)


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
    uvicorn.run(app, host="127.0.0.1", port=port)
