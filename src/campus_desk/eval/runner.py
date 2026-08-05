"""评测运行器：入口分流（M2）+ 报修链路（M3）+ 咨询（M4）+ 投诉（M5）→ 对比标注 → 指标报告。

M2 指标（需求 §10）：
- 意图分类准确率 / 混淆矩阵 / 路由准确率 / 低置信转人工明细
M3 指标（报修链路，仅 repair/repeat_repair 剧本）：
- 链路成功率：turns 断言全过 / 报修用例数
- 平均对话轮次
- 剧本失配：上轮未暂停就回复（scripted 答非所问失真，Qwen 二轮审查拍板判失败）
M5 指标（投诉链路，仅 complaint 剧本）：
- 链路成功率 / 平均对话轮次（确定性追问管道，失配判失败同报修口径）

设计（需求 §10）：评测脚本独立于业务代码；无 DEEPSEEK_API_KEY /
未配 DATABASE_URL 时相应段跳过（需外部环境的项不进 CI）。

用法：python -m campus_desk.eval.runner [--max N]（跑真 LLM，出基线报告）
"""

import argparse
import time
from dataclasses import dataclass, field

from campus_desk import telemetry
from campus_desk.config import settings
from campus_desk.entry.entry_graph import build_entry_graph
from campus_desk.entry.orchestrator import turn
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


def check_expect(prev_tools: set[str], state: dict, expect: list[str]) -> list[str]:
    """断言检查：本轮新增行为（tool 取差集，status 取本轮终态，outcome 取本轮行为）。

    返回失败列表（空 = 通过）。断言仅支持 tool:xxx / status:xxx / outcome:xxx
    （loader 已校验）。
    """
    failures: list[str] = []
    new_tools = set(state.get("tool_calls", [])) - prev_tools
    for assertion in expect:
        if assertion.startswith("tool:"):
            tool = assertion[5:]
            if tool not in new_tools:
                failures.append(f"{assertion} 本轮未调用")
        elif assertion.startswith("status:"):
            status = assertion[7:]
            if state.get("ticket_status") != status:
                failures.append(f"{assertion} 期望 {status}，实际 {state.get('ticket_status')}")
        elif assertion.startswith("outcome:"):
            outcome = assertion[8:]
            if state.get("outcome") != outcome:
                failures.append(f"{assertion} 期望 {outcome}，实际 {state.get('outcome')}")
    return failures


@dataclass
class RepairCaseResult:
    case: ScriptedCase
    turn_count: int
    failures: list[str]  # (第几轮: 断言失败/剧本失配) 明细；空 = 通过
    seconds: float


@dataclass
class RepairEvalReport:
    results: list[RepairCaseResult] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed_cases(self) -> int:
        return sum(1 for r in self.results if not r.failures)

    @property
    def success_rate(self) -> float:
        return self.passed_cases / len(self.results) if self.results else 0.0

    @property
    def avg_turns(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.turn_count for r in self.results) / len(self.results)

    def failure_details(self) -> list[str]:
        details = []
        for r in self.results:
            for failure in r.failures:
                details.append(f"{r.case.id}: {failure}")
        return details


