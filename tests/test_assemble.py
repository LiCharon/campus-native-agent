"""确定性组装单测（M2 拍板 Q6）：模板拼中文不调 LLM；空结果与降级文案齐全。"""

from campus_desk.query.assemble import (
    CIRCUIT_DEGRADED_REPLY,
    DEGRADED_REPLIES,
    HANDOFF_REPLY,
    assemble_rooms,
    assemble_seats,
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
