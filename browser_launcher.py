# -*- coding: utf-8 -*-
"""统一的「打开浏览器」入口 —— 可显式指定浏览器，不再受系统默认浏览器摆布。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
为什么需要它（2026-10-03 根因实测）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
`start.py` / `launcher.pyw` 原先都直接调 `webbrowser.open(url)`。在本机实测：

    webbrowser._tryorder  ->  None
    webbrowser.get()      ->  WindowsDefault

而 CPython 的 `WindowsDefault.open()` 实现就是 `os.startfile(url)`
→ 走 Windows ShellExecute → **完全由「系统默认浏览器」决定**：

    class WindowsDefault(BaseBrowser):
        def open(self, url, new=0, autoraise=True):
            os.startfile(url)      # ← 谁被注册为 http/https 默认关联，就开谁
            return True

⇒ **「装没装 Tabbit」与「Zenith 开哪个浏览器」毫无关系。**
   只要 Edge 是系统默认浏览器，Zenith 就永远开 Edge，哪怕 Tabbit 装在隔壁。
   （另一种常见情形：Tabbit 只被设为 https 默认、http 仍是 Edge —— 而 Zenith 用的正是
     `http://127.0.0.1:8766/`，于是表现完全一样。）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
本模块做什么
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
把「用哪个浏览器」变成 **config.yaml 里的显式配置**，不再依赖系统关联：

    browser:
      enabled: true
      path: "D:/下载文件/Tabbit Browser/Application/Tabbit Browser.exe"
      args: []          # 附加在 URL 之前的参数，一般留空

**回退语义（重要）**：未配置 / `enabled: false` / 路径不存在 / 启动抛异常
→ 一律回退 `webbrowser.open(url)`，**与改动前行为完全一致**。
因此这是一个可随时关掉、零残留风险的开关（删掉整个 `browser:` 段即恢复原状）。

路径写法：YAML 里用 **正斜杠** 最省事（`D:/...`），避免反斜杠转义踩坑；
写成转义后的双反斜杠（`"D:\\..."`）也能正确解析。
"""
from __future__ import annotations

import logging
import os
import subprocess
import webbrowser
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.resolve()
CONFIG_FILE = PROJECT_DIR / "config" / "config.yaml"

_log = logging.getLogger("zenith.browser")


def _load_browser_cfg() -> dict:
    """读取 config.yaml 的 `browser` 段。

    任何异常（文件缺失 / yaml 缺失 / 段类型不对）都返回 `{}`
    —— 即「没配」→ 走系统默认，绝不因为读配置失败而阻断启动。
    """
    try:
        import yaml
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        sec = cfg.get("browser") or {}
        return sec if isinstance(sec, dict) else {}
    except Exception as e:
        _log.debug("读取 browser 配置失败（将走系统默认）: %s", e)
        return {}


def _as_path(raw) -> Path:
    """把配置里的路径字符串规整为 Path（展开 %VAR% / ~，兼容 / 与 \\）。"""
    s = os.path.expandvars(os.path.expanduser(str(raw)))
    return Path(s)


def resolve() -> str:
    """只解析「会用哪个浏览器」，不真正打开。供自检 / 诊断用。"""
    sec = _load_browser_cfg()
    if not sec.get("enabled"):
        return "default(未启用 browser 配置)"
    exe = _as_path(sec.get("path") or "")
    if not str(exe) or str(exe) == ".":
        return "default(browser.enabled=true 但缺 path)"
    if not exe.exists():
        return f"default(路径不存在: {exe})"
    return f"browser:{exe}"


def open_url(url: str, *, dry_run: bool = False) -> str:
    """打开 `url`，返回**实际采用的方式**（供调用方写日志）。

    返回值：
      "browser:<可执行文件路径>"  —— 用了配置指定的浏览器
      "default"                   —— 用了系统默认浏览器（未配置 / 回退）

    设计约束：
      * **永不抛异常** —— 打开浏览器失败不该拖垮服务启动。
      * 子进程句柄全断（DEVNULL + close_fds），避免 pythonw 下继承句柄导致僵尸。
      * Tabbit 是 Chromium 系：若它已在运行，`exe <url>` 会在**已有实例**里新开标签页，
        不会重复拉起进程。
    """
    sec = _load_browser_cfg()

    if sec.get("enabled"):
        raw_path = sec.get("path")
        if raw_path:
            exe = _as_path(raw_path)
            if exe.exists():
                args = sec.get("args") or []
                if not isinstance(args, list):
                    args = [str(args)]
                cmd = [str(exe), *[str(a) for a in args], url]
                if dry_run:
                    return f"browser:{exe} (dry-run, 未真正启动)"
                try:
                    subprocess.Popen(
                        cmd,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        close_fds=True,
                    )
                    return f"browser:{exe}"
                except Exception as e:  # noqa: BLE001
                    _log.warning("指定浏览器启动失败，回退系统默认: %s", e)
            else:
                _log.warning("browser.path 不存在，回退系统默认: %s", exe)
        else:
            _log.warning("browser.enabled=true 但未配置 browser.path，回退系统默认")

    if dry_run:
        return "default (dry-run, 未真正打开)"
    try:
        webbrowser.open(url)
    except Exception as e:  # noqa: BLE001
        _log.warning("系统默认浏览器打开失败: %s", e)
    return "default"


if __name__ == "__main__":
    # 自检入口：默认**只解析不打开**（要真开请显式加 --open）
    import sys

    print("配置文件 :", CONFIG_FILE)
    print("browser 段:", _load_browser_cfg() or "（未配置）")
    print("解析结果 :", resolve())
    if "--open" in sys.argv:
        target = "http://127.0.0.1:8766/"
        print("实际打开 :", open_url(target), "->", target)
    else:
        print("（未传 --open，仅解析，不打开任何窗口）")
