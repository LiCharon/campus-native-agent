"""幂等种子数据（M3）。

固定幂等键 upsert（存在则更新字段，不存在则插入）——重跑不重复、不报错。
幂等键：字符串 id 表用 id；自增表用业务唯一键（dorms.building / accounts.student_no /
announcements.region+content / faq.category+keywords+question）。

覆盖：users（4 角色）/ repairmen（部门+工种，含 1 名 off_duty 供"在岗优先"测试）/
dorms / accounts（三种状态 mock）/ announcements / faq（4 类各 6 条共 24 条，M4 补全；M6 补 3 条客户端/宽带场景共 27 条——验收发现"下载软件才能联网"场景 0 命中）。

tickets/ticket_logs 为业务表，不预置种子。
业务函数通过 factory 注入会话（工具层同款依赖注入模式）。
"""

from sqlalchemy import select

from campus_desk.db.models import Account, Announcement, Dorm, Faq, Repairman, User
from campus_desk.db.session import SessionFactory
from campus_desk.security import hash_password

# 种子数据：每项 = (模型, 幂等键列名, 种子列名列表, 行元组列表)
# 幂等键列必须是种子列之一；自增 id 表不显式插 id（幂等键用业务唯一列）。
# M6 登录鉴权：所有演示账号统一密码 "123456"（seed_all 内转哈希入库）。
_DEMO_PASSWORD = "123456"
_USERS = [
    # (id, name, role, student_no, dept, phone, password)
    ("student-001", "李华", "student", "2024001", None, "13800000001", _DEMO_PASSWORD),
    ("student-002", "王芳", "student", "2024002", None, "13800000002", _DEMO_PASSWORD),
    ("student-003", "张伟", "student", "2024003", None, "13800000003", _DEMO_PASSWORD),
    ("staff-001", "陈师傅", "staff", None, "后勤", "13800000011", _DEMO_PASSWORD),
    ("staff-002", "刘师傅", "staff", None, "后勤", "13800000012", _DEMO_PASSWORD),
    ("staff-003", "周工", "staff", None, "信息中心", "13800000013", _DEMO_PASSWORD),
    ("it-001", "赵工", "it_staff", None, "信息中心", "13800000021", _DEMO_PASSWORD),
    ("it-002", "孙工", "it_staff", None, "信息中心", "13800000022", _DEMO_PASSWORD),
    ("admin-001", "系统管理员", "admin", None, "信息中心", "13800000031", _DEMO_PASSWORD),
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
        "网络",
        "认证,认证失败,登录失败",
        "连校园网提示认证失败怎么办？",
        "先自查账号是否欠费或过期（可让我帮您查询账号状态）；重启认证客户端并重新认证；仍失败建议报修，信息中心检查端口。",
    ),
    (
        "网络",
        "欠费,停机,恢复",
        "校园网账号欠费/停机了怎么恢复？",
        "通过自助服务门户（self.xxx.edu.cn）或信息中心窗口缴费；缴费成功后约 10 分钟恢复，重新认证即可上网。",
    ),
    (
        "网络",
        "新生,开通,办理",
        "大一新生怎么开通校园网？",
        "新生账号随学号自动开通；首次登录自助服务门户激活；宿舍网口接入后认证即可使用，无需额外办理。",
    ),
    (
        "网络",
        "客户端,翼讯,拨号,软件,安装",
        "校园网认证客户端怎么下载/安装？",
        "校园网接入需使用学校统一认证客户端（翼讯等）：在信息中心官网下载中心下载对应版本（Windows/macOS）→ 按向导安装 → 输入学号与上网密码登录认证。客户端下载/安装异常可报修，信息中心上门协助。",
    ),
    (
        "网络",
        "宽带,PPPoE,拨号连接,校园卡",
        "电信校园卡宽带怎么用 PPPoE 拨号上网？",
        "开通电信校园卡宽带后：1）网线连接电脑与宿舍网口，或连接校园 Wi-Fi；2）新建 PPPoE 拨号连接，账号一般为手机号或学号，密码为开通时设置；3）拨号成功后即可上网。拨号失败可先确认账号是否欠费（我可帮您查询），仍不行请报修网络故障。",
    ),
    (
        "网络",
        "打不开网页,认证页面,弹不出来",
        "连上 Wi-Fi 但打不开网页/弹不出认证页面？",
        "先确认连接的是校园网 SSID（不是运营商热点）；关闭浏览器广告拦截插件后重试；仍弹不出认证页面可手动访问认证地址（portal 页面）或重启认证客户端；持续异常建议报修排查。",
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
        "教务",
        "课表,课程表,查询",
        "课表在哪里查？",
        "登录教务系统→课表查询，或使用教务微信公众号；新学期课表以选课结束后公布的为准。",
    ),
    (
        "教务",
        "在读证明,学籍证明",
        "在读证明/学籍证明怎么开？",
        "携带学生证到教务处学籍窗口办理，或在一站式服务大厅自助打印机打印；用于办证、实习等场景均有效。",
    ),
    (
        "教务",
        "挂科,补考,重修",
        "挂科了怎么补考/重修？",
        "补考安排见教务处通知（一般在下学期开学初）；补考未过需重修，重修在选课期间随选课完成。",
    ),
    (
        "教务",
        "教室,借用,申请",
        "教室怎么借用？",
        "在教务系统提交教室借用申请，填写用途与时间段，经教务处审批后使用；活动教室建议提前 3 个工作日申请。",
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
        "密码",
        "统一认证,统一身份,密码",
        "统一身份认证密码忘了怎么办？",
        "教务系统、校园网、邮箱共用统一认证；忘记后在登录页『忘记密码』用绑定手机号重置，与教务系统同一条通道。",
    ),
    (
        "密码",
        "初始密码,默认密码",
        "初始密码是什么？",
        "新生默认密码为学号后六位；首次登录后建议立即修改，避免账号被盗用。",
    ),
    (
        "密码",
        "锁定,输错,多次",
        "密码输错多次被锁定怎么办？",
        "连续输错 5 次会临时锁定 30 分钟，稍等自动解锁；急需使用可携带学生证到信息中心窗口人工解锁。",
    ),
    (
        "密码",
        "邮箱密码,改密码",
        "邮箱密码和教务系统是同一个吗？",
        "初始密码相同（统一认证）；单独修改邮箱密码请登录自助服务门户→邮箱设置，修改后其他系统密码不受影响。",
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
    (
        "邮箱",
        "附件,发送失败,太大",
        "邮箱发附件失败/附件太大怎么办？",
        "单个附件上限 50MB；大文件请压缩后再发，或使用校园网盘分享链接代替附件。",
    ),
    (
        "邮箱",
        "邮箱忘记密码,邮箱密码忘了",
        "邮箱密码忘了怎么办？",
        "登录 mail.xxx.edu.cn 点击『忘记密码』，通过统一认证重置；重置后 10 分钟生效。",
    ),
    (
        "邮箱",
        "邮箱激活,首次登录",
        "新生的学生邮箱怎么激活？",
        "首次登录 mail.xxx.edu.cn 输入学号+初始密码即自动激活；激活后建议绑定手机号便于找回密码。",
    ),
    (
        "邮箱",
        "自动回复,转发,设置",
        "邮箱怎么设置自动回复/转发？",
        "登录邮箱网页版→设置→自动回复/邮件转发；转发支持保留原件与仅转发新邮件两种模式。",
    ),
]

# (模型, 幂等键列列表, 种子列, 行数据)——种子列顺序与行元组一一对应；
# 幂等键：字符串 id 表用 id，自增表用业务唯一列/复合键
_SEED_SPECS = [
    (User, ["id"], ["id", "name", "role", "student_no", "dept", "phone", "password"], _USERS),
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


# M6：演示密码哈希缓存（按明文缓存——pbkdf2 100k 迭代一次 ~0.1s，
# 9 个用户 × 每个用 db_session_factory 的测试都算一次会显著拖慢全量）
_PASSWORD_HASH_CACHE: dict[str, str] = {}


def _hash_cached(plain: str) -> str:
    if plain not in _PASSWORD_HASH_CACHE:
        _PASSWORD_HASH_CACHE[plain] = hash_password(plain)
    return _PASSWORD_HASH_CACHE[plain]


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
                    # 种子明文密码 → 哈希入库（force 路径同样生效）
                    data["password_hash"] = _hash_cached(data.pop("password"))
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
    return counts
