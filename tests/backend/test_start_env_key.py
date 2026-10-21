"""行为固化单测：start.py 的 `_read_env_key`（行内注释剥离规则，2026-10-14 加固）。

规则（见 start.py:640-673）：
  · 值取到配对引号为止；引号后的 `#` 一律视为注释；
  · 未加引号时，仅当 `#` 前有空白（space/tab）才剥注释；
  · 无空白前缀的 `#`（URL 片段、紧凑写法）不误剥；
  · 值缺失返回 ""；键不存在或文件不存在返回 None。

不依赖仓库真实 .env —— 每个用例用 tmp_path 造临时文件，并 monkeypatch 模块级
`PROJECT_DIR` 指向该目录。
"""
import importlib.util
from pathlib import Path

import pytest

# tests/backend/test_start_env_key.py → parents[2] = zenith-v2（仓库根）
_START_PATH = Path(__file__).resolve().parents[2] / "start.py"

_spec = importlib.util.spec_from_file_location("zenith_start_under_test", str(_START_PATH))
start_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(start_mod)

_read_env_key = start_mod._read_env_key


@pytest.fixture
def write_env(tmp_path, monkeypatch):
    """把 start.PROJECT_DIR 指向 tmp_path，并返回写 .env 的辅助函数。"""
    monkeypatch.setattr(start_mod, "PROJECT_DIR", tmp_path)

    def _write(content: str) -> None:
        (tmp_path / ".env").write_bytes(content.encode("utf-8"))

    return _write


def test_plain_value(write_env):
    write_env("KEY=abc\n")
    assert _read_env_key("KEY") == "abc"


def test_quoted_hash_not_stripped(write_env):
    write_env('KEY="abc # not comment"\n')
    assert _read_env_key("KEY") == "abc # not comment"


def test_single_quoted_hash_not_stripped(write_env):
    write_env("KEY='p@ss # word'\n")
    assert _read_env_key("KEY") == "p@ss # word"


def test_inline_comment_after_space(write_env):
    write_env("KEY=abc # comment\n")
    assert _read_env_key("KEY") == "abc"


def test_inline_comment_after_tab(write_env):
    write_env("KEY=abc\t# comment\n")
    assert _read_env_key("KEY") == "abc"


def test_hash_without_space_not_stripped(write_env):
    write_env("KEY=abc#nospace\n")
    assert _read_env_key("KEY") == "abc#nospace"


def test_empty_value_returns_empty_string(write_env):
    write_env("KEY=\n")
    assert _read_env_key("KEY") == ""


def test_missing_key_returns_none(write_env):
    write_env("OTHER=1\n")
    assert _read_env_key("KEY") is None


def test_crlf_line_ending_has_no_carriage_return(write_env):
    write_env("KEY=abc\r\n")
    assert _read_env_key("KEY") == "abc"


def test_url_fragment_hash_not_stripped(write_env):
    write_env("URL=https://x/#frag\n")
    assert _read_env_key("URL") == "https://x/#frag"


def test_missing_env_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(start_mod, "PROJECT_DIR", tmp_path)  # 不创建 .env
    assert _read_env_key("KEY") is None
