import re
import os
from collections import defaultdict

ROOT = r"D:\下载文件\新建文件夹\zenith-v2"
FE = os.path.join(ROOT, "frontend", "src")
BE = os.path.join(ROOT, "backend")

# ---------- 后端路由 ----------
be_routes = []
route_pat = re.compile(r'@(?:app|router)\.(get|post|put|delete|patch)\(\s*"([^"]*)"')
prefix_pat = re.compile(r'APIRouter\(prefix="([^"]*)"')
for base, dirs, fs in os.walk(BE):
    if "_archive" in base:
        continue
    for f in fs:
        if not f.endswith(".py"):
            continue
        txt = open(os.path.join(base, f), encoding="utf-8", errors="replace").read()
        m = prefix_pat.search(txt)
        pre = m.group(1) if m else ""
        for mm in route_pat.finditer(txt):
            be_routes.append(pre + mm.group(2))

def norm(p):
    p = p.split("?")[0].split("#")[0]
    p = re.sub(r"\{[^}]*\}", "*", p)
    p = re.sub(r"\$\{[^}]*\}", "*", p)
    p = re.sub(r"\$\w+", "*", p)
    p = re.sub(r":\w+", "*", p)
    return p.rstrip("/")

be_norm = sorted({norm(p) for p in be_routes})

# ---------- 前端调用 ----------
# 只取静态前缀：遇到 $ ? 或引号即停，规避嵌套模板字符串
call_pat = re.compile(
    r"""(?:request(?:<[^>]*>)?\(\s*|BASE\s*\+\s*|fetch\(\s*)['"`]([^'"`$?]+)"""
)
fe_calls = defaultdict(set)
for base, dirs, fs in os.walk(FE):
    for f in fs:
        if not f.endswith((".ts", ".tsx")):
            continue
        fp = os.path.join(base, f)
        txt = open(fp, encoding="utf-8", errors="replace").read()
        for mm in call_pat.finditer(txt):
            p = mm.group(1)
            if not p.startswith("/"):
                continue
            if not p.startswith("/api"):
                p = "/api" + p
            fe_calls[norm(p)].add(os.path.relpath(fp, FE))

print("后端路由数:", len(be_norm), " | 前端可提取路径数:", len(fe_calls))

print("\n=== A. 前端调用但后端无对应路由（断链）===")
broken = []
for p in sorted(fe_calls):
    if p in be_norm:
        continue
    if any(p.startswith(b.rstrip("*")) and b.endswith("*") for b in be_norm):
        continue
    broken.append(p)
for p in broken:
    print(f"  {p}    <- {', '.join(sorted(fe_calls[p]))}")
print("  （无）" if not broken else f"  合计 {len(broken)}")

print("\n=== B. 后端有路由但前端从不调用（僵尸/仅内部用）===")
fe_prefix = {p.rstrip("*") for p in fe_calls}
dead = [b for b in be_norm if b not in fe_calls
        and not any(b.startswith(u) and u not in ("/api", "") for u in fe_prefix)]
for d in dead:
    print(f"  {d}")
print(f"  合计 {len(dead)}")
