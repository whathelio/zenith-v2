"""
Zenith 异步任务 worker

消费 task_queue.py 里的任务，执行 search / wiki / agent handler。
对应评审 §4：让异步队列真正能跑，而不只是建任务。

用法：
    export LLM_API_KEY="sk-..."
    export LLM_BASE_URL="https://api.deepseek.com/v1"
    export LLM_MODEL="deepseek-v4-pro"

    # 正式跑（阻塞轮询）
    python task_worker.py

    # 冒烟：用 echo handler，不调 LLM/RAG
    python task_worker.py --dry-run --max-tasks 1

    # 限制处理数量后退出
    python task_worker.py --max-tasks 5
"""

from __future__ import annotations

import argparse
import io
import contextlib
import os

from task_queue import TaskQueue, DEFAULT_DB


# ------------------------------------------------------------------
# Handlers
# ------------------------------------------------------------------
def handler_search(payload: dict) -> str:
    """RAG 问答。支持 payload.collection 指定集合。"""
    from zotero_parse_rag_core import answer
    question = payload.get("question", "")
    return answer(question, collection=payload.get("collection"))


def handler_wiki(payload: dict) -> str:
    """LLM Wiki 问答。llm_wiki_compiler.query 直接 print，这里捕获 stdout。"""
    import llm_wiki_compiler as w
    question = payload.get("question", "")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        w.query(question)
    return buf.getvalue().strip()


def handler_agent(payload: dict) -> str:
    """Agent 占位：后续接入 Zenith Agent Core。"""
    msg = payload.get("message", "")
    return f"(stub) agent 未接入，收到消息：{msg}"


def handler_ingest(payload: dict) -> str:
    """批量入库（C-5 改造，2026-10-08）。

    payload 支持三种写法：
      {"paths": ["D:/.../a.md", ...], "collection": "nomad_ref", "title_prefix": ""}
      {"dir": "D:/.../md", "pattern": "*.md", "collection": "nomad_ref"}
      {"manifests": ["D:/.../manifest.txt"], "collection": ...}   # 每行一个路径

    🔴 受 ZENITH_INGEST_ROOTS 白名单约束；未配置则直接拒绝。
    把「大批量入库」从 HTTP 同步请求里解放出来 —— 请求方立即拿到 task_id，
    之后轮询 GET /tasks/{id} 看进度/结果。
    """
    import glob as _glob
    import json as _json
    from pathlib import Path as _P
    from zotero_parse_rag_core import ingest_paths, _assert_ingest_allowed, INGEST_ROOTS

    if not INGEST_ROOTS:
        return "拒绝：本地路径入库未启用（需设 ZENITH_INGEST_ROOTS 白名单）"

    paths = [str(x) for x in (payload.get("paths") or [])]
    # dir 来源单独记因由：glob 对「目录不存在 / 无匹配」一律静默返回 []，
    # 若与「没传任何输入」共用同一条错误信息，排障时会被严重误导（2026-10-08 实测踩过）。
    _dir_note = ""
    if payload.get("dir"):
        pat = payload.get("pattern", "*.md")
        # normpath 容忍 "D://a//b" 这类重复分隔符 —— HTTP/JSON 往返与本机 shell 转义常见。
        _d = os.path.normpath(str(payload["dir"]))
        if not _P(_d).is_dir():
            _dir_note = f"dir 不存在或不是目录: {_d}"
        else:
            _hit = sorted(_glob.glob(str(_P(_d) / pat)))
            if _hit:
                paths += _hit
            else:
                _dir_note = f"dir 内无匹配 {pat!r} 的文件: {_d}"
    for mf in (payload.get("manifests") or []):
        # 🟡 2026-10-08 修复（code-reviewer 查出）：原实现在做白名单校验**之前**
        # 就 `open(mf)` 读文件 → manifest 路径本身可以指向白名单外的任意文件
        # （可用来探测文件是否存在/读取内容）。这里先校验 manifest 自身在白名单内。
        try:
            _assert_ingest_allowed(_P(mf))
        except PermissionError:
            return f"拒绝：manifest 不在白名单内: {mf}"
        try:
            with open(mf, "r", encoding="utf-8") as fh:
                paths += [ln.strip() for ln in fh if ln.strip()]
        except Exception as e:
            return f"manifest 读取失败 {mf}: {e}"

    if not paths:
        if _dir_note:
            return f"拒绝：{_dir_note}"
        return "拒绝：未提供 paths / dir / manifests 任一输入"

    allowed, denied = [], 0
    for raw in paths:
        try:
            _assert_ingest_allowed(_P(raw))
            allowed.append(raw)
        except PermissionError:
            denied += 1
    if not allowed:
        return f"拒绝：{len(paths)} 个路径全部不在白名单内"

    res = ingest_paths(allowed, collection=payload.get("collection"),
                       title_prefix=payload.get("title_prefix", ""))
    res["denied_outside_whitelist"] = denied
    if _dir_note:  # dir 源零匹配但其它来源有货 → 明确告知，避免「以为全量入库了」
        res["dir_note"] = _dir_note
    return _json.dumps(res, ensure_ascii=False)


def handler_echo(payload: dict) -> str:
    """冒烟用，不调任何外部服务。"""
    return f"echo: {payload}"


HANDLERS = {
    "search": handler_search,
    "wiki": handler_wiki,
    "agent": handler_agent,
    "ingest": handler_ingest,
}


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Zenith 任务 worker")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="任务库路径")
    parser.add_argument("--poll", type=float, default=1.0, help="轮询间隔秒")
    parser.add_argument("--max-tasks", type=int, default=0, help="处理多少任务后退出，0=无限")
    parser.add_argument("--dry-run", action="store_true", help="用 echo handler，不调 LLM/RAG")
    args = parser.parse_args()

    q = TaskQueue(args.db)
    handlers = ({"search": handler_echo, "wiki": handler_echo,
                 "agent": handler_echo, "ingest": handler_echo}
                if args.dry_run else HANDLERS)

    print(f"[worker] 启动，db={args.db} dry_run={args.dry_run} max_tasks={args.max_tasks}")
    q.run_worker(handlers=handlers, poll_interval=args.poll, max_tasks=args.max_tasks)
    print("[worker] 退出")


if __name__ == "__main__":
    main()
