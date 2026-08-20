"""业务数据模型（M1-ZJUT：4 业务表 + 2 评测表，T2 删 7 张退役表）。

表职责（ZJUT 设计 §4.5 知识库 + §5.5 进化闭环 + M4 权限/审计 + M5-ZJUT 会话）：
- users             角色与账号（student/cs_staff/admin 三角色），登录用
- user_profiles     用户长期记忆画像（预留：画像启用时再定，1:1 users）
- knowledge_entries 知识库条目（FAQ 式，type 驱动组装：info/process/index）
- conversations     会话（M5-ZJUT 服务端化）：归属用户 + thread_id（LangGraph checkpointer key）
- messages          会话消息（M5-ZJUT）：展示层落库，sources/tool_calls 等 JSON 文本
- roles / permissions / role_permissions  RBAC 三表（M6）：角色-权限映射入库，perms.py 运行时查库（users.role 字符串列保留指向 roles.id）
- bad_cases         未解决反馈双通道：① M1 转人工自动沉淀 ② M3 对话页"没解决"按钮
- suggestions       用户提议通道（M3）："问题没答案"主动提议，管理员审查采纳/驳回
- audit_logs        审计日志（M4）：登录/审查/接待/用户管理关键操作留痕

约束要点：
- knowledge_entries.domain 11 领域（教务/图书馆/网络与IT/校园卡与证件/住宿后勤/奖助/医疗健康/社团与活动/就业与职业发展/安全与保卫/生活服务）；
  keywords 逗号分隔，检索计分用（campus_desk.knowledge.search）
- bad_cases.status：PENDING/RESOLVED（自动/手动反馈均 PENDING，M3 管理页审查后 RESOLVED）
- suggestions.status：PENDING/ADOPTED/REJECTED（采纳=补入知识库，驳回=不补入）
- users.permissions：附加权限位（逗号分隔），最终权限 = 角色默认 ∪ 附加位（perms.py）
- conversations：id 业务 id（服务端 UUID hex），thread_id 唯一（LangGraph checkpointer key）；
  handoff 三态 none/transferring/human（M5-ZJUT 起落库，前端 3 秒模拟仅推进状态）
- messages：role user/assistant/system；tool_calls/status_events/sources 存 JSON 文本（展示层）；
  pending 占位与请求失败标记是前端 UI 态，只落最终消息
- 外键关系不配置 relationship() 对象——ORM 查询用显式 join/id 字段，
  避免 lazy-load 隐式 SQL（工具层短会话，防 N+1/DetachedInstanceError）
"""

from datetime import UTC, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
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
    action: Mapped[str] = mapped_column(
        String(32), index=True
    )  # login/adopt/dismiss/resolve/user_create...
    object_type: Mapped[str] = mapped_column(
        String(16)
    )  # system/bad_case/suggestion/user/knowledge
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
    # domain 11 领域：教务/图书馆/网络与IT/校园卡与证件/住宿后勤/奖助/医疗健康/社团与活动/就业与职业发展/安全与保卫/生活服务
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
    status: Mapped[str] = mapped_column(
        String(8), default="PENDING", index=True
    )  # PENDING/RESOLVED
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


# ---- FC 工具扩展（M2+）：10 张新 mock 表，全部样例数据，幂等键列在 docstring 标注 ----
# 幂等键：student_no/term/... 业务唯一列；lost_items 无幂等键（register 追加行，种子仅演示）。


class Timetable(Base):
    """课表 mock 表（FC 扩展）：学生 × 教学周 × 星期 × 时段的课程明细。

    幂等键：(student_no, week, weekday, period)。
    """

    __tablename__ = "timetables"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_no: Mapped[str] = mapped_column(String(32), index=True)
    week: Mapped[int] = mapped_column(Integer)  # 教学周 1-18
    weekday: Mapped[int] = mapped_column(Integer)  # 1-7（周一=1）
    period: Mapped[str] = mapped_column(String(8))  # 上午/下午/晚上
    course: Mapped[str] = mapped_column(String(64))
    location: Mapped[str] = mapped_column(String(64))
    teacher: Mapped[str] = mapped_column(String(32))


