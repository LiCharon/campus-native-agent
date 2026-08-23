"""M10 重建脚本：读全表 → fastembed 向量化 → 写 Qdrant（可用时）+ 写 MySQL 稠密向量（始终）。

用法：
  python scripts/rebuild_qdrant.py                  # 用 .env 的 QDRANT_URL
  python scripts/rebuild_qdrant.py --qdrant-url http://localhost:6333
  python scripts/rebuild_qdrant.py --mysql-only      # 只刷 MySQL 稠密向量（不开 Qdrant）

幂等：重复运行覆盖。首次会在本地下载 bge-small-zh 模型（约 130MB，需联网一次）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from campus_desk.config import settings
from campus_desk.db.session import default_session_factory
from campus_desk.knowledge import vector_store


def main() -> None:
    ap = argparse.ArgumentParser(description="重建知识库向量（Qdrant + MySQL 稠密向量）")
    ap.add_argument("--qdrant-url", default=None, help="覆盖 settings.qdrant_url")
    ap.add_argument("--mysql-only", action="store_true", help="只刷 MySQL 稠密向量，不连 Qdrant")
    args = ap.parse_args()

    if args.qdrant_url:
        settings.qdrant_url = args.qdrant_url
    if args.mysql_only:
        settings.qdrant_url = ""

    factory = default_session_factory()
    result = vector_store.rebuild_all(factory)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
