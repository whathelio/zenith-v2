# Week 2：向量库抽象与升级评估

> 对应评审报告 A 阶段：向量库迁移路径与抽象层缺失。  
> 目标：先定义 `VectorStore` 接口，保留 Chroma 实现，再按需适配 LEANN/Zvec/Cairn，**绝不删除已有 Chroma 实现**。

---

## 1. 抽象接口（已实现）

文件：`vector_store_abstraction.py`

```python
class VectorStore(Protocol):
    def upsert(self, ids, embeddings, documents, metadatas) -> None
    def query(self, embedding, top_k) -> dict
    def delete_by_item(self, item_id) -> None
    def count(self) -> int
```

工厂：
```python
store = get_vector_store("chroma")   # 默认
store = get_vector_store("leann")    # 桩，待实现
store = get_vector_store("zvec")     # 桩，待实现
store = get_vector_store("cairn")    # 桩，待实现
```

上层（`zotero_parse_rag_core.py`、`api_gateway.py`、Agent 工具）只依赖接口，切换后端只需改工厂参数。

---

## 2. 后端对比

| 后端 | 状态 | 优势 | 劣势 | 何时升级 |
|------|------|------|------|----------|
| **Chroma** | ✅ 默认 | 成熟、Python 原生、API 简单 | 全量存 embedding，量大占磁盘 | 万级以下不急 |
| **LEANN** | 🟡 桩 | 97% 存储节省、本地百万级、MCP 原生 | 较新，无原生 Windows，需 Rust | 磁盘吃紧时 |
| **Zvec** | 🟡 桩 | 类 SQLite 嵌入式、混合检索、Windows 支持 | 中文案例少 | 需要生产级嵌入体验时 |
| **Cairn** | 🟡 桩 | SQLite+sqlite-vec、来源追踪、OCR、MCP | Rust 构建，PII 检测未确认 | 隐私敏感场景 |

---

## 3. 迁移路径（最小安全）

1. **保留 ChromaStore**，所有上层调用改走 `get_vector_store()`。
2. 新增 `LeannStore` / `ZvecStore` / `CairnStore` 实现，逐步补全接口。
3. 加配置项 `ZENITH_RAG_VECTOR_BACKEND`，默认 `chroma`。
4. 迁移数据时：
   - 从 Chroma 导出 `(ids, documents, metadatas)`；
   - 用新后端的 embedder 重新生成 embedding（或导出原 embedding）；
   - 写入新库；
   - 验证 query 结果一致后切换；
   - **保留旧 Chroma 库 7 天**作为回滚。
5. 回滚：把 `ZENITH_RAG_VECTOR_BACKEND` 改回 `chroma`。

---

## 4. 适配工作量预估

| 后端 | 工作量 | 主要工作 |
|------|--------|----------|
| LEANN | 1–2 天 | 装 Rust/uv，调 `leann` CLI 或 SDK，实现 upsert/query |
| Zvec | 1 天 | `pip install zvec`，实现 schema+insert+query |
| Cairn | 2–3 天 | cargo 构建，调 CLI/MCP，适配 OCR/来源追踪 |

---

## 5. 决策建议

- **现在**：不迁移，只用抽象层。Chroma 够用。
- **当索引 > 1GB 或文档 > 5 万**：评估 LEANN。
- **当需要混合检索/Windows 嵌入**：评估 Zvec。
- **当用户群体对隐私敏感**：评估 Cairn + Presidio。

---

## 6. 验收标准

- [x] `VectorStore` Protocol 定义
- [x] `ChromaStore` 实现
- [x] `LeannStore/ZvecStore/CairnStore` 桩
- [x] `get_vector_store()` 工厂
- [ ] 上层脚本改用工厂（`zotero_parse_rag_core.py` 已有自己的 ChromaStore，后续可统一替换）
