"""确认流状态机测试（`backend/confirm_flow.py`，583 行）。

为什么需要本文件 —— 该模块此前**实质零覆盖**：`grep confirm_flow tests/` 仅 3 处命中，
全部在 `test_git_guard.py` 里借用 `_action_desc` / `confirm_action` 测 git_guard，
**状态机本身（提案 4 态 + 动作流 + Tutorial 6 分支）完全裸奔**。

它是 Zenith 的核心安全闸门：AI 想删笔记 / 改文件 / 回退代码，都不能直接执行 ——
必须先落成「待确认」，由用户点确认才真跑（`_execute_action` 仅在 `confirm_action` 内调用）。

⚠️ 两处易被误读的语义（本文件用测试固化）：
1. **提案复用业务表的 status 字段**（`schedules.status='proposed'`），不是独立提案表。
2. **`modify_proposal` 有白名单**（schedule 11 个字段 / note 3 个字段）——
   这是安全边界：防止用户经由「修改并确认」绕过状态机（如直接传 `status='cancelled'`）。
"""
import pytest

from backend import confirm_flow


# ────────────────────── 测试夹具：隔离 DB 与内存态 ──────────────────────

@pytest.fixture(autouse=True)
def _isolate_inmemory_state():
    """清空模块级内存态，保证每个用例互不干扰。

    `_pending_actions` / `_tutorial_sessions` 都是模块级 dict（进程内，重启即丢）。
    `_action_seq` 是 list 计数器 —— 一并归位，避免断言依赖执行顺序。
    """
    confirm_flow._pending_actions.clear()
    confirm_flow._tutorial_sessions.clear()
    confirm_flow._action_seq[0] = 1000
    yield
    confirm_flow._pending_actions.clear()
    confirm_flow._tutorial_sessions.clear()


@pytest.fixture
def clean_db(test_db):
    """conftest 的 test_db 只清 memories/notes；schedules 会跨用例残留，这里补上。"""
    test_db.execute("DELETE FROM schedules")
    test_db.execute("DELETE FROM notes")
    test_db.commit()
    return test_db


def _insert_proposed_schedule(conn, title="待确认日程"):
    cur = conn.execute(
        "INSERT INTO schedules (title, start_time, status, priority, created_at) "
        "VALUES (?, '2026-10-05 09:00', 'proposed', 'normal', datetime('now'))",
        (title,),
    )
    conn.commit()
    return cur.lastrowid


def _insert_proposed_note(conn, title="待确认笔记", stage="raw"):
    cur = conn.execute(
        "INSERT INTO notes (title, content, stage, status, created_at) "
        "VALUES (?, '原始内容', ?, 'proposed', datetime('now'))",
        (title, stage),
    )
    conn.commit()
    return cur.lastrowid


def _fetch(conn, table, row_id, cols):
    cur = conn.execute(f"SELECT {', '.join(cols)} FROM {table} WHERE id = ?", (row_id,))
    row = cur.fetchone()
    return dict(zip(cols, row)) if row else None


# ────────────────────── 提案流（DB 支撑）──────────────────────

class TestPendingProposals:
    def test_empty_db_returns_empty_list(self, clean_db):
        assert confirm_flow.get_pending_proposals() == []

    def test_proposed_schedule_is_listed(self, clean_db):
        sid = _insert_proposed_schedule(clean_db, "待确认日程")
        items = confirm_flow.get_pending_proposals()
        assert len(items) == 1
        assert items[0]["type"] == "schedule"
        assert items[0]["id"] == sid
        assert items[0]["title"] == "待确认日程"

    def test_proposed_note_is_listed(self, clean_db):
        nid = _insert_proposed_note(clean_db, "待确认笔记")
        items = confirm_flow.get_pending_proposals()
        assert any(i["type"] == "note" and i["id"] == nid for i in items)

    def test_confirmed_schedule_not_listed(self, clean_db):
        """只有 status='proposed' 的才进待确认列表。"""
        clean_db.execute(
            "INSERT INTO schedules (title, start_time, status, priority, created_at) "
            "VALUES ('已确认', '2026-10-05 09:00', 'confirmed', 'normal', datetime('now'))"
        )
        clean_db.commit()
        assert confirm_flow.get_pending_proposals() == []


