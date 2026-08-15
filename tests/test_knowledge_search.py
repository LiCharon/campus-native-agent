"""知识库检索层测试（M1-T4）：关键词计分检索 + type 组装。

覆盖：命中返回 / 未命中空 / 多命中排序截断 / assemble_answer 单条与多条。
"""

from campus_desk.knowledge.search import assemble_answer, search_knowledge


def _clear_knowledge(session_factory):
    """测试隔离（T9）：清空全局 36 条种子，保证检索只看到测试自己的条目。"""
    from campus_desk.db.models import KnowledgeEntry

    with session_factory() as s, s.begin():
        s.query(KnowledgeEntry).delete()


def _seed(session_factory):
    from campus_desk.db.models import KnowledgeEntry

    _clear_knowledge(session_factory)
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


def _seed_scores(session_factory) -> int:
    """分数梯度种子（供 test_multi_hit_sorted_and_limited 验证降序 + 截断）。

    搜索文本"成绩查询"时的计分：A=4 分（"成绩"+"查询"双命中），
    B/C/D/E=2 分（单个关键词命中），F=0 分（无关）。
    返回 4 分条目（A）的 id 供降序断言。
    """
    from campus_desk.db.models import KnowledgeEntry

    _clear_knowledge(session_factory)
    rows = [
        ("教务", "成绩,查询", "成绩怎么查？", "index", "请登录教务系统查看。"),
        ("教务", "成绩", "成绩什么时候出？", "info", "考后两周。"),
        ("教务", "成绩", "成绩复查流程？", "process", "向教务处申请。"),
        ("教务", "查询", "课表在哪查？", "info", "教务系统→我的课表。"),
        ("教务", "成绩查询", "成绩查询入口？", "index", "jw 门户。"),
        ("后勤", "报修", "水管坏了？", "process", "报修平台提交。"),
    ]
    objs = []
    with session_factory() as s, s.begin():
        for d, kw, q, t, a in rows:
            objs.append(KnowledgeEntry(domain=d, keywords=kw, question=q, type=t, answer=a))
        s.add_all(objs)
        s.flush()  # 分配自增 id
        return objs[0].id


def test_hit_returns_entry(db_session_factory):
    _seed(db_session_factory)
    hits = search_knowledge(db_session_factory, "一卡通丢了怎么补办")
    assert hits and hits[0]["type"] == "process"
    assert "服务大厅" in hits[0]["answer"]


def test_miss_returns_empty(db_session_factory):
    _seed(db_session_factory)
    assert search_knowledge(db_session_factory, "量子力学怎么学") == []


def test_multi_hit_sorted_and_limited(db_session_factory):
    top_id = _seed_scores(db_session_factory)
    hits = search_knowledge(db_session_factory, "成绩查询")
    assert len(hits) == 3  # _MAX_RESULTS 截断：5 条命中只返回 3 条
    assert hits[0]["id"] == top_id  # 4 分条目（成绩+查询双命中）降序居首
    assert "报修平台提交。" not in [h["answer"] for h in hits]  # 0 分条目不进结果


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
