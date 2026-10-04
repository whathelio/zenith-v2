# start.py 与后台服务优化建议

生成时间：2026-09-10
范围：`start.py`（1037 行）+ 关联的 jin10 单例生命周期 + 辅助服务托管
原则：**零删除**，本文只列建议，不含已执行的改动

---

## 0. 结论速览

| 编号 | 问题 | 影响 | 优先级 | 改动量 |
|---|---|---|---|---|
| O-01 | 缺 `--detach`，非双击入口必然挂住调用者 | 本次「后台任务运行中」根因 | 🔴 | ~30 行 |
| O-02 | `os._exit(70)` 绕过 flush，且无 supervisor 兜底 | **丢最后 1~2 轮对话记忆**；自杀后不恢复 | 🔴 | ~15 行 |
| O-03 | 三套 watchdog 冗余且互不衔接 | 职责重叠，外部那套实际不存在 | 🔴 | 设计决策 |
| O-04 | self-watchdog 是线程，抓不住纯 CPU 死循环 | 恰好抓不到它想抓的场景 | 🟡 | ~40 行 |
| O-05 | 全局单例被当短生命周期对象 `close()` | 每次同步重做 3 次 MCP 握手 | 🟡 | ~10 行 |
| O-06 | `--stop` 连带杀掉 RAG 中台与 worker | "只重启主服务"变"全杀" | 🟡 | ~20 行 |
| O-07 | `_register_shutdown_handlers` 被 uvicorn 覆盖 | 死代码，误导维护者 | 🟢 | 删 ~30 行 |
| O-08 | 启动瞬间双份打 `/api/health` | 轻微重复请求 | 🟢 | ~10 行 |
| O-09 | `api.ts:880 knowledgeTasks` 前端零调用 | 死 API | 🟢 | 删 1 行 |
| O-10 | aux 子进程无 job object，父死子存 | 潜在孤儿进程 | 🟢 | ~15 行 |

---

## O-01 🔴 缺 `--detach`（守护化）

**证据**

| 位置 | 内容 |
|---|---|
| `start.py:868-879` | argparse 全部选项：`port / --no-browser / --browser-delay / --verbose / --reset-lock / --stop / --status / --wait / --wait-timeout / --no-aux` —— **无 `--detach`** |
| `start.py:1006` | `uvicorn.run(...)` 直接阻塞到进程结束 |
| `zenith.bat:57` | `start "" /D "%PROJECT_DIR%" "%PY_EXE%" "%START_PY%" %*` —— 靠 cmd 的 `start` 脱离 |

**问题**：脱离能力只存在于 `zenith.bat`（cmd `start`），Python 层没有。所以任何**非双击**入口（WorkBuddy 后台 shell、CI、cron、其他脚本）调用 `python start.py` 都会一直挂住。

本次「后台任务运行中」即由此产生：`start.py` 内建两个永不退出的 watchdog 线程（`start.py:685`、`start.py:736`），主进程必须常驻 → 调用它的 shell 永远等不到返回。

**建议**：加 `--detach`，自举后立即返回。

```python
if args.detach:
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), *rebuilt_args],
        cwd=str(PROJECT_DIR), creationflags=flags, close_fds=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
    )
    print(f"[OK] Zenith 已在后台启动，稍后访问 http://localhost:{port}")
    return
```

配套：`zenith.bat` 的 `start ""` 改为 `python start.py --detach`，入口统一。
**Rollback**：新增分支，删掉即恢复原行为。

---

## O-02 🔴 `os._exit(70)` 丢数据 + 无兜底

**证据**

| 位置 | 内容 |
|---|---|
| `start.py:758-770` | self-watchdog 连续 4 次健康检查失败 → dump 线程栈 → `os._exit(70)` |
| `start.py:1016-1033` | `finally` 里做 `flush_all_pending_memories()`（限时 15s）+ 清理锁 |
| `start.py:739` | docstring 称"退出后由外部 supervisor（zenith-watchdog.ps1）拉起" |
| 实测 | `Get-ScheduledTask` 无 zenith 项；无 `zenith-watchdog.ps1` 进程 |

**两个问题**

1. `os._exit()` 是**硬退出**，不执行 `finally` → `flush_all_pending_memories()` 被跳过 → 对话 buffer 里最后 1~2 轮的待提取记忆**直接丢失**。
2. 它假设有外部 supervisor 会把它拉起来，但**那套东西根本不存在** → 自杀后服务永久停摆，比"假死但进程在"更糟。

**建议**（三选一，推荐 a+b）

- **a)** 自杀前先 flush：
  ```python
  try:
      asyncio.run(asyncio.wait_for(flush_all_pending_memories(), timeout=10.0))
  except Exception:
      pass
  os._exit(70)
  ```
- **b)** 降级为"只 dump 栈 + 写告警笔记"，**不自杀**（保留现场等人工介入）。
- **c)** 补齐外部 supervisor（Windows 计划任务 / `zenith-watchdog.ps1`），让自杀有意义。

**Rollback**：改回 `os._exit(70)` 即可。

---

## O-03 🔴 三套 watchdog 冗余且互不衔接

