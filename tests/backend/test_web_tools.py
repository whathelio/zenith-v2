"""backend/web_tools.py 单元测试 —— 全程 mock，绝不做真实网络请求。

覆盖分四块：
1. 纯函数：_sanitize_query / _query_units / _match_score / _select_relevant /
   _fallback_query / _fetch_failure_hint / _extract_text
2. HTML 正文提取：fetch_url（mock httpx.AsyncClient）
3. Bing 抓取：_bing_results（mock httpx.AsyncClient + BeautifulSoup）
4. 搜索编排：web_search（mock _bing_results）

纪律：不修改被测源码；网络出口一律用 monkeypatch 替换。
"""
import httpx
import pytest

from backend import web_tools as wt


# ────────────────────────── 测试替身 ──────────────────────────
class _FakeResp:
    """最小化的 httpx.Response 替身。"""

    def __init__(self, text="", headers=None, url="https://example.com/", status=200):
        self.text = text
        self.headers = headers or {}
        self.url = url
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("GET", str(self.url))
            resp = httpx.Response(self.status_code, request=req)
            raise httpx.HTTPStatusError("boom", request=req, response=resp)


class _FakeClient:
    """AsyncClient 的 async context manager 替身。"""

    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc
        self.requested_url = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url, headers=None):
        self.requested_url = url
        if self._exc is not None:
            raise self._exc
        return self._resp


def _install_client(monkeypatch, resp=None, exc=None):
    """把 httpx.AsyncClient 换成 _FakeClient 工厂。"""
    holder = {}

    def _factory(**kwargs):
        holder["client"] = _FakeClient(resp=resp, exc=exc)
        return holder["client"]

    monkeypatch.setattr(wt.httpx, "AsyncClient", _factory)
    return holder


# ────────────────────────── 1. 纯函数 ──────────────────────────
def test_fetch_failure_hint_spa站点给出强提示():
    msg = wt._fetch_failure_hint("https://www.bilibili.com/video/1", "错误")
    assert msg.startswith("错误")
    assert "动态渲染" in msg
    assert "web_search" in msg


def test_fetch_failure_hint_普通站点走通用建议():
    msg = wt._fetch_failure_hint("https://example.com/a", "错误")
    assert msg.startswith("错误")
    assert "💡" in msg
    assert "动态渲染" not in msg


def test_fetch_failure_hint_无host视为普通站点():
    msg = wt._fetch_failure_hint("not a url", "错误")
    assert "💡" in msg


def test_extract_text_带结构标签():
    soup = wt._get_soup(
        "<html><body><h1>主标题</h1><p>正文一</p><ul><li>要点A</li></ul>"
        "<blockquote>引用</blockquote></body></html>"
    )
    text = wt._extract_text(soup)
    assert "## 主标题" in text
    assert "- 要点A" in text
    assert "正文一" in text
    assert "引用" in text


def test_extract_text_跳过空元素():
    soup = wt._get_soup("<html><body><p></p><h2>有内容</h2></body></html>")
    text = wt._extract_text(soup)
    assert "有内容" in text


def test_extract_text_无结构标签时回退全文本():
    soup = wt._get_soup("<html><body><div>只有div里的文字</div></body></html>")
    text = wt._extract_text(soup)
    assert "只有div里的文字" in text


def test_sanitize_query_剔除长数字噪声与短词():
    # 知乎 19 位回答 ID 是噪声，应被剔除；「知乎」保留
    assert wt._sanitize_query("知乎 2083123101873844765") == "知乎"
    # 单字符词（<2）被剔除
    assert wt._sanitize_query("a bcd ef") == "bcd ef"


def test_sanitize_query_压缩空白():
    assert wt._sanitize_query("  凯恩斯   主义  ") == "凯恩斯 主义"


def test_sanitize_query_全被净化时原样回退():
    # 纯长数字 → 净化后为空 → 必须回退到原始串，绝不搜空
    assert wt._sanitize_query("123456789") == "123456789"


