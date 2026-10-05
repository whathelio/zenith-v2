"""记忆判重 / 合并的阈值契约测试（`backend/memory_engine.py` + `backend/tools.py`）。

为什么需要本文件 —— 阈值此前**无单一守护**：
- `_is_duplicate` 的 0.75 只有一个用例（`test_memory_optimization.py`），
  且 `test_memory_engine.py` 的两处调用传的是自定义 0.25/0.5，**绕开了默认值**。
- `MERGE_SIM_THRESHOLD = 0.85` **零测试**（`grep MERGE_SIM tests/` = 0 命中）。
- 双阈值的**关系**（判重门槛 < 合并门槛）无人守护 —— 而这个关系是刻意的语义：
  先挡住重复入库，合并再保守，因为合并**不可逆**（源码 `:856` 自述「无备份、无回滚路径」）。

⚠️ 现实比"双阈值"更复杂 —— 实测是**三处实现、三个阈值、两种算法**：

| 位置 | 算法 | 默认阈值 |
|:--|:--|--:|
| `memory_engine._is_duplicate` | n-gram TF-IDF 余弦 / Jaccard 取 max | **0.75** |
| `tools._find_duplicate_memory` | `difflib.SequenceMatcher.ratio()`（+0.05 同类型加权） | **0.7** |
| `tools._find_duplicate_note` | 同上（笔记） | **0.85** |

本文件**不主张**它们应该一致（统一算法与阈值是独立决策），只负责：
① 锁定各自当前的默认值，防无声改动；② 锁定双阈值的相对关系；③ 防相似度路径静默退化。
"""
import inspect

import pytest

from backend import memory_engine, tools

# memory_engine 的判重默认阈值（函数签名里的字面量，非模块常量）
DEDUP_DEFAULT = 0.75


def _stub_search(monkeypatch, hits):
    """把候选召回打桩成固定结果，隔离出纯阈值逻辑。"""
    monkeypatch.setattr(memory_engine, "mem_search", lambda *a, **k: list(hits))


def _stub_similarity(monkeypatch, value):
    monkeypatch.setattr(memory_engine, "_similarity", lambda a, b: value)


class TestThresholdRelationship:
    """双阈值的相对关系 —— 判重比合并**更早、更宽松**。"""

    def test_dedup_default_is_pinned(self):
        """锁定 `_is_duplicate` 的默认阈值（防有人改签名里的字面量）。"""
        sig = inspect.signature(memory_engine._is_duplicate)
        assert sig.parameters["threshold"].default == DEDUP_DEFAULT

    def test_merge_threshold_is_pinned(self):
        assert memory_engine.MERGE_SIM_THRESHOLD == 0.85

    def test_dedup_is_looser_than_merge(self):
        """🔴 核心关系：判重门槛必须**低于**合并门槛。

        语义 = 「先挡住重复写入（0.75 就拦），但只在高度确信时才动手合并（0.85）」。
        合并会归档掉一条记忆且无回滚路径，所以门槛更高。
        若有人把两者改成相等或倒置，本测试失败。
        """
        assert DEDUP_DEFAULT < memory_engine.MERGE_SIM_THRESHOLD

    def test_merge_threshold_is_stricter_than_note_dedup_match(self):
        """笔记判重与记忆合并恰好都是 0.85 —— 记录这个巧合，改动时能被察觉。"""
        note_threshold = inspect.signature(
            tools._find_duplicate_note
        ).parameters["threshold"].default
        assert note_threshold == memory_engine.MERGE_SIM_THRESHOLD == 0.85


