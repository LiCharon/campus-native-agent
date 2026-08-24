"""M8 业务指标聚合测试：/api/admin/stats 的 business 字段组。

口径见 docs/plans/M8_PLAN.md §3——分母统一为总会话数，除零保护返回 0.0。
构造方式：db_session_factory 直接插 conversations/messages/bad_cases
（沿用 test_admin_m4 的 api_client + _login 模式），断言各指标。

关键事实：bad_cases 双通道（转人工自动沉淀 reply="" / 手动"没解决" reply 非空），
negative_feedback_rate 只计手动通道（reply != ''）且按 thread_id 去重，与 transfer_rate 零重叠。
"""

import json
from datetime import UTC, datetime, timedelta

from campus_desk.db.models import BadCase, Conversation, Message


def _login(client, username="admin-001", password="123456"):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, f"login {username}: {r.status_code} {r.text}"
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _conv(factory, cid: str, thread_id: str, handoff: str = "none", user_id: str = "student-001"):
    with factory() as s, s.begin():
        s.add(Conversation(id=cid, user_id=user_id, thread_id=thread_id, handoff=handoff))


def _msg(
    factory,
    cid: str,
    role: str,
    content: str,
    outcome=None,
    pending_question=None,
    sources=None,
    seq: int = 0,
):
    """插入一条消息；sources 为 list[dict]（SourceItem 形态）。seq 控制 created_at 顺序。"""
    with factory() as s, s.begin():
        s.add(
            Message(
                conversation_id=cid,
                role=role,
                content=content,
                outcome=outcome,
                pending_question=pending_question,
                sources=json.dumps(sources or [], ensure_ascii=False),
                created_at=datetime.now(UTC) + timedelta(seconds=seq),
            )
        )


def _bad_case(factory, thread_id: str, reply: str = ""):
    with factory() as s, s.begin():
        s.add(
            BadCase(
                user_id="student-001",
                thread_id=thread_id,
                question="测试问题",
                reply=reply,
                status="PENDING",
            )
        )


def _business(client):
    admin = _login(client)
    r = client.get("/api/admin/stats", headers=admin)
    assert r.status_code == 200
    return r.json()["business"]


# ---------- 用例 ----------


def test_empty_db_all_rates_zero(api_client, db_session_factory):
    """空库：0 会话 → 各率 0.0，不除零报错；domain_dist 为空 dict。"""
    b = _business(api_client)
    assert b["conversation_count"] == 0
    assert b["transfer_rate"] == 0.0
    assert b["first_turn_answer_rate"] == 0.0
    assert b["completion_rate"] == 0.0
    assert b["negative_feedback_rate"] == 0.0
    assert b["avg_turns"] == 0.0
    assert b["domain_dist"] == {}


def test_first_turn_answer(api_client, db_session_factory):
    """单会话首轮即答：answer 无追问 → first_turn=1.0 / completion=1.0 / transfer=0.0。"""
    _conv(db_session_factory, "c1", "t-1")
    _msg(db_session_factory, "c1", "user", "图书馆几点开？", seq=0)
    _msg(
        db_session_factory,
        "c1",
        "assistant",
        "8:00 开门",
        outcome="answer",
        sources=[{"type": "kb", "label": "知识库", "detail": "info型 · 图书馆"}],
        seq=1,
    )
    b = _business(api_client)
    assert b["conversation_count"] == 1
    assert b["first_turn_answer_rate"] == 1.0
    assert b["completion_rate"] == 1.0
    assert b["transfer_rate"] == 0.0
    assert b["avg_turns"] == 1.0
    assert b["domain_dist"] == {"图书馆": 1}


def test_transfer_rate(api_client, db_session_factory):
    """2 会话 1 个转人工 → transfer_rate=0.5；转人工会话的负反馈不重复计（自动沉淀 reply 空）。"""
    _conv(db_session_factory, "c1", "t-1")
    _msg(db_session_factory, "c1", "user", "研究生导师怎么选？", seq=0)
    _msg(db_session_factory, "c1", "assistant", "转人工", outcome="handoff", seq=1)
    _bad_case(db_session_factory, "t-1", reply="")  # 转人工自动沉淀（不计入负反馈率）

    _conv(db_session_factory, "c2", "t-2")
    _msg(db_session_factory, "c2", "user", "寒假什么时候？", seq=0)
    _msg(db_session_factory, "c2", "assistant", "1 月中旬", outcome="answer", seq=1)

    _conv(db_session_factory, "c3", "t-3", handoff="human")
    _msg(db_session_factory, "c3", "user", "帮我校医院挂号", seq=0)
    _msg(db_session_factory, "c3", "assistant", "转人工", outcome="handoff", seq=1)

    b = _business(api_client)
    assert b["conversation_count"] == 3
    assert b["transfer_rate"] == 1 / 3
    assert b["negative_feedback_rate"] == 0.0  # 自动沉淀不计入


