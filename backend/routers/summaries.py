"""Summaries API — 周期总结（日/周/月/年）查询与生成"""
from fastapi import APIRouter, HTTPException

from .. import database as db
from ..unified_distill import distill_daily, distill_weekly, distill_monthly, distill_yearly

router = APIRouter(prefix="/api/summaries", tags=["summaries"])

_PERIOD_TYPES = ("daily", "weekly", "monthly", "yearly")


@router.get("")
async def list_summaries(period_type: str = ""):
    if period_type and period_type not in _PERIOD_TYPES:
        raise HTTPException(400, "period_type 必须为 daily/weekly/monthly/yearly")
    return db.psum_list(period_type=period_type)


@router.get("/{period_type}/{period_key}")
async def get_summary(period_type: str, period_key: str):
    if period_type not in _PERIOD_TYPES:
        raise HTTPException(400, "period_type 必须为 daily/weekly/monthly/yearly")
    s = db.psum_get(period_type, period_key)
    if not s:
        raise HTTPException(404, "总结不存在")
    return s


@router.post("/{period_type}/{period_key}/generate")
async def generate_summary(period_type: str, period_key: str):
    if period_type == "daily":
        return await distill_daily(date=period_key)
    if period_type == "weekly":
        return await distill_weekly(week_start=period_key)
    if period_type == "monthly":
        return await distill_monthly(month=period_key)
    if period_type == "yearly":
        return await distill_yearly(year=period_key)
    raise HTTPException(400, "period_type 必须为 daily/weekly/monthly/yearly")