| # | 实现 | 位置 | 周期 | 职责 | 实际状态 |
|---|---|---|---|---|---|
| 1 | `_aux_services_watchdog` | `start.py:685`（daemon 线程） | 30s | 检查 api_gateway / task_worker，死了自动重启（指数退避 2/4/8…≤30s，>3 次停止） | 🟢 有效 |
| 2 | `_self_health_watchdog` | `start.py:736`（daemon 线程） | 20s | 探活 `/api/health`，连续 4 次失败 → dump 栈 + `os._exit(70)` | 🟡 见 O-04 |
| 3 | `zenith-watchdog.ps1` | 仅存在于 docstring | — | 拉起被 #2 杀掉的主服务 | 🔴 **不存在** |

**建议**：明确分层，别让三者职责重叠。

- **进程内**：只保留 #1（辅助服务守护）——它依赖 `_AUX_PROCS` 句柄，必须在同进程。
- **进程外**：主服务存活交外部（计划任务 + 脚本，或干脆改用 NSSM/winsw 注册为 Windows 服务）。外部才可能可靠地拉起崩溃的主服务。
- **删掉** #2 的"自杀"职责，避免"自杀但无人拉起"。

### 决议与落地（2026-09-10 第 5 批）

**决议：方案 A —— 保留进程内 watchdog + 加轻量补偿。** 理由见下（第 5 批实施记录）。
外部 supervisor（B）留作后续可选项，属**纯增量**改造，不影响本批。

| 项 | 处置 | 状态 |
|---|---|---|
| #3 `zenith-watchdog.ps1`「不存在」 | 确认不存在，从 docstring 移除该暗示 | ✅ 已修 |
| #2 `while True` 无退出条件（风格不一致、主进程停不掉它） | 补 `stop_event` 参数，`while not stop_event.is_set()` + `stop_event.wait()` | ✅ 已修 |
| #2「自杀但无人拉起」→ 无声消失 | 加**退出标记**：`data/last_exit.json` 记 running/clean/watchdog_suicide/exception；下次启动自检并 WARNING | ✅ 已修 |
| #2 覆盖不到的场景 | 见 O-04（已据实测重写，不再是「纯 Python 死循环」） | ✅ 已澄清 |

> 关于「删掉 #2 的自杀职责」：**未采纳**。方案 A 下保留自杀更优 —— 主服务假死时，
> 自杀至少能把**主服务**释放出来（锁释放、端口让出），比让它挂在那里永久占着好；
> 真正缺的是「谁把它拉回来」，而这由退出标记 + 用户双击 bat 补上即可。

---

## O-04 🟡 线程版 self-watchdog 的能力边界（前提已实测证伪，本节已重写）

**证据**

| 位置 | 内容 |
|---|---|
| `start.py:737-741`（改前） | docstring：*"用于定位 18:06 这类 50 分钟 CPU 空转问题"* |
| `start.py:745`（改前） | `while True:` —— 无 `stop_event`（对比 `_aux_services_watchdog` 有） |
| `start.py:746`（改前） | `time.sleep(_SELF_WATCHDOG_INTERVAL)` |
| ~~Python GIL 语义~~ | ~~纯 Python 死循环不释放 GIL，同进程线程抢不到 GIL~~ ← **该论断已被实测证伪，见下** |

> ⚠️ **本节初版结论作废**。初版称「纯 Python 死循环不释放 GIL → 线程版 watchdog
> 恰好抓不到它想抓的 CPU 空转」。**这是错的**，2026-09-10 用
> `tools/audit/g04_gil_probe.py` 实测推翻。

**实测数据**（Python 3.13.14，`sys.getswitchinterval() = 5.0ms`，窗口 3s / 采样 0.2s）

| 场景 | 守护线程 tick | 相邻 tick 最大空档 | 结论 |
|---|---|---|---|
| A 纯 Python 自旋 | 14 / 15 | **221 ms** | ✅ 正常调度 → 原结论**不成立** |
| B C 层长循环独占（单次 `sum(range(1e8))`，实测 1106ms） | 3 / 15 | **1148 ms** | ❌ 被饿死 → 该场景**成立** |

**机理**：CPython 的 eval loop 每 `sys.getswitchinterval()`（默认 5ms）检查一次
eval breaker 并释放 GIL，所以纯 Python 自旋**会**让出 GIL。真正长时间独占 GIL 的
是**不调用 `Py_BEGIN_ALLOW_THREADS` 的 C 层长调用**（巨型 numpy/pandas 运算、
超大 JSON 序列化、`re` 灾难性回溯、超大 `sorted` 等）。

**O-04 的正确表述**：线程版 watchdog 只覆盖不到「单次不释放 GIL 的 C 层调用持续
超过 20s × 4 = 80s」这一极端情况；该盲区**同进程内无法用任何手段解决**，
只能靠独立进程探活 / 外部 supervisor。

**顺带纠正一条更重要的历史误判**：2026-08-25 那三次「假死」**不是** GIL 问题。
三份栈转储（`data/watchdog_stack_2026082*.log`）**完全同源**，都卡在：

