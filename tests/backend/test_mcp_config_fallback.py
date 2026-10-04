"""mcp_config.load_mcp_servers 回退可见性回归测试。

覆盖 2026-09-11 修复：原先回退到 config.yaml 是**静默**的 ——
路径写错 / 文件为空时界面看着正常，实际一个 MCP 都没有，排查无从下手。
这正是历史上「MCP 桥静默为空」事故（F-02）的根因。现回退必须留下 WARNING。
"""
import logging
from pathlib import Path

import pytest

from backend import mcp_config


@pytest.fixture()
def stub(monkeypatch):
    """把外部依赖换成可控值：配置路径 / prefer 开关 / config.yaml 内容。"""
    def _install(path: Path, prefer: bool, yaml_servers: list | None = None):
        monkeypatch.setattr(mcp_config, "get_mcp_config_path", lambda: path)
        monkeypatch.setattr(mcp_config, "prefer_workbuddy_mcp", lambda: prefer)
        monkeypatch.setattr(mcp_config, "load_config",
                            lambda: {"mcp_servers": yaml_servers or []})
        monkeypatch.setattr(mcp_config, "load_mcp_overrides", lambda: {})
    return _install


def _warnings(caplog):
    """取出 WARNING 及以上的日志文本。

    用 `record.getMessage()` —— 它内部做一次安全的 %-格式化；
    直接写 `record.message % record.args` 会在消息已格式化时抛
    「not all arguments converted during string formatting」。
    """
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def test_mcp_json不存在_回退时必须告警(caplog, stub, tmp_path):
    """★ 本次修复的核心：静默回退 → 显式告警。"""
    stub(tmp_path / "不存在的mcp.json", True)
    with caplog.at_level(logging.WARNING, logger="zenith.mcp_config"):
        out = mcp_config.load_mcp_servers()
    msgs = _warnings(caplog)
    assert out == []
    assert any("未能从 WorkBuddy 加载" in m for m in msgs), msgs
    assert any("文件不存在" in m for m in msgs), msgs
    # 列表为空时还要额外点出"没有任何 MCP 可用"
    assert any("没有任何 MCP 可用" in m for m in msgs), msgs


def test_mcp_json为空对象_告警措辞区分于文件不存在(caplog, stub, tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text('{"mcpServers": {}}', encoding="utf-8")
    stub(p, True)
    with caplog.at_level(logging.WARNING, logger="zenith.mcp_config"):
        out = mcp_config.load_mcp_servers()
    msgs = _warnings(caplog)
    assert out == []
    assert any("解析结果为空" in m for m in msgs), msgs


def test_prefer_false_也要留下说明(caplog, stub, tmp_path):
    stub(tmp_path / "mcp.json", False)
    with caplog.at_level(logging.WARNING, logger="zenith.mcp_config"):
        mcp_config.load_mcp_servers()
    msgs = _warnings(caplog)
    assert any("prefer_workbuddy_mcp=false" in m for m in msgs), msgs


def test_正常加载时不产生回退告警(caplog, stub, tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text('{"mcpServers": {"demo": {"command": "python", "args": ["x.py"]}}}',
                 encoding="utf-8")
    stub(p, True)
    with caplog.at_level(logging.WARNING, logger="zenith.mcp_config"):
        out = mcp_config.load_mcp_servers()
    assert [s["name"] for s in out] == ["demo"]
    assert _warnings(caplog) == [], _warnings(caplog)


def test_回退后有内容时不应报列表仍为空(caplog, stub, tmp_path):
    """config.yaml 里配了服务器 → 回退有告警，但不应说"没有任何 MCP 可用"。"""
    stub(tmp_path / "缺失.json", True, yaml_servers=[{"name": "yaml-mcp", "command": "py"}])
    with caplog.at_level(logging.WARNING, logger="zenith.mcp_config"):
        out = mcp_config.load_mcp_servers()
    msgs = _warnings(caplog)
    assert [s["name"] for s in out] == ["yaml-mcp"]
    assert any("未能从 WorkBuddy 加载" in m for m in msgs), msgs
    assert not any("没有任何 MCP 可用" in m for m in msgs), msgs
