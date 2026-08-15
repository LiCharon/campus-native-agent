"""每轮编排（M1 临时最小版）：Entry 分流 → 直接返回 entry 图结果。

ZJUT Native Agent 演进中（M1-T1）：Repair/Consult/Quality/Complaint 下游
Agent 图已退役，本文件由后续任务（T7）完整重写。当前只保证：
- import 不炸（不再引用退役模块）
- turn 签名稳定（下游图参数保留占位，旧调用方按位置传参不受影响）
"""


def turn(
    entry_graph,
    repair_graph,
    consult_graph,
    thread_id: str,
    msg: str,
    *,
    quality_graph=None,
    user_id: str | None = None,
    session_factory=None,
    complaint_graph=None,
) -> dict:
    """一轮对话（M1 临时版）：仅 Entry 分流，返回 entry 图的 route/reply。

    下游图参数（repair/consult/quality/complaint）与 thread_id/user_id 保留
    占位，暂不参与逻辑——T7 重写时恢复多 Agent 编排。
    """
    entry_out = entry_graph.invoke({"user_input": msg})
    return {
        "reply": entry_out.get("reply", ""),
        "route": entry_out.get("route", ""),
    }
