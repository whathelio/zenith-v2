"""Execution Validator — 工具执行后的结果验证（移植自 WorkBuddy MCP 逻辑）"""
import re
import ast
import logging

logger = logging.getLogger("zenith.validators")

# ====== 事实核查 ======

IMPOSSIBLE_DATE_PATTERNS = [
    re.compile(p) for p in [
        r"\b(\d{4})年13月", r"\b(\d{4})年(\d{1,2})月32[日号]",
        r"\b20[2-9][5-9]年(?:1[3-9]|[2-9]\d)月",
    ]
]

HALLUCINATION_MARKERS = [
    (r"根据\s*「[^」]{30,}」", "过度具体的引用"),
    (r"第\s*\d+\s*页.*第\s*\d+\s*行", "伪造的精确页码"),
    (r"(?:所有|全部|每一个).*(?:都|均|皆)", "绝对化表述"),
    (r"研究表明|据[^，,]{0,5}报道|专家指出", "无来源权威引用"),
]

ABS_WORDS = ["一定", "必然", "绝对", "毫无疑问", "肯定", "保证"]


def verify_claim(claim: str, context: str = "") -> dict:
    """验证一条声明是否有事实支撑"""
    issues = []
    score = 1.0

    for pat in IMPOSSIBLE_DATE_PATTERNS:
        if pat.search(claim):
            issues.append({"type": "impossible_date"})
            score -= 0.4

    for pat, desc in HALLUCINATION_MARKERS:
        if re.search(pat, claim):
            issues.append({"type": "hallucination_marker", "hint": desc})
            score -= 0.2

    if context:
        claim_entities = re.findall(r"\d+\.?\d*%?", claim)
        ctx_entities = re.findall(r"\d+\.?\d*%?", context)
        for num in claim_entities[:5]:
            if num not in ctx_entities:
                issues.append({"type": "number_not_in_context", "value": num})
                score -= 0.1

    for w in ABS_WORDS:
        if w in claim:
            issues.append({"type": "absolute_language", "word": w})
            score -= 0.15
            break

    score = max(0.0, min(1.0, score))
    verdict = "supported" if score >= 0.8 else "insufficient" if score >= 0.5 else "unsupported"

    return {"verdict": verdict, "confidence": round(score, 2), "issues": issues}


# ====== 代码验证 ======

DANGEROUS_MODULES = {"subprocess", "ctypes", "socket", "requests", "urllib", "http", "ftplib", "telnetlib", "smtplib"}

DANGER_CODE_PATTERNS = [
    (r"os\.(system|popen|remove|unlink|rmdir)", "dangerous_os_call"),
    (r"subprocess\.(call|run|Popen)", "subprocess_call"),
    (r"shutil\.rmtree", "destructive_file_op"),
    (r"exec\s*\(|eval\s*\(", "dynamic_execution"),
    (r"__import__\s*\(\s*[\"']os[\"']\s*\)", "dynamic_os_import"),
]


def verify_execution(code: str, claimed_output: str = "") -> dict:
    """验证代码安全性（执行前扫描）"""
    risks = []
    for pattern, risk_type in DANGER_CODE_PATTERNS:
        if re.search(pattern, code, re.IGNORECASE):
            risks.append({"type": risk_type, "blocked": True})

    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in DANGEROUS_MODULES:
                        risks.append({"type": "dangerous_import", "module": alias.name})
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in DANGEROUS_MODULES:
                    risks.append({"type": "dangerous_import", "module": node.module})
    except SyntaxError:
        risks.append({"type": "syntax_error"})

    return {
        "safe": len(risks) == 0,
        "risks": risks,
        "recommendation": "禁止执行" if risks else "代码安全扫描通过",
    }


