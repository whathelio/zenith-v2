"""Zenith v2 — Main Entry Point
Launches the FastAPI backend server and opens the default browser.
"""

import sys
import os
import time
import logging
import webbrowser
import threading
import socket
import tempfile
from pathlib import Path

# 配置日志 — 同时输出到文件（pythonw 无控制台时仍可排查）
PROJECT_DIR = Path(__file__).parent
_LOG_FILE = PROJECT_DIR / "zenith.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(_LOG_FILE), encoding="utf-8"),
    ],
)
logger = logging.getLogger("zenith.start")

# 确保项目根目录在 sys.path
sys.path.insert(0, str(PROJECT_DIR))

# 单实例锁 + 浏览器打开时间戳
_INSTANCE_LOCK_FILE = Path(tempfile.gettempdir()) / "zenith_v2.lock"
_BROWSER_TS_FILE = PROJECT_DIR / ".zenith.browser"
_BROWSER_COOLDOWN_SECONDS = 5


def _acquire_instance_mutex():
    """跨平台单实例锁。返回 (lock_handle, is_first_instance)。

    - Windows: 使用命名互斥量 (kernel32.CreateMutexW)
    - Unix (Linux/macOS): 使用 fcntl.flock 文件锁
    """
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            kernel32.GetLastError.restype = ctypes.c_uint32

            mutex = kernel32.CreateMutexW(None, False, "ZenithV2SingleInstanceMutex")
            last_error = kernel32.GetLastError()
            # ERROR_ALREADY_EXISTS = 183
            if mutex and last_error == 183:
                return mutex, False
            return mutex, True
        except (AttributeError, OSError):
            # 非 Windows 或 ctypes 不可用，降级到文件锁
            pass

    # Unix 文件锁
    try:
        import fcntl
        fd = os.open(str(_INSTANCE_LOCK_FILE), os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd, True  # 获取锁成功 → 是首实例
        except (IOError, OSError):
            os.close(fd)
            return None, False  # 锁已被占用 → 已有实例在运行
    except (ImportError, OSError):
        # fcntl 不可用或文件系统不支持，跳过单实例检查
        return None, True


def _browser_recently_opened() -> bool:
    """浏览器是否在冷却期内刚被打开过"""
    try:
        mtime = _BROWSER_TS_FILE.stat().st_mtime
        return (time.time() - mtime) < _BROWSER_COOLDOWN_SECONDS
    except Exception:
        return False


def _write_browser_ts():
    """记录浏览器打开时间戳"""
    try:
        _BROWSER_TS_FILE.write_text(str(time.time()), encoding="utf-8")
    except Exception:
        pass


if __name__ == "__main__":
    import uvicorn

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    url = f"http://localhost:{port}"

    # 业务端口是否已被占用（额外检查）
    def _is_port_in_use(p):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(("127.0.0.1", p)) == 0

    # 原子获取 Windows 命名互斥量
    mutex_handle, is_first_instance = _acquire_instance_mutex()

    if not is_first_instance:
        # 已有实例在运行
        if _browser_recently_opened():
            logger.info("Zenith 已在运行，浏览器冷却期内，不再重复打开标签页")
        else:
            logger.info(f"Zenith 已在运行，打开浏览器: {url}")
            _write_browser_ts()
            webbrowser.open(url)
        sys.exit(0)

    # 本实例成为主服务器，立即写入时间戳，让后续实例进入冷却期
    _write_browser_ts()

    # 如果业务端口已被占用（极端情况），打开浏览器后退出
    if _is_port_in_use(port):
        logger.info(f"端口 {port} 已被占用，打开浏览器: {url}")
        webbrowser.open(url)
        sys.exit(0)

    # 首次运行检查：无 config.yaml 则从模板创建
    config_file = PROJECT_DIR / "config" / "config.yaml"
    config_example = PROJECT_DIR / "config" / "config.yaml.example"
    is_first_run = not config_file.exists()
    if is_first_run and config_example.exists():
        import shutil
        shutil.copy(config_example, config_file)
        print("=" * 60)
        print("  首次运行 - 已创建 config.yaml")
        print("  请编辑 config.yaml 填入你的 API Key")
        print("  配置引导页面将在浏览器中打开")
        print("=" * 60)
        print()

    print("=" * 60)
    print("  Zenith v2 - 本地智能助手")
    print("=" * 60)
    print(f"  后端地址: {url}")
    print(f"  API 文档: {url}/docs")
    print()
    print("  数据完全存储在本地，不会上传到任何云端服务器。")
    print("  你的 API Key 和对话数据仅保存在本机。")
    print("=" * 60)
    print()

    # 延迟 2 秒后自动打开默认浏览器
    def _open_browser():
        time.sleep(2)
        webbrowser.open(url)
        print(f"  >> 已打开默认浏览器: {url}")

    threading.Thread(target=_open_browser, daemon=True).start()

    uvicorn.run(
        "backend.app:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info",
    )
