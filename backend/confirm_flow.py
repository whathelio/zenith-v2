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
