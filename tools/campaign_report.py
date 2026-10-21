#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""交易「战役级」账本 — 命令行入口（不依赖 Zenith 服务是否重启）

聚合逻辑在 `backend/campaign_analytics.py`（未来接入对话工具链时共用同一份）。
本文件只做 CLI。

## 为什么需要这一层

`trade_analytics.py` 以 `position_id` 为交易单元。但该账户是 hedging 模式
（`margin_mode=2`），每次开仓生成独立 position，于是同一次决策里的 5 笔加仓
会变成 5 笔「独立交易」，`add_on` 恒为 False → 加仓率永远 0.0%。

## 与 trade_report.py 的分工

| 脚本 | 单元 | 看什么 |
|:--|:--|:--|
| `tools/trade_report.py` | position | 与 `/api/trade/profile` 同口径，兼容既有结论 |
| `tools/campaign_report.py` | campaign | **决策质量**：摊平 / 顺势 / 敞口增长 |

## 用法

    # 近 30 天，默认 gap=120 分钟
    .venv/Scripts/python.exe tools/campaign_report.py --days 30

    # 阈值敏感性：同一批数据换三个 gap，看结论方向是否稳定
    .venv/Scripts/python.exe tools/campaign_report.py --days 30 --sweep

    # 逐战役明细
    .venv/Scripts/python.exe tools/campaign_report.py --days 30 -v

    # 原始 JSON
    .venv/Scripts/python.exe tools/campaign_report.py --days 30 --json

## 口径声明（结论必须连口径一起引用）

1. **gap 是外生参数**。60/120/240 分钟会给出不同战役数，引用占比必须带 gap 值。
2. **摊平 vs 加仓**按「新腿价 vs 当时均价」判定，不看盈亏。
3. **不算 R 倍数**。R 需要入场到止损的距离，而该账户止损不可观测
   （`orders.sl` 对市价单恒 0，`deals` 只有 `reason=4` 的触发记录）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main() -> int:
    ap = argparse.ArgumentParser(description="MT5 战役级账本（直连终端，不依赖 Zenith 服务）")
    ap.add_argument("--from", dest="date_from", default="")
    ap.add_argument("--to", dest="date_to", default="")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--gap", type=int, default=120, help="同一战役的最大开仓间隔（分钟）")
    ap.add_argument("--sweep", action="store_true", help="跑 60/120/240 三档看稳定性")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true", help="逐战役明细")
    args = ap.parse_args()

    from backend.campaign_analytics import (
        build_campaigns, build_positions, format_groups, format_summary,
        group_stats, load_deals, summarize,
    )

    try:
        deals = load_deals(args.days, args.symbol, args.date_from or None, args.date_to or None)
    except RuntimeError as e:
        print(str(e))
        return 1
    positions = build_positions(deals)
    if not positions:
        print("该区间没有可配对的持仓")
        return 0

    label = (f"{args.date_from}~{args.date_to}" if args.date_from
             else f"近 {args.days} 天")
    print(f"配对持仓 {len(positions)} 个（{args.symbol or '全部品种'}）\n")

    if args.sweep:
        print("=== 阈值敏感性（gap 是外生参数，不是真理）===")
        for g in (60, 120, 240):
            cs = build_campaigns(positions, g)
            s = summarize(cs)
            gg = group_stats(cs)
            print(f"  gap≤{g:>3}min → 战役 {s['campaigns']:>3} | 平均腿数 {s['avg_legs']} | "
                  f"多腿 {s['multi_leg_pct']}% | 含摊平 {s['averaging_down_pct']}% | "
                  f"胜率 {s['win_rate_pct']}% | 净 {s['net']:+.2f}")
            for key, name in (("multi_pyramid_only", "纯顺势加"),
                              ("multi_with_averaging", "含摊平")):
                b = gg[key]
                if b.get("n"):
                    print(f"                        {name}：{b['n']:>2} 笔 净 {b['net']:+.2f} "
                          f"期望 {b['expectancy']:+.2f}")
        print("\n  结论只取「方向是否稳定」，具体数字须带 gap 值引用。\n")

    camps = build_campaigns(positions, args.gap)
    s = summarize(camps)
    g = group_stats(camps)

    if args.json:
        print(json.dumps({"gap_minutes": args.gap, "summary": s, "groups": g,
                          "campaigns": camps}, ensure_ascii=False, indent=2, default=str))
        return 0

    print(format_summary(s, label, args.gap))
    print(format_groups(g))

    if args.verbose:
        print("\n=== 逐战役（最近 20）===")
        for c in camps[-20:]:
            kinds = "/".join(c["leg_kinds"])
            print(f"  {c['time_local'][:16]}  {c['direction']:4s} "
                  f"腿{c['legs_count']} 手{c['total_volume']:.2f} "
                  f"均价{c['avg_entry']:.2f} 漂移{c['avg_price_drift']:+.2f} "
                  f"敞口{c['exposure_growth']:.1f}×  净{c['net']:+.2f} "
                  f"持{c['hold_minutes']:.0f}m  尾{c['tail_hold_minutes']:.0f}m  [{kinds}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
