"""Zenith v2 CFTC 持仓分析服务 — 异步 + 缓存 + JSON 输出
重构自 cftc_持仓分析.py (JPM Delta-One Table 12 复刻)
"""
from __future__ import annotations

import json
import logging
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import httpx
import yaml

logger = logging.getLogger("zenith.cftc")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PROJECT_DIR = Path(__file__).parent.parent
CONTRACTS_YAML = PROJECT_DIR / "config" / "cftc_contracts.yaml"


def _load_contracts_config() -> dict:
    """从 YAML 加载合约配置"""
    if CONTRACTS_YAML.exists():
        with open(CONTRACTS_YAML, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    # fallback: 硬编码最小配置
    return {
        "cftc_tff_url": "https://publicreporting.cftc.gov/resource/gpe5-46if.json",
        "cftc_disagg_url": "https://publicreporting.cftc.gov/resource/72hh-3qpy.json",
        "tff": [
            {"name": "黄金", "cftc": "GOLD - COMMODITY", "section": "金属", "yf": "GC=F"},
        ],
        "disagg": [
            {"name": "黄金", "cftc": "GOLD - COMMODITY", "section": "金属", "yf": "GC=F"},
        ],
    }


# ---------------------------------------------------------------------------
# Data Fetching (async)
# ---------------------------------------------------------------------------

async def _fetch_cftc_async(endpoint: str, start_date: str, limit: int = 50000) -> pd.DataFrame:
    """从 CFTC Socrata API 获取数据 (httpx 异步 + 重试)"""
    params = {
        "$where": f"report_date_as_yyyy_mm_dd >= '{start_date}'",
        "$limit": str(limit),
        "$order": "report_date_as_yyyy_mm_dd ASC",
    }
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.get(endpoint, params=params)
                resp.raise_for_status()
                data = resp.json()
                break
        except (httpx.ConnectError, httpx.ReadError) as e:
            if attempt < 2:
                logger.warning(f"CFTC fetch retry {attempt+1}: {e}")
                await asyncio.sleep(3)
            else:
                raise

    df = pd.DataFrame(data)
    if df.empty:
        return df

    skip_cols = {
        'market_and_exchange_names', 'report_date_as_yyyy_mm_dd',
        'cftc_contract_market_code', 'cftc_market_code', 'cftc_commodity_code',
        'cftc_region_code', 'cftc_subgroup_code', 'contract_market_name',
        'contract_units', 'futonly_or_combined', 'id', 'commodity',
        'commodity_group_name', 'commodity_name', 'commodity_subgroup_name',
        'report_date_as_mm_dd_yyyy', 'yyyy_report_week_ww',
    }
    for col in df.columns:
        if col not in skip_cols:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    df['report_date'] = pd.to_datetime(df['report_date_as_yyyy_mm_dd'])
    return df


def _match_cftc(df: pd.DataFrame, search_pattern: str) -> Optional[pd.DataFrame]:
    """在 CFTC 数据中按名称匹配合约 (三级匹配)"""
    names_upper = df['market_and_exchange_names'].str.upper()
    pattern_upper = search_pattern.upper()

    mask = names_upper == pattern_upper
    if not mask.any():
        mask = names_upper.str.startswith(pattern_upper, na=False)
    if not mask.any():
        mask = df['market_and_exchange_names'].str.contains(search_pattern, case=False, na=False)

    matched = df[mask].copy()
    if matched.empty:
        return None

    # 去重：同一合约名多个子合约时选 OI 最大者
    if matched['market_and_exchange_names'].nunique() > 1:
        names = matched['market_and_exchange_names'].unique()
        for n in names:
            if 'Consolidated' in n:
                matched = matched[matched['market_and_exchange_names'] == n]
                break
        else:
            avg_oi = matched.groupby('market_and_exchange_names')['open_interest_all'].mean()
            matched = matched[matched['market_and_exchange_names'] == avg_oi.idxmax()]

    if 'cftc_contract_market_code' in matched.columns and matched['cftc_contract_market_code'].nunique() > 1:
        avg_oi = matched.groupby('cftc_contract_market_code')['open_interest_all'].mean()
        matched = matched[matched['cftc_contract_market_code'] == avg_oi.idxmax()]

    return matched.sort_values('report_date').reset_index(drop=True)


# ---------------------------------------------------------------------------
# Analysis Calculations
# ---------------------------------------------------------------------------

def _calc_zscore(series: pd.Series, window: int = 156) -> float:
    s = series.dropna()
    if len(s) < 10:
        return np.nan
    tail = s.tail(window)
    mean, std = tail.mean(), tail.std()
    if std == 0 or np.isnan(std):
        return 0.0
    return round((s.iloc[-1] - mean) / std, 1)


def _calc_change_zscore(series: pd.Series, window: int = 156) -> float:
    changes = series.diff().dropna()
    if len(changes) < 10:
        return np.nan
    tail = changes.tail(window)
    mean, std = tail.mean(), tail.std()
    if std == 0 or np.isnan(std):
        return 0.0
    return round((changes.iloc[-1] - mean) / std, 1)


def _pos_group(matched: pd.DataFrame, long_col: str, short_col: str,
               zscore_window: int = 156) -> dict:
    """计算一组持仓的 net/long/short position, z-score, w/w change, flow state"""
    long_s = matched[long_col].fillna(0)
    short_s = matched[short_col].fillna(0)
    net_s = long_s - short_s
    oi = matched['open_interest_all'].fillna(0).replace(0, np.nan)

    long_oi = long_s / oi
    short_oi = short_s / oi
    net_oi = net_s / oi

    latest_long = float(long_s.iloc[-1])
    latest_short = float(short_s.iloc[-1])
    latest_net = latest_long - latest_short

    z_dlong = _calc_change_zscore(long_s, zscore_window)
    z_dshort = _calc_change_zscore(short_s, zscore_window)

    return {
        'net': int(latest_net),
        'net_z': _calc_zscore(net_oi, zscore_window),
        'net_ww': int(net_s.diff().iloc[-1]) if len(net_s) > 1 else 0,
        'net_ww_z': _calc_change_zscore(net_s, zscore_window),
        'long': int(latest_long),
        'long_z': _calc_zscore(long_oi, zscore_window),
        'long_ww': int(long_s.diff().iloc[-1]) if len(long_s) > 1 else 0,
        'long_ww_z': z_dlong,
        'short': int(latest_short),
        'short_z': _calc_zscore(short_oi, zscore_window),
        'short_ww': int(short_s.diff().iloc[-1]) if len(short_s) > 1 else 0,
        'short_ww_z': z_dshort,
        'flow_state': _flow_state(z_dlong, z_dshort),
    }


def _flow_state(z_dlong, z_dshort) -> str:
    """根据多空变化 z-score 判定 flow state"""
    if z_dlong is None or z_dshort is None:
        return ''
    if isinstance(z_dlong, float) and np.isnan(z_dlong):
        return ''
    if isinstance(z_dshort, float) and np.isnan(z_dshort):
        return ''
    zl, zs = float(z_dlong), float(z_dshort)

    if zl >= 0.8 and zs <= -0.8:
        return '多头挤压'
    if zl <= -0.8 and zs >= 0.8:
        return '空头施压'
    if zl >= 0.8 and zs >= 0.8:
        return '多空双增'
    if zl <= -0.8 and zs <= -0.8:
        return '多空双减'
    if zl >= 0.8 and abs(zs) < 0.5:
        return '多头建仓'
    if zs <= -0.8 and abs(zl) < 0.5:
        return '空头回补'
    if zs >= 0.8 and abs(zl) < 0.5:
        return '空头建仓'
    if zl <= -0.8 and abs(zs) < 0.5:
        return '多头平仓'
    return ''


def _crowding_label(net_z, long_z=None, short_z=None) -> str:
    """根据 net/long/short z-score 判定拥挤度标签"""
    def _safe(v):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return 0.0
        return float(v)
    nz, lz, sz = _safe(net_z), _safe(long_z), _safe(short_z)
    if nz >= 2.0 or lz >= 2.0:
        return '极端多头' if nz >= 2.75 or lz >= 2.75 else '拥挤多头'
    if nz <= -2.0 or sz >= 2.0:
        return '极端空头' if nz <= -2.75 or sz >= 2.75 else '拥挤空头'
    return '正常'


def _is_divergence(flow_state: str, price_chg: Optional[float]) -> bool:
    """判断动作和价格是否背离"""
    if not flow_state or price_chg is None:
        return False
    if isinstance(price_chg, float) and np.isnan(price_chg):
        return False
    bull = {'多头建仓', '空头回补', '多头挤压'}
    bear = {'空头建仓', '多头平仓', '空头施压'}
    if flow_state in bull and price_chg < -0.05:
        return True
    if flow_state in bear and price_chg > 0.05:
        return True
    return False


# ---------------------------------------------------------------------------
# Price Data (yfinance via thread wrapper)
# ---------------------------------------------------------------------------

def _fetch_tue_tue_returns_sync(contracts: list[dict], cftc_date: str) -> dict:
    """同步获取 CFTC 同期 Tue→Tue 价格变动"""
    import yfinance as yf

    results = {}
    tue_end = pd.Timestamp(cftc_date)
    tue_start = tue_end - timedelta(days=7)
    fetch_start = (tue_start - timedelta(days=5)).strftime('%Y-%m-%d')
    fetch_end = (tue_end + timedelta(days=3)).strftime('%Y-%m-%d')

    tickers = {c['name']: c.get('yf') for c in contracts if c.get('yf')}
    for name, ticker in tickers.items():
        for attempt in range(3):
            try:
                data = yf.download(ticker, start=fetch_start, end=fetch_end,
                                   interval='1d', progress=False)
                if data is None or len(data) < 2:
                    break
                if isinstance(data.columns, pd.MultiIndex):
                    close = data[('Close', ticker)]
                else:
                    close = data['Close']
                close = close.dropna()
                px_end = close[close.index <= tue_end]
                px_start = close[close.index <= tue_start]
                if len(px_end) > 0 and len(px_start) > 0:
                    p1 = float(px_start.iloc[-1])
                    p2 = float(px_end.iloc[-1])
                    ret = (p2 / p1 - 1) * 100
                    results[name] = {
                        'ret': round(ret, 2),
                        'ticker': ticker,
                        'px_start': p1,
                        'px_end': p2,
                    }
                break
            except Exception as e:
                logger.warning(f"yfinance error for {name}/{ticker}: {e}")
                if attempt < 2:
                    import time; time.sleep(2)
    return results


# ---------------------------------------------------------------------------
# CFTCService Class
# ---------------------------------------------------------------------------

class CFTCService:
    """CFTC 持仓分析异步服务 — 增量缓存 + JSON 输出 + 黄金专项"""

    def __init__(self, zscore_window: int = 156, cache_days: int = 1200):
        self._cfg = _load_contracts_config()
        self._tff_url = self._cfg.get("cftc_tff_url", "")
        self._disagg_url = self._cfg.get("cftc_disagg_url", "")
        self._tff_contracts = self._cfg.get("tff", [])
        self._disagg_contracts = self._cfg.get("disagg", [])
        self._zscore_window = zscore_window
        self._cache_days = cache_days
        self._df_tff: Optional[pd.DataFrame] = None
        self._df_disagg: Optional[pd.DataFrame] = None
        self._price_data: Optional[dict] = None
        self._report_date: Optional[str] = None

    # -- Data Loading ------------------------------------------------------

    async def fetch_incremental(self) -> dict:
        """增量拉取 CFTC 数据（优先从缓存获取，缺失部分从 API 拉取）"""
        from .database import cftc_cache_get_latest, cftc_cache_add, macro_indicator_list_latest

        start_date = (datetime.now() - timedelta(days=self._cache_days)).strftime('%Y-%m-%d')

        logger.info("Fetching CFTC data (incremental mode)...")

        # 1. 尝试从 API 获取完整数据（缓存表用于原始数据存储，分析数据直接在内存）
        try:
            self._df_tff = await _fetch_cftc_async(self._tff_url, start_date)
            logger.info(f"  TFF: {len(self._df_tff)} rows")
        except Exception as e:
            logger.error(f"  TFF fetch failed: {e}")
            self._df_tff = pd.DataFrame()

        try:
            self._df_disagg = await _fetch_cftc_async(self._disagg_url, start_date)
            logger.info(f"  Disagg: {len(self._df_disagg)} rows")
        except Exception as e:
            logger.error(f"  Disagg fetch failed: {e}")
            self._df_disagg = pd.DataFrame()

        if self._df_tff.empty and self._df_disagg.empty:
            logger.warning("No CFTC data fetched")
            return {"status": "error", "message": "Failed to fetch CFTC data"}

        # 2. 确定 report_date
        dates = []
        if not self._df_tff.empty:
            dates.append(self._df_tff['report_date'].max())
        if not self._df_disagg.empty:
            dates.append(self._df_disagg['report_date'].max())
        if dates:
            self._report_date = max(dates).strftime('%Y-%m-%d')
        else:
            self._report_date = 'N/A'

        logger.info(f"  Report date: {self._report_date}")

        # 3. 获取同期价格数据
        all_contracts = self._tff_contracts + self._disagg_contracts
        try:
            self._price_data = await asyncio.to_thread(
                _fetch_tue_tue_returns_sync, all_contracts, self._report_date
            )
            logger.info(f"  Price data: {len(self._price_data)} instruments")
        except Exception as e:
            logger.warning(f"  Price data fetch failed: {e}")
            self._price_data = {}

        return {"status": "ok", "report_date": self._report_date}

    def check_freshness(self) -> str:
        """检测 CFTC 数据新鲜度: FRESH/STALE/EMPTY"""
        if self._report_date is None or self._report_date == 'N/A':
            return 'EMPTY'
        try:
            latest = datetime.strptime(self._report_date, '%Y-%m-%d')
            # CFTC 每周五发布(对应周二持仓)，距最新>10天则为过期
            days_since = (datetime.now() - latest).days
            if days_since > 10:
                return 'STALE'
            return 'FRESH'
        except Exception:
            return 'EMPTY'

    # -- JSON Output --------------------------------------------------------

    async def get_positioning_json(self) -> list[dict]:
        """获取所有合约的结构化持仓数据 (JSON 格式)"""
        if self._df_tff is None:
            await self.fetch_incremental()

        results = []

        # TFF: Leveraged Funds
        if self._df_tff is not None and not self._df_tff.empty:
            for c in self._tff_contracts:
                matched = _match_cftc(self._df_tff, c['cftc'])
                if matched is None or matched.empty:
                    continue
                pos = _pos_group(matched, 'lev_money_positions_long', 'lev_money_positions_short',
                                 self._zscore_window)
                pd_info = self._price_data.get(c['name']) if self._price_data else None
                crowding = _crowding_label(pos['net_z'], pos['long_z'], pos['short_z'])
                divergence = _is_divergence(pos['flow_state'], pd_info['ret'] if pd_info else None)
                results.append({
                    'contract': c['name'],
                    'section': c['section'],
                    'category': 'tff',
                    'report_date': self._report_date,
                    **pos,
                    'crowding': crowding,
                    'divergence': divergence,
                    'price_chg': pd_info['ret'] if pd_info else None,
                    'price_start': pd_info['px_start'] if pd_info else None,
                    'price_end': pd_info['px_end'] if pd_info else None,
                })

        # Disagg: Managed Money
        if self._df_disagg is not None and not self._df_disagg.empty:
            for c in self._disagg_contracts:
                matched = _match_cftc(self._df_disagg, c['cftc'])
                if matched is None or matched.empty:
                    continue
                pos = _pos_group(matched, 'm_money_positions_long_all', 'm_money_positions_short_all',
                                 self._zscore_window)
                pd_info = self._price_data.get(c['name']) if self._price_data else None
                crowding = _crowding_label(pos['net_z'], pos['long_z'], pos['short_z'])
                divergence = _is_divergence(pos['flow_state'], pd_info['ret'] if pd_info else None)
                results.append({
                    'contract': c['name'],
                    'section': c['section'],
                    'category': 'disagg',
                    'report_date': self._report_date,
                    **pos,
                    'crowding': crowding,
                    'divergence': divergence,
                    'price_chg': pd_info['ret'] if pd_info else None,
                    'price_start': pd_info['px_start'] if pd_info else None,
                    'price_end': pd_info['px_end'] if pd_info else None,
                })

        return results

    # -- Gold Focus ---------------------------------------------------------

    async def gold_focus(self) -> dict:
        """黄金专项深度分析 — CFTC 持仓趋势 + 历史极值 + 背离检测"""
        all_data = await self.get_positioning_json()

        # 找到黄金的 TFF 和 Disagg 数据
        gold_tff = [d for d in all_data if d['contract'] == '黄金' and d['category'] == 'tff']
        gold_disagg = [d for d in all_data if d['contract'] == '黄金' and d['category'] == 'disagg']

        if not gold_disagg:
            # 尝试按 cftc 名匹配
            gold_disagg = [d for d in all_data if 'GOLD' in d.get('contract', '').upper()]

        result = {
            'report_date': self._report_date,
            'freshness': self.check_freshness(),
            'disagg_managed_money': gold_disagg[0] if gold_disagg else None,
            'tff_leveraged_funds': gold_tff[0] if gold_tff else None,
        }

        # 增加历史趋势分析（如果数据中有多个日期）
        if self._df_disagg is not None and not self._df_disagg.empty:
            gold_cfg = next((c for c in self._disagg_contracts if 'GOLD' in c.get('cftc', '').upper()), None)
            if gold_cfg:
                matched = _match_cftc(self._df_disagg, gold_cfg['cftc'])
                if matched is not None and not matched.empty:
                    mm_long = matched['m_money_positions_long_all'].fillna(0)
                    mm_short = matched['m_money_positions_short_all'].fillna(0)
                    mm_net = mm_long - mm_short

                    # 最近4周趋势
                    if len(mm_net) >= 4:
                        recent = mm_net.tail(4)
                        trend = '持续增仓' if recent.diff().dropna().mean() > 0 else '持续减仓' if recent.diff().dropna().mean() < 0 else '震荡'
                        result['trend_4w'] = trend
                        result['net_4w_values'] = [int(v) for v in recent.values]

                    # 历史极值
                    if len(mm_net) >= 52:
                        net_max = int(mm_net.max())
                        net_min = int(mm_net.min())
                        net_current = int(mm_net.iloc[-1])
                        pct_from_max = round((net_current - net_max) / (abs(net_max) + 1) * 100, 1) if net_max != 0 else 0
                        pct_from_min = round((net_current - net_min) / (abs(net_min) + 1) * 100, 1) if net_min != 0 else 0
                        result['history_extremes'] = {
                            'net_max': net_max,
                            'net_min': net_min,
                            'net_current': net_current,
                            'pct_from_max': pct_from_max,
                            'pct_from_min': pct_from_min,
                        }

                    # 背离检测：持仓 vs 价格
                    if len(mm_net) >= 8 and len(matched) >= 8:
                        net_changes = mm_net.diff().dropna().tail(8)
                        # 需要同期价格变化数据
                        # 简化版：如果持仓和价格数据存在，计算相关系数
                        pass

        return result


# ---------------------------------------------------------------------------
# Singleton instance for app-level use
# ---------------------------------------------------------------------------

_service: Optional[CFTCService] = None


def get_cftc_service(zscore_window: int = 156, cache_days: int = 1200) -> CFTCService:
    global _service
    if _service is None:
        _service = CFTCService(zscore_window=zscore_window, cache_days=cache_days)
    return _service