class ExamScore(Base):
    """成绩 mock 表（FC 扩展）：学生 × 学期 × 课程的成绩与学分。

    幂等键：(student_no, term, course)。
    """

    __tablename__ = "exam_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_no: Mapped[str] = mapped_column(String(32), index=True)
    term: Mapped[str] = mapped_column(String(16), index=True)  # 2025-2026-2 / 2026-2027-1
    course: Mapped[str] = mapped_column(String(64))
    score: Mapped[int] = mapped_column(Integer)
    credit: Mapped[float] = mapped_column(Float)


class ExamSchedule(Base):
    """考试安排 mock 表（FC 扩展）：学生 × 学期 × 课程的考试时间地点。

    幂等键：(student_no, term, course)。
    """

    __tablename__ = "exam_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_no: Mapped[str] = mapped_column(String(32), index=True)
    term: Mapped[str] = mapped_column(String(16), index=True)
    course: Mapped[str] = mapped_column(String(64))
    exam_date: Mapped[datetime] = mapped_column(Date)
    exam_time: Mapped[str] = mapped_column(String(16))  # 上午/下午/晚上
    location: Mapped[str] = mapped_column(String(64))


class LibraryBorrow(Base):
    """借阅 mock 表（FC 扩展）：学生在借图书与应还日。

    幂等键：(student_no, book_title, borrow_date)。
    """

    __tablename__ = "library_borrows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_no: Mapped[str] = mapped_column(String(32), index=True)
    book_title: Mapped[str] = mapped_column(String(64))
    borrow_date: Mapped[datetime] = mapped_column(Date)
    due_date: Mapped[datetime] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(8), default="BORROWED")  # BORROWED/OVERDUE


class CardBalance(Base):
    """校园卡余额 mock 表（FC 扩展）：学生 × 卡余额。

    幂等键：(student_no)。
    """

    __tablename__ = "card_balances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_no: Mapped[str] = mapped_column(String(32), index=True)
    balance: Mapped[float] = mapped_column(Float)


class DormPower(Base):
    """宿舍电量 mock 表（FC 扩展）：楼栋 × 房间的剩余电量。

    幂等键：(building, room)。
    """

    __tablename__ = "dorm_power"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    building: Mapped[str] = mapped_column(String(16), index=True)
    room: Mapped[str] = mapped_column(String(16))
    power_left: Mapped[float] = mapped_column(Float)  # 剩余电量（度）
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LostItem(Base):
    """失物招领 mock 表（FC 扩展，内部 UGC）：学生登记的拾获物品。

    无幂等键——register_lost_item 追加行（唯一写库工具）；种子行仅供 search 演示。
    """

    __tablename__ = "lost_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_name: Mapped[str] = mapped_column(String(64), index=True)
    location: Mapped[str] = mapped_column(String(64), index=True)
    lost_date: Mapped[datetime] = mapped_column(Date)
    reporter: Mapped[str] = mapped_column(String(32), default="")  # 登记人 user_id
    status: Mapped[str] = mapped_column(String(8), default="found")  # found/claimed
    description: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ShuttleSchedule(Base):
    """校车时刻 mock 表（FC 扩展）：线路 × 方向 × 发车时刻。

    幂等键：(line, direction, depart_time)。
    """

    __tablename__ = "shuttle_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    line: Mapped[str] = mapped_column(String(32), index=True)  # 屏峰-朝晖 / 朝晖-屏峰
    direction: Mapped[str] = mapped_column(String(8))  # 去程/返程
    depart_time: Mapped[str] = mapped_column(String(5))  # 07:30