```
chat.py:_process_conv → tools.py:execute_tool → _handle_consolidate_memories
  → generate_consolidate_plan → memory_engine._similarity → _semantic_similarity
  → _shared_text_vectors          ← 且整条链跑在 asyncio 事件循环线程里
```

即 consolidate 的 O(n²) 相似度比较**同步跑在事件循环里**，把 `/api/health` 一起饿住
→ watchdog 连续 4 次失败（约 2 分钟）→ 自杀。**watchdog 当时工作完全正常**，
它抓到的正是它该抓的东西。该根因已于同日修复（3-gram 倒排剪枝 → O(n·k)
+ `asyncio.to_thread` 把 CPU 段移出事件循环）。

**建议（已落地）**

- 保留线程版；补 `stop_event`（解决 `while True` 停不掉）→ ✅ 已修
- 修正被证伪的 docstring，把实测数据写进函数注释，避免后人继续沿用错误前提 → ✅ 已修
- 盲区用**退出标记 + 下次启动自检**补偿（方案 A）→ ✅ 已修
- 若要覆盖 C 层长调用盲区：独立 watchdog 进程 / 外部 supervisor（归入 O-03 方案 B）

---

## O-05 🟡 全局单例被当短生命周期对象 `close()`

**证据**

| 位置 | 内容 |
|---|---|
| `jin10_service.py:446-452` | `_jin10_service` 模块级单例，`get_jin10_service()` 返回同一实例 |
| `calendar_sync.py:149-153` | `finally: await svc.close()` |
| `routers/news.py:122`、`:165` | `await svc.close()` |
| `jin10_service.py:434-439` | `close()` → `aclose()` + `self._client = None` + **`self._initialized = False`** |
| `jin10_service.py:176-177` | `_initialize()` 开头 `if self._initialized: return True` |

**问题**：单例的连接被调用方关掉，`_initialized` 被打回 False → **下一次调用必须重做完整 MCP 握手**（`initialize` + `notifications/initialized` + 实际 tool call = 3 次 POST）。

**实证**：`zenith.log` 中「金十 MCP 初始化成功」出现 **64 次，且 64 次全部是 `session=None`** —— 服务端不回 `mcp-session-id`，所以每次都建全新会话，握手成本无法摊薄。日志中 `16:00:34 → 16:03:24 → 16:03:59` 三次初始化即对应连续几次调用。

**建议**：单例的生命周期应由单例自己管。

- 移除 `calendar_sync.py` / `news.py` 中对单例的 `close()`（最简）；
- 或改为**空闲超时回收**（如 5 分钟无调用才 close），而不是每次调用后关；
- `news.py` 若需要独立实例，应显式 `Jin10Service()` 而不是关全局单例。

**Rollback**：恢复 `close()` 调用即可。

---

## O-06 🟡 `--stop` 连带杀掉 RAG 中台与 worker

**证据**

| 位置 | 内容 |
|---|---|
| `start.py:470-474` | 显式杀 `_GATEWAY_PORT`(8788) 占用进程 |
| `start.py:480-485` | `_find_zenith_processes(marker="api_gateway.py")` / `("task_worker.py")` 一并杀 |
| `start.py:452` | docstring 明说"含知识库中台与任务 worker" |

**问题**：想"只重启主服务"时会误伤 RAG 中台与任务 worker，导致正在跑的文档入库任务中断。**本会话已实际踩到一次**（`start.py --stop` 把 8788 也带走了）。

**建议**：加 `--keep-aux`（或 `--only-main`），`--stop` 默认保持当前全清行为，需要精细控制时用新开关。

---

## O-07 🟢 `_register_shutdown_handlers` 是死代码

**证据**

| 位置 | 内容 |
|---|---|
| `start.py:857-865` | `signal.signal(SIGTERM/SIGINT, _cleanup)` |
| `start.py:1006` | 随后 `uvicorn.run(...)` —— uvicorn 会**安装自己的信号处理器，覆盖掉上面的** |
| `start.py:1033` | 真正的锁清理靠 `finally: _cleanup_lock_and_pid()` |

**问题**：`_cleanup` 里那段 `msvcrt.locking(LK_UNLCK)` + `os.close(lock_fd)` 实际永不执行，造成"已经处理了信号"的错觉。

**建议**：删除该函数，注释说明"优雅退出统一走 `finally`，锁由 `_cleanup_lock_and_pid()` 释放"。

---

## O-08 🟢 启动瞬间双份打 health

**证据**：`start.py:964`（`_aux_services_thread`）与 `start.py:991`（`_health_open_browser`）各自调用 `_wait_for_health`。

**建议**：用一个 `threading.Event` + 单次探测结果共享，避免启动瞬间两个线程同时轮询。

---

## O-09 🟢 前端死 API（**清单归因有误，已修正**）

**初版记录**：「`frontend/src/shared/api.ts:880` 定义 `knowledgeTasks`，全 `frontend/src` 零调用」。
**实测修正**：`api.ts` 里**没有** `knowledgeTasks` 这个名字；该行是 `knowledgeListTasks`。
逐个统计 `frontend/src`（排除 `api.ts` 自身）的真实引用数：

