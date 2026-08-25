#!/usr/bin/env python3
"""
Fact Check MCP Server — 事实核查验证
提供 verify_claim / check_groundedness / scan_contradictions 三个工具

运行: WorkBuddy 通过 stdio 自动启动
"""

import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path
from difflib import SequenceMatcher

from mcp.server import MCPServer

server = MCPServer("fact-check-mcp", version="2.0.0")

# ====== 常识规则库 ======

# 不可能存在的日期模式
# 2026-08-25 修复：移除硬编码 202[5-9]（2029 年后失效）与 [2-9]\d{3}（误伤 2019/2029）。
# 未来极远年份改由 verify_claim 内基于 datetime.now().year 的数值比较判定（见 _handle_verify_claim）。
IMPOSSIBLE_DATE_PATTERNS = [
    re.compile(p) for p in [
        r"\b(\d{4})年13月", r"\b(\d{4})年(\d{1,2})月32[日号]",
        r"\b(\d{4})年(?:1[3-9]|[2-9]\d)月",  # 任意年份的 13-99 月（不存在，无需限定年份）
    ]
]

# 常见幻觉模式
HALLUCINATION_MARKERS = [
    (r"根据\s*「[^」]{30,}」", "过度具体的引用"),
    (r"第\s*\d+\s*页.*第\s*\d+\s*行", "伪造的精确页码（未经核实）"),
    (r"(?:所有|全部|每一个|无一例外).*(?:都|均|皆)", "绝对化表述（需要充分证据）"),
    (r"研究表明|据[^，,]{0,5}报道|专家指出", "无具体来源的权威引用"),
]

# 已知事实快照（固定日期，用于检测时效性幻觉）
KNOWN_FACTS = {
    "python版本": {"latest": "3.13", "year": 2024},
    "openai": {"founded": 2015},
    "deepseek": {"r1_release": "2025-01"},
}


def _similarity(a: str, b: str) -> float:
    """计算两段文本的字符串相似度"""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _extract_entities(text: str) -> dict:
    """从文本中提取关键实体：日期、数字、路径、引号内容"""
    entities = {
        "dates": re.findall(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日号]?", text),
        "numbers": re.findall(r"\d+\.?\d*%?", text),
        "paths": re.findall(r"(?:/[a-zA-Z0-9._-]+)+/?", text),
        "quoted": re.findall(r'「([^」]+)」|"([^"]+)"', text),
        "urls": re.findall(r"https?://[^\s\)]+", text),
    }
    return {k: v for k, v in entities.items() if v}


# ====== 记忆库查询（可选，依赖 Zenith DB） ======

