# Zenith v2 复核工具箱

2026-09-10 核心功能复核时编写，用于**可复现**地复查下列维度。

运行方式（在仓库根目录）：

```bash
.venv/Scripts/python.exe tools/audit/routes.py    # 路由清单与归属统计
.venv/Scripts/python.exe tools/audit/api.py       # 前后端接口对账（断链 / 僵尸）
.venv/Scripts/python.exe tools/audit/shadow.py    # 路由遮蔽检测
.venv/Scripts/python.exe tools/audit/orphan.py    # 孤儿模块扫描 + router 挂载检查
.venv/Scripts/python.exe tools/audit/db.py        # 全表行数 + 时间列最新值 + 空表
.venv/Scripts/python.exe tools/audit/smoke.py     # 核心端点冒烟（需服务在 8766 运行）
.venv/Scripts/python.exe tools/audit/g04_gil_probe.py      # GIL 调度实测（判断哪类 CPU 负载会饿死同进程线程）
.venv/Scripts/python.exe tools/audit/g_exit_marker_test.py # 退出标记自检 5 分支单测（自动还原）
```

| 脚本 | 检查什么 | 判读标准 |
|---|---|---|
| `routes.py` | 每个后端文件的路由数、模块归属 | 路由暴涨说明单体在膨胀 |
| `api.py` | A 段＝前端调了后端没有（断链）；B 段＝后端有前端不调 | **A 段必须为空** |
| `shadow.py` | 静态路由被先前声明的 `{param}` 路由吞掉 | 输出必须为空，否则对应接口恒 4xx |
| `orphan.py` | 无任何 import 的文件；router 是否都挂载 | router 必须全部"已挂载" |
| `db.py` | 表行数、最新时间、空表、>30 天未更新 | 空表要能解释（未启用 / 死模块） |
| `smoke.py` | 核心端点实测 HTTP | 除既定 410（已封存）外应全 200 |
| `g04_gil_probe.py` | 同进程守护线程在有 CPU 负载时能否被调度 | A 段最大空档应 ≈ 采样间隔；B 段出现 ≈ 单次 C 调用耗时的空档即为盲区 |
| `g_exit_marker_test.py` | `start.py` 退出标记的 4 种 reason 是否被正确识别 | clean/不存在→静默；running/suicide/exception→告警 |
| `latency.py` | 各端点真实延迟（httpx 持久连接） | p50 个位数毫秒 = 本地代码非瓶颈；**不要用 `curl -w`**，Windows 上混入约 100ms 进程启动开销会误判 |
| `cost.py` | 读 `cache_stats` 算 token 结构与成本占比 | 命中率（命中/输入）与「命中输入的成本占比」是**两个数字**，别混（实测 62.46% vs 1.9%） |
| `llm_throughput.py` | LLM 真实输出吞吐（发最小请求，耗极少量 token） | 用于算「LLM 生成占单轮时延的比例」，判断本地优化的天花板 |
| **`duplicate_routes.py`** | **同一 (方法, 路径) 被多个文件注册** | **必须 0 组**。后注册者永不执行 —— 改它的代码不生效。`shadow.py` 查不出这类 |
| **`health_matrix.py`** | 核心功能**可用性**矩阵（三态：✅可用 / ⚠️可用但空 / 🔴不可用） | 比 `smoke.py` 深一层：不只看 HTTP 200，还判响应数据是否**有效**（形状/字段/非空） |
| **`gov_final.py`** | **收尾门禁**：编译 + 静态断言 + 事件循环 + start.py 修复 + 重复路由 + 基线 + 端到端 + pytest | **32 项全过才放行**；退出码可入 CI。`--no-e2e` / `--no-pytest` 可跳过需服务的部分 |

> `g04_gil_probe.py` 的由来：`start.py::_self_health_watchdog` 的 docstring 曾断言
> 「纯 Python 死循环不释放 GIL，同进程线程抢不到 GIL」。实测**证伪**该论断
> （纯 Python 自旋 14/15 tick、最大空档 221ms）。该脚本是这条结论的可复现依据，
> 用于回答「某类 CPU 负载到底会不会饿死同进程 watchdog 线程」。

## 注意事项

> ⚠️ **各脚本的检查盲区（重要 —— 「工具报 0」不等于「没问题」）**
>
> | 脚本 | 查得到 | **查不到** |
> |---|---|---|
> | `shadow.py` | 静态路径被**先声明的动态路径**吞掉（`/a/b` 被 `/a/{x}` 遮蔽） | **两个文件注册完全相同路径** ← 这正是 `duplicate_routes.py` 补的缺口 |
> | `orphan.py` | 无 import 的文件 | 同名模块互相掩盖；字符串/动态 import 引用 |
> | `api.py` | 前后端路径对账 | **变量拼接的 URL**；且会把 api.ts 的「定义」当「调用」（B 段偏小） |
> | `smoke.py` | HTTP 状态码 | 响应**数据是否有效**（200 但空 / 字段缺失都算过） ← `health_matrix.py` 补 |
>
> 结论：**任何"报 0 / 报无"的结论都要抽查反证**，并确认该脚本的检测范围覆盖了你要问的问题。

- `api.py` 的提取器按**静态前缀**匹配（遇 `$`、`?`、引号即停），因此嵌套模板字符串不会污染结果；但用变量拼接的 URL 会被漏掉，B 段结论需人工抽查。
- `orphan.py` 按模块名末段建立引用集，**同名模块会互相掩盖**；它的价值在发现"完全没人 import"的文件，不适用于同名冲突场景。
- `db.py` 里 DB 路径写死为项目相对路径，移动仓库后需同步修改。
- `smoke.py` 只打 GET，不改数据。跑之前确认服务已启动。

## 历史结论与修复状态

2026-09-10 复核发现的问题与处置（完整记录见 `docs/audit/Zenith-v2-核心功能复核报告-20260910.md`）：

| 编号 | 问题 | 状态 |
|---|---|---|
| F-01 | `backend/_archived/` 是活依赖（`calendar_sync.py:14` 顶层 import） | ✅ 已修：`jin10_service.py` 移回 `backend/`，现 0 活依赖 |
| F-02 | MCP 路径错位 → MCP 桥静默为空 | ✅ 已修：加回退链 + 告警；配置改 `~/.workbuddy/mcp.json` |
| F-03 | 3 条静态路由被 `/skills/{skill_id}` 遮蔽 | ✅ 已修：上移到动态路由之前 |
| F-04 | `skills_dir` 指向空目录 → 技能扫描恒 0 | ✅ 已修：加回退链，现扫到 4 个技能 |
| F-05 | `goal_del` 不清理 `schedules.goal_id` | ✅ 已修：先置 NULL 再删 |
| F-07 | `created_at` 为 NULL 导致日期蒸馏漏数据 | ✅ 已修：改 `COALESCE(created_at, recorded_at)` |
| F-08 | `/reports/latest` 返回 `Error:` 坏记录 | ✅ 已修：查询侧过滤（原数据保留） |
| F-09 | 12 条 `created_at` 垃圾值 | ✅ 已修：用 `recorded_at` 回填 |
| — | MCP `mt5-terminal` 实际不可达 | ⚠️ 遗留：**HTTP 401 token 失效**，需从桥应用重发 |

当前三条硬性基线（每次复核都应满足）：`orphan.py` 的 `_archived` 活依赖 = 0、`shadow.py` = 0 处、`smoke.py` = 全 200。
