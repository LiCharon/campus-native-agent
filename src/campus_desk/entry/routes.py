"""入口分流路由常量。"""
KNOWLEDGE = "knowledge"
TOOL_QUERY = "tool_query"
MULTI_INTENT = "multi_intent"
HUMAN_HANDOFF = "human_handoff"
VALID_ROUTES = {KNOWLEDGE, TOOL_QUERY, MULTI_INTENT, HUMAN_HANDOFF}
