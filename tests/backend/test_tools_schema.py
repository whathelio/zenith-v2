"""C2：常驻工具描述的长度与形态守卫（注册期约束的测试侧对应物）。

为什么需要它 —— `TOOLS_SCHEMA` 是**每轮请求都原样发出去**的常驻载荷
（`llm_client.py:117-118`：`if tools: payload["tools"] = tools`），
且位于 payload 靠前位置，因此它同时决定两件事：

* **成本**：描述总量 ≈ 3K–4K tokens 常驻，每个工具的描述都会一直计费；
* **缓存**：它是否**逐字节静态**直接决定其后所有内容的前缀缓存能否命中 ——
  只要有一个字节变了，后面的缓存全部作废。

原状（2026-10-09 实测，`_diag_archive/2026-10-09/prep_zenith_p1/tools_schema_len.json`）：
58 个工具 / 6458 字符，顶层最长 `call_mcp` **293** 字符 —— 其中
「服务 → 工具名」枚举与该工具 `mcp_name` / `tool_name` 的参数说明**完全重复**。
瘦身后为 112 字符，总量降至 6277。

本文件的作用是让"描述悄悄变长"变成**显式失败**，而不是靠人盯。
配套审计脚本：`tools/audit/tools_schema_stat.py`（`--limit 120 --strict` 可挂 CI）。
"""
from __future__ import annotations

from backend.tools import TOOLS_SCHEMA, _DISABLED_TOOLS

# 与 Study-Mate `lib/tools/define.ts` 的 DESCRIPTION_LIMIT 同构：单个 description 的码点上限。
DESCRIPTION_LIMIT = 120

# 全部工具描述字符总预算 —— 防"每次加一点"式膨胀。
# 2026-10-21 实测 6970 / 7000：含 mt5_volume_profile 合并（+79）与
# web_search/web_fetch 的 DeepSeek provider 参数说明（+53）。余量仅 30 字符，
# 下个新工具必须先精简既有长描述，不要再抬这个常量。
TOTAL_DESC_BUDGET = 7000


def _active_tools() -> list:
    """与运行时一致：过滤掉 `_DISABLED_TOOLS`（否则会把已禁用工具的体量也算进常驻）。"""
    return [t for t in TOOLS_SCHEMA if t["function"]["name"] not in _DISABLED_TOOLS]


def _iter_desc():
    """产出 `(工具名, 位置, 文本)`，位置形如 `顶层` / `参数:name` / `参数:name.sub`。"""
    for tool in _active_tools():
        fn = tool["function"]
        name = fn["name"]
        yield name, "顶层", str(fn.get("description") or "")
        props = (fn.get("parameters") or {}).get("properties") or {}
        for pname, spec in props.items():
            if not isinstance(spec, dict):
                continue
            yield name, f"参数:{pname}", str(spec.get("description") or "")
            nested = spec.get("properties")  # 一层嵌套 object
            if isinstance(nested, dict):
                for sub, sspec in nested.items():
                    if isinstance(sspec, dict):
                        yield name, f"参数:{pname}.{sub}", str(sspec.get("description") or "")


class TestDescriptionLimit:
    """单个 description 不得超过 `DESCRIPTION_LIMIT` 码点。"""

    def test_tool_count_sane(self):
        """守卫自身的守卫：一个工具都扫不到，说明解析/数据结构已变，本文件失去意义。"""
        assert len(_active_tools()) >= 40

    def test_top_level_description_within_limit(self):
        over = [(n, len(d)) for n, loc, d in _iter_desc()
                if loc == "顶层" and len(d) > DESCRIPTION_LIMIT]
        assert not over, (
            f"顶层 description 超 {DESCRIPTION_LIMIT} 码点：{over}。"
            "精简时优先删去与参数说明重复的枚举（见 call_mcp 的前例）。"
        )

    def test_param_description_within_limit(self):
        over = [(n, loc, len(d)) for n, loc, d in _iter_desc()
                if loc != "顶层" and len(d) > DESCRIPTION_LIMIT]
        assert not over, f"参数 description 超 {DESCRIPTION_LIMIT} 码点：{over}"

    def test_no_newline_in_descriptions(self):
        bad = [(n, loc) for n, loc, d in _iter_desc() if "\n" in d or "\r" in d]
        assert not bad, f"description 不得含换行（换行会显著膨胀常驻载荷）：{bad}"

    def test_total_budget(self):
        total = sum(len(d) for _, _, d in _iter_desc())
        assert total <= TOTAL_DESC_BUDGET, (
            f"常驻描述合计 {total} 字符，超预算 {TOTAL_DESC_BUDGET}。"
            "新增工具或加长描述前，请先评估其对前缀缓存与每轮 token 成本的影响；"
            "确有必要时上调本常量，并同步更新报告基线。"
        )


