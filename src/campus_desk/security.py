"""登录鉴权工具（M6）：密码哈希 + JWT 编解码，与 FastAPI 层解耦（seed/scripts 可复用）。

密码哈希：标准库 hashlib.pbkdf2_hmac，零第三方依赖。
存储格式：pbkdf2_sha256$<迭代次数>$<salt_hex>$<hash_hex>
——迭代次数内嵌在存储串里，未来调高迭代次数旧密码仍可验证（verify 按存储串内的次数算）。

JWT：pyjwt HS256，payload 只带 sub（users.id）+ role（登录时已校验存在性，鉴权不查库）。
"""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import UTC, datetime, timedelta

import jwt

from campus_desk.config import settings

# M15A ⑨ 对齐 OWASP（原 10 万；实测 26.1ms → 161.3ms/次，+135ms）
# 存储串内嵌次数 → 历史弱哈希无需迁移仍可验证，登录成功时按需重哈希升级（needs_rehash）
PBKDF2_ITERATIONS = 600_000
TOKEN_ALGORITHM = "HS256"
_HASH_PREFIX = "pbkdf2_sha256"


def hash_password(plain: str) -> str:
    """明文 → 存储串（每次调用随机 salt，同一明文两次哈希结果不同）。"""
    salt = os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f"{_HASH_PREFIX}${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(plain: str, stored: str) -> bool:
    """校验明文是否匹配存储串（迭代次数从存储串内嵌读取，可升级）。"""
    try:
        prefix, iterations_s, salt, expected = stored.split("$")
        if prefix != _HASH_PREFIX:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", plain.encode(), bytes.fromhex(salt), int(iterations_s)
        )
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):  # M15A ⑧ 标准元组写法（原 PEP 758 语法仅 3.14 可编译）
        return False


def needs_rehash(stored: str) -> bool:
    """存储串的迭代次数低于当前值 → 需要重哈希（登录成功时由调用方回写）。

    M15A ⑨ rehash-on-login：历史弱哈希在用户下次登录时自动升级到 60 万次，
    无需数据迁移。畸形串返回 False（交由 verify_password 判失败，不抛异常）。
    """
    try:
        prefix, iterations_s, _salt, _expected = stored.split("$")
        if prefix != _HASH_PREFIX:
            return False
        return int(iterations_s) < PBKDF2_ITERATIONS
    except (ValueError, TypeError):
        return False


def create_access_token(
    user_id: str, role: str, permissions: list[str] | None = None
) -> tuple[str, int]:
    """签发 JWT，返回 (token, 过期秒数)。claims 带 sub+role+perms（登录时算好的
    最终权限并集），authz 不查库——改权限需重新登录（M4 已知语义）。"""
    expire_minutes = settings.jwt_expire_minutes
    payload = {
        "sub": user_id,
        "role": role,
        "perms": permissions or [],
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(minutes=expire_minutes),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=TOKEN_ALGORITHM)
    return token, expire_minutes * 60


def decode_access_token(token: str) -> dict:
    """解 JWT 返回 claims；无效/过期/被篡改抛 jwt.PyJWTError（调用方转 401）。"""
    return jwt.decode(token, settings.jwt_secret, algorithms=[TOKEN_ALGORITHM])
