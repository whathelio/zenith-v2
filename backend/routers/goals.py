"""Goals API — 目标 CRUD + 统计"""
from fastapi import APIRouter, HTTPException, Body
from .. import database as db

router = APIRouter(prefix="/api/goals", tags=["goals"])


@router.get("")
async def get_goals():
    return db.goal_list()


@router.post("")
async def create_goal(data: dict = Body(...)):
    if not data.get("title"):
        raise HTTPException(400, "title 为必填字段")
    gid = db.goal_add(data)
    goal = db.goal_get(gid)
    return {"id": gid, **goal}


@router.put("/{gid}")
async def update_goal(gid: int, data: dict = Body(default=None)):
    db.goal_update(gid, data)
    goal = db.goal_get(gid)
    return {"success": True, **goal}


@router.delete("/{gid}")
async def delete_goal(gid: int):
    db.goal_del(gid)
    return {"success": True}


@router.get("/stats")
async def get_goal_stats():
    return db.goal_stats_all()


@router.get("/{gid}")
async def get_goal(gid: int):
    goal = db.goal_get(gid)
    if not goal:
        raise HTTPException(404, "Goal not found")
    return goal
