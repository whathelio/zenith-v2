"""每轮对话的模块需求评估器（规则式，零 LLM 成本）。

背景（2026-09-15）：
「本轮需要哪些模块/工具」原先完全由模型在生成时临时决定，系统层没有任何评估环节。
后果是「提示词教了、模型没调」——审计实测：4/5 会话是长转录整理，但没有一次先查重，
资料类也没走 distill_note。今天翻 1081 条 trace 才发现，属于事后人工排查。

本模块把评估固化为**每轮必跑**的显式步骤，产出可审计的判断结果。

三层职责边界（本模块只管第一层）：
- 评估（本模块）：纯规则，可预测、零 token、结果可审计
- 触发（routers/chat.py）：只读工具可预执行；**写入类一律只建议**，避免误写
- 留痕（routers/chat.py）：assess / assess_outcome 两类 trace

设计取舍：
① 不做 LLM 规划 —— LLM 的评估过程本身是黑盒，与「留痕」目标冲突；且每轮多一次
   调用与 token 成本关切相悖。
② 只读工具白名单（PREREAD_SAFE）是预执行的安全边界：check_duplicate / search_memory /
   kb_stats 都不写库，预执行最坏只是浪费一次查询；add_note / add_schedule 等写操作
   绝不预执行。
③ 本模块**不写 trace、不调工具**，只做纯函数式判定，便于单测与复用。
"""
from __future__ import annotations

import re

# ── 规则常量 ────────────────────────────────────────────────
LONG_TEXT_CHARS = 3000          # 超过视为「资料类长文本」
_QUESTION_TAIL = re.compile(r"[？?]\s*$|吗\s*$|呢\s*$")

_TRANSCRIPT_HINT = re.compile(r"字幕|转录|讲稿|逐字稿|全文|原文")
_LINK_RE = re.compile(r"https?://[^\s)]+")
# 时间信号拆强弱：**强信号**（明天/周X/X点）单独即可判日程；
# **弱信号**（今天/今晚/下午）必须搭配安排性动词才成立 ——
# 否则「今天天气怎么样」会被误判为日程并建议 add_schedule（实测踩到）。
_STRONG_TIME = re.compile(
    r"明天|后天|大后天|下周|本周|周[一二三四五六日天]"
    r"|\d{1,2}\s*[点时]|\d{4}-\d{2}-\d{2}"
)
_WEAK_TIME = re.compile(r"今天|今晚|早上|上午|中午|下午|晚上")
_SCHEDULE_VERB = re.compile(
    r"开会|会议|见面|约[^定]|安排|参加|截止|提交|面试|出差|行程|日程|提醒|要去做|得去"
)
_REMEMBER = re.compile(r"记住|记一下|记录一下|存一下|别忘了|备忘")
_RECALL = re.compile(r"之前|上次|上回|我说过|以前|早先|刚才提到")
_FRESH = re.compile(r"最新|刚刚|今天|目前|现在|近期|2026|2027")
_ACADEMIC = re.compile(r"论文|文献|DOI|doi|arXiv|arxiv|期刊|会议投稿")
# 知识库/笔记治理（2026-09-16 补）：实测会话 95716924「统计下所有知识库，验证是否
# 精炼标准」未命中任何规则，而该轮实际调用了 20+ 次 list_notes / read_note /
# check_duplicate —— 评估层完全没帮上忙。这是 D1①（纯规则）的已知代价，
# 此处按实测缺口补上。
_KB_GOVERNANCE = re.compile(
    r"知识库|笔记库|索引|精炼|去重|冗余|治理|归档|合并笔记|笔记数|记忆库|整理.*(库|笔记|记忆)"
)

# 只读工具白名单 —— 预执行的安全边界。
# 判定依据：这些工具只读库/只算分，最坏结果是浪费一次查询，不会产生副作用。
PREREAD_SAFE = {"check_duplicate", "search_memory", "kb_stats"}


