"""MT5 API — MetaTrader 5 桥接（2026-09-12 由 app.py 迁入，B-20 第二刀）

## 本域的状态：🟢 **可用**（2026-09-22 实测复核，推翻旧结论）

6 条路由全部转发到 `mt5_service`，该服务经 `MetaTrader5` 包**直连本机 MT5 终端的 IPC**。

⚠️ **此处原先写着「当前环境不可用」，该结论已于 2026-09-22 实测推翻**：
当时把 **MCP 旁路**（`http://127.0.0.1:22346/mcp` 返回 401）误当成了本域失败的原因，
但**本域链路根本不经过 MCP**，两者是**相互独立的两条通路**。

2026-09-22 实测（终端 PID 14840 / build 6182 / Exness-MT5Real5）：
- `/api/mt5/status` → `connected:true`；`/tick`、`/rates`、`/positions` 全部 200
- 模型侧 4 个 mt5 工具**已从 `_DISABLED_TOOLS` 移除**（见 `tools.py`），恢复可调用

**仍存在的问题（另一条通路，与本域无关）**：MCP `mt5-terminal` 401 ——
该端口由 `terminal64.exe` 自身监听，其要求的 token 与 `mcp.json` 里配的对不上。
**注意：只迁了 HTTP 路由，`mt5_service.py` 与 4 个 LLM 工具都原样保留。**
本次迁出 HTTP 路由**不改变任何行为**：路径 / 方法 / 参数 / 返回体逐字不变，
`mt5_service` 的导入仍是**函数内惰性导入**（保持原样，避免未装依赖时导入即失败）。
"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/mt5", tags=["mt5"])


# ⚠️⚠️ 这 6 条路由**必须是同步 `def`，绝不能写成 `async def`**（2026-09-22 实测踩坑）
#
# `mt5_service` 的所有函数都是**同步阻塞**的，内部直接调 `MetaTrader5` 的 IPC。
# 当 MT5 终端**未运行**时，`mt5.initialize()` 会阻塞 **60 秒**才超时（`-10003`），
# 且 `_ensure_mt5()` 失败后会再重试一次自动探测 → **累计约 160 秒**。
#
# 若路由写 `async def`，这个阻塞调用会**直接卡死整个事件循环**：不只是本接口报错，
# 而是**全站所有 API（含 `/api/health`）一起无响应**。
# 实测：2026-09-22 07:42:57–07:45:39 服务完全假死约 160 秒，curl 返回 `http_code=000`。
#
# ⚠️ **但只把路由改成同步 `def` 并不足够**（2026-09-22 后续实测推翻，勿信「改了 def 就好了」）：
# `MetaTrader5` 是 C 扩展，等待管道期间**不释放 GIL** → **整个进程**被冻结，
# 被丢进线程池的 `def` 同样拿不到 GIL。
# 实测：改成 `def` 之后，在 mt5 请求阻塞期间并发打 `/api/health` 仍返回
# `http_code=000`（5s 超时）；另起一个每秒 print 的 heartbeat 线程也**一次都没打印**。
#
# **真正的止血在 `mt5_service._terminal_running()`** —— 终端不在跑就绝不调 `initialize()`。
# 本文件保持同步 `def` 属于「正确但不充分」：它仍是 FastAPI 对阻塞函数的推荐写法，
# 且能在「终端在跑、仅 IPC 慢」等**非 GIL** 场景下保护事件循环。


@router.get("/status")
def mt5_status():
    """MT5 连接状态"""
    from ..mt5_service import get_connection_status
    return get_connection_status()


@router.get("/tick")
def mt5_tick(symbol: str = "XAUUSD"):
    """获取最新 Tick 报价"""
    from ..mt5_service import get_tick
    return get_tick(symbol)


@router.get("/rates")
def mt5_rates(symbol: str = "XAUUSD", timeframe: str = "M5", count: int = 100):
    """获取历史 K 线数据"""
    from ..mt5_service import get_rates
    count = min(max(count, 1), 1000)  # 限制 1-1000
    return get_rates(symbol, timeframe, count)


@router.get("/volume-profile")
def mt5_volume_profile(symbol: str = "XAUUSD", timeframe: str = "M5", count: int = 200):
    """获取成交量分布 (Volume Profile)"""
    from ..mt5_service import get_volume_profile
    count = min(max(count, 10), 500)
    return get_volume_profile(symbol, timeframe, count)


@router.get("/positions")
def mt5_positions():
    """获取当前持仓"""
    from ..mt5_service import get_positions
    return get_positions()


@router.get("/tick-stats")
def mt5_tick_stats(symbol: str = "XAUUSD", seconds: int = 60):
    """获取 Tick 成交统计"""
    from ..mt5_service import get_tick_stats
    seconds = min(max(seconds, 1), 3600)
    return get_tick_stats(symbol, seconds)


# ── 账户详情 / 交易记录（2026-09-22 新增）─────────────────────────────────────
# 参数范围校验统一放在 mt5_service 内部（days clamp 到 1–365），此处保持薄转发。

@router.get("/account")
def mt5_account():
    """账户完整详情（杠杆 / 保证金 / 强平线 / 公司 等 24 字段）"""
    from ..mt5_service import get_account_info
    return get_account_info()


@router.get("/history/deals")
def mt5_history_deals(days: int = 7, limit: int = 200):
    """历史成交记录（deals — 实际成交，含盈亏 / 手续费 / 库存费）"""
    from ..mt5_service import get_trade_history
    return get_trade_history(days, limit)


@router.get("/history/orders")
def mt5_history_orders(days: int = 7, limit: int = 200):
    """历史订单（orders — 委托指令，区别于 deals 成交）"""
    from ..mt5_service import get_order_history
    return get_order_history(days, limit)


@router.get("/pending-orders")
def mt5_pending_orders():
    """当前挂单（尚未成交的委托）"""
    from ..mt5_service import get_pending_orders
    return get_pending_orders()