class TestDedupBoundary:
    """`_is_duplicate` 的阈值边界行为（用打桩的相似度值隔离验证）。"""

    def test_similarity_at_threshold_counts_as_duplicate(self, monkeypatch):
        """恰好等于阈值 → 判重（源码用 `>=`）。"""
        _stub_search(monkeypatch, [{"id": 1, "content": "已有记忆内容"}])
        _stub_similarity(monkeypatch, DEDUP_DEFAULT)
        assert memory_engine._is_duplicate("足够长的待判内容") is True

    def test_just_below_threshold_is_not_duplicate(self, monkeypatch):
        _stub_search(monkeypatch, [{"id": 1, "content": "已有记忆内容"}])
        _stub_similarity(monkeypatch, DEDUP_DEFAULT - 0.01)
        assert memory_engine._is_duplicate("足够长的待判内容") is False

    def test_band_between_thresholds_dedupes_but_stays_below_merge(self, monkeypatch):
        """🔴 关键区间 [0.75, 0.85)：判重命中，但**低于**合并门槛 → 不会被合并。

        这个区间正是双阈值设计的意义所在 —— 挡住重复，但不激进合并。
        """
        sim = 0.80
        assert DEDUP_DEFAULT <= sim < memory_engine.MERGE_SIM_THRESHOLD
        _stub_search(monkeypatch, [{"id": 1, "content": "已有记忆内容"}])
        _stub_similarity(monkeypatch, sim)
        assert memory_engine._is_duplicate("处于阈值区间的内容") is True

    def test_picks_highest_similarity_among_candidates(self, monkeypatch):
        """多候选中任一超过阈值即判重（源码遍历后 return True）。"""
        _stub_search(monkeypatch, [
            {"id": 1, "content": "候选一"},
            {"id": 2, "content": "候选二"},
        ])
        calls = []

        def _varying(a, b):
            calls.append(b)
            return 0.9 if b == "候选二" else 0.1

        monkeypatch.setattr(memory_engine, "_similarity", _varying)
        assert memory_engine._is_duplicate("待判定的内容文本") is True
        assert calls == ["候选一", "候选二"], "应按顺序逐个比对"


class TestDedupPreconditions:
    """前置条件：短路返回，不查库。"""

    @pytest.mark.parametrize("content", ["", "a", "ab", "abc"])
    def test_content_shorter_than_four_is_never_duplicate(self, monkeypatch, content):
        def _explode(*a, **k):
            raise AssertionError("短内容不应触发候选召回")

        monkeypatch.setattr(memory_engine, "mem_search", _explode)
        assert memory_engine._is_duplicate(content) is False

    def test_no_candidates_returns_false(self, monkeypatch):
        _stub_search(monkeypatch, [])
        monkeypatch.setattr(memory_engine, "_extract_keywords", lambda *a, **k: [])
        assert memory_engine._is_duplicate("一段足够长的内容文本") is False

    def test_candidate_with_empty_content_does_not_crash(self, monkeypatch):
        _stub_search(monkeypatch, [{"id": 1, "content": ""}])
        _stub_similarity(monkeypatch, 0.5)
        assert memory_engine._is_duplicate("一段足够长的内容文本") is False


class TestMergeGroupingUsesConstant:
    """`find_similar_memory_groups` 的默认阈值 == `MERGE_SIM_THRESHOLD`。"""

    def test_identical_same_type_forms_one_group(self):
        """正向基线：完全相同的同类型记忆应组成一个合并组。

        （若本测试也失败，说明下面几条"负向"断言是假绿 —— 函数恒返回 []。）
        返回结构：`{keep_id, delete_ids, merged_content}`。
        """
        mems = [
            {"id": 1, "type": "fact", "content": "完全一样的一条记忆内容文本"},
            {"id": 2, "type": "fact", "content": "完全一样的一条记忆内容文本"},
        ]
        groups = memory_engine.find_similar_memory_groups(mems)
        assert len(groups) == 1
        assert set(groups[0]["delete_ids"]) == {1, 2} - {groups[0]["keep_id"]}
        assert groups[0]["merged_content"]

    def test_dissimilar_same_type_not_grouped(self):
        """同类型但内容无关 → 不组。"""
        mems = [
            {"id": 1, "type": "fact", "content": "今天去公园散步了"},
            {"id": 2, "type": "fact", "content": "Python 的装饰器语法"},
        ]
        assert memory_engine.find_similar_memory_groups(mems) == []

    def test_default_equals_explicit_constant(self):
        """不传 threshold 时，行为应与显式传常量一致（证明没写死别的数）。"""
        mems = [
            {"id": 1, "type": "fact", "content": "完全一样的一条记忆内容文本"},
            {"id": 2, "type": "fact", "content": "完全一样的一条记忆内容文本"},
            {"id": 3, "type": "preference", "content": "另一条无关内容"},
        ]
        assert (memory_engine.find_similar_memory_groups(mems)
                == memory_engine.find_similar_memory_groups(
                    mems, threshold=memory_engine.MERGE_SIM_THRESHOLD))

    def test_cross_type_never_grouped(self, monkeypatch):
        """跨 type 不比较 —— 相同内容但类型不同不得成为合并组。"""
        _stub_similarity(monkeypatch, 1.0)  # 即便相似度满分
        mems = [
            {"id": 1, "type": "fact", "content": "完全相同的内容文本呀"},
            {"id": 2, "type": "preference", "content": "完全相同的内容文本呀"},
        ]
        assert memory_engine.find_similar_memory_groups(mems) == []

    def test_empty_input_returns_empty(self):
        assert memory_engine.find_similar_memory_groups([]) == []


