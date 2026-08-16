"""确定性查询工具（M2）：TOOLS 注册表 = strict FC schema + 查询函数。

契约（设计 §4.3/§5.3）：工具 = 函数 + 数据源分离。函数只依赖 session_factory，
不感知数据来源；mock 表 → 学校 API → MCP 演进时 Agent 侧零改动。

失败语义：查询函数不抛异常——异常捕获后返回 {"ok": False, "error_kind", "error"}
（四层失败链第一层"分类记录"依赖 error_kind）。
"""

from datetime import UTC, date, datetime

_PERIODS = ("上午", "下午", "晚上")


def query_empty_rooms(session_factory, *, building: str, period: str, on: date | None = None) -> dict:
    """查 (楼栋, 周几, 时段) 的空闲教室列表。on 默认今天（date 不进 FC schema）。"""
    from campus_desk.db.models import EmptyRoom

    try:
        weekday = (on or datetime.now(UTC).date()).weekday() + 1  # ISO Monday=0 → 1-7
        with session_factory() as session, session.begin():
            rows = (
                session.query(EmptyRoom)
                .filter(
                    EmptyRoom.building == building,
                    EmptyRoom.weekday == weekday,
                    EmptyRoom.period == period,
                )
                .all()
            )
        return {
            "ok": True,
            "building": building,
            "period": period,
            "weekday": weekday,
            "rooms": sorted(r.room for r in rows),
        }
    except Exception as exc:  # noqa: BLE001 — 工具失败不抛，交给失败链分类
        return {"ok": False, "error_kind": "db", "error": str(exc)}


def query_library_seats(session_factory) -> dict:
    """查图书馆各层空余座位（无必填参数工具，已实测可调）。"""
    from campus_desk.db.models import LibrarySeat

    try:
        with session_factory() as session, session.begin():
            rows = session.query(LibrarySeat).order_by(LibrarySeat.floor).all()
        return {
            "ok": True,
            "floors": [
                {"floor": r.floor, "free": r.free_seats, "total": r.total_seats} for r in rows
            ],
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error_kind": "db", "error": str(exc)}


TOOL_FUNCS = {
    "query_empty_rooms": query_empty_rooms,
    "query_library_seats": query_library_seats,
}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "query_empty_rooms",
            "description": "查询指定楼栋某个时段的空闲教室列表",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "building": {"type": "string", "description": "教学楼栋，如 3号楼"},
                    "period": {"type": "string", "enum": list(_PERIODS), "description": "时段"},
                },
                "required": ["building", "period"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_library_seats",
            "description": "查询图书馆当前各楼层空余座位数",
            "strict": True,
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
]