def test_query_units_中文2gram与ascii词元():
    grams, toks = wt._query_units("凯恩斯 主义 OpenAI")
    assert "凯恩" in grams and "主义" in grams
    assert toks == ["openai"]


def test_query_units_单字不产生gram():
    grams, toks = wt._query_units("啊")
    assert grams == set() and toks == []


def test_match_score_中文命中且容忍Bing插空():
    units = wt._query_units("凯恩斯主义")
    # Bing 会在中间插空格：「凯恩 斯主义」
    score = wt._match_score(units, {"title": "凯恩 斯主义 简介", "snippet": ""})
    assert score >= 1


def test_match_score_ascii词元按词边界匹配():
    units = wt._query_units("本地 GPT 模型")
    assert wt._match_score(units, {"title": "本地 GPT 助手", "snippet": ""}) >= 1
    # gptx 不该被当成 gpt 命中（词边界）
    units_only = (set(), ["gpt"])
    assert wt._match_score(units_only, {"title": "gptx 系列", "snippet": ""}) == 0


def test_match_score_空结果字段不报错():
    units = wt._query_units("查询")
    assert wt._match_score(units, {}) == 0


def test_select_relevant_空结果原样返回():
    assert wt._select_relevant("任意", []) == ([], False)


def test_select_relevant_无判定单元不过滤():
    results = [{"title": "单字标题", "snippet": ""}]
    kept, knocked = wt._select_relevant("啊", results)
    assert kept == results and knocked is False


def test_select_relevant_过滤无关并在清空时回退():
    # 「孙正义」撞到「孙 姓_百度百科」：无 2-gram 命中 → 全被剔除 → 回退原始
    results = [{"title": "孙 姓_百度百科", "snippet": "姓氏解释"}]
    kept, knocked = wt._select_relevant("孙正义", results)
    assert kept == results and knocked is True


def test_select_relevant_保留相关结果():
    results = [
        {"title": "孙正义与软银", "snippet": "孙正义是软银创始人"},
        {"title": "不相关页面", "snippet": "无关内容"},
    ]
    kept, knocked = wt._select_relevant("孙正义", results)
    assert len(kept) == 1 and kept[0]["title"] == "孙正义与软银"
    assert knocked is False


def test_select_relevant_全部相关不移除():
    results = [{"title": "孙正义", "snippet": "孙正义"}]
    kept, knocked = wt._select_relevant("孙正义", results)
    assert kept == results and knocked is False


def test_fallback_query_取首个有效词():
    assert wt._fallback_query("知乎 2083123101873844765") == "知乎"
    assert wt._fallback_query("a bc") == "bc"


def test_fallback_query_无有效词返回空():
    assert wt._fallback_query("a b") == ""
    assert wt._fallback_query("") == ""


def test_fallback_query_无空格中文取主实体前缀():
    # 无空格中文：split() 会把整句当一个词原样返回 ⇒ 必须降级为首段前缀，
    # 否则调用点 alt != query 守卫恒假、重搜形同失效。
    q = "凯恩斯主义经济学的核心观点"
    alt = wt._fallback_query(q)
    assert alt == "凯恩斯主"
    assert alt != q


def test_fallback_query_短中文整句保持原样():
    # 已是主实体、无可降级空间 → 返回自身（由调用点守卫拦住重搜）
    assert wt._fallback_query("凯恩斯") == "凯恩斯"


# ────────────────────────── 2. fetch_url ──────────────────────────
@pytest.mark.asyncio
async def test_fetch_url_拒绝非http协议():
    out = await wt.fetch_url("ftp://example.com/file")
    assert out["success"] is False
    assert "仅支持 http/https" in out["result"]


@pytest.mark.asyncio
async def test_fetch_url_无netloc判为无效链接():
    out = await wt.fetch_url("http:///path")
    assert out["success"] is False
    assert "无效的链接" in out["result"]


