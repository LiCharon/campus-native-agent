"""FC 扩展工具单测（M2+）：11 个新工具，不依赖 LLM（沿用确定性工具铁律）。

覆盖：命中 / 空结果合法 / DB 异常 → error_kind=db / 个人工具无学号 → auth /
register 写库（count+1 且 reporter 正确）/ search LIKE 与倒序 / date 参数校验。
"""

from datetime import UTC, datetime

from campus_desk.db.models import CardBalance, LostItem
from campus_desk.query.tools import (
    query_announcements,
    query_calendar,
    query_card_balance,
    query_dorm_power,
    query_exam_schedule,
    query_exam_scores,
    query_library_borrow,
    query_shuttle_schedule,
    query_timetable,
    register_lost_item,
    search_lost_items,
)

STUDENT = "2024001"


class TestPersonalTools:
    """课表/成绩/考试/借阅/余额：学号注入后命中，缺失时 error_kind=auth。"""

    def test_timetable_hit(self, db_session_factory):
        res = query_timetable(db_session_factory, week=6, weekday=3, student_no=STUDENT)
        assert res["ok"] is True
        assert res["week"] == 6 and res["weekday"] == 3
        courses = [c["course"] for c in res["courses"]]
        assert "操作系统" in courses and "线性代数" in courses

    def test_timetable_missing_student_no_auth(self, db_session_factory):
        res = query_timetable(db_session_factory, week=6, weekday=3)
        assert res["ok"] is False and res["error_kind"] == "auth"

    def test_timetable_empty_week_legal(self, db_session_factory):
        res = query_timetable(db_session_factory, week=18, weekday=7, student_no=STUDENT)
        assert res["ok"] is True and res["courses"] == []

    def test_exam_scores_hit(self, db_session_factory):
        res = query_exam_scores(db_session_factory, term="2026-2027-1", student_no=STUDENT)
        assert res["ok"] is True
        assert len(res["scores"]) == 6
        assert any(s["course"] == "操作系统" and s["score"] == 91 for s in res["scores"])

    def test_exam_scores_missing_student_no_auth(self, db_session_factory):
        res = query_exam_scores(db_session_factory, term="2026-2027-1")
        assert res["ok"] is False and res["error_kind"] == "auth"

    def test_exam_schedule_hit(self, db_session_factory):
        res = query_exam_schedule(db_session_factory, term="2026-2027-1", student_no=STUDENT)
        assert res["ok"] is True
        assert len(res["exams"]) == 5
        assert res["exams"][0]["course"] == "线性代数"  # 按 exam_date 升序

    def test_library_borrow_hit(self, db_session_factory):
        res = query_library_borrow(db_session_factory, student_no=STUDENT)
        assert res["ok"] is True
        assert len(res["borrows"]) == 3
        overdue = [b for b in res["borrows"] if b["status"] == "OVERDUE"]
        assert len(overdue) == 1

    def test_library_borrow_missing_student_no_auth(self, db_session_factory):
        res = query_library_borrow(db_session_factory)
        assert res["ok"] is False and res["error_kind"] == "auth"

    def test_card_balance_hit(self, db_session_factory):
        res = query_card_balance(db_session_factory, student_no=STUDENT)
        assert res["ok"] is True and res["balance"] == 45.60

    def test_card_balance_missing_student_no_auth(self, db_session_factory):
        res = query_card_balance(db_session_factory)
        assert res["ok"] is False and res["error_kind"] == "auth"


class TestPublicTools:
    """电量/校车/校历/通知：公开数据，无学号依赖。"""

    def test_dorm_power_hit(self, db_session_factory):
        res = query_dorm_power(db_session_factory, building="3号楼", room="205")
        assert res["ok"] is True
        assert res["power_left"] == 12.6

    def test_dorm_power_unknown_room_legal(self, db_session_factory):
        res = query_dorm_power(db_session_factory, building="3号楼", room="999")
        assert res["ok"] is True and res["power_left"] is None

    def test_shuttle_hit(self, db_session_factory):
        res = query_shuttle_schedule(db_session_factory, line="屏峰-朝晖", direction="去程")
        assert res["ok"] is True
        assert len(res["departures"]) == 6
        assert res["departures"][0]["time"] == "07:30"

    def test_calendar_hit(self, db_session_factory):
        res = query_calendar(db_session_factory, term="2026-2027-1")
        assert res["ok"] is True
        assert len(res["weeks"]) == 19
        exam_week = [w for w in res["weeks"] if w["label"] == "考试周"]
        assert len(exam_week) == 1 and exam_week[0]["week"] == 19

    def test_announcements_hit(self, db_session_factory):
        res = query_announcements(db_session_factory, keyword="教务处")
        assert res["ok"] is True
        assert len(res["announcements"]) >= 2
        assert all("教务处" in a["title"] or a["source"] == "教务处" for a in res["announcements"])


