"""QueryGraph（M2）：工具查询图，collect→wait 双节点 ping-pong（复用 knowledge 铁律）。

collect 职责（纯逻辑；interrupt 重入不落盘——问句/计数必须由 return 写 state）：
1. 熔断判定：fail_count>=2 → 转人工（bad_cases）；fail_count==1 → 跳过 LLM 直接降级
2. 合并历史文本 → FC（bind_tools）→ tool_calls → 执行 → 模板组装
3. 无 tool_calls → 重试 1 次 → 规则抽取 → 字段齐直接查表 / 图书馆词直接查座位 / 缺字段确定性追问
4. 工具失败 → fail_count+1 → 索引引导降级（②）
追问上限 MAX_CLARIFY_ROUNDS=3；超限 → 转人工。
"""

from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from campus_desk import telemetry
from campus_desk.llm import build_tool_llm
from campus_desk.query.assemble import (
    CIRCUIT_DEGRADED_REPLY,
    DEGRADED_REPLIES,
    HANDOFF_REPLY,
    assemble_rooms,
    assemble_seats,
)
from campus_desk.query.field_extract import extract_fields
from campus_desk.query.tools import TOOL_FUNCS, TOOL_SCHEMAS

MAX_CLARIFY_ROUNDS = 3
CIRCUIT_BREAK_THRESHOLD = 2  # 连续失败次数达到后：下一轮直接转人工

_CLARIFY_BUILDING = "请问您想查询哪栋教学楼？（如 1号楼、2号楼、3号楼）"
_CLARIFY_PERIOD = "请问您想查询哪个时段？（上午、下午或晚上）"

_QUERY_PROMPT = (
    "你是校园服务台的查询助手。学生要查动态数据（空教室/图书馆座位）时，"
    "调用工具查询；能查就调用工具，不要直接回答。"
)


class QueryState(TypedDict):
    user_input: str
    student_answer: str | None
    history: list[str]
    rounds: int
    pending_question: str | None
    reply: str
    outcome: str | None  # answer / ask / handoff / degraded
    tool_calls: list[str]
    fail_count: int
    finished: bool
    _consumed: bool


class _Deps:
    def __init__(self, session_factory, llm, user_id: str = "student-001"):
        self.session_factory = session_factory
        self.llm = llm
        self.user_id = user_id


def _save_bad_case(deps: _Deps, question: str) -> None:
    from campus_desk.db.models import BadCase

    with deps.session_factory() as s, s.begin():
        s.add(BadCase(user_id=deps.user_id, question=question[:500], reply="", status="PENDING"))


def _run_tool(deps: _Deps, name: str, args: dict) -> dict:
    with telemetry.span("agent.tool", metadata={"name": name}):
        return TOOL_FUNCS[name](deps.session_factory, **args)


def _assemble(name: str, result: dict) -> str:
    return assemble_rooms(result) if name == "query_empty_rooms" else assemble_seats(result)


def _call_tools(deps: _Deps, text: str):
    """FC 调用：返回 tool_calls 列表（异常时返回空列表，不抛）。"""
    try:
        llm_tools = deps.llm.bind_tools(TOOL_SCHEMAS)
        reply = llm_tools.invoke([("system", _QUERY_PROMPT), ("human", text)])
        return getattr(reply, "tool_calls", None) or []
    except Exception:  # noqa: BLE001 — 外部调用兜底
        return []


def _finish(reply: str, outcome: str, **extra) -> dict:
    out = {"reply": reply, "outcome": outcome, "finished": True,
           "pending_question": None, "_consumed": True}
    out.update(extra)
    return out


