"""backend/routers/chat.py 补网单测（worker-r4-chat）。

覆盖目标（按优先级）：
  1. 纯函数：_safe_int / _auto_title / _split_query / _preread_result_is_empty /
     _infer_skill_layer / _build_skill_injection
  2. 消息构建：_build_chat_messages（背景/学习进度/persona/技能/历史截断）
  3. 后台管线：_process_conv（提醒、集合清单、评估+预读、工具循环、收尾回答、
     落痕、异常降级）
  4. HTTP 端点：chat / regenerate / edit / delete / stop / stream-compat

纪律：所有网络出口（chat_stream / collection_hint_text / execute_tool /
check_reminders / validate_* / maybe_extract_memories / log_event）一律 monkeypatch，
绝不发真实请求、绝不写审计文件。
"""
import asyncio
import json

import pytest

from backend import database as db
from backend.routers import chat as chat_mod

pytestmark = pytest.mark.asyncio


# ──────────────────────────── 通用夹具 ────────────────────────────
@pytest.fixture(autouse=True)
def _clean_streams():
    """隔离模块级 _active_streams，避免跨测试污染 / 悬挂任务。"""
    chat_mod._active_streams.clear()
    yield
    for t in list(chat_mod._active_streams.values()):
        t.cancel()
    chat_mod._active_streams.clear()


@pytest.fixture
def cfg():
    return {
        "system_prompt": "SYS-PROMPT",
        "socratic_mode": False,
        "auditor_skill": {"enabled": False},
    }


def new_conv(title="New Chat"):
    return db.conv_create(title=title)["id"]


def drain(q):
    """取出队列内全部事件（到 None 为止），返回已解析的 dict 列表。"""
    out = []
    while not q.empty():
        ev = q.get_nowait()
        if ev is None:
            break
        out.append(json.loads(ev))
    return out


class Pipeline:
    """_process_conv 的可编程替身环境。"""

    def __init__(self):
        self.rounds = []           # 每轮 chat_stream(tools!=None) 要吐的 chunk 列表
        self.wrap = "WRAP-FINAL"   # tools=None 收尾调用的文本
        self.tools_schema = []
        self.reminders = ""
        self.coll_hint = ""
        self.execute_ret = {"result": "OK", "success": True}
        self.execute_raise = None
        self.exec_warnings = []    # validate_tool_result 返回
        self.output_warnings = []  # validate_output 返回
        self.proposals = []
        self.assessment = None     # 覆盖 assess_round 返回；None=空评估
        self.assess_raise = None
        # 记录
        self.executed = []
        self.stream_tool_args = []
        self.extract_calls = []
        self.audit_events = []
        self._round_idx = 0


def install(monkeypatch, pipe: Pipeline):
    """把 chat 模块的全部外部依赖替换为 pipe 控制的替身。"""
    async def _coll_hint():
        return pipe.coll_hint

    async def _schema():
        return pipe.tools_schema

    async def _exec(name, args, conv_id=""):
        pipe.executed.append((name, args))
        if pipe.execute_raise is not None:
            raise pipe.execute_raise
        return dict(pipe.execute_ret)

    async def _extract(*a, **k):
        pipe.extract_calls.append((a, k))

    async def _cs(messages, tools=None, temperature=None, max_tokens=None, provider_name=""):
        pipe.stream_tool_args.append(tools)
        if tools is None:                       # 收尾回答调用
            yield {"type": "text", "content": pipe.wrap}
            return
        i = pipe._round_idx
        pipe._round_idx += 1
        for c in (pipe.rounds[i] if i < len(pipe.rounds) else []):
            yield c

    monkeypatch.setattr(chat_mod, "collection_hint_text", _coll_hint)
    monkeypatch.setattr(chat_mod, "get_tools_schema", _schema)
    monkeypatch.setattr(chat_mod, "execute_tool", _exec)
    monkeypatch.setattr(chat_mod, "chat_stream", _cs)
    monkeypatch.setattr(chat_mod, "check_reminders", lambda: pipe.reminders)
    monkeypatch.setattr(chat_mod, "maybe_extract_memories", _extract)
    monkeypatch.setattr(chat_mod, "get_pending_proposals", lambda: pipe.proposals)
    monkeypatch.setattr(chat_mod, "validate_tool_result",
                        lambda name, args, text: pipe.exec_warnings)
    monkeypatch.setattr(chat_mod, "validate_output",
                        lambda text, conv_id="": pipe.output_warnings)
    monkeypatch.setattr(chat_mod, "log_event",
                        lambda et, data, conv_id="": pipe.audit_events.append((et, data)))

    # assessor 是函数内延迟 import，patch 模块属性即可生效
    def _assess(msg):
        if pipe.assess_raise is not None:
            raise pipe.assess_raise
        if pipe.assessment is not None:
            return pipe.assessment
        return {"intents": [], "suggested": [], "preread": [], "hint": "",
                "text_len": len(msg or "")}

    monkeypatch.setattr("backend.assessor.assess_round", _assess)
    monkeypatch.setattr("backend.assessor.summarize_assessment",
                        lambda a: {"intents": a.get("intents", [])})


async def run_conv(pipe, conv_id, user_message, msgs=None, cfg=None, user_msg_id=None):
    q = asyncio.Queue()
    msgs = msgs if msgs is not None else [{"role": "system", "content": "SYS"}]
    cfg = cfg if cfg is not None else {}
    await chat_mod._process_conv(conv_id, user_message, msgs, cfg, q,
                                 user_msg_id=user_msg_id)
    return drain(q), msgs


# ════════════════════════════ 1. 纯函数 ════════════════════════════
class TestSafeInt:
    def test_int_passthrough(self):
        assert chat_mod._safe_int("42", 7) == 42
        assert chat_mod._safe_int(42, 7) == 42

    def test_bad_values_fallback(self):
        assert chat_mod._safe_int(None, 7) == 7
        assert chat_mod._safe_int("abc", 7) == 7
        assert chat_mod._safe_int("", 7) == 7
        assert chat_mod._safe_int([], 7) == 7


class TestAutoTitle:
    def test_empty_and_whitespace(self):
        assert chat_mod._auto_title("") == "新对话"
        assert chat_mod._auto_title("   \n ") == "新对话"
        assert chat_mod._auto_title(None) == "新对话"

    def test_plain_text_trims_punct_and_ws(self):
        assert chat_mod._auto_title("  帮我写一份周报。！") == "帮我写一份周报"

    def test_long_text_truncated_to_max_len(self):
        assert chat_mod._auto_title("字" * 100, max_len=10) == "字" * 10

    def test_pure_bilibili_link(self):
        assert chat_mod._auto_title("https://www.bilibili.com/video/BV1xx") == "B站视频提取与总结"
        assert chat_mod._auto_title("https://b23.tv/abc") == "B站视频提取与总结"

    def test_pure_youtube_link(self):
        assert chat_mod._auto_title("https://youtube.com/watch?v=1") == "YouTube视频提取与总结"
        assert chat_mod._auto_title("https://youtu.be/abc") == "YouTube视频提取与总结"

    def test_pure_zhihu_and_other_link(self):
        assert chat_mod._auto_title("https://zhuanlan.zhihu.com/p/1") == "知乎文章"
        assert chat_mod._auto_title("https://example.com/a") == "链接内容提取"

    def test_short_text_with_video_link(self):
        assert chat_mod._auto_title("总结 https://b23.tv/abc") == "B站视频总结"
        assert chat_mod._auto_title("总结 https://youtu.be/abc") == "YouTube视频总结"

    def test_link_plus_long_text_uses_text(self):
        assert chat_mod._auto_title("帮我总结这个视频 https://b23.tv/abc") == "帮我总结这个视频"

    @pytest.mark.xfail(strict=False, reason="BUG: 纯标点消息（无任何链接）被误判为「链接内容提取」——t 为空既可能来自纯链接，也可能来自纯标点（chat.py:69 未区分）")
    def test_punctuation_only_message_is_not_a_link(self):
        assert chat_mod._auto_title("！！！") == "新对话"


