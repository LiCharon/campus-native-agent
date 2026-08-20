"""用户画像抽取纯函数（M7-ZJUT）：楼栋正则 + sources 领域解析 + 计数合并 + 文本格式化。

纯确定性（拍板）：不调 LLM，building 用正则、类别用知识命中的 domain 计数，
供 upsert 落库与 orchestrator 注入复用。所有函数无副作用，可独立单测。

⚠️ sources 解析与 api/routes/chat.py::_build_sources 的 detail 格式强耦合
（kb 来源 detail="X型 · 域"，" · " 分隔取末段）——改动该格式需同步本文件。
"""

import re

from campus_desk.api.schemas import SourceItem

# 楼栋正则：query/field_extract._BUILDING_PATTERN（r"(\d)\s*号?楼"）的放宽版——
# 数字后与"号"后均允许空格（兼容 "3号楼" / "3 号楼" / "3 号 楼"）；仅抽首个命中
_PROFILE_BUILDING_PATTERN = re.compile(r"(\d)\s*号?\s*楼")

# 工具查询类消息没有知识 domain，统一归入此虚拟类别（计数不区分具体工具）
_TOOL_CATEGORY = "工具查询"

# frequent_categories String(128) 长度保护（merge 截断用）
_MAX_CATEGORIES_LEN = 128


def extract_building(text: str) -> str | None:
    """从消息文本抽常驻楼栋，返回规范形（如 "1号楼"）；无则 None。

    数字 + 可选"号" + "楼"，数字与号/楼之间允许空格，仅抽首个命中。
    """
    m = _PROFILE_BUILDING_PATTERN.search(text or "")
    return f"{m.group(1)}号楼" if m else None


def extract_domains(sources: list[SourceItem]) -> list[str]:
    """从一轮对话的来源 chip 列表解析类别：kb 取 detail 末段 domain，tool 归"工具查询"。

    脏数据（空 detail / 分隔符缺失）跳过，不抛异常。
    """
    domains: list[str] = []
    for s in sources:
        if s.type == "kb":
            # detail 形如 "info型 · 教务"（见 _build_sources），取 " · " 后段
            if " · " in (s.detail or ""):
                domain = s.detail.split(" · ")[-1].strip()
                if domain:
                    domains.append(domain)
        elif s.type == "tool":
            domains.append(_TOOL_CATEGORY)
    return domains


def _parse_categories(text: str) -> dict[str, int]:
    """解析 "域:次数,域:次数" 字符串为计数 dict；脏数据跳过。"""
    counts: dict[str, int] = {}
    if not text:
        return counts
    for item in text.split(","):
        item = item.strip()
        if not item or ":" not in item:
            continue
        domain, _, raw = item.rpartition(":")
        if not domain:
            continue
        try:
            counts[domain.strip()] = int(raw)
        except ValueError:
            counts[domain.strip()] = 1
    return counts


def _serialize_categories(counts: dict[str, int]) -> str:
    """计数 dict → "域:次数" 逗号分隔，按次数降序（平局按域名稳定序）；128 截断。"""
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    text = ",".join(f"{domain}:{count}" for domain, count in ordered)
    if len(text) > _MAX_CATEGORIES_LEN:
        # 截断在最接近的逗号边界，保证仍是合法格式
        text = text[:_MAX_CATEGORIES_LEN].rsplit(",", 1)[0]
    return text


def merge_profile(
    existing: dict | None, building: str | None, domains: list[str]
) -> dict:
    """合并一轮抽取结果到画像 dict（可单测，不依赖 ORM）。

    - building：last-write-wins，有值覆盖
    - frequent_categories：每域 +1，轮内去重（同一轮重复命中同域只计 1 次）
    - 无任何变化时返回原 dict（id 不变，upsert 可跳过写库）
    """
    old_building = (existing or {}).get("building")
    old_categories = (existing or {}).get("frequent_categories") or ""

    new_building = building if building is not None else old_building

    counts = _parse_categories(old_categories)
    changed = False
    for domain in set(domains):
        if domain:
            counts[domain] = counts.get(domain, 0) + 1
            changed = True
    new_categories = _serialize_categories(counts)

    if new_building == old_building and not changed:
        return existing if existing is not None else {}

    return {"building": new_building, "frequent_categories": new_categories}


def format_profile_text(profile: dict | None) -> str:
    """画像 dict → 注入 prompt 的文本段；无画像/全空返回 ""（注入方判断有值才拼）。"""
    if not profile:
        return ""
    parts = []
    if profile.get("building"):
        parts.append(f"常驻楼栋 {profile['building']}")
    counts = _parse_categories(profile.get("frequent_categories") or "")
    if counts:
        domains = "、".join(f"{d}({c}次)" for d, c in sorted(
            counts.items(), key=lambda kv: (-kv[1], kv[0])
        ))
        parts.append(f"常问领域：{domains}")
    return "；".join(parts)
