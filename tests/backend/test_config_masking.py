"""config 密钥掩码 / 还原的往返测试（`backend/config.py:286-360`）。

为什么需要本文件 —— 这两个函数此前**零测试**（`grep mask_secret tests/` = 0 命中），
但它们守着一条会**静默损坏密钥**的路径：

前端设置页是「GET 整包 → 改几个字段 → PUT 整包」（见 `config.py:269-271` 注释）。
GET 时 `mask_sensitive_fields` 把密钥掩码成 `sk-abc***yz（len=51）`；
PUT 时 `restore_masked_fields` 必须把掩码换回**真值**。若这条链路断掉，
用户改个温度就会把 API Key 覆盖成一串 `***` —— 而且**不报错**。

本文件锁定三件事：
1. 掩码形状可被 `is_masked` 稳定识别（否则写端点会把掩码当成新密钥存进去）
2. ≥12 字符的密钥往返**无损**
3. 旧值缺失时**丢弃字段**，绝不写入掩码串（`config.py:332-333` 明确的设计取舍）
"""
import pytest

from backend.config import (
    is_masked,
    mask_secret,
    mask_sensitive_fields,
    restore_masked_fields,
)


class TestMaskShape:
    """掩码形状：保留前 6 位 + 末 2 位 + 长度，用户能分辨配的是哪一个。"""

    def test_long_value_keeps_prefix_and_suffix(self):
        v = "sk-" + "a" * 48  # len == 51
        assert mask_secret(v) == f"{v[:6]}***{v[-2:]}（len=51）"

    def test_short_value_uses_head_only(self):
        v = "abcdefgh"  # len == 8 → s[:max(1, 8//3)] == s[:2]
        assert mask_secret(v) == "ab***（len=8）"

    @pytest.mark.parametrize("n", [1, 2, 5, 11, 12, 20, 51, 100])
    def test_every_length_is_recognized_as_mask(self, n):
        """任何长度产出的掩码都必须能被 is_masked 认出。

        这一条断了就等于「写端点分不清掩码和新密钥」→ 密钥被覆盖成 `***`。
        """
        assert is_masked(mask_secret("z" * n)), f"len={n} 的掩码未被识别"

    @pytest.mark.parametrize("value", [
        "sk-realkey1234567890",     # 真实密钥不是掩码
        "",
        None,
        123,
        0.7,
        [],
        {},
    ])
    def test_non_mask_values_are_rejected(self, value):
        assert is_masked(value) is False

    def test_mask_does_not_leak_middle_of_secret(self):
        """掩码不得包含密钥中段（前 6 + 末 2 之外的任何字符）。"""
        v = "sk-" + "MIDDLE_MUST_NOT_APPEAR" + "x" * 20
        m = mask_secret(v)
        assert "MIDDLE_MUST_NOT_APPEAR" not in m
        assert m.startswith(v[:6])
        assert m.endswith(f"{v[-2:]}（len={len(v)}）")


class TestMaskSensitiveFields:
    """按键名掩码：只动「字段名敏感 + 值为非空字符串」的项。"""

    def test_sensitive_key_is_masked(self):
        out = mask_sensitive_fields({"api_key": "sk-" + "a" * 48})
        assert is_masked(out["api_key"])

    @pytest.mark.parametrize("name", [
        "api_key", "API_KEY", "token", "secret", "password",
        "passwd", "credential", "Authorization", "cookie", "aws_secret_key",
    ])
    def test_all_sensitive_name_patterns(self, name):
        out = mask_sensitive_fields({name: "v" * 30})
        assert is_masked(out[name]), f"{name} 应被识别为敏感字段"

    def test_non_sensitive_keys_untouched(self):
        cfg = {
            "temperature": 0.7,
            "model": "deepseek-flash",
            "max_tokens": 16384,
            "api_key": "sk-" + "a" * 48,
        }
        out = mask_sensitive_fields(cfg)
        assert out["temperature"] == 0.7
        assert out["model"] == "deepseek-flash"
        assert out["max_tokens"] == 16384
        assert is_masked(out["api_key"])

    def test_empty_value_is_not_masked(self):
        """空串不掩码 —— 免得把「未配置」显示成「已配置一个掩码」。"""
        assert mask_sensitive_fields({"api_key": ""})["api_key"] == ""

    def test_non_string_sensitive_value_untouched(self):
        assert mask_sensitive_fields({"token_count": 42})["token_count"] == 42

    def test_nested_dict_is_masked(self):
        cfg = {"headers": {"Authorization": "Bearer " + "t" * 30}}
        assert is_masked(mask_sensitive_fields(cfg)["headers"]["Authorization"])

    def test_list_items_are_masked(self):
        cfg = {"providers": [{"name": "a", "api_key": "sk-" + "a" * 48}]}
        assert is_masked(mask_sensitive_fields(cfg)["providers"][0]["api_key"])

    def test_input_object_not_mutated(self):
        """必须是纯函数 —— 掩码不能改动调用方传进来的原对象。"""
        real = "sk-" + "a" * 48
        cfg = {"api_key": real}
        mask_sensitive_fields(cfg)
        assert cfg["api_key"] == real, "mask_sensitive_fields 不应改动入参"


