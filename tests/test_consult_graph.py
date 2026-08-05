"""ConsultGraph 测试（M4，需求 §6）：三态分支 / 追问硬约束 / 工具排查 / 转人工打包。

注入：FakeConsultDecider（conftest，序列消费决策，calls 记录输入断言工具结果回填）
+ InMemorySaver（interrupt 持久化）。
"""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from campus_desk.consult.decide import MAX_ASK_ROUNDS, ConsultDecision
from campus_desk.consult.graph import build_consult_graph
from tests.conftest import FakeConsultDecider

CFG = {"configurable": {"thread_id": "consult-t1"}}


def _build(db_session_factory, decider=None):
    return build_consult_graph(
        db_session_factory,
        decider=decider
        or FakeConsultDecider(default=ConsultDecision(action="answer", reply="已为您解答。")),
        checkpointer=InMemorySaver(),
        student_no="2024001",
    )


class TestAnswerBranch:
    def test_direct_answer_ends(self, db_session_factory):
        """能直接回答 → 一轮结束（自助解决）。"""
        graph = _build(
            db_session_factory,
            FakeConsultDecider(
                default=ConsultDecision(
                    action="answer",
                    reply="密码可在教务系统点忘记密码重置。",
                    summary="解答密码问题",
                )
            ),
        )
        out = graph.invoke({"user_input": "密码忘了怎么办"}, CFG)
        assert out["finished"] is True
        assert out["outcome"] == "answer"
        assert "密码" in out["reply"]
        assert graph.get_state(CFG).next == ()  # 终态

    def test_answer_branch_keeps_history(self, db_session_factory):
        """decide.summary 进 history（后续决策上下文）。"""
        decider = FakeConsultDecider(
            default=ConsultDecision(action="answer", reply="已解答", summary="解答邮箱问题")
        )
        graph = _build(db_session_factory, decider)
        graph.invoke({"user_input": "邮箱怎么登录"}, CFG)
        _, _, _ = decider.calls[0]
        assert decider.calls[0][0] == []  # 首轮无历史


class TestAskBranch:
    def test_ask_then_resume(self, db_session_factory):
        """信息不足 → 追问挂起 → resume 后继续决策。"""
        decider = FakeConsultDecider(
            sequence=[
                ConsultDecision(
                    action="ask",
                    questions=["您的学号是多少？"],
                    reply="请补充学号",
                    summary="询问学号",
                ),
                ConsultDecision(action="answer", reply="账号状态正常。"),
            ]
        )
        graph = _build(db_session_factory, decider)
        out1 = graph.invoke({"user_input": "帮我查下账号"}, CFG)
        assert out1["outcome"] == "ask"
        assert out1["pending_question"] and "学号" in out1["pending_question"]
        assert graph.get_state(CFG).next == ("wait",)  # 暂停在 wait

        out2 = graph.invoke(Command(resume="2024001"), CFG)
        assert out2["outcome"] == "answer"
        assert out2["finished"] is True
        # 第二轮 decide 收到学生回答 + 首轮摘要历史
        assert decider.calls[1][1] == "2024001"
        assert decider.calls[1][0][0] == "询问学号"  # ask 轮的 summary 进 history

    def test_questions_truncated_to_two(self, db_session_factory):
        """LLM 给 3 个问题 → 硬截断为 2（需求 §6 每轮 ≤2 问）。"""
        decider = FakeConsultDecider(
            default=ConsultDecision(
                action="ask",
                questions=["问题1", "问题2", "问题3"],
                reply="请回答",
            )
        )
        graph = _build(db_session_factory, decider)
        out = graph.invoke({"user_input": "连不上网"}, CFG)
        assert out["pending_question"] == "问题1、问题2"
        assert "问题3" not in out["pending_question"]

    def test_rounds_capped_force_handoff(self, db_session_factory):
        """总追问超限（rounds ≥ MAX_ASK_ROUNDS）→ 强制转人工（需求 §6 触发条件）。"""
        ask = ConsultDecision(action="ask", questions=["再问一个"], reply="追问")
        decider = FakeConsultDecider(default=ask)
        graph = _build(db_session_factory, decider)
        out = None
        for _ in range(MAX_ASK_ROUNDS * 2):  # 上限防御；第 8 次 ask 时强制 handoff
            out = graph.invoke({"user_input": "a"}, CFG)
            if out["outcome"] == "handoff":
                break
            assert out["outcome"] == "ask"
            out = graph.invoke(Command(resume="b"), CFG)
        assert out["outcome"] == "handoff"
        assert out["finished"] is True
        assert "人工" in out["reply"]
        assert "对话摘要" in out["handoff_package"]


