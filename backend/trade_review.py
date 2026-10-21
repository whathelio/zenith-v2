"""Zenith v2 — 多角色交易复盘（行为 / 行情 / 风控审计）

为什么要多角色：单次 LLM 复盘最容易犯两类错 ——
  1. 从流水里自己数数 → 算错（所以数字全部由 `trade_analytics` 先算好）
  2. 编出数据里没有的因果 → 幻觉（所以第三个角色专职挑错）

编排：
    trade_analytics.build_profile()  ← 确定性统计（无 LLM）
              ↓ digest（同一份事实）
    ┌─────────────┴─────────────┐
    行为分析师                行情对照官      （并行）
    └─────────────┬─────────────┘
              风控审计官                     （串行，逐条核对证据）
                    ↓
              可靠结论 / 被驳回结论

说明：当前 config 只配了一个 provider（deepseek）时，这是
「单模型多角色」；配置第二个 provider 后可在 `role_providers` 里分配，
才是真正的多模型协同。返回体的 `meta.is_multi_model` 会如实标注。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from .config import get_providers
from .llm_client import call_llm
from .trade_analytics import build_profile, digest

logger = logging.getLogger("zenith.trade_review")

_MAX_TOKENS = 2200

_BEHAVIOR_PROMPT = """你是交易行为分析师。你的唯一任务是从给定的统计事实中归纳交易者的行为模式。

铁律：
1. 只能使用「事实摘要」中出现的字段和数字。禁止计算新数字、禁止估算、禁止引入外部知识。
2. 每条结论必须写 evidence_fields（字段名列表）和 evidence_text（含具体数值）。
3. 事实不足以支撑的结论，不要写。宁缺毋滥。
4. 只描述「行为与结果的关联」，不要给交易建议、不要预测行情、不要推荐品种。
5. 若某项指标缺失（null），明确写「数据不足」，不要用 0 代替。

输出严格 JSON：
{
  "habits": [
    {"tag": "简短标签", "detail": "一句话说明", "evidence_fields": ["overview.payoff_ratio"], "evidence_text": "盈亏比 0.56（均盈 9.11 / 均亏 16.29）", "confidence": "high|mid|low"}
  ],
  "strengths": [{"tag": "...", "evidence_text": "..."}],
  "data_gaps": ["缺失或不充分的指标"]
}"""

_MARKET_PROMPT = """你是行情对照分析师。你的任务是判断交易者的行为与当时行情环境的关系。

铁律：
1. 只能使用「事实摘要」中的 market_context / timing 字段。禁止编造行情、禁止引用外部市场信息。
2. 区分「相关」与「因果」：只能说「X 与 Y 同时出现」，不能说「X 导致 Y」。
3. 每条观察必须带 evidence_fields 与具体数值。
4. 不给交易建议、不预测价格。
5. market_context.available=false 时，直接说明「行情对照不可用」并只做时段层面的分析。

输出严格 JSON：
{
  "observations": [
    {"tag": "...", "detail": "...", "evidence_fields": ["market_context.entry_position.long_avg_pct"], "evidence_text": "多单平均入场位于当日 12.3% 分位", "confidence": "high|mid|low"}
  ],
  "regime_fit": "一句话总结：当前习惯更适合/不适合什么样的行情环境（仅基于统计）",
  "data_gaps": ["..."]
}"""

_AUDITOR_PROMPT = """你是风控审计官。你的任务不是写新分析，而是**挑错**。

你会收到：① 事实摘要 ② 行为分析师报告 ③ 行情对照官报告。

逐条核对每份报告中的每个结论：
- supported：evidence_text 里的数字能在事实摘要中找到，且一致
- unsupported：事实摘要中找不到对应依据
- contradicted：与事实摘要矛盾（写出矛盾点）

额外职责：
- 标记任何「交易建议 / 行情预测 / 外部知识」——这类内容一律驳回（verdict=rejected, reason=超出行为归因范围）
- 标记把 null / 缺失指标当成 0 使用的结论

