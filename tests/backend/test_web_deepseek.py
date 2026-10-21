"""web_deepseek + web_tools provider 层的单元测试（全程不打真网）。

纪律：所有 HTTP 出口都 monkeypatch 掉；不读真 .env、不花真配额。
"""
from __future__ import annotations

import pytest

from backend import web_deepseek as ds
from backend import web_tools as wt


# ────────────────────────── 1. 纯函数 ──────────────────────────
def test_unescape_markdown_去掉标点转义():
    raw = r"彻底火了！上线DeepSeek\-V4，来源\：证券时报"
    assert ds.unescape_markdown(raw) == "彻底火了！上线DeepSeek-V4，来源：证券时报"


def test_unescape_markdown_代码块内原样保留():
    raw = "正文\n```python\nre.match(r'\\d+', s)\n```\n尾巴\\-x"
    out = ds.unescape_markdown(raw)
    assert r"r'\d+'" in out          # 代码块里的 \d 不能被吃掉
    assert "尾巴-x" in out


def test_unescape_markdown_空值安全():
    assert ds.unescape_markdown("") == ""
    assert ds.unescape_markdown(None) == ""


def test_norm_result_缺字段给默认值():
    r = ds._norm_result({"title": " 标题 ", "url": "https://a.com"})
    assert r["title"] == "标题"
    assert r["snippet"] == "" and r["summary"] == "" and r["content"] == ""
    assert r["authority"] == -1
    assert r["date"] == ""


# ────────────────────────── 2. ds_search ──────────────────────────
@pytest.mark.asyncio
async def test_ds_search_无key直接拒绝(monkeypatch):
    monkeypatch.setattr(ds, "_api_key", lambda: "")
    out = await ds.ds_search("x")
    assert out["success"] is False
    assert "API Key" in out["result"]


@pytest.mark.asyncio
async def test_ds_search_成功规范化并过滤空url(monkeypatch):
    async def _fake_post(path, payload, timeout):
        assert path == "/v1/tools/search"
        assert payload["count"] == 5
        return 200, {"results": [
            {"title": "A", "url": "https://a.com", "summary": "s", "authority": 4,
             "rerank_score": 2.4, "date": "2026-06-01T00:00:00+00:00"},
            {"title": "无链接", "url": ""},
        ]}

    monkeypatch.setattr(ds, "_api_key", lambda: "sk-test")
    monkeypatch.setattr(ds, "_post", _fake_post)
    out = await ds.ds_search("q", count=5)
    assert out["success"] is True
    assert len(out["results"]) == 1
    assert out["results"][0]["authority"] == 4
    assert out["results"][0]["score"] == 2.4


@pytest.mark.asyncio
async def test_ds_search_402余额不足不重试(monkeypatch):
    calls = {"n": 0}

    async def _fake_post(path, payload, timeout):
        calls["n"] += 1
        return 402, "Insufficient Balance"

    monkeypatch.setattr(ds, "_api_key", lambda: "sk-test")
    monkeypatch.setattr(ds, "_post", _fake_post)
    out = await ds.ds_search("q")
    assert out["success"] is False
    assert out["retryable"] is False
    assert "余额" in out["result"]
    assert calls["n"] == 1  # 4xx 不该重试


