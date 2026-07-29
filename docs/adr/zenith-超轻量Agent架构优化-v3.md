# Zenith 超轻量 Agent 架构优化分析（v3）

> 在前两版的基础上，这一版从**架构轻量化、能力外包、数据隐私**三个维度进一步收敛，目标是把 Zenith 从“能跑的代码”变成“个人日常真正顺手”的本地 AI 助理。核心原则：**能本地的不跑服务，能外包的不重复造轮子，能换小的不用大的。**

---

## 1. 架构轻量化：告别“重”依赖

### 1.1 向量数据库：Chroma 之外，还有更极致的选择

| 方案 | 定位 | 优势 | 劣势 | 适用场景 |
|------|------|------|------|----------|
| **Chroma** | 嵌入式向量库 | 生态成熟、API 简单、Python 原生 | 存储全量 embedding，量大后吃磁盘 | 快速 MVP、已有 LangChain 生态 |
| **LEANN** | 轻量个人向量索引 | 宣称 97% 存储节省、本地百万级文档、MCP 原生 | 较新（2025–2026），平台支持有限（macOS/Ubuntu/WSL），需 Rust 构建 | 磁盘敏感、想“笔记本跑大库” |
| **Zvec** | 阿里巴巴嵌入式向量库 | 类 SQLite 体验、毫秒级十亿级、Windows 支持、混合检索 | 中文文档较新，社区案例相对少 | 追求“嵌入即 SQLite”的开发者 |
| **FAISS** | 纯索引库 | 快、省内存 | 无持久化/CRUD，需要额外工程 | 只想要索引，不需要数据库能力 |

**推荐**：
- 如果 Zenith 现在已经在用 Chroma，先别急着迁。LEANN/Zvec 的优势在“量大后省存储”，但在万级文档以下差距不明显。
- 当你发现 Chroma 索引占了几百 MB 甚至 GB 时，再评估 LEANN（节省磁盘）或 Zvec（生产级嵌入体验）。
- 两者都支持 MCP，未来可以作为外部服务被 Claude Code / OpenClaw 直接调用。

### 1.2 RAG 框架极简化

| 方案 | 特点 | 适用场景 |
|------|------|----------|
| **自研函数 `retrieve_docs`** | 自己封装 Chroma + Sentence-Transformers，完全可控 | 已在 v2 推荐，Agent 即核心 |
| **ragkitpy** | 5 行代码跑 RAG，完全本地化，无 API Key | 初学者、快速验证一个文件 |
| **rag-from-zero (MisterBooo)** | 手写核心、无 LangChain，有完整教程 | 想彻底理解 RAG 每个模块 |
| **rag-framework / PyRAGFromZero** | 不依赖框架、模块化 | 想定制文档解析/检索策略 |

**建议**：
- **不要**把 ragkitpy 这类“玩具级”包直接当生产依赖。它适合验证概念，但分块、重排、引用、OCR 回退都需要自己补。
- **推荐**把 `rag-from-zero` 作为学习材料，然后提取其中自己需要的模块（chunker、retriever、vector_store）放进 Zenith 后端。
- 最终形态仍是：Zenith 内部一个 `rag_service.py` 小模块，而不是依赖一个完整的 RAG 框架。

### 1.3 “无向量数据库”新范式：LLM Wiki

Andrej Karpathy 提出的 LLM Wiki 核心理念：
- **原始资料**放在 `raw/` 目录。
- **LLM 把原始资料编译成结构化 Markdown wiki**（含摘要、实体页、交叉链接、索引）。
- 查询时 LLM 读 `index.md` + 相关页面，无需向量数据库。

**优势**：
- 零基础设施，一个文件夹即可。
- 完全人类可读、可审计、可编辑。
- 知识会“生长”：每次摄入都更新相关页面，而不是堆 chunk。

**局限**：
- 适合 ** curated 知识**（读书笔记、项目文档、研究综述），不适合 **海量原始 PDF**（如 Zotero 上千篇论文）。
- 受 LLM 上下文限制，Karpathy 自己测试在 ~100 篇文章/40 万字规模有效；更大规模仍需检索层（如 qmd）。
- 需要 LLM 主动维护，不是“丢进去就完事”。

