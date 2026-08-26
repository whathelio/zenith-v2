"""noise_guard.py — 记忆入库前的元数据噪声剥离

与 sanitize_guard.py 分工明确：
- sanitize_guard.py：检测明文密钥（安全维度），命中则拒绝写入。
- noise_guard.py：剥离平台元数据噪声（数据卫生维度），防止 LLM 把系统噪声当事实提取进记忆。

借鉴 edict 的 _sanitize_text 思路，但**更窄**：zenith 的提取输入是「用户消息 + AI 回复」
（routers/chat.py 的 combined），不是 edict 的「平台消息→任务标题」场景。因此：
- 只剥「明确是噪声」的平台元数据：Conversation 头部块、系统键值对、转发/下旨前缀。
- 绝不剥离文件路径 / URL / 代码块 —— 那些在 zenith 对话里是讨论内容，剥离会误伤。

原则：只做减法（剥噪声），不做加法；空输入原样返回。
"""
import re

# 平台系统元数据键（= 或 : 后跟一个 token）
_SYSTEM_KEY_RE = re.compile(
    r"\b(message_id|session_id|chat_id|open_id|user_id|tenant_key)\s*[:=]\s*\S+",
    re.IGNORECASE,
)

# 平台转发/下旨前缀（仅行首）
_EDICT_PREFIX_RE = re.compile(r"^(传旨|下旨|转发消息|转发)([（(][^)）]*[)）])?[：:\uff1a]\s*")


def strip_noise(text: str) -> str:
    """剥离平台元数据噪声，返回清洗后的文本。"""
    if not text:
        return text
    t = text.strip()
    # 1) 剥离 "Conversation" 元数据尾部块（平台导出消息会在正文后追加 Conversation info 元数据；
    #    大小写敏感，避免误伤正文里的 conversation 一词）
    t = re.split(r"\n*Conversation\b", t, maxsplit=1)[0].strip()
    # 2) 剥离系统元数据键值对
    t = _SYSTEM_KEY_RE.sub("", t)
    # 3) 剥离平台转发/下旨前缀（仅行首）
    t = _EDICT_PREFIX_RE.sub("", t)
    # 4) 压缩行内连续空白（保留换行结构）
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()