@pytest.mark.asyncio
async def test_fetch_url_http错误码走spa提示(monkeypatch):
    _install_client(monkeypatch, resp=_FakeResp(status=404, url="https://www.zhihu.com/x"))
    out = await wt.fetch_url("https://www.zhihu.com/x")
    assert out["success"] is False
    assert "错误状态码" in out["result"]
    assert "动态渲染" in out["result"]  # zhihu 属 SPA_HOSTS


@pytest.mark.asyncio
async def test_fetch_url_请求异常走通用提示(monkeypatch):
    _install_client(monkeypatch, exc=httpx.ConnectError("boom"))
    out = await wt.fetch_url("https://example.com/x")
    assert out["success"] is False
    assert "请求失败" in out["result"]


@pytest.mark.asyncio
async def test_fetch_url_非html内容原样截断(monkeypatch):
    _install_client(monkeypatch, resp=_FakeResp(
        text="纯文本内容" * 100,
        headers={"content-type": "text/plain; charset=utf-8"},
        url="https://example.com/raw",
    ))
    out = await wt.fetch_url("https://example.com/raw", max_chars=50)
    assert out["success"] is True
    assert "非HTML内容" in out["result"]
    assert "内容已截断" in out["result"]
    assert out["title"] == ""


@pytest.mark.asyncio
async def test_fetch_url_html正文超长截断(monkeypatch):
    html = "<html><head><title>T</title></head><body><p>" + ("字" * 500) + "</p></body></html>"
    _install_client(monkeypatch, resp=_FakeResp(
        text=html, headers={"content-type": "text/html"}, url="https://example.com/",
    ))
    out = await wt.fetch_url("https://example.com/", max_chars=50)
    assert out["success"] is True
    assert "内容已截断" in out["result"]


@pytest.mark.asyncio
async def test_fetch_url_提取html正文与标题(monkeypatch):
    html = ("<html><head><title>页面标题</title></head><body>"
            "<script>var x=1</script><h1>大标题</h1><p>正文段落</p></body></html>")
    _install_client(monkeypatch, resp=_FakeResp(
        text=html, headers={"content-type": "text/html"}, url="https://example.com/page",
    ))
    out = await wt.fetch_url("https://example.com/page")
    assert out["success"] is True
    assert out["title"] == "页面标题"
    assert "标题: 页面标题" in out["result"]
    assert "大标题" in out["result"]
    assert "正文段落" in out["result"]
    assert "var x=1" not in out["result"]  # script 被移除


@pytest.mark.asyncio
async def test_fetch_url_spa站点追加提示(monkeypatch):
    html = "<html><head><title>T</title></head><body><p>骨架</p></body></html>"
    _install_client(monkeypatch, resp=_FakeResp(
        text=html, headers={"content-type": "text/html"},
        url="https://space.bilibili.com/1",
    ))
    out = await wt.fetch_url("https://space.bilibili.com/1")
    assert out["success"] is True
    assert "analyze_content" in out["result"]


@pytest.mark.asyncio
async def test_fetch_url_缺少bs4依赖时友好报错(monkeypatch):
    html = "<html><head><title>T</title></head><body><p>x</p></body></html>"
    _install_client(monkeypatch, resp=_FakeResp(
        text=html, headers={"content-type": "text/html"}, url="https://example.com/",
    ))

    def _boom(_html):
        raise ImportError("no bs4")

    monkeypatch.setattr(wt, "_get_soup", _boom)
    out = await wt.fetch_url("https://example.com/")
    assert out["success"] is False
    assert "beautifulsoup4" in out["result"]


