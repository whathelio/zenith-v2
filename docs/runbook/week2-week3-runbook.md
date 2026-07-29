# Week 2–3 运行手册：向量库抽象 + Agent 工具封装 + 任务 worker

> 对应评审 A/B/D 阶段。  
> 目标：把前面 Day1/Day4/Day2 的能力收口成“可被 DeepSeek Function Calling 调用”的工具，并补上异步 worker 执行器。

---

## 1. 产出文件

| 文件 | 作用 |
|------|------|
| `vector_store_abstraction.py` | `VectorStore` 接口 + Chroma 实现 + LEANN/Zvec/Cairn 桩 + 工厂 |
| `zenith_rag_tools.py` | 8 个 Agent 工具 schema + `handle_tool()` 执行入口 |
| `task_worker.py` | 异步任务 worker，消费 `task_queue` 任务 |
| `week2-vector-store-eval.md` | 向量库对比与迁移路径 |

---

## 2. VectorStore 抽象（Week2）

```python
from vector_store_abstraction import get_vector_store

store = get_vector_store("chroma")   # 默认
store.upsert(ids, embeddings, documents, metadatas)
res = store.query(embedding, top_k=5)
```

- 后端可切换：`chroma` / `leann` / `zvec` / `cairn`
- LEANN/Zvec/Cairn 目前是桩，按需实现
- 迁移原则：**保留 Chroma，不删除旧库**，见 `week2-vector-store-eval.md`

---

## 3. Agent 工具（Week3）

`zenith_rag_tools.py` 注册了 8 个工具：

| 工具 | 作用 |
|------|------|
| `retrieve_docs` | 本地 RAG 检索 |
| `sync_zotero` | Zotero Collection → 索引 |
| `ingest_pdfs` | 本地目录 PDF → 索引（待扩展） |
| `compile_wiki_page` | 原始资料 → LLM Wiki 页面 |
| `query_wiki` | 查询 LLM Wiki |
| `list_collections` | 列出 Zotero Collection |
| `kb_stats` | 知识库统计 |
| `deploy_rag_stack` | 拉取并部署 RAG 服务（白名单+确认） |

调用方式：
```python
from zenith_rag_tools import TOOLS_SCHEMA, handle_tool

# 把 TOOLS_SCHEMA 合并进 Zenith backend/tools.py 的工具列表
result = handle_tool("query_wiki", {"question": "Transformer 是什么？"})
```

`deploy_rag_stack` 安全设计：
- 白名单：`fastgpt / ragflow / maxkb`
- 不直接执行，返回 `pending_confirm`，需用户在聊天里回复“确认部署”

---

## 4. 任务 worker（补 §4）

```bash
# 冒烟（echo handler，不调 LLM/RAG）
python task_worker.py --dry-run --max-tasks 1

# 正式跑
export LLM_API_KEY="sk-..."
export LLM_BASE_URL="https://api.deepseek.com/v1"
export LLM_MODEL="deepseek-v4-pro"
python task_worker.py
```

worker 会：
- 轮询 `zenith_rag/tasks.db`
- 领取 `pending` 任务，状态置 `processing`
- 执行 handler（search / wiki / agent）
- 写回 `done` 或 `failed`
- 超过 60 秒的 `processing` 视为僵死，可重新领取

---

## 5. 冒烟测试结果

| 测试 | 结果 |
|------|------|
| `vector_store_abstraction` 导入 | ✅ |
| `zenith_rag_tools.py` 列出 8 个工具 | ✅ |
| `task_worker.py --dry-run --max-tasks 1` | ✅ 处理了 1 个 pending wiki 任务，状态 done |

---

## 6. 与 Zenith 集成方式（后续）

1. 把 `TOOLS_SCHEMA` 合并进 `zenith-v2/backend/tools.py`。
2. 在 `/api/chat` 里把 `handle_tool` 接入 Function Calling 循环。
3. 用 `task_worker.py` 作为后台进程，处理长任务。
4. `api_gateway.py` 暴露 `/tasks` 给微信通道。

---

## 7. 下一步

只剩 **Day 2–3 微信/OpenClaw 通道**（你要求最后做）。基础设施已全部就绪：
- 知识库：RAG + LLM Wiki
- 中台：认证 + 健康 + 异步任务
- 工具：8 个 schema 可被 LLM 调用
- 向量库：抽象层就位，可按需升级
