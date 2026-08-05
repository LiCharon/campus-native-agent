"""报修侧 6 个确定性工具（M3，需求 §5）。

设计：
- 工厂注入 session_factory（测试传 SQLite 内存库工厂）+ user_id/actor
  （M3 无鉴权，演示固定调用方；M6 JWT 后由 token 决定）
- 函数体 = 唯一实现（测试通过 tool.func 直调，无第二份实现漂移）
- 业务异常（工单不存在/非法跳转）→ 返回以"错误:"开头的字符串（不抛）——
  LLM 能读懂并回复学生；参数 schema 校验失败由 @tool 层抛（模型可见）
- 超时升级 = 字段（urgent_followup 不改状态，只递增计数 + 审计日志）
- M5-T3 埋点：每个工具函数体首行包 telemetry.span("tool.<名>")——工具调用
  作为独立 span 落在 agent.repair 之下（无 key 时纯 no-op）
"""

import json
from datetime import UTC, datetime

from langchain.tools import BaseTool, tool

from campus_desk import telemetry
from campus_desk.db.models import Dorm, Repairman, Ticket, TicketLog
from campus_desk.db.session import SessionFactory
from campus_desk.state_machine.machine import EVENT_TRANSITIONS, TransitionError
from campus_desk.state_machine.transitions import TicketNotFound, apply_transition


