"""Zenith 核心功能可用性矩阵 —— 判「功能能不能真的用」，不只是「接口返 200」。

与 smoke.py 的区别：
- smoke.py 只判 HTTP 状态码；
- 本脚本进一步判**响应数据是否有效**（形状对不对、该有数据的地方有没有数据、
  关键字段在不在），给出三态判定：
    ✅ 可用      接口正常且返回有效数据
    ⚠️ 可用但空  接口正常，但没有数据（是「没内容」，不是「坏了」）
    🔴 不可用    报错 / 超时 / 形状不符 / 关键字段缺失

用法：
    export PATH="/usr/bin:/bin:$PATH"
    cd D:\\下载文件\\新建文件夹\\zenith-v2
    .venv/Scripts/python.exe tools/audit/health_matrix.py
    .venv/Scripts/python.exe tools/audit/health_matrix.py --chat-roundtrip   # 额外做一次真实对话（耗少量 token，会自动清理）
    .venv/Scripts/python.exe tools/audit/health_matrix.py --json ../_diag_archive/health.json

退出码：0 = 无 🔴；1 = 存在 🔴
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8766"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 显式禁用代理

OK, EMPTY, BAD = "OK", "EMPTY", "BAD"
MARK = {OK: "✅ 可用", EMPTY: "⚠️ 可用但空", BAD: "🔴 不可用"}


def req(method: str, path: str, body=None, timeout: int = 30):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with OPENER.open(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), (time.perf_counter() - t0) * 1000
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), (time.perf_counter() - t0) * 1000
    except Exception as e:                                   # noqa: BLE001
        return 0, str(e), (time.perf_counter() - t0) * 1000


def jload(body: str):
    try:
        return json.loads(body)
    except Exception:                                        # noqa: BLE001
        return None


def is_list(d) -> bool:
    if isinstance(d, list):
        return True
    # 常见的「包一层」形状
    return isinstance(d, dict) and any(isinstance(v, list) for v in d.values())


def list_of(d):
    if isinstance(d, list):
        return d
    for v in d.values():
        if isinstance(v, list):
            return v
    return []


# ---------------------------------------------------------------- 检查项
# 每项：(模块, 方法, 路径, body, 期望)
#   期望 "list_nonempty" / "dict" / "any"
CHECKS = [
    ("基础",     "GET",  "/api/health",                    None, "any"),
    ("Dashboard", "GET", "/api/modules/stats",             None, "any"),

    ("聊天",     "GET",  "/api/conversations",             None, "list_nonempty"),
    ("笔记",     "GET",  "/api/notes",                     None, "list_nonempty"),
    ("记忆",     "GET",  "/api/memories",                  None, "list_nonempty"),
    ("记忆",     "GET",  "/api/memories?limit=5",          None, "list_nonempty"),

    ("技能",     "GET",  "/api/modules/skills",            None, "list_nonempty"),
    ("技能",     "GET",  "/api/modules/skills/files",      None, "any"),
    ("MCP",      "GET",  "/api/modules/mcp",               None, "any"),
    ("MCP",      "GET",  "/api/modules/mcp/health",        None, "any"),

    ("日程",     "GET",  "/api/schedules",                 None, "list_nonempty"),
    ("日历",     "GET",  "/api/calendar/week",             None, "any"),
    ("日历",     "GET",  "/api/calendar/templates",        None, "any"),
    ("提醒",     "GET",  "/api/reminders",                 None, "any"),
    ("提醒",     "GET",  "/api/reminders/presets",         None, "any"),

    ("目标",     "GET",  "/api/goals",                     None, "any"),
    ("目标",     "GET",  "/api/goals/stats",               None, "any"),

    ("知识库",   "GET",  "/api/knowledge/health",          None, "any"),
    ("知识库",   "GET",  "/api/knowledge/documents",       None, "any"),
    ("知识库",   "GET",  "/api/knowledge/tasks",           None, "any"),
    ("知识库",   "GET",  "/api/health-gw-skip",            None, "skip"),

    ("蒸馏",     "GET",  "/api/summaries",                 None, "list_nonempty"),
    ("蒸馏",     "GET",  "/api/distill/files",             None, "any"),

    ("审计",     "GET",  "/api/audit/traces",              None, "list_nonempty"),
    ("审计",     "GET",  "/api/audit/trace-history",       None, "any"),

    ("进程监控", "GET",  "/api/processes/snapshot",        None, "any"),
    ("缓存统计", "GET",  "/api/cache/stats",               None, "any"),
    ("设置",     "GET",  "/api/settings",                  None, "any"),
    ("文件分析", "GET",  "/api/analysis-documents",        None, "any"),
]
CHECKS = [c for c in CHECKS if c[4] != "skip"]


def judge(status: int, body: str, expect: str, ms: float):
    if status == 0:
        return BAD, f"连接失败：{body[:70]}"
    if status >= 500:
        return BAD, f"HTTP {status}：{body[:90]}"
    if status >= 400:
        return BAD, f"HTTP {status}：{body[:90]}"
    d = jload(body)
    if d is None:
        return BAD, "响应不是合法 JSON"
    # 有的接口用 {error: ...} + 200 表达失败（项目既有约定），要单独抓出来。
    # ⚠️ 只在 error 是**字符串**时才算失败 —— 某些健康检查接口把 error 当**计数**
    #    （如 /api/modules/mcp/health 的 {"ok":0,"error":1} 表示「1 个 server 异常」），
    #    若不分类型会把正常报告误判为不可用。
    if isinstance(d, dict) and isinstance(d.get("error"), str) and not d.get("success"):
        return BAD, f"200 但带回 error：{str(d.get('error'))[:80]}"
    if expect == "list_nonempty":
        if not is_list(d):
            return BAD, f"期望列表，实得 {type(d).__name__}"
        items = list_of(d)
        if not items:
            return EMPTY, "列表为空（无数据）"
        return OK, f"{len(items)} 条，{ms:.0f}ms"
    if is_list(d):
        items = list_of(d)
        return (OK if items else EMPTY), (f"{len(items)} 条" if items else "空")
    if isinstance(d, dict):
        if not d:
            return EMPTY, "空 dict"
        return OK, f"keys={list(d.keys())[:4]}，{ms:.0f}ms"
    return OK, str(d)[:50]


def run_matrix():
    rows = []
    print(f"{'模块':<10}{'HTTP':>5}{'ms':>7}  {'判定':<12}说明")
    print("-" * 100)
    for mod, m, p, b, exp in CHECKS:
        st, body, ms = req(m, p, b)
        verdict, detail = judge(st, body, exp, ms)
        rows.append({"module": mod, "path": p, "http": st, "ms": round(ms),
                     "verdict": verdict, "detail": detail})
        print(f"{mod:<10}{st:>5}{ms:>7.0f}  {MARK[verdict]:<12}{detail}")
    return rows


# ---------------------------------------------------------------- 深度检查
def deep_checks():
    extra = []

    def add(name, verdict, detail):
        extra.append({"module": "深度", "path": name, "http": "", "ms": "",
                      "verdict": verdict, "detail": detail})
        print(f"{'深度':<10}{'':>5}{'':>7}  {MARK[verdict]:<12}{name}：{detail}")

    print("\n=== 深度检查（真实业务路径）===")

    # 1. 取一个会话读它的消息（验证会话历史可读）
    st, body, _ = req("GET", "/api/conversations")
    convs = list_of(jload(body) or [])
    if convs:
        cid = convs[0].get("id") or convs[0].get("conversation_id")
        st2, b2, _ = req("GET", f"/api/conversations/{cid}")
        d2 = jload(b2) or {}
        msgs = d2.get("messages") if isinstance(d2, dict) else None
        if st2 == 200 and isinstance(msgs, list) and msgs:
            add("读会话历史", OK, f"{len(msgs)} 条消息（会话 {cid}）")
        elif st2 == 200:
            add("读会话历史", EMPTY, f"会话 {cid} 无消息")
        else:
            add("读会话历史", BAD, f"HTTP {st2}：{b2[:80]}")
    else:
        add("读会话历史", EMPTY, "没有会话可读")

    # 2. 记忆搜索（另一会话改过 memories —— 重点验证不 500）
    st, body, _ = req("GET", "/api/memories?limit=3")
    if st == 200:
        add("记忆列表(limit)", OK, f"{len(list_of(jload(body) or []))} 条")
    else:
        add("记忆列表(limit)", BAD, f"HTTP {st}：{body[:100]}")

    # 3. 知识库真实检索（走 8788 网关）
    st, body, ms = req("POST", "/api/knowledge/search", {"question": "知识管理", "top_k": 3}, timeout=60)
    d = jload(body) or {}
    if st == 200 and isinstance(d, dict):
        ans = d.get("answer") or ""
        if ans and not str(ans).startswith("Error"):
            add("知识库检索", OK, f"返回 {len(str(ans))} 字，{ms:.0f}ms")
        else:
            add("知识库检索", BAD, f"answer 为空或报错：{str(d)[:100]}")
    else:
        add("知识库检索", BAD, f"HTTP {st}：{str(body)[:100]}")

    # 4. 进程快照的关键字段
    st, body, _ = req("GET", "/api/processes/snapshot")
    d = jload(body) or {}
    if st == 200 and isinstance(d, dict) and "summary" in d:
        add("进程快照字段", OK, f"summary={d.get('summary')}")
    else:
        add("进程快照字段", BAD, f"缺 summary：{str(d)[:80]}")

    return extra


def chat_roundtrip():
    """真实走一轮对话：建临时会话 → 发一条 → 读 SSE → 删会话。"""
    print("\n=== 真实对话往返（会消耗少量 token，测试后自动清理）===")
    res = {"module": "对话往返", "path": "POST /api/chat", "http": "", "ms": "",
           "verdict": BAD, "detail": ""}
    st, body, _ = req("POST", "/api/conversations", {"title": "__health_matrix_probe__"})
    d = jload(body) or {}
    cid = d.get("id") or d.get("conversation_id")
    if st not in (200, 201) or not cid:
        res["detail"] = f"建会话失败 HTTP {st}：{str(body)[:90]}"
        print(f"{'':<10}{'':>5}{'':>7}  {MARK[BAD]:<12}{res['detail']}")
        return res

    text_chunks, got_text, err = [], False, None
    try:
        data = json.dumps({"message": "只回复两个字：收到", "conversation_id": cid}).encode()
        r = urllib.request.Request(BASE + "/api/chat", data=data, method="POST",
                                   headers={"Content-Type": "application/json"})
        t0 = time.perf_counter()
        with OPENER.open(r, timeout=90) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload in ("", "[DONE]"):
                    continue
                try:
                    ev = json.loads(payload)
                except Exception:                            # noqa: BLE001
                    continue
                if ev.get("type") == "text" and ev.get("content"):
                    got_text = True
                    text_chunks.append(ev["content"])
                elif ev.get("type") == "error":
                    err = ev.get("content") or ev.get("message")
        ms = (time.perf_counter() - t0) * 1000
        reply = "".join(text_chunks)
        if err:
            res.update(verdict=BAD, detail=f"流内报错：{str(err)[:90]}", ms=round(ms))
        elif got_text and reply.strip():
            res.update(verdict=OK, detail=f"回复“{reply.strip()[:30]}”（{ms:.0f}ms）", ms=round(ms))
        else:
            res.update(verdict=BAD, detail="SSE 流中未收到任何 text 事件", ms=round(ms))
    except Exception as e:                                   # noqa: BLE001
        res["detail"] = f"对话请求异常：{e}"[:110]
    finally:
        st, _, _ = req("DELETE", f"/api/conversations/{cid}")
        cleaned = "已清理测试会话" if st in (200, 204) else f"⚠️ 清理失败 HTTP {st}"
        res["detail"] += f"；{cleaned}"
    print(f"{'':<10}{'':>5}{'':>7}  {MARK[res['verdict']]:<12}{res['detail']}")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chat-roundtrip", action="store_true", help="做一次真实对话（耗少量 token）")
    ap.add_argument("--json", default="", help="把结果写入 JSON")
    a = ap.parse_args()

    st, _, _ = req("GET", "/api/health", timeout=5)
    if st != 200:
        print(f"🔴 服务不可达（/api/health → {st}）。请先：.venv/Scripts/python.exe start.py --detach")
        return 1

    print("Zenith 核心功能可用性矩阵")
    print("=" * 100)
    rows = run_matrix()
    rows += deep_checks()
    if a.chat_roundtrip:
        rows.append(chat_roundtrip())

    bad = [r for r in rows if r["verdict"] == BAD]
    empt = [r for r in rows if r["verdict"] == EMPTY]
    print("\n" + "=" * 100)
    print(f"合计 {len(rows)} 项：✅ {len(rows) - len(bad) - len(empt)} 可用 / "
          f"⚠️ {len(empt)} 可用但空 / 🔴 {len(bad)} 不可用")
    if bad:
        print("\n🔴 不可用明细：")
        for r in bad:
            print(f"   {r['module']:<10}{r['path']}  →  {r['detail']}")
    if empt:
        print("\n⚠️ 无数据的模块（接口正常，只是没内容）：")
        for r in empt:
            print(f"   {r['module']:<10}{r['path']}")
    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        print(f"\n已写入 {a.json}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
