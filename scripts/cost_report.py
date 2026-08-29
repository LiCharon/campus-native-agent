"""M13-ZJUT 成本报表：聚合 `llm_usage` 表 → token 用量 + 估算费用。

费用一律在报表层派生（表只存 token，不存钱）：
    费用 = prompt_tokens / 1e6 * 输入单价 + completion_tokens / 1e6 * 输出单价
单价取 config（DEEPSEEK_INPUT_PRICE / DEEPSEEK_OUTPUT_PRICE，元/百万 token），
改单价后重跑报表即按新价计算，历史记录不回改。

用法（需 DATABASE_URL；scripts 目录脚本统一从仓库根跑）：
  PYTHONPATH=src python scripts/cost_report.py --days 7
  PYTHONPATH=src python scripts/cost_report.py --since 2026-08-01 --until 2026-08-29
  PYTHONPATH=src python scripts/cost_report.py --user student-001 --days 30
  PYTHONPATH=src python scripts/cost_report.py --call-point intent --format json
  PYTHONPATH=src python scripts/cost_report.py --days 7 --format csv > cost.csv

维度：总计 / 按天 / 按调用点 / 按模型 / 按用户（top N）/ 按会话（top N）。
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import func, literal, select

from campus_desk.config import settings
from campus_desk.db.models import LLMUsage
from campus_desk.db.session import default_session_factory

_PER_MILLION = 1_000_000


def _cost_yuan(prompt_tokens: int, completion_tokens: int) -> float:
    """按当前单价估算费用（元）。"""
    return (
        prompt_tokens / _PER_MILLION * settings.deepseek_input_price
        + completion_tokens / _PER_MILLION * settings.deepseek_output_price
    )


def _agg(factory, *, group_col, filters: dict):
    """按某维度聚合：[(key, calls, prompt, completion, total)]。

    group_col 为 None → 汇总成单行（key="ALL"）。
    """
    key = group_col if group_col is not None else literal("ALL")
    stmt = select(
        key.label("key"),
        func.count(LLMUsage.id).label("calls"),
        func.coalesce(func.sum(LLMUsage.prompt_tokens), 0).label("prompt"),
        func.coalesce(func.sum(LLMUsage.completion_tokens), 0).label("completion"),
        func.coalesce(func.sum(LLMUsage.total_tokens), 0).label("total"),
    )
    stmt = _apply_filters(stmt, filters)
    if group_col is not None:
        stmt = stmt.group_by(group_col).order_by(func.count(LLMUsage.id).desc())
    with factory() as session:
        rows = session.execute(stmt).all()
    return [
        (
            # key 一律转 str：按天维度 SQL 侧返回 datetime.date（json 序列化会炸）
            ("ALL" if group_col is None else (str(r.key) if r.key is not None else "(空)")),
            int(r.calls),
            int(r.prompt),
            int(r.completion),
            int(r.total),
        )
        for r in rows
    ]


def _apply_filters(stmt, filters: dict):
    """时间区间 + 维度过滤（SQL 侧，减少传输）。"""
    if filters.get("since") is not None:
        stmt = stmt.where(LLMUsage.created_at >= filters["since"])
    if filters.get("until") is not None:
        stmt = stmt.where(LLMUsage.created_at < filters["until"])
    if filters.get("user_id"):
        stmt = stmt.where(LLMUsage.user_id == filters["user_id"])
    if filters.get("thread_id"):
        stmt = stmt.where(LLMUsage.thread_id == filters["thread_id"])
    if filters.get("call_point"):
        stmt = stmt.where(LLMUsage.call_point == filters["call_point"])
    if filters.get("status") in ("success", "error"):
        stmt = stmt.where(LLMUsage.status == filters["status"])
    return stmt


def _status_counts(factory, filters: dict) -> dict[str, int]:
    """成功/失败调用次数（汇总口径，不受 group 影响）。"""
    stmt = select(LLMUsage.status, func.count(LLMUsage.id)).group_by(LLMUsage.status)
    stmt = _apply_filters(stmt, filters)
    with factory() as session:
        return {str(s): int(c) for s, c in session.execute(stmt).all()}


def _fmt_table(title: str, rows: list[tuple], key_header: str) -> str:
    """渲染一个分表：key / 调用 / 输入 / 输出 / 合计 / 费用。"""
    lines = [f"【{title}】"]
    if not rows:
        lines.append("  （无数据）")
        return "\n".join(lines)
    # key 列宽自适应（模型名 27 字符会顶破固定 20 → 20~40 之间取最长 key）
    key_width = max(20, min(40, max(len(str(r[0])) for r in rows), len(key_header)))
    head = (
        f"{key_header:<{key_width}}{'调用':>8}{'输入token':>12}"
        f"{'输出token':>12}{'合计token':>12}{'费用(元)':>12}"
    )
    lines.append(head)
    lines.append("-" * (key_width + 56))
    for key, calls, prompt, completion, total in rows:
        lines.append(
            f"{key!s:<{key_width}}{calls:>8,}{prompt:>12,}{completion:>12,}{total:>12,}"
            f"{_cost_yuan(prompt, completion):>12.4f}"
        )
    return "\n".join(lines)


def _print_table(sections: list[tuple[str, list[tuple], str]], *, header_lines: list[str]) -> None:
    for line in header_lines:
        print(line)
    for title, rows, key_header in sections:
        print()
        print(_fmt_table(title, rows, key_header))


def _print_csv(sections: list[tuple[str, list[tuple], str]]) -> None:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")  # 默认 \r\n 叠加 print 会多出空行
    writer.writerow(
        [
            "section",
            "key",
            "calls",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "cost_yuan",
        ]
    )
    for title, rows, _ in sections:
        for key, calls, prompt, completion, total in rows:
            writer.writerow(
                [
                    title,
                    key,
                    calls,
                    prompt,
                    completion,
                    total,
                    f"{_cost_yuan(prompt, completion):.6f}",
                ]
            )
    print(buf.getvalue().rstrip())


def _print_json(sections: list[tuple[str, list[tuple], str]], *, meta: dict) -> None:
    payload = {"meta": meta, "sections": {}}
    for title, rows, _ in sections:
        payload["sections"][title] = [
            {
                "key": key,
                "calls": calls,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": total,
                "cost_yuan": round(_cost_yuan(prompt, completion), 6),
            }
            for key, calls, prompt, completion, total in rows
        ]
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LLM 调用成本报表（聚合 llm_usage）")
    p.add_argument("--days", type=int, default=7, help="最近 N 天（默认 7；给 --since 时忽略）")
    p.add_argument("--since", help="起始日期 YYYY-MM-DD（含）")
    p.add_argument("--until", help="结束日期 YYYY-MM-DD（含）")
    p.add_argument("--user", dest="user_id", help="按 user_id 过滤")
    p.add_argument("--thread", dest="thread_id", help="按 thread_id 过滤")
    p.add_argument(
        "--call-point",
        help="按调用点过滤（intent / decide / tool_select / unknown）",
    )
    p.add_argument(
        "--status",
        choices=["all", "success", "error"],
        default="all",
        help="按调用结果过滤（默认 all）",
    )
    p.add_argument(
        "--format", choices=["table", "csv", "json"], default="table", help="输出格式（默认 table）"
    )
    p.add_argument("--top", type=int, default=10, help="用户/会话分表取前 N（默认 10）")
    return p.parse_args(argv)


def _day(s: str | None, *, default: datetime | None = None) -> datetime | None:
    if not s:
        return default
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    since = _day(args.since)
    if since is None:
        since = datetime.now(UTC) - timedelta(days=args.days)
    until = _day(args.until)
    if until is not None:
        until = until + timedelta(days=1)  # --until 含当天 → 上界 +1 天

    filters = {
        "since": since,
        "until": until,
        "user_id": args.user_id,
        "thread_id": args.thread_id,
        "call_point": args.call_point,
        "status": args.status,
    }

    try:
        factory = default_session_factory()
    except RuntimeError as exc:  # 无 DATABASE_URL：明确提示，不算异常退出
        print(f"[cost_report] {exc}")
        print("提示：先配置 .env 的 DATABASE_URL，再重跑本脚本。")
        return 1

    total = _agg(factory, group_col=None, filters=filters)
    if not total or total[0][1] == 0:
        print("[cost_report] 指定区间内没有 llm_usage 记录。")
        print(f"  区间：{since:%Y-%m-%d} ~ {(until - timedelta(days=1)) if until else '今天'}")
        print("  可能原因：未开启计量（llm.py 已无条件挂载）、区间太短，或过滤条件过窄。")
        return 0

    by_day = _agg(factory, group_col=func.date(LLMUsage.created_at), filters=filters)
    by_point = _agg(factory, group_col=LLMUsage.call_point, filters=filters)
    by_model = _agg(factory, group_col=LLMUsage.model, filters=filters)
    by_user = _agg(factory, group_col=LLMUsage.user_id, filters=filters)[: args.top]
    by_thread = _agg(factory, group_col=LLMUsage.thread_id, filters=filters)[: args.top]

    status = _status_counts(factory, filters)
    sections = [
        ("总计", total, "范围"),
        ("按天", by_day, "日期"),
        ("按调用点", by_point, "调用点"),
        ("按模型", by_model, "模型"),
        ("按用户 top", by_user, "user_id"),
        ("按会话 top", by_thread, "thread_id"),
    ]

    meta = {
        "since": f"{since:%Y-%m-%d}",
        "until": f"{(until - timedelta(days=1)):%Y-%m-%d}" if until else None,
        # status=all 是"不过滤"，不进 filters 展示（避免误读成过滤条件）
        "filters": {
            k: v for k, v in filters.items() if k not in ("since", "until", "status") and v
        },
        "status": args.status,
        "input_price_per_mtok": settings.deepseek_input_price,
        "output_price_per_mtok": settings.deepseek_output_price,
        "status_counts": status,
    }

    if args.format == "csv":
        _print_csv(sections)
    elif args.format == "json":
        _print_json(sections, meta=meta)
    else:
        until_show = f"{(until - timedelta(days=1)):%Y-%m-%d}" if until else "今天"
        span = f"近 {args.days} 天" if args.until is None else f"{since:%Y-%m-%d} ~ {until_show}"
        header = [
            "=== LLM 调用成本报表（llm_usage）===",
            f"区间：{span}  过滤：{meta['filters'] or '无'}  status：{args.status}",
            (
                f"单价：输入 {settings.deepseek_input_price} 元/百万 token，"
                f"输出 {settings.deepseek_output_price} 元/百万 token（.env 可调）"
            ),
            (
                f"调用次数：{total[0][1]:,}（成功 {status.get('success', 0):,}"
                f" / 失败 {status.get('error', 0):,}）"
            ),
        ]
        _print_table(sections, header_lines=header)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
