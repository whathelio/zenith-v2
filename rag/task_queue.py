"""
轻量异步任务队列（SQLite + 轮询 worker）

对应评审报告 §4 承重缺失点：异步任务队列只有概念无设计。
本模块用 SQLite 任务表 + 简单 worker 实现：
- 任务状态：pending / processing / done / failed
- 超时兜底：processing 超过 N 秒视为 stale，可被重新领取
- 结果回传：result 字段存 JSON
- 不依赖 Celery/RQ，零额外服务

用法：
    from task_queue import TaskQueue
    q = TaskQueue("./zenith_rag/tasks.db")
    task_id = q.create("search", {"question": "..."})
    q.run_worker(handlers={"search": my_handler}, poll_interval=1.0)
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

DEFAULT_DB = Path("./zenith_rag/tasks.db")
STALE_SECONDS = 60  # processing 超过 60 秒视为僵死，可重新领取


class TaskQueue:
    def __init__(self, db_path: str | Path = DEFAULT_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    result TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_status ON tasks(status)")
            c.commit()

    def create(self, task_type: str, payload: dict) -> str:
        task_id = uuid.uuid4().hex[:12]
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._conn() as c:
            c.execute(
                "INSERT INTO tasks(id,type,payload,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (task_id, task_type, json.dumps(payload, ensure_ascii=False),
                 "pending", now, now),
            )
            c.commit()
        return task_id

    def get(self, task_id: str) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                d["payload"] = json.loads(d["payload"])
            except Exception:
                pass
            try:
                if d["result"]:
                    d["result"] = json.loads(d["result"])
            except Exception:
                pass
            return d

    def _claim(self) -> Optional[dict]:
        """领取一个 pending 或 stale processing 任务。"""
        now_ts = time.time()
        stale = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now_ts - STALE_SECONDS))
        with self._conn() as c:
            row = c.execute(
                """SELECT * FROM tasks
                   WHERE status='pending'
                      OR (status='processing' AND updated_at < ?)
                   ORDER BY created_at ASC LIMIT 1""",
                (stale,),
            ).fetchone()
            if not row:
                return None
            now = time.strftime("%Y-%m-%dT%H:%M:%S")
            c.execute(
                "UPDATE tasks SET status='processing', updated_at=? WHERE id=?",
                (now, row["id"]),
            )
            c.commit()
            d = dict(row)
            try:
                d["payload"] = json.loads(d["payload"])
            except Exception:
                pass
            return d

    def _finish(self, task_id: str, status: str, result: dict | str):
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        res = json.dumps(result, ensure_ascii=False) if isinstance(result, (dict, list)) else str(result)
        with self._conn() as c:
            c.execute(
                "UPDATE tasks SET status=?, result=?, updated_at=? WHERE id=?",
                (status, res, now, task_id),
            )
            c.commit()

    def list(self, status: Optional[str] = None, limit: int = 20) -> list[dict]:
        with self._conn() as c:
            if status:
                rows = c.execute(
                    "SELECT * FROM tasks WHERE status=? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            return [dict(r) for r in rows]

    def run_worker(self, handlers: dict[str, Callable[[dict], str]],
                   poll_interval: float = 1.0, max_tasks: int = 0):
        """
        阻塞轮询。handlers: {task_type: callable(payload)->result_str}
        max_tasks=0 表示无限循环。
        """
        done = 0
        while True:
            task = self._claim()
            if not task:
                time.sleep(poll_interval)
                continue
            handler = handlers.get(task["type"])
            if not handler:
                self._finish(task["id"], "failed", f"unknown task type: {task['type']}")
                continue
            try:
                result = handler(task["payload"])
                self._finish(task["id"], "done", result)
            except Exception as e:
                self._finish(task["id"], "failed", f"{type(e).__name__}: {e}")
            done += 1
            if max_tasks and done >= max_tasks:
                break
