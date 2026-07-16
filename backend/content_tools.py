"""Zenith v2 统一内容分析工具 — URL → 识别类型 → 提取文字 → LLM 总结

analyze_content: 自动识别文章/视频，提取文字后生成结构化摘要。
- 文章: 复用 web_fetch 抓取正文 → 丢给 LLM 总结
- 视频: 下载 → 优先软字幕 → 降级音频转写 → LLM 总结

依赖: httpx + bs4 (已有), imageio-ffmpeg + SpeechRecognition (新增)
"""
from __future__ import annotations

import os
import re
import logging
import tempfile
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("zenith.content")

VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.flv', '.wmv', '.m4v', '.3gp'}
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
MAX_TEXT_CHARS = 8000
MAX_PROMPT_CHARS = 6000

# Bilibili 公开 API 请求头（必须有 Referer，否则会被拦截）
BILIBILI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}

# ---------------------------------------------------------------------------
# FFmpeg helper
# ---------------------------------------------------------------------------

def _get_ffmpeg() -> str:
    """获取 ffmpeg 二进制路径: imageio-ffmpeg 捆绑版 > 系统 PATH"""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"

# ---------------------------------------------------------------------------
# URL 类型检测
# ---------------------------------------------------------------------------

def _is_video_url(url: str) -> bool:
    """根据扩展名判断是否为视频直链"""
    ext = Path(urlparse(url).path.lower()).suffix
    return ext in VIDEO_EXTENSIONS


def _is_bilibili_url(url: str) -> bool:
    """判断是否为 B站视频页面（需要走 API 而非静态抓取）"""
    host = urlparse(url).hostname or ""
    return "bilibili.com" in host


def _extract_bvid(url: str) -> str:
    """从 B站 URL 提取 BV 号，例如 BV1bsMw6VE3r"""
    # 匹配 BV 号模式: BV + 10 位字母数字
    m = re.search(r"(BV[a-zA-Z0-9]{10})", url)
    return m.group(1) if m else ""


async def _fetch_bilibili_info(bvid: str) -> dict:
    """调用 B站公开 API 获取视频标题和简介"""
    api_url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(api_url, headers=BILIBILI_HEADERS)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning("Bilibili view API failed: %s", e)
        return {"success": False, "result": f"B站 API 请求失败: {e}"}

    api_code = data.get("code", -1)
    if api_code != 0:
        return {"success": False,
                "result": f"B站 API 返回错误 (code={api_code}): {data.get('message', '未知')}"}

    video_data = data.get("data", {})
    title = video_data.get("title", "")
    desc = video_data.get("desc", "")
    owner = video_data.get("owner", {})
    author = owner.get("name", "")
    duration_sec = video_data.get("duration", 0)
    duration_str = f"{duration_sec // 60}:{duration_sec % 60:02d}"

    cid = video_data.get("cid", 0)

    return {
        "success": True,
        "title": title,
        "description": desc,
        "author": author,
        "duration": duration_str,
        "bvid": bvid,
        "cid": cid,
    }


async def _fetch_bilibili_subtitles(bvid: str, cid: int = 0) -> str:
    """获取 B站视频的 CC 字幕文本，无字幕返回空字符串。
    注意: player v2 API 必须传 cid，否则返回 -400 错误。"""
    params = f"bvid={bvid}"
    if cid:
        params += f"&cid={cid}"
    api_url = f"https://api.bilibili.com/x/player/v2?{params}"
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(api_url, headers=BILIBILI_HEADERS)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.info("Bilibili subtitle list fetch skipped: %s", e)
        return ""

    if data.get("code") != 0:
        return ""

    subtitle_list = (
        data.get("data", {})
        .get("subtitle", {})
        .get("subtitles", [])
    )
    if not subtitle_list:
        return ""

    # 优先中文 → 其他语言兜底
    sub_url = ""
    for sub in subtitle_list:
        sub_url = sub.get("subtitle_url", "")
        if sub.get("lan", "") in ("zh-CN", "zh-Hans", "zh", "ai-zh"):
            break

    if not sub_url:
        sub_url = subtitle_list[0].get("subtitle_url", "")

    if not sub_url:
        return ""

    # 字幕 URL 可能是相对路径，补全
    if sub_url.startswith("//"):
        sub_url = "https:" + sub_url

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(sub_url, headers=BILIBILI_HEADERS)
            r.raise_for_status()
            sub_data = r.json()
    except Exception as e:
        logger.info("Bilibili subtitle fetch failed: %s", e)
        return ""

    # 从字幕 JSON 提取纯文本
    body = sub_data.get("body", [])
    lines = []
    for item in body:
        content = item.get("content", "").strip()
        if content:
            lines.append(content)

    return " ".join(lines)


