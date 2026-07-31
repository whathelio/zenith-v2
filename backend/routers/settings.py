"""Settings API — key-value 配置存储"""
import logging
from fastapi import APIRouter, Body
from ..config import load_config, save_config

router = APIRouter(prefix="/api/settings", tags=["settings"])
logger = logging.getLogger("zenith.settings")


@router.get("")
async def get_settings():
    return load_config()


@router.put("")
async def put_settings(data: dict = Body(default=None)):
    if data:
        cfg = load_config()
        # 安全防护：不允许清空 providers。若传入空数组且现有配置非空，保留现有。
        if ("providers" in data and not data["providers"]
                and cfg.get("providers")):
            logger.warning("拒绝清空 providers，保留现有 %d 个 provider", len(cfg["providers"]))
            del data["providers"]
        cfg.update(data)
        save_config(cfg)
    return {"success": True}
