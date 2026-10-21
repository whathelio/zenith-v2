"""backend/content_tools.py 单元测试。

覆盖策略：
- 纯函数（URL 识别/解析/格式化/文件安全扫描）→ 直接断言正常 + 边界/异常。
- 依赖外部资源的函数（httpx 网络、ffmpeg 子进程、LLM、config、文件系统）
  → 用 monkeypatch 精确替换「最小依赖面」，绝不做真实网络 / LLM / ffmpeg 调用。

不改动 content_tools.py 源码。发现的疑似 bug 见文件末尾注释与本文件报告。
"""
import base64
import os
import sys
import types

import pytest

from backend import content_tools as ct
from backend import web_tools

# 异步测试由 pyproject 的 asyncio_mode = "auto" 自动收集，无需 pytestmark。


# ────────────────────────────── 假的 httpx 客户端 ──────────────────────────────

class _Resp:
    def __init__(self, json_data=None, status_code=200, text=""):
        self._json = json_data
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


class _FakeStream:
    def __init__(self, chunks, raise_on_enter=False, status_code=200):
        self._chunks = chunks or []
        self._raise = raise_on_enter
        self.status_code = status_code

    async def __aenter__(self):
        if self._raise:
            raise RuntimeError("stream open failed")
        return self

    async def __aexit__(self, *a):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    async def aiter_bytes(self, chunk_size=65536):
        for c in self._chunks:
            yield c


class _FakeAsyncClient:
    def __init__(self, responses, stream_chunks, stream_raise, urls):
        self._responses = responses   # 共享同一列表引用（跨多个 client 顺序消费）
        self._chunks = stream_chunks
        self._stream_raise = stream_raise
        self._urls = urls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        self._urls.append(url)
        return self._responses.pop(0)

    def stream(self, method, url, headers=None):
        self._urls.append(url)
        return _FakeStream(self._chunks, self._stream_raise)


def _install_async_client(monkeypatch, responses=None, stream_chunks=None, stream_raise=False):
    """把 ct.httpx.AsyncClient 换成受控假实现（按调用顺序返回 responses）。

    注意：responses 列表只建一次并在所有 client 实例间共享 —— B站字幕等路径会
    连续构造两个 client，需按序消费各自的响应。
    返回：按序记录的所有请求 URL 列表（可断言实际请求了哪个 URL）。
    """
    shared = list(responses or [])
    urls: list[str] = []

    def _factory(*a, **k):
        return _FakeAsyncClient(shared, stream_chunks or [], stream_raise, urls)
    monkeypatch.setattr(ct.httpx, "AsyncClient", _factory)
    return urls


def _install_sync_client(monkeypatch, resp=None, raise_exc=None):
    class _SyncClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, files=None, data=None, headers=None):
            if raise_exc is not None:
                raise raise_exc
            return resp

    monkeypatch.setattr(ct.httpx, "Client", _SyncClient)


def _fake_run(stdout="", stderr="", returncode=0, side_effect=None):
    captured = {}

    def _run(cmd, **kwargs):
        captured["cmd"] = cmd
        if side_effect is not None:
            raise side_effect
        return types.SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)

    _run.captured = captured
    return _run


# ══════════════════════════════ 纯函数：URL 识别 ══════════════════════════════

@pytest.mark.parametrize("url,expected", [
    ("https://x.com/a.mp4", True),
    ("https://x.com/A.MKV", True),          # 大写扩展名
    ("https://x.com/a.webm?x=1", True),
    ("https://x.com/a.html", False),
    ("https://x.com/video", False),
    ("", False),
    ("not a url", False),
])
def test_is_video_url(url, expected):
    assert ct._is_video_url(url) is expected


@pytest.mark.parametrize("url,expected", [
    ("https://www.bilibili.com/video/BV1bsMw6VE3r", True),
    ("https://bilibili.com/x", True),
    ("https://space.bilibili.com/123", True),
    ("https://notbilibili.com/x", False),        # 子串误判修复
    ("https://bilibili.com.evil.com/x", False),  # 后缀伪装修复
    ("https://evil-bilibili.com/x", False),      # 连字符前缀修复
    ("https://github.com/a/b", False),
    ("", False),
])
def test_is_bilibili_url(url, expected):
    assert ct._is_bilibili_url(url) is expected


@pytest.mark.parametrize("url,expected", [
    ("https://www.bilibili.com/video/BV1bsMw6VE3r", "BV1bsMw6VE3r"),
    ("https://b23.tv/BV1xx411c7mD/", "BV1xx411c7mD"),
    ("https://www.bilibili.com/video/av12345", ""),
    ("https://www.bilibili.com/video/BVshort", ""),   # 不足 10 位
    ("", ""),
])
def test_extract_bvid(url, expected):
    assert ct._extract_bvid(url) == expected


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/chinese-poetry/chinese-poetry", True),
    ("https://www.github.com/a/b", False),   # 严格 host == github.com
    ("https://gist.github.com/a/b", False),
    ("https://gitlab.com/a/b", False),
    ("", False),
])
def test_is_github_url(url, expected):
    assert ct._is_github_url(url) is expected


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/owner/repo", ("owner", "repo")),
    ("https://github.com/owner/repo/tree/main", ("owner", "repo")),
    ("https://github.com/owner/repo/", ("owner", "repo")),
    ("https://github.com/owner", ("", "")),
    ("https://github.com/", ("", "")),
    ("", ("", "")),
])
def test_extract_github_repo(url, expected):
    assert ct._extract_github_repo(url) == expected


# ══════════════════════════════ ffmpeg helper ══════════════════════════════

def test_get_ffmpeg_bundled(monkeypatch):
    fake = types.SimpleNamespace(get_ffmpeg_exe=lambda: "/bundled/ffmpeg")
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", fake)
    assert ct._get_ffmpeg() == "/bundled/ffmpeg"