def run_repair_evaluation(
    cases: list[ScriptedCase],
    session_factory,
    entry_graph=None,
    repair_graph=None,
    consult_graph=None,
    quality_graph=None,
    max_cases: int | None = None,
) -> RepairEvalReport:
    """报修链路评测：仅 repair/repeat_repair 剧本，多轮驱动。

    - 首轮走 orchestrator.turn（Entry → RepairGraph 新会话，thread_id=case.id）
    - 每轮 turns：先断言上轮 paused（get_state().next 非空）——scripted
      答非所问的失真防线（Qwen 二轮审查拍板）；再 resume 并检查 expect
    - entry_graph/repair_graph/consult_graph/quality_graph 可注入（测试用
      fake/规则版；默认真 LLM + InMemorySaver）
    """
    from langgraph.checkpoint.memory import InMemorySaver

    from campus_desk.consult.graph import build_consult_graph
    from campus_desk.db.session import default_session_factory
    from campus_desk.quality.graph import build_quality_graph
    from campus_desk.repair.graph import build_repair_graph

    cases = [c for c in cases if c.category in ("repair", "repeat_repair")]
    if max_cases:
        cases = cases[:max_cases]
    if not cases:
        return RepairEvalReport()

    entry_graph = entry_graph or build_entry_graph()
    # 评测用 InMemorySaver：文件级 SqliteSaver（生产 checkpointer.db）会残留
    # 上次评测的终态 thread——重跑时 get_state 命中旧终态 → 全剧本失配（实测抓出）
    repair_graph = repair_graph or build_repair_graph(
        session_factory or default_session_factory(), checkpointer=InMemorySaver()
    )
    consult_graph = consult_graph or build_consult_graph(
        session_factory or default_session_factory(), checkpointer=InMemorySaver()
    )
    quality_graph = quality_graph or build_quality_graph(
        session_factory or default_session_factory(), checkpointer=InMemorySaver()
    )

    results: list[RepairCaseResult] = []
    start = time.monotonic()
    for case in cases:
        t0 = time.monotonic()
        cfg = {"configurable": {"thread_id": f"eval-{case.id}"}}
        failures: list[str] = []
        turn_count = 0
        prev_tools: set[str] = set()

        # 首轮：进 RepairGraph（可能是追问轮）
        first = turn(
            entry_graph,
            repair_graph,
            consult_graph,
            f"eval-{case.id}",
            case.student_input,
            quality_graph=quality_graph,
        )
        prev_tools = set(first.get("tool_calls", []))

        for idx, scripted in enumerate(case.turns, start=1):
            turn_count = idx
            if repair_graph.get_state(cfg).next == ():
                failures.append(f"第{idx}轮: 剧本失配（上轮未等待学生回复，脚本答非所问）")
                break
            out = turn(
                entry_graph,
                repair_graph,
                consult_graph,
                f"eval-{case.id}",
                scripted.student_reply,
                quality_graph=quality_graph,
            )
            failures.extend(
                f"第{idx}轮: {fail}" for fail in check_expect(prev_tools, out, scripted.expect)
            )
            prev_tools = set(out.get("tool_calls", []))

        results.append(
            RepairCaseResult(
                case=case,
                turn_count=turn_count,
                failures=failures,
                seconds=time.monotonic() - t0,
            )
        )
    return RepairEvalReport(results=results, duration_seconds=time.monotonic() - start)


@dataclass
class ConsultCaseResult:
    case: ScriptedCase
    turn_count: int
    outcome: str | None  # answer/handoff/ask（剧本终态行为）
    failures: list[str]  # (第几轮: 断言失败/剧本失配) 明细；空 = 通过
    seconds: float


@dataclass
class ConsultEvalReport:
    results: list[ConsultCaseResult] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def self_served(self) -> int:
        """自助解决：终态 outcome=answer（需求 §10：未转人工且已解决）。"""
        return sum(1 for r in self.results if r.outcome == "answer")

    @property
    def self_service_rate(self) -> float:
        return self.self_served / self.total if self.total else 0.0

    @property
    def handoff_count(self) -> int:
        return sum(1 for r in self.results if r.outcome == "handoff")

    @property
    def handoff_rate(self) -> float:
        return self.handoff_count / self.total if self.total else 0.0

    @property
    def avg_turns(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.turn_count for r in self.results) / len(self.results)

    @property
    def passed_cases(self) -> int:
        return sum(1 for r in self.results if not r.failures)

    @property
    def success_rate(self) -> float:
        return self.passed_cases / self.total if self.total else 0.0

    def failure_details(self) -> list[str]:
        details = []
        for r in self.results:
            for failure in r.failures:
                details.append(f"{r.case.id}: {failure}")
        return details


