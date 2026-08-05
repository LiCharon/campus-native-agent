"""状态机白名单测试（M3）：8 条边逐条锁定 + 6×6 全矩阵仅 8 对合法。

需求 §3：完整边清单 8 条，测试照单锁定——这是状态机的唯一事实源，
边表改动必须先动这里（先写失败测试再改实现）。
"""

import pytest

from campus_desk.state_machine.machine import (
    ALL_EVENTS,
    ALL_STATUSES,
    EVENT_TRANSITIONS,
    VALID_TRANSITIONS,
    TransitionError,
    can_transition,
    validate_transition,
)

# 8 条边照单锁定（需求 §3 完整边清单）
EXPECTED_EDGES = {
    ("SUBMITTED", "ASSIGNED"),
    ("SUBMITTED", "CANCELLED"),
    ("ASSIGNED", "IN_PROGRESS"),
    ("ASSIGNED", "CANCELLED"),
    ("IN_PROGRESS", "PENDING_VERIFY"),
    ("PENDING_VERIFY", "CLOSED"),
    ("PENDING_VERIFY", "IN_PROGRESS"),
    ("PENDING_VERIFY", "CLOSED"),  # auto_close 事件同目标（边表合并为一条）
}


class TestWhiteListEdges:
    def test_all_edges_legal(self):
        """白名单包含全部 8 条边（8 个跳转动作；唯一 (src,dst) 对 7 个——
        PENDING_VERIFY→CLOSED 由 verify_ok/auto_close 双事件共享一条边）。"""
        edge_set = {(src, dst) for src, dsts in VALID_TRANSITIONS.items() for dst in dsts}
        assert EXPECTED_EDGES <= edge_set
        assert len(edge_set) == 7  # 唯一对 7 = 8 动作 - 1 共享目标

    def test_full_matrix_only_legal_pairs(self):
        """6×6 全矩阵仅 7 对可达（无白名单外的漏网之鱼）。"""
        legal = 0
        for src in ALL_STATUSES:
            for dst in ALL_STATUSES:
                if can_transition(src, dst):
                    legal += 1
        assert legal == 7

    def test_terminal_states_have_no_edges(self):
        """CLOSED / CANCELLED 是终态，无出边（CLOSED 不再激活，CANCELLED 不可恢复）。"""
        assert VALID_TRANSITIONS["CLOSED"] == frozenset()
        assert VALID_TRANSITIONS["CANCELLED"] == frozenset()

    def test_illegal_transition_raises(self):
        """非法跳转抛 TransitionError，携带诊断字段。"""
        with pytest.raises(TransitionError) as exc:
            validate_transition("SUBMITTED", "IN_PROGRESS", "start", "student-001")
        assert exc.value.current == "SUBMITTED"
        assert exc.value.target == "IN_PROGRESS"
        assert exc.value.event == "start"
        assert exc.value.actor == "student-001"

    def test_rework_edge_legal(self):
        """返工边（验收不通过 PENDING_VERIFY→IN_PROGRESS，Qwen 二轮审查补）。"""
        assert can_transition("PENDING_VERIFY", "IN_PROGRESS")


class TestEventMapping:
    def test_events_cover_all_edges(self):
        """事件映射与边集一致：每个事件的目标边都在白名单内。"""
        for event, (sources, target) in EVENT_TRANSITIONS.items():
            for src in sources:
                assert (src, target) in EXPECTED_EDGES, f"事件 {event} 的目标边不在白名单"

    def test_cancel_dual_source(self):
        """cancel 双源：SUBMITTED 和 ASSIGNED 都可撤（学生撤回，维修中不可撤）。"""
        assert EVENT_TRANSITIONS["cancel"] == (frozenset({"SUBMITTED", "ASSIGNED"}), "CANCELLED")

    def test_event_source_validation(self):
        """事件合法性校验：源状态不在事件合法源内也抛错（即使边本身合法）。"""
        # ASSIGNED→CANCELLED 边合法，但 verify_ok 事件的合法源是 PENDING_VERIFY
        with pytest.raises(TransitionError):
            validate_transition("ASSIGNED", "CANCELLED", "verify_ok", "system")

    def test_all_events_used(self):
        """7 个事件全部有定义（无死事件）。"""
        assert set(ALL_EVENTS) == {
            "assign",
            "cancel",
            "start",
            "complete",
            "verify_ok",
            "rework",
            "auto_close",
        }


class TestGraphRender:
    """图 = 白名单的渲染：编译后的图边与白名单一致（"非法跳转图结构不存在"）。"""

    def test_graph_edges_match_whitelist(self):
        from campus_desk.state_machine.state_graph import build_ticket_state_graph

        graph = build_ticket_state_graph()
        raw = graph.get_graph().edges  # set[tuple[str, str]]
        edges = {(e[0], e[1]) for e in raw}
        expected = {(src, dst) for src, dsts in VALID_TRANSITIONS.items() for dst in dsts}
        # 白名单边全部在图里（START→SUBMITTED / CLOSED/CANCELLED→END 是图机制边）
        assert expected <= edges
        assert len(edges) == 7 + 3  # 7 白名嘴边 + START/CLOSED/CANCELLED 3 条机制边

    def test_graph_walk_full_chain(self):
        """图真实走一遍完整链路：SUBMITTED→…→CLOSED（节点只记录不写库）。"""
        from campus_desk.state_machine.state_graph import build_ticket_state_graph

        graph = build_ticket_state_graph()
        out = graph.invoke({"ticket_id": 1, "status": "SUBMITTED", "status_events": []})
        assert out["status"] == "CLOSED"
        assert out["status_events"] == [
            "SUBMITTED->ASSIGNED",
            "ASSIGNED->IN_PROGRESS",
            "IN_PROGRESS->PENDING_VERIFY",
            "PENDING_VERIFY->CLOSED",
        ]

    def test_graph_no_illegal_edge(self):
        """白名单外的边在图结构中不存在（IN_PROGRESS→CLOSED 无法直连）。"""
        from campus_desk.state_machine.state_graph import build_ticket_state_graph

        graph = build_ticket_state_graph()
        edges = {(e[0], e[1]) for e in graph.get_graph().edges}
        assert ("IN_PROGRESS", "CLOSED") not in edges
        assert ("SUBMITTED", "IN_PROGRESS") not in edges

    def test_graph_cancel_branch(self):
        """_next 显式指定走撤回分支（SUBMITTED→CANCELLED，终态无环）。"""
        from campus_desk.state_machine.state_graph import build_ticket_state_graph

        graph = build_ticket_state_graph()
        out = graph.invoke(
            {"ticket_id": 1, "status": "SUBMITTED", "status_events": [], "_next": "CANCELLED"}
        )
        assert out["status"] == "CANCELLED"
        assert out["status_events"] == ["SUBMITTED->CANCELLED"]

    def test_graph_rework_edge_rendered(self):
        """返工边已渲染进图（PENDING_VERIFY→IN_PROGRESS 存在）；该边是环
        （IN_PROGRESS→PENDING_VERIFY 可回），真实推进靠外部事件驱动
        （apply_transition），图不自动走环（否则递归超限）。"""
        from campus_desk.state_machine.state_graph import build_ticket_state_graph

        graph = build_ticket_state_graph()
        edges = {(e[0], e[1]) for e in graph.get_graph().edges}
        assert ("PENDING_VERIFY", "IN_PROGRESS") in edges
        assert ("IN_PROGRESS", "PENDING_VERIFY") in edges