class TestConfirmRejectProposal:
    def test_confirm_schedule_marks_confirmed_with_timestamp(self, clean_db):
        sid = _insert_proposed_schedule(clean_db)
        res = confirm_flow.confirm_proposal("schedule", sid)
        assert res["success"] is True
        row = _fetch(clean_db, "schedules", sid, ["status", "confirmed_at"])
        assert row["status"] == "confirmed"
        assert row["confirmed_at"], "确认时必须落确认时点（可追溯性）"

    def test_confirm_note_marks_confirmed(self, clean_db):
        nid = _insert_proposed_note(clean_db)
        assert confirm_flow.confirm_proposal("note", nid)["success"] is True
        assert _fetch(clean_db, "notes", nid, ["status"])["status"] == "confirmed"

    def test_reject_schedule_marks_cancelled(self, clean_db):
        sid = _insert_proposed_schedule(clean_db)
        assert confirm_flow.reject_proposal("schedule", sid)["success"] is True
        assert _fetch(clean_db, "schedules", sid, ["status"])["status"] == "cancelled"

    def test_reject_note_marks_cancelled(self, clean_db):
        nid = _insert_proposed_note(clean_db)
        assert confirm_flow.reject_proposal("note", nid)["success"] is True
        assert _fetch(clean_db, "notes", nid, ["status"])["status"] == "cancelled"

    @pytest.mark.parametrize("fn_name", ["confirm_proposal", "reject_proposal"])
    def test_unknown_type_returns_failure(self, fn_name):
        res = getattr(confirm_flow, fn_name)("bogus_type", 1)
        assert res["success"] is False
        assert "未知类型" in res["message"]

    def test_rejected_proposal_leaves_pending_list(self, clean_db):
        sid = _insert_proposed_schedule(clean_db)
        confirm_flow.reject_proposal("schedule", sid)
        assert confirm_flow.get_pending_proposals() == []


class TestModifyProposalWhitelist:
    """🔴 `modify_proposal` 的白名单是安全边界 —— 防止绕过状态机。"""

    def test_schedule_whitelist_blocks_status_injection(self, clean_db):
        """传非白名单的 `status` 不生效，最终仍被强制为 confirmed。"""
        sid = _insert_proposed_schedule(clean_db, "原标题")
        res = confirm_flow.modify_proposal("schedule", sid, {
            "title": "改后标题",
            "status": "cancelled",          # 白名单外 → 丢弃
            "created_at": "1999-01-01",     # 白名单外 → 丢弃
            "injected_field": "x",          # 白名单外 → 丢弃
        })
        assert res["success"] is True
        row = _fetch(clean_db, "schedules", sid, ["title", "status", "created_at"])
        assert row["title"] == "改后标题"
        assert row["status"] == "confirmed", "status 必须被强制为 confirmed，不能被注入"
        assert row["created_at"] != "1999-01-01", "created_at 不应被外部改写"

    def test_note_whitelist_blocks_stage_injection(self, clean_db):
        nid = _insert_proposed_note(clean_db, "原笔记", stage="raw")
        res = confirm_flow.modify_proposal("note", nid, {
            "content": "改后内容",
            "stage": "distilled",   # 白名单外 → 丢弃
        })
        assert res["success"] is True
        row = _fetch(clean_db, "notes", nid, ["content", "stage", "status"])
        assert row["content"] == "改后内容"
        assert row["stage"] == "raw", "stage 不在白名单，不应被改"
        assert row["status"] == "confirmed"

    def test_modify_sets_confirmed_at(self, clean_db):
        nid = _insert_proposed_note(clean_db)
        confirm_flow.modify_proposal("note", nid, {"content": "新内容"})
        assert _fetch(clean_db, "notes", nid, ["confirmed_at"])["confirmed_at"]

    def test_modify_unknown_type_fails(self):
        res = confirm_flow.modify_proposal("bogus", 1, {})
        assert res["success"] is False

    def test_modify_schedule_rejected_by_secret_guard(self, clean_db):
        """🔴 含明文密钥的修改必须被落库守卫拦下，回「未更新」而非谎报成功。

        源码 `:82-91` 的判负补齐（2026-09-16）：`sch_update` 命中守卫会返回 False，
        不判负就会对「什么都没写」回「已修改并确认」—— 把拒绝谎报成成功。
        """
        sid = _insert_proposed_schedule(clean_db)
        res = confirm_flow.modify_proposal("schedule", sid, {
            "title": "正常标题",
            "description": "我的 key 是 sk-" + "a" * 48,
        })
        assert res["success"] is False, "守卫命中必须判负"
        assert "未更新" in res["message"]

    def test_modify_note_rejected_by_secret_guard(self, clean_db):
        nid = _insert_proposed_note(clean_db)
        res = confirm_flow.modify_proposal("note", nid, {
            "content": "我的 key 是 sk-" + "a" * 48,
        })
        assert res["success"] is False
        assert "未更新" in res["message"]


# ────────────────────── 动作流（内存态）──────────────────────

