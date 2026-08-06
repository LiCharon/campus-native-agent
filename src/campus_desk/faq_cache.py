"""FAQ 读路径 Redis 热点缓存（M7）：cache-aside，Redis 不可用自动降级直查 DB。

设计铁律（仿 telemetry.py 惰性 import 模式）：
1. enabled() == False（未配 REDIS_URL，本机常态）时所有函数零副作用 no-op——
   redis 包不被 import、不发任何连接、不抛任何异常。
2. Redis 连不上（未启动/网络不通）：连接失败标记进入冷却期（30s），期间全部
   直查 DB（不反复重连拖慢每次 FAQ 搜索）；冷却期过后自动重探，恢复即生效
   （compose 下 backend 早于 redis 就绪也能自愈）。
3. 缓存 key 单一化：`faq:list`（全量 FAQ 按 id 排序，TTL 300s）——search_faq
   与 FAQ 管理列表共用同一份缓存，避免同数据按关键词重复存 N 份；
   管理页任何写操作后调 flush_faqs() 失效缓存（前缀 faq:* 目前仅一个 key）。
"""

import json
import time

from campus_desk.config import settings
from campus_desk.db.models import Faq
from campus_desk.db.session import SessionFactory

_KEY = "faq:list"
_TTL_SECONDS = 300
# 连接失败后的冷却时长：期间不重连（防止 Redis 宕机时每次搜索都吃 1s 超时）
_RETRY_COOLDOWN_SECONDS = 30.0

_client = None  # redis client 惰性单例（enabled 后才被赋值）
_unavailable_until = 0.0  # 连接失败后的冷却截止（time.monotonic() 单调时间）


def enabled() -> bool:
    """缓存开关：REDIS_URL 配了才算启用。"""
    return bool(settings.redis_url)


def _get_client():
    """惰性建 redis client；未启用/冷却期内/连接失败 → None（调用方直查 DB）。"""
    global _client, _unavailable_until
    if not enabled():
        return None
    if _client is not None:
        return _client
    if time.monotonic() < _unavailable_until:
        return None
    try:
        import redis  # 惰性 import：未配 REDIS_URL 时包不被加载

        client = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
        client.ping()
    except Exception:  # noqa: BLE001 — 连不上即降级，任何网络/连接异常都不能阻断业务
        _unavailable_until = time.monotonic() + _RETRY_COOLDOWN_SECONDS
        return None
    _client = client
    return _client


def get_faqs() -> list[Faq] | None:
    """缓存读：命中返回 FAQ 列表；未命中/不可用/数据损坏 → None（视为 miss）。"""
    client = _get_client()
    if client is None:
        return None
    try:
        raw = client.get(_KEY)
    except Exception:  # noqa: BLE001 — 缓存读失败按 miss 处理，直查 DB 兜底
        return None
    if not raw:
        return None
    try:
        rows = json.loads(raw)
        return [Faq(**row) for row in rows]
    except Exception:  # noqa: BLE001 — 缓存数据损坏按 miss 处理，下一轮读覆盖回填
        return None


def set_faqs(faqs: list[Faq]) -> None:
    """缓存写（全量覆盖 + TTL）。失败静默——缓存写失败不阻断业务。"""
    client = _get_client()
    if client is None:
        return
    try:
        rows = [
            {
                "id": f.id,
                "category": f.category,
                "keywords": f.keywords,
                "question": f.question,
                "answer": f.answer,
            }
            for f in faqs
        ]
        client.set(_KEY, json.dumps(rows, ensure_ascii=False), ex=_TTL_SECONDS)
    except Exception:  # noqa: BLE001, S110 — 缓存写失败静默降级，不阻断业务
        pass


def get_all_faqs(session_factory: SessionFactory) -> list[Faq]:
    """cache-aside 读全量 FAQ（按 id 排序）：命中直返，未命中查库并回填。

    search_faq 与 FAQ 管理列表共用——同一份缓存、同一套失效逻辑。
    """
    cached = get_faqs()
    if cached is not None:
        return cached
    with session_factory() as session, session.begin():
        faqs = session.query(Faq).order_by(Faq.id).all()
    set_faqs(faqs)
    return faqs


def flush_faqs() -> None:
    """失效 FAQ 缓存（管理页增删改后调用）。缓存未启用/不可用时 no-op。"""
    client = _get_client()
    if client is None:
        return
    try:
        client.delete(_KEY)
    except Exception:  # noqa: BLE001, S110 — 失效失败静默降级，不阻断业务
        pass
