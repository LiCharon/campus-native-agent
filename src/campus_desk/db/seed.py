"""幂等种子数据（M3；M1-T2 最小存活补丁 + M1-T9 知识库与客服账号落地）。

固定幂等键 upsert（存在则更新字段，不存在则插入）——重跑不重复、不报错。
幂等键：字符串 id 表用 id；knowledge_entries 用 question（业务唯一问句）。

覆盖：
- users（5 角色，M6 起带密码哈希，演示密码统一 123456；含 cs-001 客服）
- knowledge_entries（36 条通用校园知识，domain 沿用 11 域有效标签但仅覆盖 6 个通用领域；本地真实信息走 scripts/seed_zjut_local.py，type: info/process/index）
- empty_rooms / library_seats（M2 工具查询 mock 表，189 行 / 5 层）
⚠️ T2 补丁语义：tickets/repairmen/dorms/accounts/announcements/faq 种子已随
退役表删除；T9 起知识库种子（36 条）+ cs-001 在本文件落地。
本地真实信息 → scripts/seed_zjut_local.py 本地注入（config/ 私有文件，不进 git）。

业务函数通过 factory 注入会话（工具层同款依赖注入模式）。
"""

import os
from datetime import date, timedelta

from sqlalchemy import select

from campus_desk.db.models import (
    AcademicCalendar,
    Announcement,
    CardBalance,
    DormPower,
    EmptyRoom,
    ExamSchedule,
    ExamScore,
    KnowledgeEntry,
    LibraryBorrow,
    LibrarySeat,
    LostItem,
    Permission,
    Role,
    RolePermission,
    ShuttleSchedule,
    Timetable,
    User,
)
from campus_desk.db.session import SessionFactory
from campus_desk.security import hash_password

# 种子数据：每项 = (模型, 幂等键列名, 种子列名列表, 行元组列表)
# 幂等键列必须是种子列之一；自增 id 表不显式插 id（幂等键用业务唯一列）。
# M6 登录鉴权：所有演示账号统一密码 "123456"（seed_all 内转哈希入库）。
_DEMO_PASSWORD = "123456"


def _seed_password() -> str:
    """演示账号密码：`SEED_PASSWORD` 环境变量可覆盖，默认 123456。

    M15B-②：clone 后开箱即用（默认弱口令）+ 部署方可用强密码覆盖。
    ⚠️ 幂等坑：seed_all 只回填 `password_hash is None` 的存量用户，
    已 seed 的库改 SEED_PASSWORD 重跑**不会**更新旧账号（用 M6 重置密码改）。
    """
    return os.environ.get("SEED_PASSWORD", _DEMO_PASSWORD)

# ---- M6 RBAC 三表种子：角色/权限定义入库（与 perms.py 硬编码映射一致，运行时查库） ----
_ROLES = [
    ("student", "学生"),
    ("cs_staff", "客服"),
    ("admin", "管理员"),
]

_PERMISSIONS = [
    ("chat", "对话服务"),
    ("cs_workbench", "客服工作台（接待标记）"),
    ("kb_review", "知识库审查补入"),
    ("view_stats", "数据看板"),
    ("user_mgmt", "用户管理"),
    ("view_logs", "日志管理"),
]

_ROLE_PERMISSIONS = [
    ("student", "chat"),
    ("cs_staff", "chat"),
    ("cs_staff", "cs_workbench"),
    ("admin", "chat"),
    ("admin", "cs_workbench"),
    ("admin", "kb_review"),
    ("admin", "view_stats"),
    ("admin", "user_mgmt"),
    ("admin", "view_logs"),
]

_USERS = [
    # (id, name, role, student_no, dept, phone, password)
    ("student-001", "李华", "student", "2024001", None, "13800000001", _DEMO_PASSWORD),
    ("student-002", "王芳", "student", "2024002", None, "13800000002", _DEMO_PASSWORD),
    ("student-003", "张伟", "student", "2024003", None, "13800000003", _DEMO_PASSWORD),
    ("admin-001", "系统管理员", "admin", None, "信息中心", "13800000031", _DEMO_PASSWORD),
    # T9：cs_staff 客服角色（工作台人工接待，M1 起只做种子占位）
    ("cs-001", "客服小杨", "cs_staff", None, None, "13800000041", _DEMO_PASSWORD),
]