async def _fetch_bilibili_audio_url(bvid: str, cid: int) -> str:
    """通过 playurl API 获取 B站视频的音频流 URL (DASH 格式, 无需登录)"""
    api_url = f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&qn=16&fnval=16"
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(api_url, headers=BILIBILI_HEADERS)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning("Bilibili playurl API failed: %s", e)
        return ""

    if data.get("code") != 0:
        logger.info("Bilibili playurl error: %s", data.get("message", ""))
        return ""

    dash = data.get("data", {}).get("dash", {})
    audio_streams = dash.get("audio", [])
    if not audio_streams:
        return ""

    # 选最低码率的音频流（文件最小，STT 够用）
    audio_streams.sort(key=lambda x: x.get("bandwidth", 999999))
    return audio_streams[0].get("baseUrl", "")


async def _download_bilibili_audio(audio_url: str) -> str:
    """下载 B站音频流 (m4s 格式) 到临时文件, 返回路径"""
    tmp = tempfile.NamedTemporaryFile(suffix=".m4s", delete=False)
    tmp_path = tmp.name
    tmp.close()

    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            async with client.stream("GET", audio_url, headers=BILIBILI_HEADERS) as resp:
                resp.raise_for_status()
                downloaded = 0
                with open(tmp_path, 'wb') as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if downloaded > MAX_DOWNLOAD_BYTES:
                            break
        logger.info("Bilibili audio downloaded: %d bytes", downloaded)
        return tmp_path
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _convert_audio_to_wav(audio_path: str) -> str:
    """将音频文件 (m4s/m4a/mp4 等) 转换为 16kHz mono WAV, 返回路径或空字符串"""
    ffmpeg = _get_ffmpeg()
    output = audio_path + ".wav"
    try:
        subprocess.run(
            [ffmpeg, "-i", audio_path, "-vn", "-acodec", "pcm_s16le",
             "-ar", "16000", "-ac", "1", "-y", output],
            capture_output=True, timeout=120, check=True,
        )
        return output if os.path.getsize(output) > 1024 else ""
    except Exception as e:
        logger.info("Audio to WAV conversion failed: %s", e)
        return ""


def _get_wav_duration(wav_path: str) -> float:
    """获取 WAV 文件时长 (秒)"""
    ffmpeg = _get_ffmpeg()
    try:
        r = subprocess.run(
            [ffmpeg, "-i", wav_path],
            capture_output=True, text=True, timeout=10,
        )
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)", r.stderr)
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 10
    except Exception:
        pass
    return 0.0


def _split_wav(wav_path: str, segment_seconds: int = 300) -> list:
    """将 WAV 分割成多段 (默认 5 分钟一段), 返回文件路径列表"""
    ffmpeg = _get_ffmpeg()
    output_pattern = wav_path + ".chunk_%03d.wav"
    try:
        subprocess.run(
            [ffmpeg, "-i", wav_path, "-f", "segment",
             "-segment_time", str(segment_seconds),
             "-c", "copy", "-y", output_pattern],
            capture_output=True, timeout=60, check=True,
        )
        import glob
        chunks = sorted(glob.glob(wav_path + ".chunk_*.wav"))
        return chunks if chunks else [wav_path]
    except Exception as e:
        logger.info("Audio split failed: %s", e)
        return [wav_path]


