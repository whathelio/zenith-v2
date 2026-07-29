# Zenith 集成与 UI 优化评审

> 三个问题：①如何把 RAG/Wiki 模块有效链接进 Zenith；②原版 Zenith UI 是否需要优化；③主页目标追踪 UI 是否逻辑紊乱。  
> 评审基于 `zenith-v2/frontend/src` 实际代码。

---

## 1. 如何有效组合链接 RAG/Wiki 模块

### 现状
我们这几天做的 6 个脚本都是**独立进程**，没有接进 Zenith：
```
zotero_parse_rag_core.py   # RAG 检索
llm_wiki_compiler.py       # LLM Wiki
api_gateway.py             # HTTP 中台（8788）
task_worker.py             # 异步 worker
zenith_rag_tools.py        # Function Calling schema
vector_store_abstraction.py
```

### 推荐方案：薄代理 + 前端页 + 工具注入（低耦合）

```
Zenith 前端                Zenith 后端(FastAPI)              外部进程
┌──────────────┐   /api/knowledge/*   ┌──────────────────┐   HTTP    ┌────────────┐
│ KnowledgeView│ ───────────────────► │ knowledge_service│ ───────► │ api_gateway│
│ ChatView     │ │ Function Calling   │ (薄代理，~50行)   │          │ (8788)     │
└──────────────┘                     └──────────────────┘          └──────┬─────┘
                                                                        │
                                                          ┌─────────────┼─────────────┐
                                                          ▼             ▼             ▼
                                                   zotero_parse    llm_wiki     task_worker
```

#### 后端集成（1 天）
1. 新建 `zenith-v2/backend/knowledge_service.py`：
   - `search(question)` → 调 `http://localhost:8788/search`
   - `wiki_query(question)` → 调 `/wiki`
   - `create_task(type,payload)` → `/tasks`
   - `get_task(id)` → `/tasks/{id}`
2. 在 `app.py` 加 4 个端点：`/api/knowledge/search`、`/api/knowledge/wiki`、`/api/knowledge/tasks`、`/api/knowledge/tasks/{id}`
3. 把 `zenith_rag_tools.py` 的 `TOOLS_SCHEMA` 合并进 `tools.py`，让 `/api/chat` 的 Function Calling 能调用 `retrieve_docs / compile_wiki_page / query_wiki`。

#### 前端集成（1 天）
4. 新增 `features/KnowledgeView.tsx`：
   - 上传 PDF / 选择 Zotero Collection
   - 对话框 + 引用卡片
   - 复用 ChatView 的 SSE 组件
5. `App.tsx` 加路由 `/knowledge`，`AppLayout` 导航加“📚 知识库”入口。

#### 运行时
6. `api_gateway.py` + `task_worker.py` 作为**常驻后台进程**（可注册为 Windows 服务或用 `start.py` 拉起）。
7. Zenith 后端只做薄代理，不直接依赖 chromadb/sentence-transformers，避免污染 Zenith venv。

### 为什么不直接合并进 Zenith 后端
- RAG 依赖 torch/chromadb，体积大，会让 Zenith 启动变慢。
- 外部进程崩溃不影响 Zenith 主服务。
- 符合评审报告“能力外包”原则。

---

## 2. 原版 Zenith UI 是否需要优化

### 结论：需要，但不是推倒重来，是“拆分 + 去重”。

### 主要问题

| 问题 | 证据 | 影响 |
|------|------|------|
| `AppLayout.tsx` 过大 | 777 行，承担布局+日历+目标+CRUD+映射计算 | 难维护，改一处怕动全身 |
| `CalendarView.tsx` 过大 | 1079 行，日历+日程+目标面板+表单全塞一起 | 同上 |
| 目标状态重复 | `AppLayout` 有 `goals/goalStats/goalDateMap/goalAmountMap/goalActiveMap`；`CalendarView` 又有 `fullGoals/goalStats`；`GoalsView` 再加载一次 | 三处各拉一次接口，更新后易不一致 |
| 路由碎片 | `/schedules`→`/calendar`、`/notes`→`/library`、`/memories`→`/library`、`/skills`→`/library` | 重定向多，心智负担 |
| 缺少知识库入口 | 目前无 `/knowledge` | RAG 无处落地 |

