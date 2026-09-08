"""KnowledgeGraph（M1-ZJUT）：知识问答图，collect→wait 双节点 ping-pong。

节点链：collect（检索/组装/追问计数，纯逻辑）→ wait（唯一 interrupt）。
铁律（CampusDesk 教训）：interrupt 收敛唯一 wait 节点；问句/计数由 return 持久化。
追问上限 MAX_CLARIFY_ROUNDS=3；超限或 decider 判 handoff → 转人工（存 bad_cases）。

合并检索语义（M1-T6 修正）：history 存"学生每轮原话"而非 decider 的 summary
（summary 会丢检索词），追问轮以"上一轮原问题 + 本轮补充"合并后重检索。
"""

from collections.abc import Callable
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from campus_desk.knowledge.decide import ClarifyDecider
from campus_desk.knowledge.generator import NOT_FOUND_MARK, generate_answer
from campus_desk.knowledge.search import assemble_answer, search_knowledge

MAX_CLARIFY_ROUNDS = 3

_HANDOFF_REPLY = "已记录您的问题，工作人员会尽快回复（当前为演示环境）。"


class KnowledgeState(TypedDict):
    user_input: str
    student_answer: str | None
    history: list[str]
    rounds: int
    pending_question: str | None
    reply: str
    outcome: str | None  # answer/ask/handoff
    hits: list[int]
    finished: bool
    _consumed: bool
    recent: list[str] | None  # M12-ZJUT：近期对话 user 文本，注入 decide 理解指代


class _Deps:
    def __init__(
        self,
        session_factory,
        decider: ClarifyDecider,
        user_id: str = "student-001",
        generator: Callable | None = None,
    ):
        self.session_factory = session_factory
        self.decider = decider
        self.user_id = user_id
        # M17A-T6：默认 LLM 生成节点（惰性构造），测试可注入假实现
        self.generator = generator or generate_answer


def _generate_reply(deps: _Deps, hits: list[dict], question: str) -> str:
    """M17A-T6：LLM 整合生成答案；任何异常/空输出回退模板拼装（可用性不退化）。

    空输出兜底：LLM 返回空 content 时 .strip() 得 ""，原样透传会产出空气泡
    （2026-09-08 code review 发现）。此处一并兜住。
    """
    try:
        return (deps.generator(hits, question) or "").strip() or assemble_answer(hits)
    except Exception:  # noqa: BLE001 — LLM 超时/解析错/不可用一律回退模板
        return assemble_answer(hits)


def _save_bad_case(deps: _Deps, question: str) -> None:
    from campus_desk.db.models import BadCase

    with deps.session_factory() as s, s.begin():
        s.add(BadCase(user_id=deps.user_id, question=question[:500], reply="", status="PENDING"))


def _make_collect(deps: _Deps):
    def collect(state: KnowledgeState) -> dict:
        history = list(state.get("history", []))
        if not state.get("_consumed"):
            raw = state.get("user_input", "")
            consumed = True
        else:
            raw = state.get("student_answer") or ""
            consumed = True
        # M12 防御兜底：异常残留 _consumed 且无 student_answer 时取当前输入，避免吞消息
        if state.get("_consumed") and not state.get("student_answer"):
            raw = state.get("user_input", "")

        # 追问轮：全部历史（学生每轮原话）+ 本轮补充合并检索。
        # join 全 history 而非只取 history[-1]：多轮追问时早轮关键词不丢
        # （旧实现 f"{history[-1]} {raw}" 在 3+ 轮时丢前几轮检索词）。
        text = " ".join(history + [raw]) if (consumed and history) else raw

        hits = search_knowledge(deps.session_factory, text)
        # 生成层说"材料不足"时按未命中处理（2026-09-08 code review 修复）：
        # 检索阈值杀不掉的硬假阳性（如"教室借用"sim 0.64）设计上交给生成层兜底，
        # 但兜底只改文案、仍记 outcome=answer ⇒ 不追问、不 handoff、不落 bad_case、
        # 来源 chip 照挂，等于兜底没落地。故在此回落未命中分支。
        missed = not hits
        if hits:
            answer = _generate_reply(deps, hits, text)
            missed = NOT_FOUND_MARK in answer
            if not missed:
                return {
                    "reply": answer,
                    "outcome": "answer",
                    "finished": True,
                    "pending_question": None,
                    "history": history,
                    "_consumed": consumed,
                    "hits": [h["id"] for h in hits],
                }

        rounds = state.get("rounds", 0) + 1
        decision = deps.decider.decide(history, text, missed=True, recent=state.get("recent"))
        if decision.action == "ask" and rounds <= MAX_CLARIFY_ROUNDS:
            questions = [q for q in decision.questions if q][:2]
            question_text = (
                "、".join(questions) if questions else (decision.reply or "请补充更多信息。")
            )
            history.append(raw)  # 学生原话进历史（不存 summary，防丢检索词）
            return {
                "rounds": rounds,
                "pending_question": question_text,
                "reply": question_text,
                "outcome": "ask",
                "finished": False,
                "student_answer": None,
                "history": history,
                "_consumed": consumed,
                "hits": [],
            }
        # handoff 或超限
        _save_bad_case(deps, text)
        return {
            "rounds": rounds,
            "reply": _HANDOFF_REPLY,
            "outcome": "handoff",
            "finished": True,
            "pending_question": None,
            "history": history,
            "_consumed": consumed,
            "hits": [],
        }

    return collect


def _make_wait():
    def wait(state: KnowledgeState) -> dict:
        answer = interrupt(state["pending_question"])
        return {"student_answer": str(answer)}

    return wait


def _collect_after(state: KnowledgeState) -> Literal["wait", "end"]:
    return "end" if state.get("finished") else "wait"


def build_knowledge_graph(
    session_factory,
    *,
    decider: ClarifyDecider | None = None,
    checkpointer=None,
    user_id: str = "student-001",
    profile: str = "",
    generate_fn: Callable | None = None,
):
    """构建知识问答图。checkpointer 必传（interrupt 需持久化；测试传 InMemorySaver）。

    profile（M7-ZJUT）：可选画像文本段，仅对内部默认构造的 ClarifyDecider 生效
    （调用方显式传 decider 时由调用方决定是否带画像）。
    generate_fn（M17A-T6）：答案生成器 (hits, question) -> str；默认 LLM 生成节点
    （knowledge/generator.py，异常自动回退模板拼装），测试可注入假实现。
    """
    if decider is None:
        decider = ClarifyDecider(profile=profile)
    deps = _Deps(session_factory, decider, user_id=user_id, generator=generate_fn)
    graph = (
        StateGraph(KnowledgeState)
        .add_node("collect", _make_collect(deps))
        .add_node("wait", _make_wait())
        .add_edge(START, "collect")
        .add_conditional_edges("collect", _collect_after, {"wait": "wait", "end": END})
        .add_edge("wait", "collect")
    )
    return graph.compile(checkpointer=checkpointer)
