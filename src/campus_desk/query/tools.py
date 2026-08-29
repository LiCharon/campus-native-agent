"""确定性查询工具（M2；FC 扩展 M2+）：TOOLS 注册表 = strict FC schema + 查询函数。

契约（设计 §4.3/§5.3）：工具 = 函数 + 数据源分离。函数只依赖 session_factory，
不感知数据来源；mock 表 → 学校 API → MCP 演进时 Agent 侧零改动。

统一签名（M2+）：所有工具函数尾参 `student_no=None, user_id=None`（由 graph._run_tool
从 deps 注入，不进 FC schema）；个人数据工具（课表/成绩/考试/借阅/余额）学号缺失时
返回 {"ok": False, "error_kind": "auth", ...}。

失败语义：查询函数不抛异常——异常捕获后返回 {"ok": False, "error_kind", "error"}
（四层失败链第一层"分类记录"依赖 error_kind；error_kind ∈ db/auth/param/unknown）。
"""

from datetime import UTC, date, datetime

_PERIODS = ("上午", "下午", "晚上")
_PERIOD_ORDER = {p: i for i, p in enumerate(_PERIODS)}


def _auth_missing() -> dict:
    return {"ok": False, "error_kind": "auth", "error": "未获取到学号，无法查询个人数据"}


def query_empty_rooms(
    session_factory,
    *,
    building: str,
    period: str,
    on: date | None = None,
    student_no: str | None = None,
    user_id: str | None = None,
) -> dict:
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


def query_library_seats(
    session_factory, *, student_no: str | None = None, user_id: str | None = None
) -> dict:
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


