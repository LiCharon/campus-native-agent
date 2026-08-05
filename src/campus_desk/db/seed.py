"""幂等种子数据（M3）。

固定幂等键 upsert（存在则更新字段，不存在则插入）——重跑不重复、不报错。
幂等键：字符串 id 表用 id；自增表用业务唯一键（dorms.building / accounts.student_no /
announcements.region+content / faq.category+keywords+question）。

覆盖：users（4 角色）/ repairmen（部门+工种，含 1 名 off_duty 供"在岗优先"测试）/
dorms / accounts（三种状态 mock）/ announcements / faq（4 类先入 9 条，M4 补全 20-30）。

tickets/ticket_logs 为业务表，不预置种子。
业务函数通过 factory 注入会话（工具层同款依赖注入模式）。
"""

from sqlalchemy import select

from campus_desk.db.models import Account, Announcement, Dorm, Faq, Repairman, User
from campus_desk.db.session import SessionFactory

# 种子数据：每项 = (模型, 幂等键列名, 种子列名列表, 行元组列表)
# 幂等键列必须是种子列之一；自增 id 表不显式插 id（幂等键用业务唯一列）。
_USERS = [
    # (id, name, role, student_no, dept, phone)
    ("student-001", "李华", "student", "2024001", None, "13800000001"),
    ("student-002", "王芳", "student", "2024002", None, "13800000002"),
    ("student-003", "张伟", "student", "2024003", None, "13800000003"),
    ("staff-001", "陈师傅", "staff", None, "后勤", "13800000011"),
    ("staff-002", "刘师傅", "staff", None, "后勤", "13800000012"),
    ("staff-003", "周工", "staff", None, "信息中心", "13800000013"),
    ("it-001", "赵工", "it_staff", None, "信息中心", "13800000021"),
    ("it-002", "孙工", "it_staff", None, "信息中心", "13800000022"),
    ("admin-001", "系统管理员", "admin", None, "信息中心", "13800000031"),
]

_REPAIRMEN = [
    # (id, name, dept, trade, phone, on_duty)
    ("rm-001", "陈师傅", "后勤", "水电", "13800000011", True),
    ("rm-002", "刘师傅", "后勤", "水电", "13800000012", True),
    ("rm-003", "吴师傅", "后勤", "家具", "13800000013", True),
    ("rm-004", "郑师傅", "后勤", "门窗", "13800000014", True),
    ("rm-005", "赵工", "信息中心", "网络", "13800000021", True),
    ("rm-006", "钱工", "信息中心", "网络", "13800000022", True),
    ("rm-007", "孙工", "信息中心", "账号", "13800000023", True),
    ("rm-008", "李师傅", "后勤", "水电", "13800000015", False),  # 不在岗：派单规则"在岗优先"测试用
]

_DORMS = [
    # (building, room_range, manager, note)
    ("1号楼", "101-320", "王阿姨", None),
    ("2号楼", "101-420", "李阿姨", None),
    ("3号楼", "101-502", "张阿姨", "6 人间"),
    ("4号楼", "101-410", "赵阿姨", None),
    ("6号楼", "201-608", "孙阿姨", "研究生楼"),
]

_ACCOUNTS = [
    # (student_no, status, note)
    ("2024001", "normal", "网络账号正常"),
    ("2024002", "overdue", "欠费停机"),
    ("2024003", "expired", "账号过期需激活"),
]

_ANNOUNCEMENTS = [
    # (region, content)
    ("3号楼", "3 号楼于 8 月 6 日 09:00-12:00 停电检修，受影响房间请提前安排。"),
    ("全校", "校园网于 8 月 7 日凌晨 02:00-04:00 升级维护，期间网络中断。"),
    ("信息中心", "学生网络账号办理窗口移至图书馆一层，办公时间 8:30-17:00。"),
    ("1号楼", "1 号楼热水系统 8 月 8 日起恢复供应。"),
]

