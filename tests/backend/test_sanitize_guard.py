"""落库密钥守卫测试（`backend/validators/sanitize_guard.py`）。

为什么需要本文件 —— 该模块 124 行此前**零测试**（`grep sanitize_guard tests/` = 0 命中），
而它是**明文密钥落库的最后一道闸门**：`database.py:17` 直接 import 它的 `guard_store`，
notes / memories / schedules / goals / tools 五条写入路径都过它。

值得注意的是它自带的设计原则（见模块 docstring）：
「宁可漏检，绝不误杀 —— 仅匹配精确前缀，不做启发式猜测」。
本文件的**反例组**正是这条原则的守护 —— 误杀会把正常内容挡在库外。
"""
import pytest

from backend.validators.sanitize_guard import (
    GUIDE_PLACEHOLDER,
    GUIDE_SHIELD,
    _FIELD_SUBJECTS,
    contains_plain_secret,
    find_plain_secrets,
    guard_store,
    refusal_text,
)

# 精确前缀类（零误杀）—— 每例都是「前缀 + 足够长度」
PREFIX_SECRET_CASES = [
    ("ghp_" + "a" * 36, "GitHub_Token"),
    ("github_pat_" + "a" * 30, "GitHub_PAT"),
    ("glpat-" + "a" * 25, "GitLab_PAT"),
    ("sk-ant-api03-" + "a" * 40, "Anthropic_Key"),
    ("xoxb-1234567890-abcdefghij", "Slack_Token"),
]

JWT_SAMPLE = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
    ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
)

# 必须是「干净」的文本 —— 本组失败意味着规则过度匹配（误杀）
CLEAN_TEXTS = [
    "",
    "今天天气很好，我去公园散步了",
    "Python 是一门通用编程语言",
    "完成了三项任务：写文档、改代码、开会",
    "普通笔记：记录一下会议要点",
    "sk-short",  # 前缀对但长度不足，不应命中
]


class TestFindPlainSecretsPositive:
    """正例：各类明文密钥必须被识别。"""

    @pytest.mark.parametrize("text,expected_name", PREFIX_SECRET_CASES)
    def test_prefixed_tokens(self, text, expected_name):
        names = {f["name"] for f in find_plain_secrets(text)}
        assert expected_name in names, f"{text[:14]}... 未被识别为 {expected_name}"

    def test_jwt(self):
        names = {f["name"] for f in find_plain_secrets(JWT_SAMPLE)}
        assert "JWT_Token" in names

    def test_bearer_header(self):
        names = {f["name"] for f in find_plain_secrets("Authorization: Bearer " + "a" * 30)}
        assert "Bearer_Token" in names

    def test_secret_assignment_pattern(self):
        names = {f["name"] for f in find_plain_secrets("api_key = abcdefghijklmnop")}
        assert "Secret_Assignment" in names

    def test_env_var_pattern(self):
        names = {f["name"] for f in find_plain_secrets("SECRET=" + "A" * 40)}
        assert "Env_Var" in names

    def test_secret_embedded_in_sentence(self):
        """密钥夹在正常语句里也要能捞出来。"""
        text = "这是我的配置，请帮我看看：token=abcdefghijklmnopqrst 是不是过期了"
        assert contains_plain_secret(text) is True


class TestFindPlainSecretsNegative:
    """反例：正常内容绝不能被误杀（模块自述原则「宁可漏检，绝不误杀」）。"""

    @pytest.mark.parametrize("text", CLEAN_TEXTS)
    def test_clean_text_yields_nothing(self, text):
        assert find_plain_secrets(text) == [], f"误杀：{text!r}"

    @pytest.mark.parametrize("text", CLEAN_TEXTS)
    def test_contains_plain_secret_false(self, text):
        assert contains_plain_secret(text) is False

    def test_short_prefix_alone_is_not_a_secret(self):
        """`sk-` 前缀但长度不足 → 不是密钥。"""
        assert find_plain_secrets("sk-abc") == []
        assert find_plain_secrets("ghp_") == []

    def test_chinese_password_word_without_value_not_flagged(self):
        """「密码」是中文，规则只认英文关键词。"""
        assert find_plain_secrets("我把密码忘了，需要重置") == []


class TestFindPlainSecretsShape:
    """返回结构的形状约定（下游 `guard_store` 依赖这些键）。"""

    def test_finding_has_expected_keys(self):
        f = find_plain_secrets("ghp_" + "a" * 36)[0]
        assert set(f.keys()) == {"name", "snippet", "position"}

    def test_snippet_truncated_when_over_24_chars(self):
        f = find_plain_secrets("ghp_" + "a" * 36)[0]
        assert f["snippet"].endswith("...")
        assert len(f["snippet"]) == 27  # 24 + "..."

    def test_snippet_kept_intact_when_short(self):
        """短命中不做截断（不补省略号）。"""
        f = find_plain_secrets("token=abcdefghijklmnop")[0]  # raw 共 22 字符
        assert f["snippet"] == "token=abcdefghijklmnop"
        assert not f["snippet"].endswith("...")

    def test_position_points_at_match_start(self):
        text = "前缀 ghp_" + "a" * 36
        f = find_plain_secrets(text)[0]
        assert text[f["position"]:].startswith("ghp_")

    def test_identical_raw_reported_once(self):
        tok = "ghp_" + "a" * 36
        assert len(find_plain_secrets(f"{tok} 以及 {tok}")) == 1

    def test_max_report_caps_findings(self):
        text = "\n".join("ghp_" + ch * 36 for ch in "abcdefghijk")
        assert len(find_plain_secrets(text, max_report=3)) == 3


