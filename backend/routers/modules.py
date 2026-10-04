"""Modules API — Skills (from memories) + MCP configurations (WorkBuddy 风格)"""
import json
import logging
import os
from pathlib import Path
from fastapi import APIRouter, Body, HTTPException, Query
from .. import database as db
from .. import skill_loader
from ..config import (
    load_config,
    save_config,
    get_skills_dir,
    mask_sensitive_fields,
    restore_masked_fields,
)
from ..mcp_health import DEFAULT_TIMEOUT as MCP_HEALTH_TIMEOUT
from ..mcp_health import DEFAULT_TTL as MCP_HEALTH_TTL

router = APIRouter(prefix="/api/modules", tags=["modules"])

logger = logging.getLogger("zenith.modules")


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
        # memories 表无计数字段（列见 database.py:467）→ 不存在真实「使用次数」，
        # 故不再硬编码 0：读不到即返回 None（前端仅声明类型、无消费点）。
        # 「是否/何时被使用」由 last_used_at（= memories.last_touched_at）体现。
        "usage_count": m.get("usage_count"),
        "last_used_at": m.get("last_touched_at") or "",
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


# ⚠️ 静态段路由必须声明在 /skills/{skill_id} **之前**。
# FastAPI 按声明顺序匹配，动态段在前会把 /skills/stats、/skills/match、/skills/files
# 全部吞成 skill_id="stats" → 422 int_parsing。2026-09-10 修复（原为潜伏 bug）。

@router.get("/skills/stats")
async def skills_stats():
    all_memories = db.mem_list(type_="skill")
    confirmed = sum(1 for m in all_memories if m.get("importance", 0) >= 3)
    return {"loaded": len(all_memories), "total": len(all_memories), "confirmed": confirmed}


@router.get("/skills/match")
async def match_skills(scene: str = Query("")):
    if not scene.strip():
        return []
    results = db.mem_search(scene.strip()[:30], limit=10)
    skill_mems = [m for m in results if m.get("type") == "skill"]
    return [_memory_to_skill(m) for m in skill_mems[:5]]


@router.get("/skills/files")
async def list_skill_files(dir: str = Query("")):
    """列出 skills 目录下所有 SKILL.md 文件"""
    skills_dir = dir or get_skills_dir()
    scanned = skill_loader.scan_skills_dir(skills_dir)
    return {"skills_dir": skills_dir, "count": len(scanned), "skills": scanned}


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
    """记录技能被使用一次（原为空操作，只返回 {"success": True}）。

    落点说明：`{skill_id}` 是 **memories 表**的 id —— 技能以 `type='skill'` 存于 memories
    （见 get_skill / _memory_to_skill 的取数口径）。`skills` 表当前 0 行，且全仓已无任何
    建表/读写它的代码（grep 无 `INTO skills` / `FROM skills`），故不可作为落点。

    memories **无计数字段**（列见 database.py:467），无法累加次数；按既定方案复用既有
    `last_touched_at` 记录「最后一次被使用时间」，避免加列（加列需 migration，超出本队范围）。
    此处直接 UPDATE 而非复用 memory_engine.mem_touch：后者会额外把 importance +1
    （对技能是语义外副作用），且该文件正由其他队并行修改；直连更新与本文件
    confirm_skill / improve_skill 的写法一致，更自洽。
    """
    skill = db.mem_get(skill_id)
    if not skill or skill.get("type") != "skill":
        raise HTTPException(404, "Skill not found")
    from datetime import datetime
    now = datetime.now().astimezone().isoformat()
    with db.db() as c:
        c.execute("UPDATE memories SET last_touched_at = ? WHERE id = ?", (now, skill_id))
    updated = db.mem_get(skill_id)
    return {
        "success": True,
        "id": skill_id,
        "last_used_at": (updated or {}).get("last_touched_at") or now,
    }


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

@router.post("/skills/import")
async def import_skills_from_dir(data: dict = Body(default=None)):
    """从目录批量导入 SKILL.md 到 memories 表。

    返回 scanned / imported / skipped / rejected / errors 及各自明细列表。
    2026-09-28 D9：`imported` 由「尝试数」收紧为「**真正落库**数」；
    `skipped`（内容去重拦截）/`rejected`（明文密钥守卫拒绝）均**未写入**，
    此前它们被并进 imported，造成「提示导入 36 个、库里其实更少」。
    """
    dir_path = (data or {}).get("dir", "")
    skills_dir = dir_path or get_skills_dir()
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
    skills_dir = get_skills_dir()

    mem_id = skill_loader.import_skill_to_memory(
        name=name,
        frontmatter=metadata,
        body=body,
        skills_dir=skills_dir,
        source_file=str(skill_md),
    )

    # D9（2026-09-28）：必须判负。mem_add 契约：>0 落库成功；-1 明文密钥守卫拒绝；
    # -2 内容去重拦截（均**未写入**）。此前无条件返回 {"success": True, "id": -2}，
    # 前端 LibraryView 又只看 result.name → 弹「已导入技能 ✓」，而库里根本没有。
    # 与本文件其余端点一致，改用 HTTPException（request() 会把 detail 带进 e.message，
    # 前端现有 catch 无需改动即可如实提示）。
    if mem_id <= 0:
        if mem_id == -1:
            raise HTTPException(422, "技能未导入：内容含明文密钥，被安全守卫拒绝（未写入）")
        if mem_id == -2:
            raise HTTPException(409, "技能未导入：与已有记忆高度相似，被去重拦截（未写入）")
        raise HTTPException(500, f"技能写入失败：mem_add 返回 {mem_id}（未写入）")

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
    """统一返回格式：enabled 字段兼容前端，同时保留原 disabled/command/args/serverUrl"""
    server = dict(s)
    server["enabled"] = not s.get("disabled", False) and s.get("enabled", True)
    return server


