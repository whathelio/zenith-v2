"""Output Validator — 流式输出结束前的兜底检测"""
import re
import logging
from .. import database as db
from ..memory_engine import _similarity

# 绝对化词语模式
_ABSOLUTE_PATTERNS = [
    r'(肯定|绝对|100%|一定|必然|毫无疑问|毋庸置疑|百分百)',
]

# 高风险领域关键词
_HIGH_RISK_DOMAINS = [
    r'(医疗建议|诊断|处方|手术|用药)',
    r'(法律意见|诉讼|判决|合同条款|法律咨询)',
    r'(投资建议|必涨|必跌|稳赚|保证收益)',
]


def validate_output(text: str, conv_id: str = "") -> list[dict]:
    """
    检测最后一条 assistant 消息的问题。
    返回 warning 事件列表，每个包含 {level, type, message}。
    """
    if not text or len(text) < 10:
        return []

    warnings: list[dict] = []

    # 1. 绝对化表述检测
    for pattern in _ABSOLUTE_PATTERNS:
        matches = re.findall(pattern, text)
        if matches:
            # 检查是否有引用或来源支撑
            has_citation = bool(re.search(r'\[(?:来源|引用|参考|source)\]|https?://', text, re.I))
            if not has_citation:
                warnings.append({
                    "level": "warning",
                    "type": "absolute_claim",
                    "message": f"检测到绝对化表述（{', '.join(matches[:3])}），但未附来源引用。已标记为低置信度。",
                })
            break  # 只报一次

    # 2. 高风险领域越界检查
    for pattern in _HIGH_RISK_DOMAINS:
        if re.search(pattern, text):
            warnings.append({
                "level": "warning",
                "type": "high_risk_domain",
                "message": "模型输出涉及高风险领域，请以专业机构意见为准。AI 建议仅供参考。",
            })
            break

    # 3. 与历史记忆的矛盾检测（轻量）
    if conv_id and len(text) > 20:
        try:
            _check_memory_contradiction(text, warnings)
        except Exception as e:
            logging.getLogger("zenith.validators").debug("记忆矛盾检测失败: %s", e)

    return warnings


def _check_memory_contradiction(text: str, warnings: list[dict]):
    """检查输出是否与近期记忆存在明显矛盾"""
    # 提取文本中的关键断言（句号/换行分隔的第一句）
    sentences = re.split(r'[。\n]', text)
    key_sentences = [s.strip() for s in sentences[:5] if len(s.strip()) > 15]

    if not key_sentences:
        return

    for sentence in key_sentences[:2]:  # 只检查前2句
        # 用关键词搜索相关记忆
        kw = sentence[:20]
        candidates = db.mem_search(kw, limit=5)
        for mem in candidates:
            sim = _similarity(sentence, mem.get("content", ""))
            # 高相似度但内容相反 → 矛盾
            if sim > 0.5:
                mem_text = mem.get("content", "")
                # 简单检测否定词
                has_negation = bool(re.search(r'(不|否|没|别|非|无)', sentence))
                mem_has_negation = bool(re.search(r'(不|否|没|别|非|无)', mem_text))
                if has_negation != mem_has_negation and sim > 0.4:
                    warnings.append({
                        "level": "warning",
                        "type": "memory_contradiction",
                        "message": f"当前输出可能与历史记忆存在矛盾: \"{mem_text[:80]}...\"",
                    })
                    return
