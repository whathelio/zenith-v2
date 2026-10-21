"""黄金分析通路修补的回归守卫（2026-10-21）。

对应 `知识库-v1.0/日志审查库/zenith/2026-10/Zenith_黄金日内分析对话诊断与修复方案_20261016.md`
的 P0 三项（A/B/C）。这三项的共同性质是「数据在、通路断」，所以**单测要盯的是通路，
不是数据** —— 换句话说：不是测「MT5 能取到 K 线吗」，而是测「取到的 K 线能不能
原封不动走到模型面前」。

覆盖：
  A. `list_calendar_events` —— 财经事件从 `schedule_events` 表到 LLM 的第一个读工具
  B. `mt5_rates(tail_n=)`   —— 逐根 OHLCV 必须落在 `result` 字符串里（不是只进 dict）
  C. `mt5_vwap`             —— 服务端算 VWAP，数值必须与手算**完全相等**

⚠️ 三条纪律：
  1. 替换模块级函数一律走 `monkeypatch`，不手工赋值（手工赋值不还原会污染后续用例）。
  2. **不依赖 MT5 终端**：全部 mock `get_rates`，否则测试会因「终端没开」而红，
     那是环境问题不是代码问题，会训练人忽略红灯。
  3. A 项必须断言边界字符串的**分隔符**。`event_time` 存的是
     `'2026-10-21T17:00:00+08:00'`，边界若用空格拼会静默漏掉全部未来事件 ——
     这个 bug 不会报错，只会让模型永远说「今天没有数据」。
"""
import asyncio



# ---------- 夹具：构造一段可手算的 K 线 ----------

def _bars():
    """3 根 K 线，TP 分别为 95 / 105 / 115，量 10 / 20 / 30。

    手算基准：
        VWAP = (95*10 + 105*20 + 115*30) / 60 = 6500/60 = 108.3333…
        σ    = sqrt((10*13.333² + 20*3.333² + 30*6.667²)/60) ≈ 7.4536
    """
    return [
        {"time": "2026-10-21T07:00:00", "open": 95, "high": 100, "low": 90, "close": 95, "volume": 10},
        {"time": "2026-10-21T07:15:00", "open": 95, "high": 110, "low": 100, "close": 105, "volume": 20},
        {"time": "2026-10-21T07:30:00", "open": 105, "high": 120, "low": 110, "close": 115, "volume": 30},
    ]


def _fake_rates(symbol="XAUUSD", timeframe="M15", count=300):
    bars = _bars()
    return {
        "success": True,
        "symbol": symbol,
        "timeframe": timeframe,
        "count": len(bars),
        "rates": bars,
        "stats": {
            "open": bars[0]["open"], "high": 120, "low": 90, "close": 115,
            "change": 20, "change_pct": 21.05, "avg_volume": 20,
        },
    }


# ---------- 前置自检：确保拿到的是真实现 ----------

def test_前置自检_工具已注册():
    from backend import tools

    names = {t["function"]["name"] for t in tools.TOOLS_SCHEMA}
    assert "list_calendar_events" in names, "schema 未注册"
    assert "list_calendar_events" in tools._TOOL_HANDLERS, "handler 未注册"
    # C 项刻意**不**做成独立工具：VWAP 并入 mt5_volume_profile，
    # 避免为每轮常驻的 tools schema 多付一份描述预算（见 test_tools_schema 的总预算）。
    assert "mt5_vwap" not in names, "VWAP 应并入 mt5_volume_profile，不要独立开工具体"
    assert "mt5_volume_profile" in tools._TOOL_HANDLERS


# ---------- A 项 ----------

def test_A_财经事件_边界必须用T分隔符(monkeypatch):
    """最危险的静默 bug：边界用空格拼会让字符串比较整体错位。"""
    captured = {}

    def fake_event_list(**kw):
        captured.update(kw)
        return []

    monkeypatch.setattr("backend.database.event_list", fake_event_list)
    from backend.tools import _handle_list_calendar_events

    asyncio.run(_handle_list_calendar_events({"days": 3, "min_star": 2}))

    assert "T" in captured["date_from"], f"date_from 用了空格分隔: {captured['date_from']!r}"
    assert " " not in captured["date_from"], f"date_from 含空格: {captured['date_from']!r}"
    assert "T" in captured["date_to"], f"date_to 用了空格分隔: {captured['date_to']!r}"
    assert captured["min_star"] == 2
    assert captured["limit"] >= 100


def test_A_财经事件_渲染含状态星级与前值预期(monkeypatch):
    rows = [
        {
            "event_time": "2026-10-21T17:00:00+08:00", "star": 4, "name": "欧元区9月CPI年率终值",
            "previous": "2.1", "consensus": "2.2", "actual": "2.3",
        },
        {
            "event_time": "2026-10-21T20:30:00+08:00", "star": 3, "name": "美国9月进口物价指数月率",
            "previous": "0.3", "consensus": "0.2", "actual": "",
        },
    ]
    monkeypatch.setattr("backend.database.event_list", lambda **kw: rows)
    from backend.tools import _handle_list_calendar_events

    r = asyncio.run(_handle_list_calendar_events({"days": 1, "min_star": 2}))

    assert r["success"] is True
    assert r["count"] == 2
    txt = r["result"]
    assert "欧元区9月CPI年率终值" in txt
    assert "★★★★" in txt                      # 4 星必须完整渲染（库里确有 star=4）
    assert "[已公布]" in txt and "实际2.3" in txt
    assert "[待公布]" in txt and "预期0.2" in txt
    assert "前值0.3" in txt


