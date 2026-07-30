"""Modules API — Skills (from memories) + MCP configurations"""
from fastapi import APIRouter, Body, HTTPException
from .. import database as db
from ..config import load_config, save_config

router = APIRouter(prefix="/api/modules", tags=["modules"])

# ---------- Skills (type="skill" in memories table) ----------

@router.get("/skills")
async def list_skills(search: str = ""):
    all_memories = db.mem_list(type_="skill")
    if search:
        kw = search.strip().lower()
        all_memories = [m for m in all_memories
                        if kw in m.get("content", "").lower()
                        or kw in m.get("keywords", "").lower()]
    return [_memory_to_skill(m) for m in all_memories]


@router.get("/skills/stats")
async def skills_stats():
    all_memories = db.mem_list(type_="skill")
    return {"loaded": len(all_memories), "total": len(all_memories)}


@router.delete("/skills/{skill_id}")
async def delete_skill(skill_id: int):
    skill = db.mem_get(skill_id)
    if not skill or skill.get("type") != "skill":
        raise HTTPException(404, "Skill not found")
    db.mem_del(skill_id)
    return {"success": True}


# ---------- MCP Configurations ----------

@router.get("/mcp")
async def list_mcp():
    cfg = load_config()
    servers = cfg.get("mcp_servers", [])
    return {"servers": servers, "count": len(servers)}


@router.post("/mcp")
async def add_mcp(data: dict = Body(default=None)):
    if not data:
        raise HTTPException(400, "Config data required")
    cfg = load_config()
    servers = list(cfg.get("mcp_servers", []))
    name = (data.get("name") or "").strip()
    url = (data.get("url") or "").strip()
    if not name or not url:
        raise HTTPException(400, "name and url are required")

    entry = {"name": name, "url": url, "enabled": data.get("enabled", True)}
    # Upsert by name
    replaced = False
    for i, s in enumerate(servers):
        if s.get("name") == name:
            servers[i] = entry
            replaced = True
            break
    if not replaced:
        servers.append(entry)

    cfg["mcp_servers"] = servers
    save_config(cfg)
    return {"success": True, "server": entry}


@router.delete("/mcp/{name}")
async def delete_mcp(name: str):
    cfg = load_config()
    servers = cfg.get("mcp_servers", [])
    cfg["mcp_servers"] = [s for s in servers if s.get("name") != name]
    save_config(cfg)
    return {"success": True}


@router.get("/stats")
async def modules_stats():
    skill_memories = db.mem_list(type_="skill")
    cfg = load_config()
    servers = cfg.get("mcp_servers", [])
    enabled = [s for s in servers if s.get("enabled")]
    return {"skills": len(skill_memories), "mcp_servers": len(servers), "mcp_enabled": len(enabled)}


# ---------- Helper ----------

def _memory_to_skill(m: dict) -> dict:
    return {
        "id": m["id"], "name": (m.get("keywords") or "Skill").split(",")[0].strip() or "Unnamed",
        "trigger_scene": m.get("content", ""), "steps": [],
        "tags": [t.strip() for t in (m.get("keywords") or "").split(",") if t.strip()],
        "usage_count": 0, "confirmed_by_user": 1,
        "source_conv_id": m.get("source_conv_id", ""),
        "created_at": m.get("created_at", ""),
        "importance": m.get("importance", 3),
        "content": m.get("content", ""),
    }
