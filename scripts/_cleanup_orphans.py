"""一次性孤儿条目清理脚本（验证后删除）。

删除 knowledge_entries 中既不在通用种子(_KNOWLEDGE 36 条)也不在
本地 JSON(240 条)的残留行——这些多半是历次 question 改名/合并后
遗留在库里的旧问句（如「宿舍洗衣怎么用？」「学校大概有多少个社团？」
「毕业户口怎么迁？」「在校外怎么用 VPN 访问校内资源？」等），
会制造近重复、并可能在检索时返回陈旧/错误答案。

通用种子占位（仍在 36 条内）与本地真实条目都会保留。
"""
import json
import sys
from pathlib import Path

from sqlalchemy import delete, select

from campus_desk.db.models import KnowledgeEntry
from campus_desk.db.seed import _KNOWLEDGE
from campus_desk.db.session import default_session_factory

_CONFIG = Path(__file__).resolve().parent.parent / "config" / "zjut_local_data.json"


def main() -> int:
    generic_q = {q for (_, _, q, _, _) in _KNOWLEDGE}
    local_q = {item["question"] for item in json.loads(_CONFIG.read_text(encoding="utf-8"))}
    valid = generic_q | local_q

    factory = default_session_factory()
    with factory() as session:
        rows = session.execute(select(KnowledgeEntry)).scalars().all()
        orphans = [r for r in rows if r.question not in valid]
        print(f"DB 当前 {len(rows)} 条；合法集合(通用∪本地) {len(valid)} 条；孤儿 {len(orphans)} 条：")
        for r in orphans:
            print(f"  [删除] 「{r.question}」 (domain={r.domain})")
        if orphans:
            qset = [r.question for r in orphans]
            session.execute(delete(KnowledgeEntry).where(KnowledgeEntry.question.in_(qset)))
            session.commit()
            print(f"已删除 {len(orphans)} 条孤儿。")
        else:
            print("无孤儿，无需清理。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