class TestToolBranch:
    def test_tool_call_fills_next_decision(self, db_session_factory):
        """工具排查：调 query_account_status → 结果回填下一轮决策（不回给学生）。

        act→act 自环：工具轮不暂停，同一 invoke 内连续决策到 ask/终态——
        outcome 以最终行为为准（answer），tool_calls 记录工具调用。
        """
        decider = FakeConsultDecider(
            sequence=[
                ConsultDecision(
                    action="tool",
                    tool="query_account_status",
                    tool_args={"student_no": "2024001"},
                    reply="",
                    summary="查询账号状态",
                ),
                ConsultDecision(action="answer", reply="您的账号状态正常。", summary="告知结果"),
            ]
        )
        graph = _build(db_session_factory, decider)
        out = graph.invoke({"user_input": "查下账号"}, CFG)
        assert out["outcome"] == "answer"
        assert out["finished"] is True
        assert "query_account_status" in out["tool_calls"]
        assert out.get("pending_question") is None  # 工具轮不暂停
        # 第二轮 decide 收到工具结果（mock 账号 2024001 normal）
        tool_results = decider.calls[1][2]
        assert tool_results and "正常" in tool_results[0]

    def test_tool_chain_capped_force_handoff(self, db_session_factory):
        """连续工具轮超限（≥3）→ 强制转人工（防死循环）。"""
        tool = ConsultDecision(
            action="tool",
            tool="search_faq",
            tool_args={"keyword": "密码"},
            reply="",
            summary="查 FAQ",
        )
        graph = _build(db_session_factory, FakeConsultDecider(default=tool))
        out = graph.invoke({"user_input": "a"}, CFG)  # 一次 invoke 内 3 次 tool → handoff
        assert out["outcome"] == "handoff"
        assert out["finished"] is True

    def test_unknown_tool_returns_error_string(self, db_session_factory):
        """LLM 幻觉工具名 → 错误串回填（不抛异常）。"""
        decider = FakeConsultDecider(
            sequence=[
                ConsultDecision(
                    action="tool", tool="not_exist_tool", tool_args={}, reply="", summary="x"
                ),
                ConsultDecision(action="answer", reply="已解答"),
            ]
        )
        graph = _build(db_session_factory, decider)
        out = graph.invoke({"user_input": "帮我查"}, CFG)
        assert out["outcome"] == "answer"
        assert "未知工具" in decider.calls[1][2][0]


class TestHandoffBranch:
    def test_handoff_package_built(self, db_session_factory):
        """转人工打包：对话摘要 + 已排查步骤 + 初步判断（需求 §6 人机协同）。"""
        decider = FakeConsultDecider(
            sequence=[
                ConsultDecision(
                    action="tool",
                    tool="search_faq",
                    tool_args={"keyword": "网速"},
                    reply="",
                    summary="查 FAQ",
                ),
                ConsultDecision(
                    action="handoff",
                    reply="这个情况需要信息中心进一步排查。",
                    summary="无法解决转人工",
                ),
            ]
        )
        graph = _build(db_session_factory, decider)
        out = graph.invoke({"user_input": "网速很慢"}, CFG)  # 一次 invoke：tool → handoff
        assert out["outcome"] == "handoff"
        assert out["finished"] is True
        pkg = out["handoff_package"]
        assert "对话摘要" in pkg
        assert "search_faq" in pkg  # 已排查步骤（工具结果）
        assert "初步判断" in pkg and "信息中心" in pkg

    def test_terminal_thread_not_resumed(self, db_session_factory):
        """终态 thread 再 invoke = 新会话语义已实测混乱（M3 坑）→ 评测必须新 thread；
        此处验证 answer 终态后 get_state().next 为空。"""
        graph = _build(db_session_factory)
        graph.invoke({"user_input": "密码"}, CFG)
        assert graph.get_state(CFG).next == ()


class TestStudentNoInjection:
    def test_student_no_supports_tool_call(self, db_session_factory):
        """构建层注入 student_no 后，工具轮能成功查询（2024001 mock 状态 normal）。"""
        tool = ConsultDecision(
            action="tool",
            tool="query_account_status",
            tool_args={"student_no": "2024001"},
            reply="",
            summary="x",
        )
        decider = FakeConsultDecider(sequence=[tool, ConsultDecision(action="answer", reply="ok")])
        g2 = _build(db_session_factory, decider)
        g2.invoke({"user_input": "查账号"}, CFG)
        assert "正常" in decider.calls[1][2][0]  # 2024001 的 mock 状态是 normal
