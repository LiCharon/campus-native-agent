"""追问决策器（M1-ZJUT）：检索未命中时决定 ask 追问 / handoff 转人工。

复用 CampusDesk 结构化输出模板（自写 prompt 含 json + json_object + pydantic 校验 + 重试 1 次）。
"""

import json
import re

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, Field, ValidationError

from campus_desk.llm import build_llm

_USE_DEFAULT_LLM = object()

_DECIDE_PROMPT = """你是校园服务台的追问决策器。学生的问题在知识库中没有找到答案，请决定下一步，输出 JSON。

动作：
- ask: 问题缺少关键限定词（校区/楼栋/时间/身份等），追问后可能检索到答案
- handoff: 问题已完整但确实超出知识库范围，转人工

JSON 格式（严格只输出 JSON）：
{"action": "ask|handoff", "questions": ["问题1"], "reply": "给学生的话", "summary": "一句话摘要"}

注意：
- action=ask 时 questions 必填且最多 2 个，追问具体简短
- action=handoff 时 questions 给空数组
"""


class ClarifyDecision(BaseModel):
    action: str = Field(description="ask 追问 / handoff 转人工")
    questions: list[str] = Field(default_factory=list, description="ask 时的追问，最多 2 个")
    reply: str = Field(default="", description="给学生的话")
    summary: str = Field(default="", description="一句话摘要")


class ClarifyDecider:
    """追问决策：LLM 决定；失败兜底 handoff。"""

    def __init__(self, llm: BaseChatModel | None = _USE_DEFAULT_LLM, max_attempts: int = 2):
        self.llm = self._default_llm() if llm is _USE_DEFAULT_LLM else llm
        self.max_attempts = max_attempts
        self._last_error = ""

    @staticmethod
    def _default_llm() -> BaseChatModel:
        return build_llm()

    def decide(self, history: list[str], user_text: str, missed: bool) -> ClarifyDecision:
        context = (
            f"对话历史:\n{chr(10).join(history[-4:]) or '（无）'}\n学生本轮: {user_text}"
        )
        if self.llm is None:
            return ClarifyDecision(action="handoff", reply="抱歉，暂时无法处理，已为您转人工。")
        for _ in range(self.max_attempts):
            try:
                raw = self.llm.invoke([("system", _DECIDE_PROMPT), ("human", context)])
            except Exception as exc:  # noqa: BLE001 — 外部调用兜底
                self._last_error = f"LLM 调用异常: {exc!r}"
                continue
            content = raw.content if hasattr(raw, "content") else str(raw)
            try:
                return self._parse_json_content(content)
            except (json.JSONDecodeError, ValidationError) as exc:
                self._last_error = f"结构化输出解析失败: {exc}"
        return ClarifyDecision(action="handoff", reply="抱歉，暂时无法处理，已为您转人工。")

    @staticmethod
    def _parse_json_content(content: str) -> ClarifyDecision:
        text = content.strip()
        if "```" in text:
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise json.JSONDecodeError("未找到 JSON 对象", content, 0)
        return ClarifyDecision.model_validate_json(text[start : end + 1])
