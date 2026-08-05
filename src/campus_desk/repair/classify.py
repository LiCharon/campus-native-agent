"""报修分类定级（M3，需求 §4）：确定性规则优先 + LLM 辅助解析 + 置信门控。

三层防线（与 intent.py 同模板）：
1. 规则层（确定性）：类别关键词计分 + P1 安全规则（漏水/断电/漏电等）——
   P1 规则命中直接定级（安全规则不被 LLM 推翻）
2. LLM 辅助解析：自写 prompt（含 json）+ response_format=json_object +
   pydantic 校验 + 重试 1 次（DeepSeek thinking 模式兼容模板，禁止
   with_structured_output——M2 实测全 400）
3. 置信门控：LLM 低置信（<0.7）或规则 P1 → needs_human_confirm（人工确认），
   不硬答
"""

import json
import re
from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, ValidationError

from campus_desk.config import settings

# sentinel：区分"默认构造（用真 LLM）"与"显式禁用 LLM（llm=None，纯规则模式）"
_USE_DEFAULT_LLM = object()

Category = Literal["水电", "网络", "门窗", "设备", "环境", "其他"]
Priority = Literal["P1", "P2", "P3"]

# 类别关键词表（确定性规则层，计分取最高）
# 用词组不用单字（"水"会误命中"风水"、"电"会误命中"电话"——M3 测试抓出）
_CATEGORY_KEYWORDS: dict[Category, list[str]] = {
    "水电": [
        "漏水",
        "停水",
        "水龙头",
        "热水器",
        "水表",
        "电闸",
        "插座",
        "灯",
        "开关",
        "断电",
        "停电",
        "管道",
        "短路",
    ],
    "网络": ["网络", "网线", "wifi", "无线", "宽带", "信号", "连不上", "网速"],
    "门窗": ["门", "窗", "锁", "把手", "玻璃", "合页"],
    "设备": ["空调", "风扇", "洗衣机", "马桶", "床", "桌", "椅", "衣柜", "投影仪", "饮水机"],
    "环境": ["卫生", "蟑螂", "老鼠", "异味", "堵塞", "垃圾"],
    "其他": [],
}

# P1 安全规则：命中即 P1（影响面规则判定，需求 §4；LLM 辅助不推翻）
_P1_KEYWORDS = ["漏水", "漏电", "断电", "停电", "火", "冒烟", "爆裂", "渗水", "水淹", "电火花"]

# LLM 低置信门控阈值（与 entry 门控口径一致）
CONFIRM_THRESHOLD = 0.7

_PRIORITY_CN = {"P1": "紧急", "P2": "普通", "P3": "预约"}


class ClassificationResult(BaseModel):
    """分类定级契约（LLM 结构化输出 + 规则层共用）。"""

    category: Category = Field(description="类别：水电/网络/门窗/设备/环境/其他")
    priority: Priority = Field(description="优先级：P1 紧急 / P2 普通 / P3 预约")
    confidence: float = Field(description="置信度 0-1，不确定给 0.4-0.6", ge=0, le=1)
    needs_human_confirm: bool = Field(
        default=False, description="是否需人工确认（门控层按置信度+安全规则计算，模型无需填）"
    )
    reason: str = Field(default="", description="判定依据一句话")


# 结构化输出 prompt：必须含 "json" 字样（DeepSeek json_object 模式硬性要求）
_CLASSIFY_PROMPT = """你是校园服务台的报修分类定级器。请根据学生的报修描述，输出一个 JSON 对象。

类别定义：
- 水电: 水/电/照明/管道/插座/热水器等设施故障
- 网络: 网络/宽带/wifi/网线/信号故障
- 门窗: 门/窗/锁/玻璃故障
- 设备: 空调/风扇/洗衣机/家具/电器等设备故障
- 环境: 卫生/虫害/异味/堵塞等环境问题
- 其他: 无法归入上述类别

优先级定义：
- P1: 紧急（漏水/漏电/断电/安全隐患，影响面大，需 4 小时内处理）
- P2: 普通（一般故障，48 小时内处理）
- P3: 预约（可预约时间处理）

JSON 格式（严格只输出 JSON，不要任何其他文字）：
{"category": "水电|网络|门窗|设备|环境|其他", "priority": "P1|P2|P3", "confidence": 0到1之间的小数, "reason": "一句话依据"}

注意：
- confidence 表示你对分类定级的把握：确定时 0.8-1.0，不确定时 0.4-0.6
- 涉及漏水/漏电/断电等安全问题时 priority 必须是 P1
"""


