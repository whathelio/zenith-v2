"""Skill Loader — 仿 WorkBuddy 目录扫描 SKILL.md 文件加载技能"""
import re
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("zenith.skill_loader")

# 技能导入路径：被 mem_add 的**内容相似度去重**拦截（返回 -2）时，是否以
# `dedup=False` 重试一次（D9，2026-09-28）。
#
# 为什么要绕：mem_add 的去重判定是拿**全库记忆**（不限 type）做候选池、阈值 0.75，
# 目标是抑制「对话蒸馏出的记忆」膨胀。而目录技能是**磁盘的确定性镜像**：
#   - 技能之间同名重复，已由 import_skill_to_memory 的 name-upsert 拦掉；
#   - 技能与某条对话记忆"像"，不构成"该技能是重复的"证据 —— 属误伤。
# 被误拦的后果极其隐蔽：技能没写进库，但 import_all_from_dir 仍把它计入 imported，
# 且**每次启动重试都会被同样拦掉** → 永久静默丢失。
#
# 回滚：改为 False 即恢复「被拦即丢」的旧行为（其余分类/告警逻辑仍生效）。
SKILL_IMPORT_RETRY_ON_DEDUP = True


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
    """将解析后的 SKILL.md 导入到 memories 表（upsert：按 name 命中则 UPDATE，否则 INSERT）。

    **返回值契约**（与 `database.mem_add` 对齐，2026-09-28 D9 显性化）：
      - `> 0`  : 写入成功，值为 memory_id（新增或更新）
      - `-1`   : 内容含明文密钥，被 `mem_add` 的密钥守卫拒绝，**未写入**
      - `-2`   : 被 `mem_add` 的内容相似度去重拦截，**未写入**
                 （若 `SKILL_IMPORT_RETRY_ON_DEDUP` 为 True，会先以 dedup=False 重试，
                 重试成功则返回真实 id，仍失败/关闭开关时才把 -2 透出）
    调用方**必须判负**，否则会把"写入失败"当成功 —— 见 routers/modules.py 与
    import_all_from_dir 的处理。
    """
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
    def _insert(dedup: bool) -> int:
        return db.mem_add(
            type_="skill",
            content=content,
            importance=3,
            keywords=keywords,
            source_conv_id=f"file:{source_file}" if source_file else "",
            dedup=dedup,
        )

    mem_id = _insert(True)
    if mem_id == -2 and SKILL_IMPORT_RETRY_ON_DEDUP:
        # 去重门禁面向「对话记忆」，对目录技能属误伤 → 按磁盘权威重试一次
        # （见模块顶部 SKILL_IMPORT_RETRY_ON_DEDUP 的完整依据）
        logger.warning("技能被内容去重拦截，按目录权威重试写入: %s", name)
        mem_id = _insert(False)

    if mem_id < 0:
        logger.warning("技能写入失败（未落库）: %s (code=%s)", name, mem_id)
    else:
        logger.info("技能已导入: %s (id=%s)", name, mem_id)
    return mem_id


def import_all_from_dir(skills_dir: str) -> dict:
    """扫描目录并批量导入所有 SKILL.md 到 memories 表。

    返回口径（2026-09-28 D9 修正）：
      - `imported` : **真正落库**的条数（新增 + 更新，即 mem_id > 0）。
                     修正前它把被守卫拒绝/去重拦截的条目也算作成功 —— 表现为
                     「日志说导入 36 个，库里其实少几个」且每次启动重复丢同一批。
      - `skipped`  : 被内容去重拦截、重试后仍未落库（mem_id == -2）
      - `rejected` : 被明文密钥守卫拒绝（mem_id == -1）
      - `errors`   : 解析/读写异常，或 mem_add 返回了预期外的码
    新增的三个键是**追加**的：`scanned/imported/errors/imported_list/error_list`
    保持原语义与原名，旧调用方不受影响。
    """
    scanned = scan_skills_dir(skills_dir)
    imported: list = []
    skipped: list = []
    rejected: list = []
    errors: list = []

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

            if mem_id > 0:
                imported.append({"name": skill["name"], "id": mem_id})
            elif mem_id == -2:
                skipped.append({"name": skill["name"], "id": mem_id, "reason": "dedup"})
                logger.warning("技能未落库（内容去重拦截）: %s", skill["name"])
            elif mem_id == -1:
                rejected.append({"name": skill["name"], "id": mem_id, "reason": "secret_guard"})
                logger.warning("技能未落库（明文密钥守卫拒绝）: %s", skill["name"])
            else:
                errors.append({"name": skill["name"],
                               "error": f"mem_add 返回预期外的码 {mem_id}"})
        except Exception as e:
            errors.append({"name": skill["name"], "error": str(e)})

    return {
        "scanned": len(scanned),
        "imported": len(imported),
        "skipped": len(skipped),
        "rejected": len(rejected),
        "errors": len(errors),
        "imported_list": imported,
        "skipped_list": skipped,
        "rejected_list": rejected,
        "error_list": errors,
    }