class TestPendingActions:
    def test_create_registers_and_returns_id_above_1000(self):
        a = confirm_flow.create_action("delete_note", "删除笔记", {"note_id": 5})
        assert a["id"] > 1000, "id 从 1000 起，避免与 DB id 混淆"
        assert confirm_flow.get_pending_actions() == [a]

    def test_ids_are_monotonic(self):
        a1 = confirm_flow.create_action("delete_note", "t1", {})
        a2 = confirm_flow.create_action("delete_note", "t2", {})
        assert a2["id"] > a1["id"]

    def test_action_carries_created_at(self):
        a = confirm_flow.create_action("edit_file", "编辑", {"path": "x"})
        assert a["created_at"]

    def test_confirm_unknown_action_fails(self):
        res = confirm_flow.confirm_action(999999)
        assert res["success"] is False
        assert "不存在或已过期" in res["message"]

    def test_reject_removes_action(self):
        a = confirm_flow.create_action("delete_note", "t", {})
        assert confirm_flow.reject_action(a["id"])["success"] is True
        assert confirm_flow.get_pending_actions() == []

    def test_reject_unknown_action_fails(self):
        assert confirm_flow.reject_action(999999)["success"] is False

    def test_merged_view_includes_actions(self, clean_db):
        a = confirm_flow.create_action("delete_note", "待办", {})
        merged = confirm_flow.get_pending_proposals_merged()
        assert any(m["type"] == "action" and m["id"] == a["id"] for m in merged)

    def test_merged_view_combines_proposals_and_actions(self, clean_db):
        _insert_proposed_schedule(clean_db)
        confirm_flow.create_action("delete_note", "待办", {})
        merged = confirm_flow.get_pending_proposals_merged()
        types = {m["type"] for m in merged}
        assert "schedule" in types and "action" in types

    @pytest.mark.parametrize("atype,payload,expect_prefix", [
        ("delete_note", {"note_id": 5, "title": "会议记录"}, "删除笔记 #5"),
        ("edit_note", {"note_id": 7, "title": "草稿"}, "修改笔记 #7"),
        ("delete_memory", {"memory_id": 3, "content": "旧记忆"}, "删除记忆 #3"),
        ("edit_memory", {"memory_id": 4, "content": "记忆"}, "修改记忆 #4"),
        ("edit_file", {"path": "backend/app.py"}, "编辑文件: backend/app.py"),
        ("create_snapshot", {"label": "改前快照"}, "创建代码快照"),
    ])
    def test_action_desc_formats(self, atype, payload, expect_prefix):
        desc = confirm_flow._action_desc({"type": atype, "payload": payload})
        assert desc.startswith(expect_prefix)

    def test_action_desc_rollback_is_marked_high_risk(self):
        desc = confirm_flow._action_desc({
            "type": "rollback_code", "payload": {"hash": "abcdef1234567890"},
        })
        assert "高风险" in desc, "回退代码必须在描述里显式标风险"

    def test_action_desc_unknown_type_returns_raw_type(self):
        assert confirm_flow._action_desc({"type": "mystery", "payload": {}}) == "mystery"

    def test_action_desc_merge_notes_warns_when_originals_deleted(self):
        desc = confirm_flow._action_desc({
            "type": "merge_notes",
            "payload": {"note_ids": [1, 2], "new_title": "合并稿", "keep_originals": False},
        })
        assert "原稿将删除" in desc


# ────────────────────── TutorialFlow 状态机 ──────────────────────

