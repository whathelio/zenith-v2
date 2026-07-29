# Archived Modules — 已封存模块

这些模块于 2026-07-29 从 `backend/` 归档至此。市场分析功能已完全封存（`market_analysis_enabled: false`）。

| 文件 | 用途 | 封存原因 |
|------|------|---------|
| `market_analyzer.py` | 每日黄金分析引擎 | LLM 分析成本高，用户决定暂停 |
| `jin10_service.py` | 金十数据 MCP 客户端 | 依赖 market_analyzer |
| `cftc_service.py` | CFTC 持仓数据服务 | CFTC API 返回 403 |
| `macro_data.py` | 宏观数据聚合器 | 依赖 market_analyzer |

## 如需恢复

将文件移回 `backend/` 并设置 `config.yaml` 中 `market_analysis_enabled: true`。
API 路由 (`/api/market/*`) 目前返回 HTTP 410 Gone。
