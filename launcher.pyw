"""Zenith v2 Launcher — start.py 友好入口（双击 .pyw 不弹控制台）

v2（2026-07-31）：统一为 start.py 薄封装，消除与 zenith.bat 的行为差异。
- 主服务启动 / 单实例锁 / 健康检查 / 打开浏览器 全部委托 start.py（含 --wait 一致的
  浏览器冷却与知识库中台托管）
- 本文件只做：前置检查 → 拉起 start.py → 等待健康 → 异常弹窗提示
- 不再重复启动 api_gateway / task_worker（已由 start.py 统一幂等托管）
"""

import subprocess
import sys
import os
import time
import urllib.request
import json
import ctypes
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.resolve()
PYTHONW = PROJECT_DIR / ".venv" / "Scripts" / "pythonw.exe"
PYTHON = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"
START_PY = PROJECT_DIR / "start.py"
CONFIG = PROJECT_DIR / "config" / "config.yaml"
LOG_FILE = PROJECT_DIR / "launcher.log"

PORT = 8766
HEALTH_URL = f"http://127.0.0.1:{PORT}/api/health"


def log(msg: str):
    """写日志到文件"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def show_error(msg: str):
    """弹窗显示错误"""
    try:
        ctypes.windll.user32.MessageBoxW(0, msg, "Zenith v2 Error", 0x10)
    except Exception:
        pass


def check_prerequisites() -> bool:
    errors = []
    if not PYTHONW.exists():
        errors.append(f"Python not found:\n{PYTHONW}")
    if not START_PY.exists():
        errors.append(f"start.py not found:\n{START_PY}")
    if not CONFIG.exists():
        errors.append(f"config.yaml not found:\n{CONFIG}")
    if errors:
        msg = "Zenith v2 启动失败:\n\n" + "\n\n".join(errors)
        log(f"PREREQ FAIL: {msg}")
        show_error(msg)
        return False
    return True


def wait_for_health(timeout: float = 30.0) -> bool:
    """等待 /api/health 返回 ok（与 start.py 的健康检查口径一致）"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=1) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("status") == "ok":
                        return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def main():
    log("Launcher starting")

    if not check_prerequisites():
        sys.exit(1)

    # 启动主服务（start.py 负责单实例锁 / 健康检查 / 浏览器冷却 / 知识库中台托管）
    env = os.environ.copy()
    env["ZENITH_ROOT"] = str(PROJECT_DIR.parent)
    log(f"Starting server: {PYTHONW} {START_PY} {PORT}")
    try:
        proc = subprocess.Popen(
            [str(PYTHONW), str(START_PY), str(PORT)],
            cwd=str(PROJECT_DIR),
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log(f"Server PID: {proc.pid}")
    except Exception as e:
        log(f"FAILED to start server: {e}")
        show_error(f"启动服务器失败:\n{e}")
        sys.exit(1)

    # 等待就绪（start.py 会在就绪后自行打开浏览器）
    if wait_for_health():
        log("Server ready")
    else:
        log("Server timeout (30s) — 请查看 zenith.log")
        show_error("Zenith v2 未在 30 秒内就绪。\n请查看日志: zenith-v2\\zenith.log")

    log("Launcher done")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        log(f"FATAL: {tb}")
        show_error(f"Zenith 启动异常:\n\n{tb[-500:]}")
        sys.exit(1)