def run_consult_evaluation(
    cases: list[ScriptedCase],
    session_factory,
    entry_graph=None,
    repair_graph=None,
    consult_graph=None,
    quality_graph=None,
    student_no: str | None = "2024001",
    max_cases: int | None = None,
) -> ConsultEvalReport:
    """咨询链路评测：仅 consult 剧本，多轮驱动（同 repair runner 模式）。

    - 首轮走 orchestrator.turn（Entry → ConsultGraph 新会话，thread_id=case.id）
    - 每轮 turns：先断言上轮 paused（consult_graph.get_state().next 非空）；
      再 resume 并检查 expect（tool:/outcome: 行为断言）
    - 指标（需求 §10）：自助解决率（answer 终态占比）/ 人工介入率（handoff）/
      平均对话轮次（剧本 turns 数 + 首轮）
    - 图可注入（测试用 fake 决策；默认真 LLM + InMemorySaver 隔离）
    """
    from langgraph.checkpoint.memory import InMemorySaver

    from campus_desk.consult.graph import build_consult_graph
    from campus_desk.db.session import default_session_factory
    from campus_desk.quality.graph import build_quality_graph
    from campus_desk.repair.graph import build_repair_graph

    cases = [c for c in cases if c.category == "consult"]
    if max_cases:
        cases = cases[:max_cases]
    if not cases:
        return ConsultEvalReport()

    session_factory = session_factory or default_session_factory()
    entry_graph = entry_graph or build_entry_graph()
    consult_graph = consult_graph or build_consult_graph(
        session_factory, checkpointer=InMemorySaver(), student_no=student_no
    )
    repair_graph = repair_graph or build_repair_graph(session_factory, checkpointer=InMemorySaver())
    quality_graph = quality_graph or build_quality_graph(
        session_factory, checkpointer=InMemorySaver()
    )

    results: list[ConsultCaseResult] = []
    start = time.monotonic()
    for case in cases:
        t0 = time.monotonic()
        cfg = {"configurable": {"thread_id": f"eval-{case.id}"}}
        failures: list[str] = []
        turn_count = 0
        prev_tools: set[str] = set()
        outcome: str | None = None

        first = turn(
            entry_graph,
            repair_graph,
            consult_graph,
            f"eval-{case.id}",
            case.student_input,
            quality_graph=quality_graph,
        )
        outcome = first.get("outcome") or outcome
        prev_tools = set(first.get("tool_calls", []))

        for idx, scripted in enumerate(case.turns, start=1):
            turn_count = idx
            if consult_graph.get_state(cfg).next == ():
                # 咨询失配不判失败：Agent 提前给出答案 = 自助解决（LLM 自由对话
                # 下"学生补充信息"被提前解答是合理行为，非脚本答非所问失真——
                # 失真判定口径仅保留给报修链路的确定性追问轮）
                break
            out = turn(
                entry_graph,
                repair_graph,
                consult_graph,
                f"eval-{case.id}",
                scripted.student_reply,
                quality_graph=quality_graph,
            )
            failures.extend(
                f"第{idx}轮: {fail}" for fail in check_expect(prev_tools, out, scripted.expect)
            )
            prev_tools = set(out.get("tool_calls", []))
            outcome = out.get("outcome") or outcome

        results.append(
            ConsultCaseResult(
                case=case,
                turn_count=turn_count,
                outcome=outcome,
                failures=failures,
                seconds=time.monotonic() - t0,
            )
        )
    return ConsultEvalReport(results=results, duration_seconds=time.monotonic() - start)


@dataclass
class ComplaintCaseResult:
    case: ScriptedCase
    turn_count: int
    failures: list[str]  # (第几轮: 断言失败/剧本失配) 明细；空 = 通过
    seconds: float


@dataclass
class ComplaintEvalReport:
    results: list[ComplaintCaseResult] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed_cases(self) -> int:
        return sum(1 for r in self.results if not r.failures)

    @property
    def success_rate(self) -> float:
        return self.passed_cases / len(self.results) if self.results else 0.0

    @property
    def avg_turns(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.turn_count for r in self.results) / len(self.results)

    def failure_details(self) -> list[str]:
        details = []
        for r in self.results:
            for failure in r.failures:
                details.append(f"{r.case.id}: {failure}")
        return details


