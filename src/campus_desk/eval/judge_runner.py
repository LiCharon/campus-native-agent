"""E1 · LLM-as-judge 评测层：对生成节点输出做三维自动评分（依据性/相关性/格式）。

定位（grilling 拍板 2026-09-07，勿改）：
- **零门**：judge 分数只进报表，规则断言（chain_runner）仍是唯一上线闸门——
  本模块任何输出不得用于拦截发布/使 CI 变红。
- 只评 knowledge 线（生成节点产物，含拒答）；tool_query 线不评。
- judge = 同款 DeepSeek（build_llm json_object 结构化）；自评偏差靠人工抽检
  一致率量化（抽检表见报告），不换模型。
- 运行环境 = 链路评测同款 seed_all SQLite（gold id 单源可解析；生产 834 库版
  留待需要时做，gold 需按库重标）。

用法：python -m campus_desk.eval.judge_runner
输出：scripts/judge_report.json（逐条明细 + 聚合）+ 控制台摘要
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from campus_desk import usage
from campus_desk.knowledge.generator import NOT_FOUND_MARK
from campus_desk.llm import build_llm
from campus_desk.prompt_guard import UNTRUSTED_INPUT_NOTICE, wrap_input

DATASET = Path(__file__).parent / "dataset" / "judge" / "zjut_judge.json"
CHAIN_DATASET = Path(__file__).parent / "dataset" / "chain" / "zjut_chain.json"
REPORT = ROOT / "scripts" / "judge_report.json"

_ANSWER_CLIP = 300  # 单条材料截断（与 generator 同量级，防拼爆 token）

_JUDGE_PROMPT = """你是校园办事助手问答质量的评审（judge）。给定学生问题、检索材料、标准答案（gold）和助手回答，按三个维度打 0-5 分，输出 JSON。

维度定义：
- faithfulness 依据性：回答内容是否全部来自检索材料或标准答案。编造事实=0-1；部分引申=3；严格基于材料=5。拒答类（声明未找到/转人工）无内容可编造=5。
- relevance 相关性：是否恰当回应了学生问题。直答切题=5；合理追问澄清=4；该答没答（gold 存在却答"未找到"或答非所问）=0-2；拒答类：恰当拒答（未找到/追问/转人工）=5，硬发沾边内容=0-1。
- format 格式：answer 类回答应含 [n] 引用标注（有=5，缺=2）；追问/拒答类不适用，记 5。