# T9 通用知识库 36 条：通用校园数据，无特定地名；domain 沿用 11 域有效标签，仅覆盖 6 个通用领域
# （教务/图书馆/网络与IT/校园卡与证件/住宿后勤/生活服务），其余 5 域由 scripts/seed_zjut_local.py 注入真实信息。
# 各项 = (domain, keywords, question, type, answer)
_KNOWLEDGE = [
    # 教务 6
    (
        "教务",
        "校历,寒假",
        "什么时候放寒假？",
        "info",
        "寒假起止时间以学校官方通知为准，通常在 1 月中旬至 2 月下旬。",
    ),
    (
        "教务",
        "选课,退课",
        "怎么选课退课？",
        "index",
        "请登录教务系统 → 选课中心，在开放窗口期内操作；退课同上入口。",
    ),
    ("教务", "成绩,绩点", "成绩怎么查？", "index", "登录教务系统 → 我的成绩 查看各科成绩与绩点。"),
    (
        "教务",
        "考试,缓考",
        "考试冲突怎么申请缓考？",
        "process",
        "材料：缓考申请表+证明。流程：学院教务办盖章 → 教务处备案。时间：考前 3 个工作日内。",
    ),
    (
        "教务",
        "学籍,休学",
        "怎么办理休学？",
        "process",
        "材料：休学申请+家长知情书。流程：辅导员 → 学院 → 教务处。",
    ),
    (
        "教务",
        "毕业,学位,学分",
        "毕业学分要求多少？",
        "info",
        "各专业培养方案不同，以教务系统培养方案页为准。",
    ),
    # 住宿后勤 6
    (
        "住宿后勤",
        "宿舍,报修",
        "宿舍东西坏了找谁？",
        "index",
        "报修请走后勤报修平台（如公众号后勤服务），线上提交即可。",
    ),
    (
        "住宿后勤",
        "水电,缴费",
        "宿舍水电费怎么缴？",
        "index",
        "后勤服务号 → 水电缴费，绑定宿舍号后在线缴纳。",
    ),
    (
        "住宿后勤",
        "食堂,营业时间",
        "食堂几点开门？",
        "info",
        "各食堂早餐约 6:30、午餐 10:30、晚餐 16:30 起供餐，具体以食堂公告为准。",
    ),
    (
        "住宿后勤",
        "快递,驿站",
        "快递在哪里取？",
        "info",
        "快递驿站位于生活区，凭取件码在驿站自取，大件可预约配送。",
    ),
    (
        "住宿后勤",
        "物业,保洁",
        "公共区域卫生问题反馈？",
        "index",
        "联系楼栋宿管或后勤服务号留言，注明楼栋与位置。",
    ),
    (
        "住宿后勤",
        "住宿,调换",
        "宿舍怎么申请调换？",
        "process",
        "材料：调宿申请。流程：辅导员同意 → 学院盖章 → 宿管中心办理。",
    ),
    # 图书馆 6
    (
        "图书馆",
        "借书,还书",
        "图书怎么借还？",
        "info",
        "凭校园卡在自助借还机操作，借期 30 天，可续借一次。",
    ),
    (
        "图书馆",
        "开放时间,开馆",
        "图书馆几点开门？",
        "info",
        "工作日 8:00-22:00，周末 9:00-21:00，节假日另行通知。",
    ),
    (
        "图书馆",
        "座位,预约",
        "图书馆座位怎么预约？",
        "index",
        "图书馆公众号/座位预约系统，选座签到，离座需释放。",
    ),
    (
        "图书馆",
        "文献,数据库,论文",
        "怎么下载论文？",
        "index",
        "校内 IP 登录图书馆 → 数字资源 → 知网/万方等数据库免费下载。",
    ),
    (
        "图书馆",
        "超期,罚款",
        "图书超期怎么办？",
        "info",
        "超期按天计费（0.1 元/天），还书时在服务台结清。",
    ),
    (
        "图书馆",
        "自习室",
        "自习室在哪层？",
        "info",
        "各馆均设自习区，楼层分布见图书馆一楼导览或官网。",
    ),
    # 网络与IT 6
    (
        "网络与IT",
        "校园网,上网,连接",
        "校园网怎么连？",
        "process",
        "连接校园 WiFi → 浏览器弹出认证页 → 学号+统一密码登录。",
    ),
    (
        "网络与IT",
        "密码,重置",
        "统一认证密码忘了？",
        "index",
        "登录统一认证平台 → 忘记密码 → 手机/邮箱验证重置。",
    ),
    (
        "网络与IT",
        "邮箱,学生邮箱",
        "学生邮箱怎么开通？",
        "info",
        "入学后自动开通，账号为学号@学校邮箱域名，首次登录需激活。",
    ),
    (
        "网络与IT",
        "vpn,校外访问",
        "校外怎么访问校内资源？",
        "index",
        "安装学校 VPN 客户端，用统一认证账号登录后访问内网。",
    ),
    (
        "网络与IT",
        "软件,正版",
        "正版软件哪里下载？",
        "index",
        "信息中心官网 → 正版软件平台，登录后下载激活。",
    ),
    (
        "网络与IT",
        "故障,断网",
        "宿舍断网了怎么办？",
        "process",
        "先重启路由器与终端；仍不通 → 信息中心报障电话/工单平台报修。",
    ),
    # 校园卡与证件 6
    (
        "校园卡与证件",
        "一卡通,补办",
        "一卡通丢了怎么补办？",
        "process",
        "材料：身份证+学生证。流程：服务大厅窗口挂失并补办，工本费按公示标准。",
    ),
    (
        "校园卡与证件",
        "一卡通,充值",
        "一卡通怎么充值？",
        "index",
        "线上：校园服务号 → 一卡通充值；线下：服务大厅充值窗口。",
    ),
    (
        "校园卡与证件",
        "学生证,补办",
        "学生证怎么补办？",
        "process",
        "材料：一寸照+申请表。流程：学院盖章 → 教务处制证，周期约一周。",
    ),
    (
        "校园卡与证件",
        "校园卡,挂失",
        "一卡通挂失怎么操作？",
        "info",
        "服务号在线挂失（即时生效）或服务大厅窗口挂失。",
    ),
    (
        "校园卡与证件",
        "在读证明",
        "在读证明怎么开？",
        "index",
        "教务系统 → 证明打印 自助下载，或服务大厅自助机打印。",
    ),
    (
        "校园卡与证件",
        "银行卡,绑定",
        "奖助学金发放卡怎么绑定？",
        "index",
        "财务系统 → 银行卡维护，绑定本人借记卡。",
    ),
    # 生活服务 6（校园通用占位，无特定地名）：医疗/医保/社团/活动/失物/交通 均按通用占位，
    # 真实浙工大信息由 scripts/seed_zjut_local.py 注入；domain 沿用 11 域有效标签「生活服务」。
    (
        "生活服务",
        "失物,招领",
        "东西丢了去哪找？",
        "index",
        "失物招领处（学生服务中心）登记；或关注学校失物招领公告。",
    ),
    (
        "生活服务",
        "交通,出行",
        "校区之间怎么通行？",
        "info",
        "校区间有定点班车，时刻表见后勤服务号；校外出行地铁/公交便利。",
    ),
    (
        "生活服务",
        "医疗,校医院",
        "校医院在哪？几点开？",
        "info",
        "校医院位于生活区，门诊 8:00-17:00，急诊 24 小时。",
    ),
    (
        "生活服务",
        "医保,报销",
        "医疗费怎么报销？",
        "process",
        "材料：发票+病历。流程：校医院 → 医保办审核 → 打款到绑定卡。",
    ),
    (
        "生活服务",
        "社团,招新",
        "社团怎么加入？",
        "info",
        "每学期社团统一招新（线上报名+线下摆摊），关注校团委通知。",
    ),
    (
        "生活服务",
        "活动,讲座",
        "校园活动在哪看？",
        "index",
        "学校公众号/校园 App 活动频道 + 各学院公告栏。",
    ),
]

