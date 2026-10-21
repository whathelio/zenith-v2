"""
Zenith 私有知识 API 中台（最小契约版）

对应评审报告 §2.3 承重缺失点：API 中台只有端点名，无契约。
本模块实现最小安全契约：
- 认证：X-API-Key 头
- 健康检查：GET /health
- 统一错误体：{"error": "msg", "code": "..."}
- 异步任务：POST /tasks + GET /tasks/{id}（对应 §4 轻量队列）
- 知识端点：/search（RAG）、/wiki（LLM Wiki）、/agent（占位）

依赖：
    pip install fastapi uvicorn
运行：
    export ZENITH_API_KEY="your-token"
    python api_gateway.py
    # 或
    uvicorn api_gateway:app --host 0.0.0.0 --port 8788
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Header, HTTPException, Request, UploadFile, File, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from task_queue import TaskQueue, DEFAULT_DB

# 认证密钥：绝不允许 "test-key" 之类的公开默认值。
# 原因：认证面上的默认密钥 = 已知后门——任何读过源码的人都可以直接调用
# 全部知识端点（检索/入库/删除任务）。因此未设置 ZENITH_API_KEY 时
# 生成一次性随机密钥（仅本次进程有效，重启即变），并打 WARNING 提示固定设置；
# 调用方（backend/knowledge_service.py、start.py 注入的子进程环境）必须使用
# 同一把 key，否则会 401——这是「显式失败」而非「静默放行」。
_env_key = os.environ.get("ZENITH_API_KEY", "").strip()
if _env_key:
    API_KEY = _env_key
else:
    API_KEY = secrets.token_urlsafe(32)
    print(
        "[api_gateway] WARNING: 未设置 ZENITH_API_KEY，本次使用一次性随机密钥"
        "（重启后失效，无法与其他组件共享）。生产/联调请设置固定值："
        'export ZENITH_API_KEY="your-token"',
        file=sys.stderr,
    )
PORT = int(os.environ.get("ZENITH_API_PORT", "8788"))
DB_PATH = os.environ.get("ZENITH_TASK_DB", str(DEFAULT_DB))
UPLOAD_DIR = Path(os.environ.get("ZENITH_UPLOAD_DIR", "./zenith_rag/uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_MB = int(os.environ.get("ZENITH_MAX_UPLOAD_MB", "20"))
MAX_BATCH_FILES = int(os.environ.get("ZENITH_MAX_BATCH_FILES", "200"))

app = FastAPI(title="Zenith Knowledge API", version="0.2.0")
q = TaskQueue(DB_PATH)


# ------------------------------------------------------------------
# 上传落盘（路径穿越防护，2026-09-11 修复；/ingest 与 /ingest/batch 共用）
# ------------------------------------------------------------------
_ALLOWED_EXT = (".pdf", ".md", ".txt")


def _safe_save_name(filename: str | None) -> str:
    """只取基名，禁止目录成分与 ..（原样拼接可把文件写到 UPLOAD_DIR 之外）。"""
    raw = (filename or "").replace("\\", "/")
    name = raw.rsplit("/", 1)[-1].strip()
    if not name or name in (".", ".."):
        raise ValueError("非法文件名")
    return name


def _accepts(name: str) -> bool:
    return name.lower().endswith(_ALLOWED_EXT)


def _save_bytes(name: str, data: bytes) -> Path:
    save_path = UPLOAD_DIR / name
    # 兜底校验：解析后的落点必须仍在 UPLOAD_DIR 之内
    if UPLOAD_DIR.resolve() != save_path.resolve().parent:
        raise ValueError("非法文件名")
    save_path.write_bytes(data)
    return save_path


# ------------------------------------------------------------------
# 认证
# ------------------------------------------------------------------
def require_key(x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


# ------------------------------------------------------------------
# 统一错误处理
# ------------------------------------------------------------------
@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail, "code": f"HTTP_{exc.status_code}"},
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": str(exc), "code": "INTERNAL"},
    )


# ------------------------------------------------------------------
# 健康检查
# ------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "service": "zenith-knowledge-api", "version": "0.2.0"}


# ------------------------------------------------------------------
# 请求体
# ------------------------------------------------------------------
class SearchReq(BaseModel):
    question: str
    top_k: Optional[int] = 5
    collection: Optional[str] = None


class WikiReq(BaseModel):
    question: str


class AgentReq(BaseModel):
    message: str
    context: Optional[dict] = None


class TaskReq(BaseModel):
    type: str  # search / wiki / agent / ingest
    payload: dict


class LocalIngestReq(BaseModel):
    """本地路径入库（需 ZENITH_INGEST_ROOTS 白名单，默认关闭）。"""
    paths: List[str]
    collection: Optional[str] = None
    title_prefix: str = ""


# ------------------------------------------------------------------
# 同步知识端点（适合快速验证，长任务请走 /tasks）
# ------------------------------------------------------------------
@app.post("/search", dependencies=[])
def search(req: SearchReq, x_api_key: str = Header(default=None, alias="X-API-Key")):
    require_key(x_api_key)
    try:
        from zotero_parse_rag_core import answer

        # 查询翻译桥（2026-10-08 新增，**可选**）：
        # 嵌入模型是 bge-small-zh（纯中文），而英文语料（nomad_ref）上中文提问
        # 相似度仅 0.42–0.50 → 低于地板。此处先把 CJK 提问译成英文再检索。
        # ⚠️ 默认关闭（ZENITH_QUERY_BRIDGE=1 才生效）；不可用时**必须静默回退**，
        #    绝不能因为桥接出问题而让 /search 整体失败。
        _q, _bridge = req.question, None
        try:
            from query_bridge import maybe_bridge
            _q, _bridge = maybe_bridge(req.question, req.collection)
        except Exception as _e:                                   # noqa: BLE001
            print(f"[bridge] 不可用，跳过（保持旧行为）: {_e}", file=sys.stderr)

        # 🔴 诊断开关（ZENITH_SEARCH_DEBUG=1）：把**检索内部数字**打到 stderr。
        # 起因：同一 query 走网关时好时坏（"未找到"），而干净进程 8/8 稳定，
        # 必须看到 floor / docs 数 / 相似度 才能定位，否则只能猜。
        _ans = answer(_q, collection=req.collection)
        if os.environ.get("ZENITH_SEARCH_DEBUG", "0") == "1":
            try:
                from zotero_parse_rag_core import (get_store, get_embedder,
                                                   min_score_for, TOP_K)
                _st = get_store(req.collection)
                _em = get_embedder()
                _e = _em.encode([_q], normalize_embeddings=True).tolist()[0]
                _r = _st.query(_e, top_k=TOP_K)
                _d = (_r.get("distances") or [[]])[0]
                _sims = [round(1.0 - float(x), 4) for x in _d]
                print(f"[dbg] q={_q!r} coll={req.collection!r} "
                      f"floor={min_score_for(req.collection)} count={_st.count()} "
                      f"n={len(_r['documents'][0])} sims={_sims} "
                      f"titles={[m.get('title') for m in _r['metadatas'][0]]} "
                      f"ans={'MISS' if '未找到' in str(_ans) else 'HIT'}", file=sys.stderr)
            except Exception as _e2:                              # noqa: BLE001
                print(f"[dbg] 诊断失败: {_e2}", file=sys.stderr)

        _out = {"answer": _ans, "collection": req.collection or "default"}
        if _bridge:
            _out["bridge"] = _bridge
        return _out
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "code": "SEARCH_FAIL"})


@app.post("/wiki", dependencies=[])
def wiki(req: WikiReq, x_api_key: str = Header(default=None, alias="X-API-Key")):
    require_key(x_api_key)
    try:
        import llm_wiki_compiler as w
        import io
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            w.query(req.question)
        return {"answer": buf.getvalue()}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "code": "WIKI_FAIL"})


@app.post("/agent", dependencies=[])
def agent(req: AgentReq, x_api_key: str = Header(default=None, alias="X-API-Key")):
    require_key(x_api_key)
    # 占位：后续接入 Zenith Agent Core
    return {"answer": "(stub) agent 未接入", "message": req.message}


# ------------------------------------------------------------------
# 文件上传入库（PDF 含入库前审查；Markdown/文本直接分块嵌入）
# C-4/C-1/C-2 改造（2026-10-08）：collection 参数化 + 单文件/批量/本地路径三入口
# ------------------------------------------------------------------
@app.post("/ingest")
async def ingest_file(file: UploadFile = File(...),
                      collection: Optional[str] = Query(default=None),
                      x_api_key: str = Header(default=None, alias="X-API-Key")):
    require_key(x_api_key)
    if not file.filename:
        return JSONResponse(status_code=400, content={"error": "missing filename", "code": "BAD_TYPE"})
    try:
        safe_name = _safe_save_name(file.filename)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e), "code": "BAD_NAME"})
    if not _accepts(safe_name):
        return JSONResponse(status_code=400, content={"error": "仅支持 PDF / Markdown / 文本", "code": "BAD_TYPE"})
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        return JSONResponse(status_code=413, content={"error": f"文件超过 {MAX_UPLOAD_MB}MB", "code": "TOO_LARGE"})
    try:
        save_path = _save_bytes(safe_name, data)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e), "code": "BAD_NAME"})
    return _ingest_one(save_path, safe_name, collection)


def _ingest_one(save_path: Path, safe_name: str, collection: Optional[str]):
    """单文件分派：PDF 走审查+提取，.md/.txt 直接分块。返回 dict 或 JSONResponse。"""
    try:
        title = safe_name.rsplit(".", 1)[0]
        if safe_name.lower().endswith(".pdf"):
            from zotero_parse_rag_core import review_pdf, ingest_pdf
            review = review_pdf(save_path)
            if not review.get("passed"):
                return {"status": "rejected", **review}
            return ingest_pdf(save_path, title=title, collection=collection)
        from zotero_parse_rag_core import ingest_text_file
        return ingest_text_file(save_path, title=title, collection=collection)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "code": "INGEST_FAIL"})


@app.post("/ingest/batch")
async def ingest_batch(files: List[UploadFile] = File(...),
                       collection: Optional[str] = Query(default=None),
                       x_api_key: str = Header(default=None, alias="X-API-Key")):
    """批量上传入库（C-2）。单请求最多 MAX_BATCH_FILES 个文件。

    返回逐文件结果；单个文件失败不影响其余文件。
    """
    require_key(x_api_key)
    if len(files) > MAX_BATCH_FILES:
        return JSONResponse(status_code=413, content={
            "error": f"单批最多 {MAX_BATCH_FILES} 个文件（收到 {len(files)}）",
            "code": "TOO_MANY"})
    ok, failed, chunks = 0, [], 0
    for f in files:
        try:
            name = _safe_save_name(f.filename)
            if not _accepts(name):
                raise ValueError("仅支持 PDF / Markdown / 文本")
            data = await f.read()
            if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
                raise ValueError(f"文件超过 {MAX_UPLOAD_MB}MB")
            p = _save_bytes(name, data)
            r = _ingest_one(p, name, collection)
            if isinstance(r, dict):
                if r.get("status") == "ok":
                    ok += 1
                    chunks += int(r.get("chunks", 0))
                else:
                    failed.append({"file": name, "error": r.get("reason", str(r))})
            else:
                failed.append({"file": name, "error": "ingest 返回非 dict"})
        except Exception as e:
            failed.append({"file": getattr(f, "filename", "?"),
                           "error": f"{type(e).__name__}: {e}"})
    return {"status": "ok", "ingested": ok, "failed": len(failed), "chunks": chunks,
            "collection": collection or "default", "failures": failed[:50]}


@app.post("/ingest/local")
def ingest_local(req: LocalIngestReq,
                 x_api_key: str = Header(default=None, alias="X-API-Key")):
    """本地路径批量入库（C-2）。

    🔴 默认**关闭**：需设 `ZENITH_INGEST_ROOTS`（os.pathsep 分隔的白名单根目录），
    否则返回 403。这是为了避免「任意本地文件读取」面。
    """
    require_key(x_api_key)
    try:
        from zotero_parse_rag_core import (ingest_paths, INGEST_ROOTS,
                                           _assert_ingest_allowed)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "code": "IMPORT_FAIL"})
    if not INGEST_ROOTS:
        return JSONResponse(status_code=403, content={
            "error": "本地路径入库未启用（需设 ZENITH_INGEST_ROOTS 白名单）",
            "code": "LOCAL_DISABLED"})
    allowed, denied = [], []
    for raw in req.paths:
        try:
            _assert_ingest_allowed(Path(raw))
            allowed.append(raw)
        except PermissionError as e:
            denied.append({"path": raw, "error": str(e)})
    if not allowed:
        return JSONResponse(status_code=403, content={
            "error": "全部路径不在白名单内", "code": "LOCAL_DENIED",
            "denied": denied[:50]})
    res = ingest_paths(allowed, collection=req.collection, title_prefix=req.title_prefix)
    res["denied"] = denied[:50]
    return res


# ------------------------------------------------------------------
# 文档段落读取（逐段学习用）
# ------------------------------------------------------------------
@app.get("/documents")
def list_docs(collection: Optional[str] = Query(default=None),
              x_api_key: str = Header(default=None, alias="X-API-Key")):
    """列出已入库文档（从 progress.json 读取）。

    collection=None → 只列**默认集合**（与改造前行为一致）。
    """
    require_key(x_api_key)
    try:
        from zotero_parse_rag_core import load_progress, DEFAULT_COLLECTION
        prog = load_progress()
        want = collection or DEFAULT_COLLECTION
        docs = []
        for item_id, info in prog.items():
            if not isinstance(info, dict):
                continue
            col = info.get("collection") or DEFAULT_COLLECTION
            if col != want:
                continue
            docs.append({
                "item_id": int(item_id),
                "title": info.get("title", item_id),
                "chunks": info.get("chunks", 0),
                "source": info.get("source", ""),
                "source_file": info.get("source_file", ""),
                "collection": col,
            })
        docs.sort(key=lambda d: d["title"])
        return {"docs": docs, "collection": want, "total": len(docs)}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "code": "DOCS_FAIL"})


@app.get("/documents/{item_id}/chunks")
def get_doc_chunks(item_id: int, collection: Optional[str] = Query(default=None),
                   x_api_key: str = Header(default=None, alias="X-API-Key")):
    """按 item_id 返回该文档全部段落文本（按 chunk_index 排序）"""
    require_key(x_api_key)
    try:
        from zotero_parse_rag_core import get_store
        store = get_store(collection)
        chunks = store.get_by_item(item_id)
        if not chunks:
            return JSONResponse(status_code=404, content={"error": "文档不存在或未入库", "code": "NOT_FOUND"})
        return {"item_id": item_id, "chunks": chunks, "total": len(chunks)}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "code": "CHUNKS_FAIL"})


# ------------------------------------------------------------------
# 集合管理（C-4 改造）
# ------------------------------------------------------------------
@app.get("/collections")
def get_collections(x_api_key: str = Header(default=None, alias="X-API-Key")):
    """列出向量库内全部集合 + 默认集合 + 本地路径入库是否启用。"""
    require_key(x_api_key)
    try:
        from zotero_parse_rag_core import (list_rag_collections, DEFAULT_COLLECTION,
                                           INGEST_ROOTS)
        return {"collections": list_rag_collections(),
                "default": DEFAULT_COLLECTION,
                "local_ingest_enabled": bool(INGEST_ROOTS),
                "ingest_roots": [str(r) for r in INGEST_ROOTS]}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "code": "COLLECTIONS_FAIL"})


@app.post("/collections/{name}/archive")
def archive_collection_ep(name: str, x_api_key: str = Header(default=None, alias="X-API-Key")):
    """归档集合 = **重命名为墓碑**，不物理删除（对应「零删除」红线）。

    真正的物理删除需人工另行处置，本端点不提供。
    """
    require_key(x_api_key)
    try:
        from zotero_parse_rag_core import archive_collection, DEFAULT_COLLECTION
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "code": "IMPORT_FAIL"})
    if name == DEFAULT_COLLECTION:
        return JSONResponse(status_code=400, content={
            "error": "拒绝对默认集合执行归档", "code": "PROTECTED"})
    r = archive_collection(name)
    return JSONResponse(status_code=200 if r.get("status") == "ok" else 400, content=r)


# ------------------------------------------------------------------
# 异步任务端点（对应 §4 轻量队列）
# ------------------------------------------------------------------
@app.post("/tasks")
def create_task(req: TaskReq, x_api_key: str = Header(default=None, alias="X-API-Key")):
    """建异步任务。

    C-5 改造（2026-10-08）：新增 `ingest` 类型。
    payload = {"paths": [...], "collection": "..."}（本地路径，受 ZENITH_INGEST_ROOTS 约束）
    或 {"dir": "...", "pattern": "*.md", "collection": "..."}（目录批量）。
    由 task_worker.py 的 handler_ingest 消费 —— 大批量入库终于可以脱离 HTTP 同步阻塞。
    """
    require_key(x_api_key)
    if req.type not in ("search", "wiki", "agent", "ingest"):
        return JSONResponse(status_code=400, content={"error": "unknown task type", "code": "BAD_TYPE"})
    task_id = q.create(req.type, req.payload)
    return {"task_id": task_id, "status": "pending"}


@app.get("/tasks/{task_id}")
def get_task(task_id: str, x_api_key: str = Header(default=None, alias="X-API-Key")):
    require_key(x_api_key)
    t = q.get(task_id)
    if not t:
        return JSONResponse(status_code=404, content={"error": "task not found", "code": "NOT_FOUND"})
    return t


@app.get("/tasks")
def list_tasks(status: Optional[str] = None, limit: int = 20,
               x_api_key: str = Header(default=None, alias="X-API-Key")):
    require_key(x_api_key)
    return {"tasks": q.list(status, limit)}


# ------------------------------------------------------------------
# 入口
# ------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    # 🔴 2026-10-14：默认绑回环。此前写死 0.0.0.0，把知识端点（含入库/删除任务）暴露到
    #   整个网段，且与主服务 start.py:1408 的 127.0.0.1 不一致。
    #   确需 LAN 访问时：设 `ZENITH_API_HOST=0.0.0.0`（并要求 .env 里 ZENITH_API_KEY 为强随机值）。
    uvicorn.run(app, host=os.environ.get("ZENITH_API_HOST", "127.0.0.1"), port=PORT)
