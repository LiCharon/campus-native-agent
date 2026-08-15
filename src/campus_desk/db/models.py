"""业务数据模型（M1-ZJUT：4 业务表 + 2 评测表，T2 删 7 张退役表）。

表职责（ZJUT 设计 §4.5 知识库 + 转人工兜底）：
- users             角色与账号（student/staff/it_staff/admin），M6 登录用
- user_profiles     用户长期记忆画像（M4：楼栋/常报类别/上次工单摘要，1:1 users）
- knowledge_entries 知识库条目（FAQ 式，type 驱动组装：info/process/index）
- bad_cases         未解决反馈（转人工兜底沉淀；M3 进化闭环接工作台）

约束要点：
- knowledge_entries.domain 六领域（教务/后勤/图书馆/IT/证件/生活）；
  keywords 逗号分隔，检索计分用（campus_desk.knowledge.search）
- bad_cases.status：PENDING/RESOLVED（M1 转人工写入 PENDING，M3 工作台处理闭环）
- 外键关系不配置 relationship() 对象——ORM 查询用显式 join/id 字段，
  避免 lazy-load 隐式 SQL（工具层短会话，防 N+1/DetachedInstanceError）
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from campus_desk.db.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True
    )  # student-001 / staff-001 / it-001 / admin-001
    name: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16))  # student / staff / it_staff / admin
    student_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dept: Mapped[str | None] = mapped_column(String(64), nullable=True)  # staff 所在部门
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # M6 登录鉴权：pbkdf2 哈希串（格式 pbkdf2_sha256$迭代次数$salt$hash，见 security.py）；
    # nullable 兼容存量行，种子负责回填
    password_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)


class UserProfile(Base):
    """用户长期记忆画像（M4，需求 §7）：随工单提交/关闭更新，分类定级前注入。

    1:1 users；无画像行 = 新用户（首次报修，正常流程不注入）。
    """

    __tablename__ = "user_profiles"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    building: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 常驻楼栋
    frequent_categories: Mapped[str] = mapped_column(
        String(128), default=""
    )  # 逗号分隔，按报修次数排序（"又坏了"关联用）
    last_ticket_summary: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # 上次工单摘要（描述+类别+状态）
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class KnowledgeEntry(Base):
    """知识库条目（M1-ZJUT）：FAQ 式，type 驱动组装（设计 §4.5）。"""

    __tablename__ = "knowledge_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(16), default="", index=True)  # 教务/后勤/图书馆/IT/证件/生活
    keywords: Mapped[str] = mapped_column(String(128))  # 逗号分隔，检索计分用
    question: Mapped[str] = mapped_column(String(256))
    type: Mapped[str] = mapped_column(String(8), default="info")  # info/process/index
    answer: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class BadCase(Base):
    """未解决反馈（M1 转人工兜底沉淀；M3 进化闭环接工作台）。"""

    __tablename__ = "bad_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    question: Mapped[str] = mapped_column(Text)
    reply: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(8), default="PENDING", index=True)  # PENDING/RESOLVED
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