JSON 格式（严格只输出 JSON）：
{"faithfulness": 0-5整数, "relevance": 0-5整数, "format": 0-5整数, "reason": "一句话理由"}""" + UNTRUSTED_INPUT_NOTICE


def load_cases() -> list[dict]:
    """加载 judge 集：ref 引用链路集解析题面/gold，inline 直接用。

    k-009/k-010 死 gold 已在数据集排除；解析失败的 ref 直接抛错（数据集错误要显性炸）。
    """
    raw = json.loads(DATASET.read_text(encoding="utf-8"))
    chain_data = json.loads(CHAIN_DATASET.read_text(encoding="utf-8"))
    chain_items = chain_data if isinstance(chain_data, list) else chain_data.get("cases", [])
    chain = {c["id"]: c for c in chain_items}
    cases: list[dict] = []
    for i, item in enumerate(raw["cases"]):
        if "ref" in item:
            base = chain[item["ref"]]
            cases.append(
                {
                    "case_id": item["ref"],
                    "query": base["student_input"],
                    "expected_outcome": base["expected_outcome"],
                    "expected_refusal": item["expected_refusal"],
                    "gold_ids": base.get("expected_entry_ids") or [],
                    "gold_texts": [],
                }
            )
        else:
            cases.append(
                {
                    "case_id": f"inline-refusal-{i}",
                    "query": item["query"],
                    "expected_outcome": "handoff",
                    "expected_refusal": True,
                    "gold_ids": [],
                    "gold_texts": [],
                }
            )
    return cases


def _resolve_gold_texts(factory, cases: list[dict]) -> None:
    """gold id → 条目题面+答案文本（就地写回 gold_texts）。种子库逐条查，缺失记空。"""
    from campus_desk.db.models import KnowledgeEntry

    need = {gid for c in cases for gid in c["gold_ids"]}
    texts: dict[int, dict] = {}
    if need:
        with factory() as s:
            for gid in need:
                row = s.get(KnowledgeEntry, gid)
                if row is not None:
                    texts[gid] = {"id": gid, "question": row.question, "answer": row.answer}
    for c in cases:
        c["gold_texts"] = [texts[gid] for gid in c["gold_ids"] if gid in texts]


def _resolve_hits(factory, ids: list[int]) -> list[dict]:
    """链路回传的 hits 是 id 列表 → 回库解析成题面/答案文本（judge 材料用）。"""
    if not ids or factory is None:
        return []
    from campus_desk.db.models import KnowledgeEntry

    with factory() as s:
        rows = s.query(KnowledgeEntry).filter(KnowledgeEntry.id.in_(ids)).all()
    by_id = {r.id: r for r in rows}
    return [
        {"id": i, "question": by_id[i].question, "answer": by_id[i].answer}
        for i in ids
        if i in by_id
    ]


def _judge_material(hits: list[dict]) -> str:
    """hits → 编号材料（answer 截断，与 generator 同口径）。"""
    lines = []
    for i, h in enumerate(hits):
        answer = (h.get("answer") or "")[:_ANSWER_CLIP]
        lines.append(f"{i + 1}. {h.get('question', '')} → {answer}")
    return "\n".join(lines)


def _build_judge_messages(case: dict, reply: str, outcome: str, hits: list[dict]) -> list[tuple]:
    gold_lines = [
        f"- {g['question']} → {g['answer'][:_ANSWER_CLIP]}" for g in case["gold_texts"]
    ] or ["（无——预期库内没有该信息）"]
    human = (
        f"学生问题：{case['query']}\n"
        f"期望情形：{case['expected_outcome']}（answer=应直答 / ask=应追问澄清 / handoff=应转人工）\n"
        f"拒答预期：{case['expected_refusal']}（true=库内没有，恰当拒答/追问/转人工均算成功拒答）\n"
        f"标准答案（gold）：\n" + "\n".join(gold_lines) + "\n"
        f"检索材料：\n{ _judge_material(hits) or '（无检索结果）' }\n"
        f"实际 outcome：{outcome}\n"
        f"助手回答：{reply}"
    )
    return [("system", _JUDGE_PROMPT), ("human", wrap_input(human))]


def _parse_judge_json(content: str) -> dict[str, Any] | None:
    """解析 judge 输出（容 markdown 围栏）；缺字段/类型不对 → None。"""
    text = content.strip()
    if "```" in text:
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    dims = ("faithfulness", "relevance", "format")
    # bool 是 int 子类：不排除的话 LLM 返回 true 会被当成 1 分（2026-09-08 review）
    if not all(
        isinstance(data.get(d), (int, float))
        and not isinstance(data.get(d), bool)
        and 0 <= data[d] <= 5
        for d in dims
    ):
        return None
    return {d: data[d] for d in dims} | {"reason": str(data.get("reason", ""))}


def _run_case(kg, judge_llm, case: dict, factory=None) -> dict:
    """跑单条：真链路取 reply → hits 回库解析材料 → judge 打分。单条失败不阻塞批量。"""
    state = kg.invoke({"user_input": case["query"]}, {"configurable": {"thread_id": f"judge-{case['case_id']}"}})
    reply, outcome = state.get("reply", ""), state.get("outcome", "")
    hit_texts = _resolve_hits(factory, state.get("hits", []))
    row: dict[str, Any] = {
        "case_id": case["case_id"],
        "query": case["query"],
        "expected_refusal": case["expected_refusal"],
        "expected_outcome": case["expected_outcome"],
        "outcome": outcome,
        "reply": reply,
        "hits": state.get("hits", []),
        "hit_texts": hit_texts,
        "scores": None,
        "reason": "",
        "error": None,
    }
    if judge_llm is None:
        row["error"] = "judge LLM 不可用"
        return row
    # judge 输入含学生输入/回答等不可信数据 → wrap_input（声明在 system 末尾）
    try:
        with usage.call_point(usage.CALL_POINT_JUDGE):
            raw = judge_llm.invoke(_build_judge_messages(case, reply, outcome, hit_texts))
        parsed = _parse_judge_json(raw.content if hasattr(raw, "content") else str(raw))
    except Exception as exc:  # noqa: BLE001 — 单条 judge 失败不阻塞批量
        row["error"] = f"judge 调用异常: {exc!r}"
        return row
    if parsed is None:
        row["error"] = "judge 输出解析失败"
        return row
    row["scores"] = {k: parsed[k] for k in ("faithfulness", "relevance", "format")}
    row["reason"] = parsed["reason"]
    return row


def _refusal_ok(row: dict) -> bool:
    """合格拒答：未找到文案 或 追问/转人工 outcome（不硬发沾边内容即算）。

    未找到文案引用 generator.NOT_FOUND_MARK 常量——此前本处与 prompt 各自硬编码
    同一串，改任一处都会让判据静默失效（2026-09-08 code review 发现）。
    """
    return row["outcome"] in ("ask", "handoff") or NOT_FOUND_MARK in row["reply"]


def _aggregate(rows: list[dict]) -> dict:
    scored = [r for r in rows if r["scores"] is not None]
    means = {
        d: round(sum(r["scores"][d] for r in scored) / len(scored), 2) if scored else 0.0
        for d in ("faithfulness", "relevance", "format")
    }
    dist = {d: dict(Counter(r["scores"][d] for r in scored)) for d in ("faithfulness", "relevance", "format")}
    # 拒答是纯规则判定（outcome/reply），与 judge LLM 无关：按 error 过滤会在
    # judge 挂掉时把 refusal_n 归零，白丢一份不依赖 LLM 的指标。
    # 只排除链路本身没跑出回复的行（那种无内容可判）。
    refusal_rows = [r for r in rows if r["expected_refusal"] and r.get("reply")]
    return {
        "n": len(rows),
        "judge_errors": sum(1 for r in rows if r["error"] is not None),
        "means": means,
        "distribution": dist,
        "refusal_n": len(refusal_rows),
        "refusal_ok_n": sum(1 for r in refusal_rows if _refusal_ok(r)),
    }


def main() -> None:
    from langgraph.checkpoint.memory import InMemorySaver

    from campus_desk.eval.chain_runner import _make_factory
    from campus_desk.knowledge.graph import build_knowledge_graph

    factory = _make_factory()
    kg = build_knowledge_graph(factory, checkpointer=InMemorySaver())
    judge_llm = build_llm()

    cases = load_cases()
    _resolve_gold_texts(factory, cases)
    print(f"judge 集 {len(cases)} 条（拒答 {sum(1 for c in cases if c['expected_refusal'])} 条）\n")

    rows = []
    # route="eval" 包住整轮：judge 触发的 decide/generate 也归属评测，
    # 否则它们与线上流量同名（call_point=generate、route 空），
    # 成本报表里"过滤 judge"会过滤不干净（2026-09-08 review）
    with usage.usage_ctx(route="eval"):
        for c in cases:
            row = _run_case(kg, judge_llm, c, factory=factory)
            mark = "ERR" if row["error"] else (
                f"F{row['scores']['faithfulness']}/R{row['scores']['relevance']}/G{row['scores']['format']}"
            )
            print(f"- {row['case_id']} [{mark}] {row['query'][:24]}")
            rows.append(row)

    agg = _aggregate(rows)
    report = {"meta": {"gate": "零门：judge 只进报表，不拦发布", "dataset": str(DATASET)}, "aggregate": agg, "rows": rows}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(
        f"\n均分 F{agg['means']['faithfulness']} / R{agg['means']['relevance']} / G{agg['means']['format']}"
        f" | judge 错误 {agg['judge_errors']}/{agg['n']}"
        f" | 拒答合格 {agg['refusal_ok_n']}/{agg['refusal_n']}"
    )
    print(f"明细 → {REPORT}")


if __name__ == "__main__":
    main()
