# Zenith v2 — 本地智能助手

一个运行在本地的 AI 对话助手，支持流式对话、记忆引擎、日程管理、笔记系统、目标追踪、知识库（RAG）等功能。

[![Release](https://img.shields.io/github/v/release/whathelio/zenith-v2)](https://github.com/whathelio/zenith-v2/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> **只想用，不想折腾** → [下载最新发布包](https://github.com/whathelio/zenith-v2/releases/latest)，解压后双击 `install.bat`
> **内网 / 完全离线** → 额外下载 `zenith-v2-deps-win64-py313.zip`，用法见 [安装指南](INSTALL.md)
> **想改代码** → `git clone` 之后看 [安装指南](INSTALL.md) 的 C 节

完整步骤、依赖清单、排错对照表都在 **[INSTALL.md](INSTALL.md)**。

## 功能

- **流式对话** — DeepSeek / OpenAI 兼容 API
- **记忆引擎** — LLM 自动提取对话记忆 → 分类存储 → 后续注入上下文
- **日程管理** — AI 检测意图 → 创建/确认/取消/完成，支持批量操作
- **日历整合** — 周视图日历 + 事件 CRUD + 财经快捷模板
- **目标追踪** — 进度条 + 日化收益 + 在轨/偏离检测
- **笔记系统** — CRUD + AI 提议确认流程
- **知识库 (RAG)** — PDF 上传入库 + 向量检索 + LLM Wiki（需外部网关）
- **记忆/笔记/行程互转** — ⇄ 一键将任意类型转化为另一种
- **对话蒸馏** — 对话结束自动提取经验/决定/知识存入记忆库

## 快速开始

### 1. 一键安装

> **不需要 Node.js。** 前端已预构建在 `frontend/dist/`，普通用户只需要 Python 3.10+。

- **Windows** — 双击 `install.bat`
- **Linux / macOS** — `bash install.sh`

脚本做四件事：校验 Python ≥ 3.10 → 创建 `.venv` → 安装运行依赖（PyPI 失败自动回退阿里云镜像）→ 从模板生成 `config/config.yaml`。**幂等**，重复运行不会破坏已有环境。

### 2. 启动

- **Windows**：双击 `zenith.bat`
- **Linux / macOS**：`bash zenith.sh`

浏览器会自动打开 <http://127.0.0.1:8766>。

### 3. 填入 API Key

首次进入后到**设置**页填写 API Key 与 API Base，保存在本地 `config/config.yaml`：

| 服务 | API Base |
|---|---|
| DeepSeek | `https://api.deepseek.com/v1` |
| SiliconFlow | `https://api.siliconflow.cn/v1` |
| 自建 / 其他 | 任何 OpenAI 兼容接口 |

也可以跳过设置页，直接把 key 写进 `.env`（复制 `.env.example` 为 `.env`）：

```ini
ZENITH_LLM_API_KEY=sk-...
```

> `.env` 可以完全留空 —— 知识库网关密钥缺失时，启动会自动生成一次性会话密钥，不影响使用。

### 4. 手动安装（不想用脚本时）

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python start.py 8766
```

> 国内网络 PyPI 直连常失败，加镜像：
> `pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/`


### 5. 知识库 RAG（可选，默认不启用）

知识库（RAG 检索 + PDF 入库 + LLM Wiki）依赖**不在本仓库内**的外部网关进程。主功能（对话/日程/笔记/目标）完全不依赖它；没部署时 `/knowledge` 页面显示离线，其余一切照常。

需要的组件（自行放在仓库**同级目录**）：

- `api_gateway.py` — 知识库 API 中台（端口 8788）
- `task_worker.py` — 异步任务消费
- `zotero_parse_rag_core.py` — PDF 解析 + 向量化
- `bge-small-model/` — 本地 embedding 模型

`start.py` 会检测这些文件是否存在：存在就拉起并挂 watchdog，不存在就静默跳过。

> ⚠️ **部署要点（2026-08-25 排障沉淀）**：
> - `zotero_parse_rag_core.py` 的 Chroma 向量库（HNSW）**必须放在纯 ASCII 路径**，否则 chromadb 1.5 Rust HNSW reader 在中文路径下加载失败（报 `Error loading hnsw index`）。
> - embedding 模型默认回退同级 `bge-small-model/`，也可用 `ZENITH_RAG_EMBED_MODEL` 指定。
> - 参考副本见 `rag/zotero_parse_rag_core.py`。
> - `tools/zotero_parse_rag_core.py` 是**旧版单集合快照，已过时**（缺 collection 参数化 / 多集合），请勿据此重建环境。

### 6. 构建前端（仅改前端时需要）

前端已预构建在 `frontend/dist/`，跑起来不需要 Node.js。只有要改 UI 时才需要：

```bash
cd frontend
npm install
npm run build    # 产物直接覆盖 dist/
npm run dev      # 开发模式 (localhost:5173)
```

> 改了 `frontend/src/` 却忘了 `npm run build`，浏览器里看到的还是旧界面 —— 这是最常见的坑。


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
│   ├── _archived/           # 已封存模块（market_analyzer/jin10/cftc/macro）
│   ├── unified_distill.py  # 知识蒸馏模块
│   ├── confirm_flow.py     # 确认流程 + 教程
│   ├── schedule_reminder.py
│   ├── context_compressor.py
│   ├── file_analyzer.py
│   ├── config.py
│   ├── mt5_service.py      # MT5 桥接（仅 Windows）
├── frontend/
│   ├── src/                # React + TypeScript 源码
│   ├── dist/               # 构建产物（已包含）
│   └── package.json
├── install.bat             # Windows 一键安装（建 venv + 装依赖）
├── install.sh              # Linux/macOS 一键安装
├── start.py                # 入口脚本（跨平台单实例锁）
├── zenith.bat              # Windows 启动脚本
├── zenith.sh               # Linux/macOS 启动脚本
├── .env.example            # 环境变量模板（可选，全留空也能跑）
├── requirements.txt        # 运行依赖
├── requirements-dev.txt    # 开发依赖（pytest / pytest-cov / pytest-timeout / ruff）
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

- **技术栈**: Python FastAPI + SQLite + React + TypeScript + Vite
- **LLM**: DeepSeek v4 Pro（兼容 OpenAI Chat Completions API）

## 分发形态：源码 + 预构建前端

本项目**不提供** PyInstaller 单文件 exe，只发源码：

| 你想做什么 | 需要装什么 | 怎么做 |
|---|---|---|
| 直接使用 | 只要 Python 3.10+ | `install.bat` → `zenith.bat` |
| 改界面 | + Node.js 18+ | 改 `frontend/src/` → `npm run build` |
| 改后端 | 只要 Python | 改 `backend/*.py` → 重启 |

**为什么不做 exe**：FastAPI + uvicorn 的动态路由、`data/` 与 `config/` 的相对路径解析，打包进单文件后要逐个打补丁；体积从 11MB 涨到 200MB+；Windows 下 Defender / 火绒 对 PyInstaller 产物误报率极高。一键安装脚本的体验已经接近了，代价却小得多。

## 数据与隐私

- 所有数据（对话、记忆、笔记、日程、目标）都在本地 `data/zenith.db`（SQLite），**不上传任何服务器**
- `.env`、`config/config.yaml`、`data/*.db`、`data/*.jsonl` 均已 gitignore，不会被提交
- 只有调用 LLM 时，你的 prompt 才会发给你自己配置的那家 API 服务商
- ⚠️ 内置的代码执行功能（`/api/code/run`）**不是沙箱**，默认关闭。多用户 / 公网部署必须先做容器隔离，详见 [`SECURITY.md`](SECURITY.md)

## License

MIT