class TestSplitQuery:
    def test_english_words_min_len_3(self):
        out = chat_mod._split_query("fix the ab bug now")
        assert "fix" in out and "the" in out and "bug" in out and "now" in out
        assert "ab" not in out  # <3 字符丢弃

    def test_chinese_ngrams(self):
        out = chat_mod._split_query("写周报")
        assert "写周" in out and "周报" in out and "写周报" in out

    def test_dedup_preserves_order(self):
        out = chat_mod._split_query("ab ab ab abc")
        assert out.count("abc") == 1

    def test_empty(self):
        assert chat_mod._split_query("") == []


class TestPrereadEmpty:
    def test_exact_empty_marker(self):
        assert chat_mod._preread_result_is_empty("未找到相关记忆。") is True

    def test_blank(self):
        assert chat_mod._preread_result_is_empty("") is True
        assert chat_mod._preread_result_is_empty(None) is True
        assert chat_mod._preread_result_is_empty("   ") is True

    def test_prefix_variant_within_limit(self):
        assert chat_mod._preread_result_is_empty("未找到相关记忆。 试试别的。") is True

    def test_long_text_not_empty_even_if_prefix(self):
        long = "未找到相关记忆。" + "补充" * 40
        assert chat_mod._preread_result_is_empty(long) is False

    def test_other_content_not_empty(self):
        assert chat_mod._preread_result_is_empty("本地暂无黄金仓位") is False


class TestInferSkillLayer:
    def test_short_content_is_l1(self):
        assert chat_mod._infer_skill_layer("短内容") == "L1"

    def test_long_content_without_steps_is_l1(self):
        assert chat_mod._infer_skill_layer("段落文字\n" * 400) == "L1"

    def test_long_content_with_ordered_steps_is_l2(self):
        body = "简介" * 400          # 800 字 >= SKILL_FALLBACK_L2_MIN_CHARS
        steps = "\n".join(f"{i}. 执行步骤内容若干" for i in range(1, 7))
        assert chat_mod._infer_skill_layer(body + "\n" + steps) == "L2"

    def test_long_content_with_many_step_chars_is_l2(self):
        body = "简介" * 200
        # 仅 3 条步骤，但累计字符 >= 400
        steps = "\n".join(f"{i}. " + "步骤" * 80 for i in range(1, 4))
        assert chat_mod._infer_skill_layer(body + "\n" + steps) == "L2"

    def test_none_safe(self):
        assert chat_mod._infer_skill_layer(None) == "L1"


