"""用户画像落库（M7-ZJUT）：一轮对话后增量 upsert user_profiles。

设计（拍板）：
- 每轮 assistant 落库后同步调用，只处理本轮 user 消息 + 本轮 sources
- building last-write-wins；frequent_categories 每域 +1（轮内去重，天然按轮次计数）
- 独立事务 + 整体异常隔离：画像抽取失败**旁路不阻断**主对话流程
- 本轮不写 last_ticket_summary（LLM 摘要后置里程碑）
"""

import logging

from campus_desk.api.schemas import SourceItem
from campus_desk.db.models import UserProfile
from campus_desk.db.session import SessionFactory
from campus_desk.profile.extract import extract_building, extract_domains, merge_profile

logger = logging.getLogger(__name__)


def update_profile_after_turn(
    session_factory: SessionFactory,
    *,
    user_id: str,
    msg: str,
    sources: list[SourceItem],
) -> None:
    """抽取本轮画像并 upsert；任何异常仅记日志，不向外抛（旁路）。

    调用方（chat 路由）负责按 user.role == "student" 门控。
    """
    try:
        building = extract_building(msg)
        domains = extract_domains(sources)
        with session_factory() as session, session.begin():
            profile = session.get(UserProfile, user_id)
            merged = merge_profile(
                (
                    {
                        "building": profile.building,
                        "frequent_categories": profile.frequent_categories,
                    }
                    if profile is not None
                    else None
                ),
                building,
                domains,
            )
            if profile is None:
                session.add(
                    UserProfile(
                        user_id=user_id,
                        building=merged["building"],
                        frequent_categories=merged["frequent_categories"],
                    )
                )
            elif merged != {
                "building": profile.building,
                "frequent_categories": profile.frequent_categories,
            }:
                profile.building = merged["building"]
                profile.frequent_categories = merged["frequent_categories"]
    except Exception:
        logger.exception("profile update skipped for user %s (non-blocking)", user_id)