def _speech_to_text_chunked(wav_path: str, max_chunk_seconds: int = 300) -> str:
    """对长音频分块进行 STT, 拼接结果。短音频直接转写。"""
    duration = _get_wav_duration(wav_path)

    if duration <= max_chunk_seconds:
        return _speech_to_text(wav_path)

    chunks = _split_wav(wav_path, max_chunk_seconds)
    if len(chunks) <= 1:
        return _speech_to_text(wav_path)

    logger.info("Audio split into %d chunks for STT (duration=%.0fs)", len(chunks), duration)
    transcripts = []
    for i, chunk in enumerate(chunks):
        text = _speech_to_text(chunk)
        if text:
            transcripts.append(text)
            logger.info("Chunk %d/%d transcribed: %d chars", i + 1, len(chunks), len(text))
        if chunk != wav_path and os.path.exists(chunk):
            try:
                os.unlink(chunk)
            except Exception:
                pass

    return " ".join(transcripts)


async def _handle_bilibili(url: str, language: str) -> dict:
    """处理 B站视频页面: API 获取信息 → CC字幕 → 音频STT → 简介兜底 → LLM 总结"""
    bvid = _extract_bvid(url)
    if not bvid:
        return {"success": False,
                "result": "无法从 URL 中提取 B站 BV 号，请确认链接格式"}

    # Step 1: 获取视频信息（含 cid）
    info = await _fetch_bilibili_info(bvid)
    if not info.get("success"):
        return info

    cid = info.get("cid", 0)

    # Step 2: 尝试 CC 字幕
    subtitle_text = await _fetch_bilibili_subtitles(bvid, cid)

    # 组装基础信息
    text_parts = [f"标题: {info['title']}"]
    if info.get("author"):
        text_parts.append(f"UP主: {info['author']}")
    if info.get("duration"):
        text_parts.append(f"时长: {info['duration']}")

    if subtitle_text and len(subtitle_text) > 100:
        # 通道 1: CC 字幕
        text_parts.append(f"\n字幕内容:\n{subtitle_text[:MAX_TEXT_CHARS]}")
        method = "bilibili_subtitles"
    else:
        # 通道 2: 下载音频 → STT 转写
        transcript = ""
        audio_path = None
        wav_path = None
        try:
            audio_url = await _fetch_bilibili_audio_url(bvid, cid)
            if audio_url:
                audio_path = await _download_bilibili_audio(audio_url)
                wav_path = _convert_audio_to_wav(audio_path)
                if wav_path:
                    transcript = _speech_to_text_chunked(wav_path)
        except Exception as e:
            logger.warning("Bilibili audio STT failed: %s", e)
        finally:
            if audio_path and os.path.exists(audio_path):
                try:
                    os.unlink(audio_path)
                except Exception:
                    pass
            if wav_path and os.path.exists(wav_path):
                try:
                    os.unlink(wav_path)
                except Exception:
                    pass

        if transcript and len(transcript) > 50:
            text_parts.append(f"\n语音转写:\n{transcript[:MAX_TEXT_CHARS]}")
            method = "bilibili_stt"
        elif info.get("description") and len(info["description"]) > 10:
            # 通道 3: 简介兜底
            text_parts.append(f"\n视频简介:\n{info['description'][:2000]}")
            method = "bilibili_description"
        else:
            method = "bilibili_title_only"

    text = "\n".join(text_parts)
    summary = await _llm_summarize(text, language, content_type="video")

    return {
        "success": True,
        "type": "video",
        "source": "bilibili",
        "summary": summary,
        "source_text": text[:2000],
        "source_url": url,
        "title": info["title"],
        "author": info.get("author", ""),
        "method": method,
    }

# ---------------------------------------------------------------------------
# GitHub 路径 — 公开 API 获取 README + 仓库信息
# ---------------------------------------------------------------------------