class TestBuildSkillInjection:
    def test_empty_or_short_query(self):
        assert chat_mod._build_skill_injection("") == ""
        assert chat_mod._build_skill_injection("x") == ""

    def test_no_hit_returns_empty(self, test_db):
        db.mem_add("skill", "技能：写周报\n层级：L1\n触发：写周报", keywords="周报", dedup=False)
        assert chat_mod._build_skill_injection("完全无关的英文 query zzzz") == ""

    def test_l1_full_injection(self, test_db):
        db.mem_add("skill", "技能：写周报\n层级：L1\n触发：写周报\n步骤全文",
                   keywords="周报", dedup=False)
        out = chat_mod._build_skill_injection("帮我写周报")
        assert "【已启用技能 · 必须遵循】" in out
        assert "### 技能：写周报" in out
        assert "步骤全文" in out

    def test_l2_summary_when_no_detail_requested(self, test_db):
        db.mem_add("skill", "技能：写周报\n层级：L2\n触发：写周报\n" + "很长内容" * 60,
                   keywords="周报", dedup=False)
        out = chat_mod._build_skill_injection("帮我写周报")
        assert "摘要：" in out and "如需详细步骤请询问" in out

    def test_l2_full_when_detail_requested(self, test_db):
        db.mem_add("skill", "技能：写周报\n层级：L2\n触发：写周报\n" + "很长内容" * 60,
                   keywords="周报", dedup=False)
        out = chat_mod._build_skill_injection("帮我写周报的详细步骤")
        assert "摘要：" not in out
        assert "很长内容" in out

    def test_missing_layer_uses_fallback(self, test_db):
        # 无「层级：」行，长内容 + 有序步骤 → 兜底判 L2 → 摘要
        # 注意：查询/技能名不得含 want_detail 触发词（详细/步骤/流程…），否则会走全量分支
        body = "背景说明" * 200
        steps = "\n".join(f"{i}. 步骤内容" for i in range(1, 8))
        db.mem_add("skill", "技能：航海日志\n触发：航海日志\n" + body + "\n" + steps,
                   keywords="航海日志", dedup=False)
        out = chat_mod._build_skill_injection("航海日志")
        assert "摘要：" in out

    def test_db_error_returns_empty(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("db down")
        monkeypatch.setattr(chat_mod.db, "mem_list", _boom)
        assert chat_mod._build_skill_injection("任何查询") == ""

    def test_skill_without_prefix_ignored(self, test_db):
        db.mem_add("skill", "非标准技能文本 触发：x", keywords="x", dedup=False)
        assert chat_mod._build_skill_injection("非标准技能文本") == ""


# ════════════════════════════ 2. _maybe_auto_title ════════════════════════════
class TestMaybeAutoTitle:
    def test_sets_title_for_new_chat(self, test_db):
        cid = new_conv()
        chat_mod._maybe_auto_title(cid, "帮我写周报")
        assert db.conv_get(cid)["title"] == "帮我写周报"

    def test_keeps_user_custom_title(self, test_db):
        cid = new_conv(title="我的自定义标题")
        chat_mod._maybe_auto_title(cid, "帮我写周报")
        assert db.conv_get(cid)["title"] == "我的自定义标题"

    def test_empty_message_keeps_placeholder(self, test_db):
        cid = new_conv()
        chat_mod._maybe_auto_title(cid, "   ")
        assert db.conv_get(cid)["title"] == "New Chat"

    def test_missing_conv_no_crash(self, test_db):
        chat_mod._maybe_auto_title("no-such-conv", "x")   # 不抛异常即通过

    def test_error_is_swallowed(self, test_db, monkeypatch):
        monkeypatch.setattr(chat_mod.db, "conv_get",
                            lambda cid: (_ for _ in ()).throw(RuntimeError("boom")))
        chat_mod._maybe_auto_title("x", "y")   # 异常被吞


class TestConvPersona:
    def test_returns_bound_persona(self, test_db):
        cid = new_conv()
        db.conv_update_persona(cid, "导师")
        assert chat_mod._conv_persona(cid) == "导师"

    def test_missing_persona_returns_empty(self, test_db):
        cid = new_conv()
        assert chat_mod._conv_persona(cid) == ""

    def test_db_error_returns_empty(self, test_db, monkeypatch):
        monkeypatch.setattr(chat_mod.db, "conv_get",
                            lambda c: (_ for _ in ()).throw(RuntimeError("db down")))
        assert chat_mod._conv_persona("c") == ""


# ════════════════════════════ 3. _build_chat_messages ════════════════════════════
class TestBuildChatMessages:
    def test_minimal_system_prompt(self, test_db, cfg):
        cid = new_conv()
        msgs = chat_mod._build_chat_messages(cid, cfg, "")
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"].startswith("SYS-PROMPT")

    def test_background_injection(self, test_db, cfg):
        cid = new_conv()
        db.conv_update_background(cid, "你是航海助手")
        msgs = chat_mod._build_chat_messages(cid, cfg, "")
        assert "## 对话背景" in msgs[0]["content"]
        assert "你是航海助手" in msgs[0]["content"]
        assert "【当前时间感知】" in msgs[0]["content"]

    def test_learning_progress_injection(self, test_db, cfg):
        cid = new_conv()
        db.conv_update_learning_progress(cid, {
            "doc_id": 7, "title": "线性代数", "chunk_index": 3, "total_chunks": 20})
        msgs = chat_mod._build_chat_messages(cid, cfg, "")
        assert "## 学习进度" in msgs[0]["content"]
        assert "read_document_chunk(item_id=7, chunk_index=4)" in msgs[0]["content"]

    def test_persona_found_and_missing(self, test_db, cfg):
        cfg = dict(cfg)
        cfg["personas"] = [{"name": "导师", "system_prompt": "耐心讲解"}]
        cid = new_conv()
        msgs = chat_mod._build_chat_messages(cid, cfg, "导师")
        assert "## 当前工作模式: 导师" in msgs[0]["content"]
        msgs2 = chat_mod._build_chat_messages(cid, cfg, "不存在的人")
        assert "当前工作模式" not in msgs2[0]["content"]

    def test_socratic_and_auditor_toggles(self, test_db):
        cid = new_conv()
        on = {"system_prompt": "S", "socratic_mode": True, "auditor_skill": {"enabled": True}}
        c = chat_mod._build_chat_messages(cid, on, "")[0]["content"]
        assert "苏格拉底追问原则" in c
        off = {"system_prompt": "S", "socratic_mode": False, "auditor_skill": {"enabled": False}}
        c2 = chat_mod._build_chat_messages(cid, off, "")[0]["content"]
        assert "苏格拉底追问原则" not in c2

    def test_edit_capability_and_sandbox_declared(self, test_db, cfg):
        cid = new_conv()
        c = chat_mod._build_chat_messages(cid, cfg, "")[0]["content"]
        assert "## 你的编辑能力" in c
        assert "## 代码执行沙箱说明" in c
        assert "## 你的学术检索能力" in c

    def test_history_summary_folded_into_system(self, test_db, cfg):
        cid = new_conv()
        db.msg_add(cid, "system", "[历史摘要] 之前聊了天气")
        db.msg_add(cid, "user", "继续")
        msgs = chat_mod._build_chat_messages(cid, cfg, "")
        assert "[历史摘要] 之前聊了天气" in msgs[0]["content"]
        # system 消息不重复出现在 history 部分
        roles = [m["role"] for m in msgs]
        assert roles.count("system") == 1

    def test_history_cap(self, test_db, cfg):
        cid = new_conv()
        for i in range(10):
            db.msg_add(cid, "user", f"m{i}")
        cfg2 = dict(cfg, chat_history_cap=3, chat_history_max_tokens=0)
        msgs = chat_mod._build_chat_messages(cid, cfg2, "")
        assert [m["content"] for m in msgs[1:]] == ["m7", "m8", "m9"]

    def test_token_truncation_keeps_newest(self, test_db, cfg):
        cid = new_conv()
        for i in range(6):
            db.msg_add(cid, "user", "字" * 30)   # 每条约 10 token
        cfg2 = dict(cfg, chat_history_cap=100, chat_history_max_tokens=25)
        msgs = chat_mod._build_chat_messages(cid, cfg2, "")
        assert len(msgs) - 1 < 6   # 发生了截断
        assert len(msgs) - 1 >= 1

    def test_memory_injection_and_recall_trace(self, test_db, cfg, monkeypatch):
        cid = new_conv()
        monkeypatch.setattr(chat_mod, "build_memory_injection",
                            lambda current_query="", with_meta=False: ("MEM-INJ", {"mode": "query"}))
        msgs = chat_mod._build_chat_messages(cid, cfg, "", current_query="x")
        assert "MEM-INJ" in msgs[0]["content"]
        traces = db.trace_list(cid, "mem_recall")
        assert len(traces) == 1

    def test_skill_injection_appended(self, test_db, cfg, monkeypatch):
        cid = new_conv()
        monkeypatch.setattr(chat_mod, "build_memory_injection",
                            lambda current_query="", with_meta=False: ("", {}))
        monkeypatch.setattr(chat_mod, "_build_skill_injection", lambda current_query="": "SKILL-INJ")
        msgs = chat_mod._build_chat_messages(cid, cfg, "", current_query="x")
        assert "SKILL-INJ" in msgs[0]["content"]

    def test_conv_get_failure_degrades(self, test_db, cfg, monkeypatch):
        """背景/persona 读取失败时不应炸掉消息构建（fail-safe）。"""
        cid = new_conv()
        monkeypatch.setattr(chat_mod.db, "conv_get",
                            lambda c: (_ for _ in ()).throw(RuntimeError("db down")))
        msgs = chat_mod._build_chat_messages(cid, cfg, "任意Persona")
        assert msgs[0]["role"] == "system"

    def test_mem_recall_trace_failure_degrades(self, test_db, cfg, monkeypatch):
        cid = new_conv()
        monkeypatch.setattr(chat_mod, "build_memory_injection",
                            lambda current_query="", with_meta=False: ("MEM", {"mode": "query"}))
        monkeypatch.setattr(chat_mod.db, "trace_add",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("trace down")))
        msgs = chat_mod._build_chat_messages(cid, cfg, "")
        assert "MEM" in msgs[0]["content"]


