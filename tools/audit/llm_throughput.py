"""LLM 真实输出吞吐实测（发一次最小请求，key 从 .env 读，不打印）。

用途：算出「LLM 生成占单轮时延的比例」，从而判断本地代码优化（含换语言）
的天花板。实测 51.5 tok/s；chat 单轮平均输出 923 tokens → 约 18s 纯生成。

注意：会真实消耗极少量 token（max_tokens=300，实测输出约 40 tokens）。

运行：`.venv/Scripts/python.exe tools/audit/llm_throughput.py`
"""
import re
import time
import json
env = {}
for line in open(".env", encoding="utf-8"):
    m = re.match(r'\s*([A-Z_0-9]+)\s*=\s*(.+)\s*$', line)
    if m:
        env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
key = env.get("ZENITH_LLM_API_KEY") or env.get("DEEPSEEK_API_KEY") or ""
print("key found:", bool(key), "| keys:", [k for k in env if "KEY" in k])
import httpx
body = {"model": "deepseek-flash", "messages": [{"role": "user", "content": "数到20，每个数字之间用空格分隔，不要别的内容。"}],
        "max_tokens": 300, "stream": False, "thinking": {"type": "disabled"}}
t0 = time.perf_counter()
r = httpx.post("https://api.deepseek.com/v1/chat/completions",
               headers={"Authorization": f"Bearer {key}"}, json=body, timeout=120)
el = time.perf_counter() - t0
d = r.json(); u = d.get("usage", {})
ct = u.get("completion_tokens", 0)
print(f"HTTP {r.status_code} | 总耗时 {el:.2f}s | 输出 {ct} tokens | 吞吐 {ct/el:.1f} tok/s")
print(f"usage: {json.dumps(u, ensure_ascii=False)}")
