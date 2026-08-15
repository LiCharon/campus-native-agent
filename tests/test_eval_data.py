"""评测集数据质量锁定：数量达标 / schema 合法 / 标注-路由一致性 / 区分度抽样。

这些测试不调 LLM，锁定的是"数据本身没坏"——剧本进 git 前必须过这道门。
"""

import re

from campus_desk.eval.loader import COUNT_RANGES, load_all, validate_dataset


def test_dataset_counts_within_ranges():
    cases = load_all()
    assert len(cases) >= 24, f"总剧本数不足: {len(cases)}"
    by_category: dict[str, int] = {}
    for case in cases:
        by_category[case.category] = by_category.get(case.category, 0) + 1
    for category, (lo, hi) in COUNT_RANGES.items():
        count = by_category.get(category, 0)
        assert lo <= count <= hi, f"{category}: {count} 不在 [{lo}, {hi}]"


def test_dataset_validation_passes():
    problems = validate_dataset(load_all())
    assert problems == [], f"数据校验失败: {problems}"


def test_ids_unique_and_well_formed():
    cases = load_all()
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids))
    # 编号规范：zjut-intent-序号（如 zjut-intent-001）
    for case in cases:
        assert re.fullmatch(r"zjut-intent-\d{3}", case.id), f"{case.id} 编号不规范"


def test_inputs_are_distinct_not_paraphrase_clones():
    """区分度检查：同类剧本的输入不得大量重复（防换皮凑数）。

    全序重复直接报错；相似度抽验交给人工评审（note 字段已记录场景差异）。
    """
    cases = load_all()
    by_category: dict[str, list[str]] = {}
    for case in cases:
        by_category.setdefault(case.category, []).append(case.student_input)
    for category, inputs in by_category.items():
        assert len(set(inputs)) == len(inputs), f"{category}: 存在完全重复的输入"


def test_every_case_has_scenario_note():
    """评审辅助：每条剧本必须有场景说明（note），保证可评审、可复盘。"""
    for case in load_all():
        assert case.note.strip(), f"{case.id}: 缺 note 场景说明"