# ════════════════════════════ 4. _process_conv 管线 ════════════════════════════
class TestProcessConv:
    async def test_simple_text_persists_and_emits(self, monkeypatch, test_db):
        pipe = Pipeline()
        pipe.rounds = [[{"type": "text", "content": "你好"}]]
        install(monkeypatch, pipe)
        cid = new_conv()
        events, _ = await run_conv(pipe, cid, "hi")
        assert events[-1]["type"] == "done"
        assert any(e["type"] == "full_text" and e["content"] == "你好" for e in events)
        saved = [m for m in db.msg_list(cid) if m["role"] == "assistant"]
        assert saved and saved[-1]["content"] == "你好"
        assert pipe.extract_calls   # maybe_extract_memories 被调用

    async def test_reminder_event(self, monkeypatch, test_db):
        pipe = Pipeline()
        pipe.rounds = [[{"type": "text", "content": "ok"}]]
        pipe.reminders = "该吃药了"
        install(monkeypatch, pipe)
        events, _ = await run_conv(pipe, new_conv(), "hi")
        assert events[0]["type"] == "reminder"
        assert events[0]["content"] == "该吃药了"

    async def test_collection_hint_appended_to_system(self, monkeypatch, test_db):
        pipe = Pipeline()
        pipe.rounds = [[{"type": "text", "content": "ok"}]]
        pipe.coll_hint = "KB: foo"
        install(monkeypatch, pipe)
        _, msgs = await run_conv(pipe, new_conv(), "hi")
        assert msgs[0]["content"].endswith("KB: foo")

    async def test_collection_hint_failure_degrades(self, monkeypatch, test_db):
        pipe = Pipeline()
        pipe.rounds = [[{"type": "text", "content": "ok"}]]
        install(monkeypatch, pipe)

        async def _boom():
            raise RuntimeError("gateway down")
        monkeypatch.setattr(chat_mod, "collection_hint_text", _boom)
        events, msgs = await run_conv(pipe, new_conv(), "hi")
        assert events[-1]["type"] == "done"
        assert "None" not in (msgs[0]["content"] or "")

    async def test_no_assistant_text_not_saved(self, monkeypatch, test_db):
        pipe = Pipeline()
        pipe.rounds = [[{"type": "thinking", "content": "只是思考"}]]
        install(monkeypatch, pipe)
        cid = new_conv()
        events, _ = await run_conv(pipe, cid, "hi")
        assert events[-1]["type"] == "done"
        assert not [m for m in db.msg_list(cid) if m["role"] == "assistant"]

    async def test_tool_round_and_bubbles(self, monkeypatch, test_db):
        pipe = Pipeline()
        pipe.rounds = [
            [{"type": "text", "content": "先查"},
             {"type": "tool_call", "id": "c1", "name": "search_memory", "args": {"keyword": "x"}}],
            [{"type": "text", "content": "答案"}],
        ]
        pipe.execute_ret = {"result": "命中 1 条", "success": True}
        install(monkeypatch, pipe)
        cid = new_conv()
        events, msgs = await run_conv(pipe, cid, "hi", user_msg_id=None)
        types = [e["type"] for e in events]
        assert "tool_call_start" in types and "tool_call_end" in types
        end = next(e for e in events if e["type"] == "tool_call_end")
        assert end["id"] == "c1" and end["success"] is True
        assert end["result_summary"] == "命中 1 条"
        assert any(e["type"] == "tool_results" for e in events)
        # 工具结果作为 role=tool 回灌
        assert msgs[-1]["role"] == "tool" and msgs[-1]["content"] == "命中 1 条"
        # tool_call trace 落库
        assert db.trace_list(cid, "tool_call")
        assert pipe.executed == [("search_memory", {"keyword": "x"})]

    async def test_tool_call_without_id_gets_fallback(self, monkeypatch, test_db):
        pipe = Pipeline()
        pipe.rounds = [
            [{"type": "tool_call", "name": "kb_stats", "args": {}}],
            [{"type": "text", "content": "done"}],
        ]
        install(monkeypatch, pipe)
        events, msgs = await run_conv(pipe, new_conv(), "hi")
        end = next(e for e in events if e["type"] == "tool_call_end")
        assert end["id"] == "call_0_0"
        assert msgs[-2]["tool_calls"][0]["id"] == "call_0_0"

    async def test_tool_error_result_passthrough(self, monkeypatch, test_db):
        pipe = Pipeline()
        pipe.rounds = [
            [{"type": "tool_call", "id": "e1", "name": "read_note", "args": {}}],
            [{"type": "text", "content": "ok"}],
        ]
        pipe.execute_ret = {"error": "笔记不存在"}
        install(monkeypatch, pipe)
        _, msgs = await run_conv(pipe, new_conv(), "hi")
        assert msgs[-1]["content"] == "[工具错误] 笔记不存在"

    async def test_tool_exception_marks_failure(self, monkeypatch, test_db):
        pipe = Pipeline()
        pipe.rounds = [
            [{"type": "tool_call", "id": "x1", "name": "boom_tool", "args": {}}],
            [{"type": "text", "content": "ok"}],
        ]
        pipe.execute_raise = RuntimeError("kaboom")
        install(monkeypatch, pipe)
        events, msgs = await run_conv(pipe, new_conv(), "hi")
        end = next(e for e in events if e["type"] == "tool_call_end")
        assert end["success"] is False
        assert "kaboom" in end["result_summary"]
        # 审计 tool_error
        assert any(et == "tool_error" for et, _ in pipe.audit_events)
        # 异常路径同时写入 result，故回灌内容是「工具执行异常: …」而非「[工具错误] …」
        assert msgs[-1]["content"] == "工具执行异常: kaboom"

    async def test_success_false_not_treated_as_success(self, monkeypatch, test_db):
        pipe = Pipeline()
        pipe.rounds = [
            [{"type": "tool_call", "id": "s1", "name": "t", "args": {}}],
            [{"type": "text", "content": "ok"}],
        ]
        pipe.execute_ret = {"result": "partial", "success": False}
        install(monkeypatch, pipe)
        events, _ = await run_conv(pipe, new_conv(), "hi")
        end = next(e for e in events if e["type"] == "tool_call_end")
        assert end["success"] is False

    async def test_confirm_result_emits_proposal_and_remaps(self, monkeypatch, test_db):
        pipe = Pipeline()
        pipe.rounds = [
            [{"type": "tool_call", "id": "p1", "name": "delete_note", "args": {"note_id": 3}}],
            [{"type": "text", "content": "ok"}],
        ]
        pipe.execute_ret = {"result": "待确认", "success": True, "confirm": True,
                            "confirm_type": "delete", "confirm_id": "abc"}
        install(monkeypatch, pipe)
        events, _ = await run_conv(pipe, new_conv(), "hi")
        prop = next(e for e in events if e["type"] == "proposal")
        assert prop["data"]["type"] == "delete"
        assert prop["data"]["id"] == "abc"

    async def test_show_bubbles_disabled_uses_text_passthrough(self, monkeypatch, test_db):
        pipe = Pipeline()
        pipe.rounds = [
            [{"type": "tool_call", "id": "b1", "name": "t", "args": {}}],
            [{"type": "text", "content": "ok"}],
        ]
        pipe.execute_ret = {"result": "R", "success": True}
        install(monkeypatch, pipe)
        dbc = {"trace": {"show_tool_bubbles": False}}
        events, _ = await run_conv(pipe, new_conv(), "hi", cfg=dbc)
        assert not any(e["type"] == "tool_call_start" for e in events)
        assert any(e["type"] == "text" and "[t]: R" in e["content"] for e in events)

    async def test_trace_disabled_skips_trace_write(self, monkeypatch, test_db):
        pipe = Pipeline()
        pipe.rounds = [
            [{"type": "tool_call", "id": "d1", "name": "t", "args": {}}],
            [{"type": "text", "content": "ok"}],
        ]
        install(monkeypatch, pipe)
        cid = new_conv()
        await run_conv(pipe, cid, "hi", cfg={"trace": {"enabled": False}})
        assert not [t for t in db.trace_list(cid, "tool_call")]

    async def test_execution_validator_warnings(self, monkeypatch, test_db):
        pipe = Pipeline()
        pipe.rounds = [
            [{"type": "tool_call", "id": "v1", "name": "t", "args": {}}],
            [{"type": "text", "content": "ok"}],
        ]
        pipe.exec_warnings = [{"level": "warning", "message": "结果可疑", "type": "suspicious"}]
        install(monkeypatch, pipe)
        cid = new_conv()
        events, _ = await run_conv(pipe, cid, "hi")
        w = next(e for e in events if e["type"] == "warning")
        assert w["message"] == "结果可疑" and w["tool"] == "t"
        assert db.trace_list(cid, "validation")

    async def test_validation_disabled_by_cfg(self, monkeypatch, test_db):
        pipe = Pipeline()
        pipe.rounds = [
            [{"type": "tool_call", "id": "v2", "name": "t", "args": {}}],
            [{"type": "text", "content": "ok"}],
        ]
        pipe.exec_warnings = [{"level": "warning", "message": "m", "type": "t"}]
        install(monkeypatch, pipe)
        cfg = {"validators": {"execution": {"enabled": False}}}
        events, _ = await run_conv(pipe, new_conv(), "hi", cfg=cfg)
        assert not any(e["type"] == "warning" for e in events)

    async def test_output_validator_warnings(self, monkeypatch, test_db):
        pipe = Pipeline()
        pipe.rounds = [[{"type": "text", "content": "绝对正确"}]]
        pipe.output_warnings = [{"level": "info", "message": "绝对化表述"}]
        install(monkeypatch, pipe)
        events, _ = await run_conv(pipe, new_conv(), "hi")
        assert any(e["type"] == "warning" and e["message"] == "绝对化表述" for e in events)

    async def test_pending_proposals_event(self, monkeypatch, test_db):
        pipe = Pipeline()
        pipe.rounds = [[{"type": "text", "content": "ok"}]]
        pipe.proposals = [{"id": "p", "type": "delete"}]
        install(monkeypatch, pipe)
        events, _ = await run_conv(pipe, new_conv(), "hi")
        assert any(e["type"] == "proposals" for e in events)

    async def test_wrapup_when_rounds_exhausted(self, monkeypatch, test_db):
        pipe = Pipeline()
        # 连续 10 轮都有 tool_call → 触发收尾回答
        pipe.rounds = [[{"type": "tool_call", "id": f"w{i}", "name": "t", "args": {}}
                        for i in range(1)] for i in range(10)]
        pipe.wrap = "最终正式回答"
        install(monkeypatch, pipe)
        cid = new_conv()
        events, _ = await run_conv(pipe, cid, "hi")
        assert any(e["type"] == "full_text" and e["content"] == "最终正式回答" for e in events)
        saved = [m for m in db.msg_list(cid) if m["role"] == "assistant"]
        assert saved[-1]["content"] == "最终正式回答"
        # 收尾调用 tools=None；10 轮工具调用 tools!=None
        assert pipe.stream_tool_args.count(None) == 1
        assert len([t for t in pipe.stream_tool_args if t is not None]) == 10

    async def test_wrapup_failure_degrades(self, monkeypatch, test_db):
        pipe = Pipeline()
        pipe.rounds = [[{"type": "tool_call", "id": f"w{i}", "name": "t", "args": {}}]
                       for i in range(10)]
        install(monkeypatch, pipe)

        async def _cs(messages, tools=None, temperature=None, max_tokens=None, provider_name=""):
            if tools is None:
                raise RuntimeError("wrap fail")
            yield {"type": "tool_call", "id": "z", "name": "t", "args": {}}
        monkeypatch.setattr(chat_mod, "chat_stream", _cs)
        events, _ = await run_conv(pipe, new_conv(), "hi")
        assert events[-1]["type"] == "done"   # 降级后仍正常收尾

    async def test_assessment_preread_injected_and_traced(self, monkeypatch, test_db):
        pipe = Pipeline()
        pipe.rounds = [[{"type": "text", "content": "ok"}]]
        pipe.assessment = {
            "intents": ["recall"],
            "suggested": [{"tool": "search_memory", "why": "w", "confidence": "medium"}],
            "preread": [{"tool": "search_memory", "args": {"keyword": "k"}}],
            "hint": "HINT-LINE",
            "text_len": 2,
        }
        pipe.execute_ret = {"result": "记忆内容", "success": True}
        install(monkeypatch, pipe)
        _, msgs = await run_conv(pipe, new_conv(), "hi")
        assert "HINT-LINE" in msgs[0]["content"]
        assert "[系统已自动执行 search_memory]" in msgs[0]["content"]
        assert "已由系统自动执行" in msgs[0]["content"]

    async def test_assessment_preread_empty_not_injected(self, monkeypatch, test_db):
        """空预读结果不注入 system（负向信号），但仍落 preread/admin 痕迹。

        备注（设计取舍，见 assess_outcome）：预读无论结果是否为空都记入 preread_tools，
        故 missed 不包含它 —— 即便结果为空、模型从未拿到内容，也不会出现在 missed 里。
        """
        pipe = Pipeline()
        pipe.rounds = [[{"type": "text", "content": "ok"}]]
        pipe.assessment = {
            "intents": [], "suggested": [{"tool": "search_memory", "why": "w", "confidence": "medium"}],
            "preread": [{"tool": "search_memory", "args": {"keyword": "k"}}],
            "hint": "HINT", "text_len": 2,
        }
        pipe.execute_ret = {"result": "未找到相关记忆。", "success": True}
        install(monkeypatch, pipe)
        cid = new_conv()
        _, msgs = await run_conv(pipe, cid, "hi")
        assert "[系统已自动执行" not in msgs[0]["content"]
        assert "HINT" in msgs[0]["content"]          # 评估 hint 仍注入
        # assess / preread / assess_outcome 留痕
        assert db.trace_list(cid, "assess")
        pr = db.trace_list(cid, "preread")
        assert pr and json.loads(pr[0]["data"])["empty"] is True
        outcome = db.trace_list(cid, "assess_outcome")
        assert outcome and json.loads(outcome[0]["data"])["missed"] == []

    async def test_assess_outcome_missed_when_never_executed(self, monkeypatch, test_db):
        """建议了、且既非模型调用也非预读 → 必须落进 missed（漏执行检测的核心价值）。"""
        pipe = Pipeline()
        pipe.rounds = [[{"type": "text", "content": "ok"}]]
        pipe.assessment = {
            "intents": [], "suggested": [{"tool": "add_note", "why": "w", "confidence": "high"}],
            "preread": [], "hint": "H", "text_len": 2,
        }
        install(monkeypatch, pipe)
        cid = new_conv()
        await run_conv(pipe, cid, "hi")
        outcome = db.trace_list(cid, "assess_outcome")
        data = json.loads(outcome[0]["data"])
        assert data["missed"] == ["add_note"] and data["called"] == []

    async def test_preread_missing_arg_degrades(self, monkeypatch, test_db):
        pipe = Pipeline()
        pipe.rounds = [[{"type": "text", "content": "ok"}]]
        pipe.assessment = {"intents": [], "suggested": [], "preread": [{"tool": "search_memory"}],
                           "hint": "", "text_len": 1}
        install(monkeypatch, pipe)
        events, _ = await run_conv(pipe, new_conv(), "hi")
        assert events[-1]["type"] == "done"

    async def test_assessment_failure_degrades(self, monkeypatch, test_db):
        pipe = Pipeline()
        pipe.rounds = [[{"type": "text", "content": "ok"}]]
        pipe.assess_raise = RuntimeError("assessor down")
        install(monkeypatch, pipe)
        events, _ = await run_conv(pipe, new_conv(), "hi")
        assert events[-1]["type"] == "done"

    async def test_outer_exception_emits_error_event(self, monkeypatch, test_db):
        pipe = Pipeline()
        install(monkeypatch, pipe)

        async def _boom():
            raise RuntimeError("schema down")
        monkeypatch.setattr(chat_mod, "get_tools_schema", _boom)
        cid = new_conv()
        events, _ = await run_conv(pipe, cid, "hi")
        err = next(e for e in events if e["type"] == "error")
        assert "schema down" in err["message"]
        assert db.trace_list(cid, "error")

    async def test_audit_disabled_no_conversation_complete(self, monkeypatch, test_db):
        pipe = Pipeline()
        pipe.rounds = [[{"type": "text", "content": "ok"}]]
        install(monkeypatch, pipe)
        await run_conv(pipe, new_conv(), "hi", cfg={"audit": {"enabled": False}})
        assert not any(et == "conversation_complete" for et, _ in pipe.audit_events)

    async def test_tool_structured_code_fields_in_trace(self, monkeypatch, test_db):
        pipe = Pipeline()
        pipe.rounds = [
            [{"type": "tool_call", "id": "c9", "name": "execute_code", "args": {}}],
            [{"type": "text", "content": "ok"}],
        ]
        pipe.execute_ret = {"result": "out", "success": True, "stdout": "S" * 5000,
                            "stderr": "e", "exit_code": 0, "lang": "python"}
        install(monkeypatch, pipe)
        cid = new_conv()
        events, _ = await run_conv(pipe, cid, "hi")
        end = next(e for e in events if e["type"] == "tool_call_end")
        assert end["stdout"] == "S" * 5000 and end["exit_code"] == 0 and end["lang"] == "python"
        tr = db.trace_list(cid, "tool_call")[0]
        data = json.loads(tr["data"])
        assert len(data["stdout"]) == 4000   # 截断到 4000
        assert data["lang"] == "python"

    async def test_wrapup_collects_thinking(self, monkeypatch, test_db):
        """收尾回答路径的 thinking 分支也应被收集并持久化。"""
        pipe = Pipeline()
        pipe.rounds = [[{"type": "tool_call", "id": f"w{i}", "name": "t", "args": {}}]
                       for i in range(10)]
        install(monkeypatch, pipe)

        async def _cs(messages, tools=None, temperature=None, max_tokens=None, provider_name=""):
            if tools is None:
                yield {"type": "thinking", "content": "深思"}
                yield {"type": "text", "content": "终答"}
                return
            yield {"type": "tool_call", "id": "z", "name": "t", "args": {}}
        monkeypatch.setattr(chat_mod, "chat_stream", _cs)
        cid = new_conv()
        await run_conv(pipe, cid, "hi")
        saved = [m for m in db.msg_list(cid) if m["role"] == "assistant"]
        assert saved[-1]["content"] == "终答"
        assert saved[-1]["thinking"] == "深思"

    async def test_trace_add_failure_is_failsafe(self, monkeypatch, test_db):
        """所有 trace_add 失败都必须被吞掉，不阻断对话（评估/预读/工具/校验/漏执行）。"""
        pipe = Pipeline()
        pipe.rounds = [
            [{"type": "tool_call", "id": "t1", "name": "t", "args": {}}],
            [{"type": "text", "content": "ok"}],
        ]
        pipe.exec_warnings = [{"level": "warning", "message": "m", "type": "t"}]
        pipe.assessment = {"intents": [], "suggested": [], "preread": [
            {"tool": "search_memory", "args": {"keyword": "k"}}], "hint": "", "text_len": 1}
        install(monkeypatch, pipe)
        monkeypatch.setattr(chat_mod.db, "trace_add",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("trace down")))
        events, _ = await run_conv(pipe, new_conv(), "hi")
        assert events[-1]["type"] == "done"

    async def test_outer_error_trace_failure_still_emits_error(self, monkeypatch, test_db):
        pipe = Pipeline()
        install(monkeypatch, pipe)

        async def _boom():
            raise RuntimeError("schema down")
        monkeypatch.setattr(chat_mod, "get_tools_schema", _boom)
        monkeypatch.setattr(chat_mod.db, "trace_add",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("trace down")))
        events, _ = await run_conv(pipe, new_conv(), "hi")
        assert any(e["type"] == "error" and "schema down" in e["message"] for e in events)

    async def test_tool_result_validator_crash_degrades(self, monkeypatch, test_db):
        """validate_tool_result 自身抛异常 → 吞掉，不阻断对话。"""
        pipe = Pipeline()
        pipe.rounds = [
            [{"type": "tool_call", "id": "c1", "name": "t", "args": {}}],
            [{"type": "text", "content": "ok"}],
        ]
        install(monkeypatch, pipe)
        monkeypatch.setattr(chat_mod, "validate_tool_result",
                            lambda name, args, text: (_ for _ in ()).throw(RuntimeError("validator bug")))
        events, _ = await run_conv(pipe, new_conv(), "hi")
        assert events[-1]["type"] == "done"


