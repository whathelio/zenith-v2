"""Zenith v2 配置管理 — YAML + .env 双格式支持"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import yaml
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
DATA_DIR = PROJECT_DIR / "data"
# 2026-09-16: 支持 ZENITH_CONFIG_DIR 重定向。
# 起因：tests/conftest.py 此前只重定向了 DB，没有重定向配置目录 →
# `PUT /api/settings` 的测试会把 model/temperature **直接写进生产 config/config.yaml**
# （实测该文件 mtime 随每次 pytest 运行变化）。生产路径保持不变，仅在显式设置该变量时改道。
CONFIG_DIR = Path(os.environ.get("ZENITH_CONFIG_DIR") or (PROJECT_DIR / "config"))
CONFIG_JSON = DATA_DIR / "config.json"
CONFIG_YAML = CONFIG_DIR / "config.yaml"
ENV_FILE = PROJECT_DIR / ".env"
DB_PATH = DATA_DIR / "zenith.db"

logger = logging.getLogger(__name__)


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
    "7. 网页访问 — 读取网页内容（服务端渲染抓取优先）或联网搜索最新信息\n"
    "8. 内容总结 — 分析任意链接（文章/B站/GitHub/视频），生成结构化摘要\n"
    "9. 学术检索 — 按关键词/日期/期刊检索论文（OpenAlex/Crossref），结果存入本地学术库\n"
    "\n"
    "## 行为准则\n"
    "1. 发现日程安排 → 调用 add_schedule 记录\n"
    "2. 发现值得记录的想法 → 调用 add_note 记录\n"
    "3. 准备写入新笔记前 → 先调用 check_duplicate 查重；若已存在相似条目，改用 edit_note 更新，不要重复入库\n"
    "4. 用户粘贴大段转录或资料（视频字幕 / 长文章 / 讲稿）→ 属「资料整理」的用 distill_note 做结构化提取；只有用户本人的观点、决定、偏好才用 add_note\n"
    "5. 用户要求跑代码 → 调用 execute_code\n"
    "6. 需要查已有日程/笔记/记忆 → 调用对应搜索工具\n"
    "7. 需要分析时间安排 → 调用 time_plan\n"
    "8. 用户发来链接 / 让你看某个网页 / 总结这篇文章或视频 → 优先调用 analyze_content（自动识别B站/GitHub/文章/视频并生成摘要）\n"
    "9. 需要读取网页原始内容（如提取特定文字）→ 调用 web_fetch\n"
    "10. 需要联网查最新信息 → 调用 web_search 搜索（默认 DeepSeek 引擎，不可用时自动回退 Bing；"
    "query 支持 site: / after: / before: / lang: / \"精确短语\" 等运算符；"
    "只有确实需要正文细节时才传 need_content=true，否则拿到摘要后再用 web_fetch 取全文）\n"
    "11. 需要查本地文献/论文/书籍内容 → 调用 retrieve_docs（RAG 检索；返回「未命中」时会自动附本地笔记/记忆回落结果）\n"
    "12. 用户问知识库状态 → 调用 kb_stats\n"
    "13. 需要记录日程/笔记/记忆/技能 → 调用 smart_classify（不要用 retrieve_docs 记录信息）\n"
    "14. 用户要查论文/文献/最新研究/高影响力文章 → 调用 academic_search；用户给出 DOI 或论文链接 → 调用 paper_lookup\n"
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
    "max_code_output": 10000,  # 代码输出截断上限（code_runner._max_output_len 读取，非死配置）
    # 代码执行开关（默认关闭。开源仓库面向未知部署者，需显式开启。
    # 本地单用户可在 config.yaml 设为 true。多用户部署必须先用 Docker 隔离，见 SECURITY.md）
    "code_execution_enabled": False,
    "context_compress_threshold": 20,
    "context_token_budget": 32000,
    "chat_history_max_tokens": 12000,  # 历史注入的 token 上限（E 项：从新到旧保留）
    "tool_result_prune": {
        "enabled": True,
        "threshold_chars": 8192,
        "head_chars": 4096,
        "tail_chars": 1024,
    },
    "memory_extract_interval": 5,
    "auto_distill_enabled": True,

    # 财经日历自动同步（已开启，每日 8:00 拉取，run_on_start 启动时也同步一次）
    "calendar_sync": {
        "enabled": True,
        "hour": 8,
        "minute": 0,
        "days": 7,
        "min_star": 2,
        "run_on_start": True,
    },

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

    # 全局背景图片（设置页「外观」常用设置，存于 data/backgrounds/global{ext}）
    "background_image": "",
}


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


_config_cache: dict | None = None
_config_cache_sig: tuple | None = None


def _config_file_signature() -> tuple | None:
    """返回配置文件签名（存在时），用于判断是否需要重新加载。"""
    try:
        st = CONFIG_YAML.stat()
        return ("yaml", st.st_mtime_ns, st.st_size)
    except OSError:
        pass
    try:
        st = CONFIG_JSON.stat()
        return ("json", st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _read_config_mapping(path: Path) -> dict:
    """读 YAML 并保证返回 dict；损坏时抛 yaml.YAMLError（由调用方兜底）。"""
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return {}
    if not isinstance(data, dict):
        # 顶层是列表/字符串（例如写到一半被截断）都算损坏，不能当配置用
        raise yaml.YAMLError(f"配置文件顶层不是映射，而是 {type(data).__name__}")
    return data


def _config_backup_path() -> Path:
    return CONFIG_YAML.with_name(CONFIG_YAML.name + ".bak")


def _load_config_uncached() -> dict:
    """加载配置，优先级：YAML > JSON > 默认值

    2026-10-17 审计 A3-R4 修复：
      原实现对 `yaml.safe_load` **零异常捕获**，而 save_config 又是截断重写。
      一次写中断（断电/杀进程/磁盘满）留下的半截 YAML 会让此后**每次** load
      都抛异常 —— 全站 500 且服务起不来，且用户没有任何自助恢复路径。
      现改为：YAML 损坏 → 回退同目录 .bak → 仍不行则用默认值运行并**大声报错**。
      注意：损坏时**不覆盖**原文件（保留现场供人工抢救），也不静默吞掉。
    """
    ensure_dirs()

    # 1. 尝试 YAML
    if CONFIG_YAML.exists():
        try:
            return {**DEFAULT_CONFIG, **_read_config_mapping(CONFIG_YAML)}
        except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
            logger.error("config.yaml 解析失败：%s", exc)
            bak = _config_backup_path()
            if bak.exists():
                try:
                    cfg = {**DEFAULT_CONFIG, **_read_config_mapping(bak)}
                    logger.warning("已从 %s 恢复配置（损坏的原文件保留未覆盖）", bak.name)
                    return cfg
                except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc2:
                    logger.error("%s 同样不可用：%s", bak.name, exc2)
            logger.error(
                "配置全部不可用，本次以默认值运行；请人工检查 %s（文件已保留，未被覆盖）",
                CONFIG_YAML,
            )
            return dict(DEFAULT_CONFIG)

    # 2. 尝试 JSON（兼容 v1）
    if CONFIG_JSON.exists():
        try:
            with open(CONFIG_JSON, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if not isinstance(saved, dict):
                raise ValueError(f"顶层不是对象：{type(saved).__name__}")
            return {**DEFAULT_CONFIG, **saved}
        except (ValueError, OSError, UnicodeDecodeError) as exc:
            logger.error("config.json 解析失败，本次以默认值运行：%s", exc)
            return dict(DEFAULT_CONFIG)

    # 3. 首次运行，写入默认配置
    save_config(DEFAULT_CONFIG)
    return dict(DEFAULT_CONFIG)



def load_config() -> dict:
    """加载配置（带缓存），优先级：YAML > JSON > 默认值。

    配置写入后 mtime/size 会变化，签名不匹配即自动重载，避免每次调用都解析 YAML。
    """
    global _config_cache, _config_cache_sig
    ensure_dirs()
    sig = _config_file_signature()
    if _config_cache is not None and sig == _config_cache_sig:
        return copy.deepcopy(_config_cache)
    _config_cache = _load_config_uncached()
    _config_cache_sig = _config_file_signature()
    return copy.deepcopy(_config_cache)


def save_config(cfg: dict):
    """保存配置到 YAML（原子写 + 写后更新配置缓存）

    2026-10-17 审计 A3-R4 修复：原实现 `open(path,'w')` 直接截断重写，
    写到一半失败就留下半截文件。现改为「临时文件 → fsync → 上一版转 .bak →
    os.replace 原子替换」，保证任何时刻磁盘上都有一个完整的 config.yaml。
    """
    global _config_cache, _config_cache_sig
    ensure_dirs()
    text = yaml.dump(cfg, allow_unicode=True, default_flow_style=False, sort_keys=False)
    tmp = CONFIG_YAML.with_name(f"{CONFIG_YAML.name}.tmp-{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if CONFIG_YAML.exists():
            try:
                os.replace(CONFIG_YAML, _config_backup_path())
            except OSError as exc:  # 备份失败不该挡住写入
                logger.warning("保留 config.yaml.bak 失败：%s", exc)
        os.replace(tmp, CONFIG_YAML)
    except OSError:
        # 落盘失败必须让调用方知道（否则界面提示"已保存"但实际没写）
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise
    _config_cache = copy.deepcopy(cfg)
    _config_cache_sig = _config_file_signature()


# ===== 敏感字段掩码（仅供「读」端点使用）=====
#
# 背景：GET /api/settings 与 GET /api/modules/mcp 会把 config / mcp.json 原样返回，
# 其中 api_key、headers.Authorization 等是明文密钥。本服务无认证（本地单用户是有意
# 设计），任何能访问 127.0.0.1:8766 的本机进程都能读走，故只能在「读」这一侧收敛暴露面。
#
# ⚠️ 掩码只用于「读」。写端点**必须**配合 restore_masked_fields()：前端是
#    「GET 整包 → 改几个字段 → PUT 整包」的流程（SettingsView.tsx:68 → :108），
#    少了这一步，掩码串会顺着写回把真实密钥覆盖掉 —— 那比泄漏更糟。

# 字段名命中即视为敏感。只对「值是非空 str」的项生效，因此 max_tokens /
# context_token_budget / chat_history_max_tokens 这类「名字含 token、值是数字」
# 的配置天然不会被误掩码。
_SENSITIVE_NAME_RE = re.compile(
    r"key|token|secret|password|passwd|credential|authorization|cookie",
    re.IGNORECASE,
)

# 掩码串的形状：`sk-abc***yz（len=51）`。保留前缀 + 末两位 + 长度，用户能分辨
# 「配的是哪一个」，不会误以为配置丢了。
_MASK_RE = re.compile(r"^.{0,8}\*\*\*.{0,4}（len=\d+）$")


def mask_secret(value: str) -> str:
    """把明文密钥转成可辨识的掩码（保留前缀与长度，足够区分不同密钥）。"""
    s = str(value)
    n = len(s)
    if n >= 12:
        return f"{s[:6]}***{s[-2:]}（len={n}）"
    return f"{s[:max(1, n // 3)]}***（len={n}）"


def is_masked(value) -> bool:
    """value 是否是本模块生成的掩码串。

    写端点靠它区分「这是读出来又被回传的掩码」和「用户刚输入的新密钥」。
    """
    return isinstance(value, str) and bool(_MASK_RE.match(value))


def _is_sensitive_name(name: str) -> bool:
    return bool(_SENSITIVE_NAME_RE.search(str(name)))


def mask_sensitive_fields(obj):
    """递归掩码 obj 中「字段名敏感 + 值为非空字符串」的项。

    只按键名判定、不猜内容，因此 headers 里的 Authorization、env 里的
    *_API_KEY、providers[].api_key 都被同一条规则覆盖。
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(v, str) and v and _is_sensitive_name(k):
                out[k] = mask_secret(v)
            else:
                out[k] = mask_sensitive_fields(v)
        return out
    if isinstance(obj, list):
        return [mask_sensitive_fields(x) for x in obj]
    return obj


