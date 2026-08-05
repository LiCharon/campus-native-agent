"""ConsultGraph（M4，需求 §6 ConsultAgent 诊断式）：多轮追问 → 工具排查 → 三态分支。

节点链：act（LLM 决策 + 工具调用，纯逻辑）→ wait（唯一 interrupt）→ act …

结构对齐 RepairGraph 已验证的双节点 ping-pong（interrupt 重入不落盘 → 问句/计数
必须由 return 持久化；wait 是唯一暂停点）。

三态分支（需求 §6）：
- 能解决 → action=answer：给步骤 + 确认记录（outcome=answer，自助解决）
- 不能解决 → action=handoff：打包信息 = 对话摘要 + 已排查步骤 + 初步判断
- 不确定 → action=ask：给 2-3 个最可能原因让学生试（每轮 ≤2 问，总 ≤8 轮）

硬约束（graph 层执行，不依赖 LLM 自觉）：
- rounds ≥ MAX_ASK_ROUNDS（ask 时）+1 后超限 → 强制 handoff
- tool_chain ≥ MAX_TOOL_CHAIN（连续工具轮）→ 强制 handoff（防死循环）

每轮输出契约（评测断言）：reply / outcome / tool_calls / finished。
"""

from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt  # resume 由编排层传 Command

from campus_desk.consult.decide import (
    MAX_ASK_ROUNDS,
    MAX_QUESTIONS_PER_ROUND,
    MAX_TOOL_CHAIN,
    ConsultDecider,
    ConsultDecision,
)
from campus_desk.db.session import SessionFactory
from campus_desk.tools.consult_tools import create_consult_tools


class ConsultState(TypedDict):
    user_input: str
    student_answer: str | None
    history: list[str]  # 每轮摘要（decide.summary），最近 N 条进 prompt
    rounds: int  # 已追问轮数（ask 计数，≤ MAX_ASK_ROUNDS）
    tool_chain: int  # 连续工具轮计数（≤ MAX_TOOL_CHAIN）
    tool_results: list[str]  # 最近工具结果（进下一轮决策，不回给学生）
    pending_question: str | None
    reply: str
    outcome: Literal["ask", "tool", "answer", "handoff"] | None  # 本轮行为（评测断言）
    tool_calls: list[str]
    handoff_package: str | None  # 转人工打包信息（需求 §6 人机协同）
    finished: bool
    _consumed: bool


class _NodeDeps:
    """节点闭包依赖（构造注入，节点签名保持 (state)）。"""

    def __init__(
        self,
        session_factory: SessionFactory,
        decider: ConsultDecider,
        *,
        student_no: str | None = None,
    ):
        self.session_factory = session_factory
        self.decider = decider
        self.student_no = student_no
        self.tools = {t.name: t for t in create_consult_tools(session_factory)}


def _call_tool(deps: _NodeDeps, decision: ConsultDecision) -> str:
    """确定性工具调用：未知工具/参数异常返回错误串（不抛，LLM 可见）。"""
    tool = deps.tools.get(decision.tool or "")
    if tool is None:
        return f"错误: 未知工具 {decision.tool}"
    try:
        return str(tool.func(**decision.tool_args))
    except Exception as exc:  # noqa: BLE001 — 参数 schema 校验失败由 @tool 层抛，兜底转错误串
        return f"错误: 工具调用失败 {exc!r}"


def _handoff_package(history: list[str], tool_results: list[str], judgment: str) -> str:
    """转人工打包信息（需求 §6：人工无需重复询问）。"""
    lines = ["【咨询转人工】", "对话摘要:"]
    lines += [f"- {h}" for h in history[-6:]] or ["- 无"]
    lines.append("已排查步骤:")
    lines += [f"- {r}" for r in tool_results[-3:]] or ["- 无"]
    lines.append(f"初步判断: {judgment}")
    return "\n".join(lines)


