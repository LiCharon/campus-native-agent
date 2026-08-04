"""意图识别：LLM 结构化输出 + 三层防线（NFR §9 已拍板）。

三层防线：
1. 主路径：自写 prompt（含 json 字样）+ response_format=json_object + pydantic 校验
   —— 不走 langchain with_structured_output（实测 2026-08-04：
   deepseek-v4-flash thinking 模式与三种 method 全不兼容：json_schema 400
   "response_format type unavailable"、json_mode 400 "prompt must contain json"、
   function_calling 400 "thinking mode does not support tool_choice"）。
2. 解析失败 / LLM 异常 → 重试 1 次（max_attempts 可配，默认 2 次调用）
3. 仍失败 → 关键词规则兜底（确定性，置信度固定 0.5 低值 → 门控必然转人工，
   兜底意图只作为人工接待的参考信息，不硬答）

LLM 可注入（测试用 fake）；默认构造 DeepSeek 实例（配置见 config.py）。
"""

import json
import re
from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, ValidationError

from campus_desk.config import settings

# 4 类意图（与路由分离，见 routes.py）
IntentName = Literal["repair", "consult", "complaint", "other"]


class IntentResult(BaseModel):
    """LLM 结构化输出契约（JSON Schema）。

    intent: 主意图
    confidence: 置信度 0-1（门控阈值 0.7，低于则转人工）
    secondary_intents: 次要意图列表（多意图场景，如"灯坏了顺便问密码"→ [consult]）
    reason: 判定依据（排障/评测可读）
    """

    intent: IntentName = Field(
        description="主意图：repair 报修 / consult 咨询 / complaint 投诉 / other 其他"
    )
    confidence: float = Field(description="置信度 0-1，低置信请给 0.4 左右", ge=0, le=1)
    secondary_intents: list[IntentName] = Field(
        default_factory=list, description="次要意图列表（一句话包含多个问题时）"
    )
    reason: str = Field(default="", description="判定依据，一句话说明")


# 规则兜底关键词表：意图 → 关键词（确定性，仅 LLM 不可用时使用）
_RULE_KEYWORDS: dict[str, list[str]] = {
    "repair": ["坏", "漏水", "断电", "不亮", "报修", "堵", "修", "故障"],
    "consult": ["密码", "账号", "网络", "怎么", "如何", "查询", "邮箱", "认证", "连不上"],
    "complaint": ["投诉", "态度", "不满", "举报", "差评", "太差"],
}

# 兜底固定低置信度：意图只是参考信息，门控层必然转人工
_FALLBACK_CONFIDENCE = 0.5
_FALLBACK_REASON = "LLM 不可用，关键词规则兜底（低置信）"


# 结构化输出 prompt：必须含 "json" 字样（DeepSeek json_object 模式硬性要求，
# 否则 400 "Prompt must contain the word 'json'"）
_STRUCTURED_PROMPT = """你是校园服务台的入口分流器。请判断学生输入的意图，并输出一个 JSON 对象。

意图定义：
- repair: 报修（灯/水/电/门/窗/空调/家具等设施故障，请求维修）
- consult: 咨询（网络/账号/密码/邮箱/教务等问答）
- complaint: 投诉（服务态度/收费/卫生/噪音等不满）
- other: 其他（闲聊/问候/课业求助等学习问题/不在服务范围）

JSON 格式（严格只输出 JSON，不要任何其他文字）：
{"intent": "repair|consult|complaint|other", "confidence": 0到1之间的小数, "secondary_intents": ["intent", ...], "reason": "一句话依据"}

注意：
- confidence 表示你对判断的把握：确定时给 0.8-1.0，不确定时给 0.4-0.6
- 一句话包含多个问题（如"灯坏了顺便问密码"）时，主意图填最重要的，其余填 secondary_intents
"""


class IntentClassifier:
    """意图识别器：三层防线，返回 IntentResult。"""

    def __init__(
        self,
        llm: BaseChatModel | None = None,
        max_attempts: int = 2,
    ):
        self.llm = llm if llm is not None else self._default_llm()
        self.max_attempts = max_attempts
        self._last_error = ""  # 最近一次失败原因（排障用）

    @staticmethod
    def _default_llm() -> BaseChatModel:
        # response_format=json_object：DeepSeek 官方支持的结构化输出（OpenAI 兼容参数）
        return ChatOpenAI(
            model=settings.deepseek_model,
            api_key=settings.deepseek_api_key,
            base_url="https://api.deepseek.com",
            temperature=0,  # 分类任务确定性优先
            timeout=30,
            model_kwargs={"response_format": {"type": "json_object"}},
        )

    def classify(self, user_input: str) -> IntentResult:
        """三层防线：结构化输出 → 重试 → 规则兜底。保证不抛异常、必有结果。"""
        for _ in range(self.max_attempts):
            result = self._invoke_structured(user_input)
            if result is not None:
                return result
        return self._rule_fallback(user_input)

    def _invoke_structured(self, user_input: str) -> IntentResult | None:
        """第一/二层：调 LLM 并解析 JSON；失败返回 None（不抛）。"""
        try:
            raw = self.llm.invoke([("system", _STRUCTURED_PROMPT), ("human", user_input)])
        except Exception as exc:  # noqa: BLE001 — 外部调用需兜底所有错误（env_check 同款先例）
            self._last_error = f"LLM 调用异常: {exc!r}"
            return None
        content = raw.content if hasattr(raw, "content") else str(raw)
        try:
            parsed = self._parse_json_content(content)
        except (json.JSONDecodeError, ValidationError) as exc:
            self._last_error = f"结构化输出解析失败: {exc}"
            return None
        return parsed

    @staticmethod
    def _parse_json_content(content: str) -> IntentResult:
        """容错提取 JSON：容忍模型输出前后缀/代码块围栏，pydantic 严格校验。"""
        text = content.strip()
        if "```" in text:  # 容忍 ```json ... ``` 代码块
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise json.JSONDecodeError("未找到 JSON 对象", content, 0)
        return IntentResult.model_validate_json(text[start : end + 1])

    def _rule_fallback(self, user_input: str) -> IntentResult:
        """第三层：关键词规则兜底。命中取分数最高意图，未命中 other；置信度固定低值。"""
        best_intent: str = "other"
        best_score = 0
        for intent, keywords in _RULE_KEYWORDS.items():
            score = sum(1 for word in keywords if word in user_input)
            if score > best_score:
                best_intent, best_score = intent, score
        return IntentResult(
            intent=best_intent,
            confidence=_FALLBACK_CONFIDENCE,
            reason=_FALLBACK_REASON,
        )