@router.get("/mcp")
async def list_mcp():
    # 优先读取 WorkBuddy 真实 mcp.json（含 zenith-auditor 依赖项），回退 config.yaml 占位
    from ..mcp_config import load_mcp_servers
    servers = load_mcp_servers()
    # 只读端点：掩码 headers.Authorization / env 里的凭据。实测 mcp.json 的
    # mt5-terminal 带明文 Bearer token，原样返回等于把它交给任何本机进程。
    # 注意 enabled/count/source 都由未掩码字段派生，掩码不影响它们。
    servers = mask_sensitive_fields(servers)
    enabled = len([s for s in servers if s.get("enabled")])
    return {"servers": servers, "count": len(servers), "enabled": enabled,
            "source": "workbuddy" if servers and any(s.get("command") or s.get("serverUrl") for s in servers) else "config"}


# 注意：这两个 health 路由必须留在 /mcp/{name} 系列之前（静态段优先），
# 否则 "health" 会被当成 name 匹配走。
@router.get("/mcp/health")
async def mcp_health_all(
    refresh: bool = Query(False, description="忽略 TTL 缓存，全部重新握手"),
    timeout: float = Query(MCP_HEALTH_TIMEOUT, ge=1, le=60, description="单个服务握手超时（秒）"),
    ttl: float = Query(MCP_HEALTH_TTL, ge=0, le=3600, description="结果缓存秒数"),
):
    """MCP 服务真实健康检查。

    与 `GET /mcp` 的区别：`/mcp` 的 enabled 只是配置开关（开关开了不代表能用），
    这里做真实握手（stdio 拉起子进程 / HTTP initialize + tools/list），
    返回 ok / error / disabled / unknown，并附延迟、工具数与失败原因。

    默认走 TTL 缓存，避免每次开面板都拉起全部子进程；`refresh=1` 强制重测。
    """
    from .. import mcp_health
    from ..mcp_config import load_mcp_servers

    servers = load_mcp_servers()
    return await mcp_health.health_snapshot(servers, timeout=timeout, ttl=ttl, refresh=refresh)


@router.get("/mcp/health/{name}")
async def mcp_health_one(
    name: str,
    refresh: bool = Query(False, description="忽略缓存，重新握手"),
    timeout: float = Query(MCP_HEALTH_TIMEOUT, ge=1, le=60, description="握手超时（秒）"),
):
    """单个 MCP 服务的真实健康检查。"""
    from .. import mcp_health
    from ..mcp_config import load_mcp_servers

    cfg = next((s for s in load_mcp_servers() if s.get("name") == name), None)
    if cfg is None:
        raise HTTPException(404, f"MCP 服务不存在: {name}")
    if refresh:
        mcp_health.invalidate(name)
        return await mcp_health.check_server(cfg, timeout=timeout)
    # 复用 TTL 缓存语义：命中直接返回，未命中才握手
    snap = await mcp_health.health_snapshot([cfg], timeout=timeout)
    return (snap["servers"] or [{}])[0]


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

    # ⚠️ 防「读→写回」：headers 是 GET /api/modules/mcp 的掩码字段。调用方若把读到的
    # server 对象整包提交回来，必须把掩码换回真值，否则 token 会被写成 `Bear***yz（len=49）`。
    # （今天的前端只提交表单里的 name/url/enabled，不触发该路径；这是防后续加编辑表单踩坑。）
    from ..mcp_config import load_mcp_servers
    existing = next((s for s in load_mcp_servers() if s.get("name") == name), None)
    headers = restore_masked_fields(
        data.get("headers") or {}, (existing or {}).get("headers") or {}
    )

    # 构建 WorkBuddy 风格的条目
    entry = {"name": name, "disabled": data.get("disabled", False)}
    if data.get("serverUrl"):
        entry["serverUrl"] = data["serverUrl"]
        if headers:
            entry["headers"] = headers
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
    # 配置变了 → 该服务的健康结论作废（事件驱动失效，不做后台轮询）
    from .. import mcp_health
    mcp_health.invalidate(name)
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
        from .. import mcp_health
        # 注意是 `..mcp_config`（backend 包），不是 `.mcp_config`（backend.routers 包）。
        # 写错时此处会抛 ModuleNotFoundError，被下方 except 吞成 500，
        # 表现为「UI 上切换启用开关一直失败」。
        from ..mcp_config import save_mcp_override
        save_mcp_override(name, disabled)
        # 启停变化直接影响「是否握手」，必须让缓存失效
        mcp_health.invalidate(name)
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
        from .. import mcp_health
        from ..mcp_config import clear_mcp_override
        clear_mcp_override(name)
        mcp_health.invalidate(name)
    except Exception:
        pass
    return {"success": True}


