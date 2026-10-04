"""路由重复注册检测 —— 同一 (方法, 规范化路径) 被注册多次。

为什么单独写一个：
- `shadow.py` 只查「静态路径被先声明的动态路径吞掉」（`/a/b` 被 `/a/{x}` 遮蔽）；
- `orphan.py` 只查「模块有没有被 import」；
- **两者都查不出「两个文件注册了完全相同的路径」** —— 而这正是本仓库实际存在的坑
  （`routers/distill.py` 的 6 条路由全被 `app.py` 的版本遮蔽，改那边代码不生效）。

判定「谁生效」：FastAPI/Starlette 按**注册顺序**匹配，先注册者胜。
本仓库的注册顺序恒为：
  1) `app.py` 里所有 `@app.*` 装饰器（按源码顺序，import 时执行）
  2) 各 router，按 `app.py` 中 `include_router(...)` 的出现顺序
所以 app.py 的版本总是赢过任何 router 的同名路由。

用法：
    cd D:\\下载文件\\新建文件夹\\zenith-v2
    .venv/Scripts/python.exe tools/audit/duplicate_routes.py
    .venv/Scripts/python.exe tools/audit/duplicate_routes.py --json dup.json

退出码：0 = 无重复；1 = 存在重复（可直接作为门禁）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APP = os.path.join(ROOT, "backend", "app.py")
ROUTERS_DIR = os.path.join(ROOT, "backend", "routers")

DECO = re.compile(r'@(app|router)\.(get|post|put|delete|patch)\(\s*"([^"]*)"')
PREFIX = re.compile(r'APIRouter\(\s*prefix="([^"]*)"')
INCLUDE = re.compile(r'include_router\(\s*(\w+)\.router\s*\)')


def norm(path: str) -> str:
    """把路径参数归一化，便于比较 `/weekly/{start}` 与 `/weekly/{week_start}`。"""
    p = path.split("?")[0].split("#")[0]
    p = re.sub(r"\{[^}]*\}", "*", p)
    return p.rstrip("/") or "/"


def scan_app():
    """app.py 内的 @app.* 路由（按源码顺序）。"""
    out = []
    with open(APP, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, 1):
            m = DECO.search(line)
            if m and m.group(1) == "app":
                out.append({"method": m.group(2).upper(), "path": norm(m.group(3)),
                            "raw": m.group(3), "where": f"app.py:{i}", "order": len(out)})
    return out


def include_order():
    """app.py 里 include_router 的顺序 → 模块名列表。"""
    order = []
    with open(APP, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = INCLUDE.search(line)
            if m:
                order.append(m.group(1))
    return order


def scan_routers(base_order: int):
    """各 router 的路由，按 include 顺序排在 app.py 之后。"""
    out = []
    order = include_order()
    for mod in order:
        fn = os.path.join(ROUTERS_DIR, f"{mod}.py")
        if not os.path.exists(fn):
            continue
        txt = open(fn, encoding="utf-8", errors="replace").read()
        pm = PREFIX.search(txt)
        pre = pm.group(1) if pm else ""
        for i, line in enumerate(txt.splitlines(), 1):
            m = DECO.search(line)
            if m and m.group(1) == "router":
                out.append({"method": m.group(2).upper(), "path": norm(pre + m.group(3)),
                            "raw": pre + m.group(3), "where": f"routers/{mod}.py:{i}",
                            "order": base_order + len(out)})
    # include 列表里没有的（未挂载）单独标出来，避免漏检
    for f in sorted(os.listdir(ROUTERS_DIR)):
        if not f.endswith(".py") or f == "__init__.py":
            continue
        mod = f[:-3]
        if mod in order:
            continue
        txt = open(os.path.join(ROUTERS_DIR, f), encoding="utf-8", errors="replace").read()
        pm = PREFIX.search(txt)
        pre = pm.group(1) if pm else ""
        for i, line in enumerate(txt.splitlines(), 1):
            m = DECO.search(line)
            if m and m.group(1) == "router":
                out.append({"method": m.group(2).upper(), "path": norm(pre + m.group(3)),
                            "raw": pre + m.group(3),
                            "where": f"routers/{mod}.py:{i} (未在 include_router 中)",
                            "order": base_order + len(out)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    app_routes = scan_app()
    rt_routes = scan_routers(len(app_routes))
    allr = app_routes + rt_routes

    groups: dict[tuple[str, str], list[dict]] = {}
    for r in allr:
        groups.setdefault((r["method"], r["path"]), []).append(r)
    dups = {k: sorted(v, key=lambda x: x["order"]) for k, v in groups.items() if len(v) > 1}

    print(f"共扫描 {len(allr)} 条路由注册（app.py {len(app_routes)} + routers {len(rt_routes)}）")
    print(f"其中同一 (方法, 路径) 被重复注册的：{len(dups)} 组\n")

    if not dups:
        print("  （未发现重复注册）")
    for (method, path), items in sorted(dups.items()):
        print(f"  ⚠️  {method:5} {path}")
        for i, it in enumerate(items):
            mark = "← 生效" if i == 0 else "← 被遮蔽（永不执行）"
            print(f"        {it['where']:38} {mark}")
        if len({it["raw"] for it in items}) > 1:
            print(f"        注：原始路径写法不同 —— {[it['raw'] for it in items]}")
        print()

    shadowed = sum(len(v) - 1 for v in dups.values())
    print("=" * 66)
    print(f"合计 {len(dups)} 组重复，其中 {shadowed} 条路由被遮蔽（写了但永不执行）")
    print("=" * 66)

    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({f"{k[0]} {k[1]}": v for k, v in dups.items()}, f,
                      ensure_ascii=False, indent=2)
        print(f"已写入 {a.json}")
    return 1 if dups else 0


if __name__ == "__main__":
    sys.exit(main())
