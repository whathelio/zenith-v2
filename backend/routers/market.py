"""Market API — 黄金市场分析（2026-09-12 由 app.py 迁入，B-20 第二刀）

## 本域的状态：**已主动封存**，但保留可读入口

- `/cftc`、`/cftc/gold` → **HTTP 410 Gone**（CFTC 服务已归档）
- `/run-analysis`、`/refresh-data`、`/predictions/verify` → `{"success": false, "error": "市场分析模块已封存"}`
- `/predictions`、`/predictions/hit-rate` → 空数组 / 全零
- `/status`、`/reports*` → **仍读历史数据**（`market_reports` 表停在 2026-07-17）

**为什么保留而不是删掉**（backlog §10.3 裁定）：
这些 410 端点返回的是 **410 + 中文说明**，**本身就在解释「为什么没了」** ——
删掉它们只会变成 404，**信息量反而变少**；而 `/reports*` 是历史报告的**只读入口**，仍有价值。

**为什么从 app.py 迁出来**：本域 11 条与 mt5 的 6 条长期混在 `app.py` 里（该文件曾达 1511 行）。
迁出后路径 / 方法 / 参数 / 返回体**逐字不变**，注册时机由「装饰器」变为「include_router 时」——
本域无重复注册（`tools/audit/duplicate_routes.py` 实测 0 组），故匹配结果不变。
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from .. import database as db

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/status")
async def market_status():
    """当前市场状态（黄金价格+宏观指标+最新报告）"""
    indicators = db.macro_indicator_list_latest(limit=15)
    latest_report = db.market_report_get_latest()
    return {
        "indicators": indicators,
        "latest_report": {
            "id": latest_report["id"] if latest_report else None,
            "report_date": latest_report["report_date"] if latest_report else None,
            "gold_price": latest_report.get("gold_price", "") if latest_report else "",
            "daily_advice": latest_report.get("daily_advice", "") if latest_report else "",
        } if latest_report else None,
    }


@router.get("/cftc")
async def market_cftc():
    """CFTC 持仓数据 — 模块已封存"""
    return JSONResponse(status_code=410, content={"error": "市场分析模块已封存", "detail": "CFTC 服务已归档"})


@router.get("/cftc/gold")
async def market_cftc_gold():
    """CFTC 黄金专项分析 — 模块已封存"""
    return JSONResponse(status_code=410, content={"error": "市场分析模块已封存", "detail": "CFTC 黄金分析已归档"})


@router.get("/reports")
async def market_reports(limit: int = 30):
    """分析报告列表"""
    return db.market_report_list(limit=limit)


@router.get("/reports/latest")
async def market_reports_latest():
    """最新分析报告"""
    report = db.market_report_get_latest()
    if not report:
        return JSONResponse(content={"id": None, "report_date": None, "gold_price": "", "daily_advice": "", "weekly_advice": "", "analysis_text": ""}, status_code=200)
    return report


@router.get("/reports/{report_id}")
async def market_report_detail(report_id: int):
    """单份报告详情"""
    report = db.market_report_get(report_id)
    if not report:
        raise HTTPException(404, "报告不存在")
    return report


@router.post("/run-analysis")
async def run_market_analysis():
    return {"success": False, "error": "市场分析模块已封存"}


@router.get("/refresh-data")
async def refresh_market_data():
    return {"success": False, "error": "市场分析模块已封存"}


@router.get("/predictions")
async def market_predictions(date: str = "", verified: str = ""):
    """预测列表 — 模块已封存"""
    return []


@router.get("/predictions/hit-rate")
async def market_predictions_hit_rate(days: int = 30):
    return {"hit_rate": 0, "total": 0}


@router.post("/predictions/verify")
async def verify_predictions():
    return {"success": False, "error": "市场分析模块已封存"}
