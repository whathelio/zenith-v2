"""output_validator 单元测试。

该模块在流式输出结束前做兜底检测（绝对化表述 / 高风险领域 / 记忆矛盾），
其 warnings 会被 chat.py 直接 `{**w}` 展开成 SSE 事件 —— 故同时校验字典契约。
重点：应当报警的确实报警、有引用时确实抑制、记忆分支异常确实被吞掉不炸主流程。
`test_known_*` 用例记录当前（已知有缺陷）行为，供人工裁定。
"""
from backend.validators import output_validator as ov

# 中性长文本（>20 字符，且不含绝对化词/高风险领域词），用于隔离记忆分支
_NEUTRAL_LONG = "今天天气很好啊我们一起去公园里散步吧真的很好呢"


# ===================== validate_output：长度边界 =====================

def test_validate_output_empty_returns_empty():
    assert ov.validate_output("") == []


def test_validate_output_shorter_than_10_returns_empty():
    assert ov.validate_output("短") == []
    assert ov.validate_output("九字符都不到") == []


def test_validate_output_exactly_10_chars_is_checked():
    # 恰好 10 字符应进入检测（len<10 才短路）
    assert len("结果是绝对正确的呀") == 9
    assert len("结果是绝对正确的呀啊") == 10
    w = ov.validate_output("结果是绝对正确的呀啊")
    assert any(x["type"] == "absolute_claim" for x in w)


# ===================== 绝对化表述 =====================

def test_validate_output_absolute_claim_no_citation():
    w = ov.validate_output("这个结果是绝对正确的。")
    assert any(x["type"] == "absolute_claim" for x in w)
    assert all("message" in x and "level" in x for x in w)


def test_validate_output_absolute_with_bracket_citation_suppressed():
    w = ov.validate_output("这个结果是绝对正确的 [来源]")
    assert not any(x["type"] == "absolute_claim" for x in w)


def test_validate_output_absolute_with_url_suppressed():
    w = ov.validate_output("这个结果肯定是对的 https://example.com/paper")
    assert not any(x["type"] == "absolute_claim" for x in w)


def test_validate_output_absolute_reported_once():
    w = ov.validate_output("绝对肯定必然 100% 一定 毫无疑问 毋庸置疑 百分百")
    abs_w = [x for x in w if x["type"] == "absolute_claim"]
    assert len(abs_w) == 1              # break → 只报一次


# ===================== 高风险领域 =====================

def test_validate_output_high_risk_domain_medical():
    w = ov.validate_output("建议你尽快就医并接受专业的诊断与治疗。")
    assert any(x["type"] == "high_risk_domain" for x in w)


def test_validate_output_high_risk_domain_investment():
    w = ov.validate_output("这只股票必涨，可以稳赚不赔。")
    assert any(x["type"] == "high_risk_domain" for x in w)


def test_validate_output_high_risk_domain_reported_once():
    w = ov.validate_output("涉及到医疗建议和投资建议两种高风险内容的综合说明")
    dom_w = [x for x in w if x["type"] == "high_risk_domain"]
    assert len(dom_w) == 1              # break → 只报一次


def test_validate_output_absolute_and_domain_both_fire():
    w = ov.validate_output("这个投资建议绝对可靠，保证收益。")
    types = {x["type"] for x in w}
    assert "high_risk_domain" in types
    assert "absolute_claim" in types


# ===================== 记忆矛盾（monkeypatch 外部依赖） =====================

def test_validate_output_memory_contradiction(monkeypatch):
    monkeypatch.setattr(ov.db, "mem_search",
                        lambda kw, limit=5: [{"content": "这个方案建议采用因为风险很低"}])
    monkeypatch.setattr(ov, "_similarity", lambda a, b: 0.9)
    # >20 字符且含否定词，与记忆（无否定）相反
    w = ov.validate_output("这个方案我不建议采用因为它带来的风险实在是太高了。",
                           conv_id="c1")
    assert any(x["type"] == "memory_contradiction" for x in w)


def test_validate_output_no_contradiction_when_both_negated(monkeypatch):
    monkeypatch.setattr(ov.db, "mem_search",
                        lambda kw, limit=5: [{"content": "这个方案不建议采用因为风险不高"}])
    monkeypatch.setattr(ov, "_similarity", lambda a, b: 0.9)
    w = ov.validate_output("这个方案我不建议采用因为它带来的风险实在是太高了。",
                           conv_id="c1")
    assert not any(x["type"] == "memory_contradiction" for x in w)