def test_A_财经事件_空缓存给出可执行提示(monkeypatch):
    monkeypatch.setattr("backend.database.event_list", lambda **kw: [])
    from backend.tools import _handle_list_calendar_events

    r = asyncio.run(_handle_list_calendar_events({"days": 3}))
    assert r["success"] is True and r["count"] == 0
    assert "sync_calendar" in r["result"], "空结果必须指向同步动作，否则模型会干等"


# ---------- B 项 ----------

def test_B_不传tail_n时输出不含逐根段(monkeypatch):
    """默认行为必须与改动前一致 —— 这是回滚安全的依据。"""
    monkeypatch.setattr("backend.mt5_service.get_rates", _fake_rates)
    from backend.tools import _handle_mt5_rates

    r = _handle_mt5_rates({"symbol": "XAUUSD", "timeframe": "M15", "count": 3})
    assert r["success"] is True
    assert "最近" not in r["result"]
    assert "07:15" not in r["result"]


def test_B_tail_n返回N根且落在result字符串里(monkeypatch):
    """核心断言：逐根数据必须在 `result` 里 —— chat.py 只把这一项送进模型。"""
    monkeypatch.setattr("backend.mt5_service.get_rates", _fake_rates)
    from backend.tools import _handle_mt5_rates

    r = _handle_mt5_rates({"symbol": "XAUUSD", "timeframe": "M15", "count": 3, "tail_n": 2})
    txt = r["result"]
    assert "最近 2 根" in txt
    assert "10-21 07:15" in txt          # 时间已裁成 MM-DD HH:MM
    assert "10-21 07:30" in txt
    assert "10-21 07:00" not in txt      # 只留最后 2 根
    assert "95 110 100 105 20" in txt   # OHLCV 顺序与量（bar2: o95 h110 l100 c105 v20）

    # 反向对照：dict 里本来就有 rates —— 所以"有 rates"不能证明通路通了
    assert len(r["rates"]) == 3


def test_B_tail_n超上限被截并明示(monkeypatch):
    bars = [
        {"time": f"2026-10-21T{h:02d}:00:00", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 1}
        for h in range(24)
    ]
    fake = _fake_rates()
    fake["rates"] = bars
    fake["count"] = 24
    monkeypatch.setattr("backend.mt5_service.get_rates", lambda *a, **k: fake)
    from backend.tools import _handle_mt5_rates

    r = _handle_mt5_rates({"count": 24, "tail_n": 999})
    assert "已截到 24 根" in r["result"], "截断必须明示，否则模型以为拿到了全部"
    assert "上限 120" in r["result"]


# ---------- C 项 ----------

def test_C_vwap数值与手算完全相等(monkeypatch):
    monkeypatch.setattr("backend.mt5_service.get_rates", _fake_rates)
    from backend.mt5_service import get_vwap

    r = get_vwap("XAUUSD", "M15", 3)
    assert r["success"] is True
    assert r["vwap"] == 108.33, r["vwap"]          # 6500/60 = 108.3333…
    assert r["close"] == 115.0
    assert r["deviation"] == 6.67
    assert r["stdev"] == 7.45
    assert "上方" in r["position"]

    # 标准差带必须围绕 VWAP 对称
    b = r["bands"]
    assert abs((b["上轨 +1σ"] - r["vwap"]) - (r["vwap"] - b["下轨 -1σ"])) < 0.02
    assert r["bands"]["上轨 +1σ"] > r["bands"]["下轨 -1σ"]
    assert r["volume_basis"] == "tick_volume"


def test_C_vwap零成交量退化并标注(monkeypatch):
    """全零量时不能假装是 VWAP —— 必须退化 + 明说。"""
    bars = [dict(b, volume=0) for b in _bars()]
    fake = _fake_rates()
    fake["rates"] = bars
    monkeypatch.setattr("backend.mt5_service.get_rates", lambda *a, **k: fake)
    from backend.mt5_service import get_vwap

    r = get_vwap("XAUUSD", "M15", 3)
    assert r["success"] is True
    assert r["vwap"] == 105.0, "应退化为算术均价 (95+105+115)/3"
    assert "none" in r["volume_basis"]
    assert "bands" not in r, "无标准差时不应返回空带"


def test_C_volume_profile内含VWAP段(monkeypatch):
    """C 项的最终消费路径：模型调 mt5_volume_profile 就能拿到 VWAP。"""
    monkeypatch.setattr("backend.mt5_service.get_rates", _fake_rates)
    from backend.tools import _handle_mt5_volume_profile

    r = _handle_mt5_volume_profile({"symbol": "XAUUSD", "timeframe": "M15", "count": 3})
    assert r["success"] is True
    txt = r["result"]
    assert "VWAP" in txt
    assert "108.33" in txt                       # 与手算一致，不是模型自己算的
    assert "tick_volume" in txt                  # 口径警告必须随数据走


def test_C_vwap窗口起点终点可追溯(monkeypatch):
    monkeypatch.setattr("backend.mt5_service.get_rates", _fake_rates)
    from backend.mt5_service import get_vwap

    r = get_vwap("XAUUSD", "M15", 3)
    assert r["from_time"].startswith("2026-10-21T07:00")
    assert r["to_time"].startswith("2026-10-21T07:30")
    assert r["count"] == 3