GITHUB_HEADERS = {
    "User-Agent": "Zenith/2.0",
    "Accept": "application/vnd.github.v3+json",
}

def _is_github_url(url: str) -> bool:
    """判断是否为 GitHub 仓库页面"""
    host = urlparse(url).hostname or ""
    return host == "github.com"

def _extract_github_repo(url: str) -> tuple:
    """从 GitHub URL 提取 owner/repo, 例如 owner='chinese-poetry', repo='chinese-poetry'"""
    path = urlparse(url).path.strip("/")
    parts = path.split("/")
    if len(parts) >= 2:
        return parts[0], parts[1]
    return "", ""

async def _fetch_github_repo_info(owner: str, repo: str) -> dict:
    """调用 GitHub API 获取仓库元数据: 描述/语言/Star/Fork"""
    api_url = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(api_url, headers=GITHUB_HEADERS)
            resp.raise_for_status()
            data = resp.json()
        return {
            "success": True,
            "name": data.get("full_name", f"{owner}/{repo}"),
            "description": data.get("description", ""),
            "language": data.get("language", ""),
            "stars": data.get("stargazers_count", 0),
            "forks": data.get("forks_count", 0),
            "topics": data.get("topics", []),
            "license": (data.get("license") or {}).get("spdx_id", ""),
            "homepage": data.get("homepage", ""),
        }
    except Exception as e:
        logger.warning("GitHub repo API failed: %s", e)
        return {"success": False, "result": f"GitHub API 请求失败: {e}"}

async def _fetch_github_readme(owner: str, repo: str) -> str:
    """调用 GitHub API 获取 README 原文 (base64 解码)"""
    import base64
    api_url = f"https://api.github.com/repos/{owner}/{repo}/readme"
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(api_url, headers=GITHUB_HEADERS)
            if resp.status_code == 404:
                return ""
            resp.raise_for_status()
            data = resp.json()
        content_b64 = data.get("content", "")
        encoding = data.get("encoding", "base64")
        if encoding == "base64" and content_b64:
            return base64.b64decode(content_b64).decode("utf-8", errors="replace")
        return ""
    except Exception as e:
        logger.info("GitHub README fetch failed: %s", e)
        return ""

async def _handle_github(url: str, language: str) -> dict:
    """处理 GitHub 仓库页面: API 获取 README + 仓库信息 → LLM 总结"""
    owner, repo = _extract_github_repo(url)
    if not owner or not repo:
        return {"success": False,
                "result": "无法从 URL 中提取 GitHub owner/repo, 请确认链接格式"}

    # 并行获取仓库信息和 README
    import asyncio
    info_task = _fetch_github_repo_info(owner, repo)
    readme_task = _fetch_github_readme(owner, repo)
    info, readme_text = await asyncio.gather(info_task, readme_task)

    if not info.get("success"):
        return info

    # 组装文字: 仓库信息 + README
    text_parts = [f"项目: {info['name']}"]
    if info.get("description"):
        text_parts.append(f"简介: {info['description']}")
    if info.get("language"):
        text_parts.append(f"语言: {info['language']}")
    text_parts.append(f"Star: {info.get('stars', 0)} | Fork: {info.get('forks', 0)}")
    if info.get("license"):
        text_parts.append(f"许可证: {info['license']}")
    if info.get("topics"):
        text_parts.append(f"标签: {', '.join(info['topics'])}")

    if readme_text and len(readme_text) > 50:
        text_parts.append(f"\nREADME:\n{readme_text[:MAX_TEXT_CHARS]}")
        method = "github_readme"
    elif info.get("description"):
        text_parts.append(f"\n仓库描述:\n{info['description']}")
        method = "github_description"
    else:
        method = "github_title_only"

    text = "\n".join(text_parts)
    summary = await _llm_summarize(text, language, content_type="repository")

    return {
        "success": True,
        "type": "repository",
        "source": "github",
        "summary": summary,
        "source_text": text[:2000],
        "source_url": url,
        "title": info["name"],
        "description": info.get("description", ""),
        "method": method,
    }

