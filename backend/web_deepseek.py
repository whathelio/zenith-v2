"""DeepSeek 官方 Search / Fetch 接口封装（api.deepseek.com/v1/tools/*）。

分工边界（重要，别把职责搬来搬去）：
- 本模块**只管 HTTP 与字段规范化**：鉴权、重试、错误码→中文提示、markdown 反转义、截断。
- provider 选择、回退链、缓存、日限、记账 → 全在 web_tools.py。本模块不 import 它，避免循环。

接口文档要点：
- Search: POST /v1/tools/search  {query, count(1~50), ori_question, localization, need_content}
- Fetch:  POST /v1/tools/fetch   {url}
- 计费：每次成功请求计费（与 count 无关）；失败 / 无结果 / 超时不计费。
- 错误码：400/401/402/403/404/408/413 不可重试；429/5xx/504 可重试。

返回约定（与 web_tools 现有风格一致）：
  成功 → {"success": True, ...}
  失败 → {"success": False, "result": "中文可执行提示", "status": int, "retryable": bool}
"""
from __future__ import annotations

import asyncio
import logging
import os
import re

import httpx

logger = logging.getLogger("zenith.web.ds")

_SEARCH_PATH = "/v1/tools/search"
_FETCH_PATH = "/v1/tools/fetch"
_DEFAULT_BASE = "https://api.deepseek.com"

# 可重试的状态码：文档明确「408、429 或 5xx 时等待后重试」
_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}

_STATUS_HINTS = {
    400: "请求不合法（query 需 1~128 字符且不能全空白；count 需 1~50；url 需含 host 的 http/https）",
    401: "API Key 缺失或无效，请检查 ZENITH_LLM_API_KEY / ZENITH_WEB_API_KEY",
    402: "DeepSeek 账户余额不足，请充值后重试",
    403: "请求被 DeepSeek 安全策略拒绝",
    404: "未能获取可用的网页内容（URL 不可用 / 被站点策略拦截 / 无 Markdown 正文）",
    408: "读取请求体超时",
    413: "请求体超过大小限制",
    429: "超过 QPS 限制（Search 100/s、Fetch 25/s），稍后重试",
    500: "DeepSeek 服务端内部错误",
    504: "下游请求超时",
}

# markdown 反斜杠转义：\* \_ \- \: \. 等。覆盖 ASCII 标点 + 全角标点，代码块内跳过。
_MD_ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~\u3000-\u303f\uff01-\uff5e])")
_FENCE = re.compile(r"```.*?```", re.S)


def _api_base() -> str:
    """工具接口根地址。允许带 /v1 后缀（自动剥掉，避免 /v1/v1/...）。"""
    base = (os.environ.get("ZENITH_WEB_API_BASE") or _DEFAULT_BASE).strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base


def _api_key() -> str:
    """专用 Key 优先（ZENITH_WEB_API_KEY），否则复用 LLM Key。"""
    key = (os.environ.get("ZENITH_WEB_API_KEY") or "").strip()
    if key:
        return key
    try:
        from .config import get_api_key
        return (get_api_key() or "").strip()
    except Exception:  # 配置模块异常不应让抓取直接崩
        return ""


def available() -> bool:
    """是否具备调用条件（有 Key）。无 Key 时调用方应静默回退本地实现。"""
    return bool(_api_key())


def _resolve_host_ok(url: str) -> bool:
    return url.startswith(("http://", "https://"))


def _error(status: int | None, detail: str = "") -> dict:
    hint = _STATUS_HINTS.get(status or 0, "")
    if not hint:
        hint = f"DeepSeek 接口返回 HTTP {status}" if status else detail or "请求失败"
    msg = f"DeepSeek 网页接口不可用：{hint}"
    if status:
        msg += f"（HTTP {status}）"
    if detail and status not in _STATUS_HINTS:
        msg += f"：{detail}"
    return {
        "success": False,
        "result": msg,
        "status": status or 0,
        "retryable": (status or 0) in _RETRYABLE_STATUS,
    }


async def _post(path: str, payload: dict, timeout: float) -> tuple[int, dict | str]:
    """POST 一次，返回 (status, parsed_json 或 原始文本)。网络异常 status=0。"""
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            resp = await client.post(_api_base() + path, headers=headers, json=payload)
    except httpx.TimeoutException as e:
        return 0, f"请求超时: {e}"
    except httpx.RequestError as e:
        return 0, f"网络错误: {e}"

    if resp.status_code == 200:
        try:
            return 200, resp.json()
        except ValueError:
            return 200, "响应不是合法 JSON"
    # 错误体是 OpenAI 兼容格式：{"error": {"message": ...}}
    try:
        body = resp.json()
        detail = str((body.get("error") or {}).get("message") or body)[:200]
    except ValueError:
        detail = resp.text[:200]
    return resp.status_code, detail