def test_get_ffmpeg_fallback_to_path(monkeypatch):
    # sys.modules[name] = None → import 抛 ImportError
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)
    assert ct._get_ffmpeg() == "ffmpeg"


# ══════════════════════════════ B站 API（mock httpx） ══════════════════════════════

async def test_fetch_bilibili_info_success(monkeypatch):
    payload = {"code": 0, "data": {
        "title": "标题", "desc": "简介", "owner": {"name": "UP"},
        "duration": 125, "cid": 999,
    }}
    _install_async_client(monkeypatch, responses=[_Resp(json_data=payload)])
    info = await ct._fetch_bilibili_info("BV1bsMw6VE3r")
    assert info["success"] is True
    assert info["title"] == "标题" and info["author"] == "UP"
    assert info["duration"] == "2:05" and info["cid"] == 999


async def test_fetch_bilibili_info_api_error_code(monkeypatch):
    _install_async_client(monkeypatch, responses=[_Resp(json_data={"code": -400, "message": "bad"})])
    info = await ct._fetch_bilibili_info("BVx")
    assert info["success"] is False and "code=-400" in info["result"]


async def test_fetch_bilibili_info_network_exception(monkeypatch):
    _install_async_client(monkeypatch, responses=[_Resp(json_data=None, status_code=500)])
    info = await ct._fetch_bilibili_info("BVx")
    assert info["success"] is False


async def test_fetch_bilibili_subtitles_success_with_relative_url(monkeypatch):
    sub_json = {"body": [{"content": " 第一句 "}, {"content": ""}, {"content": "第二句"}]}
    _install_async_client(monkeypatch, responses=[
        _Resp(json_data={"code": 0, "data": {"subtitle": {"subtitles": [
            {"lan": "zh-CN", "subtitle_url": "//sub.example/a.json"},
        ]}}}),
        _Resp(json_data=sub_json),
    ])
    text = await ct._fetch_bilibili_subtitles("BVx", cid=1)
    assert text == "第一句 第二句"


async def test_fetch_bilibili_subtitles_no_list(monkeypatch):
    _install_async_client(monkeypatch, responses=[
        _Resp(json_data={"code": 0, "data": {"subtitle": {"subtitles": []}}}),
    ])
    assert await ct._fetch_bilibili_subtitles("BVx") == ""


async def test_fetch_bilibili_subtitles_bad_code(monkeypatch):
    _install_async_client(monkeypatch, responses=[_Resp(json_data={"code": -1})])
    assert await ct._fetch_bilibili_subtitles("BVx") == ""


async def test_fetch_bilibili_subtitles_network_exception(monkeypatch):
    _install_async_client(monkeypatch, responses=[_Resp(status_code=500)])
    assert await ct._fetch_bilibili_subtitles("BVx") == ""


async def test_fetch_bilibili_subtitles_json_fetch_exception(monkeypatch):
    # 字幕列表拿到 URL，但下载字幕 JSON 时网络失败 → 走 except 分支返回 ""
    _install_async_client(monkeypatch, responses=[
        _Resp(json_data={"code": 0, "data": {"subtitle": {"subtitles": [
            {"lan": "zh-CN", "subtitle_url": "https://sub.example/a.json"},
        ]}}}),
        _Resp(status_code=500),
    ])
    assert await ct._fetch_bilibili_subtitles("BVx", cid=1) == ""


async def test_fetch_bilibili_subtitles_empty_sub_url(monkeypatch):
    _install_async_client(monkeypatch, responses=[
        _Resp(json_data={"code": 0, "data": {"subtitle": {"subtitles": [{"lan": "en"}]}}}),
    ])
    assert await ct._fetch_bilibili_subtitles("BVx") == ""


async def test_fetch_bilibili_subtitles_non_chinese_picks_last(monkeypatch):
    # 无中文时，循环自然结束 → 取「最后一个」条目的 URL 兜底（锁定当前实现）
    urls = _install_async_client(monkeypatch, responses=[
        _Resp(json_data={"code": 0, "data": {"subtitle": {"subtitles": [
            {"lan": "en", "subtitle_url": "https://sub.example/en.json"},
            {"lan": "ja", "subtitle_url": "https://sub.example/ja.json"},
        ]}}}),
        _Resp(json_data={"body": [{"content": "挨拶"}]}),
    ])
    assert await ct._fetch_bilibili_subtitles("BVx", cid=1) == "挨拶"
    assert urls[-1] == "https://sub.example/ja.json"   # 实际取的是末尾条目


async def test_fetch_bilibili_audio_url_picks_lowest_bandwidth(monkeypatch):
    _install_async_client(monkeypatch, responses=[_Resp(json_data={"code": 0, "data": {"dash": {
        "audio": [
            {"bandwidth": 500, "baseUrl": "u-high"},   # 故意乱序
            {"bandwidth": 100, "baseUrl": "u-low"},
        ]
    }}})])
    assert await ct._fetch_bilibili_audio_url("BVx", 1) == "u-low"


async def test_fetch_bilibili_audio_url_no_streams(monkeypatch):
    _install_async_client(monkeypatch, responses=[_Resp(json_data={"code": 0, "data": {"dash": {}}})])
    assert await ct._fetch_bilibili_audio_url("BVx", 1) == ""


async def test_fetch_bilibili_audio_url_api_error(monkeypatch):
    _install_async_client(monkeypatch, responses=[_Resp(json_data={"code": -404, "message": "x"})])
    assert await ct._fetch_bilibili_audio_url("BVx", 1) == ""


async def test_fetch_bilibili_audio_url_network_exception(monkeypatch):
    _install_async_client(monkeypatch, responses=[_Resp(status_code=503)])
    assert await ct._fetch_bilibili_audio_url("BVx", 1) == ""


async def test_download_bilibili_audio_success(monkeypatch):
    _install_async_client(monkeypatch, stream_chunks=[b"aaa", b"bbb"])
    path = await ct._download_bilibili_audio("https://x/a.m4s")
    try:
        assert os.path.exists(path)
        with open(path, "rb") as f:
            assert f.read() == b"aaabbb"
    finally:
        if os.path.exists(path):
            os.unlink(path)


