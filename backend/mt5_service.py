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
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("zenith.mt5")

# 延迟导入 MetaTrader5，避免未安装时崩溃
_mt5 = None
_initialized = False
_last_error = ""
_mt5_lock = threading.Lock()  # 重连竞态保护（FastAPI 并发调用）

# 终端路径：显式指定可避免自动探测到错误实例（实测无 path 时 -10003 IPC 超时）
# 可用环境变量 ZENITH_MT5_TERMINAL_PATH 覆盖
_TERMINAL_PATH = os.environ.get(
    "ZENITH_MT5_TERMINAL_PATH", r"C:\Program Files\MetaTrader 5\terminal64.exe"
)


def _terminal_running() -> bool:
    """检测 MT5 终端进程（terminal64.exe）是否在运行。

    ⚠️⚠️ **这是必需的护栏，不是优化**（2026-09-22 实测踩坑）：

    `MetaTrader5` 的 `initialize()` 是 C 扩展，它等待命名管道响应期间
    **不释放 GIL**。终端未运行时它会阻塞满 **60 秒**才超时（`-10003`），
    且 `_ensure_mt5_locked()` 失败后还会再走一次自动探测 → **累计约 160 秒**。

    被冻结的不是某个线程，而是**整个 Python 进程**：实测起一个 heartbeat 线程
    每秒 print，在 `initialize()` 期间**一次都没打印**（连主线程都停住）。
    因此把 FastAPI 路由写成同步 `def`（丢线程池）**也救不了** —— 线程池线程
    同样拿不到 GIL。

    所以唯一低成本的止血办法：**终端不在跑就根本不调用 `initialize()`**，
    直接把结论返回给调用方。

    探测失败（tasklist 异常）时**保守放行** —— 宁可让 initialize 去试，
    也不误判「终端没开」而把可用环境判死。
    """
    if sys.platform != "win32":
        return True
    try:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq terminal64.exe", "/NH"],
            capture_output=True, text=True, timeout=5, errors="replace",
        )
        return "terminal64.exe" in (r.stdout or "").lower()
    except Exception:
        return True


def _ensure_mt5():
    """延迟导入并初始化 MetaTrader5（含管道失活自动重连）"""
    global _mt5, _initialized, _last_error
    with _mt5_lock:
        return _ensure_mt5_locked()


def _ensure_mt5_locked():
    global _mt5, _initialized, _last_error

    # ⚠️ 终端进程前置检查 —— 必须放在**任何** mt5 API 调用之前，见 _terminal_running()。
    # 注意此处**故意不设 _initialized = True**：这样终端稍后被启动时，
    # 下一次调用会自动重新探测并连接，无需重启 Zenith。
    if not _terminal_running():
        _last_error = (
            "MT5 终端未运行（已跳过 initialize，避免 60s×2 的 GIL 冻结）。"
            f"请先启动终端：{_TERMINAL_PATH}"
        )
        _mt5 = None
        return False

    if _initialized:
        if _mt5 is None:
            return False
        # 长驻进程管道活性探测：终端重启/掉线后 IPC 句柄失效，
        # MetaTrader5 包的各查询函数只会静默返回 None，必须主动探测并重连
        try:
            if _mt5.terminal_info() is None:
                logger.warning("MT5 管道失活（终端可能已重启/掉线），尝试重连")
                try:
                    _mt5.shutdown()
                except Exception:
                    pass
                _mt5 = None
                _initialized = False
                return _ensure_mt5_locked()
        except Exception:
            _mt5 = None
            _initialized = False
            return _ensure_mt5_locked()
        return True

    try:
        import MetaTrader5 as mt5

        # 显式路径优先，失败则回退自动探测
        ok = False
        if _TERMINAL_PATH and os.path.exists(_TERMINAL_PATH):
            _mt5 = mt5
            ok = mt5.initialize(path=_TERMINAL_PATH)
            if not ok:
                logger.warning("显式路径初始化失败，回退自动探测: %s", mt5.last_error())
        else:
            _mt5 = mt5
        if not ok:
            _mt5 = mt5
            ok = mt5.initialize()
        if not ok:
            _last_error = f"MT5 initialize() 失败: {mt5.last_error()}"
            logger.warning(_last_error)
            # 关键：失败时置空 _mt5，否则后续调用会因 _mt5 非 None 被误判为已连接
            _mt5 = None
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
        _mt5 = None
        _initialized = True
        return False
    except Exception as e:
        _last_error = f"MT5 连接失败: {e}"
        logger.warning(_last_error)
        _mt5 = None
        _initialized = True
        return False


