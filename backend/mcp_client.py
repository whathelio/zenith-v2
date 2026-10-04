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
from typing import Any, Optional

import httpx

# 复用 code_runner 的环境剥离实现（_DANGEROUS_ENV_PREFIXES + clean_subprocess_env）。
# 刻意不在此处复制一份前缀常量：两份实现必然漂移，而漂移的后果是「一边加固、另一边静默泄漏」。
# 与 code_runner 唯一耦合是这两个符号，改动 code_runner 时需同步检查本文件。
from .code_runner import clean_subprocess_env

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
    def __init__(self, config: dict, trust_env: bool = True):
        # 应用 ${ENV} 占位符
        config = _substitute_env(config)
        self.config = config
        self.name = config.get("name", "mcp-server")
        self.disabled = bool(config.get("disabled", False))
        # trust_env=False 时 httpx 不读环境变量代理。本机 MCP 服务几乎都在 127.0.0.1，
        # 走系统代理会被拦截（实测 urllib 经代理访问 localhost 返回 502），
        # 故健康检查等本地探测场景应传 False。默认 True 保持既有行为不变。
        self.trust_env = trust_env

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
        # 旁路错误通道：记录最近一次 HTTP 失败的**具体**原因，仅供诊断。
        # 刻意与 return {} 的既有返回语义并存 —— 上游 tools.py:4014 依赖
        # "error" in result 判断，改动返回语义会破坏 stdio 路径。
        self._last_error: str = ""

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

    async def list_tools(self) -> dict | list[dict]:
        """``tools/list`` 的原始返回值。

        注意：这里返回的是 JSON-RPC 的 result 本体，即 ``{"tools": [...]}``，
        **不是**工具列表本身。既有调用方（tools.py）已按 dict 处理，
        为不破坏它们，返回值保持原样，此处仅把类型标注改得与实际一致。
        新代码请自行取 ``["tools"]``，并兼容 list 形态以防服务端实现差异。
        """
        await self.connect()
        if self.transport == "stdio":
            return await self._stdio_request("tools/list", {})
        return await self._http_request("tools/list", {})

    def last_error(self) -> str:
        """返回最近一次失败的原因；成功调用后为空串。

        旁路诊断通道：失败时 call_tool/list_tools 仍然返回 ``{}``（语义不变），
        调用方可在此读取「为什么空」，避免把失败当成功继续推理。
        """
        return self._last_error

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
        self._http = httpx.AsyncClient(timeout=REQUEST_TIMEOUT, trust_env=self.trust_env)
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
            # 401/403 这类鉴权失败此前被压成同一句「初始化失败」，无法分辨。
            # 这里带上 _last_error 的具体原因（如 HTTP 401: unauthorized）。
            # 异常类型必须保持 MCPClientError —— 上游 tools.py:3941 依赖它。
            detail = self._last_error or "响应为空且无错误详情"
            prefix = f"HTTP MCP '{self.name}' "
            if detail.startswith(prefix):
                detail = detail[len(prefix):]
            raise MCPClientError(f"HTTP MCP '{self.name}' 初始化失败: {detail}")
        # initialized 通知
        await self._mcp_post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            is_notification=True,
        )

    async def _mcp_post(self, payload: dict, is_notification: bool = False) -> dict:
        # 入口先清空上一次的失败原因：只有本次真正失败时才会被重新写入，
        # 从而保证「陈旧的失败原因不会被当成本次调用的结果」。
        self._last_error = ""
        headers = dict(self.headers)
        headers["Content-Type"] = "application/json"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        try:
            resp = await self._http.post(self.server_url, json=payload, headers=headers)
        except Exception as e:
            logger.warning("HTTP MCP '%s' 请求失败: %s", self.name, e)
            self._initialized = False
            self._last_error = f"HTTP MCP '{self.name}' 请求异常: {type(e).__name__}: {e}"
            return {}
        new_sid = resp.headers.get("mcp-session-id")
        if new_sid:
            self._session_id = new_sid
        if is_notification:
            return {}
        if resp.status_code != 200:
            logger.warning("HTTP MCP '%s' HTTP %s", self.name, resp.status_code)
            self._initialized = False
            detail = f"HTTP {resp.status_code}"
            try:
                snippet = " ".join((resp.text or "").split())[:300]
            except Exception:
                snippet = ""
            if snippet:
                detail = f"{detail}: {snippet}"
            self._last_error = f"HTTP MCP '{self.name}' {detail}"
            return {}
        body = resp.text
        if "text/event-stream" in resp.headers.get("content-type", ""):
            json_body = self._parse_sse(body, want_id=payload.get("id"))
        else:
            try:
                json_body = json.loads(body)
            except Exception:
                json_body = {}
        if not json_body:
            self._last_error = f"HTTP MCP '{self.name}' 响应体为空或非 JSON"
            return {}
        if "error" in json_body:
            logger.warning("HTTP MCP '%s' 错误: %s", self.name, json_body["error"])
            self._last_error = (
                f"HTTP MCP '{self.name}' JSON-RPC error: {str(json_body['error'])[:300]}"
            )
            return {}
        if "result" not in json_body:
            # 拿到帧却既无 result 也无 error —— JSON-RPC 2.0 要求两者必有其一，属协议违规。
            # 选帧规则已保证通知帧不会走到这里，故不会误伤「尾随通知」类 server。
            # （按既定要求此处只写错误通道，不新增 logger.warning。）
            self._last_error = (
                f"HTTP MCP '{self.name}' JSON-RPC 响应既无 result 也无 error: "
                f"{str(json_body)[:300]}"
            )
            return {}
        return json_body.get("result", {})

    @staticmethod
    def _parse_sse(body_text: str, want_id: Any = None) -> dict:
        """从 SSE 响应体里选出「最像本次响应」的那一帧。

        选帧规则（按形状选，不盲取末帧）：
        ① 给出了 ``want_id`` 时，优先返回 id 匹配的帧；
        ② 否则返回 cands 中最后一个含 ``result`` 或 ``error`` 键的帧
           —— 通知帧这两个键都没有，因此**不可能**再被选中（堵死根因）；
        ③ 都没有则退回 cands 末帧，保持既有语义；cands 为空返回 ``{}``。

        职责边界：本方法只负责“选帧”，**不写 ``_last_error``、不做失败判定**
        —— **调用方必须自行判定失败**（③ 可能返回不含 ``result``/``error`` 的帧）。
        """
        data_lines = []
        for line in body_text.split("\n"):
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
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

    async def _http_call_tool(self, tool_name: str, arguments: dict) -> dict:
        result = await self._http_request(
            "tools/call", {"name": tool_name, "arguments": arguments}
        )
        return self._extract_tool_result(result)

    @staticmethod
    def _extract_tool_result(result: dict) -> dict:
        if not result:
            return {}
        # MCP **工具级错误**（CallToolResult.isError）：JSON-RPC 信封层是成功的，
        # 失败发生在工具内部。此处只负责把该标志**翻译并保留**下来，
        # 失败判定交给消费侧 —— 与 `_parse_sse` 只选帧、不判失败的边界一致。
        is_error = bool(result.get("isError"))

        def stamp(out: dict) -> dict:
            """剔除入站同名键后，按信封层 isError 决定是否打标。

            内部保留键 `_mcp_is_error` 只能来源于 `result["isError"]`：
            工具返回的业务数据里若自带同名键，不得冒充成工具级错误，
            否则会把一次正常调用误报为失败。
            仅在为真时写入、**绝不写 False** —— 保证不含 isError 的既有
            server 的返回结果与改动前逐字节一致（fail-safe）。
            """
            out.pop("_mcp_is_error", None)
            if is_error:
                out["_mcp_is_error"] = True
            return out

        structured = result.get("structuredContent")
        if structured and isinstance(structured, dict):
            # structured 与 result["structuredContent"] 是同一引用，先拷贝再改
            return stamp(dict(structured))
        content_list = result.get("content", [])
        for item in content_list:
            if item.get("type") == "text":
                try:
                    parsed = json.loads(item["text"])
                    if isinstance(parsed, dict):
                        return stamp(parsed)  # json.loads 每次新建，无需拷贝
                except Exception:
                    return stamp({"text": item["text"]})
        return stamp({})

    # ------------------------------------------------------------------
    # stdio 传输
    # ------------------------------------------------------------------
    async def _connect_stdio(self):
        # 子进程环境不走 os.environ 全量继承：MCP server 是第三方脚本，
        # 没有理由拿到 ZENITH_LLM_API_KEY 这类宿主密钥。
        # 顺序不可颠倒 —— 先剥离宿主变量，再叠加 server_cfg["env"]：
        # 后者是用户主动配给该 server 的（如它自己的 API key），即使键名命中
        # 危险前缀也必须原样保留，否则「显式配置」会被自己的加固逻辑吃掉。
        clean = clean_subprocess_env()
        dropped = sorted(set(os.environ) - set(clean))
        env = clean
        if isinstance(self.env, dict):
            env.update({k: str(v) for k, v in self.env.items()})
        if dropped:
            # 静默剥离会让「MCP 读不到某个变量」变成无从排查的问题，故留下日志
            logger.info("stdio MCP '%s' 剥离宿主环境变量 %d 个: %s",
                        self.name, len(dropped), ", ".join(dropped))
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


def build_client(server_cfg: dict, trust_env: bool = True) -> MCPClient:
    return MCPClient(server_cfg, trust_env=trust_env)


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
