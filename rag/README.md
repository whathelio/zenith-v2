# rag/ — Zenith RAG 服务层快照

本目录是根工作区 RAG 上传链路脚本的**入库快照**，源文件位于 zenith-v2 仓库的上级工作区（本机 RAG 服务运行目录），
8788 网关继续以源位置运行；本目录仅用于版本管理 / 备份，不改变运行位置。

## 文件

| 文件 | 作用 |
| --- | --- |
| `api_gateway.py` | 8788 知识 API 网关，`/ingest` 上传入库端点 |
| `zotero_parse_rag_core.py` | 解析 / 分块 / 嵌入核心 |
| `task_queue.py` | SQLite 轻量异步任务队列 |
| `task_worker.py` | 任务消费 worker |
| `vector_store_abstraction.py` | 向量库抽象层（Chroma + 桩） |
| `import_shiji.py` | shiji-kb 章节 / 实体导入 |

## 脱敏约定

- 所有硬编码绝对路径已改为环境变量 + 相对路径默认值：
  - `ZENITH_RAG_WORK_DIR`（默认 `./zenith_rag_new`）
  - `ZENITH_RAG_EMBED_MODEL`
  - `ZENITH_UPLOAD_DIR`
  - `ZENITH_TASK_DB`
  - `ZENITH_API_KEY`
  - `SHIJI_KB_DIR`（默认 `./shiji-kb`）
  - `ZENITH_DB_PATH`（默认 `./data/zenith.db`）
- 不在本目录存放任何密钥 / token / 用户名路径。

## 运行（源位置不变）

源脚本仍在上级工作区继续运行，本快照不作为运行入口。
