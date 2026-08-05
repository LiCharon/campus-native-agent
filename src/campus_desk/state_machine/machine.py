"""工单状态机（M3）：6 态 + 事件白名单，纯函数无 IO——唯一事实源。

需求 §3 已拍板：
- 6 态：SUBMITTED → ASSIGNED → IN_PROGRESS → PENDING_VERIFY → CLOSED + CANCELLED
- 完整边清单 8 条（测试照单锁定，6×6 全矩阵仅这 8 对合法）
- 非法跳转图结构不允许（state_graph.py 渲染 + 此处校验双保险）

超时升级是字段不是状态（escalation_count/escalated_at，见 transitions.py），
这里只定义生命周期位置。
"""

from typing import Literal, TypedDict

TicketStatus = Literal[
    "SUBMITTED", "ASSIGNED", "IN_PROGRESS", "PENDING_VERIFY", "CLOSED", "CANCELLED"
]
TicketEvent = Literal["assign", "cancel", "start", "complete", "verify_ok", "rework", "auto_close"]

# 8 条边白名单：源状态 → 可达目标集合（需求 §3 完整边清单，测试逐条锁定）
VALID_TRANSITIONS: dict[TicketStatus, frozenset[TicketStatus]] = {
    "SUBMITTED": frozenset({"ASSIGNED", "CANCELLED"}),  # 管理员派单 / 学生撤回
    "ASSIGNED": frozenset({"IN_PROGRESS", "CANCELLED"}),  # 维修工接单 / 学生撤回
    "IN_PROGRESS": frozenset({"PENDING_VERIFY"}),  # 完工待验收
    "PENDING_VERIFY": frozenset(
        {"CLOSED", "IN_PROGRESS"}
    ),  # 验收通过 / 返工 / 挂起自动关闭（同目标不同事件）
    "CLOSED": frozenset(),
    "CANCELLED": frozenset(),
}

# 事件 → (合法源集合, 目标)。cancel 双源（SUBMITTED/ASSIGNED 都可撤）。
EVENT_TRANSITIONS: dict[TicketEvent, tuple[frozenset[TicketStatus], TicketStatus]] = {
    "assign": (frozenset({"SUBMITTED"}), "ASSIGNED"),  # 管理员派单
    "cancel": (frozenset({"SUBMITTED", "ASSIGNED"}), "CANCELLED"),  # 学生撤回（维修中不可撤）
    "start": (frozenset({"ASSIGNED"}), "IN_PROGRESS"),  # 维修工接单
    "complete": (frozenset({"IN_PROGRESS"}), "PENDING_VERIFY"),  # 完工待验收
    "verify_ok": (frozenset({"PENDING_VERIFY"}), "CLOSED"),  # 验收通过
    "rework": (frozenset({"PENDING_VERIFY"}), "IN_PROGRESS"),  # 验收不通过返工
    "auto_close": (frozenset({"PENDING_VERIFY"}), "CLOSED"),  # 挂起 3 天无响应自动关闭（事件）
}

ALL_STATUSES: tuple[TicketStatus, ...] = tuple(VALID_TRANSITIONS)
ALL_EVENTS: tuple[TicketEvent, ...] = tuple(EVENT_TRANSITIONS)


class TransitionError(Exception):
    """状态机校验失败（非法跳转）。"""

    def __init__(
        self, current: TicketStatus, target: TicketStatus, event: TicketEvent | None, actor: str
    ):
        self.current = current
        self.target = target
        self.event = event
        self.actor = actor
        super().__init__(f"非法状态跳转: {current} -{event}-> {target}（actor={actor}）")


def can_transition(current: TicketStatus, target: TicketStatus) -> bool:
    """白名单内可达？（纯函数，无 IO）"""
    return target in VALID_TRANSITIONS.get(current, frozenset())


def validate_transition(
    current: TicketStatus,
    target: TicketStatus,
    event: TicketEvent | None = None,
    actor: str = "system",
) -> None:
    """校验单次跳转：白名单内通过，否则抛 TransitionError（纯函数，零 IO）。

    event 可空——边集校验与事件校验统一入口；事件存在时额外校验事件合法源。
    """
    if not can_transition(current, target):
        raise TransitionError(current, target, event, actor)
    if event is not None:
        sources, _ = EVENT_TRANSITIONS[event]
        if current not in sources:
            raise TransitionError(current, target, event, actor)


class TransitionRecord(TypedDict):
    """apply_transition 的返回契约（工具/图/评测断言用）。"""

    ticket_id: int
    from_status: TicketStatus
    to_status: TicketStatus
    event: TicketEvent
    actor: str
