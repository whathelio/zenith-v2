"""Memories API — 记忆 CRUD + 归档"""
from fastapi import APIRouter, Body, HTTPException
from .. import database as db

router = APIRouter(prefix="/api/memories", tags=["memories"])


@router.get("")
async def get_memories(type_: str = "", search: str = "", include_archived: bool = False):
    """列出 / 搜索记忆。

    默认排除已归档条目（archived=1）—— 归档语义是「退出日常检索」。
    需要看归档内容时传 `include_archived=true`（2026-09-11 新增）。
    """
    if search:
        return db.mem_search(search)
    return db.mem_list(type_=type_, include_archived=include_archived)


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


@router.post("/{mid}/archive")
async def archive_memory(mid: int):
    """归档一条记忆：保留内容、退出日常检索，可随时恢复。

    2026-09-11 新增。与 DELETE 的区别是**可逆** —— 归档是「收起来」，
    删除是「扔掉」。按项目零删除原则，批量整理建议优先用归档。
    """
    if not db.mem_archive(mid, archived=True):
        raise HTTPException(status_code=404, detail=f"记忆 ID:{mid} 不存在")
    return {"success": True, "memory": db.mem_get(mid), "archived": True}


@router.delete("/{mid}/archive")
async def unarchive_memory(mid: int):
    """取消归档，恢复为参与检索。"""
    if not db.mem_archive(mid, archived=False):
        raise HTTPException(status_code=404, detail=f"记忆 ID:{mid} 不存在")
    return {"success": True, "memory": db.mem_get(mid), "archived": False}


@router.delete("/{mid}")
async def delete_memory(mid: int):
    """⚠️ 硬删除，不可恢复。批量整理请优先用 POST /{mid}/archive。"""
    if not db.mem_del(mid):
        raise HTTPException(status_code=404, detail=f"记忆 ID:{mid} 不存在")
    return {"success": True}
