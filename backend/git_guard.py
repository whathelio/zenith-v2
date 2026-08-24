"""Zenith 代码版本守护 — git 封装：改前快照 / 回退 / 快照列表。

设计原则（2026-08-20 治理评审）：
- 快照失败**不阻断**业务（.bak 兜底），仅 warn
- rollback 用 `git checkout <hash> -- backend/` + commit，**禁 reset --hard**
  （HEAD 可再回退、data/ 与 frontend 不受影响、不毁后续提交）
- ZENITH_TESTING=1 时全部短路（测试不碰真实仓库）
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

logger = logging.getLogger("zenith.git_guard")

# 项目根 = 本文件上级的上级（zenith-v2/）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 回退保护范围：只允许回退 backend/ 代码，data/ 与 frontend/ 永不触碰
ROLLBACK_SCOPE = ("backend/",)

_HASH_RE = re.compile(r"^[0-9a-f]{7,40}$")

# 提交身份兜底（不改全局配置，仅单次 commit 内联）
_FALLBACK_NAME = "Zenith"
_FALLBACK_EMAIL = "zenith@local"


def _is_testing() -> bool:
    return os.environ.get("ZENITH_TESTING") == "1"


def _run_git(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess | None:
    """执行 git 命令（在项目根目录）。失败返回 None，不抛异常。"""
    if _is_testing():
        return None
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError:
        logger.warning("git 命令不可用（未安装或不在 PATH）")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("git 命令超时: %s", " ".join(args[:3]))
        return None
    except Exception as e:
        logger.warning("git 命令异常: %s", e)
        return None


def _is_git_repo() -> bool:
    if _is_testing():
        return False
    r = _run_git(["rev-parse", "--is-inside-work-tree"])
    return bool(r and r.returncode == 0 and r.stdout.strip() == "true")


def _commit(reason: str, paths: list[str] | None) -> bool:
    """git add + commit（带身份兜底）。返回是否产生提交。"""
    if paths:
        r = _run_git(["add", "--", *paths])
    else:
        r = _run_git(["add", "-A"])
    if not r or r.returncode != 0:
        logger.warning("git add 失败: %s", (r.stderr if r else "no result").strip()[:200])
        return False

    # 无改动则不产生空提交
    r = _run_git(["diff", "--cached", "--quiet"])
    if r and r.returncode == 0:
        return False  # 没有暂存改动

    msg = f"{reason} [{_now_str()}]"
    r = _run_git([
        "-c", f"user.name={_FALLBACK_NAME}",
        "-c", f"user.email={_FALLBACK_EMAIL}",
        "commit", "-m", msg,
    ])
    if not r or r.returncode != 0:
        logger.warning("git commit 失败: %s", (r.stderr if r else "no result").strip()[:200])
        return False
    logger.info("代码快照已创建: %s", msg)
    return True


def _now_str() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_git_clean_snapshot(reason: str, paths: list[str] | None = None) -> dict:
    """改代码前自动快照。返回 {snapshot_created: bool, error: str}。

    paths 给定时只 add 指定文件（edit_file 聚焦单文件，避免收纳无关改动）；
    不给定时 add -A（create_snapshot 手动快照用）。
    失败降级：返回 snapshot_created=False，调用方继续（.bak 兜底）。
    """
    if _is_testing():
        return {"snapshot_created": False, "error": "testing-mode-skip"}
    if not _is_git_repo():
        return {"snapshot_created": False, "error": "not-a-git-repo"}
    ok = _commit(reason, paths)
    return {"snapshot_created": ok, "error": "" if ok else "git snapshot failed"}


def rollback_to_commit(commit_hash: str) -> dict:
    """回退 backend/ 到指定 commit（HEAD 前移，可再回退）。

    流程：校验 hash → 先快照当前未提交改动（可逆）→ checkout hash -- backend/
    → commit "rollback: <hash>"。
    局限：不删除 hash 之后**新建**的 backend 文件（安全优先，v1 不做 git clean）。
    """
    if _is_testing():
        return {"success": False, "error": "testing-mode-skip", "restart_required": True}
    if not _is_git_repo():
        return {"success": False, "error": "not-a-git-repo", "restart_required": True}
    commit_hash = (commit_hash or "").strip()
    if not _HASH_RE.match(commit_hash):
        return {"success": False, "error": f"非法 commit hash: {commit_hash}", "restart_required": True}

    # 1. 先快照未提交改动（保证 rollback 前的工作不丢）
    _commit(f"pre-rollback {commit_hash}", None)

    # 2. checkout 指定 commit 的 backend/ 目录
    r = _run_git(["checkout", commit_hash, "--", *ROLLBACK_SCOPE])
    if not r or r.returncode != 0:
        return {"success": False, "error": (r.stderr if r else "checkout failed").strip()[:200],
                "restart_required": True}

    # 3. 提交回退结果
    _commit(f"rollback: {commit_hash}", None)
    return {"success": True, "hash": commit_hash, "restart_required": True,
            "note": "backend/ 已回退；hash 后新建的 backend 文件保留未删；重启后生效"}


def list_snapshots(limit: int = 20) -> list[dict]:
    """列出最近快照（git log 解析）。"""
    if _is_testing():
        return []
    if not _is_git_repo():
        return []
    limit = max(1, min(int(limit or 20), 100))
    r = _run_git(["log", "--oneline", f"-{limit}"])
    if not r or r.returncode != 0:
        return []
    snapshots = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(" ", 1)
        if len(parts) < 2:
            continue
        snapshots.append({"hash": parts[0], "summary": parts[1]})
    return snapshots
