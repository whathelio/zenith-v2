"""P0-1 回归：消息删除的作用域必须限定在**同一会话内**。

历史故障（2026-09-11 实测）：`messages.id` 是**全局自增、跨会话连续**的，
删除时若只按 `id >= ?` 比较而不限定 `conversation_id`，就会连带删掉
**其它会话里所有更大的 id** —— 实测一次 edit 误删 **647 条**、波及 **104 个会话**。

本测试刻意**交错插入**两个会话的消息，使「其它会话的更大 id」真实存在；
若实现回退成不限定会话，下面的断言会立刻失败。
"""
from backend import database as db


def test_删除不再跨会话误删(test_db):
    """★ 核心回归：A 会话的删除绝不能波及 B 会话。"""
    a = db.conv_create("会话A")["id"]
    b = db.conv_create("会话B")["id"]

    # 交错：A1 B1 A2 B2 —— 于是 B 的两条 id 都大于 A1
    a1 = db.msg_add(a, "user", "A1")
    db.msg_add(b, "user", "B1")
    db.msg_add(a, "assistant", "A2")
    db.msg_add(b, "assistant", "B2")

    n = db.msg_del_from(a1)
    assert n == 2, f"应只删 A 会话内 id>=a1 的 2 条，实删 {n} 条"

    assert [m["content"] for m in db.msg_list(a)] == [], "A 会话应被清空"
    # ★ 这两条的 id 更大，旧实现会把它们一起删掉
    assert [m["content"] for m in db.msg_list(b)] == ["B1", "B2"], \
        "跨会话误删：B 会话的消息被连带删除"


def test_从中间删除只影响其后消息(test_db):
    a = db.conv_create("A")["id"]
    ids = [db.msg_add(a, "user", f"m{i}") for i in range(5)]

    n = db.msg_del_from(ids[2])
    assert n == 3, f"ids[2] 及其后共 3 条，实删 {n}"
    assert [m["content"] for m in db.msg_list(a)] == ["m0", "m1"]


def test_不存在的_msg_id_不删任何东西(test_db):
    """msg_id 不存在时必须返回 0 —— 不能退化成「删掉所有更大的 id」。"""
    a = db.conv_create("A")["id"]
    b = db.conv_create("B")["id"]
    db.msg_add(a, "user", "a-keep")
    db.msg_add(b, "user", "b-keep")

    assert db.msg_del_from(10 ** 9) == 0
    assert [m["content"] for m in db.msg_list(a)] == ["a-keep"]
    assert [m["content"] for m in db.msg_list(b)] == ["b-keep"]


def test_单条删除不影响前后(test_db):
    """msg_del_one 只删一条（与 msg_del_from 的语义区分开）。"""
    a = db.conv_create("A")["id"]
    ids = [db.msg_add(a, "user", f"m{i}") for i in range(3)]

    assert db.msg_del_one(ids[1]) == 1
    assert [m["content"] for m in db.msg_list(a)] == ["m0", "m2"]