_BUILDINGS = ["1号楼", "2号楼", "3号楼"]
_PERIODS = ["上午", "下午", "晚上"]


def _mock_empty_rooms() -> list[tuple]:
    """M2 空教室种子：3 楼 × 7 天 × 3 时段 × 3 间（周中/周末两模式）。"""
    rows = []
    for idx, building in enumerate(_BUILDINGS, start=1):
        for weekday in range(1, 8):  # 1=周一 … 7=周日
            for period in _PERIODS:
                rooms = (
                    [f"{idx}01", f"{idx}05", f"{idx}08"]  # 周中模式
                    if weekday <= 5
                    else [f"{idx}02", f"{idx}06", f"{idx}09"]  # 周末模式
                )
                rows.extend((building, room, weekday, period) for room in rooms)
    return rows


_LIBRARY_SEATS = [
    ("1F", 35, 120),
    ("2F", 42, 130),
    ("3F", 18, 90),
    ("4F", 50, 150),
    ("5F", 27, 100),
]

# ---- FC 工具扩展（M2+）：10 张 mock 表样例种子 ----
_SEMESTER_WEEKS = 18  # 每学期教学周数
_CURRENT_TERM = "2026-2027-1"
_PREV_TERM = "2025-2026-2"
_CALENDAR_START_2026_1 = date(2026, 9, 7)  # 2026-2027-1 秋季学期第一周周一
_CALENDAR_START_2025_2 = date(2026, 2, 23)  # 2025-2026-2 春季学期第一周周一

