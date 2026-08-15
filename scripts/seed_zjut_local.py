"""浙工大（ZJUT）真实信息本地注入脚本（M1-T9）。

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

_CONFIG = Path(__file__).resolve().parent.parent / "config" / "zjut_local_data.json"


def main() -> int:
    if not _CONFIG.exists():
        print(f"[seed_zjut_local] 未找到 {_CONFIG}（本地私有文件，不进 git）——跳过注入。")
        return 0

    data = json.loads(_CONFIG.read_text(encoding="utf-8"))
    factory = default_session_factory()
    # 先保证通用种子存在（users + 36 条），本地条目作为注入层叠加
    seed_all(factory)

    touched = 0
    with factory() as session, session.begin():
        for item in data:
            domain = item["domain"]
            keywords = item["keywords"]
            question = item["question"]
            ktype = item.get("type", "info")
            answer = item["answer"]
            obj = session.execute(
                select(KnowledgeEntry).where(KnowledgeEntry.question == question)
            ).scalar_one_or_none()
            if obj is None:
                session.add(
                    KnowledgeEntry(domain=domain, keywords=keywords, question=question,
                                   type=ktype, answer=answer)
                )
            else:
                obj.domain, obj.keywords, obj.type, obj.answer = domain, keywords, ktype, answer
            touched += 1

    print(f"[seed_zjut_local] 注入/更新 {touched} 条本地知识（来源 {_CONFIG}）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
