"""M15A-③ 登录失败锁定（进程内计数，与 security.py 同级以便脚本也能解锁）。

设计要点（对抗性审查结论）：
- **按用户实体计数**（users.id），不按输入的用户名：学生可用 id 或学号登录，
  按用户名计数会让"学号试满 5 次再换 id 试"轻易绕过锁定。
- **只给真实存在的账号计数**：查不到用户的登录请求根本不进 dict，
  既防内存被塞爆，也避免"账号不存在"被探测出来。
- **锁定对外无感**：锁定与密码错返回完全一致的响应，锁定事实只进审计；
  否则等于给用户枚举接口送了一个"这个账号存在且被锁了"的信号。
- **留解锁通道**：管理员（user_mgmt）可一键解锁，否则任何人都能靠连续试错
  把真管理员锁死 15 分钟（反向 DoS 比暴力破解更现实）。

⚠️ 已知约束：状态在进程内存，重启即清，多 worker 各自计数。
当前部署为单 worker（docker-compose 未配 replicas），接受此约束；
将来多 worker 需换 Redis 计数。
"""

from __future__ import annotations

import time
from threading import Lock

MAX_FAILS = 5  # 连续失败达此数即锁定
LOCK_SECONDS = 15 * 60

_guard = Lock()
_FAILS: dict[str, int] = {}  # user_id -> 连续失败次数
_LOCKED_UNTIL: dict[str, float] = {}  # user_id -> 解锁时刻（monotonic）


def _is_locked_unlocked(user_id: str) -> bool:
    """调用方须已持有 _guard。到期则顺带清干净（惰性过期）。"""
    until = _LOCKED_UNTIL.get(user_id)
    if until is None:
        return False
    if time.monotonic() >= until:
        _LOCKED_UNTIL.pop(user_id, None)
        _FAILS.pop(user_id, None)
        return False
    return True


def is_locked(user_id: str) -> bool:
    with _guard:
        return _is_locked_unlocked(user_id)


def locked_remaining_seconds(user_id: str) -> int:
    """剩余锁定秒数（未锁定返回 0），供审计/提示文案使用。"""
    with _guard:
        until = _LOCKED_UNTIL.get(user_id)
        if until is None:
            return 0
        return max(0, int(until - time.monotonic()))


def register_failure(user_id: str) -> bool:
    """记一次密码失败；返回 True 表示**本次**触发了锁定（调用方据此写审计）。"""
    with _guard:
        if _is_locked_unlocked(user_id):
            return False  # 已锁：不再累加，也不重复报锁定事件
        count = _FAILS.get(user_id, 0) + 1
        _FAILS[user_id] = count
        if count >= MAX_FAILS:
            _LOCKED_UNTIL[user_id] = time.monotonic() + LOCK_SECONDS
            return True
        return False


def register_success(user_id: str) -> None:
    """登录成功：清空该用户的失败计数与锁定。"""
    with _guard:
        _FAILS.pop(user_id, None)
        _LOCKED_UNTIL.pop(user_id, None)


def unlock(user_id: str) -> bool:
    """管理员解锁：返回 True 表示确实清掉了锁定/失败计数。"""
    with _guard:
        was_locked = _is_locked_unlocked(user_id)
        had_state = _FAILS.pop(user_id, None) is not None
        _LOCKED_UNTIL.pop(user_id, None)
        return was_locked or had_state or False


def reset_all() -> None:
    """清空全部计数（测试隔离 / 运维脚本用）。"""
    with _guard:
        _FAILS.clear()
        _LOCKED_UNTIL.clear()
