"""Zenith v2 — Main Entry Point

启动 FastAPI 后端并打开浏览器。提供单实例控制、进程清理、启动诊断。

CLI:
    python start.py [PORT] [--no-browser] [--browser-delay N] [--verbose]
    python start.py --stop
    python start.py --status
    python start.py --reset-lock
"""

from __future__ import annotations

import sys
import os

# pythonw.exe 无控制台，必须在最早阶段捕获 stdout/stderr，否则导入期异常会静默丢失。
if sys.executable and sys.executable.lower().endswith("pythonw.exe"):
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
import signal
import webbrowser
import threading
import socket
import tempfile
import subprocess
import shutil
from pathlib import Path
from typing import Optional, Tuple

PROJECT_DIR = Path(__file__).parent.resolve()
_LOG_FILE = PROJECT_DIR / "zenith.log"
_LOCK_FILE = Path(tempfile.gettempdir()) / "zenith_v2.lock"
_PID_FILE = Path(tempfile.gettempdir()) / "zenith_v2.pid"
_BROWSER_TS_FILE = PROJECT_DIR / ".zenith.browser"
_BROWSER_COOLDOWN_SECONDS = 5
_HEALTH_TIMEOUT_SECONDS = 20

# 知识库中台与异步任务 worker（launcher 旧版直接拉起，此处统一托管，避免多入口不一致）
_GATEWAY_PATH = PROJECT_DIR.parent / "api_gateway.py"
_WORKER_PATH = PROJECT_DIR.parent / "task_worker.py"
_GATEWAY_PORT = 8788

DEFAULT_PORT = 8766


def _is_running_under_pythonw() -> bool:
    """检测是否在 pythonw.exe 下运行（无控制台，stdout/stderr 不可用）。"""
    return Path(sys.executable).name.lower().startswith("pythonw")


def _setup_logging(verbose: bool = False):
    """配置日志。pythonw 下将 stdout/stderr 重定向到日志文件，避免崩溃无声。"""
    level = logging.DEBUG if verbose else logging.INFO
    handlers: list[logging.Handler] = [logging.FileHandler(str(_LOG_FILE), encoding="utf-8", mode="a")]

    # pythonw 没有控制台；普通 python 仍可在终端看到输出
    if not _is_running_under_pythonw():
        handlers.append(logging.StreamHandler())
    else:
        # 捕获 print 与未处理异常，写入同一日志文件
        try:
            log_stream = open(str(_LOG_FILE), "a", encoding="utf-8", errors="replace")
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
            handle = kernel.OpenProcess(1, False, pid)  # PROCESS_TERMINATE=1，查询也够用
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
    """判断当前锁是否为僵尸：端口空闲 或 PID 文件指向的进程已不存在。"""
    pid = _read_pid_file()
    port_free = not _is_port_in_use(port)
    pid_dead = pid is not None and not _process_exists(pid)
    # 端口空闲即大概率是僵尸；PID 死亡也确认是僵尸
    return port_free or pid_dead


def _cleanup_lock_and_pid():
    """清理锁文件和 PID 文件。"""
    try:
        _LOCK_FILE.unlink(missing_ok=True)
    except Exception as e:
        logger.debug("清理锁文件失败: %s", e)
    try:
        _PID_FILE.unlink(missing_ok=True)
    except Exception as e:
        logger.debug("清理 PID 文件失败: %s", e)


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
                if f":{port}" in line and ("LISTENING" in line or "ESTABLISHED" in line):
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
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False, timeout=5)
            return True
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


def stop_existing_instance(port: int = DEFAULT_PORT) -> dict:
    """停止已运行的 Zenith 实例（含知识库中台与任务 worker），返回清理摘要。"""
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
    for p in _find_processes_by_port(port):
        if _kill_process(p):
            summary["by_port"].append(p)
        else:
            summary["errors"].append(f"端口占用 PID={p} 终止失败")
    for p in _find_processes_by_port(_GATEWAY_PORT):
        if _kill_process(p):
            summary["by_port"].append(p)
        else:
            summary["errors"].append(f"中台端口占用 PID={p} 终止失败")

    # 3. 命令行匹配兜底（start.py / api_gateway.py / task_worker.py）
    for p in _find_zenith_processes():
        if _kill_process(p):
            summary["by_name"].append(p)
    for p in _find_zenith_processes(marker="api_gateway.py"):
        if _kill_process(p):
            summary["by_name"].append(p)
    for p in _find_zenith_processes(marker="task_worker.py"):
        if _kill_process(p):
            summary["by_name"].append(p)

    _cleanup_lock_and_pid()
    summary["lock_cleared"] = True
    return summary


def _is_cmdline_running(keyword: str) -> bool:
    """检查是否有 python/pythonw 进程的命令行包含关键字（幂等判断）。

    注意：查询必须限定 Name='python*'，否则 powershell.exe 自身的命令行
    （含关键字）会被误匹配，导致永远判定为"已在运行"。
    """
    if sys.platform != "win32":
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


