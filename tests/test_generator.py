"""M17A-T5：生成节点测试——LLM 整合 hits 为自然语言 + [n] 引用。

注入假 LLM（不依赖网络）；异常语义 = 原样上抛，由 KnowledgeGraph.collect
兜底回退模板拼装（T6 覆盖回退路径）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from campus_desk.knowledge import generator
from campus_desk.prompt_guard import wrap_input

_HITS = [
    {
        "id": 7,
        "domain": "图书馆",
        "type": "info",
        "question": "图书馆几点闭馆",
        "keywords": "闭馆",
        "score": 0.9,
        "answer": "22:00 闭馆。",
    },
    {
        "id": 9,
        "domain": "教务",
        "type": "process",
        "question": "怎么办缓考",
        "keywords": "缓考",
        "score": 0.8,
        "answer": "材料：缓考申请表+证明。",
    },
]


class FakeLLM:
    """记录输入、返回固定文本的假 LLM。"""

    def __init__(self, reply: str = "整合后的回答 [1][2]。"):
        self.reply = reply
        self.prompts: list = []

    def invoke(self, messages):
        self.prompts.append(messages)
        return SimpleNamespace(content=self.reply)


@pytest.fixture
def fake_llm(monkeypatch):
    llm = FakeLLM()
    monkeypatch.setattr(generator, "_get_llm", lambda: llm)
    return llm


def test_generate_returns_llm_text_with_material(fake_llm):
    out = generator.generate_answer(_HITS, "图书馆几点闭馆")
    assert out == "整合后的回答 [1][2]。"
    messages = fake_llm.prompts[0]
    assert messages[0][0] == "system" and messages[1][0] == "human"
    # 条目按 `n. [domain/type] question → answer` 编号拼入材料
    assert "1. [图书馆/info] 图书馆几点闭馆 → 22:00 闭馆。" in messages[1][1]
    assert "2. [教务/process] 怎么办缓考" in messages[1][1]


def test_generate_system_prompt_hard_clauses(fake_llm):
    """system prompt 三条硬约束在位：禁编造/不足明说未找到/关键事实保原文。"""
    generator.generate_answer(_HITS, "图书馆几点闭馆")
    system = fake_llm.prompts[0][0][1]
    assert "不得编造" in system
    assert "未在知识库中找到相关信息" in system
    assert "保持原文表述" in system
    assert "[n]" in system


def test_generate_wraps_untrusted_input(fake_llm):
    """M15B-⑤ 语义延续：学生问题为不可信数据，经 wrap_input 包裹。"""
    generator.generate_answer(_HITS, "图书馆几点闭馆")
    human = fake_llm.prompts[0][1][1]
    assert human == wrap_input(f"学生问题：图书馆几点闭馆\n\n检索到的知识条目：\n{generator._material(_HITS)}")


def test_generate_truncates_long_answer(fake_llm):
    long_answer = "很" * 500
    hits = [{**_HITS[0], "answer": long_answer}]
    generator.generate_answer(hits, "q")
    human = fake_llm.prompts[0][1][1]
    assert "很" * 300 in human
    assert "很" * 301 not in human


def test_generate_raises_without_llm(monkeypatch):
    monkeypatch.setattr(generator, "_get_llm", lambda: None)
    with pytest.raises(RuntimeError):
        generator.generate_answer(_HITS, "q")


def test_generate_llm_exception_propagates(monkeypatch):
    class _BoomLLM:
        def invoke(self, messages):
            raise TimeoutError("LLM 超时")

    monkeypatch.setattr(generator, "_get_llm", lambda: _BoomLLM())
    with pytest.raises(TimeoutError):
        generator.generate_answer(_HITS, "q")


# conftest autouse 会把 generator._get_llm 换成掐断 stub——模块导入期捕获原实现供缓存用例还原
_REAL_GET_LLM = generator._get_llm


def test_llm_cache_built_once(monkeypatch):
    calls = []
    monkeypatch.setattr(
        generator, "build_tool_llm", lambda: (calls.append(1), FakeLLM())[1]
    )
    monkeypatch.setattr(generator, "_get_llm", _REAL_GET_LLM)  # 绕开 autouse 掐断
    generator._reset_llm_cache()
    try:
        first = generator._get_llm()
        second = generator._get_llm()
        assert first is second and len(calls) == 1  # 模块级缓存：只构造一次
    finally:
        generator._reset_llm_cache()
