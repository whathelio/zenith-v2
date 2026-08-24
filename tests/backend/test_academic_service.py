"""学术论文本地缓存测试 — 不依赖外网，只测 SQLite 嵌入层与 venue 过滤/去重纯函数。"""
from backend.database import (
    academic_paper_upsert,
    academic_paper_get_by_doi,
    academic_paper_search,
    academic_paper_stats,
)
from backend.academic_service import (
    _venue_matches,
    _normalize_venue_name,
    _merge_papers,
)


def _paper(**kw):
    base = {
        "doi": "10.1038/s41586-024-07421-0",
        "title": "Detecting hallucinations in large language models using semantic entropy",
        "authors": "Farquhar et al.",
        "venue": "Nature",
        "year": 2024,
        "date": "2024-06-19",
        "citations": 751,
        "tier": "S",
        "rankings": "JCR Q1, 中科院1区",
        "abstract": "Semantic entropy detects hallucinations.",
        "url": "https://doi.org/10.1038/s41586-024-07421-0",
        "source": "openalex",
        "region": "international",
        "venue_kind": "journal",
    }
    base.update(kw)
    return base


class TestAcademicPaperCache:
    def test_upsert_and_get_by_doi(self, test_db):
        pid = academic_paper_upsert(_paper())
        assert pid > 0
        p = academic_paper_get_by_doi("10.1038/s41586-024-07421-0")
        assert p and p["venue"] == "Nature"

    def test_search_by_venue_and_query(self, test_db):
        academic_paper_upsert(_paper())
        rows = academic_paper_search(query="hallucinations", venue="Nature", limit=10)
        assert any("hallucinations" in (r["title"] or "").lower() for r in rows)

    def test_stats(self, test_db):
        academic_paper_upsert(_paper())
        stats = academic_paper_stats()
        assert stats["total"] >= 1
        assert any(v["venue"] == "Nature" for v in stats["by_venue"])

    def test_upsert_implementation_links(self, test_db):
        paper = _paper(
            arxiv_id="2406.10741",
            pdf_url="https://arxiv.org/pdf/2406.10741",
            code_links=[{"name": "repo", "url": "https://github.com/example/repo", "source": "github"}],
        )
        pid = academic_paper_upsert(paper)
        assert pid > 0
        p = academic_paper_get_by_doi("10.1038/s41586-024-07421-0")
        assert p["arxiv_id"] == "2406.10741"
        assert p["pdf_url"] == "https://arxiv.org/pdf/2406.10741"
        assert p["code_links"][0]["url"] == "https://github.com/example/repo"


class TestVenueMatching:
    """正刊/子刊精确区分（修复 _venue_matches 后，单词名不再误配子刊）。"""

    def test_science_journal_exact(self):
        assert _venue_matches({"venue": "Science"}, "Science")
        assert _venue_matches({"venue": "Science (New York, N.Y.)"}, "Science")
        assert not _venue_matches({"venue": "Science Advances"}, "Science")
        assert not _venue_matches({"venue": "Science Robotics"}, "Science")

    def test_nature_journal_exact(self):
        assert _venue_matches({"venue": "Nature"}, "Nature")
        assert _venue_matches({"venue": "Nature (London)"}, "Nature")
        assert not _venue_matches({"venue": "Nature Machine Intelligence"}, "Nature")
        assert not _venue_matches({"venue": "Nature Communications"}, "Nature")

    def test_subjournal_prefix(self):
        assert _venue_matches({"venue": "Nature Machine Intelligence"}, "Nature Machine Intelligence")
        assert _venue_matches({"venue": "Science Advances"}, "Science Advances")

    def test_empty_venue_not_matched(self):
        assert not _venue_matches({"venue": ""}, "Science")

    def test_normalize_venue_name(self):
        assert _normalize_venue_name("Science (New York, N.Y.)") == "science"
        assert _normalize_venue_name("Nature, London") == "nature"


class TestMergePapers:
    def test_dedup_by_doi_keeps_higher_citations(self):
        a = {"doi": "10.1/x", "title": "A", "citations": 10}
        b = {"doi": "10.1/X", "title": "A", "citations": 20}
        merged = _merge_papers([a, b], 10)
        assert len(merged) == 1
        assert merged[0]["citations"] == 20
