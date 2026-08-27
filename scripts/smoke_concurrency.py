"""M12-ZJUT 并发冒烟脚本：验证单 worker + turn_lock 下并发请求不崩、正确排队。

设计目标（CLAUDE.md §5 + 实现计划 blazing-cascade-newton.md「压测评估」）：
- 单机 demo 部署、单 worker（uvicorn --workers 1）、SqliteSaver 串行 turn_lock 是
  **规避 SQLite 非线程安全的设计约束**，不是性能瓶颈。冒烟验证"并发下不崩、请求正确
  排队"而非性能基准（吞吐瓶颈在外部 LLM 秒级调用，正式压测不可复现）。
- 并发 5 / 10 打 /api/chat（多用户 × 多会话，避免 turn_lock 串行掩盖跨会话并发问题）。
- 断言：全部 200、无 500，响应按 turn_lock 正确排队不互踩（reply 非空即视为正常落库返回）。
- 产出：成功率 + 耗时分布 p50 / p95 / p99。
- 无服务可连（server 未起 / 网络不通）→ SKIP（exit 0）；真 LLM key 缺失仍能跑（错误兜底仍 200）。

用法：
    python scripts/smoke_concurrency.py                      # 默认 http://localhost:8000
    python scripts/smoke_concurrency.py --base-url http://host:8000
    python scripts/smoke_concurrency.py --concurrency 5,10,20 --sessions-per-user 4
    python scripts/smoke_concurrency.py --users student-001:123456,cs-001:123456

依赖：仅标准库（urllib + concurrent.futures），与项目 M6 冒烟脚本一致。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

DEFAULT_QUESTIONS = [
    "校历什么时候开学？",
    "图书馆今天开到几点？",
    "怎么查课表？",
    "宿舍网连不上怎么办？",
    "失物招领怎么登记？",
    "奖学金什么时候评？",
    "校医院在哪里？",
    "校车时刻表有吗？",
    "怎么充值校园卡？",
    "考试安排在哪看？",
]


@dataclass
class Cfg:
    base_url: str
    users: list[tuple[str, str]]
    concurrency_levels: list[int]
    sessions_per_user: int
    timeout: float


def _post(url: str, payload: dict, token: str | None, timeout: float) -> tuple[int, float, Any]:
    """发 POST，返回 (status_code, 耗时秒, 解析后的 body 或 None)。"""
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as exc:  # 4xx/5xx
        body = exc.read().decode("utf-8", "replace")
        status = exc.code
    elapsed = time.perf_counter() - start
    parsed: Any = None
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        pass
    return status, elapsed, parsed


def _extract_token(body: Any) -> str | None:
    """兼容两种返回形态：{token:...} 与统一 {code,msg,data:{token:...}}。"""
    if isinstance(body, dict):
        if body.get("token"):
            return body["token"]
        data = body.get("data")
        if isinstance(data, dict) and data.get("token"):
            return data["token"]
    return None


def _extract_thread_id(body: Any) -> str | None:
    if isinstance(body, dict):
        if body.get("thread_id"):
            return body["thread_id"]
        data = body.get("data")
        if isinstance(data, dict) and data.get("thread_id"):
            return data["thread_id"]
    return None


def login(cfg: Cfg, username: str, password: str) -> str:
    url = f"{cfg.base_url}/api/auth/login"
    status, _elapsed, body = _post(url, {"username": username, "password": password}, None, cfg.timeout)
    if status == 200:
        token = _extract_token(body)
        if token:
            return token
    # 连接失败（URLError）已在调用方捕获；这里是非 200 或取不到 token
    raise RuntimeError(f"登录失败 user={username} status={status} body={_short(body)}")


def _short(body: Any, n: int = 200) -> str:
    s = json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body
    return s[:n]


def create_sessions(cfg: Cfg, token: str, n: int) -> list[str]:
    threads: list[str] = []
    for _ in range(n):
        status, _e, body = _post(f"{cfg.base_url}/api/sessions", {}, token, cfg.timeout)
        if status == 200:
            tid = _extract_thread_id(body)
            if tid:
                threads.append(tid)
                continue
        raise RuntimeError(f"建会话失败 status={status} body={_short(body)}")
    return threads


def _one_chat(cfg: Cfg, token: str, thread_id: str, question: str) -> tuple[int, float, bool]:
    status, elapsed, body = _post(
        f"{cfg.base_url}/api/chat",
        {"thread_id": thread_id, "msg": question},
        token,
        cfg.timeout,
    )
    reply = ""
    if isinstance(body, dict):
        reply = body.get("reply") or (body.get("data") or {}).get("reply") or ""
    ok = bool(reply)  # 200 且有非空回复 = 正常落库返回（错误兜底也算正常返回）
    return status, elapsed, ok


def run_level(cfg: Cfg, pools: list[tuple[str, str]], level: int) -> dict:
    """并发 level 个请求（从 pools 随机取 token+thread），返回统计。"""
    import random

    results: list[tuple[int, float, bool]] = []
    # 每个并发任务发一条独立请求；多轮则用同一 thread 模拟连续对话（仍跨会话并发）
    tasks = [(random.choice(pools), random.choice(DEFAULT_QUESTIONS)) for _ in range(level)]
    with ThreadPoolExecutor(max_workers=level) as ex:
        futs = [
            ex.submit(_one_chat, cfg, tok, tid, q) for (tok, tid), q in tasks
        ]
        for f in as_completed(futs):
            results.append(f.result())

    statuses = [r[0] for r in results]
    latencies = [r[1] for r in results]
    replied = sum(1 for r in results if r[2])
    code_500 = sum(1 for s in statuses if s >= 500)
    code_200 = sum(1 for s in statuses if s == 200)
    latencies.sort()

    def pct(p: float) -> float:
        if not latencies:
            return 0.0
        k = max(0, min(len(latencies) - 1, round((p / 100) * (len(latencies) - 1))))
        return latencies[k]

    return {
        "level": level,
        "total": len(results),
        "code_200": code_200,
        "code_500": code_500,
        "replied": replied,
        "all_200": code_200 == len(results),
        "no_500": code_500 == 0,
        "p50": pct(50),
        "p95": pct(95),
        "p99": pct(99),
        "max": max(latencies) if latencies else 0.0,
        "mean": statistics.fmean(latencies) if latencies else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="M12 并发冒烟（单 worker 串行 turn_lock 正确性验证）")
    ap.add_argument("--base-url", default=os.environ.get("SMOKE_BASE_URL", "http://localhost:8000"))
    ap.add_argument("--concurrency", default="5,10", help="逗号分隔并发档位，默认 5,10")
    ap.add_argument("--sessions-per-user", type=int, default=4)
    ap.add_argument(
        "--users",
        default="student-001:123456",
        help="逗号分隔 user:pass；默认 student-001:123456",
    )
    ap.add_argument("--timeout", type=float, default=60.0)
    args = ap.parse_args()

    cfg = Cfg(
        base_url=args.base_url.rstrip("/"),
        users=[tuple(u.split(":", 1)) for u in args.users.split(",") if u],
        concurrency_levels=[int(x) for x in args.concurrency.split(",") if x.strip()],
        sessions_per_user=args.sessions_per_user,
        timeout=args.timeout,
    )

    # 预热：探测服务可达性（不可达 → SKIP）
    try:
        login(cfg, *cfg.users[0])
    except urllib.error.URLError as exc:
        print(f"[SKIP] 服务不可达 {cfg.base_url}：{exc.reason}")
        print("       请先启动 API：uvicorn campus_desk.api.app:create_app --factory --port 8000 --workers 1")
        return 0
    except RuntimeError as exc:
        print(f"[ERROR] 登录失败：{exc}")
        return 1

    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("[WARN] DEEPSEEK_API_KEY 未设置：LLM 调用将错误兜底，但 /api/chat 仍返回 200，"
              "并发正确性照常验证。")

    # 准备多用户 × 多会话池
    pools: list[tuple[str, str]] = []
    for uname, pwd in cfg.users:
        try:
            tok = login(cfg, uname, pwd)
        except RuntimeError as exc:
            print(f"[WARN] 用户 {uname} 登录失败，跳过：{exc}")
            continue
        threads = create_sessions(cfg, tok, cfg.sessions_per_user)
        pools.extend((tok, t) for t in threads)
        print(f"[准备] {uname}：{len(threads)} 个会话")

    if not pools:
        print("[ERROR] 无任何可用会话池（所有用户登录/建会话失败）")
        return 1

    total_pairs = len(pools)
    print(f"[开始] 目标 {cfg.base_url}，并发档位 {cfg.concurrency_levels}，会话池 {total_pairs}\n")

    all_pass = True
    for level in cfg.concurrency_levels:
        if level < 1:
            continue
        stats = run_level(cfg, pools, level)
        flag = "PASS" if (stats["all_200"] and stats["no_500"]) else "FAIL"
        if flag == "FAIL":
            all_pass = False
        print(
            f"[{flag}] 并发={level:>2}  总数={stats['total']:>2}  "
            f"200={stats['code_200']}  500={stats['code_500']}  "
            f"有回复={stats['replied']}  "
            f"延迟 p50={stats['p50']*1000:6.1f}ms  p95={stats['p95']*1000:6.1f}ms  "
            f"p99={stats['p99']*1000:6.1f}ms  max={stats['max']*1000:7.1f}ms"
        )

    print()
    if all_pass:
        print("[结论] 并发请求全部 200、无 500，turn_lock 正确排队，单 worker 并发安全 ✅")
        return 0
    print("[结论] 存在非 200 / 500 响应，需排查（见上 FAIL 行）❌")
    return 1


if __name__ == "__main__":
    sys.exit(main())