def test_multi_turn_completion(api_client, db_session_factory):
    """追问两轮后答上：ask → answer → first_turn=0.0 / completion=1.0 / avg_turns=2.0。"""
    _conv(db_session_factory, "c1", "t-1")
    _msg(db_session_factory, "c1", "user", "有空教室吗？", seq=0)
    _msg(db_session_factory, "c1", "assistant", "哪个楼栋？", outcome="ask", pending_question="哪个楼栋？", seq=1)
    _msg(db_session_factory, "c1", "user", "3号楼", seq=2)
    _msg(
        db_session_factory,
        "c1",
        "assistant",
        "3号楼下午有空教室",
        outcome="answer",
        sources=[{"type": "tool", "label": "工具查询", "detail": "query_empty_rooms"}],
        seq=3,
    )
    b = _business(api_client)
    assert b["first_turn_answer_rate"] == 0.0
    assert b["completion_rate"] == 1.0
    assert b["avg_turns"] == 2.0
    assert b["domain_dist"] == {"工具查询": 1}


def test_negative_feedback_rate_manual_only(api_client, db_session_factory):
    """手动"没解决"计入、按 thread_id 去重；同会话多次反馈只计 1；与转人工零重叠。"""
    _conv(db_session_factory, "c1", "t-1")
    _msg(db_session_factory, "c1", "user", "校医院在哪？", seq=0)
    _msg(db_session_factory, "c1", "assistant", "朝晖校区", outcome="answer", seq=1)
    # 同会话手动反馈两次（去重 → 1）；回复内容非空 = 手动通道
    _bad_case(db_session_factory, "t-1", reply="没解决，地址不对")
    _bad_case(db_session_factory, "t-1", reply="还是没解决")

    _conv(db_session_factory, "c2", "t-2")
    _msg(db_session_factory, "c2", "user", "寒假什么时候？", seq=0)
    _msg(db_session_factory, "c2", "assistant", "1 月中旬", outcome="answer", seq=1)

    b = _business(api_client)
    assert b["conversation_count"] == 2
    assert b["negative_feedback_rate"] == 0.5
    assert b["transfer_rate"] == 0.0  # 手动反馈 ≠ 转人工，零重叠


def test_domain_dist_mixed(api_client, db_session_factory):
    """domain 分布：kb 多域 + 工具查询混排，脏 sources 跳过。"""
    _conv(db_session_factory, "c1", "t-1")
    _msg(db_session_factory, "c1", "user", "成绩怎么查？顺便问校历", seq=0)
    _msg(
        db_session_factory,
        "c1",
        "assistant",
        "教务系统可查",
        outcome="answer",
        sources=[
            {"type": "kb", "label": "知识库", "detail": "process型 · 教务"},
            {"type": "kb", "label": "知识库", "detail": "info型 · 住宿后勤"},
        ],
        seq=1,
    )
    _msg(
        db_session_factory,
        "c1",
        "assistant",
        "校历工具可查",
        outcome="answer",
        sources=[{"type": "tool", "label": "工具查询", "detail": "query_calendar"}],
        seq=2,
    )
    # 脏 sources（坏 JSON 不落库；构造格式不合法但可解析的 dict）→ 解析层跳过
    _msg(db_session_factory, "c1", "assistant", "无来源", outcome="answer", sources=[{"type": "kb"}], seq=3)

    b = _business(api_client)
    assert b["domain_dist"] == {"教务": 1, "住宿后勤": 1, "工具查询": 1}


# ---------- 边界补充（2026-08-24 收尾） ----------


def test_transferring_not_counted(api_client, db_session_factory):
    """handoff=transferring 是中间态，不算转人工；仅 human 计。"""
    _conv(db_session_factory, "c1", "t-1", handoff="transferring")
    _msg(db_session_factory, "c1", "user", "问题A", seq=0)
    _msg(db_session_factory, "c1", "assistant", "转接中", outcome="ask", seq=1)

    _conv(db_session_factory, "c2", "t-2", handoff="human")
    _msg(db_session_factory, "c2", "user", "问题B", seq=0)
    _msg(db_session_factory, "c2", "assistant", "转人工", outcome="handoff", seq=1)

    b = _business(api_client)
    assert b["transfer_rate"] == 0.5  # 仅 c2 计入
    assert b["conversation_count"] == 2


