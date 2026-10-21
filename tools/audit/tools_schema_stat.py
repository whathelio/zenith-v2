#!/usr/bin/env python3
"""TOOLS_SCHEMA 描述长度统计（P1-C2 基线 / 回归门禁）。

用法::

    python tools/audit/tools_schema_stat.py                 # 全量表格 + 汇总
    python tools/audit/tools_schema_stat.py --limit 120     # 只列超限项
    python tools/audit/tools_schema_stat.py --json out.json # 落 JSON（供前后对比）
    python tools/audit/tools_schema_stat.py --limit 120 --strict   # 有超限 → exit 1

设计要点：

* **AST 静态解析**，不 import `backend.tools` —— 后者会拉起 aiohttp / services
  等重依赖，且需完整运行环境；统计只需要这一份字面量。
* 过滤 `_DISABLED_TOOLS`（与运行时 `tools.py:996` 的过滤保持一致），
  否则会把已禁用的 `query_wiki` 也算进常驻规模。
* `total = 顶层 description + 各参数 description`，与常驻 payload 中该工具
  贡献的字节量同阶（JSON 骨架另有少量开销，此处不计）。
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS_PY = ROOT / "backend" / "tools.py"


def _literal_assigns(src: str) -> dict:
    """收集模块顶层所有可静态求值的 `NAME = <字面量>`（后写的成功赋值覆盖先前的）。"""
    out: dict = {}
    for node in ast.parse(src).body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
        for tgt in targets:
            try:
                out[tgt.id] = ast.literal_eval(node.value)
            except (ValueError, TypeError, SyntaxError):
                continue  # 非字面量（如列表推导）——跳过，保留此前成功的值
    return out


def _param_desc_len(params: dict) -> tuple[int, int]:
    """返回 (参数说明总字符数, 参数个数)；支持一层嵌套 object。"""
    props = (params or {}).get("properties") or {}
    total = 0
    for spec in props.values():
        if not isinstance(spec, dict):
            continue
        total += len(str(spec.get("description") or ""))
        nested = spec.get("properties")  # 一层嵌套（如 retrieve_docs 的 collection 除外）
        if isinstance(nested, dict):
            for sub in nested.values():
                if isinstance(sub, dict):
                    total += len(str(sub.get("description") or ""))
    return total, len(props)


def collect() -> list[dict]:
    src = TOOLS_PY.read_text(encoding="utf-8")
    assign = _literal_assigns(src)
    raw = assign.get("TOOLS_SCHEMA")
    if not isinstance(raw, list):
        raise SystemExit(f"无法从 {TOOLS_PY} 静态解析 TOOLS_SCHEMA")
    disabled = assign.get("_DISABLED_TOOLS") or set()

    rows = []
    for tool in raw:
        fn = (tool or {}).get("function") or {}
        name = fn.get("name")
        if not name or name in disabled:
            continue
        top = len(str(fn.get("description") or ""))
        pdesc, nparams = _param_desc_len(fn.get("parameters") or {})
        rows.append({
            "name": name,
            "top": top,
            "param_desc": pdesc,
            "total": top + pdesc,
            "n_params": nparams,
        })
    rows.sort(key=lambda r: r["total"], reverse=True)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="只列 total 或 top 超过该值的工具（0=全部）")
    ap.add_argument("--json", dest="json_path", default="",
                    help="把明细写入 JSON 文件")
    ap.add_argument("--strict", action="store_true",
                    help="存在超限项时 exit 1（可挂 CI / pre-commit）")
    args = ap.parse_args()

    rows = collect()
    total_chars = sum(r["total"] for r in rows)
    top_max = max((r["top"] for r in rows), default=0)

    shown = rows
    if args.limit > 0:
        shown = [r for r in rows if r["top"] > args.limit or r["total"] > args.limit]

    print(f"{'tool':<28}{'top':>6}{'param':>7}{'total':>7}{'#p':>4}")
    print("-" * 52)
    for r in shown:
        print(f"{r['name']:<28}{r['top']:>6}{r['param_desc']:>7}{r['total']:>7}{r['n_params']:>4}")
    print("-" * 52)
    print(f"工具数 {len(rows)} ｜ 常驻描述合计 {total_chars} 字符 ｜ "
          f"顶层最长 {top_max} ｜ 单工具最长 {rows[0]['total'] if rows else 0}")
    if args.limit > 0:
        print(f"超限（top 或 total > {args.limit}）：{len(shown)} / {len(rows)}")

    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"明细已写入 {args.json_path}")

    if args.strict and args.limit > 0 and shown:
        print(f"❌ 有 {len(shown)} 个工具超限 → exit 1")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