async def _post_with_retry(path: str, payload: dict, timeout: float, retries: int = 1) -> tuple[int, dict | str]:
    """408 / 429 / 5xx 退避重试（默认 1 次）。其它 4xx 立即返回，不浪费配额。"""
    status, body = await _post(path, payload, timeout)
    attempt = 0
    while status in _RETRYABLE_STATUS and attempt < retries:
        attempt += 1
        delay = 0.8 * attempt
        logger.info("DS %s 返回 %s，%.1fs 后重试第 %d 次", path, status, delay, attempt)
        await asyncio.sleep(delay)
        status, body = await _post(path, payload, timeout)
    return status, body


def unescape_markdown(text: str) -> str:
    """去掉 DeepSeek Fetch 返回里的 markdown 反斜杠转义（代码块内原样保留）。

    官方 content 形如「彻底火了！多家AI巨头，上线DeepSeek\\-V4」，直接喂模型
    会凭空多出反斜杠、浪费 token 也影响可读性。
    """
    if not text or "\\" not in text:
        return text or ""
    out: list[str] = []
    pos = 0
    for m in _FENCE.finditer(text):
        out.append(_MD_ESCAPE.sub(r"\1", text[pos:m.start()]))
        out.append(m.group(0))  # 代码块不反转义：里面的 \d \s 是正则本体
        pos = m.end()
    out.append(_MD_ESCAPE.sub(r"\1", text[pos:]))
    return "".join(out)


def _norm_result(r: dict) -> dict:
    """统一字段。缺失一律给安全默认值，调用方不必再判 None。"""
    return {
        "title": (r.get("title") or "").strip(),
        "url": (r.get("url") or "").strip(),
        "snippet": r.get("snippet") or "",
        "summary": r.get("summary") or "",
        "content": r.get("content") or "",
        "date": r.get("date") or "",
        "site_name": r.get("site_name") or "",
        "authority": r.get("authority", -1),
        "score": r.get("rerank_score", 0.0),
    }


async def ds_search(
    query: str,
    count: int = 10,
    need_content: bool = False,
    ori_question: str | None = None,
    timeout: float = 60.0,
) -> dict:
    """调用 Search 接口。成功返回 {"success": True, "results": [...]}。"""
    if not _api_key():
        return {"success": False, "result": "DeepSeek 未配置 API Key", "status": 0, "retryable": False}

    payload: dict = {
        "query": query[:128],
        "count": max(1, min(int(count), 50)),
        "need_content": bool(need_content),
    }
    if ori_question:
        payload["ori_question"] = ori_question[:128]

    status, body = await _post_with_retry(_SEARCH_PATH, payload, timeout)
    if status != 200 or not isinstance(body, dict):
        return _error(status, body if isinstance(body, str) else "")

    raw = body.get("results")
    if not isinstance(raw, list):
        return {"success": False, "result": "DeepSeek Search 响应缺少 results 字段", "status": 200, "retryable": False}

    results = [_norm_result(r) for r in raw if isinstance(r, dict)]
    results = [r for r in results if r["url"]]
    return {"success": True, "results": results, "raw_count": len(raw)}


async def ds_fetch(url: str, max_chars: int = 8000, timeout: float = 45.0) -> dict:
    """调用 Fetch 接口，返回 markdown 正文（已反转义 + 截断）。"""
    if not _api_key():
        return {"success": False, "result": "DeepSeek 未配置 API Key", "status": 0, "retryable": False}
    if not _resolve_host_ok(url):
        return {"success": False, "result": f"仅支持 http/https 链接，收到: {url}", "status": 0, "retryable": False}

    status, body = await _post_with_retry(_FETCH_PATH, {"url": url}, timeout)
    if status != 200 or not isinstance(body, dict):
        return _error(status, body if isinstance(body, str) else "")

    title = (body.get("title") or "").strip()
    content = unescape_markdown(body.get("content") or "").strip()
    if not content:
        return _error(404, "返回内容为空")

    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars] + "\n...(内容已截断)"

    header = f"标题: {title}\n链接: {url}\n\n" if title else f"链接: {url}\n\n"
    return {
        "success": True,
        "result": header + content,
        "title": title,
        "content": content,
        "url": url,
        "truncated": truncated,
    }
