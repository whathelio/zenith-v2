"""
Zotero → 本地 RAG 核心脚本（Day1 增强版）

增强点（按 v3 评审报告最小安全第一刀）：
1. VectorStore 抽象层：先实现 ChromaStore，后续可替换 LEANN/Zvec/Cairn 而不改上层。
2. 断点续跑：progress.json 记录已处理 item_id + 文件 mtime，重跑自动跳过未变更。
3. 进度条：tqdm 显示整体与单篇进度。
4. 并行提取：ThreadPoolExecutor 并行做 PDF 文字提取（I/O 密集），embedding 批量执行。
5. 按 Collection 增量索引：--collection 只索引指定收藏夹。
6. OCR 回退判定：文字覆盖率 < 阈值时尝试 PaddleOCR/MinerU，并记录是否回退。
7. 工具命令：--list-collections / --stats / --test-pdf / --rebuild / --force。

依赖：
    pip install pypdfium2 sentence-transformers chromadb openai tqdm

可选（扫描件/公式）：
    pip install paddlepaddle paddleocr
    或按 MinerU 官方文档安装。

用法：
    python zotero_parse_rag_core.py --list-collections
    python zotero_parse_rag_core.py --build --collection "投资"
    python zotero_parse_rag_core.py --build --workers 4
    python zotero_parse_rag_core.py --query "这篇论文的主要贡献是什么？"
    python zotero_parse_rag_core.py --test-pdf "某论文.pdf"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Protocol, Tuple

# ------------------------------------------------------------------
# CONFIG（可用环境变量覆盖）
# ------------------------------------------------------------------
ZOTERO_DATA_DIR = Path(os.environ.get("ZOTERO_DATA_DIR", str(Path.home() / "Zotero")))
ZOTERO_SQLITE = ZOTERO_DATA_DIR / "zotero.sqlite"
STORAGE_DIR = ZOTERO_DATA_DIR / "storage"

# 🔴 RAG 工作目录**必须**落在纯 ASCII 路径。
# ChromaDB 1.5.x 的 Rust HNSW reader 在含中文的路径下直接读不出索引：
# `Error loading hnsw index`（**索引字节本身完好**，换个路径就能读）。
# 决定性实验（2026-10-03）：把**同一份库**从 `D:\dshs\zenith_rag_new` 复制到
# `D:\下载文件\新建文件夹\` 下 → 立刻 `count()` 抛 InternalError；
# 留在 `D:\dshs\` 下 → count=13134 / query() 正常。**唯一变量是路径。**
# 工作区根那份 `zenith_rag_new`（10 篇/8716 chunk）就是这样坏的 —— 建在了中文路径下。
# 与 start.py:81-84 的既有注释一致（同一结论，两处都要留）。
_DEFAULT_RAG_WORK_DIR = (
    Path(r"D:\dshs\zenith_rag_new") if os.name == "nt" else Path("./zenith_rag")
)
WORK_DIR = Path(os.environ.get("ZENITH_RAG_WORK_DIR", str(_DEFAULT_RAG_WORK_DIR)))
CHROMA_PATH = WORK_DIR / "chroma_db"
PROGRESS_FILE = WORK_DIR / "progress.json"
# 🔴 C-4 改造（2026-10-08）：集合名**参数化**。
# 原实现把 "zotero_papers" 硬编码在 get_store() 里 ⇒ 任何外部语料（如 NOMAD）
# 只能灌进论文集合，**无法隔离、无法整块回滚**（import_shiji.py 即被这条挡住的先例）。
# 现在：DEFAULT_COLLECTION 仍是原值（旧行为不变），但所有入库/检索入口都接受显式 collection。
DEFAULT_COLLECTION = os.environ.get("ZENITH_RAG_COLLECTION", "zotero_papers")
COLLECTION_NAME = DEFAULT_COLLECTION          # 兼容旧引用，勿删

# 🔴 本地路径入库白名单（仅 /ingest/local 与 worker 的 ingest 用到）。
# 默认**空 = 该能力关闭**，避免出现「任意本地文件读取」面。迁移时显式开。
_INGEST_ROOTS_RAW = os.environ.get("ZENITH_INGEST_ROOTS", "")
INGEST_ROOTS = [Path(x).resolve() for x in _INGEST_ROOTS_RAW.split(os.pathsep) if x.strip()]

_LOCAL_BGE = Path(__file__).resolve().parent / "bge-small-model"
_DEFAULT_EMBED_MODEL = str(_LOCAL_BGE) if _LOCAL_BGE.exists() else "BAAI/bge-small-zh-v1.5"
EMBED_MODEL = os.environ.get("ZENITH_RAG_EMBED_MODEL", _DEFAULT_EMBED_MODEL)
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.siliconflow.cn/v1")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "sk-your-api-key")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-ai/DeepSeek-V3")

TEXT_COVERAGE_THRESHOLD = float(os.environ.get("ZENITH_RAG_COVERAGE", "0.8"))
MAX_CHUNK_SIZE = int(os.environ.get("ZENITH_RAG_CHUNK_SIZE", "512"))
CHUNK_OVERLAP = int(os.environ.get("ZENITH_RAG_CHUNK_OVERLAP", "64"))
TOP_K = int(os.environ.get("ZENITH_RAG_TOP_K", "5"))
# 问答输出额度。注意 LLM_MODEL 是推理模型，其 reasoning 会占用该额度 —— 见 _chat_once()。
ANSWER_MAX_TOKENS = int(os.environ.get("ZENITH_RAG_ANSWER_TOKENS", "2048"))
# 🔴 2026-10-03 新增：向量检索相关性地板（余弦相似度）。
# 向量检索永远返回 k 个最近近邻，没有"空结果" ⇒ 无关问题也会拿到片段，
# 模型可能拿无关片段硬答（实测出过自相矛盾输出）。低于此值一律报未命中。
#
# 阈值来自实测分布（bge-small-zh-v1.5，cosine，语料=145 篇 CFA/ML 论文，
# 每类 6~8 条正负样本，**不是拍脑袋估的**）：
#   正样本 min = 0.5904（neural network backpropagation）
#   负样本 max = 0.4731（推荐一款口红）
#   → 可用区间 (0.4731, 0.5904)，取中点 **0.53**
# ⚠️ 换嵌入模型或换语料**必须重测分布**再改这个值（区间可能重叠，那就别用阈值，
#    改走别的判据）。此前写的 0.45 是照搬 kb-v2 在另一个语料上的值，
#    在本语料上会放过「莎士比亚四大悲剧」(0.4582) 这类假命中。
MIN_SCORE = float(os.environ.get("ZENITH_RAG_MIN_SCORE", "0.53"))

# 🟡 2026-10-08（多集合改造 C-4 的延伸）：MIN_SCORE 允许**按集合覆盖**。
# 实测（_diag_archive/2026-10-08/min_score_recheck.py，chunk↔chunk 口径）两个语料
# 的 Youden 最优阈值相差 0.08（nomad_ref 0.708 / zotero_papers 0.788）——
# 语料不同，单个全局值必然让其中一方妥协。故开一个**可选**覆盖通道：
#   ZENITH_RAG_MIN_SCORE_MAP='{"nomad_ref": 0.53, "zotero_papers": 0.53}'
# 未配置 / 未命中 / 解析失败 → 一律回落全局 MIN_SCORE（**默认行为完全不变**）。
# ⚠️ 覆盖值同样必须来自该语料的实测分布，不要照搬别的语料。
_MIN_SCORE_MAP: dict = {}
try:
    _raw_min_map = os.environ.get("ZENITH_RAG_MIN_SCORE_MAP", "").strip()
    if _raw_min_map:
        import json as _json_minmap
        _MIN_SCORE_MAP = {str(k): float(v)
                          for k, v in _json_minmap.loads(_raw_min_map).items()}
except Exception:  # noqa: BLE001 —— 环境变量写坏不能拖垮整个 RAG 服务
    _MIN_SCORE_MAP = {}


def min_score_for(collection: "str | None" = None) -> float:
    """取该集合生效的相关性地板；未覆盖则回落全局 MIN_SCORE。

    🔴 2026-10-08 修复（code-reviewer 查出、我实测复现）：
    `collection=None` 必须按 **DEFAULT_COLLECTION** 解析，不能直接回落全局值 ——
    因为 `get_store(None)` 用的就是 DEFAULT_COLLECTION。否则「给默认集合设的
    覆盖永远不生效」。实测：map={"zotero_papers":0.70} 时
    `min_score_for(None)` 仍返回 0.53，而 `min_score_for("zotero_papers")` 返回 0.70。
    """
    return _MIN_SCORE_MAP.get(collection or DEFAULT_COLLECTION, MIN_SCORE)

DEFAULT_WORKERS = int(os.environ.get("ZENITH_RAG_WORKERS", "4"))


# ------------------------------------------------------------------
# VectorStore 抽象层（评审报告 A 阶段）
# ------------------------------------------------------------------
class VectorStore(Protocol):
    def upsert(self, ids: list[str], embeddings: list[list[float]],
               documents: list[str], metadatas: list[dict]) -> None: ...
    def query(self, embedding: list[float], top_k: int) -> dict: ...
    def delete_by_item(self, item_id: int) -> None: ...
    def count(self) -> int: ...


class ChromaStore:
    """Chroma 实现。后续可新增 LEANNStore / ZvecStore / CairnStore。

    C-4 改造：client 可注入（多集合共用同一 PersistentClient），name 即集合名。
    """

    def __init__(self, path: Path, name: str, client=None):
        if client is None:
            import chromadb
            path.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=str(path))
        self._client = client
        self.name = name
        try:
            self._col = self._client.get_or_create_collection(
                name=name, metadata={"hnsw:space": "cosine"}
            )
        except Exception as e:
            # 已存在但 metadata 声明不一致时，退化为直接取用（**不重建、不清空**）
            print(f"[warn] get_or_create_collection({name}) 失败，退化为 get_collection: {e}",
                  file=sys.stderr)
            self._col = self._client.get_collection(name=name)

    def upsert(self, ids, embeddings, documents, metadatas):
        self._col.upsert(ids=ids, embeddings=embeddings,
                         documents=documents, metadatas=metadatas)

    def query(self, embedding, top_k):
        return self._col.query(query_embeddings=[embedding], n_results=top_k)

    def get_by_item(self, item_id: int) -> list[dict]:
        """按 item_id 取该文档的全部 chunk（按 chunk_index 排序）"""
        try:
            res = self._col.get(
                where={"item_id": item_id},
                include=["documents", "metadatas"],
            )
        except Exception as e:
            print(f"[warn] get_by_item 失败（HNSW 索引损坏？），降级 sqlite 直读: {e}")
            return self._get_by_item_sqlite(item_id)
        ids = res.get("ids") or []
        docs = res.get("documents") or []
        metas = res.get("metadatas") or []
        items = []
        for i, mid in enumerate(ids):
            meta = metas[i] if i < len(metas) else {}
            items.append({
                "id": mid,
                "chunk_index": int(meta.get("chunk_index", 0)),
                "text": docs[i] if i < len(docs) else "",
                "title": meta.get("title", ""),
            })
        items.sort(key=lambda x: x["chunk_index"])
        return items

    def _get_by_item_sqlite(self, item_id: int) -> list[dict]:
        """HNSW 索引损坏时降级：直接读 chroma.sqlite3 的 embedding_metadata 表取正文。"""
        sqlite_path = CHROMA_PATH / "chroma.sqlite3"
        if not sqlite_path.exists():
            print(f"[warn] chroma.sqlite3 不存在: {sqlite_path}")
            return []
        conn = None
        try:
            conn = sqlite3.connect(str(sqlite_path))
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                    MAX(CASE WHEN m.key='chroma:document' THEN m.string_value END) AS doc_text,
                    MAX(CASE WHEN m.key='chunk_index' THEN m.int_value END) AS chunk_idx,
                    MAX(CASE WHEN m.key='title' THEN m.string_value END) AS title
                FROM embedding_metadata m
                WHERE m.id IN (
                    SELECT id FROM embedding_metadata
                    WHERE key='item_id' AND int_value=?
                )
                GROUP BY m.id
                ORDER BY chunk_idx
                """,
                (item_id,),
            )
            rows = cur.fetchall()
        except Exception as e:
            print(f"[warn] get_by_item sqlite 直读失败: {e}")
            return []
        finally:
            if conn:
                conn.close()
        items = []
        for doc_text, chunk_idx, title in rows:
            idx = int(chunk_idx) if chunk_idx is not None else 0
            items.append({
                "id": f"{item_id}_{idx}",
                "chunk_index": idx,
                "text": doc_text or "",
                "title": title or "",
            })
        items.sort(key=lambda x: x["chunk_index"])
        return items

    def delete_by_item(self, item_id: int):
        try:
            self._col.delete(where={"item_id": item_id})
        except Exception as e:
            print(f"[warn] delete_by_item 失败: {e}")

    def count(self) -> int:
        try:
            return self._col.count()
        except Exception as e:
            print(f"[warn] count 失败（HNSW 索引损坏？），降级 sqlite 直读: {e}")
            return self._count_by_sqlite()

    def _count_by_sqlite(self) -> int:
        """HNSW 索引损坏时降级：统计 embedding_metadata 中正文条目数（即 chunk 数）。"""
        sqlite_path = CHROMA_PATH / "chroma.sqlite3"
        if not sqlite_path.exists():
            return -1
        conn = None
        try:
            conn = sqlite3.connect(str(sqlite_path))
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM embedding_metadata WHERE key='chroma:document'")
            return int(cur.fetchone()[0])
        except Exception as e:
            print(f"[warn] count sqlite 直读失败: {e}")
            return -1
        finally:
            if conn:
                conn.close()


