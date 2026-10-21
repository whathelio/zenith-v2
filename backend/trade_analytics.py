"""Zenith v2 — 交易习惯分析引擎（确定性统计层）

设计原则（很重要）：
    本模块**不调用任何 LLM**。所有数字由 Python 从 MT5 deals + K 线算出，
    LLM 只负责解读（见 `trade_review.py`）。原因：让模型从几十行流水里
    自己数胜率/持仓时长，必然算错且无法复现。

输入：
    mt5_service.get_deals_raw()   → 原始 deal（含 position_id，可配对持仓生命周期）
    mt5_service.get_rates_range() → 指定区间 K 线（用于日内位置 / MFE / MAE）

输出：
    一份结构化 profile：overview / timing / behavior / risk / market_context / habits

口径声明：
    - 时间：内部一律 epoch（UTC），展示转 UTC+8
    - 「日内」按 UTC 日期切分（经纪商日界可能不同，已在此声明）
    - 只统计真实交易（buy/sell 且带品种），入金/出金计入 funding，不混入盈亏
"""
from __future__ import annotations

import logging
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("zenith.trade_analytics")

_LOCAL_TZ = timezone(timedelta(hours=8))

# 交易时段（本地 UTC+8 近似划分，用于粗粒度归因，不做精确开盘时间判断）
_SESSIONS = [
    ("深夜", 2, 8),
    ("亚盘", 8, 16),
    ("欧盘", 16, 21),
    ("美盘", 21, 2),
]


def _local(epoch: int) -> datetime:
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).astimezone(_LOCAL_TZ)


def _fmt(epoch: int) -> str:
    try:
        return _local(epoch).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def _session_of(hour: int) -> str:
    for name, start, end in _SESSIONS:
        if start < end:
            if start <= hour < end:
                return name
        else:  # 跨零点
            if hour >= start or hour < end:
                return name
    return "其他"


def _safe_pct(a: float, b: float, default: float = 0.0) -> float:
    return round(a / b * 100, 2) if b else default


def _median(values: list[float]) -> float:
    return round(statistics.median(values), 2) if values else 0.0


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0


# ── 持仓配对 ────────────────────────────────────────────────────────────────

def _build_round_trips(trades: list[dict], now_epoch: int) -> tuple[list[dict], list[dict]]:
    """按 position_id 把 deal 配对成「一笔完整交易」（round trip）。

    deals 是**成交腿**，不是交易：一个 position 可能 1 次开仓 + 多次部分平仓，
    也可能多次加仓。只有配对后才能算持仓时长、MFE/MAE、加仓行为。

    Returns:
        (round_trips, orphan_deals)
        orphan = 没有 position_id 或只有单边的 deal（旧数据 / 特殊订单）
    """
    groups: dict[int, dict] = defaultdict(lambda: {"in": [], "out": []})
    orphans: list[dict] = []

    for d in trades:
        pid = int(d.get("position_id") or 0)
        if pid <= 0:
            orphans.append(d)
            continue
        entry = d.get("entry")
        if entry == "in":
            groups[pid]["in"].append(d)
        elif entry in ("out", "out_by", "inout"):
            # inout（反手）在 MT5 里同时是平仓与开仓，profit 记在这一腿上 → 计入平仓
            groups[pid]["out"].append(d)
        else:
            orphans.append(d)

    trips: list[dict] = []
    for pid, g in groups.items():
        ins, outs = g["in"], g["out"]
        if not ins:
            # 窗口起点之前开的仓：只有平仓腿，无法算完整生命周期
            orphans.extend(outs)
            continue

        open_vol = sum(float(d["volume"]) for d in ins)
        if open_vol <= 0:
            orphans.extend(ins + outs)
            continue

        open_time = min(int(d["time"]) for d in ins)
        entry_price = sum(float(d["price"]) * float(d["volume"]) for d in ins) / open_vol
        direction = "buy" if ins[0].get("type") == "buy" else "sell"

        close_vol = sum(float(d["volume"]) for d in outs)
        close_time = max((int(d["time"]) for d in outs), default=0)
        exit_price = (
            sum(float(d["price"]) * float(d["volume"]) for d in outs) / close_vol
            if close_vol > 0 else 0.0
        )

        realized = sum(float(d["profit"]) for d in outs)
        commission = sum(float(d["commission"]) for d in ins + outs)
        swap = sum(float(d["swap"]) for d in ins + outs)

        if not outs:
            status = "open"
            holding_min = (now_epoch - open_time) / 60.0
        else:
            # 允许 1e-6 容差：部分平仓 vs 全平
            status = "closed" if close_vol >= open_vol - 1e-6 else "partial"
            holding_min = (close_time - open_time) / 60.0

        trips.append({
            "position_id": pid,
            "symbol": ins[0].get("symbol") or "",
            "direction": direction,
            "open_time": open_time,
            "close_time": close_time,
            "open_time_local": _fmt(open_time),
            "close_time_local": _fmt(close_time) if close_time else "",
            "open_volume": round(open_vol, 2),
            "close_volume": round(close_vol, 2),
            "entry_price": round(entry_price, 5),
            "exit_price": round(exit_price, 5),
            "profit": round(realized, 2),
            "commission": round(commission, 2),
            "swap": round(swap, 2),
            "net": round(realized + commission + swap, 2),
            "holding_minutes": round(holding_min, 1),
            "status": status,
            # 平仓原因（SL/TP/强平/手动）—— 旧接口未暴露，导致「有没有止损」无法回答
            "close_reason": (
                max(outs, key=lambda d: int(d["time"])).get("reason_text", "")
                if outs else ""
            ),
            "add_on": len(ins) > 1,
            "partial_close": len(outs) > 1,
            "has_inout": any(d.get("entry") == "inout" for d in outs),
            "legs_in": len(ins),
            "legs_out": len(outs),
        })

    trips.sort(key=lambda t: t["open_time"])
    return trips, orphans