### 建议优化（按优先级）
1. **目标状态统一到 Context**：`CalendarGoalContext` 已存在，但 `CalendarView` 没用它，自己又建了一套。让所有页面消费 Context，不再各自 `api.listGoals()`。
2. **拆组件**：
   - `AppLayout` 拆出 `<GoalSidebar>`、`<CalendarSidebar>`
   - `CalendarView` 拆出 `<GoalTrackerPanel>`（已有）、`<ScheduleFormModal>`、`<WeekStrip>`
3. **加 `/knowledge` 路由**。
4. **路由清理**：保留 `/calendar`、`/library`、`/goals`、`/chat`、`/knowledge`、`/settings`，减少重定向。

---

## 3. 主页目标追踪 UI 是否逻辑紊乱

### 结论：**是的，当前确实紊乱**。不是 bug，是“同一份数据在三个地方用三种方式展示”，用户心智模型不一致。

### 证据

| 位置 | 文件 | 做了什么 |
|------|------|----------|
| 左侧面板 | `AppLayout.tsx` | 目标卡片（极简进度条）+ 显示字段切换（现金额/日化/目标额）+ 月历激活点 |
| 主日历页 | `CalendarView.tsx` | `GoalTrackerPanel`：目标网格 + 标记今日 + 内联编辑 + 当日激活列表 |
| 目标页 | `GoalsView.tsx` | 目标列表 + 详情弹窗 + 编辑/删除 |
| 详情弹窗 | `GoalDetailModal.tsx` | 百尺式月历激活 + 余额更新 + 编辑 |

问题：
1. **三处各自 `loadGoals()`**：`AppLayout` L132、`CalendarView` L178、`GoalsView` L32。更新一个目标后，另两处不会自动刷新，除非手动 `loadGoals()` 或切页面。
2. **显示字段切换只在左侧面板生效**：`goalDisplayField` 在 `AppLayout` 定义，`CalendarView` 的 `GoalTrackerPanel` 用的是另一套字段展示，用户切换后主日历无变化。
3. **激活日期操作入口分散**：
   - 左侧月概览：只显示点，不能操作
   - 主日历：`GoalTrackerPanel` 可“标记今日”
   - 详情弹窗：`GoalDetailModal` 可点任意日期切换
   用户不知道去哪改激活日。
4. **“未激活日期不显示”规则只在主日历生效**（`CalendarView` L94-97），左侧月概览仍按 `goalDateMap` 显示，两套逻辑。

### 推荐收敛方案（最小改动）
- **单一数据源**：所有目标状态走 `CalendarGoalContext`，删除 `CalendarView` 和 `GoalsView` 里的本地 `goals/stats` state。
- **单一操作入口**：激活日期只保留一个地方——推荐 `GoalDetailModal`（已有完整月历）。左侧面板和主日历只做**展示**，点击统一打开 `GoalDetailModal`。
- **显示字段统一**：`goalDisplayField` 放进 Context，左侧面板和 `GoalTrackerPanel` 都消费它。
- **左侧面板职责**：只显示目标列表 + 进度，不做日期操作。
- **主日历职责**：只显示当日激活目标的小圆点，点击打开详情弹窗。

---

## 4. 一句话总结

> 集成 RAG 用“薄代理 + 前端页 + 工具注入”最稳；Zenith UI 需要拆分大文件和去重目标状态；目标追踪 UI 当前确实紊乱，根因是“三处各拉数据 + 操作入口分散”，收敛到 Context + 单一详情弹窗即可理顺。

---

## 5. 建议执行顺序

1. **先收敛目标状态**（半天）：把 `CalendarView`/`GoalsView` 的本地 goal state 改用 `CalendarGoalContext`。
2. **再拆 AppLayout/CalendarView**（半天）：抽出 `<GoalSidebar>` 和 `<ScheduleFormModal>`。
3. **再接知识库**（1 天）：加 `knowledge_service.py` + `/api/knowledge/*` + `KnowledgeView.tsx`。
4. **最后接微信通道**。

这样 UI 先理顺，知识库接入时不会被旧 UI 拖累。