_DROP = object()


def _has_blank_sensitive(d: dict) -> bool:
    """该 dict 里是否存在「名字命敏感规则、值是空串」的字段。

    用于区分「改名」（会把掩码原样发回来）与「同位置换成一个新 provider」
    （敏感字段是空的）—— 后者绝不能继承上一个 provider 的密钥。
    """
    return any(
        isinstance(k, str) and _SENSITIVE_NAME_RE.search(k)
        and isinstance(v, str) and not v.strip()
        for k, v in d.items()
    )


def restore_masked_fields(new, old):
    """把 new 中「看起来是本模块生成的掩码」的值换回 old 里的真实值。

    old 中找不到对应真值时**丢弃该字段**（而不是写入掩码串）—— 宁可不改配置，
    也不能把密钥写坏。列表元素（providers）优先按 name 对齐，避免顺序变化张冠李戴。
    """
    if isinstance(new, dict):
        out = {}
        for k, v in new.items():
            old_v = old.get(k) if isinstance(old, dict) else None
            r = restore_masked_fields(v, old_v)
            if r is _DROP:
                continue
            # 2026-10-17 审计 0-2 修复：敏感字段被「清空」时保留旧值。
            # 前端是「GET 掩码 → 改几处 → PUT 整包」，用户把 provider 的 api_key
            # 输入框清空后保存会提交空串；而 is_masked("") 为 False → 空串原样落盘，
            # 真实密钥被静默清空（界面还提示"已保存"）。
            # 语义取舍：**UI 无法通过清空来删除密钥**（要删请直接改 config.yaml）——
            # 宁可"删不掉"也不能"误删"。
            if (
                isinstance(r, str) and not r.strip()
                and isinstance(old_v, str) and old_v.strip() and not is_masked(old_v)
                and isinstance(k, str) and _SENSITIVE_NAME_RE.search(k)
            ):
                logger.info("字段 %s 被提交为空值，已保留原配置（不做删除）", k)
                out[k] = old_v
                continue
            out[k] = r
        return out
    if isinstance(new, list):
        out = []
        for i, item in enumerate(new):
            old_item = old[i] if isinstance(old, list) and i < len(old) else None
            if isinstance(item, dict) and item.get("name") and isinstance(old, list):
                matched = next(
                    (o for o in old
                     if isinstance(o, dict) and o.get("name") == item.get("name")),
                    None,
                )
                if matched is not None:
                    old_item = matched
                elif _has_blank_sensitive(item):
                    # 名字对不上 + 敏感字段是空值 = 这一格被换成了一个新 provider，
                    # 不能沿用 old[i] 的密钥（否则新 provider 会静默继承上一个的 key）。
                    old_item = None
                # 其余情况保留 index 兜底：改名场景下密钥跟着位置走，是既有设计
                # （见 tests/backend/test_config_masking.py::test_rename_falls_back_to_index）。
            r = restore_masked_fields(item, old_item)
            if r is not _DROP:
                out.append(r)
        return out
    if is_masked(new):
        if isinstance(old, str) and old and not is_masked(old):
            return old
        return _DROP
    return new


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


