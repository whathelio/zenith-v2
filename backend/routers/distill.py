"""Distill API — 蒸馏 + 文件管理"""
from fastapi import APIRouter, HTTPException, Body
from pathlib import Path
from ..unified_distill import (
    distill_conversation, distill_schedules, distill_memories,
    distill_all, distill_daily, distill_weekly, _OUTPUT_DIR as DISTILL_OUTPUT
)

router = APIRouter(prefix="/api/distill", tags=["distill"])


@router.post("/conversation")
async def api_distill_conversation(conv_id: str = "", save_txt: bool = True):
    if not conv_id:
        raise HTTPException(400, "conv_id is required")
    return await distill_conversation(conv_id, save_txt=save_txt)


@router.post("/schedules")
async def api_distill_schedules(status: str = "confirmed", save_txt: bool = True):
    return await distill_schedules(status=status, save_txt=save_txt)


@router.post("/memories")
async def api_distill_memories(type_: str = "", search: str = "", save_txt: bool = True):
    return await distill_memories(type_=type_, search=search, save_txt=save_txt)


@router.post("/all")
async def api_distill_all(conv_id: str = "", schedule_status: str = "confirmed", memory_type: str = "", save_txt: bool = True):
    return await distill_all(conv_id=conv_id, schedule_status=schedule_status, memory_type=memory_type, save_txt=save_txt)


@router.post("/daily/{date}")
async def api_distill_daily(date: str, save_txt: bool = True, save_md: bool = True):
    return await distill_daily(date=date, save_txt=save_txt, save_md=save_md)


@router.post("/weekly/{start}")
async def api_distill_weekly(start: str, save_txt: bool = True):
    return await distill_weekly(week_start=start, save_txt=save_txt)


@router.get("/files")
async def api_distill_files():
    if not DISTILL_OUTPUT.exists():
        return {"files": [], "path": str(DISTILL_OUTPUT)}
    files = []
    for p in sorted(DISTILL_OUTPUT.rglob("*.txt"), reverse=True):
        files.append({"name": p.relative_to(DISTILL_OUTPUT).as_posix(), "size": p.stat().st_size, "modified": p.stat().st_mtime})
    return {"files": files[:100], "path": str(DISTILL_OUTPUT)}
