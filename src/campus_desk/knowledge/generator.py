"""生成节点（M17A-T5）：LLM 整合检索条目为自然语言回答 + [n] 引用（补 RAG 的 G）。

职责边界：
- 只负责"检索命中后怎么答"：相关性判断在检索层（阈值过滤），追问/转人工是
  KnowledgeGraph 的职责（retrieve 工具路径本批不接，M17B 删工具时统一）
- 异常原样向调用方上抛，由 KnowledgeGraph.collect 兜底回退模板拼装（可用性不退化）
- LLM 惰性构造 + 模块级缓存（纯文本生成复用 build_tool_llm——不带 json_object 模式）
- system prompt 单源本模块；system 末尾带 UNTRUSTED_INPUT_NOTICE、human 经
  wrap_input 包裹（延续 M15B-⑤ 注入隔离语义，为 M14 多模态留口）
"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from campus_desk import usage
from campus_desk.llm import build_tool_llm
from campus_desk.prompt_guard import UNTRUSTED_INPUT_NOTICE, wrap_input

_GENERATE_PROMPT = (
    "你是校园办事助手。下面是根据用户问题检索到的知识条目（编号 1..n）。"
    "请用简洁中文整合成一段回答；必须基于给定材料，不得编造；"
    "若材料不相关或不足以回答，明确说'未在知识库中找到相关信息'，不要强行作答；"
    "引用用 [n] 标注来源条目；数字、时间、金额、名称等关键事实保持原文表述，不得改写格式。"
) + UNTRUSTED_INPUT_NOTICE

_ANSWER_CLIP = 300  # 单条 answer 截断，防多条目拼爆 token（grilling 2026-09-07 拍板）

_cached_llm: BaseChatModel | None = None
_llm_resolved = False


def _get_llm() -> BaseChatModel:
    """惰性构造并缓存 LLM 实例（无 key 时构造成功、invoke 期失败 → 图层回退模板）。"""
    global _cached_llm, _llm_resolved
    if not _llm_resolved:
        _cached_llm = build_tool_llm()
        _llm_resolved = True
    return _cached_llm


def _reset_llm_cache() -> None:
    """测试用：清模块级缓存，保证注入/计数用例互不污染。"""
    global _cached_llm, _llm_resolved
    _cached_llm, _llm_resolved = None, False


def _material(hits: list[dict]) -> str:
    """hits → `n. [domain/type] question → answer` 编号材料（answer 截断防超 token）。"""
    lines = []
    for i, h in enumerate(hits):
        answer = (h.get("answer") or "")[:_ANSWER_CLIP]
        lines.append(
            f"{i + 1}. [{h.get('domain', '')}/{h.get('type', '')}] "
            f"{h.get('question', '')} → {answer}"
        )
    return "\n".join(lines)


def generate_answer(hits: list[dict], question: str) -> str:
    """整合 hits 为一段自然语言回答；LLM 异常/超时原样上抛（不在此吞）。"""
    llm = _get_llm()
    if llm is None:
        raise RuntimeError("LLM 不可用（未配置），由调用方回退模板")
    human = wrap_input(f"学生问题：{question}\n\n检索到的知识条目：\n{_material(hits)}")
    # M13：标记调用点（ContextVar）；generate 为 M17A 新增调用点
    with usage.call_point(usage.CALL_POINT_GENERATE):
        raw = llm.invoke([("system", _GENERATE_PROMPT), ("human", human)])
    return (raw.content if hasattr(raw, "content") else str(raw)).strip()