# ---------------------------------------------------------------------------
# 文章路径 — 复用 web_fetch
# ---------------------------------------------------------------------------

async def _fetch_and_extract(url: str) -> dict:
    """抓取网页正文, 返回 {success, text, title, final_url}"""
    from .web_tools import fetch_url
    result = await fetch_url(url, max_chars=MAX_TEXT_CHARS)

    if not result.get("success"):
        return result

    return {
        "success": True,
        "text": result["result"],
        "title": result.get("title", ""),
        "final_url": result.get("url", url),
    }

# ---------------------------------------------------------------------------
# 视频路径 — 下载 + 文字提取
# ---------------------------------------------------------------------------

async def _download_video(url: str) -> str:
    """流式下载视频到临时文件, 上限 50MB, 返回路径"""
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp_path = tmp.name
    tmp.close()

    try:
        from .web_tools import HEADERS
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            async with client.stream("GET", url, headers=HEADERS) as resp:
                resp.raise_for_status()
                downloaded = 0
                with open(tmp_path, 'wb') as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if downloaded > MAX_DOWNLOAD_BYTES:
                            break
        logger.info("Video downloaded: %s (%d bytes)", url[:60], downloaded)
        return tmp_path
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

def _extract_video_text(video_path: str) -> dict:
    """从视频提取文字: 软字幕 → 音频 STT → 元数据兜底"""
    ffmpeg = _get_ffmpeg()

    # 通道 1: 内嵌软字幕
    text = _extract_subtitles(ffmpeg, video_path)
    if text:
        return {"success": True, "text": text, "method": "subtitles"}

    # 通道 2: 音频提取 + 语音转文字
    audio_path = _extract_audio(ffmpeg, video_path)
    if audio_path:
        try:
            text = _speech_to_text(audio_path)
            if text:
                return {"success": True, "text": text, "method": "stt"}
        finally:
            if os.path.exists(audio_path):
                os.unlink(audio_path)

    # 通道 3: 元数据兜底
    return _video_metadata(ffmpeg, video_path)

def _extract_subtitles(ffmpeg: str, video_path: str) -> str:
    """ffmpeg 提取内嵌软字幕 (SRT 流) → 纯文本。无字幕流返回空字符串。"""
    try:
        r = subprocess.run(
            [ffmpeg, "-i", video_path, "-map", "0:s:0?",
             "-f", "srt", "-"],
            capture_output=True, text=True, timeout=30,
        )
        # 只取 stdout（SRT 正文），stderr 是 ffmpeg 日志不能当字幕
        raw = r.stdout.strip()
        if not raw or len(raw) < 50:
            return ""

        # 去掉 SRT 时间戳和序号
        lines = []
        for line in raw.split('\n'):
            line = line.strip()
            if not line or line.isdigit() or '-->' in line:
                continue
            lines.append(line)

        text = ' '.join(lines).strip()
        return text[:MAX_TEXT_CHARS] if len(text) > 60 else ""
    except Exception:
        return ""

def _extract_audio(ffmpeg: str, video_path: str) -> str:
    """视频音轨 → 16kHz mono WAV, 返回路径或空字符串"""
    output = video_path + ".audio.wav"
    try:
        subprocess.run(
            [ffmpeg, "-i", video_path, "-vn", "-acodec", "pcm_s16le",
             "-ar", "16000", "-ac", "1", "-y", output],
            capture_output=True, timeout=60, check=True,
        )
        return output if os.path.getsize(output) > 1024 else ""
    except Exception as e:
        logger.info("Audio extraction skipped: %s", e)
        return ""