def _mcp_config_candidates() -> list[Path]:
    """按优先级返回 WorkBuddy mcp.json 的候选路径（去重保序）。

    WorkBuddy 有两处可能落点，且**实测两者不一致**：
      - 用户主目录  ~/.workbuddy/mcp.json        ← mcp.json 实际所在
      - 应用数据目录 $WORKBUDDY_CONFIG_DIR/mcp.json ← 配置模板指向的位置

    因此不能只认配置值，必须带回退链，否则「配了 MCP 但找不到文件」会静默为空。
    """
    cfg = load_config().get("mcp", {})
    raw = cfg.get("workbuddy_config_path", "~/.workbuddy/mcp.json")
    # 支持 ${ENV} 占位符
    for key, val in os.environ.items():
        raw = raw.replace(f"${{{key}}}", val)

    cands: list[Path] = [Path(raw).expanduser()]
    env_dir = os.environ.get("WORKBUDDY_CONFIG_DIR", "").strip()
    if env_dir:
        cands.append(Path(env_dir) / "mcp.json")
    cands.append(Path.home() / ".workbuddy" / "mcp.json")

    seen: set[str] = set()
    out: list[Path] = []
    for c in cands:
        key = str(c).lower()
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


_mcp_path_warned = False


def get_mcp_config_paths() -> list[Path]:
    """返回候选链中**实际存在**的全部 mcp.json（去重保序，优先级即顺序）。

    为什么与 `get_mcp_config_path()` 并存：后者按「返回首个存在者」的语义被
    MCP 之外的调用方当作「唯一路径」使用，语义不动；而 MCP 配置需要**跨文件
    合并** —— 实测两份文件长期并存且内容不同（用户主目录 1 个 server /
    应用数据目录 7 个 server），只认首个命中会让后面那份的 server 静默消失。
    """
    return [p for p in _mcp_config_candidates() if p.exists()]