# ------------------------------------------------------------------
# 集合与存储（C-4 改造：多集合）
# ------------------------------------------------------------------
_CHROMA_CLIENT = None
_CHROMA_LOCK = threading.Lock()
_STORES: dict[str, "ChromaStore"] = {}
_STORES_LOCK = threading.Lock()


def _get_chroma_client():
    """进程内复用的 PersistentClient（同一 path 只建一次）。

    多集合共用同一 client，只按集合名取不同 handle —— 避免反复构造 client
    （ChromaDB 的 client 构造含 sqlite 连接与目录扫描，不是免费的）。
    """
    global _CHROMA_CLIENT
    if _CHROMA_CLIENT is None:
        with _CHROMA_LOCK:
            if _CHROMA_CLIENT is None:
                import chromadb
                WORK_DIR.mkdir(parents=True, exist_ok=True)
                _CHROMA_CLIENT = chromadb.PersistentClient(path=str(CHROMA_PATH))
    return _CHROMA_CLIENT


def get_store(collection: str | None = None) -> VectorStore:
    """取指定集合的 store。collection=None → 默认集合（旧行为不变）。"""
    name = collection or COLLECTION_NAME
    with _STORES_LOCK:
        st = _STORES.get(name)
        if st is None:
            st = ChromaStore(CHROMA_PATH, name, client=_get_chroma_client())
            _STORES[name] = st
        return st


