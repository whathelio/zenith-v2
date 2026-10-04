"""O-04 前提实测：同进程 watchdog 线程能否在「有线程 CPU 空转」时获得调度。

O-04 原文断言：
    「纯 Python 死循环（while True: pass）不释放 GIL，同进程线程抢不到 GIL」

CPython 的 eval loop 会按 sys.getswitchinterval()（默认 5ms）检查 eval breaker 并释放 GIL，
所以「纯 Python 死循环」理论上**会**让出 GIL。真正会长时间独占 GIL 的是
**不调用 Py_BEGIN_ALLOW_THREADS 的 C 层长循环**（如单次 sum(range(N))）。

方法（三种场景各跑一次）：
  · 主线程    ：计时窗口 DURATION 秒
  · 负载线程  ：执行 load() 直到窗口结束
  · 守护线程  ：每 TICK 秒记一次时间戳
指标：tick 次数 + **相邻 tick 的最大空档**（max gap）。
  空档 ≈ TICK            → 守护线程正常调度，该场景下线程版 watchdog 有效
  空档 ≫ TICK（近 DURATION）→ 守护线程被饿死，该场景下必须用独立进程

用法：.venv/Scripts/python.exe tools/audit/g04_gil_probe.py
"""
from __future__ import annotations

import sys
import threading
import time

DURATION = 3.0
TICK = 0.2
IDEAL = int(DURATION / TICK)


def _measure(name: str, load) -> tuple[int, float]:
    ticks: list[float] = []
    stop = threading.Event()

    def watchdog():
        while not stop.is_set():
            ticks.append(time.monotonic())
            time.sleep(TICK)

    def loader():
        while not stop.is_set():
            load()

    t_watch = threading.Thread(target=watchdog, daemon=True, name="watchdog-probe")
    t_load = threading.Thread(target=loader, daemon=True, name="cpu-load")
    t_watch.start()
    t_load.start()

    time.sleep(DURATION)
    stop.set()
    t_watch.join(timeout=1.5)
    t_load.join(timeout=1.5)

    got = max(len(ticks) - 1, 0)
    gap = 0.0
    if len(ticks) > 1:
        gap = max(b - a for a, b in zip(ticks, ticks[1:]))
    print(f"[{name}] tick={got:>2}/{IDEAL}  最大空档={gap * 1000:>6.0f} ms")
    return got, gap


def load_pure_python() -> None:
    """纯 Python 字节码自旋：eval loop 会检查 eval breaker → 应释放 GIL。"""
    x = 0
    for _ in range(200_000):
        x += 1


def load_c_loop() -> None:
    """单次 C 层长循环：sum() 内部不检查 eval breaker → 期间独占 GIL。"""
    sum(range(100_000_000))


def main() -> None:
    print(f"Python         : {sys.version.split()[0]}")
    print(f"switchinterval : {sys.getswitchinterval() * 1000:.1f} ms")
    print(f"窗口 / 采样    : {DURATION}s / {TICK}s（理想 tick = {IDEAL}）")
    print("-" * 64)

    # 先量一次 C 层调用的单次时长，便于解释结果
    t0 = time.monotonic()
    load_c_loop()
    c_once = time.monotonic() - t0
    print(f"单次 sum(range(1e8)) 耗时参考：{c_once * 1000:.0f} ms")
    print("-" * 64)

    a = _measure("A 纯 Python 自旋", load_pure_python)
    b = _measure("B C 层长循环   ", load_c_loop)

    print("-" * 64)
    print("结论：")
    ok_a = a[1] < TICK * 2.5
    say_a = "✅ 守护线程仍被正常调度 → O-04 对「纯 Python 空转」不成立" if ok_a else "❌ 守护线程被饿死 → O-04 对该场景成立"
    print(f"  A 纯 Python 自旋：{say_a}")
    ok_b = b[1] < TICK * 2.5
    say_b = "✅ 未被饿死" if ok_b else "❌ 被 C 层调用饿死 → 该场景必须用独立进程探活"
    print(f"  B C 层长循环    ：{say_b}")


if __name__ == "__main__":
    main()