# 课表模板：每学生 5 个固定时段（(weekday, period, course, location, teacher)），复制到 18 周
_TIMETABLE_TEMPLATES: dict[str, list[tuple]] = {
    "2024001": [
        (1, "上午", "高等数学", "教101", "陈老师"),
        (1, "下午", "大学英语", "教205", "刘老师"),
        (2, "上午", "数据结构", "教302", "王老师"),
        (3, "上午", "操作系统", "教401", "赵老师"),
        (3, "下午", "线性代数", "教105", "孙老师"),
    ],
    "2024002": [
        (1, "上午", "大学英语", "教203", "刘老师"),
        (2, "上午", "线性代数", "教104", "孙老师"),
        (3, "下午", "计算机网络", "实验楼305", "周老师"),
        (4, "上午", "操作系统", "教402", "赵老师"),
        (5, "下午", "体育", "操场", "张老师"),
    ],
    "2024003": [
        (1, "上午", "数据结构", "教301", "王老师"),
        (2, "下午", "大学英语", "教204", "刘老师"),
        (3, "上午", "线性代数", "教106", "孙老师"),
        (4, "上午", "操作系统", "实验楼203", "赵老师"),
        (5, "晚上", "计算机网络", "实验楼306", "周老师"),
    ],
}

# 成绩模板：(student_no, term, course, score, credit)，每学期 6 门
_SCORE_TEMPLATES: list[tuple] = [
    ("2024001", _PREV_TERM, "高等数学", 92, 5.0),
    ("2024001", _PREV_TERM, "大学英语", 85, 3.0),
    ("2024001", _PREV_TERM, "线性代数", 88, 3.0),
    ("2024001", _PREV_TERM, "数据结构", 90, 4.0),
    ("2024001", _PREV_TERM, "体育", 95, 1.0),
    ("2024001", _PREV_TERM, "思想道德与法治", 87, 2.0),
    ("2024001", _CURRENT_TERM, "操作系统", 91, 4.0),
    ("2024001", _CURRENT_TERM, "计算机网络", 87, 3.5),
    ("2024001", _CURRENT_TERM, "大学英语", 86, 3.0),
    ("2024001", _CURRENT_TERM, "线性代数", 89, 3.0),
    ("2024001", _CURRENT_TERM, "数据结构", 92, 4.0),
    ("2024001", _CURRENT_TERM, "形势与政策", 90, 1.0),
    ("2024002", _PREV_TERM, "高等数学", 88, 5.0),
    ("2024002", _PREV_TERM, "大学英语", 90, 3.0),
    ("2024002", _PREV_TERM, "线性代数", 91, 3.0),
    ("2024002", _PREV_TERM, "数据结构", 84, 4.0),
    ("2024002", _PREV_TERM, "体育", 93, 1.0),
    ("2024002", _PREV_TERM, "思想道德与法治", 89, 2.0),
    ("2024002", _CURRENT_TERM, "操作系统", 86, 4.0),
    ("2024002", _CURRENT_TERM, "计算机网络", 90, 3.5),
    ("2024002", _CURRENT_TERM, "大学英语", 91, 3.0),
    ("2024002", _CURRENT_TERM, "线性代数", 85, 3.0),
    ("2024002", _CURRENT_TERM, "数据结构", 88, 4.0),
    ("2024002", _CURRENT_TERM, "形势与政策", 92, 1.0),
    ("2024003", _PREV_TERM, "高等数学", 95, 5.0),
    ("2024003", _PREV_TERM, "大学英语", 87, 3.0),
    ("2024003", _PREV_TERM, "线性代数", 92, 3.0),
    ("2024003", _PREV_TERM, "数据结构", 89, 4.0),
    ("2024003", _PREV_TERM, "体育", 96, 1.0),
    ("2024003", _PREV_TERM, "思想道德与法治", 85, 2.0),
    ("2024003", _CURRENT_TERM, "操作系统", 90, 4.0),
    ("2024003", _CURRENT_TERM, "计算机网络", 88, 3.5),
    ("2024003", _CURRENT_TERM, "大学英语", 89, 3.0),
    ("2024003", _CURRENT_TERM, "线性代数", 93, 3.0),
    ("2024003", _CURRENT_TERM, "数据结构", 91, 4.0),
    ("2024003", _CURRENT_TERM, "形势与政策", 88, 1.0),
]

