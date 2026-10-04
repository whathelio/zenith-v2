"""端点延迟实测（持久连接，排除 curl.exe 进程启动开销）。

用途：判定「本地代码是不是瓶颈」。Windows 上 `curl -w time_total` 会混入
约 100ms 的进程启动开销，导致误判为「端点很慢」。本脚本用 httpx 持久连接
重测，得到真实数字（实测 p50 约 1.3ms）。

运行：`.venv/Scripts/python.exe tools/audit/latency.py`（需服务在 8766 运行）
判读：p50 在个位数毫秒 = 本地代码非瓶颈；换语言收益 ≈ 0。
"""
import time
import statistics
import httpx
BASE="http://127.0.0.1:8766"
eps=["/api/health","/api/conversations","/api/memories","/api/notes","/api/schedules","/api/summaries","/api/cache/stats","/api/knowledge/documents","/api/distill/files"]
with httpx.Client(base_url=BASE, timeout=30) as c:
    print(f"{'endpoint':32s} {'p50(ms)':>9s} {'p95(ms)':>9s} {'max(ms)':>9s}")
    for ep in eps:
        ts=[]
        for i in range(20):
            t=time.perf_counter(); r=c.get(ep); ts.append((time.perf_counter()-t)*1000)
        ts.sort()
        print(f"{ep:32s} {statistics.median(ts):9.2f} {ts[int(len(ts)*0.95)-1]:9.2f} {ts[-1]:9.2f}")
