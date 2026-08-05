"""报修信息采集（M3，用户拍板：固定信息一次性收集 + Agent 只问缺项）。

draft（RepairState["draft"]）字段：
- description 问题描述（必填，首轮即得）
- contact     联系人（必填，可 LLM/用户补）
- building    楼栋（报修必填）
- room        房间号（可选，描述里常有）
- location    投诉对象/位置（投诉类用，如 食堂阿姨；报修类不用）
- rounds      已追问轮数（≤ MAX_ROUNDS，双节点 ping-pong 的计数持久化点）

抽取：LLM 结构化抽取优先 + 规则兜底（楼栋/房间正则，contact 规则无法抽取）。
追问：缺啥问啥一次问齐（不啰嗦，符合"表单收集固定信息"拍板）。
"""

import json
import re

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, Field, ValidationError

from campus_desk.llm import build_llm

MAX_ROUNDS = 2  # 追问 ≤2 轮（需求 §4 已拍板）

# 必填字段（报修类）：description 首轮必有；contact/building 缺则追问
REQUIRED = ("contact", "building")

# 抽取兜底用 sentinel（同 classify.py：None = 显式禁用 LLM）
_USE_DEFAULT_LLM = object()


class DraftExtract(BaseModel):
    """LLM 抽取契约：从学生文本提取固定字段。

    description 默认空串——测试 fake 用空串占位由注入层替换为输入原文；
    真实抽取器必须给非空描述（merge_extract 会兜底）。
    """

    description: str = Field(default="", description="问题描述（原样保留）")
    building: str | None = Field(default=None, description="楼栋，如 3号楼；没有则 null")
    room: str | None = Field(default=None, description="房间号，如 502；没有则 null")
    contact: str | None = Field(default=None, description="联系人姓名；没有则 null")
    location: str | None = Field(
        default=None, description="投诉对象/位置，如 食堂阿姨；没有则 null"
    )


_EXTRACT_PROMPT = """你是校园服务台的报修信息抽取器。从学生的报修描述中抽取固定信息，输出 JSON。

JSON 格式（严格只输出 JSON）：
{"description": "问题描述原样保留", "building": "楼栋如3号楼或null", "room": "房间号如502或null", "contact": "联系人姓名或null", "location": "投诉对象/位置如食堂阿姨或null"}

注意：
- description 保留学生的原始描述，不要改写
- building 形如"3号楼"/"6栋"，只取楼栋名；房间号（3位数字）放 room
- contact 是姓名/学号，无法确定时给 null
- location 是投诉对象/位置（投诉类用，如"食堂阿姨"），报修类没有则给 null
"""


def rule_extract(text: str) -> DraftExtract:
    """规则兜底抽取（确定性）：楼栋/房间正则，contact 无法规则抽取。"""
    building = None
    m = re.search(r"(\d+)号楼|(\d+)栋", text)
    if m:
        building = f"{m.group(1) or m.group(2)}号楼"
    room = None
    m2 = re.search(r"(\d{3})(?:室|房)?", text)
    if m2:
        room = m2.group(1)
    return DraftExtract(
        description=text.strip(), building=building, room=room, contact=None, location=None
    )


class FieldExtractor:
    """字段抽取器：LLM 优先 + 规则兜底（三层防线同款）。"""

    def __init__(self, llm: BaseChatModel | None = _USE_DEFAULT_LLM, max_attempts: int = 2):
        self.llm = self._default_llm() if llm is _USE_DEFAULT_LLM else llm
        self.max_attempts = max_attempts
        self._last_error = ""

    @staticmethod
    def _default_llm() -> BaseChatModel:
        return build_llm()

    def extract(self, text: str) -> DraftExtract:
        """LLM 抽取失败 → 规则兜底（保证不抛、必有结果）。"""
        if self.llm is None:
            return rule_extract(text)
        for _ in range(self.max_attempts):
            try:
                raw = self.llm.invoke([("system", _EXTRACT_PROMPT), ("human", text)])
            except Exception as exc:  # noqa: BLE001 — 外部调用兜底（intent.py 同款先例）
                self._last_error = f"LLM 调用异常: {exc!r}"
                continue
            content = raw.content if hasattr(raw, "content") else str(raw)
            try:
                return self._parse_json_content(content)
            except (json.JSONDecodeError, ValidationError) as exc:
                self._last_error = f"结构化输出解析失败: {exc}"
        return rule_extract(text)

    @staticmethod
    def _parse_json_content(content: str) -> DraftExtract:
        text = content.strip()
        if "```" in text:
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise json.JSONDecodeError("未找到 JSON 对象", content, 0)
        return DraftExtract.model_validate_json(text[start : end + 1])


def merge_extract(draft: dict, ext: DraftExtract) -> dict:
    """合并抽取结果：已填字段不覆盖（学生后面说的不顶掉前面给的）。

    注意：description 只在为空时更新——resume 轮（学生回答"王芳"）的抽取
    是补充信息不是新描述，覆盖主描述会把分类定级输入顶成只言片语
    （M3 测试抓出：description 被覆盖成"王芳"→ 分类误判"其他"）。
    """
    merged = dict(draft)
    if not merged.get("description"):
        merged["description"] = ext.description
    for field in ("building", "room", "contact", "location"):
        value = getattr(ext, field)
        if value and not merged.get(field):
            merged[field] = value
    return merged


def required_missing(draft: dict, required: tuple[str, ...] = REQUIRED) -> list[str]:
    """缺哪些必填字段（报修类 contact/building；投诉类仅 contact）。"""
    return [f for f in required if not draft.get(f)]


def pick_question(missing: list[str], mode: str = "repair") -> str:
    """缺啥问啥，一次问齐（不逐项追问，符合"表单收集固定信息"拍板）。

    投诉类（mode="complaint"）不追问楼栋——投诉必填集只有联系人。
    """
    parts = []
    if "contact" in missing:
        parts.append("您的联系人姓名或学号")
    if "building" in missing and mode != "complaint":
        parts.append("报修位置（楼栋，如 3号楼）")
    return "请补充：" + "、".join(parts) + "。"


def draft_reply_for(draft: dict, classification: dict | None = None) -> str:
    """建单前的首轮引导文案（信息齐全时直接告知下一步）。"""
    return "好的，我来帮您处理报修，正在为您创建工单。"
