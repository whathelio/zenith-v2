"""Zenith v2 网页工具 — 抓取网页正文 + 网页搜索

双轨 provider（2026-10-21）：
- DeepSeek 官方 Search / Fetch（`backend/web_deepseek.py`）：结构化结果、服务端渲染抓取
- 本地兜底：Bing HTML 解析 + httpx/BeautifulSoup 直抓（免 Key，不依赖配额）

对外仍是两个函数：
- fetch_url: 抓取任意 http/https 链接 → 可读文本 / markdown
- web_search: 网页搜索 → 标题/链接/摘要

provider 由环境变量决定（见 `_resolve_provider`）：
- ZENITH_WEB_SEARCH_PROVIDER = auto | deepseek | bing   （auto=DeepSeek 优先，失败回退 Bing）
- ZENITH_WEB_FETCH_PROVIDER  = auto | deepseek | local  （auto=DeepSeek 优先，失败回退直抓）
- ZENITH_WEB_DAILY_LIMIT     = 每日 DeepSeek 调用上限（0=不限，默认 500）
- ZENITH_WEB_SEARCH_CACHE_TTL / ZENITH_WEB_FETCH_CACHE_TTL = 进程内缓存秒数（0=关）
- 测试环境（ZENITH_TESTING=1）强制走本地实现，绝不打真网/花配额

依赖: httpx (已有) + beautifulsoup4
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import time
from pathlib import Path
from urllib.parse import quote_plus, urlparse

import httpx

from . import web_deepseek as ds

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


# ---------------------------------------------------------------------------
# provider 选择 / 缓存 / 日限 / 记账
# ---------------------------------------------------------------------------

_PROVIDER_VALUES = {
    "search": {"auto", "deepseek", "bing"},
    "fetch": {"auto", "deepseek", "local"},
}


def _testing() -> bool:
    return os.environ.get("ZENITH_TESTING") == "1"


def _resolve_provider(kind: str, explicit: str | None = None) -> str:
    """决定这次用哪个 provider。显式入参 > 环境变量 > auto。

    测试环境（ZENITH_TESTING=1）强制本地：pytest 里调 web_search/fetch_url
    不该打真网、更不该花真配额；需要测 DeepSeek 分支的用例直接调
    web_deepseek.ds_* 或显式传 provider="deepseek"。
    """
    if explicit:
        val = explicit.strip().lower()
        if val in _PROVIDER_VALUES[kind]:
            return val
    if _testing():
        return "bing" if kind == "search" else "local"
    env_name = "ZENITH_WEB_SEARCH_PROVIDER" if kind == "search" else "ZENITH_WEB_FETCH_PROVIDER"
    val = (os.environ.get(env_name) or "auto").strip().lower()
    return val if val in _PROVIDER_VALUES[kind] else "auto"


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


# 进程内 TTL 缓存：同一 query/url 在 TTL 内不重复计费。0 = 关闭。
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_MAX_ENTRIES = 200


def _cache_ttl(kind: str) -> int:
    # 测试环境关闭缓存：否则同 query 的用例会互相污染（前一个用例的结果被后一个读到）
    if _testing():
        return 0
    name = "ZENITH_WEB_SEARCH_CACHE_TTL" if kind == "search" else "ZENITH_WEB_FETCH_CACHE_TTL"
    return _env_int(name, 300 if kind == "search" else 600)


def _cache_key(kind: str, *parts) -> str:
    return kind + "|" + "|".join(str(p) for p in parts)


def _cache_get(key: str) -> dict | None:
    item = _CACHE.get(key)
    if not item:
        return None
    ts, value = item
    if time.time() - ts > _cache_ttl(key.split("|", 1)[0]):
        _CACHE.pop(key, None)
        return None
    return copy.deepcopy(value)


def _cache_put(key: str, value: dict) -> None:
    if _cache_ttl(key.split("|", 1)[0]) <= 0:
        return
    if len(_CACHE) >= _CACHE_MAX_ENTRIES:
        oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
        _CACHE.pop(oldest, None)
    _CACHE[key] = (time.time(), copy.deepcopy(value))


_USAGE_PATH = Path(__file__).resolve().parent.parent / "data" / "web_calls.jsonl"
_usage_state: dict = {"date": "", "counts": {}}


def _ensure_usage_loaded() -> None:
    """按天懒加载调用计数（首次使用时扫一次当日 JSONL）。"""
    today = time.strftime("%Y-%m-%d")
    if _usage_state["date"] == today:
        return
    counts: dict[str, int] = {}
    if _USAGE_PATH.exists():
        try:
            with open(_USAGE_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    if today not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if rec.get("date") == today and rec.get("ok"):
                        api = rec.get("api") or ""
                        counts[api] = counts.get(api, 0) + 1
        except OSError as e:  # 记账文件读不了不该阻塞抓取
            logger.debug("读取 web_calls.jsonl 失败: %s", e)
    _usage_state["date"] = today
    _usage_state["counts"] = counts


def _over_daily_limit(api: str) -> bool:
    limit = _env_int("ZENITH_WEB_DAILY_LIMIT", 500)
    if limit <= 0:
        return False
    _ensure_usage_loaded()
    return _usage_state["counts"].get(api, 0) >= limit


def _record_call(api: str, ok: bool, status: int, ms: float, target: str, chars: int = 0) -> None:
    """记账：一行一条，失败不影响主流程。"""
    if _testing():
        # 测试环境不落盘：否则用例会把假调用写进真实日限计数
        return
    today = time.strftime("%Y-%m-%d")
    rec = {
        "date": today,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "api": api,
        "ok": bool(ok),
        "status": int(status or 0),
        "ms": round(ms, 1),
        "chars": int(chars),
        "target": (target or "")[:120],
        # 官方价：¥5/1000 次成功请求 → 0.005 元/次
        "est_cost_cny": 0.005 if ok else 0.0,
    }
    try:
        _USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_USAGE_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.debug("写 web_calls.jsonl 失败: %s", e)
    if ok:
        _ensure_usage_loaded()
        _usage_state["counts"][api] = _usage_state["counts"].get(api, 0) + 1


def _join_failures(failures: list[str]) -> str:
    """把各 provider 的失败说明合成一条可执行提示。"""
    clean = [f for f in failures if f]
    if not clean:
        return "网页接口全部不可用，且没有拿到可用内容。"
    if len(clean) == 1:
        return clean[0]
    return "DeepSeek 接口失败：\n" + clean[0] + "\n\n本地兜底也失败：\n" + clean[-1]



async def _fetch_local(url: str, max_chars: int = 8000) -> dict:
    """本地直抓：httpx GET + BeautifulSoup 提正文（免 Key 的兜底路径）。

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


