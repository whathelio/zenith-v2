#!/usr/bin/env python3
"""CLI：导出一份本机进程 / 端口快照为 JSON。

采集逻辑在 `backend/process_monitor.py` —— 页面（`/processes`）与本 CLI
共用同一份实现，避免两份代码漂移。

用法（在 zenith-v2 根目录下执行）:
    .venv/Scripts/python.exe tools/process_map/collect.py
    .venv/Scripts/python.exe tools/process_map/collect.py --raw
    .venv/Scripts/python.exe tools/process_map/collect.py --out _snap.json --full

注意：这里用 importlib 按文件路径直接加载 `backend/process_monitor.py`，
不走 `backend` 包 —— 该模块是纯 stdlib，这样 CLI 不需要拉起 FastAPI / SQLite。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # = zenith-v2
MONITOR = ROOT / "backend" / "process_monitor.py"


def _load_monitor():
    spec = importlib.util.spec_from_file_location("zenith_process_monitor", MONITOR)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载采集模块: {MONITOR}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["zenith_process_monitor"] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser(description="导出本机进程 / 端口快照")
    ap.add_argument("--raw", action="store_true", help="关闭命令行脱敏（仅本机调试）")
    ap.add_argument("--out", default="", help="写入 JSON 文件路径")
    ap.add_argument("--full", action="store_true", help="stdout 输出完整 JSON")
    args = ap.parse_args()

    pm = _load_monitor()
    data = pm.snapshot(force=True, raw=args.raw)

    if args.out:
        Path(args.out).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入 {args.out}")

    payload = data if args.full else data["summary"]
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
