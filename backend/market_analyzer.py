"""Zenith v2 市场分析引擎 — 每日分析 + 预测追踪验证闭环
组合 CFTC持仓 + 宏观指标 + 事件 → LLM → 报告 → 预测追踪
"""
from __future__ import annotations

import json
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional

from .cftc_service import get_cftc_service
from .macro_data import get_macro_service
from .llm_client import call_llm
from .config import load_config
from .database import (
    market_report_add, market_report_get_latest, market_report_list,
    prediction_add, prediction_batch_add, prediction_get_pending,
    prediction_verify, prediction_list, prediction_get_hit_rate,
    macro_indicator_get_by_name,
    conv_list_by_date, msg_list, mem_list_by_date, mem_search,
)

logger = logging.getLogger("zenith.market")

# ---------------------------------------------------------------------------
# Analysis Prompt Template
# ---------------------------------------------------------------------------

ANALYSIS_PROMPT_TEMPLATE = """你是 Zenith 的市场分析引擎，专注于现货黄金的日度分析。

## 任务
基于以下结构化数据，生成今日现货黄金市场分析报告。

## 今日数据
### 1. 宏观指标
{macro_data}

### 2. CFTC 黄金持仓分析
{cftc_data}

### 3. 今日财经事件（逾期=已发生需确认消化，未达=今日待发布）
{events_data}

### 4. 昨日预测验证（如有）
{yesterday_predictions}

### 5. 过往工作流经验（对话 + 记忆 + 近期报告）
{workflow_experience}

## 输出格式（严格遵守 JSON 结构）
返回一个 JSON 对象，包含以下字段：
```json
{
  "factor_table": "影响因素汇总表（Markdown格式，列：影响因素|当前数据|对黄金影响|趋势方向）",
  "overdue_events": [
    {"name": "事件名", "impact": "利多/利空/双向", "strength": 1-5, "direction": "bullish/bearish/bidirectional/neutral", "note": "确认要点"}
  ],
  "upcoming_events": [
    {"name": "事件名", "time": "预计时间", "expected_value": "预期值", "prev_value": "前值", "potential_impact": "利多/利空", "direction": "bullish/bearish/neutral"}
  ],
  "daily_advice": "日内操作建议（关键点位+方向+止损+逻辑）",
  "weekly_advice": "本周操作建议（整体方向+关键观察点+风险提示）",
  "predictions": [
    {"event_name": "事件名", "predicted_direction": "bullish/bearish/neutral/bidirectional", "predicted_strength": 1-5, "predicted_range": "价格区间如3985-4080"}
  ],
  "yesterday_verification": [
    {"event_name": "事件名", "predicted_direction": "昨日预测方向", "actual_result": "bullish/bearish/neutral", "hit": true/false, "note": "验证说明"}
  ],
  "summary": "3句话核心结论"
}
```

## 分析要求
1. 逾期事件必须明确标注市场消化程度
2. 未达事件必须给出预期值和潜在影响方向
3. 每个事件预测都必须有 direction + strength
4. 操作建议必须包含具体关键点位
5. 昨日预测验证必须逐一对照实际走势
6. 预测方向只能选: bullish / bearish / neutral / bidirectional
7. 必须结合「过往工作流经验」中的交易规则/经验教训/决策记录，让操作建议更具一致性"""

# ---------------------------------------------------------------------------
# MarketAnalyzer
# ---------------------------------------------------------------------------

