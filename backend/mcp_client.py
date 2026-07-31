"""Zenith v2 — 通用 MCP 客户端（HTTP/SSE + stdio）

支持两种传输：
- http  : 通过 serverUrl 走 JSON-RPC over HTTP（兼容 SSE 响应），复用 jin10_service 的逻辑
- stdio : 通过 command+args 拉起本地 MCP server 子进程，走 stdin/stdout 的 JSON-RPC

所有字符串配置支持 ${ENV} 占位符替换（如 jin10 的 Bearer Token 写为 Bearer ${ZENITH_JIN10_API_TOKEN}）。

连接生命周期：
- HTTP 复用 httpx.AsyncClient（会话级 Mcp-Session-Id）
- stdio 复用同一子进程，多轮 tools/call 不发重复 initialize
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger("zenith.mcp_client")

MCP_PROTOCOL_VERSION = "2025-11-25"
REQUEST_TIMEOUT = 30.0


def _substitute_env(value: Any) -> Any:
    """递归替换字符串中的 ${ENV} 占位符"""
    if isinstance(value, str):
        for k, v in os.environ.items():
            value = value.replace(f"${{{k}}}", v)
        return value
    if isinstance(value, list):
        return [_substitute_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    return value


class MCPClientError(RuntimeError):
    pass


class MCPClient:
    def __init__(self, config: dict):
        # 应用 ${ENV} 占位符
        config = _substitute_env(config)
        self.config = config
        self.name = config.get("name", "mcp-server")
        self.disabled = bool(config.get("disabled", False))

        if "command" in config and config["command"]:
            self.transport = "stdio"
            self.command = config["command"]
            self.args = config.get("args", []) or []
            self.env = config.get("env")
        elif config.get("serverUrl"):
            self.transport = "http"
            self.server_url = config["serverUrl"]
            self.headers = dict(config.get("headers", {}))
        else:
            raise MCPClientError(f"MCP '{self.name}' 缺少 command 或 serverUrl")

        self._proc: Optional[asyncio.subprocess.Process] = None
        self._http: Optional[httpx.AsyncClient] = None
        self._session_id: Optional[str] = None
        self._initialized = False
        self._req_id = 0
        self._lock = asyncio.Lock()
        self._reader_task: Optional[asyncio.Task] = None
        # stdio 响应收集：request id -> Future
        self._pending: dict[int, asyncio.Future] = {}

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------
    async def connect(self):
        async with self._lock:
            if self._initialized:
                return
            if self.transport == "stdio":
                await self._connect_stdio()
            else:
                await self._connect_http()
            self._initialized = True

    async def call_tool(self, tool_name: str, arguments: dict | None = None) -> dict:
        """调用工具，返回 result 字典（优先 structuredContent，回退 content 文本）"""
        await self.connect()
        if self.transport == "stdio":
            return await self._stdio_call_tool(tool_name, arguments or {})
        return await self._http_call_tool(tool_name, arguments or {})

    async def list_tools(self) -> list[dict]:
        await self.connect()
        if self.transport == "stdio":
            return await self._stdio_request("tools/list", {})
        return await self._http_request("tools/list", {})

    async def close(self):
        async with self._lock:
            if self._reader_task:
                self._reader_task.cancel()
                self._reader_task = None
            if self._proc and self._proc.returncode is None:
                try:
                    self._proc.terminate()
                    await asyncio.wait_for(self._proc.wait(), timeout=5)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
                self._proc = None
            if self._http and not self._http.is_closed:
                await self._http.aclose()
                self._http = None
            self._initialized = False

    # ------------------------------------------------------------------
    # HTTP (SSE) 传输 — 复用 jin10_service 逻辑
    # ------------------------------------------------------------------
    async def _connect_http(self):
        self._http = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
        await self._initialize_http()

    async def _http_request(self, method: str, params: dict) -> dict:
        self._req_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._req_id,
            "method": method,
            "params": params,
        }
        return await self._mcp_post(payload)

    async def _initialize_http(self):
        result = await self._http_request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "zenith-v2", "version": "1.0"},
            },
        )
        if not result:
            raise MCPClientError(f"HTTP MCP '{self.name}' 初始化失败")
        # initialized 通知
        await self._mcp_post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            is_notification=True,
        )

    async def _mcp_post(self, payload: dict, is_notification: bool = False) -> dict:
        headers = dict(self.headers)
        headers["Content-Type"] = "application/json"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        try:
            resp = await self._http.post(self.server_url, json=payload, headers=headers)
        except Exception as e:
            logger.warning("HTTP MCP '%s' 请求失败: %s", self.name, e)
            self._initialized = False
            return {}
        new_sid = resp.headers.get("mcp-session-id")
        if new_sid:
            self._session_id = new_sid
        if is_notification:
            return {}
        if resp.status_code != 200:
            logger.warning("HTTP MCP '%s' HTTP %s", self.name, resp.status_code)
            self._initialized = False
            return {}
        body = resp.text
        if "text/event-stream" in resp.headers.get("content-type", ""):
            json_body = self._parse_sse(body)
        else:
            try:
                json_body = json.loads(body)
            except Exception:
                json_body = {}
        if not json_body:
            return {}
        if "error" in json_body:
            logger.warning("HTTP MCP '%s' 错误: %s", self.name, json_body["error"])
            return {}
        return json_body.get("result", {})

    @staticmethod
    def _parse_sse(body_text: str) -> dict:
        data_lines = []
        for line in body_text.split("\n"):
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if not data_lines:
            return {}
        try:
            return json.loads(data_lines[-1])
        except Exception:
            combined = "".join(data_lines)
            try:
                return json.loads(combined)
            except Exception:
                return {}

    async def _http_call_tool(self, tool_name: str, arguments: dict) -> dict:
        result = await self._http_request(
            "tools/call", {"name": tool_name, "arguments": arguments}
        )
        return self._extract_tool_result(result)

    @staticmethod
    def _extract_tool_result(result: dict) -> dict:
        if not result:
            return {}
        structured = result.get("structuredContent")
        if structured and isinstance(structured, dict):
            return structured
        content_list = result.get("content", [])
        for item in content_list:
            if item.get("type") == "text":
                try:
                    parsed = json.loads(item["text"])
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    return {"text": item["text"]}
        return {}

    # ------------------------------------------------------------------
    # stdio 传输
    # ------------------------------------------------------------------
    async def _connect_stdio(self):
        env = dict(os.environ)
        if isinstance(self.env, dict):
            env.update({k: str(v) for k, v in self.env.items()})
        try:
            self._proc = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
        except Exception as e:
            raise MCPClientError(f"stdio MCP '{self.name}' 启动失败: {e}")
        self._reader_task = asyncio.create_task(self._stdio_read_loop())
        await self._stdio_initialize()

    async def _stdio_read_loop(self):
        """持续读取 stdout，按 id 派发响应；忽略 stderr 与日志行"""
        assert self._proc and self._proc.stdout
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except Exception:
                    continue  # 非 JSON（如服务器日志）直接跳过
                rid = msg.get("id")
                if rid is not None and rid in self._pending:
                    fut = self._pending.pop(rid)
                    if not fut.done():
                        if "error" in msg:
                            fut.set_exception(MCPClientError(str(msg["error"])))
                        else:
                            fut.set_result(msg.get("result", {}))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("stdio MCP '%s' 读取循环异常: %s", self.name, e)

    async def _stdio_send(self, method: str, params: dict, expect_response: bool = True) -> dict:
        if not self._proc or not self._proc.stdin:
            raise MCPClientError(f"stdio MCP '{self.name}' 未连接")
        self._req_id += 1
        rid = self._req_id
        payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        if not expect_response:
            payload.pop("id", None)
        self._proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        await self._proc.stdin.drain()
        if not expect_response:
            return {}
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        try:
            return await asyncio.wait_for(fut, timeout=REQUEST_TIMEOUT)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            raise MCPClientError(f"stdio MCP '{self.name}' 调用超时: {method}")

    async def _stdio_initialize(self):
        for attempt in range(2):
            try:
                result = await self._stdio_send(
                    "initialize",
                    {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "zenith-v2", "version": "1.0"},
                    },
                )
            except MCPClientError as e:
                raise MCPClientError(f"stdio MCP '{self.name}' 初始化失败: {e}")
            if result:
                break
            # 若服务器建议了协议版本，重试
            suggested = None
            if attempt == 0:
                # 某些服务器在错误里带 latestProtocolVersion
                pass
        await self._stdio_send("notifications/initialized", {}, expect_response=False)

    async def _stdio_request(self, method: str, params: dict) -> dict:
        return await self._stdio_send(method, params)

    async def _stdio_call_tool(self, tool_name: str, arguments: dict) -> dict:
        result = await self._stdio_send("tools/call", {"name": tool_name, "arguments": arguments})
        return self._extract_tool_result(result)


# ----------------------------------------------------------------------
# 简易连接池（按 name 缓存，进程级长连接）
# ----------------------------------------------------------------------
_POOL: dict[str, MCPClient] = {}


def build_client(server_cfg: dict) -> MCPClient:
    return MCPClient(server_cfg)


def get_client(name: str) -> Optional[MCPClient]:
    return _POOL.get(name)


def register_client(client: MCPClient):
    _POOL[client.name] = client


async def close_all():
    for c in list(_POOL.values()):
        try:
            await c.close()
        except Exception:
            pass
    _POOL.clear()