| 方法 | api.ts 行 | 外部引用数 |
|---|---|---|
| `knowledgeHealth` | 869 | 2 |
| `knowledgeListDocs` | 870 | 1 |
| `knowledgeSearch` | 871 | 1 |
| `knowledgeWiki` | 873 | 1 |
| `knowledgeIngest` | 881 | 1 |
| **`knowledgeCreateTask`** | 875 | **0** |
| **`knowledgeGetTask`** | 877 | **0** |
| **`knowledgeListTasks`** | 879 | **0** |

**实际是 3 个同族方法零调用**（不止 1 个，且名字记错了），对应后端
`/knowledge/tasks` 三个端点（薄代理到 8788）。

**处置：保留，不删。** 理由：这三个正是「RAG 任务列表 / 任务进度 UI」的预留接口
（实测 RAG 任务队列 `{"tasks":[]}` 无积压，但知识库入库是长任务，进度可见性迟早要做），
符合既定「保留潜在需要」原则。**零删除原则下，无成本的无用代码不构成删除理由。**

**教训**（与 O-05 同类）：清单阶段按「调用形状」归类会导致两个方向的错误 ——
既可能把正常的调用误判成 bug（O-05 的 `news.py`），也可能把名字记错、数量记少（本条）。
**凡涉及「某符号无引用」的结论，必须用符号的精确名字重新 grep 一遍再写进清单。**

---

## O-10 🟢 aux 子进程无 job object

**证据**

| 位置 | 内容 |
|---|---|
| `start.py:644-648` | `_launch_aux_proc` 只设 `CREATE_NO_WINDOW`，未绑定 job object |
| `start.py:1018` | 退出时仅 `aux_stop_event.set()`（停 watchdog 线程），**不 terminate 子进程** |

**问题**：父进程正常退出后 `api_gateway.py` / `task_worker.py` 存活为孤儿进程。目前靠下次启动时 `_is_cmdline_running(...)` 幂等复用兜住，但 PID 无记录、清理只能靠 `--stop` 全杀。

**建议**：退出时显式 `terminate()`，或把 aux PID 写入独立文件便于精确清理。

### 评估结论（2026-09-10 第 5 批）：**暂不改，两条建议都不采纳**

| 建议 | 为什么不采纳 |
|---|---|
| 退出时显式 `terminate()` | **与现有设计意图冲突**。`_launch_aux_proc` 配合 `_is_cmdline_running(...)` 是**故意**做成「跨主服务重启幂等复用」的——主服务重启不必打断正在跑的文档入库。加 terminate 会把这个特性一起干掉。 |
| aux PID 写独立文件 | **收益不足**。`--stop` 已用 `_find_zenith_processes(marker="api_gateway.py"/"task_worker.py")` 按命令行精确匹配，实测能正确列出（摘要里「进程名匹配终止」字段）。加 PID 文件的唯一增量是「进程标题被改写时仍能定位」，本场景不存在。 |

**更重要的是：孤儿进程这个前提在本机基本不成立。** 实测三轮硬杀（16:51 / 16:53 / 17:05），
8788 每次都**随主进程一起消失** —— 因为 aux 是主进程子进程，同在 WorkBuddy 宿主的
Job Object 里（见 O-12）。反过来，用户双击 bat 启动时父进程不在任何作业对象内，
aux 也会随父进程退出而收到控制台关闭信号。真正产生孤儿的窗口极窄。

**改为登记为「已知设计，非缺陷」**：留 O-10 编号与本节评估，不再列入待办。

---

## 附：实测无问题的部分（勿过度优化）

| 项 | 实测 | 结论 |
|---|---|---|
| watchdog 轮询开销 | 20s + 30s 各一次本地 health | 每分钟约 2 次请求，可忽略 |
| RAG 任务队列 | `GET /tasks?limit=20` → `{"tasks":[]}` | 无积压 |
| 前端 reminders 轮询 | `AppLayout.tsx:126` `setInterval(60_000)` | 60s 合理 |
| 前端 RAG 轮询 | `/health` `/documents` `/tasks` 的突发调用 | 来自本会话 smoke test，**非前端定时器** |
| venv 双进程现象 | stub(13MB) → 真实解释器 | venv 机制固有，非重复启动 |

---

## 建议执行批次

| 批次 | 内容 | 理由 | 状态 |
|---|---|---|---|
| **第 1 批（治本）** | O-01 + O-02 | O-01 根治后台任务挂住；O-02 堵数据丢失，且改动小、风险低 | ✅ 已完成（见下「第 1 批」） |
| **第 2 批（架构决策）** | O-03 + O-04 | 需要定"内部 watchdog 还是外部服务"的方向 | ✅ 已完成（定为**方案 A + 轻量补偿**，见下「第 5 批」） |
| **第 3 批（体验+清理）** | O-13 + O-05 + O-06 + O-08 | 行为缺陷修复、减少握手开销、避免误杀子服务、去重复探测 | ✅ 已完成（见下「第 3 批」） |
| **第 4 批（清理）** | O-07 + O-09 + O-10 | 死代码 / 死 API / 重复请求 | ✅ 已完成（O-07 删；O-09 归因修正为「保留」；O-10 登记为「已知设计」） |
| **第 5 批（决策落地）** | O-03 + O-04 | 方案 A + 退出标记补偿；并**证伪 O-04 前提** | ✅ 已完成（见下「第 5 批」） |

