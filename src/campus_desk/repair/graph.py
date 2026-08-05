"""RepairGraph（M3 核心，M5-T2 扩展）：报修主链路图，ticket_type="complaint" 复用为投诉管道。

节点链：collect（消费输入/抽字段/算缺项）→ wait（唯一 interrupt）→
classify（规则+LLM 分类定级+人工确认门控）→ create（建单+自动派单）→ finalize。

投诉模式（M5：complaint 复用同图，构建参数 ticket_type 驱动）：
- 必填集仅 contact（不追问楼栋）；跳过 classify（省一次 LLM、无确认轮）
- create 建 complaint 单停 SUBMITTED：不自动派单、不更新报修画像（投诉不污染画像）
- 无实质描述（<4 字）转人工不建单：collect 标记 rejected → finalize 直出转人工文案

核心语义（M1 已验证 + 本会话实测确认）：
- interrupt 收敛到唯一节点 wait：collect/classify 是纯逻辑节点（问句+计数
  由 return 持久化）；interrupt 重入不落盘，节点内不得依赖"中断前修改"
- 恢复：graph.invoke(Command(resume=msg), cfg)，interrupt() 返回 msg；
  中断时 get_state().next=('wait',)，终态 next=()
- 终态 thread 再 invoke = 旧 state 残留 + 再次中断（实测混乱）→ 新会话必须新 thread_id
- 状态变更不嵌套状态机子图（规避嵌套子图未验证面）：create 节点直接调工具
  （create_ticket → apply_transition assign），8 条边白名单由 machine.py 锁

每轮输出契约：reply（给学生的话）/ pending_question（等待中的问题，
None = 本轮可继续）/ tool_calls、status_events（评测断言）。
"""

import re
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt  # resume 由编排层传 Command

from campus_desk.db.models import Repairman
from campus_desk.db.session import SessionFactory
from campus_desk.repair.classify import RepairClassifier
from campus_desk.repair.drafting import (
    MAX_ROUNDS,
    REQUIRED,
    FieldExtractor,
    merge_extract,
    pick_question,
    required_missing,
)
from campus_desk.repair.profile import (
    ProfileStore,
    profile_text,
    same_category_as_before,
)
from campus_desk.tools.repair_tools import create_repair_tools

CONFIRM_WORDS = ("对", "好", "可以", "没问题", "确认", "是的", "是", "行")
# 否定词优先判定（"不对"含"对"子串——M3 测试抓出，先查否定再查确认）
_DENY_WORDS = ("不对", "不是", "错了", "不行", "不用", "别", "错")

# 类别 → (部门, 工种) 派单映射（需求 §4：网络/账号→信息中心；水电/家具/门窗→后勤）
CATEGORY_DEPT_TRADE: dict[str, tuple[str | None, str | None]] = {
    "网络": ("信息中心", "网络"),
    "水电": ("后勤", "水电"),
    "门窗": ("后勤", "门窗"),
    "设备": ("后勤", "家具"),
    "环境": ("后勤", "家具"),
    "其他": (None, None),  # 兜底：后勤在岗第一个
}


class RepairState(TypedDict):
    user_input: str
    student_answer: str | None
    draft: dict  # description/contact/building/room/rounds
    pending_question: str | None
    pending_stage: Literal["collect", "classify"] | None
    classification: dict | None
    profile: dict | None  # 画像快照（M4：classify 前读取，finalize 判断"上次同类"用）
    ticket_id: int | None
    ticket_status: str | None
    repairman: dict | None
    rejected: bool  # M5：投诉无实质 → 转人工不建单（collect 标记，finalize 出文案）
    reply: str
    tool_calls: list[str]
    status_events: list[str]
    finished: bool
    _consumed: bool


def _is_confirm(answer: str) -> bool:
    """确认语义判定：否定词优先（"不对"含"对"，先查否定再查确认）。"""
    if any(word in answer for word in _DENY_WORDS):
        return False
    return any(word in answer for word in CONFIRM_WORDS)


