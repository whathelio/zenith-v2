"""
Zenith VectorStore 抽象层

对应评审报告 A 阶段：向量库迁移路径与抽象层缺失。
目标：上层（RAG、Agent 工具）只依赖 VectorStore 接口，
后端可在 Chroma / LEANN / Zvec / Cairn 之间切换，不改业务代码。

当前实现：
- ChromaStore：完整可用（基于 chromadb）
- LeannStore / ZvecStore / CairnStore：桩，待按需实现

用法：
    from vector_store_abstraction import get_vector_store
    store = get_vector_store("chroma")
    store.upsert(ids, embeddings, documents, metadatas)
    res = store.query(embedding, top_k=5)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol, runtime_checkable

WORK_DIR = Path(os.environ.get("ZENITH_RAG_WORK_DIR", "./zenith_rag"))
CHROMA_PATH = WORK_DIR / "chroma_db"
DEFAULT_COLLECTION = "zotero_papers"


@runtime_checkable
class VectorStore(Protocol):
    """向量库最小契约。"""

    def upsert(self, ids: list[str], embeddings: list[list[float]],
               documents: list[str], metadatas: list[dict]) -> None: ...

    def query(self, embedding: list[float], top_k: int) -> dict: ...

    def delete_by_item(self, item_id: int) -> None: ...

    def count(self) -> int: ...


# ------------------------------------------------------------------
# Chroma 实现
# ------------------------------------------------------------------
class ChromaStore:
    def __init__(self, path: Path = CHROMA_PATH, name: str = DEFAULT_COLLECTION):
        import chromadb
        path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(path))
        self._col = self._client.get_or_create_collection(
            name=name, metadata={"hnsw:space": "cosine"}
        )

    def upsert(self, ids, embeddings, documents, metadatas):
        self._col.upsert(ids=ids, embeddings=embeddings,
                         documents=documents, metadatas=metadatas)

    def query(self, embedding, top_k):
        return self._col.query(query_embeddings=[embedding], n_results=top_k)

    def delete_by_item(self, item_id: int):
        try:
            self._col.delete(where={"item_id": item_id})
        except Exception as e:
            print(f"[warn] delete_by_item 失败: {e}")

    def count(self) -> int:
        try:
            return self._col.count()
        except Exception:
            return -1


# ------------------------------------------------------------------
# 桩实现（后续按需补全）
# ------------------------------------------------------------------
class LeannStore:
    """LEANN 桩：97% 存储节省，按需重计算。需 Rust 构建，暂未实现。"""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError("LEANN 适配待实现：见 week2-vector-store-eval.md")

    def upsert(self, *args, **kwargs):
        raise NotImplementedError

    def query(self, *args, **kwargs):
        raise NotImplementedError

    def delete_by_item(self, *args, **kwargs):
        raise NotImplementedError

    def count(self) -> int:
        raise NotImplementedError


class ZvecStore:
    """Zvec 桩：阿里嵌入式向量库，类 SQLite。需 zvec 包。"""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError("Zvec 适配待实现：见 week2-vector-store-eval.md")

    def upsert(self, *args, **kwargs):
        raise NotImplementedError

    def query(self, *args, **kwargs):
        raise NotImplementedError

    def delete_by_item(self, *args, **kwargs):
        raise NotImplementedError

    def count(self) -> int:
        raise NotImplementedError


class CairnStore:
    """Cairn 桩：Rust 本地优先，SQLite+sqlite-vec。需 cargo 构建。"""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError("Cairn 适配待实现：见 week2-vector-store-eval.md")

    def upsert(self, *args, **kwargs):
        raise NotImplementedError

    def query(self, *args, **kwargs):
        raise NotImplementedError

    def delete_by_item(self, *args, **kwargs):
        raise NotImplementedError

    def count(self) -> int:
        raise NotImplementedError


# ------------------------------------------------------------------
# 工厂
# ------------------------------------------------------------------
def get_vector_store(backend: str = "chroma") -> VectorStore:
    backend = backend.lower()
    if backend == "chroma":
        return ChromaStore()
    if backend == "leann":
        return LeannStore()
    if backend == "zvec":
        return ZvecStore()
    if backend == "cairn":
        return CairnStore()
    raise ValueError(f"未知向量库后端: {backend}")
