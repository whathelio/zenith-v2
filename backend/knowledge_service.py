"""Zenith 知识库薄代理

把前端 /api/knowledge/* 请求转发到外部 api_gateway（默认 http://localhost:8788）。
不在 Zenith 主进程里引入 chromadb/torch，保持主服务轻量。

对应评审 §2.3 API 中台契约：Zenith 只做转发，鉴权/队列/RAG 逻辑都在外部进程。
"""
from __future__ import annotations

import os
import logging
from typing import Optional

import httpx

from .http_client import get_client

logger = logging.getLogger("zenith.knowledge")

KNOWLEDGE_API_BASE = os.environ.get("KNOWLEDGE_API_BASE", "http://localhost:8788")
# 不设公开默认值（2026-10-14 安全债 D9b）：主服务是**客户端**，不能自造随机值
# （会与网关不一致），故走「显式空 + WARNING」，绝不回落公开默认值。
KNOWLEDGE_API_KEY = os.environ.get("KNOWLEDGE_API_KEY", "")
if not KNOWLEDGE_API_KEY:
    logger.warning("KNOWLEDGE_API_KEY 未设置 —— 对 api_gateway 的 /api/knowledge/* 调用将 401；"
                   "请在 .env 设置（须与网关的 ZENITH_API_KEY 同值）")
TIMEOUT = 60.0


def _headers() -> dict:
    return {"X-API-Key": KNOWLEDGE_API_KEY}


def _err_from_response(r) -> dict:
    """把网关的错误响应转成 {error, code}，**保留网关自己的语义 code**。

    🔴 2026-10-08 修复（code-reviewer 查出）：原来一律 `code=f"HTTP_{r.status_code}"`，
    导致网关返回 400 + `{"code":"PROTECTED"}` 时被改写成 `HTTP_400` ——
    上层路由里 `if code == "PROTECTED"` 的判断成了**死分支**，用户拿到的是 200 + 一坨原始 JSON 字符串。
    """
    code = f"HTTP_{r.status_code}"
    msg = r.text
    try:
        body = r.json()
        if isinstance(body, dict):
            code = body.get("code") or code
            msg = body.get("error") or msg
    except Exception:  # noqa: BLE001 —— 非 JSON 响应就退回原始文本
        pass
    return {"error": msg, "code": code}


async def health() -> dict:
    r = await get_client().get(f"{KNOWLEDGE_API_BASE}/health", timeout=5.0)
    r.raise_for_status()
    return r.json()


async def search(question: str, top_k: int = 5, collection: Optional[str] = None) -> dict:
    """检索。collection 为空则走网关默认集合（zotero_papers）。

    2026-10-08（C-4 多集合改造）：补 ConnectError/Timeout 捕获 ——
    原实现与 wiki_query 同样裸抛，网关未启动时路由无守卫会 500。
    """
    body: dict = {"question": question, "top_k": top_k}
    if collection:
        body["collection"] = collection
    try:
        r = await get_client().post(
            f"{KNOWLEDGE_API_BASE}/search",
            headers=_headers(),
            json=body,
            timeout=TIMEOUT,
        )
    except httpx.ConnectError as e:
        logger.warning("knowledge search gateway down: %s", e)
        return {"error": "知识库服务未启动", "code": "GATEWAY_DOWN"}
    except httpx.TimeoutException as e:
        logger.warning("knowledge search timeout: %s", e)
        return {"error": "知识库服务响应超时", "code": "GATEWAY_TIMEOUT"}
    if r.status_code >= 400:
        return {"error": r.text, "code": f"HTTP_{r.status_code}"}
    return r.json()


async def list_collections() -> dict:
    """列出全部 RAG 集合（含 chunks/docs 计数）、默认集合与本地入库白名单状态。"""
    try:
        r = await get_client().get(
            f"{KNOWLEDGE_API_BASE}/collections", headers=_headers(), timeout=10.0
        )
    except httpx.ConnectError as e:
        logger.warning("knowledge collections gateway down: %s", e)
        return {"error": "知识库服务未启动", "code": "GATEWAY_DOWN", "collections": []}
    if r.status_code >= 400:
        return {"error": r.text, "code": f"HTTP_{r.status_code}", "collections": []}
    return r.json()


async def archive_collection(name: str) -> dict:
    """归档（改名保留，不删除）一个集合。默认集合会被网关拒绝（PROTECTED）。"""
    try:
        r = await get_client().post(
            f"{KNOWLEDGE_API_BASE}/collections/{name}/archive",
            headers=_headers(),
            timeout=30.0,
        )
    except httpx.ConnectError as e:
        logger.warning("knowledge archive gateway down: %s", e)
        return {"error": "知识库服务未启动", "code": "GATEWAY_DOWN"}
    if r.status_code >= 400:
        return _err_from_response(r)
    return r.json()


