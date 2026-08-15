"""入口分流图：学生输入 → 意图识别 → 置信度门控 → 路由。

三段节点（可单测、可面试讲"分流=识别/门控/路由"）：
- recognize：IntentClassifier.classify（三层防线，不抛异常）
- gate：低置信（<0.7）或 other 意图 → HUMAN_HANDOFF（决策节点）
- route：intent → 主流程路由 + 多意图次要提示

门控分流 = 图的条件边（LangGraph 语义）：gate 后判定 route，
HUMAN_HANDOFF 直接到 END，其余进 route 节点。
"""

from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from campus_desk.entry.intent import IntentClassifier, IntentResult
from campus_desk.entry.routes import (
    HUMAN_HANDOFF,
    KNOWLEDGE,
    MULTI_INTENT,
    TOOL_QUERY,
)

# 门控阈值：低于则转人工（需求 §2 置信度门控）
CONFIDENCE_THRESHOLD = 0.7

# 意图 → 中文名（给学生看的提示用，不用英文枚举）
_INTENT_LABELS = {
    "knowledge": "校园知识问答",
    "tool_query": "动态查询",
    "multi_intent": "多问题",
    "other": "其他",
}

# 各路由回复文案（占位壳；M3 接入 KnowledgeGraph 后由下游回复覆盖）
_ROUTE_REPLIES = {
    KNOWLEDGE: "好的，我来为您查询，请稍等。",
    TOOL_QUERY: "该查询功能正在建设中，您可以先问我其他校园问题。",
    MULTI_INTENT: "好的，我逐一来回答您的问题。",
    HUMAN_HANDOFF: "已为您转人工处理，请稍候。",
}


class EntryState(TypedDict):
    user_input: str
    intent: IntentResult | None
    route: str
    reply: str


def _recognize(state: EntryState, classifier: IntentClassifier) -> dict:
    return {"intent": classifier.classify(state["user_input"])}


def _gate(state: EntryState) -> dict:
    """门控决策：低置信 / other 意图 → 转人工；其余放行。"""
    intent = state["intent"]
    if intent.confidence < CONFIDENCE_THRESHOLD or intent.intent == "other":
        return {"route": HUMAN_HANDOFF, "reply": _ROUTE_REPLIES[HUMAN_HANDOFF]}
    return {}


def _after_gate(state: EntryState) -> Literal["route", "end"]:
    """条件边：已转人工则直接结束，否则进路由节点。

    注意：gate 正常路径返回 {}（增量合并，state 无 route key），
    因此用 .get 判定"是否被置为 HUMAN_HANDOFF"。"""
    return "end" if state.get("route") == HUMAN_HANDOFF else "route"


def _route(state: EntryState) -> dict:
    """路由：intent → 主流程；多意图追加"可继续提问"提示。"""
    intent = state["intent"]
    reply = _ROUTE_REPLIES[intent.intent]
    if intent.secondary_intents:
        labels = "、".join(_INTENT_LABELS.get(s, s) for s in intent.secondary_intents)
        reply += f" 另外，您提到的其他问题（{labels}），可以继续问我。"
    return {"route": intent.intent, "reply": reply}


def build_entry_graph(classifier: IntentClassifier | None = None):
    """构建入口分流图。classifier 可注入（测试用 fake，默认真 LLM）。"""
    clf = classifier if classifier is not None else IntentClassifier()

    graph = (
        StateGraph(EntryState)
        .add_node("recognize", lambda state: _recognize(state, clf))
        .add_node("gate", _gate)
        .add_node("route", _route)
        .add_edge(START, "recognize")
        .add_edge("recognize", "gate")
        .add_conditional_edges("gate", _after_gate, {"route": "route", "end": END})
        .add_edge("route", END)
    )
    return graph.compile()
