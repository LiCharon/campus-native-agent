"""幂等种子数据（M3；M1-T2 最小存活补丁 + M1-T9 知识库与客服账号落地）。

固定幂等键 upsert（存在则更新字段，不存在则插入）——重跑不重复、不报错。
幂等键：字符串 id 表用 id；knowledge_entries 用 question（业务唯一问句）。

覆盖：
- users（5 角色，M6 起带密码哈希，演示密码统一 123456；含 cs-001 客服）
- knowledge_entries（36 条通用校园知识，6 领域 × 6 条，type: info/process/index）
⚠️ T2 补丁语义：tickets/repairmen/dorms/accounts/announcements/faq 种子已随
退役表删除；T9 起知识库种子（36 条）+ cs-001 在本文件落地。
浙工大真实信息 → scripts/seed_zjut_local.py 本地注入（config/ 私有文件，不进 git）。

业务函数通过 factory 注入会话（工具层同款依赖注入模式）。
"""

from sqlalchemy import select

from campus_desk.db.models import KnowledgeEntry, User
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
    # T9：cs_staff 客服角色（工作台人工接待，M1 起只做种子占位）
    ("cs-001", "客服小杨", "cs_staff", None, None, "13800000041", _DEMO_PASSWORD),
]

# T9 通用知识库 36 条（6 领域 × 6 条）：通用校园数据，无浙工大特定地名
# （本地真实信息走 scripts/seed_zjut_local.py）。每项 = (domain, keywords, question, type, answer)
_KNOWLEDGE = [
    # 教务 6
    ("教务", "校历,寒假", "什么时候放寒假？", "info", "寒假起止时间以学校官方通知为准，通常在 1 月中旬至 2 月下旬。"),
    ("教务", "选课,退课", "怎么选课退课？", "index", "请登录教务系统 → 选课中心，在开放窗口期内操作；退课同上入口。"),
    ("教务", "成绩,绩点", "成绩怎么查？", "index", "登录教务系统 → 我的成绩 查看各科成绩与绩点。"),
    ("教务", "考试,缓考", "考试冲突怎么申请缓考？", "process", "材料：缓考申请表+证明。流程：学院教务办盖章 → 教务处备案。时间：考前 3 个工作日内。"),
    ("教务", "学籍,休学", "怎么办理休学？", "process", "材料：休学申请+家长知情书。流程：辅导员 → 学院 → 教务处。"),
    ("教务", "毕业,学位,学分", "毕业学分要求多少？", "info", "各专业培养方案不同，以教务系统培养方案页为准。"),
    # 后勤 6
    ("后勤", "宿舍,报修", "宿舍东西坏了找谁？", "index", "报修请走后勤报修平台（如公众号后勤服务），线上提交即可。"),
    ("后勤", "水电,缴费", "宿舍水电费怎么缴？", "index", "后勤服务号 → 水电缴费，绑定宿舍号后在线缴纳。"),
    ("后勤", "食堂,营业时间", "食堂几点开门？", "info", "各食堂早餐约 6:30、午餐 10:30、晚餐 16:30 起供餐，具体以食堂公告为准。"),
    ("后勤", "快递,驿站", "快递在哪里取？", "info", "快递驿站位于生活区，凭取件码在驿站自取，大件可预约配送。"),
    ("后勤", "物业,保洁", "公共区域卫生问题反馈？", "index", "联系楼栋宿管或后勤服务号留言，注明楼栋与位置。"),
    ("后勤", "住宿,调换", "宿舍怎么申请调换？", "process", "材料：调宿申请。流程：辅导员同意 → 学院盖章 → 宿管中心办理。"),
    # 图书馆 6
    ("图书馆", "借书,还书", "图书怎么借还？", "info", "凭校园卡在自助借还机操作，借期 30 天，可续借一次。"),
    ("图书馆", "开放时间,开馆", "图书馆几点开门？", "info", "工作日 8:00-22:00，周末 9:00-21:00，节假日另行通知。"),
    ("图书馆", "座位,预约", "图书馆座位怎么预约？", "index", "图书馆公众号/座位预约系统，选座签到，离座需释放。"),
    ("图书馆", "文献,数据库,论文", "怎么下载论文？", "index", "校内 IP 登录图书馆 → 数字资源 → 知网/万方等数据库免费下载。"),
    ("图书馆", "超期,罚款", "图书超期怎么办？", "info", "超期按天计费（0.1 元/天），还书时在服务台结清。"),
    ("图书馆", "自习室", "自习室在哪层？", "info", "各馆均设自习区，楼层分布见图书馆一楼导览或官网。"),
    # IT 6
    ("IT", "校园网,上网,连接", "校园网怎么连？", "process", "连接校园 WiFi → 浏览器弹出认证页 → 学号+统一密码登录。"),
    ("IT", "密码,重置", "统一认证密码忘了？", "index", "登录统一认证平台 → 忘记密码 → 手机/邮箱验证重置。"),
    ("IT", "邮箱,学生邮箱", "学生邮箱怎么开通？", "info", "入学后自动开通，账号为学号@学校邮箱域名，首次登录需激活。"),
    ("IT", "vpn,校外访问", "校外怎么访问校内资源？", "index", "安装学校 VPN 客户端，用统一认证账号登录后访问内网。"),
    ("IT", "软件,正版", "正版软件哪里下载？", "index", "信息中心官网 → 正版软件平台，登录后下载激活。"),
    ("IT", "故障,断网", "宿舍断网了怎么办？", "process", "先重启路由器与终端；仍不通 → 信息中心报障电话/工单平台报修。"),
    # 证件 6
    ("证件", "一卡通,补办", "一卡通丢了怎么补办？", "process", "材料：身份证+学生证。流程：服务大厅窗口挂失并补办，工本费按公示标准。"),
    ("证件", "一卡通,充值", "一卡通怎么充值？", "index", "线上：校园服务号 → 一卡通充值；线下：服务大厅充值窗口。"),
    ("证件", "学生证,补办", "学生证怎么补办？", "process", "材料：一寸照+申请表。流程：学院盖章 → 教务处制证，周期约一周。"),
    ("证件", "校园卡,挂失", "一卡通挂失怎么操作？", "info", "服务号在线挂失（即时生效）或服务大厅窗口挂失。"),
    ("证件", "在读证明", "在读证明怎么开？", "index", "教务系统 → 证明打印 自助下载，或服务大厅自助机打印。"),
    ("证件", "银行卡,绑定", "奖助学金发放卡怎么绑定？", "index", "财务系统 → 银行卡维护，绑定本人借记卡。"),
    # 生活 6
    ("生活", "医疗,校医院", "校医院在哪？几点开？", "info", "校医院位于生活区，门诊 8:00-17:00，急诊 24 小时。"),
    ("生活", "医保,报销", "医疗费怎么报销？", "process", "材料：发票+病历。流程：校医院 → 医保办审核 → 打款到绑定卡。"),
    ("生活", "失物,招领", "东西丢了去哪找？", "index", "失物招领处（学生服务中心）登记；或关注学校失物招领公告。"),
    ("生活", "社团,招新", "社团怎么加入？", "info", "每学期社团统一招新（线上报名+线下摆摊），关注校团委通知。"),
    ("生活", "活动,讲座", "校园活动在哪看？", "index", "学校公众号/校园 App 活动频道 + 各学院公告栏。"),
    ("生活", "交通,出行", "校区之间怎么通行？", "info", "校区间有定点班车，时刻表见后勤服务号；校外出行地铁/公交便利。"),
]

# (模型, 幂等键列列表, 种子列, 行数据)——种子列顺序与行元组一一对应；
# 幂等键：字符串 id 表用 id
_SEED_SPECS = [
    (User, ["id"], ["id", "name", "role", "student_no", "dept", "phone", "password"], _USERS),
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
                    KnowledgeEntry(domain=domain, keywords=keywords, question=question,
                                   type=ktype, answer=answer)
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
    counts["knowledge_entries"] = _seed_knowledge(factory, force=force)
    return counts