def _rule_fallback(description: str) -> ClassificationResult:
    """规则层（确定性）：类别关键词计分取最高；P1 安全规则命中即 P1。"""
    best_category: Category = "其他"
    best_score = 0
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw.lower() in description.lower())
        if score > best_score:
            best_category, best_score = cat, score
    priority: Priority = "P1" if any(kw in description for kw in _P1_KEYWORDS) else "P2"
    return ClassificationResult(
        category=best_category,
        priority=priority,
        confidence=0.9 if best_score > 0 else 0.5,
        needs_human_confirm=priority == "P1" or best_score == 0,
        reason="规则判定（LLM 不可用时兜底）",
    )


class RepairClassifier:
    """报修分类定级器：规则优先 + LLM 辅助 + 门控。"""

    def __init__(self, llm: BaseChatModel | None = _USE_DEFAULT_LLM, max_attempts: int = 2):
        # llm=None 显式禁用（纯规则测试）；不传用默认 DeepSeek
        self.llm = self._default_llm() if llm is _USE_DEFAULT_LLM else llm
        self.max_attempts = max_attempts
        self._last_error = ""

    @staticmethod
    def _default_llm() -> BaseChatModel:
        return ChatOpenAI(
            model=settings.deepseek_model,
            api_key=settings.deepseek_api_key,
            base_url="https://api.deepseek.com",
            temperature=0,
            timeout=30,
            model_kwargs={"response_format": {"type": "json_object"}},
        )

    def classify(
        self, description: str, profile_context: str | None = None
    ) -> ClassificationResult:
        """规则优先 + LLM 辅助 + 门控，保证不抛异常、必有结果。

        profile_context（M4 用户画像，可选）：只拼进 LLM prompt——"又坏了"
        场景让 LLM 看到上次工单摘要；规则层仍用原始 description（防画像
        关键词干扰规则计分）。

        合并规则：规则 P1 覆盖 LLM 定级（安全规则优先）；类别按 LLM 结果
        （规则只做兜底/辅助）；低置信 → needs_human_confirm。
        """
        rule = _rule_fallback(description)
        llm_input = (
            f"{profile_context}\n本次报修描述: {description}" if profile_context else description
        )
        llm_result = self._invoke_llm(llm_input) if self.llm is not None else None

        if llm_result is None:
            return rule

        # 安全规则优先：规则判 P1 则强制 P1（LLM 辅助不推翻安全定级）
        priority = "P1" if rule.priority == "P1" else llm_result.priority
        category = llm_result.category if llm_result.category != "其他" else rule.category
        needs_confirm = llm_result.confidence < CONFIRM_THRESHOLD or priority == "P1"
        return ClassificationResult(
            category=category,
            priority=priority,
            confidence=llm_result.confidence,
            needs_human_confirm=needs_confirm,
            reason=llm_result.reason or rule.reason,
        )

    def _invoke_llm(self, description: str) -> ClassificationResult | None:
        """LLM 辅助解析：失败重试；仍失败返回 None（走规则兜底）。"""
        for _ in range(self.max_attempts):
            try:
                raw = self.llm.invoke([("system", _CLASSIFY_PROMPT), ("human", description)])
            except Exception as exc:  # noqa: BLE001 — 外部调用兜底所有错误（intent.py 同款先例）
                self._last_error = f"LLM 调用异常: {exc!r}"
                continue
            content = raw.content if hasattr(raw, "content") else str(raw)
            try:
                return self._parse_json_content(content)
            except (json.JSONDecodeError, ValidationError) as exc:
                self._last_error = f"结构化输出解析失败: {exc}"
        return None

    @staticmethod
    def _parse_json_content(content: str) -> ClassificationResult:
        """容错提取 JSON（容忍代码块围栏），pydantic 严格校验（intent.py 同款）。"""
        text = content.strip()
        if "```" in text:
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise json.JSONDecodeError("未找到 JSON 对象", content, 0)
        return ClassificationResult.model_validate_json(text[start : end + 1])