def run_complaint_evaluation(
    cases: list[ScriptedCase],
    session_factory,
    entry_graph=None,
    repair_graph=None,
    consult_graph=None,
    quality_graph=None,
    complaint_graph=None,
    max_cases: int | None = None,
) -> ComplaintEvalReport:
    """投诉链路评测（M5-T4）：仅 complaint 剧本，多轮驱动，镜像报修口径。

    - 首轮走 orchestrator.turn（COMPLAINT 路由进 complaint_graph，thread_id=case.id）
    - 每轮 turns：先断言上轮 paused（complaint_graph.get_state().next 非空）——
      投诉是确定性追问管道（缺 contact 必追问），剧本失配 = 脚本答非所问失真，
      判失败（与报修同口径，区别于咨询的"提前解答=自助解决"）
    - complaint_graph 缺省时 build_repair_graph(ticket_type="complaint") 构建
      （必填集仅 contact、跳过 classify、建单停 SUBMITTED、无实质转人工）
    - 各图可注入（测试用 fake/规则版；默认真 LLM + InMemorySaver 隔离）
    """
    from langgraph.checkpoint.memory import InMemorySaver

    from campus_desk.consult.graph import build_consult_graph
    from campus_desk.db.session import default_session_factory
    from campus_desk.quality.graph import build_quality_graph
    from campus_desk.repair.graph import build_repair_graph

    cases = [c for c in cases if c.category == "complaint"]
    if max_cases:
        cases = cases[:max_cases]
    if not cases:
        return ComplaintEvalReport()

    session_factory = session_factory or default_session_factory()
    entry_graph = entry_graph or build_entry_graph()
    # 评测用 InMemorySaver（同 repair 段）：文件级 SqliteSaver 残留终态会全剧本失配
    complaint_graph = complaint_graph or build_repair_graph(
        session_factory, ticket_type="complaint", checkpointer=InMemorySaver()
    )
    repair_graph = repair_graph or build_repair_graph(session_factory, checkpointer=InMemorySaver())
    consult_graph = consult_graph or build_consult_graph(
        session_factory, checkpointer=InMemorySaver()
    )
    quality_graph = quality_graph or build_quality_graph(
        session_factory, checkpointer=InMemorySaver()
    )

    results: list[ComplaintCaseResult] = []
    start = time.monotonic()
    for case in cases:
        t0 = time.monotonic()
        cfg = {"configurable": {"thread_id": f"eval-{case.id}"}}
        failures: list[str] = []
        turn_count = 0
        prev_tools: set[str] = set()

        first = turn(
            entry_graph,
            repair_graph,
            consult_graph,
            f"eval-{case.id}",
            case.student_input,
            quality_graph=quality_graph,
            complaint_graph=complaint_graph,
        )
        prev_tools = set(first.get("tool_calls", []))

        for idx, scripted in enumerate(case.turns, start=1):
            turn_count = idx
            if complaint_graph.get_state(cfg).next == ():
                failures.append(f"第{idx}轮: 剧本失配（上轮未等待学生回复，脚本答非所问）")
                break
            out = turn(
                entry_graph,
                repair_graph,
                consult_graph,
                f"eval-{case.id}",
                scripted.student_reply,
                quality_graph=quality_graph,
                complaint_graph=complaint_graph,
            )
            failures.extend(
                f"第{idx}轮: {fail}" for fail in check_expect(prev_tools, out, scripted.expect)
            )
            prev_tools = set(out.get("tool_calls", []))

        results.append(
            ComplaintCaseResult(
                case=case,
                turn_count=turn_count,
                failures=failures,
                seconds=time.monotonic() - t0,
            )
        )
    return ComplaintEvalReport(results=results, duration_seconds=time.monotonic() - start)


def format_complaint_report(report: ComplaintEvalReport) -> str:
    lines = [
        "## 投诉链路评测（M5）",
        "",
        f"- 投诉用例数: {report.total}",
        f"- 链路成功率: **{report.success_rate:.1%}**（{report.passed_cases}/{report.total}）",
        f"- 平均对话轮次: {report.avg_turns:.1f}",
        f"- 耗时: {report.duration_seconds:.1f}s",
    ]
    details = report.failure_details()
    lines += ["", "### 失败明细", ""] if details else ["", "### 失败明细", "", "无"]
    for d in details:
        lines.append(f"- {d}")
    return "\n".join(lines)


def format_consult_report(report: ConsultEvalReport) -> str:
    lines = [
        "## 咨询链路评测（M4）",
        "",
        f"- 咨询用例数: {report.total}",
        f"- 自助解决率: **{report.self_service_rate:.1%}**（{report.self_served}/{report.total}，目标 ≥70%）",
        f"- 人工介入率: **{report.handoff_rate:.1%}**（{report.handoff_count}/{report.total}，目标 ≤30%）",
        f"- 平均对话轮次: {report.avg_turns:.1f}（目标 ≤4）",
        f"- 剧本断言通过率: {report.success_rate:.1%}（{report.passed_cases}/{report.total}）",
        f"- 耗时: {report.duration_seconds:.1f}s",
    ]
    details = report.failure_details()
    lines += ["", "### 失败明细", ""] if details else ["", "### 失败明细", "", "无"]
    for d in details:
        lines.append(f"- {d}")
    return "\n".join(lines)


