import sys
import re
import os
from collections import defaultdict

# 2026-10-21 开源分发：改用相对本文件推导（tools/audit/x.py → 上三级 = 项目根）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

root = os.path.join(_PROJECT_ROOT, "backend")
files = []
for base, dirs, fs in os.walk(root):
    if "_archive" in base:
        continue
    for f in fs:
        if f.endswith(".py"):
            files.append(os.path.join(base, f))

pat = re.compile(r'@(app|router)\.(get|post|put|delete|patch)\(\s*"([^"]*)"')
by_file = defaultdict(list)
total = 0
for fp in sorted(files):
    txt = open(fp, encoding="utf-8", errors="replace").read()
    for m in pat.finditer(txt):
        owner, method, path = m.group(1), m.group(2).upper(), m.group(3)
        by_file[os.path.relpath(fp, root)].append((method, path))
        total += 1

print("=== 各文件路由数 ===")
for f, rs in sorted(by_file.items(), key=lambda kv: -len(kv[1])):
    print(f"  {len(rs):>3}  {f}")
print(f"  --- 合计 {total} ---")

print("\n=== market / mt5 / news / academic / cache 相关路由归属 ===")
for f, rs in sorted(by_file.items()):
    hits = [(m, p) for m, p in rs if any(k in p for k in ("/market/", "/mt5/", "/news/", "/academic", "/cache", "/papers"))]
    if hits:
        print(f"  {f}")
        for m, p in hits:
            print(f"      {m:<6} {p}")