@pytest.mark.asyncio
async def test_ds_search_429重试后成功(monkeypatch):
    calls = {"n": 0}

    async def _fake_post(path, payload, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            return 429, "rate limited"
        return 200, {"results": [{"title": "A", "url": "https://a.com"}]}

    async def _fast_sleep(_):
        return None

    monkeypatch.setattr(ds, "_api_key", lambda: "sk-test")
    monkeypatch.setattr(ds, "_post", _fake_post)
    monkeypatch.setattr(ds.asyncio, "sleep", _fast_sleep)
    out = await ds.ds_search("q")
    assert out["success"] is True
    assert calls["n"] == 2


# ────────────────────────── 3. ds_fetch ──────────────────────────
@pytest.mark.asyncio
async def test_ds_fetch_成功带标题并截断(monkeypatch):
    async def _fake_post(path, payload, timeout):
        assert path == "/v1/tools/fetch"
        return 200, {"title": "标题", "content": "正文" * 100}

    monkeypatch.setattr(ds, "_api_key", lambda: "sk-test")
    monkeypatch.setattr(ds, "_post", _fake_post)
    out = await ds.ds_fetch("https://a.com/x", max_chars=50)
    assert out["success"] is True
    assert out["title"] == "标题"
    assert out["truncated"] is True
    assert "内容已截断" in out["result"]


@pytest.mark.asyncio
async def test_ds_fetch_空正文按404处理(monkeypatch):
    async def _fake_post(path, payload, timeout):
        return 200, {"title": "空页", "content": ""}

    monkeypatch.setattr(ds, "_api_key", lambda: "sk-test")
    monkeypatch.setattr(ds, "_post", _fake_post)
    out = await ds.ds_fetch("https://a.com/x")
    assert out["success"] is False
    assert "未能获取" in out["result"]


@pytest.mark.asyncio
async def test_ds_fetch_403安全策略(monkeypatch):
    async def _fake_post(path, payload, timeout):
        return 403, "blocked"

    monkeypatch.setattr(ds, "_api_key", lambda: "sk-test")
    monkeypatch.setattr(ds, "_post", _fake_post)
    out = await ds.ds_fetch("https://a.com/x")
    assert out["success"] is False
    assert "安全策略" in out["result"]
    assert out["retryable"] is False


@pytest.mark.asyncio
async def test_ds_fetch_拒绝非http协议():
    out = await ds.ds_fetch("ftp://a.com/x")
    assert out["success"] is False
    assert "http/https" in out["result"]


# ────────────────────────── 4. provider 调度 ──────────────────────────
@pytest.mark.asyncio
async def test_web_search_deepseek_优先(monkeypatch):
    async def _fake_search(query, count=10, need_content=False, ori_question=None, timeout=60.0):
        return {"success": True, "results": [{
            "title": "结果一", "url": "https://a.com", "snippet": "短", "summary": "长摘要",
            "content": "", "date": "2026-10-15T00:00:00+00:00", "site_name": "新财观",
            "authority": 4, "score": 2.57,
        }]}

    monkeypatch.setattr(ds, "available", lambda: True)
    monkeypatch.setattr(ds, "ds_search", _fake_search)
    out = await wt.web_search("测试", provider="auto")
    assert out["success"] is True
    assert out["provider"] == "deepseek"
    assert "DeepSeek" in out["result"]
    assert "新财观" in out["result"] and "权威 4/4" in out["result"]
    assert "长摘要" in out["result"]


@pytest.mark.asyncio
async def test_web_search_deepseek失败回退bing并标注(monkeypatch):
    async def _fake_search(query, count=10, need_content=False, ori_question=None, timeout=60.0):
        return {"success": False, "result": "DeepSeek 网页接口不可用：余额不足", "status": 402}

    async def _fake_bing(query, max_results):
        return ([{"title": "结果一", "url": "https://a.com", "snippet": "摘要"}], "")

    monkeypatch.setattr(ds, "available", lambda: True)
    monkeypatch.setattr(ds, "ds_search", _fake_search)
    monkeypatch.setattr(wt, "_bing_results", _fake_bing)
    out = await wt.web_search("测试", provider="auto")
    assert out["success"] is True
    assert out["provider"] == "bing"
    assert "已回退 Bing" in out["result"]
    assert out["results"][0]["url"] == "https://a.com"


@pytest.mark.asyncio
async def test_web_search_无key只走bing(monkeypatch):
    async def _fake_bing(query, max_results):
        return ([{"title": "B", "url": "https://b.com", "snippet": ""}], "")

    monkeypatch.setattr(ds, "available", lambda: False)
    monkeypatch.setattr(wt, "_bing_results", _fake_bing)
    out = await wt.web_search("测试", provider="auto")
    assert out["success"] is True
    assert out["provider"] == "bing"


@pytest.mark.asyncio
async def test_fetch_url_deepseek_成功不调本地(monkeypatch):
    async def _fake_fetch(url, max_chars=8000, timeout=45.0):
        return {"success": True, "result": "标题: T\n\n正文", "title": "T",
                "content": "正文", "url": url, "truncated": False}

    async def _boom(*a, **k):
        raise AssertionError("DS 成功时不该走本地直抓")

    monkeypatch.setattr(ds, "available", lambda: True)
    monkeypatch.setattr(ds, "ds_fetch", _fake_fetch)
    monkeypatch.setattr(wt, "_fetch_local", _boom)
    out = await wt.fetch_url("https://a.com/x", provider="auto")
    assert out["success"] is True
    assert out["provider"] == "deepseek"


@pytest.mark.asyncio
async def test_fetch_url_deepseek失败回退本地(monkeypatch):
    async def _fake_fetch(url, max_chars=8000, timeout=45.0):
        return {"success": False, "result": "DeepSeek 网页接口不可用：未能获取", "status": 404}

    async def _fake_local(url, max_chars=8000):
        return {"success": True, "result": "本地正文", "url": url, "title": "L"}

    monkeypatch.setattr(ds, "available", lambda: True)
    monkeypatch.setattr(ds, "ds_fetch", _fake_fetch)
    monkeypatch.setattr(wt, "_fetch_local", _fake_local)
    out = await wt.fetch_url("https://a.com/x", provider="auto")
    assert out["success"] is True
    assert out["provider"] == "local"
    assert "已回退本地直抓" in out["result"]


# ────────────────────────── 5. 缓存 / 日限 ──────────────────────────
def test_缓存命中后不再调用(monkeypatch):
    monkeypatch.setattr(wt, "_cache_ttl", lambda kind: 60)
    wt._CACHE.clear()
    key = wt._cache_key("search", "q", 5, False)
    wt._cache_put(key, {"success": True, "result": "cached"})
    assert wt._cache_get(key)["result"] == "cached"
    # 深拷贝：改返回值不应污染缓存本体
    got = wt._cache_get(key)
    got["result"] = "mutated"
    assert wt._cache_get(key)["result"] == "cached"
    wt._CACHE.clear()


def test_日限按当日成功次数判断(monkeypatch):
    monkeypatch.setattr(wt, "_usage_state", {"date": "", "counts": {}})
    monkeypatch.setattr(wt, "_ensure_usage_loaded", lambda: None)
    monkeypatch.setenv("ZENITH_WEB_DAILY_LIMIT", "2")
    wt._usage_state["date"] = "x"
    wt._usage_state["counts"] = {"search": 2}
    assert wt._over_daily_limit("search") is True
    assert wt._over_daily_limit("fetch") is False
    monkeypatch.setenv("ZENITH_WEB_DAILY_LIMIT", "0")
    assert wt._over_daily_limit("search") is False