def dispatch(
    session_factory: SessionFactory, category: str | None, priority: str | None
) -> dict | None:
    """自动派单：部门+工种两层匹配，在岗优先（规则优先拍板）。

    返回 {"id","name","dept","trade"}；无在岗维修工时返回 None（人工待派）。
    P1 紧急单在此无额外标记（在岗列表第一个即最高优先级可用人选，M5 可加）。
    """
    dept, trade = CATEGORY_DEPT_TRADE.get(category or "", (None, None))
    with session_factory() as session, session.begin():
        query = session.query(Repairman).filter(Repairman.on_duty.is_(True))
        if trade:
            query = query.filter(Repairman.trade == trade)
        elif dept:
            query = query.filter(Repairman.dept == dept)
        else:  # 其他/未分类：后勤兜底
            query = query.filter(Repairman.dept == "后勤")
        rm = query.order_by(Repairman.id).first()
    if rm is None:
        return None
    return {"id": rm.id, "name": rm.name, "dept": rm.dept, "trade": rm.trade}


class _NodeDeps:
    """节点闭包依赖（构造注入，节点签名保持 (state)）。"""

    def __init__(
        self,
        session_factory: SessionFactory,
        extractor: FieldExtractor,
        classifier: RepairClassifier,
        *,
        user_id: str,
        actor: str,
        default_contact: str,
        profile_store: ProfileStore,
        mode: str = "repair",
    ):
        self.session_factory = session_factory
        self.extractor = extractor
        self.classifier = classifier
        self.default_contact = default_contact
        self.user_id = user_id
        self.profile_store = profile_store
        # mode 是构建参数不是 state 字段（M5：ticket_type="complaint" 复用为投诉管道）
        self.mode = "complaint" if mode == "complaint" else "repair"
        self.tools = {
            t.name: t for t in create_repair_tools(session_factory, user_id=user_id, actor=actor)
        }


def _make_collect(deps: _NodeDeps):
    def collect(state: RepairState) -> dict:
        """纯逻辑节点：消费输入 → 抽字段 → 算缺项 → 追问或放行。

        问句 + 追问计数由 return 持久化（wait 节点负责暂停）——
        若在此节点内直接 interrupt，重入不落盘导致计数永远数不上。
        """
        draft = dict(state.get("draft", {}))
        tool_calls = list(state.get("tool_calls", []))

        if not state.get("_consumed"):
            ext = deps.extractor.extract(state["user_input"])
            draft = merge_extract(draft, ext)
            draft.setdefault("rounds", 0)
            consumed = True
        elif state.get("student_answer"):
            ext = deps.extractor.extract(state["student_answer"])
            draft = merge_extract(draft, ext)
            draft["rounds"] = draft.get("rounds", 0) + 1
            consumed = True
        else:
            consumed = state.get("_consumed", False)

        missing = required_missing(
            draft, required=("contact",) if deps.mode == "complaint" else REQUIRED
        )

        # 投诉无实质判定（描述 <4 字，如"我投诉"）：首轮仍先走 contact 追问
        # （保证剧本可断言：先问联系人），resume 轮再次判定仍无实质 → 拒绝
        # 转人工不建单（rejected 标记，finalize 出转人工文案）
        if deps.mode == "complaint" and len(draft.get("description", "").strip()) < 4:
            first_ask = (
                not state.get("_consumed") and missing and draft.get("rounds", 0) < MAX_ROUNDS
            )
            if not first_ask:
                return {
                    "draft": draft,
                    "pending_question": None,
                    "pending_stage": None,
                    "student_answer": None,
                    "tool_calls": [*tool_calls, "handoff_reject"],
                    "rejected": True,
                    "_consumed": consumed,
                }

        if missing and draft.get("rounds", 0) < MAX_ROUNDS:
            question = pick_question(missing, mode=deps.mode)
            return {
                "draft": draft,
                "pending_question": question,
                "pending_stage": "collect",
                "reply": question,
                "tool_calls": [*tool_calls, "ask_collect"],
                "student_answer": None,
                "_consumed": consumed,
            }
        return {
            "draft": draft,
            "pending_question": None,
            "student_answer": None,
            "_consumed": consumed,
        }

    return collect


def _make_wait():
    def wait(state: RepairState) -> dict:
        """唯一 interrupt 节点。用 state 里已持久化的同一 value 暂停——
        interrupt 按 value 匹配暂停点，重入时返回 Command(resume=) 的值。"""
        answer = interrupt(state["pending_question"])
        return {"student_answer": str(answer)}

    return wait


