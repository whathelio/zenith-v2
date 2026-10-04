"""Zenith v2 网页工具 — 抓取网页正文 + 网页搜索（免 API Key）

- fetch_url: 抓取任意 http/https 链接，HTML → 可读文本，喂给模型
- web_search: 用 Bing 网页搜索（无需 Key），返回标题/链接/摘要

依赖: httpx (已有) + beautifulsoup4
"""
from __future__ import annotations

import re
import logging
from urllib.parse import quote_plus, urlparse

import httpx

logger = logging.getLogger("zenith.web")

# 模拟浏览器请求头，降低被反爬拦截概率
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 抓取正文时丢弃的噪声标签
NOISE_TAGS = ["script", "style", "noscript", "nav", "footer", "header",
              "aside", "form", "iframe", "svg", "button", "figure"]

# JS 动态渲染的 SPA 站点 — 静态抓取只能拿到页面骨架，实际内容需 JS 执行
SPA_HOSTS = ("bilibili.com", "github.com", "zhihu.com", "weibo.com",
             "douyin.com", "xiaohongshu.com", "juejin.cn", "csdn.net")

# 抓取失败时的替代路径提示：避免模型只说一句「没读到」就无下一步
_SPA_FALLBACK = (
    "该站点为动态渲染（JS 加载）+ 反爬，静态抓取通常拿不到正文。建议改用以下路径：\n"
    "  1) web_search 检索同一主题的关键词，从搜索结果的标题/摘要中获取信息；\n"
    "  2) 若你能提供该页面正文（或关键段落），我直接基于正文作答；\n"
    "  3) 也可试试 analyze_content（页面可被其抓取时可用）。"
)
_GENERIC_FALLBACK = (
    "建议改用以下路径：\n"
    "  1) web_search 检索同一主题的关键词，从搜索结果的标题/摘要中获取信息；\n"
    "  2) 或者把该页面正文（或关键段落）粘贴给我，我直接基于正文作答。"
)


def _fetch_failure_hint(url: str, base: str) -> str:
    """抓取失败时，在原始错误说明后追加「下一步怎么走」的替代路径提示。

    SPA / 反爬站点给出更强提示（静态抓取基本无解），其它站点给通用建议。
    """
    host = urlparse(url).hostname or ""
    if any(h in host for h in SPA_HOSTS):
        return f"{base}\n\n⚠️ {_SPA_FALLBACK}"
    return f"{base}\n\n💡 {_GENERIC_FALLBACK}"


def _get_soup(html: str):
    """延迟导入 BeautifulSoup，避免未安装时影响其它模块加载"""
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser")


