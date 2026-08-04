"""评测运行器：跑 EntryGraph → 对比标注 → 指标报告（M2 范围）。

指标（对应需求 §10）：
- 意图分类准确率：识别意图 == 人工标注 / 总数
- 混淆矩阵：4×4（标注 × 预测），定位易混类别
- 路由准确率：最终分流 == 期望路由（含门控逻辑）
- 低置信转人工明细：本应进主流程却被门控转人工的用例（门控保守度审计）

设计（需求 §10）：评测脚本独立于业务代码；无 DEEPSEEK_API_KEY 时跳过
（与 verify_env 同模式，需外部环境的项不进 CI）。

用法：python -m campus_desk.eval.runner [--max N]（跑真 LLM，出基线报告）
"""

import argparse
import time
from dataclasses import dataclass, field

from campus_desk.config import settings
from campus_desk.entry.entry_graph import build_entry_graph
from campus_desk.entry.routes import HUMAN_HANDOFF
from campus_desk.eval.loader import load_all
from campus_desk.eval.models import IntentLabel, ScriptedCase

INTENT_LABELS: list[IntentLabel] = ["repair", "consult", "complaint", "other"]


@dataclass
class CaseResult:
    case: ScriptedCase
    predicted_intent: IntentLabel
    predicted_route: str
    confidence: float
    seconds: float


@dataclass
class EvalReport:
    results: list[CaseResult] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def intent_correct(self) -> int:
        return sum(1 for r in self.results if r.predicted_intent == r.case.intent)

    @property
    def intent_accuracy(self) -> float:
        return self.intent_correct / self.total if self.total else 0.0

    @property
    def route_correct(self) -> int:
        return sum(1 for r in self.results if r.predicted_route == r.case.expected_route)

    @property
    def handoff_cases(self) -> list[CaseResult]:
        """本应进主流程但被门控转人工的用例（低置信审计）。"""
        return [
            r
            for r in self.results
            if r.predicted_route == HUMAN_HANDOFF and r.case.expected_route != HUMAN_HANDOFF
        ]

    def confusion_matrix(self) -> dict[str, dict[str, int]]:
        matrix: dict[str, dict[str, int]] = {
            label: {l2: 0 for l2 in INTENT_LABELS} for label in INTENT_LABELS
        }
        for r in self.results:
            matrix[r.case.intent][r.predicted_intent] += 1
        return matrix


def run_evaluation(
    cases: list[ScriptedCase] | None = None, classifier=None, max_cases: int | None = None
):
    """跑一遍评测。classifier 可注入（测试用 fake），默认真 LLM。"""
    cases = cases or load_all()
    if max_cases:
        cases = cases[:max_cases]
    graph = build_entry_graph(classifier=classifier)

    results: list[CaseResult] = []
    start = time.monotonic()
    for case in cases:
        t0 = time.monotonic()
        out = graph.invoke({"user_input": case.student_input})
        results.append(
            CaseResult(
                case=case,
                predicted_intent=out["intent"].intent,
                predicted_route=out["route"],
                confidence=out["intent"].confidence,
                seconds=time.monotonic() - t0,
            )
        )
    report = EvalReport(results=results, duration_seconds=time.monotonic() - start)
    return report


def format_report(report: EvalReport) -> str:
    """Markdown 格式评测报告（可存档可面试展示）。"""
    lines = [
        "# M2 入口分流评测报告",
        "",
        f"- 用例数: {report.total}",
        f"- 意图分类准确率: **{report.intent_accuracy:.1%}**（{report.intent_correct}/{report.total}）",
        f"- 路由准确率: **{report.route_correct / report.total:.1%}**（{report.route_correct}/{report.total}）"
        if report.total
        else "- 路由准确率: -",
        f"- 总耗时: {report.duration_seconds:.1f}s",
        "",
        "## 混淆矩阵（标注 \\ 预测）",
        "",
        "| 标注 \\ 预测 | repair | consult | complaint | other |",
        "|---|---|---|---|---|",
    ]
    matrix = report.confusion_matrix()
    for label in INTENT_LABELS:
        row = matrix[label]
        values = " | ".join(str(row[l]) for l in INTENT_LABELS)
        lines.append(f"| {label} | {values} |")

    if report.handoff_cases:
        lines += ["", "## 低置信转人工明细（门控审计）", ""]
        for r in report.handoff_cases:
            lines.append(
                f"- {r.case.id}（标注 {r.case.intent}，置信度 {r.confidence:.2f}）: {r.case.student_input}"
            )
    else:
        lines += ["", "## 低置信转人工明细", "", "无（门控未误伤）"]

    misclassified = [r for r in report.results if r.predicted_intent != r.case.intent]
    lines += ["", "## 错误用例明细", ""] if misclassified else ["", "## 错误用例明细", "", "无"]
    for r in misclassified:
        lines.append(
            f"- {r.case.id}: 标注 {r.case.intent}，预测 {r.predicted_intent}（置信度 "
            f"{r.confidence:.2f}）｜{r.case.student_input}"
        )

    lines += ["", "## 慢用例（Top 3）", ""]
    for r in sorted(report.results, key=lambda x: x.seconds, reverse=True)[:3]:
        lines.append(f"- {r.case.id}: {r.seconds:.1f}s")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="CampusDesk M2 入口分流评测")
    parser.add_argument("--max", type=int, default=None, help="只跑前 N 条（调试用）")
    parser.add_argument("--out", type=str, default=None, help="报告写入路径（默认打印）")
    args = parser.parse_args()

    if not settings.deepseek_api_key:
        print("SKIP: 未配置 DEEPSEEK_API_KEY（.env 填写后重跑）——需外部环境的项不进 CI")
        return

    report = run_evaluation(max_cases=args.max)
    text = format_report(report)
    if args.out:
        from pathlib import Path

        Path(args.out).write_text(text, encoding="utf-8")
        print(f"报告已写入: {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
