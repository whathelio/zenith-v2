# Zenith v2 — 本地智能助手

一个运行在本地的 AI 对话助手，支持流式对话、记忆引擎、日程管理、笔记系统、目标追踪、知识库（RAG）等功能。

## 功能

- **流式对话** — SiliconFlow / OpenAI 兼容 API
- **记忆引擎** — LLM 自动提取对话记忆 → 分类存储 → 后续注入上下文
- **日程管理** — AI 检测意图 → 创建/确认/取消/完成，支持批量操作
- **日历整合** — 周视图日历 + 事件 CRUD + 财经快捷模板
- **目标追踪** — 进度条 + 日化收益 + 在轨/偏离检测
- **笔记系统** — CRUD + AI 提议确认流程
- **知识库 (RAG)** — PDF 上传入库 + 向量检索 + LLM Wiki（需外部网关）
- **记忆/笔记/行程互转** — ⇄ 一键将任意类型转化为另一种
- **对话蒸馏** — 对话结束自动提取经验/决定/知识存入记忆库

## 快速开始

### 1. 环境要求

- Python 3.10+
- Node.js 18+（如需重新构建前端）
- 支持 Windows / Linux / macOS

### 2. 安装依赖

```bash
cd zenith-v2
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# 或 .venv\Scripts\activate  # Windows
pip install -r requirements.txt
```

### 3. 配置 API Key

启动后首次访问会自动打开设置页面，填写你的 API Key 和 API Base URL。

默认兼容 SiliconFlow (https://api.siliconflow.cn)，也支持任何 OpenAI 兼容接口。

### 4. 启动

**Windows**:
```bash
zenith.bat          # 一键启动（含知识库网关 + worker）
# 或
python start.py 8766
```

**Linux / macOS**:
```bash
bash zenith.sh      # 一键启动（含知识库网关 + worker）
# 或
python start.py 8766
```

访问 http://localhost:8766

### 5. 知识库功能（可选）

知识库（RAG 检索 + PDF 入库 + LLM Wiki）依赖外部网关进程。`zenith.bat` / `zenith.sh` 会自动尝试启动（若脚本存在于上级目录）：

- `api_gateway.py` — 知识库 API 中台（端口 8788）
- `task_worker.py` — 异步任务消费
- `zotero_parse_rag_core.py` — PDF 解析 + 向量化
- `bge-small-model/` — 本地 embedding 模型

若未部署这些外部脚本，Zenith 主功能（对话/日程/笔记/目标）不受影响，仅 `/knowledge` 页面显示离线。

### 6. 构建前端（可选）

前端已预构建在 `frontend/dist/`，如需修改前端：

```bash
cd frontend
npm install
npm run build    # 构建
npm run dev      # 开发模式 (localhost:5173)
```

## 项目结构

```
zenith-v2/
├── backend/
│   ├── app.py              # FastAPI 主应用
│   ├── database.py         # SQLite 数据层
│   ├── llm_client.py       # LLM 流式调用
│   ├── tools.py            # Function Calling 工具
│   ├── memory_engine.py    # 记忆提取引擎
│   ├── knowledge_service.py # 知识库薄代理
│   ├── market_analyzer.py  # 市场分析引擎
│   ├── unified_distill.py  # 知识蒸馏模块
│   ├── confirm_flow.py     # 确认流程 + 教程
│   ├── schedule_reminder.py
│   ├── context_compressor.py
│   ├── file_analyzer.py
│   ├── config.py
│   ├── cftc_service.py     # CFTC 数据服务
│   ├── macro_data.py       # 宏观数据服务
│   └── mt5_service.py      # MT5 桥接（仅 Windows）
├── frontend/
│   ├── src/                # React + TypeScript 源码
│   ├── dist/               # 构建产物（已包含）
│   └── package.json
├── start.py                # 入口脚本（跨平台单实例锁）
├── zenith.bat              # Windows 启动脚本
├── zenith.sh               # Linux/macOS 启动脚本
├── requirements.txt
└── README.md
```

## 跨平台说明

| 功能 | Windows | Linux | macOS |
|------|---------|-------|-------|
| 对话/日程/笔记/目标 | ✅ | ✅ | ✅ |
| 知识库 RAG | ✅ | ✅（需外部脚本） | ✅（需外部脚本） |
| MT5 实时行情 | ✅ | ❌（优雅降级） | ❌（优雅降级） |
| 单实例锁 | 命名互斥量 | fcntl 文件锁 | fcntl 文件锁 |
| 桌面快捷方式 | ✅ | 需手动创建 | 需手动创建 |

## 技术栈

- **后端**: Python FastAPI + SQLite (peewee ORM)
- **前端**: React + TypeScript + Vite
- **设计**: Dracula 主题 (Shinsekai 设计系统)
- **LLM**: 兼容 OpenAI Chat Completions API

## License

MIT