输出严格 JSON：
{
  "verdicts": [
    {"source": "behavior|market", "tag": "...", "verdict": "supported|unsupported|contradicted|rejected", "reason": "...", "corrected_text": "若 contradicted，给出修正后的表述"}
  ],
  "reliable_conclusions": [{"tag": "...", "detail": "...", "evidence_text": "..."}],
  "rejected_conclusions": [{"tag": "...", "reason": "..."}],
  "overall_confidence": "high|mid|low",
  "audit_note": "一句话说明本次审计最需要提醒注意的问题"
}"""


def _strip_json(text: str) -> dict:
    """从模型输出中提取 JSON（容忍 ```json 围栏与前后废话）。"""
    if not text:
        return {}
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", t)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    return {"_parse_error": True, "_raw": t[:1500]}


async def _role(
    system_prompt: str,
    user_content: str,
    provider_name: str = "",
    use_background: bool = True,
    temperature: float = 0.2,
    max_tokens: int = _MAX_TOKENS,
) -> dict:
    """跑一个角色。失败时返回结构化错误，不抛异常。"""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    try:
        msg = await call_llm(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            use_background=use_background,
        )
        content = (msg or {}).get("content") or ""
        if not content:
            return {"ok": False, "error": "模型返回空内容"}
        if content.startswith("Error:"):
            return {"ok": False, "error": content}
        data = _strip_json(content)
        # 截断的 JSON 会被 _strip_json 标成 _parse_error —— 必须当失败处理，
        # 否则上层会把「没解析出来」误报成角色成功（实测 auditor 2200 tokens 不够）。
        if data.get("_parse_error"):
            return {
                "ok": False,
                "error": "模型输出不是合法 JSON（多半是 max_tokens 截断）",
                "raw_head": str(data.get("_raw", ""))[:300],
            }
        return {"ok": True, "data": data}
    except Exception as e:
        logger.warning("trade_review role failed: %s", e)
        return {"ok": False, "error": str(e)}


async def run_review(
    days: int = 90,
    symbol: str = "",
    provider_name: str = "",
    use_background: bool = True,
    profile: dict | None = None,
    date_from=None,
    date_to=None,
) -> dict:
    """执行一次多角色复盘。

    Args:
        days: 回溯天数
        symbol: 品种过滤（空=全部，行情对照取交易最多的品种）
        provider_name: 指定 provider；空=default/background
        use_background: True 用 background_provider（便宜模型）
        profile: 可传入已算好的 profile，避免重复读 MT5
        date_from / date_to: 绝对区间（如 "2025-01-01"），优先于 days
    """
    t0 = time.time()
    if profile is None:
        profile = await asyncio.to_thread(
            build_profile, days, symbol, True, date_from, date_to
        )
    if not profile.get("success"):
        return {"success": False, "error": profile.get("error", "统计失败")}

    fact = digest(profile)

    # 并行：行为 + 行情
    behavior_task = _role(
        _BEHAVIOR_PROMPT,
        f"【事实摘要】\n{fact}\n\n请输出行为分析 JSON。",
        provider_name, use_background,
    )
    market_task = _role(
        _MARKET_PROMPT,
        f"【事实摘要】\n{fact}\n\n请输出行情对照 JSON。",
        provider_name, use_background,
    )
    behavior, market = await asyncio.gather(behavior_task, market_task)

    # 审计：必须基于前两份报告串行
    audit = {"ok": False, "error": "前置角色失败，未执行审计"}
    if behavior.get("ok") and market.get("ok"):
        audit = await _role(
            _AUDITOR_PROMPT,
            (
                f"【事实摘要】\n{fact}\n\n"
                f"【行为分析师报告】\n{json.dumps(behavior['data'], ensure_ascii=False)}\n\n"
                f"【行情对照官报告】\n{json.dumps(market['data'], ensure_ascii=False)}\n\n"
                "请输出审计 JSON。"
            ),
            provider_name, use_background, temperature=0.0,
            max_tokens=4000,   # 审计要逐条核对 10+ 条结论，2200 会被截断
        )

    providers = get_providers()
    models = {p.get("model", "") for p in providers if p.get("model")}
    elapsed = round((time.time() - t0) * 1000)

    ok_roles = sum(1 for r in (behavior, market, audit) if r.get("ok"))
    return {
        "success": ok_roles >= 1,
        "partial": ok_roles < 3,
        "facts": {
            "days": days,
            "symbol": symbol or (profile.get("recent_trips") or [{}])[0].get("symbol", ""),
            "generated_at": profile.get("generated_at"),
            "overview": profile.get("overview"),
            "habits_deterministic": profile.get("habits"),
        },
        "roles": {
            "behavior": behavior,
            "market": market,
            "auditor": audit,
        },
        "meta": {
            "elapsed_ms": elapsed,
            "mode": "multi_role",
            "role_providers": [provider_name or ("background" if use_background else "default")] * 3,
            "configured_providers": [p.get("name") for p in providers],
            "configured_models": sorted(models),
            "is_multi_model": len(models) > 1,
            "note": (
                "当前为单模型多角色（同一 provider 跑三个独立角色）。"
                if len(models) <= 1 else
                "已配置多个模型，可在 role_providers 中分配不同角色。"
            ),
            "facts_digest_chars": len(fact),
        },
    }
