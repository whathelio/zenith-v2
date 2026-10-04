"""Distill API — 蒸馏全量路由（2026-09-12 由 app.py 迁入，B-20 拆分第一刀）

## 为什么会有这个文件（两次修正的合并结果）

**第一次（2026-09-11，B-7）**：本文件原先重复注册了 6 条与 app.py 相同的路径
（`/schedules` · `/memories` · `/all` · `/daily/{date}` · `/weekly/{start}` · `/files`）。
`distill.router` 在 `app.py` 中 `include_router` 的时机**晚于** app.py 里那批 `@app.post(...)`
装饰器，而 FastAPI/Starlette 按**注册顺序**匹配、先注册者胜 → 本文件那 6 条**永不执行**，
改这里的代码不生效。当时只保留了 app.py 未注册的 1 条 `/conversation`。

**第二次（2026-09-12，B-20）**：但那样换来的是**同一个域分裂在两处** ——
app.py 持 8 条、本文件持 1 条。现**全部收进本 router**，
`app.py` 只留 `include_router(distill.router)`，本域自此**只有一个家**。

迁入的 8 条（`/api/distill` 前缀由 `APIRouter(prefix=...)` 提供）：

    POST /conversation/{conv_id}   POST /schedules   POST /memories   POST /all
    POST /daily/{date}             POST /weekly/{week_start}
    GET  /files                    GET  /file/{filename}

**行为不变**：路径、方法、参数、返回体与 app.py 版本逐字一致。
注册时机由「app.py 装饰器」变为「include_router 时」，但该域已无重复注册
（`tools/audit/duplicate_routes.py` 实测 0 组），故路由匹配结果不变。

**注意**：本文件保留两条 conversation 路由，路径不同、**互不遮蔽** ——
`POST /conversation`（query 参数版，供脚本用）与 `POST /conversation/{conv_id}`（path 参数版，前端用）。
"""
import os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..unified_distill import (
    _OUTPUT_DIR,
    distill_all,
    distill_conversation,
    distill_daily,
    distill_memories,
    distill_schedules,
    distill_weekly,
)

router = APIRouter(prefix="/api/distill", tags=["distill"])


@router.post("/conversation")
async def api_distill_conversation(conv_id: str = "", save_txt: bool = True):
    """按会话蒸馏（`conv_id` 走 **query 参数**）。

    与下方 `POST /conversation/{conv_id}`（**path 参数**版）路径不同、互不遮蔽。
    前端实际调用的是 path 参数版（`api.ts` 的 `/conversation/${id}`）；本端点供 query 形式调用方使用。
    """
    if not conv_id:
        raise HTTPException(400, "conv_id is required")
    return await distill_conversation(conv_id, save_txt=save_txt)


@router.post("/conversation/{conv_id}")
async def api_distill_conv(conv_id: str, save_txt: bool = True):
    """对话蒸馏 — 总结 + 知识提取 + 记忆存储 + txt 输出"""
    result = await distill_conversation(conv_id, save_txt=save_txt)
    if not result.get("success", True) and "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.post("/schedules")
async def api_distill_schedules(
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    save_txt: bool = True,
):
    """日程蒸馏 — 规律/遗漏/优化 + txt 输出"""
    return await distill_schedules(status=status, date_from=date_from, date_to=date_to, save_txt=save_txt)


@router.post("/memories")
async def api_distill_memories(
    type_: str = "",
    search: str = "",
    save_txt: bool = True,
):
    """记忆蒸馏 — 精华/合并/过时 + txt 输出"""
    return await distill_memories(type_=type_, search=search, save_txt=save_txt)


@router.post("/all")
async def api_distill_all(
    conv_id: str = "",
    schedule_status: str = "confirmed",
    memory_type: str = "",
    save_txt: bool = True,
):
    """全维度综合蒸馏 — 交叉关联对话/日程/记忆 + txt 输出"""
    return await distill_all(
        conv_id=conv_id,
        schedule_status=schedule_status,
        memory_type=memory_type,
        save_txt=save_txt,
    )


@router.post("/daily/{date}")
async def api_distill_daily(date: str, save_txt: bool = True, save_md: bool = True):
    """每日蒸馏 — 聚合指定日期的对话/日程/笔记/记忆 → 生成每日总结"""
    result = await distill_daily(date=date, save_txt=save_txt, save_md=save_md)
    if not result.get("success", True) and "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.post("/weekly/{week_start}")
async def api_distill_weekly(week_start: str, save_txt: bool = True):
    """每周蒸馏 — 聚合指定周（从周一开始）的对话/日程/笔记/记忆 → 生成周总结"""
    result = await distill_weekly(week_start=week_start, save_txt=save_txt)
    if not result.get("success", True) and "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.get("/files")
async def api_distill_list_files():
    """列出已保存的蒸馏 txt 文件"""
    if not os.path.exists(_OUTPUT_DIR):
        return {"files": []}
    files = []
    for f in sorted(os.listdir(_OUTPUT_DIR)):
        if f.endswith(".txt"):
            filepath = os.path.join(_OUTPUT_DIR, f)
            stat = os.stat(filepath)
            files.append({
                "name": f,
                "path": filepath,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
    return {"files": files, "count": len(files)}


@router.get("/file/{filename}")
async def api_distill_get_file(filename: str):
    """下载指定蒸馏 txt 文件（仅允许 `_OUTPUT_DIR` 内的 .txt，防路径穿越）"""
    base = Path(_OUTPUT_DIR).resolve()
    filepath = (base / filename).resolve()
    if not str(filepath).startswith(str(base)):
        raise HTTPException(400, "非法文件路径")
    if filepath.suffix.lower() != ".txt":
        raise HTTPException(400, "仅支持 .txt 文件")
    if not filepath.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(filepath, media_type="text/plain", filename=filepath.name)
