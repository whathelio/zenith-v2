"""日历同步链路的回归守卫（2026-09-28，D8 治理配套）。

为什么需要这个测试：`scheduler.py` / `calendar_sync.py` / `jin10_service.py` 此前在
`tests/` 下**零单元测试覆盖**。D8-d（金十链路）的修复验证全压在
`_diag_archive/2026-09-28/zenith-gov/dz08_verify.py` 上 —— 那个脚本很强（含反向对照），
但它**不在 pytest 套件里，未来回归不会自动跑**。本文件把其中三组下沉进常规门禁。

覆盖（全部来自 dz08_verify.py）：
  1. `_calendar_sync_ok` 形态矩阵            —— 纯函数，判据本身
  2. 行为级：连续 3 次带 errors 是否**真的**产生待确认告警笔记
     + 反向对照：复刻旧语义（恒记 True）时告警**必须不产生** ← 这条是防"假测试"的关键
  3. 消费链：`sync_calendar_events` 是否把金十侧原因带出来 + 正常路径严格优势

⚠️ 两条纪律（2026-09-28 踩过，见 code-governance-workflow §5b.12）：
  - 替换模块级函数（`sync_calendar_events`）**必须可还原** → 一律走 `monkeypatch`，
    不要手工赋值；手工赋值不还原会污染后续用例，曾因此产生 2 个假 FAIL。
  - 断言前先确认**拿到的是真实现**（见 `test_前置自检_*`）—— 把静默污染变成显式失败。
"""
import asyncio
from datetime import datetime, timedelta

import pytest


class _Sentinel(Exception):
    """哨兵：让 `_calendar_sync_loop` 的 while 循环退出。

    `_calendar_sync_loop` 的 `await asyncio.sleep(...)` 在 `try/except` **之外**
    （scheduler.py:296），所以从 fake_sleep 里抛异常能干净退出，不会被 loop 自己吞掉。
    """


# ────────────────────────── 0. 前置自检 ──────────────────────────

def test_前置自检_被测对象是真实现():
    """防「桩件泄漏」：确认 import 到的是生产实现，而不是别处替换过的桩。

    若有人（或某个用例）把 `sync_calendar_events` / `_calendar_sync_ok` 换掉且没还原，
    后续断言会针对桩件通过 —— 这个测试会先把这种污染点出来。
    """
    from backend import calendar_sync as cs
    from backend import scheduler as sch

    assert cs.sync_calendar_events.__module__ == "backend.calendar_sync"
    assert sch._calendar_sync_ok.__module__ == "backend.scheduler"


# ────────────────────────── 1. D10-a 形态矩阵 ──────────────────────────

@pytest.mark.parametrize("result,expect_ok,desc", [
    ({"synced": 3, "errors": []}, True, "errors 为空 → 成功"),
    ({"synced": 0, "errors": ["无法获取外部财经日历：金十 MCP HTTP 401"]}, False, "errors 非空 → 失败"),
    ({"synced": 1}, True, "缺 errors 键 → 成功（不误伤）"),
    ({"synced": 0, "errors": None}, True, "errors=None → 成功（不误伤）"),
])
def test_calendar_sync_ok_形态矩阵(result, expect_ok, desc):
    from backend import scheduler as sch

    ok, errs = sch._calendar_sync_ok(result)
    assert ok is expect_ok, desc
    assert isinstance(errs, list), desc


def test_calendar_sync_ok_errors_原样带出():
    """判据必须把 errors 原样返回 —— 调用方靠它写 warning 日志。"""
    from backend import scheduler as sch

    errs_in = ["A", "B"]
    ok, errs = sch._calendar_sync_ok({"synced": 0, "errors": errs_in})
    assert ok is False
    assert errs == errs_in


# ────────────────────────── 2. D10-c 行为级（核心）──────────────────────────

def _drive_loop(monkeypatch, sync_impl, rounds=3, ok_override=None):
    """驱动 `_calendar_sync_loop` 恰好完成 `rounds` 次同步后退出。

    adapter 顺序：run_on_start 同步#1 → sleep#1 → 同步#2 → sleep#2 → 同步#3 → sleep#3 抛哨兵。
    即 rounds=3 时**恰好 3 次同步**，正好够触发 _TASK_FAIL_ALERT_THRESHOLD(3)。
    """
    from backend import calendar_sync as cs
    from backend import scheduler as sch

    monkeypatch.setattr(sch, "load_config", lambda: {
        "calendar_sync": {"hour": 3, "minute": 0, "days": 7,
                          "min_star": 2, "run_on_start": True}})
    monkeypatch.setattr(cs, "sync_calendar_events", sync_impl)
    if ok_override is not None:
        monkeypatch.setattr(sch, "_calendar_sync_ok", ok_override)

    calls = {"n": 0}

    async def fake_sleep(_seconds):
        calls["n"] += 1
        if calls["n"] >= rounds:
            raise _Sentinel()

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    sch._task_health.clear()
    try:
        asyncio.run(sch._calendar_sync_loop())
    except _Sentinel:
        pass


def _alert_titles() -> list:
    from backend import database as db
    with db.db() as c:
        return [r[0] for r in c.execute(
            "SELECT title FROM notes WHERE title LIKE '%后台任务告警%'").fetchall()]


async def _sync_with_errors(days=7, min_star=2):
    """模拟金十断供：不抛异常，用 errors 表达失败（这正是契约的样子）。"""
    return {"synced": 0, "errors": ["无法获取外部财经日历：金十 MCP HTTP 401"],
            "next_sync": ""}


