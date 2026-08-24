"""Zenith v2 — MCP 配置加载

优先读取 WorkBuddy 的真实 mcp.json（含 4 个 zenith-auditor 依赖项），
缺失或为空时回退到 config.yaml 的 mcp_servers 占位。

支持 ${ENV} 占位符（如 jin10 的 Bearer Token 写为 "Bearer ${ZENITH_JIN10_API_TOKEN}"），
避免明文密钥落入配置文件。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from .config import (
    load_config,
    get_mcp_config_path,
    prefer_workbuddy_mcp,
    CONFIG_DIR,
)

logger = None  # 延迟导入避免循环


def _log(msg, *a):
    import logging
    logging.getLogger("zenith.mcp_config").warning(msg, *a)


def _normalize(server: dict) -> dict:
    """统一字段：补充 enabled / type 派生"""
    name = server.get("name") or server.get("serverUrl") or server.get("command")
    disabled = bool(server.get("disabled", False))
    if "command" in server and server.get("command"):
        mcp_type = "stdio"
    elif server.get("serverUrl"):
        mcp_type = "http"
    else:
        mcp_type = "unknown"
    return {
        "name": name,
        "type": mcp_type,
        "enabled": not disabled,
        "disabled": disabled,
        "serverUrl": server.get("serverUrl", ""),
        "command": server.get("command", ""),
        "args": server.get("args", []) or [],
        "headers": server.get("headers", {}) or {},
        "description": server.get("description", ""),
        "env": server.get("env", {}) or {},
    }


def _load_workbuddy(path: Path) -> Optional[list[dict]]:
    """读取 ~/.workbuddy/mcp.json，转换 mcpServers 对象 → 列表"""
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        _log("解析 mcp.json 失败 %s: %s", path, e)
        return None
    servers = raw.get("mcpServers", {})
    if not isinstance(servers, dict):
        return None
    out = []
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        cfg = dict(cfg)
        cfg["name"] = name
        out.append(_normalize(cfg))
    return out


def _apply_env_overrides(servers: list[dict]) -> list[dict]:
    """若进程环境存在 ZENITH_JIN10_API_TOKEN，将 jin10 服务的 Authorization 重写为
    ``Bearer ${ZENITH_JIN10_API_TOKEN}``，使 MCPClient 优先使用 .env 中的密钥，
    而非 ~/.workbuddy/mcp.json 里的明文 Token。

    这是 Zenith 侧的防御措施，**不改动**共享的 mcp.json：
    - 设了 ZENITH_JIN10_API_TOKEN → Zenith 用环境变量密钥，忽略 mcp.json 明文值
    - 没设 → 回退到 mcp.json 明文值（保持现有行为）
    """
    token = os.environ.get("ZENITH_JIN10_API_TOKEN")
    if not token:
        return servers
    for s in servers:
        if s.get("name") == "jin10":
            headers = dict(s.get("headers", {}))
            old = headers.get("Authorization", "")
            # 仅当当前是明文 Bearer 时才替换，避免重复包裹 ${...}
            if old and "ZENITH_JIN10_API_TOKEN" not in old:
                headers["Authorization"] = "Bearer ${ZENITH_JIN10_API_TOKEN}"
                s["headers"] = headers
    return servers


# Zenith 本地的 MCP 覆盖（仅 enabled/disabled），写在 config/mcp_overrides.json。
# 这样切换开关不触碰共享的 ~/.workbuddy/mcp.json（那是 WorkBuddy 的域）。
OVERRIDES_PATH = CONFIG_DIR / "mcp_overrides.json"


def load_mcp_overrides() -> dict:
    """读取本地覆盖：{server_name: {"disabled": bool}}"""
    if not OVERRIDES_PATH.exists():
        return {}
    try:
        data = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_mcp_override(name: str, disabled: bool):
    """写入单个服务的 disabled 覆盖。"""
    overrides = load_mcp_overrides()
    overrides[name] = {"disabled": bool(disabled)}
    OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDES_PATH.write_text(
        json.dumps(overrides, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def clear_mcp_override(name: str):
    """删除某个服务的覆盖（恢复为 mcp.json 中的默认状态）。"""
    overrides = load_mcp_overrides()
    if name in overrides:
        del overrides[name]
        OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        OVERRIDES_PATH.write_text(
            json.dumps(overrides, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _apply_overrides(servers: list[dict]) -> list[dict]:
    """把本地 enabled/disabled 覆盖叠加到 mcp.json 派生出的列表上。"""
    overrides = load_mcp_overrides()
    if not overrides:
        return servers
    for s in servers:
        ov = overrides.get(s.get("name"))
        if isinstance(ov, dict) and "disabled" in ov:
            s["disabled"] = bool(ov["disabled"])
            s["enabled"] = not s["disabled"]
    return servers


def load_mcp_servers(force_workbuddy: bool = False) -> list[dict]:
    """返回统一格式的 MCP 服务器列表。

    优先级：
    1. mcp.json（若 prefer_workbuddy 且文件存在/非空）+ 本地 override 叠加
    2. config.yaml 的 mcp_servers 占位 + 本地 override 叠加
    """
    if force_workbuddy or prefer_workbuddy_mcp():
        wb = _load_workbuddy(get_mcp_config_path())
        if wb:
            return _apply_overrides(_apply_env_overrides(wb))
    # 回退
    cfg = load_config()
    return _apply_overrides(_apply_env_overrides([_normalize(s) for s in cfg.get("mcp_servers", [])]))


def load_mcp_server(name: str) -> Optional[dict]:
    for s in load_mcp_servers():
        if s["name"] == name:
            return s
    return None