async def wiki_query(question: str) -> dict:
    """转发《史记》wiki 问答到 api_gateway /wiki。

    2026-09-11：补网关不可用时的捕获。原 search() 未捕获 ConnectError，
    网关未启动会裸抛异常 → 路由无守卫 → 500。此处沿用 ingest_file 的失败语义，
    返回 {error, code}，由前端展示友好提示而非裸 500。
    """
    try:
        r = await get_client().post(
            f"{KNOWLEDGE_API_BASE}/wiki",
            headers=_headers(),
            json={"question": question},
            timeout=TIMEOUT,
        )
    except httpx.ConnectError as e:
        logger.warning("knowledge wiki gateway down: %s", e)
        return {"error": "知识库服务未启动", "code": "GATEWAY_DOWN"}
    except httpx.TimeoutException as e:
        logger.warning("knowledge wiki timeout: %s", e)
        return {"error": "知识库服务响应超时", "code": "GATEWAY_TIMEOUT"}
    if r.status_code >= 400:
        return {"error": r.text, "code": f"HTTP_{r.status_code}"}
    return r.json()


async def create_task(task_type: str, payload: dict) -> dict:
    r = await get_client().post(
        f"{KNOWLEDGE_API_BASE}/tasks",
        headers=_headers(),
        json={"type": task_type, "payload": payload},
        timeout=10.0,
    )
    if r.status_code >= 400:
        return {"error": r.text, "code": f"HTTP_{r.status_code}"}
    return r.json()


async def get_task(task_id: str) -> dict:
    r = await get_client().get(
        f"{KNOWLEDGE_API_BASE}/tasks/{task_id}",
        headers=_headers(),
        timeout=10.0,
    )
    if r.status_code >= 400:
        return {"error": r.text, "code": f"HTTP_{r.status_code}"}
    return r.json()


async def list_tasks(status: Optional[str] = None, limit: int = 20) -> dict:
    params = {"limit": limit}
    if status:
        params["status"] = status
    r = await get_client().get(
        f"{KNOWLEDGE_API_BASE}/tasks",
        headers=_headers(),
        params=params,
        timeout=10.0,
    )
    if r.status_code >= 400:
        return {"error": r.text, "code": f"HTTP_{r.status_code}"}
    return r.json()


async def list_docs(collection: Optional[str] = None) -> dict:
    """列出已入库文档（逐段学习用）。collection 为空 → 网关默认集合。"""
    params = {"collection": collection} if collection else None
    r = await get_client().get(
        f"{KNOWLEDGE_API_BASE}/documents", headers=_headers(), params=params, timeout=10.0
    )
    if r.status_code >= 400:
        return {"error": r.text, "code": f"HTTP_{r.status_code}", "docs": []}
    return r.json()


async def get_doc_chunks(item_id: int, collection: Optional[str] = None) -> dict:
    """按 item_id 取该文档全部段落文本

    注：item_id 由「文件名」稳定哈希得出（C-7），同一文件在不同集合中 id 相同，
    因此跨集合按 id 取段落时必须带 collection 消歧，否则可能取到另一个集合的同名段落。
    """
    params = {"collection": collection} if collection else None
    r = await get_client().get(
        f"{KNOWLEDGE_API_BASE}/documents/{item_id}/chunks",
        headers=_headers(), params=params, timeout=30.0,
    )
    if r.status_code >= 400:
        return {"error": r.text, "code": f"HTTP_{r.status_code}", "chunks": []}
    return r.json()


async def ingest_file(filename: str, content: bytes, collection: Optional[str] = None) -> dict:
    """转发文件上传到 api_gateway /ingest（PDF/Markdown/文本）。"""
    lower = filename.lower()
    if lower.endswith(".pdf"):
        content_type = "application/pdf"
    elif lower.endswith(".md") or lower.endswith(".txt"):
        content_type = "text/plain"
    else:
        return {"error": "仅支持 PDF / Markdown / 文本", "code": "BAD_TYPE"}
    params = {"collection": collection} if collection else None
    try:
        r = await get_client().post(
            f"{KNOWLEDGE_API_BASE}/ingest",
            headers=_headers(),
            params=params,
            files={"file": (filename, content, content_type)},
            timeout=120.0,
        )
        if r.status_code >= 400:
            return {"error": r.text, "code": f"HTTP_{r.status_code}"}
        return r.json()
    except httpx.ConnectError as e:
        logger.warning("knowledge ingest gateway down: %s", e)
        return {"error": "知识库服务未启动", "code": "GATEWAY_DOWN"}
    except httpx.TimeoutException as e:
        logger.warning("knowledge ingest timeout: %s", e)
        return {"error": "知识库服务响应超时", "code": "GATEWAY_TIMEOUT"}
    except Exception as e:
        logger.exception("knowledge ingest failed: %s", e)
        return {"error": str(e), "code": "INGEST_ERROR"}