def _get_zenith_db_path() -> str | None:
    """获取 Zenith 数据库路径。优先级：环境变量 > 默认路径"""
    env_path = os.environ.get("ZENITH_DB_PATH", "").strip()
    if env_path and os.path.exists(env_path):
        return env_path
    # 默认路径（Windows 开发环境）
    default = os.path.join(
        os.environ.get("USERPROFILE", os.path.expanduser("~")),
        "下载文件", "新建文件夹", "zenith-v2", "data", "zenith.db"
    )
    # 尝试多个候选路径
    candidates = [
        default,
        r"D:\下载文件\新建文件夹\zenith-v2\data\zenith.db",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _search_memories(keyword: str, limit: int = 10) -> list[dict]:
    """在 Zenith memories 表中 FTS5 搜索相关记忆"""
    db_path = _get_zenith_db_path()
    if not db_path:
        return []
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        kw = keyword.strip()
        if not kw:
            conn.close()
            return []
        # FTS5 搜索
        results = []
        try:
            rows = conn.execute(
                "SELECT m.* FROM memories m "
                "JOIN memories_fts fts ON m.id = fts.rowid "
                "WHERE memories_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (kw, limit)
            ).fetchall()
            results = [dict(r) for r in rows]
        except Exception:
            pass
        # 降级: LIKE 搜索
        if not results:
            rows = conn.execute(
                "SELECT * FROM memories WHERE content LIKE ? OR keywords LIKE ? "
                "ORDER BY importance DESC LIMIT ?",
                (f"%{kw}%", f"%{kw}%", limit)
            ).fetchall()
            results = [dict(r) for r in rows]
        conn.close()
        return results
    except Exception:
        return []


def _check_memory_conflicts(claim: str, memories: list[dict]) -> list[dict]:
    """检查声明是否与记忆库中的内容冲突"""
    conflicts = []
    for mem in memories:
        content = mem.get("content", "")
        if not content:
            continue
        sim = _similarity(claim, content)
        if sim > 0.5:
            # 高相似但内容可能矛盾（需要更细粒度的实体对比）
            ent_claim = _extract_entities(claim)
            ent_mem = _extract_entities(content)
            for date_v in ent_claim.get("dates", []):
                if date_v in ent_mem.get("dates", []):
                    continue  # 日期一致，不算冲突
            for num_v in ent_claim.get("numbers", []):
                if num_v in ent_mem.get("numbers", []):
                    continue
            # 高相似但关键实体不匹配 → 潜在冲突
            conflicts.append({
                "memory_id": mem.get("id"),
                "memory_preview": content[:200],
                "similarity": round(sim, 2),
                "type": mem.get("type", ""),
                "importance": mem.get("importance", 0),
            })
    return conflicts[:5]  # 最多返回5个冲突


# ====== MCP 工具定义 ======

import json as _json


@server.tool()
async def verify_claim(claim: str, context: str = "") -> str:
    """验证一条声明是否有事实支撑。检查日期有效性、绝对化表述、虚假引用等常见幻觉模式。返回 verdict (supported/insufficient/contradiction/unsupported) 和 confidence 评分。"""
    return _json.dumps(await _handle_verify_claim({"claim": claim, "context": context}), ensure_ascii=False)


@server.tool()
async def check_groundedness(answer: str, reference_context: str) -> str:
    """检查 AI 回答是否基于给定的参考上下文。提取回答中的关键声明，逐一检查是否在 context 中有对应支撑。返回 grounded（有支撑）、ungrounded（无支撑）两部分。"""
    return _json.dumps(await _handle_check_groundedness({"answer": answer, "reference_context": reference_context}), ensure_ascii=False)


@server.tool()
async def scan_contradictions(text: str) -> str:
    """扫描文本中的自相矛盾之处。检测前后矛盾的数值、日期、方向性表述。"""
    return _json.dumps(await _handle_scan_contradictions({"text": text}), ensure_ascii=False)


@server.tool()
async def check_memory_conflict(claim: str, keyword: str = "") -> str:
    """检查声明是否与 Zenith 记忆库中的已有记忆冲突。通过 FTS5 搜索相关记忆，对比关键实体（日期/数字/名称）是否一致。需要 ZENITH_DB_PATH 环境变量或默认路径可访问。"""
    return _json.dumps(await _handle_check_memory_conflict({"claim": claim, "keyword": keyword}), ensure_ascii=False)


async def _handle_verify_claim(args: dict) -> dict:
    claim = args.get("claim", "").strip()
    context = args.get("context", "").strip()

    if not claim:
        return {"verdict": "error", "error": "claim 不能为空"}

    issues = []
    score = 1.0

    # 1. 检查不可能日期
    for pat in IMPOSSIBLE_DATE_PATTERNS:
        if pat.search(claim):
            issues.append({"type": "impossible_date", "match": pat.search(claim).group()})
            score -= 0.4

    # 1.5 未来极远年份 — 动态基准（>= 当前年+50 判为不可能），替代硬编码 202[5-9]
    _now_year = datetime.now().year
    for _m in re.finditer(r"\b(\d{4})年", claim):
        _y = int(_m.group(1))
        if _y >= _now_year + 50:
            issues.append({"type": "impossible_date", "match": f"{_y}年"})
            score -= 0.4
            break

    # 2. 检查幻觉标记
    for pat, desc in HALLUCINATION_MARKERS:
        m = re.search(pat, claim)
        if m:
            issues.append({"type": "hallucination_marker", "match": m.group(), "hint": desc})
            score -= 0.2

    # 3. 与 context 交叉验证
    if context:
        entities_claim = _extract_entities(claim)
        entities_ctx = _extract_entities(context)

        for date_val in entities_claim.get("dates", []):
            if date_val not in str(entities_ctx.get("dates", [])):
                issues.append({"type": "date_not_in_context", "value": date_val})
                score -= 0.15

        for num_val in entities_claim.get("numbers", [])[:5]:
            if num_val not in str(entities_ctx.get("numbers", [])):
                issues.append({"type": "number_not_in_context", "value": num_val})
                score -= 0.1

    # 4. 绝对化表述扣分
    abs_words = ["一定", "必然", "绝对", "毫无疑问", "肯定", "保证"]
    for w in abs_words:
        if w in claim:
            issues.append({"type": "absolute_language", "word": w})
            score -= 0.15
            break  # 只记一次

    # 5. 记忆库交叉验证（可选，依赖 Zenith DB 可访问）
    memory_conflicts = []
    try:
        search_kw = claim[:40]
        related_mems = _search_memories(search_kw, limit=5)
        if related_mems:
            memory_conflicts = _check_memory_conflicts(claim, related_mems)
            if memory_conflicts:
                issues.append({
                    "type": "memory_conflict",
                    "count": len(memory_conflicts),
                })
                score -= 0.2 * len(memory_conflicts)
    except Exception:
        pass  # DB 不可访问时静默降级

    score = max(0.0, min(1.0, score))

    if score >= 0.8:
        verdict = "supported"
    elif score >= 0.5:
        verdict = "insufficient"
    elif issues:
        verdict = "contradiction"
    else:
        verdict = "unsupported"

    return {
        "verdict": verdict,
        "confidence": round(score, 2),
        "issues": issues,
        "memory_conflicts": memory_conflicts,
        "checked_at": datetime.now().isoformat(),
    }


async def _handle_check_groundedness(args: dict) -> dict:
    answer = args.get("answer", "").strip()
    context = args.get("reference_context", "").strip()

    if not answer or not context:
        return {"error": "answer 和 reference_context 都是必需的"}

    # 将回答按句子拆分
    sentences = re.split(r"[。！？\n]", answer)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 5]

    grounded = []
    ungrounded = []

    for sent in sentences:
        sim = _similarity(sent, context)
        if sim > 0.3:
            grounded.append({"sentence": sent[:200], "similarity": round(sim, 2)})
        else:
            # 进一步检查关键实体是否在上下文中
            entities = _extract_entities(sent)
            key_matches = 0
            for key_list in entities.values():
                for item in key_list:
                    if item in context:
                        key_matches += 1
            if key_matches > 0:
                grounded.append({"sentence": sent[:200], "similarity": round(sim, 2),
                                 "entity_match": True})
            else:
                ungrounded.append(sent[:200])

    return {
        "grounded_count": len(grounded),
        "ungrounded_count": len(ungrounded),
        "grounded_ratio": round(len(grounded) / len(sentences), 2) if sentences else 0,
        "grounded": grounded[:10],
        "ungrounded": ungrounded[:10],
    }


