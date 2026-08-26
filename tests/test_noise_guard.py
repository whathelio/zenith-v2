from backend.validators.noise_guard import strip_noise


def test_strip_conversation_trailer():
    # 真实格式：正文在前，Conversation 元数据尾部在后
    out = strip_noise("张三: 帮我查一下行情\n\nConversation info\nID: 123\nmessage_id: abc123")
    assert out == "张三: 帮我查一下行情", repr(out)


def test_strip_system_keys():
    assert strip_noise("session_id: xyz 帮我查行情") == "帮我查行情"
    assert strip_noise("message_id=abc 调研需求") == "调研需求"


def test_strip_edict_prefix():
    assert strip_noise("传旨：调研工业数据分析") == "调研工业数据分析"
    assert strip_noise("下旨（张三）：写周报") == "写周报"


def test_keep_real_content():
    t = "读了 https://arxiv.org/abs/2501.12948 这篇论文，代码在 D:/下载文件/x.py"
    assert "https://arxiv.org" in strip_noise(t), "URL 被误剥"
    assert "D:/下载文件/x.py" in strip_noise(t), "路径被误剥"
    code = "```python\nprint(1)\n```"
    assert strip_noise(code) == code, "代码块被误剥"
    assert strip_noise("用户偏好简洁中文") == "用户偏好简洁中文"


def test_no_false_positive():
    assert strip_noise("") == ""
    assert "conversation" in strip_noise("we had a conversation about AI"), "小写 conversation 被误剥"
