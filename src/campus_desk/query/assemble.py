"""确定性回答组装（M2 拍板 Q6）：不调 LLM 润色——可单测、评测关键词不抖动。

失败链降级文案也在此（设计 §5.3 ②索引引导降级）：实时数据不可用时告诉学生
去哪看，契合"查不了就告诉去哪查"的定位。③熔断期用通用降级文案（此时可能
无法确定学生要查哪个工具）。
"""

EMPTY_ROOMS_DEGRADED = "空教室实时数据暂时查不到，可到教学楼一层电子屏或值班室查看。"
LIBRARY_SEATS_DEGRADED = "图书馆座位实时数据暂时查不到，可到图书馆入口大屏查看。"
CIRCUIT_DEGRADED_REPLY = (
    "实时数据暂时查询不到，可到教学楼一层电子屏或图书馆入口大屏查看，稍后再试。"
)
HANDOFF_REPLY = "查询服务暂时不可用，已为您记录问题，工作人员会尽快回复。"

DEGRADED_REPLIES = {
    "query_empty_rooms": EMPTY_ROOMS_DEGRADED,
    "query_library_seats": LIBRARY_SEATS_DEGRADED,
    "query_timetable": "课表数据暂时查不到，可到教务系统查看。",
    "query_exam_scores": "成绩数据暂时查不到，可到教务系统查看。",
    "query_exam_schedule": "考试安排暂时查不到，可到教务系统查看。",
    "query_library_borrow": "借阅数据暂时查不到，可到图书馆服务台查看。",
    "query_card_balance": "校园卡余额暂时查不到，可到服务大厅自助机查看。",
    "query_dorm_power": "宿舍电量暂时查不到，可到宿管值班室或后勤服务号查看。",
    "register_lost_item": "失物登记暂不可用，请稍后再试或到学生服务中心登记。",
    "search_lost_items": "失物查询暂不可用，请稍后再试或到学生服务中心咨询。",
    "query_shuttle_schedule": "校车时刻暂时查不到，可到后勤服务号查看。",
    "query_calendar": "校历数据暂时查不到，可到学校官网查看。",
    "query_announcements": "通知公告暂时查不到，可到学校官网公告栏查看。",
}

_BORROW_STATUS_CN = {"BORROWED": "在借", "OVERDUE": "已超期"}
_WEEKDAY_CN = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "日"}


def assemble_rooms(result: dict) -> str:
    building, period = result["building"], result["period"]
    if not result["rooms"]:
        return f"{building}{period}暂无空闲教室，可以试试其他时段或其他楼栋。"
    return f"{building}今天{period}空闲教室：{'、'.join(result['rooms'])}。"


def assemble_seats(result: dict) -> str:
    floors = result["floors"]
    if not floors:
        return "当前图书馆暂无座位数据。"
    free = sum(f["free"] for f in floors)
    detail = "，".join(f"{f['floor']} {f['free']} 个" for f in floors)
    return f"图书馆当前空余座位约 {free} 个（{detail}）。"


def assemble_timetable(result: dict) -> str:
    courses = result["courses"]
    weekday_cn = _WEEKDAY_CN.get(result["weekday"], str(result["weekday"]))
    if not courses:
        return f"第 {result['week']} 周周{weekday_cn}暂无课程安排。"
    items = "；".join(
        f"{c['period']} {c['course']}（{c['location']}，{c['teacher']}）" for c in courses
    )
    return f"第 {result['week']} 周周{weekday_cn}课程：{items}。"


def assemble_scores(result: dict) -> str:
    scores = result["scores"]
    if not scores:
        return f"未查询到 {result['term']} 的成绩记录。"
    items = "；".join(f"{s['course']} {s['score']} 分（{s['credit']} 学分）" for s in scores)
    return f"您在 {result['term']} 的成绩：{items}。"


def assemble_exams(result: dict) -> str:
    exams = result["exams"]
    if not exams:
        return f"未查询到 {result['term']} 的考试安排。"
    items = "；".join(
        f"{e['course']} {e['exam_date']} {e['exam_time']}（{e['location']}）" for e in exams
    )
    return f"{result['term']} 考试安排：{items}。"


def assemble_borrow(result: dict) -> str:
    borrows = result["borrows"]
    if not borrows:
        return "当前没有在借图书。"
    items = "；".join(
        f"{b['book_title']}，应还 {b['due_date']}（{_BORROW_STATUS_CN.get(b['status'], b['status'])}）"
        for b in borrows
    )
    return f"您在借图书：{items}。"


def assemble_card_balance(result: dict) -> str:
    balance = result["balance"]
    if balance is None:
        return "未查询到校园卡余额信息。"
    return f"您校园卡当前余额 {balance} 元。"


def assemble_dorm_power(result: dict) -> str:
    power = result["power_left"]
    if power is None:
        return f"未查询到 {result['building']} {result['room']} 的电量信息。"
    return f"{result['building']} {result['room']} 剩余电量 {power} 度。"


def assemble_lost_register(result: dict) -> str:
    return (
        f"已为您登记拾获物品：{result['item']}（{result['location']}，{result['lost_date']}），"
        f"登记号 {result['record_id']}。失主搜索物品名称即可匹配。"
    )


def assemble_lost_search(result: dict) -> str:
    items = result["items"]
    if not items:
        return "暂未找到匹配的失物登记，可稍后再来查看或到学生服务中心咨询。"
    parts = "；".join(f"{i['item_name']}（{i['location']}，{i['lost_date']}）" for i in items)
    return f"找到 {len(items)} 条可能匹配：{parts}。"


def assemble_shuttle(result: dict) -> str:
    departures = result["departures"]
    if not departures:
        return f"未查询到 {result['line']} {result['direction']} 的班车时刻。"
    times = "、".join(d["time"] for d in departures)
    return f"{result['line']} {result['direction']}发车时间：{times}。"


def assemble_calendar(result: dict) -> str:
    weeks = result["weeks"]
    if not weeks:
        return f"未查询到 {result['term']} 的校历信息。"
    items = "；".join(
        f"第 {w['week']} 周（{w['week_start']} 起，{w['label']}）" for w in weeks
    )
    return f"{result['term']} 校历：{items}。"


def assemble_announcements(result: dict) -> str:
    announcements = result["announcements"]
    if not announcements:
        return "未查询到相关通知公告。"
    items = "；".join(
        f"{a['title']}（{a['source']}，{a['publish_date']}）" for a in announcements
    )
    return f"近期通知公告：{items}。"