_FAQ = [
    # (category, keywords, question, answer)
    (
        "网络",
        "连不上,断网,无法上网,网络故障",
        "宿舍网络连不上怎么办？",
        "先确认网口指示灯是否亮起；重启网口交换机和电脑；仍不行可在对话中报修，信息中心会安排网络组处理。",
    ),
    (
        "网络",
        "wifi,无线,连不上",
        "WiFi 连不上/信号弱怎么办？",
        "建议靠近宿舍阳台信号较好位置；忘记网络后重新连接；宿舍内多人掉线可报修排查交换机。",
    ),
    (
        "网络",
        "网速,慢,卡顿",
        "网速慢怎么办？",
        "高峰期（20:00-23:00）网速下降属正常现象；检查是否占用大量带宽的软件；持续多日可报修排查。",
    ),
    (
        "教务",
        "成绩,查询,教务系统",
        "成绩在哪里查？",
        "登录教务系统（jw.xxx.edu.cn）→ 成绩查询；账号初始密码为学号后六位。",
    ),
    (
        "教务",
        "选课,补选,退课",
        "选课/退课时间怎么安排？",
        "选课在开学前两周开放，补退选在开学第一周；具体时间见教务处通知。",
    ),
    (
        "密码",
        "密码,忘记,重置",
        "教务系统密码忘了怎么办？",
        "在登录页点『忘记密码』，用绑定的手机号/邮箱重置；或携带学生证到教务处窗口办理。",
    ),
    (
        "密码",
        "上网账号,密码修改",
        "校园网上网账号密码怎么修改？",
        "登录自助服务门户（self.xxx.edu.cn）→ 修改密码；密码需包含字母和数字，8 位以上。",
    ),
    (
        "邮箱",
        "邮箱,登录,学生邮箱",
        "学生邮箱怎么开通/登录？",
        "邮箱自动开通，登录 mail.xxx.edu.cn，用户名=学号，初始密码同教务系统。",
    ),
    (
        "邮箱",
        "邮箱,容量,清理",
        "邮箱容量满了怎么办？",
        "清理发件箱中的大附件；超过 10GB 可到信息中心申请扩容。",
    ),
]

# (模型, 幂等键列列表, 种子列, 行数据)——种子列顺序与行元组一一对应；
# 幂等键：字符串 id 表用 id，自增表用业务唯一列/复合键
_SEED_SPECS = [
    (User, ["id"], ["id", "name", "role", "student_no", "dept", "phone"], _USERS),
    (Repairman, ["id"], ["id", "name", "dept", "trade", "phone", "on_duty"], _REPAIRMEN),
    (Dorm, ["building"], ["building", "room_range", "manager", "note"], _DORMS),
    (Account, ["student_no"], ["student_no", "status", "note"], _ACCOUNTS),
    (Announcement, ["region", "content"], ["region", "content"], _ANNOUNCEMENTS),
    (
        Faq,
        ["category", "keywords", "question"],
        ["category", "keywords", "question", "answer"],
        _FAQ,
    ),
]


def seed_all(factory: SessionFactory, *, force: bool = False) -> dict[str, int]:
    """幂等种子入库。返回各表写入计数；force=True 时按幂等键更新字段（测试用）。

    幂等键匹配存在 → 跳过（force 则更新字段）；不存在 → 插入。
    """
    counts: dict[str, int] = {}
    with factory() as session, session.begin():
        for model, key_cols, cols, rows in _SEED_SPECS:
            table = model.__tablename__
            touched = 0
            for row in rows:
                data = dict(zip(cols, row))
                obj = session.execute(
                    select(model).where(*(getattr(model, c) == data[c] for c in key_cols))
                ).scalar_one_or_none()
                if obj is None:
                    session.add(model(**data))
                    touched += 1
                elif force:
                    for c, v in data.items():
                        setattr(obj, c, v)
                    touched += 1
            counts[table] = touched
    return counts
