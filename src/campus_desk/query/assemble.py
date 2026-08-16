"""确定性回答组装（M2 拍板 Q6）：不调 LLM 润色——可单测、评测关键词不抖动。

失败链降级文案也在此（设计 §5.3 ②索引引导降级）：实时数据不可用时告诉学生
去哪看，契合"查不了就告诉去哪查"的定位。③熔断期用通用降级文案（此时可能
无法确定学生要查哪个工具）。
"""

EMPTY_ROOMS_DEGRADED = "空教室实时数据暂时查不到，可到教学楼一层电子屏或值班室查看。"
LIBRARY_SEATS_DEGRADED = "图书馆座位实时数据暂时查不到，可到图书馆入口大屏查看。"
CIRCUIT_DEGRADED_REPLY = "实时数据暂时查询不到，可到教学楼一层电子屏或图书馆入口大屏查看，稍后再试。"
HANDOFF_REPLY = "查询服务暂时不可用，已为您记录问题，工作人员会尽快回复。"

DEGRADED_REPLIES = {
    "query_empty_rooms": EMPTY_ROOMS_DEGRADED,
    "query_library_seats": LIBRARY_SEATS_DEGRADED,
}


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