def get_mcp_config_path() -> Path:
    """返回实际存在的 mcp.json 绝对路径；全都不存在时告警一次并返回首选候选。"""
    global _mcp_path_warned
    cands = _mcp_config_candidates()
    for c in cands:
        if c.exists():
            return c
    if not _mcp_path_warned:
        _mcp_path_warned = True
        import logging
        logging.getLogger("zenith.config").warning(
            "未找到 WorkBuddy mcp.json，MCP 桥将为空。已尝试: %s",
            " | ".join(str(c) for c in cands),
        )
    return cands[0]


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


def get_skills_dir_candidates() -> list[Path]:
    """按优先级返回技能目录候选链（去重保序）。

    实测复核（2026-09-28，`ls` 逐目录点数 SKILL.md）：
      - 配置项 `skills_dir`（config/config.yaml:100）= `D:\\WorkBuddyData\\.workbuddy\\skills`，
        该目录**已非空**，含 **36** 个 `<name>/SKILL.md`；
        它是 `get_skills_dir()` 当前的返回值（候选链首项命中）。
      - 项目级 `<PROJECT_DIR>/.workbuddy/skills`（= `zenith-v2\\.workbuddy\\skills`）**不存在**。
        （旧注释称「项目级技能落在这里、含 zenith-* 四个技能」，已与实测不符。）
      - 工作区级 `<PROJECT_DIR>.parent/.workbuddy/skills`
        （= `下载文件\\新建文件夹\\.workbuddy\\skills`）存在，含 **4** 个技能
        （zenith-calendar-sync / zenith-memory-consolidation / zenith-rag-planning / zenith-sqlite-patterns）。
      - 用户级 `~/.workbuddy/skills` 不存在。

    因此**仍需**保留回退链：配置目录一旦缺失/被清空，能落到工作区级目录，
    否则「技能文件扫描/导入」会变成 0。

    与 `get_skills_dir()` 的区别：后者只回答「当前用哪个目录」，而技能目录
    是**多个并列**的约定位置（项目级 / 工作区级 / 用户级 / 应用数据级），
    只按「当前那个」做白名单会把放在其他约定位置的文件误挡。
    """
    cands: list[Path] = []
    raw = (load_config().get("skills_dir", "") or "").strip()
    if raw:
        cands.append(Path(raw).expanduser())
    cands.append(PROJECT_DIR / ".workbuddy" / "skills")
    # 工作区级技能：{workspace}/.workbuddy/skills（本项目位于工作区下一层）
    cands.append(PROJECT_DIR.parent / ".workbuddy" / "skills")
    cands.append(Path.home() / ".workbuddy" / "skills")
    env_dir = os.environ.get("WORKBUDDY_CONFIG_DIR", "").strip()
    if env_dir:
        cands.append(Path(env_dir) / "skills")

    seen: set[str] = set()
    out: list[Path] = []
    for c in cands:
        key = str(c).lower()
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _has_skill_files(d: Path) -> bool:
    """目录下是否存在 <name>/SKILL.md 结构"""
    try:
        return any((sub / "SKILL.md").exists() for sub in d.iterdir() if sub.is_dir())
    except OSError:
        return False


_skills_dir_warned = False


def get_skills_dir() -> str:
    """返回实际可用的技能目录绝对路径。

    优先返回「存在且含 SKILL.md」的候选；都没有则退回首个已存在目录；
    全都不存在则告警一次并返回配置值。
    """
    global _skills_dir_warned
    cands = get_skills_dir_candidates()
    for c in cands:
        if c.is_dir() and _has_skill_files(c):
            return str(c)
    for c in cands:
        if c.is_dir():
            return str(c)
    if not _skills_dir_warned:
        _skills_dir_warned = True
        import logging
        logging.getLogger("zenith.config").warning(
            "未找到含 SKILL.md 的技能目录，技能文件扫描将为空。已尝试: %s",
            " | ".join(str(c) for c in cands),
        )
    return str(cands[0]) if cands else ""


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
