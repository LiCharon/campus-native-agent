"""咨询决策器（M4，需求 §6 ConsultAgent 诊断式）：LLM 每轮输出动作决策。

动作契约：
- ask    追问（每轮 ≤2 问，总轮次 ≤8——硬约束在 graph.act 层执行）
- tool   调咨询工具（query_account_status/query_announcement/search_faq）
- answer 直接给出解决步骤/FAQ 答案（自助解决）
- handoff 转人工（打包信息 = 对话摘要 + 已排查步骤 + 初步判断，需求 §6）

结构化输出：复用 M2 模板（自写 prompt 含 "json" + response_format=json_object +
pydantic 校验 + 重试 1 次；DeepSeek 不支持 with_structured_output，实测全 400）。
"""

import json
import re

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, Field, ValidationError

from campus_desk.llm import build_llm

# sentinel：区分"默认构造（用真 LLM）"与"显式禁用 LLM（llm=None，规则兜底）"
_USE_DEFAULT_LLM = object()

MAX_ASK_ROUNDS = 8  # 总追问轮次上限（需求 §6：≤8 轮）
MAX_QUESTIONS_PER_ROUND = 2  # 每轮 ≤2 问
MAX_TOOL_CHAIN = 3  # 连续工具轮上限（防"不停调工具"死循环）

_TOOL_DESC = {
    "query_account_status": "查学生网络账号状态（正常/欠费/过期）。参数 student_no: 学号",
    "query_announcement": "查区域故障公告。参数 region: 区域（楼栋名或全校）",
    "search_faq": "检索常见问题。参数 keyword: 关键词（如 密码/网速/选课）",
}


class ConsultDecision(BaseModel):
    """LLM 每轮决策契约（graph.act 按 action 分支执行）。"""

    action: str = Field(description="ask 追问 / tool 调工具 / answer 直接回答 / handoff 转人工")
    questions: list[str] = Field(
        default_factory=list, description="ask 时的追问，每轮最多 2 个问题"
    )
    tool: str | None = Field(default=None, description="tool 时要调用的工具名（其他动作给 null）")
    tool_args: dict = Field(default_factory=dict, description="tool 时工具参数（其他动作给空对象）")
    reply: str = Field(default="", description="本轮给学生的话（答案/问题/说明）")
    summary: str = Field(default="", description="本轮对话一句话摘要（进 history 供后续决策）")


def _tool_hint() -> str:
    return "\n".join(f"- {name}: {desc}" for name, desc in _TOOL_DESC.items())


def build_decide_prompt(student_no: str | None) -> str:
    """决策系统 prompt（每次调用组装，含工具说明与硬约束）。"""
    student_line = (
        f"当前学生学号: {student_no}（工具需要时可直接用）"
        if student_no
        else "学生学号未知（需要时向学生询问）"
    )
    return f"""你是校园服务台的 IT 咨询顾问。通过追问和工具排查帮学生解决问题。{student_line}

可用工具：
{_tool_hint()}

规则：
- 每轮最多问 {MAX_QUESTIONS_PER_ROUND} 个问题，追问总计不超过 {MAX_ASK_ROUNDS} 轮
- 能直接回答（FAQ/常识/明确流程）→ action=answer，给出具体可执行步骤
- 信息不足 → action=ask，一次问齐（具体、简短）
- 需要查证（账号状态/故障公告/常见问题）→ action=tool，先调工具，用结果回答
- 解决不了 / 学生明确要求 / 问题超出范围 → action=handoff
- 上一轮工具结果会以"工具结果: ..."给出，参考它继续决策（别重复调同参数工具）
- reply 是对学生说的话（中文，自然友好）；summary 是给系统看的一句话摘要

JSON 格式（严格只输出 JSON，不要任何其他文字）：
{{"action": "ask|tool|answer|handoff", "questions": ["问题1", "问题2"], "tool": "工具名或null", "tool_args": {{"参数名": 值}}, "reply": "本轮给学生的话", "summary": "一句话摘要"}}

注意：
- action=tool 时必须填 tool 和 tool_args，questions 给空数组
- action=ask 时 questions 必填且最多 {MAX_QUESTIONS_PER_ROUND} 个，reply 可简要重复
- action=answer/handoff 时 questions/tool/tool_args 给空值
"""


def build_history_text(history: list[str]) -> str:
    """history → prompt 文本（最近 8 条，防上下文膨胀；需求 §7 会话压缩）。"""
    return "\n".join(history[-8:]) if history else "（无历史）"


class ConsultDecider:
    """LLM 决策器：失败重试；仍失败返回 answer 兜底（不让学生等死循环）。"""

    def __init__(self, llm: BaseChatModel | None = _USE_DEFAULT_LLM, max_attempts: int = 2):
        self.llm = self._default_llm() if llm is _USE_DEFAULT_LLM else llm
        self.max_attempts = max_attempts
        self._last_error = ""

    @staticmethod
    def _default_llm() -> BaseChatModel:
        return build_llm()

    def decide(
        self,
        history: list[str],
        user_text: str,
        tool_results: list[str] | None = None,
        student_no: str | None = None,
    ) -> ConsultDecision:
        """LLM 决策。失败兜底 answer（"暂时无法处理，已为您转人工"由 act 层转换）。"""
        prompt = build_decide_prompt(student_no)
        context_parts = [f"对话历史:\n{build_history_text(history)}"]
        if tool_results:
            context_parts.append("工具结果:\n" + "\n".join(tool_results[-3:]))
        context_parts.append(f"学生本轮: {user_text}")
        if self.llm is None:
            return ConsultDecision(action="answer", reply="抱歉，系统繁忙，请稍后再试。")
        for _ in range(self.max_attempts):
            try:
                raw = self.llm.invoke([("system", prompt), ("human", "\n".join(context_parts))])
            except Exception as exc:  # noqa: BLE001 — 外部调用兜底所有错误（intent.py 同款先例）
                self._last_error = f"LLM 调用异常: {exc!r}"
                continue
            content = raw.content if hasattr(raw, "content") else str(raw)
            try:
                return self._parse_json_content(content)
            except (json.JSONDecodeError, ValidationError) as exc:
                self._last_error = f"结构化输出解析失败: {exc}"
        return ConsultDecision(action="answer", reply="抱歉，暂时无法处理，已为您转人工，请稍候。")

    @staticmethod
    def _parse_json_content(content: str) -> ConsultDecision:
        """容错提取 JSON（容忍代码块围栏），pydantic 严格校验（intent.py 同款）。"""
        text = content.strip()
        if "```" in text:
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise json.JSONDecodeError("未找到 JSON 对象", content, 0)
        return ConsultDecision.model_validate_json(text[start : end + 1])