# 考试安排模板：(student_no, term, course, exam_date, exam_time, location)
_EXAM_TEMPLATES: list[tuple] = [
    ("2024001", _CURRENT_TERM, "操作系统", date(2027, 1, 11), "上午", "教401"),
    ("2024001", _CURRENT_TERM, "计算机网络", date(2027, 1, 13), "下午", "实验楼301"),
    ("2024001", _CURRENT_TERM, "高等数学", date(2027, 1, 15), "上午", "教101"),
    ("2024001", _CURRENT_TERM, "线性代数", date(2027, 1, 8), "上午", "教105"),
    ("2024001", _CURRENT_TERM, "大学英语", date(2027, 1, 18), "下午", "教205"),
    ("2024002", _CURRENT_TERM, "操作系统", date(2027, 1, 11), "下午", "教402"),
    ("2024002", _CURRENT_TERM, "计算机网络", date(2027, 1, 13), "上午", "实验楼305"),
    ("2024002", _CURRENT_TERM, "高等数学", date(2027, 1, 15), "下午", "教103"),
    ("2024002", _CURRENT_TERM, "线性代数", date(2027, 1, 8), "下午", "教106"),
    ("2024002", _CURRENT_TERM, "大学英语", date(2027, 1, 18), "上午", "教203"),
    ("2024003", _CURRENT_TERM, "操作系统", date(2027, 1, 11), "上午", "实验楼203"),
    ("2024003", _CURRENT_TERM, "计算机网络", date(2027, 1, 13), "晚上", "实验楼306"),
    ("2024003", _CURRENT_TERM, "高等数学", date(2027, 1, 15), "上午", "教102"),
    ("2024003", _CURRENT_TERM, "线性代数", date(2027, 1, 8), "上午", "教106"),
    ("2024003", _CURRENT_TERM, "大学英语", date(2027, 1, 18), "晚上", "教204"),
]

