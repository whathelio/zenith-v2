# Day 2 准备：API 中台最小契约 + 轻量任务队列

> 对应评审报告 §2.3 与 §4 两个 🔴 高风险缺失点。  
> 目标：在接微信/OpenClaw 之前，先给知识 API 穿上“认证 + 健康检查 + 异步兜底”三件护甲。

---

## 0. 为什么先做这个

直接硬接微信通道会撞上：
1. **API 无契约**：外部调用没有认证、错误码、健康检查，超时/滥用无法排查。
2. **无异步队列**：长回复超过微信 5–15 秒超时，消息丢失。

花半天补这两块，后续通道只需专注收发逻辑。

---

## 1. 产出文件

| 文件 | 作用 |
|------|------|
| `api_gateway.py` | FastAPI 中台：`/health` `/search` `/wiki` `/agent` `/tasks` |
| `task_queue.py` | SQLite 轻量任务队列：pending/processing/done/failed + stale 兜底 |

---

## 2. API 契约（最小版）

### 认证
- 所有业务端点需要头：`X-API-Key: <token>`
- `/health` 不需要认证，用于探活。

### 统一错误体
```json
{"error": "invalid or missing X-API-Key", "code": "HTTP_401"}
```

### 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 返回 `{"status":"ok"}` |
| POST | `/search` | 同步 RAG 问答，body: `{"question":"...","top_k":5}` |
| POST | `/wiki` | 同步 LLM Wiki 问答，body: `{"question":"..."}` |
| POST | `/agent` | Agent 占位，body: `{"message":"..."}` |
| POST | `/tasks` | 创建异步任务，body: `{"type":"search|wiki|agent","payload":{...}}` |
| GET | `/tasks/{id}` | 查询任务状态与结果 |
| GET | `/tasks?status=pending&limit=20` | 列出任务 |

### 任务状态
`pending → processing → done | failed`

---

## 3. 任务表结构（SQLite）

```sql
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,           -- search / wiki / agent
    payload TEXT NOT NULL,        -- JSON
    status TEXT NOT NULL DEFAULT 'pending',
    result TEXT,                  -- JSON 或错误串
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

- `processing` 超过 60 秒视为僵死，可被 worker 重新领取。
- `result` 存 JSON，`GET /tasks/{id}` 自动解析返回。

---

## 4. 运行

```bash
# 配置
export ZENITH_API_KEY="your-token"
export ZENITH_API_PORT=8788
export LLM_API_KEY="sk-..."          # /wiki 需要
export LLM_BASE_URL="https://api.deepseek.com/v1"
export LLM_MODEL="deepseek-v4-pro"

# 启动中台
.venv/Scripts/python.exe api_gateway.py
# 或
uvicorn api_gateway:app --host 0.0.0.0 --port 8788
```

---

## 5. 冒烟测试（已通过）

```bash
# 健康检查
curl http://127.0.0.1:8788/health
# => {"status":"ok","service":"zenith-knowledge-api","version":"0.1.0"}

# 无 key → 401
curl -X POST http://127.0.0.1:8788/tasks -H "Content-Type: application/json" -d '{"type":"wiki","payload":{"question":"test"}}'
# => 401

# 有 key → 创建任务
curl -X POST http://127.0.0.1:8788/tasks -H "X-API-Key: test-key" -H "Content-Type: application/json" -d '{"type":"wiki","payload":{"question":"test"}}'
# => {"task_id":"172d309be609","status":"pending"}

# 查询任务列表
curl "http://127.0.0.1:8788/tasks?limit=5" -H "X-API-Key: test-key"
```

---

## 6. 异步 worker（可选，接微信前再起）

`task_queue.py` 提供 `run_worker(handlers)`，可单独起一个进程：

```python
from task_queue import TaskQueue

q = TaskQueue("./zenith_rag/tasks.db")

def wiki_handler(payload):
    import llm_wiki_compiler as w
    # 捕获 stdout 返回
    ...

q.run_worker(handlers={"wiki": wiki_handler}, poll_interval=1.0)
```

微信通道收到长任务时：
1. `POST /tasks` 拿 `task_id`
2. 立刻回用户“⏳ 正在思考，稍后查询”
3. 轮询 `GET /tasks/{id}` 或 worker 完成后回调推送结果

---

## 7. 验收标准

- [x] `/health` 不需认证返回 ok
- [x] 业务端点无 key 返回 401
- [x] 有 key 能创建/查询任务
- [x] 统一错误体格式
- [ ] worker 能执行 wiki 任务并写回结果（接微信前补）

---

## 8. 后续

护甲穿好后，Day 2–3 接 OpenClaw 或 WeChatFerry 时：
- 机器人只调 `POST /search` 或 `POST /tasks`
- 长任务走异步，避免微信超时
- `/health` 用于通道存活检测