def assess_round(user_message: str) -> dict:
    """评估本轮对话需要哪些模块。

    返回:
        {
          "intents":   ["long_transcript", ...],        # 命中的意图标签
          "suggested": [{"tool","why","confidence"}],   # 建议动作（给模型 + 给留痕）
          "preread":   [{"tool","args"}],               # 可安全预执行的只读项
          "hint":      "…",                             # 注入 system prompt 的文本
          "text_len":  1234,
        }

    纯函数、无副作用、不抛异常（判定失败时返回空结果，由调用方降级放行）。
    """
    msg = (user_message or "").strip()
    out: dict = {"intents": [], "suggested": [], "preread": [], "hint": "", "text_len": len(msg)}
    if not msg:
        return out

    def _suggest(tool: str, why: str, conf: str, preread_args: dict | None = None) -> None:
        if any(s["tool"] == tool for s in out["suggested"]):
            return  # 同一工具不重复建议
        out["suggested"].append({"tool": tool, "why": why, "confidence": conf})
        if preread_args is not None and tool in PREREAD_SAFE:
            out["preread"].append({"tool": tool, "args": preread_args})

    # ① 长文本 / 转录类 → 资料整理（审计实测的首要场景）
    if len(msg) >= LONG_TEXT_CHARS and not _QUESTION_TAIL.search(msg):
        out["intents"].append("long_text")
        if _TRANSCRIPT_HINT.search(msg[:500]):
            out["intents"].append("transcript")
        _suggest("check_duplicate", "长文本入库前查重，避免与已有笔记重复",
                 "high", {"title": msg[:60], "content": msg[:1500]})
        _suggest("distill_note", "资料类长文本应结构化提取，而非直接整段入库", "high")

    # ② 含链接
    if _LINK_RE.search(msg):
        out["intents"].append("has_link")
        _suggest("analyze_content", "含链接，优先自动识别站点类型并生成摘要", "high")

    # ③ 日程安排：强时间信号，或（弱时间信号 + 安排性动词），避免「今天天气」误报
    if _STRONG_TIME.search(msg) or (_WEAK_TIME.search(msg) and _SCHEDULE_VERB.search(msg)):
        out["intents"].append("schedule_ref")
        _suggest("add_schedule", "含时间表达；若确为安排，需经确认卡片再落日程", "medium")

    # ④ 明确要求记录
    if _REMEMBER.search(msg):
        out["intents"].append("explicit_remember")
        _suggest("check_duplicate", "入库前查重", "high", {"title": msg[:60]})
        _suggest("add_note", "用户明确要求记录", "high")

    # ⑤ 追问历史
    if _RECALL.search(msg):
        out["intents"].append("recall")
        _suggest("search_memory", "用户在追问历史内容，先查记忆再作答",
                 "medium", {"keyword": msg[:30]})

    # ⑥ 时效性 / 学术
    if _FRESH.search(msg):
        out["intents"].append("fresh_info")
        _suggest("web_search", "涉及最新信息，本地知识库可能滞后", "medium")
    if _ACADEMIC.search(msg):
        out["intents"].append("academic")
        _suggest("academic_search", "涉及论文/文献检索", "high")

    # ⑦ 知识库 / 笔记治理（2026-09-16 据实测缺口补）
    # 建议「先盘点再查重」——实测该场景下模型会自己摸索出这个顺序，
    # 但那是靠它逐轮试错（20+ 次调用）换来的，评估层本可以一开始就点明。
    if _KB_GOVERNANCE.search(msg):
        out["intents"].append("kb_governance")
        _suggest("list_notes", "涉及知识库盘点，先取全量清单再下判断", "medium")
        _suggest("check_duplicate", "治理场景通常需要查重，避免误删非重复项", "medium")

    out["hint"] = _build_hint(out)
    return out


def _build_hint(a: dict) -> str:
    """把评估结果渲染成注入 system prompt 的短文本（无命中则返回空串）。"""
    sug = a.get("suggested") or []
    if not sug:
        return ""
    lines = [
        "",
        "【本轮模块评估 · 系统自动生成，非用户输入】",
        f"检测到的意图：{', '.join(a['intents']) or '（无）'}",
        "建议按以下顺序使用工具；标 high 的项建议务必执行，若判断不适用请说明理由：",
    ]
    for s in sug:
        lines.append(f"  - {s['tool']} [{s['confidence']}]：{s['why']}")
    return "\n".join(lines)


def summarize_assessment(a: dict) -> dict:
    """给 trace 用的精简快照（去掉 hint 与 preread 里的长文本，避免 traces 膨胀）。"""
    return {
        "intents": a.get("intents", []),
        "suggested": [s["tool"] for s in a.get("suggested", [])],
        "preread": [p["tool"] for p in a.get("preread", [])],
        "text_len": a.get("text_len", 0),
    }
