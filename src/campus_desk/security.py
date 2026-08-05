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

PBKDF2_ITERATIONS = 100_000  # 演示级迭代次数（测试拖慢防护：seed 按明文缓存哈希）
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
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: str, role: str) -> tuple[str, int]:
    """签发 JWT，返回 (token, 过期秒数)。claims 只带 sub+role，authz 不查库。"""
    expire_minutes = settings.jwt_expire_minutes
    payload = {
        "sub": user_id,
        "role": role,
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(minutes=expire_minutes),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=TOKEN_ALGORITHM)
    return token, expire_minutes * 60


def decode_access_token(token: str) -> dict:
    """解 JWT 返回 claims；无效/过期/被篡改抛 jwt.PyJWTError（调用方转 401）。"""
    return jwt.decode(token, settings.jwt_secret, algorithms=[TOKEN_ALGORITHM])
