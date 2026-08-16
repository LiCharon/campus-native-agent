"""规则字段抽取（M2 兜底）：真 FC 失败时用关键词/正则从原文抽楼栋与时段。

范围（拍板 Q3）：只抽楼栋+时段；日期不抽（服务端默认今天）。兜底只为"还能查表"。
"""

import re

_BUILDING_PATTERN = re.compile(r"(\d)\s*号?楼")

_PERIOD_KEYWORDS = {
    "上午": ("上午", "早上", "早间"),
    "下午": ("下午", "午后"),
    "晚上": ("晚上", "晚间", "夜间", "晚自习"),
}


def extract_building(text: str) -> str | None:
    m = _BUILDING_PATTERN.search(text)
    return f"{m.group(1)}号楼" if m else None


def extract_period(text: str) -> str | None:
    for period, words in _PERIOD_KEYWORDS.items():
        if any(word in text for word in words):
            return period
    return None


def extract_fields(text: str) -> dict:
    return {"building": extract_building(text), "period": extract_period(text)}
