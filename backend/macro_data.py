"""Zenith v2 宏观指标数据服务 — yfinance + web_search 结构化提取"""
from __future__ import annotations

import json
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional

import httpx

logger = logging.getLogger("zenith.macro")

# ---------------------------------------------------------------------------
# yfinance Sync Wrappers (run in thread)
# ---------------------------------------------------------------------------

def _yf_fetch_price_sync(ticker: str, period: str = "5d") -> Optional[dict]:
    """同步获取 yfinance 价格数据"""
    import yfinance as yf
    try:
        data = yf.download(ticker, period=period, interval="1d", progress=False)
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
        logger.warning(f"yfinance fetch failed for {ticker}: {e}")
        return None


# ---------------------------------------------------------------------------
# MacroDataService
# ---------------------------------------------------------------------------

class MacroDataService:
    """宏观指标获取服务 — DXY, 美债收益率, 原油, 黄金, SPDR ETF 等"""

    # yfinance ticker 映射
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

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=30)

    async def close(self):
        await self._client.aclose()

    # -- Individual Fetchers -----------------------------------------------

    async def fetch_gold(self) -> dict:
        """获取现货黄金价格"""
        data = await asyncio.to_thread(_yf_fetch_price_sync, "GC=F", "5d")
        if data:
            return {"indicator": "gold", **data, "source": "yfinance"}
        return {"indicator": "gold", "value": "", "change_pct": "", "source": "failed"}

    async def fetch_dxy(self) -> dict:
        """获取美元指数"""
        data = await asyncio.to_thread(_yf_fetch_price_sync, "DX-Y.NYB", "5d")
        if data:
            return {"indicator": "dxy", **data, "source": "yfinance"}
        return {"indicator": "dxy", "value": "", "change_pct": "", "source": "failed"}

    async def fetch_yields(self) -> list[dict]:
        """获取美债收益率 (2Y + 10Y)"""
        results = []
        for name, ticker in [("10y_yield", "^TNX"), ("2y_yield", "^IRX")]:
            data = await asyncio.to_thread(_yf_fetch_price_sync, ticker, "5d")
            if data:
                results.append({"indicator": name, **data, "source": "yfinance"})
            else:
                results.append({"indicator": name, "value": "", "change_pct": "", "source": "failed"})
        return results

    async def fetch_oil(self) -> list[dict]:
        """获取原油价格 (WTI + Brent)"""
        results = []
        for name, ticker in [("wti", "CL=F"), ("brent", "BZ=F")]:
            data = await asyncio.to_thread(_yf_fetch_price_sync, ticker, "5d")
            if data:
                results.append({"indicator": name, **data, "source": "yfinance"})
            else:
                results.append({"indicator": name, "value": "", "change_pct": "", "source": "failed"})
        return results

    async def fetch_all_yf_indicators(self) -> list[dict]:
        """一键拉取所有 yfinance 覆盖的指标"""
        results = []
        tasks = []
        for name, ticker in self.INDICATOR_TICKERS.items():
            tasks.append(asyncio.to_thread(_yf_fetch_price_sync, ticker, "5d"))

        raw = await asyncio.gather(*tasks, return_exceptions=True)

        for i, (name, ticker) in enumerate(self.INDICATOR_TICKERS.items()):
            r = raw[i]
            if isinstance(r, Exception):
                logger.warning(f"Indicator {name} failed: {r}")
                results.append({"indicator": name, "value": "", "change_pct": "", "source": "failed"})
            elif r is None:
                results.append({"indicator": name, "value": "", "change_pct": "", "source": "no_data"})
            else:
                results.append({"indicator": name, **r, "source": "yfinance"})
        return results

    # -- Web Search for Events / Qualitative Data ---------------------------

    async def fetch_events_today(self) -> list[dict]:
        """获取今日财经事件（逾期/未达分类）"""
        from .web_tools import web_search

        today = datetime.now().strftime('%Y-%m-%d')
        overdue_events = []
        upcoming_events = []

        try:
            # 搜索今日财经日历
            results = await asyncio.to_thread(
                web_search,
                f"economic calendar {today} important data releases Fed",
                count=5
            )
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

        # 搜索昨日重大事件（逾期）
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        try:
            results = await asyncio.to_thread(
                web_search,
                f"important economic data released {yesterday} gold impact",
                count=5
            )
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

        # 1. yfinance 指标
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

        # 3. 事件
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
