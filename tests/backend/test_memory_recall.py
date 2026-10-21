"""记忆召回修复的回归测试（2026-10-09）。

覆盖四处改动（此前全部零单测覆盖）：
  1. `memory_engine.recall_by_association` —— 字符 n-gram **短语判据**
     （≥1 个 4-gram 或 ≥2 个 3-gram），只返回实质命中、不做重要度兜底。
  2. `tools._handle_search_memory` —— 词法（FTS5+LIKE）空手时回落联想召回。
  3. `memory_engine.build_memory_injection(with_meta=True)` —— mem_recall 留痕契约。
  4. `routers.chat._preread_result_is_empty` —— 预读空结果不注入（消除负向信号）。

判据的标定依据与已知边界见 `recall_by_association` 的 docstring。
"""
import pytest

import backend.memory_engine as engine
from backend.tools import _handle_search_memory
from backend.routers.chat import _preread_result_is_empty


@pytest.fixture
def seed_memories(test_db):
    """插入两条内容差异明显的记忆，并**强制重建** gram 缓存。

    ⚠️ gram 缓存是模块级、仅按「记忆条数变化」刷新 —— 用例之间条数可能相同，
    沿用缓存会读到上一个用例的内容。故必须显式重建。
    """
    test_db.executemany(
        "INSERT INTO memories (type, content, importance, keywords, created_at) "
        "VALUES ('decision', ?, 4, ?, datetime('now'))",
        [
            ("用户决定将日本泡沫经济的分析存为笔记，并纳入交易风控手册", "泡沫经济,风控"),
            ("MT5 账户使用的是 Exness 的模拟账户", "MT5,账户"),
        ],
    )
    test_db.commit()
    engine._build_gram_cache()
    yield


class TestRecallByAssociation:
    def test_hits_paraphrased_query(self, seed_memories):
        """口语整句（含 4-gram 短语重合）应召回 —— 这是本次修复的主场景。"""
        hits = engine.recall_by_association("之前我们聊过日本泡沫经济的事，你还记得吗")
        assert hits, "口语整句应召回"
        assert "日本泡沫经济" in hits[0]["content"]

    def test_rejects_out_of_library_topics(self, seed_memories):
        """库外主题必须**拒绝**（不硬凑）—— 短语判据的核心保证。"""
        for q in ("推荐几部科幻电影", "今天天气怎么样", "如何做一道番茄炒蛋"):
            assert engine.recall_by_association(q) == [], f"应拒绝: {q}"

    def test_rejects_pure_noise(self, seed_memories):
        """纯噪声串曾能在**总分**上拿到 20.70（高于部分真查询）—— 短语判据必须拦住它。"""
        assert engine.recall_by_association("zzz_绝不存在的词_qqq") == []

    def test_empty_and_whitespace(self, seed_memories):
        assert engine.recall_by_association("") == []
        assert engine.recall_by_association("   ") == []

    def test_short_query_deadzone_is_known(self, seed_memories):
        """已知边界：≤4 字符查询判据不成立（3 字只有 1 个 3-gram）→ 返回空。

        该场景应走**词法通道**（见 test_search_memory_lexical_path）。
        若将来改了判据，这条会失败 —— 那是提醒同步更新 docstring。
        """
        assert engine.recall_by_association("MT5") == []


class TestSearchMemoryFallback:
    def test_falls_back_to_association(self, seed_memories):
        """整句词法空手 → 回落联想，且结果**标注**为联想匹配。"""
        r = _handle_search_memory({"keyword": "之前我们聊过日本泡沫经济的事，你还记得吗"})
        assert r["success"] is True
        assert "联想" in r["result"], r["result"][:60]
        assert "可用ID:" in r["result"]

    def test_lexical_path_unchanged(self, seed_memories):
        """精确词仍走词法：结果**不得**带联想标注（避免污染模型判断）。"""
        r = _handle_search_memory({"keyword": "日本泡沫经济"})
        assert r["success"] is True
        assert "未找到相关记忆" not in r["result"]
        assert "联想" not in r["result"]

    def test_honest_when_nothing_found(self, seed_memories):
        """两路都空手时如实返回 —— 不硬凑结果。"""
        assert _handle_search_memory({"keyword": "推荐几部科幻电影"})["result"] == "未找到相关记忆。"