# 借阅模板：(student_no, book_title, borrow_date, due_date, status)
_BORROW_TEMPLATES: list[tuple] = [
    ("2024001", "数据结构与算法导论", date(2026, 8, 1), date(2026, 8, 31), "BORROWED"),
    ("2024001", "Python 编程从入门到实践", date(2026, 7, 20), date(2026, 8, 19), "OVERDUE"),
    ("2024001", "机器学习基础", date(2026, 8, 18), date(2026, 9, 17), "BORROWED"),
    ("2024002", "计算机网络自顶向下方法", date(2026, 8, 10), date(2026, 9, 9), "BORROWED"),
    ("2024002", "算法导论", date(2026, 8, 12), date(2026, 9, 11), "BORROWED"),
    ("2024002", "高等数学辅导讲义", date(2026, 8, 15), date(2026, 9, 14), "BORROWED"),
    ("2024003", "操作系统概念", date(2026, 8, 5), date(2026, 9, 4), "BORROWED"),
    ("2024003", "线性代数讲义", date(2026, 8, 8), date(2026, 9, 7), "BORROWED"),
    ("2024003", "大学英语四六级词汇", date(2026, 8, 16), date(2026, 9, 15), "BORROWED"),
]

# 校园卡余额（3 学生各一条）
_CARD_BALANCES = [
    ("2024001", 45.60),
    ("2024002", 128.30),
    ("2024003", 8.50),
]

# 宿舍电量：(building, room, power_left)；3号楼含 205 供 demo 剧本使用
_DORM_POWER = [
    ("1号楼", "101", 8.5),
    ("1号楼", "105", 12.6),
    ("1号楼", "109", 21.3),
    ("1号楼", "112", 5.2),
    ("1号楼", "115", 30.0),
    ("2号楼", "201", 18.2),
    ("2号楼", "205", 9.4),
    ("2号楼", "209", 25.7),
    ("2号楼", "212", 14.8),
    ("2号楼", "215", 6.9),
    ("3号楼", "205", 12.6),
    ("3号楼", "209", 28.1),
    ("3号楼", "301", 4.7),
    ("3号楼", "305", 16.3),
    ("3号楼", "312", 22.5),
]

# 失物种子（均为拾获 found，供 search 演示）：(item_name, location, lost_date, reporter)
_LOST_ITEMS = [
    ("校园卡", "3号楼201", date(2026, 8, 12), "student-001"),
    ("黑色书包", "图书馆2楼", date(2026, 8, 15), "student-002"),
    ("蓝色雨伞", "教101", date(2026, 8, 10), "student-003"),
    ("保温水杯", "操场看台", date(2026, 8, 8), "student-001"),
    ("眼镜盒", "实验楼301", date(2026, 8, 6), "student-002"),
    ("无线耳机", "食堂一楼", date(2026, 8, 3), "student-003"),
    ("黑色钱包", "校车站", date(2026, 7, 28), "student-001"),
    ("钥匙串", "图书馆3楼", date(2026, 7, 25), "student-002"),
    ("笔记本电脑充电器", "教205", date(2026, 7, 22), "student-003"),
    ("红色围巾", "行政楼大厅", date(2026, 7, 18), "student-001"),
]

# 校车时刻：line × direction × 6 个发车时刻
_SHUTTLE_TIMES = {
    ("屏峰-朝晖", "去程"): ["07:30", "09:00", "11:30", "13:30", "16:00", "18:00"],
    ("屏峰-朝晖", "返程"): ["08:10", "09:40", "12:10", "14:10", "16:40", "18:40"],
    ("朝晖-屏峰", "去程"): ["07:15", "08:45", "11:15", "13:15", "15:45", "17:45"],
    ("朝晖-屏峰", "返程"): ["08:30", "10:00", "12:30", "14:30", "17:00", "19:00"],
}

