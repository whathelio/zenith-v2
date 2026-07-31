"""Zenith v2 配置管理 — YAML + .env 双格式支持"""
from __future__ import annotations

import json
import os
import yaml
from pathlib import Path
from typing import Optional

PROJECT_DIR = Path(__file__).parent.parent
DATA_DIR = PROJECT_DIR / "data"
CONFIG_DIR = PROJECT_DIR / "config"
CONFIG_JSON = DATA_DIR / "config.json"
CONFIG_YAML = CONFIG_DIR / "config.yaml"
ENV_FILE = PROJECT_DIR / ".env"
DB_PATH = DATA_DIR / "zenith.db"


def _load_dotenv() -> dict:
    """手动加载 .env 文件（零依赖，不引入 python-dotenv）"""
    env_vars = {}
    if not ENV_FILE.exists():
        return env_vars
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    env_vars[key] = value
                    os.environ.setdefault(key, value)
    return env_vars


# 启动时加载 .env
_DOTENV_VARS = _load_dotenv()

SYSTEM_PROMPT = (
    "你是 Zenith，用户的本地智能助手。\n"
    "\n"
    "## 角色定位\n"
    "你是一个贴心、直率、高效的助手，帮助用户管理生活和工作。\n"
    "回答风格：简洁直接，不啰嗦。用户偏好短句回复。\n"
    "\n"
    "## 核心能力\n"
    "1. 智能对话与信息检索\n"
    "2. 记忆管理 — 自动记录重要信息，后续对话中引用\n"
    "3. 日程管理 — 发现日程意图自动提议记录\n"
    "4. 笔记管理 — 捕捉值得保存的想法和观点\n"
    "5. 代码执行 — 在代码运行器中执行 Python 代码（非隔离，仅限本地单用户）\n"
    "6. 上下文压缩 — 长对话自动生成摘要\n"
    "7. 网页访问 — 读取网页内容或主动联网搜索最新信息\n"
    "8. 内容总结 — 分析任意链接（文章/B站/GitHub/视频），生成结构化摘要\n"
    "\n"
    "## 行为准则\n"
    "1. 发现日程安排 → 调用 add_schedule 记录\n"
    "2. 发现值得记录的想法 → 调用 add_note 记录\n"
    "3. 用户要求跑代码 → 调用 execute_code\n"
    "4. 需要查已有日程/笔记/记忆 → 调用对应搜索工具\n"
    "5. 需要分析时间安排 → 调用 time_plan\n"
    "6. 用户发来链接 / 让你看某个网页 / 总结这篇文章或视频 → 优先调用 analyze_content（自动识别B站/GitHub/文章/视频并生成摘要）\n"
    "7. 需要读取网页原始内容（如提取特定文字）→ 调用 web_fetch\n"
    "8. 需要联网查最新信息 → 调用 web_search 搜索\n"
    "9. 需要查本地文献/论文/书籍内容 → 调用 retrieve_docs（RAG 检索）\n"
    "10. 需要查已编译的专题/Wiki → 调用 query_wiki\n"
    "11. 用户问知识库状态 → 调用 kb_stats\n"
    "12. 需要记录日程/笔记/记忆/技能 → 调用 smart_classify（不要用 retrieve_docs 记录信息）\n"
    "\n"
    "## 确认卡片（Confirm Card）\n"
    "当需要用户确认不可逆操作（删除、合并、归档等）或提供多个互斥决策时，"
    "在回复末尾输出确认卡片标记：\n"
    "<!-- zenith-confirm-card:{\\\"id\\\":\\\"唯一标识\\\",\\\"title\\\":\\\"标题\\\",\\\"description\\\":\\\"说明\\\",\\\"options\\\":[{\\\"label\\\":\\\"按钮文字\\\",\\\"value\\\":\\\"动作标识\\\",\\\"confirmText\\\":\\\"用户点击后自动发送的确认消息\\\",\\\"variant\\\":\\\"primary|danger|default\\\"}]} -->\n"
    "要求：id 唯一、options 至少一个、confirmText 必须是一句用户可直接发送的完整确认指令。"
)

