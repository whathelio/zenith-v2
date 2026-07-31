"""unshield.py — 提取时还原，将 {{SEC_xxx}} 替换回真实值
用法：复制 AI 回复 → 双击运行 → 粘贴还原后的内容
映射表：secure_map.json（与 shield.py 同目录）
"""
import re
import json
import os
import sys

MAP_FILE = os.path.join(os.path.dirname(__file__), "secure_map.json")


def load_map() -> dict:
    if os.path.exists(MAP_FILE):
        try:
            with open(MAP_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def unmask_text(text: str) -> str:
    """还原 {{SEC_xxx}} 占位符为原始值"""
    mapping = load_map()
    if not mapping:
        return text

    def replace_placeholder(match):
        key = match.group(1)
        return mapping.get(key, match.group(0))

    return re.sub(r"\{\{([A-Za-z0-9_]+)\}\}", replace_placeholder, text)


def get_clipboard():
    """获取剪贴板内容"""
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
    print("❌ 无法读取剪贴板")
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

    restored = unmask_text(raw)
    set_clipboard(restored)

    has_placeholders = "{{SEC_" in raw
    if has_placeholders:
        count = len(re.findall(r"\{\{SEC_\d+\}\}", raw))
        missing = count - len(re.findall(r"\{\{([A-Za-z0-9_]+)\}\}", restored))
        print(f"✅ 已还原 {count} 个占位符并复制到剪贴板！")
        if missing > 0:
            print(f"⚠️  {missing} 个占位符在映射表中未找到（可能已清理）")
    else:
        print("ℹ️  未检测到占位符，内容未变化")
