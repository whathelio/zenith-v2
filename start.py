"""Zenith v2 -- Main Entry Point
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

sys.path.insert(0, str(PROJECT_DIR))

# Single-instance file lock + PID tracking
_INSTANCE_LOCK_FILE = Path(tempfile.gettempdir()) / "zenith_v2.lock"
_PID_FILE = Path(tempfile.gettempdir()) / "zenith_v2.pid"
_BROWSER_TS_FILE = PROJECT_DIR / ".zenith.browser"
_BROWSER_COOLDOWN_SECONDS = 5


def _acquire_instance_lock(port: int = 8766):
    """Cross-platform single-instance file lock. Returns (lock_handle, is_first_instance).

    Uses exclusive file lock to detect if another instance is running.
    If lock fails, checks if port is actually in use to rule out zombie locks.
    - Lock acquired -> (fd, True), fd must be kept open to hold lock
    - Lock held + port in use -> (None, False)
    - Lock held but port free -> delete stale lock and retry
    - File locks unsupported -> (None, True), skip check
    """
    max_retries = 2

    for attempt in range(max_retries):
        if sys.platform == "win32":
            try:
                import msvcrt
                fd = os.open(str(_INSTANCE_LOCK_FILE), os.O_CREAT | os.O_RDWR)
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    return fd, True
                except (IOError, OSError):
                    os.close(fd)
                    if attempt < max_retries - 1:
                        if not _is_port_in_use(port):
                            logger.info("Zombie lock detected: port free, clearing stale lock and retrying")
                            _INSTANCE_LOCK_FILE.unlink(missing_ok=True)
                            continue
                    return None, False
            except (ImportError, OSError):
                return None, True

        # Unix (Linux/macOS) fcntl file lock
        try:
            import fcntl
            fd = os.open(str(_INSTANCE_LOCK_FILE), os.O_CREAT | os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd, True
            except (IOError, OSError):
                os.close(fd)
                if attempt < max_retries - 1:
                    if not _is_port_in_use(port):
                        logger.info("Zombie lock detected: port free, clearing stale lock and retrying")
                        _INSTANCE_LOCK_FILE.unlink(missing_ok=True)
                        continue
                return None, False
        except (ImportError, OSError):
            return None, True

    return None, True


def _browser_recently_opened() -> bool:
    """Check if browser was opened within the cooldown period."""
    try:
        mtime = _BROWSER_TS_FILE.stat().st_mtime
        return (time.time() - mtime) < _BROWSER_COOLDOWN_SECONDS
    except Exception:
        return False


def _write_browser_ts():
    """Record browser open timestamp."""
    try:
        _BROWSER_TS_FILE.write_text(str(time.time()), encoding="utf-8")
    except Exception:
        pass


def _is_port_in_use(p: int) -> bool:
    """Check if a port is currently in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", p)) == 0


if __name__ == "__main__":
    import uvicorn

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    url = f"http://localhost:{port}"

    lock_handle, is_first_instance = _acquire_instance_lock(port)

    if not is_first_instance:
        if _browser_recently_opened():
            logger.info("Zenith already running, browser cooldown active, skipping")
        else:
            logger.info(f"Zenith already running, opening browser: {url}")
            _write_browser_ts()
            webbrowser.open(url)
        sys.exit(0)

    _write_browser_ts()

    # 写入 PID 文件，供 zenith.bat 一键停止使用
    _PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

    if _is_port_in_use(port):
        logger.info(f"Port {port} already in use, opening browser: {url}")
        webbrowser.open(url)
        sys.exit(0)

    # First-run: create config.yaml from template if not exists
    config_file = PROJECT_DIR / "config" / "config.yaml"
    config_example = PROJECT_DIR / "config" / "config.yaml.example"
    is_first_run = not config_file.exists()
    if is_first_run and config_example.exists():
        import shutil
        shutil.copy(config_example, config_file)
        logger.info("=" * 60)
        logger.info("  First run - created config.yaml")
        logger.info("  Edit config.yaml to add your API Key")
        logger.info("  Setup page will open in browser")
        logger.info("=" * 60)

    logger.info("=" * 60)
    logger.info("  Zenith v2 - Local AI Assistant")
    logger.info("=" * 60)
    logger.info(f"  Backend: {url}")
    logger.info(f"  API Docs: {url}/docs")
    logger.info("  All data stored locally. Nothing is uploaded.")
    logger.info("  Your API key and conversations stay on this machine.")
    logger.info("=" * 60)

    def _open_browser():
        time.sleep(2)
        webbrowser.open(url)
        logger.info(f"  Browser opened: {url}")

    threading.Thread(target=_open_browser, daemon=True).start()

    uvicorn.run(
        "backend.app:app",
        host="127.0.0.1",
        port=port,
        reload=False,
        log_level="info",
    )
