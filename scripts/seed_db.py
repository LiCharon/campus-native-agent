"""种子数据入库脚本（M3）：连 .env 配置的 MySQL 跑幂等种子。

用法：`PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/seed_db.py [--force]`
--force：按幂等键更新字段（默认只插入缺失行）。
"""

import argparse
import sys

from campus_desk.db.seed import seed_all
from campus_desk.db.session import default_session_factory


def main() -> int:
    parser = argparse.ArgumentParser(description="CampusDesk 种子数据入库（MySQL，幂等）")
    parser.add_argument("--force", action="store_true", help="按幂等键更新已存在行")
    args = parser.parse_args()

    factory = default_session_factory()
    counts = seed_all(factory, force=args.force)
    print("种子入库完成（写入行数）：")
    for table, n in counts.items():
        print(f"  {table}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
