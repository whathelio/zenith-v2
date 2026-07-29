"""Memories API — 记忆 CRUD"""
from fastapi import APIRouter
from .. import database as db

router = APIRouter(prefix="/api/memories", tags=["memories"])


@router.get("")
async def get_memories(type_: str = "", search: str = ""):
    if search:
        return db.mem_search(search)
    return db.mem_list(type_=type_)


@router.delete("/{mid}")
async def delete_memory(mid: int):
    db.mem_del(mid)
    return {"success": True}
