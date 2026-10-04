import os
import re

ROOT = r"D:\下载文件\新建文件夹\zenith-v2"
BE = os.path.join(ROOT, "backend")

modules = {}
for base, dirs, fs in os.walk(BE):
    if "_archived" in base or "__pycache__" in base:
        continue
    for f in fs:
        if f.endswith(".py"):
            fp = os.path.join(base, f)
            rel = os.path.relpath(fp, ROOT).replace("\\", ".").replace("/", ".")[:-3]
            modules[rel] = fp
p = os.path.join(ROOT, "start.py")
if os.path.exists(p):
    modules["start"] = p

imp_pat = re.compile(r"^\s*from\s+([\w\.]+)\s+import\s+([^\n]+)", re.M)
imp2_pat = re.compile(r"^\s*import\s+([\w\.]+)", re.M)

def last_seg(t):
    t = t.strip().lstrip(".")
    return t.split(".")[-1] if t else ""

referenced = set()
for rel, fp in modules.items():
    txt = open(fp, encoding="utf-8", errors="replace").read()
    for m in imp_pat.finditer(txt):
        referenced.add(last_seg(m.group(1)))
        names = m.group(2)
        names = names.split("#")[0]
        for part in names.split(","):
            part = part.strip().split(" as ")[0].strip()
            if part and part != "*":
                referenced.add(last_seg(part))
    for m in imp2_pat.finditer(txt):
        referenced.add(last_seg(m.group(1)))

print("=== 从未被任何模块 import 的后端模块（孤儿候选）===")
orphans = []
for rel in sorted(modules):
    name = rel.split(".")[-1]
    if name == "__init__" or rel in ("backend.app", "start"):
        continue
    if name not in referenced:
        lines = sum(1 for _ in open(modules[rel], encoding="utf-8", errors="replace"))
        orphans.append((rel, lines))
        print(f"  {rel:<42} {lines:>5} 行")
print("  （无）" if not orphans else f"  合计 {len(orphans)} 个")

print("\n=== _archived 是否被活代码 import（必须为 0，否则归档目录不可删）===")
arch_hits = []
for rel, fp in modules.items():
    txt = open(fp, encoding="utf-8", errors="replace").read()
    for i, ln in enumerate(txt.split("\n"), 1):
        s = ln.strip()
        if "_archived" in s and (s.startswith("from ") or s.startswith("import ")):
            arch_hits.append((rel, i, s))
if arch_hits:
    for rel, i, s in arch_hits:
        print(f"  ⚠️  {rel}:{i}  {s}")
    print(f"  合计 {len(arch_hits)} 处 → **归档目录当前不可删除**")
else:
    print("  ✅ 0 处：_archived 已无活依赖，可安全删除（但仍建议保留）")

print("\n=== 路由模块是否都被 app.py 挂载 ===")
app = open(modules["backend.app"], encoding="utf-8", errors="replace").read()
for rel in sorted(modules):
    if not rel.startswith("backend.routers."):
        continue
    name = rel.split(".")[-1]
    if name == "__init__":
        continue
    mounted = f"include_router({name}.router)" in app
    print(f"  {name:<14} {'已挂载' if mounted else '❌ 未挂载'}")
