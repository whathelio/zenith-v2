"""Zenith v2 Python 代码运行器 — 双路径隔离执行

执行路径（按优先级）：
1. Docker 容器（若可用）— 真隔离：--read-only --network=none --memory --cpus --cap-drop=ALL
2. 加固子进程（降级）— resource.setrlimit + 模块黑名单 + 清空危险 env
3. 禁用 — 由调用方 config.is_code_execution_enabled() 把关，返回 403

⚠️ 即使 Docker 路径，也仅限本地单用户。多用户/公网部署参阅 SECURITY.md。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path

TEMP = Path(tempfile.gettempdir()) / "zenith_code"
logger = logging.getLogger("zenith.code_runner")
DOCKER_IMAGE = os.environ.get("ZENITH_SANDBOX_IMAGE", "python:3.13-slim")
DOCKER_MEM = os.environ.get("ZENITH_SANDBOX_MEM", "256m")
DOCKER_CPUS = os.environ.get("ZENITH_SANDBOX_CPUS", "0.5")
DOCKER_TIMEOUT_DEFAULT = 30

# Phase 2 加固参数
HARDENED_MEM_MB = 256
HARDENED_CPU_SEC = 10
MAX_OUTPUT_LEN = 5000

# 危险模块黑名单（静态扫描，可被绕过，仅提高门槛）
_DANGEROUS_PATTERNS = [
    (r"\bsubprocess\b", "subprocess（可执行任意系统命令）"),
    (r"\bos\.system\b", "os.system（可执行任意系统命令）"),
    (r"\bos\.popen\b", "os.popen（可执行任意系统命令）"),
    (r"\bsocket\b", "socket（可发起网络连接，外带数据）"),
    (r"\bctypes\b", "ctypes（可调用任意系统 API）"),
    (r"\b__import__\s*\(", "__import__（动态导入可绕过黑名单）"),
    (r"\beval\s*\(", "eval（可执行任意代码字符串）"),
    (r"\bexec\s*\(", "exec（可执行任意代码字符串）"),
    (r"\bopen\s*\(\s*['\"][^'\"]*config\.yaml", "读取 config.yaml（含 API Key）"),
    (r"\bopen\s*\(\s*['\"][^'\"]*\.env", "读取 .env（含密钥）"),
]

# 危险环境变量前缀（不继承给子进程）
_DANGEROUS_ENV_PREFIXES = ("ZENITH_", "LLM_", "API_", "TOKEN", "SECRET", "KEY", "PASSWORD")


# ────────────────────────────────────────────────────────────
# 可复用：wrapper 构建 + 输出解析
# ────────────────────────────────────────────────────────────

def _build_wrapper(code: str) -> str:
    """构建 wrapper 脚本：重定向 stdout/stderr + try/except + 分隔符标记。"""
    wrapper_lines = [
        "import sys, io",
        "_s, _e = io.StringIO(), io.StringIO()",
        "sys.stdout, sys.stderr = _s, _e",
        "try:",
    ]
    for ln in code.split("\n"):
        wrapper_lines.append(f"    {ln}")
    wrapper_lines.extend([
        "except Exception:",
        "    import traceback",
        "    traceback.print_exc(file=sys.stderr)",
        "sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__",
        'print("__STDOUT__")',
        "print(_s.getvalue(), end='')",
        'print("__STDERR__")',
        "print(_e.getvalue(), end='')",
    ])
    return "\n".join(wrapper_lines)


def _parse_output(stdout_text: str, stderr_text: str = "") -> dict:
    """解析带 __STDOUT__/__STDERR__ 分隔符的输出。返回 {"success", "output"}。

    success 由调用方根据 returncode 设置；这里只负责 output 文本。
    """
    if "__STDOUT__" in stdout_text:
        _, _, rest = stdout_text.partition("__STDOUT__")
        if "__STDERR__" in rest:
            out, _, err = rest.partition("__STDERR__")
        else:
            out, err = rest, ""
        final = out.strip()
        if err.strip():
            final += f"\n\n[stderr]\n{err.strip()}"
    else:
        final = stdout_text.strip()
        if stderr_text:
            final += f"\n\n[stderr]\n{stderr_text}"

    if len(final) > MAX_OUTPUT_LEN:
        final = final[:MAX_OUTPUT_LEN] + "\n\n... (输出被截断)"

    return {"success": True, "output": final or "(无输出)"}


def _static_safety_check(code: str) -> str | None:
    """静态扫描危险模式。返回拒绝原因，None 表示通过。

    注意：可被绕过（如字符串拼接 import），仅作为第一道门槛。
    """
    for pattern, reason in _DANGEROUS_PATTERNS:
        if re.search(pattern, code):
            return f"代码包含危险模式：{reason}"
    return None


def _clean_env() -> dict:
    """返回清空危险变量的环境字典。"""
    return {
        k: v for k, v in os.environ.items()
        if not any(k.startswith(p) for p in _DANGEROUS_ENV_PREFIXES)
    }


# ────────────────────────────────────────────────────────────
# 路径 1：Docker 容器真隔离
# ────────────────────────────────────────────────────────────

async def _run_in_docker(code: str, timeout: int = 30) -> dict:
    """在 Docker 容器中执行代码。真隔离：只读根文件系统 + 无网络 + 内存/CPU 配额。"""
    TEMP.mkdir(parents=True, exist_ok=True)
    script = TEMP / f"exec_{int(time.time() * 1000)}.py"
    script.write_text(_build_wrapper(code), encoding="utf-8")

    # Windows 路径需要转换为 Docker 接受的格式
    script_path_host = str(script)
    if sys.platform == "win32":
        # O:\foo\bar.py -> /o/foo/bar.py
        drive = script_path_host[0].lower()
        rest = script_path_host[2:].replace("\\", "/")
        script_path_container = f"/{drive}{rest}"
    else:
        script_path_container = script_path_host

    docker_cmd = [
        "docker", "run", "--rm",
        "--read-only",
        "--network=none",
        f"--memory={DOCKER_MEM}",
        f"--cpus={DOCKER_CPUS}",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--tmpfs=/tmp:rw,size=64m",
        "-v", f"{script_path_host}:/code/script.py:ro",
        DOCKER_IMAGE,
        "python", "/code/script.py",
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *docker_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_clean_env(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout + 5  # 额外 5s 给 Docker 启动
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"success": False, "output": f"⏱ 执行超时 ({timeout}s)"}

        out_text = stdout.decode("utf-8", errors="replace")
        err_text = stderr.decode("utf-8", errors="replace")

        # Docker 启动失败（如镜像未拉取）
        if proc.returncode == 125 and "Unable to find image" in err_text:
            return {
                "success": False,
                "output": f"❌ Docker 镜像未拉取：{DOCKER_IMAGE}\n请运行 docker pull {DOCKER_IMAGE}",
            }

        result = _parse_output(out_text, err_text)
        result["success"] = proc.returncode == 0
        if not result["success"] and not result["output"].strip():
            result["output"] = f"❌ 容器执行失败 (exit {proc.returncode})\n[stderr]\n{err_text[:1000]}"
        return result

    except FileNotFoundError:
        return {
            "success": False,
            "output": "❌ Docker 未安装或不在 PATH。请安装 Docker Desktop 或降级使用子进程模式。",
        }
    except Exception as e:
        return {"success": False, "output": f"❌ Docker 执行错误: {str(e)}"}
    finally:
        try:
            script.unlink(missing_ok=True)
        except Exception:
            pass


# ────────────────────────────────────────────────────────────
# 路径 2：加固子进程（降级）
# ────────────────────────────────────────────────────────────

def _apply_resource_limits():
    """Unix 下设置子进程资源限制。在 preexec_fn 中调用。"""
    try:
        import resource
        # 内存上限（RLIMIT_AS 在 Linux 上生效，macOS 部分生效）
        mem_bytes = HARDENED_MEM_MB * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
        # CPU 时间上限（秒）
        resource.setrlimit(resource.RLIMIT_CPU, (HARDENED_CPU_SEC, HARDENED_CPU_SEC))
        # 文件大小上限（防磁盘填满）：10MB
        resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))
    except (ImportError, ValueError, OSError):
        # Windows 无 resource 模块或限制不可用，跳过
        pass


async def _run_subprocess_hardened(code: str, timeout: int = 30) -> dict:
    """加固子进程执行：资源限制 + 黑名单 + 清空 env。

    ⚠️ 黑名单可被绕过，资源限制在 Windows 不生效。仅作为 Docker 不可用时的降级。
    """
    # 静态安全检查
    reject_reason = _static_safety_check(code)
    if reject_reason:
        return {"success": False, "output": f"🚫 代码被安全检查拒绝：{reject_reason}\n（降级模式下，危险模块被禁止。安装 Docker 可获得真隔离。）"}

    TEMP.mkdir(parents=True, exist_ok=True)
    script = TEMP / f"exec_{int(time.time() * 1000)}.py"
    script.write_text(_build_wrapper(code), encoding="utf-8")
    script_path = str(script.resolve())
    logger.info("subprocess exec: %s (size=%d, path=%s, exists=%s)",
                script.name, script.stat().st_size, script_path, script.exists())

    try:
        kwargs = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        # Unix 才能用 preexec_fn 设置资源限制
        if sys.platform != "win32":
            kwargs["preexec_fn"] = _apply_resource_limits
            kwargs["env"] = _clean_env()

        proc = await asyncio.create_subprocess_exec(
            sys.executable, script_path,
            **kwargs,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"success": False, "output": f"⏱ 执行超时 ({timeout}s)"}

        out_text = stdout.decode("utf-8", errors="replace")
        err_text = stderr.decode("utf-8", errors="replace")
        logger.info("subprocess done: rc=%d stdout=%d bytes stderr=%d bytes",
                     proc.returncode, len(out_text), len(err_text))
        if proc.returncode != 0:
            logger.warning("subprocess non-zero exit: rc=%d stderr=%s",
                           proc.returncode, err_text[:300])

        result = _parse_output(out_text, err_text)
        result["success"] = proc.returncode == 0
        return result

    except Exception as e:
        return {"success": False, "output": f"❌ 执行错误: {str(e)}"}
    finally:
        try:
            script.unlink(missing_ok=True)
        except Exception:
            pass


# ────────────────────────────────────────────────────────────
# 主入口：双路径分派
# ────────────────────────────────────────────────────────────

async def run(code: str, timeout: int = 30) -> dict:
    """
    执行 Python 代码。自动检测 Docker 可用性，优先用容器隔离。

    调用前必须已检查 config.is_code_execution_enabled()。
    Returns: {"success": bool, "output": str}
    """
    from .config import docker_available

    if docker_available():
        return await _run_in_docker(code, timeout)
    return await _run_subprocess_hardened(code, timeout)