**对 Zenith 的意义**：
- 把 LLM Wiki 作为 Zenith 的**“上层知识层”**：Zotero/本地 PDF 用 RAG 粗检索，重要主题再让 LLM 编译成 wiki 页面。
- 这样既有“快搜全库”，又有“精读专题”。

---

## 2. 能力外包：把“脏活累活”交给专业服务

### 2.1 大模型与嵌入

| 能力 | 推荐 | 备选 |
|------|------|------|
| **推理大脑** | DeepSeek API（SiliconFlow / 官方 / 无问芯穹） | 本地 Ollama + Qwen2.5 / Llama 3.2 |
| **Embedding** | 本地 `BAAI/bge-small-zh-v1.5`（免费、隐私） | 无问芯穹 GenStudio 嵌入 API（当前免费/低价） |

**说明**：
- DeepSeek 仍然是 2026 年性价比最高的“大脑”。
- Embedding 建议优先本地。本地 bge-small 在 CPU 上很快，且避免把文档内容发上网。如果本地 GPU 受限或不想装模型，再用无问芯穹 API。
- 无问芯穹的“当前嵌入 API 免费/低价”是平台补贴，不是长期承诺，别把它当默认。

### 2.2 消息通道

| 方案 | 优劣 | 建议 |
|------|------|------|
| **OpenClaw API / 微信 ClawBot** | 官方插件、稳定、私聊可用；但受微信版本/地区限制 | 优先尝试。如果个人微信可用，这是风险最低的通道 |
| **WeChatFerry / WeChaty** | 功能全、灵活 | 小号 + 低频率，作为 OpenClaw 不可用的 fallback |
| **LangBot** | 多平台统一接入 | 如果你同时要 QQ + 微信，可以用它作为转发层 |

**建议**：
- 把 OpenClaw 当作“外包”的消息通道中台：Zenith 只需要调用 OpenClaw API/CLI，不用自己维护微信协议。
- 如果 OpenClaw 在你的环境不可用，再退回到 WeChatFerry + 自写 Flask 转发接口。

### 2.3 私有知识 API 中台

未来可以把 Zenith 的本地知识封装成一个**私有知识 API 中台**：
- `/search`：向量检索
- `/wiki`：LLM Wiki 页面查询
- `/ingest`： ingestion 入口
- `/agent`： Agent 执行入口

这样微信/QQ/OpenClaw/Claude Code/浏览器扩展都是前端，Zenith 是统一后端。

---

## 3. 数据隐私本地化：你的数据你做主

### 3.1 基础原则

- **原始文档只存在本地**：Zotero 的 PDF、本地书籍、笔记，绝不默认上传。
- **向量索引只存在本地**：`chroma_db/`、`zvec_data/`、`.cairn/` 都放在本地。
- **API Key 不暴露**：`.env` 由用户自己管理，Zenith 只读取。

### 3.2 本地模型（可选）

| 场景 | 方案 |
|------|------|
| 对隐私极度敏感 | Ollama + Qwen2.5:7B / Llama 3.2 |
| 无网络环境 | 本地 Embedding + 本地 LLM |
| 成本优先 | 本地 Embedding + DeepSeek API |

**注意**：本地 7B 模型在复杂推理上仍弱于 DeepSeek-V3，建议作为“离线兜底”或“隐私模式”。

### 3.3 隐私优先系统：Cairn

Cairn（github.com/lamb356/cairn）是一个 Rust 写的本地优先知识库：
- 基于 SQLite + `sqlite-vec` 做混合检索。
- 支持 OCR 导入（PDF/图片）。
- 内置来源追踪、矛盾检测、PII 检测。
- 提供 MCP Server，Agent 可直接调用。

**对 Zenith 的意义**：
- 可以作为 Zenith 的**“隐私知识底座”**，替代 Chroma。
- 如果 Zenith 用户群体扩大到对隐私敏感的人群（律师、医生、研究员），Cairn 是更合适的默认存储。
- 代价：Rust 构建门槛，Windows 需要配置环境。

---

## 4. 优化后的 Zenith 架构（v3）

