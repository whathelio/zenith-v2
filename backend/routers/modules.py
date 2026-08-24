"""Modules API — Skills (from memories) + MCP configurations (WorkBuddy 风格)"""
import json
import os
from pathlib import Path
from fastapi import APIRouter, Body, HTTPException, Query
from .. import database as db
from .. import skill_loader
from ..config import load_config, save_config

router = APIRouter(prefix="/api/modules", tags=["modules"])


# ---------- Helpers ----------

def _extract_mcp_required_line(content: str) -> str:
    """从技能 content 中提取 `依赖MCP：...` 行（若已存储）。"""
    for line in (content or "").split("\n"):
        if line.startswith("依赖MCP："):
            return line
    return ""


def _memory_to_skill(m: dict) -> dict:
    content = m.get("content", "")
    # Extract name from content pattern: "技能：xxx\n触发：...\n步骤：...\n依赖MCP：..."
    name = "Unnamed"
    trigger_scene = ""
    steps = []
    mcp_required = []
    if content.startswith("技能："):
        lines = content.split("\n")
        for line in lines:
            if line.startswith("技能："):
                name = line[3:].strip()
            elif line.startswith("触发："):
                trigger_scene = line[3:].strip()
            elif line.startswith("步骤："):
                try:
                    steps = json.loads(line[3:].strip())
                except (json.JSONDecodeError, TypeError):
                    steps = []
            elif line.startswith("依赖MCP："):
                mcp_required = [x.strip() for x in line[6:].split(",") if x.strip()]

    return {
        "id": m["id"],
        "name": name,
        "trigger_scene": trigger_scene,
        "steps": steps,
        "mcp_required": mcp_required,
        "tags": [t.strip() for t in (m.get("keywords") or "").split(",") if t.strip()],
        "usage_count": 0,
        "confirmed_by_user": 1 if m.get("importance", 0) >= 3 else 0,
        "source_conv_id": m.get("source_conv_id", ""),
        "created_at": m.get("created_at", ""),
        "importance": m.get("importance", 3),
        "content": content,
    }


# ---------- Skills CRUD ----------

@router.get("/skills")
async def list_skills(search: str = Query(""), confirmed: int = Query(-1)):
    all_memories = db.mem_list(type_="skill")
    result = [_memory_to_skill(m) for m in all_memories]
    if search:
        kw = search.strip().lower()
        result = [s for s in result
                  if kw in s.get("name", "").lower()
                  or kw in s.get("content", "").lower()
                  or kw in s.get("trigger_scene", "").lower()]
    if confirmed >= 0:
        result = [s for s in result if s["confirmed_by_user"] == confirmed]
    return result


@router.get("/skills/{skill_id}")
async def get_skill(skill_id: int):
    skill = db.mem_get(skill_id)
    if not skill or skill.get("type") != "skill":
        raise HTTPException(404, "Skill not found")
    return _memory_to_skill(skill)


@router.post("/skills")
async def create_skill(data: dict = Body(default=None)):
    if not data or not data.get("name"):
        raise HTTPException(400, "name is required")
    name = data["name"].strip()
    trigger_scene = data.get("trigger_scene", "").strip()
    steps = data.get("steps", [])
    tags = data.get("tags", [])

    content = f"技能：{name}"
    if trigger_scene:
        content += f"\n触发：{trigger_scene}"
    if steps:
        content += f"\n步骤：{json.dumps(steps, ensure_ascii=False)}"

    keywords = ",".join(tags) if tags else name
    mem_id = db.mem_add(
        type_="skill",
        content=content,
        importance=3,
        keywords=keywords,
        source_conv_id=data.get("source_conv_id", ""),
    )
    skill = db.mem_get(mem_id)
    return {"success": True, "id": mem_id, **_memory_to_skill(skill)}


@router.put("/skills/{skill_id}")
async def update_skill(skill_id: int, data: dict = Body(default=None)):
    if not data:
        raise HTTPException(400, "Update data required")
    skill = db.mem_get(skill_id)
    if not skill or skill.get("type") != "skill":
        raise HTTPException(404, "Skill not found")

    name = data.get("name", "").strip()
    trigger_scene = data.get("trigger_scene", "").strip()
    steps = data.get("steps", skill.get("steps", []))
    tags = data.get("tags", [])

    content = f"技能：{name or 'Unnamed'}"
    if trigger_scene:
        content += f"\n触发：{trigger_scene}"
    if steps:
        content += f"\n步骤：{json.dumps(steps, ensure_ascii=False)}"

    # 保留已存储的 MCP 依赖，避免编辑技能时丢失
    mcp_line = _extract_mcp_required_line(skill.get("content", ""))
    if mcp_line:
        content += f"\n{mcp_line}"

    from .. import database as _db
    with _db.db() as c:
        c.execute(
            "UPDATE memories SET content=?, keywords=?, importance=? WHERE id=?",
            (content, ",".join(tags) if tags else name, data.get("importance", skill.get("importance", 3)), skill_id)
        )

    updated = db.mem_get(skill_id)
    return {"success": True, **_memory_to_skill(updated)}