def test_conversation_without_assistant(api_client, db_session_factory):
    """只有 user 消息的会话：不贡献首轮即答/完成，但分母（总会话数）含它。"""
    _conv(db_session_factory, "c1", "t-1")
    _msg(db_session_factory, "c1", "user", "只发了消息没收到回复", seq=0)

    _conv(db_session_factory, "c2", "t-2")
    _msg(db_session_factory, "c2", "user", "寒假什么时候？", seq=0)
    _msg(db_session_factory, "c2", "assistant", "1 月中旬", outcome="answer", seq=1)

    b = _business(api_client)
    assert b["conversation_count"] == 2
    assert b["first_turn_answer_rate"] == 0.5  # 1/2：c1 无 assistant 不算
    assert b["completion_rate"] == 0.5
    assert b["avg_turns"] == 1.0  # 2 条 user / 2 会话


def test_bad_sources_json_skipped(api_client, db_session_factory):
    """sources 存了非法 JSON（脏数据）不阻断看板，domain 分布只计合法条目。"""
    _conv(db_session_factory, "c1", "t-1")
    _msg(db_session_factory, "c1", "user", "问题", seq=0)
    with db_session_factory() as s, s.begin():
        s.add(
            Message(
                conversation_id="c1",
                role="assistant",
                content="坏来源",
                outcome="answer",
                sources="not-json{{{",
                created_at=datetime.now(UTC) + timedelta(seconds=1),
            )
        )
    _msg(
        db_session_factory,
        "c1",
        "assistant",
        "好来源",
        outcome="answer",
        sources=[{"type": "kb", "label": "知识库", "detail": "info型 · 教务"}],
        seq=2,
    )
    b = _business(api_client)
    assert b["domain_dist"] == {"教务": 1}
    assert b["first_turn_answer_rate"] == 1.0  # 非法 JSON 不影响 outcome 统计


def test_stats_old_fields_preserved(api_client):
    """旧字段不被 M8 扩展破坏：种子库计数 + 14 天补零 + type 分布，business 空库为 0。"""
    admin = _login(api_client)
    r = api_client.get("/api/admin/stats", headers=admin)
    assert r.status_code == 200
    data = r.json()
    assert data["user_count"] == 5  # 种子 5 账号
    assert data["knowledge_count"] == 36  # 种子 36 条
    assert sum(data["type_dist"].values()) == 36
    assert len(data["feedback_by_day"]) == 14  # 近 14 天补零
    assert data["business"]["conversation_count"] == 0
    assert data["business"]["domain_dist"] == {}


def test_mixed_scenario(api_client, db_session_factory):
    """综合场景精确断言：首轮即答 / 追问完成 / 转人工 / 无 assistant 四类混排。"""
    # c1: 首轮即答
    _conv(db_session_factory, "c1", "t-1")
    _msg(db_session_factory, "c1", "user", "寒假什么时候？", seq=0)
    _msg(db_session_factory, "c1", "assistant", "1 月中旬", outcome="answer", seq=1)
    # c2: 追问完成（2 轮）
    _conv(db_session_factory, "c2", "t-2")
    _msg(db_session_factory, "c2", "user", "有空教室吗？", seq=0)
    _msg(db_session_factory, "c2", "assistant", "哪个楼栋？", outcome="ask", pending_question="哪个楼栋？", seq=1)
    _msg(db_session_factory, "c2", "user", "3号楼", seq=2)
    _msg(
        db_session_factory,
        "c2",
        "assistant",
        "3号楼下午有空教室",
        outcome="answer",
        sources=[{"type": "tool", "label": "工具查询", "detail": "query_empty_rooms"}],
        seq=3,
    )
    # c3: 转人工
    _conv(db_session_factory, "c3", "t-3", handoff="human")
    _msg(db_session_factory, "c3", "user", "帮我校医院挂号", seq=0)
    _msg(db_session_factory, "c3", "assistant", "转人工", outcome="handoff", seq=1)
    # c4: 只有 user 消息
    _conv(db_session_factory, "c4", "t-4")
    _msg(db_session_factory, "c4", "user", "只发没回", seq=0)

    b = _business(api_client)
    assert b["conversation_count"] == 4
    assert b["transfer_rate"] == 0.25  # 1/4
    assert b["first_turn_answer_rate"] == 0.25  # c1
    assert b["completion_rate"] == 0.5  # c1 + c2
    assert b["negative_feedback_rate"] == 0.0
    assert b["avg_turns"] == 1.25  # (1+2+1+1) / 4
    assert b["domain_dist"] == {"工具查询": 1}
