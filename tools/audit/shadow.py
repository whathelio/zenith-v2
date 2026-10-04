import os
import re

BE = r"D:\下载文件\新建文件夹\zenith-v2\backend"
routers = []
for base, dirs, fs in os.walk(BE):
    if "_archived" in base:
        continue
    for f in fs:
        if f.endswith(".py") and ("routers" in base or f == "app.py"):
            routers.append(os.path.join(base, f))

route_re = re.compile(r'@(?:app|router)\.(get|post|put|delete|patch)\(\s*"([^"]*)"')

def to_regex(path):
    # 把 {param} 变成命名捕获，并转义其余
    parts = re.split(r"(\{[^}]*\})", path)
    out = ""
    params = []
    for p in parts:
        if p.startswith("{") and p.endswith("}"):
            name = p[1:-1].split(":")[0]
            params.append(name)
            out += "(?P<%s>[^/]+)" % name
        else:
            out += re.escape(p)
    return re.compile("^" + out + "$"), params

print("=== 路由遮蔽检测（同方法下，静态路径被先前声明的动态路径吞掉）===\n")
found = 0
for fp in sorted(routers):
    txt = open(fp, encoding="utf-8", errors="replace").read()
    lines = txt.split("\n")
    routes = []
    for i, ln in enumerate(lines):
        m = route_re.search(ln)
        if m:
            routes.append((i + 1, m.group(1).upper(), m.group(2)))
    # 同前缀分组（取第一段做前缀，如 /skills）
    for a in range(len(routes)):
        la, ma, pa = routes[a]
        if "{" not in pa:            # 只看动态路由作为"遮蔽者"
            continue
        rgx, params = to_regex(pa)
        for b in range(a + 1, len(routes)):
            lb, mb, pb = routes[b]
            if ma != mb or "{" in pb:   # 只检测后声明的静态路由
                continue
            if rgx.match(pb):
                found += 1
                print(f"  {os.path.relpath(fp, BE)}")
                print(f"      遮蔽者  L{la:<4} {ma:<5} {pa}")
                print(f"      被遮蔽  L{lb:<4} {mb:<5} {pb}   ← 永远匹配不到")
                print()

if not found:
    print("  （未发现）")
print(f"合计 {found} 处")
