"""backend/assessor.py 单元测试 —— 纯规则式评估器，零外部依赖。

覆盖：
- assess_round：各条规则（长文本/转录、链接、日程强/弱信号、记录、追问历史、
  时效、学术、知识库治理）+ 无副作用/空输入 + 同工具去重
- _build_hint：有命中 / 无命中
- summarize_assessment：正常快照 + 缺键兜底（trace 写入点依赖它）

该模块被 routers/chat.py 的 trace 写入点消费，风格对齐 test_trace_roundtrip.py。
"""
from backend import assessor as az


# ────────────────────────── assess_round：空 / 基础 ──────────────────────────
def test_空输入返回空评估():
    out = az.assess_round("")
    assert out["intents"] == []
    assert out["suggested"] == []
    assert out["preread"] == []
    assert out["hint"] == ""
    assert out["text_len"] == 0


def test_仅空白输入视同空():
    out = az.assess_round("   \n  ")
    assert out["text_len"] == 0
    assert out["intents"] == []


def test_无命中时不产生建议与hint():
    out = az.assess_round("随便说句话")
    assert out["intents"] == []
    assert out["suggested"] == []
    assert out["hint"] == ""
    assert out["text_len"] == len("随便说句话")


# ────────────────────────── ① 长文本 / 转录 ──────────────────────────
def test_长文本命中资料整理():
    out = az.assess_round("x" * 3000)
    assert "long_text" in out["intents"]
    tools = [s["tool"] for s in out["suggested"]]
    assert "check_duplicate" in tools
    assert "distill_note" in tools
    # check_duplicate 属只读白名单 → 应进 preread 且带 args
    assert out["preread"] and out["preread"][0]["tool"] == "check_duplicate"
    assert out["preread"][0]["args"]["content"].startswith("x")


def test_长文本含转录关键词额外命中transcript():
    out = az.assess_round("字幕 " + "x" * 3000)
    assert "long_text" in out["intents"]
    assert "transcript" in out["intents"]


def test_长文本但是提问不判为资料():
    # 结尾是问号/吗/呢 → 不算「资料类长文本」
    out = az.assess_round("x" * 3000 + "吗")
    assert "long_text" not in out["intents"]


def test_短文本不触发长文本规则():
    out = az.assess_round("短")
    assert "long_text" not in out["intents"]


# ────────────────────────── ② 链接 ──────────────────────────
def test_含链接命中has_link():
    out = az.assess_round("看看这个 https://example.com/a 谢谢")
    assert "has_link" in out["intents"]
    tools = [s["tool"] for s in out["suggested"]]
    assert "analyze_content" in tools


# ────────────────────────── ③ 日程（强/弱时间信号） ──────────────────────────
def test_强时间信号单独即可判日程():
    out = az.assess_round("明天开会")
    assert "schedule_ref" in out["intents"]
    assert "add_schedule" in [s["tool"] for s in out["suggested"]]


def test_弱时间信号单用不误报日程():
    # 「今天天气怎么样」不应命中日程（历史上误报到 add_schedule）
    out = az.assess_round("今天天气怎么样")
    assert "schedule_ref" not in out["intents"]


def test_弱时间信号加安排动词才触发日程():
    out = az.assess_round("下午安排见面")
    assert "schedule_ref" in out["intents"]


# ────────────────────────── ④ 明确记录 ──────────────────────────
def test_明确记录命中explicit_remember():
    out = az.assess_round("记住这个号码")
    assert "explicit_remember" in out["intents"]
    tools = [s["tool"] for s in out["suggested"]]
    assert "add_note" in tools
    assert "check_duplicate" in tools


# ────────────────────────── ⑤ 追问历史 ──────────────────────────
def test_追问历史命中recall():
    out = az.assess_round("上次我说的那个方案呢")
    assert "recall" in out["intents"]
    assert "search_memory" in [s["tool"] for s in out["suggested"]]


# ────────────────────────── ⑥ 时效 / 学术 ──────────────────────────
def test_时效性命中fresh_info():
    out = az.assess_round("最新的进展如何")
    assert "fresh_info" in out["intents"]
    assert "web_search" in [s["tool"] for s in out["suggested"]]


def test_学术命中academic():
    out = az.assess_round("帮我找这篇论文")
    assert "academic" in out["intents"]
    assert "academic_search" in [s["tool"] for s in out["suggested"]]


# ────────────────────────── ⑦ 知识库治理 ──────────────────────────
def test_知识库治理命中kb_governance():
    out = az.assess_round("整理下所有知识库笔记")
    assert "kb_governance" in out["intents"]
    tools = [s["tool"] for s in out["suggested"]]
    assert "list_notes" in tools
    assert "check_duplicate" in tools


# ────────────────────────── 去重 & hint ──────────────────────────
def test_同一工具不重复建议():
    # 同时命中长文本 + 明确记录 + 知识库治理，三处都想建议 check_duplicate
    msg = "记住 字幕 " + "x" * 3000 + " 知识库"
    out = az.assess_round(msg)
    tools = [s["tool"] for s in out["suggested"]]
    assert tools.count("check_duplicate") == 1
    assert sum(1 for p in out["preread"] if p["tool"] == "check_duplicate") == 1


def test_有命中时hint包含工具与意图():
    out = az.assess_round("看看 https://a.com")
    assert "analyze_content" in out["hint"]
    assert "系统自动生成" in out["hint"]
    assert "has_link" in out["hint"]


# ────────────────────────── _build_hint ──────────────────────────
def test_build_hint_无建议返回空串():
    assert az._build_hint({"suggested": [], "intents": []}) == ""


def test_build_hint_渲染置信度与理由():
    a = {"intents": ["has_link"],
         "suggested": [{"tool": "analyze_content", "why": "含链接", "confidence": "high"}]}
    hint = az._build_hint(a)
    assert "analyze_content" in hint
    assert "[high]" in hint
    assert "含链接" in hint


# ────────────────────────── summarize_assessment ──────────────────────────
def test_summarize_取精简快照():
    a = az.assess_round("看看 https://a.com")
    snap = az.summarize_assessment(a)
    assert set(snap.keys()) == {"intents", "suggested", "preread", "text_len"}
    # suggested / preread 只保留工具名（去掉 why/args 长文本）
    assert all(isinstance(x, str) for x in snap["suggested"])
    assert "analyze_content" in snap["suggested"]
    assert snap["text_len"] == a["text_len"]


def test_summarize_缺键时兜底为空():
    snap = az.summarize_assessment({})
    assert snap == {"intents": [], "suggested": [], "preread": [], "text_len": 0}
