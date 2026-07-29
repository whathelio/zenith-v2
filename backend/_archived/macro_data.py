"""Zenith v2 宏观指标数据服务 — 已封存 (SEALED)

本模块随 market_analyzer.py 一起封存。Jin10 数据源本身可用，但市场分析整体功能已禁用。
数据链路保留在代码中，供未来恢复时参考。"""
from __future__ import annotations

import json
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional

import httpx

logger = logging.getLogger("zenith.macro")

# ---------------------------------------------------------------------------
# yfinance Sync Wrappers (run in thread, as fallback)
# ---------------------------------------------------------------------------

def _yf_fetch_price_sync(ticker: str, period: str = "5d") -> Optional[dict]:
    """同步获取 yfinance 价格数据，单个 ticker 失败返回 None 不抛异常"""
    import yfinance as yf
    try:
        import pandas as pd  # 确保 pandas 可用
        data = yf.download(ticker, period=period, interval="1d", progress=False, auto_adjust=False)
        if data is None or len(data) < 2:
            return None
        if isinstance(data.columns, pd.MultiIndex):
            close = data[('Close', ticker)]
        else:
            close = data['Close']
        close = close.dropna()
        if len(close) < 2:
            return None
        current = float(close.iloc[-1])
        prev = float(close.iloc[-2])
        change_pct = round((current / prev - 1) * 100, 2)
        return {"value": round(current, 4), "change_pct": change_pct, "prev": round(prev, 4)}
    except Exception as e:
        logger.debug(f"yfinance fetch failed for {ticker}: {e}")
        return None


# ---------------------------------------------------------------------------
# MacroDataService
# ---------------------------------------------------------------------------

