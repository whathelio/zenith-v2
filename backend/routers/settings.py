"""Settings API — key-value 配置存储"""
import logging
from fastapi import APIRouter, Body, HTTPException
from ..config import (
    load_config,
    save_config,
    mask_sensitive_fields,
    restore_masked_fields,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])
logger = logging.getLogger("zenith.settings")


# 高权限配置键（2026-09-16）—— 本服务无认证、只监听 127.0.0.1，本机任意进程都能 PUT；
# 这些键被改动会**扩大攻击面或改变安全边界**，故只允许直接编辑 config/config.yaml。
# 判断标准：改它会不会新增「能做的事」或撤掉「已有的门禁」？会 → 进本表。
# 反例（**不在**本表、必须继续走设置页）：model / api_base / api_key / temperature /
# max_tokens / providers / system_prompt / personas / *_provider / context_* /
# memory_extract_interval / socratic_mode / background_image / trace / tool_result_prune /
# calendar_sync(纯调度参数) / auto_distill_enabled / auditor_skill / market_*（已封存的功能开关）
# / code_exec_timeout + max_code_output（只是已开启运行器的资源上限，调不调都改变不了
# 「运行器本身无隔离」这个事实，拦它属于安全剧场）。
_PROTECTED_KEYS = {
    # 代码执行总开关（DEFAULT_CONFIG 默认 False）。code_runner 自述「非沙箱，仅限本地单用户启用」，
    # SECURITY.md 要求多用户部署必须先 Docker 隔离 —— 一次 PUT 就打开 = 把「需人工确认的高风险决策」
    # 降级成「任意本机进程可做」。
    "code_execution_enabled": "代码执行开关（代码运行器非沙箱，见 SECURITY.md）",
    # 注入 MCP server 条目 = 让服务去连任意 serverUrl（等价于注入工具）。
    "mcp_servers": "MCP 服务器条目（可注入任意 serverUrl）",
    # 与上一条同源，只是绕道文件：workbuddy_config_path 可指向任意 mcp.json。
    "mcp": "MCP 配置来源（workbuddy_config_path 可指向任意文件）",
    # SKILL.md 会被当指令读进上下文、被技能扫描/导入消费：改这里 = 换一个内容源。
    "skills_dir": "技能目录（SKILL.md 会被当指令加载）",
    # 审计日志是唯一的可追溯性来源：enabled=False 或改 log_path 等于关掉/搬走监控。
    "audit": "审计日志开关与落盘路径（关掉即失去可追溯性）",
    # 输入/输出/执行三道校验（高风险拦截 + 防幻觉 + 事实核查），关掉即安全边界内缩。
    "validators": "输入/输出/执行校验开关（关掉即绕过风险门禁）",
}


@router.get("")
async def get_settings():
    """返回配置（敏感字段已掩码）。

    本服务无认证、只监听 127.0.0.1，任何本机进程都能读这里的内容；
    api_key / providers[].api_key / 名字含 token|secret|key 的字段一律掩码。
    掩码只影响本端点的返回，不影响 config.yaml 与 save_config()。
    """
    return mask_sensitive_fields(load_config())


@router.put("")
async def put_settings(data: dict = Body(default=None)):
    if data:
        cfg = load_config()
        # ⚠️ 必须在 cfg.update() 之前：前端 SettingsView 是「GET 整包 → 改几个字段 →
        # PUT 整包」（SettingsView.tsx:68 → :108），GET 返回的掩码会顺着这条路径
        # 覆盖真实密钥。这里把掩码换回真值（找不到真值则丢弃该字段）。
        data = restore_masked_fields(data, cfg)
        # 安全防护：不允许清空 providers。若传入空数组且现有配置非空，保留现有。
        if ("providers" in data and not data["providers"]
                and cfg.get("providers")):
            logger.warning("拒绝清空 providers，保留现有 %d 个 provider", len(cfg["providers"]))
            del data["providers"]
        # 高权限键保护（2026-09-16）：只拦「值真的被改」的那几个。
        # ⚠️ 不能写成「键出现即 403」：SettingsView 是「GET 整包 → PUT 整包」，
        # 且 merged = { ...defaultSettings, ...s }（SettingsView.tsx:69）、
        # toSave = { ...settings }（:101）—— GET 返回完整配置，所以 PUT 包里**必然携带**
        # code_execution_enabled / mcp_servers / mcp / skills_dir / audit / validators。
        # 「出现即拒」会让设置页每次保存都 403，把 model / temperature 这类正常改动一起挡死。
        # 故按**值是否变化**判定：未变（正常回传）→ 剔除，本端点永不写这些键；
        # 真被改动 → 403，提示直接编辑 config/config.yaml。
        # 判定放在 restore_masked_fields 之后：掩码已还原为真值，不会把「掩码串 ≠ 真值」误判成改动。
        changed = [k for k in _PROTECTED_KEYS if k in data and data[k] != cfg.get(k)]
        if changed:
            detail = "、".join(f"{k}（{_PROTECTED_KEYS[k]}）" for k in changed)
            logger.warning("拒绝通过 API 修改高权限配置键: %s", changed)
            raise HTTPException(
                status_code=403,
                detail=f"以下高权限配置不允许通过 /api/settings 修改：{detail}。"
                       "这些键会扩大攻击面或改变安全边界，如需变更请直接编辑 config/config.yaml。",
            )
        for k in _PROTECTED_KEYS:
            data.pop(k, None)
        cfg.update(data)
        save_config(cfg)
    return {"success": True}