class TestMemoryInjectionMeta:
    def test_default_returns_str(self, seed_memories):
        """默认调用契约不变（既有调用点零影响）。"""
        assert isinstance(engine.build_memory_injection("日本泡沫经济"), str)

    def test_meta_shape(self, seed_memories):
        text, meta = engine.build_memory_injection("日本泡沫经济", with_meta=True)
        assert isinstance(text, str)
        assert meta["mode"] == "query"
        assert meta["chars"] == len(text)
        assert isinstance(meta["memory_ids"], list)
        assert all(isinstance(i, int) for i in meta["memory_ids"])
        assert meta["replaced_by_summary"] is False

    def test_meta_on_empty_db(self, test_db):
        engine._build_gram_cache()
        text, meta = engine.build_memory_injection("任意查询内容", with_meta=True)
        assert text == ""
        assert meta["chars"] == 0
        assert meta["memory_ids"] == []

    def test_meta_cleared_when_replaced_by_summary(self, test_db, monkeypatch):
        """正文被周期摘要**整体替换**时，ids 必须清空 —— 否则痕迹会把
        「其实没进 prompt」的记忆记成「已注入」（2026-10-09 独立复核修正项）。

        需要注入体量真正超过 MAX_INJECT_CHARS(1500) 才会走到截断分支，
        故这里自备一批**长内容**记忆（用无 query 的 importance 模式，不依赖打分命中）。
        """
        long_text = "这是一段关于系统部署与运维流程细节的详细说明文本。" * 9
        rows = [
            (t, f"第{i}号记录：{long_text}", 4, f"tag{i}{t}", "2026-01-01T00:00:00")
            for i in range(8) for t in ("fact", "experience", "decision")
        ]
        test_db.executemany(
            "INSERT INTO memories (type, content, importance, keywords, created_at) "
            "VALUES (?, ?, ?, ?, ?)", rows,
        )
        test_db.commit()
        engine._build_gram_cache()

        import backend.database as dbm
        monkeypatch.setattr(dbm, "psum_list", lambda: [
            {"period_type": "weekly", "summary": "周" * 400}
        ])
        text, meta = engine.build_memory_injection("", with_meta=True)
        assert meta["truncated"] is True, "用例前提：注入体量必须超过预算（否则测不到该分支）"
        assert meta["replaced_by_summary"] is True
        assert meta["memory_ids"] == [] and meta["note_ids"] == [] and meta["groups"] == {}
        assert "周期摘要" in text


@pytest.fixture
def seed_bulk_memories(test_db):
    """灌入足以撑爆 MAX_INJECT_CHARS(1500) 预算的记忆（跨 4 个 type，保证多分组渲染）。

    2026-10-09 D-A 修复的回归基座：只有真正超预算，才能区分
    「整体换摘要」（旧）与「按预算逐条渲染」（新）。
    """
    filler = "关于日本泡沫经济的成因与交易风控应对细节的完整说明文字。" * 5  # ≈145 字符
    types = ("fact", "experience", "decision", "event")
    rows = []
    for gi, tp in enumerate(types):
        for i in range(5):
            rows.append((tp, f"第{gi}-{i}条：{filler}", 5 - i % 3,
                         f"tag{gi}{i}", "2026-01-01T00:00:00"))
    test_db.executemany(
        "INSERT INTO memories (type, content, importance, keywords, created_at) "
        "VALUES (?, ?, ?, ?, ?)", rows,
    )
    test_db.commit()
    engine._build_gram_cache()
    yield


