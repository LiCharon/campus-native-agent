"""确定性组装单测（M2 拍板 Q6）：模板拼中文不调 LLM；空结果与降级文案齐全。"""

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


def test_assemble_rooms_with_hits():
    reply = assemble_rooms(
        {"ok": True, "building": "3号楼", "period": "下午", "rooms": ["301", "305", "308"]}
    )
    assert "3号楼" in reply and "下午" in reply and "301" in reply and "空闲教室" in reply


def test_assemble_rooms_empty_result():
    reply = assemble_rooms({"building": "3号楼", "period": "晚上", "rooms": []})
    assert "暂无空闲教室" in reply


def test_assemble_seats_sums_free():
    reply = assemble_seats(
        {
            "ok": True,
            "floors": [
                {"floor": "1F", "free": 35, "total": 120},
                {"floor": "2F", "free": 42, "total": 130},
            ],
        }
    )
    assert "77" in reply and "空余座位" in reply and "1F" in reply


def test_degraded_replies_cover_both_tools():
    assert "电子屏" in DEGRADED_REPLIES["query_empty_rooms"]
    assert "大屏" in DEGRADED_REPLIES["query_library_seats"]
    assert "工作人员" in HANDOFF_REPLY
    assert "电子屏" in CIRCUIT_DEGRADED_REPLY


def test_degraded_replies_cover_all_fc_tools():
    """FC 扩展：13 个工具全部有专属降级文案（四层失败链②索引引导降级）。"""
    assert set(DEGRADED_REPLIES) == {
        "query_empty_rooms",
        "query_library_seats",
        "query_timetable",
        "query_exam_scores",
        "query_exam_schedule",
        "query_library_borrow",
        "query_card_balance",
        "query_dorm_power",
        "register_lost_item",
        "search_lost_items",
        "query_shuttle_schedule",
        "query_calendar",
        "query_announcements",
    }
    for reply in DEGRADED_REPLIES.values():
        assert reply.endswith("。")


class TestFcAssemblers:
    """FC 扩展：11 个新 assemble_* 模板（命中/空结果两分支）。"""

    def test_timetable_hits(self):
        reply = assemble_timetable(
            {
                "ok": True,
                "week": 6,
                "weekday": 3,
                "courses": [{"period": "上午", "course": "操作系统", "location": "教401", "teacher": "赵老师"}],
            }
        )
        assert "第 6 周" in reply and "周三" in reply and "操作系统" in reply and "教401" in reply

    def test_timetable_empty(self):
        reply = assemble_timetable({"ok": True, "week": 18, "weekday": 7, "courses": []})
        assert "暂无课程安排" in reply

    def test_scores_hits(self):
        reply = assemble_scores(
            {"ok": True, "term": "2026-2027-1", "scores": [{"course": "操作系统", "score": 91, "credit": 4.0}]}
        )
        assert "2026-2027-1" in reply and "操作系统" in reply and "91" in reply and "4.0" in reply

    def test_scores_empty(self):
        reply = assemble_scores({"ok": True, "term": "2026-2027-1", "scores": []})
        assert "暂无成绩" in reply or "未查询到" in reply

    def test_exams_hits(self):
        reply = assemble_exams(
            {
                "ok": True,
                "term": "2026-2027-1",
                "exams": [{"course": "操作系统", "exam_date": "2027-01-11", "exam_time": "上午", "location": "教401"}],
            }
        )
        assert "2027-01-11" in reply and "教401" in reply and "上午" in reply

    def test_borrow_overdue_cn(self):
        reply = assemble_borrow(
            {
                "ok": True,
                "borrows": [
                    {"book_title": "Python 编程", "due_date": "2026-08-19", "status": "OVERDUE"},
                    {"book_title": "算法导论", "due_date": "2026-09-11", "status": "BORROWED"},
                ],
            }
        )
        assert "已超期" in reply and "在借" in reply and "算法导论" in reply

    def test_borrow_empty(self):
        reply = assemble_borrow({"ok": True, "borrows": []})
        assert "没有在借" in reply

    def test_card_balance_hit(self):
        reply = assemble_card_balance({"ok": True, "balance": 45.6})
        assert "45.6" in reply and "余额" in reply

    def test_card_balance_none(self):
        reply = assemble_card_balance({"ok": True, "balance": None})
        assert "未查询到" in reply

    def test_dorm_power_hit(self):
        reply = assemble_dorm_power({"ok": True, "building": "3号楼", "room": "205", "power_left": 12.6})
        assert "3号楼" in reply and "205" in reply and "12.6" in reply and "剩余电量" in reply

    def test_lost_register(self):
        reply = assemble_lost_register(
            {"ok": True, "item": "校园卡", "location": "3号楼201", "lost_date": "2026-08-19", "record_id": 11}
        )
        assert "校园卡" in reply and "3号楼201" in reply and "11" in reply

    def test_lost_search_hits(self):
        reply = assemble_lost_search(
            {
                "ok": True,
                "keyword": "书包",
                "items": [{"item_name": "黑色书包", "location": "图书馆2楼", "lost_date": "2026-08-15"}],
            }
        )
        assert "1 条" in reply and "黑色书包" in reply and "图书馆2楼" in reply

    def test_lost_search_empty(self):
        reply = assemble_lost_search({"ok": True, "keyword": "xyz", "items": []})
        assert "暂未找到" in reply

    def test_shuttle_hits(self):
        reply = assemble_shuttle(
            {
                "ok": True,
                "line": "屏峰-朝晖",
                "direction": "去程",
                "departures": [{"time": "07:30"}, {"time": "09:00"}],
            }
        )
        assert "屏峰-朝晖" in reply and "去程" in reply and "07:30" in reply and "09:00" in reply

    def test_calendar_hits(self):
        reply = assemble_calendar(
            {
                "ok": True,
                "term": "2026-2027-1",
                "weeks": [{"week": 19, "week_start": "2027-01-11", "label": "考试周"}],
            }
        )
        assert "考试周" in reply and "19" in reply

    def test_announcements_hits(self):
        reply = assemble_announcements(
            {
                "ok": True,
                "keyword": "教务处",
                "announcements": [
                    {"title": "选课通知", "publish_date": "2026-08-25", "source": "教务处"}
                ],
            }
        )
        assert "选课通知" in reply and "教务处" in reply
