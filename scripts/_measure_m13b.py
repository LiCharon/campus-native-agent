"""M13B 临门一刀：量化工具 schema 描述瘦身收益（无需真实调用）。

对比「当前 TOOL_SCHEMAS」与「原始描述」的描述字符/估算 token 总量差。
原始描述按 M13B 计划重建：5 个个人数据工具带「（学生学号由系统注入，无需询问）」
后缀 + retrieve_knowledge 长描述。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from campus_desk.query import tools

_SUFFIX = "（学生学号由系统注入，无需询问）"
_SUFFIX_TOOLS = {
    "query_timetable",
    "query_exam_scores",
    "query_exam_schedule",
    "query_library_borrow",
    "query_card_balance",
}
_ORIG_RETRIEVE = (
    "检索校园知识库，回答校园办事类问题（校园卡/图书馆/奖助/宿舍/教务等）。"
    "当对话中需要解答校园常识或办事流程、且不是纯数据查询时使用。"
)


def _reconstruct_original(name: str, cur: str) -> str:
    orig = cur
    if name in _SUFFIX_TOOLS:
        orig = cur + _SUFFIX
    if name == "retrieve_knowledge":
        orig = _ORIG_RETRIEVE
    return orig


def _approx_tokens(s: str) -> int:
    """中文混排粗略估算：CJK 字 ~1 token，ASCII/标点 ~0.3 token。

    仅用于横向对比，量级正确即可（DeepSeek tokenizer 实际值会略不同）。
    """
    cjk = sum(1 for ch in s if "一" <= ch <= "鿿")
    other = len(s) - cjk
    return cjk + round(other * 0.3)


def main() -> None:
    schemas = tools.TOOL_SCHEMAS
    current = {s["function"]["name"]: s["function"]["description"] for s in schemas}
    original = {n: _reconstruct_original(n, d) for n, d in current.items()}

    cur_chars = sum(len(v) for v in current.values())
    orig_chars = sum(len(v) for v in original.values())
    cur_tok = sum(_approx_tokens(v) for v in current.values())
    orig_tok = sum(_approx_tokens(v) for v in original.values())

    print(f"工具数: {len(schemas)}")
    print(f"描述字符  原={orig_chars}  现={cur_chars}  省={orig_chars - cur_chars}  ({(1-cur_chars/orig_chars)*100:.1f}%)")
    print(f"估算token 原={orig_tok}  现={cur_tok}  省={orig_tok - cur_tok}  ({(1-cur_tok/orig_tok)*100:.1f}%)")
    print("\n各工具节省字符:")
    for n, desc in current.items():
        saved = len(original[n]) - len(desc)
        if saved:
            print(f"  {n}: -{saved}")


if __name__ == "__main__":
    main()
