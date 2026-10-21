"""Trade API — 交易习惯分析（2026-10-19 新增）

与 `/api/mt5/*` 的分工：
- `/api/mt5/*`   ：原始数据通道（账户 / 成交 / 挂单 / 行情）
- `/api/trade/*` ：**分析层**，把原始数据加工成可解释的习惯结论

路由说明：
- `GET  /api/trade/profile` 确定性统计（无 LLM，可复算）
- `POST /api/trade/review`  多角色复盘（行为 / 行情 / 审计，较慢）
"""
import asyncio
import time

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/trade", tags=["trade"])

# profile 计算会读 MT5 + 遍历 K 线（约 1–4 秒），短 TTL 缓存避免前端重复打
_PROFILE_CACHE: dict[tuple, tuple[float, dict]] = {}
_PROFILE_TTL = 60.0


class ReviewRequest(BaseModel):
    days: int = 90
    symbol: str = ""
    date_from: str = ""
    date_to: str = ""
    provider_name: str = ""
    use_background: bool = True


def _clamp_days(days: int) -> int:
    return min(max(int(days), 1), 3650)


@router.get("/profile")
def trade_profile(days: int = 90, symbol: str = "", include_market: bool = True,
                  date_from: str = "", date_to: str = ""):
    """交易习惯画像（确定性统计，不调用 LLM）。

    包含：总览（胜率/PF/盈亏比/平仓原因）· 时段分布 · 行为（持仓时长/加仓/隔夜）·
    风险（回撤/连亏）· 行情对照（日内位置/MFE/MAE）· 规则化习惯标签。

    绝对区间：`?date_from=2025-01-01&date_to=2025-12-31`（可查询超过 365 天的区间）。
    """
    from ..trade_analytics import build_profile

    days = _clamp_days(days)
    df = date_from or None
    dt = date_to or None
    key = (days, symbol, bool(include_market), df, dt)
    now = time.time()
    hit = _PROFILE_CACHE.get(key)
    if hit and now - hit[0] < _PROFILE_TTL:
        return {**hit[1], "_cached": True}

    result = build_profile(days, symbol, include_market, df, dt)
    if result.get("success"):
        _PROFILE_CACHE[key] = (now, result)
    return result


@router.post("/review")
async def trade_review(req: ReviewRequest | None = None):
    """多角色交易复盘（行为分析师 → 行情对照官 → 风控审计官）。

    ⚠️ 会发起 3 次 LLM 调用（前两次并行），通常 20–90 秒。
    审计官只保留「能在统计事实中找到依据」的结论，被驳回的结论会单列。
    """
    from ..trade_analytics import build_profile
    from ..trade_review import run_review

    r = req or ReviewRequest()
    days = _clamp_days(r.days)
    df = r.date_from or None
    dt = r.date_to or None

    key = (days, r.symbol, True, df, dt)
    now = time.time()
    hit = _PROFILE_CACHE.get(key)
    if hit and now - hit[0] < _PROFILE_TTL:
        profile = hit[1]
    else:
        profile = await asyncio.to_thread(build_profile, days, r.symbol, True, df, dt)
        if profile.get("success"):
            _PROFILE_CACHE[key] = (now, profile)

    return await run_review(
        days=days,
        symbol=r.symbol,
        date_from=df,
        date_to=dt,
        provider_name=r.provider_name,
        use_background=r.use_background,
        profile=profile,
    )
