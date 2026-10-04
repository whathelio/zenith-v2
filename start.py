"""Zenith v2 — Main Entry Point

启动 FastAPI 后端并打开浏览器。提供单实例控制、进程清理、启动诊断。

CLI:
    python start.py [PORT] [--no-browser] [--browser-delay N] [--verbose]
    python start.py --detach            # 守护化：后台启动后立即返回（脚本/自动化入口）
    python start.py --stop              # 停止实例（含中台与 worker）
    python start.py --stop --keep-aux   # 只停主服务，保留中台与 worker
    python start.py --status
    python start.py --reset-lock
"""

from __future__ import annotations

import sys
import os

# pythonw.exe / --detach 无控制台，必须在最早阶段捕获 stdout/stderr，否则导入期异常会静默丢失。
if sys.executable and (
    sys.executable.lower().endswith("pythonw.exe")
    or os.environ.get("ZENITH_DETACHED") == "1"
):
    try:
        _early_log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zenith.log")
        _early_log_stream = open(_early_log_path, "a", encoding="utf-8", errors="replace")
        sys.stdout = _early_log_stream
        sys.stderr = _early_log_stream
    except Exception:
        pass
    finally:
        del _early_log_path
import time
import json
import logging
import argparse
import asyncio
import signal
import threading
import faulthandler
import socket
import tempfile
import subprocess
import shutil
from pathlib import Path
from typing import Optional, Tuple

PROJECT_DIR = Path(__file__).parent.resolve()

# 统一的「打开浏览器」入口（2026-10-03）：支持 config.yaml 的 browser 段显式指定浏览器，
# 不再受系统默认浏览器摆布 —— 根因与回退语义详见 browser_launcher.py 的模块 docstring。
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
from browser_launcher import open_url as _open_browser_url  # noqa: E402

_LOG_FILE = PROJECT_DIR / "zenith.log"
_LOCK_FILE = Path(tempfile.gettempdir()) / "zenith_v2.lock"
_PID_FILE = Path(tempfile.gettempdir()) / "zenith_v2.pid"
_BROWSER_TS_FILE = PROJECT_DIR / ".zenith.browser"
# 退出标记：记录「上次是否干净退出」，供下次启动自检（O-03 方案 A 的轻量补偿）。
_EXIT_MARKER_FILE = PROJECT_DIR / "data" / "last_exit.json"
_BROWSER_COOLDOWN_SECONDS = 5
_HEALTH_TIMEOUT_SECONDS = 20

# 辅助服务进程句柄（gateway / worker），供 watchdog 监控与重启
_AUX_PROCS: dict[str, subprocess.Popen] = {}

# 知识库中台与异步任务 worker（launcher 旧版直接拉起，此处统一托管，避免多入口不一致）
_GATEWAY_PATH = PROJECT_DIR.parent / "api_gateway.py"
_WORKER_PATH = PROJECT_DIR.parent / "task_worker.py"
_GATEWAY_PORT = 8788

# RAG 运行环境（api_gateway / task_worker 依赖 pypdfium2/chromadb/torch，位于根工作区 .venv，
# 该 venv 的 site-packages 已装好依赖，仅 pyvenv.cfg 指针需指向本机解释器）
_RAG_VENV_PYTHONW = PROJECT_DIR.parent / ".venv" / "Scripts" / "pythonw.exe"
_RAG_VENV_PYTHON = PROJECT_DIR.parent / ".venv" / "Scripts" / "python.exe"
_RAG_VENV_PYTHON_UNIX = PROJECT_DIR.parent / ".venv" / "bin" / "python"

_RAG_EMBED_DIR = PROJECT_DIR.parent / "bge-small-model"
# 注意：旧库 zenith_rag/chroma_db 的 HNSW 索引损坏（link_lists.bin 0 字节，2026-08-05 验证），
# 已重建至 zenith_rag_new；旧目录因句柄占用暂保留（含 .bak 备份），勿改回。
# 注意：ChromaDB 1.5.x 的 Rust HNSW reader 在含中文的路径下会报
# "Error loading hnsw index"（索引本身完好）。因此 Windows 上 RAG 工作目录
# 必须落在纯 ASCII 路径（2026-08-25 实测复现）。
_RAG_WORK_DIR = Path(os.environ.get(
    "ZENITH_RAG_WORK_DIR",
    r"D:\dshs\zenith_rag_new" if os.name == "nt" else str(PROJECT_DIR.parent / "zenith_rag_new"),
))
_RAG_API_KEY = "test-key"  # 与 backend/knowledge_service.py 默认一致

DEFAULT_PORT = 8766


def _is_running_under_pythonw() -> bool:
    """检测是否在 pythonw.exe 下运行（无控制台，stdout/stderr 不可用）。"""
    return Path(sys.executable).name.lower().startswith("pythonw")


def _is_detached() -> bool:
    """是否由 --detach 守护化启动。

    --detach 的子进程没有控制台（stdout/stderr 指向 DEVNULL），日志处理需要
    与 pythonw 走同一条「重定向到日志文件」的分支，否则崩溃现场无处可查。
    """
    return os.environ.get("ZENITH_DETACHED") == "1"


def _pick_log_file() -> Path:
    """选择一个可写的日志文件。

    zenith.log 可能被外部日志查看器以只读句柄占用（此时无法以 append 打开），
    按 zenith.log → zenith-1.log → zenith-2.log 顺序回退，保证服务可启动。
    """
    candidates = [_LOG_FILE] + [PROJECT_DIR / f"zenith-{i}.log" for i in range(1, 6)]
    for candidate in candidates:
        try:
            with open(candidate, "a", encoding="utf-8", errors="replace"):
                pass
            return candidate
        except OSError:
            continue
    return _LOG_FILE


# ⚠️ 模块级 logger —— **必须在此定义，不要指望函数内临时取**（2026-09-22 修复）
#
# 本文件有 10 个函数在内部**直接使用 `logger`**：
#   `_acquire_instance_lock` / `_cleanup_lock_and_pid` / `_write_pid_file` /
#   `_find_processes_by_port` / `_find_zenith_processes` / `_kill_process` /
#   `_spawn_aux_services` / `_aux_services_watchdog` / `_first_run_setup` / `stop_existing_instance`
# 但原先只有 3 处（`stop_existing_instance` / `_self_health_watchdog` / `main`）各自在函数内
# 临时 `logging.getLogger(...)` —— 其余函数一旦执行到 logger 那一行就 `NameError`。
#
# 为什么长期没被发现：那些调用**绝大多数位于 `except` 分支**（如 `logger.debug("wmic 查询失败")`），
# 只有真出错才走到；而 Win11 24H2+ 已移除 `wmic` → 「wmic 必然失败 → except → NameError」成为必然崩溃。
#
# 实测影响：`python start.py --stop` 必然**中途崩溃**（崩点是命令行匹配那步的 wmic 兜底），
# 导致后面的 `_cleanup_lock_and_pid()` / `_mark_intentional_stop()` 都不执行 →
# 锁不清、`last_exit.json` 停在 "running"（下次启动误报「被硬杀」）。
#
# 修法：模块级定义一次即可（`getLogger` 返回单例；函数内的同名局部定义只会 shadow，无害）。
logger = logging.getLogger("zenith.start")


