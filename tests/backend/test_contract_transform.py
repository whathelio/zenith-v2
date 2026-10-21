"""契约测试：`POST /api/transform` 的返回字段必须与前端类型声明**逐字段一致**。

背景（2026-10-17 全库审计 `0-1` / 复核 V5）：
    后端 `app.py` 只返回 `{success, source_type, target_type, id, item}`，
    而 `frontend/src/shared/api.ts` 的 `transform` 却声明了
    `{success, source_type, source_id, target_type, created_id, created_item, preview}`
    —— 后 4 个字段**运行时不存在**。TypeScript 编译期完全放行（它只信声明），
    于是 `TransformButton.tsx` 解引用 `preview.preview.title` 时抛 TypeError，
    被 `App.tsx` 唯一一处 ErrorBoundary 接住 → **整站白屏**。

为什么用"静态对齐"而不是 TestClient：
    这个缺陷的本质是「类型声明 ≠ 实际返回」，两边都在仓库里、都能读，
    静态比对能**零依赖、毫秒级**地卡住回归；而走 HTTP 需要起 app + 建库 +
    造三张表的种子数据，会把一条契约断言变成一个集成测试，反而没人跑。
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
APP_PY = PROJECT_DIR / "backend" / "app.py"
API_TS = PROJECT_DIR / "frontend" / "src" / "shared" / "api.ts"


def _extract_balanced(text: str, start: int) -> str:
    """从 `start`（指向 `{`）起截取配平的括号内容（含首尾花括号）。"""
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise AssertionError("括号未配平，正则定位可能已失效（源码结构变了就更新本测试）")


def _backend_transform_fields() -> set[str]:
    src = APP_PY.read_text(encoding="utf-8")
    m = re.search(r'@app\.post\(\s*"/api/transform"\s*\)', src)
    assert m, "backend/app.py 里找不到 @app.post('/api/transform')"

    # 从装饰器起，到下一个顶层 @app. 装饰器为止 = 本处理函数体
    nxt = re.search(r"\n@app\.", src[m.end():])
    body = src[m.end(): m.end() + nxt.start()] if nxt else src[m.end():]

    returns = list(re.finditer(r"return\s*\{", body))
    assert returns, "/api/transform 里找不到 return {...}"
    block = _extract_balanced(body, returns[-1].end() - 1)
    return set(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:', block))


def _frontend_transform_fields() -> set[str]:
    src = API_TS.read_text(encoding="utf-8")
    m = re.search(r"transform:\s*\(\s*sourceType", src)
    assert m, "frontend/src/shared/api.ts 里找不到 transform 方法"

    gen = re.search(r"request<\s*\{", src[m.end():])
    assert gen, "transform 的 request<> 泛型里找不到对象字面量类型"
    block_start = m.end() + gen.end() - 1
    block = _extract_balanced(src, block_start)
    # 去掉方法签名/嵌套，只取本层字段名（支持 `a?: T` 与 `a: T;`）
    return set(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*\??\s*:", block))


def test_transform_contract_fields_match():
    backend = _backend_transform_fields()
    frontend = _frontend_transform_fields()

    extra_in_ts = sorted(frontend - backend)
    missing_in_ts = sorted(backend - frontend)

    assert not extra_in_ts, (
        f"前端声明了后端**不会返回**的字段：{extra_in_ts}\n"
        f"  后端实际返回：{sorted(backend)}\n"
        f"  这正是 2026-10-17 审计里导致整站白屏的那类缺陷（类型上存在、运行时 undefined）。\n"
        f"  修法：改 api.ts 的类型声明，不要去后端补假字段。"
    )
    assert not missing_in_ts, (
        f"后端返回了前端类型**没有声明**的字段：{missing_in_ts}\n"
        f"  前端声明的：{sorted(frontend)}\n"
        f"  修法：同步 api.ts 的类型，否则调用方拿不到这些字段的类型提示。"
    )


def test_transform_contract_smoke_fields_present():
    """最低限度：三个必要时段字段必须在，防止有人把整个返回改成 `{success: True}`。"""
    backend = _backend_transform_fields()
    for field in ("success", "source_type", "target_type", "id", "item"):
        assert field in backend, f"/api/transform 返回里缺少必要字段 {field}（现有：{sorted(backend)}）"
