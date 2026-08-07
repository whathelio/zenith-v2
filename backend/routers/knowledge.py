"""Knowledge API - RAG gateway proxy"""
from fastapi import APIRouter, HTTPException, Body, UploadFile, File
from fastapi.responses import JSONResponse
from .. import knowledge_service

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("/health")
async def knowledge_health():
    try:
        return await knowledge_service.health()
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e), "code": "GATEWAY_DOWN"})


@router.get("/status")
async def knowledge_status():
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get("http://127.0.0.1:8788/health")
            return {"available": resp.status_code == 200}
    except Exception:
        return {"available": False}


@router.post("/search")
async def knowledge_search(data: dict = Body(default=None)):
    q = (data or {}).get("question", "").strip()
    if not q:
        raise HTTPException(400, "question is required")
    return await knowledge_service.search(q)


@router.post("/ingest")
async def knowledge_ingest(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(400, "missing filename")
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(400, "pdf too large")
    result = await knowledge_service.ingest_pdf(file.filename, content)
    if result.get("code") in ("GATEWAY_DOWN", "GATEWAY_TIMEOUT", "INGEST_ERROR"):
        raise HTTPException(503, result.get("error", "kb service down"))
    if result.get("error"):
        raise HTTPException(400, result["error"])
    return result


@router.get("/documents")
async def knowledge_list_docs():
    return await knowledge_service.list_docs()


@router.get("/documents/{item_id}/chunks")
async def knowledge_get_doc_chunks(item_id: int):
    return await knowledge_service.get_doc_chunks(item_id)


@router.post("/tasks")
async def knowledge_create_task(data: dict = Body(default=None)):
    if not data or not data.get("type"):
        raise HTTPException(400, "type is required")
    return await knowledge_service.create_task(data["type"], data.get("payload", {}))


@router.get("/tasks")
async def knowledge_list_tasks(status: str = "", limit: int = 20):
    return await knowledge_service.list_tasks(status or None, limit)


@router.get("/tasks/{task_id}")
async def knowledge_get_task(task_id: str):
    return await knowledge_service.get_task(task_id)
