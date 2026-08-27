"""QueryGraph（M2）：工具查询图，collect→wait 双节点 ping-pong（复用 knowledge 铁律）。

collect 职责（纯逻辑；interrupt 重入不落盘——问句/计数必须由 return 写 state）：
1. 熔断判定：fail_count>=2 → 转人工（bad_cases）；fail_count==1 → 跳过 LLM 直接降级
2. 合并历史文本 → FC（bind_tools）→ tool_calls → 执行 → 模板组装
3. 无 tool_calls → 重试 1 次 → 规则抽取 → 字段齐直接查表 / 图书馆词直接查座位 / 缺字段确定性追问
4. 工具失败 → fail_count+1 → 索引引导降级（②）
追问上限 MAX_CLARIFY_ROUNDS=3；超限 → 转人工。

M2+ FC 扩展：
- 工具参数白名单从 TOOL_SCHEMAS required 动态派生（不再硬编码 building/period）
- system prompt 注入时间上下文（今天/周次/学期），LLM 据此填 week/term
- _run_tool 统一注入 student_no/user_id（deps 携带，个人数据工具免问学号）
- clarify 追问按领域关键词路由（电量/校车/失物登记问对应的问题，只问不答）
"""

from datetime import UTC, date, datetime, timedelta
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from campus_desk import telemetry
from campus_desk.llm import build_tool_llm
from campus_desk.query.assemble import (
    CIRCUIT_DEGRADED_REPLY,
    DEGRADED_REPLIES,
    HANDOFF_REPLY,
    assemble_announcements,
    assemble_borrow,
    assemble_calendar,
    assemble_card_balance,
    assemble_dorm_power,
    assemble_exams,
    assemble_lost_register,
    assemble_lost_search,
    assemble_rooms,
    assemble_scores,
    assemble_seats,
    assemble_shuttle,
    assemble_timetable,
)
from campus_desk.query.field_extract import extract_fields
from campus_desk.query.tools import TOOL_FUNCS, TOOL_SCHEMAS

MAX_CLARIFY_ROUNDS = 3
CIRCUIT_BREAK_THRESHOLD = 2  # 连续失败次数达到后：下一轮直接转人工

_CLARIFY_BUILDING = "请问您想查询哪栋教学楼？（如 1号楼、2号楼、3号楼）"
_CLARIFY_PERIOD = "请问您想查询哪个时段？（上午、下午或晚上）"

# M2+：缺参追问按领域路由（只问不答，不做规则直查兜底）
_CLARIFY_PROMPTS = {
    "query_empty_rooms": _CLARIFY_BUILDING,
    "query_timetable": "请问您想查哪天的课？可以告诉我星期几（如 周三），周次默认当前教学周。",
    "query_dorm_power": "请问您所在宿舍是哪栋楼几号房间？（如 3号楼 205）",
    "query_shuttle_schedule": "请问您想查询哪条校车线路、什么方向？（如 屏峰-朝晖 去程/返程）",
    "register_lost_item": "请问您是在哪里捡到的？大概什么时间？（如 3号楼201，今天）",
}
_CLARIFY_ROUTES = [
    ("query_timetable", ("课表", "课程", "上课")),
    ("query_dorm_power", ("电量", "电费", "宿舍电", "还有多少电", "还剩多少电")),
    ("query_shuttle_schedule", ("校车", "班车", "通勤车")),
    ("register_lost_item", ("捡到", "拾到", "失物登记")),
]

_WEEKDAY_CN = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "日"}

# M2+：时间上下文默认值（暑假等校历查不到时回退，保证课表/校历查询不空）
_DEFAULT_TERM = "2026-2027-1"
_DEFAULT_WEEK = 1

_QUERY_PROMPT_TEMPLATE = (
    "你是校园服务台的查询助手。学生要查动态数据时，调用工具查询；能查就调用工具，不要直接回答。\n"
    "今天是 {today}（周{weekday_cn}，{term} 第 {week} 周）。时间换算：学生说'这周/今天'即第 {week} 周，"
    "'下周'为第 {week_plus} 周；'这学期/本学期'用 {term}，'上学期'用 {prev_term}；"
    "未指明周次/学期时按当前值填。\n"
    "查询个人数据（课表/成绩/考试/借阅/余额）无需学生提供学号，系统已注入。\n"
    "校车 line 格式为'起点-终点'（如 屏峰-朝晖），direction=去程 表示从起点发车，"
    "direction=返程 表示从终点返回；学生说'屏峰到朝晖'即 line=屏峰-朝晖、direction=去程。\n"
    "学生未提供宿舍楼栋/房间号、校车线路/方向、课表查询的星期几时，不要猜测，先向学生确认。"
)


