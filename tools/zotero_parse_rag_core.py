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
import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, List, Protocol, Tuple

# ------------------------------------------------------------------
# CONFIG（可用环境变量覆盖）
# ------------------------------------------------------------------
ZOTERO_DATA_DIR = Path(os.environ.get("ZOTERO_DATA_DIR", str(Path.home() / "Zotero")))
ZOTERO_SQLITE = ZOTERO_DATA_DIR / "zotero.sqlite"
STORAGE_DIR = ZOTERO_DATA_DIR / "storage"

_DEFAULT_RAG_WORK_DIR = Path(r"D:\dshs\zenith_rag_new") if os.name == "nt" else Path("./zenith_rag")
WORK_DIR = Path(os.environ.get("ZENITH_RAG_WORK_DIR", str(_DEFAULT_RAG_WORK_DIR)))
CHROMA_PATH = WORK_DIR / "chroma_db"
PROGRESS_FILE = WORK_DIR / "progress.json"
COLLECTION_NAME = "zotero_papers"

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
    """Chroma 实现。后续可新增 LEANNStore / ZvecStore / CairnStore。"""

    def __init__(self, path: Path, name: str):
        import chromadb
        self._client = chromadb.PersistentClient(path=str(path))
        self._col = self._client.get_or_create_collection(
            name=name, metadata={"hnsw:space": "cosine"}
        )

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


def get_store() -> VectorStore:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    return ChromaStore(CHROMA_PATH, COLLECTION_NAME)


def get_embedder():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBED_MODEL)


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
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(prog, f, ensure_ascii=False, indent=2)


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
                force: bool = False):
    try:
        from tqdm import tqdm
    except ImportError:
        tqdm = lambda x, **k: x  # noqa: E731

    store = get_store()
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
def answer(question: str) -> str:
    import openai
    store = get_store()
    embedder = get_embedder()
    q_emb = embedder.encode([question], normalize_embeddings=True).tolist()[0]
    results = store.query(q_emb, top_k=TOP_K)
    docs = results["documents"][0]
    metas = results["metadatas"][0]
    if not docs:
        return "知识库中未找到相关内容。"

    context = "\n\n---\n\n".join(
        f"[来源: {m.get('title')}, 文件: {m.get('source_file')}]\n{d}"
        for d, m in zip(docs, metas)
    )
    prompt = (
        "你是 Zenith，一个基于本地文献知识库回答问题的助手。\n"
        "请仅根据下面提供的文献片段回答问题，并引用来源。\n"
        "如果片段中没有答案，请明确说明“知识库中未找到相关内容”。\n\n"
        f"---\n{context}\n---\n\n"
        f"用户问题：{question}"
    )
    client = openai.OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)
    resp = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": "简洁回答，标注引用来源。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.5,
        max_tokens=1024,
    )
    return resp.choices[0].message.content


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
    args = parser.parse_args()

    if args.rebuild:
        import shutil
        if CHROMA_PATH.exists():
            shutil.rmtree(CHROMA_PATH)
        if PROGRESS_FILE.exists():
            PROGRESS_FILE.unlink()
        print("[info] 已清空向量库与进度，开始重建")
        args.build = True

    if args.list_collections:
        for name, cnt in list_collections():
            print(f"{cnt:5d}  {name}")
    elif args.stats:
        cmd_stats()
    elif args.test_pdf:
        cmd_test_pdf(args.test_pdf)
    elif args.ingest_pdf:
        import json as _json
        print(_json.dumps(ingest_pdf(args.ingest_pdf), ensure_ascii=False, indent=2))
    elif args.build:
        build_index(args.collection, args.workers, args.limit, args.force)
    elif args.query:
        print(answer(args.query))
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


def ingest_pdf(pdf_path: str | Path, title: str | None = None) -> dict:
    """单 PDF 入库：提取 → 分块 → 向量化 → upsert。返回状态。"""
    p = Path(pdf_path)
    review = review_pdf(p)
    if not review["passed"]:
        return {"status": "rejected", **review}

    text, coverage, source = extract_text_with_fallback(p)
    chunks = split_text(text)
    if not chunks:
        return {"status": "rejected", "reason": "无可入库文本", "coverage": coverage}

    store = get_store()
    embedder = get_embedder()
    import time as _t
    item_id = abs(hash(p.name + str(p.stat().st_mtime))) % 1000000
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
        }
        for i in range(len(chunks))
    ]
    embeddings = embedder.encode(chunks, normalize_embeddings=True, batch_size=32, show_progress_bar=False).tolist()
    store.upsert(ids=ids, embeddings=embeddings, documents=chunks, metadatas=metadatas)

    prog = load_progress()
    prog[str(item_id)] = {
        "mtime": p.stat().st_mtime,
        "title": title,
        "chunks": len(chunks),
        "source": source,
        "source_file": str(p),
    }
    save_progress(prog)
    return {"status": "ok", "chunks": len(chunks), "coverage": round(coverage, 3), "source": source, "title": title}


if __name__ == "__main__":
    main()
