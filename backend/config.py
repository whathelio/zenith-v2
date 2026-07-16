"""Zenith v2 配置管理 — YAML + JSON 双格式支持"""
from __future__ import annotations

import json
import yaml
from pathlib import Path
from typing import Optional

PROJECT_DIR = Path(__file__).parent.parent
DATA_DIR = PROJECT_DIR / "data"
CONFIG_DIR = PROJECT_DIR / "config"
CONFIG_JSON = DATA_DIR / "config.json"
CONFIG_YAML = CONFIG_DIR / "config.yaml"
DB_PATH = DATA_DIR / "zenith.db"

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
    "5. 代码执行 — 安全沙箱中运行 Python 代码\n"
    "6. 上下文压缩 — 长对话自动生成摘要\n"
    "7. 网页访问 — 读取网页内容或主动联网搜索最新信息\n"
    "8. 内容总结 — 分析任意链接（文章/B站/GitHub/视频），生成结构化摘要\n"
    "9. 市场分析 — 查询现货黄金市场状态、CFTC持仓分析、宏观数据、每日分析报告\n"
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
    "9. 用户问黄金/市场/持仓/CFTC → 调用 query_market 或 cftc_positioning\n"
    "10. 用户要求做市场分析 → 调用 analyze_gold 生成综合分析报告\n"
    "11. 用户问预测命中情况 → 调用 track_predictions\n"
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
    "context_compress_threshold": 20,
    "memory_extract_interval": 5,
    "auto_distill_enabled": True,
    # 市场分析配置
    "market_analysis_enabled": True,
    "market_analysis_time": "07:00",
    "gold_focus_contract": "GOLD - COMMODITY",
    "cftc_zscore_window": 156,
    "cftc_cache_days": 1200,
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
    return load_config().get("api_key", "").strip()


def get_model() -> str:
    return load_config().get("model", DEFAULT_CONFIG["model"])