# 通知公告：(title, content, publish_date, source)
_ANNOUNCEMENTS = [
    (
        "教务处关于2026-2027-1学期选课的通知",
        "选课系统将于 8 月 28 日开放，分两轮进行，请同学们按时完成选课。",
        date(2026, 8, 25),
        "教务处",
    ),
    (
        "图书馆暑期闭馆时间安排",
        "暑期周末闭馆，工作日 9:00-17:00 开放，借还书请在工作时间办理。",
        date(2026, 8, 20),
        "图书馆",
    ),
    (
        "2025-2026-2学期期末补考安排",
        "补考定于 9 月第一周进行，考场信息请登录教务系统查看。",
        date(2026, 8, 18),
        "教务处",
    ),
    (
        "2026级新生入学体检安排",
        "新生体检定于 9 月 5 日-7 日在校医院进行，请按学院安排参加。",
        date(2026, 8, 15),
        "校医院",
    ),
    (
        "校园卡服务大厅暑期值班调整",
        "暑期服务大厅工作时间调整为 9:00-16:00，周末休息。",
        date(2026, 8, 10),
        "校园卡中心",
    ),
    (
        "图书馆新学期开馆时间通知",
        "新学期图书馆开放时间为 8:00-22:00，周末 9:00-21:00。",
        date(2026, 9, 1),
        "图书馆",
    ),
    (
        "屏峰校区班车时刻调整通知",
        "自 9 月 1 日起班车时刻表调整，最新时刻详见后勤服务号。",
        date(2026, 8, 28),
        "后勤",
    ),
    (
        "2026-2027-1学期校历发布",
        "新学期校历已发布，共 19 周，第 19 周为考试周。",
        date(2026, 8, 5),
        "教务处",
    ),
]


def _mock_timetable() -> list[tuple]:
    """课表种子：每学生固定周模板复制到 18 周（3 × 18 × 5 = 270 行）。"""
    rows = []
    for student_no, template in _TIMETABLE_TEMPLATES.items():
        for week in range(1, _SEMESTER_WEEKS + 1):
            for weekday, period, course, location, teacher in template:
                rows.append((student_no, week, weekday, period, course, location, teacher))
    return rows


def _mock_calendar() -> list[tuple]:
    """校历种子：2025-2026-2 春季 18 周 + 2026-2027-1 秋季 18 教学周 + 第 19 周考试周。

    秋季第 4 周（国庆假期）特殊标注；暑假不种（时间上下文回退默认学期）。
    """
    rows = []
    for week in range(1, _SEMESTER_WEEKS + 1):
        rows.append(
            (_PREV_TERM, week, _CALENDAR_START_2025_2 + timedelta(weeks=week - 1), "教学周")
        )
    for week in range(1, _SEMESTER_WEEKS + 1):
        label = "国庆假期" if week == 4 else "教学周"
        rows.append(
            (_CURRENT_TERM, week, _CALENDAR_START_2026_1 + timedelta(weeks=week - 1), label)
        )
    rows.append(
        (_CURRENT_TERM, 19, _CALENDAR_START_2026_1 + timedelta(weeks=_SEMESTER_WEEKS), "考试周")
    )
    return rows