def _speech_to_text(wav_path: str) -> str:
    """SiliconFlow STT: FunAudioLLM/SenseVoiceSmall (复用现有 api_key)"""
    from .config import get_api_key, get_api_base

    api_key = get_api_key()
    if not api_key:
        logger.info("STT skipped: api_key 未配置")
        return ""

    api_base = get_api_base().rstrip("/")
    stt_url = f"{api_base}/audio/transcriptions"

    try:
        import httpx
        with open(wav_path, "rb") as f:
            files = {"file": ("audio.wav", f, "audio/wav")}
            data = {"model": "FunAudioLLM/SenseVoiceSmall"}
            headers = {"Authorization": f"Bearer {api_key}"}
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(stt_url, files=files, data=data, headers=headers)
                resp.raise_for_status()
                result = resp.json()
                text = result.get("text", "").strip()
                if text:
                    logger.info("STT result: %d chars", len(text))
                return text
    except Exception as e:
        logger.info("STT failed: %s", e)
        return ""

def _video_metadata(ffmpeg: str, video_path: str) -> dict:
    """元数据兜底 — 无法提取文字时返回基本信息"""
    filename = Path(video_path).name
    try:
        r = subprocess.run(
            [ffmpeg, "-i", video_path],
            capture_output=True, text=True, timeout=10,
        )
        stderr = r.stderr
        duration = re.search(r"Duration:\s*(\S+)", stderr)
        resolution = re.search(r"(\d{2,4}x\d{2,4})", stderr)
        meta = f"视频: {filename}"
        if duration:
            meta += f", 时长: {duration.group(1)}"
        if resolution:
            meta += f", 分辨率: {resolution.group(1)}"
        meta += "\n（该视频无内嵌字幕且无法提取语音, 仅显示元数据）"
        return {"success": True, "text": meta, "method": "metadata"}
    except Exception:
        return {"success": False,
                "result": f"无法分析视频 ({filename}), 请确认文件可访问"}

# ---------------------------------------------------------------------------
# LLM 总结
# ---------------------------------------------------------------------------

async def _llm_summarize(text: str, language: str = "zh-CN", content_type: str = "article") -> str:
    """调用 LLM 生成结构化摘要，按内容类型适配 prompt"""
    if len(text) < 30:
        return "内容过短, 无法生成有意义的摘要。"

    # 按内容类型选择总结框架
    if content_type == "repository":
        prompt = (
            f"请总结以下 GitHub 项目内容, 用{language}回复。\n\n"
            f"## 项目简介\n(1-2 句话说明这是什么项目)\n\n"
            f"## 核心功能\n- 列出主要功能点\n\n"
            f"## 使用场景\n- 适合什么人/什么场景使用\n\n"
            f"## 技术栈\n主要语言和依赖\n\n"
            f"## 标签\n标签1, 标签2, 标签3\n\n"
            f"---\n{text[:MAX_PROMPT_CHARS]}"
        )
    elif content_type == "video":
        prompt = (
            f"请总结以下视频内容, 用{language}回复。\n\n"
            f"## 内容概述\n(2-3 句话概括视频讲了什么)\n\n"
            f"## 核心话题\n- 列出主要话题/知识点\n\n"
            f"## 标签\n标签1, 标签2, 标签3\n\n"
            f"---\n{text[:MAX_PROMPT_CHARS]}"
        )
    else:
        prompt = (
            f"请总结以下内容, 用{language}回复。\n\n"
            f"## 核心摘要\n(2-3 句话概括)\n\n"
            f"## 关键要点\n- ...\n\n"
            f"## 标签\n标签1, 标签2, 标签3\n\n"
            f"---\n{text[:MAX_PROMPT_CHARS]}"
        )

    from .llm_client import call_llm
    msg = await call_llm(
        [{"role": "user", "content": prompt}],
        temperature=0.3, max_tokens=1500,
    )
    content = msg.get("content", "")
    return content if not content.startswith("Error:") else f"摘要生成失败: {content}"

# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------

