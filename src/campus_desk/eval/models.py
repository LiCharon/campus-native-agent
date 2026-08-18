"""评测集数据模型：对话剧本（scripted）格式。

格式要点（需求 §10 已拍死）：
- 每条用例预写学生的输入，确定性可复现
- 断言点 = 行为不是文本（expected_route 检查分流结果，turns 里补
  工具调用/状态跳转断言，不检查对话字面）
- turns（后续轮次）：M1 只有入口分流，留空；后续里程碑补工具调用轮
"""

from typing import Literal

from pydantic import BaseModel, Field

# ZJUT 4 类意图（与 entry.intent.IntentName / entry.routes 保持一致）
IntentName = Literal["knowledge", "tool_query", "multi_intent", "other"]
Category = IntentName  # 剧本类别 = 意图类别（决定数量校验区间）
IntentLabel = IntentName
RouteLabel = Literal["knowledge", "tool_query", "multi_intent", "human_handoff"]


class ScriptedTurn(BaseModel):
    """后续轮次（M3/M4 填充）：学生应答 + 期望行为断言。"""

    student_reply: str = Field(default="", description="学生对 Agent 上一轮提问的回答")
    expect: list[str] = Field(
        default_factory=list,
        description="期望行为断言（工具调用/状态跳转），如 ['tool:create_ticket', 'status:SUBMITTED']",
    )


class ScriptedCase(BaseModel):
    """一条对话剧本用例。"""

    id: str = Field(description="唯一标识，如 zjut-intent-001")
    category: Category = Field(description="剧本类别（决定数量校验区间）")
    student_input: str = Field(min_length=1, description="学生本轮的输入（M2 首轮即全量输入）")
    intent: IntentLabel = Field(description="人工标注的主意图（ground truth）")
    expected_route: RouteLabel = Field(description="期望分流结果（行为断言）")
    secondary_intents: list[IntentLabel] = Field(
        default_factory=list, description="次要意图（仅 multi_intent 剧本使用）"
    )
    turns: list[ScriptedTurn] = Field(default_factory=list, description="后续轮次（M3/M4 填充）")
    note: str = Field(default="", description="场景说明（评审/面试复盘用）")
    # ---- M2 链路评测扩展（意图剧本不填，可选）----
    primary_intent: IntentLabel | None = Field(
        default=None, description="multi_intent 剧本的主意图（M2）"
    )
    expected_outcome: str | None = Field(
        default=None, description="首轮期望 outcome（answer/ask/handoff/degraded）"
    )
    expected_entry_ids: list[int] = Field(
        default_factory=list, description="知识剧本：期望命中条目 id（⊆ 断言）"
    )
    expected_tool: str | None = Field(default=None, description="工具剧本：期望调用工具名")
    expected_keywords: list[str] = Field(
        default_factory=list, description="期望答案关键词（全部出现在最终回复）"
    )
