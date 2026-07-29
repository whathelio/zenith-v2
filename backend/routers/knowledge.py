"""Knowledge API — RAG 网关代理"""
from fastapi import APIRouter, HTTPException, Body
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


@router.get("/query")
async def knowledge_query(q: str = "", namespace: str = "default", top_k: int = 3):
    if not q.strip():
        raise HTTPException(400, "q is required")
    return await knowledge_service.query(q, namespace, top_k)


@router.get("/stats")
async def knowledge_stats():
    return await knowledge_service.stats()