def stable_item_id(p: Path, extra: str = "") -> int:
    """稳定 item_id（C-7 改造）。

    🔴 原实现 `abs(hash(p.name + str(mtime))) % 1000000`：
    Python 的 str hash **每进程随机化**（PYTHONHASHSEED）⇒ 同一文件重启后
    算出不同 id ⇒ 重灌产生**重复数据**且无法按 id 清理。

    改为 sha1(name|extra) 前 12 hex → int。**不含 mtime**：这样同一文件的
    重复入库是幂等 upsert（同 id 覆盖），而不是不断新增。
    ⚠️ 旧库中已有条目仍是旧的 hash-id，**不重算**（避免打乱既有 13,134 chunk）。
    """
    h = hashlib.sha1(f"{p.name}|{extra}".encode("utf-8")).hexdigest()
    return int(h[:12], 16)


def resolve_item_id(store, p: Path, extra: str = "") -> tuple[int, bool]:
    """撞名 guard（N-1 §5，附加式、零迁移，未实施前的待办项）。

    `stable_item_id` 仅以 basename(+extra) 为键 ⇒ 不同目录下的**同名文件**会算出
    **同一个 item_id**：后灌者先 `delete_by_item()` 再覆盖，静默丢数据；同时
    progress.json（扁平的 str(item_id) → 记录）也会互相覆盖。

    此处在 upsert **之前**探测：base_id 已在 store 中 **且** 已存 source 的 basename
    与 `p.name` 相同 **且** 两者规范化绝对路径不同 ⇒ 命中，把 extra 升级为
    `f"{extra}|{全路径}"`（extra 为空时即纯全路径键）后重算 item_id。
    **保留原 extra**：ingest_pdf 的抽取器名不能被路径键顶掉，否则同一 PDF 用不同
    抽取器入库会塌成同一 id、互相覆盖。

    :returns: `(item_id, collided)`
        - 未命中：item_id **逐字节等于** `stable_item_id(p, extra)` ——
          未撞名的文件 id 完全不变（零迁移）。
        - 命中：collided=True，item_id 为含全路径的新键。

    ⚠️ 已存 source 的取回：`ChromaStore.get_by_item()`（本文件 :188-198）只回传
    id/chunk_index/text/title，**不含 source_file** ⇒ 无法从 store 取回旧路径。
    故优先读 chunk dict（若某 store 实现带 source_file），否则回落到 progress.json
    （写入侧 :1052/:1115 的 "source_file"，键为 str(item_id)）。
    """
    item_id = stable_item_id(p, extra)
    try:
        existing = store.get_by_item(item_id)
    except Exception:
        existing = []
    if not existing:
        return item_id, False
    prev = ""
    for item in existing:
        prev = str((item or {}).get("source_file") or "")
        if prev:
            break
    if not prev:
        try:
            prev = str((load_progress().get(str(item_id)) or {}).get("source_file") or "")
        except Exception:
            prev = ""
    if (prev and Path(prev).name == p.name
            and os.path.normcase(os.path.abspath(prev))
            != os.path.normcase(os.path.abspath(str(p)))):
        _path_key = str(p.resolve()).replace("\\", "/").lower()
        # 保留 extra 语义：ingest_pdf 的抽取器名不能被路径键顶掉，否则同一 PDF
        # 用不同抽取器入库会塌成同一 id、互相覆盖（语义退化）。
        new_extra = f"{extra}|{_path_key}" if extra else _path_key
        new_id = stable_item_id(p, new_extra)
        print(f"[warn] item_id 撞名：{prev} 与 {p} 同名（{p.name}）但路径不同 "
              f"⇒ 改用含路径键 {new_id}（原 {item_id}）")
        return new_id, True
    return item_id, False


def _assert_ingest_allowed(p: Path) -> None:
    """本地路径入库白名单校验。未配置白名单时一律拒绝。"""
    if not INGEST_ROOTS:
        raise PermissionError(
            "本地路径入库未启用（需设 ZENITH_INGEST_ROOTS 白名单目录）")
    rp = p.resolve()
    for root in INGEST_ROOTS:
        try:
            rp.relative_to(root)
            return
        except ValueError:
            continue
    raise PermissionError(f"路径不在白名单内: {rp}")


def list_rag_collections() -> list[dict]:
    """列出向量库内全部集合（含 chunk 数）与 progress 内的文档数。"""
    try:
        client = _get_chroma_client()
        cols = client.list_collections()
    except Exception as e:
        print(f"[warn] list_collections 失败: {e}")
        return []
    prog = load_progress()
    out = []
    for c in cols:
        try:
            nm = c.name if hasattr(c, "name") else str(c)
        except Exception:
            nm = str(c)
        try:
            cnt = get_store(nm).count()
        except Exception:
            cnt = -1
        docs = sum(1 for v in prog.values()
                   if isinstance(v, dict)
                   and (v.get("collection") or DEFAULT_COLLECTION) == nm)
        out.append({"collection": nm, "chunks": cnt, "docs": docs})
    out.sort(key=lambda d: d["collection"])
    return out