@router.delete("/skills/{skill_id}")
async def delete_skill(skill_id: int):
    skill = db.mem_get(skill_id)
    if not skill or skill.get("type") != "skill":
        raise HTTPException(404, "Skill not found")
    db.mem_del(skill_id)
    return {"success": True}


@router.get("/skills/stats")
async def skills_stats():
    all_memories = db.mem_list(type_="skill")
    confirmed = sum(1 for m in all_memories if m.get("importance", 0) >= 3)
    return {"loaded": len(all_memories), "total": len(all_memories), "confirmed": confirmed}


# ---------- Skill Actions ----------

@router.post("/skills/{skill_id}/confirm")
async def confirm_skill(skill_id: int):
    skill = db.mem_get(skill_id)
    if not skill or skill.get("type") != "skill":
        raise HTTPException(404, "Skill not found")
    with db.db() as c:
        c.execute("UPDATE memories SET importance = MAX(importance, 4) WHERE id = ?", (skill_id,))
    updated = db.mem_get(skill_id)
    return {"success": True, **_memory_to_skill(updated)}


@router.post("/skills/{skill_id}/use")
async def use_skill(skill_id: int):
    skill = db.mem_get(skill_id)
    if not skill or skill.get("type") != "skill":
        raise HTTPException(404, "Skill not found")
    return {"success": True, "id": skill_id}


@router.get("/skills/match")
async def match_skills(scene: str = Query("")):
    if not scene.strip():
        return []
    results = db.mem_search(scene.strip()[:30], limit=10)
    skill_mems = [m for m in results if m.get("type") == "skill"]
    return [_memory_to_skill(m) for m in skill_mems[:5]]


@router.post("/skills/{skill_id}/feedback")
async def feedback_skill(skill_id: int, data: dict = Body(default=None)):
    if not data:
        raise HTTPException(400, "Feedback data required")
    return {"success": True, "memory_id": skill_id}


@router.get("/skills/{skill_id}/suggestions")
async def get_skill_suggestions(skill_id: int):
    return {"ready": False, "feedback_count": 0, "min_required": 3}


@router.post("/skills/{skill_id}/improve")
async def improve_skill(skill_id: int, data: dict = Body(default=None)):
    if not data or not data.get("steps"):
        raise HTTPException(400, "steps is required")
    steps = data["steps"]
    skill = db.mem_get(skill_id)
    if not skill or skill.get("type") != "skill":
        raise HTTPException(404, "Skill not found")
    with db.db() as c:
        content = skill.get("content", "")
        import re as _re
        new_steps_str = f"步骤：{json.dumps(steps, ensure_ascii=False)}"
        if "步骤：" in content:
            content = _re.sub(r"步骤：.*", new_steps_str, content)
        else:
            content += f"\n{new_steps_str}"
        c.execute("UPDATE memories SET content = ? WHERE id = ?", (content, skill_id))
    updated = db.mem_get(skill_id)
    return {"success": True, **_memory_to_skill(updated)}


# ---------- Skill Directory Scan (仿 WorkBuddy 目录加载) ----------

@router.get("/skills/files")
async def list_skill_files(dir: str = Query("")):
    """列出 skills 目录下所有 SKILL.md 文件"""
    cfg = load_config()
    skills_dir = dir or cfg.get("skills_dir", os.path.expanduser("~/.workbuddy/skills"))
    scanned = skill_loader.scan_skills_dir(skills_dir)
    return {"skills_dir": skills_dir, "count": len(scanned), "skills": scanned}


@router.post("/skills/import")
async def import_skills_from_dir(data: dict = Body(default=None)):
    """从目录批量导入 SKILL.md 到 memories 表"""
    dir_path = (data or {}).get("dir", "")
    cfg = load_config()
    skills_dir = dir_path or cfg.get("skills_dir", os.path.expanduser("~/.workbuddy/skills"))
    result = skill_loader.import_all_from_dir(skills_dir)
    return result


