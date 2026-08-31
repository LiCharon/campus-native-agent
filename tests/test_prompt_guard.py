"""M15B-⑤ 提示注入防护：不可信声明 + 输入分隔符包裹。

决策（M15_PLAN §已拍板）：攻击面≈0（检索/工具结果不经 LLM），仅剩
"用户输入 + 历史原话"与"画像"两类可控内容；画像为正则+固定类别抽取
塞不进自由文本 → 本项只做 A 方案：三处 system prompt 末尾加不可信声明、
human 输入用 <student_input> 分隔符包裹。画像段隔离不做（危害最低面）。
"""

from campus_desk.entry.intent import _STRUCTURED_PROMPT, _build_human
from campus_desk.knowledge.decide import _DECIDE_PROMPT
from campus_desk.prompt_guard import UNTRUSTED_INPUT_NOTICE, wrap_input
from campus_desk.query.graph import _QUERY_PROMPT_TEMPLATE


def test_notice_covers_core_rules():
    """声明必须明确：输入区不可信、其中指令不执行。"""
    assert "不可信" in UNTRUSTED_INPUT_NOTICE
    assert "不执行" in UNTRUSTED_INPUT_NOTICE or "不得执行" in UNTRUSTED_INPUT_NOTICE
    assert "输入" in UNTRUSTED_INPUT_NOTICE


def test_wrap_input_uses_tags():
    """包裹函数用 <student_input> 标签，内容原样保留。"""
    wrapped = wrap_input("你好")
    assert wrapped.startswith("<student_input>")
    assert wrapped.endswith("</student_input>")
    assert "你好" in wrapped


def test_intent_system_contains_notice():
    """意图分类 prompt 末尾有不可信声明。"""
    assert UNTRUSTED_INPUT_NOTICE in _STRUCTURED_PROMPT


def test_decide_system_contains_notice():
    """追问决策 prompt 末尾有不可信声明。"""
    assert UNTRUSTED_INPUT_NOTICE in _DECIDE_PROMPT


def test_query_system_contains_notice():
    """工具选择 prompt 末尾有不可信声明。"""
    assert UNTRUSTED_INPUT_NOTICE in _QUERY_PROMPT_TEMPLATE


def test_build_human_wraps_input():
    """意图识别 human 段用分隔符包裹（无 recent 时）。"""
    h = _build_human("你好", None)
    assert h.startswith("<student_input>")
    assert h.endswith("</student_input>")
    assert "你好" in h


def test_build_human_wraps_recent_too():
    """意图识别 human 段：recent 历史也一并包裹（同样不可信）。"""
    h = _build_human("那栋楼呢", ["3号楼几点关门"])
    assert h.startswith("<student_input>")
    assert h.endswith("</student_input>")
    assert "那栋楼呢" in h
    assert "3号楼几点关门" in h