class TestToolsSchemaIsStaticallyFrozen:
    """D3-1：tools 数组必须**逐字节静态** —— 它渲染在 prompt 最前，是前缀缓存的头部。

    历史包袱：2026-10-08 版曾在 `get_tools_schema()` 里把「当前有哪些集合、各装多少」
    动态注入 `retrieve_docs` 的 collection 说明。**实测该设计会每 60 秒重置一次前缀缓存**
    （入库任务让计数变化 → tools 字节变 → 其后全部缓存作废）。
    改法：清单整体移出 schema，改注入 system 尾部（`collection_hint_text()`）。
    """

    @staticmethod
    def _freeze(obj) -> str:
        import json
        return json.dumps(obj, ensure_ascii=False, sort_keys=True)

    async def test_get_tools_schema_is_byte_identical_across_calls(self, monkeypatch):
        """网关返回的集合清单在两次调用之间**变化**，schema 仍须完全一致。"""
        from backend import tools as T

        class _FakeKnowledge:
            def __init__(self):
                self.n = 0

            async def list_collections(self):
                self.n += 1
                return {
                    "collections": [{"collection": f"coll_{self.n}",
                                     "docs": self.n, "chunks": self.n * 10}],
                    "default": f"coll_{self.n}",
                }

        monkeypatch.setattr(T.knowledge_service, "list_collections",
                            _FakeKnowledge().list_collections)
        T._COLL_HINT_CACHE.update({"at": 0.0, "text": None})

        a = await T.get_tools_schema()
        T._COLL_HINT_CACHE.update({"at": 0.0, "text": None})
        b = await T.get_tools_schema()

        assert self._freeze(a) == self._freeze(b), (
            "tools schema 两次调用不一致 —— 前缀缓存会被击穿"
        )

    async def test_schema_equals_static_constant(self):
        """返回内容必须与静态常量逐字节一致（即没有夹带任何运行时注入）。"""
        from backend import tools as T

        got = await T.get_tools_schema()
        assert self._freeze(got) == self._freeze(T.TOOLS_SCHEMA)

    async def test_schema_is_a_copy_not_the_global(self):
        """返回副本 —— 调用方若误改不会污染全局常量，但内容仍与全局一致。"""
        from backend import tools as T

        got = await T.get_tools_schema()
        assert got is not T.TOOLS_SCHEMA
        assert self._freeze(got) == self._freeze(T.TOOLS_SCHEMA)

    async def test_collection_hint_carries_the_list(self, monkeypatch):
        """清单没丢 —— 它只是换了位置（system 尾部），模型仍能拿到集合名与规模。"""
        from backend import tools as T

        class _FakeKnowledge:
            async def list_collections(self):
                return {
                    "collections": [{"collection": "science_zh", "docs": 2795, "chunks": 13134}],
                    "default": "science_zh",
                }

        monkeypatch.setattr(T.knowledge_service, "list_collections",
                            _FakeKnowledge().list_collections)
        T._COLL_HINT_CACHE.update({"at": 0.0, "text": None})

        text = await T.collection_hint_text()
        assert "science_zh" in text
        assert "2795" in text and "13134" in text
        assert "【默认】" in text
        assert "retrieve_docs" in text          # 得说清这清单是给哪个参数用的

    async def test_collection_hint_fails_safe(self, monkeypatch):
        """网关报错 → 返回空串（不抛异常、不注入半截内容、不阻断对话）。"""
        from backend import tools as T

        async def _boom():
            raise RuntimeError("gateway down")

        monkeypatch.setattr(T.knowledge_service, "list_collections", _boom)
        T._COLL_HINT_CACHE.update({"at": 0.0, "text": None})
        assert await T.collection_hint_text() == ""

    async def test_collection_hint_empty_when_no_collections(self, monkeypatch):
        """网关正常但一个集合都没有 → 同样返回空串（不留空标题污染 system）。"""
        from backend import tools as T

        class _FakeKnowledge:
            async def list_collections(self):
                return {"collections": [], "default": ""}

        monkeypatch.setattr(T.knowledge_service, "list_collections",
                            _FakeKnowledge().list_collections)
        T._COLL_HINT_CACHE.update({"at": 0.0, "text": None})
        assert await T.collection_hint_text() == ""