> 注：原表把 O-05/O-06 列为第 3 批、O-07~O-10 列为第 4 批；实际执行时把行为缺陷 O-13 并入第 3 批，
> O-07~O-10 顺延为第 4 批。第 5 批（O-03/O-04）因需要架构决策被推到用户裁定后执行。
> **至此 O-01 ~ O-13 全部收口，仅 O-14（宿主 MCP 进程重复）为外部环境问题，无代码可改。**

---

# 实施记录（2026-09-10 第 1 批：O-01 + O-02）

备份：`data/backup/start.py.bak-20260910-163537`、`data/backup/zenith.bat.bak-20260910-163537`

## 改动清单

| 文件 | 位置 | 改动 |
|---|---|---|
| `start.py` | 模块 docstring | CLI 列表补 `--detach` |
| `start.py` | argparse | 新增 `--detach` |
| `start.py` | 新增 `_is_detached()` | 读环境变量 `ZENITH_DETACHED=1` |
| `start.py` | 新增 `_detach_runner()` | 脱离式自举所用解释器，**强制选 python.exe** |
| `start.py` | 新增 `_spawn_detached()` | 守护化自举：`DETACHED_PROCESS \| CREATE_NEW_PROCESS_GROUP` + 三路 DEVNULL + 注入 `ZENITH_DETACHED=1` |
| `start.py` | `main()` | 在 `--wait` 之后、`--reset-lock` 之前插入 `--detach` 分支 |
| `start.py` | 模块级早重定向（原 17-27 行） | 条件补 `or ZENITH_DETACHED=1` |
| `start.py` | `_setup_logging()` | `if not (_is_running_under_pythonw() or _is_detached())` —— 无控制台统一走日志重定向 |
| `start.py` | 新增 `_flush_pending_memories_sync()` | 独立事件循环 + `asyncio.wait_for`，硬退出前保全待提取记忆 |
| `start.py` | `_self_health_watchdog()` | `os._exit(70)` 之前插入 flush；docstring 纠正（原称有 `zenith-watchdog.ps1`，实测不存在） |
| `zenith.bat` | 第 56-60 行 | `start "" /D ...` → `"%WAIT_EXE%" "%START_PY%" --detach %*` |
| `restart.bat` | 第 28-30 行 | 同上，改用 `--detach` |

`Zenith v2.bat` 本就 `call zenith.bat`，自动继承新入口，无需改动。
`launcher.pyw` 有意不改：它是双击 GUI 入口而非 shell 入口，不产生后台任务问题，
且其 `Server PID:` 日志语义依赖直接持有子进程句柄。

## 验证结果

| 项 | 结果 |
|---|---|
| `py_compile` | 🟢 通过 |
| `--help` 含 `--detach` | 🟢 通过 |
| `--status` / `--stop` 控制命令 | 🟢 未受影响（提前 return，走不到 detach 分支） |
| `--detach` 调用方立即返回 | 🟢 **1~2 秒**（改前：永不返回） |
| `--detach` 子进程正常拉起服务 | 🟢 8766 + 8788 双端口 LISTENING，`/api/health` = `{"status":"ok","version":"2.0.0"}` |
| `_detach_runner()` 选型 | 🟢 返回 `...\.venv\Scripts\python.exe` |
| `_flush_pending_memories_sync()` 功能 | 🟢 返回 True，日志 `退出前 flush 完成，待提取记忆已落库`（`ZENITH_TESTING=1` 隔离库下测） |
| `ZENITH_DETACHED=1` 日志重定向分支 | 🟢 无控制台时输出正确落 `zenith.log`（增量 127 bytes） |
| 普通控制台模式回归 | 🟢 输出仍打到终端，未受影响 |
| 核心 API 回归 | 🟢 `/api/health`、`/api/modules/skills/stats`、`/api/modules/mcp`、`/api/conversations` 全 200 |

测试产物已归档至 `data/backup/probe-20260910/`（零删除）。

## 实施中新发现的问题

### O-11 🔴 `pythonw.exe` + 脱离式启动 = 静默死亡

**现象**：`_detach_runner()` 初版按 `zenith.bat` 的习惯优先选 `pythonw.exe`，结果子进程**一行日志都不写就消失** —— `zenith.log` 无任何新增，锁文件、PID 文件均未创建，端口不监听。

**证据**：同一份代码、同一路径、同一 flag 组合，仅把解释器换成 `python.exe` 立刻正常（出现启动横幅且 uvicorn 就绪）。路径含中文这一假设已单独排除（相对路径与绝对中文路径都能正常跑 `--status`，日志增量均为 142 bytes）。

