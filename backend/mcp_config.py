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
    get_mcp_config_paths,
    prefer_workbuddy_mcp,
    CONFIG_DIR,
)

logger = None  # 延迟导入避免循环


def _log(msg, *a):
    import logging
    logging.getLogger("zenith.mcp_config").warning(msg, *a)


def _info(msg, *a):
    """加载过程的正常信息（非告警）走 INFO —— 与「回退/失败必须告警」区分开，
    否则正常的合并加载会淹没真正的告警，反过来又变成不好排查。"""
    import logging
    logging.getLogger("zenith.mcp_config").info(msg, *a)


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


def _merge_files_for(primary: Path) -> list[Path]:
    """返回本次要读取的文件顺序：primary 打头，其后是候选链中排在它之后的已存在文件。

    为什么是「合并」而不是「只读首个存在的候选」（2026-09-16 定位的真实故障）：
    实测机器上两份 mcp.json 长期并存 —— 用户主目录那份只有 1 个 server，
    应用数据目录（$WORKBUDDY_CONFIG_DIR）那份有 7 个。只认首个命中 → 后一份
    的 server 全部「未找到」（cache-scheduler / fact-check-mcp / code-verify-mcp /
    guard-mcp / audit-mcp / governance-iteration-mcp），且**完全静默**。
    合并 + 同名以靠前者为准，既恢复缺失的 server，又不改变既有优先级语义。

    primary 不在候选链中（调用方显式指定了链外路径）时只读它自己：显式指定的路径
    是独占意图，不应该被其他候选文件的内容污染。
    """
    primary_key = str(primary).lower()
    cands = get_mcp_config_paths()
    idx = next((i for i, p in enumerate(cands) if str(p).lower() == primary_key), None)
    if idx is None:
        return [primary]
    return [primary] + [p for p in cands[idx + 1:] if str(p).lower() != primary_key]


def _load_workbuddy_merged(primary: Path) -> tuple[list[dict], list[tuple[Path, int]]]:
    """按优先级合并多个 mcp.json。返回 (servers, [(文件, 该文件解析出的 server 数)])。

    同名 server 保留最先出现的那个（即高优先级候选）。
    """
    merged: dict[str, dict] = {}
    report: list[tuple[Path, int]] = []
    for p in _merge_files_for(primary):
        srv = _load_workbuddy(p) or []
        report.append((p, len(srv)))
        for s in srv:
            merged.setdefault(s.get("name"), s)
    return list(merged.values()), report


# 同一份加载结果只记一次日志：前端会轮询 /modules/stats，每次都刷一条会淹掉日志，
# 而配置没变时这条信息没有新增量。
_last_load_summary: tuple | None = None


def _log_load_summary(report: list[tuple[Path, int]], total: int):
    """记录「读了哪些文件 / 各自几个 server / 合并后几个」。

    这个故障拖了很久的原因就是加载过程零日志 —— 必须能一眼看出到底读了什么。
    """
    global _last_load_summary
    summary = tuple((str(p), n) for p, n in report) + ((total,),)
    if summary == _last_load_summary:
        return
    _last_load_summary = summary
    detail = " ｜ ".join(f"{p}={n}个" for p, n in report)
    _info("MCP 配置加载：读取 %d 个文件 → 合并后 %d 个 server ｜ %s",
          len(report), total, detail)
    dropped = [str(p) for p, n in report if n == 0]
    if dropped:
        _info("MCP 配置加载：以下文件存在但未解析出 server（已跳过）: %s", " ｜ ".join(dropped))


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

    ⚠️ 2026-09-11 修正：**回退必须可见**。
    原先回退是静默的 —— 路径写错 / 文件为空时界面看着一切正常，
    实际一个 MCP 都没有，排查无从下手。这正是历史上「MCP 桥静默为空」事故的根因
    （见 `docs/audit/Zenith-v2-核心功能复核报告-20260910.md` 的 F-02）。
    """
    path = get_mcp_config_path()
    if force_workbuddy or prefer_workbuddy_mcp():
        wb, report = _load_workbuddy_merged(path)
        _log_load_summary(report, len(wb))
        if wb:
            return _apply_overrides(_apply_env_overrides(wb))
        _log("未能从 WorkBuddy 加载 MCP 配置（%s）：%s → 回退到 config.yaml 的 mcp_servers",
             path, "文件不存在" if not path.exists() else "文件存在但解析结果为空")
    else:
        _log("prefer_workbuddy_mcp=false → 跳过 %s，直接使用 config.yaml 的 mcp_servers", path)

    cfg = load_config()
    servers = _apply_overrides(
        _apply_env_overrides([_normalize(s) for s in cfg.get("mcp_servers", [])])
    )
    if not servers:
        _log("⚠️ 回退后 MCP 列表仍为空 —— 当前将没有任何 MCP 可用"
             "（请检查 %s 与 config.yaml 的 mcp_servers）", path)
    return servers


def load_mcp_server(name: str) -> Optional[dict]:
    for s in load_mcp_servers():
        if s["name"] == name:
            return s
    return None