def archive_collection(name: str, suffix: str | None = None) -> dict:
    """集合**归档**（重命名墓碑），不做物理删除。

    对应「零删除」红线：把集合改名成 `<name>__archived_<日期>`，数据仍在磁盘上，
    随时可改回。物理删除需另行人工处置。
    """
    ts = suffix or time.strftime("%Y%m%d")
    new_name = f"{name}__archived_{ts}"
    try:
        client = _get_chroma_client()
        col = client.get_collection(name=name)
    except Exception as e:
        return {"status": "error", "reason": f"集合不存在或读取失败: {e}"}
    try:
        col.modify(name=new_name)
    except Exception as e:
        return {"status": "error", "reason": f"改名失败: {e}"}
    with _STORES_LOCK:
        _STORES.pop(name, None)
    return {"status": "ok", "from": name, "to": new_name,
            "note": "已归档（重命名），数据未删除"}



_EMBEDDER = None
_EMBEDDER_LOCK = threading.Lock()


def get_embedder():
    """返回**进程内复用**的 embedding 模型（懒加载 + 线程安全）。

    ⚠️ 2026-09-11 修复：原实现是 `return SentenceTransformer(EMBED_MODEL)` ——
    **每次调用都从磁盘重建整个模型**，而它有 4 处调用点（answer / ingest / …），
    等于每个请求都白付一次模型构造。实测三连调返回的对象 id 各不相同（确证无缓存），
    第 2、3 次各花 ~80ms（首次 42s 含一次性的 torch 导入）—— 纯浪费。

    FastAPI 的**同步** handler 跑在线程池里，本函数可能被并发进入，
    故用锁保护懒加载（双重检查，避免并发重复构造出多个模型实例）。
    """
    global _EMBEDDER
    if _EMBEDDER is None:
        with _EMBEDDER_LOCK:
            if _EMBEDDER is None:
                from sentence_transformers import SentenceTransformer
                print(f"[embedder] 首次加载模型：{EMBED_MODEL}", file=sys.stderr)
                _EMBEDDER = SentenceTransformer(EMBED_MODEL)
    return _EMBEDDER


