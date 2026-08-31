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
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Request, UploadFile, File
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from task_queue import TaskQueue, DEFAULT_DB

API_KEY = os.environ.get("ZENITH_API_KEY", "test-key")
PORT = int(os.environ.get("ZENITH_API_PORT", "8788"))
DB_PATH = os.environ.get("ZENITH_TASK_DB", str(DEFAULT_DB))
UPLOAD_DIR = Path(os.environ.get("ZENITH_UPLOAD_DIR", "./zenith_rag/uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_MB = 20

app = FastAPI(title="Zenith Knowledge API", version="0.1.0")
q = TaskQueue(DB_PATH)


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
    return {"status": "ok", "service": "zenith-knowledge-api", "version": "0.1.0"}


# ------------------------------------------------------------------
# 请求体
# ------------------------------------------------------------------
class SearchReq(BaseModel):
    question: str
    top_k: Optional[int] = 5


class WikiReq(BaseModel):
    question: str


class AgentReq(BaseModel):
    message: str
    context: Optional[dict] = None


class TaskReq(BaseModel):
    type: str  # search / wiki / agent
    payload: dict


# ------------------------------------------------------------------
# 同步知识端点（适合快速验证，长任务请走 /tasks）
# ------------------------------------------------------------------
@app.post("/search", dependencies=[])
def search(req: SearchReq, x_api_key: str = Header(default=None, alias="X-API-Key")):
    require_key(x_api_key)
    try:
        from zotero_parse_rag_core import answer
        return {"answer": answer(req.question)}
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
# ------------------------------------------------------------------
@app.post("/ingest")
async def ingest_file(file: UploadFile = File(...), x_api_key: str = Header(default=None, alias="X-API-Key")):
    require_key(x_api_key)
    if not file.filename:
        return JSONResponse(status_code=400, content={"error": "missing filename", "code": "BAD_TYPE"})
    lower = file.filename.lower()
    is_pdf = lower.endswith(".pdf")
    is_text = lower.endswith(".md") or lower.endswith(".txt")
    if not (is_pdf or is_text):
        return JSONResponse(status_code=400, content={"error": "仅支持 PDF / Markdown / 文本", "code": "BAD_TYPE"})
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        return JSONResponse(status_code=413, content={"error": f"文件超过 {MAX_UPLOAD_MB}MB", "code": "TOO_LARGE"})
    save_path = UPLOAD_DIR / file.filename
    save_path.write_bytes(data)
    try:
        title = file.filename.rsplit(".", 1)[0]
        if is_pdf:
            from zotero_parse_rag_core import review_pdf, ingest_pdf
            review = review_pdf(save_path)
            if not review.get("passed"):
                return {"status": "rejected", **review}
            return ingest_pdf(save_path, title=title)
        else:
            from zotero_parse_rag_core import ingest_text_file
            return ingest_text_file(save_path, title=title)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "code": "INGEST_FAIL"})


# ------------------------------------------------------------------
# 文档段落读取（逐段学习用）
# ------------------------------------------------------------------
@app.get("/documents")
def list_docs(x_api_key: str = Header(default=None, alias="X-API-Key")):
    """列出已入库文档（从 progress.json 读取）"""
    require_key(x_api_key)
    try:
        from zotero_parse_rag_core import load_progress
        prog = load_progress()
        docs = []
        for item_id, info in prog.items():
            docs.append({
                "item_id": int(item_id),
                "title": info.get("title", item_id),
                "chunks": info.get("chunks", 0),
                "source": info.get("source", ""),
                "source_file": info.get("source_file", ""),
            })
        docs.sort(key=lambda d: d["title"])
        return {"docs": docs}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "code": "DOCS_FAIL"})


@app.get("/documents/{item_id}/chunks")
def get_doc_chunks(item_id: int, x_api_key: str = Header(default=None, alias="X-API-Key")):
    """按 item_id 返回该文档全部段落文本（按 chunk_index 排序）"""
    require_key(x_api_key)
    try:
        from zotero_parse_rag_core import get_store
        store = get_store()
        chunks = store.get_by_item(item_id)
        if not chunks:
            return JSONResponse(status_code=404, content={"error": "文档不存在或未入库", "code": "NOT_FOUND"})
        return {"item_id": item_id, "chunks": chunks, "total": len(chunks)}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "code": "CHUNKS_FAIL"})


# ------------------------------------------------------------------
# 异步任务端点（对应 §4 轻量队列）
# ------------------------------------------------------------------
@app.post("/tasks")
def create_task(req: TaskReq, x_api_key: str = Header(default=None, alias="X-API-Key")):
    require_key(x_api_key)
    if req.type not in ("search", "wiki", "agent"):
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
    uvicorn.run(app, host="0.0.0.0", port=PORT)