DEFAULT_CONFIG = {
    "api_base": "https://api.siliconflow.cn/v1",
    "api_key": "",
    "model": "deepseek-ai/DeepSeek-V3",
    "temperature": 0.7,
    "max_tokens": 4096,
    "system_prompt": SYSTEM_PROMPT,
    "code_exec_timeout": 30,
    "max_code_output": 10000,
    # 代码执行开关（默认关闭。开源仓库面向未知部署者，需显式开启。
    # 本地单用户可在 config.yaml 设为 true。多用户部署必须先用 Docker 隔离，见 SECURITY.md）
    "code_execution_enabled": False,
    "context_compress_threshold": 20,
    "memory_extract_interval": 5,
    "auto_distill_enabled": True,

    # Phase 2.1: 防幻觉 — 审计员 Skill
    "auditor_skill": {
        "enabled": True,
    },

    # Phase 2.2-2.4: 验证器配置
    "validators": {
        "input": {"enabled": True, "block_high_risk": True},
        "output": {"enabled": True, "confidence_check": True, "memory_contradiction_check": True},
    },

    # 市场分析配置（已封存）
    "market_analysis_enabled": False,
    "market_analysis_time": "07:00",
    "gold_focus_contract": "GOLD - COMMODITY",
    "cftc_zscore_window": 156,
    "cftc_cache_days": 1200,

    # Phase 1: 执行追踪
    "trace": {
        "enabled": True,
        "show_tool_bubbles": True,
    },

    # Phase 3.5: 审计日志
    "audit": {
        "enabled": True,
        "log_path": "data/audit/",
        "retention_days": 90,
    },

    # MCP 服务器配置（仿 WorkBuddy mcp.json 格式）
    "mcp_servers": [
        {"name": "fact-check-mcp", "disabled": True, "serverUrl": "https://localhost/mcp/fact-check", "description": "事实核查验证"},
        {"name": "code-verify-mcp", "disabled": True, "serverUrl": "https://localhost/mcp/code-verify", "description": "代码执行验证"},
        {"name": "guard-mcp", "disabled": True, "serverUrl": "https://localhost/mcp/guard", "description": "执行门控验证"},
    ],

    # MCP 配置来源：优先读取 WorkBuddy 的真实 mcp.json（含 4 个 zenith-auditor 依赖项）
    # 支持 ${ENV} 占位符（如 jin10 的 Bearer Token 应写为 "Bearer ${ZENITH_JIN10_API_TOKEN}"）
    "mcp": {
        "workbuddy_config_path": "~/.workbuddy/mcp.json",
        # 若 mcp.json 缺失或为空，回退到上方 mcp_servers 占位
        "prefer_workbuddy": True,
    },

    # 技能目录（仿 WorkBuddy ~/.workbuddy/skills/）
    "skills_dir": "~/.workbuddy/skills",

    # 多 Provider 配置
    "default_provider": "",
    "background_provider": "",
    "providers": [
        {
            "name": "siliconflow",
            "type": "openai",
            "api_base": "https://api.siliconflow.cn/v1",
            "api_key": "",
            "model": "deepseek-ai/DeepSeek-V3",
        },
    ],

    # Persona 配置
    "personas": [],
    "socratic_mode": True,
}


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """加载配置，优先级：YAML > JSON > 默认值"""
    ensure_dirs()

    # 1. 尝试 YAML
    if CONFIG_YAML.exists():
        with open(CONFIG_YAML, "r", encoding="utf-8") as f:
            saved = yaml.safe_load(f) or {}
        return {**DEFAULT_CONFIG, **saved}

    # 2. 尝试 JSON（兼容 v1）
    if CONFIG_JSON.exists():
        with open(CONFIG_JSON, "r", encoding="utf-8") as f:
            saved = json.load(f)
        return {**DEFAULT_CONFIG, **saved}

    # 3. 首次运行，写入默认配置
    save_config(DEFAULT_CONFIG)
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict):
    """保存配置到 YAML"""
    ensure_dirs()
    with open(CONFIG_YAML, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def get_api_base() -> str:
    return load_config().get("api_base", DEFAULT_CONFIG["api_base"])


def get_api_key() -> str:
    """获取 API Key：优先级 .env > config.yaml"""
    env_key = os.environ.get("ZENITH_LLM_API_KEY", "").strip()
    if env_key:
        return env_key
    return load_config().get("api_key", "").strip()


def get_model() -> str:
    return load_config().get("model", DEFAULT_CONFIG["model"])


def get_mcp_config_path() -> Path:
    """返回 WorkBuddy mcp.json 的绝对路径（支持 ~ 展开与 ${ENV} 占位符）"""
    cfg = load_config().get("mcp", {})
    raw = cfg.get("workbuddy_config_path", "~/.workbuddy/mcp.json")
    # 支持 ${ENV} 占位符
    for key, val in os.environ.items():
        raw = raw.replace(f"${{{key}}}", val)
    return Path(raw).expanduser()


def prefer_workbuddy_mcp() -> bool:
    return bool(load_config().get("mcp", {}).get("prefer_workbuddy", True))


def get_temperature() -> float:
    return float(load_config().get("temperature", DEFAULT_CONFIG["temperature"]))


def get_max_tokens() -> int:
    return int(load_config().get("max_tokens", DEFAULT_CONFIG["max_tokens"]))


def get_system_prompt() -> str:
    return load_config().get("system_prompt", DEFAULT_CONFIG["system_prompt"])


def is_code_execution_enabled() -> bool:
    """代码执行是否启用。默认关闭，需在 config.yaml 显式设 code_execution_enabled: true。"""
    return bool(load_config().get("code_execution_enabled", False))


def is_auto_distill_enabled() -> bool:
    """自动蒸馏是否启用。控制 daily/weekly 定时蒸馏循环（每对话自动蒸馏独立受 _auto_distill_conv 控制）。"""
    return bool(load_config().get("auto_distill_enabled", True))


_DOCKER_AVAILABLE_CACHE = None


def docker_available() -> bool:
    """Docker 是否安装且守护进程运行中。用于 code_runner 选择执行路径。

    缓存结果避免每次执行代码都检测。
    """
    global _DOCKER_AVAILABLE_CACHE
    if _DOCKER_AVAILABLE_CACHE is not None:
        return _DOCKER_AVAILABLE_CACHE
    import subprocess
    try:
        subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        _DOCKER_AVAILABLE_CACHE = True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        _DOCKER_AVAILABLE_CACHE = False
    return _DOCKER_AVAILABLE_CACHE


# ===== Provider 管理 =====

def get_providers() -> list[dict]:
    """返回所有 provider 配置列表"""
    return list(load_config().get("providers", []))


def get_provider(name: str = "") -> dict:
    """获取指定 provider 的完整配置。
    name 为空时使用 default_provider；未找到时回退到 providers[0]。
    """
    cfg = load_config()
    providers: list[dict] = cfg.get("providers", [])
    if not providers:
        raise RuntimeError(
            "无可用 LLM Provider，请在 config.yaml 中配置 providers 数组"
        )
    if not name:
        name = cfg.get("default_provider", "")
    if name:
        for p in providers:
            if p.get("name") == name:
                return dict(p)
        import logging
        logging.getLogger("zenith.config").warning(
            "Provider '%s' 不存在，回退到 %s", name, providers[0].get("name", "unknown")
        )
    return dict(providers[0])


def get_background_provider() -> dict:
    """获取后台任务专用 provider（蒸馏/记忆提取/日程检测）。
    若未配置 background_provider，自动回退到 default_provider。
    """
    cfg = load_config()
    name = cfg.get("background_provider", "")
    return get_provider(name)


def get_provider_api_key(provider: dict) -> str:
    """获取 provider 的 api_key，支持多层回退。
    优先级：ZENITH_{NAME}_API_KEY > provider.api_key > ZENITH_LLM_API_KEY > 全局 api_key
    """
    pname = provider.get("name", "")
    # 1. 该 provider 专属的环境变量
    env_var = f"ZENITH_{pname.upper().replace('-', '_')}_API_KEY"
    env_key = os.environ.get(env_var, "").strip()
    if env_key:
        return env_key
    # 2. provider 配置中的 api_key
    pk = provider.get("api_key", "").strip()
    if pk:
        return pk
    # 3. 回退到全局 ZENITH_LLM_API_KEY（v2 旧版兼容）
    global_env = os.environ.get("ZENITH_LLM_API_KEY", "").strip()
    if global_env:
        return global_env
    # 4. 回退到 config.yaml 全局 api_key（旧设置页兼容）
    cfg = load_config()
    global_key = cfg.get("api_key", "").strip()
    return global_key


def get_personas() -> list[dict]:
    """返回所有 Persona 配置列表"""
    return list(load_config().get("personas", []))