# ════════════════════════════ 5. HTTP 端点 ════════════════════════════
@pytest.fixture
async def client(test_db):
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    app = FastAPI()
    app.include_router(chat_mod.router)
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                           base_url="http://t") as c:
        yield c


def install_http(monkeypatch, text="HTTP-ANSWER"):
    async def _cs(messages, tools=None, temperature=None, max_tokens=None, provider_name=""):
        yield {"type": "text", "content": text}

    async def _noop():
        return ""

    async def _compress(cid):
        return False

    monkeypatch.setattr(chat_mod, "chat_stream", _cs)
    monkeypatch.setattr(chat_mod, "collection_hint_text", _noop)
    monkeypatch.setattr(chat_mod, "get_tools_schema", _noop)
    monkeypatch.setattr(chat_mod, "maybe_compress", _compress)
    monkeypatch.setattr(chat_mod, "check_reminders", lambda: "")
    monkeypatch.setattr(chat_mod, "get_pending_proposals", lambda: [])

    async def _extract(*a, **k):
        return None
    monkeypatch.setattr(chat_mod, "maybe_extract_memories", _extract)
    monkeypatch.setattr(chat_mod, "validate_input",
                        lambda t: {"passed": True, "warning": None, "block": False})
    monkeypatch.setattr(chat_mod, "validate_output", lambda t, cid="": [])


