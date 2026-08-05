"""Langfuse 埋点冒烟（M5-T3）：构建 entry/repair/consult 图跑一轮 orchestrator.turn。

用法（需 .env 配置 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / DATABASE_URL）：
    .venv/Scripts/python scripts/smoke_langfuse.py

预期：一轮报修对话（"3号楼502灯坏了，李华"）产生的 trace 层次：
orchestrator.turn → agent.repair → LLM call（intent/classify/drafting）→
tool.create_ticket → transition.submitted→assigned 等 span。
无 key 时静默 no-op（埋点零影响验证）。
"""

from langgraph.checkpoint.memory import InMemorySaver

from campus_desk import telemetry
from campus_desk.config import settings
from campus_desk.consult.graph import build_consult_graph
from campus_desk.db.session import default_session_factory
from campus_desk.entry.entry_graph import build_entry_graph
from campus_desk.entry.orchestrator import turn
from campus_desk.repair.graph import build_repair_graph


def main() -> None:
    if not telemetry.enabled():
        print("SKIP: 未配置 LANGFUSE_PUBLIC_KEY/SECRET_KEY（.env 填写后重跑）")
        return
    if not settings.database_url:
        print("SKIP: 未配置 DATABASE_URL（.env 填写后重跑）")
        return

    factory = default_session_factory()
    out = turn(
        build_entry_graph(),
        build_repair_graph(factory, checkpointer=InMemorySaver()),
        build_consult_graph(factory, checkpointer=InMemorySaver()),
        "smoke-001",
        "3号楼502灯坏了，李华",
        user_id="student-001",
        session_factory=factory,
    )
    telemetry.flush()
    print(f"回复: {out.get('reply', '')}")
    print("请到 Langfuse UI 查看 trace（session_id=smoke-001, tags: campusdesk-m5）")


if __name__ == "__main__":
    main()
