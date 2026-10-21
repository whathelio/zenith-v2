# -*- coding: utf-8 -*-
"""战役级（campaign）交易账本 — 聚合与统计

## 为什么需要这一层

`trade_analytics.py` 以 `position_id` 为交易单元。但该账户是 hedging 模式
（`account.margin_mode = 2`），**每次开仓生成独立 position**，于是：

- 同一次决策里的 5 笔加仓 → 5 个 position → 5 笔「独立交易」
- `trade_analytics.py:162` 的 `add_on = len(ins) > 1` 恒为 False
  → `add_on_ratio_pct` 报 0.0%，而实测真实值约 43%
- 胜率 / 盈亏比 / 持仓时长全部按腿算，不是按决策算

本模块把 deal 先配对成 position，再按「同向 + 间隔 ≤ gap」聚合成 **campaign**
（一次方向性押注），并按腿结构分组归因。

## 核心结论（2026-10-20 实测，30 天 63 个战役）

| 腿结构 | 笔数 | 净盈亏 | 单笔期望 |
|:--|--:|--:|--:|
| 单腿 | 36 | +26.77 | +0.74 |
| 多腿·纯顺势加 | 8 | +134.08 | **+16.76** |
| 多腿·含摊平 | 19 | **−229.07** | **−12.06** |

→ **亏损全部来自摊平；顺势加是唯一强正期望的行为。**
→ 三个 gap（60/120/240）下方向不翻，可作为规则依据。

## 口径声明

1. **gap 是外生参数**，60/120/240 给不同战役数（72/63/56）。引用「多腿占比」必须带 gap。
2. **摊平 vs 加仓**按「新腿价 vs 当时均价」判定，不看盈亏。
3. **不算 R**。R 需要初始风险 = 入场到止损距离，而止损不可观测
   （`orders.sl` 对市价单恒 0，`deals` 只有触发记录）→ 一律用美元期望。
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime

ENTRY_OUT = ("out", "out_by", "inout")


def _epoch(d: dict) -> int:
    """deal 的时间戳。

    ⚠️ 两种形态都要兼容：
    - `get_deals_raw()`（直连 MT5）→ `time` 是 **epoch int**，无 `time_epoch`
    - `/api/mt5/history/deals`（序列化后）→ `time` 是字符串，`time_epoch` 才是 int
    2026-10-20 实测：只读 `time_epoch` 会让 close_epoch 全为 0，战役被误判为「未平仓」。
    """
    v = d.get("time_epoch")
    if v:
        return int(v)
    t = d.get("time")
    if isinstance(t, (int, float)):
        return int(t)
    return 0


def _fmt_epoch(e: int) -> str:
    try:
        return datetime.fromtimestamp(e).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


# ── 第一层：deal → position ────────────────────────────────────────────────
def build_positions(trades: list[dict]) -> list[dict]:
    """按 position_id 配对成持仓。每个 position 一条记录。"""
    groups: dict[int, dict] = {}
    for d in trades:
        pid = int(d.get("position_id") or 0)
        if pid <= 0:
            continue
        g = groups.setdefault(pid, {"in": [], "out": []})
        if d.get("entry") == "in":
            g["in"].append(d)
        elif d.get("entry") in ENTRY_OUT:
            g["out"].append(d)

    out: list[dict] = []
    for pid, g in groups.items():
        ins, outs = g["in"], g["out"]
        if not ins:
            continue          # 窗口起点前已开的仓，无法算生命周期
        open_vol = sum(float(x["volume"]) for x in ins)
        if open_vol <= 0:
            continue
        close_vol = sum(float(x["volume"]) for x in outs)
        out.append({
            "position_id": pid,
            "symbol": ins[0].get("symbol") or "",
            "direction": "buy" if ins[0].get("type") == "buy" else "sell",
            "open_epoch": min(_epoch(x) for x in ins),
            "close_epoch": max((_epoch(x) for x in outs), default=0),
            "open_volume": round(open_vol, 2),
            "close_volume": round(close_vol, 2),
            "entry_price": round(
                sum(float(x["price"]) * float(x["volume"]) for x in ins) / open_vol, 2),
            "exit_price": round(
                sum(float(x["price"]) * float(x["volume"]) for x in outs) / close_vol, 2
            ) if close_vol else 0.0,
            "profit": round(sum(float(x.get("profit") or 0) for x in outs), 2),
            "commission": round(sum(float(x.get("commission") or 0) for x in ins + outs), 2),
            "swap": round(sum(float(x.get("swap") or 0) for x in ins + outs), 2),
            "close_reason": (max(outs, key=_epoch).get("reason_text", "") if outs else ""),
            "status": "closed" if close_vol >= open_vol - 1e-6 else ("partial" if outs else "open"),
            "legs_in": len(ins),
            "legs_out": len(outs),
            "time_local": _fmt_epoch(min(_epoch(x) for x in ins)),
        })
    out.sort(key=lambda x: x["open_epoch"])
    return out


# ── 第二层：position → campaign ────────────────────────────────────────────
def build_campaigns(positions: list[dict], gap_min: int = 120) -> list[dict]:
    """同向 + 相邻开仓间隔 ≤ gap 分钟 → 同一战役。

    实现取简：只看**开仓序列**，不做反向平仓切分（当前账户无反向对冲记录）。
    若后续出现对冲/锁仓，需在此加「反向平仓即断」判定。
    """
    camps: list[dict] = []
    for p in positions:
        prev = camps[-1] if camps else None
        same_dir = prev is not None and prev["direction"] == p["direction"]
        gap_ok = False
        if same_dir:
            gap_ok = (p["open_epoch"] - prev["last_open_epoch"]) / 60.0 <= gap_min
        if same_dir and gap_ok:
            prev["legs"].append(p)
            prev["last_open_epoch"] = p["open_epoch"]
            prev["close_epoch"] = max(prev["close_epoch"], p["close_epoch"])
        else:
            camps.append({
                "direction": p["direction"],
                "symbol": p["symbol"],
                "legs": [p],
                "open_epoch": p["open_epoch"],
                "last_open_epoch": p["open_epoch"],
                "close_epoch": p["close_epoch"],
                "time_local": p["time_local"],
            })
    for c in camps:
        _enrich(c)
    return camps


def _enrich(c: dict) -> None:
    """补齐战役级指标：敞口增长、均价漂移、摊平腿数、持仓时长。"""
    legs = c["legs"]
    total_vol = sum(x["open_volume"] for x in legs)
    first = legs[0]
    c["legs_count"] = len(legs)
    c["total_volume"] = round(total_vol, 2)
    c["profit"] = round(sum(x["profit"] for x in legs), 2)
    c["cost"] = round(sum(x["commission"] + x["swap"] for x in legs), 2)
    c["net"] = round(c["profit"] + c["cost"], 2)
    c["win_legs"] = sum(1 for x in legs if x["profit"] > 0)
    c["loss_legs"] = sum(1 for x in legs if x["profit"] < 0)

    avg = (sum(x["entry_price"] * x["open_volume"] for x in legs) / total_vol) if total_vol else 0.0
    c["avg_entry"] = round(avg, 2)
    c["first_entry"] = first["entry_price"]

    # 均价相对首腿的偏移，按「不利方向」取正
    drift = (avg - first["entry_price"]) if c["direction"] == "sell" else (first["entry_price"] - avg)
    c["avg_price_drift"] = round(drift, 2)      # >0 表示均价被推到了不利方向

    c["exposure_growth"] = round(total_vol / first["open_volume"], 2) if first["open_volume"] else 0.0

    # 逐腿判「摊平 / 顺势加 / 首腿」—— 判据只看价格在均价的哪一侧
    running_vol = 0.0
    running_cost = 0.0
    kinds = []
    for i, x in enumerate(legs):
        if i == 0:
            kinds.append("first")
        else:
            run_avg = running_cost / running_vol if running_vol else x["entry_price"]
            adverse = (x["entry_price"] > run_avg) if c["direction"] == "sell" \
                else (x["entry_price"] < run_avg)
            kinds.append("averaging_down" if adverse else "pyramid")
        running_vol += x["open_volume"]
        running_cost += x["entry_price"] * x["open_volume"]
    c["leg_kinds"] = kinds
    c["averaging_legs"] = sum(1 for k in kinds if k == "averaging_down")
    c["pyramid_legs"] = sum(1 for k in kinds if k == "pyramid")

    c["hold_minutes"] = round((c["close_epoch"] - c["open_epoch"]) / 60.0, 1) if c["close_epoch"] else 0.0
    c["tail_hold_minutes"] = (
        round((c["close_epoch"] - c["last_open_epoch"]) / 60.0, 1) if c["close_epoch"] else 0.0
    )
    c["spread_minutes"] = round((c["last_open_epoch"] - c["open_epoch"]) / 60.0, 1)
    c["close_reasons"] = dict(Counter(x["close_reason"] for x in legs if x["close_reason"]))
    closes = [x["close_epoch"] for x in legs if x["close_epoch"]]
    c["flat_close"] = bool(closes) and (max(closes) - min(closes)) <= 60


# ── 汇总 ──────────────────────────────────────────────────────────────────
def summarize(camps: list[dict]) -> dict:
    closed = [c for c in camps if c["close_epoch"]]
    if not closed:
        return {"campaigns": 0}
    wins = [c for c in closed if c["net"] > 0]
    losses = [c for c in closed if c["net"] < 0]
    gross_win = sum(c["net"] for c in wins)
    gross_loss = sum(c["net"] for c in losses)
    multi = [c for c in closed if c["legs_count"] > 1]
    avg_down = [c for c in closed if c["averaging_legs"] > 0]
    return {
        "campaigns": len(closed),
        "multi_leg": len(multi),
        "multi_leg_pct": round(len(multi) / len(closed) * 100, 1),
        "avg_legs": round(sum(c["legs_count"] for c in closed) / len(closed), 2),
        "with_averaging_down": len(avg_down),
        "averaging_down_pct": round(len(avg_down) / len(closed) * 100, 1),
        "with_pyramid": sum(1 for c in closed if c["pyramid_legs"] > 0),
        "flat_close_pct": round(sum(1 for c in closed if c["flat_close"]) / len(closed) * 100, 1),
        "win_rate_pct": round(len(wins) / len(closed) * 100, 1),
        "net": round(sum(c["net"] for c in closed), 2),
        "gross_win": round(gross_win, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(gross_win / abs(gross_loss), 2) if gross_loss else None,
        "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
        "avg_loss": round(gross_loss / len(losses), 2) if losses else 0.0,
        "max_volume": max(c["total_volume"] for c in closed),
        "max_exposure_growth": max(c["exposure_growth"] for c in closed),
        "legs_dist": dict(Counter(c["legs_count"] for c in closed)),
        "volume_dist": dict(Counter(c["total_volume"] for c in closed)),
        "multi_win_rate_pct": round(
            sum(1 for c in multi if c["net"] > 0) / len(multi) * 100, 1) if multi else None,
        "single_win_rate_pct": round(
            sum(1 for c in closed if c["legs_count"] == 1 and c["net"] > 0)
            / max(1, len(closed) - len(multi)) * 100, 1),
    }


def group_stats(camps: list[dict]) -> dict:
    """按「腿结构」分组，看哪一类才是负期望的来源。

    这是本模块的**主结论**所在：不是「多腿 = 坏」，而是要看多腿里
    有没有摊平腿。纯顺势加（金字塔）与逆势摊平风险方向相反。
    """
    def _bucket(items: list[dict]) -> dict:
        if not items:
            return {"n": 0}
        w = [c for c in items if c["net"] > 0]
        l = [c for c in items if c["net"] < 0]
        gw = sum(c["net"] for c in w)
        gl = sum(c["net"] for c in l)
        return {
            "n": len(items),
            "net": round(sum(c["net"] for c in items), 2),
            "win_rate_pct": round(len(w) / len(items) * 100, 1),
            "avg_win": round(gw / len(w), 2) if w else 0.0,
            "avg_loss": round(gl / len(l), 2) if l else 0.0,
            "expectancy": round(sum(c["net"] for c in items) / len(items), 2),
            "max_loss": round(min((c["net"] for c in items), default=0.0), 2),
            "pf": round(gw / abs(gl), 2) if gl else None,
        }

    closed = [c for c in camps if c["close_epoch"]]
    return {
        "single": _bucket([c for c in closed if c["legs_count"] == 1]),
        "multi_pyramid_only": _bucket(
            [c for c in closed if c["legs_count"] > 1 and c["averaging_legs"] == 0]),
        "multi_with_averaging": _bucket([c for c in closed if c["averaging_legs"] > 0]),
    }


# ── 一站式入口（供脚本与未来工具 handler 共用） ──────────────────────────
def load_deals(days: int = 30, symbol: str = "", date_from=None, date_to=None) -> list[dict]:
    from .mt5_service import get_deals_raw

    r = get_deals_raw(days, date_from, date_to)
    if not r.get("success"):
        raise RuntimeError(f"读取成交失败：{r.get('error')}")
    out = []
    for d in (r.get("deals") or []):
        if not d.get("symbol"):
            continue                       # 资金流水，不是交易
        if d.get("kind") and d.get("kind") != "trade":
            continue
        if symbol and symbol.upper() not in (d.get("symbol") or "").upper():
            continue
        out.append(d)
    return out


def build_campaign_profile(days: int = 30, symbol: str = "", gap_min: int = 120,
                           date_from=None, date_to=None) -> dict:
    """战役级画像（确定性统计，无 LLM）。等同于 summarize + group_stats。"""
    deals = load_deals(days, symbol, date_from, date_to)
    positions = build_positions(deals)
    camps = build_campaigns(positions, gap_min)
    summary = summarize(camps)
    return {
        "success": True,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "gap_minutes": gap_min,
        "symbol": symbol or "all",
        "positions": len(positions),
        "summary": summary,
        "groups": group_stats(camps),
        "campaigns": camps,
        "caveat": (
            "gap 为外生参数，60/120/240 会给出不同战役数；"
            "R 倍数不可算（止损不可观测），一律用美元期望；"
            "分组后单格样本 6–36，只作方向参考"
        ),
    }


# ── 文本格式化 ────────────────────────────────────────────────────────────
def format_summary(s: dict, label: str, gap_min: int) -> str:
    if not s.get("campaigns"):
        return "无已平仓战役"
    return "\n".join([
        f"📊 战役级账本（{label}，gap≤{gap_min}min）",
        f"  战役数 {s['campaigns']} | 平均腿数 {s['avg_legs']} | 多腿战役 "
        f"{s['multi_leg']}（{s['multi_leg_pct']}%）| 腿数分布 {s['legs_dist']}",
        f"  含摊平腿 {s['with_averaging_down']}（{s['averaging_down_pct']}%）| "
        f"含顺势加 {s['with_pyramid']} | 齐平（≤60s）{s['flat_close_pct']}%",
        "",
        f"  净盈亏 {s['net']:+.2f} | 胜率 {s['win_rate_pct']}% | PF "
        f"{s['profit_factor']} | 均盈 {s['avg_win']:+.2f} / 均亏 {s['avg_loss']:+.2f}",
        f"  最大手数 {s['max_volume']} | 最大敞口增长 {s['max_exposure_growth']}× | "
        f"手数分布 {s['volume_dist']}",
        "",
        f"  ⚠️ 对照（同批数据）：单腿战役胜率 {s['single_win_rate_pct']}% vs "
        f"多腿战役胜率 {s['multi_win_rate_pct']}%",
    ])


def format_groups(g: dict) -> str:
    rows = [
        ("单腿（1 笔进场）", g["single"]),
        ("多腿·纯顺势加", g["multi_pyramid_only"]),
        ("多腿·含摊平", g["multi_with_averaging"]),
    ]
    L = ["", "=== 按腿结构分组（哪一类才是负期望来源）===",
         f"{'类别':<18}{'笔数':>5}{'净盈亏':>10}{'胜率':>8}{'均盈':>8}{'均亏':>9}"
         f"{'单笔期望':>10}{'最大单亏':>10}{'PF':>7}"]
    for name, b in rows:
        if not b.get("n"):
            L.append(f"{name:<18}{0:>5}{'—':>10}")
            continue
        L.append(
            f"{name:<18}{b['n']:>5}{b['net']:>+10.2f}{b['win_rate_pct']:>7.1f}%"
            f"{b['avg_win']:>+8.2f}{b['avg_loss']:>+9.2f}"
            f"{b['expectancy']:>+10.2f}{b['max_loss']:>+10.2f}"
            f"{(b['pf'] if b['pf'] is not None else 0):>7.2f}")
    buckets = [(n, b) for n, b in rows if b.get("n")]
    if buckets:
        worst_name, worst = min(buckets, key=lambda x: x[1]["expectancy"])
        L.append(f"\n  期望最低的一类是「{worst_name}」：单笔期望 {worst['expectancy']:+.2f}")
    L.append("  ⚠️ 样本为已平仓战役，分组后每格 6–36 个，结论只能作方向参考。")
    return "\n".join(L)
