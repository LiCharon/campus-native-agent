"""本地校园真实信息注入脚本（M1-T9）。

读取 config/zjut_local_data.json 逐条 upsert 到 knowledge_entries——
本地私有文件，已 gitignore（不进公开 repo），换机/克隆后需自行放置。
与 seed_all 同款幂等模式：幂等键 question，存在则更新字段、不存在则插入。

JSON 格式（数组）：
[
  {"domain": "教务", "keywords": "成绩,绩点", "question": "…？",
   "type": "info", "answer": "…"},
  ...
]

用法：`PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/seed_zjut_local.py`
配置文件不存在时打印提示并以 0 退出（可安全重跑，不报错）。
"""

import json
import sys
from pathlib import Path

from sqlalchemy import select

from campus_desk.db.models import KnowledgeEntry
from campus_desk.db.seed import seed_all
from campus_desk.db.session import default_session_factory

_CONFIGS = [
    Path(__file__).resolve().parent.parent / "config" / "zjut_local_data.json",
    # M11 采集产物（2026-08-25：手册/制度/图书馆条款，build_zjut_entries.py 输出）
    Path(__file__).resolve().parent.parent / "config" / "zjut_m11_data.json",
]


def main() -> int:
    factory = default_session_factory()
    # 先保证通用种子存在（users + 36 条），本地条目作为注入层叠加
    # force=True：把 seed.py 中的领域重命名（后勤→住宿后勤 等）同步到已有通用行，
    # 保证全库领域词表与 9 领域契约一致（本地条目自身按 question 幂等 upsert，不受影响）
    seed_all(factory, force=True)

    total_touched = 0
    for config in _CONFIGS:
        if not config.exists():
            print(f"[seed_zjut_local] 未找到 {config}（本地私有文件，不进 git）——跳过。")
            continue
        data = json.loads(config.read_text(encoding="utf-8"))
        touched = 0
        with factory() as session, session.begin():
            for item in data:
                domain = item["domain"]
                keywords = (item["keywords"] or "")[:120]  # String(128) 留余量
                question = (item["question"] or "")[:250]  # String(256) 留余量
                ktype = item.get("type", "info")
                answer = item["answer"]
                obj = session.execute(
                    select(KnowledgeEntry).where(KnowledgeEntry.question == question)
                ).scalar_one_or_none()
                if obj is None:
                    session.add(
                        KnowledgeEntry(
                            domain=domain,
                            keywords=keywords,
                            question=question,
                            type=ktype,
                            answer=answer,
                        )
                    )
                else:
                    obj.domain, obj.keywords, obj.type, obj.answer = domain, keywords, ktype, answer
                touched += 1
        print(f"[seed_zjut_local] 注入/更新 {touched} 条（来源 {config.name}）。")
        total_touched += touched

    print(f"[seed_zjut_local] 合计 {total_touched} 条。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
