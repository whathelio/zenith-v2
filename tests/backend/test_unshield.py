"""unshield.py 占位符还原的边界回归测试。

覆盖 2026-09-11 修复：`unmask_text` 原先**不限定 SEC_ 前缀**，
会把任意 `{{词}}` 送去查表 —— 一旦被诱导输出与映射表键同名的占位符即可被还原，
违背「绝不还原」铁律。现只认 shield.py 的产出格式 `SEC_<数字>`（`shield.py:83`）。
"""
import importlib.util
import json
from pathlib import Path

import pytest

# tools/ 不是包，按文件路径加载（该模块只用标准库，且 __main__ 有守卫）
_TOOLS = Path(__file__).resolve().parents[2] / "tools"
_spec = importlib.util.spec_from_file_location("unshield_mod", _TOOLS / "unshield.py")
unshield = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(unshield)


@pytest.fixture()
def with_map(tmp_path, monkeypatch):
    """把映射表指到临时文件，避免碰真实 secure_map.json。"""
    f = tmp_path / "secure_map.json"

    def _set(mapping: dict):
        f.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(unshield, "MAP_FILE", str(f))
        return mapping

    return _set


def test_正常占位符被还原(with_map):
    with_map({"SEC_001": "REAL-SECRET", "SEC_002": "另一条"})
    assert unshield.unmask_text("值={{SEC_001}} 和 {{SEC_002}}") == "值=REAL-SECRET 和 另一条"


def test_非_SEC_前缀的键不得被还原(with_map):
    """★ 本次修复的核心：即使映射表里真有这个键，也不该被还原。"""
    with_map({"EMAIL_1": "a@b.com", "PHONE_2": "13800000000"})
    src = "联系 {{EMAIL_1}} 电话 {{PHONE_2}}"
    assert unshield.unmask_text(src) == src


def test_SEC_后必须跟数字(with_map):
    with_map({"SEC_abc": "不该被还原", "SEC_": "也不该"})
    src = "{{SEC_abc}} {{SEC_}}"
    assert unshield.unmask_text(src) == src


def test_映射表缺失的占位符原样保留(with_map):
    with_map({"SEC_001": "x"})
    assert unshield.unmask_text("{{SEC_999}}") == "{{SEC_999}}"


def test_空映射表时原样返回(with_map):
    with_map({})
    assert unshield.unmask_text("{{SEC_001}}") == "{{SEC_001}}"


def test_无占位符时不变(with_map):
    with_map({"SEC_001": "x"})
    assert unshield.unmask_text("普通文本，无占位符") == "普通文本，无占位符"


def test_多位数字也认(with_map):
    with_map({"SEC_1000": "v"})
    assert unshield.unmask_text("{{SEC_1000}}") == "v"


def test_单花括号不匹配(with_map):
    with_map({"SEC_001": "x"})
    assert unshield.unmask_text("{SEC_001}") == "{SEC_001}"