class AcademicCalendar(Base):
    """校历 mock 表（FC 扩展）：学期 × 教学周的起止与标签。

    幂等键：(term, week)。week_start 用于按当前日期推算"今天第几周/当前学期"。
    """

    __tablename__ = "academic_calendar"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    term: Mapped[str] = mapped_column(String(16), index=True)
    week: Mapped[int] = mapped_column(Integer)
    week_start: Mapped[datetime] = mapped_column(Date)
    label: Mapped[str] = mapped_column(String(16), default="教学周")  # 教学周/考试周/国庆假期


class Announcement(Base):
    """通知公告 mock 表（FC 扩展）：标题/正文/发布日期/来源。

    幂等键：(title, publish_date)。
    """

    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text, default="")
    publish_date: Mapped[datetime] = mapped_column(Date, index=True)
    source: Mapped[str] = mapped_column(String(32), default="")


class Conversation(Base):
    """会话（M5-ZJUT 服务端化）：归属用户 + thread_id（LangGraph checkpointer key）。

    - id：业务 id（服务端 UUID hex，32 位）；thread_id 唯一——会话上下文键
    - title/title_source：标题（auto 自动=首条消息前 12 字 / manual 手动重命名）
    - handoff：转人工三态 none/transferring/human（M5-ZJUT 起落库，
      前端 3 秒模拟仅推进状态；刷新停在中间态不再自动推进——已确认接受）
    - updated_at：发消息/改名/转态时更新，会话列表按它降序
    """

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    thread_id: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(64), default="新对话")
    title_source: Mapped[str] = mapped_column(String(8), default="auto")  # auto/manual
    handoff: Mapped[str] = mapped_column(String(16), default="none")  # none/transferring/human
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class Message(Base):
    """会话消息（M5-ZJUT）：展示层落库，sources/tool_calls 等 JSON 文本。

    role：user/assistant/system（转人工系统提示条）。
    tool_calls/status_events/sources 存 JSON 数组文本（默认 "[]"），读时解析。
    pending 占位与请求失败标记是前端 UI 态，不落库；只落最终消息。
    级联删除：conversation 删除时 messages 随删（ondelete CASCADE）。
    """

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    # String(16)：assistant 9 字符——VARCHAR(8) 在 MySQL 严格模式存不下
    # （SQLite 测试库不校验长度，真实环境冒烟才暴露）
    role: Mapped[str] = mapped_column(String(16))  # user/assistant/system
    content: Mapped[str] = mapped_column(Text)
    route: Mapped[str | None] = mapped_column(String(16), nullable=True)  # knowledge/query/...
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)  # answer/ask/handoff/...
    pending_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_calls: Mapped[str] = mapped_column(Text, default="[]")  # JSON 数组
    status_events: Mapped[str] = mapped_column(Text, default="[]")  # JSON 数组
    sources: Mapped[str] = mapped_column(Text, default="[]")  # JSON 数组（来源 chip）
    error: Mapped[bool] = mapped_column(Boolean, default=False)
    feedback_submitted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Role(Base):
    """角色定义（M6 RBAC）：users.role 字符串列指向本表 id（一人一角色，无 user_roles）。"""

    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # student / cs_staff / admin
    name: Mapped[str] = mapped_column(String(32))  # 学生 / 客服 / 管理员
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Permission(Base):
    """权限位定义（M6 RBAC）：chat/cs_workbench/kb_review/view_stats/user_mgmt/view_logs。"""

    __tablename__ = "permissions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # chat / cs_workbench / ...
    name: Mapped[str] = mapped_column(String(64))  # 中文显示名（前端勾选 label）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RolePermission(Base):
    """角色-权限关联（M6 RBAC）：复合主键 (role_id, permission_id)，运行时查库取角色默认权限。"""

    __tablename__ = "role_permissions"

    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"), primary_key=True)
    permission_id: Mapped[str] = mapped_column(ForeignKey("permissions.id"), primary_key=True)
