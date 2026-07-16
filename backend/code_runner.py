"""Zenith v2 Python 代码沙箱 — subprocess + tempfile + timeout"""
from __future__ import annotations

import asyncio
import sys
import time
import traceback
from pathlib import Path

TEMP = Path(__file__).parent.parent / "data" / "code_temp"


async def run(code: str, timeout: int = 30) -> dict:
    """
    在隔离的 subprocess 中安全执行 Python 代码。
    Returns: {"success": bool, "output": str}
    """
    TEMP.mkdir(parents=True, exist_ok=True)
    script = TEMP / f"exec_{int(time.time() * 1000)}.py"

    # 包装代码，重定向 stdout/stderr
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
    script.write_text("\n".join(wrapper_lines), encoding="utf-8")

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(script),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"success": False, "output": f"⏱ 执行超时 ({timeout}s)"}

        output = stdout.decode("utf-8", errors="replace")

        # 解析分隔符
        if "__STDOUT__" in output:
            _, _, rest = output.partition("__STDOUT__")
            if "__STDERR__" in rest:
                out, _, err = rest.partition("__STDERR__")
            else:
                out, err = rest, ""
            final = out.strip()
            if err.strip():
                final += f"\n\n[stderr]\n{err.strip()}"
        else:
            final = output.strip()
            if stderr:
                final += f"\n\n[stderr]\n{stderr.decode('utf-8', 'replace')}"

        # 截断过长输出
        max_len = 5000
        if len(final) > max_len:
            final = final[:max_len] + "\n\n... (输出被截断)"

        return {
            "success": proc.returncode == 0,
            "output": final or "(无输出)"
        }

    except Exception as e:
        return {"success": False, "output": f"❌ 执行错误: {str(e)}"}
    finally:
        try:
            script.unlink(missing_ok=True)
        except Exception:
            try:
                script.unlink()
            except Exception:
                pass
