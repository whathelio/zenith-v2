"""Browser API — 在系统默认浏览器打开外链（A15 契约补齐）

前端链路：Markdown.tsx 将 LLM 输出中的外链渲染为 `.external-link`（data-url），
ChatMessages.tsx 用户点击时调 `POST /api/open-url`（body `{url}`，返回 `{success: bool}`），
失败前端回退 `window.open`。此前全仓无该端点，点击只会走回退路径。

复用根级 `browser_launcher.open_url`：支持配置指定浏览器 + 系统默认回退，永不抛异常。
"""
import sys
import logging
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# 与 start.py 启动方式对齐：确保仓库根在 sys.path（browser_launcher 位于根级）。
# 本文件在 backend/routers/ 下，故 parents[2] 才是仓库根（parents[0]=routers、
# parents[1]=backend、parents[2]=zenith-v2）。此前误写 parents[1]，靠 start.py 预先
# 注入仓库根才没炸；独立导入（单测/脚本）会 ImportError —— 见 _probe_browser_root.py。
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from browser_launcher import open_url as _open_url  # noqa: E402

router = APIRouter(prefix="/api", tags=["browser"])
logger = logging.getLogger("zenith.browser")


class OpenUrlRequest(BaseModel):
    url: str


def _validate_open_url(url: str) -> str:
    """校验待打开的外链，返回规范化后的 url；不合法则抛 HTTPException(400)。

    抽成独立函数便于单测（_probe_browser_root.py / 未来 pytest），避免测试触发真实开浏览器。
    """
    url = (url or "").strip()
    if not url:
        raise HTTPException(400, "url 不能为空")
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(400, "仅支持 http/https 链接")
    # 拒绝 userinfo 形式（https://trusted.com@evil.com）：经典钓鱼/混淆向量，
    # 且本端点的 URL 来自 LLM 输出渲染的外链，攻击面真实存在。
    # ⚠️ 只检查 authority(netloc) 段：path/query 里的 @ 是合法的
    # （https://example.com/@user、https://x.com/a?e=a@b.com），不应误拒。
    try:
        netloc = urlsplit(url).netloc
    except ValueError:
        # urlsplit 对畸形输入（如非法方括号 IPv6）可能抛 ValueError —— 按 400 处理，别泄漏 500。
        raise HTTPException(400, "链接格式非法")
    if "@" in netloc:
        raise HTTPException(400, "不允许包含 userinfo(@) 的链接")
    return url


@router.post("/open-url")
async def open_url_endpoint(body: OpenUrlRequest):
    """在系统默认浏览器打开 `url`。仅允许 http/https scheme（防 file:// 等滥用）。"""
    url = _validate_open_url(body.url)
    try:
        mode = _open_url(url)
        logger.info("open-url: %s (mode=%s)", url, mode)
        return {"success": True}
    except Exception as e:  # browser_launcher 设计上不抛，双保险
        logger.warning("open-url 失败: %s (%s)", url, e)
        return {"success": False, "error": str(e)}