def format_repair_report(report: RepairEvalReport) -> str:
    lines = [
        "## 报修链路评测（M3）",
        "",
        f"- 报修用例数: {report.total}",
        f"- 链路成功率: **{report.success_rate:.1%}**（{report.passed_cases}/{report.total}）",
        f"- 平均对话轮次: {report.avg_turns:.1f}",
        f"- 耗时: {report.duration_seconds:.1f}s",
    ]
    details = report.failure_details()
    lines += ["", "### 失败明细", ""] if details else ["", "### 失败明细", "", "无"]
    for d in details:
        lines.append(f"- {d}")
    return "\n".join(lines)


def format_report(
    report: EvalReport,
    repair_report: RepairEvalReport | None = None,
    consult_report: ConsultEvalReport | None = None,
    complaint_report: ComplaintEvalReport | None = None,
) -> str:
    """Markdown 格式评测报告（可存档可面试展示）。"""
    lines = [
        "# CampusDesk 评测报告",
        "",
        "## M2 入口分流",
        "",
        f"- 用例数: {report.total}",
        f"- 意图分类准确率: **{report.intent_accuracy:.1%}**（{report.intent_correct}/{report.total}）",
        f"- 路由准确率: **{report.route_correct / report.total:.1%}**（{report.route_correct}/{report.total}）"
        if report.total
        else "- 路由准确率: -",
        f"- 总耗时: {report.duration_seconds:.1f}s",
        "",
        "### 混淆矩阵（标注 \\ 预测）",
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
        lines += ["", "### 低置信转人工明细（门控审计）", ""]
        for r in report.handoff_cases:
            lines.append(
                f"- {r.case.id}（标注 {r.case.intent}，置信度 {r.confidence:.2f}）: {r.case.student_input}"
            )
    else:
        lines += ["", "### 低置信转人工明细", "", "无（门控未误伤）"]

    misclassified = [r for r in report.results if r.predicted_intent != r.case.intent]
    lines += ["", "### 错误用例明细", ""] if misclassified else ["", "### 错误用例明细", "", "无"]
    for r in misclassified:
        lines.append(
            f"- {r.case.id}: 标注 {r.case.intent}，预测 {r.predicted_intent}（置信度 "
            f"{r.confidence:.2f}）｜{r.case.student_input}"
        )

    lines += ["", "### 慢用例（Top 3）", ""]
    for r in sorted(report.results, key=lambda x: x.seconds, reverse=True)[:3]:
        lines.append(f"- {r.case.id}: {r.seconds:.1f}s")

    if repair_report is not None:
        lines += ["", format_repair_report(repair_report)]
    if consult_report is not None:
        lines += ["", format_consult_report(consult_report)]
    if complaint_report is not None:
        lines += ["", format_complaint_report(complaint_report)]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="CampusDesk 评测（入口分流 + 报修/咨询/投诉链路）")
    parser.add_argument("--max", type=int, default=None, help="只跑前 N 条（调试用）")
    parser.add_argument("--out", type=str, default=None, help="报告写入路径（默认打印）")
    parser.add_argument("--no-repair", action="store_true", help="跳过报修链路评测")
    parser.add_argument("--no-consult", action="store_true", help="跳过咨询链路评测")
    parser.add_argument("--no-complaint", action="store_true", help="跳过投诉链路评测")
    args = parser.parse_args()

    if not settings.deepseek_api_key:
        print("SKIP: 未配置 DEEPSEEK_API_KEY（.env 填写后重跑）——需外部环境的项不进 CI")
        return

    report = run_evaluation(max_cases=args.max)
    repair_report = None
    consult_report = None
    complaint_report = None
    if not (args.no_repair and args.no_consult and args.no_complaint) and not settings.database_url:
        print("SKIP: 未配置 DATABASE_URL——链路评测需 MySQL（.env 填写后重跑）")
    else:
        if not args.no_repair:
            repair_report = run_repair_evaluation(load_all(), None, max_cases=args.max)
        if not args.no_consult:
            consult_report = run_consult_evaluation(load_all(), None, max_cases=args.max)
        if not args.no_complaint:
            complaint_report = run_complaint_evaluation(load_all(), None, max_cases=args.max)
    text = format_report(report, repair_report, consult_report, complaint_report)
    telemetry.flush()  # 冲刷 trace 事件（短生命周期脚本必调；无 key 时 no-op）
    if args.out:
        from pathlib import Path

        Path(args.out).write_text(text, encoding="utf-8")
        print(f"报告已写入: {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
