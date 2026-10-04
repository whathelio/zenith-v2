# Archived Modules — 已封存模块

> ⚠️ **2026-09-10 修订**：`jin10_service.py` 已**移回** `backend/jin10_service.py`。
> 原封存理由写的是「依赖 market_analyzer」，实测**不成立** —— 该文件自包含（仅 stdlib + httpx + yaml），
> 且被 `calendar_sync.py`（财经日历同步，每日在跑）和 `routers/news.py` 活跃引用。
> 归档判断有误，已纠正。**归档不等于没人用，封存前必须做引用扫描。**

本目录现存的文件经全仓引用扫描确认：**无任何活代码 import**。

| 文件 | 用途 | 封存原因 |
|------|------|---------|
| `market_analyzer.py` | 每日黄金分析引擎 | LLM 分析成本高，用户决定暂停 |
| `cftc_service.py` | CFTC 持仓数据服务 | CFTC API 返回 403 |
| `macro_data.py` | 宏观数据聚合器 | 依赖 market_analyzer |
| `probe_jin10.py` | 金十连通性一次性探针 | **2026-09-11 归档**：`orphan.py` 报孤儿、零引用；且其 docstring 的前提已过时（称 jin10「已封存」，实际已移回并在跑） |
| `cftc_contracts.yaml` | CFTC 合约配置（原 `config/` 下） | **2026-09-11 归档**：全仓仅 `_archived/cftc_service.py` 读它，即**只被已归档代码引用** |

> ⚠️ **2026-09-11 第二次修订 —— B-15「死面清理」的范围被大幅收窄**
>
> 原计划把 `/api/market/*`(11) + `/api/mt5/*`(6) + `/api/academic/*`(6) 共 **23 条路由**
> 连同 `mt5_service.py`(466) / `academic_service.py`(591) 一并归档。**核实后推翻**：
>
> | 族 | 前端调用 | **LLM 工具数** | 真实性质 |
> |---|---:|---:|---|
> | `market` | 0 | **0** | ✅ 真死面（且早已主动封存，路由返回 410/空壳） |
> | `mt5` | 0 | **4**（`mt5_tick`/`mt5_rates`/`mt5_volume_profile`/`mt5_positions`） | ⚠️ **是模型可调用的能力**，不是死码 |
> | `academic` | 0 | **2**（`academic_search`/`paper_lookup`） | ⚠️ 同上 |
>
> **「前端零调用 + 数据表为空」≠「死代码」** —— 还有**第三条通道：LLM 工具**。
> 归档 mt5/academic 等于**砍掉模型的两项能力**，属产品决策，不可当清理做。
> 本次只归档了上面两处**经引用扫描确认无歧义**的项。

## 现状

- `market_analysis_enabled: false`，`/api/market/*` 中 CFTC 相关路由返回 HTTP 410 Gone
- 本目录保留为**潜在需要**，不删除
- 三者互相之间可能有依赖，若要恢复请整体移回 `backend/`

## 如需恢复 market 分析

1. 将 `market_analyzer.py` / `cftc_service.py` / `macro_data.py` 移回 `backend/`
2. 修正各自 import 路径（原为同目录相对导入，可直接用）
3. 设 `config.yaml` 的 `market_analysis_enabled: true`

## 检查本目录是否仍为死代码

```bash
.venv/Scripts/python.exe tools/audit/orphan.py
```

该脚本会把「从未被 import 的文件」列出来。本目录 3 个文件若出现在列表中，说明确实是死代码。
