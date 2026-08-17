"""业务数据模型（M1-ZJUT：4 业务表 + 2 评测表，T2 删 7 张退役表）。

表职责（ZJUT 设计 §4.5 知识库 + §5.5 进化闭环 + M4 权限/审计）：
- users             角色与账号（student/cs_staff/admin 三角色），登录用
- user_profiles     用户长期记忆画像（预留：画像启用时再定，1:1 users）
- knowledge_entries 知识库条目（FAQ 式，type 驱动组装：info/process/index）
- bad_cases         未解决反馈双通道：① M1 转人工自动沉淀 ② M3 对话页"没解决"按钮
- suggestions       用户提议通道（M3）："问题没答案"主动提议，管理员审查采纳/驳回
- audit_logs        审计日志（M4）：登录/审查/接待/用户管理关键操作留痕

约束要点：
- knowledge_entries.domain 六领域（教务/后勤/图书馆/IT/证件/生活）；
  keywords 逗号分隔，检索计分用（campus_desk.knowledge.search）
- bad_cases.status：PENDING/RESOLVED（自动/手动反馈均 PENDING，M3 管理页审查后 RESOLVED）
- suggestions.status：PENDING/ADOPTED/REJECTED（采纳=补入知识库，驳回=不补入）
- users.permissions：附加权限位（逗号分隔），最终权限 = 角色默认 ∪ 附加位（perms.py）
- 外键关系不配置 relationship() 对象——ORM 查询用显式 join/id 字段，
  避免 lazy-load 隐式 SQL（工具层短会话，防 N+1/DetachedInstanceError）
"""

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from campus_desk.db.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True
    )  # student-001 / cs-001 / admin-001
    name: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16))  # student / cs_staff / admin
    student_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dept: Mapped[str | None] = mapped_column(String(64), nullable=True)  # admin 所在部门
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # M6 登录鉴权：pbkdf2 哈希串（格式 pbkdf2_sha256$迭代次数$salt$hash，见 security.py）；
    # nullable 兼容存量行，种子负责回填
    password_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # M4 附加权限位（逗号分隔，如 kb_review,view_stats）；最终权限 = 角色默认 ∪ 附加位
    permissions: Mapped[str] = mapped_column(String(128), default="")
    # M4 账号启用状态：False 时登录拒绝
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AuditLog(Base):
    """审计日志（M4）：关键操作留痕（登录/审查/接待/用户管理）。"""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(32), index=True)  # login/adopt/dismiss/resolve/user_create...
    object_type: Mapped[str] = mapped_column(String(16))  # system/bad_case/suggestion/user/knowledge
    object_id: Mapped[str] = mapped_column(String(32), default="")
    detail: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


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
    # domain 六领域：教务/后勤/图书馆/IT/证件/生活
    # （IT 域用英文缩写是刻意——T9 Minor 决策：种子/评测/检索同源一致，改中文需同步迁移数据，成本高收益低）
    domain: Mapped[str] = mapped_column(String(16), default="", index=True)
    keywords: Mapped[str] = mapped_column(String(128))  # 逗号分隔，检索计分用
    question: Mapped[str] = mapped_column(String(256))
    type: Mapped[str] = mapped_column(String(8), default="info")  # info/process/index
    answer: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class BadCase(Base):
    """未解决反馈（M1 转人工自动沉淀 + M3 对话页"没解决"手动通道；进化闭环①）。

    手动通道带 reply（agent 实际回复）供管理员审查判断；thread_id 存对话关联。
    """

    __tablename__ = "bad_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    question: Mapped[str] = mapped_column(Text)
    reply: Mapped[str] = mapped_column(Text, default="")
    # note 可空：存量行（转人工自动沉淀）为 NULL，手动通道才填补充说明
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(8), default="PENDING", index=True)  # PENDING/RESOLVED
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Suggestion(Base):
    """用户提议（M3 进化闭环②）：学生主动提"问题没有答案"。

    status：PENDING（待审）/ ADOPTED（已采纳=补入知识库）/ REJECTED（已驳回）。
    """

    __tablename__ = "suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    question: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)  # 用户补充说明（可空）
    status: Mapped[str] = mapped_column(String(8), default="PENDING", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class EmptyRoom(Base):
    """空教室 mock 表（M2）：楼栋×房间×周几×时段的空闲教室明细。"""

    __tablename__ = "empty_rooms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    building: Mapped[str] = mapped_column(String(16), index=True)  # 1号楼/2号楼/3号楼
    room: Mapped[str] = mapped_column(String(16))  # 101/302 等
    weekday: Mapped[int] = mapped_column(Integer)  # 1-7（周一=1）
    period: Mapped[str] = mapped_column(String(8))  # 上午/下午/晚上


class LibrarySeat(Base):
    """图书馆座位 mock 表（M2）：每层空余/总座位数。"""

    __tablename__ = "library_seats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    floor: Mapped[str] = mapped_column(String(8))  # 1F..5F
    free_seats: Mapped[int] = mapped_column(Integer)
    total_seats: Mapped[int] = mapped_column(Integer)
