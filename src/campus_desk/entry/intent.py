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

意图枚举：ZJUT 4 类——
- knowledge: 校园知识问答（校历/放假/办事流程/开放时间/联系方式等，知识库可答）
- tool_query: 动态数据查询（空教室/图书馆座位/课表等，需调用查询工具，当前版本暂未开放）
- multi_intent: 一句话包含多个独立问题（如"成绩单怎么打？宿舍什么时候清退？"）
- other: 闲聊/问候/超出校园服务范围
"""

import json
import re
from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, Field, ValidationError

from campus_desk.llm import build_llm

# 4 类意图（与路由分离，见 routes.py）
IntentName = Literal["knowledge", "tool_query", "multi_intent", "other"]


class IntentResult(BaseModel):
    """LLM 结构化输出契约（JSON Schema）。

    intent: 主意图
    confidence: 置信度 0-1（门控阈值 0.7，低于则转人工）
    secondary_intents: 次要意图列表（多意图场景，如"成绩单怎么打？顺便问校历"→ [knowledge]）
    reason: 判定依据（排障/评测可读）
    """

    intent: IntentName = Field(
        description="主意图：knowledge 知识问答 / tool_query 动态数据查询 / multi_intent 多意图 / other 其他"
    )
    confidence: float = Field(description="置信度 0-1，低置信请给 0.4 左右", ge=0, le=1)
    secondary_intents: list[IntentName] = Field(
        default_factory=list, description="次要意图列表（一句话包含多个问题时）"
    )
    reason: str = Field(default="", description="判定依据，一句话说明")
    primary_intent: IntentName | None = Field(
        default=None, description="multi_intent 时的主意图（最重要的那个问题）；其余意图填 None"
    )


# 规则兜底关键词表：意图 → 关键词（确定性，仅 LLM 不可用时使用）
_RULE_KEYWORDS: dict[str, list[str]] = {
    "knowledge": ["校历", "放假", "怎么", "如何", "流程", "办理", "补办", "开放时间",
                  "在哪", "电话", "食堂", "图书馆", "宿舍", "选课", "考试", "学分", "成绩单"],
    "tool_query": ["空教室", "空余教室", "自习室", "座位", "课表", "余量"],
    "multi_intent": ["还有", "另外", "顺便", "以及"],
}

# 兜底固定低置信度：意图只是参考信息，门控层必然转人工
_FALLBACK_CONFIDENCE = 0.5
_FALLBACK_REASON = "LLM 不可用，关键词规则兜底（低置信）"


# 结构化输出 prompt：必须含 "json" 字样（DeepSeek json_object 模式硬性要求，
# 否则 400 "Prompt must contain the word 'json'"）
_STRUCTURED_PROMPT = """你是校园服务台的入口分流器。请判断学生输入的意图，并输出一个 JSON 对象。

意图定义：
- knowledge: 校园知识问答（校历/放假/办事流程/开放时间/联系方式等，可用知识库回答）
- tool_query: 查询动态数据（空教室/图书馆座位等，需调用查询工具）
- multi_intent: 一句话包含多个独立问题（如"成绩单怎么打？宿舍什么时候清退？"）
- other: 闲聊/问候/超出校园服务范围

JSON 格式（严格只输出 JSON，不要任何其他文字）：
{"intent": "knowledge|tool_query|multi_intent|other", "confidence": 0到1之间的小数, "primary_intent": "intent或null", "secondary_intents": ["intent", ...], "reason": "一句话依据"}

注意：
- confidence 表示把握：确定时给 0.8-1.0，不确定时给 0.4-0.6
- 多个问题时 intent 填 multi_intent，primary_intent 填最重要的那个问题的意图，其余填 secondary_intents；单一问题时 primary_intent 填 null
- 问候语/语气词不算意图："你好，顺便问下校历"只算一个知识问题（intent=knowledge），不要因为有个问候语就判 multi_intent 或 other
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
        # 统一构造（llm.py）：response_format=json_object 构造期声明（DeepSeek 铁律）
        return build_llm()

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
        """第三层：关键词规则兜底。multi_intent 强信号优先，其余取分数最高意图，未命中 other；置信度固定低值。

        多意图优先：出现"还有/另外/顺便/以及"即判 multi_intent（不参与单意图分数竞争），
        否则"一卡通怎么补办？顺便问下校历"会被 knowledge 词（怎么/补办/校历）盖过。
        """
        if any(word in user_input for word in _RULE_KEYWORDS["multi_intent"]):
            primary = (
                "tool_query"
                if any(w in user_input for w in _RULE_KEYWORDS["tool_query"])
                else "knowledge"
            )
            return IntentResult(
                intent="multi_intent",
                confidence=_FALLBACK_CONFIDENCE,
                reason=_FALLBACK_REASON,
                primary_intent=primary,  # 低置信必转人工，仅作人工接待参考（设计 §5.1 注）
            )
        best_intent = "other"
        best_score = 0
        for intent, keywords in _RULE_KEYWORDS.items():
            if intent == "multi_intent":
                continue  # 已在上面强信号分支处理
            score = sum(1 for word in keywords if word in user_input)
            if score > best_score:
                best_intent, best_score = intent, score
        return IntentResult(
            intent=best_intent,
            confidence=_FALLBACK_CONFIDENCE,
            reason=_FALLBACK_REASON,
        )
