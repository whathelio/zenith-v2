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
from pathlib import Path

from task_queue import TaskQueue, DEFAULT_DB


# ------------------------------------------------------------------
# Handlers
# ------------------------------------------------------------------
def handler_search(payload: dict) -> str:
    """RAG 问答。"""
    from zotero_parse_rag_core import answer
    question = payload.get("question", "")
    return answer(question)


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


def handler_echo(payload: dict) -> str:
    """冒烟用，不调任何外部服务。"""
    return f"echo: {payload}"


HANDLERS = {
    "search": handler_search,
    "wiki": handler_wiki,
    "agent": handler_agent,
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
    handlers = {"search": handler_echo, "wiki": handler_echo, "agent": handler_echo} if args.dry_run else HANDLERS

    print(f"[worker] 启动，db={args.db} dry_run={args.dry_run} max_tasks={args.max_tasks}")
    q.run_worker(handlers=handlers, poll_interval=args.poll, max_tasks=args.max_tasks)
    print("[worker] 退出")


if __name__ == "__main__":
    main()
