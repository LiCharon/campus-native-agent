"""幂等种子数据（M3；M1-T2 最小存活补丁：删退役表种子，仅保留 users）。

固定幂等键 upsert（存在则更新字段，不存在则插入）——重跑不重复、不报错。
幂等键：字符串 id 表用 id。

覆盖：users（4 角色，M6 起带密码哈希，演示密码统一 123456）。
⚠️ T2 补丁语义：tickets/repairmen/dorms/accounts/announcements/faq 种子已随
退役表删除；知识库种子（36 条）+ cs-001 由 M1-T9 重写本文件时落地。

业务函数通过 factory 注入会话（工具层同款依赖注入模式）。
"""

from sqlalchemy import select

from campus_desk.db.models import User
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

# (模型, 幂等键列列表, 种子列, 行数据)——种子列顺序与行元组一一对应；
# 幂等键：字符串 id 表用 id
_SEED_SPECS = [
    (User, ["id"], ["id", "name", "role", "student_no", "dept", "phone", "password"], _USERS),
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