async def analyze_content(url: str, language: str = "zh-CN") -> dict:
    """分析任意 URL 内容: 文章或视频 → 提取 → 总结

    Returns:
        {success, type, summary, source_text, source_url, title?, method?}
    """
    url = url.strip()
    if not url:
        return {"success": False, "result": "URL 不能为空"}

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"success": False, "result": "仅支持 http/https 链接"}

    # B站视频页面 → 走公开 API（静态抓取拿不到 JS 渲染内容）
    if _is_bilibili_url(url):
        return await _handle_bilibili(url, language)

    # GitHub 仓库页面 → 走公开 API（README 在 JS 动态加载中）
    if _is_github_url(url):
        return await _handle_github(url, language)

    is_video = _is_video_url(url)

    if is_video:
        return await _handle_video(url, language)
    else:
        return await _handle_article(url, language)

async def _handle_article(url: str, language: str) -> dict:
    """文章/网页: 抓取正文 → LLM 总结"""
    fetch_result = await _fetch_and_extract(url)
    if not fetch_result.get("success"):
        return fetch_result

    text = fetch_result["text"]
    summary = await _llm_summarize(text, language)

    return {
        "success": True,
        "type": "article",
        "summary": summary,
        "source_text": text[:2000],
        "source_url": fetch_result.get("final_url", url),
        "title": fetch_result.get("title", ""),
    }

async def _handle_video(url: str, language: str) -> dict:
    """视频: 下载 → 文字提取 → LLM 总结"""
    video_path = None
    try:
        video_path = await _download_video(url)
        extract_result = _extract_video_text(video_path)

        if not extract_result.get("success"):
            return extract_result

        text = extract_result["text"]
        summary = await _llm_summarize(text, language, content_type="video")

        return {
            "success": True,
            "type": "video",
            "summary": summary,
            "source_text": text[:2000],
            "source_url": url,
            "method": extract_result.get("method", "unknown"),
        }
    except Exception as e:
        return {"success": False, "result": f"视频处理失败: {e}"}
    finally:
        if video_path and os.path.exists(video_path):
            try:
                os.unlink(video_path)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 文件安全扫描
# ---------------------------------------------------------------------------

# 可疑的文件类型组合（出现这些组合时提高风险等级）
_SUSPICIOUS_COMBOS = [
    {".bat", ".txt"},
    {".bat", ".exe"},
    {".bat", ".ps1"},
    {".scr", ".txt"},
    {".vbs", ".txt"},
    {".exe", ".dll", ".txt"},
]

# bat 脚本中的可疑命令模式
_SUSPICIOUS_BAT_PATTERNS = [
    (r"compiler\.exe", "调用 compiler.exe — 常见于混淆恶意代码的编译器"),
    (r"powershell\s+-e", "PowerShell 编码命令执行 — 常用于混淆恶意载荷"),
    (r"powershell\s+-enc", "PowerShell 编码命令执行 — 常用于混淆恶意载荷"),
    (r"certutil\s+-decode", "certutil 解码 — 常用于绕过安全检测解码恶意载荷"),
    (r"bitsadmin\s+/transfer", "bitsadmin 传输 — 常用于后台静默下载恶意文件"),
    (r"mshta\s+", "mshta 执行 — 常用于执行远程恶意脚本"),
    (r"wscript\s+", "wscript 执行 — 可能执行恶意 VBS 脚本"),
    (r"reg\s+add\s+HK", "注册表修改 — 可能设置持久化后门"),
    (r"taskkill\s+/f\s+/im", "强制结束进程 — 可能关闭安全软件"),
]

# 混淆代码特征
_OBFUSCATION_PATTERNS = [
    (r"eval\s*\(", "eval 调用 — 可能执行动态生成的代码"),
    (r"exec\s*\(", "exec 调用 — 可能执行动态生成的代码"),
    (r"base64", "Base64 编码 — 可能隐藏恶意载荷"),
    (r"\\x[0-9a-f]{2}", "十六进制编码 — 可能混淆代码"),
    (r"chr\s*\(\s*\d+\s*\)", "chr 编码 — 可能混淆字符串"),
    (r"\\u[0-9a-f]{4}", "Unicode 编码 — 可能混淆代码"),
]

# 大文件阈值（txt 超过此大小可能是混淆代码）
_LARGE_TXT_THRESHOLD = 100 * 1024  # 100KB