async def test_download_bilibili_audio_stream_error_cleanup(monkeypatch):
    _install_async_client(monkeypatch, stream_raise=True)
    with pytest.raises(RuntimeError):
        await ct._download_bilibili_audio("https://x/a.m4s")


# ══════════════════════════════ 音频/视频子进程处理 ══════════════════════════════

def test_convert_audio_to_wav_success(monkeypatch, tmp_path):
    audio = tmp_path / "a.m4s"
    audio.write_bytes(b"x")
    out = str(audio) + ".wav"

    monkeypatch.setattr(ct, "_get_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(ct.subprocess, "run", _fake_run())
    monkeypatch.setattr(os.path, "getsize", lambda p: 2048)
    assert ct._convert_audio_to_wav(str(audio)) == out


def test_convert_audio_to_wav_too_small(monkeypatch, tmp_path):
    monkeypatch.setattr(ct, "_get_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(ct.subprocess, "run", _fake_run())
    monkeypatch.setattr(os.path, "getsize", lambda p: 10)
    assert ct._convert_audio_to_wav(str(tmp_path / "a.m4s")) == ""


def test_convert_audio_to_wav_exception(monkeypatch, tmp_path):
    monkeypatch.setattr(ct, "_get_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(ct.subprocess, "run", _fake_run(side_effect=RuntimeError("boom")))
    assert ct._convert_audio_to_wav(str(tmp_path / "a.m4s")) == ""


@pytest.mark.parametrize("stderr,expected", [
    ("  Duration: 00:00:00.99, start", 0.99),
    ("  Duration: 01:02:03.45, start", 3723.45),
    ("  Duration: 00:01:30.50, start", 90.5),
])
def test_get_wav_duration_parses(monkeypatch, stderr, expected):
    monkeypatch.setattr(ct, "_get_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(ct.subprocess, "run", _fake_run(stderr=stderr))
    # FFmpeg Duration 小数为百分秒：HH:MM:SS.ss，须 /100（此前 /10 会放大 10 倍）
    assert ct._get_wav_duration("x.wav") == pytest.approx(expected)


def test_get_wav_duration_no_match(monkeypatch):
    monkeypatch.setattr(ct, "_get_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(ct.subprocess, "run", _fake_run(stderr="no duration here"))
    assert ct._get_wav_duration("x.wav") == 0.0


def test_get_wav_duration_exception(monkeypatch):
    monkeypatch.setattr(ct, "_get_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(ct.subprocess, "run", _fake_run(side_effect=RuntimeError("boom")))
    assert ct._get_wav_duration("x.wav") == 0.0


def test_split_wav_success(monkeypatch, tmp_path):
    wav = str(tmp_path / "a.wav")
    chunk = wav + ".chunk_000.wav"
    monkeypatch.setattr(ct, "_get_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(ct.subprocess, "run", _fake_run())
    monkeypatch.setattr("glob.glob", lambda p: [chunk])
    assert ct._split_wav(wav) == [chunk]


def test_split_wav_exception_returns_original(monkeypatch, tmp_path):
    wav = str(tmp_path / "a.wav")
    monkeypatch.setattr(ct, "_get_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(ct.subprocess, "run", _fake_run(side_effect=RuntimeError("boom")))
    assert ct._split_wav(wav) == [wav]


def test_speech_to_text_chunked_short(monkeypatch):
    monkeypatch.setattr(ct, "_get_wav_duration", lambda p: 100)
    monkeypatch.setattr(ct, "_speech_to_text", lambda p: "短音频转录")
    assert ct._speech_to_text_chunked("a.wav", max_chunk_seconds=300) == "短音频转录"


def test_speech_to_text_chunked_multi(monkeypatch, tmp_path):
    c1 = tmp_path / "a.wav.chunk_000.wav"; c1.write_bytes(b"x")
    c2 = tmp_path / "a.wav.chunk_001.wav"; c2.write_bytes(b"x")
    monkeypatch.setattr(ct, "_get_wav_duration", lambda p: 700)
    monkeypatch.setattr(ct, "_split_wav", lambda p, s: [str(c1), str(c2)])
    texts = {"0": "第一段", "1": "第二段"}

    def _stt(p):
        return texts["0"] if "000" in p else texts["1"]

    monkeypatch.setattr(ct, "_speech_to_text", _stt)
    assert ct._speech_to_text_chunked("a.wav") == "第一段 第二段"
    assert not c1.exists() and not c2.exists()   # 分块被清理


def test_speech_to_text_chunked_split_single(monkeypatch):
    monkeypatch.setattr(ct, "_get_wav_duration", lambda p: 700)
    monkeypatch.setattr(ct, "_split_wav", lambda p, s: ["a.wav"])   # 只切出 1 段
    monkeypatch.setattr(ct, "_speech_to_text", lambda p: "整体")
    assert ct._speech_to_text_chunked("a.wav") == "整体"


def test_speech_to_text_chunked_unlink_failure_swallowed(monkeypatch, tmp_path):
    import stat
    chunk = tmp_path / "a.wav.chunk_000.wav"; chunk.write_bytes(b"x")
    other = tmp_path / "a.wav.chunk_001.wav"; other.write_bytes(b"x")
    os.chmod(str(chunk), stat.S_IREAD)   # 只读 → Windows 上 unlink 抛错，被 except 吞
    try:
        monkeypatch.setattr(ct, "_get_wav_duration", lambda p: 700)
        monkeypatch.setattr(ct, "_split_wav", lambda p, s: [str(chunk), str(other)])
        monkeypatch.setattr(ct, "_speech_to_text", lambda p: "文本")
        assert ct._speech_to_text_chunked("a.wav") == "文本 文本"
    finally:
        os.chmod(str(chunk), stat.S_IWRITE)
        for p in (chunk, other):
            if p.exists():
                os.unlink(str(p))


def test_extract_subtitles_success(monkeypatch, tmp_path):
    line1 = "这是一句足够长的字幕内容用来通过长度阈值检测的第一个句子需要更长一些"
    line2 = "第二句字幕内容也需要足够长度才行否则整体长度不够就会被丢弃掉哦"
    srt = (
        "1\n00:00:01,000 --> 00:00:03,000\n"
        f"{line1}\n"
        "2\n00:00:04,000 --> 00:00:06,000\n"
        f"{line2}\n"
    )
    monkeypatch.setattr(ct.subprocess, "run", _fake_run(stdout=srt))
    text = ct._extract_subtitles("ffmpeg", str(tmp_path / "v.mp4"))
    assert line1 in text and line2 in text
    assert "00:00:01" not in text        # 时间轴/序号已被剥离
    assert "-->" not in text


def test_extract_subtitles_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(ct.subprocess, "run", _fake_run(stdout="short"))
    assert ct._extract_subtitles("ffmpeg", str(tmp_path / "v.mp4")) == ""


def test_extract_subtitles_exception(monkeypatch, tmp_path):
    monkeypatch.setattr(ct.subprocess, "run", _fake_run(side_effect=RuntimeError("boom")))
    assert ct._extract_subtitles("ffmpeg", str(tmp_path / "v.mp4")) == ""


def test_extract_audio_success(monkeypatch, tmp_path):
    monkeypatch.setattr(ct.subprocess, "run", _fake_run())
    monkeypatch.setattr(os.path, "getsize", lambda p: 5000)
    out = str(tmp_path / "v.mp4") + ".audio.wav"
    assert ct._extract_audio("ffmpeg", str(tmp_path / "v.mp4")) == out


def test_extract_audio_too_small(monkeypatch, tmp_path):
    monkeypatch.setattr(ct.subprocess, "run", _fake_run())
    monkeypatch.setattr(os.path, "getsize", lambda p: 10)
    assert ct._extract_audio("ffmpeg", str(tmp_path / "v.mp4")) == ""


def test_extract_audio_exception(monkeypatch, tmp_path):
    monkeypatch.setattr(ct.subprocess, "run", _fake_run(side_effect=RuntimeError("boom")))
    assert ct._extract_audio("ffmpeg", str(tmp_path / "v.mp4")) == ""


def test_video_metadata_success(monkeypatch, tmp_path):
    stderr = "Duration: 00:00:42.00, bitrate: 100 kb/s\n  Stream #0:0: Video: h264, 1920x1080"
    monkeypatch.setattr(ct.subprocess, "run", _fake_run(stderr=stderr))
    res = ct._video_metadata("ffmpeg", str(tmp_path / "movie.mp4"))
    assert res["success"] is True and res["method"] == "metadata"
    assert "00:00:42.00" in res["text"] and "1920x1080" in res["text"]


def test_video_metadata_exception(monkeypatch, tmp_path):
    monkeypatch.setattr(ct.subprocess, "run", _fake_run(side_effect=RuntimeError("boom")))
    res = ct._video_metadata("ffmpeg", str(tmp_path / "movie.mp4"))
    assert res["success"] is False and "movie.mp4" in res["result"]


def test_speech_to_text_no_api_key(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.config.get_api_key", lambda: "")
    wav = tmp_path / "a.wav"; wav.write_bytes(b"RIFF")
    assert ct._speech_to_text(str(wav)) == ""


def test_speech_to_text_success(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.config.get_api_key", lambda: "k")
    monkeypatch.setattr("backend.config.get_api_base", lambda: "https://api.test/v1/")
    _install_sync_client(monkeypatch, resp=_Resp(json_data={"text": " 转写结果 "}))
    wav = tmp_path / "a.wav"; wav.write_bytes(b"RIFF")
    assert ct._speech_to_text(str(wav)) == "转写结果"


def test_speech_to_text_http_exception(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.config.get_api_key", lambda: "k")
    monkeypatch.setattr("backend.config.get_api_base", lambda: "https://api.test/v1")
    _install_sync_client(monkeypatch, raise_exc=RuntimeError("net down"))
    wav = tmp_path / "a.wav"; wav.write_bytes(b"RIFF")
    assert ct._speech_to_text(str(wav)) == ""


def test_extract_video_text_subtitles_channel(monkeypatch, tmp_path):
    monkeypatch.setattr(ct, "_get_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(ct, "_extract_subtitles", lambda f, p: "字幕文本")
    res = ct._extract_video_text(str(tmp_path / "v.mp4"))
    assert res == {"success": True, "text": "字幕文本", "method": "subtitles"}


def test_extract_video_text_stt_channel(monkeypatch, tmp_path):
    audio = tmp_path / "v.mp4.audio.wav"; audio.write_bytes(b"RIFF")
    monkeypatch.setattr(ct, "_get_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(ct, "_extract_subtitles", lambda f, p: "")
    monkeypatch.setattr(ct, "_extract_audio", lambda f, p: str(audio))
    monkeypatch.setattr(ct, "_speech_to_text", lambda p: "语音文本")
    res = ct._extract_video_text(str(tmp_path / "v.mp4"))
    assert res["method"] == "stt" and res["text"] == "语音文本"
    assert not audio.exists()   # 临时音频被清理


def test_extract_video_text_metadata_channel(monkeypatch, tmp_path):
    monkeypatch.setattr(ct, "_get_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(ct, "_extract_subtitles", lambda f, p: "")
    monkeypatch.setattr(ct, "_extract_audio", lambda f, p: "")
    monkeypatch.setattr(ct, "_video_metadata", lambda f, p: {"success": True, "text": "meta", "method": "metadata"})
    assert ct._extract_video_text(str(tmp_path / "v.mp4"))["method"] == "metadata"


# ══════════════════════════════ LLM 总结 ══════════════════════════════

async def test_llm_summarize_too_short():
    assert ct._llm_summarize.__doc__ is not None
    res = await ct._llm_summarize("很短")
    assert "内容过短" in res


@pytest.mark.parametrize("ctype", ["article", "video", "repository"])
async def test_llm_summarize_calls_llm(monkeypatch, ctype):
    captured = {}

    async def _fake_call_llm(messages, **kwargs):
        captured["messages"] = messages
        return {"content": "摘要结果"}

    monkeypatch.setattr("backend.llm_client.call_llm", _fake_call_llm)
    out = await ct._llm_summarize("x" * 100, language="en", content_type=ctype)
    assert out == "摘要结果"
    assert captured["messages"][0]["role"] == "user"


async def test_llm_summarize_error_prefix(monkeypatch):
    async def _fake_call_llm(messages, **kwargs):
        return {"content": "Error: quota exceeded"}

    monkeypatch.setattr("backend.llm_client.call_llm", _fake_call_llm)
    out = await ct._llm_summarize("x" * 100)
    assert out.startswith("摘要生成失败: ")


async def test_llm_summarize_truncates_long_text(monkeypatch):
    captured = {}

    async def _fake_call_llm(messages, **kwargs):
        captured["prompt"] = messages[0]["content"]
        return {"content": "ok"}

    monkeypatch.setattr("backend.llm_client.call_llm", _fake_call_llm)
    await ct._llm_summarize("y" * 20000)
    # prompt = 框架 + text[:MAX_PROMPT_CHARS]
    assert captured["prompt"].count("y") == ct.MAX_PROMPT_CHARS


# ══════════════════════════════ GitHub 路径 ══════════════════════════════

async def test_fetch_github_repo_info_success(monkeypatch):
    payload = {
        "full_name": "owner/repo", "description": "d", "language": "Python",
        "stargazers_count": 7, "forks_count": 3, "topics": ["a"],
        "license": {"spdx_id": "MIT"}, "homepage": "https://h",
    }
    _install_async_client(monkeypatch, responses=[_Resp(json_data=payload)])
    info = await ct._fetch_github_repo_info("owner", "repo")
    assert info["success"] and info["stars"] == 7 and info["license"] == "MIT"


async def test_fetch_github_repo_info_no_license(monkeypatch):
    _install_async_client(monkeypatch, responses=[_Resp(json_data={"full_name": "o/r", "license": None})])
    info = await ct._fetch_github_repo_info("o", "r")
    assert info["success"] and info["license"] == ""


async def test_fetch_github_repo_info_exception(monkeypatch):
    _install_async_client(monkeypatch, responses=[_Resp(status_code=500)])
    info = await ct._fetch_github_repo_info("o", "r")
    assert info["success"] is False


async def test_fetch_github_readme_success(monkeypatch):
    content = base64.b64encode("# 标题\n正文".encode("utf-8")).decode()
    _install_async_client(monkeypatch, responses=[_Resp(json_data={"content": content, "encoding": "base64"})])
    assert "正文" in await ct._fetch_github_readme("o", "r")


async def test_fetch_github_readme_404(monkeypatch):
    _install_async_client(monkeypatch, responses=[_Resp(json_data={}, status_code=404)])
    assert await ct._fetch_github_readme("o", "r") == ""


async def test_fetch_github_readme_non_base64_encoding(monkeypatch):
    _install_async_client(monkeypatch, responses=[_Resp(json_data={"content": "abc", "encoding": "utf-8"})])
    assert await ct._fetch_github_readme("o", "r") == ""


async def test_fetch_github_readme_exception(monkeypatch):
    _install_async_client(monkeypatch, responses=[_Resp(status_code=500)])
    assert await ct._fetch_github_readme("o", "r") == ""


async def test_handle_github_bad_url():
    res = await ct._handle_github("https://github.com/onlyowner", "zh-CN")
    assert res["success"] is False


async def test_handle_github_info_fail(monkeypatch):
    async def _fail(o, r):
        return {"success": False, "result": "no"}

    async def _readme(o, r):
        return ""

    monkeypatch.setattr(ct, "_fetch_github_repo_info", _fail)
    monkeypatch.setattr(ct, "_fetch_github_readme", _readme)
    res = await ct._handle_github("https://github.com/o/r", "zh-CN")
    assert res["success"] is False


async def test_handle_github_readme_method(monkeypatch):
    async def _info(o, r):
        return {"success": True, "name": "o/r", "description": "desc", "language": "Py",
                "stars": 1, "forks": 0, "topics": ["t"], "license": "MIT"}

    async def _readme(o, r):
        return "x" * 100

    async def _sum(text, language="zh-CN", content_type="article"):
        return "SUMMARY"

    monkeypatch.setattr(ct, "_fetch_github_repo_info", _info)
    monkeypatch.setattr(ct, "_fetch_github_readme", _readme)
    monkeypatch.setattr(ct, "_llm_summarize", _sum)
    res = await ct._handle_github("https://github.com/o/r", "zh-CN")
    assert res["success"] and res["method"] == "github_readme" and res["summary"] == "SUMMARY"


async def test_handle_github_description_method(monkeypatch):
    async def _info(o, r):
        return {"success": True, "name": "o/r", "description": "desc here", "language": "",
                "stars": 0, "forks": 0, "topics": [], "license": ""}

    async def _readme(o, r):
        return ""

    async def _sum(text, language="zh-CN", content_type="article"):
        return "S"

    monkeypatch.setattr(ct, "_fetch_github_repo_info", _info)
    monkeypatch.setattr(ct, "_fetch_github_readme", _readme)
    monkeypatch.setattr(ct, "_llm_summarize", _sum)
    res = await ct._handle_github("https://github.com/o/r", "zh-CN")
    assert res["method"] == "github_description"


async def test_handle_github_title_only(monkeypatch):
    async def _info(o, r):
        return {"success": True, "name": "o/r", "description": "", "language": "",
                "stars": 0, "forks": 0, "topics": [], "license": ""}

    async def _readme(o, r):
        return ""

    async def _sum(text, language="zh-CN", content_type="article"):
        return "S"

    monkeypatch.setattr(ct, "_fetch_github_repo_info", _info)
    monkeypatch.setattr(ct, "_fetch_github_readme", _readme)
    monkeypatch.setattr(ct, "_llm_summarize", _sum)
    res = await ct._handle_github("https://github.com/o/r", "zh-CN")
    assert res["method"] == "github_title_only"


# ══════════════════════════════ B站编排 _handle_bilibili ══════════════════════════════

async def test_handle_bilibili_bad_url():
    res = await ct._handle_bilibili("https://bilibili.com/video/av1", "zh-CN")
    assert res["success"] is False and "BV" in res["result"]


async def test_handle_bilibili_info_fail(monkeypatch):
    async def _info(bvid):
        return {"success": False, "result": "api down"}

    monkeypatch.setattr(ct, "_fetch_bilibili_info", _info)
    res = await ct._handle_bilibili("https://bilibili.com/video/BV1bsMw6VE3r", "zh-CN")
    assert res["success"] is False


async def test_handle_bilibili_subtitle_channel(monkeypatch):
    async def _info(bvid):
        return {"success": True, "title": "T", "author": "UP", "duration": "1:00",
                "description": "", "bvid": bvid, "cid": 5}

    async def _subs(bvid, cid=0):
        return "字" * 200

    async def _sum(text, language="zh-CN", content_type="article"):
        return "SUM"

    monkeypatch.setattr(ct, "_fetch_bilibili_info", _info)
    monkeypatch.setattr(ct, "_fetch_bilibili_subtitles", _subs)
    monkeypatch.setattr(ct, "_llm_summarize", _sum)
    res = await ct._handle_bilibili("https://bilibili.com/video/BV1bsMw6VE3r", "zh-CN")
    assert res["method"] == "bilibili_subtitles" and res["title"] == "T"


async def test_handle_bilibili_stt_channel(monkeypatch, tmp_path):
    audio_file = tmp_path / "fake.m4s"; audio_file.write_bytes(b"x")
    wav_file = tmp_path / "fake.wav"; wav_file.write_bytes(b"x")

    async def _info(bvid):
        return {"success": True, "title": "T", "author": "", "duration": "1:00",
                "description": "", "bvid": bvid, "cid": 5}

    async def _subs(bvid, cid=0):
        return "短"

    async def _audio_url(bvid, cid):
        return "https://a.m4s"

    async def _download(u):
        return str(audio_file)

    def _conv(p):
        return str(wav_file)

    def _stt(p):
        return "语" * 100

    async def _sum(text, language="zh-CN", content_type="article"):
        return "SUM"

    monkeypatch.setattr(ct, "_fetch_bilibili_info", _info)
    monkeypatch.setattr(ct, "_fetch_bilibili_subtitles", _subs)
    monkeypatch.setattr(ct, "_fetch_bilibili_audio_url", _audio_url)
    monkeypatch.setattr(ct, "_download_bilibili_audio", _download)
    monkeypatch.setattr(ct, "_convert_audio_to_wav", _conv)
    monkeypatch.setattr(ct, "_speech_to_text_chunked", _stt)
    monkeypatch.setattr(ct, "_llm_summarize", _sum)
    res = await ct._handle_bilibili("https://bilibili.com/video/BV1bsMw6VE3r", "zh-CN")
    assert res["method"] == "bilibili_stt"
    assert not audio_file.exists() and not wav_file.exists()   # finally 清理临时文件


async def test_handle_bilibili_stt_cleanup_failure_swallowed(monkeypatch, tmp_path):
    # audio_path / wav_path 为目录 → finally 里 unlink 抛错，被 except 吞掉
    adir = tmp_path / "a.m4s"; adir.mkdir()
    wdir = tmp_path / "a.wav"; wdir.mkdir()

    async def _info(bvid):
        return {"success": True, "title": "T", "author": "", "duration": "1:00",
                "description": "", "bvid": bvid, "cid": 5}

    async def _subs(bvid, cid=0):
        return ""

    async def _audio_url(bvid, cid):
        return "https://a.m4s"

    async def _download(u):
        return str(adir)

    monkeypatch.setattr(ct, "_fetch_bilibili_info", _info)
    monkeypatch.setattr(ct, "_fetch_bilibili_subtitles", _subs)
    monkeypatch.setattr(ct, "_fetch_bilibili_audio_url", _audio_url)
    monkeypatch.setattr(ct, "_download_bilibili_audio", _download)
    monkeypatch.setattr(ct, "_convert_audio_to_wav", lambda p: str(wdir))
    monkeypatch.setattr(ct, "_speech_to_text_chunked", lambda p: "语" * 100)

    async def _sum(*a, **k):
        return "S"

    monkeypatch.setattr(ct, "_llm_summarize", _sum)
    res = await ct._handle_bilibili("https://bilibili.com/video/BV1bsMw6VE3r", "zh-CN")
    assert res["method"] == "bilibili_stt"
    assert adir.exists() and wdir.exists()   # 目录未被误删


async def test_handle_bilibili_description_fallback(monkeypatch):
    async def _info(bvid):
        return {"success": True, "title": "T", "author": "", "duration": "",
                "description": "这是一段足够长的视频简介内容", "bvid": bvid, "cid": 5}

    async def _subs(bvid, cid=0):
        return ""

    async def _audio_url(bvid, cid):
        raise RuntimeError("no audio")   # 触发 except 分支

    async def _sum(text, language="zh-CN", content_type="article"):
        return "SUM"

    monkeypatch.setattr(ct, "_fetch_bilibili_info", _info)
    monkeypatch.setattr(ct, "_fetch_bilibili_subtitles", _subs)
    monkeypatch.setattr(ct, "_fetch_bilibili_audio_url", _audio_url)
    monkeypatch.setattr(ct, "_llm_summarize", _sum)
    res = await ct._handle_bilibili("https://bilibili.com/video/BV1bsMw6VE3r", "zh-CN")
    assert res["method"] == "bilibili_description"


async def test_handle_bilibili_title_only(monkeypatch):
    async def _info(bvid):
        return {"success": True, "title": "T", "author": "", "duration": "",
                "description": "", "bvid": bvid, "cid": 5}

    async def _subs(bvid, cid=0):
        return ""

    async def _audio_url(bvid, cid):
        return ""    # 无音频 → transcript 空

    async def _sum(text, language="zh-CN", content_type="article"):
        return "SUM"

    monkeypatch.setattr(ct, "_fetch_bilibili_info", _info)
    monkeypatch.setattr(ct, "_fetch_bilibili_subtitles", _subs)
    monkeypatch.setattr(ct, "_fetch_bilibili_audio_url", _audio_url)
    monkeypatch.setattr(ct, "_llm_summarize", _sum)
    res = await ct._handle_bilibili("https://bilibili.com/video/BV1bsMw6VE3r", "zh-CN")
    assert res["method"] == "bilibili_title_only"


# ══════════════════════════════ 文章 / 视频 编排 ══════════════════════════════

async def test_fetch_and_extract_success(monkeypatch):
    async def _fetch(url, max_chars=8000):
        return {"success": True, "result": "正文", "title": "T", "url": "https://final"}

    monkeypatch.setattr(web_tools, "fetch_url", _fetch)
    res = await ct._fetch_and_extract("https://x/a")
    assert res == {"success": True, "text": "正文", "title": "T", "final_url": "https://final"}


async def test_fetch_and_extract_failure(monkeypatch):
    async def _fetch(url, max_chars=8000):
        return {"success": False, "result": "fail"}

    monkeypatch.setattr(web_tools, "fetch_url", _fetch)
    res = await ct._fetch_and_extract("https://x/a")
    assert res["success"] is False


async def test_download_video_success(monkeypatch):
    _install_async_client(monkeypatch, stream_chunks=[b"12", b"34"])
    path = await ct._download_video("https://x/a.mp4")
    try:
        with open(path, "rb") as f:
            assert f.read() == b"1234"
    finally:
        if os.path.exists(path):
            os.unlink(path)


async def test_download_video_stream_error(monkeypatch):
    _install_async_client(monkeypatch, stream_raise=True)
    with pytest.raises(RuntimeError):
        await ct._download_video("https://x/a.mp4")


async def test_handle_article_success(monkeypatch):
    async def _fetch(url):
        return {"success": True, "text": "正文内容", "title": "T", "final_url": "https://f"}

    async def _sum(text, language="zh-CN", content_type="article"):
        return "SUMMARY"

    monkeypatch.setattr(ct, "_fetch_and_extract", _fetch)
    monkeypatch.setattr(ct, "_llm_summarize", _sum)
    res = await ct._handle_article("https://x/a", "zh-CN")
    assert res["type"] == "article" and res["summary"] == "SUMMARY" and res["source_url"] == "https://f"


async def test_handle_article_fetch_fail(monkeypatch):
    async def _fetch(url):
        return {"success": False, "result": "err"}

    monkeypatch.setattr(ct, "_fetch_and_extract", _fetch)
    res = await ct._handle_article("https://x/a", "zh-CN")
    assert res["success"] is False


async def test_handle_video_success(monkeypatch, tmp_path):
    video = tmp_path / "v.mp4"; video.write_bytes(b"x")

    async def _download(url):
        return str(video)

    def _extract(p):
        return {"success": True, "text": "视频文字", "method": "stt"}

    async def _sum(text, language="zh-CN", content_type="article"):
        return "VS"

    monkeypatch.setattr(ct, "_download_video", _download)
    monkeypatch.setattr(ct, "_extract_video_text", _extract)
    monkeypatch.setattr(ct, "_llm_summarize", _sum)
    res = await ct._handle_video("https://x/a.mp4", "zh-CN")
    assert res["success"] and res["method"] == "stt"
    assert not video.exists()   # finally 清理


async def test_handle_video_extract_fail(monkeypatch, tmp_path):
    video = tmp_path / "v.mp4"; video.write_bytes(b"x")

    async def _download(url):
        return str(video)

    monkeypatch.setattr(ct, "_download_video", _download)
    monkeypatch.setattr(ct, "_extract_video_text", lambda p: {"success": False, "result": "no"})
    res = await ct._handle_video("https://x/a.mp4", "zh-CN")
    assert res["success"] is False


async def test_handle_video_cleanup_failure_swallowed(monkeypatch, tmp_path):
    # video_path 为目录 → finally 里 os.unlink(目录) 抛错，被 except 吞掉
    d = tmp_path / "vdir.mp4"
    d.mkdir()

    async def _download(url):
        return str(d)

    monkeypatch.setattr(ct, "_download_video", _download)
    monkeypatch.setattr(ct, "_extract_video_text", lambda p: {"success": False, "result": "no"})
    res = await ct._handle_video("https://x/a.mp4", "zh-CN")
    assert res["success"] is False
    assert d.exists()   # 目录未被误删


async def test_handle_video_download_exception(monkeypatch):
    async def _download(url):
        raise RuntimeError("net died")

    monkeypatch.setattr(ct, "_download_video", _download)
    res = await ct._handle_video("https://x/a.mp4", "zh-CN")
    assert res["success"] is False and "视频处理失败" in res["result"]


# ══════════════════════════════ 统一入口 analyze_content ══════════════════════════════

async def test_analyze_content_empty_url():
    res = await ct.analyze_content("   ")
    assert res["success"] is False and "空" in res["result"]


async def test_analyze_content_bad_scheme():
    res = await ct.analyze_content("ftp://example.com/x")
    assert res["success"] is False and "http" in res["result"]


async def test_analyze_content_routes_bilibili(monkeypatch):
    async def _b(url, language):
        return {"success": True, "route": "bilibili"}

    monkeypatch.setattr(ct, "_handle_bilibili", _b)
    assert (await ct.analyze_content("https://www.bilibili.com/video/BV1bsMw6VE3r"))["route"] == "bilibili"


async def test_analyze_content_routes_github(monkeypatch):
    async def _g(url, language):
        return {"success": True, "route": "github"}

    monkeypatch.setattr(ct, "_handle_github", _g)
    assert (await ct.analyze_content("https://github.com/o/r"))["route"] == "github"


async def test_analyze_content_routes_video(monkeypatch):
    async def _v(url, language):
        return {"success": True, "route": "video"}

    monkeypatch.setattr(ct, "_handle_video", _v)
    assert (await ct.analyze_content("https://x/a.mp4"))["route"] == "video"


async def test_analyze_content_routes_article(monkeypatch):
    async def _a(url, language):
        return {"success": True, "route": "article"}

    monkeypatch.setattr(ct, "_handle_article", _a)
    assert (await ct.analyze_content("https://x/a.html"))["route"] == "article"


# ══════════════════════════════ 文件安全扫描 scan_file_safety ══════════════════════════════

def test_scan_file_not_exist(tmp_path):
    res = ct.scan_file_safety(str(tmp_path / "nope.txt"))
    assert res["risk_level"] == "unknown"


def test_scan_clean_small_txt(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("普通内容", encoding="utf-8")
    res = ct.scan_file_safety(str(f))
    assert res["risk_level"] == "low" and res["risks"] == []


def test_scan_bat_suspicious_powershell(tmp_path):
    f = tmp_path / "run.bat"
    f.write_text("powershell -enc QQBBAEMA\n", encoding="utf-8")
    res = ct.scan_file_safety(str(f))
    assert res["risk_level"] == "high"
    assert any(r["type"] == "suspicious_command" for r in res["risks"])


def test_scan_bat_exe_invocation_only(tmp_path):
    f = tmp_path / "build.bat"
    f.write_text("foo.exe /build\n", encoding="utf-8")
    res = ct.scan_file_safety(str(f))
    assert res["risk_level"] == "medium"
    assert any(r["type"] == "exe_invocation" for r in res["risks"])


@pytest.mark.parametrize("name", ["tool.exe", "screen.scr", "script.vbs", "run.ps1"])
def test_scan_executable_types(tmp_path, name):
    f = tmp_path / name
    f.write_bytes(b"MZ")
    res = ct.scan_file_safety(str(f))
    assert any(r["type"] == "executable" for r in res["risks"])


def test_scan_suspicious_filename(tmp_path):
    f = tmp_path / "photoshop_crack.txt"
    f.write_text("x", encoding="utf-8")
    res = ct.scan_file_safety(str(f))
    assert res["risk_level"] == "high"
    assert any(r["type"] == "suspicious_name" for r in res["risks"])


def test_scan_large_obfuscated_txt(tmp_path):
    f = tmp_path / "payload.txt"
    long_line = "A" * 600
    body = "import base64\n" + long_line + "\n" + ("filler line\n" * 9000)
    f.write_text(body, encoding="utf-8")
    assert f.stat().st_size > 100 * 1024
    res = ct.scan_file_safety(str(f))
    types = {r["type"] for r in res["risks"]}
    assert {"large_text", "obfuscation", "long_lines"} <= types
    assert res["risk_level"] == "high"


def test_scan_medium_two_medium_risks(tmp_path):
    # 大文件(medium) + 超长行(medium)，且不含混淆特征 → medium_count>=2 分支
    f = tmp_path / "big.txt"
    long_line = "A" * 600
    f.write_text(long_line + "\n" + ("plain filler\n" * 9000), encoding="utf-8")
    assert f.stat().st_size > 100 * 1024
    res = ct.scan_file_safety(str(f))
    types = {r["type"] for r in res["risks"]}
    assert {"large_text", "long_lines"} <= types
    assert "obfuscation" not in types
    assert res["risk_level"] == "medium"


def test_scan_single_medium_risk(tmp_path):
    f = tmp_path / "app.exe"
    f.write_bytes(b"MZ")
    res = ct.scan_file_safety(str(f))
    assert res["risk_level"] == "medium"
    assert "谨慎" in res["recommendation"]


def test_scan_bat_read_error_is_swallowed(tmp_path):
    # 目录名以 .bat 结尾 → open() 抛 IsADirectoryError，被 except 吞掉
    d = tmp_path / "weird.bat"
    d.mkdir()
    res = ct.scan_file_safety(str(d))
    assert res["risk_level"] == "low"    # 无风险项，不崩溃


def test_scan_txt_read_error_is_swallowed(tmp_path, monkeypatch):
    d = tmp_path / "weird.txt"
    d.mkdir()
    # 骗过大小阈值判断，再让 open() 因是目录而失败
    monkeypatch.setattr(ct.os.path, "getsize", lambda p: 200 * 1024)
    res = ct.scan_file_safety(str(d))
    assert any(r["type"] == "large_text" for r in res["risks"])


# ──────────────────────────────────────────────────────────────────────────────
# 已修复（2026-10-14）：
#   1. `_get_wav_duration` 曾把 Duration 小数部分 /10（百分秒当整十）：`00:01:30.50`
#      → 95.0；已改为 /100 → 90.5。test_get_wav_duration_parses 参数化守护。
#   2. `_is_bilibili_url` 曾用 `"bilibili.com" in host` 子串判断，`notbilibili.com` /
#      `bilibili.com.evil.com` 会误命中；已改为 `host == "bilibili.com" or
#      host.endswith(".bilibili.com")`。test_is_bilibili_url 参数化守护。
# ──────────────────────────────────────────────────────────────────────────────
