# Zenith 接入 DeepSeek + RAG 知识库落地分析

> 目标：把 Zenith v2 从“临时网页问答”升级为“DeepSeek 驱动 + 可私有化部署的 RAG 知识库”，把 PDF 书本/文档变成可检索、可溯源的知识库内容，显著降低幻觉。

---

## 1. 现状速览

- **Zenith v2 已经是最佳起点**：FastAPI + React + SQLite，`config.py` 已默认接入 `deepseek-ai/DeepSeek-V3`（通过 SiliconFlow 的 OpenAI 兼容接口）。
- **缺少的拼图**：向量数据库、Embedding 模型、PDF 解析、RAG 检索、引用溯源。
- **你的优势**：本地运行、数据私有、可扩展工具链（`tools.py` + `app.py`）。

---

## 2. 核心思路：先让“外部 RAG 服务”跑起来，再接到 Zenith 里

你提到“让智能体帮你拉下来部署”，最符合直觉的落地顺序是：

1. **选一个开箱即用的开源 RAG 知识库框架**，用 Docker Compose 在本地跑起来。
2. **配置 DeepSeek 作为模型源**（复用 SiliconFlow 或 DeepSeek 官方 API）。
3. **把 PDF 书本批量灌进去**，自动解析、切块、向量化。
4. **Zenith 通过 API 调用这个知识库**，把检索结果注入对话上下文，并显示引用来源。
5. **后续再把“拉取-部署-灌数据”封装成 Zenith 的一个工具/技能**，让智能体一键完成。

---

## 3. 开源框架对比与选型建议

| 框架 | 定位 | 部署难度 | PDF 解析能力 | 资源占用 | 与 DeepSeek 兼容 | 推荐指数 |
|------|------|----------|--------------|----------|------------------|----------|
| **FastGPT** | 轻量对话 + 知识库 | 低（Docker Compose） | 中等（支持 PDF/Word/Markdown） | 低（4–8 GB 内存可跑） | 支持 OpenAI 兼容接口 | ⭐⭐⭐⭐ 首选 MVP |
| **RAGFlow** | 深度文档理解 + 企业级 RAG | 中（Docker Compose，含 ES/MySQL/Redis） | 很强（版面还原、表格、扫描件 OCR） | 高（建议 16 GB+） | 支持 OpenAI 兼容接口 | ⭐⭐⭐⭐ 质量优先 |
| **MaxKB** | 私有化知识库问答 | 低（Docker） | 中等（支持 OCR） | 中 | 多模型接口 | ⭐⭐⭐ |
| **Dify** | 完整 AI 应用开发平台 | 中 | 中（依赖外部解析） | 中高 | 支持 | ⭐⭐⭐ 重编排 |
| **LightRAG** | 极简库级 RAG，图增强 | 极低（pip install） | 弱（需自己处理 PDF） | 低 | 代码配置 | ⭐⭐ 适合二次开发 |
| **WeKnora** | 腾讯开源 RAG 知识库 | 中 | 较强 | 中 | 支持 | ⭐⭐ 生态较新 |
| **Open WebUI** | ChatGPT 式本地 UI | 低 | 中（自带 RAG） | 中 | 支持 | ⭐⭐ 纯聊天可用 |

**推荐组合**：
- **快速验证**：`FastGPT` — 5 分钟内把知识库跑起来，先验证端到端流程。
- **质量升级**：如果书本里表格、公式、扫描页多，换 `RAGFlow` 或给 FastGPT 外挂 `MinerU` 做 PDF 解析。
- **深度整合**：最终把“检索能力”收回到 Zenith 后端，用 `LangChain` / `LlamaIndex` 直连 `Chroma/Qdrant` + 本地 Embedding，这是最干净但开发量最大的方案。

---

## 4. 推荐落地路径（分阶段）

### Phase 0：1 小时内跑通最小可用知识库

1. 安装 Docker Desktop（如未安装）。
2. 让 Zenith 的 agent 拉取并启动 FastGPT：
   ```bash
   git clone https://github.com/labring/FastGPT.git
   cd FastGPT/projects/app
   cp .env.template .env
   # 编辑 .env：填入 DeepSeek/SiliconFlow 的 API_BASE 和 API_KEY
   docker compose up -d
   ```
3. 访问 `http://localhost:3000` 创建账号 → 新建知识库 → 上传 1 本测试 PDF → 开始解析。
4. 在 FastGPT 对话里选 DeepSeek 模型，提问测试引用效果。

### Phase 1：与 Zenith 后端对接（1–2 天）

在 `backend/` 新增一个 `knowledge_service.py`：
- 封装 FastGPT / RAGFlow 的 HTTP API：创建知识库、上传文件、发起对话、检索片段。
- 暴露给 `app.py` 的新端点，例如 `/api/knowledge/bases`、`/api/knowledge/upload`、`/api/knowledge/chat`。
- 在 `/api/chat` 中，当用户问题命中“知识库”意图时，先调用检索接口拿到 Top-K 片段，再把片段塞进 `llm_client.chat_stream` 的 system/user 提示词里，要求模型只依据片段回答并标注引用。

示例注入模板：
```text
下面是从知识库中检索到的相关片段，请仅依据这些内容回答，并引用来源 [doc:...] [page:...]。
如果片段中没有答案，请明确说明“知识库中未找到相关内容”。

---
{retrieved_chunks}
---

用户问题：{user_question}
```

### Phase 2：前端知识库页面（2–3 天）

在 React 前端新增 `KnowledgeView.tsx`：
- 左侧：知识库列表 + 上传 PDF/Word 按钮 + 解析进度。
- 右侧：对话窗口，显示“基于知识库回答”和每条回复的引用来源卡片。
- 复用现有 `ChatView` 的 SSE 组件，新接口 `/api/knowledge/chat` 即可复用流式渲染。

