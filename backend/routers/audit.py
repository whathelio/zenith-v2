"""Audit API — 审计日志查询、导出、验证 + 执行痕迹回读"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from ..audit.audit_log import verify_chain, export_logs
from .. import database as db

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/traces")
async def get_audit_traces(conv_id: str = "", date: str = ""):
    """查询审计追踪记录"""
    logs = export_logs(conv_id=conv_id, date=date)
    return logs[-100:]  # 最近 100 条


@router.get("/conv-traces")
async def get_conv_traces(conv_id: str = "", trace_type: str = "", limit: int = 200):
    """查询某对话的执行痕迹（conversation_traces 表）— 前端切换模块后回读用"""
    if not conv_id:
        raise HTTPException(400, "conv_id 必填")
    return db.trace_list(conv_id=conv_id, trace_type=trace_type, limit=limit)


@router.get("/trace-history")
async def get_trace_history(keyword: str = "", trace_type: str = "",
                            conv_id: str = "", limit: int = 50, offset: int = 0):
    """跨对话查询工具调用历史（前端历史查询页）— 支持关键词/类型/对话过滤 + 分页"""
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    return db.trace_query(
        keyword=keyword, trace_type=trace_type, conv_id=conv_id,
        limit=limit, offset=offset,
    )


@router.get("/export")
async def export_audit(conv_id: str = "", date: str = ""):
    """导出审计日志为 JSONL"""
    logs = export_logs(conv_id=conv_id, date=date)
    import json
    text = "\n".join(json.dumps(l, ensure_ascii=False) for l in logs)
    return PlainTextResponse(text, media_type="application/x-ndjson")


@router.get("/verify")
async def api_verify_chain(date: str = ""):
    """验证 Hash 链完整性"""
    from datetime import datetime, timezone, timedelta
    if not date:
        date = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    return verify_chain(date)
