"""sanitize_guard.py — 后端落库守卫：检测明文密钥，拒绝写入知识库
与 frontend/src/shared/security.ts + tools/shield.py 规则保持一致。
原则：宁可漏检，绝不误杀 —— 仅匹配精确前缀，不做启发式猜测。
"""
import re
from typing import Optional

# ===== 与 shield.py / security.ts 完全同步的规则 =====
PLAIN_PATTERNS = [
    # 精确前缀模式（零误杀）
    (r"ghp_[A-Za-z0-9]{36}", "GitHub_Token"),
    (r"github_pat_[A-Za-z0-9_]{22,82}", "GitHub_PAT"),
    (r"glpat-[A-Za-z0-9_\-]{20,}", "GitLab_PAT"),
    (r"sk-(?:proj-)?[A-Za-z0-9]{32,}", "OpenAI_Key"),
    (r"sk-ant-(?:api03-)?[A-Za-z0-9_\-]{32,}", "Anthropic_Key"),
    (r"sk-[A-Za-z0-9]{32}", "DeepSeek_Key"),
    (r"sk-[A-Za-z0-9]{40,}", "SiliconFlow_Key"),
    (r"eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}", "JWT_Token"),
    (r"xox[bap]-[A-Za-z0-9-]+", "Slack_Token"),
    # 上下文赋值模式（需关键词引导）
    (r"(?:API[_-]?KEY|api[_-]?key|apikey|token|secret|password|pwd|pass)\s*[=:]\s*[\"']?[^\s\"'<>]{16,}[\"']?", "Secret_Assignment"),
    (r"(?:KEY|TOKEN|SECRET|PASSWORD|API_KEY)\s*=\s*[A-Za-z0-9+/=_-]{32,}", "Env_Var"),
    (r"(?:Bearer|bearer)\s+[A-Za-z0-9_\-.]{20,}", "Bearer_Token"),
]

_PATTERN_COMPILED = [(re.compile(p), name) for p, name in PLAIN_PATTERNS]


def find_plain_secrets(text: str, max_report: int = 10) -> list[dict]:
    """扫描文本中的明文密钥。返回 [{name, snippet, position}]"""
    if not text:
        return []
    findings = []
    seen = set()
    for rx, name in _PATTERN_COMPILED:
        for m in rx.finditer(text):
            raw = m.group(0)
            if raw in seen:
                continue
            seen.add(raw)
            findings.append({
                "name": name,
                "snippet": raw[:24] + "..." if len(raw) > 24 else raw,
                "position": m.start(),
            })
            if len(findings) >= max_report:
                return findings
    return findings


def contains_plain_secret(text: str) -> bool:
    """快速布尔检查：文本是否含明文密钥"""
    return bool(find_plain_secrets(text, max_report=1))


# ===== 统一的「守卫拒绝」文案（2026-09-18）=====
# 背景：同一个「拒绝写入」事件原先在 tools.py / confirm_flow.py / app.py /
# routers/goals.py / guard_store 里有三套措辞，维护时要改多处。
# 此处只收敛**句子结构**，不参与任何拒绝判定 —— 是否拒绝仍由各调用点决定。
#
# 两档操作指引（刻意分档，不是遗漏）：
# - GUIDE_PLACEHOLDER：面向**模型**。只说「产出什么」，不提工具名 ——
#   tools.py 的 result 是给模型转述用的，模型需要知道改写目标格式。
# - GUIDE_SHIELD：面向**用户 / API 调用方**。多给一步「怎么做到」。
GUIDE_PLACEHOLDER = "请先脱敏为 {{SEC_xxx}} 占位符后重试。"
GUIDE_SHIELD = "请先运行 shield.py 脱敏为 {{SEC_xxx}} 占位符后重试。"

# field 参数（存储层口径，如 "note"/"schedule"/"memory"/"goal"）→ 中文主题词。
# 兜底保留原样，避免未登记的 field 丢失辨识度。
_FIELD_SUBJECTS = {
    "content": "内容",
    "note": "笔记",
    "schedule": "日程",
    "memory": "记忆",
    "skill": "技能记忆",
    "goal": "目标",
}


def refusal_text(
    subject: str,
    *,
    action: str = "",
    names: str = "",
    guide: str = GUIDE_SHIELD,
) -> str:
    """生成统一的「守卫拒绝」文案（纯文案，无副作用）。

    subject: 主题词，如「笔记」「日程 (ID:5)」「生成的记忆内容」。
             必填 —— 否则用户不知道是哪个动作被拒。
    action:  可选，后果补充，如「未写入」「原稿未改动」。
    names:   可选，命中的密钥类型名（如 "OpenAI_Key"），供 API 层诊断。
    guide:   操作指引；传 "" 省略（列表型 actions 汇总行等场景，
             同一段文本里重复 N 遍指引只会变成噪音）。

    模板：{subject}被拒绝：内容含疑似明文密钥（{names}；{action}）。{guide}
    """
    meta = [m for m in (names, action) if m]
    suffix = f"（{'；'.join(meta)}）" if meta else ""
    return f"{subject}被拒绝：内容含疑似明文密钥{suffix}。{guide}"


def guard_store(content: str, field: str = "content") -> Optional[dict]:
    """落库前守卫。返回 None = 安全；返回 dict = 需拒绝，含详细说明。
    用法：
        risk = guard_store(note_content)
        if risk:
            raise HTTPException(400, risk["message"])
    """
    findings = find_plain_secrets(content)
    if not findings:
        return None
    names = ", ".join(dict.fromkeys(f["name"] for f in findings))
    return {
        "safe": False,
        "findings": findings,
        "names": names,
        "message": refusal_text(
            _FIELD_SUBJECTS.get(field, field),
            names=names,
            guide=GUIDE_SHIELD,
        ),
    }