# ── 行情上下文 ──────────────────────────────────────────────────────────────

def _market_context(trips: list[dict], symbol: str) -> dict:
    """把每笔交易 join 到当时行情：日内位置、MFE/MAE、入场前波动、趋势一致性。

    只对已平仓/部分平仓的 trip 计算（open 的还没有结果）。
    """
    from .mt5_service import get_rates_range

    closed = [t for t in trips if t["status"] in ("closed", "partial") and t["symbol"] == symbol]
    if len(closed) < 5:
        return {
            "available": False,
            "reason": f"样本不足（{len(closed)} 笔可配对交易，需 ≥5）",
        }

    start = datetime.fromtimestamp(min(t["open_time"] for t in closed), tz=timezone.utc).replace(tzinfo=None)
    end = datetime.fromtimestamp(max(t["close_time"] for t in closed), tz=timezone.utc).replace(tzinfo=None)
    start -= timedelta(days=1)
    end += timedelta(hours=2)

    r = get_rates_range(symbol, "M15", start, end)
    if not r.get("success"):
        return {"available": False, "reason": r.get("error", "K 线获取失败")}

    bars = sorted(r["rates"], key=lambda b: b["time_epoch"])
    if len(bars) < 50:
        return {"available": False, "reason": f"K 线不足（{len(bars)} 根）"}

    # 按 UTC 日期聚合日内高低点
    day_hl: dict[str, list[float]] = {}
    for b in bars:
        key = datetime.fromtimestamp(b["time_epoch"], tz=timezone.utc).strftime("%Y-%m-%d")
        cur = day_hl.get(key)
        if cur is None:
            day_hl[key] = [b["low"], b["high"]]
        else:
            cur[0] = min(cur[0], b["low"])
            cur[1] = max(cur[1], b["high"])

    times = [b["time_epoch"] for b in bars]
    import bisect

    def idx_of(epoch: int) -> int:
        return bisect.bisect_left(times, epoch)

    rows = []
    for t in closed:
        i_in = max(idx_of(t["open_time"]) - 1, 0)
        i_out = min(idx_of(t["close_time"]), len(bars) - 1)

        day_key = datetime.fromtimestamp(t["open_time"], tz=timezone.utc).strftime("%Y-%m-%d")
        lo, hi = day_hl.get(day_key, [0.0, 0.0])
        day_pos = _safe_pct(t["entry_price"] - lo, hi - lo) if hi > lo else None

        # 入场前 20 根 M15 的区间宽度（波动代理）
        pre = bars[max(i_in - 20, 0): i_in + 1]
        pre_vol_pct = None
        if len(pre) >= 5 and pre[-1]["close"]:
            pre_vol_pct = round(
                (max(b["high"] for b in pre) - min(b["low"] for b in pre)) / pre[-1]["close"] * 100, 3
            )

        # 入场前 4 小时方向（16 根 M15）
        pre_dir = 0
        if i_in - 16 >= 0:
            ref = bars[i_in - 16]["close"]
            if ref:
                pre_dir = 1 if t["entry_price"] > ref else -1

        window = bars[i_in: i_out + 1] or bars[i_in: i_in + 1]
        hi_w = max(b["high"] for b in window)
        lo_w = min(b["low"] for b in window)
        ep = t["entry_price"]
        if t["direction"] == "buy":
            mfe = _safe_pct(hi_w - ep, ep)
            mae = _safe_pct(lo_w - ep, ep)
            aligned = pre_dir >= 0
        else:
            mfe = _safe_pct(ep - lo_w, ep)
            mae = _safe_pct(ep - hi_w, ep)
            aligned = pre_dir <= 0

        # 盈亏占价格百分比（捕捉率 = 实际 / MFE）
        exit_pct = _safe_pct(
            (t["exit_price"] - ep) if t["direction"] == "buy" else (ep - t["exit_price"]), ep
        ) if t["exit_price"] else 0.0

        rows.append({
            "position_id": t["position_id"],
            "direction": t["direction"],
            "profit": t["profit"],
            "day_position_pct": round(day_pos, 2) if day_pos is not None else None,
            "pre_entry_vol_pct": pre_vol_pct,
            "pre_entry_dir": pre_dir,
            "trend_aligned": aligned,
            "mfe_pct": mfe,
            "mae_pct": mae,
            "exit_pct": exit_pct,
            "capture_ratio": round(exit_pct / mfe * 100, 1) if mfe > 0 else None,
            "holding_minutes": t["holding_minutes"],
        })

    def _avg(key: str, subset: list[dict]) -> float:
        vals = [x[key] for x in subset if x[key] is not None]
        return _mean(vals)

    longs = [x for x in rows if x["direction"] == "buy"]
    shorts = [x for x in rows if x["direction"] == "sell"]
    wins = [x for x in rows if x["profit"] > 0]
    losses = [x for x in rows if x["profit"] < 0]

    return {
        "available": True,
        "symbol": symbol,
        "sample": len(rows),
        "timeframe": "M15",
        "note": "日内位置按 UTC 日界计算；0%=当日最低，100%=当日最高",
        "entry_position": {
            "long_avg_pct": _avg("day_position_pct", longs),
            "short_avg_pct": _avg("day_position_pct", shorts),
            "win_avg_pct": _avg("day_position_pct", wins),
            "loss_avg_pct": _avg("day_position_pct", losses),
        },
        "volatility": {
            "entry_pre_vol_pct_avg": _avg("pre_entry_vol_pct", rows),
            "win_vs_loss_pre_vol": {
                "win": _avg("pre_entry_vol_pct", wins),
                "loss": _avg("pre_entry_vol_pct", losses),
            },
        },
        "excursion": {
            "avg_mfe_pct": _avg("mfe_pct", rows),
            "avg_mae_pct": _avg("mae_pct", rows),
            "win_avg_mae_pct": _avg("mae_pct", wins),
            "loss_avg_mfe_pct": _avg("mfe_pct", losses),
            # ⚠️ 捕捉率只对**盈利单**聚合：亏损单的 exit_pct 为负，比值会变成
            # 负数（实测 -134%），既无经济含义又会误触发「过早止盈」规则。
            "win_capture_ratio_avg_pct": _avg("capture_ratio", wins),
            "note": (
                "win_capture_ratio < 100% 表示盈利单平均未吃满最大有利波动；"
                "loss_avg_mfe 高说明亏损单入场后曾有明显浮盈但没走"
            ),
        },
        "trend_alignment": {
            "aligned_count": sum(1 for x in rows if x["trend_aligned"]),
            "total": len(rows),
            "aligned_rate_pct": _safe_pct(sum(1 for x in rows if x["trend_aligned"]), len(rows)),
            "note": "trend_aligned = 入场前 4 小时方向与持仓方向一致",
        },
        "detail": rows,
    }