async def fetch_url(url: str, max_chars: int = 8000) -> dict:
    """抓取网页并提取正文文本。

    返回:
        {"success": True, "result": "可读文本", "url": final_url, "title": ...}
        或 {"success": False, "result": "错误说明"}
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"success": False, "result": f"仅支持 http/https 链接，收到: {url}"}
    if not parsed.netloc:
        return {"success": False, "result": f"无效的链接: {url}"}

    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=HEADERS)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "").lower()
            final_url = str(resp.url)
            html = resp.text
    except httpx.HTTPStatusError as e:
        return {"success": False,
                "result": _fetch_failure_hint(
                    url, f"网页返回错误状态码 ({e.response.status_code})")}
    except httpx.RequestError as e:
        return {"success": False,
                "result": _fetch_failure_hint(url, f"请求失败: {e}")}

    # 非 HTML 内容（如纯文本 / JSON / RSS）直接截断返回
    if "text/html" not in content_type and "application/xhtml" not in content_type:
        text = html.strip()
        if len(text) > max_chars:
            text = text[:max_chars] + "\n...(内容已截断)"
        return {"success": True,
                "result": f"[非HTML内容 {content_type}]\n{text}",
                "url": final_url, "title": ""}

    try:
        soup = _get_soup(html)
    except ImportError:
        return {"success": False,
                "result": "缺少 beautifulsoup4 依赖，请在项目根目录执行: pip install beautifulsoup4"}

    # 移除噪声节点
    for tag in soup(NOISE_TAGS):
        tag.decompose()

    title = soup.title.string.strip() if soup.title and soup.title.string else ""

    # 优先 <main> / <article>，回退整页
    main = soup.find("main") or soup.find("article") or soup
    text = _extract_text(main)

    # 压缩多余空行
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...(内容已截断)"

    header = (f"标题: {title}\n链接: {final_url}\n\n"
              if title else f"链接: {final_url}\n\n")

    # SPA 站点提示：静态抓取拿不到 JS 渲染的实际内容
    host = urlparse(final_url).hostname or ""
    spa_hint = ""
    if any(h in host for h in SPA_HOSTS):
        spa_hint = ("\n\n⚠️ 此页面为动态渲染页面，静态抓取仅获取页面骨架，"
                    "实际内容需 JavaScript 加载。如需完整内容摘要，建议改用 analyze_content 工具。")

    return {"success": True, "result": header + text + spa_hint,
            "url": final_url, "title": title}


def _extract_text(node) -> str:
    """从 BeautifulSoup 节点提取带结构的纯文本"""
    lines = []
    for el in node.find_all(
        ["h1", "h2", "h3", "h4", "h5", "p", "li", "td", "th", "pre", "blockquote"]
    ):
        txt = el.get_text(" ", strip=True)
        if not txt:
            continue
        if el.name in ("h1", "h2", "h3", "h4", "h5"):
            lines.append(f"\n## {txt}")
        elif el.name == "li":
            lines.append(f"- {txt}")
        else:
            lines.append(txt)
    if not lines:
        return node.get_text("\n", strip=True)
    return "\n".join(lines)


# --- 搜索词净化 / 结果相关性判定 ---
# 长数字串（如知乎 19 位回答 ID）是纯噪声：Bing 会把它当一个无意义词元，
# 极易把结果带偏到反爬页或无关页
_DIGIT_NOISE = re.compile(r"\d{6,}")
_WS_RUN = re.compile(r"\s+")
_CJK_SEG = re.compile(r"[\u4e00-\u9fff]+")
_ASCII_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+#._-]*")


def _sanitize_query(query: str) -> str:
    """净化搜索词：剔除长数字串与长度 < 2 的噪声词，压缩多余空白。

    原实现是「长度 > 30 且按空格分词 > 5 个词 → 取前 5 词」，对中文无效
    （中文没有空格分词），还会把「知乎 2083123101873844765」里的长数字 ID
    当成有效词送进搜索引擎。
    净化后若为空则原样回退——绝不因净化失败而搜空。
    """
    original = query.strip()
    tokens = [t for t in _WS_RUN.split(_DIGIT_NOISE.sub(" ", original)) if t]
    tokens = [t for t in tokens if len(t) >= 2]
    cleaned = _WS_RUN.sub(" ", " ".join(tokens)).strip()
    return cleaned or original


def _query_units(query: str) -> tuple[set[str], list[str]]:
    """把查询拆成「中文 2-gram 集合」+「ASCII 词元列表」，作为相关性判定单元。"""
    flat = _WS_RUN.sub("", query)
    grams: set[str] = set()
    for seg in _CJK_SEG.findall(flat):
        for i in range(len(seg) - 1):
            grams.add(seg[i:i + 2])
    return grams, [t.lower() for t in _ASCII_TOKEN.findall(query)]


def _match_score(units: tuple[set[str], list[str]], result: dict) -> int:
    """结果命中查询的单元数（中文 2-gram 命中数 + ASCII 词元命中数）。"""
    grams, ascii_tokens = units
    title = result.get("title", "") or ""
    snippet = result.get("snippet", "") or ""
    # Bing 会在标题里给命中词插空格（「凯恩斯 主义」），中文按去空白后的文本匹配
    flat = _WS_RUN.sub("", title + snippet)
    score = sum(1 for g in grams if g in flat)
    for tok in ascii_tokens:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(tok)}(?![A-Za-z0-9])",
                     f"{title} {snippet}", re.IGNORECASE):
            score += 1
    return score


def _select_relevant(query: str, results: list[dict]) -> tuple[list[dict], bool]:
    """过滤与查询明显无关的结果。

    单字撞库（搜「孙正义」返回「孙 姓_百度百科」）只命中单个汉字，构不成任何
    2-gram、也没有 ASCII 词元命中，会被剔除。
    返回 (保留的结果, 是否因过滤后为空而回退到原始结果)。
    """
    if not results:
        return results, False
    units = _query_units(query)
    if not units[0] and not units[1]:
        return results, False  # 查询里没有可判定的单元，不做过滤
    kept = [r for r in results if _match_score(units, r) >= 1]
    if not kept:
        logger.info("相关性过滤会清空结果，保留原始 %d 条: %s", len(results), query)
        return results, True
    if len(kept) < len(results):
        logger.info("相关性过滤 %d -> %d 条: %s", len(results), len(kept), query)
    return kept, False


def _fallback_query(query: str) -> str:
    """降级搜索词：取首个有效词（通常是主实体），用于整体无关时重搜一次。"""
    for tok in query.split():
        if len(tok) >= 2:
            return tok
    return ""


async def _bing_results(query: str, max_results: int) -> tuple[list[dict], str]:
    """抓取 Bing 结果列表。返回 (结果列表, 错误说明)；错误说明非空时结果为空。"""
    # 多抓一些再截断，规避 Bing 结果数不稳定
    search_url = f"https://www.bing.com/search?q={quote_plus(query)}&count={max_results * 2}"
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(search_url, headers=HEADERS)
            resp.raise_for_status()
            html = resp.text
    except httpx.RequestError as e:
        return [], f"搜索请求失败: {e}"

    try:
        soup = _get_soup(html)
    except ImportError:
        return [], "缺少 beautifulsoup4 依赖，请在项目根目录执行: pip install beautifulsoup4"

    results = []
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a")
        if not a:
            continue
        href = a.get("href", "")
        title = a.get_text(" ", strip=True)
        snippet_el = li.select_one(".b_caption p") or li.select_one("p")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        if title and href:
            results.append({"title": title, "url": href, "snippet": snippet})
        if len(results) >= max_results:
            break

    if not results:
        # Bing 偶尔会调整结构，回退一次：抓所有 h2>a
        for a in soup.select("h2 a")[:max_results]:
            href = a.get("href", "")
            title = a.get_text(" ", strip=True)
            if title and href and href.startswith("http"):
                results.append({"title": title, "url": href, "snippet": ""})

    # URL 去重
    seen_urls = set()
    deduped = []
    for r in results:
        if r["url"] not in seen_urls:
            seen_urls.add(r["url"])
            deduped.append(r)
    return deduped, ""


async def web_search(query: str, max_results: int = 5) -> dict:
    """用 Bing 网页搜索（免 API Key），返回结果列表。

    返回:
        {"success": True, "result": "格式化结果", "results": [...]}
    """
    if not query.strip():
        return {"success": False, "result": "搜索关键词不能为空"}

    query = _sanitize_query(query)
    results, err = await _bing_results(query, max_results)
    if err:
        return {"success": False, "result": err}
    if not results:
        return {"success": True,
                "result": f"未找到关于「{query}」的搜索结果（可能被搜索引擎拦截，稍后再试）。"}

    retry_note = ""
    results, knocked_out = _select_relevant(query, results)
    if knocked_out:
        # 原始结果与查询整体无关（单字撞库等）：用首个关键词降级重搜一次。
        # 重搜仍拿不到相关结果时保留原始结果——宁可不滤，也不把结果清空。
        alt = _fallback_query(query)
        if alt and alt != query:
            alt_results, alt_err = await _bing_results(alt, max_results)
            if not alt_err and alt_results:
                alt_kept, alt_knocked_out = _select_relevant(alt, alt_results)
                if not alt_knocked_out:
                    retry_note = (f"（原查询「{query}」结果全部与主题无关，"
                                  f"已改用「{alt}」重搜）")
                    query, results = alt, alt_kept
                else:
                    logger.info("降级重搜「%s」仍全部无关，保留原始结果", alt)

    lines = [retry_note] if retry_note else []
    lines.append(f"🔍 搜索「{query}」(共 {len(results)} 条)：")
    for i, r in enumerate(results, 1):
        lines.append(f"\n{i}. {r['title']}")
        lines.append(f"   链接: {r['url']}")
        if r["snippet"]:
            lines.append(f"   摘要: {r['snippet']}")
    return {"success": True, "result": "\n".join(lines), "results": results}
