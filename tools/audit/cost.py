"""LLM 成本与 token 结构实测（读 cache_stats 表，不发请求）。

用途：定位成本杠杆在哪。关键是把「缓存命中率」（命中/输入）与
「缓存命中输入的成本占比」分开——两者数值差一个量级，极易混淆。

实测基线（2026-09-11）：命中率 62.46%（chat 66.2% / background 18.6%）；
成本结构 = 命中输入 1.9% + 未命中输入 56.5% + 输出 41.6%。

运行：`.venv/Scripts/python.exe tools/audit/cost.py`
"""
import sqlite3
c=sqlite3.connect("data/zenith.db"); c.row_factory=sqlite3.Row
q="SELECT COUNT(*) n, SUM(prompt_tokens) pt, SUM(prompt_cache_hit_tokens) ht, SUM(completion_tokens) ct FROM cache_stats"
r=c.execute(q).fetchone()
print(f"调用次数 {r['n']}  输入 {r['pt']:,}  缓存命中 {r['ht']:,}  输出 {r['ct']:,}")
print(f"缓存命中率 {r['ht']/r['pt']*100:.2f}%   未命中输入 {r['pt']-r['ht']:,}")
print("\n=== 按 kind 分组（前台 vs 后台）===")
for x in c.execute("SELECT kind, COUNT(*) n, SUM(prompt_tokens) pt, SUM(prompt_cache_hit_tokens) ht, SUM(completion_tokens) ct FROM cache_stats GROUP BY kind ORDER BY pt DESC"):
    d=dict(x); hr=d['ht']/d['pt']*100 if d['pt'] else 0
    print(f"{d['kind']:12s} n={d['n']:5d}  输入={d['pt']:9,}  命中率={hr:5.1f}%  输出={d['ct']:8,}")
print("\n=== 按 model 分组 ===")
for x in c.execute("SELECT model, COUNT(*) n, SUM(prompt_tokens) pt, SUM(prompt_cache_hit_tokens) ht, SUM(completion_tokens) ct FROM cache_stats GROUP BY model ORDER BY pt DESC"):
    print(dict(x))