class TestChatEndpoint:
    async def test_invalid_json_400(self, client):
        r = await client.post("/api/chat", content=b"{not json")
        assert r.status_code == 400
        assert "无效的 JSON" in r.json()["error"]

    async def test_empty_message_400(self, client):
        r = await client.post("/api/chat", json={"message": "   "})
        assert r.status_code == 400

    async def test_normal_flow_sse(self, client, monkeypatch):
        install_http(monkeypatch, "HTTP-ANSWER")
        r = await client.post("/api/chat", json={"message": "你好"})
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        assert "HTTP-ANSWER" in r.text
        assert "data: " in r.text

    async def test_unknown_conv_id_creates_new(self, client, monkeypatch):
        """conv_id 非空但不存在 → 兜底新建（生成新 id，不复用幽灵 id），不 500。"""
        install_http(monkeypatch)
        before = len(db.conv_list())
        r = await client.post("/api/chat", json={"conversation_id": "ghost123", "message": "hi"})
        assert r.status_code == 200
        assert len(db.conv_list()) == before + 1
        assert db.conv_get("ghost123") is None   # 幽灵 id 未被误用

    async def test_input_blocked_by_validator(self, client, monkeypatch):
        install_http(monkeypatch)
        monkeypatch.setattr(chat_mod, "validate_input",
                            lambda t: {"passed": False, "warning": "高危", "block": True})
        r = await client.post("/api/chat", json={"message": "rm -rf /"})
        assert r.status_code == 400
        assert r.json()["validation"]["blocked"] is True

    async def test_stream_compat_route(self, client, monkeypatch):
        install_http(monkeypatch, "COMPAT")
        r = await client.post("/api/chat/stream", json={"message": "hi"})
        assert r.status_code == 200
        assert "COMPAT" in r.text

    async def test_input_soft_warning_continues(self, client, monkeypatch):
        """passed=True + 非阻塞 warning → 继续对话（soft warning 分支）。"""
        install_http(monkeypatch, "SOFT")
        monkeypatch.setattr(chat_mod, "validate_input",
                            lambda t: {"passed": True, "warning": "请求模糊", "block": False})
        r = await client.post("/api/chat", json={"message": "帮我搞一下那个"})
        assert r.status_code == 200 and "SOFT" in r.text

    async def test_new_message_cancels_existing_task(self, client, monkeypatch):
        """同一会话已有在跑的任务时，新消息应取消旧任务。"""
        install_http(monkeypatch, "NEW")
        cid = new_conv()
        t = asyncio.create_task(asyncio.sleep(30))
        chat_mod._active_streams[cid] = t
        try:
            r = await client.post("/api/chat", json={"conversation_id": cid, "message": "hi"})
            assert r.status_code == 200
            assert t.cancelled() or t.done()
        finally:
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)

    async def test_maybe_compress_failure_degrades(self, client, monkeypatch):
        install_http(monkeypatch, "OK")
        async def _boom(cid):
            raise RuntimeError("compress fail")
        monkeypatch.setattr(chat_mod, "maybe_compress", _boom)
        r = await client.post("/api/chat", json={"message": "hi"})
        assert r.status_code == 200

    async def test_conv_get_failure_falls_back(self, client, monkeypatch):
        """conv_id 存在但 conv_get 抛异常 → 兜底新建；persona/背景读取异常也须降级。"""
        install_http(monkeypatch, "FB")
        monkeypatch.setattr(chat_mod.db, "conv_get",
                            lambda c: (_ for _ in ()).throw(RuntimeError("db down")))
        r = await client.post("/api/chat", json={"conversation_id": "any", "message": "hi"})
        assert r.status_code == 200 and "FB" in r.text

    @pytest.mark.xfail(strict=False, reason="BUG: message 非字符串（null/数字/数组）→ 500，应在边界返回 400（chat.py:646/673 直接 .strip()）")
    async def test_non_string_message_should_400_not_500(self, client, monkeypatch):
        """边界：message 为 null / 非字符串时应返回 400，而不是 500。"""
        install_http(monkeypatch)
        r = await client.post("/api/chat", json={"message": None})
        assert r.status_code in (400, 422), f"非字符串 message 返回了 {r.status_code}"