class MarketAnalyzer:
    """市场分析编排引擎 — CFTC + 宏观 + 事件 → LLM → 报告 + 预测追踪"""

    def __init__(self):
        self._cftc = get_cftc_service()
        self._macro = get_macro_service()

    async def run_daily_analysis(self) -> dict:
        """执行每日分析全流程"""
        logger.info("Starting daily market analysis...")

        # 1. 获取 CFTC 持仓数据
        try:
            await self._cftc.fetch_incremental()
            cftc_json = await self._cftc.get_positioning_json()
            gold_focus = await self._cftc.gold_focus()
        except Exception as e:
            logger.error(f"CFTC data fetch failed: {e}")
            cftc_json = []
            gold_focus = {"error": str(e)}

        # 2. 获取宏观数据
        try:
            macro_result = await self._macro.fetch_all_indicators()
            macro_indicators = macro_result.get('indicators', [])
            events = macro_result.get('events', [])
        except Exception as e:
            logger.error(f"Macro data fetch failed: {e}")
            macro_indicators = []
            events = []

        # 3. 获取昨日待验证预测
        yesterday_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        pending_preds = prediction_get_pending(date=yesterday_date)

        # 4. 获取过往工作流经验
        workflow_exp = await self._gather_workflow_experience()

        # 5. 组装 prompt
        macro_str = self._format_macro(macro_indicators)
        cftc_str = self._format_cftc(gold_focus, cftc_json)
        events_str = self._format_events(events)
        preds_str = self._format_predictions(pending_preds)
        workflow_str = self._format_workflow_experience(workflow_exp)

        prompt = ANALYSIS_PROMPT_TEMPLATE.format(
            macro_data=macro_str,
            cftc_data=cftc_str,
            events_data=events_str,
            yesterday_predictions=preds_str,
            workflow_experience=workflow_str,
        )

        # 5. LLM 分析
        messages = [
            {"role": "system", "content": "你是一个专业的金融市场分析师，专注于现货黄金。输出严格遵循 JSON 格式。"},
            {"role": "user", "content": prompt},
        ]

        try:
            result = await call_llm(messages, temperature=0.5, max_tokens=4000)
            content = result.get("content", "")
        except Exception as e:
            logger.error(f"LLM analysis failed: {e}")
            content = f"分析失败: {e}"

        # 6. 解析 LLM 返回的 JSON
        analysis = self._parse_analysis(content)

        # 7. 存入 market_reports
        today = datetime.now().strftime('%Y-%m-%d')
        gold_price = ""
        for ind in macro_indicators:
            if ind.get('indicator') == 'gold' and ind.get('value'):
                gold_price = str(ind['value'])
                break

        report_id = market_report_add({
            "report_date": today,
            "gold_price": gold_price,
            "factor_data": json.dumps(analysis.get('factor_table', ''), ensure_ascii=False),
            "events_overdue": json.dumps(analysis.get('overdue_events', []), ensure_ascii=False),
            "events_upcoming": json.dumps(analysis.get('upcoming_events', []), ensure_ascii=False),
            "analysis_text": content,
            "daily_advice": analysis.get('daily_advice', ''),
            "weekly_advice": analysis.get('weekly_advice', ''),
        })

        # 8. 存入 market_predictions（今日新预测）
        predictions = analysis.get('predictions', [])
        pred_ids = []
        for pred in predictions:
            pred_id = prediction_add({
                "report_date": today,
                "event_name": pred.get('event_name', ''),
                "predicted_direction": pred.get('predicted_direction', 'neutral'),
                "predicted_strength": pred.get('predicted_strength', 0),
                "predicted_range": pred.get('predicted_range', ''),
            })
            pred_ids.append(pred_id)

        # 9. 验证昨日预测
        verification = analysis.get('yesterday_verification', [])
        for v in verification:
            # 找到匹配的 pending 预测
            for p in pending_preds:
                if p['event_name'] == v.get('event_name', ''):
                    actual_dir = v.get('actual_result', 'neutral')
                    hit = v.get('hit', False)
                    prediction_verify(p['id'], actual_dir, "", "")

        logger.info(f"Daily analysis complete: report_id={report_id}, predictions={len(pred_ids)}")

        return {
            "report_id": report_id,
            "report_date": today,
            "gold_price": gold_price,
            "analysis": analysis,
            "predictions_count": len(pred_ids),
        }

    async def verify_yesterday_predictions(self) -> dict:
        """验证昨日预测 vs 今日实际走势"""
        yesterday_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        pending = prediction_get_pending(date=yesterday_date)

        if not pending:
            return {"total": 0, "hit": 0, "miss": 0, "hit_rate": 0, "details": []}

        # 获取今日实际黄金价格变化
        gold_data = macro_indicator_get_by_name('gold')
        actual_direction = 'neutral'
        if gold_data and gold_data.get('change_pct'):
            try:
                pct = float(gold_data['change_pct'])
                actual_direction = 'bullish' if pct > 0.3 else ('bearish' if pct < -0.3 else 'neutral')
            except (ValueError, TypeError):
                pass

        # 验证每条预测
        details = []
        for p in pending:
            pred_dir = p['predicted_direction']
            hit = False

            # 简化判定：预测方向与实际方向一致则为命中
            # bidirectional 永远命中（因为两个方向都预测了）
            if pred_dir == 'bidirectional':
                hit = True
            elif pred_dir == actual_direction:
                hit = True
            elif pred_dir == 'neutral' and actual_direction == 'neutral':
                hit = True

            prediction_verify(p['id'], actual_direction, gold_data.get('change_pct', ''),
                              gold_data.get('value', ''))
            details.append({
                'event_name': p['event_name'],
                'predicted_direction': pred_dir,
                'predicted_strength': p['predicted_strength'],
                'actual_direction': actual_direction,
                'hit': hit,
            })

        total = len(pending)
        hit_count = sum(1 for d in details if d['hit'])
        return {
            "total": total,
            "hit": hit_count,
            "miss": total - hit_count,
            "hit_rate": round(hit_count / total * 100, 1) if total > 0 else 0,
            "details": details,
        }

    # -- Formatting Helpers ------------------------------------------------

    def _format_macro(self, indicators: list[dict]) -> str:
        lines = []
        for ind in indicators:
            val = ind.get('value', '')
            chg = ind.get('change_pct', '')
            if val:
                line = f"- **{ind['indicator']}**: {val} (变化{chg}%)"
                lines.append(line)
        return '\n'.join(lines) if lines else '（宏观数据获取失败）'

    def _format_cftc(self, gold_focus: dict, all_cftc: list[dict]) -> str:
        parts = []
        if gold_focus.get('disagg_managed_money'):
            mm = gold_focus['disagg_managed_money']
            parts.append(f"### Managed Money (Disagg)")
            parts.append(f"- 净持仓: {mm.get('net', 'N/A')} | z-score: {mm.get('net_z', 'N/A')}")
            parts.append(f"- 多头: {mm.get('long', 'N/A')} | 空头: {mm.get('short', 'N/A')}")
            parts.append(f"- Flow State: {mm.get('flow_state', 'N/A')} | 拥挤度: {mm.get('crowding', 'N/A')}")
            parts.append(f"- 价格变化: {mm.get('price_chg', 'N/A')}%")
            parts.append(f"- 背离检测: {mm.get('divergence', False)}")
        if gold_focus.get('tff_leveraged_funds'):
            lf = gold_focus['tff_leveraged_funds']
            parts.append(f"### Leveraged Funds (TFF)")
            parts.append(f"- 净持仓: {lf.get('net', 'N/A')} | z-score: {lf.get('net_z', 'N/A')}")
        if gold_focus.get('history_extremes'):
            ext = gold_focus['history_extremes']
            parts.append(f"### 历史极值")
            parts.append(f"- 净持仓最高: {ext.get('net_max', 'N/A')} | 最低: {ext.get('net_min', 'N/A')}")
            parts.append(f"- 当前距离极值: {ext.get('pct_from_max', 'N/A')}% / {ext.get('pct_from_min', 'N/A')}%")
        if gold_focus.get('trend_4w'):
            parts.append(f"### 4周趋势: {gold_focus['trend_4w']}")

        # 其他重要合约摘要
        key_contracts = [d for d in all_cftc if d.get('section') in ['金属', '能源', '外汇/加密']]
        if key_contracts:
            parts.append("\n### 其他关键合约持仓")
            for d in key_contracts[:6]:
                parts.append(f"- {d['contract']}: net={d.get('net', 'N/A')} z={d.get('net_z', 'N/A')} flow={d.get('flow_state', '')}")

        return '\n'.join(parts) if parts else '（CFTC数据获取失败）'

    def _format_events(self, events: list[dict]) -> str:
        overdue = [e for e in events if e.get('overdue')]
        upcoming = [e for e in events if not e.get('overdue')]
        parts = []
        if overdue:
            parts.append("### 逾期（已发生/已到期，需确认消化）")
            for e in overdue:
                parts.append(f"- {e.get('name', 'N/A')} ({e.get('time', '')})")
        if upcoming:
            parts.append("### 未达（今日待发布/待发生）")
            for e in upcoming:
                parts.append(f"- {e.get('name', 'N/A')} ({e.get('time', '')})")
        return '\n'.join(parts) if parts else '（事件数据获取失败）'

    def _format_predictions(self, predictions: list[dict]) -> str:
        if not predictions:
            return '（无昨日待验证预测）'
        parts = ["### 昨日预测列表（待验证）"]
        for p in predictions:
            parts.append(f"- {p['event_name']}: 预测方向={p['predicted_direction']} 力度={p['predicted_strength']}⭐")
        return '\n'.join(parts)

    # -- Workflow Experience Helpers --------------------------------------

    async def _gather_workflow_experience(self) -> dict:
        """收集过往工作流经验：近期对话、相关记忆、近期市场报告"""
        from .database import mem_list
        today = datetime.now()
        week_ago = (today - timedelta(days=7)).strftime('%Y-%m-%d')
        three_days_ago = (today - timedelta(days=3)).strftime('%Y-%m-%d')

        # 1. 最近3天对话（按更新时间）
        recent_convs = conv_list_by_date(date_from=three_days_ago)
        # 只保留与交易/市场/黄金相关的对话
        market_convs = []
        for c in recent_convs[:10]:
            title = (c.get('title') or '').lower()
            summary = (c.get('summary') or '').lower()
            if any(k in title or k in summary for k in ['黄金', '交易', '行情', '分析', '市场', '策略', 'gold', 'trade', 'market', 'cftc']):
                market_convs.append(c)

        # 读取这些对话的消息摘要
        conv_excerpts = []
        for c in market_convs[:5]:
            cid = c.get('id')
            try:
                messages = msg_list(cid) if cid else []
                # 只取最近几条用户消息，避免太长
                user_msgs = [m.get('content', '') for m in messages[-6:] if m.get('role') == 'user']
                if not user_msgs:
                    user_msgs = [m.get('content', '') for m in messages[-3:]]
                text = ' | '.join([m[:200] for m in user_msgs if m])
                if text:
                    conv_excerpts.append({
                        'title': c.get('title', '未命名对话'),
                        'summary': c.get('summary', '')[:200],
                        'excerpt': text[:500],
                    })
            except Exception as e:
                logger.debug(f"读取对话 {cid} 消息失败: {e}")

        # 2. 最近7天新增记忆（经验/决策/事实）
        recent_memories = mem_list_by_date(date_from=week_ago)
        relevant_memories = [
            m for m in recent_memories
            if m.get('type') in ('experience', 'decision', 'fact')
            and m.get('importance', 0) >= 3
        ][:10]

        # 3. 搜索长期交易相关记忆
        try:
            trading_mems = mem_search('黄金') + mem_search('交易') + mem_search('策略')
        except Exception:
            trading_mems = []
        # 去重并取高重要度
        seen = set()
        unique_trading_mems = []
        for m in trading_mems:
            mid = m.get('id')
            if mid not in seen:
                seen.add(mid)
                unique_trading_mems.append(m)
        trading_mems = [m for m in unique_trading_mems if m.get('importance', 0) >= 4][:8]

        # 4. 最近3天市场报告
        recent_reports = market_report_list(limit=3)

        return {
            'conversations': conv_excerpts,
            'recent_memories': relevant_memories,
            'trading_memories': trading_mems,
            'recent_reports': recent_reports,
        }

    def _format_workflow_experience(self, exp: dict) -> str:
        parts = []

        # 近期对话经验
        convs = exp.get('conversations', [])
        if convs:
            parts.append("### 近期工作流对话要点")
            for c in convs:
                parts.append(f"- 对话「{c['title']}」: {c['summary'] or c['excerpt']}")

        # 近期记忆
        recent_mems = exp.get('recent_memories', [])
        if recent_mems:
            parts.append("### 近期提炼记忆")
            for m in recent_mems:
                type_label = {'experience': '经验', 'decision': '决定', 'fact': '事实'}.get(m.get('type'), m.get('type', ''))
                parts.append(f"- [{type_label}] {m.get('content', '')}")

        # 长期交易规则
        trading_mems = exp.get('trading_memories', [])
        if trading_mems:
            parts.append("### 长期交易规则/偏好")
            for m in trading_mems:
                type_label = {'experience': '经验', 'decision': '决定', 'fact': '事实'}.get(m.get('type'), m.get('type', ''))
                parts.append(f"- [{type_label}] {m.get('content', '')}")

        # 近期报告摘要
        reports = exp.get('recent_reports', [])
        if reports:
            parts.append("### 近期报告追踪")
            for r in reports:
                parts.append(f"- {r.get('report_date', '')}: 黄金{str(r.get('gold_price', '')).strip()}")

        return '\n'.join(parts) if parts else '（暂无过往工作流经验）'

    def _parse_analysis(self, content: str) -> dict:
        """解析 LLM 返回的分析内容，尝试提取 JSON"""
        from .llm_client import _parse_json_response
        # 尝试直接解析
        parsed = _parse_json_response(content)
        if isinstance(parsed, dict):
            return parsed
        # fallback: 将原文作为 analysis_text
        return {
            'analysis_text': content,
            'factor_table': content,
            'overdue_events': [],
            'upcoming_events': [],
            'daily_advice': '',
            'weekly_advice': '',
            'predictions': [],
            'yesterday_verification': [],
            'summary': '',
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_analyzer: Optional[MarketAnalyzer] = None


def get_market_analyzer() -> MarketAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = MarketAnalyzer()
    return _analyzer


# ---------------------------------------------------------------------------
# Scheduled Task Runner
# ---------------------------------------------------------------------------

_pending_analysis_tasks: set = set()


async def scheduled_daily_analysis():
    """每日定时分析任务（07:00 触发）"""
    cfg = load_config()
    if not cfg.get('market_analysis_enabled', True):
        logger.info("Market analysis disabled, skipping")
        return

    logger.info("Running scheduled daily market analysis...")
    analyzer = get_market_analyzer()
    try:
        result = await analyzer.run_daily_analysis()
        logger.info(f"Daily analysis complete: {result.get('report_id')}")
    except Exception as e:
        logger.error(f"Scheduled analysis failed: {e}")


async def start_market_scheduler():
    """启动每日定时分析（在 app.py lifespan 中调用）"""
    cfg = load_config()
    analysis_time = cfg.get('market_analysis_time', '07:00')

    # 解析时间
    try:
        hour, minute = map(int, analysis_time.split(':'))
    except (ValueError, AttributeError):
        hour, minute = 7, 0

    logger.info(f"Market scheduler started: daily at {hour}:{minute}")

    while True:
        now = datetime.now()
        # 计算下次运行时间
        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= now:
            next_run = next_run + timedelta(days=1)

        wait_seconds = (next_run - now).total_seconds()
        logger.info(f"Next analysis at {next_run}, waiting {wait_seconds:.0f}s")
        await asyncio.sleep(wait_seconds)

        # 执行分析
        task = asyncio.create_task(scheduled_daily_analysis())
        _pending_analysis_tasks.add(task)
        task.add_done_callback(_pending_analysis_tasks.discard)
