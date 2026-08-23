"""确定性查询工具单测（M2）：不依赖 LLM（沿用 CampusDesk 铁律）。

覆盖：命中/空结果（未知楼栋）/显式日期注入（周中模式锁定）/DB 异常返回 ok=False
（四层失败链第一层"分类记录"依赖 error_kind）。
"""

from datetime import UTC, date, datetime

from campus_desk.query.tools import (
    TOOL_FUNCS,
    TOOL_SCHEMAS,
    query_empty_rooms,
    query_library_seats,
)


def test_empty_rooms_hit_uses_today_weekday(db_session_factory):
    res = query_empty_rooms(db_session_factory, building="3号楼", period="下午")
    assert res["ok"] is True
    assert res["weekday"] == datetime.now(UTC).date().weekday() + 1
    assert len(res["rooms"]) == 3
    assert all(r.startswith("3") for r in res["rooms"])


def test_empty_rooms_explicit_date_locks_weekday_pattern(db_session_factory):
    monday = date(2026, 8, 17)  # 周一 → 周中模式
    res = query_empty_rooms(db_session_factory, building="1号楼", period="上午", on=monday)
    assert res["rooms"] == ["101", "105", "108"]


def test_empty_rooms_unknown_building_empty_result(db_session_factory):
    res = query_empty_rooms(db_session_factory, building="9号楼", period="下午")
    assert res["ok"] is True and res["rooms"] == []  # 空结果是合法答案，不是失败


def test_library_seats_returns_five_floors(db_session_factory):
    res = query_library_seats(db_session_factory)
    assert res["ok"] is True
    assert len(res["floors"]) == 5
    assert res["floors"][0]["floor"] == "1F"


def test_db_failure_returns_ok_false_with_error_kind():
    def boom():
        raise RuntimeError("db down")

    res = query_library_seats(boom)
    assert res["ok"] is False
    assert res["error_kind"] == "db"
    assert "db down" in res["error"]


def test_tool_schemas_strict_and_required():
    assert set(TOOL_FUNCS) == {
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
        "retrieve_knowledge",
    }
    assert len(TOOL_SCHEMAS) == len(TOOL_FUNCS) == 14
    schema_names = {s["function"]["name"] for s in TOOL_SCHEMAS}
    assert schema_names == set(TOOL_FUNCS)
    for schema in TOOL_SCHEMAS:
        fn = schema["function"]
        assert fn["strict"] is True
        assert fn["parameters"]["additionalProperties"] is False
        assert set(fn["parameters"]["properties"]) == set(fn["parameters"].get("required", []))