### Phase 3：把“拉取-部署-灌数据”封装成 Zenith 技能（3–5 天）

在 `backend/tools.py` 加一个 `deploy_rag_stack` 工具：
- 接收 GitHub repo 地址（默认 FastGPT 或 RAGFlow）。
- 执行 `git clone` + `docker compose up -d`（或调用 `subprocess` 安全沙箱）。
- 写入 `.env` 配置：DeepSeek API base/key、模型名、Embedding 模型。
- 返回部署状态、访问地址、健康检查 URL。
- **安全**：必须要求用户确认，且只能拉取可信仓库；Docker 运行权限需要由用户预授权。

### Phase 4：把 RAG 引擎内嵌回 Zenith（可选，长期）

如果外部服务耦合让你不舒服，可以替换为本地内嵌方案：
- `vector_store`: Chroma（最简单）或 Qdrant（高性能）。
- `embedding`: `BAAI/bge-small-zh-v1.5` 或 `bge-m3`（多语言/跨语言好）。
- `pdf_parser`: `MinerU` / `marker` / `PyMuPDF`。
- `rerank`: `bge-reranker-v2-m3` 或 Cohere API。
- `framework`: `LangChain` 或 `LlamaIndex` 做管道编排。
- 数据存在本地 `data/vector_store/`，完全离线。

---

## 5. 关键技术选型

| 环节 | 推荐方案 | 备选 |
|------|----------|------|
| 大模型 | DeepSeek-V3 / DeepSeek-R1（通过 SiliconFlow 或官方 API） | 本地 Ollama + DeepSeek-R1-Distill-Qwen |
| Embedding | `BAAI/bge-m3`（多语言，支持长文本） | `text-embedding-v2`（阿里 DashScope） |
| 向量库 | Chroma（起步）→ Qdrant（规模上去） | FAISS、Milvus |
| PDF 解析 | FastGPT/RAGFlow 内置 → MinerU（高质量结构化） | PyMuPDF + marker |
| 检索增强 | 向量检索 + 关键词重排 + 重排序模型 | 混合检索（BM25 + 向量） |
| 引用展示 | 片段原文 + 文档名 + 页码 | 高亮原文位置 |

---

## 6. 幻觉能被消灭吗？

**不能 100% 消灭，但能大幅降低。**

RAG 解决的是“知识来源”问题：
- 模型回答必须基于检索到的片段，而不是训练参数里的陈旧知识。
- 可以显示“引用来源”，让你人工校验。

但仍有以下风险点：
- **检索失败**：问题没在知识库里，或 Embedding 没召回相关内容。
- **片段理解错误**：模型把片段信息张冠李戴。
- **PDF 解析错误**：扫描页 OCR 错、表格被拆乱、公式丢失。
- **长上下文污染**：塞进去太多不相关片段，模型被干扰。

**建议**：在提示词里强制加“如果找不到就说不知道”，并对关键答案要求显示引用原文。

---

## 7. 资源与成本估算

| 场景 | 硬件 | 说明 |
|------|------|------|
| 仅使用 API 版 DeepSeek + FastGPT | 8 GB 内存 / 4 核 CPU | Docker 容器本身轻量，推理走云端 |
| 本地 Embedding 模型 | 额外 2–4 GB 内存 | `bge-small` 级别可用 CPU 跑 |
| 本地运行 DeepSeek-R1-Distill-Qwen 14B | 16 GB+ 内存 / 有 GPU 更好 | 量大、慢，但完全离线 |
| RAGFlow 全栈 | 16 GB+ 内存 / SSD 100 GB+ | 含 Elasticsearch、MySQL、MinIO |

API 费用：DeepSeek 和 SiliconFlow 的 Token 费用都很低，Embedding 如果自己跑则几乎免费。

---

## 8. 风险与注意事项

1. **GitHub 自动拉取有风险**：不要把任意仓库的 `docker compose up` 直接交给 agent 执行。应限定在固定白名单仓库（FastGPT/RAGFlow 官方），并在沙箱中运行，用户确认后再执行。
2. **PDF 版权**：把购买的书籍 PDF 做本地知识库自用没问题，但别上传到公开云服务或分享出去。
3. **Docker 网络**：Windows 下 Docker 网络偶尔不稳定，建议用 WSL2 后端。
4. **向量数据备份**： embedding 和 chunk 重建费时，定期备份 `vector_store/` 或容器卷。
5. **不要删掉现有记忆系统**：RAG 是“外部知识库”，Zenith 的 memory/notes 是“个人动态记忆”，二者互补。

---

## 9. 下一步建议（按优先级）

1. **先跑一个 FastGPT 最小Demo**：把 1 本测试 PDF 灌进去，确认 DeepSeek 回答能引用原文。
2. **确定 PDF 质量需求**：如果是纯文字书，FastGPT 够用；如果含大量图表/扫描页，直接上 RAGFlow 或 MinerU。
3. **在 Zenith 里加 `/api/knowledge/*` 端点**：先实现“上传 + 对话 + 引用”闭环。
4. **再封装 `deploy_rag_stack` 工具**：让 agent 帮你拉仓库、改配置、起容器。
5. **评估长期是否内嵌**：如果数据量大、对延迟敏感，再考虑把 LangChain + Chroma 直接写进 Zenith 后端。

---

## 10. 一句话总结

> **现阶段最稳的落地方式：在本地 Docker 里跑 FastGPT（或 RAGFlow）作为独立知识库引擎，DeepSeek 做生成模型，Zenith 负责编排和对话 UI；先把 PDF 灌进去验证“有源可溯”，再逐步把部署流程收进 Zenith 的智能体工具链。**
