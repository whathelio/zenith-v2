"""金十 SSE 选帧的回归守卫（2026-09-28，D12 配套）。

为什么需要：`jin10_service._parse_sse_body` 与 `mcp_client._parse_sse` 原本**逐行等价**
（盲取 `data_lines[-1]` + `"".join` 兜底），Z12 只修了 `mcp_client` 那一份 →
金十这份是**同缺陷副本**，且没有 id 匹配，比 Z12 修后版本更弱。
本文件把那 11 个用例固化进常规回归（原先只存在于 `_diag_archive/.../dz12b_verify.py`）。

四个可复现的缺陷形态（D12 前）：
  ① 通知帧在响应帧**之后** → 盲取末帧拿到通知帧 → 静默返回 `{}`，调用方以为「成功但空」
  ⑦ 只有通知帧、确无响应帧 → 同上，**该报未报**
  ⑩ 非 SSE 体是合法 JSON-RPC 但既无 `result` 也无 `error`（协议违规）→ 静默返回 `{}`

选帧规则（对齐 Z12）：
  ① 给了 `want_id` → 优先返回 id 匹配的帧（`str()` 比较，兼容字符串 id）
  ② 否则返回最后一个含 `result`/`error` 的帧 —— 通知帧两键皆无，**不可能**被选中
  ③ 都没有 → 退回末帧；无候选 → `{}`
调用方（`_mcp_post`）必须自行判定 ③ 返回的帧是否合法。
"""
import asyncio

import pytest


class _FakeResp:
    def __init__(self, status, text, ctype):
        self.status_code = status
        self.text = text
        self.headers = {"content-type": ctype}


class _FakeClient:
    """替身 httpx.AsyncClient：只实现 `_mcp_post` 用到的 surface。"""

    def __init__(self, resp):
        self._resp = resp
        self.is_closed = False

    async def post(self, url, json=None, headers=None):
        return self._resp


def _make_svc(body, ctype, status=200):
    from backend.jin10_service import Jin10Service

    s = Jin10Service.__new__(Jin10Service)
    s._url = "https://example.invalid/mcp"
    s._token = "x" * 40
    s._client = _FakeClient(_FakeResp(status, body, ctype))
    s._initialized = True
    s._session_id = None
    s._req_id = 0
    s._last_error = ""
    return s


def _call(body, ctype, req_id=7):
    """跑一遍 `_mcp_post` 全链路，返回 (返回帧, 错误原因)。"""
    s = _make_svc(body, ctype)
    frame = asyncio.run(s._mcp_post({"jsonrpc": "2.0", "id": req_id,
                                     "method": "tools/call", "params": {}}))
    return frame, s.last_error()


def _sse(*payloads):
    return "\n".join("data: " + p for p in payloads) + "\n\n"


_NOTIFY = '{"jsonrpc":"2.0","method":"notifications/message","params":{"level":"info"}}'
_OK = '{"jsonrpc":"2.0","id":7,"result":{"content":[{"type":"text","text":"{\\"ok\\":true}"}]}}'

# (用例名, body, content-type, 期望)  期望 'data'=拿到结果 / 'visible'=可见失败
CASES = [
    ("通知帧在后", _sse(_OK, _NOTIFY), "text/event-stream", "data"),
    ("通知帧在前", _sse(_NOTIFY, _OK), "text/event-stream", "data"),
    ("单帧正常", _sse(_OK), "text/event-stream", "data"),
    ("响应帧无 id（形状回退）",
     _sse('{"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"{\\"a\\":1}"}]}}'),
     "text/event-stream", "data"),
    ("跨多行 data 的 JSON（守住 join 兜底）",
     'data: {"jsonrpc":"2.0","id":7,\n'
     'data: "result":{"content":[{"type":"text","text":"{\\"m\\":1}"}]}}\n\n',
     "text/event-stream", "data"),
    ("id 被回成字符串", _sse(_OK.replace('"id":7', '"id":"7"')), "text/event-stream", "data"),
    ("只有通知帧、确无响应帧", _sse(_NOTIFY), "text/event-stream", "visible"),
    ("合法空结果（必须不误判）",
     _sse('{"jsonrpc":"2.0","id":7,"result":{"content":[]}}'), "text/event-stream", "data"),
    ("JSON-RPC error",
     _sse('{"jsonrpc":"2.0","id":7,"error":{"code":-32601,"message":"no tool"}}'),
     "text/event-stream", "visible"),
    ("非 SSE 畸形体（无 result/error）", '{"jsonrpc":"2.0","id":7}',
     "application/json", "visible"),
    ("非 SSE 正常体",
     '{"jsonrpc":"2.0","id":7,"result":{"content":[{"type":"text","text":"{\\"n\\":1}"}]}}',
     "application/json", "data"),
]


@pytest.mark.parametrize("name,body,ctype,expect", CASES, ids=[c[0] for c in CASES])
def test_mcp_post_选帧与失败可见性(name, body, ctype, expect):
    """11 例端到端（与 dz12b_verify.py 逐字同源）。"""
    frame, err = _call(body, ctype)
    if expect == "data":
        assert frame, f"{name}: 期望拿到数据，实际空帧（err={err}）"
        assert not err, f"{name}: 拿到数据却同时记了错误: {err}"
    else:
        assert not frame, f"{name}: 期望可见失败，实际拿到 {frame}"
        assert err, f"{name}: 静默返回空帧 —— 调用方无法区分「失败」与「成功但空」"


# ── 选帧规则的单测（直接打 `_parse_sse_body`，定位更精确）──────────────────

def test_选帧_优先id匹配而非末帧():
    """① 通知帧排在响应帧之后时，必须按 id 命中响应帧，而不是取末帧。"""
    from backend.jin10_service import Jin10Service

    frame = Jin10Service._parse_sse_body(_sse(_OK, _NOTIFY), want_id=7)
    assert frame.get("result", {}).get("content"), f"选错帧: {frame}"
    assert frame.get("id") == 7


def test_选帧_形状回退跳过通知帧():
    """② 无 id 匹配时，必须跳过不含 result/error 的通知帧。"""
    from backend.jin10_service import Jin10Service

    frame = Jin10Service._parse_sse_body(_sse(_OK, _NOTIFY), want_id=None)
    assert "result" in frame or "error" in frame, f"选中了通知帧: {frame}"


def test_选帧_末帧兜底与空候选():
    """③ 多帧皆无 result/error 时退回末帧；无 data 行时返回 {}。"""
    from backend.jin10_service import Jin10Service

    assert Jin10Service._parse_sse_body(_sse(_NOTIFY, _NOTIFY), want_id=None)["method"] \
        == "notifications/message"
    assert Jin10Service._parse_sse_body("", want_id=7) == {}
    assert Jin10Service._parse_sse_body("event: ping\n\n", want_id=7) == {}


def test_协议违规与JSON_RPC错误_文案可区分():
    """两类失败必须给出不同原因，否则排障时看不出是服务端报错还是帧不合法。"""
    _f1, e1 = _call(_sse('{"jsonrpc":"2.0","id":7}'), "text/event-stream")
    _f2, e2 = _call(_sse('{"jsonrpc":"2.0","id":7,"error":{"code":-1,"message":"x"}}'),
                    "text/event-stream")
    assert e1 and e2 and e1 != e2, f"文案不可区分: {e1!r} / {e2!r}"
    assert "result" in e1, f"协议违规文案应指出缺 result: {e1}"