async def fetch_url(url: str, max_chars: int = 8000, provider: str | None = None) -> dict:
    """抓取网页正文：DeepSeek Fetch 优先，失败自动回退本地直抓。

    provider: None=按环境变量（默认 auto）/ "deepseek" / "local" / "auto"
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"success": False, "result": f"仅支持 http/https 链接，收到: {url}"}
    if not parsed.netloc:
        return {"success": False, "result": f"无效的链接: {url}"}

    chosen = _resolve_provider("fetch", provider)
    chain = ["deepseek", "local"] if chosen == "auto" else [chosen]
    cache_key = _cache_key("fetch", url, max_chars)

    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    failures: list[str] = []
    for p in chain:
        if p == "deepseek":
            if not ds.available():
                failures.append("DeepSeek 未配置 API Key")
                continue
            if _over_daily_limit("fetch"):
                failures.append("DeepSeek 抓取已达今日调用上限（ZENITH_WEB_DAILY_LIMIT）")
                continue
            t0 = time.perf_counter()
            out = await ds.ds_fetch(url, max_chars=max_chars)
            _record_call("fetch", out.get("success", False), out.get("status") or (200 if out.get("success") else 0),
                         (time.perf_counter() - t0) * 1000, url, len(out.get("content") or ""))
            if out.get("success"):
                res = {
                    "success": True,
                    "result": out["result"],
                    "url": out.get("url", url),
                    "title": out.get("title", ""),
                    "provider": "deepseek",
                    "truncated": out.get("truncated", False),
                }
                _cache_put(cache_key, res)
                return res
            failures.append(out.get("result", ""))
            logger.info("DeepSeek fetch 失败，回退本地: %s | %s",
                        url[:80], str(out.get("result", ""))[:120])
            continue

        out = await _fetch_local(url, max_chars)
        if out.get("success"):
            if failures:
                out["result"] = (f"（DeepSeek 抓取失败：{failures[0][:80]}；已回退本地直抓）\n"
                                 + out["result"])
            out["provider"] = "local"
            _cache_put(cache_key, out)
            return out
        failures.append(out.get("result", ""))

    return {"success": False, "result": _join_failures(failures), "provider": chosen}


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


_FALLBACK_CJK_CHARS = 4


def _fallback_query(query: str) -> str:
    """降级搜索词：取首个有效词（通常是主实体），用于整体无关时重搜一次。

    多词查询按空白分词取首个；无空格中文查询不能交给 split()——整句会被当成
    一个词原样返回、与入参相等，使调用点 ``alt != query`` 守卫恒假、降级重搜
    形同失效。此时改取首个中文段的前若干字，得到一个更宽泛的主实体。
    """
    parts = [t for t in query.split() if len(t) >= 2]
    if len(parts) >= 2:
        return parts[0]
    seg_match = _CJK_SEG.search(query)
    if seg_match is not None:
        seg = seg_match.group(0)
        if len(seg) > _FALLBACK_CJK_CHARS:
            return seg[:_FALLBACK_CJK_CHARS]
    return parts[0] if parts else ""


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


async def _search_bing(query: str, max_results: int = 5) -> dict:
    """本地兜底搜索：Bing HTML + 相关性过滤（免 API Key）。

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