# (模型, 幂等键列列表, 种子列, 行数据)——种子列顺序与行元组一一对应；
# 幂等键：字符串 id 表用 id
_SEED_SPECS = [
    # M6 RBAC 三表（排在 User 前：role_permissions 依赖 roles/permissions 已存在，PRAGMA foreign_keys=ON）
    (Role, ["id"], ["id", "name"], _ROLES),
    (Permission, ["id"], ["id", "name"], _PERMISSIONS),
    (
        RolePermission,
        ["role_id", "permission_id"],
        ["role_id", "permission_id"],
        _ROLE_PERMISSIONS,
    ),
    (User, ["id"], ["id", "name", "role", "student_no", "dept", "phone", "password"], _USERS),
    (
        EmptyRoom,
        ["building", "room", "weekday", "period"],
        ["building", "room", "weekday", "period"],
        _mock_empty_rooms(),
    ),
    (LibrarySeat, ["floor"], ["floor", "free_seats", "total_seats"], _LIBRARY_SEATS),
    # ---- FC 工具扩展（M2+）：10 张 mock 表样例种子 ----
    (
        Timetable,
        ["student_no", "week", "weekday", "period"],
        ["student_no", "week", "weekday", "period", "course", "location", "teacher"],
        _mock_timetable(),
    ),
    (
        ExamScore,
        ["student_no", "term", "course"],
        ["student_no", "term", "course", "score", "credit"],
        _SCORE_TEMPLATES,
    ),
    (
        ExamSchedule,
        ["student_no", "term", "course"],
        ["student_no", "term", "course", "exam_date", "exam_time", "location"],
        _EXAM_TEMPLATES,
    ),
    (
        LibraryBorrow,
        ["student_no", "book_title", "borrow_date"],
        ["student_no", "book_title", "borrow_date", "due_date", "status"],
        _BORROW_TEMPLATES,
    ),
    (
        CardBalance,
        ["student_no"],
        ["student_no", "balance"],
        _CARD_BALANCES,
    ),
    (
        DormPower,
        ["building", "room"],
        ["building", "room", "power_left"],
        _DORM_POWER,
    ),
    # lost_items 无幂等键：种子仅供 search 演示，register 追加行（写库工具）
    (
        LostItem,
        ["item_name", "location", "lost_date"],
        ["item_name", "location", "lost_date", "reporter"],
        _LOST_ITEMS,
    ),
    (
        ShuttleSchedule,
        ["line", "direction", "depart_time"],
        ["line", "direction", "depart_time"],
        [
            (line, direction, t)
            for (line, direction), times in _SHUTTLE_TIMES.items()
            for t in times
        ],
    ),
    (
        AcademicCalendar,
        ["term", "week"],
        ["term", "week", "week_start", "label"],
        _mock_calendar(),
    ),
    (
        Announcement,
        ["title", "publish_date"],
        ["title", "content", "publish_date", "source"],
        _ANNOUNCEMENTS,
    ),
]


# M6：演示密码哈希缓存（按明文缓存——pbkdf2 100k 迭代一次 ~0.1s，
# 10 个用户 × 每个用 db_session_factory 的测试都算一次会显著拖慢全量）
_PASSWORD_HASH_CACHE: dict[str, str] = {}


def _hash_cached(plain: str) -> str:
    if plain not in _PASSWORD_HASH_CACHE:
        _PASSWORD_HASH_CACHE[plain] = hash_password(plain)
    return _PASSWORD_HASH_CACHE[plain]


def _seed_knowledge(factory: SessionFactory, *, force: bool = False) -> int:
    """36 条通用知识库入库（幂等键 question：存在则更新字段/不存在则插入）。

    与 User upsert 同风格；question 为业务唯一问句（36 条互不重复），
    普通模式跳过存量行，force=True 覆盖更新（测试用）。
    """
    touched = 0
    with factory() as session, session.begin():
        for domain, keywords, question, ktype, answer in _KNOWLEDGE:
            obj = session.execute(
                select(KnowledgeEntry).where(KnowledgeEntry.question == question)
            ).scalar_one_or_none()
            if obj is None:
                session.add(
                    KnowledgeEntry(
                        domain=domain,
                        keywords=keywords,
                        question=question,
                        type=ktype,
                        answer=answer,
                    )
                )
                touched += 1
            elif force:
                obj.domain, obj.keywords, obj.type, obj.answer = domain, keywords, ktype, answer
                touched += 1
    return touched


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
                if model is User and data.get("password"):
                    # 种子明文密码 → 哈希入库（force 路径同样生效）。
                    # M15B-②：实际落库密码取 _seed_password()（SEED_PASSWORD 覆盖），
                    # _USERS 元组里的 _DEMO_PASSWORD 仅是占位。
                    data["password_hash"] = _hash_cached(_seed_password())
                    data.pop("password")
                obj = session.execute(
                    select(model).where(*(getattr(model, c) == data[c] for c in key_cols))
                ).scalar_one_or_none()
                if obj is None:
                    session.add(model(**data))
                    touched += 1
                elif force or (model is User and obj.password_hash is None):
                    # M6 密码回填：存量用户缺 password_hash 时也更新（demo 补丁语义，
                    # 幂等——重跑后已有哈希即跳过；普通模式不影响其他表存量行）
                    for c, v in data.items():
                        setattr(obj, c, v)
                    touched += 1
            counts[table] = touched
    counts["knowledge_entries"] = _seed_knowledge(factory, force=force)
    return counts