def _resolve_symbol(symbol: str) -> str:
    """解析经纪商实际品种名。

    Exness 等经纪商的品种常带后缀（如 XAUUSDm），裸名 XAUUSD 不存在，
    symbol_info_tick/copy_rates 会静默返回 None。按 原名 → +m → .r → . 顺序探测。
    """
    if _mt5 is None:
        return symbol
    if _mt5.symbol_info(symbol) is not None:
        return symbol
    for suffix in ("m", ".r", "."):
        candidate = f"{symbol}{suffix}"
        if _mt5.symbol_info(candidate) is not None:
            _mt5.symbol_select(candidate, True)  # 确保加入行情窗口
            logger.info("品种 %s 解析为 %s", symbol, candidate)
            return candidate
    return symbol


def _diagnose_empty_data(symbol: str) -> dict:
    """tick / rates 为空时区分真实成因，返回 {"reason": ..., "error": ...}。

    历史问题（2026-09-28 Z11）：`get_tick` 把三种完全不同的成因统一报成
    「请检查品种名称」，导致用户拿不到可行动的结论。三种成因：
      - symbol_not_found: 品种在终端里根本不存在（symbol_info 返回 None）
      - ipc_down:         终端管道失活（连 terminal_info 都返回 None），
                          此时 symbol_info 也会假性返回 None，必须与上一项区分
      - market_closed:    品种存在，但当前无报价 / 无 K 线（休市、未订阅行情）

    注意：`symbol_info` 为 None 可能是「品种不存在」也可能是「IPC 管道死」两种
    完全不同的情况，用 `terminal_info()` 做二次判别（管道死时它同样返回 None）。
    """
    assert _mt5 is not None
    try:
        info = _mt5.symbol_info(symbol)
    except Exception:
        info = None

    if info is None:
        try:
            if _mt5.terminal_info() is None:
                logger.warning("MT5 终端管道失活（symbol_info/terminal_info 均为 None）")
                return {
                    "reason": "ipc_down",
                    "error": (
                        f"MT5 终端连接失活，无法获取 {symbol} 数据"
                        "（IPC 管道已失效，终端可能已重启或掉线）。请检查终端状态后重试"
                    ),
                }
        except Exception:
            return {
                "reason": "ipc_down",
                "error": f"MT5 终端连接异常，无法获取 {symbol} 数据，请检查终端是否运行中",
            }
        logger.warning("symbol_info(%s) 返回 None，品种不存在", symbol)
        return {
            "reason": "symbol_not_found",
            "error": (
                f"品种 {symbol} 不存在（已尝试自动补全 m / .r / . 后缀）。"
                "请在 MT5 终端「市场报价」中确认品种名称并启用该品种"
            ),
        }

    return {
        "reason": "market_closed",
        "error": (
            f"品种 {symbol} 存在，但当前无行情数据"
            "（市场可能休市，或该品种行情未订阅 / 无历史 K 线）"
        ),
    }


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
        return {"success": False, "reason": "ipc_down", "error": _last_error}

    assert _mt5 is not None
    symbol = _resolve_symbol(symbol)
    tick = _mt5.symbol_info_tick(symbol)
    if tick is None:
        logger.warning("symbol_info_tick(%s) 返回 None", symbol)
        # Z11: 不再统一报「请检查品种名称」—— 区分 品种不存在 / IPC 死 / 休市无报价
        # reason 字段供上游与 LLM 判别；success 仍为 False（不擅自改契约）。
        return {"success": False, **_diagnose_empty_data(symbol)}

    info = _mt5.symbol_info(symbol)
    # `symbol_info.spread` 的单位是 **point（整数）**，真实点差价格 = spread × point。
    # ⚠️ 原写法 `info.spread / 10` 是硬编码假设，对 3 位报价品种会**放大 100 倍**
    # （实测 XAUUSDm：spread=260 points、point=0.001 → 真实 0.26，却返回 26.0）。
    # 这里改为按 point 折算，与 `get_tick_stats` 的 `ask - bid` 口径对齐。
    if info is not None and info.point:
        spread = info.spread * info.point
    else:
        spread = tick.ask - tick.bid

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
        return {"success": False, "reason": "ipc_down", "error": _last_error}

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

    symbol = _resolve_symbol(symbol)
    rates = _mt5.copy_rates_from_pos(symbol, mt5_tf, 0, count)
    if rates is None or len(rates) == 0:
        # Z11: 区分「空历史 / 连接死 / 品种不存在」，不再混为一谈
        return {"success": False, **_diagnose_empty_data(symbol)}

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

    symbol = _resolve_symbol(symbol)
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