def check_imports(code: str) -> dict:
    """检查代码 import 是否有效"""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {"valid": [], "invalid": [], "parse_error": str(e)}

    valid, invalid, dangerous = [], [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name.split(".")[0]
                if mod in DANGEROUS_MODULES:
                    dangerous.append(mod)
                else:
                    valid.append(mod)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mod = node.module.split(".")[0]
            if mod in DANGEROUS_MODULES:
                dangerous.append(mod)
            else:
                valid.append(mod)

    return {"valid": list(set(valid)), "invalid": list(set(invalid)), "dangerous": list(set(dangerous))}


# ====== 执行门控 ======

DANGER_ACTIONS = [
    (r"rm\s+-rf\s+[/~]", "critical", "递归删除系统目录"),
    (r"rm\s+-rf\s+\*", "critical", "删除当前目录所有文件"),
    (r"format\s+[A-Z]:", "critical", "格式化磁盘"),
    (r"DROP\s+TABLE\s", "critical", "删除数据库表"),
    (r"DROP\s+DATABASE\s", "critical", "删除数据库"),
    (r"shutil\.rmtree\s*\(\s*[\"']/", "critical", "递归删除系统目录"),
    (r"chmod\s+777\s", "high", "无限制权限设置"),
    (r"del\s+/[SQ]\s", "critical", "Windows 强制删除"),
]

INJECTION_PATTERNS = [
    (r"ignore\s+(?:all\s+)?(?:previous|prior)\s+(?:instructions?|directives?)", "critical", "覆盖系统指令"),
    (r"(?:forget|disregard)\s+(?:all\s+)?(?:previous)\s+(?:instructions?|rules?)", "critical", "遗忘系统规则"),
    (r"you\s+are\s+now\s+(?:an?\s+)?(?:uncensored|evil|malicious)", "high", "角色劫持"),
    (r"(?:DAN|Do\s*Anything\s*Now)\s+mode", "critical", "DAN 越狱尝试"),
    (r"(?:print|show|reveal)\s+(?:me\s+)?(?:your|the)\s+(?:system\s+)?(?:prompt|instructions?)", "high", "提取系统指令"),
]

SQL_INJECTION_PATTERNS = [
    (r"(?:'|\")\s+OR\s+1\s*=\s*1", "critical", "SQL 注入"),
    (r"UNION\s+(?:ALL\s+)?SELECT\s+", "critical", "UNION SELECT 注入"),
    (r"DROP\s+TABLE\s+", "critical", "SQL 删表"),
    (r"xp_cmdshell", "critical", "MSSQL 危险存储过程"),
]


def guard_action(action_desc: str, code: str = "") -> dict:
    """高风险操作执行前检查"""
    combined = f"{action_desc}\n{code}"
    detected = []

    for pattern, level, desc in DANGER_ACTIONS:
        if re.search(pattern, combined, re.IGNORECASE):
            detected.append({"pattern": pattern, "risk_level": level, "description": desc})

    if not detected:
        return {"verdict": "allow", "risk_level": "low", "reason": "未检测到高风险模式"}

    level_rank = {"critical": 3, "high": 2, "medium": 1, "low": 0}
    max_risk = max(detected, key=lambda d: level_rank.get(d["risk_level"], 0))
    verdict = "block" if level_rank.get(max_risk["risk_level"], 0) >= 3 else "review"

    return {"verdict": verdict, "risk_level": max_risk["risk_level"],
            "reason": max_risk["description"], "detected_risks": detected}


def detect_injection(text: str) -> dict:
    """检测注入风险（提示词/SQL）"""
    findings = []

    for pattern, level, desc in INJECTION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            findings.append({"type": "prompt_injection", "risk_level": level, "description": desc})

    for pattern, level, desc in SQL_INJECTION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            findings.append({"type": "sql_injection", "risk_level": level, "description": desc})

    total = len(findings)
    has_critical = any(f["risk_level"] == "critical" for f in findings)

    return {"safe": not has_critical and total == 0, "has_critical": has_critical,
            "total_findings": total, "findings": findings}


# ====== 统一验证入口 ======

def validate_tool_result(tool_name: str, args: dict, result_text: str) -> list[dict]:
    """工具执行后统一验证，返回警告列表"""
    warnings = []

    if tool_name == "web_search" or tool_name == "web_fetch":
        if not result_text or len(result_text) < 5:
            warnings.append({"level": "warning", "type": "empty_result",
                             "message": f"{tool_name} 返回了空或无意义结果"})
        elif "error" in result_text.lower()[:50]:
            warnings.append({"level": "warning", "type": "error_in_result",
                             "message": f"{tool_name} 返回结果包含错误"})

    elif tool_name == "execute_code":
        scan = verify_execution(args.get("code", ""))
        if not scan["safe"]:
            warnings.append({"level": "warning", "type": "dangerous_code",
                             "message": f"代码包含风险模式: {[r['type'] for r in scan['risks']]}"})

    elif tool_name == "add_schedule":
        claim = f"日程: {args.get('title','')} 时间: {args.get('start_time','')}"
        v = verify_claim(claim)
        if v["confidence"] < 0.5:
            warnings.append({"level": "warning", "type": "unreliable_schedule",
                             "message": f"日程信息可信度较低 ({v['confidence']})，请核实时间"})

    elif tool_name == "mem_add":
        content = args.get("content", "")
        v = verify_claim(content)
        if v.get("issues"):
            warnings.append({"level": "info", "type": "memory_issues",
                             "message": f"记忆内容存在疑虑: {[i['type'] for i in v['issues']]}"})

    # 通用 guard 检查
    if args:
        args_str = str(args)
        g = guard_action(f"工具 {tool_name}", args_str)
        if g["verdict"] == "block":
            warnings.append({"level": "warning", "type": "danger_action",
                             "message": f"高风险操作: {g['reason']}"})

    return warnings