def _prev_term(term: str) -> str:
    """学期前推：2026-2027-1 → 2025-2026-2；2025-2026-2 → 2025-2026-1。"""
    try:
        start, end = term.split("-")[0:2]
        suffix = term.rsplit("-", 1)[-1]
        if suffix == "1":
            return f"{int(start) - 1}-{int(end) - 1}-2"
        return f"{start}-{end}-1"
    except Exception:  # noqa: BLE001 — 格式异常原样返回，不阻断 prompt 构建
        return term


def _time_context(deps: _Deps) -> dict:
    """按 today 推算学期/周次/上下周；today 不在任何教学周（暑假等）回退默认；异常整体兜底。"""
    today = deps.today or datetime.now(UTC).date()
    ctx = {
        "today": today.isoformat(),
        "weekday_cn": _WEEKDAY_CN.get(today.weekday() + 1, ""),
        "term": _DEFAULT_TERM,
        "week": _DEFAULT_WEEK,
        "week_plus": _DEFAULT_WEEK + 1,
        "prev_term": _prev_term(_DEFAULT_TERM),
    }
    try:
        from campus_desk.db.models import AcademicCalendar

        with deps.session_factory() as session, session.begin():
            candidates = (
                session.query(AcademicCalendar)
                .filter(AcademicCalendar.week_start <= today)
                .all()
            )
        row = next(
            (r for r in candidates if r.week_start + timedelta(days=7) > today), None
        )
        if row is not None:
            ctx["term"] = row.term
            ctx["week"] = row.week
            ctx["week_plus"] = row.week + 1
            ctx["prev_term"] = _prev_term(row.term)
    except Exception:  # noqa: BLE001, S110 — 校历查询失败不阻断（inject_error 场景）
        pass
    return ctx


def _build_query_prompt(deps: _Deps) -> str:
    prompt = _QUERY_PROMPT_TEMPLATE.format(**_time_context(deps))
    if deps.profile_text:
        # M7-ZJUT：画像段追加在模板之后（不用占位符——模板 format 缺键会 KeyError）
        prompt += f"\n{deps.profile_text}"
    return prompt


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
    recent: list[str] | None  # M12-ZJUT：近期对话 user 文本，注入工具选择理解指代


class _Deps:
    def __init__(
        self,
        session_factory,
        llm,
        user_id: str = "student-001",
        student_no: str | None = None,
        today: date | None = None,
        profile_text: str = "",
    ):
        self.session_factory = session_factory
        self.llm = llm
        self.user_id = user_id
        self.student_no = student_no
        self.today = today
        self.profile_text = profile_text  # M7-ZJUT：画像文本段（空串不注入）
        self._prompt: str | None = None

    def query_prompt(self) -> str:
        """时间上下文 prompt（图生命周期内构建一次，避免每轮重复查校历）。"""
        if self._prompt is None:
            self._prompt = _build_query_prompt(self)
        return self._prompt


def _save_bad_case(deps: _Deps, question: str) -> None:
    from campus_desk.db.models import BadCase

    with deps.session_factory() as s, s.begin():
        s.add(BadCase(user_id=deps.user_id, question=question[:500], reply="", status="PENDING"))


def _run_tool(deps: _Deps, name: str, args: dict) -> dict:
    with telemetry.span("agent.tool", metadata={"name": name}):
        return TOOL_FUNCS[name](
            deps.session_factory, **args, student_no=deps.student_no, user_id=deps.user_id
        )


_ASSEMBLERS = {
    "query_empty_rooms": assemble_rooms,
    "query_library_seats": assemble_seats,
    "query_timetable": assemble_timetable,
    "query_exam_scores": assemble_scores,
    "query_exam_schedule": assemble_exams,
    "query_library_borrow": assemble_borrow,
    "query_card_balance": assemble_card_balance,
    "query_dorm_power": assemble_dorm_power,
    "register_lost_item": assemble_lost_register,
    "search_lost_items": assemble_lost_search,
    "query_shuttle_schedule": assemble_shuttle,
    "query_calendar": assemble_calendar,
    "query_announcements": assemble_announcements,
}


def _assemble(name: str, result: dict) -> str:
    assembler = _ASSEMBLERS.get(name)
    return assembler(result) if assembler else CIRCUIT_DEGRADED_REPLY


