"""评测运行器测试（M1-T11 重建）：run_evaluation/EvalReport/format_report 适配 ZJUT 4 类。

注入 Fake 意图分类器（不依赖 LLM）：
- conftest.FakeIntentClassifier：固定返回预设 IntentResult（测门控/统计口径）
- PerInputClassifier（本文件）：按 student_input 返回标注意图（测全链路全对）

run_evaluation(cases, classifier) 注入方式与 runner 签名一致
（classifier 透传给 build_entry_graph）。
"""

from conftest import FakeIntentClassifier

from campus_desk.entry.intent import IntentResult
from campus_desk.entry.routes import HUMAN_HANDOFF
from campus_desk.eval.loader import load_all
from campus_desk.eval.runner import INTENT_LABELS, format_report, run_evaluation


class PerInputClassifier:
    """按 student_input 返回对应标注意图（完美分类 fake）。"""

    def __init__(self, cases):
        self.by_input = {c.student_input: c.intent for c in cases}

    def classify(self, user_input):
        intent = self.by_input[user_input]
        return IntentResult(intent=intent, confidence=0.9, secondary_intents=[], reason="fake")


def test_run_evaluation_all_correct_with_fake():
    """per-input fake → 24 条意图/路由全对，门控不误伤。"""
    cases = load_all()
    report = run_evaluation(cases=cases, classifier=PerInputClassifier(cases))
    assert report.total == 24
    assert report.intent_accuracy == 1.0
    assert report.route_correct == 24
    assert report.handoff_cases == []


def test_run_evaluation_fixed_knowledge_classifier():
    """固定返回 knowledge → 仅 knowledge 类命中，其余全错（统计口径正确）。"""
    cases = load_all()
    clf = FakeIntentClassifier(
        IntentResult(intent="knowledge", confidence=0.9, secondary_intents=[], reason="fake")
    )
    report = run_evaluation(cases=cases, classifier=clf)
    assert report.intent_correct == sum(1 for c in cases if c.intent == "knowledge")
    assert report.route_correct == sum(1 for c in cases if c.expected_route == "knowledge")
    assert report.handoff_cases == []  # 预测从不转人工


def test_run_evaluation_low_confidence_handoff():
    """低置信（0.4）→ 全部转人工；other 类命中路由、其余进低置信审计。"""
    cases = load_all()
    clf = FakeIntentClassifier(
        IntentResult(intent="knowledge", confidence=0.4, secondary_intents=[], reason="fake")
    )
    report = run_evaluation(cases=cases, classifier=clf)
    assert all(r.predicted_route == HUMAN_HANDOFF for r in report.results)
    handoff_expected = sum(1 for c in cases if c.expected_route == HUMAN_HANDOFF)
    assert len(report.handoff_cases) == report.total - handoff_expected


def test_confusion_matrix_covers_4_labels():
    cases = load_all()
    report = run_evaluation(cases=cases, classifier=PerInputClassifier(cases))
    matrix = report.confusion_matrix()
    assert set(matrix) == set(INTENT_LABELS)
    for row in matrix.values():
        assert set(row) == set(INTENT_LABELS)


def test_format_report_contains_4_class_headers():
    cases = load_all()
    report = run_evaluation(cases=cases, classifier=PerInputClassifier(cases))
    text = format_report(report)
    for label in INTENT_LABELS:
        assert label in text
    assert "意图分类准确率: **100.0%**" in text