async def _handle_scan_contradictions(args: dict) -> dict:
    text = args.get("text", "").strip()

    if not text:
        return {"error": "text 不能为空"}

    contradictions = []

    # 检测转折词标记的潜在矛盾
    contrast_patterns = [
        r"(?:但是|然而|不过|可是|却|反而|与之相反)",
        r"(?:一方面.*另一方面)",
    ]
    for pat in contrast_patterns:
        for m in re.finditer(pat, text):
            start = max(0, m.start() - 50)
            end = min(len(text), m.end() + 80)
            context = text[start:end].strip()
            # 找转折前后的数字/观点
            before = text[max(0, m.start() - 100):m.start()]
            after = text[m.end():min(len(text), m.end() + 100)]
            nums_before = re.findall(r"\d+", before)
            nums_after = re.findall(r"\d+", after)
            if nums_before and nums_after and nums_before != nums_after:
                contradictions.append({
                    "type": "numeric_shift",
                    "context": context[:150],
                    "before_numbers": nums_before,
                    "after_numbers": nums_after,
                })

    # 检测自相反表述
    self_contradict = [
        (r"(?:上升|增加|增长|提高).*(?:下降|减少|降低|回落)", "增减矛盾"),
        (r"(?:赞成|支持|肯定).*(?:反对|否定|拒绝)", "立场矛盾"),
    ]
    for pat, desc in self_contradict:
        for m in re.finditer(pat, text, re.DOTALL):
            start = max(0, m.start() - 30)
            end = min(len(text), m.end() + 30)
            contradictions.append({
                "type": desc,
                "context": text[start:end].strip()[:150],
            })

    # 5. 实体级矛盾检测（新建）：同类型实体出现但数值冲突
    entities = _extract_entities(text)
    # 检测矛盾日期（如：出现"2024年"又出现"2025年"表示同一事件）
    dates = entities.get("dates", [])
    if len(dates) > 1 and len(set(dates)) > 1:
        contradictions.append({
            "type": "date_divergence",
            "context": f"文本中出现多个不同日期: {list(set(dates))[:5]}",
        })
    # 检测矛盾数字（如：先说是3，后说是5）
    numbers = [n for n in entities.get("numbers", []) if n.replace(".", "").isdigit()]
    if len(numbers) >= 2:
        unique_nums = sorted(set(numbers), key=lambda x: float(x.replace("%", "")))
        if len(unique_nums) >= 4:  # 数字过多且分散，可能是正常数据
            pass
        elif len([n for n in unique_nums if n.endswith("%")]) >= 2:
            # 多个百分比，检查是否在同一语境下矛盾
            pct_vals = [float(n.replace("%", "")) for n in unique_nums if n.endswith("%")]
            if pct_vals and max(pct_vals) - min(pct_vals) > 10:
                contradictions.append({
                    "type": "percentage_divergence",
                    "context": f"百分比数值跨度大: {min(pct_vals)}% ~ {max(pct_vals)}%",
                })

    return {
        "contradiction_count": len(contradictions),
        "contradictions": contradictions[:10],
        "scanned_length": len(text),
    }


async def _handle_check_memory_conflict(args: dict) -> dict:
    claim = args.get("claim", "").strip()
    keyword = args.get("keyword", "").strip()

    if not claim:
        return {"error": "claim 不能为空"}

    search_kw = keyword if keyword else claim[:40]
    memories = _search_memories(search_kw, limit=5)

    if not memories:
        return {
            "found": False,
            "note": "未找到相关记忆（Zenith DB 不可达或关键词无匹配）",
            "keyword": search_kw,
        }

    conflicts = _check_memory_conflicts(claim, memories)
    return {
        "found": True,
        "memory_count": len(memories),
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
        "related_memories": [
            {
                "id": m.get("id"),
                "type": m.get("type"),
                "preview": m.get("content", "")[:150],
                "importance": m.get("importance"),
            }
            for m in memories[:5]
        ],
    }


# ====== 主入口 ======

async def main():
    await server.run_stdio_async()


if __name__ == "__main__":
    asyncio.run(main())
