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
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

DEFAULT_DB = Path("./zenith_rag/tasks.db")
# 🔴 2026-10-08 修复 + 放开配置：
# 原值 60 秒，且 staleness 用 `time.gmtime()` 计算，而 updated_at 用本地时间
# `time.strftime()` 写入 —— **两者时基不一致**。
#
# ⚠️ 方向很重要（2026-10-08 由 code-reviewer 指出、我复算确认）：
#   本地 21:00 写入的 updated_at，与 gmtime 算出的 13:00 阈值比较，
#   `"21:00" < "13:00"` → **False** ⇒ 刚领取的任务**不会**被判僵死。
#   真正的后果是**反向的**：任务要「本地时间超过 8 小时 + 60 秒」才可被重领
#   ⇒ 崩溃/中断的 task 会在 processing 里**僵死约 8 小时**才被回收（**活性问题**，
#   不是重复领取问题）。原注释把方向写反了，此处更正。
#
# 现统一用**本地时间**，阈值 1800s。
# ⚠️ 注意：1800s < 原有效窗口（~8h），所以「长任务被误判僵死」的风险**上升**了。
#   缓解：`touch()` 心跳必须被真正调用（worker 侧按文件续期），否则一个跑超
#   30 分钟的 ingest 会被另一个 worker 抢走重跑。
STALE_SECONDS = int(os.environ.get("ZENITH_TASK_STALE_SECONDS", "1800"))


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
        """领取一个 pending 或 stale processing 任务。

        🔴 2026-10-08 修复（code-reviewer 实测：40 线程抢 1 个任务，**全部**都拿到同一个）：
        原实现是「SELECT 拿一行 → 无条件下 UPDATE → commit」，两个并发 worker 会双双
        选中同一行、双双 UPDATE 成功（SQLite 只保证写串行，不保证这段 read-modify-write 原子），
        于是同一任务被**跑两遍**（对本系统即同一批文件重复入库）。

        现改为 **compare-and-swap**：UPDATE 的 WHERE 再带上「原状态仍匹配」条件，
        靠 `rowcount` 判断自己是不是唯一抢到的那一个；不是就重试下一行。
        """
        now_ts = time.time()
        # 🔴 必须用 localtime：updated_at 是本地时间写入的（见上文 STALE_SECONDS 注释）
        stale = time.strftime("%Y-%m-%dT%H:%M:%S",
                              time.localtime(now_ts - STALE_SECONDS))
        with self._conn() as c:
            for _attempt in range(20):
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
                cur = c.execute(
                    """UPDATE tasks SET status='processing', updated_at=?
                       WHERE id=?
                         AND (status='pending'
                              OR (status='processing' AND updated_at < ?))""",
                    (now, row["id"], stale),
                )
                c.commit()
                if cur.rowcount == 1:
                    d = dict(row)
                    try:
                        d["payload"] = json.loads(d["payload"])
                    except Exception:
                        pass
                    return d
                # rowcount=0 → 已被别的 worker 抢走，重试下一行
            return None

    def _finish(self, task_id: str, status: str, result: dict | str):
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        res = json.dumps(result, ensure_ascii=False) if isinstance(result, (dict, list)) else str(result)
        with self._conn() as c:
            c.execute(
                "UPDATE tasks SET status=?, result=?, updated_at=? WHERE id=?",
                (status, res, now, task_id),
            )
            c.commit()

    def touch(self, task_id: str):
        """心跳：长时任务可周期性调用，避免被判定为僵死而重复领取。"""
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._conn() as c:
            c.execute("UPDATE tasks SET updated_at=? WHERE id=? AND status='processing'",
                      (now, task_id))
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
            # 🟢 2026-10-08：把 touch() 真正接上。
            # 背景：STALE_SECONDS 从「原有效窗口 ~8 小时」收紧到 1800s，
            # 若长任务（大批量 ingest 可能跑十几分钟）中途不续期，
            # 会被另一个 worker 判为僵死并抢走重跑。这里在 handler 执行期间
            # 起一个心跳线程，周期性刷新 updated_at，避免误抢。
            stop_hb = threading.Event()

            def _heartbeat(_id=task["id"], _stop=stop_hb):
                while not _stop.wait(max(5.0, poll_interval * 5)):
                    try:
                        self.touch(_id)
                    except Exception:
                        pass  # 心跳失败不影响任务本身

            hb = threading.Thread(target=_heartbeat, daemon=True)
            hb.start()
            try:
                result = handler(task["payload"])
                self._finish(task["id"], "done", result)
            except Exception as e:
                self._finish(task["id"], "failed", f"{type(e).__name__}: {e}")
            finally:
                stop_hb.set()
            done += 1
            if max_tasks and done >= max_tasks:
                break
