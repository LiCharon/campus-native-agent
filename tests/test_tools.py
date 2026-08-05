"""9 个确定性工具独立单测（M3，不依赖 LLM）。

工具 = 工厂注入 session_factory 的 @tool 薄壳；测试通过 tool.func 直调
（函数体是唯一实现，避免 invoke 参数校验干扰断言）。
"""

import json

from campus_desk.db.models import Ticket, TicketLog
from campus_desk.tools.consult_tools import create_consult_tools
from campus_desk.tools.repair_tools import create_repair_tools


def _repair_tools(db_session_factory):
    """工厂构造（演示固定调用方 student-001）。"""
    return {t.name: t for t in create_repair_tools(db_session_factory)}


def _consult_tools(db_session_factory):
    return {t.name: t for t in create_consult_tools(db_session_factory)}


class TestCreateTicket:
    def test_create_repair_ticket(self, db_session_factory):
        tools = _repair_tools(db_session_factory)
        out = tools["create_ticket"].func(
            description="宿舍灯坏了", contact="李华", building="3号楼"
        )
        assert "工单 #1 已创建" in out
        assert "SUBMITTED" in out
        with db_session_factory() as session, session.begin():
            t = session.get(Ticket, 1)
            assert t.building == "3号楼"
            assert t.ticket_type == "repair"
            assert t.status == "SUBMITTED"

    def test_missing_required_fields(self, db_session_factory):
        """必填校验：description/contact 缺失拒绝（返回错误文案不抛）。"""
        tools = _repair_tools(db_session_factory)
        assert "错误" in tools["create_ticket"].func(
            description="", contact="李华", building="3号楼"
        )
        assert "错误" in tools["create_ticket"].func(
            description="灯坏了", contact="", building="3号楼"
        )

    def test_repair_requires_building(self, db_session_factory):
        """报修类必填楼栋（需求 §4 拍板：投诉类用 location 通用字段）。"""
        tools = _repair_tools(db_session_factory)
        assert "错误" in tools["create_ticket"].func(description="灯坏了", contact="李华")
        out = tools["create_ticket"].func(
            description="食堂阿姨态度差", contact="李华", location="食堂", ticket_type="complaint"
        )
        assert "工单 #1 已创建" in out
        with db_session_factory() as session, session.begin():
            t = session.get(Ticket, 1)
            assert t.ticket_type == "complaint"
            assert t.location == "食堂"
            assert t.building is None

    def test_priority_param_persisted(self, db_session_factory):
        """priority 参数落库：传值生效、默认 P2 保持现状、非法值拒绝（M5 投诉 P1 依据）。"""
        tools = _repair_tools(db_session_factory)
        # 传 P1 落库（投诉管道用）
        tools["create_ticket"].func(
            description="食堂态度差", contact="李华", ticket_type="complaint", priority="P1"
        )
        # 不传 → 默认 P2（报修路径零行为变化）
        tools["create_ticket"].func(description="灯坏了", contact="李华", building="3号楼")
        # 非法值拒绝（错误文案不抛）
        out = tools["create_ticket"].func(
            description="灯坏了", contact="李华", building="3号楼", priority="P9"
        )
        assert "错误" in out and "P9" in out
        with db_session_factory() as session, session.begin():
            t1 = session.get(Ticket, 1)
            assert t1.priority == "P1"
            assert session.get(Ticket, 2).priority == "P2"
            assert session.get(Ticket, 3) is None  # 非法值不落库


class TestGetTicket:
    def test_get_existing(self, db_session_factory):
        tools = _repair_tools(db_session_factory)
        tools["create_ticket"].func(description="灯坏了", contact="李华", building="3号楼")
        out = tools["get_ticket"].func(1)
        assert "灯坏了" in out
        assert "SUBMITTED" in out
        assert "跳转记录 0 条" in out

    def test_get_missing(self, db_session_factory):
        tools = _repair_tools(db_session_factory)
        assert "不存在" in tools["get_ticket"].func(999)


class TestUpdateTicketStatus:
    def test_assign_chain(self, db_session_factory):
        """assign → start → complete → verify_ok 全链路 + 审计日志。"""
        tools = _repair_tools(db_session_factory)
        tools["create_ticket"].func(description="灯坏了", contact="李华", building="3号楼")
        assert "ASSIGNED" in tools["update_ticket_status"].func(1, "assign")
        assert "IN_PROGRESS" in tools["update_ticket_status"].func(1, "start")
        assert "PENDING_VERIFY" in tools["update_ticket_status"].func(1, "complete")
        assert "CLOSED" in tools["update_ticket_status"].func(1, "verify_ok", note="修好了")
        with db_session_factory() as session, session.begin():
            logs = session.query(TicketLog).filter(TicketLog.ticket_id == 1).all()
            assert len(logs) == 4
            assert logs[-1].note == "修好了"
            assert logs[-1].actor == "student-001"

    def test_illegal_transition_rejected(self, db_session_factory):
        """非法跳转（SUBMITTED→complete）拒绝，状态零残留。"""
        tools = _repair_tools(db_session_factory)
        tools["create_ticket"].func(description="灯坏了", contact="李华", building="3号楼")
        out = tools["update_ticket_status"].func(1, "complete")
        assert "错误" in out
        with db_session_factory() as session, session.begin():
            assert session.get(Ticket, 1).status == "SUBMITTED"

    def test_unknown_event_rejected(self, db_session_factory):
        tools = _repair_tools(db_session_factory)
        tools["create_ticket"].func(description="灯坏了", contact="李华", building="3号楼")
        assert "未知事件" in tools["update_ticket_status"].func(1, "explode")

    def test_missing_ticket(self, db_session_factory):
        tools = _repair_tools(db_session_factory)
        assert "不存在" in tools["update_ticket_status"].func(999, "assign")

    def test_custom_actor_in_log(self, db_session_factory):
        """工厂注入不同 actor：审计日志记录操作人。"""
        tools = {t.name: t for t in create_repair_tools(db_session_factory, actor="rm-001")}
        tools["create_ticket"].func(description="灯坏了", contact="李华", building="3号楼")
        tools["update_ticket_status"].func(1, "assign")
        with db_session_factory() as session, session.begin():
            assert (
                session.query(TicketLog).filter(TicketLog.ticket_id == 1).first().actor == "rm-001"
            )


