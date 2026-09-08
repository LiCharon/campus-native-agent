"""M17A code review 修复（2026-09-08 补 zcode 欠的 review）。

对应三条审查发现，均为"关键事实保原文 / 不硬答"这一核心承诺上的缺口：

1. CRITICAL · 长条目硬截断切坏关键事实
   `_material` 按 300 字硬切。生产库 config/zjut_m11_data.json 572 条里有
   **42 条 answer 超 300 字（最长 2201）**，而测试库 zjut_local_data.json 最长
   234 字 —— 测试永远触不到，用户侧却会拿到掐头去尾的答案 + 自信的 [1] 引用。
   修法：改按句末标点/换行边界安全截断，并追加 _CLIP_MARK 让"被截断"可见。

2. IMPORTANT · 生成层说了"未找到"、outcome 却仍写 answer
   prompt 要求材料不足时明说"未在知识库中找到相关信息"，但 collect 只要 hits
   非空就写死 outcome=answer → 不追问、不 handoff、不落 bad_case、来源 chip 照挂。
   config.py 注释把 2 条硬假阳性"交生成节点兜底"，这条兜底此前只兜了文案。
   修法：生成结果含 NOT_FOUND_MARK 时回落未命中分支；文案常量单源（同时解掉
   judge_runner:200 硬编码同一串的耦合）。

3. IMPORTANT · 只兜异常、不兜空输出
   LLM 返回空 content → .strip() 得 "" → 透传成空气泡。修法：空则回退模板。
"""

from __future__ import annotations

from types import SimpleNamespace

from langgraph.checkpoint.memory import InMemorySaver

from campus_desk.knowledge import generator
from campus_desk.knowledge.generator import _CLIP_MARK, NOT_FOUND_MARK, _material
from campus_desk.knowledge.graph import build_knowledge_graph
from campus_desk.knowledge.search import assemble_answer


class FakeDecider:
    def __init__(self, sequence):
        self.sequence = list(sequence)

    def decide(self, history, user_text, missed, recent=None):
        return self.sequence.pop(0)


def _hit(answer: str) -> dict:
    return {
        "id": 7,
        "domain": "教务",
        "type": "process",
        "question": "项目评审流程",
        "keywords": "评审",
        "score": 0.9,
        "answer": answer,
    }


# ── 1. 安全截断 ────────────────────────────────────────────────────────


def test_material_clips_on_sentence_boundary():
    """超长 answer 不从句中硬切：截断点落在句末标点或换行上。"""
    # 每句 20 字，40 句 = 800 字，远超 _ANSWER_CLIP
    long_answer = "。".join(f"第{i}条说明学生需提交材料" for i in range(40))
    out = _material([_hit(long_answer)])
    body = out.split("→ ", 1)[1]
    assert _CLIP_MARK in body, "被截断必须有可见标记"
    # 去掉标记后，结尾应是完整一句（句末标点或换行），不是半个句子
    trimmed = body.replace(_CLIP_MARK, "").rstrip()
    assert trimmed[-1] in "。！？；\n", f"截断点在句中：…{trimmed[-12:]!r}"


def test_material_short_answer_untouched():
    """短条目保持原文，不加截断标记（关键事实保原文）。"""
    out = _material([_hit("22:00 闭馆。")])
    assert out.endswith("22:00 闭馆。")
    assert _CLIP_MARK not in out


def test_material_clips_unbreakable_text_with_mark():
    """无标点长文本（OCR 噪音常见）切不动标点时：仍截断 + 打标记，不静默丢内容。"""
    out = _material([_hit("很" * 900)])
    body = out.split("→ ", 1)[1]
    assert _CLIP_MARK in body
    assert len(body) < 900


# ── 2/3. 生成结果的语义兜底 ─────────────────────────────────────────────


def _seed_one(session_factory):
    from campus_desk.db.models import KnowledgeEntry

    with session_factory() as s, s.begin():
        s.query(KnowledgeEntry).delete()
        s.add(
            KnowledgeEntry(
                domain="教务",
                keywords="校历,寒假,时候",
                question="放假？",
                type="info",
                answer="寒假以通知为准。",
            )
        )


def _invoke(graph, thread="t-review"):
    return graph.invoke(
        {"user_input": "什么时候放寒假？"}, {"configurable": {"thread_id": thread}}
    )


def test_generated_not_found_goes_to_miss_branch(db_session_factory):
    """生成层明说"未找到" ⇒ 不得记成已答，须回落追问/handoff（否则兜底只兜文案）。"""
    _seed_one(db_session_factory)

    def gen_not_found(hits, question):
        return f"抱歉，{NOT_FOUND_MARK}，建议咨询教务处。"

    graph = build_knowledge_graph(
        db_session_factory,
        decider=FakeDecider([SimpleNamespace(action="handoff", reply=None, questions=[])]),
        checkpointer=InMemorySaver(),
        generate_fn=gen_not_found,
    )
    out = _invoke(graph)
    assert out["outcome"] == "handoff"
    assert out["hits"] == [], "生成层判未找到 ⇒ 不得再挂来源 chip"
    assert out["reply"] != f"抱歉，{NOT_FOUND_MARK}，建议咨询教务处。"


def test_empty_generation_falls_back_to_template(db_session_factory):
    """LLM 返回空 content ⇒ 回退模板拼装，不产出空气泡。"""
    _seed_one(db_session_factory)
    hits_seen = {}

    def gen_empty(hits, question):
        hits_seen["hits"] = hits
        return "   "  # 空白

    graph = build_knowledge_graph(
        db_session_factory,
        decider=FakeDecider([]),
        checkpointer=InMemorySaver(),
        generate_fn=gen_empty,
    )
    out = _invoke(graph)
    assert out["outcome"] == "answer"
    assert out["reply"] == assemble_answer(hits_seen["hits"])
    assert out["reply"].strip(), "不得返回空回复"


def test_not_found_mark_is_single_sourced():
    """prompt 文案与 graph/judge 判定共用同一常量，改一处不会静默失效。"""
    assert NOT_FOUND_MARK in generator._GENERATE_PROMPT
    with open("src/campus_desk/knowledge/graph.py", encoding="utf-8") as f:
        src = f.read()
    assert 'generator.NOT_FOUND_MARK' in src or "NOT_FOUND_MARK" in src