# ────────────────────────── 3. _bing_results ──────────────────────────
@pytest.mark.asyncio
async def test_bing_results_解析结果并截断(monkeypatch):
    html = (
        '<html><body><ol>'
        '<li class="b_algo"><h2><a href="https://a.com/1">标题一</a></h2>'
        '<div class="b_caption"><p>摘要一</p></div></li>'
        '<li class="b_algo"><h2><a href="https://b.com/2">标题二</a></h2>'
        '<div class="b_caption"><p>摘要二</p></div></li>'
        '<li class="b_algo"><h2><a href="https://c.com/3">标题三</a></h2></li>'
        '</ol></body></html>'
    )
    _install_client(monkeypatch, resp=_FakeResp(text=html, url="https://www.bing.com/search"))
    results, err = await wt._bing_results("测试", max_results=2)
    assert err == ""
    assert len(results) == 2
    assert results[0] == {"title": "标题一", "url": "https://a.com/1", "snippet": "摘要一"}


@pytest.mark.asyncio
async def test_bing_results_url去重(monkeypatch):
    html = (
        '<html><body>'
        '<li class="b_algo"><h2><a href="https://dup.com/x">标题一</a></h2></li>'
        '<li class="b_algo"><h2><a href="https://dup.com/x">标题一重复</a></h2></li>'
        '</body></html>'
    )
    _install_client(monkeypatch, resp=_FakeResp(text=html))
    results, err = await wt._bing_results("测试", max_results=5)
    assert err == ""
    assert len(results) == 1


@pytest.mark.asyncio
async def test_bing_results_结构变化时回退h2抓取(monkeypatch):
    html = (
        '<html><body>'
        '<h2><a href="https://fallback.com/a">回退标题</a></h2>'
        '<a href="javascript:void(0)">无协议链接</a>'
        '</body></html>'
    )
    _install_client(monkeypatch, resp=_FakeResp(text=html))
    results, err = await wt._bing_results("测试", max_results=5)
    assert err == ""
    assert results == [{"title": "回退标题", "url": "https://fallback.com/a", "snippet": ""}]


@pytest.mark.asyncio
async def test_bing_results_跳过无标题链接的条目(monkeypatch):
    html = (
        '<html><body>'
        '<li class="b_algo"><div>没有 h2 的块</div></li>'
        '<li class="b_algo"><h2><a href="https://ok.com">正常标题</a></h2></li>'
        '</body></html>'
    )
    _install_client(monkeypatch, resp=_FakeResp(text=html))
    results, err = await wt._bing_results("测试", max_results=5)
    assert err == ""
    assert results == [{"title": "正常标题", "url": "https://ok.com", "snippet": ""}]


@pytest.mark.asyncio
async def test_bing_results_请求异常返回错误(monkeypatch):
    _install_client(monkeypatch, exc=httpx.ConnectError("net down"))
    results, err = await wt._bing_results("测试", max_results=5)
    assert results == []
    assert "搜索请求失败" in err


@pytest.mark.asyncio
async def test_bing_results_缺bs4返回错误(monkeypatch):
    _install_client(monkeypatch, resp=_FakeResp(text="<html></html>"))

    def _boom(_html):
        raise ImportError("no bs4")

    monkeypatch.setattr(wt, "_get_soup", _boom)
    results, err = await wt._bing_results("测试", max_results=5)
    assert results == []
    assert "beautifulsoup4" in err


# ────────────────────────── 4. web_search ──────────────────────────
@pytest.mark.asyncio
async def test_web_search_空查询直接拒绝():
    out = await wt.web_search("   ")
    assert out["success"] is False
    assert "不能为空" in out["result"]


@pytest.mark.asyncio
async def test_web_search_正常格式化输出(monkeypatch):
    async def _fake_bing(query, max_results):
        return ([{"title": "结果一", "url": "https://a.com", "snippet": "摘要"}], "")

    monkeypatch.setattr(wt, "_bing_results", _fake_bing)
    out = await wt.web_search("测试查询")
    assert out["success"] is True
    assert "结果一" in out["result"]
    assert "链接: https://a.com" in out["result"]
    assert out["results"][0]["url"] == "https://a.com"


