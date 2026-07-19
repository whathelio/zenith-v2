"""Zenith v2 — 金十数据 MCP 客户端服务 — 已封存 (SEALED)

本模块随 market_analyzer.py 一起封存。金十 MCP 本身工作正常，但市场行情分析功能已禁用。
所有工具方法保留，供未来恢复市场分析时直接复用。"""
from __future__ import annotations

import asyncio
import json
import logging
import yaml
from datetime import datetime
from pathlib import Path
from typing import Optional

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
    """从 config.yaml 读取 jin10 配置段"""
    try:
        with open(CONFIG_YAML, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        jin10_cfg = cfg.get("jin10", {})
        return jin10_cfg
    except Exception:
        return {}


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

    # -- Session Management --------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 httpx 客户端"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30)
            self._initialized = False  # 新客户端需要重新初始化
        return self._client

    async def _mcp_post(self, payload: dict, is_notification: bool = False) -> dict:
        """发送 MCP JSON-RPC POST 请求，支持 SSE 响应格式"""
        if not self._token:
            logger.debug("金十 API Token 未配置，跳过 MCP 请求")
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
                # 通知不期望响应（SSE 可能返回空流或直接关闭）
                return {}

            if resp.status_code != 200:
                logger.warning(f"金十 MCP HTTP {resp.status_code}")
                self._initialized = False
                return {}

            # 解析响应 — SSE 格式或普通 JSON
            content_type = resp.headers.get("content-type", "")
            body_text = resp.text

            logger.debug(f"金十 MCP 响应: ct={content_type}, len={len(body_text)}, body[:200]={body_text[:200]}")

            if "text/event-stream" in content_type:
                # SSE 格式: event: message\ndata: {json}\n\n
                json_body = self._parse_sse_body(body_text)
            else:
                # 普通 JSON
                try:
                    json_body = json.loads(body_text)
                except Exception:
                    json_body = {}

            if not json_body:
                logger.warning(f"金十 MCP 响应解析失败, body_len={len(body_text)}")
                return {}

            if "error" in json_body:
                logger.warning(f"金十 MCP 错误: {json_body['error']}")
                return {}
            return json_body.get("result", {})

        except Exception as e:
            logger.warning(f"金十 MCP 请求失败: {e}")
            self._initialized = False
            return {}

    @staticmethod
    def _parse_sse_body(body_text: str) -> dict:
        """解析 SSE 响应体，提取 JSON-RPC 消息

        SSE 格式:
            event: message
            data: {"jsonrpc":"2.0","id":1,"result":{...}}

        可能有多个事件，取最后一个 message 事件。
        """
        data_lines = []
        for line in body_text.split("\n"):
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())  # 去掉 "data:" 前缀

        if not data_lines:
            return {}

        # 取最后一个 data 行（通常只有一个）
        try:
            return json.loads(data_lines[-1])
        except Exception:
            # 如果单行解析失败，尝试合并所有 data 行
            combined = "".join(data_lines)
            try:
                return json.loads(combined)
            except Exception:
                return {}

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
        """调用 MCP 工具，优先读取 structuredContent"""
        if not await self._initialize():
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
            return None

        # 优先 structuredContent
        structured = result.get("structuredContent")
        if structured and isinstance(structured, dict):
            status = structured.get("status", 0)
            if status == 200:
                return structured.get("data", {})
            else:
                logger.warning(f"金十工具 {tool_name} 返回状态 {status}: {structured.get('message', '')}")
                return None

        # 回退到 content 文本
        content_list = result.get("content", [])
        if content_list:
            for item in content_list:
                if item.get("type") == "text":
                    try:
                        import json
                        parsed = json.loads(item["text"])
                        if parsed.get("status") == 200:
                            return parsed.get("data", {})
                    except Exception:
                        pass

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
