"""一次性 KB 核验脚本（验证后删除）。"""
import difflib
import re
import sys
from collections import Counter

from sqlalchemy import select

from campus_desk.db.models import KnowledgeEntry
from campus_desk.db.session import default_session_factory

_DOMAINS = [
    "教务", "图书馆", "网络与IT", "校园卡与证件", "住宿后勤", "奖助",
    "医疗健康", "社团与活动", "就业与职业发展", "安全与保卫", "生活服务",
]
_PUNCT_RE = re.compile(
    r"[\s，。？！、；：\"'（）()「」【】《》…—\-·.,?!;:\"'`()\[\]{}<>~`@#$%^&*_+=|\\/]"
)


def _norm(s: str) -> str:
    return _PUNCT_RE.sub("", s).lower()


def _sim(a, b):
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def main() -> int:
    factory = default_session_factory()
    with factory() as session:
        rows = session.execute(select(KnowledgeEntry)).scalars().all()
        total = len(rows)
        counter = Counter(r.domain for r in rows)
        questions = [r.question for r in rows]

        # 1) 定义域白名单
        bad = sorted({r.domain for r in rows if r.domain not in _DOMAINS})
        # 2) 精确重复
        seen = set()
        exact_dups = [q for q in questions if q in seen or seen.add(q) or False]
        # 重新算精确重复
        seen = set()
        exact_dups = []
        for q in questions:
            if q in seen:
                exact_dups.append(q)
            else:
                seen.add(q)
        # 3) 近重复 >= 0.85
        near = []
        for i in range(len(questions)):
            for j in range(i + 1, len(questions)):
                s = _sim(questions[i], questions[j])
                if s >= 0.85:
                    near.append(f"  {s:.2f}  「{questions[i]}」 ⇔ 「{questions[j]}」")
        # 4) 空字段
        empty = [r.question for r in rows if not (r.domain and r.keywords and r.question and r.type and r.answer)]

        print(f"总条数: {total}")
        print("领域分布:")
        for d in _DOMAINS:
            print(f"    {d}: {counter.get(d, 0)}")
        extra = {d: c for d, c in counter.items() if d not in _DOMAINS}
        if extra:
            print(f"    [非白名单域] {extra}")
        print(f"精确重复 question 数: {len(exact_dups)}")
        if exact_dups:
            print("  " + "\n  ".join(exact_dups[:20]))
        print(f"近重复(>=0.85) 对数: {len(near)}")
        if near:
            print("\n".join(near[:40]))
        print(f"空字段条目数: {len(empty)}")
        if bad:
            print(f"[FAIL] 存在非白名单域: {bad}")
            return 1
        if exact_dups:
            print("[FAIL] 存在精确重复")
            return 1
        if near:
            print("[FAIL] 存在近重复 >=0.85")
            return 1
        if empty:
            print("[FAIL] 存在空字段")
            return 1
        print("[OK] 核验通过：11 域全覆盖、无精确重复、无近重复、无空字段、无非法域。")
        return 0


if __name__ == "__main__":
    sys.exit(main())