# ---------- MCP 脚本导入的路径白名单 ----------
#
# 这个端点的产出会被 MCP 客户端当子进程拉起执行（mcp_client._connect_stdio），
# 而端点本身没有鉴权（服务只监听 127.0.0.1，本机任意进程都能 POST）——
# 「接受任意路径」于是等价于「本机任意进程可让 Zenith 执行任意脚本」。
# 因此把范围收到三处**约定位置**：项目内 / 技能目录候选链 / WorkBuddy 配置域
# （实测 7 个 stdio server 的脚本都落在 $WORKBUDDY_CONFIG_DIR\skills\... 下），
# 另加用户在 config.yaml 里显式声明的 mcp.allowed_import_dirs。
_ALLOWED_IMPORT_CONFIG_KEY = "allowed_import_dirs"


def _mcp_import_allowed_roots() -> list[Path]:
    """返回允许被 import-file 注册的目录（已 resolve、去重保序）。"""
    from ..config import PROJECT_DIR, get_skills_dir_candidates

    roots: list[Path] = [PROJECT_DIR]
    # 技能目录是「多个并列的约定位置」，只按当前生效那个做白名单会误挡其他位置。
    # 无需再单独 append get_skills_dir()：其返回值必为候选链中的元素（见 config.py
    # get_skills_dir 实现），候选链已覆盖；下方 resolve + seen 去重再做兜底。
    roots.extend(get_skills_dir_candidates())
    env_dir = os.environ.get("WORKBUDDY_CONFIG_DIR", "").strip()
    if env_dir:
        roots.append(Path(env_dir))
    roots.append(Path.home() / ".workbuddy")
    extra = (load_config().get("mcp") or {}).get(_ALLOWED_IMPORT_CONFIG_KEY, [])
    if isinstance(extra, list):
        roots.extend(Path(str(x)).expanduser() for x in extra if str(x).strip())

    out: list[Path] = []
    seen: set[str] = set()
    for r in roots:
        try:
            resolved = r.resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key not in seen:
            seen.add(key)
            out.append(resolved)
    return out


def _mcp_import_outside_roots(path: Path) -> bool:
    """path 是否在白名单之外。Windows 下 PurePath 比较自带大小写折叠。"""
    return not any(path.is_relative_to(r) for r in _mcp_import_allowed_roots())


@router.post("/mcp/import-file")
async def import_mcp_file(data: dict = Body(default=None)):
    """导入本地 MCP Server 脚本，自动检测运行时并注册到 mcp_servers。
    接受：{ "file_path": "/path/to/mcp_server.py", "name": "my-mcp" }
          { "file_path": "/path/to/mcp_server.js", "name": "my-mcp", "args": ["--flag"] }
          { "file_path": "/path/to/mcp_server.py" } — name 自动从文件名推导
          { "file_path": "/path/to/mcp_server.py", "confirm_outside": true }
              — 目录白名单之外的脚本，必须显式确认才允许注册

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

    # 白名单之外的路径要求显式确认：本机手工导入第三方 server 是真实需求，
    # 不硬挡；但必须由调用方明确表达「我知道这是目录外的脚本」。
    if _mcp_import_outside_roots(path):
        if not data.get("confirm_outside"):
            allowed = " ｜ ".join(str(r) for r in _mcp_import_allowed_roots())
            raise HTTPException(
                403,
                f"脚本路径不在允许目录内: {path}。允许的目录: {allowed}。"
                f"如确实需要注册目录外脚本，请显式传 confirm_outside=true，"
                f"或把所在目录加入 config.yaml 的 mcp.{_ALLOWED_IMPORT_CONFIG_KEY}。",
            )
        logger.warning("import-file 注册了目录白名单之外的脚本（已显式确认）: %s", path)

    if not path.is_file():
        raise HTTPException(404, f"文件不存在: {file_path}")

    suffix = path.suffix.lower()
    extra_args = data.get("args", [])
    # args 会原样拼进子进程 argv，非字符串元素会让 create_subprocess_exec 崩在启动阶段
    if not isinstance(extra_args, list) or not all(isinstance(a, str) for a in extra_args):
        raise HTTPException(400, "args 必须是字符串数组")

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

    from .. import mcp_health
    mcp_health.invalidate(entry.get("name"))

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
    skills_dir = get_skills_dir()
    files_count = 0
    if skills_dir:
        files_count = len(skill_loader.scan_skills_dir(skills_dir))
    return {
        "skills": len(skill_memories),
        "mcp_servers": len(servers),
        "mcp_enabled": enabled,
        "skill_files": files_count,
    }