def _make_collect(deps: _Deps):
    def collect(state: QueryState) -> dict:
        history = list(state.get("history", []))
        raw = state.get("user_input", "") if not state.get("_consumed") \
            else (state.get("student_answer") or "")
        # 追问轮合并全部历史原话（拍板 Q11：对话短无上下文过长风险，早轮关键词不丢）
        text = " ".join(history + [raw]) if history else raw
        fail_count = state.get("fail_count", 0)
        rounds = state.get("rounds", 0)

        # ④ 熔断后兜底：连续失败达到阈值 → 转人工 + bad_cases
        if fail_count >= CIRCUIT_BREAK_THRESHOLD:
            _save_bad_case(deps, text)
            return _finish(HANDOFF_REPLY, "handoff", rounds=rounds, history=history,
                           tool_calls=[], fail_count=fail_count)
        # ③ 熔断中：跳过 LLM 直接降级（失败计数继续累计）
        if fail_count >= 1:
            return _finish(CIRCUIT_DEGRADED_REPLY, "degraded", rounds=rounds, history=history,
                           tool_calls=[], fail_count=fail_count + 1)

        # 正常路径：FC 两次尝试（重试 1 次）
        tcs = []
        for _ in range(2):
            tcs = _call_tools(deps, text)
            if tcs:
                break
        if tcs:
            first = tcs[0]
            name = first.get("name", "") if isinstance(first, dict) else getattr(first, "name", "")
            args = first.get("args", {}) if isinstance(first, dict) else (getattr(first, "args", {}) or {})
            if name in TOOL_FUNCS:
                result = _run_tool(deps, name, {k: v for k, v in args.items() if k in ("building", "period")})
                if result.get("ok"):
                    return _finish(_assemble(name, result), "answer", rounds=rounds, history=history,
                                   tool_calls=[name], fail_count=0)
                return _finish(DEGRADED_REPLIES.get(name, CIRCUIT_DEGRADED_REPLY), "degraded",
                               rounds=rounds, history=history, tool_calls=[name], fail_count=fail_count + 1)
            return _finish(CIRCUIT_DEGRADED_REPLY, "degraded", rounds=rounds, history=history,
                           tool_calls=[], fail_count=fail_count + 1)

        # 无 tool_calls → 规则抽取兜底
        fields = extract_fields(text)
        building, period = fields["building"], fields["period"]
        if building and period:
            result = _run_tool(deps, "query_empty_rooms", {"building": building, "period": period})
            if result.get("ok"):
                return _finish(_assemble("query_empty_rooms", result), "answer", rounds=rounds,
                               history=history, tool_calls=["query_empty_rooms"], fail_count=0)
            return _finish(DEGRADED_REPLIES["query_empty_rooms"], "degraded", rounds=rounds,
                           history=history, tool_calls=["query_empty_rooms"], fail_count=fail_count + 1)
        if "图书馆" in text or "座位" in text:
            result = _run_tool(deps, "query_library_seats", {})
            if result.get("ok"):
                return _finish(_assemble("query_library_seats", result), "answer", rounds=rounds,
                               history=history, tool_calls=["query_library_seats"], fail_count=0)
            return _finish(DEGRADED_REPLIES["query_library_seats"], "degraded", rounds=rounds,
                           history=history, tool_calls=["query_library_seats"], fail_count=fail_count + 1)

        # 缺字段 → 确定性追问（拍板 Q4：不调 LLM）
        rounds += 1
        if rounds > MAX_CLARIFY_ROUNDS:
            _save_bad_case(deps, text)
            return _finish(HANDOFF_REPLY, "handoff", rounds=rounds, history=history,
                           tool_calls=[], fail_count=fail_count)
        question = _CLARIFY_BUILDING if not building else _CLARIFY_PERIOD
        history.append(raw)
        return {"rounds": rounds, "pending_question": question, "reply": question,
                "outcome": "ask", "finished": False, "student_answer": None,
                "history": history, "tool_calls": [], "fail_count": fail_count, "_consumed": True}

    return collect


def _make_wait():
    def wait(state: QueryState) -> dict:
        answer = interrupt(state["pending_question"])
        return {"student_answer": str(answer)}

    return wait


def _collect_after(state: QueryState) -> Literal["wait", "end"]:
    return "end" if state.get("finished") else "wait"


def build_query_graph(session_factory, *, llm=None, checkpointer=None, user_id: str = "student-001"):
    """构建工具查询图。llm/checkpointer 可注入（测试用 fake/InMemorySaver），默认真 FC。"""
    deps = _Deps(session_factory, llm if llm is not None else build_tool_llm(), user_id=user_id)
    graph = (
        StateGraph(QueryState)
        .add_node("collect", _make_collect(deps))
        .add_node("wait", _make_wait())
        .add_edge(START, "collect")
        .add_conditional_edges("collect", _collect_after, {"wait": "wait", "end": END})
        .add_edge("wait", "collect")
    )
    return graph.compile(checkpointer=checkpointer)