# ── 规则化习惯标签 ──────────────────────────────────────────────────────────

def _derive_habits(p: dict) -> list[dict]:
    """把统计数字翻译成可执行的「习惯结论」。

    规则刻意保守：样本 < 20 笔不判定；每条结论都必须带 evidence。
    这不是 LLM 的猜测，是可复算的规则输出。
    """
    habits: list[dict] = []
    ov = p["overview"]
    beh = p["behavior"]
    risk = p["risk"]
    mc = p.get("market_context") or {}

    n = ov.get("closed_trades", 0)
    if n < 20:
        return [{
            "tag": "样本不足",
            "severity": "info",
            "evidence": f"仅 {n} 笔可配对平仓交易（<20），以下不做习惯判定",
        }]

    if ov["payoff_ratio"] and ov["payoff_ratio"] < 1.0:
        habits.append({
            "tag": "赢小亏大（处置效应特征）",
            "severity": "high",
            "evidence": f"均盈 {ov['avg_win']} / 均亏 {ov['avg_loss']} → 盈亏比 {ov['payoff_ratio']}",
        })
    if ov["win_rate_pct"] >= 55 and (ov.get("profit_factor") or 0) < 1:
        habits.append({
            "tag": "高胜率但净亏损：靠扛单撑胜率",
            "severity": "high",
            "evidence": f"胜率 {ov['win_rate_pct']}% / PF {ov['profit_factor']} / 净利 {ov['net_profit']}",
        })
    if beh.get("loss_hold_ratio") and beh["loss_hold_ratio"] > 1.5:
        habits.append({
            "tag": "亏损单持有时间显著长于盈利单",
            "severity": "high",
            "evidence": f"亏损单均持 {beh['loss_avg_holding_min']} 分钟 vs 盈利单 {beh['win_avg_holding_min']} 分钟（比值 {beh['loss_hold_ratio']}）",
        })
    if beh.get("overnight_ratio_pct", 0) > 50:
        habits.append({
            "tag": "以隔夜持仓为主",
            "severity": "mid",
            "evidence": f"隔夜占比 {beh['overnight_ratio_pct']}%（{beh['overnight_count']}/{n}）",
        })
    if beh.get("add_on_ratio_pct", 0) > 20:
        habits.append({
            "tag": "倾向加仓（含摊平风险）",
            "severity": "mid",
            "evidence": f"加仓交易占比 {beh['add_on_ratio_pct']}%（{beh['add_on_count']}/{n}）",
        })
    if beh.get("scalp_ratio_pct", 0) > 50:
        habits.append({
            "tag": "超短线为主",
            "severity": "mid",
            "evidence": f"持仓 <15 分钟占比 {beh['scalp_ratio_pct']}%",
        })
    if ov.get("max_consecutive_losses", 0) >= 5:
        habits.append({
            "tag": "存在长连亏段（≥5 笔）",
            "severity": "mid",
            "evidence": f"最长连续亏损 {ov['max_consecutive_losses']} 笔",
        })
    if (risk.get("max_drawdown_pct") or 0) > 20:
        habits.append({
            "tag": "回撤偏大",
            "severity": "high",
            "evidence": f"最大回撤 {risk['max_drawdown']}（{risk['max_drawdown_pct']}% of 峰值权益）",
        })
    so = ov.get("stop_out_count", 0)
    if so and so / max(n, 1) >= 0.1:
        habits.append({
            "tag": "强平（爆仓）平仓占比高",
            "severity": "high",
            "evidence": (
                f"{so}/{n} 笔由强平离场（{_safe_pct(so, n)}%）；"
                f"平仓原因分布 {ov.get('close_reasons')}"
            ),
        })
    if ov.get("sl_close_count", 0) == 0:
        habits.append({
            "tag": "样本内止损从未被触发",
            "severity": "mid",
            "evidence": (
                f"{n} 笔平仓中 0 笔由止损触发（reason=4）。"
                "这不等于没设止损，但说明止损从未真正保护过仓位"
            ),
        })

    # 时段归因：找最亏的时段
    by_hour = p["timing"].get("by_hour", [])
    if by_hour:
        worst = min(by_hour, key=lambda x: x["profit"])
        if worst["trades"] >= 8 and worst["profit"] < 0:
            habits.append({
                "tag": f"{worst['hour']:02d} 点时段持续失血",
                "severity": "mid",
                "evidence": f"{worst['hour']:02d} 点 {worst['trades']} 笔，合计 {worst['profit']}，胜率 {worst['win_rate_pct']}%",
            })

    if mc.get("available"):
        ep = mc["entry_position"]
        if ep["long_avg_pct"] and ep["long_avg_pct"] < 30:
            habits.append({
                "tag": "做多偏好低位入场（抄底倾向）",
                "severity": "mid",
                "evidence": f"多单平均入场位于当日 {ep['long_avg_pct']}% 分位（0%=最低）",
            })
        if ep["short_avg_pct"] and ep["short_avg_pct"] > 70:
            habits.append({
                "tag": "做空偏好高位入场（摸顶倾向）",
                "severity": "mid",
                "evidence": f"空单平均入场位于当日 {ep['short_avg_pct']}% 分位（100%=最高）",
            })
        cr = mc["excursion"].get("win_capture_ratio_avg_pct")
        if cr is not None and cr < 40:
            habits.append({
                "tag": "盈利未能吃满波动（过早止盈）",
                "severity": "mid",
                "evidence": f"盈利单平均仅捕捉 MFE 的 {cr}%",
            })
        ta = mc["trend_alignment"].get("aligned_rate_pct")
        if ta is not None and ta < 40:
            habits.append({
                "tag": "逆短期趋势入场为主",
                "severity": "mid",
                "evidence": f"入场前 4 小时方向与持仓方向一致率仅 {ta}%",
            })

    if not habits:
        habits.append({
            "tag": "未见显著负面习惯",
            "severity": "info",
            "evidence": f"{n} 笔样本未触发任何阈值规则",
        })
    return habits


