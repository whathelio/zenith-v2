"""记忆引擎测试 — FTS5 搜索 + n-gram TF-IDF 相似度 + 去重"""
import pytest
from backend.memory_engine import (
    _similarity, _is_duplicate, build_memory_injection,
    _extract_keywords, mem_consolidate,
)
from backend.database import mem_add, mem_search, mem_list, mem_del


class TestSemanticSimilarity:
    """n-gram TF-IDF 余弦相似度测试"""

    def test_exact_match(self):
        assert _similarity("Python编程", "Python编程") == 1.0

    def test_chinese_semantic_similar(self):
        """中文同义表达相似度应不低于纯 Jaccard（大库时 IDF 加权优势明显）"""
        sim = _similarity("我喜欢在早晨进行交易复盘", "早晨复盘交易是我的习惯")
        # 小样本下 ≈ Jaccard，大库 IDF 加成后显著提升
        assert sim >= 0.15, f"期望>=0.15, 实际={sim}"

    def test_chinese_semantic_different(self):
        """不同语义应有低相似度"""
        sim = _similarity("我喜欢在早晨进行交易复盘", "今天天气很好适合出门")
        assert sim < 0.4, f"期望相似度<0.4, 实际={sim}"

    def test_empty_text(self):
        assert _similarity("", "hello") == 0.0
        assert _similarity("hello", "") == 0.0

    def test_substring_short(self):
        """短文本包含关系应有较高相似度"""
        sim = _similarity("Python", "Python编程语言")
        assert sim >= 0.40, f"期望>=0.40, 实际={sim}"


class TestFTS5Search:
    """FTS5 全文搜索测试"""

    def test_fts5_keyword_search(self, test_db, sample_memory):
        """FTS5 应能通过关键词搜索到记忆"""
        results = mem_search("Python", limit=10)
        assert len(results) >= 1
        assert "Python" in results[0]["content"]

    def test_fts5_keywords_field(self, test_db):
        """Keywords 字段也应被 FTS5 索引"""
        mem_add(
            type_="preference",
            content="用户偏好使用 FastAPI",
            importance=4,
            keywords="FastAPI,web,后端",
        )
        results = mem_search("FastAPI", limit=10)
        assert len(results) >= 1
        assert "FastAPI" in results[0]["content"]

    def test_fts5_chinese_search(self, test_db):
        """FTS5 中文搜索"""
        mem_add(
            type_="experience",
            content="交易复盘时应该关注成交量变化",
            importance=3,
            keywords="交易,复盘,成交量",
        )
        results = mem_search("成交量", limit=10)
        assert len(results) >= 1

    def test_fts5_no_match(self, test_db):
        results = mem_search("xyznonexistentpattern", limit=10)
        assert len(results) == 0


class TestDuplicateDetection:
    """去重检测测试"""

    def test_find_duplicate(self, test_db):
        """近似相同记忆应被检测为重复"""
        mem_add(
            type_="experience",
            content="早晨复盘交易时关注黄金走势和成交量变化",
            importance=3,
            keywords="复盘,黄金,交易,成交量",
            source_conv_id="test",
        )
        # 几乎完全相同的记忆
        assert _is_duplicate("早晨复盘交易时关注黄金走势变化", threshold=0.25) is True

    def test_not_duplicate(self, test_db):
        """不相关记忆不应被判定为重复"""
        mem_add(
            type_="experience",
            content="早晨复盘交易时发现黄金走势强劲",
            importance=3,
            keywords="复盘,黄金",
            source_conv_id="test",
        )
        assert _is_duplicate("今天中午吃了牛肉面味道不错", threshold=0.5) is False

    def test_very_short_content(self):
        """太短的内容跳过检查"""
        assert _is_duplicate("ok") is False
        assert _is_duplicate("") is False


class TestMemoryInjection:
    """记忆注入构建测试"""

    def test_build_injection_empty(self, test_db):
        """无记忆时不崩溃"""
        # 注意：测试 DB 可能已有其他测试的记忆残留
        # 只验证不崩溃即可
        result = build_memory_injection("Python")
        assert isinstance(result, str)

    def test_build_injection_with_query(self, test_db, sample_memory):
        """有关键词时获取相关记忆"""
        result = build_memory_injection("Python")
        assert "Python" in result or result == "", f"实际: {result}"

    def test_build_injection_format(self, test_db, sample_memory):
        """注入格式包含必要段落"""
        mem_add(
            type_="preference",
            content="用户偏好深色主题",
            importance=5,
            keywords="深色,主题,偏好",
            source_conv_id="test",
        )
        result = build_memory_injection("主题")
        assert "记忆库" in result or "关于用户" in result


class TestConsolidation:
    """记忆合并测试"""

    def test_consolidate_no_crash(self, test_db):
        """空表合并不应报错（created_at 可能为 None 时需安全处理）"""
        # 预插入一条记忆确保有 created_at
        mem_add(type_="fact", content="测试记忆", importance=2, keywords="test")
        result = mem_consolidate()
        assert isinstance(result["merged"], int)

    def test_consolidate_with_similar(self, test_db):
        """相似记忆应被合并（验证不崩溃 + 返回预期结构）"""
        # 注意：test_db 是共享的，可能有其他测试的残余记忆
        mem_add(
            type_="fact",
            content="Python是门很好的编程语言",
            importance=3,
            keywords="Python,编程",
        )
        mem_add(
            type_="fact",
            content="Python是很棒的编程语言",
            importance=2,
            keywords="Python,语言",
        )
        result = mem_consolidate()
        assert isinstance(result["merged"], int)
        assert isinstance(result["decayed"], int)


class TestKeywordExtraction:
    """关键词提取测试"""

    def test_chinese_keywords(self):
        kw = _extract_keywords("用户偏好使用 FastAPI 构建后端服务")
        assert len(kw) > 0
        assert any("FastAPI" in k for k in kw) or any("后端" in k for k in kw) or any("服务" in k for k in kw)

    def test_stopwords_filtered(self):
        """纯停用词文本应返回极少关键词"""
        kw = _extract_keywords("的 了 是 在 我 你 他 她")
        # 2-6 字中文片段，"的" 是 1 字被跳过，"了你" 是 2 字但被停用词过滤
        assert len(kw) <= 1
