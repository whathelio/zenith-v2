"""Zenith v2 Launcher — 纯 Python 启动器
替代 zenith.bat，解决 cmd start 命令兼容性问题。
双击 .pyw 不会弹出控制台窗口。
"""

import subprocess
import sys
import os
import time
import webbrowser
import socket
import ctypes
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.resolve()
PYTHONW = PROJECT_DIR / ".venv" / "Scripts" / "pythonw.exe"
START_PY = PROJECT_DIR / "start.py"
API_GATEWAY = PROJECT_DIR.parent / "api_gateway.py"
TASK_WORKER = PROJECT_DIR.parent / "task_worker.py"
CONFIG = PROJECT_DIR / "config" / "config.yaml"
LOG_FILE = PROJECT_DIR / "launcher.log"

PORT = 8766
URL = f"http://localhost:{PORT}"


def log(msg: str):
    """写日志到文件"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")


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


def is_port_open(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False


def wait_for_server(timeout: float = 15.0) -> bool:
    """等待服务器就绪，返回是否成功"""
    start = time.time()
    while time.time() - start < timeout:
        if is_port_open("127.0.0.1", PORT):
            return True
        # 检查子进程是否还活着
        time.sleep(0.5)
    return False


def main():
    log("Launcher starting")

    if not check_prerequisites():
        sys.exit(1)

    # 检测是否已有实例在运行
    if is_port_open("127.0.0.1", PORT):
        log("Server already running, opening browser")
        webbrowser.open(URL)
        sys.exit(0)

    # 设置环境变量
    env = os.environ.copy()
    env["ZENITH_ROOT"] = str(PROJECT_DIR.parent)
    env["ZENITH_API_KEY"] = "zenith-internal-v2"
    env["KNOWLEDGE_API_KEY"] = "zenith-internal-v2"
    env["ZENITH_RAG_EMBED_MODEL"] = str(PROJECT_DIR.parent / "bge-small-model")

    # 从 config.yaml 提取 API key
    try:
        import yaml
        with open(CONFIG, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        if config:
            for key, env_key in [("api_key", "LLM_API_KEY"), ("api_base", "LLM_BASE_URL"), ("model", "LLM_MODEL")]:
                val = config.get(key, "")
                if val:
                    env[env_key] = str(val)
    except Exception as e:
        log(f"Config read warning: {e}")

    # 启动主服务
    log(f"Starting server: {PYTHONW} {START_PY} {PORT}")
    try:
        proc = subprocess.Popen(
            [str(PYTHONW), str(START_PY), str(PORT)],
            cwd=str(PROJECT_DIR),
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        log(f"Server PID: {proc.pid}")
    except Exception as e:
        log(f"FAILED to start server: {e}")
        show_error(f"启动服务器失败:\n{e}")
        sys.exit(1)

    # 等待服务就绪
    log("Waiting for server...")
    if wait_for_server():
        log("Server ready")
    else:
        log("Server timeout (15s), opening browser anyway")

    # 打开浏览器
    webbrowser.open(URL)
    log("Browser opened")

    # 启动知识库 API 中台
    if API_GATEWAY.exists():
        time.sleep(1)
        subprocess.Popen(
            [str(PYTHONW), str(API_GATEWAY)],
            cwd=str(PROJECT_DIR.parent),
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        log("Started API gateway")

    # 启动异步任务 worker
    if TASK_WORKER.exists():
        time.sleep(1)
        subprocess.Popen(
            [str(PYTHONW), str(TASK_WORKER), "--poll", "2.0"],
            cwd=str(PROJECT_DIR.parent),
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        log("Started task worker")

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
