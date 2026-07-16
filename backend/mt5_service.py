"""Zenith v2 — MT5 (MetaTrader 5) Python 桥接服务

通过 MetaTrader5 Python 包连接本地 MT5 终端，获取实时行情、K线、成交量、持仓数据。
用于替代/补充 yfinance 的延迟数据，为 market_analyzer 提供实时数据源。

依赖: MetaTrader5 (pip install MetaTrader5)
前提: MT5 终端已安装并登录，且在本机运行中
平台: 仅 Windows 可用

架构:
    MT5 终端 (运行中)
        ↕  (IPC)
    MetaTrader5 Python 包
        ↕
    mt5_service.py (本模块)
        ↕
    tools.py / app.py (API + Function Calling)
        ↕
    market_analyzer.py (分析引擎)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("zenith.mt5")

# 延迟导入 MetaTrader5，避免未安装时崩溃
_mt5 = None
_initialized = False
_last_error = ""


def _ensure_mt5():
    """延迟导入并初始化 MetaTrader5"""
    global _mt5, _initialized, _last_error
    if _initialized:
        return _mt5 is not None

    try:
        import MetaTrader5 as mt5
        _mt5 = mt5
        # 初始化连接
        if not mt5.initialize():
            _last_error = f"MT5 initialize() 失败: {mt5.last_error()}"
            logger.warning(_last_error)
            _initialized = True
            return False

        info = mt5.terminal_info()
        if info:
            logger.info("MT5 connected: %s (build %s)", info.name, info.build)
        _initialized = True
        return True
    except ImportError:
        _last_error = "MetaTrader5 包未安装 (pip install MetaTrader5)"
        logger.info(_last_error)
        _initialized = True
        return False
    except Exception as e:
        _last_error = f"MT5 连接失败: {e}"
        logger.warning(_last_error)
        _initialized = True
        return False


def get_connection_status() -> dict:
    """获取 MT5 连接状态"""
    if not _ensure_mt5():
        return {
            "connected": False,
            "error": _last_error,
            "hint": "请确保 MT5 终端已安装并运行，且已安装 MetaTrader5 Python 包",
        }

    assert _mt5 is not None
    info = _mt5.terminal_info()
    acc = _mt5.account_info()

    return {
        "connected": True,
        "terminal": info.name if info else "unknown",
        "build": info.build if info else 0,
        "account": acc.login if acc else 0,
        "server": acc.server if acc else "",
        "currency": acc.currency if acc else "",
        "balance": acc.balance if acc else 0,
        "equity": acc.equity if acc else 0,
    }


def get_tick(symbol: str = "XAUUSD") -> dict:
    """获取最新 Tick 报价

    Args:
        symbol: 交易品种符号，默认 XAUUSD (黄金)

    Returns:
        {symbol, bid, ask, last, volume, time, spread}
    """
    if not _ensure_mt5():
        return {"success": False, "error": _last_error}

    assert _mt5 is not None
    tick = _mt5.symbol_info_tick(symbol)
    if tick is None:
        return {"success": False, "error": f"无法获取 {symbol} 的 Tick 数据，请检查品种名称"}

    info = _mt5.symbol_info(symbol)
    spread = (info.spread / 10) if info else (tick.ask - tick.bid)

    return {
        "success": True,
        "symbol": symbol,
        "bid": tick.bid,
        "ask": tick.ask,
        "last": tick.last,
        "volume": tick.volume,
        "time": datetime.fromtimestamp(tick.time).isoformat(),
        "spread": round(spread, 2),
        "point": info.point if info else 0.01,
        "digits": info.digits if info else 2,
    }


def get_rates(
    symbol: str = "XAUUSD",
    timeframe: str = "M5",
    count: int = 100,
) -> dict:
    """获取历史 K 线数据

    Args:
        symbol: 交易品种符号
        timeframe: 时间周期 (M1/M5/M15/M30/H1/H4/D1/W1/MN1)
        count: K 线数量

    Returns:
        {symbol, timeframe, count, rates: [{time, open, high, low, close, volume}]}
    """
    if not _ensure_mt5():
        return {"success": False, "error": _last_error}

    assert _mt5 is not None

    # 时间周期映射
    tf_map = {
        "M1": _mt5.TIMEFRAME_M1,
        "M5": _mt5.TIMEFRAME_M5,
        "M15": _mt5.TIMEFRAME_M15,
        "M30": _mt5.TIMEFRAME_M30,
        "H1": _mt5.TIMEFRAME_H1,
        "H4": _mt5.TIMEFRAME_H4,
        "D1": _mt5.TIMEFRAME_D1,
        "W1": _mt5.TIMEFRAME_W1,
        "MN1": _mt5.TIMEFRAME_MN1,
    }

    mt5_tf = tf_map.get(timeframe.upper())
    if mt5_tf is None:
        return {"success": False, "error": f"不支持的时间周期: {timeframe}"}

    rates = _mt5.copy_rates_from_pos(symbol, mt5_tf, 0, count)
    if rates is None or len(rates) == 0:
        return {"success": False, "error": f"无法获取 {symbol} {timeframe} K线数据"}

    # 转换为可序列化的列表
    bars = []
    for r in rates:
        bars.append({
            "time": datetime.fromtimestamp(r["time"]).isoformat(),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": int(r["tick_volume"]),
            "real_volume": int(r["real_volume"]) if r["real_volume"] else 0,
            "spread": int(r["spread"]),
        })

    # 计算简单统计
    closes = [b["close"] for b in bars]
    stats = {
        "high": max(b["high"] for b in bars),
        "low": min(b["low"] for b in bars),
        "open": bars[0]["open"],
        "close": bars[-1]["close"],
        "change": round(bars[-1]["close"] - bars[0]["open"], 2),
        "change_pct": round((bars[-1]["close"] - bars[0]["open"]) / bars[0]["open"] * 100, 2),
        "avg_volume": sum(b["volume"] for b in bars) // len(bars),
    }

    return {
        "success": True,
        "symbol": symbol,
        "timeframe": timeframe,
        "count": len(bars),
        "rates": bars,
        "stats": stats,
    }


def get_volume_profile(
    symbol: str = "XAUUSD",
    timeframe: str = "M5",
    count: int = 200,
    bin_size: float = 0.0,
) -> dict:
    """计算成交量分布 (Volume Profile)

    将指定数量的 K 线按价格区间分箱统计成交量，
    找出 POC (Point of Control，最大成交量价位)。

    Args:
        symbol: 交易品种
        timeframe: 时间周期
        count: K 线数量
        bin_size: 价格分箱大小，0=自动计算

    Returns:
        {symbol, poc_price, value_area_high, value_area_low,
         bins: [{price_low, price_high, volume, pct}]}
    """
    rates_result = get_rates(symbol, timeframe, count)
    if not rates_result.get("success"):
        return rates_result

    bars = rates_result["rates"]

    # 找价格范围
    price_low = min(b["low"] for b in bars)
    price_high = max(b["high"] for b in bars)

    # 自动分箱大小
    if bin_size <= 0:
        price_range = price_high - price_low
        bin_size = max(price_range / 30, 0.5)  # 30个箱，最小0.5

    # 分箱统计
    bins = {}
    for b in bars:
        # 将每根K线的成交量分配到其价格范围覆盖的箱中
        low_bin = int((b["low"] - price_low) / bin_size)
        high_bin = int((b["high"] - price_low) / bin_size)
        vol_per_bin = b["volume"] / max(high_bin - low_bin + 1, 1)

        for i in range(low_bin, high_bin + 1):
            bin_key = i
            if bin_key not in bins:
                bins[bin_key] = {
                    "price_low": price_low + i * bin_size,
                    "price_high": price_low + (i + 1) * bin_size,
                    "volume": 0,
                }
            bins[bin_key]["volume"] += vol_per_bin

    # 排序并计算百分比
    sorted_bins = sorted(bins.values(), key=lambda x: x["price_low"])
    total_vol = sum(b["volume"] for b in sorted_bins)

    for b in sorted_bins:
        b["pct"] = round(b["volume"] / total_vol * 100, 2) if total_vol > 0 else 0

    # POC: 成交量最大的箱
    poc = max(sorted_bins, key=lambda x: x["volume"])
    poc_price = (poc["price_low"] + poc["price_high"]) / 2

    # Value Area: 包含 70% 成交量的价格区间
    sorted_by_vol = sorted(sorted_bins, key=lambda x: x["volume"], reverse=True)
    va_volume = 0
    va_target = total_vol * 0.70
    va_bins = []
    for b in sorted_by_vol:
        va_volume += b["volume"]
        va_bins.append(b)
        if va_volume >= va_target:
            break

    va_prices = []
    for b in va_bins:
        va_prices.extend([b["price_low"], b["price_high"]])

    return {
        "success": True,
        "symbol": symbol,
        "timeframe": timeframe,
        "count": len(bars),
        "bin_size": round(bin_size, 2),
        "poc_price": round(poc_price, 2),
        "value_area_high": round(max(va_prices) if va_prices else price_high, 2),
        "value_area_low": round(min(va_prices) if va_prices else price_low, 2),
        "total_volume": int(total_vol),
        "bins": sorted_bins,
    }


def get_positions() -> dict:
    """获取当前持仓信息"""
    if not _ensure_mt5():
        return {"success": False, "error": _last_error}

    assert _mt5 is not None
    positions = _mt5.positions_get()

    if not positions:
        return {
            "success": True,
            "count": 0,
            "positions": [],
            "message": "当前无持仓",
        }

    pos_list = []
    for p in positions:
        pos_list.append({
            "ticket": p.ticket,
            "symbol": p.symbol,
            "type": "buy" if p.type == 0 else "sell",
            "volume": p.volume,
            "price_open": p.price_open,
            "price_current": p.price_current,
            "sl": p.sl,
            "tp": p.tp,
            "profit": round(p.profit, 2),
            "swap": round(p.swap, 2),
            "time": datetime.fromtimestamp(p.time).isoformat(),
            "comment": p.comment,
        })

    total_profit = sum(p["profit"] for p in pos_list)

    return {
        "success": True,
        "count": len(pos_list),
        "total_profit": round(total_profit, 2),
        "positions": pos_list,
    }


def get_tick_stats(symbol: str = "XAUUSD", seconds: int = 60) -> dict:
    """获取 Tick 成交统计（用于 Tick 图）

    统计指定时间内的成交笔数，用于判断市场活跃度。

    Args:
        symbol: 交易品种
        seconds: 统计时间窗口（秒）

    Returns:
        {symbol, tick_count, avg_per_sec, bid, ask, spread}
    """
    if not _ensure_mt5():
        return {"success": False, "error": _last_error}

    assert _mt5 is not None

    # 获取最近 N 秒的 Tick 数据
    utc_to = datetime.utcnow()
    utc_from = utc_to - timedelta(seconds=seconds)

    ticks = _mt5.copy_ticks_range(symbol, utc_from, utc_to, _mt5.COPY_TICKS_ALL)

    if ticks is None or len(ticks) == 0:
        return {
            "success": True,
            "symbol": symbol,
            "tick_count": 0,
            "avg_per_sec": 0,
            "message": f"最近{seconds}秒无Tick数据",
        }

    # 当前报价
    tick = _mt5.symbol_info_tick(symbol)

    return {
        "success": True,
        "symbol": symbol,
        "tick_count": len(ticks),
        "avg_per_sec": round(len(ticks) / seconds, 1),
        "seconds": seconds,
        "bid": tick.bid if tick else 0,
        "ask": tick.ask if tick else 0,
        "spread": round((tick.ask - tick.bid), 2) if tick else 0,
        "first_tick_time": datetime.fromtimestamp(ticks[0]["time"]).isoformat() if len(ticks) > 0 else "",
        "last_tick_time": datetime.fromtimestamp(ticks[-1]["time"]).isoformat() if len(ticks) > 0 else "",
    }


def shutdown():
    """关闭 MT5 连接"""
    global _mt5, _initialized
    if _mt5:
        _mt5.shutdown()
        _mt5 = None
        _initialized = False
        logger.info("MT5 connection closed")
