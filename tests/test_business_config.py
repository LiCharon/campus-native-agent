"""M6 业务参数可配化测试：parity 快照（配置值 == 旧代码硬编码值，行为不变底线）+ 坏配置快速失败。

改了 config/business_rules.json 就必须连本文件的快照一起改——否则语义漂移立刻红。
"""

import json
from pathlib import Path
from typing import get_args

import pytest

from campus_desk import business_config
from campus_desk.repair import classify, graph

# —— parity 快照：M3 硬编码的原始值（2026-08-05 迁移时逐字搬入 JSON）——

_SNAPSHOT_CATEGORIES = ("水电", "网络", "门窗", "设备", "环境", "其他")

_SNAPSHOT_KEYWORDS = {
    "水电": ("漏水", "停水", "水龙头", "热水器", "水表", "电闸", "插座", "灯", "开关", "断电", "停电", "管道", "短路"),
    "网络": ("网络", "网线", "wifi", "无线", "宽带", "信号", "连不上", "网速"),
    "门窗": ("门", "窗", "锁", "把手", "玻璃", "合页"),
    "设备": ("空调", "风扇", "洗衣机", "马桶", "床", "桌", "椅", "衣柜", "投影仪", "饮水机"),
    "环境": ("卫生", "蟑螂", "老鼠", "异味", "堵塞", "垃圾"),
    "其他": (),
}

_SNAPSHOT_P1 = ("漏水", "漏电", "断电", "停电", "火", "冒烟", "爆裂", "渗水", "水淹", "电火花")

_SNAPSHOT_DEPT_TRADE = {
    "网络": ("信息中心", "网络"),
    "水电": ("后勤", "水电"),
    "门窗": ("后勤", "门窗"),
    "设备": ("后勤", "家具"),
    "环境": ("后勤", "家具"),
    "其他": (None, None),
}

_SNAPSHOT_CONFIRM = ("对", "好", "可以", "没问题", "确认", "是的", "是", "行")
_SNAPSHOT_DENY = ("不对", "不是", "错了", "不行", "不用", "别", "错")


class TestParity:
    """配置值与旧代码快照逐项相等（改 JSON 必须连测试一起改）。"""

    def test_categories_match_code_literal(self):
        assert business_config.CATEGORIES == _SNAPSHOT_CATEGORIES
        # 与 classify.Category Literal 一致（防代码/配置两套类别漂移）
        assert set(business_config.CATEGORIES) == set(get_args(classify.Category))

    def test_category_keywords_parity(self):
        assert business_config.CATEGORY_KEYWORDS == _SNAPSHOT_KEYWORDS

    def test_p1_keywords_parity(self):
        assert business_config.P1_KEYWORDS == _SNAPSHOT_P1

    def test_dept_trade_parity(self):
        assert business_config.CATEGORY_DEPT_TRADE == _SNAPSHOT_DEPT_TRADE

    def test_confirm_deny_words_parity(self):
        assert business_config.CONFIRM_WORDS == _SNAPSHOT_CONFIRM
        assert business_config.DENY_WORDS == _SNAPSHOT_DENY

    def test_scalar_parity(self):
        assert business_config.CONFIRM_THRESHOLD == 0.7
        assert business_config.FALLBACK_DEPT == "后勤"

    def test_category_definitions_parity(self):
        # 类别定义文案（LLM prompt 渲染用）与 M3 prompt 原文案一致
        assert business_config.CATEGORY_DEFINITIONS == {
            "水电": "水/电/照明/管道/插座/热水器等设施故障",
            "网络": "网络/宽带/wifi/网线/信号故障",
            "门窗": "门/窗/锁/玻璃故障",
            "设备": "空调/风扇/洗衣机/家具/电器等设备故障",
            "环境": "卫生/虫害/异味/堵塞等环境问题",
            "其他": "无法归入上述类别",
        }

    def test_classify_reexports_config(self):
        # 模块级重导出：调用面零改动（_rule_fallback 遍历的就是配置）
        assert classify.CATEGORY_KEYWORDS is business_config.CATEGORY_KEYWORDS
        assert classify.CONFIRM_THRESHOLD == 0.7
        assert classify.P1_KEYWORDS is business_config.P1_KEYWORDS

    def test_graph_reexports_config(self):
        assert graph.CATEGORY_DEPT_TRADE is business_config.CATEGORY_DEPT_TRADE
        assert graph.CONFIRM_WORDS is business_config.CONFIRM_WORDS
        assert graph.DENY_WORDS is business_config.DENY_WORDS

    def test_prompt_renders_all_categories(self):
        # 类别进 prompt 与配置双源一致（加类目 prompt 自动同步）
        prompt = classify._CLASSIFY_PROMPT
        for name in _SNAPSHOT_CATEGORIES:
            assert f"- {name}: " in prompt
        assert "|".join(_SNAPSHOT_CATEGORIES) in prompt


class TestLoadRulesFailure:
    """坏配置快速失败（防静默退化，规则兜底不能没有数据源）。"""

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(RuntimeError, match="业务配置缺失"):
            business_config.load_rules(tmp_path / "nope.json")

    def test_bad_json_raises(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        with pytest.raises(RuntimeError, match="业务配置非法"):
            business_config.load_rules(p)

    def test_missing_field_raises(self, tmp_path):
        p = tmp_path / "missing.json"
        p.write_text(json.dumps({"categories": {}}), encoding="utf-8")
        with pytest.raises(RuntimeError):
            business_config.load_rules(p)

    def test_illegal_category_key_raises(self, tmp_path):
        p = tmp_path / "illegal.json"
        # 从真实配置出发，只改一个非法键
        rules = json.loads(Path("config/business_rules.json").read_text(encoding="utf-8"))
        rules["category_keywords"]["空调"] = ["空调"]
        p.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(RuntimeError, match="非法类别键"):
            business_config.load_rules(p)