def _spawn_aux_services():
    """幂等拉起知识库中台（api_gateway:8788）与异步任务 worker。

    脚本缺失 / 端口被占用 / 进程已存在时自动跳过，失败只记日志不影响主服务。
    由 start.py 统一托管，保证 bat / launcher / 命令行各入口行为一致。
    """
    pythonw = PROJECT_DIR / ".venv" / "Scripts" / "pythonw.exe"
    runner = str(pythonw) if (sys.platform == "win32" and pythonw.exists()) else sys.executable
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    # 必须重定向子进程输出：否则子进程持有父进程 stdout/stderr 管道，
    # 在 bash/cmd 场景会导致父命令无法结束
    devnull = subprocess.DEVNULL

    if _GATEWAY_PATH.exists() and not _is_port_in_use(_GATEWAY_PORT) and not _is_cmdline_running("api_gateway.py"):
        try:
            subprocess.Popen(
                [runner, str(_GATEWAY_PATH)],
                cwd=str(_GATEWAY_PATH.parent), creationflags=flags,
                stdout=devnull, stderr=devnull,
            )
            logger.info("已启动知识库中台: %s (端口 %d)", _GATEWAY_PATH.name, _GATEWAY_PORT)
        except Exception as e:
            logger.warning("启动知识库中台失败: %s", e)
    else:
        logger.debug("api_gateway 跳过（脚本缺失/端口占用/已运行）")

    if _WORKER_PATH.exists() and not _is_cmdline_running("task_worker.py"):
        try:
            subprocess.Popen(
                [runner, str(_WORKER_PATH), "--poll", "2.0"],
                cwd=str(_WORKER_PATH.parent), creationflags=flags,
                stdout=devnull, stderr=devnull,
            )
            logger.info("已启动异步任务 worker: %s", _WORKER_PATH.name)
        except Exception as e:
            logger.warning("启动 task_worker 失败: %s", e)
    else:
        logger.debug("task_worker 跳过（脚本缺失/已运行）")


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


def _register_shutdown_handlers(lock_fd: Optional[int]):
    """注册信号处理程序，保证退出时释放锁和 PID 文件。"""
    def _cleanup(signum=None, frame=None):
        logger.info("正在关闭 Zenith v2...")
        try:
            if lock_fd is not None:
                try:
                    if sys.platform == "win32":
                        import msvcrt
                        msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
                except Exception:
                    pass
                try:
                    os.close(lock_fd)
                except Exception:
                    pass
        finally:
            _cleanup_lock_and_pid()
        if signum is not None:
            sys.exit(0)

    if sys.platform == "win32":
        try:
            signal.signal(signal.SIGTERM, _cleanup)
            signal.signal(signal.SIGINT, _cleanup)
        except Exception:
            pass
    else:
        signal.signal(signal.SIGTERM, _cleanup)
        signal.signal(signal.SIGINT, _cleanup)


def main():
    parser = argparse.ArgumentParser(description="Zenith v2 启动器")
    parser.add_argument("port", nargs="?", type=int, default=DEFAULT_PORT, help=f"服务端口（默认 {DEFAULT_PORT}）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--browser-delay", type=int, default=2, help="打开浏览器前的等待秒数（默认 2）")
    parser.add_argument("--verbose", action="store_true", help="输出 DEBUG 级别日志")
    parser.add_argument("--reset-lock", action="store_true", help="强制清除残留锁和 PID 文件后启动")
    parser.add_argument("--stop", action="store_true", help="停止当前运行的 Zenith 实例")
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
        summary = stop_existing_instance(port)
        _print_stop_summary(summary)
        return

    if args.wait:
        # 供 bat 双击后反馈：阻塞等待健康检查，打印结果后退出
        _print_wait_result(port, args.wait_timeout)
        return

    if args.reset_lock:
        _cleanup_lock_and_pid()
        logger.info("已强制清除锁和 PID 文件")

    # 单实例锁
    lock_fd, is_first_instance = _acquire_instance_lock(port)

    if not is_first_instance:
        if _browser_recently_opened():
            logger.info("Zenith 已在运行，浏览器冷却期内，跳过打开")
        else:
            logger.info("Zenith 已在运行，打开浏览器: %s", url)
            _write_browser_ts()
            webbrowser.open(url)
        return

    # 启动前再次确认端口（防止锁刚释放但旧进程仍在收尾）
    if _is_port_in_use(port):
        owners = _find_processes_by_port(port)
        logger.info("端口 %d 仍被占用(PID=%s)，打开浏览器: %s", port, owners, url)
        _write_browser_ts()
        webbrowser.open(url)
        return

    _write_pid_file()
    _register_shutdown_handlers(lock_fd)

    # 首次运行配置
    is_first_run = _first_run_setup()

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

    # 知识库中台与任务 worker 托管 — 独立线程，与是否打开浏览器无关
    if not args.no_aux:
        def _aux_services_thread():
            if _wait_for_health(port, timeout=_HEALTH_TIMEOUT_SECONDS):
                _spawn_aux_services()
            else:
                logger.warning("健康检查超时，跳过知识库中台与任务 worker 启动")
        threading.Thread(target=_aux_services_thread, daemon=True).start()

    # 注册启动后健康检查线程，服务就绪后再打开浏览器，避免打开无效标签页
    def _health_open_browser():
        delay = max(0, args.browser_delay)
        if delay > 0:
            time.sleep(delay)
        if _wait_for_health(port, timeout=_HEALTH_TIMEOUT_SECONDS):
            if not _browser_recently_opened():
                _write_browser_ts()
                try:
                    webbrowser.open(url)
                    logger.info("健康检查通过，浏览器已打开: %s", url)
                except Exception as e:
                    logger.warning("打开浏览器失败: %s", e)
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
        raise
    finally:
        _cleanup_lock_and_pid()


if __name__ == "__main__":
    main()
