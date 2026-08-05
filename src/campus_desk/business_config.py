"""业务参数配置（M6 可配化）：关键词表/派单映射从 JSON 加载，模块级冻结导出。

背景：M3 起类别关键词表（repair/classify.py）与派单映射（repair/graph.py）
写死在代码里，加"空调/电梯"类目要改代码。M6 移入 config/business_rules.json
（settings.business_config_path 可覆盖路径）——改配置不改代码。

行为不变底线（三重守护）：
1. 关键词/映射内容与旧代码逐字一致（迁移时原样搬入）
2. classify/graph 模块级重导出本模块常量，调用面零改动
3. tests/test_business_config.py 断言配置值与旧代码快照逐项相等——
   今后任何人改 JSON 必须连测试一起改，语义漂移立刻红

设计约束：本模块不 import repair.classify（classify → business_config 单向依赖，
避免循环 import）；ALLOWED_CATEGORIES 独立定义，与 classify.Category Literal
的一致性由 parity 测试断言。
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from campus_desk.config import settings

# 合法类别集合（与 repair/classify.py 的 Category Literal 语义一致，parity 测试锁死）
ALLOWED_CATEGORIES = ("水电", "网络", "门窗", "设备", "环境", "其他")


class _DeptTrade(BaseModel):
    """派单映射项：部门 + 工种（均可空——"其他"类无映射，走兜底部门）。"""

    dept: str | None = None
    trade: str | None = None


class BusinessRules(BaseModel):
    """业务参数 schema：与 config/business_rules.json 一一对应。"""

    categories: dict[str, str] = Field(description="类别 → LLM prompt 定义文案")
    category_keywords: dict[str, list[str]] = Field(description="类别 → 关键词表（规则层计分）")
    p1_keywords: list[str] = Field(description="P1 安全关键词（命中即 P1，LLM 不推翻）")
    confirm_threshold: float = 0.7
    category_dept_trade: dict[str, _DeptTrade] = Field(description="类别 → (部门, 工种)")
    confirm_words: list[str] = Field(description="确认词（否定词优先判定）")
    deny_words: list[str] = Field(description="否定词")
    fallback_dept: str = "后勤"


def load_rules(path: str | Path) -> BusinessRules:
    """加载并校验业务配置；缺失/坏 JSON/非法类别键 → RuntimeError 快速失败。

    快速失败语义：配置是规则层与 LLM prompt 的数据源，坏配置静默退化
    会让分类定级无规则兜底——宁可启动即报错。
    """
    p = Path(path)
    if not p.exists():
        raise RuntimeError(f"业务配置缺失: {p}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        rules = BusinessRules.model_validate(raw)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise RuntimeError(f"业务配置非法: {p}: {exc}") from exc
    for field in ("categories", "category_keywords", "category_dept_trade"):
        illegal = set(getattr(rules, field)) - set(ALLOWED_CATEGORIES)
        if illegal:
            raise RuntimeError(f"业务配置 {field} 含非法类别键: {sorted(illegal)}")
    return rules


_rules = load_rules(settings.business_config_path)

# —— 模块级冻结导出（classify/graph 重导出用；tuple 只读防运行期误改）——
CATEGORIES: tuple[str, ...] = tuple(_rules.categories)
CATEGORY_DEFINITIONS: dict[str, str] = dict(_rules.categories)
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    k: tuple(v) for k, v in _rules.category_keywords.items()
}
P1_KEYWORDS: tuple[str, ...] = tuple(_rules.p1_keywords)
CONFIRM_THRESHOLD: float = _rules.confirm_threshold
CATEGORY_DEPT_TRADE: dict[str, tuple[str | None, str | None]] = {
    k: (v.dept, v.trade) for k, v in _rules.category_dept_trade.items()
}
CONFIRM_WORDS: tuple[str, ...] = tuple(_rules.confirm_words)
DENY_WORDS: tuple[str, ...] = tuple(_rules.deny_words)
FALLBACK_DEPT: str = _rules.fallback_dept
