"""M1-ZJUT 环境验证：3 项地基能力检查，可被 pytest 直接测试。

- check_langgraph_quickstart：最小 StateGraph 编译+运行（纯确定性节点，不调 LLM）
- check_deepseek_structured：build_llm + 自写 prompt 结构化输出一次真实调用
  （无 key 标 SKIP，需外部环境的项不进 CI）
- check_fc_support：真 FC 可用性探测——bind_tools 一次真实调用，成功且返回
  tool_calls → FC_SUPPORTED=True；400/异常 → FC_SUPPORTED=False（设计 §4.4：
  探测结果出来前不写死 FC 实现，不可用则 M2 工具管道走伪 FC 兜底）

设计：逻辑放包内模块（单一实现），scripts/verify_env.py 只是 CLI 薄入口。
"""

import json
import re
from dataclasses import dataclass
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from campus_desk.config import settings
from campus_desk.llm import build_llm


@dataclass
class CheckResult:
    name: str
    status: str  # PASS / SKIP / FAIL
    detail: str

    @property
    def passed(self) -> bool:
        return self.status == "PASS"


# ---------- 1. LangGraph quickstart ----------


class QuickState(TypedDict):
    topic: str
    joke: str


def refine_topic(state: QuickState) -> dict:
    return {"topic": state["topic"] + "（已润色）"}


def generate_joke(state: QuickState) -> dict:
    return {"joke": f"关于{state['topic']}的笑话"}


def check_langgraph_quickstart() -> CheckResult:
    graph = (
        StateGraph(QuickState)
        .add_node(refine_topic)
        .add_node(generate_joke)
        .add_edge(START, "refine_topic")
        .add_edge("refine_topic", "generate_joke")
        .add_edge("generate_joke", END)
        .compile()
    )
    result = graph.invoke({"topic": "校园网络"})
    if not result["joke"].startswith("关于"):
        return CheckResult("LangGraph quickstart", "FAIL", f"图输出异常: {result}")
    return CheckResult(
        "LangGraph quickstart",
        "PASS",
        f"2 节点图编译+运行成功：{result['joke']}",
    )


# ---------- 2. DeepSeek 结构化输出一次调用 ----------

# 自写 prompt（含 "json" 字样，DeepSeek json_object 模式硬性要求，见 intent.py 注释）
_STRUCTURED_CHECK_PROMPT = """你是校园助手。请输出一个 JSON 对象回答下面的问题。

JSON 格式（严格只输出 JSON，不要任何其他文字）：
{"answer": "一句话回答"}

问题：校历在哪里查？"""


def check_deepseek_structured() -> CheckResult:
    """build_llm + json_object 结构化输出一次真实调用，校验返回合法 JSON。"""
    if not settings.deepseek_api_key:
        return CheckResult(
            "DeepSeek 结构化输出",
            "SKIP",
            "未配置 DEEPSEEK_API_KEY（.env 填写后重跑）",
        )
    llm = build_llm()  # 统一构造：response_format=json_object 构造期声明 + langfuse 挂载
    try:
        reply = llm.invoke([("system", _STRUCTURED_CHECK_PROMPT)])
    except Exception as exc:  # noqa: BLE001 — 外部调用需兜底所有错误转为检查结果，不让脚本崩溃
        return CheckResult("DeepSeek 结构化输出", "FAIL", f"调用异常: {exc!r}")
    content = str(reply.content or "").strip()
    if not content:
        return CheckResult("DeepSeek 结构化输出", "FAIL", "模型返回空内容")
    try:
        text = content
        if "```" in text:  # 容忍 ```json ... ``` 代码块（与 intent.py 同款容错）
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise json.JSONDecodeError("未找到 JSON 对象", content, 0)
        json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError) as exc:
        return CheckResult("DeepSeek 结构化输出", "FAIL", f"输出非合法 JSON: {exc}")
    return CheckResult(
        "DeepSeek 结构化输出",
        "PASS",
        f"json_object 模式返回合法 JSON：{content[:40]}…",
    )


# ---------- 3. 真 FC 可用性探测 ----------

_FC_PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "query_canteen",
        "description": "查询食堂营业时间",
        # langchain-openai 1.4.1 起非 strict 工具无法自动解析（bind_tools 即抛），
        # 必须 strict + additionalProperties=False 才能发出真实请求
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {"canteen": {"type": "string", "description": "食堂名称"}},
            "required": ["canteen"],
            "additionalProperties": False,
        },
    },
}


def check_fc_support() -> CheckResult:
    """真 FC 探测：build_llm().bind_tools() 一次调用，返回 tool_calls → FC_SUPPORTED=True。

    实测结论（2026-08-15，deepseek-v4-flash）：**FC 可用**——bind_tools 返回
    真实 tool_calls；但 build_llm 构造期写死 response_format=json_object，
    要求 prompt 含 "json" 字样，否则 400 "Prompt must contain the word 'json'"
    （探测 prompt 因此必须带 json；M2 工具管道同样受此约束：要么 prompt 带
    json 字样，要么工具场景单独构造不带 response_format 的实例）。
    结果打印并写 docs/STATUS.md，供 M2 工具管道定真 FC / 伪 FC（ZJUT_DESIGN §4.4）。
    """
    if not settings.deepseek_api_key:
        return CheckResult(
            "FC 可用性探测",
            "SKIP",
            "未配置 DEEPSEEK_API_KEY，无法探测（无 key 调用会误报 FC_SUPPORTED=False）",
        )
    llm = build_llm()
    try:
        reply = llm.bind_tools([_FC_PROBE_TOOL]).invoke(
            "请输出 JSON 调用工具回答：食堂几点开门？"
        )
    except Exception as exc:  # noqa: BLE001 — 探测要兜底一切异常（含 400/网络）
        return CheckResult(
            "FC 可用性探测",
            "FAIL",
            f"bind_tools 调用异常（FC 不支持或网络问题）: {exc!r}",
        )
    tool_calls = getattr(reply, "tool_calls", None) or []
    if not tool_calls:
        return CheckResult(
            "FC 可用性探测",
            "FAIL",
            "调用成功但未返回 tool_calls（FC 未生效）",
        )
    names = [
        tc.get("name", "?") if isinstance(tc, dict) else getattr(tc, "name", "?")
        for tc in tool_calls
    ]
    return CheckResult(
        "FC 可用性探测",
        "PASS",
        f"bind_tools 返回 {len(tool_calls)} 个 tool_calls: {names}（FC_SUPPORTED=True）",
    )
