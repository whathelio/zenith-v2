"""Schedules API — 日程 CRUD + 提醒确认（日历/提醒 presets 由 app.py 提供）

⚠️ 2026-09-11 修正：本 router **只保留 app.py 未注册的路径**（从 12 条删到 7 条）。

删除的 5 条**全部被 app.py 遮蔽、从未执行过**：

    /api/reminders/presets    ← app.py:722
    /api/calendar/templates   ← app.py:749
    /api/calendar/week        ← app.py:754
    /api/calendar/month       ← app.py:827
    /api/calendar             ← app.py:1241

原因：`schedules.router` 在 `app.py:1475` 才 include，**晚于** app.py 里那批
`@app.get(...)` 装饰器；FastAPI/Starlette 按**注册顺序**匹配，先注册者胜。

**删除不改变任何运行时行为**（它们本就从未执行）。附带消除一个地雷：
被删的 `/api/calendar/week` 那版返回的是 dict-of-days，与前端契约不符 ——
一旦遮蔽关系反转就会直接打崩周视图。

用 `tools/audit/duplicate_routes.py` 可复现此检测。
"""
from fastapi import APIRouter, HTTPException, Body, Request

from .. import database as db
from ..validators.sanitize_guard import guard_store

router = APIRouter(tags=["schedules"])


@router.get("/api/schedules")
async def get_schedules(status: str = "", date_from: str = "", date_to: str = "", overdue: str = ""):
    items = db.sch_list(status=status, date_from=date_from, date_to=date_to)
    if overdue:
        from ..schedule_reminder import _parse_time
        from ..database import _now
        now = _now()
        filtered = []
        for s in items:
            st = s.get("start_time", "")
            start = _parse_time(st) if st else None
            if start is None:
                continue
            is_overdue = start < now and s.get("status") not in ("done", "cancelled")
            if overdue == "true" and is_overdue:
                filtered.append(s)
            elif overdue == "false" and not is_overdue:
                filtered.append(s)
        return filtered
    return items


@router.post("/api/schedules")
async def create_schedule(request: Request):
    data = await request.json() or {}
    if not data.get("title"):
        raise HTTPException(400, "title 为必填字段")
    # 落库守卫：拒绝明文密钥写入知识库
    risk = guard_store(f"{data.get('title', '')}\n{data.get('description', '')}")
    if risk:
        raise HTTPException(400, risk["message"])
    data["source"] = data.get("source", "manual")
    start_time = data.get("start_time", "")
    if start_time:
        from ..tools import _find_time_conflict, _suggest_alternative_time
        conflict = _find_time_conflict(start_time, data.get("end_time"))
        if conflict:
            suggestions = _suggest_alternative_time(start_time, data.get("end_time"))
            raise HTTPException(409, detail={
                "error": "时间冲突", "conflict_with": {"id": conflict.get("id"), "title": conflict.get("title"), "start_time": conflict.get("start_time")},
                "suggestions": suggestions[:3],
            })
    sid = db.sch_add(data)
    return {"id": sid, **data}


@router.put("/api/schedules/{sid}")
async def update_schedule(sid: int, data: dict = Body(default=None)):
    old = db.sch_get(sid)
    if not old:
        raise HTTPException(404, "日程不存在")
    if data:
        risk = guard_store(f"{data.get('title', '')}\n{data.get('description', '')}")
        if risk:
            raise HTTPException(400, risk["message"])
    if data.get("apply_to") == "instance" and old.get("recurrence"):
        instance = dict(old)
        instance.pop("id", None)
        instance["parent_id"] = old["id"]
        instance["recurrence"] = ""
        for k in ["title", "description", "start_time", "end_time", "location", "status", "priority", "importance", "category", "impact", "country", "remind_before", "goal_id"]:
            if k in data:
                instance[k] = data[k]
        new_id = db.sch_add(instance)
        return {"success": True, "instance_id": new_id, "message": "已创建独立实例"}
    db.sch_update(sid, data)
    if data.get("status") == "done":
        goal_id = data.get("goal_id") or old.get("goal_id")
        if goal_id:
            g = db.goal_get(goal_id)
            if g:
                strategy = g.get("strategy", "compound")
                current = float(g.get("current_value", 0))
                daily = float(g.get("daily_target", 5))
                if strategy == "linear":
                    # daily_target 统一按百分比理解：线性策略的每日固定增量 = 起始值 × 日化率
                    base = float(g.get("start_value") or current)
                    db.goal_update(goal_id, {"current_value": current + base * daily / 100})
                elif strategy == "compound":
                    db.goal_update(goal_id, {"current_value": current * (1 + daily / 100)})
    return {"success": True}


@router.delete("/api/schedules/{sid}")
async def delete_schedule(sid: int):
    db.sch_del(sid)
    return {"success": True}


@router.post("/api/schedules/{sid}/complete")
async def complete_schedule(sid: int):
    old = db.sch_get(sid)
    if not old:
        raise HTTPException(404, "日程不存在")
    db.sch_update(sid, {"status": "done"})
    return {"success": True}


@router.post("/api/schedules/ai-plan")
async def schedule_ai_plan(data: dict = Body(default=None)):
    from ..llm_client import plan_time
    text = (data or {}).get("text", "")
    if not text:
        raise HTTPException(400, "text is required")
    result = await plan_time(text)
    return result


@router.post("/api/reminders/ack")
async def ack_reminders(data: dict = Body(default=None)):
    from ..schedule_reminder import ack_reminders as _ack
    ids = (data or {}).get("schedule_ids", [])
    if not isinstance(ids, list):
        raise HTTPException(400, "schedule_ids 必须为数组")
    return {"success": True, "acked": _ack(ids)}