def create_repair_tools(
    session_factory: SessionFactory,
    *,
    user_id: str = "student-001",
    actor: str = "student-001",
) -> list[BaseTool]:
    """报修侧 6 工具工厂。user_id=建单提交人；actor=状态跳转操作人（审计）。"""

    @tool("create_ticket", parse_docstring=True)
    def create_ticket(
        description: str,
        contact: str,
        building: str | None = None,
        location: str | None = None,
        ticket_type: str = "repair",
        priority: str = "P2",
    ) -> str:
        """创建报修/投诉工单（状态 SUBMITTED）。报修类必填楼栋，投诉类用位置/对象描述。

        Args:
            description: 问题描述（必填）
            contact: 联系人姓名或学号（必填）
            building: 楼栋（报修类必填，投诉类可空）
            location: 位置/对象（投诉类用，如"食堂阿姨"；报修类可空）
            ticket_type: 工单类型 repair 报修 / complaint 投诉
            priority: 优先级 P1 紧急 / P2 普通 / P3 预约（投诉单由管道传 P1）
        """
        with telemetry.span(
            "tool.create_ticket",
            metadata={"ticket_type": ticket_type, "building": building},
        ):
            if not description.strip() or not contact.strip():
                return "错误: 问题描述和联系人是必填项"
            if priority not in ("P1", "P2", "P3"):
                return f"错误: 未知优先级 {priority}（可选: P1/P2/P3）"
            if ticket_type == "repair" and not (building and building.strip()):
                return "错误: 报修工单需要楼栋信息"
            with session_factory() as session, session.begin():
                ticket = Ticket(
                    user_id=user_id,
                    ticket_type=ticket_type,
                    description=description.strip(),
                    contact=contact.strip(),
                    building=building.strip() if building else None,
                    location=location.strip() if location else None,
                    priority=priority,
                )
                session.add(ticket)
                session.flush()
                ticket_id = ticket.id
            return f"工单 #{ticket_id} 已创建，状态: SUBMITTED，当前待派单"

    @tool("get_ticket", parse_docstring=True)
    def get_ticket(ticket_id: int) -> str:
        """查询工单详情（状态/类别/优先级/维修工/日志）。

        Args:
            ticket_id: 工单号
        """
        with telemetry.span("tool.get_ticket", metadata={"ticket_id": ticket_id}):
            with session_factory() as session, session.begin():
                ticket = session.get(Ticket, ticket_id)
                if ticket is None:
                    return f"错误: 工单 #{ticket_id} 不存在"
                logs = session.query(TicketLog).filter(TicketLog.ticket_id == ticket_id).count()
                repairman = ticket.repairman_id or "未派单"
                return (
                    f"工单 #{ticket_id}: {ticket.description} | 状态 {ticket.status} | "
                    f"类别 {ticket.category} | 等级 {ticket.priority} | "
                    f"维修工 {repairman} | 联系人 {ticket.contact} | 跳转记录 {logs} 条"
                )

    @tool("update_ticket_status", parse_docstring=True)
    def update_ticket_status(
        ticket_id: int, event: str, note: str = "", repairman_id: str | None = None
    ) -> str:
        """推进工单状态（状态机白名单校验，非法跳转拒绝）。事件: assign 派单 / cancel 撤回 / start 接单 / complete 完工 / verify_ok 验收通过 / rework 返工 / auto_close 自动关闭。

        Args:
            ticket_id: 工单号
            event: 状态事件（assign/cancel/start/complete/verify_ok/rework/auto_close）
            note: 备注（如验收意见）
            repairman_id: 派单时指定维修工（如 rm-001，仅 assign 事件用）
        """
        with telemetry.span(
            "tool.update_ticket_status", metadata={"ticket_id": ticket_id, "event": event}
        ):
            if event not in EVENT_TRANSITIONS:
                return f"错误: 未知事件 {event}（可选: {', '.join(EVENT_TRANSITIONS)}）"
            with session_factory() as session, session.begin():
                try:
                    record = apply_transition(
                        session, ticket_id, event, actor, note=note, repairman_id=repairman_id
                    )
                except TicketNotFound:
                    return f"错误: 工单 #{ticket_id} 不存在"
                except TransitionError as exc:  # 状态机白名单校验失败
                    return f"错误: {exc}"
            return f"工单 #{ticket_id} 状态已更新: {record['from_status']} → {record['to_status']}"

    @tool("list_repairmen", parse_docstring=True)
    def list_repairmen(dept: str | None = None, trade: str | None = None) -> str:
        """查询可用维修工（部门+工种过滤，在岗优先排序）。

        Args:
            dept: 部门（信息中心/后勤）
            trade: 工种（网络/账号/水电/家具/门窗）
        """
        with telemetry.span(
            "tool.list_repairmen", metadata={"dept": dept, "trade": trade}
        ):
            with session_factory() as session, session.begin():
                query = session.query(Repairman)
                if dept:
                    query = query.filter(Repairman.dept == dept)
                if trade:
                    query = query.filter(Repairman.trade == trade)
                rows = query.order_by(Repairman.on_duty.desc(), Repairman.id).all()
            if not rows:
                return "未找到匹配的维修工"
            items = [
                {"id": r.id, "name": r.name, "dept": r.dept, "trade": r.trade, "on_duty": r.on_duty}
                for r in rows
            ]
            return json.dumps(items, ensure_ascii=False)

    @tool("query_dorm_info", parse_docstring=True)
    def query_dorm_info(building: str) -> str:
        """查询楼栋/宿舍信息（只读）。

        Args:
            building: 楼栋名（如 3号楼）
        """
        with telemetry.span("tool.query_dorm_info", metadata={"building": building}):
            with session_factory() as session, session.begin():
                dorm = session.query(Dorm).filter(Dorm.building == building).first()
            if dorm is None:
                return f"错误: 未找到楼栋 {building} 的信息"
            return f"{dorm.building}（{dorm.room_range or '无房间范围'}），管理员: {dorm.manager or '未知'}"

    @tool("urgent_followup", parse_docstring=True)
    def urgent_followup(ticket_id: int, note: str = "") -> str:
        """催办/升级工单（超时升级=字段不是状态：计数+1 并记录，工单留在原状态）。

        Args:
            ticket_id: 工单号
            note: 催办说明
        """
        with telemetry.span("tool.urgent_followup", metadata={"ticket_id": ticket_id}):
            with session_factory() as session, session.begin():
                ticket = session.get(Ticket, ticket_id)
                if ticket is None:
                    return f"错误: 工单 #{ticket_id} 不存在"
                if ticket.status in ("CLOSED", "CANCELLED"):
                    return f"错误: 工单 #{ticket_id} 已{('关闭' if ticket.status == 'CLOSED' else '取消')}，无需催办"
                ticket.escalation_count += 1
                ticket.escalated_at = datetime.now(UTC)
                session.add(
                    TicketLog(
                        ticket_id=ticket_id,
                        from_status=ticket.status,
                        to_status=ticket.status,
                        actor=actor,
                        note=f"催办升级（第 {ticket.escalation_count} 次）: {note}",
                    )
                )
                count = ticket.escalation_count
                status = ticket.status
            return f"工单 #{ticket_id} 已升级（第 {count} 次），状态不变: {status}"

    return [
        create_ticket,
        get_ticket,
        update_ticket_status,
        list_repairmen,
        query_dorm_info,
        urgent_followup,
    ]
