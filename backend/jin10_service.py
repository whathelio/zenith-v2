"""Zenith v2 — 金十数据 MCP 客户端服务

⚠️ **本模块是活代码，不是归档模块**（2026-09-28 更正）。

此前首行写的是「已封存 (SEALED) / 随 market_analyzer.py 一起封存」—— **与事实不符**。
实测它有三条活调用路径，且每日定时运行：
  - `calendar_sync.py:14` → `get_jin10_service()`，`sync_calendar_events()` 是**每日定时任务**
    （`scheduler._calendar_sync_loop` + 启动即同步）
  - `routers/news.py:60` → `/api/news/*` 端点
  - `tools.py` 侧的 LLM 工具入口

**行情/分析类方法**（`fetch_quote_indicators` / `get_kline` / `get_quote` / `search_news` /
`list_news` / `get_news` / `QUOTE_CODE_MAP` 等）**确为幽灵**（零引用或仅被
`_archived/macro_data.py` 引用），但**方法存在 ≠ 模块封存** —— 请以调用方为准。
幽灵清单登记见 `知识库-v1.0/日志审查库/zenith/2026-09/Zenith-D8立项审计-金十链路-20260928.md` §4。
所有工具方法保留，供未来恢复市场分析时直接复用。
"""
from __future__ import annotations

import asyncio
import json
import logging
import yaml
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger("zenith.jin10")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PROJECT_DIR = Path(__file__).parent.parent
CONFIG_YAML = PROJECT_DIR / "config" / "config.yaml"

DEFAULT_JIN10_URL = "https://mcp.jin10.com/mcp"
DEFAULT_JIN10_TOKEN = ""
MCP_PROTOCOL_VERSION = "2025-11-25"