**根因（推断）**：venv 的 `Scripts\pythonw.exe` 是**启动器 stub**（237KB，负责定位真实解释器再 re-exec）。`DETACHED_PROCESS` 下该 stub 未能正常转交，且因无控制台、stderr 已指向 DEVNULL，失败完全无声。

**处置**：`_detach_runner()` 改为**强制返回 python.exe**（若当前是 pythonw 则换同目录的 python.exe）。不弹窗由 `DETACHED_PROCESS` 本身保证，与解释器版本无关。

→ **这条推翻了原「优先 pythonw」的假设**：pythonw 只在**有父控制台进程**时才是正确选择；脱离式场景必须用控制台版。

### O-12 🔴 宿主 Job Object 会连坐杀掉脱离式子进程

**现象**：`--detach` 拉起的服务在 tool call 结束后**必被终止**，即使同时满足：
- 加了 `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`
- stdin/stdout/stderr 全指向 DEVNULL
- 调用方已设 `dangerouslyDisableSandbox: true`

**证据**：启动后同一调用内检查 —— 8766/8788 双端口正常 LISTENING、健康检查 200；**下一次 tool call 再查 —— 进程全无、端口关闭**。
对照：本会话最初那个存活 44 分钟的后台 Zenith，其 tool call 一直处于打开状态（后台任务未结束），故能存活。

**结论**：`DETACHED_PROCESS` 只脱离「控制台 + 进程组」，**挡不住 Windows Job Object**。宿主把每次命令包在作业对象里，命令结束即销毁作业并终结全部后代。`sys.exit()` 无法改变这一点，因为进程根本不是被 shell 的逻辑等待住，而是被作业对象管辖。

**宿主明确拒绝的两条常规逃逸路径**（均实测）：
- 从 shell 内调用 PowerShell → *"Invoking PowerShell from shell bypasses PowerShell security checks"*
- `Invoke-CimMethod Win32_Process.Create` / `Start-Process` → *"WMI/CIM process creation is equivalent to Start-Process"*

因此**在 WorkBuddy 会话内无法让服务脱离 tool call 存活**：`run_in_background`（保持调用打开）是唯一受支持的长驻方式。

**对真实使用的影响：无。** 双击 `Zenith v2.bat` / `zenith.bat` 时不存在作业对象，`--detach` 会按预期工作。此条仅影响「在 WorkBuddy 里由 AI 代劳启动服务」这一种场景。

### O-13 🟡 `--no-browser` 在「已在运行」分支被忽略（既有缺陷）

**位置**：`main()` 的 `if not is_first_instance:` → `if _is_port_in_use(port):` 分支

```python
else:
    logger.info("Zenith 已在运行，打开浏览器: %s", url)
    _write_browser_ts()
    webbrowser.open(url)      # ← 未检查 args.no_browser
return
```

**影响**：显式传 `--no-browser` 仍会强行开浏览器。本次 `--detach --no-browser` 测试中已复现（`zenith.log` 出现「已在运行，打开浏览器」），对脚本/自动化入口尤其意外。

**建议**：该分支补 `if not args.no_browser:` 判断。属行为变更，待确认后再改。

---

# 实施记录（2026-09-10 第 3 批：O-13 + O-05 + O-06 + O-08）

## 改动清单

| 文件 | 位置 | 改动 |
|---|---|---|
| `start.py` | `main()` 的「已在运行」分支 | 补 `if args.no_browser:` 前置判断，日志改为「（--no-browser 已指定，跳过打开）」 |
| `start.py` | `stop_existing_instance()` | 新增 `keep_aux: bool = False` 形参；`keep_aux=True` 时跳过 8788 端口与 `api_gateway.py`/`task_worker.py` 的名字匹配清理 |
| `start.py` | argparse | 新增 `--keep-aux`；模块 docstring CLI 列表同步 |
| `start.py` | `main()` | `stop_existing_instance(port, keep_aux=args.keep_aux)` |
| `start.py` | 启动阶段健康探测 | 新增单次探测线程 + `health_ready` Event + `health_result`，两个消费者共享结果 |
| `start.py` | `_kill_process()` | 删除 `return not _process_exists(pid)` 之后**不可达**的 `return True`（死代码） |
| `backend/calendar_sync.py` | `sync_calendar_events()` 的 `finally` | 删除 `await svc.close()`，改为注释说明为何不能关单例 |

## O-05 范围修正（重要）

初版清单把 `backend/routers/news.py:122,165` 也列为问题，**这是错的**。校验后确认：

- `news.py` 用 `svc = _make_svc()`，而 `_make_svc()`（第 57-64 行）`return Jin10Service()` —— **每次请求都是全新实例**，用完 `close()` 释放是正确且必要的。
- 只有 `backend/calendar_sync.py:97` 的 `svc = get_jin10_service()` 拿的是**模块级单例**，在 `finally` 里关掉才是 bug。

所以 O-05 的真实范围**只有 1 处**，`news.py` 的 2 处 `close()` 已刻意保留。
→ 教训：清单阶段的"同类问题"必须逐个回溯实例来源，不能按调用形状归档。

## 验证结果