class TestGuardStore:
    """落库守卫：返回 None = 放行；返回 dict = 拒绝。"""

    def test_blocks_content_with_secret(self):
        risk = guard_store("我的 key 是 sk-" + "a" * 48, field="note")
        assert risk is not None
        assert risk["safe"] is False
        assert risk["findings"], "拒绝时必须带上 findings 供诊断"
        assert "笔记" in risk["message"]
        assert "被拒绝" in risk["message"]

    def test_passes_clean_content(self):
        assert guard_store("今天完成了三件事") is None

    def test_passes_empty_content(self):
        assert guard_store("") is None

    @pytest.mark.parametrize("field,expected", [
        ("note", "笔记"),
        ("memory", "记忆"),
        ("schedule", "日程"),
        ("goal", "目标"),
        ("skill", "技能记忆"),
    ])
    def test_known_field_maps_to_chinese_subject(self, field, expected):
        risk = guard_store("sk-" + "a" * 48, field=field)
        assert expected in risk["message"]

    def test_unknown_field_falls_back_to_raw_name(self):
        """未登记的 field 保留原样，避免丢失辨识度。"""
        risk = guard_store("sk-" + "a" * 48, field="unregistered_field")
        assert "unregistered_field" in risk["message"]

    def test_default_field_is_content(self):
        risk = guard_store("sk-" + "a" * 48)
        assert _FIELD_SUBJECTS["content"] in risk["message"]

    def test_names_deduplicated_across_same_type(self):
        """两个不同 raw 但同类型 → names 只出现一次（`dict.fromkeys` 去重）。"""
        jwt1 = "eyJ" + "a" * 30 + "." + "b" * 30 + "." + "c" * 20
        jwt2 = "eyJ" + "d" * 30 + "." + "e" * 30 + "." + "f" * 20
        risk = guard_store(f"{jwt1} 以及 {jwt2}")
        assert risk["names"] == "JWT_Token", f"names 未去重：{risk['names']}"

    def test_long_sk_key_reports_multiple_type_names(self):
        """⚠️ 已知行为（非 bug，但**不要**假设 names == 密钥真实类型）。

        `sk-` + 48 字符会命中 OpenAI_Key / DeepSeek_Key / SiliconFlow_Key 三条规则，
        但最终只上报**两个**类型名：
          - `OpenAI_Key`      `sk-[A-Za-z0-9]{32,}` → raw = 整串 51 字符
          - `SiliconFlow_Key` `sk-[A-Za-z0-9]{40,}` → raw **也是整串 51 字符** → 被 `seen` 去重
          - `DeepSeek_Key`    `sk-[A-Za-z0-9]{32}`（无 `+`）→ raw = 前 35 字符 → 算新 raw，上报

        即 `seen` 只对**完全相同的 raw** 去重。**不影响「是否拒绝」的判定**，
        只影响诊断信息 —— 所以 names 不等于密钥的真实类型。
        """
        names = {f["name"] for f in find_plain_secrets("sk-" + "a" * 48)}
        assert names == {"OpenAI_Key", "DeepSeek_Key"}

    def test_names_deduplicated_for_long_sk_key(self):
        """长 `sk-` 密钥虽命中三条规则，names 仍不重复（各类型只出现一次）。"""
        risk = guard_store("sk-" + "a" * 48)
        assert len(risk["names"].split(", ")) == len(set(risk["names"].split(", ")))

    def test_message_includes_shield_hint_for_users(self):
        risk = guard_store("sk-" + "a" * 48)
        assert "shield.py" in risk["message"]


class TestRefusalText:
    """统一拒绝文案（纯字符串拼接，无副作用）。"""

    def test_minimal_form(self):
        assert refusal_text("笔记", guide="") == "笔记被拒绝：内容含疑似明文密钥。"

    def test_with_names_and_action_order(self):
        t = refusal_text("笔记", names="OpenAI_Key", action="未写入", guide="")
        assert t == "笔记被拒绝：内容含疑似明文密钥（OpenAI_Key；未写入）。"

    def test_only_names(self):
        assert "（JWT_Token）" in refusal_text("日程", names="JWT_Token", guide="")

    def test_only_action(self):
        assert "（原稿未改动）" in refusal_text("目标", action="原稿未改动", guide="")

    def test_empty_meta_omits_bracket_entirely(self):
        assert "（" not in refusal_text("笔记", guide="")

    def test_default_guide_mentions_shield(self):
        assert "shield.py" in refusal_text("笔记")

    def test_empty_guide_suppresses_hint(self):
        """列表型汇总场景传 guide="" —— 重复 N 遍指引只会变噪音。"""
        assert "shield.py" not in refusal_text("笔记", guide="")

    def test_two_guide_levels_are_distinct(self):
        assert GUIDE_PLACEHOLDER != GUIDE_SHIELD
        assert "shield.py" not in GUIDE_PLACEHOLDER
        assert "shield.py" in GUIDE_SHIELD
