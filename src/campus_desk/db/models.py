"""业务数据模型（10 张表，M3 建 8 + M4 加 2）。

表职责（需求 §5/§7 工具上下文 + 工单数据）：
- users         角色与账号（student/staff/it_staff/admin），M6 登录用
- user_profiles 用户长期记忆画像（M4：楼栋/常报类别/上次工单摘要，1:1 users）
- tickets       工单主表（6 态状态机 + 超时升级 + 回访评价字段）
- ticket_logs   状态跳转审计日志（谁/何时/备注，每次跳转必写）
- repairmen     维修工（部门+工种两层派单、在岗状态）
- dorms         楼栋/宿舍只读信息（query_dorm_info）
- accounts      学生网络账号 mock（query_account_status，生产接计费系统见契约）
- announcements 故障公告（query_announcement）
- faq           关键词 FAQ 库（search_faq）

约束要点：
- tickets.status 默认 SUBMITTED；升级=字段（escalation_count/escalated_at）不是状态
- 回访=字段（closed_at/rating/review_comment/reviewed_at）不是状态——关闭时间
  用于"关闭 24h 后"惰性判定，评价随回访写入（需求 §6 QualityAgent）
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


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)  # 提交人
    ticket_type: Mapped[str] = mapped_column(
        String(16), default="repair"
    )  # repair 报修 / complaint 投诉（投诉复用管道）
    description: Mapped[str] = mapped_column(Text)
    contact: Mapped[str] = mapped_column(String(64))  # 联系人姓名/学号（建单必填）
    category: Mapped[str] = mapped_column(
        String(16), default="其他"
    )  # 水电/网络/门窗/设备/环境/其他
    priority: Mapped[str] = mapped_column(String(4), default="P2")  # P1 紧急 / P2 普通 / P3 预约
    status: Mapped[str] = mapped_column(String(20), default="SUBMITTED", index=True)
    building: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 楼栋（报修类）
    location: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # 位置/对象（投诉类，如"食堂阿姨"）
    dept: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 派单部门
    repairman_id: Mapped[str | None] = mapped_column(ForeignKey("repairmen.id"), nullable=True)
    # 超时升级 = 字段不是状态（需求 §3）：工单留在原状态，升级后仍可流转
    escalation_count: Mapped[int] = mapped_column(Integer, default=0)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 回访 = 字段不是状态（M4，需求 §6）：closed_at 供"关闭 24h 后"惰性判定，
    # 评价随 QualityAgent 回访写入（rating 1-5 / review_comment / reviewed_at）
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class TicketLog(Base):
    __tablename__ = "ticket_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    from_status: Mapped[str] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    actor: Mapped[str] = mapped_column(String(64))  # user_id / repairman_id / system
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Repairman(Base):
    __tablename__ = "repairmen"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # rm-001
    name: Mapped[str] = mapped_column(String(64))
    dept: Mapped[str] = mapped_column(String(32), index=True)  # 信息中心 / 后勤
    trade: Mapped[str] = mapped_column(String(32), index=True)  # 网络/账号/水电/家具/门窗
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    on_duty: Mapped[bool] = mapped_column(default=True)  # 在岗状态（派单规则：在岗优先）


class Dorm(Base):
    __tablename__ = "dorms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    building: Mapped[str] = mapped_column(String(32), unique=True)  # 3号楼
    room_range: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 101-420
    manager: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(String(128), nullable=True)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_no: Mapped[str] = mapped_column(String(32), unique=True)
    status: Mapped[str] = mapped_column(String(16))  # normal 正常 / overdue 欠费 / expired 过期
    note: Mapped[str | None] = mapped_column(String(128), nullable=True)


class Announcement(Base):
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    region: Mapped[str] = mapped_column(
        String(32), index=True
    )  # 区域关键词：3号楼 / 全校 / 信息中心
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Faq(Base):
    __tablename__ = "faq"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(16), index=True)  # 网络/教务/密码/邮箱
    keywords: Mapped[str] = mapped_column(String(128))  # 逗号分隔，search_faq 匹配用
    question: Mapped[str] = mapped_column(String(256))
    answer: Mapped[str] = mapped_column(Text)