# ── 时间处理（⚠️ MT5 全链路时间戳均为 UTC） ────────────────────────────────
# MT5 服务端返回的所有时间戳都是 **UTC**（且不带时区标记），而本机在东八区。
# 若直接用本地时间构造查询窗口，会整体偏 8 小时 —— 典型症状是「今天的成交查不到」。
# 因此：查询窗口用 UTC 构造，对外返回的时间统一转成本地（+8）并显式标注。
_LOCAL_TZ = timezone(timedelta(hours=8))


def _fmt_ts(ts, with_tz: bool = True) -> str:
    """MT5 的 UTC 时间戳 → 本地可读字符串（默认标注 UTC+8）。"""
    try:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(_LOCAL_TZ)
        return dt.strftime("%Y-%m-%d %H:%M:%S") + (" UTC+8" if with_tz else "")
    except Exception:
        return ""


def _query_window(days: int):
    """构造 MT5 历史查询窗口（naive UTC）。

    `to` 额外 +12h 余量，覆盖经纪商服务器与本机之间的时钟偏差，
    避免「刚刚发生的成交」因边界问题查不到。
    """
    days = min(max(int(days), 1), 365)
    to = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=12)
    return to - timedelta(days=days), to


# ── 账户详情 ────────────────────────────────────────────────────────────────

def get_account_info() -> dict:
    """获取**完整**账户详情（比 get_connection_status 多出杠杆 / 保证金 / 强平线等）"""
    if not _ensure_mt5():
        return {"success": False, "error": _last_error}

    assert _mt5 is not None
    acc = _mt5.account_info()
    if acc is None:
        return {"success": False, "error": "account_info() 返回 None（终端未登录？）"}

    d = acc._asdict()
    # margin_level 本身就是百分比；**无持仓时 MT5 恒返回 0.0**，此时并无「爆仓」含义
    ml = d.get("margin_level") or 0.0
    return {
        "success": True,
        "login": d.get("login"),
        "name": d.get("name"),
        "server": d.get("server"),
        "company": d.get("company"),
        "currency": d.get("currency"),
        "leverage": d.get("leverage"),
        "balance": d.get("balance"),
        "credit": d.get("credit"),
        "equity": d.get("equity"),
        "profit": d.get("profit"),
        "margin": d.get("margin"),
        "margin_free": d.get("margin_free"),
        "margin_level": ml,
        "margin_level_note": "无持仓时 MT5 恒返回 0.0，无参考意义" if ml == 0 else "",
        "margin_so_call": d.get("margin_so_call"),
        "margin_so_so": d.get("margin_so_so"),
        "margin_initial": d.get("margin_initial"),
        "margin_maintenance": d.get("margin_maintenance"),
        "assets": d.get("assets"),
        "liabilities": d.get("liabilities"),
        "trade_allowed": d.get("trade_allowed"),
        "trade_expert": d.get("trade_expert"),
        "trade_mode": d.get("trade_mode"),
        "margin_mode": d.get("margin_mode"),
        "currency_digits": d.get("currency_digits"),
        "fifo_close": d.get("fifo_close"),
    }


# ── 历史成交 / 历史订单 / 挂单 ───────────────────────────────────────────────

_ENTRY_MAP = {0: "in", 1: "out", 2: "inout", 3: "out_by"}


