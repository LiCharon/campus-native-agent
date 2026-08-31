"""M15A-① 启动期 JWT 密钥校验（fail-fast）。

校验落点在 `create_app()`（不是 config import）：
- 真正对外起服务（uvicorn --factory / docker）必过 create_app → 拦得住
- 测试、seed、eval 脚本只 import settings，不受影响

判定只看密钥值本身，不看 APP_ENV——最典型的翻车是忘了设 APP_ENV，
那样"仅 production 才拦"的保护永远不会触发。
"""

import pytest

from campus_desk.api.app import create_app
from campus_desk.config import DEFAULT_JWT_SECRET, JWT_SECRET_MIN_LENGTH, settings

# 占位依赖：校验应发生在建真 MySQL 工厂/真 LLM 之前，传占位对象可确保
# "校验没生效"时测试是 DID NOT RAISE（正常的红），而不是连库超时。
_STUB_FACTORY = object()
_STUB_REGISTRY = object()


def _build():
    return create_app(session_factory=_STUB_FACTORY, registry=_STUB_REGISTRY)


def test_rejects_default_secret(monkeypatch):
    """默认密钥（GitHub 公开）+ 未开逃生阀 → 拒绝启动。"""
    monkeypatch.setattr(settings, "jwt_secret", DEFAULT_JWT_SECRET)
    monkeypatch.setattr(settings, "allow_insecure_dev", False)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        _build()


def test_error_message_explains_how_to_fix(monkeypatch):
    """报错信息必须说清"怎么修"，而不是只说"不安全"。"""
    monkeypatch.setattr(settings, "jwt_secret", DEFAULT_JWT_SECRET)
    monkeypatch.setattr(settings, "allow_insecure_dev", False)
    with pytest.raises(RuntimeError) as exc:
        _build()
    msg = str(exc.value)
    assert "JWT_SECRET" in msg
    assert "ALLOW_INSECURE_DEV" in msg


def test_escape_hatch_allows_default_secret(monkeypatch):
    """显式 ALLOW_INSECURE_DEV=1 → 放行默认密钥（本地偷懒通道）。"""
    monkeypatch.setattr(settings, "jwt_secret", DEFAULT_JWT_SECRET)
    monkeypatch.setattr(settings, "allow_insecure_dev", True)
    assert _build() is not None


def test_accepts_strong_custom_secret(monkeypatch):
    """够长且非默认的密钥 → 放行，无需逃生阀。"""
    monkeypatch.setattr(settings, "jwt_secret", "a" * 48)
    monkeypatch.setattr(settings, "allow_insecure_dev", False)
    assert _build() is not None


def test_rejects_short_secret_even_with_escape_hatch(monkeypatch):
    """逃生阀只放行"默认值"，不放行自己设的弱密钥。"""
    monkeypatch.setattr(settings, "jwt_secret", "short")
    monkeypatch.setattr(settings, "allow_insecure_dev", True)
    with pytest.raises(RuntimeError, match=str(JWT_SECRET_MIN_LENGTH)):
        _build()


@pytest.mark.parametrize(
    "secret",
    [
        DEFAULT_JWT_SECRET.upper(),
        DEFAULT_JWT_SECRET + "x",
        "dev-secret-change-me-0123456789abcde",  # 末尾少一位
    ],
)
def test_rejects_default_lookalikes(monkeypatch, secret):
    """改大小写/加尾巴/截短的默认值同样是公开可猜的 → 一律拒。"""
    monkeypatch.setattr(settings, "jwt_secret", secret)
    monkeypatch.setattr(settings, "allow_insecure_dev", False)
    with pytest.raises(RuntimeError):
        _build()


def test_settings_default_is_the_constant():
    """单源：config 默认值必须与常量同源，防改一处忘另一处。"""
    assert type(settings).model_fields["jwt_secret"].default == DEFAULT_JWT_SECRET
