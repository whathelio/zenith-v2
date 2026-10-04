import json
import urllib.request
import urllib.error
import time

BASE = "http://127.0.0.1:8766"
# (模块, 方法, 路径)
CASES = [
    ("Dashboard",   "GET", "/api/modules/stats"),
    ("聊天",        "GET", "/api/conversations"),
    ("笔记",        "GET", "/api/notes"),
    ("记忆",        "GET", "/api/memories"),
    ("技能",        "GET", "/api/modules/skills"),
    ("技能",        "GET", "/api/modules/skills/stats"),
    ("技能",        "GET", "/api/modules/skills/files"),
    ("MCP",         "GET", "/api/modules/mcp"),
    ("MCP",         "GET", "/api/modules/mcp/health"),
    ("日历",        "GET", "/api/calendar/week"),
    ("日历",        "GET", "/api/calendar/month"),
    ("日历",        "GET", "/api/calendar/templates"),
    ("日程",        "GET", "/api/schedules"),
    ("提醒",        "GET", "/api/reminders"),
    ("提醒",        "GET", "/api/reminders/presets"),
    ("目标",        "GET", "/api/goals"),
    ("目标",        "GET", "/api/goals/stats"),
    ("知识库",      "GET", "/api/knowledge/health"),
    ("知识库",      "GET", "/api/knowledge/documents"),
    ("知识库",      "GET", "/api/knowledge/tasks"),
    ("蒸馏摘要",    "GET", "/api/summaries"),
    ("蒸馏摘要",    "GET", "/api/distill/files"),
    ("审计",        "GET", "/api/audit/traces"),
    ("审计",        "GET", "/api/audit/trace-history"),
    ("缓存统计",    "GET", "/api/cache/stats"),
    ("学术论文",    "GET", "/api/academic/stats"),
    ("学术论文",    "GET", "/api/academic/papers"),
    ("新闻",        "GET", "/api/news/flash"),
    ("MT5",         "GET", "/api/mt5/status"),
    ("市场分析",    "GET", "/api/market/status"),
    ("设置",        "GET", "/api/settings"),
    ("健康",        "GET", "/api/health"),
    ("文件分析",    "GET", "/api/analysis-documents"),
    ("教程",        "GET", "/api/tutorials/active"),
]

def hit(method, path):
    req = urllib.request.Request(BASE + path, method=method)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, body, (time.time() - t0) * 1000
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        return e.code, body, (time.time() - t0) * 1000
    except Exception as e:
        return 0, str(e), (time.time() - t0) * 1000

def summarize(body):
    try:
        d = json.loads(body)
    except Exception:
        return body[:60].replace("\n", " ")
    if isinstance(d, list):
        return f"list[{len(d)}]"
    if isinstance(d, dict):
        keys = list(d.keys())
        return f"dict{{{', '.join(keys[:5])}{'...' if len(keys) > 5 else ''}}}"
    return str(d)[:50]

print(f"{'模块':<10}{'HTTP':>6}{'耗时ms':>9}  返回摘要")
print("-" * 92)
bad = []
for mod, m, p in CASES:
    code, body, ms = hit(m, p)
    flag = "OK " if 200 <= code < 300 else ("410" if code == 410 else "!! ")
    print(f"{mod:<10}{code:>5} {ms:>8.0f}  {flag} {p}")
    print(f"{'':<10}{'':>6}{'':>9}     {summarize(body)}")
    if not (200 <= code < 300) and code != 410:
        bad.append((mod, p, code, body[:180]))

print("\n=== 异常端点明细 ===")
for mod, p, code, b in bad:
    print(f"  [{code}] {mod} {p}\n      {b}")
if not bad:
    print("  （无）")
