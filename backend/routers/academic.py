"""Academic API — 学术论文检索 + 本地 SQLite 缓存查询"""
from fastapi import APIRouter, HTTPException, Query

from .. import academic_service
from .. import database as db

router = APIRouter(prefix="/api/academic", tags=["academic"])


@router.get("/search")
async def academic_search(
    query: str = Query(..., min_length=1, description="检索词"),
    from_date: str = "",
    to_date: str = "",
    venue: str = "",
    limit: int = Query(10, ge=1, le=20),
):
    """检索学术论文（OpenAlex + Crossref），结果写入本地 SQLite。"""
    r = await academic_service.search_papers(
        query=query, from_date=from_date, to_date=to_date,
        venue=venue, limit=limit, store=True,
    )
    if not r.get("success"):
        raise HTTPException(400, r.get("error", "学术检索失败"))
    return r


@router.get("/lookup")
async def academic_lookup(doi: str = Query(..., min_length=3)):
    """按 DOI 查询题录并写入本地缓存。"""
    r = await academic_service.lookup_doi(doi, store=True)
    if not r.get("success"):
        raise HTTPException(404, r.get("error", "DOI 查询失败"))
    return r


@router.get("/papers")
async def academic_papers(
    query: str = "",
    venue: str = "",
    year_from: int = 0,
    year_to: int = 0,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """查询本地 SQLite 学术缓存。"""
    return academic_service.search_local(
        query=query, venue=venue, year_from=year_from,
        year_to=year_to, limit=limit, offset=offset,
    )


@router.get("/papers/{paper_id}")
async def academic_paper_detail(paper_id: int):
    """查询本地缓存中的单篇论文。"""
    paper = db.academic_paper_get(paper_id)
    if not paper:
        raise HTTPException(404, "论文不存在")
    return paper


@router.post("/papers/{paper_id}/enrich")
async def academic_paper_enrich(paper_id: int):
    """重新抓取一篇缓存论文的实现路径（arXiv/PDF/代码仓库）。"""
    paper = db.academic_paper_get(paper_id)
    if not paper:
        raise HTTPException(404, "论文不存在")
    try:
        enriched = await academic_service._enrich_paper(paper)
        db.academic_paper_upsert(enriched)
        return {"success": True, "paper": enriched}
    except Exception as e:
        raise HTTPException(502, f"增强失败: {e}")


@router.get("/stats")
async def academic_stats():
    """本地学术缓存统计。"""
    return db.academic_paper_stats()