def scan_file_safety(file_path: str) -> dict:
    """扫描文件安全性，检测恶意脚本和混淆代码

    Returns:
        {
            "file_path": str,
            "risk_level": "high" | "medium" | "low",
            "risks": [{"severity": "high/medium/low", "type": str, "description": str}],
            "recommendation": str,
        }
    """
    risks = []

    if not os.path.exists(file_path):
        return {
            "file_path": file_path,
            "risk_level": "unknown",
            "risks": [],
            "recommendation": "文件不存在，无法扫描",
        }

    file_size = os.path.getsize(file_path)
    ext = Path(file_path).suffix.lower()
    filename = Path(file_path).name

    # 1. 检查 .bat 文件内容
    if ext == ".bat":
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                bat_content = f.read()
            for pattern, desc in _SUSPICIOUS_BAT_PATTERNS:
                if re.search(pattern, bat_content, re.IGNORECASE):
                    risks.append({
                        "severity": "high",
                        "type": "suspicious_command",
                        "description": desc,
                    })
            # bat 文件调用其他可执行文件
            if re.search(r"\b\w+\.exe\b", bat_content):
                risks.append({
                    "severity": "medium",
                    "type": "exe_invocation",
                    "description": "bat 脚本中调用 .exe 文件",
                })
        except Exception as e:
            logger.warning("Failed to read bat file: %s", e)

    # 2. 检查 .txt 文件大小和内容
    if ext == ".txt" and file_size > _LARGE_TXT_THRESHOLD:
        risks.append({
            "severity": "medium",
            "type": "large_text",
            "description": f"文本文件过大 ({file_size // 1024}KB)，可能包含混淆代码",
        })
        # 检查混淆特征
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                txt_content = f.read(10000)  # 只读前10KB
            for pattern, desc in _OBFUSCATION_PATTERNS:
                if re.search(pattern, txt_content, re.IGNORECASE):
                    risks.append({
                        "severity": "high",
                        "type": "obfuscation",
                        "description": desc,
                    })
            # 检查超长单行（混淆代码常见特征）
            lines = txt_content.split("\n")
            long_lines = [l for l in lines if len(l) > 500]
            if long_lines:
                risks.append({
                    "severity": "medium",
                    "type": "long_lines",
                    "description": f"发现 {len(long_lines)} 行超长文本 (>500字符)，可能为混淆代码",
                })
        except Exception as e:
            logger.warning("Failed to read txt file: %s", e)

    # 3. 检查 .exe / .scr / .vbs 等可执行文件
    if ext in (".exe", ".scr", ".vbs", ".ps1"):
        risks.append({
            "severity": "medium",
            "type": "executable",
            "description": f"可执行文件 ({ext})，建议在沙箱中运行前先进行杀毒扫描",
        })

    # 4. 检查文件名可疑特征
    suspicious_names = ["crack", "keygen", "patch", "activator", "loader"]
    for kw in suspicious_names:
        if kw in filename.lower():
            risks.append({
                "severity": "high",
                "type": "suspicious_name",
                "description": f"文件名包含可疑关键词: {kw}",
            })

    # 5. 判断风险等级
    high_count = sum(1 for r in risks if r["severity"] == "high")
    medium_count = sum(1 for r in risks if r["severity"] == "medium")

    if high_count > 0:
        risk_level = "high"
        recommendation = "高风险！建议立即删除该文件，并用杀毒软件扫描电脑"
    elif medium_count >= 2:
        risk_level = "medium"
        recommendation = "中等风险，建议进一步检查文件内容后再决定是否使用"
    elif medium_count == 1:
        risk_level = "medium"
        recommendation = "存在一定风险，建议谨慎处理"
    else:
        risk_level = "low"
        recommendation = "未发现明显安全风险"

    return {
        "file_path": file_path,
        "filename": filename,
        "file_size": file_size,
        "extension": ext,
        "risk_level": risk_level,
        "risks": risks,
        "recommendation": recommendation,
    }
