"""Input Validator — LLM 调用前的高风险请求拦截"""
import re

# 高危指令模式
_HIGH_RISK_PATTERNS = [
    r'rm\s+-rf\s+/',
    r'DROP\s+TABLE',
    r'format\s+[cC]:',
    r'del\s+/[fFsSqQ]',
    r'chmod\s+777',
    r'>\s*/dev/sda',
    r'dd\s+if=.*of=',
    r'shutdown\s',
    r'reboot\s',
]

# 模糊请求模式（缺乏实体的短句）
_VAGUE_PATTERNS = [
    r'^(帮我|给我)(搞|弄|做)一下(那个|这个)',
    r'^那个(东西|文件|代码)',
    r'^搞一下',
]


def validate_input(text: str) -> dict:
    """
    检查用户输入。
    返回 {"passed": bool, "warning": str | None, "block": bool}
    """
    if not text or not text.strip():
        return {"passed": True, "warning": None, "block": False}

    # 1. 高危指令检测
    for pattern in _HIGH_RISK_PATTERNS:
        if re.search(pattern, text, re.I):
            return {
                "passed": False,
                "warning": "检测到高危指令，操作已被拦截。如需执行，请明确确认意图。",
                "block": True,
            }

    # 2. 模糊请求检测 — 追问缺失实体
    for pattern in _VAGUE_PATTERNS:
        if re.search(pattern, text):
            return {
                "passed": True,
                "warning": "请求比较模糊，缺少具体目标。请补充：要操作什么？具体内容是什么？",
                "block": False,
            }

    return {"passed": True, "warning": None, "block": False}