# ── 主入口 ──────────────────────────────────────────────────────────────────

def build_profile(
    days: int = 90,
    symbol: str = "",
    include_market: bool = True,
    date_from=None,
    date_to=None,
) -> dict:
    """构建交易习惯画像（纯计算，无 LLM）。

    date_from / date_to 走绝对区间（如 "2025-01-01"），可突破 days 的上限。
    """
    from .mt5_service import get_account_info, get_deals_raw

    raw = get_deals_raw(days, date_from, date_to)
    if not raw.get("success"):
        return {"success": False, "error": raw.get("error", "读取成交记录失败")}

    deals = raw["deals"]
    if symbol:
        # 允许裸名匹配带后缀品种（XAUUSD → XAUUSDm）
        deals_trade = [d for d in deals if d["kind"] == "trade" and
                       (d["symbol"] == symbol or d["symbol"].startswith(symbol))]
    else:
        deals_trade = [d for d in deals if d["kind"] == "trade"]

    funding = [d for d in deals if d["kind"] == "funding"]
    now_epoch = int(datetime.now(timezone.utc).timestamp())
    trips, orphans = _build_round_trips(deals_trade, now_epoch)

    finished = [t for t in trips if t["status"] in ("closed", "partial")]
    open_trips = [t for t in trips if t["status"] == "open"]

    wins = [t for t in finished if t["profit"] > 0]
    losses = [t for t in finished if t["profit"] < 0]
    gross_profit = round(sum(t["profit"] for t in wins), 2)
    gross_loss = round(-sum(t["profit"] for t in losses), 2)
    net_profit = round(sum(t["net"] for t in finished), 2)

    # 连续亏损（按平仓时间）
    seq = sorted(finished, key=lambda t: t["close_time"])
    max_streak = cur = 0
    for t in seq:
        if t["profit"] < 0:
            cur += 1
            max_streak = max(max_streak, cur)
        else:
            cur = 0

    # 最大回撤：**账户权益曲线**，按 deal 时间顺序重放「出入金 + 已实现盈亏」。
    # 旧实现只累加交易 net（不含出入金），算出来的是「盈亏曲线回撤」，
    # 不是账户真实回撤 —— 出金会让本金变小，必须以当时余额为分母。
    events = [(d["time"], d["profit"]) for d in funding]
    events += [(t["close_time"], t["net"]) for t in finished]
    events.sort(key=lambda x: x[0])
    peak = cum = 0.0
    max_dd = 0.0
    equity_curve = []
    for ts, delta in events:
        cum += delta
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
        equity_curve.append({"time": _fmt(ts), "balance": round(cum, 2)})

    acc = get_account_info()
    balance = float(acc.get("balance") or 0) if acc.get("success") else 0.0
    # 入金/出金分开累计：只看 funding_net 会把「入 500 出 500」误读成「没动过本金」
    deposit_total = round(sum(
        d["profit"] for d in funding
        if d.get("funding_direction") == "deposit" and d.get("funding_label") != "余额调整"
    ), 2)
    withdrawal_total = round(sum(
        d["profit"] for d in funding if d.get("funding_direction") == "withdrawal"
    ), 2)
    adjustment_total = round(sum(
        d["profit"] for d in funding if d.get("funding_label") == "余额调整"
    ), 2)
    funding_net = round(sum(d["profit"] for d in funding), 2)
    # 峰值权益 = 权益曲线重放的真实峰值（已含出入金），不再用「总入金 + 盈亏峰值」近似
    peak_equity = peak if peak > 0 else None

    overview = {
        "closed_trades": len(finished),
        "open_trades": len(open_trips),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": _safe_pct(len(wins), len(finished)),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else None,
        "avg_win": _mean([t["profit"] for t in wins]),
        "avg_loss": _mean([t["profit"] for t in losses]),
        "payoff_ratio": (
            round(_mean([t["profit"] for t in wins]) / abs(_mean([t["profit"] for t in losses])), 2)
            if wins and losses and _mean([t["profit"] for t in losses]) != 0 else None
        ),
        "expectancy_per_trade": round(net_profit / len(finished), 2) if finished else 0.0,
        "net_profit": net_profit,
        "max_single_win": round(max((t["profit"] for t in finished), default=0.0), 2),
        "max_single_loss": round(min((t["profit"] for t in finished), default=0.0), 2),
        "max_consecutive_losses": max_streak,
    }

    # 平仓原因分布：SL / TP / 强平 / 手动。这是「平仓数据异常」的核心答案 ——
    # 旧接口只返回「平仓」，看不出是爆仓还是主动离场。
    reason_counter = Counter(t.get("close_reason") or "未知" for t in finished)
    overview["close_reasons"] = dict(reason_counter)
    overview["stop_out_count"] = reason_counter.get("强平(爆仓)", 0)
    overview["sl_close_count"] = reason_counter.get("止损触发", 0)
    overview["tp_close_count"] = reason_counter.get("止盈触发", 0)
    overview["manual_close_count"] = sum(
        v for k, v in reason_counter.items()
        if k in ("手动", "手机端", "网页端", "EA", "移动止损")
    )

    # 时段
    by_hour = []
    for h in range(24):
        sub = [t for t in finished if _local(t["close_time"]).hour == h]
        if sub:
            by_hour.append({
                "hour": h,
                "trades": len(sub),
                "profit": round(sum(t["profit"] for t in sub), 2),
                "win_rate_pct": _safe_pct(sum(1 for t in sub if t["profit"] > 0), len(sub)),
            })

    wd_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    by_weekday = []
    for i, name in enumerate(wd_names):
        sub = [t for t in finished if _local(t["close_time"]).weekday() == i]
        if sub:
            by_weekday.append({
                "weekday": name,
                "trades": len(sub),
                "profit": round(sum(t["profit"] for t in sub), 2),
                "win_rate_pct": _safe_pct(sum(1 for t in sub if t["profit"] > 0), len(sub)),
            })

    by_session = []
    for name, _, _ in _SESSIONS:
        sub = [t for t in finished if _session_of(_local(t["close_time"]).hour) == name]
        by_session.append({
            "session": name,
            "trades": len(sub),
            "profit": round(sum(t["profit"] for t in sub), 2),
            "win_rate_pct": _safe_pct(sum(1 for t in sub if t["profit"] > 0), len(sub)),
        })

    # 行为
    holds = [t["holding_minutes"] for t in finished]
    win_holds = [t["holding_minutes"] for t in wins]
    loss_holds = [t["holding_minutes"] for t in losses]
    overnight = sum(
        1 for t in finished
        if t["close_time"] and _local(t["open_time"]).date() != _local(t["close_time"]).date()
    )
    active_days = {_local(t["open_time"]).date() for t in finished}

    behavior = {
        "avg_holding_min": _mean(holds),
        "median_holding_min": _median(holds),
        "p90_holding_min": round(sorted(holds)[int(len(holds) * 0.9)], 1) if len(holds) >= 10 else None,
        "win_avg_holding_min": _mean(win_holds),
        "loss_avg_holding_min": _mean(loss_holds),
        "loss_hold_ratio": (
            round(_mean(loss_holds) / _mean(win_holds), 2) if win_holds and loss_holds and _mean(win_holds) else None
        ),
        "overnight_count": overnight,
        "overnight_ratio_pct": _safe_pct(overnight, len(finished)),
        "add_on_count": sum(1 for t in finished if t["add_on"]),
        "add_on_ratio_pct": _safe_pct(sum(1 for t in finished if t["add_on"]), len(finished)),
        "partial_close_count": sum(1 for t in finished if t["partial_close"]),
        "partial_close_ratio_pct": _safe_pct(sum(1 for t in finished if t["partial_close"]), len(finished)),
        "scalp_count": sum(1 for t in finished if t["holding_minutes"] < 15),
        "scalp_ratio_pct": _safe_pct(sum(1 for t in finished if t["holding_minutes"] < 15), len(finished)),
        "buy_count": sum(1 for t in finished if t["direction"] == "buy"),
        "sell_count": sum(1 for t in finished if t["direction"] == "sell"),
        "avg_volume": _mean([t["open_volume"] for t in finished]),
        "max_volume": round(max((t["open_volume"] for t in finished), default=0.0), 2),
        "active_days": len(active_days),
        "trades_per_active_day": round(len(finished) / len(active_days), 2) if active_days else 0.0,
    }

    risk = {
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": _safe_pct(max_dd, peak_equity) if peak_equity else None,
        "drawdown_note": (
            "回撤相对**权益曲线峰值**；>100% 表示权益曾跌破峰值本金的全部（穿仓/爆仓）。"
            "曲线按 deal 重放，不含浮动盈亏"
        ),
        "drawdown_basis": "账户权益曲线（含出入金），按 deal 时间顺序重放；不含浮动盈亏",
        "balance": balance,
        "max_single_loss_pct_of_balance": _safe_pct(abs(min((t["profit"] for t in finished), default=0.0)), balance)
        if balance else None,
        "recovery_factor": round(net_profit / max_dd, 2) if max_dd else None,
        "equity_curve": equity_curve[-60:],  # 只留最近 60 个点给图表
        "equity_curve_note": "最近 60 个 deal 后的余额轨迹（含出入金）",
    }

    # 行情上下文：只分析交易笔数最多的品种
    market_context = {"available": False, "reason": "未启用"}
    if include_market and finished:
        sym_counter = Counter(t["symbol"] for t in finished if t["symbol"])
        if sym_counter:
            target = symbol or sym_counter.most_common(1)[0][0]
            market_context = _market_context(finished, target)

    # ── 对账：资金流水 + 交易净额 是否等于当前余额 ──────────────────────
    # 这是判断「数据读全了没有」最硬的证据：不相等说明窗口内有缺失 deal。
    expected = round(funding_net + net_profit, 2)
    diff = round(balance - expected, 2)
    # 对账要同时满足两个条件才成立：
    #   ① 覆盖账户起点（首条 deal 是入金/调整）—— 否则没有初始余额
    #   ② 覆盖到当前（窗口末不早于最后一条 deal）—— 否则算出来的是「窗口末余额」，
    #      不能拿今天的 balance 去比。
    # 2026-10-19 踩过：只判①，查 2025 年时显示「差 211.0 ⚠️」，
    # 实际是 2025 年末余额 0.01 与今天的 211.01 在做无意义比较。
    covers_start = bool(
        deals and deals[0].get("kind") == "funding"
        and deals[0].get("funding_label") in ("入金", "余额调整")
    )
    covers_now = bool(deals and deals[-1]["time"] >= now_epoch - 2 * 86400)
    applicable = covers_start and covers_now

    reconciliation = {
        "applicable": applicable,
        "covers_account_start": covers_start,
        "covers_now": covers_now,
        "funding_net": funding_net,
        "trading_net": net_profit,
        "expected_balance": expected,
        "actual_balance": balance,
        "diff": diff if applicable else None,
        "ok": (abs(diff) <= 0.05) if applicable else None,
        # 覆盖起点但没覆盖当前时，这个值就是「窗口末的推算余额」（很有用：
        # 例如 2025 年区间末余额 0.01，说明那年把账户做空了）
        "estimated_balance_at_window_end": expected if covers_start else None,
        "note": (
            "期望余额 = 净入金 + 交易净额。applicable=true 才与当前余额比较；"
            "若只覆盖起点（covers_now=false），该值表示窗口末的推算余额"
        ),
    }

    profile = {
        "success": True,
        "generated_at": datetime.now(_LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S UTC+8"),
        "range": {
            "days": raw.get("days"),
            "window": raw.get("window"),
            "deal_from": _fmt(deals[0]["time"]) if deals else "",
            "deal_to": _fmt(deals[-1]["time"]) if deals else "",
        },
        "account": {
            "balance": balance,
            "equity": acc.get("equity") if acc.get("success") else None,
            "floating_pl": acc.get("floating_pl") if acc.get("success") else None,
            "balance_note": acc.get("balance_note") if acc.get("success") else "",
            "equity_note": acc.get("equity_note") if acc.get("success") else "",
            "leverage": acc.get("leverage") if acc.get("success") else None,
            "currency": acc.get("currency") if acc.get("success") else None,
        },
        "funding": {
            "count": len(funding),
            "deposit_total": deposit_total,
            "withdrawal_total": withdrawal_total,
            "adjustment_total": adjustment_total,
            "net": funding_net,
            "note": (
                "入金/出金/余额调整，不计入交易盈亏；"
                "余额变动 = 交易净额 + net。deposit_total 为累计入金，"
                "withdrawal_total 为累计出金（负数）"
            ),
        },
        "reconciliation": reconciliation,
        "overview": overview,
        "timing": {"by_hour": by_hour, "by_weekday": by_weekday, "by_session": by_session},
        "behavior": behavior,
        "risk": risk,
        "market_context": market_context,
        "data_quality": {
            "trade_deals": len(deals_trade),
            "funding_deals": len(funding),
            "round_trips": len(trips),
            "orphan_deals": len(orphans),
            "orphan_note": "无 position_id 或只有单边的 deal（窗口起点前已开仓等），未纳入统计",
            "missing_fields": ["entry 时刻的点差", "滑点（需 order 与 deal 对照）"] if deals else [],
        },
        "recent_trips": [
            {
                "symbol": t["symbol"],
                "direction": t["direction"],
                "open": t["open_time_local"],
                "close": t["close_time_local"],
                "vol": t["open_volume"],
                "profit": t["profit"],
                "holding_min": t["holding_minutes"],
                "status": t["status"],
            }
            for t in finished[-20:][::-1]
        ],
    }
    profile["habits"] = _derive_habits(profile)
    return profile


def digest(profile: dict, max_chars: int = 7000) -> str:
    """把 profile 压成给 LLM 的紧凑摘要（保留全部关键数字，丢弃明细）。

    多 AI 复盘的三个角色都只读这个 digest —— 保证它们看到的是同一份事实。
    """
    import json

    keep = {
        "range": profile.get("range"),
        "account": profile.get("account"),
        "funding": profile.get("funding"),
        "reconciliation": profile.get("reconciliation"),
        "overview": profile.get("overview"),
        "timing": profile.get("timing"),
        "behavior": profile.get("behavior"),
        "risk": {k: v for k, v in (profile.get("risk") or {}).items() if k != "equity_curve"},
        "market_context": {
            k: v for k, v in (profile.get("market_context") or {}).items() if k != "detail"
        },
        "data_quality": profile.get("data_quality"),
        "habits": profile.get("habits"),
    }
    text = json.dumps(keep, ensure_ascii=False, indent=1)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…（已截断）"
    return text


def format_profile(p: dict) -> str:
    ov = p.get("overview") or {}
    beh = p.get("behavior") or {}
    risk = p.get("risk") or {}
    mc = p.get("market_context") or {}
    rng = p.get("range") or {}
    fnd = p.get("funding") or {}
    dq = p.get("data_quality") or {}
    win = rng.get("window") or {}
    # 优先展示**实际数据范围**（deal_from/to），而不是查询窗口 ——
    # 否则 days=3650 会显示「2016~2026」，让人误以为有 10 年数据。
    if rng.get("deal_from"):
        span = f"{str(rng.get('deal_from'))[:10]} ~ {str(rng.get('deal_to'))[:10]}"
    elif win:
        span = f"{str(win.get('from', ''))[:10]} ~ {str(win.get('to', ''))[:10]}"
    else:
        span = f"{rng.get('days')} 天"

    lines = [
        f"📊 交易习惯画像（{span}，{ov.get('closed_trades')} 笔已平仓）",
        f"  净盈亏 {ov.get('net_profit')} | 胜率 {ov.get('win_rate_pct')}% "
        f"| PF {ov.get('profit_factor')} | 盈亏比 {ov.get('payoff_ratio')}",
        f"  均盈 {ov.get('avg_win')} / 均亏 {ov.get('avg_loss')} | "
        f"单笔期望 {ov.get('expectancy_per_trade')}",
        f"  最长连亏 {ov.get('max_consecutive_losses')} 笔 | "
        f"最大回撤 {risk.get('max_drawdown')}（{risk.get('max_drawdown_pct')}%）",
        f"  平仓原因：强平 {ov.get('stop_out_count', 0)} / 止损 {ov.get('sl_close_count', 0)} "
        f"/ 止盈 {ov.get('tp_close_count', 0)} / 手动 {ov.get('manual_close_count', 0)}",
        f"  持仓：均值 {beh.get('avg_holding_min')} 分钟 / 中位 {beh.get('median_holding_min')} 分钟",
        f"  盈利单均持 {beh.get('win_avg_holding_min')} vs 亏损单均持 {beh.get('loss_avg_holding_min')} "
        f"（比值 {beh.get('loss_hold_ratio')}）",
        f"  隔夜 {beh.get('overnight_ratio_pct')}% | 加仓 {beh.get('add_on_ratio_pct')}% "
        f"| 超短线(<15min) {beh.get('scalp_ratio_pct')}%",
        f"  日均 {beh.get('trades_per_active_day')} 笔 / 活跃 {beh.get('active_days')} 天",
    ]
    if fnd.get("count"):
        lines.append(
            f"  资金流水：入金 {fnd.get('deposit_total')} / 出金 {fnd.get('withdrawal_total')} "
            f"/ 余额调整 {fnd.get('adjustment_total')}（不计入盈亏）"
        )
    rec = p.get("reconciliation") or {}
    if rec.get("applicable"):
        flag = "✅" if rec.get("ok") else "⚠️"
        lines.append(
            f"  对账：净入金 {rec.get('funding_net')} + 交易净额 {rec.get('trading_net')} "
            f"= {rec.get('expected_balance')}，实际余额 {rec.get('actual_balance')}"
            f"（差 {rec.get('diff')}）{flag}"
        )
    elif rec.get("estimated_balance_at_window_end") is not None:
        lines.append(
            f"  区间末推算余额 {rec['estimated_balance_at_window_end']}"
            f"（净入金 {rec.get('funding_net')} + 交易 {rec.get('trading_net')}；"
            "未覆盖到今天，不与当前余额对账）"
        )

    if not ov.get("closed_trades"):
        lines.append(
            "  提示：该区间没有可配对的平仓交易。先确认区间是否早于账户最早成交"
            "（命令行 `tools/trade_report.py --span`），不要把「区间外」误判成「没交易」"
        )

    by_hour = (p.get("timing") or {}).get("by_hour") or []
    if by_hour:
        worst = min(by_hour, key=lambda x: x["profit"])
        best = max(by_hour, key=lambda x: x["profit"])
        lines.append(
            f"  最差时段 {worst['hour']:02d}点（{worst['trades']}笔 {worst['profit']:+}）"
            f" | 最好时段 {best['hour']:02d}点（{best['trades']}笔 {best['profit']:+}）"
        )

    if mc.get("available"):
        ep = mc.get("entry_position") or {}
        ex = mc.get("excursion") or {}
        lines.append(
            f"  行情对照：多单入场日内分位 {ep.get('long_avg_pct')}% / 空单 {ep.get('short_avg_pct')}%"
        )
        lines.append(
            f"  平均 MFE {ex.get('avg_mfe_pct')}% / MAE {ex.get('avg_mae_pct')}%"
            f" | 盈利单捕捉率 {ex.get('win_capture_ratio_avg_pct')}%"
        )
    else:
        lines.append(f"  行情对照不可用：{mc.get('reason', '未知')}")

    habits = p.get("habits") or []
    if habits:
        lines.append("  习惯标签：")
        for h in habits:
            lines.append(f"    [{h.get('severity')}] {h.get('tag')} —— {h.get('evidence')}")

    if dq:
        lines.append(
            f"  数据质量：可配对 {dq.get('round_trips')} 笔，未配对 {dq.get('orphan_deals')} 条"
        )
    return "\n".join(lines)
