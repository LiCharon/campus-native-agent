"""一次性脚本：把 scripts/judge_report.json 渲染成人工抽检表（E1 验收第 4 步）。

产物：docs/eval/judge_manual_review_e1.md（docs 为独立仓）
用法：python scripts/_make_manual_review.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "scripts" / "judge_report.json"
OUT = ROOT / "docs" / "eval" / "judge_manual_review_e1.md"


def esc(s: object) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ").strip()


def main() -> None:
    data = json.loads(REPORT.read_text(encoding="utf-8"))
    agg = data["aggregate"]

    lines = [
        "# E1 · judge 人工抽检表（回填一致率）",
        "",
        "> 用法：逐条看「问题 / 回答 / 三维分 / 理由」，在最后一列填 `认可` 或 `不认可（原因）`。",
        "> 填完交回 AI 统计一致率，回写 `docs/eval/eval_report_zjut_e1.md`。",
        "> 零门纪律：judge 分数只进报表、不拦发布；本表只用于校准 judge 本身的偏差。",
        "",
        f"- 样本 {agg['n']} 条（拒答预期 {agg['refusal_n']} 条，judge 判合规 {agg['refusal_ok_n']} 条）",
        "- judge 均分：依据性 {faithfulness} / 相关性 {relevance} / 格式 {format}".format(
            **agg["means"]
        ),
        "- F=依据性（是否只来自检索材料）R=相关性 G=格式，各 0–5 分",
        "",
        "| # | case | 问题 | 回答（生成节点输出） | F/R/G | judge 理由 | 人工判定 |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(data["rows"], 1):
        s = r["scores"]
        lines.append(
            f"| {i} | `{r['case_id']}` | {esc(r['query'])} | {esc(r['reply'])} "
            f"| {s['faithfulness']}/{s['relevance']}/{s['format']} | {esc(r['reason'])} |  |"
        )
    lines.append("")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"写出 {OUT}（{len(data['rows'])} 条）")


if __name__ == "__main__":
    main()