def get_trade_history(days: int = 7, limit: int = 200) -> dict:
    """获取历史**成交记录**（deals）

    deals 是「实际成交」：开仓 entry=in、平仓 entry=out
    （**只有 out 条目带 profit**，所以 total_profit 与 closed_profit 正常情况下相等）。

    Args:
        days: 回溯天数（1–365，默认 7）
        limit: 最多返回条数（默认 200，超出截断并置 `truncated`）
    """
    if not _ensure_mt5():
        return {"success": False, "error": _last_error}

    assert _mt5 is not None
    days = min(max(int(days), 1), 365)
    frm, to = _query_window(days)
    deals = _mt5.history_deals_get(frm, to)
    if deals is None:
        return {"success": False, "error": f"history_deals_get 失败: {_mt5.last_error()}"}

    rows = []
    for x in deals:
        d = x._asdict()
        rows.append({
            "ticket": d.get("ticket"),
            "order": d.get("order"),
            "time": _fmt_ts(d.get("time")),
            "type": "buy" if d.get("type") == 0 else "sell",
            "entry": _ENTRY_MAP.get(d.get("entry"), str(d.get("entry"))),
            "symbol": d.get("symbol"),
            "volume": d.get("volume"),
            "price": d.get("price"),
            "profit": round(d.get("profit") or 0.0, 2),
            "commission": round(d.get("commission") or 0.0, 2),
            "swap": round(d.get("swap") or 0.0, 2),
            "comment": d.get("comment") or "",
        })

    rows.sort(key=lambda r: r["time"], reverse=True)          # 新的在前
    closed = [r for r in rows if r["entry"] in ("out", "out_by")]
    net = round(sum(r["profit"] + r["commission"] + r["swap"] for r in rows), 2)
    return {
        "success": True,
        "days": days,
        "count": len(rows),
        "returned": min(len(rows), limit),
        "truncated": len(rows) > limit,
        "total_profit": round(sum(r["profit"] for r in rows), 2),
        "closed_profit": round(sum(r["profit"] for r in closed), 2),
        "closed_count": len(closed),
        "net_after_costs": net,
        "deals": rows[:limit],
    }


def get_order_history(days: int = 7, limit: int = 200) -> dict:
    """获取历史**订单**（orders = 委托指令，含开仓/平仓请求与改单记录）

    注意与 deals 的区别：orders 是「委托」，deals（get_trade_history）才是「成交」。
    """
    if not _ensure_mt5():
        return {"success": False, "error": _last_error}

    assert _mt5 is not None
    days = min(max(int(days), 1), 365)
    frm, to = _query_window(days)
    orders = _mt5.history_orders_get(frm, to)
    if orders is None:
        return {"success": False, "error": f"history_orders_get 失败: {_mt5.last_error()}"}

    rows = []
    for o in orders:
        d = o._asdict()
        rows.append({
            "ticket": d.get("ticket"),
            "time_setup": _fmt_ts(d.get("time_setup")),
            "time_done": _fmt_ts(d.get("time_done")) if d.get("time_done") else "",
            "type": d.get("type"),
            "state": d.get("state"),
            "symbol": d.get("symbol"),
            "volume_initial": d.get("volume_initial"),
            "volume_current": d.get("volume_current"),
            "price_open": d.get("price_open"),
            "sl": d.get("sl"),
            "tp": d.get("tp"),
            "price_current": d.get("price_current"),
            "comment": d.get("comment") or "",
        })

    rows.sort(key=lambda r: r["time_setup"], reverse=True)
    return {
        "success": True,
        "days": days,
        "count": len(rows),
        "returned": min(len(rows), limit),
        "truncated": len(rows) > limit,
        "orders": rows[:limit],
    }


def get_pending_orders() -> dict:
    """获取当前**挂单**（尚未成交的委托；不含已成交/已撤销的历史）"""
    if not _ensure_mt5():
        return {"success": False, "error": _last_error}

    assert _mt5 is not None
    orders = _mt5.orders_get()
    if not orders:
        return {"success": True, "count": 0, "orders": [], "message": "当前无挂单"}

    rows = []
    for o in orders:
        rows.append({
            "ticket": o.ticket,
            "symbol": o.symbol,
            "type": o.type,
            "volume": o.volume_current,
            "price_open": o.price_open,
            "price_current": o.price_current,
            "sl": o.sl,
            "tp": o.tp,
            "time_setup": _fmt_ts(o.time_setup),
            "comment": o.comment or "",
        })

    return {"success": True, "count": len(rows), "orders": rows}


def shutdown():
    """关闭 MT5 连接"""
    global _mt5, _initialized
    if _mt5:
        _mt5.shutdown()
        _mt5 = None
        _initialized = False
        logger.info("MT5 connection closed")