def _call_tools(deps: _Deps, text: str, recent: list[str] | None = None):
    """FC 调用：返回 tool_calls 列表（异常时返回空列表，不抛）。

    recent（M12-ZJUT）：近期对话 user 文本，拼入 human 消息帮助工具选择理解
    指代（如"那栋楼"）；不进入检索拼接（检索只看当前+图内 ≤3 追问轮）。
    """
    try:
        llm_tools = deps.llm.bind_tools(TOOL_SCHEMAS)
        human = text
        if recent:
            lines = "\n".join(f"- {m}" for m in recent)
            human = (
                f"近期对话（仅参考，用于理解指代如'那栋楼/这个'）:\n{lines}\n\n"
                f"当前问题: {text}"
            )
        reply = llm_tools.invoke([("system", deps.query_prompt()), ("human", human)])
        return getattr(reply, "tool_calls", None) or []
    except Exception:  # noqa: BLE001 — 外部调用兜底
        return []


def _match_clarify_domain(text: str) -> str | None:
    """按关键词匹配追问领域（电量/校车/失物登记）；未命中返回 None 走默认追问。"""
    for name, keywords in _CLARIFY_ROUTES:
        if any(k in text for k in keywords):
            return name
    return None


# M2+：工具参数白名单从 schema required 动态派生（防 FC 幻觉参数，扩容免改）
_REQUIRED_ARGS: dict[str, set[str]] = {
    s["function"]["name"]: set(s["function"]["parameters"].get("required", []))
    for s in TOOL_SCHEMAS
}


def _finish(reply: str, outcome: str, **extra) -> dict:
    out = {
        "reply": reply,
        "outcome": outcome,
        "finished": True,
        "pending_question": None,
        "_consumed": True,
    }
    out.update(extra)
    return out


def _make_collect(deps: _Deps):
    def collect(state: QueryState) -> dict:
        history = list(state.get("history", []))
        raw = (
            state.get("user_input", "")
            if not state.get("_consumed")
            else (state.get("student_answer") or "")
        )
        # M12 防御兜底：异常残留 _consumed 且无 student_answer 时取当前输入，避免吞消息
        if state.get("_consumed") and not state.get("student_answer"):
            raw = state.get("user_input", "")
        # 追问轮合并全部历史原话（拍板 Q11：对话短无上下文过长风险，早轮关键词不丢）
        text = " ".join(history + [raw]) if history else raw
        fail_count = state.get("fail_count", 0)
        rounds = state.get("rounds", 0)

        # ④ 熔断后兜底：连续失败达到阈值 → 转人工 + bad_cases
        if fail_count >= CIRCUIT_BREAK_THRESHOLD:
            _save_bad_case(deps, text)
            return _finish(
                HANDOFF_REPLY,
                "handoff",
                rounds=rounds,
                history=history,
                tool_calls=[],
                fail_count=fail_count,
            )
        # ③ 熔断中：跳过 LLM 直接降级（失败计数继续累计）
        if fail_count >= 1:
            return _finish(
                CIRCUIT_DEGRADED_REPLY,
                "degraded",
                rounds=rounds,
                history=history,
                tool_calls=[],
                fail_count=fail_count + 1,
            )

        # 正常路径：FC 两次尝试（重试 1 次）
        tcs = []
        for _ in range(2):
            tcs = _call_tools(deps, text, recent=state.get("recent"))
            if tcs:
                break
        if tcs:
            first = tcs[0]
            name = first.get("name", "") if isinstance(first, dict) else getattr(first, "name", "")
            args = (
                first.get("args", {})
                if isinstance(first, dict)
                else (getattr(first, "args", {}) or {})
            )
            if name in TOOL_FUNCS:
                filtered = {k: v for k, v in args.items() if k in _REQUIRED_ARGS.get(name, set())}
                result = _run_tool(deps, name, filtered)
                if result.get("ok"):
                    return _finish(
                        _assemble(name, result),
                        "answer",
                        rounds=rounds,
                        history=history,
                        tool_calls=[name],
                        fail_count=0,
                    )
                return _finish(
                    DEGRADED_REPLIES.get(name, CIRCUIT_DEGRADED_REPLY),
                    "degraded",
                    rounds=rounds,
                    history=history,
                    tool_calls=[name],
                    fail_count=fail_count + 1,
                )
            return _finish(
                CIRCUIT_DEGRADED_REPLY,
                "degraded",
                rounds=rounds,
                history=history,
                tool_calls=[],
                fail_count=fail_count + 1,
            )

        # 无 tool_calls → 规则抽取兜底
        fields = extract_fields(text)
        building, period = fields["building"], fields["period"]
        if building and period:
            result = _run_tool(deps, "query_empty_rooms", {"building": building, "period": period})
            if result.get("ok"):
                return _finish(
                    _assemble("query_empty_rooms", result),
                    "answer",
                    rounds=rounds,
                    history=history,
                    tool_calls=["query_empty_rooms"],
                    fail_count=0,
                )
            return _finish(
                DEGRADED_REPLIES["query_empty_rooms"],
                "degraded",
                rounds=rounds,
                history=history,
                tool_calls=["query_empty_rooms"],
                fail_count=fail_count + 1,
            )
        if "图书馆" in text or "座位" in text:
            result = _run_tool(deps, "query_library_seats", {})
            if result.get("ok"):
                return _finish(
                    _assemble("query_library_seats", result),
                    "answer",
                    rounds=rounds,
                    history=history,
                    tool_calls=["query_library_seats"],
                    fail_count=0,
                )
            return _finish(
                DEGRADED_REPLIES["query_library_seats"],
                "degraded",
                rounds=rounds,
                history=history,
                tool_calls=["query_library_seats"],
                fail_count=fail_count + 1,
            )

        # 缺字段 → 确定性追问（拍板 Q4：不调 LLM）
        # M2+：先按领域关键词路由（电量/校车/失物登记），未命中回退空教室两问
        rounds += 1
        if rounds > MAX_CLARIFY_ROUNDS:
            _save_bad_case(deps, text)
            return _finish(
                HANDOFF_REPLY,
                "handoff",
                rounds=rounds,
                history=history,
                tool_calls=[],
                fail_count=fail_count,
            )
        clarify_name = _match_clarify_domain(text)
        if clarify_name:
            question = _CLARIFY_PROMPTS[clarify_name]
        elif not building:
            question = _CLARIFY_PROMPTS["query_empty_rooms"]
        else:
            question = _CLARIFY_PERIOD
        history.append(raw)
        return {
            "rounds": rounds,
            "pending_question": question,
            "reply": question,
            "outcome": "ask",
            "finished": False,
            "student_answer": None,
            "history": history,
            "tool_calls": [],
            "fail_count": fail_count,
            "_consumed": True,
        }

    return collect