def test_validate_output_no_contradiction_when_similarity_low(monkeypatch):
    monkeypatch.setattr(ov.db, "mem_search",
                        lambda kw, limit=5: [{"content": "完全无关的另一段内容"}])
    monkeypatch.setattr(ov, "_similarity", lambda a, b: 0.3)
    w = ov.validate_output("这个方案我不建议采用因为它带来的风险实在是太高了。",
                           conv_id="c1")
    assert not any(x["type"] == "memory_contradiction" for x in w)


def test_validate_output_no_memory_check_without_conv_id(monkeypatch):
    called = {"n": 0}

    def _spy(text, warnings):
        called["n"] += 1

    monkeypatch.setattr(ov, "_check_memory_contradiction", _spy)
    ov.validate_output("这个方案我不建议采用因为它带来的风险实在是太高了。", conv_id="")
    assert called["n"] == 0


def test_validate_output_no_memory_check_when_text_short(monkeypatch):
    called = {"n": 0}

    def _spy(text, warnings):
        called["n"] += 1

    monkeypatch.setattr(ov, "_check_memory_contradiction", _spy)
    # len<=20 → 不进入记忆分支
    ov.validate_output("今天天气很好啊我们出去玩吧", conv_id="c1")
    assert called["n"] == 0


def test_validate_output_memory_exception_swallowed(monkeypatch):
    """记忆检测内部异常必须被吞掉（debug 日志），不得炸掉主流程。"""
    def _raise(text, warnings):
        raise RuntimeError("db down")

    monkeypatch.setattr(ov, "_check_memory_contradiction", _raise)
    assert ov.validate_output(_NEUTRAL_LONG, conv_id="c1") == []


# ===================== _check_memory_contradiction 直测 =====================

def test_check_memory_contradiction_no_key_sentences(monkeypatch):
    monkeypatch.setattr(ov.db, "mem_search", lambda kw, limit=5: [])
    warnings = []
    ov._check_memory_contradiction("短。短。短。", warnings)
    assert warnings == []


def test_check_memory_contradiction_direct(monkeypatch):
    monkeypatch.setattr(ov.db, "mem_search",
                        lambda kw, limit=5: [{"content": "这个方案建议采用因为风险很低"}])
    monkeypatch.setattr(ov, "_similarity", lambda a, b: 0.9)
    warnings = []
    ov._check_memory_contradiction(
        "这个方案我不建议采用因为它带来的风险实在是太高", warnings)
    assert warnings and warnings[0]["type"] == "memory_contradiction"
    assert "message" in warnings[0]


def test_check_memory_contradiction_both_negated_no_warn(monkeypatch):
    monkeypatch.setattr(ov.db, "mem_search",
                        lambda kw, limit=5: [{"content": "这个方案不建议采用因为风险不高"}])
    monkeypatch.setattr(ov, "_similarity", lambda a, b: 0.9)
    warnings = []
    ov._check_memory_contradiction(
        "这个方案我不建议采用因为它带来的风险实在是太高", warnings)
    assert warnings == []


# ===================== 已知缺陷（仅报告） =====================

def test_known_false_positive_to_some_extent():
    """「在一定程度上」是弱化表述，却被 _ABSOLUTE_PATTERNS 的「一定」命中。"""
    w = ov.validate_output("在一定程度上，这个结果还需要进一步验证。")
    assert any(x["type"] == "absolute_claim" for x in w)


def test_known_false_positive_absolute_value_math():
    """数学用语「绝对值」被「绝对」命中。"""
    w = ov.validate_output("请计算这个数值的绝对值并给出结果。")
    assert any(x["type"] == "absolute_claim" for x in w)


def test_known_lenient_short_high_risk_text_not_checked():
    """边界(仅报告): 文本 <10 字符直接返回 []，
    短高危文本（如「必涨，稳赚」）完全绕过高风险领域检测。"""
    assert ov.validate_output("必涨，稳赚") == []


def test_known_any_url_suppresses_all_absolute_claims():
    """边界(仅报告): 只要正文出现任意 URL，所有绝对化表述一律不再告警（过宽抑制）。"""
    w = ov.validate_output("保证收益，绝对没问题，详见 https://unrelated.example/a")
    assert not any(x["type"] == "absolute_claim" for x in w)
