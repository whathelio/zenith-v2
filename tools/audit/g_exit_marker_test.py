"""退出标记自检逻辑的单元测试（O-03 方案 A 的轻量补偿）。

验证：
  · reason=clean      → 静默（不告警）
  · reason=running    → 告警「未记录退出 = 被硬杀/崩溃」
  · reason=watchdog_suicide → 告警「假死自杀」
  · reason=exception  → 告警「异常退出」
  · 标记文件不存在    → 静默

测试前后会还原 `data/last_exit.json` 的原始状态（不存在则删除），不污染真实运行。

用法：.venv/Scripts/python.exe tools/audit/g_exit_marker_test.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import start  # noqa: E402

MARKER: Path = start._EXIT_MARKER_FILE


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


def _probe(label: str, reason: str | None) -> None:
    """reason=None 表示「文件不存在」。"""
    if reason is None:
        MARKER.unlink(missing_ok=True)
    else:
        start._write_exit_marker(reason, detail="测试注入")

    logger = logging.getLogger("zenith.start")
    cap = _Capture()
    logger.addHandler(cap)
    try:
        start._check_previous_exit()
    finally:
        logger.removeHandler(cap)

    warned = [l for l in cap.lines if "WARNING" in l or "未记录退出" in l or "假死" in l or "异常退出" in l or "原因未知" in l]
    verdict = "⚠️ 告警" if warned else "✅ 静默"
    print(f"[{label:<24}] 文件={'不存在' if reason is None else reason:<18} → {verdict}")
    for l in cap.lines:
        print(f"      └─ {l[:150]}")


def main() -> int:
    original = MARKER.read_text(encoding="utf-8") if MARKER.exists() else None
    print(f"标记文件: {MARKER}")
    print("-" * 70)
    try:
        _probe("clean（干净退出）", "clean")
        _probe("running（被硬杀）", "running")
        _probe("watchdog_suicide", "watchdog_suicide")
        _probe("exception", "exception")
        _probe("文件不存在", None)
    finally:
        if original is None:
            MARKER.unlink(missing_ok=True)
            print("-" * 70)
            print("已还原：标记文件不存在（与测试前一致）")
        else:
            MARKER.write_text(original, encoding="utf-8")
            print("-" * 70)
            print("已还原：标记文件恢复为测试前内容")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
