"""Re-apply Phase 1-3 changes to app.py (lost by git checkout)"""
from pathlib import Path
app_path = Path(__file__).parent.parent / 'app.py'

with open(app_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. _auto_distill_conv
old = '''async def _auto_distill_conv(conv_id: str):
    """后台自动蒸馏对话内容：提取经验/决策/知识 → 存入记忆库"""
    import logging
    logger = logging.getLogger("zenith.distill")
    try:
        result = await distill_conversation(conv_id)
        saved = result.get("saved_count", 0)
        if saved > 0:
            logger.info("自动蒸馏完成: 对话%s, 已保存%d条记忆", conv_id, saved)
        else:
            logger.debug("自动蒸馏完成: 对话%s, 无新记忆提取", conv_id)
    except Exception as e:
        logging.getLogger("zenith.distill").warning("自动蒸馏失败: %s", e)'''

new = '''async def _auto_distill_conv(conv_id: str):
    """后台自动提取对话记忆（共用 _do_extract 内核，与 periodic 同路径）"""
    import logging
    logger = logging.getLogger("zenith.distill")
    try:
        msgs = db.msg_list(conv_id)
        text = "\\n".join(m.get("content", "") for m in msgs if m.get("role") in ("user", "assistant"))
        if not text.strip():
            return
        result = await extract_memories_from_text(text, conv_id)
        new_count = result.get("new", 0)
        if new_count > 0:
            logger.info("对话结束记忆提取: conv=%s, 新增%d条", conv_id, new_count)
    except Exception as e:
        logging.getLogger("zenith.distill").warning("对话结束记忆提取失败: %s", e)'''

if old in content:
    content = content.replace(old, new)
    print("1. _auto_distill_conv: replaced")
else:
    print("1. _auto_distill_conv: NOT FOUND")

# 2. _build_skill_injection
idx = content.find('def _build_skill_injection')
if idx > 0:
    end = content.find('\n\n@app.', idx + 10)
    new_func = '''def _build_skill_injection(current_query: str) -> str:
    """从记忆库检索 type='skill' 匹配当前查询，注入 system prompt"""
    if not current_query or len(current_query.strip()) < 2:
        return ""
    try:
        results = db.mem_search(current_query.strip()[:30], limit=5)
        skill_mems = [m for m in results if m.get("type") == "skill"]
        if not skill_mems:
            return ""
        parts = ["【已记录技能参考】"]
        for m in skill_mems[:3]:
            c = m.get("content", "")
            parts.append(f"- {c[:300]}")
        return "\\n".join(parts).strip()
    except Exception:
        return ""'''
    content = content[:idx] + new_func + content[end:]
    print("2. _build_skill_injection: replaced")

# 3. Delete Skills API
idx = content.find('# Skills API\n')
if idx > 0:
    end = content.find('@app.post("/api/skills/{sid}/improve")', idx)
    if end > 0:
        end2 = content.find('\n\n', end + 60)
        content = content[:idx] + '\n' + content[end2:]
        print("3. Skills API: deleted")

# 4. Host 127.0.0.1
content = content.replace('host="0.0.0.0"', 'host="127.0.0.1"')
print("4. Host: 127.0.0.1")

with open(app_path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Done.")
