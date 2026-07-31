"""shield.py — 发送前脱敏，用占位符替代敏感值，生成映射表
用法：复制原文 → 双击运行 → 粘贴脱敏后的内容
映射表：secure_map.json（与脚本同目录）
"""
import re
import json
import os
import sys
from datetime import datetime

MAP_FILE = os.path.join(os.path.dirname(__file__), "secure_map.json")

# ===== 检测规则 =====
# 优先级从高到低：先匹配精确模式，再匹配上下文模式
PATTERNS = [
    # --- 精确模式（前缀明确，几乎零误杀）---
    ("GITHUB_CLASSIC",       r'(ghp_[A-Za-z0-9]{36})'),
    ("GITHUB_FINE",          r'(github_pat_[A-Za-z0-9_]{22,82})'),
    ("GITLAB_PAT",           r'(glpat-[A-Za-z0-9_\-]{20,})'),
    ("OPENAI_KEY",           r'(sk-(?:proj-)?[A-Za-z0-9]{32,})'),
    ("ANTHROPIC_KEY",        r'(sk-ant-(?:api03-)?[A-Za-z0-9_\-]{32,})'),
    ("DEEPSEEK_KEY",         r'(sk-[A-Za-z0-9]{32})'),
    ("SILICONFLOW_KEY",      r'(sk-[A-Za-z0-9]{40,})'),
    ("JWT_TOKEN",            r'(eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,})'),

    # --- 上下文模式（需包含关键词引导）---
    ("API_ASSIGN",           r'(?:API[_-]?KEY|api[_-]?key|apikey|token|secret|password|pwd|pass)\s*[=:]\s*["\']?([^\s"\'<>]{16,})["\']?'),
    ("ENV_VAR",              r'(?:KEY|TOKEN|SECRET|PASSWORD|API_KEY)\s*=\s*([A-Za-z0-9+/=_-]{32,})'),
    ("BEARER_TOKEN",         r'(?:Bearer|bearer)\s+([A-Za-z0-9_\-\.]{20,})'),

    # --- 弱模式（可被用户禁用）---
    ("WEAK_HEX40",           r'(?:hex|sha|hash)\S{0,10}\s*[=:]\s*([A-Fa-f0-9]{40})\b'),
]

# 用户可以在这里禁用的规则
DISABLED_RULES = {"WEAK_HEX40"}  # 默认禁用泛化的 hex 匹配


def load_map() -> dict:
    if os.path.exists(MAP_FILE):
        try:
            with open(MAP_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            print(f"⚠️  映射表损��，重新创建")
    return {}


def save_map(mapping: dict):
    with open(MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2, ensure_ascii=False)


def _find_existing(value: str, mapping: dict) -> str | None:
    """查找 value 是否已有映射，返回占位符名或 None"""
    for k, v in mapping.items():
        if v == value:
            return k
    return None


def mask_text(text: str) -> tuple[str, int]:
    """脱敏文本，返回 (脱敏后文本, 新增映射数)"""
    mapping = load_map()
    next_id = len(mapping) + 1
    new_count = 0

    for rule_name, pattern in PATTERNS:
        if rule_name in DISABLED_RULES:
            continue

        def make_replacer():
            nonlocal next_id, new_count

            def replacer(match):
                nonlocal next_id, new_count
                raw = match.group(0)
                # 尝试复用已有映射
                existing = _find_existing(raw, mapping)
                if existing:
                    return f"{{{{{existing}}}}}"
                # 新建占位符
                placeholder = f"SEC_{next_id:03d}"
                mapping[placeholder] = raw
                next_id += 1
                new_count += 1
                return f"{{{{{placeholder}}}}}"

            return replacer

        text = re.sub(pattern, make_replacer(), text)

    save_map(mapping)
    return text, new_count


def get_clipboard():
    """获取剪贴板内容（兼容多种后端）"""
    try:
        import pyperclip
        return pyperclip.paste()
    except ImportError:
        pass
    try:
        import subprocess
        r = subprocess.run(["powershell", "-Command", "Get-Clipboard"],
                          capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            return r.stdout
    except Exception:
        pass
    print("❌ 无法读取剪贴板，请安装 pyperclip: pip install pyperclip")
    sys.exit(1)


def set_clipboard(text: str):
    """写入剪贴板"""
    try:
        import pyperclip
        pyperclip.copy(text)
        return
    except ImportError:
        pass
    try:
        import subprocess
        # PowerShell 无法直接设置含特殊字符的文本
        # 用 clip.exe（Windows 自带）
        p = subprocess.Popen(["clip"], stdin=subprocess.PIPE, text=True)
        p.communicate(input=text, timeout=3)
        return
    except Exception:
        pass
    print("❌ 无法写入剪贴板")
    sys.exit(1)


if __name__ == "__main__":
    raw = get_clipboard()
    if not raw or not raw.strip():
        print("剪贴板为空")
        sys.exit(0)

    masked, count = mask_text(raw)
    set_clipboard(masked)

    total = len(load_map())
    print(f"✅ 已脱敏并复制到剪贴板！")
    print(f"   本次新增: {count}  累计映射: {total}")
    print(f"   映射表: {MAP_FILE}")
    if masked != raw:
        preview = masked[:200].replace("\n", "\\n")
        print(f"   预览: {preview}...")
