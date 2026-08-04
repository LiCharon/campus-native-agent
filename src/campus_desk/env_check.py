"""M1 环境验证：3 项地基能力检查，可被 pytest 直接测试。

- check_langgraph_quickstart：最小 StateGraph 编译+运行（纯确定性节点，不调 LLM）
- check_deepseek_call：真实调用一次 DeepSeek（无 key 标 SKIP，需外部环境的项不进 CI）
- check_sqlite_resume：SqliteSaver 中断 + 模拟进程重启后恢复（M4 会话记忆地基）

设计：逻辑放包内模块（单一实现），scripts/verify_env.py 只是 CLI 薄入口。
"""

import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from campus_desk.config import settings


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


# ---------- 2. DeepSeek 一次调用 ----------


def check_deepseek_call() -> CheckResult:
    if not settings.deepseek_api_key:
        return CheckResult(
            "DeepSeek 一次调用",
            "SKIP",
            "未配置 DEEPSEEK_API_KEY（.env 填写后重跑）",
        )
    llm = ChatOpenAI(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        base_url="https://api.deepseek.com",
        timeout=30,
    )
    try:
        reply = llm.invoke("请用一句话介绍你自己")
    except Exception as exc:  # noqa: BLE001 — 外部调用需兜底所有错误转为检查结果，不让脚本崩溃
        return CheckResult("DeepSeek 一次调用", "FAIL", f"调用异常: {exc!r}")
    content = reply.content
    if not content:
        return CheckResult("DeepSeek 一次调用", "FAIL", "模型返回空内容")
    return CheckResult(
        "DeepSeek 一次调用",
        "PASS",
        f"回复 {len(content)} 字符：{str(content)[:40]}…",
    )


# ---------- 3. SqliteSaver 中断恢复 ----------


class CheckpointState(TypedDict):
    question: str
    answer: str
    final: str


def ask_question(state: CheckpointState) -> dict:
    """中断点：模拟等待用户输入（如转人工确认）。"""
    answer = interrupt("需要用户输入")
    return {"answer": answer}


def finish(state: CheckpointState) -> dict:
    return {"final": f"完成：{state['answer']}"}


def build_checkpoint_graph(db_path: Path):
    conn = sqlite3.connect(db_path, check_same_thread=False)
    graph = (
        StateGraph(CheckpointState)
        .add_node(ask_question)
        .add_node(finish)
        .add_edge(START, "ask_question")
        .add_edge("ask_question", "finish")
        .add_edge("finish", END)
        .compile(checkpointer=SqliteSaver(conn))
    )
    return conn, graph


def check_sqlite_resume() -> CheckResult:
    db_dir = Path(tempfile.mkdtemp(prefix="campusdesk_env_"))
    db_path = db_dir / "checkpoint.db"

    # 第一次运行：跑到 interrupt 暂停，检查点落库
    conn1, graph1 = build_checkpoint_graph(db_path)
    config = {"configurable": {"thread_id": "env-verify-1"}}
    graph1.invoke({"question": "测试"}, config)
    paused = graph1.get_state(config)
    # langgraph 1.x 语义：interrupt 是节点内暂停点，中断时 next 指向当前节点，
    # 恢复时该节点重入（从 interrupt 处继续）。
    if paused.next != ("ask_question",):
        return CheckResult(
            "SqliteSaver 中断恢复",
            "FAIL",
            f"中断后应停在 ask_question 节点，实际 next={paused.next}",
        )
    conn1.close()  # 模拟进程退出

    # 第二次运行：新连接（模拟进程重启），从落库的检查点恢复
    conn2, graph2 = build_checkpoint_graph(db_path)
    graph2.invoke(Command(resume="已恢复"), config)
    final_state = graph2.get_state(config)
    conn2.close()
    if final_state.values.get("final") != "完成：已恢复":
        return CheckResult(
            "SqliteSaver 中断恢复",
            "FAIL",
            f"恢复后状态不完整: {final_state.values}",
        )
    return CheckResult(
        "SqliteSaver 中断恢复",
        "PASS",
        "中断暂停 → 落库 → 新连接恢复，状态完整（会话记忆地基可用）",
    )
