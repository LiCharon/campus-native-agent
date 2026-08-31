"""M15A 安全加固：⑧ 语法兼容（无 PEP 758）+ ⑨ PBKDF2 60 万次 + rehash-on-login。

TDD：测试先定义"完成"，再最小实现。

⑧ `security.py` 原先写的是 `except ValueError, TypeError:`——Python 3.14 专属的
PEP 758 语法，换到 3.13 及以下直接编译失败。改标准元组写法后行为完全等价，
但源码形态可断言：正则不允许出现「不带括号的多异常 except」。
（注：PEP 758 的 `except A, B:` 与 `except (A, B):` 解析出的 AST 相同，
所以只能从源码形态校验，不能靠 ast 区分。）

⑨ 迭代次数从 10 万提到 60 万（对齐 OWASP）。存储串内嵌次数，
`verify_password` 按串内次数算 → 历史弱哈希无需迁移即可验证；
登录成功时若 `needs_rehash` 为真则重哈希回写，完成平滑升级。
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from campus_desk.security import (
    PBKDF2_ITERATIONS,
    hash_password,
    needs_rehash,
    verify_password,
)

_SECURITY_SRC = (
    Path(__file__).resolve().parent.parent / "src" / "campus_desk" / "security.py"
).read_text(encoding="utf-8")

# PEP 758 特征：`except A, B:`（多异常不带括号）。3.14 专属。
_PEP758_EXCEPT = re.compile(r"except\s+[A-Za-z_][\w.]*\s*,\s*[A-Za-z_][\w.]*\s*:")


def _hash_with_iterations(plain: str, iterations: int) -> str:
    """按指定迭代次数造存储串（模拟历史弱哈希，避免真跑 60 万次拖慢测试）。"""
    salt = os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode(), bytes.fromhex(salt), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


# ---- ⑧ 语法兼容 ----


def test_no_pep758_except_syntax():
    """源码不含 PEP 758 专属写法，保证 3.13 及以下也能编译。"""
    offending = _PEP758_EXCEPT.findall(_SECURITY_SRC)
    assert not offending, f"发现 PEP 758 专属写法（3.14 only）：{offending}"
    assert "except (ValueError, TypeError):" in _SECURITY_SRC


# ---- ⑨ 迭代次数 ----


def test_iterations_raised_to_600k():
    """新哈希用 60 万次（对齐 OWASP）。"""
    assert PBKDF2_ITERATIONS == 600_000
    assert hash_password("123456").split("$")[1] == "600000"


def test_legacy_100k_hash_still_verifies():
    """历史 10 万次哈希仍可验证（存储串内嵌次数，无需迁移）。"""
    old = _hash_with_iterations("123456", 100_000)
    assert verify_password("123456", old) is True
    assert verify_password("wrong-password", old) is False


# ---- ⑨ rehash-on-login ----


def test_needs_rehash_flags_legacy_only():
    """旧串需要升级，新串不需要。"""
    assert needs_rehash(_hash_with_iterations("123456", 100_000)) is True
    assert needs_rehash(hash_password("123456")) is False


def test_needs_rehash_on_malformed_is_false():
    """畸形存储串不算"需要升级"（交由 verify_password 判失败）。"""
    assert needs_rehash("garbage") is False
    assert needs_rehash("other_prefix$100000$aa$bb") is False


def test_verify_password_rejects_malformed():
    """畸形串一律 False，不抛异常（登录路径不能因脏数据 500）。"""
    assert verify_password("x", "not-a-hash") is False
    assert verify_password("x", "other_prefix$100000$aa$bb") is False
