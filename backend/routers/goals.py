"""Goals API — 目标 CRUD + 统计"""
from fastapi import APIRouter, HTTPException, Body, Query
from .. import database as db
from ..validators.sanitize_guard import GUIDE_SHIELD, guard_store, refusal_text

router = APIRouter(prefix="/api/goals", tags=["goals"])


@router.get("")
async def get_goals():
    return db.goal_list()


@router.post("")
async def create_goal(data: dict = Body(...)):
    if not data.get("title"):
        raise HTTPException(400, "title 为必填字段")
    # 落库守卫（路由层，与 notes/schedules 路由同范式）：
    # database.goal_add 内已下沉一道守卫，此处保留是为了给出可读的 400 提示文案，
    # 而不是让用户看到下面那句兜底的「已拒绝写入」。
    risk = guard_store(f"{data.get('title', '') or ''}", field="goal")
    if risk:
        raise HTTPException(400, risk["message"])
    gid = db.goal_add(data)
    # 2026-09-16（判负补齐）：goal_add 下沉落库守卫后可能返回 -1（= 被拒绝，未写入）。
    # 不判负的后果不是「静默少写一条」而是**500**：下一行 goal_get(-1) 返回 None，
    # `{"id": gid, **goal}` 会抛 TypeError: argument of type 'NoneType' is not a mapping
    # —— 把「内容含密钥」伪装成服务故障，排查时毫无指向性。
    if gid < 0:
        raise HTTPException(400, refusal_text(
            "目标", action="未写入", guide=GUIDE_SHIELD,
        ))
    goal = db.goal_get(gid)
    return {"id": gid, **goal}


@router.put("/{gid}")
async def update_goal(gid: int, data: dict = Body(default=None)):
    if data is None:
        raise HTTPException(400, "Update data required")
    risk = guard_store(f"{data.get('title', '') or ''}", field="goal")
    if risk:
        raise HTTPException(400, risk["message"])
    # 2026-09-16（判负补齐）：goal_update 现按 sch_update 的布尔语义返回。
    # 不判负就会在未写入的情况下照回 {"success": True, **goal}（读的是旧值）——
    # 前端会显示「已保存」但标题仍是旧的，且无任何错误提示。
    if not db.goal_update(gid, data):
        raise HTTPException(400, refusal_text(
            "目标", action="未写入", guide=GUIDE_SHIELD,
        ))
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