```
                    ┌──────────────┐
                    │  移动端入口   │
                    │ 微信/QQ/PC  │
                    └──────┬───────┘
                           │
              ┌────────────▼────────────┐
              │   消息通道外包层         │
              │ OpenClaw / WeChatFerry  │
              │    / LangBot            │
              └────────────┬────────────┘
                           │
           ┌───────────────▼────────────────┐
           │      Zenith Agent Core          │
           │  FastAPI + 工具系统 + 记忆引擎   │
           │  - 日程/笔记/目标/记忆           │
           │  - Zotero 同步                  │
           │  - RAG 检索（Chroma/LEANN/Zvec） │
           │  - LLM Wiki 编译                 │
           │  - 异步任务队列（Celery/RQ）      │
           └───────┬─────────────┬─────────┘
                   │             │
       ┌───────────▼──┐  ┌───────▼─────────┐
       │  DeepSeek API │  │ 本地知识层       │
       │  （大脑）      │  │ Chroma/LEANN/   │
       │              │  │ Zvec/Cairn +    │
       │              │  │ Zotero sqlite   │
       └──────────────┘  └─────────────────┘
```

### 关键变化

- **向量库默认**：Chroma；**升级选项**：LEANN/Zvec/Cairn。
- **RAG 层**：Zenith 内部自研小模块，不依赖外部 Docker。
- **知识层**：RAG 负责粗检索，LLM Wiki 负责精编专题。
- **消息通道**：优先 OpenClaw 外包，fallback 到自部署机器人。
- **大脑**：默认 DeepSeek API，隐私模式切 Ollama 本地模型。

---

## 5. 技术选型决策矩阵

| 你关心什么 | 推荐方案 |
|------------|----------|
| 最快速度跑通 | Chroma + bge-small + DeepSeek API |
| 磁盘最小 | LEANN（按需重计算） |
| 生产级嵌入体验 | Zvec |
| 隐私最严 | Cairn + Ollama 本地模型 |
| 不想维护微信机器人 | OpenClaw API |
| 追求知识“生长” | LLM Wiki 上层 |
| 处理扫描件/公式 | MinerU/PaddleOCR 回退 |

---

## 6. 调整后的 MVP 路线图（v3）

| 阶段 | 目标 | 技术栈 | 产出 |
|------|------|--------|------|
| **Day 1** | 本地 Zotero → RAG 闭环 | `zotero_parse_rag_core.py`（Chroma + bge-small + DeepSeek） | 终端可问答并溯源 |
| **Day 2–3** | 消息通道外包 | OpenClaw API 或 WeChatFerry | 手机能问 Zenith |
| **Day 4** | 知识“生长”实验 | 选 1 个重要主题，让 LLM 生成 LLM Wiki 页面 | 得到一篇可读的 Markdown 专题 |
| **Week 2** | 替换/升级向量库 | 评估 LEANN/Zvec/Cairn，按需替换 Chroma | 存储更小或隐私更强 |
| **Week 3** | 打磨 Agent 工具 | 把“同步 Zotero”“编译 wiki”“部署服务”封装成 tools | 微信一句话触发 |

---

## 7. 风险与权衡

| 选择 | 风险 | 何时避免 |
|------|------|----------|
| LEANN | 项目较新，平台支持有限 | Windows 原生环境、不想折腾 Rust 构建 |
| Zvec | 中文社区案例少 | 需要大量 Python 生态集成 |
| LLM Wiki | 不适合大规模原始文献 | Zotero 上千篇论文时，不能替代 RAG |
| OpenClaw | 受微信版本、账号、地区限制 | 无法使用 ClawBot 插件时 |
| 无问芯穹 Embedding | 平台补贴可能变化 | 追求长期免费/隐私 |
| Cairn | Rust 构建门槛 | 不熟悉 Rust/Windows 开发环境 |

---

## 8. 一句话总结

> **最顺手的 Zenith 应该是：Zenith Agent Core 做大脑和调度，DeepSeek 做推理，本地 Chroma（或 LEANN/Zvec/Cairn）做记忆，Zotero 做文献源，LLM Wiki 做专题知识，OpenClaw 做消息通道。轻量本地是默认，重型服务和大模型 API 都是可选项。先把 Day 1 的 Zotero 本地闭环跑起来，再按需升级，永远避免“为了用重工具而买重硬件”。**