class TestTutorialFlow:
    def _flow(self, n_steps=2):
        steps = [{"action": f"动作{i}", "verify": f"验证{i}"} for i in range(1, n_steps + 1)]
        return confirm_flow.TutorialFlow.create("测试教程", steps)

    def test_create_registers_session(self):
        flow = self._flow()
        assert flow.session_id.startswith("tutorial_")
        assert confirm_flow.TutorialFlow.get(flow.session_id) is flow

    def test_get_unknown_session_returns_none(self):
        assert confirm_flow.TutorialFlow.get("tutorial_nope") is None

    def test_initial_state(self):
        flow = self._flow()
        assert flow.current == 0
        assert flow.status == "active"
        assert flow.history == []

    def test_current_step_shape(self):
        step = self._flow().current_step()
        assert step["step_index"] == 1
        assert step["total_steps"] == 2
        assert step["action"] == "动作1"
        assert step["verify"] == "验证1"
        assert step["status"] == "active"

    def test_confirm_step_advances_and_records_history(self):
        flow = self._flow()
        res = flow.confirm_step()
        assert res["success"] is True
        assert res["next_step"]["step_index"] == 2
        assert flow.current == 1
        assert flow.history[-1]["result"] == "confirmed"

    def test_confirm_last_step_completes_and_unregisters(self):
        flow = self._flow(n_steps=1)
        res = flow.confirm_step()
        assert res["completed"] is True
        assert flow.status == "completed"
        assert confirm_flow.TutorialFlow.get(flow.session_id) is None, "完成后应移出注册表"

    def test_confirm_after_completion_fails(self):
        flow = self._flow(n_steps=1)
        flow.confirm_step()
        assert flow.confirm_step()["success"] is False

    def test_fail_step_records_reason_without_advancing(self):
        flow = self._flow()
        res = flow.fail_step("找不到菜单")
        assert res["success"] is True
        assert flow.current == 0, "失败不应推进"
        assert flow.history[-1]["result"] == "failed"
        assert flow.history[-1]["reason"] == "找不到菜单"
        assert "可以重试" in res["suggestion"]

    def test_fail_step_does_not_change_status(self):
        flow = self._flow()
        flow.fail_step("x")
        assert flow.status == "active"

    def test_retry_step_does_not_advance_or_record(self):
        flow = self._flow()
        res = flow.retry_step()
        assert res["step"]["step_index"] == 1
        assert flow.current == 0
        assert flow.history == [], "重试不应写历史"

    def test_skip_step_advances_with_skipped_result(self):
        flow = self._flow()
        res = flow.skip_step()
        assert res["next_step"]["step_index"] == 2
        assert flow.history[-1]["result"] == "skipped"

    def test_skip_last_step_completes(self):
        flow = self._flow(n_steps=1)
        assert flow.skip_step()["completed"] is True
        assert flow.status == "completed"

    def test_operations_after_completion_fail(self):
        flow = self._flow(n_steps=1)
        flow.confirm_step()
        assert flow.current_step() is None
        assert flow.fail_step("x")["success"] is False
        assert flow.retry_step()["success"] is False
        assert flow.skip_step()["success"] is False

    def test_to_dict_shape(self):
        flow = self._flow()
        d = flow.to_dict()
        assert d["title"] == "测试教程"
        assert d["total_steps"] == 2
        assert d["current_step"] == 1
        assert d["status"] == "active"
        assert len(d["steps"]) == 2
        assert d["created_at"]

    def test_to_dict_current_step_clamps_at_end(self):
        """全部完成后 current_step 不应超出 total（源码用 else len(steps)）。"""
        flow = self._flow(n_steps=1)
        flow.confirm_step()
        assert flow.to_dict()["current_step"] == 1

    def test_list_active_excludes_completed(self):
        """已完成（自动移出注册表）的教程不出现在活跃列表。"""
        flow = self._flow(n_steps=1)
        assert len(confirm_flow.list_active_tutorials()) == 1
        flow.confirm_step()  # 完成 → confirm_step 内部已 pop
        assert confirm_flow.list_active_tutorials() == []

    def test_list_active_includes_multiple_distinct_sessions(self):
        """多个并存会话都应被列出（用注入式建会话绕开秒级 id 碰撞）。"""
        a = confirm_flow.TutorialFlow("tutorial_a", "甲", [{"action": "x"}])
        b = confirm_flow.TutorialFlow("tutorial_b", "乙", [{"action": "y"}])
        confirm_flow._tutorial_sessions.update({"tutorial_a": a, "tutorial_b": b})
        ids = {t["session_id"] for t in confirm_flow.list_active_tutorials()}
        assert ids == {"tutorial_a", "tutorial_b"}

    def test_create_same_second_collides_known_limitation(self, monkeypatch):
        """⚠️ 已知局限（非 bug，但**不要**假设 create 总能拿到唯一 id）。

        `create` 的 session_id 为 `tutorial_%Y%m%d%H%M%S`（**秒级**），
        同一秒内创建两个教程 → id 相同 → 后者**静默覆盖**前者。
        实践中教程由用户逐个手动触发、前端一次只跑一个，故未修；
        本测试固化事实，避免误以为 id 唯一。（时间已打桩，断言是确定性的。）
        """
        import datetime as _dt
        frozen = _dt.datetime(2026, 10, 4, 12, 0, 0)
        monkeypatch.setattr(confirm_flow, "now_tz", lambda: frozen)

        a = confirm_flow.TutorialFlow.create("甲", [{"action": "x"}])
        b = confirm_flow.TutorialFlow.create("乙", [{"action": "y"}])
        assert a.session_id == b.session_id, "秒级时间戳 → 同秒创建必然碰撞"
        assert confirm_flow.TutorialFlow.get(a.session_id) is b, "后者覆盖前者"
        assert len(confirm_flow.list_active_tutorials()) == 1, "碰撞后只剩一个会话"

    def test_empty_steps_flow_is_immediately_exhausted(self):
        flow = confirm_flow.TutorialFlow.create("空教程", [])
        assert flow.current_step() is None
        assert flow.confirm_step()["success"] is False
