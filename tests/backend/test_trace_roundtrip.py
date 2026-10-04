"""conversation_traces 的 tool_call 痕迹必须能完整往返（含代码执行的结构化字段）。

覆盖 2026-09-11 修复 **B-13②**：
`chat.py` 的 `tool_call_end` 事件本就带 `stdout/stderr/exit_code/lang`（:298-301），
但落库 `trace_add` 时**漏了这 4 个字段**；而前端**重载历史痕迹时正是读它们**
（`ChatView.tsx:125-128`）→ 表现为「刷新页面后代码体丢失」。

本测试锁住这条往返链路的 DB 侧（`trace_add` → `trace_list`）。
至于「`chat.py` 构造 data 时是否带这 4 个键」，由 `tools/audit/gov_final.py` 的静态断言守。
"""
import json

from backend import database as db

_CODE_PAYLOAD = {
    "name": "execute_code",
    "args": {"code": "print(1)"},
    "result_summary": "1",
    "success": True,
    "duration_ms": 42,
    "stdout": "1\n",
    "stderr": "",
    "exit_code": 0,
    "lang": "python",
}


def test_tool_call_痕迹保留代码执行字段(test_db):
    """★ 本次修复的核心：这 4 个字段必须能存能读。"""
    db.trace_add("conv-b13", "tool_call", data=_CODE_PAYLOAD)
    rows = db.trace_list(conv_id="conv-b13", trace_type="tool_call")
    assert rows, "痕迹未写入"

    data = json.loads(rows[0]["data"])
    for k in ("stdout", "stderr", "exit_code", "lang"):
        assert k in data, f"落库后缺字段 {k}"
    assert data["stdout"] == "1\n"
    assert data["exit_code"] == 0
    assert data["lang"] == "python"


def test_非代码工具的字段可为_None(test_db):
    """普通工具没有 stdout 等字段——存 None 不应报错，也不该被伪造成空串。"""
    db.trace_add("conv-b13b", "tool_call", data={
        "name": "list_notes", "success": True,
        "stdout": None, "stderr": None, "exit_code": None, "lang": None,
    })
    rows = db.trace_list(conv_id="conv-b13b", trace_type="tool_call")
    data = json.loads(rows[0]["data"])
    assert data["stdout"] is None and data["lang"] is None


def test_痕迹按会话隔离(test_db):
    """trace_list 必须按 conv_id 过滤 —— 前端按会话回读，串了就显示别人的工具气泡。"""
    db.trace_add("conv-A", "tool_call", data={"name": "t1"})
    db.trace_add("conv-B", "tool_call", data={"name": "t2"})
    a = [json.loads(r["data"])["name"] for r in db.trace_list(conv_id="conv-A")]
    assert a == ["t1"]