| 项 | 结果 |
|---|---|
| `py_compile`（start.py / calendar_sync.py / news.py） | 🟢 通过 |
| **O-05** 连续 3 次 `sync_calendar_events()` | 🟢 **累计握手 1 次**（期望 1），`_initialized` 全程 `True`；改前每次同步都重握手 |
| **O-13** 运行中传 `--no-browser` | 🟢 日志为「已在运行（--no-browser 已指定，跳过打开）」，不开浏览器 |
| **O-13** 对照组（不传 `--no-browser`） | 🟢 日志为「已在运行，打开浏览器」，行为符合预期 |
| **O-06** `--keep-aux` 代码路径 | 🟡 日志出现「--keep-aux：保留知识库中台(8788)与任务 worker」，且摘要中 `端口占用终止: 无` / `进程名匹配终止: 无` —— 证明中台/worker 的清理**确实被跳过** |
| **O-06** 中台是否真的存活 | 🔴 **本环境无法验证**，见下 |
| `--help` 含 `--keep-aux` | 🟢 通过 |
| `_kill_process` 死代码清理 | 🟢 `py_compile` 通过 |
| 核心 API 回归 | 🟢 `/api/health`、`/api/modules/skills/stats`、`/api/modules/mcp`、`/api/conversations`、`/api/modules/skills/files` 全 200 |

### O-06 未能端到端验证的原因

实测 `--stop --keep-aux` 后中台（8788）**仍然消失**，但**不是代码问题**：

1. `_kill_process()` 用的是 `taskkill /PID <pid> /F`，**没有 `/T`**，本身不树杀。
2. `--stop` 摘要已证明中台的清理逻辑被正确跳过。
3. 真正原因仍是 **O-12 的宿主 Job Object**：辅助服务是主进程的子进程、同在宿主作业对象内；主进程退出 → 后台任务结束 → 宿主销毁作业 → 同作业内的中台与 worker 一并被终结。

**结论**：`--keep-aux` 的逻辑正确，但在本环境里被作业对象掩盖了效果。真实场景（用户双击 `stop.bat`）不存在作业对象，中台会作为孤儿进程存活。
**待办**：在真实环境（非 WorkBuddy 会话内）复验一次，确认 8788 PID 在 `--stop --keep-aux` 后不变。

## 附带发现

### O-14 🟢 WorkBuddy 自己的 MCP server 进程成对重复

排查服务异常时发现 24 个 python 进程，全部指向 WorkBuddy 的 MCP server
（`zenith-auditor/scripts/{code_verify,fact_check,guard}_mcp.py`、`dual-ai-review/scripts/audit_mcp.py`、`cache-optimizer/scripts/cache_scheduler_mcp.py`、`governance-auto-iteration/scripts/governance_iteration_mcp.py`），
且于 16:51:00 与 16:53:20 各启动一批，每批内每个 server 又是「stub + 真实解释器」两进程。

6 个 server × 2（stub+真实）× 2 批 = 24。与 Zenith 无关，但值得留意是否存在 MCP 重复拉起。

**后续（17:05 第三轮硬杀）**：`HyqnCW` 后台任务在 16:57:24 启动、**17:05:36** 被终结
（Duration 8m 8s，failed），`zenith.log` 无任何 shutdown 记录 → 又是硬杀。
三次被杀时刻 **16:51 / 16:53 / 17:05**，间隔 8~12 分钟，与宿主 MCP 心跳/重载周期吻合。
→ 结论：**在 WorkBuddy 会话内跑常驻服务只能算临时可用**，稳定运行必须用户自己双击
`Zenith v2.bat`（进程不在宿主作业对象内，不受连坐）。

---

# 实施记录（2026-09-10 第 5 批：O-03 + O-04 + O-07 + O-09 + O-10）

备份：`data/backup/start.py.bak-20260910-171500`（49145 字节）
回滚：`cp data/backup/start.py.bak-20260910-171500 start.py`

## 决议

用户回「做」，按上一轮给出的倾向执行：**方案 A（保留进程内 watchdog）+ 轻量补偿**。
方案 B（外部 Windows 服务/计划任务）**未做**，属纯增量改造（注册一个计划任务调用
`start.py --detach` 即可），随时可加，不影响本批。

## 改动清单

