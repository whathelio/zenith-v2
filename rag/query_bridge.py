r"""查询翻译桥（Query Bridge）—— 跨语言检索的可选中间层

问题（2026-10-08 实测）：
  嵌入模型是 BAAI/bge-small-zh（**纯中文**），但 nomad_ref 语料是**英文维基**。
  中文提问 → 英文语料：相似度 0.42–0.50（英文提问是 0.62–0.83），top-1 常错，
  且低于 0.60 地板 → 用户看到「知识库中未找到相关内容」。

对策：
  检索前用 LLM 把 CJK 提问译成英文，复用**现有向量库**（零重嵌入）。
  实测：纳尔逊·曼德拉 → "Who is Nelson Mandela and what did he do?" → 0.7116 PASS。

设计要点（全部为了「可回退、不添乱」）：
  1. **默认关闭**：`ZENITH_QUERY_BRIDGE=1` 才生效，否则完全等于旧行为。
  2. **按需触发**：提问不含 CJK → 不译（英文提问零开销）；
     目标集合本身是 CJK 语料 → 不译（中文问中文不需要桥）。
  3. **集合语言自动判定**：用一句中文探测词检索，看命中正文的 CJK 占比，
     **每进程每集合只探一次**并缓存 —— 不依赖任何硬编码集合名，也不改 core。
  4. **落盘缓存**：sha1(提问) → 译文，重复提问不再调用 LLM。
  5. **绝不因翻译失败而拒答**：任何异常都静默降级回原始提问。

外部依赖：无（复用 zotero_parse_rag_core 已有的 openai 客户端与 LLM_* 配置）。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from pathlib import Path

# ── 开关与阈值 ──
_CJK_MIN_RATIO = 0.15      # 提问中 CJK 字符占比低于此值 → 视为英文提问，不译
_PROBE_K = 3               # 集合语言探测取前 K 条
_PROBE_QUERY = "这是什么"

_LOCK = threading.Lock()
_LANG_CACHE: dict[str, bool] = {}     # collection -> 是否 CJK 语料
_MEM_CACHE: dict[str, str] = {}       # sha1(提问) -> 译文

_SYS_PROMPT = (
    "You are a translation engine for a bilingual retrieval system. "
    "Translate the user's query into a concise English search query, "
    "preserving every named entity, number and key term. "
    "Output ONLY the translation — no quotes, no notes, no explanation."
)


def enabled() -> bool:
    return os.environ.get("ZENITH_QUERY_BRIDGE", "0").strip() in ("1", "true", "yes", "on")


def cjk_ratio(s: str) -> float:
    """CJK 统一表意文字占比（0.0–1.0）。"""
    if not s:
        return 0.0
    n = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff" or "\u3400" <= ch <= "\u4dbf")
    return n / len(s)


def _cache_file() -> Path:
    try:
        from zotero_parse_rag_core import WORK_DIR
        base = Path(WORK_DIR)
    except Exception:
        base = Path(os.environ.get("ZENITH_RAG_WORK_DIR", "./zenith_rag"))
    return base / "query_bridge_cache.json"


def _load_disk_cache() -> dict:
    f = _cache_file()
    try:
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[bridge] 缓存读取失败（忽略）: {e}", file=sys.stderr)
    return {}


def _save_disk_cache(d: dict) -> None:
    f = _cache_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(f.suffix + f".tmp-{os.getpid()}")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, f)
    except Exception as e:
        print(f"[bridge] 缓存写入失败（忽略）: {e}", file=sys.stderr)


def _collection_is_cjk(collection: str | None) -> bool:
    """用一句中文探测词检索，看命中正文是否以 CJK 为主。每进程每集合只探一次。"""
    key = collection or "__default__"
    with _LOCK:
        if key in _LANG_CACHE:
            return _LANG_CACHE[key]
    is_cjk = False
    try:
        from zotero_parse_rag_core import get_store, get_embedder
        store = get_store(collection)
        emb = get_embedder().encode([_PROBE_QUERY], normalize_embeddings=True).tolist()[0]
        docs = (store.query(emb, top_k=_PROBE_K).get("documents") or [[]])[0]
        if docs:
            is_cjk = (sum(cjk_ratio(d) for d in docs) / len(docs)) >= _CJK_MIN_RATIO
    except Exception as e:
        print(f"[bridge] 集合语言探测失败，按「非 CJK」处理: {e}", file=sys.stderr)
        is_cjk = False
    with _LOCK:
        _LANG_CACHE[key] = is_cjk
    print(f"[bridge] 集合 {key} 语言判定: {'CJK（不译）' if is_cjk else '非CJK（需译）'}",
          file=sys.stderr)
    return is_cjk


def _translate(text: str) -> str:
    """调 LLM 翻译。复用 core 的配置与「关闭思考」逻辑，保证与生成步骤同源。"""
    from zotero_parse_rag_core import (LLM_BASE_URL, LLM_API_KEY, _chat_once, _extract_content)
    import openai
    client = openai.OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)
    resp = _chat_once(client, [
        {"role": "system", "content": _SYS_PROMPT},
        {"role": "user", "content": text},
    ])
    out = _extract_content(resp) or ""
    # 只取第一行，去掉模型可能加的引号
    out = out.strip().splitlines()[0].strip().strip('"').strip("'").strip()
    return out[:400]


def maybe_bridge(question: str, collection: str | None) -> tuple[str, dict | None]:
    """返回 (实际用于检索的提问, 桥接信息或 None)。**任何异常都退回原提问。**"""
    if not enabled():
        return question, None
    if cjk_ratio(question) < _CJK_MIN_RATIO:
        return question, None                      # 英文提问，零开销
    try:
        if _collection_is_cjk(collection):
            return question, None                  # 中文语料，不必译
    except Exception:
        return question, None

    h = hashlib.sha1(question.encode("utf-8")).hexdigest()
    with _LOCK:
        if h in _MEM_CACHE:
            return _MEM_CACHE[h], {"translated": True, "cached": True, "en": _MEM_CACHE[h]}

    disk = _load_disk_cache()
    if h in disk:
        with _LOCK:
            _MEM_CACHE[h] = disk[h]
        return disk[h], {"translated": True, "cached": True, "en": disk[h]}

    try:
        en = _translate(question)
    except Exception as e:
        print(f"[bridge] 翻译失败，回退裸检索: {type(e).__name__}: {e}", file=sys.stderr)
        return question, {"translated": False, "error": f"{type(e).__name__}: {e}"}

    if not en or en == question:
        return question, {"translated": False, "error": "译文为空或与原文相同"}

    with _LOCK:
        _MEM_CACHE[h] = en
    disk[h] = en
    _save_disk_cache(disk)
    return en, {"translated": True, "cached": False, "en": en}