class TestRegenerateEndpoint:
    async def test_invalid_json_400(self, client):
        r = await client.post("/api/chat/regenerate", content=b"xx")
        assert r.status_code == 400

    async def test_missing_conv_id_400(self, client):
        r = await client.post("/api/chat/regenerate", json={})
        assert r.status_code == 400

    async def test_empty_conversation_400(self, client):
        cid = new_conv()
        r = await client.post("/api/chat/regenerate", json={"conversation_id": cid})
        assert r.status_code == 400
        assert "对话为空" in r.json()["error"]

    async def test_last_not_assistant_400(self, client):
        cid = new_conv()
        db.msg_add(cid, "user", "只有用户消息")
        r = await client.post("/api/chat/regenerate", json={"conversation_id": cid})
        assert r.status_code == 400
        assert "上一条 AI 回复" in r.json()["error"]

    async def test_happy_path_reruns_last_user_message(self, client, monkeypatch):
        install_http(monkeypatch, "REGEN")
        cid = new_conv()
        db.msg_add(cid, "user", "原始提问")
        db.msg_add(cid, "assistant", "旧回答")
        r = await client.post("/api/chat/regenerate", json={"conversation_id": cid})
        assert r.status_code == 200
        assert "REGEN" in r.text
        # 旧 assistant 被删除
        assert not [m for m in db.msg_list(cid) if m["role"] == "assistant" and m["content"] == "旧回答"]

    async def test_no_user_message_400(self, client, monkeypatch):
        install_http(monkeypatch)
        cid = new_conv()
        db.msg_add(cid, "assistant", "凭空一条 AI 消息")
        r = await client.post("/api/chat/regenerate", json={"conversation_id": cid})
        assert r.status_code == 400
        assert "没有可用的用户消息" in r.json()["error"]


class TestEditEndpoint:
    async def test_invalid_json_400(self, client):
        r = await client.post("/api/chat/edit", content=b"{")
        assert r.status_code == 400

    async def test_missing_fields_400(self, client):
        r = await client.post("/api/chat/edit", json={"conversation_id": "c"})
        assert r.status_code == 400

    async def test_msg_not_found_404(self, client):
        cid = new_conv()
        r = await client.post("/api/chat/edit",
                              json={"conversation_id": cid, "msg_id": 999999, "content": "x"})
        assert r.status_code == 404

    async def test_msg_belongs_to_other_conv_404(self, client):
        cid1 = new_conv()
        cid2 = new_conv()
        mid = db.msg_add(cid1, "user", "hi")
        r = await client.post("/api/chat/edit",
                              json={"conversation_id": cid2, "msg_id": mid, "content": "x"})
        assert r.status_code == 404

    async def test_update_failure_500(self, client, monkeypatch):
        cid = new_conv()
        mid = db.msg_add(cid, "user", "x")
        monkeypatch.setattr(chat_mod.db, "msg_update", lambda m, content: False)
        r = await client.post("/api/chat/edit",
                              json={"conversation_id": cid, "msg_id": mid, "content": "y"})
        assert r.status_code == 500
        assert "更新失败" in r.json()["error"]

    async def test_edit_assistant_returns_json_only(self, client, monkeypatch):
        install_http(monkeypatch)
        cid = new_conv()
        mid = db.msg_add(cid, "assistant", "旧内容")
        r = await client.post("/api/chat/edit",
                              json={"conversation_id": cid, "msg_id": mid, "content": "新内容"})
        assert r.status_code == 200
        body = r.json()
        assert body == {"success": True, "regenerate": False}
        assert db.msg_get(mid)["content"] == "新内容"

    async def test_edit_user_truncates_tail_and_reruns(self, client, monkeypatch):
        install_http(monkeypatch, "EDITED")
        cid = new_conv()
        uid = db.msg_add(cid, "user", "原始问题")
        db.msg_add(cid, "assistant", "旧回答")
        db.msg_add(cid, "user", "后续问题")
        r = await client.post("/api/chat/edit",
                              json={"conversation_id": cid, "msg_id": uid, "content": "改后的问题"})
        assert r.status_code == 200
        assert "EDITED" in r.text
        contents = {m["content"] for m in db.msg_list(cid)}
        # 编辑点之后的消息（旧回答 / 后续问题）被删除；随后重跑生成新的 assistant 回复
        assert "旧回答" not in contents and "后续问题" not in contents
        assert contents == {"改后的问题", "EDITED"}

    async def test_edit_never_touches_other_conversation(self, client, monkeypatch):
        """★ 回归（P0-1 残留）：edit 截断其后历史时绝不能误删其它会话的消息。

        messages.id 全局自增、跨会话连续；旧实现 `msg_del_from(msg_id + 1)` 在
        msg_id+1 属于另一个会话时会删掉那个会话的消息（实测 A 的 edit 清空 B）。
        """
        install_http(monkeypatch, "ED")
        a = new_conv("A")
        b = new_conv("B")
        # 交错插入：A1 B1 A2 B2
        a1 = db.msg_add(a, "user", "A1")
        db.msg_add(b, "user", "B1")
        db.msg_add(a, "assistant", "A2")
        db.msg_add(b, "assistant", "B2")

        r = await client.post("/api/chat/edit",
                              json={"conversation_id": a, "msg_id": a1, "content": "A1改"})
        assert r.status_code == 200

        # B 会话必须完好无损
        assert [m["content"] for m in db.msg_list(b)] == ["B1", "B2"], "跨会话误删！"
        # A 会话：仅保留编辑后的消息（+重跑生成的回复），A2 被截断
        a_contents = [m["content"] for m in db.msg_list(a)]
        assert "A2" not in a_contents and "A1改" in a_contents