# ---------------------------------------------------------------------------
# DeepSeek 结果格式化 + 对外 web_search（双轨调度）
# ---------------------------------------------------------------------------

_SUMMARY_MAX = 600   # 每条摘要上限：官方 summary 600~800 字，全量喂会白烧 token
_CONTENT_MAX = 2000  # need_content=True 时每条正文上限

_FULLTEXT_HINT = "\n💡 需要某条全文时，对该条链接调用 web_fetch。"


def _format_ds_results(query: str, results: list[dict]) -> str:
    """把 DeepSeek 结构化结果排成模型好读的块：日期/来源/权威/相关度都给。"""
    lines = [f"🔍 搜索「{query}」(DeepSeek, {len(results)} 条)："]
    for i, r in enumerate(results, 1):
        lines.append(f"\n{i}. {r['title']}")
        lines.append(f"   链接: {r['url']}")
        meta = []
        if r.get("date"):
            meta.append(str(r["date"])[:10])
        if r.get("site_name"):
            meta.append(str(r["site_name"]))
        authority = r.get("authority")
        if isinstance(authority, int) and authority >= 0:
            meta.append(f"权威 {authority}/4")
        score = r.get("score")
        if isinstance(score, (int, float)) and score:
            meta.append(f"相关 {score:.2f}")
        if meta:
            lines.append("   " + " · ".join(meta))
        body = (r.get("summary") or r.get("snippet") or "").strip()
        if body:
            lines.append(f"   摘要: {body[:_SUMMARY_MAX]}")
        if r.get("content"):
            lines.append(f"   正文: {r['content'][:_CONTENT_MAX]}")
    lines.append(_FULLTEXT_HINT)
    return "\n".join(lines)


async def web_search(
    query: str,
    max_results: int = 5,
    provider: str | None = None,
    need_content: bool = False,
    ori_question: str | None = None,
) -> dict:
    """联网搜索：DeepSeek Search 优先，失败自动回退 Bing。

    provider: None=按环境变量（默认 auto）/ "deepseek" / "bing" / "auto"
    need_content: 让接口直接返回网页正文（token 更贵，默认关）
    ori_question: 触发搜索的用户原始问题，便于理解搜索意图
    """
    if not query.strip():
        return {"success": False, "result": "搜索关键词不能为空"}

    query = _sanitize_query(query)
    chosen = _resolve_provider("search", provider)
    chain = ["deepseek", "bing"] if chosen == "auto" else [chosen]
    cache_key = _cache_key("search", query, max_results, need_content)

    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    failures: list[str] = []
    for p in chain:
        if p == "deepseek":
            if not ds.available():
                failures.append("DeepSeek 未配置 API Key")
                continue
            if _over_daily_limit("search"):
                failures.append("DeepSeek 搜索已达今日调用上限（ZENITH_WEB_DAILY_LIMIT）")
                continue
            t0 = time.perf_counter()
            out = await ds.ds_search(
                query,
                count=max_results,
                need_content=need_content,
                ori_question=ori_question,
            )
            _record_call("search", out.get("success", False), out.get("status") or (200 if out.get("success") else 0),
                         (time.perf_counter() - t0) * 1000, query)
            if out.get("success"):
                results = out.get("results") or []
                if results:
                    res = {
                        "success": True,
                        "result": _format_ds_results(query, results),
                        "results": results,
                        "provider": "deepseek",
                    }
                    _cache_put(cache_key, res)
                    return res
                # 官方明确：搜索正常结束但无匹配 → 空数组。此时试一次本地，不成也不亏。
                failures.append("DeepSeek 搜索无结果")
                logger.info("DeepSeek 搜索无结果，尝试本地兜底: %s", query)
                continue
            failures.append(out.get("result", ""))
            logger.info("DeepSeek 搜索失败，回退 Bing: %s | %s",
                        query[:60], str(out.get("result", ""))[:120])
            continue

        out = await _search_bing(query, max_results)
        if out.get("success"):
            if failures:
                # 让模型/日志看得见「这次是回退来的」，避免把降级结果当官方结果用
                out["result"] = (f"（DeepSeek 搜索不可用：{failures[0][:80]}；已回退 Bing）\n"
                                 + out["result"])
            out["provider"] = "bing"
            _cache_put(cache_key, out)
            return out
        failures.append(out.get("result", ""))

    return {"success": False, "result": _join_failures(failures), "provider": chosen}

