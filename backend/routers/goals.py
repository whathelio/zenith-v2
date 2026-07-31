"""Goals API — 目标 CRUD + 统计"""
from fastapi import APIRouter, HTTPException, Body, Query
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
    if data is None:
        raise HTTPException(400, "Update data required")
    db.goal_update(gid, data)
    goal = db.goal_get(gid)
    if not goal:
        raise HTTPException(404, "Goal not found")
    return {"success": True, **goal}


@router.delete("/{gid}")
async def delete_goal(gid: int):
    db.goal_del(gid)
    return {"success": True}


@router.get("/stats")
async def get_goal_stats_all():
    """聚合所有目标的统计（避免旧 goal_stats_all 缺失导致的 500）"""
    out = {}
    for g in db.goal_list():
        st = db.goal_get_stats(g["id"])
        if st:
            out[g["id"]] = st
    return out


@router.get("/{gid}/stats")
async def get_goal_stat(gid: int):
    st = db.goal_get_stats(gid)
    if st is None:
        raise HTTPException(404, "Goal not found")
    return st


@router.get("/{gid}/schedules")
async def list_goal_schedules(gid: int, status: str = Query("")):
    all_schedules = db.sch_list(status=status)
    return [s for s in all_schedules if s.get("goal_id") == gid]


@router.get("/{gid}")
async def get_goal(gid: int):
    goal = db.goal_get(gid)
    if not goal:
        raise HTTPException(404, "Goal not found")
    return goal