class TestListRepairmen:
    def test_filter_by_dept_trade(self, db_session_factory):
        tools = _repair_tools(db_session_factory)
        out = json.loads(tools["list_repairmen"].func(dept="信息中心", trade="网络"))
        assert len(out) == 2
        assert all(r["dept"] == "信息中心" for r in out)

    def test_on_duty_first(self, db_session_factory):
        """在岗优先排序（种子含 1 名 off_duty 的后勤·水电）。"""
        tools = _repair_tools(db_session_factory)
        out = json.loads(tools["list_repairmen"].func(dept="后勤", trade="水电"))
        assert len(out) == 3
        assert out[0]["on_duty"] is True
        assert out[0]["id"] != "rm-008"
        assert out[-1]["id"] == "rm-008"  # off_duty 排最后

    def test_no_match(self, db_session_factory):
        tools = _repair_tools(db_session_factory)
        assert "未找到" in tools["list_repairmen"].func(trade="不存在")


class TestQueryDormInfo:
    def test_existing(self, db_session_factory):
        tools = _repair_tools(db_session_factory)
        out = tools["query_dorm_info"].func("3号楼")
        assert "3号楼" in out
        assert "101-502" in out

    def test_missing(self, db_session_factory):
        tools = _repair_tools(db_session_factory)
        assert "未找到" in tools["query_dorm_info"].func("99号楼")


class TestUrgentFollowup:
    def test_escalate_field_not_status(self, db_session_factory):
        """催办 = 字段升级：计数 +1、时间戳、日志，状态不变（需求 §3 升级不是状态）。"""
        tools = _repair_tools(db_session_factory)
        tools["create_ticket"].func(description="灯坏了", contact="李华", building="3号楼")
        out = tools["urgent_followup"].func(1, note="催一下")
        assert "已升级（第 1 次）" in out
        assert "SUBMITTED" in out  # 状态不变
        with db_session_factory() as session, session.begin():
            t = session.get(Ticket, 1)
            assert t.escalation_count == 1
            assert t.escalated_at is not None
            log = session.query(TicketLog).filter(TicketLog.ticket_id == 1).first()
            assert "催办升级（第 1 次）" in log.note

    def test_escalate_twice(self, db_session_factory):
        tools = _repair_tools(db_session_factory)
        tools["create_ticket"].func(description="灯坏了", contact="李华", building="3号楼")
        tools["urgent_followup"].func(1)
        out = tools["urgent_followup"].func(1)
        assert "第 2 次" in out

    def test_terminal_ticket_rejected(self, db_session_factory):
        """已关闭/已取消的工单不可催办。"""
        tools = _repair_tools(db_session_factory)
        tools["create_ticket"].func(description="灯坏了", contact="李华", building="3号楼")
        tools["update_ticket_status"].func(1, "cancel")
        assert "无需催办" in tools["urgent_followup"].func(1)


class TestConsultTools:
    def test_account_status_three_states(self, db_session_factory):
        tools = _consult_tools(db_session_factory)
        assert "正常" in tools["query_account_status"].func("2024001")
        assert "欠费" in tools["query_account_status"].func("2024002")
        assert "过期" in tools["query_account_status"].func("2024003")

    def test_account_missing(self, db_session_factory):
        tools = _consult_tools(db_session_factory)
        assert "未找到" in tools["query_account_status"].func("0000000")

    def test_announcement_match(self, db_session_factory):
        tools = _consult_tools(db_session_factory)
        out = json.loads(tools["query_announcement"].func("3号楼"))
        assert len(out) == 1
        assert "停电检修" in out[0]["content"]

    def test_announcement_empty(self, db_session_factory):
        tools = _consult_tools(db_session_factory)
        assert "未找到" in tools["query_announcement"].func("5号楼")

    def test_faq_keyword_hit(self, db_session_factory):
        tools = _consult_tools(db_session_factory)
        out = json.loads(tools["search_faq"].func("忘记密码"))
        assert out
        assert "密码" in out[0]["question"]
        # 命中排序：多关键词命中排前
        out2 = json.loads(tools["search_faq"].func("连不上 wifi"))
        assert len(out2) >= 2

    def test_faq_no_hit(self, db_session_factory):
        tools = _consult_tools(db_session_factory)
        assert "未找到" in tools["search_faq"].func("量子力学")


class TestToolSchemas:
    """工具参数 JSON Schema 声明（需求 §5：LangChain @tool 生成）。"""

    def test_schema_required_fields(self, db_session_factory):
        tools = _repair_tools(db_session_factory)
        schema = tools["create_ticket"].args_schema
        assert "description" in schema.model_fields
        assert "contact" in schema.model_fields
        assert schema.model_fields["description"].is_required()
        assert not schema.model_fields["building"].is_required()

    def test_consult_tool_descriptions(self, db_session_factory):
        tools = _consult_tools(db_session_factory)
        assert "网络账号" in tools["query_account_status"].description
        assert "公告" in tools["query_announcement"].description
        assert (
            "FAQ" in tools["search_faq"].description
            or "常见问题" in tools["search_faq"].description
        )
