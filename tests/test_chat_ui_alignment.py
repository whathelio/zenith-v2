"""zenith v2 对话 UI 对齐 — 新端点端到端验证（临时测试脚本）"""
import json
import time
import httpx

BASE = "http://127.0.0.1:8766/api"
results = []


def report(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")


def sse_collect(resp):
    """收集 SSE 事件类型序列"""
    types = []
    text_len = 0
    for line in resp.iter_lines():
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if not data:
            continue
        try:
            ev = json.loads(data)
        except Exception:
            continue
        types.append(ev.get("type"))
        if ev.get("type") == "text":
            text_len += len(ev.get("content", ""))
    return types, text_len


def main():
    c = httpx.Client(base_url=BASE, timeout=120)

    # 1. 创建会话
    conv = c.post("/conversations", json={"title": "UI对齐测试", "persona_name": ""}).json()
    cid = conv["id"]
    report("create conversation", bool(cid), cid)

    # 2. 发送消息（真实 LLM 调用）
    with c.stream("POST", "/chat", json={"message": "请用一句中文介绍你自己", "conversation_id": cid}) as resp:
        types, text_len = sse_collect(resp)
    report("chat SSE 包含 text", "text" in types, f"types={types[:8]} len={text_len}")

    # 3. regenerate（最后一条是 assistant）
    with c.stream("POST", "/chat/regenerate", json={"conversation_id": cid}) as resp:
        types2, text_len2 = sse_collect(resp)
    report("regenerate SSE", "text" in types2, f"len={text_len2}")

    # 验证 regenerate 后消息数正确（user 1 + assistant 1）
    conv2 = c.get(f"/conversations/{cid}").json()
    msgs = conv2.get("messages", [])
    roles = [m["role"] for m in msgs]
    report("regenerate 后消息结构", roles == ["user", "assistant"], str(roles))

    # 4. delete 最后一条消息（id = 最后 assistant）
    last_id = msgs[-1]["id"]
    r = c.delete(f"/chat/messages/{last_id}")
    report("delete message", r.status_code == 200 and r.json().get("deleted", 0) >= 1, str(r.json()))
    conv3 = c.get(f"/conversations/{cid}").json()
    report("delete 后只剩 user 消息", len(conv3.get("messages", [])) == 1, str([m["role"] for m in conv3.get("messages", [])]))

    # 5. stop（当前无任务 → stopped=false，不报错）
    r = c.post("/chat/stop", json={"conversation_id": cid})
    report("stop (idle)", r.status_code == 200 and r.json().get("stopped") is False, str(r.json()))

    # 6. edit 用户消息（触发 SSE 重新生成）
    user_msg = conv3["messages"][0]
    with c.stream("POST", "/chat/edit", json={
        "conversation_id": cid, "msg_id": user_msg["id"],
        "content": "请用一句话介绍你自己",
    }) as resp:
        types4, text_len4 = sse_collect(resp)
    report("edit user SSE", "text" in types4, f"len={text_len4}")
    conv4 = c.get(f"/conversations/{cid}").json()
    report("edit 后消息结构", len(conv4.get("messages", [])) == 2, str([m["role"] for m in conv4.get("messages", [])]))

    # 7. 清理测试会话
    c.delete(f"/conversations/{cid}")
    report("cleanup", True)

    print("\n===== SUMMARY =====")
    failed = [r for r in results if not r[1]]
    print(f"{len(results) - len(failed)}/{len(results)} passed")
    for name, ok, detail in failed:
        print(f"  FAILED: {name} {detail}")


if __name__ == "__main__":
    main()
