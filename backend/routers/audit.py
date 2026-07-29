"""Audit API — 审计日志查询、导出、验证"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from ..audit.audit_log import verify_chain, export_logs

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/traces")
async def get_audit_traces(conv_id: str = "", date: str = ""):
    """查询审计追踪记录"""
    logs = export_logs(conv_id=conv_id, date=date)
    return logs[-100:]  # 最近 100 条


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
