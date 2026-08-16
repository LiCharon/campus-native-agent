"""keywords 预填建议函数测试（M3）：管理页补入弹窗的拆词建议。

规则：按常见问句虚词切分 → 去长度<2 的碎片 → 去重 → 取前 4 个逗号连接。
只做预填建议，管理员可编辑（人工把关防污染是设计核心）。
"""

from campus_desk.knowledge.suggest import suggest_keywords


def test_typical_question():
    assert suggest_keywords("研究生导师怎么选？") == "研究生导师"


def test_multi_keywords_kept_order():
    assert suggest_keywords("图书馆座位怎么预约？") == "图书馆座位,预约"


def test_empty_input():
    assert suggest_keywords("") == ""
    assert suggest_keywords("   ") == ""


def test_all_stopwords_yields_empty():
    assert suggest_keywords("怎么怎么办的呢？") == ""


def test_result_capped_at_four():
    kw = suggest_keywords("一卡通丢了怎么补办挂失充值？")
    assert len(kw.split(",")) <= 4


def test_deduplicated():
    assert suggest_keywords("选课退课怎么选课？") == "选课退课,选课"