def _load_jin10_config() -> dict:
    """从 config.yaml 读取 jin10 配置段，api_token 优先从环境变量读取"""
    import os
    jin10_cfg = {}
    try:
        with open(CONFIG_YAML, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        jin10_cfg = cfg.get("jin10", {})
    except Exception:
        pass
    # .env 优先级高于 config.yaml
    env_token = os.environ.get("ZENITH_JIN10_API_TOKEN", "").strip()
    if env_token:
        jin10_cfg["api_token"] = env_token
    return jin10_cfg


# ---------------------------------------------------------------------------
# Jin10Service — MCP JSON-RPC Client
# ---------------------------------------------------------------------------

# Jin10 品种代码 → Zenith 内部指标名映射
QUOTE_CODE_MAP = {
    "XAUUSD": "gold",       # 现货黄金
    "XAGUSD": "silver",     # 现货白银
    "USOIL":  "wti",        # WTI 原油
    "UKOIL":  "brent",      # 布伦特原油
    "COPPER": "copper",     # 现货铜
    "USDCNH": "usd_cny",    # 美元/人民币
}


class Jin10Service:
    """金十数据 MCP 客户端 — httpx AsyncClient 实现"""

    def __init__(self):
        cfg = _load_jin10_config()
        self._url = cfg.get("mcp_url", DEFAULT_JIN10_URL)
        self._token = cfg.get("api_token", DEFAULT_JIN10_TOKEN)
        self._client: Optional[httpx.AsyncClient] = None
        self._initialized = False
        self._session_id: Optional[str] = None
        self._req_id = 0
        # 2026-09-28 D8-b：最近一次失败的**可读原因**。
        # 此前 `_mcp_post` 把 5 类失败全部折叠成 `{}`（token 未配置甚至只记 debug），
        # 调用方只能拿到 None，无法区分 401 / 超时 / 协议错 ——
        # 前序会话因此被迫另写一次性探针（见 backend/_archived/probe_jin10.py:4-6 自述）。
        self._last_error: str = ""

    def last_error(self) -> str:
        """返回最近一次失败的原因（空串 = 上一次调用未失败）。

        与 `_mcp_post` 同一生命周期：每次调用**入口先清空**，失败时写入。
        设计对齐 `mcp_client.MCPClient.last_error()`（Z2，2026-09-28）。
        """
        return self._last_error

    # -- Session Management --------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 httpx 客户端"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30)
            self._initialized = False  # 新客户端需要重新初始化
        return self._client

    async def _mcp_post(self, payload: dict, is_notification: bool = False) -> dict:
        """发送 MCP JSON-RPC POST 请求，支持 SSE 响应格式。

        **返回契约（2026-09-28 D8-b）**：失败一律返回 `{}`，但**必定**在
        `self._last_error` 留下可读原因。调用方判断失败后应先读 `last_error()`
        再决定如何上报 —— 不要只报「返回空」。
        """
        self._last_error = ""          # 入口清空：与 last_error() 共享同一生命周期

        if not self._token:
            self._last_error = ("金十 API Token 未配置：文件 .env 的 ZENITH_JIN10_API_TOKEN "
                                "与 config.yaml 的 jin10.api_token 均为空")
            # 原为 logger.debug —— 默认日志级别下完全静默，故提升到 warning（D8-b N1）
            logger.warning("金十 API Token 未配置，跳过 MCP 请求")
            return {}

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._token}",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        client = await self._get_client()
        try:
            resp = await client.post(self._url, json=payload, headers=headers)

            # 捕获 session ID
            new_sid = resp.headers.get("mcp-session-id")
            if new_sid:
                self._session_id = new_sid

            if is_notification:
                # 通知不期望响应（SSE 可能返回空流或直接关闭；实测服务端返回 HTTP 202 空体）
                return {}

            if resp.status_code != 200:
                self._last_error = (f"金十 MCP HTTP {resp.status_code} "
                                    f"(url={self._url}, body[:200]={(resp.text or '')[:200]!r})")
                logger.warning("金十 MCP HTTP %s", resp.status_code)
                self._initialized = False
                return {}

            # 解析响应 — SSE 格式或普通 JSON
            content_type = resp.headers.get("content-type", "")
            body_text = resp.text

            logger.debug(f"金十 MCP 响应: ct={content_type}, len={len(body_text)}, body[:200]={body_text[:200]}")

            if "text/event-stream" in content_type:
                # SSE 格式: event: message\ndata: {json}\n\n
                # 2026-09-28 D8-c：把本次请求的 id 传进去，让选帧按 id 匹配而非盲取末帧。
                json_body = self._parse_sse_body(body_text, want_id=payload.get("id"))
            else:
                # 普通 JSON
                try:
                    json_body = json.loads(body_text)
                except Exception as e:
                    json_body = {}
                    self._last_error = (f"金十 MCP 非 SSE 响应 JSON 解析失败: {type(e).__name__}: {e} "
                                        f"(ct={content_type}, body[:300]={body_text[:300]!r})")

            if not json_body:
                # 走到这里 = 帧存在但一个都没解析成 / 解析成了空 —— 区分于上面的 JSON 解析失败
                if not self._last_error:
                    self._last_error = (f"金十 MCP 响应无可用 JSON-RPC 帧 "
                                        f"(ct={content_type}, body_len={len(body_text)}, "
                                        f"body[:300]={body_text[:300]!r})")
                    logger.warning("金十 MCP 响应解析失败, body_len=%s", len(body_text))
                return {}

            if "error" in json_body:
                self._last_error = f"金十 MCP JSON-RPC 错误: {str(json_body['error'])[:300]}"
                logger.warning(f"金十 MCP 错误: {json_body['error']}")
                return {}
            if "result" not in json_body:
                # 2026-09-28 D8-c：拿到帧却既无 result 也无 error —— JSON-RPC 2.0 要求
                # 两者必有其一，属协议违规。选帧规则已保证「只有通知帧」的情形会被这里逮到
                # （此前盲取末帧时，通知帧会被当成合法响应静默返回空）。
                # 判定与 `mcp_client.py:241` 逐字对齐（Z12 已修的那一份）。
                self._last_error = (f"金十 MCP JSON-RPC 响应既无 result 也无 error: "
                                    f"{str(json_body)[:300]}")
                return {}
            return json_body.get("result", {})

        except Exception as e:
            self._last_error = f"金十 MCP 请求异常: {type(e).__name__}: {e} (url={self._url})"
            logger.warning(f"金十 MCP 请求失败: {e}")
            self._initialized = False
            return {}

    @staticmethod
    def _parse_sse_body(body_text: str, want_id: Any = None) -> dict:
        """从 SSE 响应体里选出「最像本次响应」的那一帧。

        2026-09-28 D8-c：本方法此前与 `mcp_client._parse_sse` **逐行等价**
        （盲取 `data_lines[-1]` + `"".join` 兜底），而 Z12 只修了 `mcp_client` 那一份
        —— 这正是「同缺陷副本」的典型：修一处等于没修。现按 Z12 的选帧规则逐字对齐。

        选帧规则（按形状选，不盲取末帧）：
        ① 给出了 ``want_id`` 时，优先返回 id 匹配的帧（`str()` 比较，兼容字符串/数字 id）；
        ② 否则返回 cands 中最后一个含 ``result`` 或 ``error`` 键的帧 —— 通知帧
           这两个键都没有，因此**不可能**再被选中（堵死根因）；
        ③ 都没有则退回 cands 末帧，保持既有语义；cands 为空返回 ``{}``。

        职责边界：本方法只负责“选帧”，**不写 `_last_error`、不做失败判定**
        —— **调用方必须自行判定失败**（③ 可能返回不含 `result`/`error` 的帧，
        该判定在 `_mcp_post` 的 `if "result" not in json_body` 分支）。
        """
        data_lines = []
        for line in body_text.split("\n"):
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())  # 去掉 "data:" 前缀

        if not data_lines:
            return {}

        cands: list[dict] = []
        for raw in data_lines:
            try:
                frame = json.loads(raw)
            except Exception:
                continue
            if isinstance(frame, dict):
                cands.append(frame)
        # 整段兜底（保留既有行为）：单行都不是完整 JSON 时（帧被折行等）
        try:
            combined = json.loads("".join(data_lines))
        except Exception:
            combined = None
        if isinstance(combined, dict):
            cands.append(combined)

        if not cands:
            return {}
        if want_id is not None:
            for frame in cands:
                if str(frame.get("id")) == str(want_id):
                    return frame
        for frame in reversed(cands):
            if "result" in frame or "error" in frame:
                return frame
        return cands[-1]

    async def _initialize(self) -> bool:
        """初始化 MCP 连接 (initialize → notifications/initialized)"""
        if self._initialized:
            return True

        self._req_id += 1
        init_payload = {
            "jsonrpc": "2.0",
            "id": self._req_id,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "zenith-v2", "version": "1.0"},
            },
        }
        result = await self._mcp_post(init_payload)
        if not result:
            logger.warning("金十 MCP 初始化失败")
            return False

        # 发送 initialized 通知
        notify_payload = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        await self._mcp_post(notify_payload, is_notification=True)
        self._initialized = True
        logger.info(f"金十 MCP 初始化成功, session={self._session_id}")
        return True

    async def _call_tool(self, tool_name: str, arguments: dict = None) -> Optional[dict]:
        """调用 MCP 工具，优先读取 structuredContent。

        失败返回 `None`，原因见 `last_error()`（含握手失败、传输失败与**工具级**失败）。
        """
        if not await self._initialize():
            if not self._last_error:
                self._last_error = f"金十 MCP 初始化失败（工具 {tool_name} 未发起调用）"
            return None

        self._req_id += 1
        params = {"name": tool_name}
        if arguments:
            params["arguments"] = arguments

        payload = {
            "jsonrpc": "2.0",
            "id": self._req_id,
            "method": "tools/call",
            "params": params,
        }

        result = await self._mcp_post(payload)
        if not result:
            if not self._last_error:
                self._last_error = f"金十工具 {tool_name} 未取到结果（_mcp_post 返回空且未记录原因）"
            return None

        # 优先 structuredContent
        structured = result.get("structuredContent")
        if structured and isinstance(structured, dict):
            status = structured.get("status", 0)
            if status == 200:
                return structured.get("data", {})
            # 工具级失败（服务端明确告知原因），与传输层错误是两层
            self._last_error = f"金十工具 {tool_name} 返回状态 {status}: {structured.get('message', '')}"
            logger.warning("金十工具 %s 返回状态 %s", tool_name, status)
            return None

        # 回退到 content 文本
        content_items = result.get("content", []) or []
        for item in content_items:
            if item.get("type") == "text":
                try:
                    parsed = json.loads(item["text"])
                except Exception:
                    continue
                if isinstance(parsed, dict) and parsed.get("status") == 200:
                    return parsed.get("data", {})
                if isinstance(parsed, dict):
                    self._last_error = (f"金十工具 {tool_name} 文本结果状态 "
                                        f"{parsed.get('status')}: {parsed.get('message', '')}")
                    return None

        self._last_error = (f"金十工具 {tool_name} 响应既无可用 structuredContent，"
                            f"也无状态为 200 的文本结果（result_keys={sorted(result.keys())}）")
        logger.warning("金十工具 %s 响应无法解析出结果", tool_name)
        return None

    # -- Business Methods (金十工具封装) --------------------------------------

    async def get_quote(self, code: str) -> Optional[dict]:
        """获取指定品种实时行情

        Returns: {code, name, time, open, close, high, low, volume, ups_price, ups_percent}
        """
        return await self._call_tool("get_quote", {"code": code})

    async def get_kline(self, code: str, count: int = 20) -> Optional[dict]:
        """获取指定品种K线数据

        Returns: {code, name, klines: [{close, high, low, open, time, volume}, ...]}
        """
        return await self._call_tool("get_kline", {"code": code, "count": count})

    async def list_calendar(self) -> Optional[list]:
        """获取财经日历数据

        Returns: [{pub_time, star, title, previous, consensus, actual, revised, affect_txt}, ...]
        """
        data = await self._call_tool("list_calendar", {})
        if data is None:
            return None
        # list_calendar 返回 data 为数组
        return data if isinstance(data, list) else []

    async def search_flash(self, keyword: str) -> Optional[dict]:
        """按关键词搜索快讯

        Returns: {items: [{id, title, content, time, url}, ...], next_cursor, has_more}
        """
        return await self._call_tool("search_flash", {"keyword": keyword})

    async def list_flash(self, cursor: str = None) -> Optional[dict]:
        """获取最新快讯列表"""
        args = {}
        if cursor:
            args["cursor"] = cursor
        return await self._call_tool("list_flash", args)

    async def search_news(self, keyword: str) -> Optional[dict]:
        """按关键词搜索资讯"""
        return await self._call_tool("search_news", {"keyword": keyword})

    async def list_news(self, cursor: str = None) -> Optional[dict]:
        """获取最新资讯列表"""
        args = {}
        if cursor:
            args["cursor"] = cursor
        return await self._call_tool("list_news", args)

    async def get_news(self, id: str) -> Optional[dict]:
        """获取单篇资讯详情"""
        return await self._call_tool("get_news", {"id": id})

    # -- Zenith 整合方法（直接返回指标格式） ----------------------------------

    async def fetch_quote_indicators(self) -> list[dict]:
        """批量获取金十行情指标，返回与 yfinance 格式兼容的指标列表

        优先金十，失败则跳过（macro_data.py 会回退到 yfinance）
        """
        results = []
        tasks = []
        codes = []

        for code, indicator_name in QUOTE_CODE_MAP.items():
            tasks.append(self.get_quote(code))
            codes.append((code, indicator_name))

        raw = await asyncio.gather(*tasks, return_exceptions=True)

        for i, (code, indicator_name) in enumerate(codes):
            r = raw[i]
            if isinstance(r, Exception) or r is None:
                logger.debug(f"金十 {code} 获取失败，将由 yfinance 回退")
                continue
            # 金十报价数据 → Zenith 指标格式
            try:
                value = float(r.get("close", 0))
                ups_price = float(r.get("ups_price", 0))
                ups_percent = float(r.get("ups_percent", 0))
                # prev = close - ups_price (金十给出涨跌额)
                prev = round(value - ups_price, 4) if ups_price else None
                results.append({
                    "indicator": indicator_name,
                    "value": round(value, 4),
                    "change_pct": ups_percent,
                    "prev": prev if prev else round(value / (1 + ups_percent / 100), 4),
                    "source": "jin10",
                    "jin10_raw": r,  # 保留原始数据供 market_analyzer 使用
                })
            except (ValueError, TypeError) as e:
                logger.debug(f"金十 {code} 数据解析失败: {e}")
                continue

        return results

    async def fetch_calendar_events(self) -> Optional[list[dict]]:
        """获取财经日历 → Zenith 事件格式

        金十日历数据结构: pub_time, star(星级), title, previous, consensus,
                          actual, revised, affect_txt(利多/利空/中性)
        """
        cal = await self.list_calendar()
        if cal is None:
            return None

        events = []
        now = datetime.now()

        for item in cal:
            # 星级过滤：只保留 ★★★ (3星) 及以上重要事件
            # 金十星级: 1=一般, 2=重要, 3=极重要
            raw_star = item.get("star", 0)
            try:
                star = int(raw_star)
            except (ValueError, TypeError):
                star = 1
            if star < 3:
                continue  # 跳过低星级事件，减少 LLM 输入量

            pub_time = item.get("pub_time", "")
            # 判断是否逾期（pub_time 早于当前时间）
            is_overdue = False
            try:
                # 金十 pub_time 格式: "2026-07-17 20:30" 或类似
                if pub_time:
                    event_dt = datetime.strptime(pub_time[:16], "%Y-%m-%d %H:%M")
                    is_overdue = event_dt < now
            except Exception:
                pass

            # affect_txt 映射: 利多→bullish, 利空→bearish, 中性→neutral
            affect = item.get("affect_txt", "")
            direction_map = {"利多": "bullish", "利空": "bearish", "中性": "neutral"}
            direction = direction_map.get(affect, "neutral")

            events.append({
                "name": item.get("title", ""),
                "time": pub_time,
                "star": star,
                "previous": item.get("previous", ""),
                "consensus": item.get("consensus", ""),
                "actual": item.get("actual", ""),
                "revised": item.get("revised", ""),
                "affect_txt": affect,
                "direction": direction,
                "overdue": is_overdue,
                "source": "jin10",
            })

        return events

    async def fetch_flash_news(self, keywords: list[str] = None) -> Optional[list[dict]]:
        """搜索关键词快讯 → 简化格式

        默认搜索黄金相关快讯
        """
        if keywords is None:
            keywords = ["黄金", "美联储", "非农"]

        all_items = []
        for kw in keywords:
            data = await self.search_flash(kw)
            if data and isinstance(data, dict):
                items = data.get("items", [])[:15]  # 每关键词最多15条，避免过长
                for item in items:
                    all_items.append({
                        "id": item.get("id", ""),
                        "title": item.get("title", ""),
                        "content": item.get("content", ""),
                        "time": item.get("time", ""),
                        "url": item.get("url", ""),
                        "keyword": kw,
                        "source": "jin10_flash",
                    })

        return all_items if all_items else None

    # -- Cleanup -------------------------------------------------------------

    async def close(self):
        """关闭 httpx 客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
            self._initialized = False


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_jin10_service: Optional[Jin10Service] = None


def get_jin10_service() -> Jin10Service:
    global _jin10_service
    if _jin10_service is None:
        _jin10_service = Jin10Service()
    return _jin10_service
