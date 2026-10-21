# rag/ — Zenith RAG 服务层快照

本目录是根工作区 RAG 上传链路脚本的**入库快照**，源文件位于 zenith-v2 仓库的上级工作区（本机 RAG 服务运行目录），
8788 网关继续以源位置运行；本目录仅用于版本管理 / 备份，不改变运行位置。

## 文件

| 文件 | 作用 |
| --- | --- |
| `api_gateway.py` | 8788 知识 API 网关（v0.2.0）：`/ingest`（含 `/ingest/batch`、`/ingest/local`）、`/search`、`/documents`、`/collections` |
| `zotero_parse_rag_core.py` | 解析 / 分块 / 嵌入核心 |
| `task_queue.py` | SQLite 轻量异步任务队列 |
| `task_worker.py` | 任务消费 worker |
| `vector_store_abstraction.py` | 向量库抽象层（Chroma + 桩） |
| `import_shiji.py` | shiji-kb 章节 / 实体导入 |

## 脱敏约定

- 所有硬编码绝对路径已改为环境变量 + 相对路径默认值：
  - `ZENITH_RAG_WORK_DIR`（默认：Windows 为纯 ASCII 工作目录，其他平台 `./zenith_rag`）
  - `ZENITH_RAG_EMBED_MODEL`
  - `ZENITH_UPLOAD_DIR`
  - `ZENITH_TASK_DB`
  - `ZENITH_API_KEY`
  - `SHIJI_KB_DIR`（默认 `./shiji-kb`）
  - `ZENITH_DB_PATH`（默认 `./data/zenith.db`）
  - `ZENITH_RAG_COLLECTION`（默认集合名，未设 = `zotero_papers`）
  - `ZENITH_RAG_MIN_SCORE_MAP`（按集合覆盖检索地板值，JSON；未设 = 全局 `0.53`）
  - `ZENITH_INGEST_ROOTS`（本地路径入库白名单，未设 = 该能力整体禁用）
- 不在本目录存放任何密钥 / token / 用户名路径。

## 运行（源位置不变）

源脚本仍在上级工作区继续运行，本快照不作为运行入口。

## 多集合（2026-10-08 改造）

- 向量集合名不再硬编码：默认集合由 `ZENITH_RAG_COLLECTION` 决定，未设时仍为 `zotero_papers`。
- 所有入库 / 检索入口均接受显式 `collection` 参数；网关对应 `/search`、`/documents`、`/ingest` 的 `collection` 查询参数。
- 集合管理：`GET /collections` 列出集合与默认集合；`POST /collections/{name}/archive` 归档（= 改名保留，**不物理删除**；默认集合拒绝，返回 400 `PROTECTED`）。
- 检索地板值可按集合覆盖（`ZENITH_RAG_MIN_SCORE_MAP`）；未配置 / 未命中时回落全局 `0.53`。