def query_timetable(
    session_factory,
    *,
    week: int,
    weekday: int,
    student_no: str | None = None,
    user_id: str | None = None,
) -> dict:
    """查 (学号, 第几周, 周几) 的当日课程列表（学号由系统注入）。"""
    from campus_desk.db.models import Timetable

    if student_no is None:
        return _auth_missing()
    try:
        with session_factory() as session, session.begin():
            rows = (
                session.query(Timetable)
                .filter(
                    Timetable.student_no == student_no,
                    Timetable.week == week,
                    Timetable.weekday == weekday,
                )
                .all()
            )
        rows.sort(key=lambda r: _PERIOD_ORDER.get(r.period, 99))
        return {
            "ok": True,
            "week": week,
            "weekday": weekday,
            "courses": [
                {"period": r.period, "course": r.course, "location": r.location, "teacher": r.teacher}
                for r in rows
            ],
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error_kind": "db", "error": str(exc)}


def query_exam_scores(
    session_factory,
    *,
    term: str,
    student_no: str | None = None,
    user_id: str | None = None,
) -> dict:
    """查 (学号, 学期) 的成绩列表（学号由系统注入）。"""
    from campus_desk.db.models import ExamScore

    if student_no is None:
        return _auth_missing()
    try:
        with session_factory() as session, session.begin():
            rows = (
                session.query(ExamScore)
                .filter(ExamScore.student_no == student_no, ExamScore.term == term)
                .all()
            )
        return {
            "ok": True,
            "term": term,
            "scores": [
                {"course": r.course, "score": r.score, "credit": r.credit} for r in rows
            ],
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error_kind": "db", "error": str(exc)}


def query_exam_schedule(
    session_factory,
    *,
    term: str,
    student_no: str | None = None,
    user_id: str | None = None,
) -> dict:
    """查 (学号, 学期) 的考试时间地点（学号由系统注入）。"""
    from campus_desk.db.models import ExamSchedule

    if student_no is None:
        return _auth_missing()
    try:
        with session_factory() as session, session.begin():
            rows = (
                session.query(ExamSchedule)
                .filter(ExamSchedule.student_no == student_no, ExamSchedule.term == term)
                .order_by(ExamSchedule.exam_date)
                .all()
            )
        return {
            "ok": True,
            "term": term,
            "exams": [
                {
                    "course": r.course,
                    "exam_date": r.exam_date.isoformat(),
                    "exam_time": r.exam_time,
                    "location": r.location,
                }
                for r in rows
            ],
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error_kind": "db", "error": str(exc)}


def query_library_borrow(
    session_factory, *, student_no: str | None = None, user_id: str | None = None
) -> dict:
    """查 (学号) 在借图书与应还日（学号由系统注入；无在借是合法空结果）。"""
    from campus_desk.db.models import LibraryBorrow

    if student_no is None:
        return _auth_missing()
    try:
        with session_factory() as session, session.begin():
            rows = (
                session.query(LibraryBorrow)
                .filter(LibraryBorrow.student_no == student_no)
                .order_by(LibraryBorrow.due_date)
                .all()
            )
        return {
            "ok": True,
            "borrows": [
                {
                    "book_title": r.book_title,
                    "due_date": r.due_date.isoformat(),
                    "status": r.status,
                }
                for r in rows
            ],
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error_kind": "db", "error": str(exc)}


def query_card_balance(
    session_factory, *, student_no: str | None = None, user_id: str | None = None
) -> dict:
    """查 (学号) 校园卡余额（学号由系统注入）。"""
    from campus_desk.db.models import CardBalance

    if student_no is None:
        return _auth_missing()
    try:
        with session_factory() as session, session.begin():
            row = (
                session.query(CardBalance)
                .filter(CardBalance.student_no == student_no)
                .first()
            )
        return {"ok": True, "balance": row.balance if row else None}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error_kind": "db", "error": str(exc)}


def query_dorm_power(
    session_factory,
    *,
    building: str,
    room: str,
    student_no: str | None = None,
    user_id: str | None = None,
) -> dict:
    """查 (楼栋, 房间) 的宿舍剩余电量。"""
    from campus_desk.db.models import DormPower

    try:
        with session_factory() as session, session.begin():
            row = (
                session.query(DormPower)
                .filter(DormPower.building == building, DormPower.room == room)
                .first()
            )
        return {
            "ok": True,
            "building": building,
            "room": room,
            "power_left": row.power_left if row else None,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error_kind": "db", "error": str(exc)}


def register_lost_item(
    session_factory,
    *,
    item: str,
    location: str,
    date: str,
    student_no: str | None = None,
    user_id: str | None = None,
) -> dict:
    """登记拾获物品（UGC 写库工具）：date 支持"今天"或 YYYY-MM-DD。

    登记人取 user_id（登录用户）；失物状态初始 found（待认领）。
    """
    from datetime import date as _date_cls

    from campus_desk.db.models import LostItem

    try:
        if date == "今天":
            lost_date = datetime.now(UTC).date()
        else:
            try:
                lost_date = _date_cls.fromisoformat(date)
            except ValueError:
                return {"ok": False, "error_kind": "param", "error": f"日期格式不合法: {date}"}
        with session_factory() as session, session.begin():
            row = LostItem(
                item_name=item,
                location=location,
                lost_date=lost_date,
                reporter=user_id or "",
                status="found",
            )
            session.add(row)
            session.flush()
            record_id = row.id
        return {
            "ok": True,
            "item": item,
            "location": location,
            "lost_date": lost_date.isoformat(),
            "record_id": record_id,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error_kind": "db", "error": str(exc)}


def search_lost_items(
    session_factory,
    *,
    keyword: str,
    location: str | None = None,
    student_no: str | None = None,
    user_id: str | None = None,
) -> dict:
    """按关键词（+可选地点）模糊匹配失物登记，按日期倒序。

    location 不进 FC schema（strict 三约束），仅直接调用/测试可用。
    """
    from campus_desk.db.models import LostItem

    try:
        with session_factory() as session, session.begin():
            query = session.query(LostItem).filter(LostItem.item_name.like(f"%{keyword}%"))
            if location:
                query = query.filter(LostItem.location.like(f"%{location}%"))
            rows = query.order_by(LostItem.lost_date.desc()).limit(10).all()
        return {
            "ok": True,
            "keyword": keyword,
            "items": [
                {
                    "item_name": r.item_name,
                    "location": r.location,
                    "lost_date": r.lost_date.isoformat(),
                }
                for r in rows
            ],
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error_kind": "db", "error": str(exc)}


def query_shuttle_schedule(
    session_factory,
    *,
    line: str,
    direction: str,
    student_no: str | None = None,
    user_id: str | None = None,
) -> dict:
    """查 (线路, 方向) 的校车发车时刻表。"""
    from campus_desk.db.models import ShuttleSchedule

    try:
        with session_factory() as session, session.begin():
            rows = (
                session.query(ShuttleSchedule)
                .filter(
                    ShuttleSchedule.line == line,
                    ShuttleSchedule.direction == direction,
                )
                .order_by(ShuttleSchedule.depart_time)
                .all()
            )
        return {
            "ok": True,
            "line": line,
            "direction": direction,
            "departures": [{"time": r.depart_time} for r in rows],
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error_kind": "db", "error": str(exc)}


def query_calendar(
    session_factory,
    *,
    term: str,
    student_no: str | None = None,
    user_id: str | None = None,
) -> dict:
    """查 (学期) 的校历（教学周/考试周/节假日安排）。"""
    from campus_desk.db.models import AcademicCalendar

    try:
        with session_factory() as session, session.begin():
            rows = (
                session.query(AcademicCalendar)
                .filter(AcademicCalendar.term == term)
                .order_by(AcademicCalendar.week)
                .all()
            )
        return {
            "ok": True,
            "term": term,
            "weeks": [
                {
                    "week": r.week,
                    "week_start": r.week_start.isoformat(),
                    "label": r.label,
                }
                for r in rows
            ],
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error_kind": "db", "error": str(exc)}


def query_announcements(
    session_factory,
    *,
    keyword: str,
    student_no: str | None = None,
    user_id: str | None = None,
) -> dict:
    """按关键词查近期通知公告（标题/正文 LIKE，按日期倒序取 5 条）。"""
    from campus_desk.db.models import Announcement

    try:
        with session_factory() as session, session.begin():
            rows = (
                session.query(Announcement)
                .filter(
                    Announcement.title.like(f"%{keyword}%")
                    | Announcement.content.like(f"%{keyword}%")
                    | Announcement.source.like(f"%{keyword}%")
                )
                .order_by(Announcement.publish_date.desc())
                .limit(5)
                .all()
            )
        return {
            "ok": True,
            "keyword": keyword,
            "announcements": [
                {
                    "title": r.title,
                    "publish_date": r.publish_date.isoformat(),
                    "source": r.source,
                }
                for r in rows
            ],
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error_kind": "db", "error": str(exc)}


def retrieve_knowledge(
    session_factory,
    *,
    query: str,
    domain: str,
    student_no: str | None = None,
    user_id: str | None = None,
) -> dict:
    """检索校园知识库（FAQ 式问答），用于混合意图中需解答校园办事类问题。

    返回结构化命中（id/domain/question/type/answer），供 LLM 内联引用；
    不背追问/转人工逻辑（那是 KnowledgeGraph 的职责）。
    """
    from campus_desk.knowledge.search import search_knowledge

    try:
        hits = search_knowledge(session_factory, query, domain=domain)
        return {
            "ok": True,
            "query": query,
            "results": [
                {
                    "id": h["id"],
                    "domain": h["domain"],
                    "question": h["question"],
                    "type": h["type"],
                    "answer": h["answer"],
                }
                for h in hits
            ],
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error_kind": "unknown", "error": str(exc)}


TOOL_FUNCS = {
    "query_empty_rooms": query_empty_rooms,
    "query_library_seats": query_library_seats,
    "query_timetable": query_timetable,
    "query_exam_scores": query_exam_scores,
    "query_exam_schedule": query_exam_schedule,
    "query_library_borrow": query_library_borrow,
    "query_card_balance": query_card_balance,
    "query_dorm_power": query_dorm_power,
    "register_lost_item": register_lost_item,
    "search_lost_items": search_lost_items,
    "query_shuttle_schedule": query_shuttle_schedule,
    "query_calendar": query_calendar,
    "query_announcements": query_announcements,
    "retrieve_knowledge": retrieve_knowledge,
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
    {
        "type": "function",
        "function": {
            "name": "query_timetable",
            "description": "查询学生某教学周某天的课程表",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "week": {"type": "integer", "description": "教学周次，如 6"},
                    "weekday": {
                        "type": "integer",
                        "description": "星期几，周一=1，周日=7",
                    },
                },
                "required": ["week", "weekday"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_exam_scores",
            "description": "查询学生某学期的课程成绩",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "term": {"type": "string", "description": "学期，如 2026-2027-1"},
                },
                "required": ["term"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_exam_schedule",
            "description": "查询学生某学期的考试时间地点安排",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "term": {"type": "string", "description": "学期，如 2026-2027-1"},
                },
                "required": ["term"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_library_borrow",
            "description": "查询学生在借图书与应还日期",
            "strict": True,
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_card_balance",
            "description": "查询学生校园卡余额",
            "strict": True,
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_dorm_power",
            "description": "查询指定宿舍房间的剩余电量",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "building": {"type": "string", "description": "宿舍楼栋，如 3号楼"},
                    "room": {"type": "string", "description": "房间号，如 205"},
                },
                "required": ["building", "room"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "register_lost_item",
            "description": "登记一条拾获物品信息（失物招领，写入系统供失主搜索匹配）",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "item": {"type": "string", "description": "拾获物品名称，如 校园卡"},
                    "location": {"type": "string", "description": "拾获地点，如 3号楼201教室"},
                    "date": {
                        "type": "string",
                        "description": "拾获日期，'今天'或 YYYY-MM-DD",
                    },
                },
                "required": ["item", "location", "date"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_lost_items",
            "description": "按关键词搜索失物招领登记（找丢失/拾获的物品）",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "物品关键词，如 书包"},
                },
                "required": ["keyword"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_shuttle_schedule",
            "description": "查询校车线路某方向的发车时刻表",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "line": {"type": "string", "description": "线路，如 屏峰-朝晖"},
                    "direction": {
                        "type": "string",
                        "enum": ["去程", "返程"],
                        "description": "方向",
                    },
                },
                "required": ["line", "direction"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_calendar",
            "description": "查询某学期校历（教学周/考试周/节假日安排）",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "term": {"type": "string", "description": "学期，如 2026-2027-1"},
                },
                "required": ["term"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_announcements",
            "description": "按关键词查询近期通知公告",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "关键词，如 教务处"},
                },
                "required": ["keyword"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retrieve_knowledge",
            "description": "检索校园知识库，解答校园办事类常识与流程（非纯数据查询时使用）。",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "关于校园办事的自然语言问题，如 校园卡丢了怎么办",
                    },
                    "domain": {
                        "type": "string",
                        "enum": [
                            "教务",
                            "图书馆",
                            "网络与IT",
                            "校园卡与证件",
                            "住宿后勤",
                            "奖助",
                            "医疗健康",
                            "社团与活动",
                            "就业与职业发展",
                            "安全与保卫",
                            "生活服务",
                        ],
                        "description": "限定检索领域，缩小范围；跨领域问题可分两次调用",
                    },
                },
                "required": ["query", "domain"],
                "additionalProperties": False,
            },
        },
    },
]