class MacroDataService:
    """宏观指标获取服务 — Jin10(主) + yfinance(降级) + web_search(降级)"""

    # yfinance ticker 映射 (降级回退用)
    INDICATOR_TICKERS = {
        "gold": "GC=F",
        "dxy": "DX-Y.NYB",
        "10y_yield": "^TNX",
        "2y_yield": "^IRX",
        "wti": "CL=F",
        "brent": "BZ=F",
        "silver": "SI=F",
        "copper": "HG=F",
        "sp500": "^GSPC",
        "nasdaq": "^NDX",
        "usd_cny": "CNY=X",
    }

    # 金十不覆盖的指标（仍需 yfinance）
    YF_ONLY_INDICATORS = {"dxy", "10y_yield", "2y_yield", "sp500", "nasdaq"}

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=30)
        self._jin10 = None  # 懒加载

    def _get_jin10(self):
        """懒加载金十服务"""
        if self._jin10 is None:
            from .jin10_service import get_jin10_service
            self._jin10 = get_jin10_service()
        return self._jin10

    async def close(self):
        await self._client.aclose()
        if self._jin10:
            await self._jin10.close()

    # -- Individual Fetchers (金十优先 + yfinance 降级) ----------------------

    async def fetch_all_yf_indicators(self) -> list[dict]:
        """一键拉取所有指标 — 金十优先，yfinance 降级回退

        金十覆盖: gold, silver, wti, brent, copper, usd_cny
        yfinance 独占: dxy, 10y_yield, 2y_yield, sp500, nasdaq
        """
        jin10 = self._get_jin10()
        results = []

        # 1. 金十行情指标
        jin10_results = await jin10.fetch_quote_indicators()
        jin10_names = {r["indicator"] for r in jin10_results}
        results.extend(jin10_results)

        # 2. yfinance 独占指标 + 金十获取失败的指标
        yf_needed = set(self.YF_ONLY_INDICATORS)
        # 金十失败的也加入 yfinance 回退列表
        for name in self.INDICATOR_TICKERS:
            if name not in jin10_names and name not in yf_needed:
                yf_needed.add(name)

        if yf_needed:
            tasks = []
            tickers = []
            for name in yf_needed:
                ticker = self.INDICATOR_TICKERS[name]
                tasks.append(asyncio.to_thread(_yf_fetch_price_sync, ticker, "5d"))
                tickers.append((name, ticker))

            raw = await asyncio.gather(*tasks, return_exceptions=True)

            for i, (name, ticker) in enumerate(tickers):
                r = raw[i]
                if isinstance(r, Exception):
                    logger.warning(f"Indicator {name} yfinance failed: {r}")
                    results.append({"indicator": name, "value": "", "change_pct": "", "source": "failed"})
                elif r is None:
                    results.append({"indicator": name, "value": "", "change_pct": "", "source": "no_data"})
                else:
                    results.append({"indicator": name, **r, "source": "yfinance"})

        # 3. 补充金十原始数据到 jin10_raw 字段（供 market_analyzer 使用）
        #    对已有金十指标，额外获取 K 线趋势数据
        jin10_kline_map = {
            "gold": "XAUUSD", "silver": "XAGUSD", "wti": "USOIL",
            "brent": "UKOIL", "copper": "COPPER",
        }
        kline_tasks = []
        kline_names = []
        for ind_name, code in jin10_kline_map.items():
            if ind_name in jin10_names:
                kline_tasks.append(jin10.get_kline(code, count=20))
                kline_names.append(ind_name)

        if kline_tasks:
            kline_raw = await asyncio.gather(*kline_tasks, return_exceptions=True)
            for i, ind_name in enumerate(kline_names):
                kline_data = kline_raw[i]
                if isinstance(kline_data, Exception) or kline_data is None:
                    continue
                # 把 K 线数据附加到对应指标
                for r in results:
                    if r.get("indicator") == ind_name and r.get("source") == "jin10":
                        r["kline"] = kline_data.get("klines", [])

        return results

    # -- 财经事件 (金十日历优先 + web_search 降级) ---------------------------

    async def fetch_events_today(self) -> list[dict]:
        """获取今日财经事件 — 金十日历优先，web_search 降级回退

        金十日历返回结构化数据: pub_time, star(星级), title, previous,
        consensus, actual, affect_txt(利多/利空/中性)
        """
        jin10 = self._get_jin10()

        # 1. 金十财经日历
        jin10_events = await jin10.fetch_calendar_events()
        if jin10_events is not None and len(jin10_events) > 0:
            logger.info(f"金十财经日历获取成功: {len(jin10_events)} 条事件")
            # 补充金十快讯
            flash_news = await jin10.fetch_flash_news(["黄金", "美联储", "原油"])
            if flash_news:
                logger.info(f"金十快讯获取成功: {len(flash_news)} 条")
            return jin10_events

        # 2. 金十失败 → 降级到 web_search
        logger.warning("金十财经日历获取失败，降级到 web_search")
        return await self._fetch_events_via_search()

    async def _fetch_events_via_search(self) -> list[dict]:
        """web_search 降级获取财经事件"""
        from .web_tools import web_search

        today = datetime.now().strftime('%Y-%m-%d')
        overdue_events = []
        upcoming_events = []

        try:
            raw = await web_search(
                f"economic calendar {today} important data releases Fed",
                max_results=5
            )
            results = raw.get('results', []) if isinstance(raw, dict) else []
            for item in results:
                event = {
                    'name': item.get('title', ''),
                    'time': item.get('time', ''),
                    'source': item.get('url', ''),
                    'overdue': False,
                }
                upcoming_events.append(event)
        except Exception as e:
            logger.warning(f"Event search failed: {e}")

        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        try:
            raw = await web_search(
                f"important economic data released {yesterday} gold impact",
                max_results=5
            )
            results = raw.get('results', []) if isinstance(raw, dict) else []
            for item in results:
                event = {
                    'name': item.get('title', ''),
                    'time': yesterday,
                    'source': item.get('url', ''),
                    'overdue': True,
                }
                overdue_events.append(event)
        except Exception as e:
            logger.warning(f"Overdue event search failed: {e}")

        return overdue_events + upcoming_events

    # -- Combined Fetch ----------------------------------------------------

    async def fetch_all_indicators(self) -> dict:
        """一键拉取所有指标 + 事件 → 写入 macro_indicators 表"""
        from .database import macro_indicator_add

        # 1. 指标 (金十 + yfinance)
        indicators = await self.fetch_all_yf_indicators()

        # 2. 存入数据库
        for ind in indicators:
            if ind.get('value') and ind['value'] != '':
                macro_indicator_add(
                    indicator=ind['indicator'],
                    value=str(ind['value']),
                    change_pct=str(ind.get('change_pct', '')),
                    source=ind.get('source', ''),
                )

        # 3. 事件 (金十日历 + 快讯 / web_search 降级)
        events = await self.fetch_events_today()

        return {
            'indicators': indicators,
            'events': events,
            'fetch_time': datetime.now().isoformat(),
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_macro_service: Optional[MacroDataService] = None


def get_macro_service() -> MacroDataService:
    global _macro_service
    if _macro_service is None:
        _macro_service = MacroDataService()
    return _macro_service
