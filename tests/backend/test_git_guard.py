"""git_guard 测试 — 快照/回退/列表（2026-08-20 治理：zenith 自改代码+版本回退）"""
import pytest
from pathlib import Path


@pytest.fixture
def git_repo(tmp_path):
    """tmp_path 内初始化一个真实 git 仓库，返回 (repo_dir, backend_dir)。"""
    import subprocess
    repo = tmp_path / "zenith-test"
    (repo / "backend").mkdir(parents=True)
    (repo / "data").mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, text=True, check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "test"],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@local"],
                   capture_output=True, check=True)
    return repo


class TestGitGuardLive:
    """真实 git 集成测试（git 可用时执行）"""

    def test_commit_and_list(self, git_repo, monkeypatch):
        """真实 commit + list_snapshots 可见"""
        from backend import git_guard
        repo, _ = git_repo, git_repo / "backend"
        monkeypatch.setattr(git_guard, "PROJECT_ROOT", git_repo)
        monkeypatch.setattr(git_guard, "_is_testing", lambda: False)
        # 初始文件
        (git_repo / "backend" / "a.py").write_text("print(1)", encoding="utf-8")
        (git_repo / "data" / "zenith.db").write_text("db", encoding="utf-8")  # 应被忽略或不受影响
        r = git_guard.ensure_git_clean_snapshot("init commit")
        assert r["snapshot_created"] is True
        snaps = git_guard.list_snapshots(limit=5)
        assert len(snaps) >= 1
        assert snaps[0]["summary"].startswith("init commit")

    def test_no_change_no_commit(self, git_repo, monkeypatch):
        """无改动时不产生空提交"""
        from backend import git_guard
        monkeypatch.setattr(git_guard, "PROJECT_ROOT", git_repo)
        monkeypatch.setattr(git_guard, "_is_testing", lambda: False)
        (git_repo / "backend" / "a.py").write_text("print(1)", encoding="utf-8")
        assert git_guard.ensure_git_clean_snapshot("c1")["snapshot_created"] is True
        # 第二次：无改动
        assert git_guard.ensure_git_clean_snapshot("c2")["snapshot_created"] is False

    def test_rollback_restores_backend(self, git_repo, monkeypatch):
        """rollback 把 backend/ 恢复到旧版本，data/ 不受影响"""
        from backend import git_guard
        monkeypatch.setattr(git_guard, "PROJECT_ROOT", git_repo)
        monkeypatch.setattr(git_guard, "_is_testing", lambda: False)
        (git_repo / "backend" / "a.py").write_text("v1", encoding="utf-8")
        git_guard.ensure_git_clean_snapshot("v1")
        old = git_guard.list_snapshots(1)[0]["hash"]

        (git_repo / "backend" / "a.py").write_text("v2", encoding="utf-8")
        (git_repo / "data" / "keep.txt").write_text("keep", encoding="utf-8")
        git_guard.ensure_git_clean_snapshot("v2")

        r = git_guard.rollback_to_commit(old)
        assert r["success"] is True
        assert (git_repo / "backend" / "a.py").read_text(encoding="utf-8") == "v1"
        assert (git_repo / "data" / "keep.txt").read_text(encoding="utf-8") == "keep"

    def test_rollback_rejects_bad_hash(self, git_repo, monkeypatch):
        from backend import git_guard
        monkeypatch.setattr(git_guard, "PROJECT_ROOT", git_repo)
        monkeypatch.setattr(git_guard, "_is_testing", lambda: False)
        assert git_guard.rollback_to_commit("xyz!")["success"] is False
        assert git_guard.rollback_to_commit("ZZZZZZZ")["success"] is False


class TestGitGuardShortCircuit:
    """测试模式短路（ZENITH_TESTING=1 时不碰真实仓库）"""

    def test_testing_shortcut(self, monkeypatch):
        from backend import git_guard
        monkeypatch.setattr(git_guard, "_is_testing", lambda: True)
        assert git_guard.ensure_git_clean_snapshot("x")["snapshot_created"] is False
        assert git_guard.rollback_to_commit("abc1234")["success"] is False
        assert git_guard.list_snapshots() == []


class TestConfirmFlowGitTools:
    """确认流：create_snapshot / rollback_code 的 desc 与执行分支"""

    def test_action_desc(self):
        from backend.confirm_flow import _action_desc
        a = {"type": "rollback_code", "payload": {"hash": "abc1234abcd", "label": "x"}}
        assert "高风险" in _action_desc(a)
        a2 = {"type": "create_snapshot", "payload": {"label": "优化前"}}
        assert "代码快照" in _action_desc(a2)

    def test_create_snapshot_tool_flow(self, monkeypatch):
        """测试模式：create_snapshot 生成待确认动作，确认执行（git_guard 桩为成功）"""
        import asyncio
        from backend import tools as tools_mod
        from backend import git_guard
        from backend.confirm_flow import confirm_action
        # confirm_flow 内 `from .git_guard import ensure_git_clean_snapshot` 局部导入 → patch 源模块
        monkeypatch.setattr(git_guard, "ensure_git_clean_snapshot",
                            lambda reason, paths=None: {"snapshot_created": True, "error": ""})
        monkeypatch.setattr(git_guard, "_is_git_repo", lambda: True)
        async def run():
            r = await tools_mod.execute_tool("create_snapshot", {"label": "test"}, conv_id="c")
            assert r.get("confirm") is True
            assert r.get("action", {}).get("type") == "create_snapshot"
            res = confirm_action(r["action"]["id"])
            assert res["success"] is True
            assert "代码快照" in res["message"]
        asyncio.run(run())

    def test_rollback_tool_bad_hash(self, monkeypatch):
        import asyncio
        from backend import tools as tools_mod
        async def run():
            r = await tools_mod.execute_tool("rollback_code", {"snapshot_hash": "bad!"}, conv_id="c")
            assert r["success"] is False
            assert "非法" in r["result"]
        asyncio.run(run())