def _setup_logging(verbose: bool = False):
    """配置日志。pythonw 下将 stdout/stderr 重定向到日志文件，避免崩溃无声。"""
    level = logging.DEBUG if verbose else logging.INFO
    log_file = _pick_log_file()
    handlers: list[logging.Handler] = [logging.FileHandler(str(log_file), encoding="utf-8", mode="a")]

    # pythonw / --detach 都没有控制台；普通 python 仍可在终端看到输出
    if not (_is_running_under_pythonw() or _is_detached()):
        handlers.append(logging.StreamHandler())
    else:
        # 捕获 print 与未处理异常，写入同一日志文件
        try:
            log_stream = open(str(log_file), "a", encoding="utf-8", errors="replace")
            sys.stdout = log_stream
            sys.stderr = log_stream
        except Exception:
            pass

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )
    if log_file != _LOG_FILE:
        logging.getLogger("zenith.start").warning("zenith.log 被占用，本次日志回退写入 %s", log_file)



def _is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """检查端口是否被占用。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def _read_pid_file() -> Optional[int]:
    """读取 PID 文件，若文件不存在或无效返回 None。"""
    try:
        return int(_PID_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _process_exists(pid: int) -> bool:
    """跨平台检查进程是否存在。"""
    if pid is None or pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            kernel = ctypes.windll.kernel32
            handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                kernel.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except Exception:
            return False


def _acquire_instance_lock(port: int) -> Tuple[Optional[int], bool]:
    """获取单实例文件锁。

    返回 (fd, is_first_instance)。fd 需保持打开以持有锁。
    若检测到僵尸锁（锁存在但端口空闲、PID 无效），自动清理后重试。
    """
    max_retries = 2

    for attempt in range(max_retries):
        if sys.platform == "win32":
            try:
                import msvcrt
                fd = os.open(str(_LOCK_FILE), os.O_CREAT | os.O_RDWR)
                try:
                    # Windows 锁定基于文件字节区间，空文件可能导致锁定失败。
                    # 先确保文件至少包含 1 字节，再回到文件头加锁。
                    if os.fstat(fd).st_size == 0:
                        os.write(fd, b"\0")
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    return fd, True
                except (IOError, OSError):
                    os.close(fd)
                    if attempt < max_retries - 1 and _is_stale_lock(port):
                        logger.info("检测到僵尸锁，清理后重试")
                        _cleanup_lock_and_pid()
                        continue
                    return None, False
            except (ImportError, OSError):
                return None, True

        # Unix (Linux/macOS)
        try:
            import fcntl
            fd = os.open(str(_LOCK_FILE), os.O_CREAT | os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd, True
            except (IOError, OSError):
                os.close(fd)
                if attempt < max_retries - 1 and _is_stale_lock(port):
                    logger.info("检测到僵尸锁，清理后重试")
                    _cleanup_lock_and_pid()
                    continue
                return None, False
        except (ImportError, OSError):
            return None, True

    return None, True


def _is_stale_lock(port: int) -> bool:
    """判断当前锁是否为僵尸。

    ⚠️ 2026-09-11 修正：原判据是 `端口空闲 or PID 已死`（**或**）。
    启动早期存在一个窗口：「锁已持有、PID 已写、但 uvicorn 尚未绑定端口」，
    此时 `端口空闲 == True` 会把**活锁**判成僵尸 → 第二个实例删锁重抢 → 双实例。
    这正是「每次点击反复启动」的根因。

    改为「**且**」语义：只有 PID 记录不存在/已死、**并且**端口也没人听，才认定僵尸。
    PID 复用导致误判「已在运行」时，用 `--reset-lock` 可强制清除（逃生门已存在）。
    """
    pid = _read_pid_file()
    if pid is not None and _process_exists(pid):
        return False                      # 持锁者活着 → 不是僵尸（哪怕端口还没起来）
    if _is_port_in_use(port):
        return False                      # 端口有人听 → 有实例在跑 → 不是僵尸
    return True


def _cleanup_lock_and_pid():
    """清理锁文件和 PID 文件。失败必须可见（否则僵尸锁会导致后续启动误判"已在运行"）。"""
    try:
        _LOCK_FILE.unlink(missing_ok=True)
    except Exception as e:
        logger.warning("清理锁文件失败: %s (%s)", e, _LOCK_FILE)
    try:
        _PID_FILE.unlink(missing_ok=True)
    except Exception as e:
        logger.warning("清理 PID 文件失败: %s (%s)", e, _PID_FILE)


def _write_pid_file():
    """写入当前进程 PID。"""
    try:
        _PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except Exception as e:
        logger.warning("写入 PID 文件失败: %s", e)


def _browser_recently_opened() -> bool:
    """检查是否在浏览器冷却期内被重复触发。"""
    try:
        mtime = _BROWSER_TS_FILE.stat().st_mtime
        return (time.time() - mtime) < _BROWSER_COOLDOWN_SECONDS
    except Exception:
        return False


def _write_browser_ts():
    """记录浏览器打开时间戳。"""
    try:
        _BROWSER_TS_FILE.write_text(str(time.time()), encoding="utf-8")
    except Exception:
        pass


def _open_browser(url: str) -> None:
    """打开浏览器（走 browser_launcher，可被 config.yaml 的 browser 段指定为 Tabbit 等）。

    统一入口的意义：原先三处直接调 `webbrowser.open()`，等于把「开哪个浏览器」交给
    系统默认关联；现在三处共用同一个 helper，改一次全局生效 —— 见 code-governance-workflow
    §5b.11「修完一处，必须全库搜同形逻辑」。
    """
    try:
        how = _open_browser_url(url)
        logger.info("已打开浏览器（%s）: %s", how, url)
    except Exception as e:  # noqa: BLE001 —— 开浏览器失败不该拖垮服务启动
        logger.warning("打开浏览器失败: %s", e)


def _wait_for_health(port: int, timeout: float = _HEALTH_TIMEOUT_SECONDS) -> bool:
    """等待后端健康检查通过。"""
    url = f"http://127.0.0.1:{port}/api/health"
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("status") == "ok":
                        return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def _find_processes_by_port(port: int) -> list[int]:
    """查找占用指定端口的进程 PID（Windows / Linux / macOS）。"""
    pids: list[int] = []
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
                errors="replace",
            )
            for line in result.stdout.splitlines():
                # ⚠️ 2026-09-11 修正：**只认 LISTENING**。
                # netstat -ano 对每条连接会输出**两行**（服务端一行、客户端一行），
                # 两行的本地/外部地址里都含 ":port"。原判据把 ESTABLISHED 也算占用者，
                # 就会把**客户端进程**的 PID 收进来 —— 真实场景下客户端是**浏览器**，
                # 于是 `--stop` 会 taskkill 掉用户的浏览器。
                # 实测：保持一个客户端连接时，本函数返回 [服务端PID, 客户端PID]。
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.strip().split()
                    if parts:
                        try:
                            pids.append(int(parts[-1]))
                        except ValueError:
                            pass
        except Exception as e:
            logger.debug("netstat 查询失败: %s", e)
    else:
        try:
            result = subprocess.run(
                ["lsof", "-i", f"tcp:{port}", "-t"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
                errors="replace",
            )
            for line in result.stdout.splitlines():
                try:
                    pids.append(int(line.strip()))
                except ValueError:
                    pass
        except Exception as e:
            logger.debug("lsof 查询失败: %s", e)
    return list(set(pids))


def _find_zenith_processes(marker: str = "") -> list[int]:
    """按命令行匹配可能残留的 Zenith 相关进程（安全：排除自身与父进程）。

    marker 为空时匹配 start.py（主服务）；可指定 api_gateway.py / task_worker.py。
    """
    pids: list[int] = []
    keyword = marker or "start.py"
    excluded = {os.getpid(), os.getppid()}

    if sys.platform == "win32":
        # 优先 PowerShell（Get-CimInstance）；wmic 在 Win11 24H2+ 已被移除，仅作兜底
        try:
            ps_cmd = (
                "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' or Name='python.exe'\" | "
                f"Where-Object {{ $_.CommandLine -like '*{keyword}*' }} | "
                "Select-Object -ExpandProperty ProcessId"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True, text=True, check=False, timeout=10, errors="replace",
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.isdigit():
                    pid = int(line)
                    if pid not in excluded:
                        pids.append(pid)
        except Exception as e:
            logger.debug("PowerShell 查询失败: %s", e)

        if not pids:
            try:
                result = subprocess.run(
                    ["wmic", "process", "where", "name='pythonw.exe' or name='python.exe'", "get", "ProcessId,CommandLine", "/format:csv"],
                    capture_output=True,
                    text=False,
                    check=False,
                    timeout=10,
                )
                # wmic 在中文系统常返回 GBK/CP936，先尝试 UTF-8，失败则回退系统编码
                for encoding in ("utf-8", "gbk", "cp936"):
                    try:
                        stdout = result.stdout.decode(encoding, errors="replace")
                        break
                    except (UnicodeDecodeError, LookupError):
                        stdout = ""
                for line in stdout.splitlines():
                    line_lower = line.lower()
                    if keyword.lower() not in line_lower:
                        continue
                    parts = [p.strip().strip('"') for p in line.split(",")]
                    for part in reversed(parts):
                        try:
                            pid = int(part)
                            if pid not in excluded:
                                pids.append(pid)
                            break
                        except ValueError:
                            continue
            except Exception as e:
                logger.debug("wmic 查询失败: %s", e)
    else:
        try:
            result = subprocess.run(
                ["ps", "aux"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
                errors="replace",
            )
            for line in result.stdout.splitlines():
                line_lower = line.lower()
                if keyword.lower() not in line_lower:
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        pid = int(parts[1])
                        if pid not in excluded:
                            pids.append(pid)
                    except ValueError:
                        pass
        except Exception as e:
            logger.debug("ps 查询失败: %s", e)
    return list(set(pids))


def _kill_process(pid: int) -> bool:
    """安全终止进程。"""
    if pid is None or pid <= 0 or pid == os.getpid() or pid == os.getppid():
        return False
    if sys.platform == "win32":
        try:
            result = subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False, timeout=5)
            if result.returncode == 0:
                return True
            time.sleep(0.5)
            return not _process_exists(pid)
        except Exception as e:
            logger.debug("taskkill 失败 PID=%d: %s", pid, e)
            return False
    else:
        try:
            os.kill(pid, signal.SIGTERM)
            # 等待进程退出
            for _ in range(20):
                if not _process_exists(pid):
                    return True
                time.sleep(0.2)
            os.kill(pid, signal.SIGKILL)
            return True
        except ProcessLookupError:
            return True
        except Exception as e:
            logger.debug("kill 失败 PID=%d: %s", pid, e)
            return False


def stop_existing_instance(port: int = DEFAULT_PORT, keep_aux: bool = False) -> dict:
    """停止已运行的 Zenith 实例，返回清理摘要。

    keep_aux=False（默认）：连知识库中台(8788) 与任务 worker 一并停掉 —— 彻底清场。
    keep_aux=True ：只停主服务，保留中台与 worker，避免打断正在跑的文档入库任务。
    """
    # ⚠️ 2026-09-22 修复：本函数原先**直接引用模块级 `logger`**，但本文件并没有模块级 logger
    # （全文模式是每个函数内部各自 `logging.getLogger("zenith.start")`，见 L770/870/922/1146）。
    # 于是本函数内 4 处 logger 调用（原 L504 / L508 / L513 / L522）都会 `NameError`：
    #   · 以库方式调用 `stop_existing_instance(keep_aux=True)` → **必崩**在 keep_aux 分支；
    #   · `python start.py --stop` 只要走到「端口占用者被成功终止」或「命令行匹配命中」也会崩。
    # 崩点位于 `_cleanup_lock_and_pid()` / `_mark_intentional_stop()` **之前** →
    # 锁不清、`last_exit.json` 停在 "running"（下次启动误报「被硬杀」）。
    # 修法：与全文其它函数保持一致，在本函数内取 logger。
    logger = logging.getLogger("zenith.start")
    summary = {"pid_file": None, "by_port": [], "by_name": [], "lock_cleared": False, "errors": []}

    # 1. PID 文件
    pid = _read_pid_file()
    if pid and _process_exists(pid):
        if _kill_process(pid):
            summary["pid_file"] = pid
            time.sleep(0.5)
        else:
            summary["errors"].append(f"PID={pid} 终止失败")

    # 2. 端口占用（主服务 8766 + 知识库中台 8788）
    #    注：_find_processes_by_port 现在只返回 LISTENING 的持有者，不再误收客户端进程。
    for p in _find_processes_by_port(port):
        if _kill_process(p):
            summary["by_port"].append(p)
            logger.info("已终止占用端口 %d 的进程 PID=%d", port, p)
        else:
            summary["errors"].append(f"端口占用 PID={p} 终止失败")
    if keep_aux:
        logger.info("--keep-aux：保留知识库中台(%d)与任务 worker", _GATEWAY_PORT)
    else:
        for p in _find_processes_by_port(_GATEWAY_PORT):
            if _kill_process(p):
                summary["by_port"].append(p)
                logger.info("已终止占用端口 %d 的进程 PID=%d", _GATEWAY_PORT, p)
            else:
                summary["errors"].append(f"中台端口占用 PID={p} 终止失败")

    # 3. 命令行匹配兜底（start.py / api_gateway.py / task_worker.py）
    #    ⚠️ 这是**全机子串匹配**（'*start.py*'），无法区分是哪个项目的 start.py，
    #    多会话 / 多项目并行时有误杀风险。故逐条记 WARNING 并带 PID，便于事后审计。
    #    （更彻底的修法需要"项目级判别标识"，见 audit 报告 §9 —— 未验证前不盲改。）
    for p in _find_zenith_processes():
        logger.warning("命令行匹配命中 'start.py'，准备终止 PID=%d（请确认不是别的项目）", p)
        if _kill_process(p):
            summary["by_name"].append(p)
    if not keep_aux:
        for p in _find_zenith_processes(marker="api_gateway.py"):
            if _kill_process(p):
                summary["by_name"].append(p)
        for p in _find_zenith_processes(marker="task_worker.py"):
            if _kill_process(p):
                summary["by_name"].append(p)

    _cleanup_lock_and_pid()
    summary["lock_cleared"] = True
    # 这里是**操作者主动停止**，但进程是被 taskkill /F 硬杀的，main() 的 finally 跑不到，
    # 退出标记会停留在 running → 下次启动会误报「被硬杀」。故在此代为落一个 clean 标记。
    _mark_intentional_stop()
    return summary



def _is_cmdline_running_unix(keyword: str) -> bool:
    """Linux/macOS: 检查是否有进程命令行包含关键字。

    用于避免 zenith.sh 已启动的 api_gateway/task_worker 被 start.py 再次拉起。
    """
    try:
        r = subprocess.run(
            ["pgrep", "-f", keyword],
            capture_output=True, text=True, check=False, timeout=5,
            errors="replace",
        )
        return any(line.strip().isdigit() for line in r.stdout.splitlines())
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        try:
            r = subprocess.run(
                ["ps", "aux"],
                capture_output=True, text=True, check=False, timeout=5,
                errors="replace",
            )
            return any(keyword in line.lower() for line in r.stdout.splitlines())
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False

def _is_cmdline_running(keyword: str) -> bool:
    """检查是否有 python/pythonw 进程的命令行包含关键字（幂等判断）。

    注意：查询必须限定 Name='python*'，否则 powershell.exe 自身的命令行
    （含关键字）会被误匹配，导致永远判定为"已在运行"。
    """
    if sys.platform != "win32":
        return _is_cmdline_running_unix(keyword)
    if False:
        return False
    try:
        ps_cmd = (
            "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' or Name='python.exe'\" | "
            f"Where-Object {{ $_.CommandLine -like '*{keyword}*' }} | "
            "Measure-Object | Select-Object -ExpandProperty Count"
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True, text=True, check=False, timeout=10, errors="replace",
        )
        return r.stdout.strip().isdigit() and int(r.stdout.strip()) > 0
    except Exception:
        return False


def _read_env_key(name: str) -> Optional[str]:
    """从 zenith-v2/.env 读取单个键值（零依赖，与 backend/config.py 同款逻辑）。"""
    env_file = PROJECT_DIR / ".env"
    if not env_file.exists():
        return None
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip() == name:
                    return value.strip().strip('"').strip("'")
    except OSError:
        return None
    return None

def _read_yaml_key(name: str) -> Optional[str]:
    """从 config/config.yaml 读取单个标量键值，供辅助服务复用主服务 LLM 配置。"""
    config_file = PROJECT_DIR / "config" / "config.yaml"
    if not config_file.exists():
        return None
    try:
        import yaml
        with open(config_file, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        value = cfg.get(name)
        return str(value).strip() if value is not None else None
    except Exception:
        return None


def _read_provider_setting(name: str, provider_name: str = "deepseek") -> Optional[str]:
    """从 config/config.yaml 的 providers 列表读取指定 provider 的字段。

    顶层 `model`/`api_base` 是旧版兼容字段，可能是占位值（如 test-model）；
    LLM 调用应以 providers 列表中的模型与 base_url 为准。
    """
    config_file = PROJECT_DIR / "config" / "config.yaml"
    if not config_file.exists():
        return None
    try:
        import yaml
        with open(config_file, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        for provider in cfg.get("providers", []) or []:
            if isinstance(provider, dict) and provider.get("name") == provider_name:
                value = provider.get(name)
                return str(value).strip() if value is not None else None
    except Exception:
        return None
    return None



def _build_aux_env() -> dict:
    """构造 api_gateway / task_worker 的运行环境：
    本地 embedding 模型、固定工作目录、网关鉴权、复用 zenith 的 LLM 凭据。"""
    env = os.environ.copy()
    env.setdefault("ZENITH_RAG_EMBED_MODEL", str(_RAG_EMBED_DIR))
    env.setdefault("ZENITH_RAG_WORK_DIR", str(_RAG_WORK_DIR))
    env.setdefault("ZENITH_API_KEY", _RAG_API_KEY)
    llm_key = env.get("ZENITH_LLM_API_KEY") or _read_env_key("ZENITH_LLM_API_KEY") or _read_yaml_key("api_key")
    if llm_key:
        env.setdefault("LLM_API_KEY", llm_key)
    env.setdefault("LLM_BASE_URL", _read_provider_setting("api_base") or "https://api.deepseek.com/v1")
    env.setdefault("LLM_MODEL", _read_provider_setting("model") or "deepseek-flash")
    return env


def _wait_for_gateway_health(timeout: float = 15.0) -> bool:
    """等待知识库中台（api_gateway:8788）就绪，health 路径为 /health。"""
    import urllib.request
    url = f"http://127.0.0.1:{_GATEWAY_PORT}/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("status") == "ok":
                        return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def _pick_aux_runner() -> str:
    """选择辅助服务的 Python runner：优先根工作区 .venv（已装 RAG 依赖），否则用项目 .venv 或当前解释器。"""
    pythonw = PROJECT_DIR / ".venv" / "Scripts" / "pythonw.exe"
    if sys.platform == "win32" and _RAG_VENV_PYTHONW.exists():
        return str(_RAG_VENV_PYTHONW)
    if sys.platform == "win32" and pythonw.exists():
        return str(pythonw)
    if sys.platform != "win32" and _RAG_VENV_PYTHON_UNIX.exists():
        return str(_RAG_VENV_PYTHON_UNIX)
    return sys.executable


def _launch_aux_proc(script_path: Path, extra_args: list[str] | None = None) -> subprocess.Popen:
    """启动单个辅助服务子进程，返回 Popen 句柄（供 watchdog 接管）。"""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.Popen(
        [_pick_aux_runner(), str(script_path), *(extra_args or [])],
        cwd=str(script_path.parent), creationflags=flags,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=_build_aux_env(),
    )


def _spawn_aux_services():
    """幂等拉起知识库中台（api_gateway:8788）与异步任务 worker。

    脚本缺失 / 端口被占用 / 进程已存在时自动跳过，失败只记日志不影响主服务。
    由 start.py 统一托管，保证 bat / launcher / 命令行各入口行为一致。
    RAG 依赖（pypdfium2/chromadb/torch）在根工作区 .venv，优先用它作为 runner。
    进程句柄存入 _AUX_PROCS 供 watchdog 监控与自动重启。
    """
    if _GATEWAY_PATH.exists() and not _is_port_in_use(_GATEWAY_PORT) and not _is_cmdline_running("api_gateway.py"):
        try:
            _AUX_PROCS["gateway"] = _launch_aux_proc(_GATEWAY_PATH)
            logger.info("已启动知识库中台: %s (端口 %d, PID=%d)",
                        _GATEWAY_PATH.name, _GATEWAY_PORT, _AUX_PROCS["gateway"].pid)
            # 等待 8788 就绪（网关 health 路径为 /health，与主服务 /api/health 不同），
            # 把隐形崩溃变成显式日志
            if not _wait_for_gateway_health(timeout=15.0):
                logger.warning("知识库中台 %d 在 15s 内未就绪（/health 失败）", _GATEWAY_PORT)
        except Exception as e:
            logger.warning("启动知识库中台失败: %s", e)
    else:
        logger.debug("api_gateway 跳过（脚本缺失/端口占用/已运行）")

    if _WORKER_PATH.exists() and not _is_cmdline_running("task_worker.py"):
        try:
            _AUX_PROCS["worker"] = _launch_aux_proc(_WORKER_PATH, ["--poll", "2.0"])
            logger.info("已启动异步任务 worker: %s (PID=%d)",
                        _WORKER_PATH.name, _AUX_PROCS["worker"].pid)
        except Exception as e:
            logger.warning("启动 task_worker 失败: %s", e)
    else:
        logger.debug("task_worker 跳过（脚本缺失/已运行）")


def _detach_runner() -> str:
    """守护化自举所用的解释器：**必须用控制台版 python.exe**。

    实测结论（2026-09-10，本机 Windows 11）：venv 的 `Scripts\\pythonw.exe` 是
    启动器 stub，在 `DETACHED_PROCESS` 下会静默退出 —— 子进程连一行启动横幅都
    来不及写就没了，日志无任何痕迹。改用同目录的 python.exe 后，脱离式启动
    完全正常。

    不会因此弹出控制台窗口：`DETACHED_PROCESS` 本身就不为子进程创建控制台。
    若父进程是 pythonw，这里显式换成同目录的 python.exe。
    """
    exe = Path(sys.executable)
    if sys.platform == "win32" and exe.name.lower().startswith("pythonw"):
        console_exe = exe.with_name("python.exe")
        if console_exe.exists():
            return str(console_exe)
    return str(exe)


def _spawn_detached(port: int) -> bool:
    """以脱离父进程的方式在后台重新拉起自己，本进程立即返回（守护化）。

    为什么需要它：start.py 内建两个常驻 watchdog 线程（辅助服务守护、主服务自我
    健康守护），主进程必须长期存活。因此任何「非双击」入口若直接调用 start.py，
    调用方（shell / CI / 自动化）会一直阻塞等待，直到整个服务退出为止。

    实现要点（两条都不能少，否则调用方仍会挂起）：
    - 子进程用 DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP 启动，脱离父进程组，
      否则 shell 会等待整个进程组；
    - stdin/stdout/stderr 全部指向 DEVNULL。若子进程继承了调用方的管道句柄，
      调用方读取管道时会一直等不到 EOF。

    注意：DETACHED_PROCESS 只脱离「控制台/进程组」，**挡不住 Windows Job Object**。
    若调用方把本进程放进了作业对象（例如某些 IDE / 沙箱 / CI 的进程包装），作业
    对象被销毁时子进程会一并被终结。这是宿主环境的行为，脚本层无法规避。
    """
    lg = logging.getLogger("zenith.start")
    script = Path(__file__).resolve()
    # --detach 不传给子进程（否则无限自举），其余参数原样透传
    child_args = [a for a in sys.argv[1:] if a != "--detach"]
    cmd = [_detach_runner(), str(script), *child_args]

    # 标记「无控制台运行」，让子进程的日志走重定向到文件的分支
    child_env = os.environ.copy()
    child_env["ZENITH_DETACHED"] = "1"

    if sys.platform == "win32":
        popen_kwargs: dict = {
            "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        }
    else:
        popen_kwargs = {"start_new_session": True}

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_DIR),
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            **popen_kwargs,
        )
    except Exception as e:
        lg.exception("守护化启动失败: %s", e)
        print(f"[FAIL] Zenith 后台启动失败: {e}")
        return False

    print(f"[OK] Zenith 已在后台启动 (PID={proc.pid})")
    print(f"     地址: http://localhost:{port}")
    print(f"     日志: {_LOG_FILE}")
    print("     停止: python start.py --stop")
    return True


def _aux_services_watchdog(stop_event: threading.Event):
    """辅助服务 watchdog：定期检查 api_gateway / task_worker 存活，死了自动重启。

    指数退避（2/4/8/... 秒，最多 30s），单服务崩溃 > 3 次后停止重启（防崩溃循环）。
    主进程退出时通过 stop_event 通知 watchdog 线程结束。
    """
    restart_count = {"gateway": 0, "worker": 0}
    specs = [
        ("gateway", _GATEWAY_PATH, [], "知识库中台"),
        ("worker", _WORKER_PATH, ["--poll", "2.0"], "任务 worker"),
    ]
    while not stop_event.is_set():
        # 用 wait 代替 sleep，以便 stop_event.set() 后立即退出
        if stop_event.wait(timeout=30):
            break
        for key, script, extra_args, label in specs:
            proc = _AUX_PROCS.get(key)
            if proc is None:
                continue
            rc = proc.poll()
            if rc is None:
                continue  # 仍在运行
            # 进程已退出
            if restart_count[key] >= 3:
                logger.warning("%s 已崩溃 %d 次，停止自动重启（rc=%s）",
                               label, restart_count[key], rc)
                continue
            restart_count[key] += 1
            backoff = min(30, 2 ** restart_count[key])
            logger.warning("%s 已退出 (rc=%s)，%ds 后自动重启（第 %d 次）",
                           label, rc, backoff, restart_count[key])
            if stop_event.wait(timeout=backoff):
                break
            try:
                if not script.exists():
                    logger.warning("%s 脚本不存在，跳过重启", label)
                    continue
                # 重新拉起（不依赖 _is_port_in_use，避免被刚退出的端口 TIME_WAIT 影响判断）
                _AUX_PROCS[key] = _launch_aux_proc(script, extra_args)
                logger.info("%s 已自动重启 (PID=%d)", label, _AUX_PROCS[key].pid)
                # 重启成功后重置计数（让稳定后能容忍再次崩溃）
                if rc == 0:
                    restart_count[key] = 0
            except Exception as e:
                logger.warning("重启 %s 失败: %s", label, e)
    logger.debug("辅助服务 watchdog 已退出")

_SELF_WATCHDOG_INTERVAL = 20
_SELF_WATCHDOG_MAX_FAILS = 4


def _flush_pending_memories_sync(timeout: float = 10.0) -> bool:
    """在独立事件循环中同步等待「待提取记忆」落库（硬退出前的数据保全）。

    背景：self-watchdog 判定服务假死时用 os._exit(70) 硬退出，这会**跳过** main()
    的 finally 分支（其中含 flush_all_pending_memories()），导致对话 buffer 里最后
    1~2 轮的待提取记忆直接丢失。这里在退出前尽力补一次 flush。

    返回 True 表示 flush 正常完成；False 表示导入失败/异常/超时（均不抛出）。
    """
    lg = logging.getLogger("zenith.start")
    try:
        from backend.memory_engine import flush_all_pending_memories
    except Exception as e:
        lg.warning("退出前 flush 跳过（导入失败）: %s", e)
        return False

    try:
        asyncio.run(asyncio.wait_for(flush_all_pending_memories(), timeout=timeout))
        lg.info("退出前 flush 完成，待提取记忆已落库")
        return True
    except Exception as e:
        lg.warning("退出前 flush 未完成（%s）: %s", type(e).__name__, e)
        return False


# ---------------------------------------------------------------------------
# 退出标记（O-03 方案 A 的轻量补偿）
# ---------------------------------------------------------------------------
# 背景：self-watchdog 用 os._exit(70) 硬退出，宿主 Job Object 硬杀进程时更是连
# finally 都跑不到 —— 两种情况都会「无声消失」，只能靠用户发现页面打不开来察觉。
# 补偿做法：进程存活期间标记写 running；干净退出覆盖为 clean；自杀写 watchdog_suicide。
# 下次启动读到非 clean（尤其 running = 上次被硬杀）就显式告警，让「消失」变得可见。
_EXIT_REASON_RUNNING = "running"
_EXIT_REASON_CLEAN = "clean"
_EXIT_REASON_SUICIDE = "watchdog_suicide"
_EXIT_REASON_EXCEPTION = "exception"

# 本进程开始服务的时刻（main() 进入服务态时赋值），用于退出标记
_SERVICE_STARTED_AT = ""


def _write_exit_marker(reason: str, detail: str = "") -> None:
    """写/覆盖退出标记。失败只告警，不影响主流程。"""
    try:
        _EXIT_MARKER_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "reason": reason,
            "pid": os.getpid(),
            "started_at": _SERVICE_STARTED_AT,
            "written_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "detail": detail[:500],
        }
        _EXIT_MARKER_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logging.getLogger("zenith.start").warning("写退出标记失败: %s", e)


def _check_previous_exit() -> None:
    """启动自检：上次若未干净退出则显式告警（不阻断启动）。"""
    lg = logging.getLogger("zenith.start")
    if not _EXIT_MARKER_FILE.exists():
        return
    try:
        prev = json.loads(_EXIT_MARKER_FILE.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:
        lg.warning("读取上次退出标记失败（忽略）: %s", e)
        return

    reason = prev.get("reason", "unknown")
    started = prev.get("started_at", "?")
    written = prev.get("written_at", "?")
    pid = prev.get("pid", "?")

    if reason == _EXIT_REASON_CLEAN:
        return
    if reason == _EXIT_REASON_RUNNING:
        lg.warning(
            "上次运行（PID=%s，启动于 %s）**未记录退出** → 被硬杀或进程崩溃，非正常关闭。"
            "可能原因：宿主 Job Object 连坐、系统强制结束、断电。若数据异常请查该时段日志。",
            pid, started,
        )
    elif reason == _EXIT_REASON_SUICIDE:
        lg.warning(
            "上次运行被 self-watchdog 判定假死并强制退出（PID=%s，退出于 %s）。"
            "线程栈见 data/watchdog_stack_*.log，查清卡点后再重启。",
            pid, written,
        )
    elif reason == _EXIT_REASON_EXCEPTION:
        lg.warning("上次运行因异常退出（PID=%s，退出于 %s）：%s", pid, written, prev.get("detail", ""))
    else:
        lg.warning("上次退出原因未知（%s，PID=%s，退出于 %s）", reason, pid, written)


def _mark_intentional_stop() -> None:
    """由 `--stop` 代为落 clean 标记（进程被 taskkill /F 硬杀，自己写不了）。

    保留原标记里的 pid / started_at，便于日志里看出「停的是哪一次运行」。
    """
    prev: dict = {}
    try:
        if _EXIT_MARKER_FILE.exists():
            prev = json.loads(_EXIT_MARKER_FILE.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        prev = {}
    try:
        _EXIT_MARKER_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "reason": _EXIT_REASON_CLEAN,
            "pid": prev.get("pid"),
            "started_at": prev.get("started_at", ""),
            "written_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "detail": "由 start.py --stop 主动停止",
        }
        _EXIT_MARKER_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logging.getLogger("zenith.start").warning("写停止标记失败: %s", e)


def _self_health_watchdog(port: int, stop_event: threading.Event):
    """主服务自我健康守护：事件循环卡死/服务假死时，先 dump 全部线程栈，再强制退出。

    退出流程：dump 线程栈 → 尽力 flush 待提取记忆（10s 上限）→ os._exit(70)。

    ⚠️ 能力边界（2026-09-10 实测，**推翻**旧注释里的 GIL 论断）
    ------------------------------------------------------------------
    旧注释称「纯 Python 死循环不释放 GIL，同进程线程抢不到 GIL」—— **该说法是错的**。
    CPython 的 eval loop 每 sys.getswitchinterval()（本机 5ms）检查一次 eval breaker
    并释放 GIL，故纯 Python 自旋不会饿死本线程。实测 `tools/audit/g04_gil_probe.py`：
      · 纯 Python 自旋 → 本线程 tick 14/15，最大空档 221ms（正常调度）
      · C 层长循环独占 → tick 3/15，最大空档 1148ms ≈ 单次 sum(range(1e8)) 耗时（被饿死）

    故真正盲区是后者极端化：**单次不释放 GIL 的 C 层调用**（超大 numpy/pandas 运算、
    超大 JSON 序列化、re 灾难性回溯…）持续超过
    _SELF_WATCHDOG_INTERVAL × _SELF_WATCHDOG_MAX_FAILS 时，本线程同样被饿死。
    该场景只能靠独立进程探活 / 外部 supervisor；本文件用退出标记 + 下次启动自检补偿
    （见 `_write_exit_marker` / `_check_previous_exit`）。

    历史战绩：2026-08-25 20:35 / 20:40 / 20:53 三次成功触发，三份线程栈**完全同源**，
    均卡在 chat.py:_process_conv → tools.py:_handle_consolidate_memories →
    generate_consolidate_plan → memory_engine._similarity → _shared_text_vectors，
    即 consolidate 的 O(n²) 相似度比较**同步跑在事件循环线程里**。该根因已于同日修复
    （3-gram 倒排剪枝 + asyncio.to_thread），原始栈转储保留在 data/watchdog_stack_*.log。
    """
    import urllib.request
    logger = logging.getLogger("zenith.selfwatch")
    fails = 0
    while not stop_event.is_set():
        # 用 wait 代替 sleep：stop_event.set() 后立即退出（与 _aux_services_watchdog 同款写法）
        if stop_event.wait(_SELF_WATCHDOG_INTERVAL):
            break
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=10) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8", errors="replace"))
                    if data.get("status") == "ok":
                        fails = 0
                        continue
        except Exception:
            pass
        fails += 1
        logger.warning("主服务健康检查失败（连续 %d 次，阈值 %d）", fails, _SELF_WATCHDOG_MAX_FAILS)
        if fails >= _SELF_WATCHDOG_MAX_FAILS:
            dump_path = PROJECT_DIR / "data" / f"watchdog_stack_{time.strftime('%Y%m%d_%H%M%S')}.log"
            try:
                dump_path.parent.mkdir(parents=True, exist_ok=True)
                with open(dump_path, "a", encoding="utf-8", errors="replace") as f:
                    f.write(f"[self-watchdog] {fails} 次连续健康检查失败，服务疑似假死，即将强制退出。\n")
                    f.flush()
                    faulthandler.dump_traceback(file=f, all_threads=True)
                    f.flush()
                logger.error("主服务假死，线程栈已写入 %s，即将退出", dump_path)
            except Exception:
                logger.exception("写 watchdog 线程栈失败")
            # os._exit 不执行 finally，main() 里那段 flush_all_pending_memories()
            # 会被跳过 —— 退出前必须自己补一次，否则丢最后 1~2 轮对话记忆。
            logger.warning("self-watchdog: 退出前补一次记忆 flush（最多 10s）")
            if _flush_pending_memories_sync(timeout=10.0):
                logger.warning("self-watchdog: flush 已完成，即将强制退出")
            else:
                logger.warning("self-watchdog: flush 未完成，仍将强制退出")
            # 留痕：让下次启动能看出「上次是假死自杀」，而不是无声消失
            _write_exit_marker(_EXIT_REASON_SUICIDE, f"连续 {fails} 次健康检查失败")
            os._exit(70)



def _print_wait_result(port: int, timeout: float = 25.0):
    """阻塞等待健康检查并打印结果（供双击启动后的反馈窗口）。"""
    ok = _wait_for_health(port, timeout=timeout)
    if ok:
        print(f"[OK] Zenith 已就绪: http://localhost:{port}")
    else:
        print(f"[FAIL] Zenith 未在 {int(timeout)}s 内就绪")
        print(f"       请查看日志: {_LOG_FILE}")


def get_status(port: int = DEFAULT_PORT) -> dict:
    """获取 Zenith 运行状态。"""
    pid = _read_pid_file()
    return {
        "running": _is_port_in_use(port),
        "pid_file": pid,
        "pid_alive": _process_exists(pid) if pid else False,
        "lock_exists": _LOCK_FILE.exists(),
        "port": port,
        "url": f"http://localhost:{port}",
    }


def _print_status(status: dict):
    """打印状态信息到控制台（即使日志已重定向）。"""
    msg = (
        f"Zenith v2 状态:\n"
        f"  运行中: {'是' if status['running'] else '否'}\n"
        f"  服务地址: {status['url']}\n"
        f"  PID 文件: {status['pid_file']} ({'存活' if status['pid_alive'] else '未存活/不存在'})\n"
        f"  锁文件: {'存在' if status['lock_exists'] else '不存在'}"
    )
    # 绕过日志，确保在 --status 时用户能看到
    print(msg)


def _print_stop_summary(summary: dict):
    print(
        f"Zenith v2 已停止:\n"
        f"  PID 文件终止: {summary['pid_file'] or '无'}\n"
        f"  端口占用终止: {summary['by_port'] or '无'}\n"
        f"  进程名匹配终止: {summary['by_name'] or '无'}\n"
        f"  锁/文件清理: {'完成' if summary['lock_cleared'] else '失败'}\n"
        f"  错误: {summary['errors'] or '无'}"
    )


def _first_run_setup():
    """首次运行：从模板创建 config.yaml。"""
    config_file = PROJECT_DIR / "config" / "config.yaml"
    config_example = PROJECT_DIR / "config" / "config.yaml.example"
    if not config_file.exists() and config_example.exists():
        shutil.copy(config_example, config_file)
        logger.info("=" * 60)
        logger.info("  首次运行 - 已创建 config.yaml")
        logger.info("  请编辑 config.yaml 或 .env 添加 API Key")
        logger.info("  设置页面将在浏览器打开")
        logger.info("=" * 60)
        return True
    return False


# 注（2026-09-10，O-07）：这里原有一个 `_register_shutdown_handlers(lock_fd)`，
# 内部 `signal.signal(SIGTERM/SIGINT, _cleanup)` 注册了「释放 msvcrt 锁 + 关 fd」的清理。
# 但随后的 `uvicorn.run()` 会**安装自己的信号处理器并覆盖掉上面注册的**，
# 因此原 `_cleanup` 实际永不执行 —— 是死代码，且制造了「信号已被优雅处理」的错觉。
# 优雅退出的唯一真实路径是 main() 末尾的 `finally:` → `_cleanup_lock_and_pid()`；
# 即使进程被硬杀，msvcrt 文件锁也会随进程消亡由 OS 释放，不会残留。
# 回滚：`git checkout -- start.py` 或 restoredata/backup/start.py.bak-20260910-171500


def main():
    parser = argparse.ArgumentParser(description="Zenith v2 启动器")
    parser.add_argument("port", nargs="?", type=int, default=DEFAULT_PORT, help=f"服务端口（默认 {DEFAULT_PORT}）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--browser-delay", type=int, default=2, help="打开浏览器前的等待秒数（默认 2）")
    parser.add_argument("--detach", action="store_true",
                        help="守护化：脱离父进程在后台启动后立即返回（供 shell/脚本/自动化调用）")
    parser.add_argument("--verbose", action="store_true", help="输出 DEBUG 级别日志")
    parser.add_argument("--reset-lock", action="store_true", help="强制清除残留锁和 PID 文件后启动")
    parser.add_argument("--stop", action="store_true", help="停止当前运行的 Zenith 实例")
    parser.add_argument("--keep-aux", action="store_true",
                        help="配合 --stop：只停主服务，保留知识库中台(8788)与任务 worker")
    parser.add_argument("--status", action="store_true", help="查看 Zenith 运行状态")
    parser.add_argument("--wait", action="store_true", help="等待服务就绪后打印结果退出（供双击启动反馈）")
    parser.add_argument("--wait-timeout", type=float, default=25, help="--wait 超时秒数（默认 25）")
    parser.add_argument("--no-aux", action="store_true", help="不启动知识库中台与任务 worker")
    args = parser.parse_args()

    port = args.port
    url = f"http://localhost:{port}"

    # 日志必须在首件事设置，才能捕获后续异常
    _setup_logging(verbose=args.verbose)
    global logger
    logger = logging.getLogger("zenith.start")

    if args.status:
        _print_status(get_status(port))
        return

    if args.stop:
        summary = stop_existing_instance(port, keep_aux=args.keep_aux)
        _print_stop_summary(summary)
        return

    if args.wait:
        # 供 bat 双击后反馈：阻塞等待健康检查，打印结果后退出
        _print_wait_result(port, args.wait_timeout)
        return

    if args.detach:
        # 守护化：本进程只负责把真正的主进程甩到后台，随即返回。
        # 控制类命令（--stop/--status/--wait）在上面已提前返回，不受影响。
        _spawn_detached(port)
        return

    if args.reset_lock:
        _cleanup_lock_and_pid()
        logger.info("已强制清除锁和 PID 文件")

    # 单实例锁
    lock_fd, is_first_instance = _acquire_instance_lock(port)

    if not is_first_instance:
        if _is_port_in_use(port):
            # 端口确有监听 → 真在运行。是否开浏览器必须尊重 --no-browser，
            # 否则脚本/自动化入口会莫名弹出浏览器标签页。
            if args.no_browser:
                logger.info("Zenith 已在运行（--no-browser 已指定，跳过打开）: %s", url)
            elif _browser_recently_opened():
                logger.info("Zenith 已在运行，浏览器冷却期内，跳过打开")
            else:
                logger.info("Zenith 已在运行，打开浏览器: %s", url)
                _write_browser_ts()
                _open_browser(url)
            return
        # 锁没拿到但端口空闲 → 疑似僵尸锁 / 僵死进程占用锁，必须显式告警而非静默"已在运行"
        lock_exists = _LOCK_FILE.exists()
        pid = _read_pid_file()
        logger.error(
            "Zenith 启动失败：未能获取单实例锁，但端口 %d 未被监听。"
            "锁文件存在=%s，PID文件=%s（%s）。"
            "可能存在残留的僵死 start.py 进程占用了锁文件，导致新实例无法启动。"
            "请先运行 start.py --reset-lock 清理；如仍失败，请手动结束残留的 start.py / api_gateway.py / task_worker.py 进程后重试。",
            port, lock_exists, pid,
            ("存活" if pid and _process_exists(pid) else "无效/无"),
        )
        return

    # 启动前再次确认端口（防止锁刚释放但旧进程仍在收尾）
    if _is_port_in_use(port):
        owners = _find_processes_by_port(port)
        logger.info("端口 %d 仍被占用(PID=%s)，打开浏览器: %s", port, owners, url)
        _write_browser_ts()
        _open_browser(url)
        return

    _write_pid_file()

    # 上次是否干净退出？在读「本次 running 标记」之前先自检，否则会覆盖线索。
    _check_previous_exit()
    global _SERVICE_STARTED_AT
    _SERVICE_STARTED_AT = time.strftime("%Y-%m-%d %H:%M:%S")
    _write_exit_marker(_EXIT_REASON_RUNNING)

    # 首次运行配置
    # 注意：`_first_run_setup()` 的返回值（是否首次运行）当前**未被使用** ——
    # 这里只保留调用，因为它有**副作用**（首次运行时从模板创建 config.yaml）。
    # ⚠️ 疑似遗漏：该函数内日志写着「设置页面将在浏览器打开」，
    #    但全仓没有任何地方按这个返回值跳转设置页。若确实该跳，需补上；
    #    若只是文案残留，可忽略本注。见 _diag_archive 的 lint 记录。
    _first_run_setup()

    logger.info("=" * 60)
    logger.info("  Zenith v2 - Local AI Assistant")
    logger.info("=" * 60)
    logger.info("  Backend: %s", url)
    logger.info("  API Docs: %s/docs", url)
    logger.info("  Log: %s", _LOG_FILE)
    logger.info("  所有数据本地存储，不上传")
    logger.info("=" * 60)

    # 启动 uvicorn
    import uvicorn

    # 启动阶段只做**一次**健康探测，两个消费者（托管辅助服务 / 打开浏览器）共享结果。
    # 原实现让两个线程各跑一遍 _wait_for_health，启动瞬间会把 /api/health 打两份。
    health_ready = threading.Event()
    health_result = {"ok": False}

    def _probe_health_once():
        health_result["ok"] = _wait_for_health(port, timeout=_HEALTH_TIMEOUT_SECONDS)
        health_ready.set()

    threading.Thread(target=_probe_health_once, daemon=True, name="health-probe").start()

    # 知识库中台与任务 worker 托管 — 独立线程，与是否打开浏览器无关
    aux_stop_event = threading.Event()
    if not args.no_aux:
        def _aux_services_thread():
            health_ready.wait()
            if health_result["ok"]:
                _spawn_aux_services()
                # 启动 watchdog 监控辅助服务（崩溃自动重启）
                threading.Thread(
                    target=_aux_services_watchdog,
                    args=(aux_stop_event,),
                    daemon=True,
                    name="aux-watchdog",
                ).start()
            else:
                logger.warning("健康检查超时，跳过知识库中台与任务 worker 启动")
        threading.Thread(target=_aux_services_thread, daemon=True).start()

    # 主服务自我守护：事件循环卡死时 dump 线程栈并退出。
    # 硬退出后**无人自动拉起**（外部 supervisor 未落地），故用退出标记 + 下次启动自检
    # 补偿，让「上次怎么没的」可见。stop_event 用于让主进程优雅退出时能停掉它（O-03）。
    self_stop_event = threading.Event()
    threading.Thread(
        target=_self_health_watchdog,
        args=(port, self_stop_event),
        daemon=True,
        name="self-health-watchdog",
    ).start()


    # 注册启动后健康检查线程，服务就绪后再打开浏览器，避免打开无效标签页
    def _health_open_browser():
        delay = max(0, args.browser_delay)
        if delay > 0:
            time.sleep(delay)
        health_ready.wait()
        if health_result["ok"]:
            if not _browser_recently_opened():
                _write_browser_ts()
                _open_browser(url)
        else:
            logger.warning("健康检查超时，启动可能失败，请查看 %s", _LOG_FILE)

    if not args.no_browser:
        threading.Thread(target=_health_open_browser, daemon=True).start()

    try:
        uvicorn.run(
            "backend.app:app",
            host="127.0.0.1",
            port=port,
            reload=False,
            log_level="debug" if args.verbose else "info",
        )
    except Exception as e:
        logger.exception("Uvicorn 启动失败: %s", e)
        _write_exit_marker(_EXIT_REASON_EXCEPTION, f"uvicorn 启动失败: {e}")
        raise
    finally:
        # 通知 watchdog 退出
        aux_stop_event.set()
        self_stop_event.set()
        # 优雅关闭：flush 各对话 buffer 中的残余文本（最后 1~2 轮），避免数据丢失。
        # 在独立 event loop 中同步等待 LLM 提取完成（uvicorn 已退出但进程尚存）。
        # 加 15s 总超时，避免 LLM 慢/卡住时无限期阻塞退出。
        try:
            from backend.memory_engine import flush_all_pending_memories
            flush_loop = asyncio.new_event_loop()
            try:
                flush_loop.run_until_complete(
                    asyncio.wait_for(flush_all_pending_memories(), timeout=15.0)
                )
            finally:
                flush_loop.close()
        except Exception as e:
            logger.warning("flush pending memories 失败/超时: %s", e)
        _cleanup_lock_and_pid()
        # 最后一步才标 clean：任何中途异常/硬退出都不会留下这个标记
        _write_exit_marker(_EXIT_REASON_CLEAN)


if __name__ == "__main__":
    main()
