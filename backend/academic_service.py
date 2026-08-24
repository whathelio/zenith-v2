"""Zenith v2 学术论文服务 — OpenAlex/Crossref 检索 + SQLite 本地缓存

设计目标：
1. 论文检索结果本地化（数据库嵌入）：所有检索结果先写入 SQLite 再返回，
   后续相同 DOI 直接命中本地缓存，离线可查。
2. 支持 Nature / Science 等高影响力期刊过滤。
3. 不引入新依赖：使用 httpx 直接调用 OpenAlex / Crossref / Semantic Scholar 公开 API。
4. 实现路径增强：PapersWithCode + GitHub 搜索代码实现，OpenAlex/S2/Crossref 提供 PDF/arXiv 链接。

注意：本服务只获取公开题录/摘要，不抓取付费墙全文。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

import httpx

from . import database as db

logger = logging.getLogger("zenith.academic")

OPENALEX_WORKS = "https://api.openalex.org/works"
CROSSREF_WORKS = "https://api.crossref.org/works"
S2_BASE = "https://api.semanticscholar.org/graph/v1"
PWC_BASE = "https://paperswithcode.com/api/v1"
GITHUB_BASE = "https://api.github.com"

# 高影响力期刊/出版社过滤词（Nature / Science 体系）
ELITE_VENUES = {
    "Nature", "Science",
    "Nature Machine Intelligence", "Nature Communications", "Nature Medicine",
    "Nature Human Behaviour", "Nature Methods", "Nature Biotechnology",
    "Science Advances", "Science Robotics",
}

DEFAULT_TIMEOUT = 20.0


def _clean(text) -> str:
    if text is None:
        return ""
    return re.sub(r"<[^>]+>", "", str(text)).strip()


def _extract_openalex_work(w: dict) -> dict:
    """OpenAlex work → Zenith 论文缓存结构。"""
    title = _clean(w.get("title") or "")
    authors = ", ".join(
        _clean(a.get("author", {}).get("display_name")) for a in w.get("authorships", [])
    )
    source = w.get("primary_location") or {}
    src = source.get("source") or {}
    venue = _clean(src.get("display_name") or src.get("abbreviated_title"))
    doi = _clean(w.get("doi") or "").replace("https://doi.org/", "")

    # 全文/开放获取路径：best_oa_location → primary_location → open_access
    pdf_url = ""
    best_oa = w.get("best_oa_location") or {}
    pdf_url = _clean(best_oa.get("pdf_url") or source.get("pdf_url") or "")
    if not pdf_url:
        oa = w.get("open_access") or {}
        pdf_url = _clean(oa.get("oa_url") or "")

    # arXiv 归属：OpenAlex 无 ArXiv 字段时，从 landing page 推断
    arxiv_id = ""
    landing = _clean(source.get("landing_page_url") or "")
    if "arxiv.org" in landing:
        arxiv_id = landing.rstrip("/").split("/")[-1]

    return {
        "doi": doi,
        "title": title,
        "authors": authors,
        "venue": venue,
        "year": int(w.get("publication_year") or 0),
        "date": w.get("publication_date") or "",
        "citations": int(w.get("cited_by_count") or 0),
        "tier": "",
        "rankings": "",
        "abstract": _abstract_from_inverted(w.get("abstract_inverted_index")),
        "url": _clean(w.get("doi") or w.get("id") or ""),
        "source": "openalex",
        "region": "international",
        "venue_kind": "journal",
        "arxiv_id": arxiv_id,
        "pdf_url": pdf_url,
        "code_links": [],
    }


def _abstract_from_inverted(inv: dict | None) -> str:
    """重建 OpenAlex 倒排摘要。"""
    if not inv:
        return ""
    try:
        pos = {}
        for word, positions in inv.items():
            for p in positions:
                pos[p] = word
        return " ".join(v for _, v in sorted(pos.items()))
    except Exception:
        return ""


def _extract_crossref_work(item: dict) -> dict:
    """Crossref work → Zenith 论文缓存结构。"""
    raw_title = item.get("title")
    if isinstance(raw_title, list) and raw_title:
        title = _clean(raw_title[0])
    else:
        title = _clean(raw_title)

    authors = ", ".join(
        (_clean(a.get("given") or "") + " " + _clean(a.get("family") or "")).strip()
        for a in item.get("author", [])
    )

    venue = ""
    container = item.get("container-title")
    if isinstance(container, list) and container:
        venue = _clean(container[0])

    year = 0
    try:
        date_parts = (item.get("published") or item.get("published-print") or item.get("published-online") or {}).get("date-parts")
        if date_parts and date_parts[0] and date_parts[0][0]:
            year = int(date_parts[0][0])
    except (TypeError, ValueError, IndexError):
        year = 0

    pdf_url = ""
    arxiv_id = ""
    for link in item.get("link", []) or []:
        url = _clean(link.get("URL") or "")
        content_type = (link.get("content-type") or "").lower()
        if url.endswith(".pdf") or "pdf" in content_type:
            pdf_url = url
        if "arxiv" in url.lower():
            arxiv_id = url.rstrip("/").split("/")[-1]
    return {
        "doi": _clean(item.get("DOI") or ""),
        "title": title,
        "authors": authors,
        "venue": venue,
        "year": year,
        "date": (item.get("published") or {}).get("date-time", ""),
        "citations": int(item.get("is-referenced-by-count") or 0),
        "tier": "",
        "rankings": "",
        "abstract": _clean(item.get("abstract") or ""),
        "url": _clean(item.get("URL") or ""),
        "source": "crossref",
        "region": "international",
        "venue_kind": "journal",
        "arxiv_id": arxiv_id,
        "pdf_url": pdf_url,
        "code_links": [],
    }


def _normalize_venue_name(name: str) -> str:
    """归一化期刊名：去掉出版社括号/逗号后缀，便于正刊与子刊精确区分。"""
    return _clean(name).lower().split(" (")[0].split(",")[0].strip()


def _venue_matches(paper: dict, venue_filter: str) -> bool:
    """按期刊名过滤。

    - 单词名（Nature / Science）只精确匹配正刊，不误配子刊；
    - 多词名（如 "Nature Machine Intelligence"）精确或前缀匹配子刊。
    """
    if not venue_filter:
        return True
    vf = venue_filter.strip().lower()
    base = _normalize_venue_name(paper.get("venue") or "")
    if not base:
        return False
    if base == vf:
        return True
    if " " in vf and base.startswith(vf + " "):
        return True
    return False


# ---------------------------------------------------------------------------
# 多源题录抓取与实现路径增强（Semantic Scholar / PapersWithCode / GitHub）
# ---------------------------------------------------------------------------

_S2_FIELDS = ("title,abstract,authors,venue,year,citationCount,externalIds,"
              "openAccessPdf,url,publicationDate,publicationTypes")


def _extract_semantic_scholar_work(p: dict) -> dict:
    """Semantic Scholar paper → Zenith 论文缓存结构。"""
    authors = ", ".join(a.get("name", "") for a in p.get("authors", []))
    ext = p.get("externalIds") or {}
    oa = p.get("openAccessPdf") or {}
    doi = _clean(p.get("doi") or ext.get("DOI") or "")
    return {
        "doi": doi,
        "title": _clean(p.get("title") or ""),
        "authors": authors,
        "venue": _clean(p.get("venue") or ""),
        "year": int(p.get("year") or 0),
        "date": p.get("publicationDate") or "",
        "citations": int(p.get("citationCount") or 0),
        "tier": "",
        "rankings": "",
        "abstract": _clean(p.get("abstract") or ""),
        "url": _clean(p.get("url") or ""),
        "source": "semantic_scholar",
        "region": "international",
        "venue_kind": "journal",
        "arxiv_id": _clean(ext.get("ArXiv") or ""),
        "pdf_url": _clean(oa.get("url") or ""),
        "code_links": [],
    }


async def _search_semantic_scholar(query: str, venue: str, limit: int,
                                   from_date: str = "", to_date: str = "") -> list:
    params = {"query": query, "limit": str(min(limit * 2, 20)), "fields": _S2_FIELDS}
    # S2 支持 year 范围粗过滤（如 year=2024-2024）
    y_from = (from_date or "")[:4]
    y_to = (to_date or "")[:4]
    if y_from and y_to:
        params["year"] = f"{y_from}-{y_to}"
    elif y_from:
        params["year"] = f"{y_from}-"
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, follow_redirects=True) as client:
        r = await client.get(f"{S2_BASE}/paper/search", params=params)
        r.raise_for_status()
        data = r.json()
    papers = []
    for p in data.get("data", []) or []:
        paper = _extract_semantic_scholar_work(p)
        # 客户端精确日期过滤（S2 year 参数是粗过滤，此处兜底）
        pub = (paper.get("date") or "")[:10]
        if from_date and pub and pub < from_date:
            continue
        if to_date and pub and pub > to_date:
            continue
        if _venue_matches(paper, venue):
            papers.append(paper)
    return papers[:limit]


async def _lookup_semantic_scholar(doi: str) -> dict | None:
    params = {"fields": _S2_FIELDS}
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, follow_redirects=True) as client:
        r = await client.get(f"{S2_BASE}/paper/DOI:{doi}", params=params)
        if r.status_code != 200:
            return None
        return _extract_semantic_scholar_work(r.json())


async def _search_paperswithcode(paper: dict) -> list:
    """按 arxiv_id 或标题从 PapersWithCode 找官方实现。返回 code_links。"""
    arxiv_id = (paper.get("arxiv_id") or "").strip()
    title = (paper.get("title") or "").strip()
    if not arxiv_id and not title:
        return []
    params = {"arxiv_id": arxiv_id} if arxiv_id else {"title": title}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            r = await client.get(f"{PWC_BASE}/papers/", params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        logger.debug("PapersWithCode 查询失败: %s", e)
        return []

    links: list[dict] = []
    for item in data.get("results", []) or []:
        if isinstance(item, dict):
            for key in ("repository_url", "repository_urls", "paper_url", "url"):
                v = item.get(key)
                if isinstance(v, str) and v.startswith("http"):
                    links.append({"name": "PapersWithCode", "url": v, "source": "paperswithcode"})
                elif isinstance(v, list):
                    for u in v:
                        if isinstance(u, str) and u.startswith("http"):
                            links.append({"name": "PapersWithCode", "url": u, "source": "paperswithcode"})
    return links


async def _search_github_repos(paper: dict) -> list:
    """按 arXiv ID / 标题 从 GitHub 搜索实现仓库。"""
    query = (paper.get("arxiv_id") or "").strip() or (paper.get("title") or "").strip()
    if not query:
        return []
    params = {"q": query, "per_page": 5}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            r = await client.get(f"{GITHUB_BASE}/search/repositories", params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        logger.debug("GitHub 仓库搜索失败: %s", e)
        return []

    links: list[dict] = []
    for item in data.get("items", []) or []:
        if isinstance(item, dict):
            full = item.get("full_name") or ""
            url = item.get("html_url") or ""
            desc = _clean(item.get("description") or "")
            if url:
                links.append({"name": full or url, "url": url, "source": "github", "description": desc[:160]})
    return links


async def _enrich_paper(paper: dict) -> dict:
    """为单篇论文补充 arxiv / pdf / code 实现路径。失败不阻断。"""
    if not paper.get("arxiv_id") and paper.get("doi"):
        try:
            s2 = await _lookup_semantic_scholar(paper["doi"])
            if s2:
                # 注意：_extract_* 已返回空字符串 key，setdefault 永不生效，须用 if not 覆盖
                if not paper.get("arxiv_id"):
                    paper["arxiv_id"] = s2.get("arxiv_id") or ""
                if not paper.get("pdf_url"):
                    paper["pdf_url"] = s2.get("pdf_url") or ""
                if not paper.get("abstract"):
                    paper["abstract"] = s2.get("abstract") or ""
                if not paper.get("citations"):
                    paper["citations"] = s2.get("citations") or paper.get("citations") or 0
        except Exception as e:
            logger.debug("Semantic Scholar DOI 增强失败: %s", e)

    pwc_links, gh_links = await asyncio.gather(
        _search_paperswithcode(paper),
        _search_github_repos(paper),
        return_exceptions=True,
    )
    links = []
    for part in (pwc_links, gh_links):
        if isinstance(part, list):
            links.extend(part)
    # 去重
    seen, unique = set(), []
    for lk in links:
        url = lk.get("url", "")
        if url and url not in seen:
            seen.add(url)
            unique.append(lk)
    paper["code_links"] = unique[:8]
    return paper




async def search_papers(query: str, from_date: str = "", to_date: str = "",
                        venue: str = "", limit: int = 10, store: bool = True) -> dict:
    """检索学术论文并写入本地缓存。

    Args:
        query: 检索词
        from_date / to_date: YYYY-MM-DD
        venue: Nature / Science / 其它期刊名（可选）
        limit: 返回条数
        store: 是否写入 SQLite（默认 True，本地嵌入）
    """
    query = (query or "").strip()
    if not query:
        return {"success": False, "error": "query 不能为空", "papers": []}

    oa_result, cr_result, s2_result = await asyncio.gather(
        _search_openalex(query, from_date, to_date, venue, limit),
        _search_crossref(query, from_date, to_date, venue, limit),
        _search_semantic_scholar(query, venue, limit, from_date, to_date),
        return_exceptions=True,
    )
    papers: list = []
    errors: list = []
    for label, result in (("openalex", oa_result), ("crossref", cr_result), ("semantic_scholar", s2_result)):
        if isinstance(result, BaseException):
            errors.append(f"{label}: {result}")
        elif result:
            papers.extend(result or [])

    merged = _merge_papers(papers, limit)

    # 为 Top 3 抓取实现路径（arxiv / pdf / code），避免触发 GitHub 未认证限流
    enrich_top = min(3, len(merged))
    if enrich_top:
        enriched = await asyncio.gather(
            *(_enrich_paper(p) for p in merged[:enrich_top]),
            return_exceptions=True,
        )
        for i, res in enumerate(enriched):
            if isinstance(res, dict):
                merged[i] = res

    if store:
        for p in merged:
            try:
                db.academic_paper_upsert(p)
            except Exception as e:
                logger.warning("academic_paper_upsert failed: %s", e)

    return {
        "success": True,
        "query": query,
        "count": len(merged),
        "papers": merged,
        "errors": errors or [],
        "cached": True if store else False,
        "sources": ["openalex", "crossref", "semantic_scholar", "paperswithcode", "github"],
    }


async def _search_openalex(query: str, from_date: str, to_date: str, venue: str, limit: int) -> list:
    # 指定 venue 时扩大候选量（客户端精确过滤），提高正刊召回
    per_page = min(max(limit * 4, 50), 200) if venue else min(limit * 2, 25)
    params = {
        "search": query,
        "per-page": str(per_page),
        "mailto": "zenith@local",
    }
    filters = []
    if from_date:
        filters.append(f"from_publication_date:{from_date}")
    if to_date:
        filters.append(f"to_publication_date:{to_date}")
    if filters:
        params["filter"] = ",".join(filters)
    papers = []
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, follow_redirects=True) as client:
        r = await client.get(OPENALEX_WORKS, params=params)
        r.raise_for_status()
        data = r.json()
        for w in data.get("results", []):
            p = _extract_openalex_work(w)
            if _venue_matches(p, venue):
                papers.append(p)
    return papers[:limit]


async def _search_crossref(query: str, from_date: str, to_date: str, venue: str, limit: int) -> list:
    rows = min(max(limit * 4, 50), 100) if venue else min(limit * 2, 25)
    params = {
        "query": query,
        "rows": str(rows),
    }
    filters = []
    if from_date:
        filters.append(f"from-pub-date:{from_date}")
    if to_date:
        filters.append(f"until-pub-date:{to_date}")
    if filters:
        params["filter"] = ",".join(filters)
    papers = []
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, follow_redirects=True) as client:
        r = await client.get(CROSSREF_WORKS, params=params)
        r.raise_for_status()
        data = r.json()
        for item in data.get("message", {}).get("items", []):
            p = _extract_crossref_work(item)
            if _venue_matches(p, venue):
                papers.append(p)
    return papers[:limit]


def _merge_papers(papers: list, limit: int) -> list:
    """按 DOI 去重，保留 citations 更高者；返回前 limit 条。"""
    merged: dict[str, dict] = {}
    for p in papers:
        key = (p.get("doi") or p.get("title") or "").lower().strip()
        if not key:
            continue
        old = merged.get(key)
        if old is None or int(p.get("citations") or 0) > int(old.get("citations") or 0):
            merged[key] = p
    out = sorted(merged.values(), key=lambda x: -(int(x.get("citations") or 0)))[:limit]
    return out


async def lookup_doi(doi: str, store: bool = True) -> dict:
    """按 DOI 获取题录（OpenAlex 优先，Crossref 回退），并写入本地缓存。"""
    doi = (doi or "").strip().replace("https://doi.org/", "")
    if not doi:
        return {"success": False, "error": "doi 不能为空", "paper": None}

    # 先查本地缓存
    cached = db.academic_paper_get_by_doi(doi)
    if cached:
        return {"success": True, "paper": cached, "cached": True}

    paper = None
    errors = []
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, follow_redirects=True) as client:
            r = await client.get(f"{OPENALEX_WORKS}/doi:{doi}")
            if r.status_code == 200:
                paper = _extract_openalex_work(r.json())
            else:
                errors.append(f"openalex {r.status_code}")
    except Exception as e:
        errors.append(f"openalex: {e}")

    if paper is None:
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, follow_redirects=True) as client:
                r = await client.get(f"{CROSSREF_WORKS}/{doi}")
                if r.status_code == 200:
                    paper = _extract_crossref_work(r.json()["message"])
                else:
                    errors.append(f"crossref {r.status_code}")
        except Exception as e:
            errors.append(f"crossref: {e}")

    if paper is None:
        try:
            paper = await _lookup_semantic_scholar(doi)
            if paper is None:
                errors.append("semantic_scholar 404")
        except Exception as e:
            errors.append(f"semantic_scholar: {e}")

    if paper is None:
        return {"success": False, "error": "未找到该 DOI 的题录", "errors": errors, "paper": None}

    # 补充实现路径：arXiv / PDF / 代码仓库（多源，失败不阻断）
    try:
        paper = await _enrich_paper(paper)
    except Exception as e:
        errors.append(f"enrich: {e}")

    pid = db.academic_paper_upsert(paper) if store else 0
    paper["id"] = pid
    return {"success": True, "paper": paper, "cached": False, "errors": errors}


def _normalize_local_paper(p: dict) -> dict:
    """把本地 SQLite 行中的 code_links 字符串解析为列表。"""
    if isinstance(p.get("code_links"), str):
        try:
            p["code_links"] = json.loads(p["code_links"] or "[]")
        except (json.JSONDecodeError, TypeError):
            p["code_links"] = []
    return p


def search_local(query: str = "", venue: str = "", year_from: int = 0,
                 year_to: int = 0, limit: int = 20, offset: int = 0) -> dict:
    """检索本地 SQLite 学术论文缓存。"""
    papers = db.academic_paper_search(query=query, venue=venue, year_from=year_from,
                                      year_to=year_to, limit=limit, offset=offset)
    papers = [_normalize_local_paper(p) for p in papers]
    stats = db.academic_paper_stats()
    return {"success": True, "count": len(papers), "papers": papers, "stats": stats}


def _format_paper(p: dict, idx: int = 0) -> str:
    lines = [
        f"#{idx + 1} {p.get('title', '')}",
        f"   作者: {p.get('authors') or 'N/A'}",
        f"   期刊/会议: {p.get('venue') or 'N/A'}",
        f"   年份: {p.get('year') or 'N/A'} | 引用: {p.get('citations') or 0}",
    ]
    if p.get("doi"):
        lines.append(f"   DOI: {p.get('doi')}")
    if p.get("arxiv_id"):
        lines.append(f"   arXiv: {p.get('arxiv_id')}")
    if p.get("abstract"):
        lines.append(f"   摘要: {p['abstract'][:300]}")
    if p.get("pdf_url"):
        lines.append(f"   PDF: {p.get('pdf_url')}")
    code_links = p.get("code_links") or []
    if code_links:
        lines.append("   实现路径:")
        for lk in code_links[:5]:
            name = lk.get("name") or lk.get("url") or "code"
            url = lk.get("url") or ""
            desc = lk.get("description") or ""
            lines.append(f"     - [{name}] {url}" + (f" — {desc}" if desc else ""))
    if p.get("url"):
        lines.append(f"   链接: {p.get('url')}")
    return "\n".join(lines)


def format_papers(papers: list, top: int = 10) -> str:
    if not papers:
        return "未找到相关论文。"
    top = max(1, min(int(top), len(papers)))
    return "\n\n".join(_format_paper(p, i) for i, p in enumerate(papers[:top]))