def _make_classify(deps: _NodeDeps):
    def classify(state: RepairState) -> dict:
        """分类定级 + 人工确认门控（确认轮复用 wait 暂停机制）。

        M4 画像注入（需求 §7，时机已拍死 = 分类定级前）：读取画像 → 只拼进
        LLM prompt（profile_text），并快照进 state 供 finalize"上次同类"提示。
        """
        draft = state["draft"]
        description = draft.get("description", "")
        tool_calls = list(state.get("tool_calls", []))

        # 确认轮：学生回答"对/好"→ 放行；有异议 → 保留分类但标记人工复核
        if state.get("pending_stage") == "classify" and state.get("student_answer"):
            classification = state["classification"]
            answer = state["student_answer"]
            confirmed = _is_confirm(answer)
            if not confirmed:
                classification = dict(classification)
                classification["reason"] = (
                    classification.get("reason", "") + "；学生提出异议，人工复核"
                )
            return {
                "classification": classification,
                "pending_question": None,
                "pending_stage": None,
                "student_answer": None,
                "tool_calls": [*tool_calls, "ask_confirm"],
            }

        profile = deps.profile_store.get_profile(deps.user_id)
        result = deps.classifier.classify(description, profile_text(profile) if profile else None)
        classification = result.model_dump()
        if result.needs_human_confirm:
            question = (
                f"您的报修归为【{result.category}】类、按【{result.priority}】"
                f"处理，对吗？确认后为您创建工单。"
            )
            return {
                "classification": classification,
                "profile": profile,
                "pending_question": question,
                "pending_stage": "classify",
                "reply": question,
                "tool_calls": [*tool_calls, "ask_confirm"],
                "student_answer": None,
            }
        return {
            "classification": classification,
            "profile": profile,
            "student_answer": None,
        }

    return classify


def _make_create(deps: _NodeDeps):
    def create(state: RepairState) -> dict:
        """建单 + 自动派单（SUBMITTED → ASSIGNED 两步，审计日志落库）。

        投诉分支：建 complaint 单停 SUBMITTED——不自动派单（待管理员）、
        不更新报修画像（投诉不污染画像）。
        """
        draft = state["draft"]
        classification = state.get("classification") or {}
        tool_calls = list(state.get("tool_calls", []))
        status_events = list(state.get("status_events", []))

        contact = draft.get("contact") or deps.default_contact
        if deps.mode == "complaint":
            out = deps.tools["create_ticket"].func(
                description=draft.get("description", ""),
                contact=contact,
                building=None,
                location=draft.get("location"),
                ticket_type="complaint",
                priority="P1",  # 需求拍死：投诉 = P1 工单（不分类不定级，直接 P1）
            )
        else:
            building = draft.get("building")
            room = draft.get("room")
            location = f"{room}室" if room else None
            out = deps.tools["create_ticket"].func(
                description=draft.get("description", ""),
                contact=contact,
                building=building,
                location=location,
                priority=classification.get(
                    "priority", "P2"
                ),  # M5 修复：分类定级落库（P1 安全单按 4h 升级）
            )
        m = re.search(r"工单 #(\d+)", out)
        if m is None:
            return {"reply": f"建单失败：{out}", "finished": True}
        ticket_id = int(m.group(1))
        tool_calls.append("create_ticket")
        status_events.append("SUBMITTED")
        if deps.mode == "complaint":
            # 投诉：停在 SUBMITTED 待管理员，跳过 dispatch 与画像更新
            return {
                "ticket_id": ticket_id,
                "ticket_status": "SUBMITTED",
                "repairman": None,
                "tool_calls": tool_calls,
                "status_events": status_events,
            }
        # M4 画像随工单提交更新（需求 §7）：楼栋/类别计数/上次摘要
        deps.profile_store.update_profile(
            deps.user_id,
            building=draft.get("building"),
            category=classification.get("category", ""),
            description=draft.get("description", ""),
        )

        rm = dispatch(
            deps.session_factory, classification.get("category"), classification.get("priority")
        )
        if rm is not None:
            assign_out = deps.tools["update_ticket_status"].func(
                ticket_id, "assign", note=f"自动派单给 {rm['name']}", repairman_id=rm["id"]
            )
            if "已更新" in assign_out:
                tool_calls.append("update_ticket_status")
                status_events.append("SUBMITTED->ASSIGNED")
                ticket_status = "ASSIGNED"
            else:
                rm = None  # 派单失败不阻塞建单（返回状态仍 SUBMITTED）
                ticket_status = "SUBMITTED"
        else:
            ticket_status = "SUBMITTED"

        return {
            "ticket_id": ticket_id,
            "ticket_status": ticket_status,
            "repairman": rm,
            "tool_calls": tool_calls,
            "status_events": status_events,
        }

    return create


