"""unified_distill.py 补网测试。

策略：
- 全部依赖（database / llm_client / memory_engine）在 `backend.unified_distill` 命名空间内被 monkeypatch，
  绝不触碰真实 DB、绝不发网络/LLM 请求、绝不写项目 data 目录。
- 重点覆盖：数据聚合（空/满/截断/分组）、提示词占位替换、txt/md 格式化（有/无字段）、
  蒸馏主入口的落库落盘分支、异常吞掉分支。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

import backend.unified_distill as ud


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

class Recorder:
    """记录调用参数，返回可控值。"""

    def __init__(self, ret=1):
        self.calls = []
        self.ret = ret

    def __call__(self, *a, **k):
        self.calls.append((a, k))
        return self.ret


def _llm(payload, capture=None):
    """构造伪造 call_llm：返回 JSON 字符串。"""
    async def _fake(messages, **kwargs):
        if capture is not None:
            capture.append({"messages": messages, "kwargs": kwargs})
        if isinstance(payload, str):
            return {"content": payload}
        return {"content": json.dumps(payload, ensure_ascii=False)}

    return _fake


def install(monkeypatch, **overrides):
    """安装默认空依赖，可被 overrides 覆盖。

    返回 dict[name -> Recorder|override]，其中「可观测」侧写调用（mem_add / psum_upsert /
    conv_update_* / _save_txt* / _archive_external）默认都是 Recorder。
    """
    rec = {}

    class _SaveTxt(Recorder):
        def __call__(self, content, prefix, *a, **k):
            self.calls.append(((content, prefix), {}))
            return f"/fake/{prefix}.txt"

    class _SavePlan(Recorder):
        def __call__(self, content, filename, *a, **k):
            self.calls.append(((content, filename), {}))
            return f"/fake/{filename}"

    special = {"_save_txt": _SaveTxt, "_save_txt_plan": _SavePlan}
    # 可观测记录器（可被 overrides 覆盖）
    for name in ("mem_add", "psum_upsert", "conv_update_title", "conv_update_summary",
                 "_save_txt", "_save_txt_plan", "_archive_external"):
        r = overrides.pop(name, None)
        if r is None:
            r = special.get(name, Recorder)()
        rec[name] = r
        monkeypatch.setattr(ud, name, r)

    base = {
        "conv_get": lambda cid: None,
        "msg_list": lambda cid: [],
        "sch_list": lambda **k: [],
        "mem_list": lambda **k: [],
        "mem_search": lambda *a, **k: [],
        "conv_list_by_date": lambda **k: [],
        "mem_list_by_date": lambda **k: [],
        "note_list_by_date": lambda **k: [],
        "analysis_list_by_date": lambda **k: [],
        "market_report_get_by_date": lambda d: None,
        "goal_list": lambda **k: [],
        "psum_list": lambda *a, **k: [],
        "call_llm": _llm({}),
        "_is_duplicate": lambda c: False,
    }
    base.update(overrides)
    for name, val in base.items():
        monkeypatch.setattr(ud, name, val)
    return rec


def _daily_data(**over):
    d = {
        "date": "2026-10-14",
        "chat_text": "chat",
        "schedule_text": "sch",
        "note_text": "note",
        "memory_text": "mem",
        "analysis_text": "ana",
        "market_text": "mkt",
        "goal_text": "goal",
        "conv_count": 1,
        "sch_count": 1,
        "note_count": 1,
        "mem_count": 1,
    }
    d.update(over)
    return d


def _weekly_data(**over):
    d = {
        "week_start": "2026-10-12",
        "week_end": "2026-10-18",
        "chat_text": "chat",
        "schedule_text": "sch",
        "note_text": "note",
        "memory_text": "mem",
        "conv_count": 1,
        "sch_count": 1,
        "note_count": 1,
        "mem_count": 1,
    }
    d.update(over)
    return d


# ===========================================================================
# 1. _gather_conversation
# ===========================================================================

def test_gather_conversation_missing(monkeypatch):
    install(monkeypatch, conv_get=lambda cid: None)
    out = ud._gather_conversation("c1")
    assert out["success"] is False
    assert "c1" in out["error"]


def test_gather_conversation_normal(monkeypatch):
    install(
        monkeypatch,
        conv_get=lambda cid: {"title": "T", "created_at": "2026-10-14", "summary": "S"},
        msg_list=lambda cid: [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "在的"},
        ],
    )
    out = ud._gather_conversation("c1")
    assert out["success"] is True
    assert out["title"] == "T"
    assert out["existing_summary"] == "S"
    assert out["msg_count"] == 2
    assert "用户：你好" in out["chat_text"]
    assert "AI：在的" in out["chat_text"]
    assert "sys" not in out["chat_text"]


def test_gather_conversation_empty_messages(monkeypatch):
    install(monkeypatch, conv_get=lambda cid: {"title": "", "created_at": "", "summary": ""},
            msg_list=lambda cid: [])
    out = ud._gather_conversation("c1")
    assert out["msg_count"] == 0
    assert out["chat_text"] == ""


# ===========================================================================
# 2. _gather_schedules
# ===========================================================================

def test_gather_schedules_empty(monkeypatch):
    install(monkeypatch, sch_list=lambda **k: [])
    out = ud._gather_schedules()
    assert out["count"] == 0
    assert out["schedule_text"] == "（无日程数据）"
    assert out["schedules"] == []


def test_gather_schedules_fields_and_filters(monkeypatch):
    captured = {}

    def _slist(**k):
        captured.update(k)
        return [
            {"status": "confirmed", "start_time": "2026-10-14 09:00", "title": "会议",
             "importance": 3, "category": "work", "priority": "high", "description": "带资料"},
            {"status": "done", "start_time": "2026-10-14 10:00", "title": "无描述",
             "importance": 9},  # 重要度 >5 应被截到 5★，缺字段走默认
        ]

    install(monkeypatch, sch_list=_slist)
    out = ud._gather_schedules(status="confirmed", date_from="2026-10-01", date_to="2026-10-31")
    assert captured == {"status": "confirmed", "date_from": "2026-10-01", "date_to": "2026-10-31"}
    assert out["count"] == 2
    assert "重要度:★★★" in out["schedule_text"]
    assert "重要度:★★★★★" in out["schedule_text"]  # 9 被 clamp 到 5
    assert "说明: 带资料" in out["schedule_text"]
    # 第二条缺 category/priority/description，应走默认且不追加说明行
    assert "类别:other" in out["schedule_text"]
    assert "优先级:normal" in out["schedule_text"]


# ===========================================================================
# 3. _gather_memories
# ===========================================================================

def test_gather_memories_empty(monkeypatch):
    install(monkeypatch, mem_list=lambda **k: [])
    out = ud._gather_memories()
    assert out["count"] == 0
    assert out["memory_text"] == "（无记忆数据）"


def test_gather_memories_search_branch(monkeypatch):
    seen = {}

    def _search(kw, limit=30):
        seen["kw"] = kw
        return [{"type": "fact", "content": "命中", "importance": 3}]

    install(monkeypatch, mem_search=_search, mem_list=lambda **k: pytest.fail("不应走 mem_list"))
    out = ud._gather_memories(search="关键词")
    assert seen["kw"] == "关键词"
    assert out["count"] == 1


def test_gather_memories_group_and_labels(monkeypatch):
    install(monkeypatch, mem_list=lambda **k: [
        {"type": "preference", "content": "喜欢深色主题", "importance": 5, "keywords": "ui,dark"},
        {"type": "preference", "content": "习惯早起", "importance": 3},
        {"type": "custom_type", "content": "未知类型", "importance": 2},
    ])
    out = ud._gather_memories(type_="preference")
    assert out["count"] == 3
    assert set(out["by_type"].keys()) == {"preference", "custom_type"}
    assert "## 偏好习惯 (2条)" in out["memory_text"]
    assert "关键词: ui,dark" in out["memory_text"]
    assert "## custom_type (1条)" in out["memory_text"]  # 未登记类型回退原样


# ===========================================================================
# 4. _gather_daily
# ===========================================================================

def test_gather_daily_all_empty(monkeypatch):
    captured = {}

    def _ana(**k):
        captured["ana"] = k
        return []

    install(monkeypatch, analysis_list_by_date=_ana)
    out = ud._gather_daily("2026-10-14")
    assert out["chat_text"] == "（当日无对话）"
    assert out["schedule_text"] == "（当日无日程）"
    assert out["note_text"] == "（当日无笔记）"
    assert out["memory_text"] == "（当日无新增记忆）"
    assert out["analysis_text"] == "（当日无内容分析）"
    assert out["market_text"] == "（当日无市场报告）"
    assert out["goal_text"] == "（无 active 目标）"
    assert out["conv_count"] == out["sch_count"] == out["note_count"] == out["mem_count"] == 0
    # analysis 用纯日期（非 T 分隔）查询 —— 锁定既有契约
    assert captured["ana"] == {"date_from": "2026-10-14", "date_to": "2026-10-14"}


def test_gather_daily_content_truncation_and_limits(monkeypatch):
    long500 = "a" * 600
    long300 = "b" * 400
    long200 = "c" * 300
    install(
        monkeypatch,
        conv_list_by_date=lambda **k: [{"id": "c1", "title": "对话A", "msg_count": 3}],
        msg_list=lambda cid: [
            {"role": "system", "content": "x"},
            {"role": "user", "content": long500},
            {"role": "assistant", "content": "短"},
        ],
        sch_list=lambda **k: [{"status": "confirmed", "start_time": "2026-10-14 09:00:00", "title": "会"}],
        note_list_by_date=lambda **k: [{"title": "笔记", "content": long300}],
        mem_list_by_date=lambda **k: [{"type": "fact", "content": long200, "importance": 4}],
        analysis_list_by_date=lambda **k: [
            {"analysis_text": long300, "filename": "f" * 80},
            {"analysis_text": "", "filename": "empty"},
        ],
        market_report_get_by_date=lambda d: {"headline": "大标题", "daily_advice": long300},
        goal_list=lambda **k: [
            {"title": "目标1", "current_value": 3, "target_value": 10, "daily_target": 1},
            {"title": "目标2", "current_value": 5, "target_value": 0, "daily_target": 2},
        ],
    )
    out = ud._gather_daily("2026-10-14")
    # 消息截断到 500
    assert "a" * 500 in out["chat_text"]
    assert "a" * 501 not in out["chat_text"]
    # 笔记截断到 300
    assert "b" * 300 in out["note_text"]
    assert "b" * 301 not in out["note_text"]
    # 记忆截断到 200
    assert "c" * 200 in out["memory_text"]
    assert "c" * 201 not in out["memory_text"]
    # 日程 start_time 取前 16 位
    assert "2026-10-14 09:00" in out["schedule_text"]
    # 分析：空 text 被跳过，filename 截断 50
    assert "f" * 50 in out["analysis_text"]
    assert "f" * 51 not in out["analysis_text"]
    assert "empty" not in out["analysis_text"]
    # 市场报告
    assert "标题: 大标题" in out["market_text"]
    assert "当日建议" in out["market_text"]
    # 目标：进度百分比
    assert "目标1: 3/10 (30%), 日化 1" in out["goal_text"]
    assert "目标2: 5/0 (0%), 日化 2" in out["goal_text"]
    assert out["conv_count"] == 1


def test_gather_daily_analysis_limited_to_10(monkeypatch):
    install(monkeypatch, analysis_list_by_date=lambda **k: [
        {"analysis_text": f"t{i}", "filename": f"f{i}"} for i in range(15)
    ])
    out = ud._gather_daily("2026-10-14")
    assert out["analysis_text"].count("- [") == 10


def test_gather_daily_goals_limited_to_8(monkeypatch):
    install(monkeypatch, goal_list=lambda **k: [
        {"title": f"g{i}", "current_value": 1, "target_value": 2, "daily_target": 1} for i in range(12)
    ])
    out = ud._gather_daily("2026-10-14")
    assert out["goal_text"].count("\n") == 7  # 8 行 -> 7 个换行


def test_gather_daily_market_report_empty_dict(monkeypatch):
    install(monkeypatch, market_report_get_by_date=lambda d: {})
    out = ud._gather_daily("2026-10-14")
    assert out["market_text"] == "（当日无市场报告）"


# ===========================================================================
# 5. _gather_weekly
# ===========================================================================

def test_gather_weekly_week_end_and_empty(monkeypatch):
    install(monkeypatch)
    out = ud._gather_weekly("2026-10-12")
    assert out["week_end"] == "2026-10-18"  # +6 天
    assert out["chat_text"] == "（本周无对话）"
    assert out["schedule_text"] == "（本周无日程）"
    assert out["note_text"] == "（本周无笔记）"
    assert out["memory_text"] == "（本周无新增记忆）"


def test_gather_weekly_truncation_and_msg_limit(monkeypatch):
    install(
        monkeypatch,
        conv_list_by_date=lambda **k: [{"id": "c1", "title": "T", "msg_count": 30}],
        msg_list=lambda cid: [{"role": "user", "content": "z" * 300} for _ in range(30)],
        sch_list=lambda **k: [{"status": "x", "start_time": "2026-10-12 08:00:00", "title": "s"}],
        note_list_by_date=lambda **k: [{"title": "n", "content": "w" * 300}],
        mem_list_by_date=lambda **k: [{"type": "fact", "content": "v" * 300, "importance": 2}],
    )
    out = ud._gather_weekly("2026-10-12")
    # 每条消息截断 200
    assert "z" * 200 in out["chat_text"]
    assert "z" * 201 not in out["chat_text"]
    # 每个对话最多 20 条消息 -> 头部标题 1 行 + 20 行 = 21 行
    assert out["chat_text"].count("  用户:") == 20
    assert "w" * 200 in out["note_text"]
    assert "v" * 150 in out["memory_text"]
    assert out["conv_count"] == 1


def test_gather_weekly_system_message_skipped(monkeypatch):
    """周报也要跳过 system 消息（line 240 continue 分支）。"""
    install(
        monkeypatch,
        conv_list_by_date=lambda **k: [{"id": "c1", "title": "T", "msg_count": 3}],
        msg_list=lambda cid: [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "a"},
        ],
    )
    out = ud._gather_weekly("2026-10-12")
    assert "sys" not in out["chat_text"]
    assert "用户: u" in out["chat_text"]
    assert "AI: a" in out["chat_text"]


# ===========================================================================
# 6. _call_distill_llm
# ===========================================================================

async def test_call_distill_llm_replaces_placeholders(monkeypatch):
    cap = []
    install(monkeypatch, call_llm=_llm({"ok": 1}, capture=cap))
    out = await ud._call_distill_llm("A {x} B {y} A{x}", x="1", y="2")
    assert out == {"ok": 1}
    prompt = cap[0]["messages"][0]["content"]
    assert prompt == "A 1 B 2 A1"
    assert cap[0]["kwargs"]["temperature"] == 0.2
    assert cap[0]["kwargs"]["max_tokens"] == 3000


async def test_call_distill_llm_list_first(monkeypatch):
    install(monkeypatch, call_llm=_llm("[{\"a\": 1}, {\"b\": 2}]"))
    out = await ud._call_distill_llm("{t}", t="")
    assert out == {"a": 1}


async def test_call_distill_llm_non_dict_becomes_raw(monkeypatch):
    install(monkeypatch, call_llm=_llm("[1, 2, 3]"))
    out = await ud._call_distill_llm("{t}", t="")
    # 解析出 list[0]=1 -> 非 dict -> {"raw": content}
    assert out == {"raw": "[1, 2, 3]"}


async def test_call_distill_llm_missing_content(monkeypatch):
    async def _no_content(messages, **kwargs):
        return {}

    install(monkeypatch, call_llm=_no_content)
    out = await ud._call_distill_llm("{t}", t="")
    assert out == {}


async def test_call_distill_llm_empty_parse(monkeypatch):
    install(monkeypatch, call_llm=_llm("not json at all"))
    out = await ud._call_distill_llm("{t}", t="")
    assert out == {"raw": "not json at all"}


# ===========================================================================
# 7. txt/md 格式化
# ===========================================================================

def test_to_txt_conv_full_and_empty():
    full = {
        "title": "标题",
        "summary": "总结",
        "key_decisions": ["d1", "d2"],
        "experiences": [{"content": "经验", "importance": 5, "keywords": "k"}],
        "knowledge": ["k1"],
        "action_items": ["a1"],
        "tags": ["t1", "t2"],
    }
    txt = ud._to_txt_conv(full, "c1", 3)
    assert "对话ID: c1" in txt
    assert "标题: 标题" in txt
    assert "消息数: 3" in txt
    assert "--- 关键决策 ---" in txt
    assert "1. d1" in txt
    assert "重要度: 5/5  关键词: k" in txt
    assert "--- 知识点 ---" in txt
    assert "--- 待办行动 ---" in txt
    assert "标签: t1, t2" in txt

    empty = ud._to_txt_conv({}, "c2", 0)
    assert "(无标题)" in empty
    assert "(无总结)" in empty
    assert "关键决策" not in empty
    assert "标签" not in empty


def test_to_txt_schedule_full_and_empty():
    full = {
        "schedule_summary": "总览",
        "patterns": ["p"],
        "gaps": ["g"],
        "suggestions": ["s"],
        "important_upcoming": ["u"],
        "categories": {"work": 2},
        "priority_distribution": {"high": 1},
    }
    txt = ud._to_txt_schedule(full, 5)
    assert "日程总数: 5" in txt
    assert "规律模式" in txt
    assert "遗漏/缺失" in txt
    assert "优化建议" in txt
    assert "即将到来" in txt
    assert "work: 2条" in txt
    assert "high: 1条" in txt

    empty = ud._to_txt_schedule({}, 0)
    assert "(无总结)" in empty
    assert "类别分布" not in empty


def test_to_txt_memory_full_and_empty():
    full = {
        "memory_summary": "总览",
        "core_insights": ["i"],
        "merged_items": [{"original_ids": [1, 2], "merged_content": "mc",
                          "type": "fact", "importance": 4}],
        "outdated": ["o"],
        "growth_stats": {"total": 10, "by_type": {"preference": 3, "unknown_x": 1}},
        "suggestions": ["s"],
    }
    txt = ud._to_txt_memory(full, 10)
    assert "记忆总数: 10" in txt
    assert "原 ID [1, 2] → 合并为" in txt
    assert "类型: 事实知识  重要度: 4/5" in txt
    assert "总计: 10条" in txt
    assert "偏好习惯: 3条" in txt
    assert "unknown_x: 1条" in txt

    empty = ud._to_txt_memory({}, 0)
    assert "(无总结)" in empty


def test_to_txt_all_full_and_empty():
    full = {
        "overall_summary": "全览",
        "cross_insights": ["c"],
        "conv_distill": {
            "title": "T", "summary": "S",
            "key_decisions": ["d"], "experiences": [{"content": "e", "importance": 2}],
            "action_items": ["a"],
        },
        "schedule_distill": {"patterns": ["p"], "gaps": ["g"], "suggestions": ["s"]},
        "memory_distill": {"core_insights": ["i"],
                           "merged_items": [{"merged_content": "mc", "type": "decision", "importance": 5}],
                           "outdated": ["o"]},
        "tags": ["t"],
    }
    txt = ud._to_txt_all(full, 3, 2, 1)
    assert "对话消息数: 3 | 日程数: 2 | 记忆数: 1" in txt
    assert "跨维度洞察" in txt
    assert "对话维度" in txt
    assert "决策1: d" in txt
    assert "经验1: e (重要度:2)" in txt
    assert "待办1: a" in txt
    assert "日程维度" in txt
    assert "记忆维度" in txt
    assert "合并1: mc (类型:decision 重要度:5)" in txt
    assert "标签: t" in txt

    empty = ud._to_txt_all({}, 0, 0, 0)
    assert "(无总结)" in empty
    assert "跨维度洞察" not in empty


def test_to_txt_daily_full_and_empty():
    dd = _daily_data()
    full = {
        "headline": "概要", "daily_summary": "全貌",
        "key_events": ["e"], "completed": ["c"], "missed": ["m"],
        "insights": ["i"], "emotions": "平静", "next_day_suggestions": ["s"],
        "tags": ["t1", "t2"],
    }
    txt = ud._to_txt_daily(full, dd)
    assert "每日总结蒸馏报告 — 2026-10-14" in txt
    assert "重要事项" in txt
    assert "已完成" in txt
    assert "遗漏/未完成" in txt
    assert "核心洞察" in txt
    assert "当日状态" in txt
    assert "明日建议" in txt
    assert "标签 ---" in txt

    empty = ud._to_txt_daily({}, dd)
    assert "--- 重要事项 ---" not in empty
    assert "--- 已完成 ---" not in empty
    assert "--- 明日建议 ---" not in empty


def test_to_txt_daily_market_goal_markers(monkeypatch):
    # 无市场/目标 -> 不追加标记
    dd = _daily_data(market_text="（当日无市场报告）", goal_text="（无 active 目标）")
    txt = ud._to_txt_daily({"headline": "h"}, dd)
    assert "市场✓" not in txt
    assert "目标✓" not in txt

    dd2 = _daily_data(market_text="- 标题: x", goal_text="- g: 1/2 (50%)")
    txt2 = ud._to_txt_daily({"headline": "h"}, dd2)
    assert "市场✓" in txt2
    assert "目标✓" in txt2


def test_to_md_daily_full_and_empty():
    dd = _daily_data()
    full = {
        "headline": "h", "daily_summary": "s",
        "key_events": ["e"], "completed": ["c"], "missed": ["m"],
        "insights": ["i"], "emotions": "emo", "next_day_suggestions": ["n"],
        "tags": ["t"],
    }
    md = ud._to_md_daily(full, dd)
    assert md.startswith("# 每日总结 — 2026-10-14")
    assert "## 重要事项" in md
    assert "## 已完成" in md
    assert "## 遗漏/未完成" in md
    assert "## 核心洞察" in md
    assert "_emo_" in md
    assert "## 明日建议" in md
    assert "`#t`" in md

    empty = ud._to_md_daily({}, dd)
    assert "## 重要事项" not in empty


def test_to_md_daily_markers():
    dd = _daily_data(market_text="- 标题: x", goal_text="- g: 1/2")
    md = ud._to_md_daily({"headline": "h"}, dd)
    assert "· 市场✓" in md
    assert "· 目标✓" in md


def test_to_txt_weekly_full_and_empty():
    wd = _weekly_data()
    full = {
        "headline": "h", "weekly_summary": "s",
        "major_events": ["m"], "patterns": ["p"], "trends": ["t"],
        "achievements": ["a"], "lessons": ["l"],
        "goal_progress": "进度良好", "next_week_plan": ["n"], "tags": ["x"],
    }
    txt = ud._to_txt_weekly(full, wd)
    assert "每周总结蒸馏报告 — 2026-10-12 ~ 2026-10-18" in txt
    for sec in ("重要事项", "规律模式", "趋势变化", "本周成就", "经验教训", "目标进展", "下周规划"):
        assert sec in txt
    assert "进度良好" in txt
    assert "标签 ---" in txt

    empty = ud._to_txt_weekly({}, wd)
    assert "重要事项" not in empty


def test_to_md_weekly_goal_progress_string_not_exploded():
    """回归：goal_progress 是字符串，不能被逐字符拆成列表项。"""
    wd = _weekly_data()
    md = ud._to_md_weekly({"goal_progress": "CFA 三级推进中", "headline": "h"}, wd)
    assert "## 目标进展" in md
    assert "- C" not in md
    assert "CFA 三级推进中" in md


def test_to_md_weekly_full_and_empty():
    wd = _weekly_data()
    full = {
        "headline": "h", "weekly_summary": "s",
        "major_events": ["m"], "achievements": ["a"], "lessons": ["l"],
        "next_week_plan": ["n"], "patterns": ["p"], "trends": ["t"], "tags": ["x"],
    }
    md = ud._to_md_weekly(full, wd)
    assert md.startswith("# 周总结 — 2026-10-12 ~ 2026-10-18")
    for sec in ("重要事项", "本周成就", "经验教训", "下周规划", "规律模式", "趋势变化"):
        assert sec in md
    assert "`#x`" in md

    empty = ud._to_md_weekly({}, wd)
    assert "## 重要事项" not in empty


def test_to_md_monthly():
    data = {"month": "2026-10"}
    result = {
        "headline": "h", "monthly_summary": "s",
        "major_events": ["m"], "trends": ["t"], "achievements": ["a"],
        "lessons": ["l"], "next_month_plan": ["n"], "tags": ["x"],
    }
    md = ud._to_md_monthly(result, data)
    assert md.startswith("# 月总结 — 2026-10")
    for sec in ("重要事项", "趋势变化", "月度成就", "经验教训", "下月规划"):
        assert sec in md
    assert "`#x`" in md
    assert md.endswith("\n")

    empty = ud._to_md_monthly({}, data)
    assert "重要事项" not in empty and "标签" not in empty


def test_to_md_yearly():
    data = {"year": "2026"}
    result = {
        "headline": "h", "yearly_summary": "s",
        "milestones": ["m"], "growth": ["g"], "lessons": ["l"],
        "next_year_plan": ["n"], "tags": ["x"],
    }
    md = ud._to_md_yearly(result, data)
    assert md.startswith("# 年度总结 — 2026")
    for sec in ("年度里程碑", "成长与变化", "经验教训", "来年方向"):
        assert sec in md
    assert "`#x`" in md

    empty = ud._to_md_yearly({}, data)
    assert "年度里程碑" not in empty


# ===========================================================================
# 8. distill_conversation
# ===========================================================================

async def test_distill_conversation_missing(monkeypatch):
    install(monkeypatch, conv_get=lambda cid: None)
    out = await ud.distill_conversation("nope")
    assert out["success"] is False


async def test_distill_conversation_full_flow(monkeypatch):
    payload = {
        "title": " 标题 ",
        "summary": "总结",
        "key_decisions": ["d1", "  ", "d2"],
        "experiences": [
            {"content": " 经验1 ", "importance": 5, "keywords": "k"},
            {"content": "", "importance": 3},          # 空 -> continue
            {"content": "经验2", "importance": 4, "keywords": "k2"},
        ],
        "knowledge": ["k1", "  ", "k2"],
        "action_items": ["a1", "a2"],
        "tags": ["t1", "t2"],
    }

    def _dup(c):
        return c == "经验2"  # 经验2 判定为重复

    install(
        monkeypatch,
        conv_get=lambda cid: {"title": "orig", "created_at": "x", "summary": ""},
        msg_list=lambda cid: [{"role": "user", "content": "hi"}],
        call_llm=_llm(payload),
        _is_duplicate=_dup,
        _save_txt=lambda content, prefix: f"/fake/{prefix}.txt",
    )
    out = await ud.distill_conversation("c1", save_txt=True)
    assert out["txt_content"]
    assert out["txt_path"] == "/fake/conv_c1.txt"
    # 经验1 存, 经验2 跳过, 空跳过 -> saved 至少含 经验1
    calls = ud.mem_add.calls
    saved_contents = [(c[1].get("type_"), c[1].get("content")) for c in calls]
    assert ("experience", "经验1") in saved_contents
    assert ("decision", "d1") in saved_contents
    assert ("decision", "d2") in saved_contents
    assert ("fact", "k1") in saved_contents
    assert ("fact", "k2") in saved_contents
    # 空决策/空知识 -> skip 计数
    assert out["skip_count"] == 3  # 经验2 + 空决策 + 空知识
    # 标题去除空格写回
    assert ("c1", "标题") in [c[0] for c in ud.conv_update_title.calls]
    # 摘要包含待办
    summary_written = ud.conv_update_summary.calls[0][0][1]
    assert "总结" in summary_written
    assert "[待办] a1 | a2" in summary_written
    # 决策/知识用 tags 拼接关键词
    dec_call = [c for c in calls if c[1].get("content") == "d1"][0]
    assert dec_call[1]["keywords"] == "t1,t2"


async def test_distill_conversation_no_save_txt(monkeypatch):
    install(
        monkeypatch,
        conv_get=lambda cid: {"title": "t", "created_at": "x", "summary": ""},
        msg_list=lambda cid: [],
        call_llm=_llm({"summary": "s", "title": "t"}),
    )
    out = await ud.distill_conversation("c1", save_txt=False)
    assert "txt_path" not in out


async def test_distill_conversation_no_title_no_summary(monkeypatch):
    """title/summary 为空 -> 不写回。"""
    install(
        monkeypatch,
        conv_get=lambda cid: {"title": "t", "created_at": "x", "summary": ""},
        msg_list=lambda cid: [],
        call_llm=_llm({}),
    )
    out = await ud.distill_conversation("c1", save_txt=False)
    assert ud.conv_update_title.calls == []
    assert ud.conv_update_summary.calls == []
    assert out["saved_count"] == 0


# ===========================================================================
# 9. distill_schedules / distill_memories
# ===========================================================================

async def test_distill_schedules_flow(monkeypatch):
    install(
        monkeypatch,
        sch_list=lambda **k: [{"status": "confirmed", "start_time": "t", "title": "s", "importance": 3}],
        call_llm=_llm({"schedule_summary": "sum"}),
    )
    out = await ud.distill_schedules(status="confirmed", date_from="a", date_to="b")
    assert out["schedule_count"] == 1
    assert "日程总数: 1" in out["txt_content"]
    assert out["txt_path"].endswith("schedule_distill.txt")


async def test_distill_schedules_no_save(monkeypatch):
    install(monkeypatch, call_llm=_llm({"schedule_summary": "sum"}))
    out = await ud.distill_schedules(save_txt=False)
    assert "txt_path" not in out
    assert out["schedule_count"] == 0


async def test_distill_memories_flow(monkeypatch):
    install(
        monkeypatch,
        mem_list=lambda **k: [{"type": "fact", "content": "c", "importance": 3}],
        call_llm=_llm({"memory_summary": "sum"}),
    )
    out = await ud.distill_memories(type_="fact")
    assert out["memory_count"] == 1
    assert "记忆总数: 1" in out["txt_content"]
    assert out["txt_path"].endswith("memory_distill.txt")


async def test_distill_memories_search_and_no_save(monkeypatch):
    install(monkeypatch, call_llm=_llm({"memory_summary": "s"}))
    out = await ud.distill_memories(search="x", save_txt=False)
    assert "txt_path" not in out


# ===========================================================================
# 10. distill_all
# ===========================================================================

async def test_distill_all_with_conv(monkeypatch):
    payload = {
        "overall_summary": "o",
        "conv_distill": {
            "title": " T ",
            "summary": "S",
            "key_decisions": ["d"],
            "experiences": [{"content": "e", "importance": 2}],
            "action_items": ["a1", "a2"],
        },
        "tags": ["tag"],
    }
    install(
        monkeypatch,
        conv_get=lambda cid: {"title": "t", "created_at": "x", "summary": ""},
        msg_list=lambda cid: [{"role": "user", "content": "hi"}],
        sch_list=lambda **k: [],
        mem_list=lambda **k: [],
        call_llm=_llm(payload),
    )
    out = await ud.distill_all(conv_id="c1")
    assert out["txt_content"]
    assert out["txt_path"].endswith("all_distill.txt")
    contents = [c[1].get("content") for c in ud.mem_add.calls]
    assert "e" in contents and "d" in contents
    assert ud.conv_update_title.calls[0][0] == ("c1", "T")
    assert "[待办] a1 | a2" in ud.conv_update_summary.calls[0][0][1]


async def test_distill_all_no_conv(monkeypatch):
    install(monkeypatch, call_llm=_llm({"overall_summary": "o"}))
    out = await ud.distill_all(save_txt=False)
    assert "txt_path" not in out
    assert ud.mem_add.calls == []
    assert out["saved_count"] == 0


async def test_distill_all_conv_missing(monkeypatch):
    install(monkeypatch, conv_get=lambda cid: None, call_llm=_llm({"overall_summary": "o"}))
    out = await ud.distill_all(conv_id="missing", save_txt=False)
    assert out["saved_count"] == 0
    assert ud.mem_add.calls == []  # conv_data.success=False -> 不落库


async def test_distill_all_dup_skip(monkeypatch):
    payload = {"conv_distill": {"experiences": [{"content": "e", "importance": 2}],
                                "key_decisions": ["d"]}}
    install(
        monkeypatch,
        conv_get=lambda cid: {"title": "t", "created_at": "x", "summary": ""},
        msg_list=lambda cid: [],
        call_llm=_llm(payload),
        _is_duplicate=lambda c: c in ("e", "d"),
    )
    out = await ud.distill_all(conv_id="c1", save_txt=False)
    assert out["saved_count"] == 0
    assert out["skip_count"] == 2


# ===========================================================================
# 11. distill_daily
# ===========================================================================

async def test_distill_daily_empty(monkeypatch):
    install(monkeypatch, _gather_daily=lambda d: _daily_data(
        chat_text="（当日无对话）", conv_count=0, sch_count=0, note_count=0, mem_count=0))
    out = await ud.distill_daily("2026-10-14")
    assert out["headline"] == "2026-10-14 无活动记录"
    assert out["txt_content"] == ""
    assert out["saved_count"] == 0
    assert ud.psum_upsert.calls[0][0] == ("daily", "2026-10-14", "2026-10-14 无活动记录", "", "")


async def test_distill_daily_empty_psum_error(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("db down")

    install(monkeypatch, _gather_daily=lambda d: _daily_data(conv_count=0, sch_count=0, note_count=0, mem_count=0),
            psum_upsert=_boom)
    out = await ud.distill_daily("2026-10-14")
    assert out["headline"].endswith("无活动记录")


async def test_distill_daily_full(monkeypatch):
    payload = {
        "headline": "H", "daily_summary": "S",
        "insights": ["洞察1", "  ", "洞察2"],
        "key_events": ["e"], "tags": ["t"],
    }
    dup_seen = []

    def _dup(c):
        dup_seen.append(c)
        return c.endswith("洞察2")

    install(
        monkeypatch,
        _gather_daily=lambda d: _daily_data(),
        call_llm=_llm(payload),
        _is_duplicate=_dup,
    )
    out = await ud.distill_daily("2026-10-14")
    assert out["saved_count"] == 1  # 洞察1 存, 洞察2 重复, 空跳过
    assert out["conv_count"] == 1 and out["schedule_count"] == 1
    # 去重基准必须是「最终落库形态」（带前缀）
    assert "[每日总结 2026-10-14] 洞察1" in dup_seen
    assert "[每日总结 2026-10-14] 洞察2" in dup_seen
    # mem_add 用 dedup=False 且 content 为带前缀形态
    mc = ud.mem_add.calls[0]
    assert mc[1]["content"] == "[每日总结 2026-10-14] 洞察1"
    assert mc[1]["dedup"] is False
    assert mc[1]["type_"] == "experience"
    assert mc[1]["importance"] == 4
    assert out["txt_path"] == "/fake/daily_2026-10-14.txt"
    assert out["md_path"] == "/fake/daily_2026-10-14.md"
    # psum 落库
    assert ud.psum_upsert.calls[-1][0][0] == "daily"


async def test_distill_daily_no_save(monkeypatch):
    install(monkeypatch, _gather_daily=lambda d: _daily_data(), call_llm=_llm({"headline": "H"}))
    out = await ud.distill_daily("2026-10-14", save_txt=False, save_md=False)
    assert "txt_path" not in out and "md_path" not in out


async def test_distill_daily_default_date(monkeypatch):
    seen = {}

    def _g(d):
        seen["d"] = d
        return _daily_data(conv_count=0, sch_count=0, note_count=0, mem_count=0)

    install(monkeypatch, _gather_daily=_g)
    out = await ud.distill_daily("")
    assert seen["d"] == datetime.now().strftime("%Y-%m-%d")
    assert out["date"] == seen["d"]


async def test_distill_daily_psum_error_swallowed(monkeypatch):
    calls = {"n": 0}

    def _psum(*a, **k):
        calls["n"] += 1
        if calls["n"] >= 1:
            raise RuntimeError("fail")

    install(monkeypatch, _gather_daily=lambda d: _daily_data(), call_llm=_llm({"headline": "H"}),
            psum_upsert=_psum)
    out = await ud.distill_daily("2026-10-14")
    assert out["headline"] == "H"


async def test_distill_daily_insight_non_str_skipped(monkeypatch):
    install(monkeypatch, _gather_daily=lambda d: _daily_data(),
            call_llm=_llm({"headline": "H", "insights": [123, "valid", None]}))
    out = await ud.distill_daily("2026-10-14", save_txt=False, save_md=False)
    assert out["saved_count"] == 1  # 仅 "valid"


# ===========================================================================
# 12. distill_weekly
# ===========================================================================

async def test_distill_weekly_empty(monkeypatch):
    install(monkeypatch, _gather_weekly=lambda w: _weekly_data(
        conv_count=0, sch_count=0, note_count=0, mem_count=0))
    out = await ud.distill_weekly("2026-10-12")
    assert out["headline"] == "本周无活动记录"
    assert out["week_range"] == "2026-10-12 ~ 2026-10-18"
    assert ud.psum_upsert.calls[0][0][0] == "weekly"


async def test_distill_weekly_full(monkeypatch):
    payload = {
        "headline": "H", "weekly_summary": "S",
        "lessons": ["教训1", "教训2"],
        "patterns": ["规律1"],
        "goal_progress": "进度",
        "tags": ["t"],
    }

    def _dup(c):
        return c.endswith("教训2")

    install(monkeypatch, _gather_weekly=lambda w: _weekly_data(), call_llm=_llm(payload),
            _is_duplicate=_dup)
    out = await ud.distill_weekly("2026-10-12")
    # 教训1 存, 教训2 重复, 规律1 存
    assert out["saved_count"] == 2
    contents = [c[1]["content"] for c in ud.mem_add.calls]
    assert "[每周总结 2026-10-12] 教训1" in contents
    assert "[每周规律 2026-10-12] 规律1" in contents
    types = {c[1]["type_"] for c in ud.mem_add.calls}
    assert types == {"experience", "fact"}
    assert out["txt_path"].endswith("weekly_2026-10-12.txt")
    assert out["md_path"].endswith("weekly_2026-10-12.md")


async def test_distill_weekly_default_week_start(monkeypatch):
    seen = {}

    def _g(w):
        seen["w"] = w
        return _weekly_data(conv_count=0, sch_count=0, note_count=0, mem_count=0)

    install(monkeypatch, _gather_weekly=_g)
    await ud.distill_weekly("")
    today = datetime.now()
    monday = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
    assert seen["w"] == monday


async def test_distill_weekly_no_save(monkeypatch):
    install(monkeypatch, _gather_weekly=lambda w: _weekly_data(), call_llm=_llm({"headline": "H"}))
    out = await ud.distill_weekly("2026-10-12", save_txt=False, save_md=False)
    assert "txt_path" not in out and "md_path" not in out


async def test_distill_weekly_empty_psum_error(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("x")

    install(monkeypatch, _gather_weekly=lambda w: _weekly_data(conv_count=0, sch_count=0, note_count=0, mem_count=0),
            psum_upsert=_boom)
    out = await ud.distill_weekly("2026-10-12")
    assert out["headline"] == "本周无活动记录"


async def test_distill_weekly_final_psum_error_swallowed(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("x")

    install(monkeypatch, _gather_weekly=lambda w: _weekly_data(), call_llm=_llm({"headline": "H"}),
            psum_upsert=_boom)
    out = await ud.distill_weekly("2026-10-12", save_txt=False, save_md=False)
    assert out["headline"] == "H"


# ===========================================================================
# 13. _gather_monthly / _gather_yearly
# ===========================================================================

def test_gather_monthly_filter(monkeypatch):
    def _plist(pt):
        if pt == "daily":
            return [{"period_key": "2026-10-01", "content": "d1"},
                    {"period_key": "2026-09-30", "content": "d0"}]
        return [{"period_key": "2026-10-05", "content": "w1"},
                {"period_key": "2026-09-28", "content": "w0"}]

    install(monkeypatch, psum_list=_plist)
    out = ud._gather_monthly("2026-10")
    assert out["daily_count"] == 1
    assert out["weekly_count"] == 1
    assert "d1" in out["daily_text"] and "d0" not in out["daily_text"]
    assert "w1" in out["weekly_text"] and "w0" not in out["weekly_text"]
    assert out["month"] == "2026-10"


def test_gather_monthly_empty(monkeypatch):
    install(monkeypatch, psum_list=lambda pt: [])
    out = ud._gather_monthly("2026-10")
    assert out["daily_text"] == "（当月无日总结）"
    assert out["weekly_text"] == "（当月无周总结）"
    assert out["daily_count"] == 0 and out["weekly_count"] == 0


def test_gather_yearly_filter(monkeypatch):
    install(monkeypatch, psum_list=lambda pt: [
        {"period_key": "2026-01", "content": "m1"},
        {"period_key": "2025-12", "content": "m0"},
    ])
    out = ud._gather_yearly("2026")
    assert out["monthly_count"] == 1
    assert "m1" in out["monthly_text"] and "m0" not in out["monthly_text"]


def test_gather_yearly_empty(monkeypatch):
    install(monkeypatch, psum_list=lambda pt: [])
    out = ud._gather_yearly("2026")
    assert out["monthly_text"] == "（当年无月总结）"


# ===========================================================================
# 14. distill_monthly / distill_yearly
# ===========================================================================

def _psum_two(pt):
    if pt == "daily":
        return [{"period_key": "2026-10-01", "content": "d1"}]
    if pt == "weekly":
        return [{"period_key": "2026-10-05", "content": "w1"}]
    return [{"period_key": "2026-01", "content": "m1"}]


async def test_distill_monthly_empty(monkeypatch):
    install(monkeypatch, psum_list=lambda pt: [])
    out = await ud.distill_monthly("2026-10")
    assert out["skipped"] is True
    assert ud.psum_upsert.calls[0][0] == ("monthly", "2026-10", "2026-10 无活动记录", "", "")


async def test_distill_monthly_empty_psum_error(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("x")

    install(monkeypatch, psum_list=lambda pt: [], psum_upsert=_boom)
    out = await ud.distill_monthly("2026-10")
    assert out["skipped"] is True


async def test_distill_monthly_full(monkeypatch):
    install(monkeypatch, psum_list=_psum_two, call_llm=_llm({"headline": "H", "monthly_summary": "S"}))
    out = await ud.distill_monthly("2026-10")
    assert out["md_content"].startswith("# 月总结 — 2026-10")
    assert out["txt_path"].endswith("monthly_2026-10.txt")
    assert out["md_path"].endswith("monthly_2026-10.md")
    assert ud.psum_upsert.calls[-1][0][0] == "monthly"


async def test_distill_monthly_default(monkeypatch):
    seen = {}

    def _plist(pt):
        seen["pt"] = pt
        return []

    install(monkeypatch, psum_list=_plist)
    out = await ud.distill_monthly("")
    assert out["month"] == datetime.now().strftime("%Y-%m")


async def test_distill_monthly_no_save(monkeypatch):
    install(monkeypatch, psum_list=_psum_two, call_llm=_llm({"headline": "H"}))
    out = await ud.distill_monthly("2026-10", save_txt=False, save_md=False)
    assert "txt_path" not in out and "md_path" not in out


async def test_distill_monthly_psum_error_swallowed(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("x")

    install(monkeypatch, psum_list=_psum_two, call_llm=_llm({"headline": "H"}), psum_upsert=_boom)
    out = await ud.distill_monthly("2026-10", save_txt=False, save_md=False)
    assert out["headline"] == "H"


async def test_distill_yearly_empty(monkeypatch):
    install(monkeypatch, psum_list=lambda pt: [])
    out = await ud.distill_yearly("2026")
    assert out["skipped"] is True
    assert ud.psum_upsert.calls[0][0] == ("yearly", "2026", "2026 无活动记录", "", "")


async def test_distill_yearly_empty_psum_error(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("x")

    install(monkeypatch, psum_list=lambda pt: [], psum_upsert=_boom)
    out = await ud.distill_yearly("2026")
    assert out["skipped"] is True


async def test_distill_yearly_full(monkeypatch):
    install(monkeypatch, psum_list=_psum_two, call_llm=_llm({"headline": "H", "yearly_summary": "S"}))
    out = await ud.distill_yearly("2026")
    assert out["md_content"].startswith("# 年度总结 — 2026")
    assert out["txt_path"].endswith("yearly_2026.txt")
    assert out["md_path"].endswith("yearly_2026.md")


async def test_distill_yearly_default(monkeypatch):
    install(monkeypatch, psum_list=lambda pt: [])
    out = await ud.distill_yearly("")
    assert out["year"] == datetime.now().strftime("%Y")


async def test_distill_yearly_no_save(monkeypatch):
    install(monkeypatch, psum_list=_psum_two, call_llm=_llm({"headline": "H"}))
    out = await ud.distill_yearly("2026", save_txt=False, save_md=False)
    assert "txt_path" not in out and "md_path" not in out


async def test_distill_yearly_psum_error_swallowed(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("x")

    install(monkeypatch, psum_list=_psum_two, call_llm=_llm({"headline": "H"}), psum_upsert=_boom)
    out = await ud.distill_yearly("2026", save_txt=False, save_md=False)
    assert out["headline"] == "H"


# ===========================================================================
# 15. 文件保存：_save_txt / _save_txt_plan / _archive_external / get_txt_content
# ===========================================================================

def test_save_txt_real_file(monkeypatch, tmp_path):
    out_dir = tmp_path / "distill"
    monkeypatch.setattr(ud, "_OUTPUT_DIR", str(out_dir))
    path = ud._save_txt("hi", "conv_x")
    assert path.startswith(str(out_dir))
    assert path.endswith(".txt")
    with open(path, encoding="utf-8") as f:
        assert f.read() == "hi"


def test_save_txt_plan_real_file(monkeypatch, tmp_path):
    plan_dir = tmp_path / "plans"
    monkeypatch.setattr(ud, "_PLAN_DIR", str(plan_dir))
    path = ud._save_txt_plan("md", "daily_2026-10-14.md")
    assert path == str(plan_dir / "daily_2026-10-14.md")
    with open(path, encoding="utf-8") as f:
        assert f.read() == "md"


def test_archive_external_empty_returns_none(monkeypatch):
    monkeypatch.setattr(ud, "_ARCHIVE_BASE", "/should/not/be/used")
    assert ud._archive_external("", "daily", "2026-10-14", "f.txt") is None


def test_archive_external_success(monkeypatch, tmp_path):
    monkeypatch.setattr(ud, "_ARCHIVE_BASE", str(tmp_path / "arch"))
    path = ud._archive_external("data", "daily", "2026-10-14", "f.txt")
    assert path == str(tmp_path / "arch" / "daily" / "2026-10-14" / "f.txt")
    with open(path, encoding="utf-8") as f:
        assert f.read() == "data"


def test_archive_external_failure_returns_none(monkeypatch, tmp_path):
    # 用一个「文件」当基目录 -> makedirs 必然失败
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(ud, "_ARCHIVE_BASE", str(blocker))
    assert ud._archive_external("data", "daily", "2026-10-14", "f.txt") is None


def test_get_txt_content():
    assert ud.get_txt_content({"txt_content": "abc"}) == "abc"
    assert ud.get_txt_content({}) == ""


# ===========================================================================
# 16. 归档联动：distill_daily 里 save_txt/save_md 触发 archive
# ===========================================================================

async def test_distill_daily_archive_called(monkeypatch):
    arch = Recorder()
    install(monkeypatch, _gather_daily=lambda d: _daily_data(), call_llm=_llm({"headline": "H"}),
            _archive_external=arch)
    await ud.distill_daily("2026-10-14")
    kinds = [c[1].get("kind") for c in arch.calls]
    assert kinds == ["daily", "daily"]  # txt + md


# ===========================================================================
# 17. 提示词模板占位符自检（防手改漏占位）
# ===========================================================================

def test_prompt_templates_placeholders_consistent():
    assert "{chat_text}" in ud._PROMPT_CONV
    assert "{schedule_text}" in ud._PROMPT_SCHEDULE
    assert "{memory_text}" in ud._PROMPT_MEMORY
    for p in ("{chat_text}", "{schedule_text}", "{memory_text}"):
        assert p in ud._PROMPT_ALL
    for p in ("{date}", "{chat_text}", "{schedule_text}", "{note_text}",
              "{memory_text}", "{analysis_text}", "{market_text}", "{goal_text}"):
        assert p in ud._PROMPT_DAILY
    for p in ("{week_start}", "{week_end}", "{chat_text}", "{schedule_text}",
              "{note_text}", "{memory_text}"):
        assert p in ud._PROMPT_WEEKLY
    for p in ("{month}", "{daily_text}", "{weekly_text}"):
        assert p in ud._PROMPT_MONTHLY
    for p in ("{year}", "{monthly_text}"):
        assert p in ud._PROMPT_YEARLY


def test_type_labels_consistent():
    assert ud._TYPE_LABELS["personal_info"] == "个人信息"
    assert ud._TYPE_LABELS["experience"] == "经验技巧"


# ===========================================================================
# 18. 回归：None / 非预期类型 的健壮性
#     - start_time=NULL 属「已修复」的库可达崩溃（sch_update 可显式清空）。
#     - 下面带 xfail 的一组是「系统级、仅报告未修」的缺陷：`result.get(key, default)`
#       对显式 JSON null 不生效；落库循环与格式化层对 null/非 dict 元素无防护。
#       触发用例在此保留为可执行证据；一旦上游统一做 schema 归一化，它们会转为 XPASS。
# ===========================================================================

def test_regression_gather_daily_start_time_none(monkeypatch):
    """日程 start_time 可为 NULL（sch_update 显式清空）—— 不得崩溃。"""
    install(monkeypatch, sch_list=lambda **k: [
        {"status": "confirmed", "start_time": None, "title": "无时间"},
    ])
    out = ud._gather_daily("2026-10-14")
    assert "- [confirmed] ? | 无时间" in out["schedule_text"]


def test_regression_gather_weekly_start_time_none(monkeypatch):
    install(monkeypatch, sch_list=lambda **k: [
        {"status": "confirmed", "start_time": None, "title": "无时间"},
    ])
    out = ud._gather_weekly("2026-10-12")
    assert "- [confirmed] ? | 无时间" in out["schedule_text"]


@pytest.mark.xfail(reason="已知缺陷（仅报告）：experiences 混入非 dict / 其余字段为 null 时崩溃", strict=False)
async def test_regression_conv_non_dict_experience_and_null_lists(monkeypatch):
    """experiences 混入非 dict、其余列表为 null —— 应跳过而非崩溃。"""
    payload = {
        "experiences": ["纯文本经验", {"content": "有效经验", "importance": 2}],
        "key_decisions": None,
        "knowledge": None,
        "title": None,
        "summary": None,
        "tags": None,
    }
    install(
        monkeypatch,
        conv_get=lambda cid: {"title": "t", "created_at": "x", "summary": ""},
        msg_list=lambda cid: [],
        call_llm=_llm(payload),
    )
    out = await ud.distill_conversation("c1", save_txt=False)
    assert [c[1].get("content") for c in ud.mem_add.calls] == ["有效经验"]
    assert out["saved_count"] == 1
    assert ud.conv_update_title.calls == []
    assert ud.conv_update_summary.calls == []


@pytest.mark.xfail(reason="已知缺陷（仅报告）：key_decisions/knowledge 为 null 时落库循环崩溃", strict=False)
async def test_regression_conv_decisions_none_no_crash(monkeypatch):
    install(
        monkeypatch,
        conv_get=lambda cid: {"title": "t", "created_at": "x", "summary": ""},
        msg_list=lambda cid: [],
        call_llm=_llm({"experiences": None, "key_decisions": None, "knowledge": None}),
    )
    out = await ud.distill_conversation("c1", save_txt=False)
    assert out["saved_count"] == 0
    assert ud.mem_add.calls == []


@pytest.mark.xfail(reason="已知缺陷（仅报告）：insights 为 null 时落库循环崩溃", strict=False)
async def test_regression_daily_insights_null(monkeypatch):
    install(monkeypatch, _gather_daily=lambda d: _daily_data(),
            call_llm=_llm({"headline": "H", "insights": None}))
    out = await ud.distill_daily("2026-10-14", save_txt=False, save_md=False)
    assert out["saved_count"] == 0


@pytest.mark.xfail(reason="已知缺陷（仅报告）：lessons/patterns 为 null 时落库循环崩溃", strict=False)
async def test_regression_weekly_null_lists(monkeypatch):
    install(monkeypatch, _gather_weekly=lambda w: _weekly_data(),
            call_llm=_llm({"headline": "H", "lessons": None, "patterns": None}))
    out = await ud.distill_weekly("2026-10-12", save_txt=False, save_md=False)
    assert out["saved_count"] == 0


@pytest.mark.xfail(reason="已知缺陷（仅报告）：conv_distill 内字段为 null 时落库/格式化崩溃", strict=False)
async def test_regression_all_conv_distill_null_fields(monkeypatch):
    payload = {"conv_distill": {
        "experiences": None, "key_decisions": None,
        "title": None, "summary": None, "action_items": None,
    }}
    install(
        monkeypatch,
        conv_get=lambda cid: {"title": "t", "created_at": "x", "summary": ""},
        msg_list=lambda cid: [],
        call_llm=_llm(payload),
    )
    out = await ud.distill_all(conv_id="c1", save_txt=False)
    assert out["saved_count"] == 0
    assert ud.conv_update_title.calls == []
    assert ud.conv_update_summary.calls == []


@pytest.mark.xfail(reason="已知缺陷（仅报告）：conv_distill 为非 dict 时格式化/落库崩溃", strict=False)
async def test_regression_all_conv_distill_not_dict(monkeypatch):
    install(
        monkeypatch,
        conv_get=lambda cid: {"title": "t", "created_at": "x", "summary": ""},
        msg_list=lambda cid: [],
        call_llm=_llm({"conv_distill": ["not", "a", "dict"]}),
    )
    out = await ud.distill_all(conv_id="c1", save_txt=False)
    assert out["saved_count"] == 0
