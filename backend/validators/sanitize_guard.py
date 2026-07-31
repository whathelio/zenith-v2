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
        "message": (
            f"🚨 {field} 包含疑似明文密钥（{names}），已拒绝写入知识库。"
            "请先运行 shield.py 脱敏为 {{SEC_xxx}} 占位符后重试。"
        ),
    }