class TestInjectionBudget:
    """D-A（2026-10-09）：注入超预算时的策略。

    旧行为：一律用 `psum_list()` 的周期摘要**整体替换**正文 → 有 query 时
    联想召回的条目全部被顶掉（线上实测 `replaced_by_summary=True` 常态化）。
    新行为：先按预算逐条渲染；周期摘要只在 importance 模式或「一条都没装下」时启用。
    """

    def test_query_mode_keeps_items_over_summary(self, test_db, seed_bulk_memories, monkeypatch):
        """核心断言：有 query 时必须保留具体条目，不得整体替换为周期摘要。"""
        import backend.database as dbm
        monkeypatch.setattr(dbm, "psum_list", lambda: [
            {"period_type": "weekly", "summary": "周" * 400}
        ])
        text, meta = engine.build_memory_injection("日本泡沫经济怎么应对", with_meta=True)
        assert meta["mode"] == "query"
        assert meta["truncated"] is True, "用例前提：注入体量必须超预算，否则测不到该分支"
        assert meta["replaced_by_summary"] is False, "query 模式不得整体替换为摘要（D-A 核心）"
        assert meta["memory_ids"], "应保留具体召回条目"
        assert "周期摘要" not in text
        assert "日本泡沫经济" in text

    def test_query_mode_never_touches_psum(self, test_db, seed_bulk_memories, monkeypatch):
        """query 模式压根不该调用 psum_list（语义保证，而非仅仅"结果看起来对"）。"""
        import backend.database as dbm

        def _boom():
            raise AssertionError("query 模式不应调用 psum_list")

        monkeypatch.setattr(dbm, "psum_list", _boom)
        _text, meta = engine.build_memory_injection("日本泡沫经济", with_meta=True)
        assert meta["truncated"] is True
        assert meta["replaced_by_summary"] is False

    def test_ids_match_rendered_when_truncated(self, test_db, seed_bulk_memories):
        """截断时 memory_ids 必须与 groups 计数一致（不得虚报未渲染的条目）。"""
        _text, meta = engine.build_memory_injection("日本泡沫经济", with_meta=True)
        assert meta["truncated"] is True
        assert sum(meta["groups"].values()) == len(meta["memory_ids"])
        assert len(meta["memory_ids"]) == len(set(meta["memory_ids"]))

    def test_within_budget_not_truncated(self, seed_memories):
        """预算内：不截断、不替换（回归保护）。"""
        _text, meta = engine.build_memory_injection("日本泡沫经济", with_meta=True)
        assert meta["truncated"] is False
        assert meta["replaced_by_summary"] is False

    def test_rendered_is_contiguous_prefix_of_global_ranking(self, test_db, seed_bulk_memories):
        """★装载公平性（2026-10-09 优化）：渲染集合必须是**全局相关度序的连续前缀**。

        旧实现「先按 type 分组、按组顺序装满」会让排在前面的分组用低分条目挤掉
        后面分组的高分条目（40 条真实查询实测 78% 存在该偏差）。
        现实现「全局按相关度装填 → 再按组渲染」，故渲染的条目在全局序中的位置
        必须恰好是 0..k-1 的一个连续段。
        """
        mems, _notes = engine.search_related_items("日本泡沫经济", limit=15, include_notes=True)
        global_order = [m["id"] for m in mems]
        _text, meta = engine.build_memory_injection("日本泡沫经济", with_meta=True)

        pos = sorted(global_order.index(i) for i in meta["memory_ids"] if i in global_order)
        assert pos, "应至少渲染一条"
        assert pos == list(range(len(pos))), f"渲染的不是全局序连续前缀，存在高分被挤掉: {pos}"


@pytest.mark.parametrize("txt,expected", [
    ("", True),
    ("   ", True),
    ("未找到相关记忆。", True),                       # search_memory 的确切空结果
    ("未发现相似笔记或记忆，可放心入库。", False),      # check_duplicate 的**有效结论**
    ("本地暂无黄金仓位", False),                       # 复核反例：宽判据会误杀的有效命中
    ("未找到笔记 ID=7", False),                        # 非 PREREAD 工具文案
    ("🧠 相关记忆：\n  [ID:1][fact] xxx", False),
    ("x" * 200 + "未找到相关记忆。", False),           # 超过长度上限
])
def test_preread_result_is_empty(txt, expected):
    assert _preread_result_is_empty(txt) is expected
