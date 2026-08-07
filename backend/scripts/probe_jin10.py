"""Zenith v2 — 金十 MCP 连通性探测脚本（一次性）

用途：验证 mcp.jin10.com 接口 + ZENITH_JIN10_API_TOKEN 是否仍有效。
背景：jin10_service 已封存 2 个月，其 _mcp_post 会把所有失败吞成 None，
无法区分 401/超时/协议错误 —— 因此先用原始 httpx POST initialize
按 HTTP 状态码分类，再走正式 list_calendar / list_flash。

用法（在项目根目录）:
    python backend/scripts/probe_jin10.py

输出：结构化 JSON 到 stdout，退出码 0=全通 / 1=接口异常 / 2=缺 token
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_DIR))

# 触发 .env 加载（config.py 模块级执行 _load_dotenv，注入 ZENITH_JIN10_API_TOKEN）
from backend import config  # noqa: E402,F401
from backend._archived.jin10_service import Jin10Service  # noqa: E402

import httpx  # noqa: E402


async def probe_raw_initialize(url: str, token: str) -> dict:
    """原始 HTTP 探测：POST initialize，按 HTTP 状态码分类错误。"""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "zenith-probe", "version": "1.0"},
        },
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            t0 = time.time()
            resp = await client.post(url, json=payload, headers=headers)
            elapsed = round(time.time() - t0, 2)
    except httpx.TimeoutException:
        return {"ok": False, "stage": "raw_initialize", "error": "TIMEOUT"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "stage": "raw_initialize", "error": f"NETWORK: {type(e).__name__}: {e}"}

    preview = (resp.text or "")[:200]
    if resp.status_code == 200:
        return {"ok": True, "stage": "raw_initialize", "http": 200, "elapsed": elapsed, "body_preview": preview}
    if resp.status_code in (401, 403):
        return {"ok": False, "stage": "raw_initialize", "error": "AUTH_FAILED", "http": resp.status_code, "body_preview": preview}
    return {"ok": False, "stage": "raw_initialize", "error": f"HTTP_{resp.status_code}", "http": resp.status_code, "body_preview": preview}


async def main() -> int:
    svc = Jin10Service()
    result: dict = {"ok": False, "stage": "", "error": "", "url": svc._url, "samples": []}

    # 0. token 检查
    if not svc._token:
        result.update(stage="token_check", error="MISSING_TOKEN")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    result["token_masked"] = f"{svc._token[:6]}...{svc._token[-4:]}" if len(svc._token) > 12 else "(short)"

    # 1. 原始 initialize（分类错误）
    raw = await probe_raw_initialize(svc._url, svc._token)
    result["stage"] = raw["stage"]
    if not raw["ok"]:
        result["error"] = raw["error"]
        result["raw"] = {k: v for k, v in raw.items() if k != "ok"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    # 2. 正式 list_calendar
    cal_error = None
    try:
        cal = await svc.list_calendar()
        if cal:
            result["calendar_count"] = len(cal)
            result["samples"].append({
                "type": "calendar",
                "items": [
                    {
                        "title": (e.get("title") or "")[:50],
                        "star": e.get("star"),
                        "pub_time": e.get("pub_time", ""),
                        "consensus": e.get("consensus", ""),
                        "previous": e.get("previous", ""),
                        "actual": e.get("actual", ""),
                        "affect_txt": e.get("affect_txt", ""),
                    }
                    for e in cal[:3]
                ],
            })
        else:
            cal_error = "CALENDAR_EMPTY"
    except Exception as e:  # noqa: BLE001
        cal_error = f"CALENDAR_ERROR: {type(e).__name__}: {e}"

    # 3. 正式 list_flash
    flash_error = None
    try:
        flash = await svc.list_flash()
        if isinstance(flash, dict):
            items = flash.get("items") or []
            result["flash_count"] = len(items)
            result["flash_has_more"] = flash.get("has_more")
            result["samples"].append({
                "type": "flash",
                "items": [
                    {"title": (i.get("title") or "")[:60], "time": i.get("time", "")}
                    for i in items[:3]
                ],
            })
        else:
            flash_error = "FLASH_EMPTY"
    except Exception as e:  # noqa: BLE001
        flash_error = f"FLASH_ERROR: {type(e).__name__}: {e}"

    try:
        await svc.close()
    except Exception:  # noqa: BLE001
        pass

    errors = [e for e in (cal_error, flash_error) if e]
    if errors:
        result["error"] = "; ".join(errors)
    else:
        result["ok"] = True
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
