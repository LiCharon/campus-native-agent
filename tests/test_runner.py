"""评测运行器测试：指标计算与报告格式（fake classifier，不调 LLM）。"""

from campus_desk.entry.intent import IntentResult
from campus_desk.eval.models import ScriptedCase
from campus_desk.eval.runner import format_report, run_evaluation


class MappingClassifier:
    """按输入映射返回固定 IntentResult 的 fake（每 case 可控）。"""

    def __init__(self, mapping: dict[str, IntentResult]):
        self.mapping = mapping

    def classify(self, user_input: str) -> IntentResult:
        return self.mapping[user_input]


def make_case(case_id: str, intent: str, student_input: str, route: str) -> ScriptedCase:
    return ScriptedCase(
        id=case_id,
        category=intent if intent != "other" else "chitchat",
        student_input=student_input,
        intent=intent,
        expected_route=route,
        note="测试用例",
    )


def test_accuracy_when_all_correct():
    cases = [
        make_case("repair-001", "repair", "灯坏了", "repair"),
        make_case("consult-001", "consult", "密码忘了", "consult"),
    ]
    clf = MappingClassifier(
        {
            "灯坏了": IntentResult(intent="repair", confidence=0.9),
            "密码忘了": IntentResult(intent="consult", confidence=0.9),
        }
    )
    report = run_evaluation(cases=cases, classifier=clf)
    assert report.total == 2
    assert report.intent_accuracy == 1.0
    assert report.route_correct == 2
    assert report.handoff_cases == []


def test_accuracy_when_all_wrong_and_low_confidence():
    cases = [
        make_case("repair-001", "repair", "灯坏了", "repair"),
        make_case("consult-001", "consult", "密码忘了", "consult"),
    ]
    # 全部误判为 other + 低置信 → 转人工
    clf = MappingClassifier(
        {
            "灯坏了": IntentResult(intent="other", confidence=0.4),
            "密码忘了": IntentResult(intent="other", confidence=0.4),
        }
    )
    report = run_evaluation(cases=cases, classifier=clf)
    assert report.intent_accuracy == 0.0
    assert report.route_correct == 0
    assert len(report.handoff_cases) == 2  # 门控审计记录本应进主流程的转人工


def test_confusion_matrix_layout():
    cases = [
        make_case("repair-001", "repair", "灯坏了", "repair"),
        make_case("consult-001", "consult", "密码忘了", "consult"),
    ]
    clf = MappingClassifier(
        {
            "灯坏了": IntentResult(intent="repair", confidence=0.9),
            "密码忘了": IntentResult(intent="repair", confidence=0.9),  # 误判
        }
    )
    report = run_evaluation(cases=cases, classifier=clf)
    matrix = report.confusion_matrix()
    assert matrix["repair"]["repair"] == 1
    assert matrix["consult"]["repair"] == 1
    assert matrix["consult"]["consult"] == 0


def test_format_report_contains_key_sections():
    cases = [
        make_case("repair-001", "repair", "灯坏了", "repair"),
        make_case("consult-001", "consult", "密码忘了", "consult"),
    ]
    clf = MappingClassifier(
        {
            "灯坏了": IntentResult(intent="repair", confidence=0.9),
            "密码忘了": IntentResult(intent="consult", confidence=0.9),
        }
    )
    report = run_evaluation(cases=cases, classifier=clf)
    text = format_report(report)
    assert "意图分类准确率" in text and "100.0%" in text
    assert "混淆矩阵" in text
    assert "低置信转人工明细" in text