@router.post("/skills/import-file")
async def import_skill_file(data: dict = Body(default=None)):
    """导入单个 SKILL.md 文件到 memories 表。
    接受：{ "file_path": "/path/to/SKILL.md" }
    或：  { "file_path": "/path/to/skill-dir" } — 自动查找目录下的 SKILL.md"""
    if not data:
        raise HTTPException(400, "file_path is required")
    file_path = (data.get("file_path") or "").strip()
    if not file_path:
        raise HTTPException(400, "file_path is required")

    path = Path(os.path.expanduser(file_path)).resolve()

    # 如果是目录，自动找 SKILL.md
    if path.is_dir():
        skill_md = path / "SKILL.md"
        if not skill_md.exists():
            raise HTTPException(404, f"目录 {path} 中没有 SKILL.md")
    elif path.is_file():
        skill_md = path
    else:
        raise HTTPException(404, f"路径不存在: {file_path}")

    # 读取解析
    try:
        with open(skill_md, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception as e:
        raise HTTPException(400, f"读取文件失败: {e}")

    metadata, body = skill_loader._parse_frontmatter(raw)
    name = metadata.get("name", skill_md.parent.name if skill_md.parent.name else skill_md.stem)
    skills_dir = os.path.expanduser("~/.workbuddy/skills")

    mem_id = skill_loader.import_skill_to_memory(
        name=name,
        frontmatter=metadata,
        body=body,
        skills_dir=skills_dir,
        source_file=str(skill_md),
    )

    skill = db.mem_get(mem_id)
    result = {
        "success": True,
        "id": mem_id,
        "source": str(skill_md),
        "name": name,
        "mcp_required": metadata.get("mcp_required", []),
    }
    if skill:
        result.update(_memory_to_skill(skill))
    return result


# ---------- MCP Configurations (仿 WorkBuddy mcp.json 格式) ----------

def _normalize_mcp_server(s: dict) -> dict:
    """统一返回格式：enabled 字段兼容前��，同时保留原 disabled/command/args/serverUrl"""
    server = dict(s)
    server["enabled"] = not s.get("disabled", False) and s.get("enabled", True)
    return server


@router.get("/mcp")
async def list_mcp():
    # 优先读取 WorkBuddy 真实 mcp.json（含 zenith-auditor 依赖项），回退 config.yaml 占位
    from ..mcp_config import load_mcp_servers
    servers = load_mcp_servers()
    enabled = len([s for s in servers if s.get("enabled")])
    return {"servers": servers, "count": len(servers), "enabled": enabled,
            "source": "workbuddy" if servers and any(s.get("command") or s.get("serverUrl") for s in servers) else "config"}


@router.post("/mcp")
async def add_mcp(data: dict = Body(default=None)):
    """添加 MCP 服务（支持 HTTP 和 stdio 两种类型）"""
    if not data:
        raise HTTPException(400, "Config data required")
    cfg = load_config()
    servers = list(cfg.get("mcp_servers", []))

    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name is required")

    # 构建 WorkBuddy 风格的��目
    entry = {"name": name, "disabled": data.get("disabled", False)}
    if data.get("serverUrl"):
        entry["serverUrl"] = data["serverUrl"]
        if data.get("headers"):
            entry["headers"] = data["headers"]
    elif data.get("command"):
        entry["command"] = data["command"]
        entry["args"] = data.get("args", [])
    elif data.get("url"):
        # 兼容旧格式
        entry["url"] = data["url"]
        entry["enabled"] = data.get("enabled", True)
    else:
        raise HTTPException(400, "需要 serverUrl (HTTP) 或 command (stdio) 或 url (兼容)")

    if data.get("description"):
        entry["description"] = data["description"]

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


@router.put("/mcp/{name}")
async def update_mcp(name: str, data: dict = Body(default=None)):
    """切换 MCP 服务的启用/禁用状态。

    覆盖写入 Zenith 本地 config/mcp_overrides.json（不改动共享的 ~/.workbuddy/mcp.json），
    因此对任意来源的 MCP 服务（包括从 mcp.json 读入的）都能生效。
    """
    if not data:
        raise HTTPException(400, "Update data required")
    if "enabled" in data:
        disabled = not bool(data.get("enabled", True))
    elif "disabled" in data:
        disabled = bool(data.get("disabled"))
    else:
        raise HTTPException(400, "需要 enabled 或 disabled 字段")
    try:
        from .mcp_config import save_mcp_override
        save_mcp_override(name, disabled)
    except Exception as e:
        raise HTTPException(500, f"保存覆盖失败: {e}")
    return {"success": True, "name": name, "disabled": disabled}


@router.delete("/mcp/{name}")
async def delete_mcp(name: str):
    cfg = load_config()
    servers = cfg.get("mcp_servers", [])
    cfg["mcp_servers"] = [s for s in servers if s.get("name") != name]
    save_config(cfg)
    # 同时清除本地覆盖，避免残留禁用状态
    try:
        from .mcp_config import clear_mcp_override
        clear_mcp_override(name)
    except Exception:
        pass
    return {"success": True}


@router.post("/mcp/import-file")
async def import_mcp_file(data: dict = Body(default=None)):
    """导入本地 MCP Server 脚本，自动检测运行时并注册到 mcp_servers。
    接受：{ "file_path": "/path/to/mcp_server.py", "name": "my-mcp" }
          { "file_path": "/path/to/mcp_server.js", "name": "my-mcp", "args": ["--flag"] }
          { "file_path": "/path/to/mcp_server.py" } — name 自动从文件名推导

    自动检测：
      .py   → command = python 解释器路径
      .js   → command = node 路径
      .sh   → command = bash 路径
    """
    if not data:
        raise HTTPException(400, "file_path is required")

    file_path = (data.get("file_path") or "").strip()
    if not file_path:
        raise HTTPException(400, "file_path is required")

    path = Path(os.path.expanduser(file_path)).resolve()
    if not path.is_file():
        raise HTTPException(404, f"文件不存在: {file_path}")

    suffix = path.suffix.lower()
    extra_args = data.get("args", [])

    # 自动推导运行时
    python_runtimes = [
        os.path.expanduser("~/.workbuddy/binaries/python/envs/default/Scripts/python.exe"),
        os.path.expanduser("~/.workbuddy/binaries/python/versions/3.13.12/python.exe"),
        "python",
    ]
    node_runtimes = [
        os.path.expanduser("~/.workbuddy/binaries/node/versions/22.12.0/node.exe"),
        "node",
    ]

    if suffix == ".py":
        command = next((r for r in python_runtimes if Path(r).exists()), "python")
        args = [str(path)] + extra_args
    elif suffix in (".js", ".mjs"):
        command = next((r for r in node_runtimes if Path(r).exists()), "node")
        args = [str(path)] + extra_args
    elif suffix == ".sh":
        command = "bash"
        args = [str(path)] + extra_args
    else:
        raise HTTPException(400, f"不支持的文件类型: {suffix}，请使用 .py / .js / .mjs / .sh")

    # 推导名称
    name = data.get("name", "").strip()
    if not name:
        name = path.stem.replace("_", "-").replace(" ", "-")

    # 检测是否是 stdio 类型 MCP（检查文件内容是否含 mcp.server/stdio）
    detection = _detect_mcp_type(path)
    mcp_type = detection["type"]  # stdio / http / unknown

    entry = {
        "name": name,
        "command": command,
        "args": args,
        "disabled": data.get("disabled", False),
        "description": data.get("description", f"Imported from {file_path}"),
        "type": mcp_type,
    }

    cfg = load_config()
    servers = list(cfg.get("mcp_servers", []))

    # Upsert
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

    return {
        "success": True,
        "server": entry,
        "replaced": replaced,
        "detection": detection,
        "source": str(path),
    }


def _detect_mcp_type(file_path: Path) -> dict:
    """快速检测 MCP Server 脚本的类型"""
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        has_mcp_server = "from mcp.server" in content or "import mcp" in content
        has_stdio = "stdio" in content
        has_http = "fastapi" in content.lower() or "flask" in content.lower() or \
                   "http" in content.lower() or "sse" in content.lower()

        if has_mcp_server and has_stdio:
            return {"type": "stdio", "confident": True}
        elif has_http and has_mcp_server:
            return {"type": "http", "confident": True}
        elif has_mcp_server:
            return {"type": "stdio", "confident": False, "note": "未明确检测到传输方式，默认 stdio"}
        else:
            return {"type": "unknown", "confident": False, "note": "未检测到 mcp.server 导入，可能不是 MCP Server"}
    except Exception:
        return {"type": "unknown", "confident": False, "note": "无法读取文件内容"}


# ---------- Stats ----------

@router.get("/stats")
async def modules_stats():
    skill_memories = db.mem_list(type_="skill")
    from ..mcp_config import load_mcp_servers
    servers = load_mcp_servers()
    enabled = len([s for s in servers if s.get("enabled")])
    skills_dir = load_config().get("skills_dir", "")
    files_count = 0
    if skills_dir:
        files_count = len(skill_loader.scan_skills_dir(skills_dir))
    return {
        "skills": len(skill_memories),
        "mcp_servers": len(servers),
        "mcp_enabled": enabled,
        "skill_files": files_count,
    }
