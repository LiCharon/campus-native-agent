"""规则字段抽取单测（M2 拍板 Q3）：只抽楼栋+时段，日期默认今天。"""

from campus_desk.query.field_extract import extract_building, extract_fields, extract_period


def test_extract_building_digit():
    assert extract_building("3号楼下午有空教室吗") == "3号楼"
    assert extract_building("去2号楼自习") == "2号楼"


def test_extract_building_none():
    assert extract_building("有空教室吗") is None


def test_extract_period_keywords():
    assert extract_period("下午有空教室吗") == "下午"
    assert extract_period("晚上想去自习") == "晚上"
    assert extract_period("早上好") == "上午"


def test_extract_period_none():
    assert extract_period("有空教室吗") is None


def test_extract_fields_combined():
    assert extract_fields("3号楼晚上有空教室吗") == {"building": "3号楼", "period": "晚上"}
    assert extract_fields("今天天气不错") == {"building": None, "period": None}