@pytest.mark.asyncio
async def test_web_search_抓取错误透传(monkeypatch):
    async def _fake_bing(query, max_results):
        return ([], "搜索请求失败: boom")

    monkeypatch.setattr(wt, "_bing_results", _fake_bing)
    out = await wt.web_search("测试")
    assert out["success"] is False
    assert "搜索请求失败" in out["result"]


@pytest.mark.asyncio
async def test_web_search_无结果时的提示(monkeypatch):
    async def _fake_bing(query, max_results):
        return ([], "")

    monkeypatch.setattr(wt, "_bing_results", _fake_bing)
    out = await wt.web_search("测试")
    assert out["success"] is True
    assert "未找到" in out["result"]


@pytest.mark.asyncio
async def test_web_search_清空时降级重搜(monkeypatch):
    """原始结果全无关 → 用首词重搜拿到相关结果 → 命中 retry_note 分支。"""
    calls = {"n": 0}

    async def _fake_bing(query, max_results):
        calls["n"] += 1
        if calls["n"] == 1:
            # 原查询「孙正义 生日」→ 全是单字撞库的无关结果
            return ([{"title": "孙 姓_百度百科", "url": "https://x", "snippet": ""}], "")
        # 降级用首词「孙正义」重搜 → 命中
        return ([{"title": "孙正义与软银", "url": "https://y", "snippet": ""}], "")

    monkeypatch.setattr(wt, "_bing_results", _fake_bing)
    out = await wt.web_search("孙正义 生日")
    assert out["success"] is True
    assert "已改用" in out["result"]
    assert out["results"][0]["url"] == "https://y"


@pytest.mark.asyncio
async def test_web_search_降级重搜仍无关保留原始(monkeypatch):
    """原查询与降级查询都全无关 → 走 logger 分支并保留原始结果。"""

    async def _fake_bing(query, max_results):
        return ([{"title": "孙 姓_百度百科", "url": "https://x", "snippet": ""}], "")

    monkeypatch.setattr(wt, "_bing_results", _fake_bing)
    out = await wt.web_search("孙正义 生日")
    assert out["success"] is True
    assert out["results"][0]["url"] == "https://x"
    assert "已改用" not in out["result"]


@pytest.mark.asyncio
async def test_web_search_降级仍无关时保留原始(monkeypatch):
    """重搜仍全无关 → 保留原始结果，绝不把结果清空。"""

    async def _fake_bing(query, max_results):
        return ([{"title": "孙 姓_百度百科", "url": "https://x", "snippet": ""}], "")

    monkeypatch.setattr(wt, "_bing_results", _fake_bing)
    out = await wt.web_search("孙正义")
    assert out["success"] is True
    assert out["results"][0]["url"] == "https://x"
    assert "已改用" not in out["result"]


@pytest.mark.asyncio
async def test_web_search_无空格中文也降级重搜(monkeypatch):
    """回归锁：无空格中文查询必须能触发降级重搜（修复前 alt == query，永不触发）。"""
    seen = []

    async def _fake_bing(query, max_results):
        seen.append(query)
        if len(seen) == 1:
            # 原长查询 → 单字撞库样式的全无关结果
            return ([{"title": "孙 姓_百度百科", "url": "https://x", "snippet": ""}], "")
        # 降级词「凯恩斯主」重搜 → 命中 2-gram「凯恩」「恩斯」
        return ([{"title": "凯恩斯主义的核心观点", "url": "https://y", "snippet": ""}], "")

    monkeypatch.setattr(wt, "_bing_results", _fake_bing)
    out = await wt.web_search("凯恩斯主义经济学的核心观点")
    assert out["success"] is True
    assert len(seen) == 2, "必须真的发生第二次（降级）搜索"
    assert seen[1] == "凯恩斯主" and seen[1] != seen[0]
    assert "已改用" in out["result"]
    assert out["results"][0]["url"] == "https://y"
