"""Skill Loader — 仿 WorkBuddy 目录扫描 SKILL.md 文件加载技能"""
import re
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("zenith.skill_loader")


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 YAML frontmatter（零依赖，纯正则）。
    返回 (metadata_dict, body_text)"""
    if not text.startswith("---"):
        return {}, text

    end = text.find("---", 3)
    if end == -1:
        return {}, text

    yaml_block = text[3:end].strip()
    body = text[end + 3:].strip()

    metadata = {}
    current_key = None
    for line in yaml_block.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Key: value
        kv_match = re.match(r'^(\w[\w_-]*)\s*:\s*(.*)', stripped)
        if kv_match:
            key = kv_match.group(1)
            value = kv_match.group(2).strip()
            # Handle list continuation
            if value == "":
                metadata[key] = []
                current_key = key
            else:
                # Strip quotes
                if (value.startswith('"') and value.endswith('"')) or \
                   (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]
                # Handle multiline string indicator >
                if value == ">":
                    current_key = key
                    metadata[key] = ""
                else:
                    metadata[key] = value
                    current_key = None
        # List item
        elif stripped.startswith("- ") and current_key:
            item = stripped[2:].strip()
            if (item.startswith('"') and item.endswith('"')) or \
               (item.startswith("'") and item.endswith("'")):
                item = item[1:-1]
            if current_key not in metadata:
                metadata[current_key] = []
            metadata[current_key].append(item)
        # Multiline continuation (value already started with >)
        elif current_key and current_key in metadata and isinstance(metadata[current_key], str):
            metadata[current_key] += " " + stripped

    return metadata, body


def scan_skills_dir(skills_dir: str) -> list[dict]:
    """扫描目录下所有 SKILL.md 文件，返回技能列表。
    仿 WorkBuddy 的 agentskills.io 目录结构：
    <skills_dir>/
      <skill-name>/
        SKILL.md       ← YAML frontmatter + Markdown body
        scripts/        ← 可选
        references/     ← 可选
    """
    skills = []
    skills_path = Path(skills_dir).expanduser().resolve()

    if not skills_path.exists():
        logger.warning("Skills 目录不存在: %s", skills_path)
        return skills

    for skill_dir in sorted(skills_path.iterdir()):
        if not skill_dir.is_dir():
            continue

        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        try:
            with open(skill_md, "r", encoding="utf-8") as f:
                raw = f.read()

            metadata, body = _parse_frontmatter(raw)
            name = metadata.get("name", skill_dir.name)
            description = metadata.get("description", "")
            mcp_required = metadata.get("mcp_required", [])

            # Check for scripts
            scripts_dir = skill_dir / "scripts"
            has_scripts = scripts_dir.exists() and any(scripts_dir.iterdir())

            skills.append({
                "name": name,
                "directory": str(skill_dir),
                "description": description,
                "body": body[:500],  # 截断
                "mcp_required": mcp_required if isinstance(mcp_required, list) else [],
                "has_scripts": has_scripts,
                "file_size": len(raw),
                "last_modified": datetime.fromtimestamp(skill_md.stat().st_mtime).isoformat(),
            })
        except Exception as e:
            logger.warning("解析 SKILL.md 失败 %s: %s", skill_dir.name, e)

    return skills


def import_skill_to_memory(name: str, frontmatter: dict, body: str,
                           skills_dir: str = "", source_file: str = "") -> int:
    """将解析后的 SKILL.md 导入到 memories 表。
    返回 memory_id，已存在则更新。"""
    from . import database as db

    description = frontmatter.get("description", "")
    category = frontmatter.get("category", "")   # 元技能/管线/参考 分层标签
    layer = frontmatter.get("layer", "")         # 渐进载入层 L1/L2/L3
    mcp_required = frontmatter.get("mcp_required", [])
    if isinstance(mcp_required, list):
        mcp_str = ",".join(mcp_required)
    else:
        mcp_str = ""

    # 构建 content: 技能名称 + 分类/层级 + 触发场景 + 步骤
    content_parts = [f"技能：{name}"]
    if category:
        content_parts.append(f"分类：{category}")
    if layer:
        content_parts.append(f"层级：{layer}")
    if description:
        content_parts.append(f"触发：{description[:200]}")
    content_parts.append(f"步骤：{json.dumps([body[:300]], ensure_ascii=False)}")
    if mcp_str:
        content_parts.append(f"依赖MCP：{mcp_str}")
    content = "\n".join(content_parts)

    keywords = name
    if category:
        keywords = f"{name},{category}"
    if description:
        # 提取关键词
        kw = re.findall(r'[\u4e00-\u9fff]{2,4}', description)[:5]
        if kw:
            keywords = ",".join([keywords] + kw)

    # 检查是否已存在同名技能
    existing = db.mem_list(type_="skill")
    for m in existing:
        existing_name = ""
        c = m.get("content", "")
        if c.startswith("技能："):
            existing_name = c[3:].split("\n")[0].strip()
        if existing_name == name:
            # 更新
            with db.db() as c:
                c.execute("UPDATE memories SET content=?, keywords=?, importance=MAX(importance,3) WHERE id=?",
                          (content, keywords, m["id"]))
            logger.info("技能已更新: %s (id=%s)", name, m["id"])
            return m["id"]

    # 新建
    mem_id = db.mem_add(
        type_="skill",
        content=content,
        importance=3,
        keywords=keywords,
        source_conv_id=f"file:{source_file}" if source_file else "",
    )
    logger.info("技能已导入: %s (id=%s)", name, mem_id)
    return mem_id


def import_all_from_dir(skills_dir: str) -> dict:
    """扫描目录并批量导入所有 SKILL.md 到 memories 表"""
    scanned = scan_skills_dir(skills_dir)
    imported = []
    errors = []

    for skill in scanned:
        try:
            # 重新读取完整文件
            skill_md = Path(skill["directory"]) / "SKILL.md"
            with open(skill_md, "r", encoding="utf-8") as f:
                raw = f.read()
            metadata, body = _parse_frontmatter(raw)
            mem_id = import_skill_to_memory(
                name=skill["name"],
                frontmatter=metadata,
                body=body,
                skills_dir=skills_dir,
                source_file=str(skill_md),
            )
            imported.append({"name": skill["name"], "id": mem_id})
        except Exception as e:
            errors.append({"name": skill["name"], "error": str(e)})

    return {
        "scanned": len(scanned),
        "imported": len(imported),
        "errors": len(errors),
        "imported_list": imported,
        "error_list": errors,
    }
