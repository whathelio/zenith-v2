"""Settings API — key-value 配置存储"""
from fastapi import APIRouter, Body
from ..config import load_config, save_config

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
async def get_settings():
    return load_config()


@router.put("")
async def put_settings(data: dict = Body(default=None)):
    if data:
        cfg = load_config()
        cfg.update(data)
        save_config(cfg)
    return {"success": True}
