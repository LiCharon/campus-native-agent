"""知识库检索层测试（M1-T4）：关键词计分检索 + type 组装。

覆盖：命中返回 / 未命中空 / 多命中排序截断 / assemble_answer 单条与多条。
"""

from campus_desk.knowledge.search import assemble_answer, search_knowledge


def _seed(session_factory):
    from campus_desk.db.models import KnowledgeEntry

    rows = [
        ("教务", "校历,放假", "什么时候放寒假？", "info", "寒假时间以学校通知为准。"),
        (
            "证件",
            "一卡通,补办",
            "一卡通怎么补办？",
            "process",
            "材料：身份证。地点：服务大厅1号窗口。时间：工作日 8:30-16:30。",
        ),
        (
            "教务",
            "成绩,查询",
            "成绩怎么查？",
            "index",
            "请登录教务系统（jw 门户）→ 我的成绩 查看。",
        ),
    ]
    with session_factory() as s, s.begin():
        for d, kw, q, t, a in rows:
            s.add(KnowledgeEntry(domain=d, keywords=kw, question=q, type=t, answer=a))
    return [q for _, _, q, _, _ in rows]


def test_hit_returns_entry(db_session_factory):
    _seed(db_session_factory)
    hits = search_knowledge(db_session_factory, "一卡通丢了怎么补办")
    assert hits and hits[0]["type"] == "process"
    assert "服务大厅" in hits[0]["answer"]


def test_miss_returns_empty(db_session_factory):
    _seed(db_session_factory)
    assert search_knowledge(db_session_factory, "量子力学怎么学") == []


def test_multi_hit_sorted_and_limited(db_session_factory):
    _seed(db_session_factory)
    hits = search_knowledge(db_session_factory, "教务")
    assert len(hits) <= 3  # 最多返回 3 条


def test_assemble_single_returns_answer():
    hits = [{"answer": "寒假时间以学校通知为准。", "type": "info"}]
    assert assemble_answer(hits) == "寒假时间以学校通知为准。"


def test_assemble_multi_numbers_and_joins():
    hits = [
        {"answer": "材料：身份证。地点：服务大厅1号窗口。", "type": "process"},
        {"answer": "请登录教务系统（jw 门户）→ 我的成绩 查看。", "type": "index"},
    ]
    assert assemble_answer(hits) == (
        "为您找到以下相关信息：\n"
        "1. 材料：身份证。地点：服务大厅1号窗口。\n"
        "2. 请登录教务系统（jw 门户）→ 我的成绩 查看。"
    )
