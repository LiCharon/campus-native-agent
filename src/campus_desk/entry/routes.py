"""入口分流路由常量。

意图（intent）与路由（route）分离：
- intent：LLM 识别的 4 类语义（repair/consult/complaint/other）
- route：分流决策（REPAIR/CONSULT/COMPLAINT/HUMAN_HANDOFF）
「other 意图」与「低置信」都汇聚到 HUMAN_HANDOFF（需求 §2：其他/低置信度 → 兜底转人工）。
"""

# 路由（分流决策）
REPAIR = "repair"
CONSULT = "consult"
COMPLAINT = "complaint"
HUMAN_HANDOFF = "human_handoff"

VALID_ROUTES = {REPAIR, CONSULT, COMPLAINT, HUMAN_HANDOFF}
