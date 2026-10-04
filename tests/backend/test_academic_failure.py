"""academic_service.search_papers 的失败语义回归测试。

覆盖 2026-09-11 修复：原先无论数据源是否全部失败都返回 `success=True`，
于是「三个源全挂」与「真的没检索到」在调用方看来一模一样
（都是 success=True + count=0 + papers=[]）—— 典型的「看起来正常实际是空」。
路由层 `routers/academic.py` 早已有 `if not success → 400` 分支，本测试锁住该契约。
"""
import pytest

from backend import academic_service as svc


@pytest.fixture()
def patch_sources(monkeypatch):
    """把三个数据源 + enrich 换成可编程的假实现，避免真实网络与 DB。"""
    monkeypatch.setattr(svc, "_enrich_paper", lambda p: _async(p))  # 直通，不打网络

    def _install(openalex, crossref, semantic):
        async def f_oa(*a, **k):
            if isinstance(openalex, BaseException):
                raise openalex
            return openalex

        async def f_cr(*a, **k):
            if isinstance(crossref, BaseException):
                raise crossref
            return crossref

        async def f_s2(*a, **k):
            if isinstance(semantic, BaseException):
                raise semantic
            return semantic

        monkeypatch.setattr(svc, "_search_openalex", f_oa)
        monkeypatch.setattr(svc, "_search_crossref", f_cr)
        monkeypatch.setattr(svc, "_search_semantic_scholar", f_s2)

    return _install


async def _async(v):
    return v


async def test_三个源全失败必须_success_False(patch_sources):
    """★ 本次修复的核心。"""
    patch_sources(RuntimeError("oa down"), RuntimeError("cr down"), RuntimeError("s2 down"))
    r = await svc.search_papers("test", store=False)
    assert r["success"] is False
    assert r["count"] == 0
    assert r["papers"] == []
    assert len(r["errors"]) == 3
    assert "全部数据源检索失败" in r["error"]


async def test_三个源都正常但无结果应视为成功(patch_sources):
    """「真的没检索到」不等于「检索失败」—— 不能误报失败。"""
    patch_sources([], [], [])
    r = await svc.search_papers("no-hit-query", store=False)
    assert r["success"] is True
    assert r["count"] == 0
    assert r["errors"] == []
    assert r["partial"] is False


async def test_部分源失败但仍有结果应成功且标记_partial(patch_sources):
    patch_sources([_paper("A")], RuntimeError("cr down"), [])
    r = await svc.search_papers("q", store=False)
    assert r["success"] is True
    assert r["count"] >= 1
    assert r["partial"] is True
    assert len(r["errors"]) == 1


async def test_两个源失败一个源空不算全失败(patch_sources):
    """只有 2/3 失败 → 仍有源可用 → 不该报全失败。"""
    patch_sources(RuntimeError("oa"), RuntimeError("cr"), [])
    r = await svc.search_papers("q", store=False)
    assert r["success"] is True
    assert r["partial"] is True


async def test_空_query_直接失败(patch_sources):
    patch_sources([], [], [])
    r = await svc.search_papers("   ", store=False)
    assert r["success"] is False
    assert "不能为空" in r["error"]


def _paper(title: str) -> dict:
    return {
        "title": title, "doi": "", "abstract": "x", "authors": ["a"],
        "venue": "V", "year": 2026, "url": "", "pdf_url": "",
        "code_url": "", "citations": 0, "source": "openalex",
    }
