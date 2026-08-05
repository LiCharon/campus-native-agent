"""分类定级测试（M3）：规则优先 + LLM 辅助（复用 FakeStructuredLLM）+ 置信门控。

测试面：
- 规则层：类别关键词计分、P1 安全规则、兜底不炸
- LLM 层：成功解析 / 坏 JSON 重试 1 次 / LLM 全挂走规则
- 门控：低置信 → needs_human_confirm；P1 安全规则强制 P1（LLM 不推翻）
"""

from campus_desk.repair.classify import CONFIRM_THRESHOLD, RepairClassifier
from tests.conftest import FakeStructuredLLM


def _json(category: str, priority: str, confidence: float, reason: str = "") -> str:
    return f'{{"category": "{category}", "priority": "{priority}", "confidence": {confidence}, "reason": "{reason}"}}'


class TestRuleLayer:
    """规则层（LLM 不可用时的兜底 + P1 安全规则）。"""

    def test_rule_fallback_category(self):
        clf = RepairClassifier(llm=None)  # 无 LLM：纯规则
        result = clf.classify("宿舍灯管一直闪")
        assert result.category == "水电"
        assert result.priority == "P2"

    def test_rule_fallback_network(self):
        clf = RepairClassifier(llm=None)
        assert clf.classify("宿舍网口连不上网络").category == "网络"

    def test_p1_safety_rule(self):
        """漏水/断电等安全规则命中 → P1 + 需人工确认。"""
        clf = RepairClassifier(llm=None)
        for desc in ("天花板漏水了", "宿舍断电了", "闻到电线烧焦冒烟"):
            result = clf.classify(desc)
            assert result.priority == "P1", desc
            assert result.needs_human_confirm is True, desc

    def test_rule_unknown_category_low_confidence(self):
        """无法归类的描述：其他 + 低置信 + 需人工确认。"""
        clf = RepairClassifier(llm=None)
        result = clf.classify("感觉宿舍风水不好")
        assert result.category == "其他"
        assert result.confidence < CONFIRM_THRESHOLD
        assert result.needs_human_confirm is True


class TestLLMLayer:
    def test_llm_success(self):
        """LLM 正常返回：类别/级别采纳，高置信不放行人工。"""
        llm = FakeStructuredLLM([_json("水电", "P2", 0.9, "灯闪是照明故障")])
        clf = RepairClassifier(llm=llm)
        result = clf.classify("灯管闪烁")
        assert result.category == "水电"
        assert result.priority == "P2"
        assert result.needs_human_confirm is False
        assert llm.calls == 1

    def test_llm_bad_json_retries_once(self):
        """坏 JSON → 重试 1 次成功（max_attempts=2）。"""
        llm = FakeStructuredLLM(["这不是JSON", _json("门窗", "P2", 0.8)])
        clf = RepairClassifier(llm=llm)
        result = clf.classify("门锁坏了")
        assert result.category == "门窗"
        assert llm.calls == 2

    def test_llm_exception_falls_back_to_rules(self):
        """LLM 抛异常 → 规则兜底（不抛、必有结果）。"""
        llm = FakeStructuredLLM([RuntimeError("网络错误")])
        clf = RepairClassifier(llm=llm)
        result = clf.classify("门锁坏了")
        assert result.category == "门窗"  # 规则层判定
        assert result.needs_human_confirm is False  # 规则层 P2 高置信
        assert clf._last_error

    def test_llm_all_fail_uses_rule(self):
        """LLM 序列用尽（永远解析失败）→ 规则兜底。"""
        llm = FakeStructuredLLM([])  # 序列用尽 → "这不是JSON"
        clf = RepairClassifier(llm=llm)
        result = clf.classify("宿舍漏水")
        assert result.priority == "P1"  # 规则层安全规则生效
        assert result.needs_human_confirm is True


class TestGate:
    def test_low_confidence_needs_confirm(self):
        """LLM 低置信（<0.7）→ 需人工确认（门控，需求 §4 低置信转人工）。"""
        llm = FakeStructuredLLM([_json("设备", "P2", 0.4, "不确定")])
        clf = RepairClassifier(llm=llm)
        result = clf.classify("那个柜子有点问题")
        assert result.confidence < CONFIRM_THRESHOLD
        assert result.needs_human_confirm is True

    def test_p1_from_llm_needs_confirm(self):
        """LLM 判 P1 → 需人工确认（紧急单不自动放行）。"""
        llm = FakeStructuredLLM([_json("水电", "P1", 0.95)])
        clf = RepairClassifier(llm=llm)
        assert clf.classify("水管爆了").needs_human_confirm is True

    def test_rule_p1_overrides_llm_p2(self):
        """安全规则优先：LLM 判 P2 但规则命中 P1 → 强制 P1（LLM 不推翻安全定级）。"""
        llm = FakeStructuredLLM([_json("水电", "P2", 0.95)])
        clf = RepairClassifier(llm=llm)
        result = clf.classify("宿舍漏水但看着不严重")
        assert result.priority == "P1"

    def test_rule_category_fallback_for_other(self):
        """LLM 判"其他"但规则能归类 → 用规则类别（类别从宽）。"""
        llm = FakeStructuredLLM([_json("其他", "P2", 0.85)])
        clf = RepairClassifier(llm=llm)
        result = clf.classify("灯管闪烁")
        assert result.category == "水电"