def _make_wait():
    def wait(state: QueryState) -> dict:
        answer = interrupt(state["pending_question"])
        return {"student_answer": str(answer)}

    return wait


def _collect_after(state: QueryState) -> Literal["wait", "end"]:
    return "end" if state.get("finished") else "wait"


def lookup_student_no(session_factory, user_id: str) -> str | None:
    """按 user_id 查 users.student_no（图构建期一次查询；失败返回 None 不阻断）。

    chain_runner 复用（inject_error 剧本需预解析学号）；per-user 图缓存下零额外开销。
    """
    try:
        from campus_desk.db.models import User

        with session_factory() as session, session.begin():
            row = session.query(User).filter(User.id == user_id).first()
        return row.student_no if row else None
    except Exception:  # noqa: BLE001
        return None


def build_query_graph(
    session_factory,
    *,
    llm=None,
    checkpointer=None,
    user_id: str = "student-001",
    student_no: str | None = None,
    today: date | None = None,
    profile_text: str = "",
):
    """构建工具查询图。llm/checkpointer 可注入（测试用 fake/InMemorySaver），默认真 FC。

    M2+：student_no 未显式传入时按 user_id 查库一次（个人数据工具免问学号）；
    today 可注入（评测固定日期保证确定性，生产默认今天）。
    M7-ZJUT：profile_text 可选画像段（图构建期查库，注入 _QUERY_PROMPT_TEMPLATE）。
    """
    if student_no is None:
        student_no = lookup_student_no(session_factory, user_id)
    deps = _Deps(
        session_factory,
        llm if llm is not None else build_tool_llm(),
        user_id=user_id,
        student_no=student_no,
        today=today,
        profile_text=profile_text,
    )
    graph = (
        StateGraph(QueryState)
        .add_node("collect", _make_collect(deps))
        .add_node("wait", _make_wait())
        .add_edge(START, "collect")
        .add_conditional_edges("collect", _collect_after, {"wait": "wait", "end": END})
        .add_edge("wait", "collect")
    )
    return graph.compile(checkpointer=checkpointer)
