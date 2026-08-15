"""知识库检索层（M1-ZJUT）：关键词匹配 + type 组装，可替换为向量检索（设计 §4.5）。

返回 list[dict]：{id, domain, keywords, question, type, answer}，按命中分降序取前 3。
"""

from campus_desk.db.models import KnowledgeEntry

_MAX_RESULTS = 3


def search_knowledge(session_factory, text: str) -> list[dict]:
    """关键词计分检索（命中问题/关键词任一即得分，多关键词累计）。"""
    with session_factory() as session, session.begin():
        rows = session.query(KnowledgeEntry).all()
    scored = []
    for row in rows:
        score = 0
        for kw in row.keywords.split(","):
            if kw and kw in text:
                score += 2
        if row.question and row.question in text:
            score += 1
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {
            "id": r.id,
            "domain": r.domain,
            "keywords": r.keywords,
            "question": r.question,
            "type": r.type,
            "answer": r.answer,
        }
        for _, r in scored[:_MAX_RESULTS]
    ]


def assemble_answer(hits: list[dict]) -> str:
    """按 type 组装回答（info 直接答 / process 拼清单 / index 拼引导）。"""
    if not hits:
        return ""
    if len(hits) == 1:
        return hits[0]["answer"]
    parts = [f"{i + 1}. {h['answer']}" for i, h in enumerate(hits)]
    return "为您找到以下相关信息：\n" + "\n".join(parts)
