"""Split app.py into routers/ — batch create routers + clean app.py"""
from pathlib import Path

BASE = Path(__file__).parent.parent

# ── Router 4: Schedules + Calendar ──
schedules_code = '''"""Schedules API — 日程 CRUD + 日历 + 提醒"""
from fastapi import APIRouter, HTTPException, Body, Request
from .. import database as db
from ..schedule_reminder import get_due_reminders
from ..recurrence import expand_recurrence

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
                target = float(g.get("target_value", 1))
                daily = float(g.get("daily_target", 5))
                if strategy == "linear":
                    db.goal_update(goal_id, {"current_value": current + daily})
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


@router.get("/api/reminders")
async def get_reminders():
    from ..schedule_reminder import get_due_reminders
    text = get_due_reminders()
    return {"text": text, "items": []}


@router.get("/api/reminders/presets")
async def get_reminder_presets():
    from ..schedule_reminder import REMINDER_PRESETS
    return REMINDER_PRESETS


@router.get("/api/calendar/templates")
async def get_calendar_templates():
    return [
        {"id": "nonfarm", "name": "非农就业", "category": "economic", "importance": 5, "country": "US", "duration": 30, "remind_before": 1440},
        {"id": "cpi", "name": "CPI", "category": "economic", "importance": 5, "country": "US", "duration": 30, "remind_before": 1440},
        {"id": "ppi", "name": "PPI", "category": "economic", "importance": 4, "country": "US", "duration": 30, "remind_before": 1440},
        {"id": "fomc", "name": "FOMC 决议", "category": "economic", "importance": 5, "country": "US", "duration": 60, "remind_before": 2880},
        {"id": "gdp", "name": "GDP", "category": "economic", "importance": 4, "country": "US", "duration": 30, "remind_before": 1440},
        {"id": "retail_sales", "name": "零售销售", "category": "economic", "importance": 3, "country": "US", "duration": 30, "remind_before": 1440},
        {"id": "jobless_claims", "name": "初请失业", "category": "economic", "importance": 3, "country": "US", "duration": 30, "remind_before": 1440},
        {"id": "pmi", "name": "PMI", "category": "economic", "importance": 4, "country": "CN", "duration": 30, "remind_before": 1440},
    ]


@router.get("/api/calendar/week")
async def get_calendar_week(date: str = ""):
    from datetime import datetime, timedelta
    if date:
        base = datetime.strptime(date, "%Y-%m-%d")
    else:
        base = datetime.now()
    monday = base - timedelta(days=base.weekday())
    days = [(monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    result = {}
    for d in days:
        result[d] = db.sch_list(date_from=d, date_to=d)
    return result


@router.get("/api/calendar/month")
async def get_calendar_month(date: str = ""):
    from datetime import datetime, timedelta
    if date:
        base = datetime.strptime(date, "%Y-%m-%d")
    else:
        base = datetime.now()
    start = base.replace(day=1)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1, day=1)
    else:
        end = start.replace(month=start.month + 1, day=1)
    result = {}
    d = start
    while d < end:
        ds = d.strftime("%Y-%m-%d")
        result[ds] = db.sch_list(date_from=ds, date_to=ds)
        d += timedelta(days=1)
    return result


@router.get("/api/calendar")
async def get_calendar(date: str = "", month: str = ""):
    from datetime import datetime, timedelta
    days = {}
    if month:
        y, m = map(int, month.split("-"))
        start = datetime(y, m, 1)
        if m == 12:
            end = datetime(y + 1, 1, 1)
        else:
            end = datetime(y, m + 1, 1)
        d = start
        while d < end:
            days[d.strftime("%Y-%m-%d")] = []
            d += timedelta(days=1)
    else:
        if date:
            d = datetime.strptime(date, "%Y-%m-%d")
        else:
            d = datetime.now()
        days = {d.strftime("%Y-%m-%d"): []}
    items = db.sch_list()
    for item in items:
        st = item.get("start_time", "")[:10]
        if st in days:
            days[st].append(dict(item))
    return {
        "date": date or datetime.now().strftime("%Y-%m-%d"),
        "days": days,
        "all_schedules": [dict(r) for r in items],
    }
'''

# ── Router 5: Distill ──
distill_code = '''"""Distill API — 蒸馏 + 文件管理"""
from fastapi import APIRouter, HTTPException, Body
from pathlib import Path
from ..unified_distill import (
    distill_conversation, distill_schedules, distill_memories,
    distill_all, distill_daily, distill_weekly, DISTILL_OUTPUT
)

router = APIRouter(prefix="/api/distill", tags=["distill"])


@router.post("/conversation")
async def api_distill_conversation(conv_id: str = "", save_txt: bool = True):
    if not conv_id:
        raise HTTPException(400, "conv_id is required")
    return await distill_conversation(conv_id, save_txt=save_txt)


@router.post("/schedules")
async def api_distill_schedules(status: str = "confirmed", save_txt: bool = True):
    return await distill_schedules(status=status, save_txt=save_txt)


@router.post("/memories")
async def api_distill_memories(type_: str = "", search: str = "", save_txt: bool = True):
    return await distill_memories(type_=type_, search=search, save_txt=save_txt)


@router.post("/all")
async def api_distill_all(conv_id: str = "", schedule_status: str = "confirmed", memory_type: str = "", save_txt: bool = True):
    return await distill_all(conv_id=conv_id, schedule_status=schedule_status, memory_type=memory_type, save_txt=save_txt)


@router.post("/daily/{date}")
async def api_distill_daily(date: str, save_txt: bool = True):
    return await distill_daily(date=date, save_txt=save_txt)


@router.post("/weekly/{start}")
async def api_distill_weekly(start: str, save_txt: bool = True):
    return await distill_weekly(start=start, save_txt=save_txt)


@router.get("/files")
async def api_distill_files():
    if not DISTILL_OUTPUT.exists():
        return {"files": [], "path": str(DISTILL_OUTPUT)}
    files = []
    for p in sorted(DISTILL_OUTPUT.rglob("*.txt"), reverse=True):
        files.append({"name": p.relative_to(DISTILL_OUTPUT).as_posix(), "size": p.stat().st_size, "modified": p.stat().st_mtime})
    return {"files": files[:100], "path": str(DISTILL_OUTPUT)}
'''

# ── Router 6: Knowledge ──
knowledge_code = '''"""Knowledge API — RAG 网关代理"""
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
'''

# Write all routers
(BASE / "routers" / "schedules.py").write_text(schedules_code, encoding="utf-8")
(BASE / "routers" / "distill.py").write_text(distill_code, encoding="utf-8")
(BASE / "routers" / "knowledge.py").write_text(knowledge_code, encoding="utf-8")
print("Routers 4-6 created.")