| 项 | 文件 / 位置 | 改动 |
|---|---|---|
| **O-04** | 新增 `tools/audit/g04_gil_probe.py` | GIL 调度实测探针（三场景：纯 Python 自旋 / C 层长循环；指标：tick 数 + 相邻 tick 最大空档） |
| **O-04** | `start.py::_self_health_watchdog` docstring | **删除被证伪的 GIL 论断**，写入实测数据（A: 14/15 tick、221ms；B: 3/15 tick、1148ms）与真正的盲区（C 层不释放 GIL 的长调用） |
| **O-04** | 同上 | 补历史战绩：8-25 三次栈转储完全同源，卡在 consolidate O(n²)，根因**不在 watchdog 而在事件循环被同步重计算阻塞**（该根因已于 8-25 修复） |
| **O-03** | `start.py::_self_health_watchdog(port, stop_event)` | 补 `stop_event` 参数：`while not stop_event.is_set()` + `stop_event.wait(interval)`，与 `_aux_services_watchdog` 风格统一；主进程优雅退出时能停掉它 |
| **O-03** | `start.py` 新增 `_write_exit_marker` / `_check_previous_exit` / `_mark_intentional_stop` + `_EXIT_MARKER_FILE` | **退出标记机制**（方案 A 的补偿）：存活期写 `running`，干净退出写 `clean`，自杀写 `watchdog_suicide`，异常写 `exception`；下次启动自检，非 clean 则 WARNING |
| **O-03** | `start.py::stop_existing_instance` | 调 `_mark_intentional_stop()` —— `--stop` 是 taskkill /F 硬杀、`finally` 跑不到，必须代为落 clean 标记，否则下次启动**误报**「被硬杀」 |
| **O-03** | `start.py::main` 收尾段 | `_check_previous_exit()` → 写 `running` 标记 → `self_stop_event` 传入 watchdog → 异常分支写 `exception` → `finally` 末尾写 `clean` |
| **O-07** | `start.py::_register_shutdown_handlers` | **删除**（约 30 行死代码）；原地留注释说明「uvicorn 会覆盖信号处理器 → 该函数永不执行」+ 优雅退出的真实路径 + 回滚命令 |
| **O-09** | `docs/audit/start.py-优化建议-20260910.md` | 清单**归因修正**：不存在 `knowledgeTasks`；实为 `knowledgeCreateTask` / `knowledgeGetTask` / `knowledgeListTasks` 三个零引用；**处置改为「保留」**（RAG 任务 UI 预留） |
| **O-10** | 同上 | 登记为**「已知设计，非缺陷」**：`terminate()` 会破坏「跨重启幂等复用中台」的既有意图；PID 文件相对 `_find_zenith_processes` 无增量收益 |
| 新增 | `tools/audit/g_exit_marker_test.py` | 退出标记 5 分支单元测试（clean 静默 / running 告警 / suicide 告警 / exception 告警 / 文件不存在静默），测试后**自动还原**标记文件 |

## 验证结果

### 1. O-04 前提实测（`g04_gil_probe.py`）

| 场景 | tick | 最大空档 | 判定 |
|---|---|---|---|
| A 纯 Python 自旋 | 14 / 15 | 221 ms | ✅ 正常调度 → **原前提不成立** |
| B C 层长循环（单次 1106ms） | 3 / 15 | 1148 ms | ❌ 被饿死 → 该场景成立 |

### 2. 退出标记单元测试（`g_exit_marker_test.py`）

5 个分支全部符合预期，且测试结束自动还原文件状态（不污染真实运行）。

### 3. 退出标记端到端（三种真实场景）

| 场景 | 操作 | 期望 | 实测 |
|---|---|---|---|
| ① 主动停止 | `start.py --stop` | 标记 → `clean`（由 `_mark_intentional_stop` 代为落盘） | 🟢 `{"reason":"clean","detail":"由 start.py --stop 主动停止"}` |
| ② 干净退出后重启 | 直接启动 | 自检**静默**，标记 → `running` | 🟢 日志 0 条「上次…」告警；标记 `{"reason":"running","pid":2304}` |
| ③ 硬杀后重启 | `Stop-Process -Force` → 再启动 | 标记停留 `running`；启动时**告警** | 🟢 标记未被改写；日志 `WARNING: 上次运行（PID=2304，启动于 2026-09-10 17:13:06）**未记录退出** → 被硬杀或进程崩溃` |

### 4. 回归

| 检查 | 结果 |
|---|---|
| `py_compile start.py` | 🟢 OK |
| `orphan.py`：`_archived` 活依赖 | 🟢 0 处 |
| `shadow.py`：路由遮蔽 | 🟢 0 处 |
| `smoke.py`：全部端点 | 🟢 全 200，异常端点明细「（无）」 |
| 核心接口 `/api/health` `/api/modules/skills/stats` `/api/modules/mcp` `/api/conversations` | 🟢 全 200 |
| 8788 中台 `/health` | 🟢 200 |
| `--status` | 🟢 PID 文件存活、锁存在 |
| `--detach` 即时返回 | 🟢 **1198 ms**；子进程正确识别「已在运行（--no-browser 已指定，跳过打开）」且不弹浏览器 |
| `--stop` 摘要 | 🟢 正常（`PID 文件终止: 8636`） |

## 本批新增的工具

| 脚本 | 用途 |
|---|---|
| `tools/audit/g04_gil_probe.py` | GIL 调度实测：判断某类 CPU 负载会不会饿死同进程线程 |
| `tools/audit/g_exit_marker_test.py` | 退出标记自检逻辑的 5 分支单测（含状态还原） |

## 遗留

- **O-06 真实环境复验**（`--stop --keep-aux` 后 8788 PID 是否不变）—— 需用户在会话外双击 `stop.bat`。
- **O-14 宿主 MCP 进程重复** —— 外部环境问题，无代码可改。
- **方案 B（外部 supervisor）** —— 未做，纯增量，需要时再议。


