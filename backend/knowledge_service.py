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

logger = logging.getLogger("zenith.knowledge")

KNOWLEDGE_API_BASE = os.environ.get("KNOWLEDGE_API_BASE", "http://localhost:8788")
KNOWLEDGE_API_KEY = os.environ.get("KNOWLEDGE_API_KEY", "test-key")
TIMEOUT = 60.0


def _headers() -> dict:
    return {"X-API-Key": KNOWLEDGE_API_KEY}


async def health() -> dict:
    async with httpx.AsyncClient(timeout=5.0) as c:
        r = await c.get(f"{KNOWLEDGE_API_BASE}/health")
        r.raise_for_status()
        return r.json()


async def search(question: str, top_k: int = 5) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(
            f"{KNOWLEDGE_API_BASE}/search",
            headers=_headers(),
            json={"question": question, "top_k": top_k},
        )
        if r.status_code >= 400:
            return {"error": r.text, "code": f"HTTP_{r.status_code}"}
        return r.json()


async def wiki_query(question: str) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(
            f"{KNOWLEDGE_API_BASE}/wiki",
            headers=_headers(),
            json={"question": question},
        )
        if r.status_code >= 400:
            return {"error": r.text, "code": f"HTTP_{r.status_code}"}
        return r.json()


async def create_task(task_type: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(
            f"{KNOWLEDGE_API_BASE}/tasks",
            headers=_headers(),
            json={"type": task_type, "payload": payload},
        )
        if r.status_code >= 400:
            return {"error": r.text, "code": f"HTTP_{r.status_code}"}
        return r.json()


async def get_task(task_id: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(
            f"{KNOWLEDGE_API_BASE}/tasks/{task_id}",
            headers=_headers(),
        )
        if r.status_code >= 400:
            return {"error": r.text, "code": f"HTTP_{r.status_code}"}
        return r.json()


async def list_tasks(status: Optional[str] = None, limit: int = 20) -> dict:
    params = {"limit": limit}
    if status:
        params["status"] = status
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(
            f"{KNOWLEDGE_API_BASE}/tasks",
            headers=_headers(),
            params=params,
        )
        if r.status_code >= 400:
            return {"error": r.text, "code": f"HTTP_{r.status_code}"}
        return r.json()


async def ingest_pdf(filename: str, content: bytes) -> dict:
    """转发 PDF 上传到 api_gateway /ingest。"""
    async with httpx.AsyncClient(timeout=120.0) as c:
        r = await c.post(
            f"{KNOWLEDGE_API_BASE}/ingest",
            headers=_headers(),
            files={"file": (filename, content, "application/pdf")},
        )
        if r.status_code >= 400:
            return {"error": r.text, "code": f"HTTP_{r.status_code}"}
        return r.json()
