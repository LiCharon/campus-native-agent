"""M6 安全模块测试：密码哈希往返 / salt 随机 / JWT 编解码 / 过期与篡改。"""

import jwt
import pytest

from campus_desk import security
from campus_desk.config import settings


class TestPasswordHash:
    def test_hash_verify_roundtrip(self):
        stored = security.hash_password("123456")
        assert stored.startswith("pbkdf2_sha256$")
        assert security.verify_password("123456", stored)

    def test_wrong_password_rejected(self):
        stored = security.hash_password("123456")
        assert not security.verify_password("654321", stored)

    def test_salt_randomness(self):
        # 同一明文两次哈希结果不同（随机 salt）
        assert security.hash_password("123456") != security.hash_password("123456")

    def test_corrupted_stored_rejected(self):
        assert not security.verify_password("123456", "not-a-valid-format")
        assert not security.verify_password("123456", "pbkdf2_sha256$x$y$z")

    def test_custom_iterations_verifiable(self):
        # 迭代次数内嵌在存储串：用自定义次数哈希后仍可验证（未来升级兼容）
        salt = "ab" * 16
        import hashlib

        digest = hashlib.pbkdf2_hmac("sha256", b"pwd", bytes.fromhex(salt), 10_000)
        stored = f"pbkdf2_sha256$10000${salt}${digest.hex()}"
        assert security.verify_password("pwd", stored)


class TestJWT:
    def test_token_roundtrip(self):
        token, expires_in = security.create_access_token("student-001", "student")
        claims = security.decode_access_token(token)
        assert claims["sub"] == "student-001"
        assert claims["role"] == "student"
        assert expires_in == settings.jwt_expire_minutes * 60

    def test_expired_token_rejected(self):
        import time

        # 构造一个已过期的 token（直接把 exp 改到过去）
        expired = jwt.encode(
            {"sub": "student-001", "role": "student", "exp": int(time.time()) - 60},
            settings.jwt_secret,
            algorithm=security.TOKEN_ALGORITHM,
        )
        with pytest.raises(jwt.ExpiredSignatureError):
            security.decode_access_token(expired)

    def test_tampered_token_rejected(self):
        token, _ = security.create_access_token("student-001", "student")
        with pytest.raises(jwt.InvalidSignatureError):
            security.decode_access_token(token + "x")

    def test_wrong_secret_rejected(self):
        # 用错误密钥签的 token 用真实密钥解不开
        forged = jwt.encode(
            {"sub": "admin-001", "role": "admin"}, "wrong-secret", algorithm="HS256"
        )
        with pytest.raises(jwt.InvalidSignatureError):
            security.decode_access_token(forged)