class TestMsgDelAfter:
    """database.msg_del_after —— `/chat/edit` 按会话截断的底层依赖。"""

    def test_scoped_to_conversation(self, test_db):
        a = new_conv("A")
        b = new_conv("B")
        a1 = db.msg_add(a, "user", "A1")
        db.msg_add(b, "user", "B1")
        db.msg_add(a, "assistant", "A2")
        db.msg_add(b, "assistant", "B2")

        assert db.msg_del_after(a, a1) == 1
        assert [m["content"] for m in db.msg_list(a)] == ["A1"]
        assert [m["content"] for m in db.msg_list(b)] == ["B1", "B2"]

    def test_noop_when_last_message(self, test_db):
        a = new_conv("A")
        a1 = db.msg_add(a, "user", "only")
        b = new_conv("B")
        db.msg_add(b, "user", "b")   # 制造「全局更大的 id 属于另一会话」
        assert db.msg_del_after(a, a1) == 0
        assert [m["content"] for m in db.msg_list(a)] == ["only"]
        assert [m["content"] for m in db.msg_list(b)] == ["b"]


class TestDeleteEndpoint:
    async def test_delete_tail(self, client):
        cid = new_conv()
        m1 = db.msg_add(cid, "user", "a")
        db.msg_add(cid, "assistant", "b")
        db.msg_add(cid, "user", "c")
        r = await client.delete(f"/api/chat/messages/{m1}")
        assert r.status_code == 200
        assert r.json()["deleted"] == 3
        assert db.msg_list(cid) == []

    async def test_delete_single(self, client):
        cid = new_conv()
        m1 = db.msg_add(cid, "user", "a")
        db.msg_add(cid, "assistant", "b")
        db.msg_add(cid, "user", "c")
        r = await client.delete(f"/api/chat/messages/{m1}?mode=single")
        assert r.status_code == 200
        assert r.json()["deleted"] == 1
        assert len(db.msg_list(cid)) == 2

    async def test_delete_missing_404(self, client):
        r = await client.delete("/api/chat/messages/424242")
        assert r.status_code == 404


class TestStopEndpoint:
    async def test_invalid_json_400(self, client):
        r = await client.post("/api/chat/stop", content=b"xx")
        assert r.status_code == 400

    async def test_no_active_task(self, client):
        r = await client.post("/api/chat/stop", json={"conversation_id": "none"})
        assert r.status_code == 200
        assert r.json() == {"success": True, "stopped": False}

    async def test_stops_active_task(self, client, test_db):
        gate = asyncio.Event()

        async def _blocking_cs(messages, tools=None, temperature=None, max_tokens=None,
                               provider_name=""):
            await gate.wait()
            yield {"type": "text", "content": "late"}

        chat_mod.chat_stream = _blocking_cs
        try:
            cid = new_conv()
            resp = chat_mod._start_sse(cid, "hi", {"system_prompt": "SYS"})
            assert resp is not None
            task = chat_mod._active_streams.get(cid)
            assert task is not None
            r = await client.post("/api/chat/stop", json={"conversation_id": cid})
            assert r.json()["stopped"] is True
            assert task.cancelled() or task.done()
        finally:
            gate.set()
            await asyncio.sleep(0)


# ════════════════════════════ 6. _start_sse 流注册 ════════════════════════════
class TestStartSseRegistry:
    async def test_stale_task_finally_does_not_evict_new_stream(self, test_db, monkeypatch):
        """回归：旧任务被 cancel 后，其 finally 不应把**新任务**从 _active_streams 里删掉。

        复现路径：先让 task1 真正启动并阻塞在 chat_stream，再启动 task2（cancels task1）。
        task1 的 CancelledError 走到 _process_conv 的 finally → pop(conv_id) → 误删 task2。
        后果：该轮流不再被追踪 → /api/chat/stop 无法停止、再次发消息也不会 cancel 它，
        导致同一会话出现并发流与重复回复。
        """
        gate = asyncio.Event()

        async def _cs(messages, tools=None, temperature=None, max_tokens=None, provider_name=""):
            await gate.wait()
            yield {"type": "text", "content": "x"}

        async def _noop():
            return ""

        monkeypatch.setattr(chat_mod, "chat_stream", _cs)
        monkeypatch.setattr(chat_mod, "collection_hint_text", _noop)
        monkeypatch.setattr(chat_mod, "get_tools_schema", _noop)
        monkeypatch.setattr(chat_mod, "check_reminders", lambda: "")
        monkeypatch.setattr(chat_mod, "get_pending_proposals", lambda: [])

        async def _extract(*a, **k):
            return None
        monkeypatch.setattr(chat_mod, "maybe_extract_memories", _extract)
        monkeypatch.setattr(chat_mod, "validate_output", lambda t, cid="": [])

        cid = new_conv()
        cfg = {"system_prompt": "SYS"}
        chat_mod._start_sse(cid, "m1", cfg, persist_user=True)
        task1 = chat_mod._active_streams.get(cid)
        await asyncio.sleep(0.1)                      # 让 task1 真正启动并阻塞
        assert task1 is not None and not task1.done()
        chat_mod._start_sse(cid, "m2", cfg, persist_user=True)
        task2 = chat_mod._active_streams.get(cid)
        assert task2 is not None and task2 is not task1
        await asyncio.sleep(0.1)                      # 让 task1 的取消与 finally 执行
        assert chat_mod._active_streams.get(cid) is task2, (
            "旧任务的 finally 误删了新任务的注册项（并发状态机缺陷）")
        gate.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
