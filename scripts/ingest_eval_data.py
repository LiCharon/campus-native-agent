"""评测集入库脚本（M3）：JSON 剧本 → .env 配置的 MySQL。

用法：`PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/ingest_eval_data.py`
幂等（EvalCase upsert + EvalTurn 先删后插），可重复跑。
"""

import sys

from campus_desk.db.session import default_session_factory
from campus_desk.eval.ingest import ingest_cases


def main() -> int:
    factory = default_session_factory()
    cases, turns = ingest_cases(factory)
    print(f"评测集入库完成：{cases} 条剧本，{turns} 条轮次断言")
    return 0


if __name__ == "__main__":
    sys.exit(main())
