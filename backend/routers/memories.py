"""Memories API — 记忆 CRUD"""
from fastapi import APIRouter, Body, HTTPException
from .. import database as db

router = APIRouter(prefix="/api/memories", tags=["memories"])


@router.get("")
async def get_memories(type_: str = "", search: str = ""):
    if search:
        return db.mem_search(search)
    return db.mem_list(type_=type_)


@router.put("/{mid}")
async def update_memory(mid: int, data: dict = Body(default=None)):
    """更新单条记忆（内容/类型/重要性/关键词），只更新传入的非空字段。"""
    data = data or {}
    ok = db.mem_update(
        mid,
        content=data.get("content", ""),
        type_=data.get("type", ""),
        importance=int(data.get("importance") or 0),
        keywords=data.get("keywords", ""),
    )
    if not ok:
        raise HTTPException(status_code=404, detail="记忆不存在或内容被守卫拒绝")
    updated = db.mem_get(mid)
    return {"success": True, "memory": updated}


@router.delete("/{mid}")
async def delete_memory(mid: int):
    if not db.mem_del(mid):
        raise HTTPException(status_code=404, detail=f"记忆 ID:{mid} 不存在")
    return {"success": True}