class TestLostItemUGC:
    """失物招领：register 写库（count+1、reporter 正确）+ search LIKE 与倒序。"""

    def test_register_inserts_row(self, db_session_factory):
        with db_session_factory() as session:
            before = session.query(LostItem).count()
        res = register_lost_item(
            db_session_factory,
            item="黑色水笔",
            location="教302",
            date="今天",
            user_id="student-001",
        )
        assert res["ok"] is True and res["record_id"] > 0
        assert res["lost_date"] == datetime.now(UTC).date().isoformat()
        with db_session_factory() as session:
            after = session.query(LostItem).count()
            row = session.get(LostItem, res["record_id"])
        assert after == before + 1
        assert row is not None and row.reporter == "student-001" and row.status == "found"

    def test_register_iso_date(self, db_session_factory):
        res = register_lost_item(
            db_session_factory, item="钥匙", location="食堂", date="2026-08-18", user_id="s-1"
        )
        assert res["ok"] is True and res["lost_date"] == "2026-08-18"

    def test_register_bad_date_param(self, db_session_factory):
        res = register_lost_item(
            db_session_factory, item="钥匙", location="食堂", date="昨天", user_id="s-1"
        )
        assert res["ok"] is False and res["error_kind"] == "param"

    def test_search_like_and_desc(self, db_session_factory):
        res = search_lost_items(db_session_factory, keyword="书包")
        assert res["ok"] is True
        assert len(res["items"]) == 1
        assert res["items"][0]["item_name"] == "黑色书包"

    def test_search_with_location_filter(self, db_session_factory):
        res = search_lost_items(db_session_factory, keyword="校园卡", location="3号楼")
        assert res["ok"] is True and len(res["items"]) == 1

    def test_search_empty_legal(self, db_session_factory):
        res = search_lost_items(db_session_factory, keyword="不存在的物品xyz")
        assert res["ok"] is True and res["items"] == []


class TestFcFailureChain:
    """DB 异常 → error_kind=db（四层失败链第一层分类）。"""

    def test_db_failure_returns_error_kind(self):
        def boom():
            raise RuntimeError("db down")

        for fn in (
            query_timetable,
            query_exam_scores,
            query_exam_schedule,
            query_library_borrow,
            query_card_balance,
            query_dorm_power,
            register_lost_item,
            search_lost_items,
            query_shuttle_schedule,
            query_calendar,
            query_announcements,
        ):
            kwargs = {}
            if fn in (query_timetable,):
                kwargs = {"week": 6, "weekday": 3, "student_no": STUDENT}
            elif fn in (query_exam_scores, query_exam_schedule, query_calendar):
                kwargs = {"term": "2026-2027-1", "student_no": STUDENT}
            elif fn in (query_library_borrow, query_card_balance):
                kwargs = {"student_no": STUDENT}
            elif fn is query_dorm_power:
                kwargs = {"building": "3号楼", "room": "205"}
            elif fn is register_lost_item:
                kwargs = {"item": "x", "location": "y", "date": "今天", "user_id": "s-1"}
            elif fn is search_lost_items:
                kwargs = {"keyword": "书包"}
            elif fn is query_shuttle_schedule:
                kwargs = {"line": "屏峰-朝晖", "direction": "去程"}
            elif fn is query_announcements:
                kwargs = {"keyword": "教务处"}
            res = fn(boom, **kwargs)
            assert res["ok"] is False, fn.__name__
            assert res["error_kind"] == "db", fn.__name__
            assert "db down" in res["error"], fn.__name__


def test_card_balance_seed_all_students(db_session_factory):
    """种子 3 个学生都有余额（2024002=128.30 供脚本断言）。"""
    with db_session_factory() as session:
        rows = session.query(CardBalance).order_by(CardBalance.student_no).all()
    assert [(r.student_no, r.balance) for r in rows] == [
        ("2024001", 45.60),
        ("2024002", 128.30),
        ("2024003", 8.50),
    ]
