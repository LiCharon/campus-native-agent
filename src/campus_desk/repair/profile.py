"""用户画像（M4，需求 §7）：随工单提交更新，RepairAgent 分类定级前注入。

画像内容：楼栋（常驻）/ 常报问题类别（按次数排序）/ 上次工单摘要。
- 注入时机已拍死 = 分类定级前：LLM 分类时能看到"上次修过什么"，
  "又坏了"自然关联上次工单（repeat_repair-002 评测场景）
- 规则层不受画像影响：画像上下文只进 LLM prompt（graph.classify 组装），
  规则层仍用原始 description——防画像关键词干扰规则计分
- 无画像行 = 新用户：get_profile 返回 None，正常流程不注入
"""

from datetime import UTC, datetime

from campus_desk.db.models import UserProfile
from campus_desk.db.session import SessionFactory

# 常报类别保留数量（"又坏了"关联 + finalize"上次同类问题"提示用）
_TOP_CATEGORIES = 3
# 摘要截断长度（last_ticket_summary 只存要点，不进全量描述）
_SUMMARY_LEN = 80

_ProfileDict = dict[str, str | None]  # building / frequent_categories / last_ticket_summary


def _now() -> datetime:
    return datetime.now(UTC)


def profile_text(profile: _ProfileDict) -> str | None:
    """画像 → LLM prompt 注入文本（无画像内容返回 None，不拼空壳）。"""
    parts = []
    if profile.get("building"):
        parts.append(f"学生常驻楼栋: {profile['building']}")
    if profile.get("frequent_categories"):
        parts.append(f"常报问题类别: {profile['frequent_categories']}")
    if profile.get("last_ticket_summary"):
        parts.append(f"上次报修: {profile['last_ticket_summary']}")
    if not parts:
        return None
    return "学生历史报修画像（供参考，报修描述以本次为准）: " + "；".join(parts)


class ProfileStore:
    """画像读写（session_factory 注入，与工具层同款依赖注入模式）。"""

    def __init__(self, session_factory: SessionFactory):
        self.session_factory = session_factory

    def get_profile(self, user_id: str) -> _ProfileDict | None:
        """读画像。无画像行（新用户）返回 None。"""
        with self.session_factory() as session, session.begin():
            profile = session.get(UserProfile, user_id)
            if profile is None:
                return None
            return {
                "building": profile.building,
                "frequent_categories": profile.frequent_categories or "",
                "last_ticket_summary": profile.last_ticket_summary,
            }

    def update_profile(
        self,
        user_id: str,
        *,
        building: str | None = None,
        category: str = "",
        description: str = "",
    ) -> None:
        """建单成功后更新画像（需求 §7：随工单提交更新）。

        - building 非空才覆盖（首次报修没写楼栋时保留空）
        - frequent_categories：追加本次类别，按出现次数排序取前 _TOP_CATEGORIES
          （"又坏了"关联 + finalize"上次同类问题"判断的数据源）
        - last_ticket_summary：本次工单摘要（描述截断 + 类别 + 日期）
        """
        with self.session_factory() as session, session.begin():
            profile = session.get(UserProfile, user_id)
            if profile is None:
                profile = UserProfile(user_id=user_id)
                session.add(profile)
            if building:
                profile.building = building
            if category and category != "其他":
                counts: dict[str, int] = {}
                for cat in (profile.frequent_categories or "").split(","):
                    if cat:
                        counts[cat] = counts.get(cat, 0) + 1
                counts[category] = counts.get(category, 0) + 1
                top = sorted(counts, key=counts.get, reverse=True)[:_TOP_CATEGORIES]
                profile.frequent_categories = ",".join(top)
            profile.last_ticket_summary = (
                f"{description[:_SUMMARY_LEN]}（{category or '未分类'}，{_now():%m-%d}）"
            )


def same_category_as_before(profile: _ProfileDict | None, category: str) -> bool:
    """finalize 提示判定：上次也报修过同类问题（常报类别含本次类别）。"""
    if not profile or not category:
        return False
    cats = (profile.get("frequent_categories") or "").split(",")
    return category in cats
