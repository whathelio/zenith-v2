# 代码治理报告：PDF 入库 + 入库前审查

> 流程：`code-governance-workflow` 技能 7 步。

## 1. 范围与索引
- 目标：在 Zenith 前端上传本地 PDF，经审查后入向量库。
- 涉及文件：
  - `zotero_parse_rag_core.py`（新增 `review_pdf` / `ingest_pdf`）
  - `api_gateway.py`（新增 `POST /ingest`）
  - `zenith-v2/backend/knowledge_service.py`（新增 `ingest_pdf` 代理）
  - `zenith-v2/backend/app.py`（新增 `/api/knowledge/ingest`）
  - `zenith-v2/frontend/src/features/KnowledgeView.tsx`（上传按钮）
  - `zenith-v2/frontend/src/shared/api.ts`（`knowledgeIngest`）
- 不在范围：Zotero 同步、微信通道、批量目录入库。

## 2. 事实核查（READ ONLY）
- `zotero_parse_rag_core.py` 已有 `extract_text_with_fallback` / `split_text` / `get_store` / `get_embedder` / `load_progress` / `save_progress`。`[已核实事实]`
- `api_gateway.py` 已有鉴权与 `/tasks`，无上传端点。`[已核实事实]`
- `KnowledgeView.tsx` 只有问答，无上传。`[已核实事实]`
- `zenith_rag_tools.py` 的 `ingest_pdfs` handler 返回 todo，未实现。`[已核实事实]`
- Zenith 已有 `file_analyzer` 可做安全扫描，但在 backend 包内，独立脚本无法直接用。`[基于事实的推理]`

## 3. 承重风险表
| 风险 | 级别 | 后果 | 缓解 |
|------|------|------|------|
| 上传大文件撑爆内存 | 🟡 | 进程 OOM | 限制 20MB，流式保存 |
| 扫描件无文字入库 | 🟡 | 向量无效 | 覆盖率 <30% 且无 OCR 时 reject |
| 重复上传同文件 | 🟢 | 重复片段 | 用文件名+mtime 生成 item_id，upsert 覆盖 |
| 审查拖延入库 | 🟡 | 体验差 | 审查只做覆盖率+摘要，不跑完整治理 |
| api_gateway 未启动 | 🟢 | 502 | 前端显示离线提示 |

## 4. 最小安全第一刀
- 不改现有 Zotero 构建流程。
- 新增 `ingest_pdf(path)` 复用已有提取/分块/入库函数。
- 审查只做：文字覆盖率 + 来源 + 可选 LLM 一句摘要；不通过返回 `rejected`，不入库。
- 回滚：`git checkout` 各文件；删除新增端点即可。

## 5. 分阶段实施
| 阶段 | 改动 |
|------|------|
| A | `zotero_parse_rag_core.py` 加 `review_pdf` + `ingest_pdf` + `--ingest-pdf` CLI |
| B | `api_gateway.py` 加 `POST /ingest`（保存→审查→入库） |
| C | `knowledge_service.py` + `app.py` 加 `/api/knowledge/ingest` 代理 |
| D | `api.ts` + `KnowledgeView.tsx` 上传按钮 |

## 6. 验收标准
- 上传一个文字版 PDF → 返回 `status: ok`，`kb_stats` 片段数增加。
- 上传扫描件且无 OCR → 返回 `status: rejected`，原因 coverage 不足。
- 上传非 PDF → 400。
- 超 20MB → 413。
- 前端上传后显示结果（chunks 数 / 拒绝原因）。

## 7. 回滚预案
- `git checkout zotero_parse_rag_core.py api_gateway.py`
- `git checkout zenith-v2/backend/knowledge_service.py app.py`
- `git checkout zenith-v2/frontend/src/features/KnowledgeView.tsx shared/api.ts`
