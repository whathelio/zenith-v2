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
        return {"success": False, "result": f"网页返回错误状态码 ({e.response.status_code})"}
    except httpx.RequestError as e:
        return {"success": False, "result": f"请求失败: {e}"}

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


async def web_search(query: str, max_results: int = 5) -> dict:
    """用 Bing 网页搜索（免 API Key），返回结果列表。

    返回:
        {"success": True, "result": "格式化结果", "results": [...]}
    """
    if not query.strip():
        return {"success": False, "result": "搜索关键词不能为空"}

    # 搜索词过长时精简：取前 5 个词，避免长尾查询干扰 Bing 结果质量
    query = query.strip()
    if len(query) > 30:
        words = query.split()
        if len(words) > 5:
            query = " ".join(words[:5])
            logger.info("Search query trimmed to: %s", query)

    # 多抓一些再截断，规避 Bing 结果数不稳定
    search_url = f"https://www.bing.com/search?q={quote_plus(query)}&count={max_results * 2}"
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(search_url, headers=HEADERS)
            resp.raise_for_status()
            html = resp.text
    except httpx.RequestError as e:
        return {"success": False, "result": f"搜索请求失败: {e}"}

    try:
        soup = _get_soup(html)
    except ImportError:
        return {"success": False,
                "result": "缺少 beautifulsoup4 依赖，请在项目根目录执行: pip install beautifulsoup4"}

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

    if not results:
        return {"success": True,
                "result": f"未找到关于「{query}」的搜索结果（可能被搜索引擎拦截，稍后再试）。"}

    # URL 去重
    seen_urls = set()
    deduped = []
    for r in results:
        if r["url"] not in seen_urls:
            seen_urls.add(r["url"])
            deduped.append(r)
    results = deduped

    lines = [f"🔍 搜索「{query}」(共 {len(results)} 条)："]
    for i, r in enumerate(results, 1):
        lines.append(f"\n{i}. {r['title']}")
        lines.append(f"   链接: {r['url']}")
        if r["snippet"]:
            lines.append(f"   摘要: {r['snippet']}")
    return {"success": True, "result": "\n".join(lines), "results": results}
