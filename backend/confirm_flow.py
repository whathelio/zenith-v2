"""Zenith v2 确认流程 — AI 提议需用户确认/修改/忽略 + 分步教程模式"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from .database import sch_update, note_update, sch_list, note_list, db
from .timezone import now_tz

logger = logging.getLogger("zenith.confirm")


def get_pending_proposals() -> list[dict]:
    """获取所有待确认的提议"""
    results = []

    for s in sch_list(status="proposed"):
        results.append({
            "type": "schedule",
            "id": s["id"],
            "title": s["title"],
            "time": s.get("start_time", ""),
            "description": s.get("description", ""),
            "priority": s.get("priority", "normal"),
            "created_at": s["created_at"],
        })

    with db() as c:
        rs = c.execute(
            "SELECT * FROM notes WHERE status = 'proposed' ORDER BY created_at DESC"
        ).fetchall()
    for n in [dict(r) for r in rs]:
        results.append({
            "type": "note",
            "id": n["id"],
            "title": n["title"],
            "content": n.get("content", ""),
            "tags": n.get("tags", ""),
            "created_at": n["created_at"],
        })

    return results


def confirm_proposal(proposal_type: str, proposal_id: int) -> dict:
    """确认一个提议"""
    now = now_tz().isoformat()
    if proposal_type == "schedule":
        sch_update(proposal_id, {"status": "confirmed", "confirmed_at": now})
        return {"success": True, "message": f"日程 (ID:{proposal_id}) 已确认保存"}
    elif proposal_type == "note":
        note_update(proposal_id, {"status": "confirmed"})
        return {"success": True, "message": f"笔记 (ID:{proposal_id}) 已确认保存"}
    return {"success": False, "message": "未知类型"}


def reject_proposal(proposal_type: str, proposal_id: int) -> dict:
    """忽略一个提议"""
    if proposal_type == "schedule":
        sch_update(proposal_id, {"status": "cancelled"})
        return {"success": True, "message": f"日程提议 (ID:{proposal_id}) 已忽略"}
    elif proposal_type == "note":
        note_update(proposal_id, {"status": "cancelled"})
        return {"success": True, "message": f"笔记提议 (ID:{proposal_id}) 已忽略"}
    return {"success": False, "message": "未知类型"}


def modify_proposal(proposal_type: str, proposal_id: int, changes: dict) -> dict:
    """修改并确认一个提议"""
    now = now_tz().isoformat()
    if proposal_type == "schedule":
        allowed = {
            "title", "start_time", "end_time", "description", "location",
            "priority", "importance", "category", "impact", "country",
            "remind_before", "goal_id",
        }
        filtered = {k: v for k, v in changes.items() if k in allowed}
        filtered["status"] = "confirmed"
        filtered["confirmed_at"] = now
        sch_update(proposal_id, filtered)
        return {"success": True, "message": f"日程 (ID:{proposal_id}) 已修改并确认"}
    elif proposal_type == "note":
        allowed = {"title", "content", "tags"}
        filtered = {k: v for k, v in changes.items() if k in allowed}
        filtered["status"] = "confirmed"
        note_update(proposal_id, filtered)
        return {"success": True, "message": f"笔记 (ID:{proposal_id}) 已修改并确认"}
    return {"success": False, "message": "未知类型"}


# ═══════════════════════════════════════════════════════════════
# Pending Actions — AI 编辑操作需用户确认后执行
# ═══════════════════════════════════════════════════════════════
# AI 调用编辑类工具（delete_note / edit_note / edit_file）时，
# 不直接执行，而是生成一个待确认动作。用户确认后才真实执行，
# 用户忽略则丢弃。内存存储，重启后丢失（未确认的动作自动作废）。

import threading

_pending_actions: dict[int, dict] = {}
_action_seq = [1000]  # 从 1000 起，避免与 DB id 混淆
_action_lock = threading.Lock()


def create_action(action_type: str, title: str, payload: dict) -> dict:
    """创建一个待确认动作。返回动作 dict（含 action_id）。"""
    with _action_lock:
        _action_seq[0] += 1
        action_id = _action_seq[0]
    action = {
        "id": action_id,
        "type": action_type,
        "title": title,
        "payload": payload,
        "created_at": now_tz().isoformat(),
    }
    _pending_actions[action_id] = action
    logger.info("Pending action created: %s #%s (%s)", action_type, action_id, title)
    return action


def get_pending_actions() -> list[dict]:
    """列出所有待确认动作"""
    return list(_pending_actions.values())


def get_pending_proposals_merged() -> list[dict]:
    """合并 数据库 proposals + 内存 pending actions，供前端统一展示"""
    results = get_pending_proposals()
    for a in _pending_actions.values():
        results.append({
            "type": "action",
            "id": a["id"],
            "title": a["title"],
            "description": _action_desc(a),
            "created_at": a["created_at"],
        })
    return results


def _action_desc(action: dict) -> str:
    """生成动作的人类可读描述"""
    t = action["type"]
    p = action.get("payload", {})
    if t == "delete_note":
        nid = p.get("note_id")
        return f"删除笔记 #{nid}: {p.get('title', '')[:60]}"
    if t == "edit_note":
        nid = p.get("note_id")
        return f"修改笔记 #{nid}: {p.get('title', '')[:60]}"
    if t == "delete_memory":
        mid = p.get("memory_id")
        return f"删除记忆 #{mid}: {p.get('content', '')[:60]}"
    if t == "edit_memory":
        mid = p.get("memory_id")
        return f"修改记忆 #{mid}: {p.get('content', '')[:60]}"
    if t == "edit_file":
        return f"编辑文件: {p.get('path', '')[:80]}"
    return t


def confirm_action(action_id: int) -> dict:
    """用户确认 → 真实执行动作"""
    action = _pending_actions.pop(action_id, None)
    if not action:
        return {"success": False, "message": f"动作 #{action_id} 不存在或已过期（重启后未确认的动作会失效）"}
    try:
        result = _execute_action(action)
        return {"success": True, "message": f"动作已执行: {result}", "action": action}
    except Exception as e:
        logger.error("执行动作失败 #%s: %s", action_id, e, exc_info=True)
        # 失败时重新放回，允许重试
        _pending_actions[action_id] = action
        return {"success": False, "message": f"执行失败: {e}"}


def reject_action(action_id: int) -> dict:
    """用户忽略 → 丢弃动作"""
    action = _pending_actions.pop(action_id, None)
    if not action:
        return {"success": False, "message": f"动作 #{action_id} 不存在或已过期"}
    logger.info("Action rejected: %s #%s", action["type"], action_id)
    return {"success": True, "message": f"动作已忽略: {action['title']}"}


def _execute_action(action: dict) -> str:
    """真实执行动作（仅确认后调用）"""
    t = action["type"]
    p = action.get("payload", {})
    from . import database as db

    if t == "delete_note":
        nid = p.get("note_id")
        note = db.note_get(nid)
        if not note:
            raise ValueError(f"笔记 #{nid} 不存在")
        db.note_del(nid)
        return f"已删除笔记 #{nid}: {note.get('title', '')[:40]}"

    if t == "edit_note":
        nid = p.get("note_id")
        note = db.note_get(nid)
        if not note:
            raise ValueError(f"笔记 #{nid} 不存在")
        changes = {k: v for k, v in p.items() if k in ("title", "content", "tags") and v is not None}
        if not changes:
            raise ValueError("没有可应用的修改字段")
        db.note_update(nid, changes)
        return f"已修改笔记 #{nid}: {changes.get('title', note.get('title', ''))[:40]}"

    if t == "delete_memory":
        mid = p.get("memory_id")
        mem = db.mem_get(mid)
        if not mem:
            raise ValueError(f"记忆 #{mid} 不存在")
        db.mem_del(mid)
        return f"已删除记忆 #{mid}: {mem.get('content', '')[:40]}"

    if t == "edit_memory":
        mid = p.get("memory_id")
        mem = db.mem_get(mid)
        if not mem:
            raise ValueError(f"记忆 #{mid} 不存在")
        changes = {
            k: v for k, v in p.items()
            if k in ("content", "type", "importance", "keywords") and v is not None
        }
        if not changes:
            raise ValueError("没有可应用的修改字段")
        ok = db.mem_update(
            mid,
            content=changes.get("content", ""),
            type_=changes.get("type", ""),
            importance=int(changes.get("importance") or 0),
            keywords=changes.get("keywords", ""),
        )
        if not ok:
            raise ValueError("更新被守卫拒绝（内容含敏感信息）")
        return f"已修改记忆 #{mid}: {changes.get('content', mem.get('content', ''))[:40]}"

    if t == "edit_file":
        path = p.get("path")
        content = p.get("content")
        if not path or content is None:
            raise ValueError("edit_file 需要 path 和 content")
        import os as _os
        from pathlib import Path as _P
        # 安全边界：只允许编辑项目目录内的文本文件，且限制扩展名
        project_root = _P(__file__).parent.parent
        target = _P(path).expanduser().resolve()
        project_root_resolved = project_root.resolve()
        if not str(target).startswith(str(project_root_resolved)):
            raise ValueError(f"路径越界，禁止编辑项目目录外的文件: {path}")
        allowed_ext = {".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".yaml", ".yml", ".md", ".css", ".html", ".bat", ".txt"}
        if target.suffix.lower() not in allowed_ext:
            raise ValueError(f"不允许编辑该类型文件: {target.suffix}")
        if not target.exists():
            raise ValueError(f"文件不存在: {path}")
        # 备份
        backup_path = target.with_suffix(target.suffix + ".bak")
        if not backup_path.exists():
            backup_path.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        target.write_text(content, encoding="utf-8")
        return f"已编辑 {path}（备份: {backup_path.name}）"

    if t == "update_background":
        conv_id = p.get("conv_id")
        new_bg = p.get("new_background")
        if not conv_id or new_bg is None:
            raise ValueError("update_background 需要 conv_id 和 new_background")
        db.conv_update_background(conv_id, new_bg if new_bg.strip() else None)
        return f"已更新对话背景 ({new_bg[:50].replace(chr(10), ' ')}...)"

    if t == "delete_message":
        msg_id = p.get("msg_id")
        if not msg_id:
            raise ValueError("delete_message 需要 msg_id")
        msg = db.msg_get(msg_id)
        if not msg:
            raise ValueError(f"消息 #{msg_id} 不存在")
        db.msg_del_one(msg_id)
        role = "用户" if msg.get("role") == "user" else "AI"
        return f"已删除对话中的{role}消息 #{msg_id}"

    raise ValueError(f"未知动作类型: {t}")


# ═══════════════════════════════════════════════════════════════
# 分步教程模式 (Tutorial Flow)
# ═══════════════════════════════════════════════════════════════
# AI 生成多步骤计划后逐步释放，每步给出操作+验证对，
# 用户确认完成后自动进入下一步，失败可回退或重新规划。

# 内存中的教程会话（简单实现，重启后丢失）
_tutorial_sessions: dict[str, "TutorialFlow"] = {}


class TutorialFlow:
    """分步教程会话：一步一验证的交互模式

    使用方式:
        flow = TutorialFlow.create("install_mt5_indicator", [
            {"action": "打开MT5软件", "verify": "能看到黄金XAUUSD行情"},
            {"action": "按F4打开MetaEditor", "verify": "编辑器窗口已打开"},
        ])
        flow.current_step()  # 获取当前步骤
        flow.confirm_step()  # 确认完成，进入下一步
        flow.fail_step("找不到菜单")  # 标记失败，可回退
    """

    def __init__(self, session_id: str, title: str, steps: list[dict]):
        self.session_id = session_id
        self.title = title
        self.steps = steps
        self.current = 0
        self.status = "active"  # active / completed / failed
        self.history: list[dict] = []  # 记录每步执行情况
        self.created_at = now_tz().isoformat()

    @classmethod
    def create(cls, title: str, steps: list[dict]) -> "TutorialFlow":
        """创建新的教程会话"""
        session_id = f"tutorial_{now_tz().strftime('%Y%m%d%H%M%S')}"
        flow = cls(session_id, title, steps)
        _tutorial_sessions[session_id] = flow
        logger.info("TutorialFlow created: %s (%d steps)", session_id, len(steps))
        return flow

    @classmethod
    def get(cls, session_id: str) -> "TutorialFlow | None":
        """获取已有会话"""
        return _tutorial_sessions.get(session_id)

    def current_step(self) -> dict | None:
        """获取当前步骤（操作+验证）"""
        if self.current >= len(self.steps):
            return None
        step = self.steps[self.current]
        return {
            "session_id": self.session_id,
            "title": self.title,
            "step_index": self.current + 1,
            "total_steps": len(self.steps),
            "action": step.get("action", ""),
            "verify": step.get("verify", ""),
            "status": self.status,
        }

    def confirm_step(self) -> dict:
        """用户确认当前步骤完成，进入下一步"""
        if self.current >= len(self.steps):
            return {"success": False, "message": "所有步骤已完成"}

        step = self.steps[self.current]
        self.history.append({
            "step_index": self.current + 1,
            "action": step.get("action", ""),
            "result": "confirmed",
            "timestamp": now_tz().isoformat(),
        })
        self.current += 1

        if self.current >= len(self.steps):
            self.status = "completed"
            # 清理会话
            _tutorial_sessions.pop(self.session_id, None)
            return {
                "success": True,
                "message": f"教程 {self.title} 全部完成！共 {len(self.steps)} 步。",
                "completed": True,
            }

        next_step = self.current_step()
        return {
            "success": True,
            "message": f"步骤 {self.current}/{len(self.steps)} 已确认，进入下一步",
            "next_step": next_step,
        }

    def fail_step(self, reason: str = "") -> dict:
        """当前步骤失败，可回退或重新规划"""
        if self.current >= len(self.steps):
            return {"success": False, "message": "所有步骤已完成"}

        step = self.steps[self.current]
        self.history.append({
            "step_index": self.current + 1,
            "action": step.get("action", ""),
            "result": "failed",
            "reason": reason,
            "timestamp": now_tz().isoformat(),
        })
        return {
            "success": True,
            "message": f"步骤 {self.current + 1} 标记失败: {reason}",
            "failed_step": step,
            "suggestion": "可以重试当前步骤，或让 AI 重新规划后续步骤",
        }

    def retry_step(self) -> dict:
        """重试当前步骤（不清除进度）"""
        if self.current >= len(self.steps):
            return {"success": False, "message": "所有步骤已完成"}
        return {
            "success": True,
            "step": self.current_step(),
            "message": f"重试步骤 {self.current + 1}",
        }

    def skip_step(self) -> dict:
        """跳过当前步骤"""
        if self.current >= len(self.steps):
            return {"success": False, "message": "所有步骤已完成"}

        step = self.steps[self.current]
        self.history.append({
            "step_index": self.current + 1,
            "action": step.get("action", ""),
            "result": "skipped",
            "timestamp": now_tz().isoformat(),
        })
        self.current += 1

        if self.current >= len(self.steps):
            self.status = "completed"
            _tutorial_sessions.pop(self.session_id, None)
            return {"success": True, "message": "已跳过最后一步，教程完成", "completed": True}

        return {
            "success": True,
            "message": f"步骤 {self.current} 已跳过",
            "next_step": self.current_step(),
        }

    def to_dict(self) -> dict:
        """完整会话信息"""
        return {
            "session_id": self.session_id,
            "title": self.title,
            "total_steps": len(self.steps),
            "current_step": self.current + 1 if self.current < len(self.steps) else len(self.steps),
            "status": self.status,
            "steps": self.steps,
            "history": self.history,
            "created_at": self.created_at,
        }


def list_active_tutorials() -> list[dict]:
    """列出所有活跃的教程会话"""
    return [flow.to_dict() for flow in _tutorial_sessions.values() if flow.status == "active"]