def _make_act(deps: _NodeDeps):
    def act(state: ConsultState) -> dict:
        """纯逻辑节点：消费输入 → LLM 决策 → 按动作分支。

        问句/轮次/工具结果全部由 return 持久化（wait 负责暂停）——
        interrupt 重入不落盘，节点内不得依赖"中断前修改"（RepairGraph 同款坑）。
        """
        history = list(state.get("history", []))
        tool_results = list(state.get("tool_results", []))
        tool_calls = list(state.get("tool_calls", []))
        rounds = state.get("rounds", 0)

        if not state.get("_consumed"):
            user_text = state.get("user_input", "")
            consumed = True
        else:
            user_text = state.get("student_answer") or ""
            consumed = state.get("_consumed", False)

        decision = deps.decider.decide(history, user_text, tool_results, deps.student_no)
        if decision.summary:
            history.append(decision.summary)

        if decision.action == "tool":
            tool_chain = state.get("tool_chain", 0) + 1
            if tool_chain >= MAX_TOOL_CHAIN:
                return {
                    "rounds": rounds,
                    "tool_chain": tool_chain,
                    "history": history,
                    "reply": "已为您转人工，人工会结合已排查信息继续处理。",
                    "outcome": "handoff",
                    "handoff_package": _handoff_package(history, tool_results, decision.reply),
                    "finished": True,
                    "_consumed": consumed,
                }
            result = _call_tool(deps, decision)
            tool_results.append(f"{decision.tool}: {result}")
            tool_calls.append(decision.tool or "unknown_tool")
            return {
                "rounds": rounds,
                "tool_chain": tool_chain,
                "history": history,
                "tool_results": tool_results,
                "tool_calls": tool_calls,
                "outcome": "tool",
                "student_answer": None,
                "_consumed": consumed,
            }

        if decision.action == "ask":
            rounds = rounds + 1
            if rounds >= MAX_ASK_ROUNDS:  # 超 8 轮强制转人工（需求 §6 触发条件）
                return {
                    "rounds": rounds,
                    "history": history,
                    "reply": "已为您转人工，多次追问仍未解决，人工会继续帮您排查。",
                    "outcome": "handoff",
                    "handoff_package": _handoff_package(history, tool_results, decision.reply),
                    "finished": True,
                    "_consumed": consumed,
                }
            questions = [q for q in (decision.questions or []) if q][:MAX_QUESTIONS_PER_ROUND]
            question_text = "、".join(questions) if questions else (decision.reply or "")
            tool_calls.append("ask_question")
            return {
                "rounds": rounds,
                "history": history,
                "tool_calls": tool_calls,
                "pending_question": question_text,
                "reply": question_text,
                "outcome": "ask",
                "student_answer": None,
                "_consumed": consumed,
            }

        if decision.action == "handoff":
            return {
                "rounds": rounds,
                "history": history,
                "reply": decision.reply or "已为您转人工，请稍候。",
                "outcome": "handoff",
                "handoff_package": _handoff_package(history, tool_results, decision.reply),
                "finished": True,
                "_consumed": consumed,
            }

        # answer（默认分支）：给出步骤/FAQ 答案 → 确认记录，结束
        tool_calls.append("answer_question")
        return {
            "rounds": rounds,
            "history": history,
            "tool_calls": tool_calls,
            "reply": decision.reply or "已为您解答，如果还有其他问题随时告诉我。",
            "outcome": "answer",
            "finished": True,
            "_consumed": consumed,
        }

    return act


def _make_wait():
    def wait(state: ConsultState) -> dict:
        """唯一 interrupt 节点（RepairGraph 同款：按 value 匹配暂停点）。"""
        answer = interrupt(state["pending_question"])
        return {"student_answer": str(answer)}

    return wait


def _act_after(state: ConsultState) -> Literal["wait", "act", "end"]:
    if state.get("finished"):
        return "end"
    if state.get("pending_question"):
        return "wait"
    return "act"  # tool 轮继续决策


def build_consult_graph(
    session_factory: SessionFactory,
    *,
    decider: ConsultDecider | None = None,
    checkpointer=None,
    student_no: str | None = None,
):
    """构建 ConsultGraph。checkpointer 必传（interrupt 需持久化；测试传 InMemorySaver）。"""
    deps = _NodeDeps(
        session_factory,
        decider if decider is not None else ConsultDecider(),
        student_no=student_no,
    )

    graph = (
        StateGraph(ConsultState)
        .add_node("act", _make_act(deps))
        .add_node("wait", _make_wait())
        .add_edge(START, "act")
        .add_conditional_edges("act", _act_after, {"wait": "wait", "act": "act", "end": END})
        .add_edge("wait", "act")
    )
    return graph.compile(checkpointer=checkpointer)
