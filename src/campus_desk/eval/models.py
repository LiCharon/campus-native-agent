"""评测集数据模型：对话剧本（scripted）格式。

格式要点（需求 §10 已拍死）：
- 每条用例预写学生的输入，确定性可复现
- 断言点 = 行为不是文本（expected_route 检查分流结果，turns 里 M3/M4 补
  工具调用/状态跳转断言，不检查对话字面）
- turns（后续轮次）：M2 只有入口分流，留空；M3/M4 补 Repair/Consult 剧本
"""

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["repair", "consult", "complaint", "chitchat", "multi_intent", "repeat_repair"]
IntentLabel = Literal["repair", "consult", "complaint", "other"]
RouteLabel = Literal["repair", "consult", "complaint", "human_handoff"]


class ScriptedTurn(BaseModel):
    """后续轮次（M3/M4 填充）：学生应答 + 期望行为断言。"""

    student_reply: str = Field(default="", description="学生对 Agent 上一轮提问的回答")
    expect: list[str] = Field(
        default_factory=list,
        description="期望行为断言（工具调用/状态跳转），如 ['tool:create_ticket', 'status:SUBMITTED']",
    )


class ScriptedCase(BaseModel):
    """一条对话剧本用例。"""

    id: str = Field(description="唯一标识，如 repair-001")
    category: Category = Field(description="剧本类别（决定数量校验区间）")
    student_input: str = Field(min_length=1, description="学生本轮的输入（M2 首轮即全量输入）")
    intent: IntentLabel = Field(description="人工标注的主意图（ground truth）")
    expected_route: RouteLabel = Field(description="期望分流结果（行为断言）")
    secondary_intents: list[IntentLabel] = Field(
        default_factory=list, description="次要意图（仅 multi_intent 剧本使用）"
    )
    turns: list[ScriptedTurn] = Field(default_factory=list, description="后续轮次（M3/M4 填充）")
    note: str = Field(default="", description="场景说明（评审/面试复盘用）")
