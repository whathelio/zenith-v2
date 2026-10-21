#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MT5 交易复盘 — 命令行入口（不依赖 Zenith 服务是否重启）

为什么要这个脚本：Zenith 的 `start.py` 是 `reload=False`，改完代码必须重启
才生效。但排查/复盘往往等不了重启 —— 这个脚本直接调用 backend 模块，
自己连 MT5 IPC，随时可用。

用法：
    # 全历史（最多回溯 10 年）
    python tools/trade_report.py

    # 指定年份 / 任意区间
    python tools/trade_report.py --from 2025-01-01 --to 2025-12-31

    # 最近 90 天，跳过行情对照（更快）
    python tools/trade_report.py --days 90 --no-market

    # 先看这个账户到底能读到多早
    python tools/trade_report.py --span

    # 原始 JSON（给下游脚本/图表用）
    python tools/trade_report.py --from 2025-01-01 --json > 2025.json

    # 加多角色复盘（3 次 LLM 调用，20–90 秒）
    python tools/trade_report.py --from 2025-01-01 --review
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows 控制台默认 GBK，直接 print 中文/emoji 会 UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _print_span() -> int:
    from backend.mt5_service import get_history_span

    r = get_history_span()
    if not r.get("success"):
        print(f"读取失败：{r.get('error')}")
        return 1
    if r.get("count", 0) == 0:
        print(r.get("note", "该账户没有可读历史"))
        return 0
    print("该账户在 MT5 终端里可读的历史范围：")
    print(f"  最早：{r['earliest']}")
    print(f"  最晚：{r['latest']}")
    print(f"  成交条数：{r['count']}")
    print(f"  首次资金流水：{r.get('funded_from') or '无'}")
    print(f"  说明：{r.get('note', '')}")
    return 0


def _print_review(r: dict) -> None:
    if not r.get("success"):
        print(f"复盘失败：{r.get('error')}")
        return
    roles = r.get("roles") or {}
    beh = (roles.get("behavior") or {}).get("data") or {}
    mkt = (roles.get("market") or {}).get("data") or {}
    aud = (roles.get("auditor") or {}).get("data") or {}

    if beh:
        print("\n【行为分析师】")
        for h in beh.get("habits") or []:
            print(f"  · {h.get('tag')}：{h.get('detail')}（{h.get('evidence_text')}）")
        for s in beh.get("strengths") or []:
            print(f"  + {s.get('tag')}（{s.get('evidence_text')}）")
    if mkt:
        print("\n【行情对照官】")
        for o in mkt.get("observations") or []:
            print(f"  · {o.get('tag')}：{o.get('detail')}（{o.get('evidence_text')}）")
        if mkt.get("regime_fit"):
            print(f"  小结：{mkt['regime_fit']}")
    if aud:
        print(f"\n【风控审计官】置信度 {aud.get('overall_confidence')}")
        for c in aud.get("reliable_conclusions") or []:
            print(f"  ✓ {c.get('tag')}：{c.get('detail')}（{c.get('evidence_text')}）")
        for c in aud.get("rejected_conclusions") or []:
            print(f"  ✗ 驳回 {c.get('tag')}：{c.get('reason')}")
        if aud.get("audit_note"):
            print(f"  备注：{aud['audit_note']}")
    meta = r.get("meta") or {}
    print(f"\n协同模式：{meta.get('note')}（耗时 {meta.get('elapsed_ms')} ms）")


def main() -> int:
    ap = argparse.ArgumentParser(description="MT5 交易复盘（直连终端，不依赖 Zenith 服务）")
    ap.add_argument("--from", dest="date_from", default="", help="起始日期，如 2025-01-01")
    ap.add_argument("--to", dest="date_to", default="", help="结束日期，如 2025-12-31（含当天）")
    ap.add_argument("--days", type=int, default=3650, help="回溯天数（无 --from 时生效，默认 3650）")
    ap.add_argument("--symbol", default="", help="品种过滤，如 XAUUSD")
    ap.add_argument("--json", action="store_true", help="输出原始 JSON")
    ap.add_argument("--review", action="store_true", help="追加多角色复盘（较慢）")
    ap.add_argument("--no-market", action="store_true", help="跳过行情对照（更快）")
    ap.add_argument("--span", action="store_true", help="只查该账户可读的历史范围")
    args = ap.parse_args()

    if args.span:
        return _print_span()

    from backend.trade_analytics import build_profile, format_profile

    profile = build_profile(
        days=args.days,
        symbol=args.symbol,
        include_market=not args.no_market,
        date_from=args.date_from or None,
        date_to=args.date_to or None,
    )
    if not profile.get("success"):
        print(f"读取失败：{profile.get('error')}")
        return 1

    if args.json:
        print(json.dumps(profile, ensure_ascii=False, indent=2))
    else:
        print(format_profile(profile))

    if args.review:
        from backend.trade_review import run_review

        review = asyncio.run(run_review(
            days=args.days,
            symbol=args.symbol,
            date_from=args.date_from or None,
            date_to=args.date_to or None,
            profile=profile,
        ))
        _print_review(review)
    return 0


if __name__ == "__main__":
    sys.exit(main())