class TestRestoreRoundTrip:
    """往返：掩码 + 原值 → 必须还原出原值。"""

    @pytest.mark.parametrize("secret", [
        "sk-" + "a" * 48,
        "ghp_" + "b" * 36,
        "twelve_chars",
        "x" * 100,
        "中文密钥也可以很长很长的十六个字符以上",
    ])
    def test_mask_then_restore_is_lossless(self, secret):
        old = {"api_key": secret}
        masked = mask_sensitive_fields(old)
        assert is_masked(masked["api_key"])
        assert restore_masked_fields(masked, old)["api_key"] == secret

    def test_missing_old_value_drops_field(self):
        """🔴 核心安全语义：找不到真值 → 丢弃字段，**绝不写回掩码串**。

        `config.py:332-333`：「宁可不改配置，也不能把密钥写坏」。
        """
        masked = mask_sensitive_fields({"api_key": "sk-" + "z" * 48})
        restored = restore_masked_fields(masked, {})
        assert "api_key" not in restored

    def test_old_value_being_mask_also_drops(self):
        """old 里的值本身是掩码（异常情形）→ 同样丢弃，不用掩码覆盖掩码。"""
        masked = mask_sensitive_fields({"api_key": "sk-" + "z" * 48})
        restored = restore_masked_fields(masked, {"api_key": "old***mask（len=9）"})
        assert "api_key" not in restored

    def test_plain_new_value_passes_through(self):
        """PUT 时用户输入的新密钥（非掩码）必须原样保留 —— 这是「改密钥」路径。"""
        restored = restore_masked_fields({"api_key": "brand-new-key-1234567890"}, {"api_key": "old"})
        assert restored["api_key"] == "brand-new-key-1234567890"

    def test_nested_roundtrip_is_lossless(self):
        real = {
            "temperature": 0.7,
            "model": "deepseek-flash",
            "providers": [
                {"name": "deepseek", "api_key": "sk-" + "d" * 48, "model": "deepseek-flash"},
                {"name": "openai", "api_key": "sk-" + "o" * 48, "model": "gpt-4o"},
            ],
            "headers": {"Authorization": "Bearer " + "t" * 30},
        }
        masked = mask_sensitive_fields(real)
        assert is_masked(masked["providers"][0]["api_key"])
        assert is_masked(masked["providers"][1]["api_key"])
        assert is_masked(masked["headers"]["Authorization"])
        assert masked["temperature"] == 0.7
        assert restore_masked_fields(masked, real) == real

    def test_providers_aligned_by_name_not_index(self):
        """列表按 name 对齐 —— 顺序变化不能张冠李戴（`config.py:344-351`）。"""
        real = {"providers": [
            {"name": "a", "api_key": "sk-" + "a" * 48},
            {"name": "b", "api_key": "sk-" + "b" * 48},
        ]}
        masked = mask_sensitive_fields(real)
        reordered = {"providers": [masked["providers"][1], masked["providers"][0]]}

        restored = restore_masked_fields(reordered, real)
        assert restored["providers"][0]["api_key"] == real["providers"][1]["api_key"]
        assert restored["providers"][1]["api_key"] == real["providers"][0]["api_key"]

    def test_rename_falls_back_to_index(self):
        """name 对不上时退回按 index 对齐（不丢字段）。"""
        real = {"providers": [{"name": "a", "api_key": "sk-" + "a" * 48}]}
        masked = mask_sensitive_fields(real)
        masked["providers"][0]["name"] = "renamed"
        restored = restore_masked_fields(masked, real)
        assert restored["providers"][0]["api_key"] == real["providers"][0]["api_key"]

    def test_whole_config_roundtrip(self):
        """端到端：整包掩码 → 整包还原 == 原包（模拟 GET/PUT 一整轮）。"""
        real = {
            "api_base": "https://api.deepseek.com/v1",
            "api_key": "sk-" + "k" * 48,
            "model": "deepseek-flash",
            "temperature": 0.7,
            "mcp": {"workbuddy_config_path": "~/.workbuddy/mcp.json"},
            "audit": {"enabled": True, "log_path": "data/audit/", "retention_days": 90},
        }
        assert restore_masked_fields(mask_sensitive_fields(real), real) == real