def _make_finalize(deps: _NodeDeps):
    def finalize(state: RepairState) -> dict:
        # 投诉无实质 → 转人工不建单（不出现工单号）
        if state.get("rejected"):
            return {
                "reply": "您的投诉内容不明确，已为您转人工核实，请保持在线。",
                "finished": True,
            }
        ticket_id = state.get("ticket_id")
        if deps.mode == "complaint":
            return {
                "reply": (
                    f"您的投诉单 #{ticket_id} 已创建（SUBMITTED），已转交管理员处理，请耐心等待。"
                ),
                "finished": True,
            }
        rm = state.get("repairman")
        status = state.get("ticket_status")
        if rm:
            reply = (
                f"您的报修工单 #{ticket_id} 已创建并派给 {rm['name']}"
                f"（{rm['dept']}·{rm['trade']}），当前状态 {status}。"
                f"维修完成后会通知您验收。"
            )
        else:
            reply = f"您的报修工单 #{ticket_id} 已创建（{status}），正在等待派单，请耐心等待。"
        # M4 画像提示：上次也报修过同类问题（用 classify 前快照，非本次更新后的画像）
        classification = state.get("classification") or {}
        category = classification.get("category", "")
        if category and same_category_as_before(state.get("profile"), category):
            reply += " 检测到您之前也报修过同类问题，已优先跟进。"
        return {"reply": reply, "finished": True}

    return finalize


def _make_collect_after(deps: _NodeDeps):
    def collect_after(
        state: RepairState,
    ) -> Literal["wait", "classify", "create", "finalize"]:
        """collect 出口：rejected → finalize（转人工不建单）；
        投诉跳过 classify 直接 create（无确认轮）；报修走原逻辑。"""
        if state.get("rejected"):
            return "finalize"
        if deps.mode == "complaint":
            return "create" if not state.get("pending_question") else "wait"
        return "wait" if state.get("pending_question") else "classify"

    return collect_after


def _make_wait_after():
    def wait_after(state: RepairState) -> Literal["collect", "classify"]:
        # 投诉永不进 classify（pending_stage 只有 collect），此函数保持原逻辑
        return "collect" if state.get("pending_stage") == "collect" else "classify"

    return wait_after


def _make_classify_after():
    def classify_after(state: RepairState) -> Literal["wait", "create"]:
        return "wait" if state.get("pending_question") else "create"

    return classify_after


def build_repair_graph(
    session_factory: SessionFactory,
    *,
    extractor: FieldExtractor | None = None,
    classifier: RepairClassifier | None = None,
    checkpointer=None,
    user_id: str = "student-001",
    actor: str = "student-001",
    default_contact: str = "学生",
    profile_store: ProfileStore | None = None,
    ticket_type: str = "repair",
):
    """构建 RepairGraph。checkpointer 必传（interrupt 需持久化；测试传 InMemorySaver）。

    profile_store 可注入（测试用 SQLite 工厂实例，默认同 session_factory）。
    ticket_type="complaint"（M5）复用为投诉管道：必填集仅 contact、跳过
    classify、建单停 SUBMITTED 不派单、不更新报修画像、无实质描述转人工。
    默认 "repair" 保证现有调用方零改动。
    """
    deps = _NodeDeps(
        session_factory,
        extractor if extractor is not None else FieldExtractor(),
        classifier if classifier is not None else RepairClassifier(),
        user_id=user_id,
        actor=actor,
        default_contact=default_contact,
        profile_store=profile_store if profile_store is not None else ProfileStore(session_factory),
        mode=ticket_type,
    )
    collect_after = _make_collect_after(deps)
    wait_after = _make_wait_after()
    classify_after = _make_classify_after()

    graph = (
        StateGraph(RepairState)
        .add_node("collect", _make_collect(deps))
        .add_node("wait", _make_wait())
        .add_node("classify", _make_classify(deps))
        .add_node("create", _make_create(deps))
        .add_node("finalize", _make_finalize(deps))
        .add_edge(START, "collect")
        .add_conditional_edges(
            "collect",
            collect_after,
            {
                "wait": "wait",
                "classify": "classify",
                "create": "create",
                "finalize": "finalize",
            },
        )
        .add_conditional_edges("wait", wait_after, {"collect": "collect", "classify": "classify"})
        .add_conditional_edges("classify", classify_after, {"wait": "wait", "create": "create"})
        .add_edge("create", "finalize")
        .add_edge("finalize", END)
    )
    return graph.compile(checkpointer=checkpointer)