class TestSimilarityPathNotDegraded:
    """防相似度路径**静默退化**到 Jaccard 降级分支。

    `_similarity` 的结构是 try/except：语义路径抛异常就静默降级。
    2026-09-11 曾修过一次「vocab 误用全量 IDF」的性能事故（`:476-480` 有注释留档）。
    若修复被回退或再次抛异常，会无声回到旧行为 —— 本组测试守住它。
    """

    def test_delegates_to_semantic_path(self):
        a = "这是一段用于比对的中文文本内容"
        b = "这是另一段用于比对的中文文本材料"
        assert memory_engine._similarity(a, b) == memory_engine._semantic_similarity(a, b)

    @pytest.mark.parametrize("a,b", [
        ("", ""),
        ("a", "b"),
        ("中文", "中文"),
        ("x" * 500, "y" * 500),
        ("混合 English 与中文 123", "混合 English 与中文 456"),
    ])
    def test_semantic_similarity_never_raises(self, a, b):
        """语义路径对边界输入不得抛异常（一抛就静默降级）。"""
        sim = memory_engine._semantic_similarity(a, b)
        assert isinstance(sim, float)
        assert 0.0 <= sim <= 1.0

    def test_identical_content_scores_one(self):
        assert memory_engine._similarity("完全相同的文本内容", "完全相同的文本内容") == 1.0

    def test_falls_back_to_jaccard_on_exception(self, monkeypatch):
        """降级路径本身仍可用（异常时不崩，退回 Jaccard）。"""
        def _boom(a, b):
            raise RuntimeError("simulated semantic failure")

        monkeypatch.setattr(memory_engine, "_semantic_similarity", _boom)
        assert memory_engine._similarity("abc", "abc") == 1.0
        assert memory_engine._legacy_jaccard("abc", "abc") == 1.0

    def test_numpy_optional_import_does_not_break_path(self, monkeypatch):
        """numpy 是延迟导入且可缺省 —— 缺它时语义路径应降级而非崩溃。

        `_shared_text_vectors` 内 `import numpy as np` 在函数体内（:467/:506/:554）。
        这里模拟 numpy 不可用，断言 `_similarity` 仍有返回值。
        """
        import builtins
        _real_import = builtins.__import__

        def _no_numpy(name, *a, **k):
            if name == "numpy" or name.startswith("numpy."):
                raise ImportError("numpy blocked for test")
            return _real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", _no_numpy)
        sim = memory_engine._similarity("没有 numpy 时的相似度计算", "没有 numpy 时也要能算")
        assert isinstance(sim, float)


class TestToolsSideThresholdsPinned:
    """`tools.py` 侧两个判重函数的默认阈值守卫。

    ⚠️ 它们与 `memory_engine` 不一致（0.7 / 0.85 vs 0.75），**且算法不同**
    （SequenceMatcher 字符序列比 vs n-gram TF-IDF 余弦）→ 阈值之间**不可直接比较**。
    本组只防无声改动；是否统一属独立决策（见模块 docstring）。
    """

    def test_find_duplicate_memory_default_is_0_7(self):
        sig = inspect.signature(tools._find_duplicate_memory)
        assert sig.parameters["threshold"].default == 0.7

    def test_find_duplicate_note_default_is_0_85(self):
        sig = inspect.signature(tools._find_duplicate_note)
        assert sig.parameters["threshold"].default == 0.85

    def test_two_tool_thresholds_are_not_equal(self):
        """两者刻意不同（记忆 0.7 / 笔记 0.85）—— 记录这个差异。"""
        mem_t = inspect.signature(tools._find_duplicate_memory).parameters["threshold"].default
        note_t = inspect.signature(tools._find_duplicate_note).parameters["threshold"].default
        assert mem_t != note_t

    def test_callers_rely_on_defaults(self):
        """`_find_duplicate_memory` 的调用点不传 threshold → 实际生效 0.7。

        这是「改默认值会影响线上行为」的证据；用源码检查固化。
        """
        src = inspect.getsource(tools)
        assert "_find_duplicate_memory(mem_content, mem_keywords, mem_type)" in src
