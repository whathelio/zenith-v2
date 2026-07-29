"""Notes API — 笔记 CRUD + 蒸馏"""
from fastapi import APIRouter, HTTPException, Body
from .. import database as db

router = APIRouter(prefix="/api/notes", tags=["notes"])


@router.get("")
async def get_notes(search: str = ""):
    return db.note_list(search=search)


@router.post("")
async def create_note(data: dict = Body(...)):
    if not data:
        data = {}
    if not data.get("title"):
        raise HTTPException(400, "title 为必填字段")
    nid = db.note_add(data)
    return {"id": nid, **data}


@router.put("/{nid}")
async def update_note(nid: int, data: dict = Body(default=None)):
    db.note_update(nid, data)
    return {"success": True}


@router.delete("/{nid}")
async def delete_note(nid: int):
    db.note_del(nid)
    return {"success": True}


@router.post("/{nid}/distill")
async def distill_note_endpoint(nid: int):
    from ..tools import _handle_distill_note
    return await _handle_distill_note({"note_id": nid})