# ------------------------------------------------------------------
# Progress（断点续跑）
# ------------------------------------------------------------------
def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_progress(prog: dict):
    """落盘 progress.json。

    🟡 2026-10-08：改为**原子写**（写临时文件 + os.replace）。
    原实现是 `open(PROGRESS_FILE, "w")` 直接截断目标文件再写 ——
    若写入中途进程被杀/Ctrl+C/磁盘满，progress.json 会**半截损坏**，
    而 build_index 是「每篇落盘」，命中概率不低。
    `os.replace` 在同分区是原子操作，读端永远只会看到完整的旧版或完整的新版。
    """
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS_FILE.with_suffix(PROGRESS_FILE.suffix + f".tmp-{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(prog, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PROGRESS_FILE)


# ------------------------------------------------------------------
# Zotero 路径解析
# ------------------------------------------------------------------
def _zotero_conn():
    if not ZOTERO_SQLITE.exists():
        raise FileNotFoundError(f"找不到 Zotero sqlite: {ZOTERO_SQLITE}")
    uri = f"file:{ZOTERO_SQLITE.as_posix()}?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


def resolve_zotero_attachment_path(sqlite_path: str) -> Path | None:
    if not sqlite_path:
        return None
    if sqlite_path.startswith("storage:"):
        key = sqlite_path.split(":", 1)[1]
        abs_path = STORAGE_DIR / key
        if abs_path.is_file():
            return abs_path
        if abs_path.is_dir():
            for child in abs_path.iterdir():
                if child.suffix.lower() == ".pdf":
                    return child
        return None
    if sqlite_path.startswith("attachments:"):
        print(f"[warn] attachments: 相对路径暂未解析: {sqlite_path}")
        return None
    p = Path(sqlite_path)
    if p.is_file() and p.suffix.lower() == ".pdf":
        return p
    return None


def list_collections() -> list[Tuple[str, int]]:
    """返回 [(collectionName, item_count), ...]。"""
    conn = _zotero_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT c.collectionName, COUNT(ci.itemID)
        FROM collections c
        LEFT JOIN collectionItems ci ON c.collectionID = ci.collectionID
        GROUP BY c.collectionID
        ORDER BY COUNT(ci.itemID) DESC
    """)
    rows = cur.fetchall()
    conn.close()
    return rows


def list_zotero_pdfs(collection_name: str | None = None,
                     limit: int | None = None) -> List[Tuple[int, str, Path, float]]:
    """返回 (item_id, title, pdf_path, mtime)。"""
    conn = _zotero_conn()
    cur = conn.cursor()

    collection_filter = ""
    params = []
    if collection_name:
        cur.execute(
            "SELECT collectionID FROM collections WHERE collectionName=? LIMIT 1",
            (collection_name,),
        )
        row = cur.fetchone()
        if not row:
            conn.close()
            raise ValueError(f"Zotero 中不存在 Collection: {collection_name}")
        collection_filter = """
            AND items.itemID IN (
                SELECT itemID FROM collectionItems WHERE collectionID=?
            )
        """
        params.append(row[0])

    sql = f"""
    SELECT
        items.itemID,
        itemAttachments.path AS att_path,
        itemDataValues.value AS title
    FROM items
    LEFT JOIN itemAttachments ON items.itemID = itemAttachments.itemID
    LEFT JOIN (
        SELECT itemID, value
        FROM itemData
        JOIN itemDataValues USING(valueID)
        JOIN fields USING(fieldID)
        WHERE fields.fieldName = 'title'
    ) AS itemDataValues ON items.itemID = itemDataValues.itemID
    WHERE items.itemTypeID = 1
      AND itemAttachments.path IS NOT NULL
      {collection_filter}
    """
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()

    results = []
    for item_id, att_path, title in rows:
        pdf_path = resolve_zotero_attachment_path(att_path)
        if pdf_path and pdf_path.exists():
            try:
                mtime = pdf_path.stat().st_mtime
            except OSError:
                mtime = 0.0
            results.append((item_id, title or f"item_{item_id}", pdf_path, mtime))
        if limit and len(results) >= limit:
            break

    print(f"[info] 从 Zotero 解析出 {len(results)} 个有效 PDF 附件")
    return results


# ------------------------------------------------------------------
# PDF 文字提取与覆盖率
# ------------------------------------------------------------------
def extract_text_pdfium(pdf_path: Path) -> str:
    try:
        import pypdfium2 as pdfium
    except ImportError as e:
        raise ImportError("请安装 pypdfium2: pip install pypdfium2") from e
    text_parts = []
    pdf = pdfium.PdfDocument(str(pdf_path))
    for page in pdf:
        text_parts.append(page.get_textpage().get_text_bounded())
    pdf.close()
    return "\n".join(text_parts)


def estimate_text_coverage(text: str) -> float:
    if not text:
        return 0.0
    valid = sum(1 for ch in text if ch.isprintable() or ("\u4e00" <= ch <= "\u9fff"))
    return valid / len(text)


def extract_text_with_fallback(pdf_path: Path) -> Tuple[str, float, str]:
    """返回 (text, coverage, source)，source 为 'pdfium'/'ocr'/'mineru'。"""
    try:
        text = extract_text_pdfium(pdf_path)
    except Exception as e:
        print(f"[warn] pypdfium 提取失败 {pdf_path.name}: {e}")
        text = ""
    coverage = estimate_text_coverage(text)
    if coverage >= TEXT_COVERAGE_THRESHOLD:
        return text, coverage, "pdfium"

    print(f"[warn] {pdf_path.name} 覆盖率 {coverage:.2%} 不足，尝试 OCR 回退")
    try:
        from paddleocr import PaddleOCR
        ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
        result = ocr.ocr(str(pdf_path), cls=True)
        ocr_text = "\n".join(line[1][0] for page in result for line in (page or []))
        return ocr_text, estimate_text_coverage(ocr_text), "ocr"
    except Exception as e:
        print(f"[warn] PaddleOCR 失败: {e}")

    try:
        from magic_pdf import process_pdf  # MinerU 示例接口
        return process_pdf(str(pdf_path)), 1.0, "mineru"
    except Exception as e:
        print(f"[warn] MinerU 失败或未安装: {e}")

    return text, coverage, "pdfium-fallback"


# ------------------------------------------------------------------
# 分块
# ------------------------------------------------------------------
def split_text(text: str, max_size: int = MAX_CHUNK_SIZE,
               overlap: int = CHUNK_OVERLAP) -> List[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + max_size, len(text))
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start += max_size - overlap
    return chunks


def process_one(item: Tuple[int, str, Path, float]) -> Tuple[int, str, str, float, str, List[str]]:
    """单篇 PDF 提取+分块，返回 (item_id, title, text, coverage, source, chunks)。"""
    item_id, title, pdf_path, mtime = item
    text, coverage, source = extract_text_with_fallback(pdf_path)
    chunks = split_text(text)
    return item_id, title, text, coverage, source, chunks


# ------------------------------------------------------------------
# 构建索引
# ------------------------------------------------------------------
def build_index(collection_name: str | None = None,
                workers: int = DEFAULT_WORKERS,
                limit: int | None = None,
                force: bool = False,
                target_collection: str | None = None):
    try:
        from tqdm import tqdm
    except ImportError:
        tqdm = lambda x, **k: x  # noqa: E731

    store = get_store(target_collection)
    embedder = get_embedder()
    pdfs = list_zotero_pdfs(collection_name, limit)
    prog = {} if force else load_progress()

    todo = []
    skipped = 0
    for item in pdfs:
        item_id, title, pdf_path, mtime = item
        key = str(item_id)
        rec = prog.get(key)
        if rec and abs(rec.get("mtime", 0) - mtime) < 1.0:
            skipped += 1
            continue
        todo.append(item)
    print(f"[info] 待处理 {len(todo)} 篇，跳过已处理 {skipped} 篇")

    failed = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(process_one, item): item for item in todo}
        for fut in tqdm(as_completed(futures), total=len(todo), desc="索引中"):
            item = futures[fut]
            item_id, title, pdf_path, mtime = item
            try:
                _, title2, text, coverage, source, chunks = fut.result()
            except Exception as e:
                print(f"[error] 处理失败 {title}: {e}")
                failed.append((item_id, title, str(e)))
                continue
            if not chunks:
                print(f"[warn] {title} 无可入库文本")
                continue

            ids = [f"{item_id}_{i}" for i in range(len(chunks))]
            metadatas = [
                {
                    "item_id": item_id,
                    "title": title2,
                    "source_file": str(pdf_path),
                    "chunk_index": i,
                    "coverage": round(coverage, 3),
                    "source": source,
                }
                for i in range(len(chunks))
            ]
            embeddings = embedder.encode(chunks, normalize_embeddings=True,
                                         batch_size=32, show_progress_bar=False).tolist()
            store.upsert(ids=ids, embeddings=embeddings,
                         documents=chunks, metadatas=metadatas)
            prog[str(item_id)] = {
                "mtime": mtime, "title": title2,
                "chunks": len(chunks), "source": source,
                # 2026-10-08 回归修复（#3）：build_index 这条路径原来不写 collection，
                # 导致它入库的文档在 /documents 里只能靠「字段缺失 → 落默认集合」兜底。
                # 一旦 ZENITH_RAG_COLLECTION 被改动，这批记录会被整体重挂到新默认集合。
                "collection": target_collection or DEFAULT_COLLECTION,
            }
            save_progress(prog)  # 每篇落盘，可随时 Ctrl+C

    dt = time.time() - t0
    print(f"[info] 完成。耗时 {dt:.1f}s，失败 {len(failed)} 篇，库内片段数 {store.count()}")
    if failed:
        for f in failed[:10]:
            print(f"  - {f[1]}: {f[2]}")


# ------------------------------------------------------------------
# 问答
# ------------------------------------------------------------------
def answer(question: str, collection: str | None = None) -> str:
    import openai
    store = get_store(collection)
    embedder = get_embedder()
    q_emb = embedder.encode([question], normalize_embeddings=True).tolist()[0]
    results = store.query(q_emb, top_k=TOP_K)
    # 2026-10-08 间歇性未命中排查：分支级埋点（仅 ZENITH_SEARCH_DEBUG=1 时输出）。
    # 背景：同一进程内、同一入参，answer() 时好时坏；此处记录 chroma 返回的**结构**，
    # 用以区分「distances 缺失 → 全被地板滤掉」与「docs 本身为空」。
    if os.environ.get("ZENITH_SEARCH_DEBUG", "0") == "1":
        _dd = results.get("distances")
        print(f"[dbg-ans] stage=query keys={sorted(results.keys())} "
              f"n_docs={len((results.get('documents') or [[]])[0])} "
              f"n_meta={len((results.get('metadatas') or [[]])[0])} "
              f"dist_type={type(_dd).__name__} dists={_dd}", file=sys.stderr)
    docs = results["documents"][0]
    metas = results["metadatas"][0]
    if not docs:
        if os.environ.get("ZENITH_SEARCH_DEBUG", "0") == "1":
            print("[dbg-ans] stage=A_empty_docs", file=sys.stderr)
        return "知识库中未找到相关内容。"

    # 🔴 2026-10-03 补相关性闸门（往期日志 §2 的延长线）。
    # 向量检索**永远**返回 k 个最近邻，不存在"空结果" —— 所以 `if not docs`
    # 这道闸门对"问的根本不相关的问题"完全无效。
    # 后果（实测）：问「用一句话解释什么是方差」，库里全是英文金融/ML 论文，
    # 模型拿一堆无关片段先答了"方差是…统计量"，末尾又写"知识库中未找到相关内容"
    # —— **自相矛盾**，且那句"未找到"是模型自己脑补的，不是检索结论。
    # 处置：低于相似度地板就**不把片段交给模型**，直接如实报未命中。
    # 地板值 0.53 来自实测正负样本分布（见 MIN_SCORE 处注释）：
    #   正样本 min 0.5904 / 负样本 max 0.4731 → 区间中点。
    # ⚠️ 换嵌入模型或换语料**必须重测分布**再改这个值。
    # 2026-10-08：地板值改为「按集合取」—— 不同语料的分布不同，
    # 由 min_score_for(collection) 决定，未配置覆盖时与旧的全局 MIN_SCORE 等价。
    _floor = min_score_for(collection)
    _dist = (results.get("distances") or [[]])[0]
    kept = [(d, m, 1.0 - float(_dist[i]))
            for i, (d, m) in enumerate(zip(docs, metas))
            if i < len(_dist) and (1.0 - float(_dist[i])) >= _floor]
    if not kept:
        if os.environ.get("ZENITH_SEARCH_DEBUG", "0") == "1":
            print(f"[dbg-ans] stage=B_floor_filtered floor={_floor} "
                  f"n_docs={len(docs)} n_dist={len(_dist)} "
                  f"sims={[round(1.0 - float(x), 4) for x in _dist]}", file=sys.stderr)
        return "知识库中未找到相关内容。"
    docs = [k[0] for k in kept]
    metas = [k[1] for k in kept]

    context = "\n\n---\n\n".join(
        f"[来源: {m.get('title')}, 文件: {m.get('source_file')}]\n{d}"
        for d, m in zip(docs, metas)
    )
    # 🔴 2026-10-03 加两条硬约束（修「自相矛盾」实测缺陷）：
    # 旧版只说"请仅根据片段回答"+「没有就说未找到」，但**没禁止用通用知识补答**，
    # 于是模型会先用自己的统计学常识答一遍、末尾再补一句"知识库中未找到相关内容"
    # —— 两句自相矛盾，且"未找到"那句是脑补的、并非检索结论。
    # 现在明确：片段没答案就**只**输出未命中声明，**禁止**先答后 disclaim。
    #
    # 🔴🔴 2026-10-08 复查：上面这两条**引入了新的回归**——间歇性假阴性。
    # 实测（_diag_archive/2026-10-08/prompt_fix_ab.py，同 context、同问题）：
    #   旧模板 10/10 输出「知识库中未找到相关内容。」，而检索完全正常
    #   （[dbg-ans] stage=query 恒 5 docs / 5 distances，sims 0.68~0.74 全过 floor；
    #     stage=A_empty_docs 与 stage=B_floor_filtered **0 次触发**）。
    # 根因：旧模板把「知识库中未找到相关内容」这一**精确字符串写了两遍**去强调，
    #   而模型拿不到距离分数、无法自行判断"片段够不够答"，于是这个字符串成了
    #   它的**逃离出口（attractor）**：temperature=0.5 一采样就随机吐出来。
    #   后果是"未命中"信号完全不可信 —— 对真相关（Catholicism 史）与真不相关
    #   （南非史，检索到 African_Americans/Washington/Singapore）输出**一模一样**，
    #   地板闸门形同虚设。
    # 修法：ZENITH_RAG_ANSWER_PROMPT_V2=1 切到「去吸引子」模板 ——
    #   **彻底不出现"未找到"字样**（去掉吸引子本身，而不是换句更严厉的命令），
    #   并明确告知"系统已判定片段相关"，把免责决策权收回系统侧。
    #   实测 0/10 假阴性；且真不相关时会给出可诊断回答
    #   （"片段涉及 A/B/C，没有关于 X 的信息"）而非不可区分的"未找到"。
    # ⚠️ 默认仍走旧模板，**零行为变更**；切换只需一个环境变量。
    if os.environ.get("ZENITH_RAG_ANSWER_PROMPT_V2", "0") == "1":
        prompt = (
            "你是 Zenith，一个基于本地文献知识库回答问题的助手。\n"
            "下面这些是你检索到的、与问题最相关的文献片段。"
            "**系统已判定它们与问题相关**，请直接依据它们作答。\n"
            "要求：只使用片段中出现的信息；不要引入片段之外的常识；"
            "在回答末尾用 [来源: 标题] 标注你实际用到的片段。\n\n"
            f"---\n{context}\n---\n\n"
            f"用户问题：{question}"
        )
    else:
        prompt = (
            "你是 Zenith，一个基于本地文献知识库回答问题的助手。\n"
            "请仅根据下面提供的文献片段回答问题，并引用来源。\n"
            "如果片段中没有答案，请明确说明“知识库中未找到相关内容”。\n"
            "**禁止**先用模型自身的通用知识作答、再补一句“知识库中未找到相关内容”"
            " —— 这两句自相矛盾，会让调用方无法判断到底有没有查到。\n"
            "片段里没有就直接答“知识库中未找到相关内容”，一个字都不要多写。\n\n"
            f"---\n{context}\n---\n\n"
            f"用户问题：{question}"
        )
    client = openai.OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)
    messages = [
        {"role": "system", "content": "简洁回答，标注引用来源。"},
        {"role": "user", "content": prompt},
    ]
    content = _extract_content(_chat_once(client, messages))
    if os.environ.get("ZENITH_SEARCH_DEBUG", "0") == "1":
        print(f"[dbg-ans] stage=C_llm has_content={bool(content)} "
              f"head={(content or '')[:60]!r}", file=sys.stderr)
    if content:
        return content
    # 不静默返回空串 —— 否则前端显示空白，且调用方无法区分「库中没有」与「模型没答出来」
    return "（模型未返回正文内容，可能是推理占满了输出额度；请重试或换一种问法。）"


def _chat_once(client, messages: list):
    """单次 LLM 调用 —— **显式关闭思考**。

    ⚠️ 2026-09-11 修复：LLM_MODEL（deepseek-flash）是**推理模型**，其 reasoning 会
    消耗 max_tokens 的额度。实测 max_tokens=1024 时 reasoning_tokens 达 1024，
    导致 finish_reason="length" 且 message.content 为**空字符串** —— 表现为
    「知识库检索间歇性返回空答案」（推理长度随问题难度浮动，故时好时坏）。

    对策：显式关闭思考（RAG 摘录式问答不需要长推理）。若 provider 不认该参数，
    则降级为不带该参数重试一次，保证跨 provider 可用。

    ⚠️ 必须用 `extra_body=` 传该参数：openai Python SDK 会**校验**入参，
    直接写 `thinking=...` 会抛 `unexpected keyword argument`（实测踩过），
    结果是"看起来关了思考、实际每次都降级"。
    """
    kw = dict(model=LLM_MODEL, messages=messages, temperature=0.5,
              max_tokens=ANSWER_MAX_TOKENS)
    try:
        return client.chat.completions.create(
            **kw, extra_body={"thinking": {"type": "disabled"}})
    except Exception as e:                                   # noqa: BLE001
        print(f"[llm] 关闭思考失败（provider 可能不支持该参数），改为常规调用: {e}",
              file=sys.stderr)
        return client.chat.completions.create(**kw)


def _extract_content(resp) -> str:
    """取正文；正文为空时**显式告警**，而不是静默返回空串。

    静默失败比报错更危险 —— 调用方无法区分「知识库中确实没有」与「模型没答出来」。
    """
    try:
        choice = resp.choices[0]
        msg = choice.message
    except (AttributeError, IndexError) as e:                # noqa: BLE001
        print(f"[llm] 响应结构异常: {e}", file=sys.stderr)
        return ""
    content = (msg.content or "").strip()
    if content:
        return content
    reason = getattr(choice, "finish_reason", "?")
    rc = getattr(msg, "reasoning_content", None) or ""
    print(f"[llm] 正文为空：finish_reason={reason}, reasoning={len(rc)} 字, "
          f"max_tokens={ANSWER_MAX_TOKENS} —— 多为推理占满额度所致。", file=sys.stderr)
    return ""


# ------------------------------------------------------------------
# 工具命令
# ------------------------------------------------------------------
def cmd_stats():
    store = get_store()
    prog = load_progress()
    print(f"向量库片段数: {store.count()}")
    print(f"已处理文献数: {len(prog)}")
    if prog:
        ocr = sum(1 for v in prog.values() if v.get("source") not in ("pdfium", None))
        print(f"OCR/回退文献: {ocr}")


def cmd_test_pdf(pdf_path: str):
    p = Path(pdf_path)
    if not p.exists():
        print(f"文件不存在: {p}")
        return
    text, coverage, source = extract_text_with_fallback(p)
    print(f"文件: {p.name}")
    print(f"覆盖率: {coverage:.2%}")
    print(f"来源: {source}")
    print(f"字符数: {len(text)}")
    print("--- 前 500 字 ---")
    print(text[:500])


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Zotero → 本地 RAG（Day1 增强版）")
    parser.add_argument("--build", action="store_true", help="构建/增量索引")
    parser.add_argument("--query", type=str, help="提问")
    parser.add_argument("--collection", type=str, help="只索引指定 Collection")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="并行提取线程数")
    parser.add_argument("--limit", type=int, help="只处理前 N 篇（测试用）")
    parser.add_argument("--force", action="store_true", help="忽略断点，强制重处理")
    parser.add_argument("--rebuild", action="store_true", help="清空向量库与进度后重建")
    parser.add_argument("--list-collections", action="store_true", help="列出 Zotero Collection")
    parser.add_argument("--stats", action="store_true", help="显示索引统计")
    parser.add_argument("--test-pdf", type=str, help="测试单个 PDF 提取")
    parser.add_argument("--ingest-pdf", type=str, help="审查并入库单个 PDF")
    # —— 2026-10-08 新增（多集合 / 批量）——
    parser.add_argument("--rag-collection", type=str,
                        help="目标向量集合名（默认 zotero_papers）")
    parser.add_argument("--list-rag-collections", action="store_true",
                        help="列出向量库内全部集合")
    parser.add_argument("--archive-collection", type=str,
                        help="归档集合（重命名为墓碑，不物理删除）")
    parser.add_argument("--ingest-dir", type=str,
                        help="批量入库目录下的 .md/.txt/.pdf")
    parser.add_argument("--pattern", type=str, default="*.md",
                        help="配合 --ingest-dir 的匹配模式")
    args = parser.parse_args()

    if args.rebuild:
        import shutil
        if CHROMA_PATH.exists():
            shutil.rmtree(CHROMA_PATH)
        if PROGRESS_FILE.exists():
            PROGRESS_FILE.unlink()
        print("[info] 已清空向量库与进度，开始重建")
        args.build = True

    if args.list_rag_collections:
        for d in list_rag_collections():
            print(f"  {d['chunks']:>9,} chunk  {d['docs']:>6,} 文档  {d['collection']}")
    elif args.archive_collection:
        import json as _json
        print(_json.dumps(archive_collection(args.archive_collection),
                          ensure_ascii=False, indent=2))
    elif args.ingest_dir:
        import json as _json
        import glob as _glob
        files = sorted(_glob.glob(str(Path(args.ingest_dir) / args.pattern)))
        res = ingest_paths(files, collection=args.rag_collection)
        print(_json.dumps(res, ensure_ascii=False, indent=2))
    elif args.list_collections:
        for name, cnt in list_collections():
            print(f"{cnt:5d}  {name}")
    elif args.stats:
        cmd_stats()
    elif args.test_pdf:
        cmd_test_pdf(args.test_pdf)
    elif args.ingest_pdf:
        import json as _json
        print(_json.dumps(ingest_pdf(args.ingest_pdf, collection=args.rag_collection),
                          ensure_ascii=False, indent=2))
    elif args.build:
        build_index(args.collection, args.workers, args.limit, args.force,
                    target_collection=args.rag_collection)
    elif args.query:
        print(answer(args.query, collection=args.rag_collection))
    else:
        parser.print_help()


# ------------------------------------------------------------------
# 单 PDF 入库 + 入库前审查
# ------------------------------------------------------------------

def review_pdf(pdf_path: str | Path) -> dict:
    """入库前审查：文字覆盖率 + 来源 + 字符数。不通过则不应入库。"""
    p = Path(pdf_path)
    if not p.exists():
        return {"passed": False, "reason": "文件不存在"}
    if p.suffix.lower() != ".pdf":
        return {"passed": False, "reason": "仅支持 PDF"}
    size_mb = p.stat().st_size / 1024 / 1024
    if size_mb > 20:
        return {"passed": False, "reason": f"文件过大 {size_mb:.1f}MB > 20MB"}
    try:
        text, coverage, source = extract_text_with_fallback(p)
    except Exception as e:
        return {"passed": False, "reason": f"提取失败: {e}", "size_mb": round(size_mb, 2)}
    passed = coverage >= 0.3 or source in ("ocr", "mineru")
    return {
        "passed": passed,
        "coverage": round(coverage, 3),
        "source": source,
        "chars": len(text),
        "size_mb": round(size_mb, 2),
        "reason": "" if passed else "文字覆盖率不足，疑似扫描件且未启用 OCR",
    }


def ingest_pdf(pdf_path: str | Path, title: str | None = None,
               collection: str | None = None) -> dict:
    """单 PDF 入库：提取 → 分块 → 向量化 → upsert。返回状态。

    collection=None → 默认集合（旧行为不变）。
    """
    p = Path(pdf_path)
    review = review_pdf(p)
    if not review["passed"]:
        return {"status": "rejected", **review}

    text, coverage, source = extract_text_with_fallback(p)
    chunks = split_text(text)
    if not chunks:
        return {"status": "rejected", "reason": "无可入库文本", "coverage": coverage}

    col_name = collection or COLLECTION_NAME
    store = get_store(col_name)
    embedder = get_embedder()
    # C-7：稳定 id（跨进程可复现）→ 重复入库是幂等 upsert，不再堆积重复
    # N-1 §5：同名异路径 guard（未撞名时 id 完全不变）
    item_id, _ = resolve_item_id(store, p, source)
    title = title or p.stem
    ids = [f"{item_id}_{i}" for i in range(len(chunks))]
    metadatas = [
        {
            "item_id": item_id,
            "title": title,
            "source_file": str(p),
            "chunk_index": i,
            "coverage": round(coverage, 3),
            "source": source,
            "collection": col_name,
        }
        for i in range(len(chunks))
    ]
    embeddings = embedder.encode(chunks, normalize_embeddings=True, batch_size=32, show_progress_bar=False).tolist()
    # 先清同 id 旧 chunk：避免「新版块数变少」时残留高序号旧块
    try:
        store.delete_by_item(item_id)
    except Exception:
        pass
    store.upsert(ids=ids, embeddings=embeddings, documents=chunks, metadatas=metadatas)

    prog = load_progress()
    prog[str(item_id)] = {
        "mtime": p.stat().st_mtime,
        "title": title,
        "chunks": len(chunks),
        "source": source,
        "source_file": str(p),
        "collection": col_name,
    }
    save_progress(prog)
    return {"status": "ok", "chunks": len(chunks), "coverage": round(coverage, 3),
            "source": source, "title": title, "item_id": item_id,
            "collection": col_name}


def ingest_text_file(path: str | Path, title: str | None = None,
                     collection: str | None = None) -> dict:
    """单文本文件（.md/.txt/.tagged.md）入库：读文本 → 分块 → 向量化 → upsert。

    复用 split_text/get_embedder/store.upsert，跳过 PDF/OCR 逻辑。
    保留标注符号（〖@人名〗〖=地名〗⟦◈动词⟧等），利于实体问答。

    collection=None → 默认集合（旧行为不变）。
    """
    p = Path(path)
    if not p.exists():
        return {"status": "rejected", "reason": "文件不存在"}
    try:
        text = p.read_text(encoding="utf-8")
    except Exception as e:
        return {"status": "rejected", "reason": f"读取失败: {e}"}
    if not text.strip():
        return {"status": "rejected", "reason": "空文件"}

    chunks = split_text(text)
    if not chunks:
        return {"status": "rejected", "reason": "无可入库文本"}

    col_name = collection or COLLECTION_NAME
    store = get_store(col_name)
    embedder = get_embedder()
    item_id, _ = resolve_item_id(store, p)   # C-7 内容地址化 + N-1 §5 同名异路径 guard
    title = title or p.stem
    ids = [f"{item_id}_{i}" for i in range(len(chunks))]
    metadatas = [
        {
            "item_id": item_id,
            "title": title,
            "source_file": str(p),
            "chunk_index": i,
            "coverage": 1.0,
            "source": "text",
            "collection": col_name,
        }
        for i in range(len(chunks))
    ]
    embeddings = embedder.encode(chunks, normalize_embeddings=True, batch_size=32, show_progress_bar=False).tolist()
    try:
        store.delete_by_item(item_id)
    except Exception:
        pass
    store.upsert(ids=ids, embeddings=embeddings, documents=chunks, metadatas=metadatas)

    prog = load_progress()
    prog[str(item_id)] = {
        "mtime": p.stat().st_mtime,
        "title": title,
        "chunks": len(chunks),
        "source": "text",
        "source_file": str(p),
        "collection": col_name,
    }
    save_progress(prog)
    return {"status": "ok", "chunks": len(chunks), "coverage": 1.0, "source": "text",
            "title": title, "item_id": item_id, "collection": col_name}


def ingest_paths(paths: list[str | Path], collection: str | None = None,
                 title_prefix: str = "") -> dict:
    """批量入库（C-2）。逐个调 ingest_text_file/ingest_pdf，汇总结果。

    不走本地路径白名单（白名单只约束**跨进程**的 /ingest/local 端点）；
    本函数是进程内调用，由调用方自行控制输入来源。
    """
    ok, failed, total_chunks = 0, [], 0
    for raw in paths:
        p = Path(raw)
        try:
            if p.suffix.lower() == ".pdf":
                r = ingest_pdf(p, title=(title_prefix + p.stem) if title_prefix else None,
                               collection=collection)
            else:
                r = ingest_text_file(p, title=(title_prefix + p.stem) if title_prefix else None,
                                     collection=collection)
        except Exception as e:
            failed.append({"file": str(p), "error": f"{type(e).__name__}: {e}"})
            continue
        if r.get("status") == "ok":
            ok += 1
            total_chunks += int(r.get("chunks", 0))
        else:
            failed.append({"file": str(p), "error": r.get("reason", str(r))})
    return {"status": "ok", "ingested": ok, "failed": len(failed),
            "chunks": total_chunks, "collection": collection or COLLECTION_NAME,
            "failures": failed[:50]}


if __name__ == "__main__":
    main()