def test_连续三次失败_必须产生待确认告警笔记(test_db, monkeypatch):
    """🔴 本文件最重要的一条：告警**真的会响**。

    这是 D8-a 要修的那件事 —— 此前 `scheduler` 对日历同步恒记 True，
    `fail_count` 每次被归零 → 连续失败计数永远到不了阈值 → 告警永不产生
    → 金十断供时整个同步**静默停摆**，用户侧毫无感知。
    """
    from backend import scheduler as sch

    _drive_loop(monkeypatch, _sync_with_errors)

    h = sch._task_health.get("calendar_sync", {})
    assert h.get("fail_count") == 3, "3 次带 errors 的同步必须计 3 次失败"
    assert h.get("alerted") is True, "达阈值后 alerted 标记必须置位"
    assert _alert_titles() == ["后台任务告警: calendar_sync"], \
        "必须写出用户可见的待确认告警笔记"


def test_反向对照_旧语义恒记成功时告警永不产生(test_db, monkeypatch):
    """反向对照：复刻**改动前**的语义（无条件记 True），证明上面那条不是空跑。

    没有这条，「告警产生了」可能只是因为 `_record_task_result` 本身就会写笔记；
    有了这条，才能证明**判据是告警能否产生的那个决定性变量**。
    """
    from backend import scheduler as sch

    _drive_loop(monkeypatch, _sync_with_errors, ok_override=lambda _r: (True, []))

    h = sch._task_health.get("calendar_sync", {})
    assert h.get("fail_count") == 0, "旧语义下 fail_count 被每次成功归零"
    assert _alert_titles() == [], "旧语义下告警笔记永不产生 —— 这就是静默停摆"


def test_失败后成功一次_计数与告警标记必须清零(test_db, monkeypatch):
    """恢复语义：任务恢复正常后，计数与已告警标记都要复位（否则会永久沉默）。"""
    from backend import scheduler as sch

    _drive_loop(monkeypatch, _sync_with_errors)
    assert sch._task_health["calendar_sync"]["fail_count"] == 3

    async def _ok_sync(days=7, min_star=2):
        return {"synced": 5, "errors": [], "next_sync": ""}

    _drive_loop(monkeypatch, _ok_sync, rounds=1)
    h = sch._task_health.get("calendar_sync", {})
    assert h.get("fail_count") == 0
    assert h.get("alerted") is False


def test_同步抛异常时按失败计数(test_db, monkeypatch):
    """契约之外的真异常路径：loop 内的 except 分支也必须记失败（不得吞掉）。"""
    from backend import scheduler as sch

    async def _boom(days=7, min_star=2):
        raise RuntimeError("模拟同步函数自身抛错")

    _drive_loop(monkeypatch, _boom)
    assert sch._task_health.get("calendar_sync", {}).get("fail_count") == 3


# ────────────────────────── 3. D8-d 消费链 ──────────────────────────

class _FakeSvc:
    """替身金十服务：可给出日历数据 + 可选的 last_error 通道。"""

    def __init__(self, calendar, err=""):
        self._cal, self._err = calendar, err

    async def list_calendar(self):
        return self._cal

    def last_error(self):
        return self._err


class _FakeSvcNoChannel:
    """老版本替身：**没有** last_error 方法（验证消费侧 getattr 兼容）。"""

    def __init__(self, calendar):
        self._cal = calendar

    async def list_calendar(self):
        return self._cal


def _one_future_event():
    t = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
    return [{"title": "回归守卫用测试事件", "star": 3, "pub_time": t,
             "previous": "1", "consensus": "2", "actual": "",
             "revised": "", "affect_txt": "利多"}]


def test_取数失败时_必须带出金十侧具体原因(test_db, monkeypatch):
    """errors[0] 要能区分 token 缺失 / 401 / 超时 —— 否则排障只能另写一次性探针。"""
    from backend import calendar_sync as cs

    monkeypatch.setattr(cs, "get_jin10_service", lambda: _FakeSvc(
        None, "金十 MCP HTTP 401 (url=…, body[:200]='unauth')"))
    out = asyncio.run(cs.sync_calendar_events())

    assert out["synced"] == 0
    assert "HTTP 401" in out["errors"][0], out["errors"]


def test_服务无last_error通道时_降级而不崩(test_db, monkeypatch):
    """老版本服务（无 last_error）必须降级为通用文案，不能 AttributeError。"""
    from backend import calendar_sync as cs

    monkeypatch.setattr(cs, "get_jin10_service", lambda: _FakeSvcNoChannel(None))
    out = asyncio.run(cs.sync_calendar_events())

    assert out["synced"] == 0
    assert "无法获取外部财经日历" in out["errors"][0], out["errors"]
    assert "未提供原因" in out["errors"][0], out["errors"]


def test_正常路径_严格优势不被误伤(test_db, monkeypatch):
    """errors 为空且取到数据时，必须仍判成功 —— 修复不能把正常路径也打成失败。"""
    from backend import calendar_sync as cs
    from backend import scheduler as sch

    monkeypatch.setattr(cs, "get_jin10_service", lambda: _FakeSvc(_one_future_event(), ""))
    out = asyncio.run(cs.sync_calendar_events())

    assert out["synced"] > 0, out
    assert out["errors"] == [], out
    ok, errs = sch._calendar_sync_ok(out)
    assert ok is True and errs == []
