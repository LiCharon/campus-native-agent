"""keywords 预填建议（M3 进化闭环）：管理页补入弹窗的拆词建议。

只做"建议"——管理员在弹窗里可编辑后提交（人工把关防污染是设计核心，
见 docs/design/ZJUT_DESIGN.md §5.5）。因此拆词规则务实即可：
按常见问句虚词/标点切分 → 去长度<2 的碎片 → 去重 → 取前 4 个逗号连接。
不引入分词库（演示项目不加依赖；整词作为关键词在子串计分下同样可命中）。
"""

import re

# 交替顺序：长词在前（"怎么办" 先于 "怎么"），避免被短词截断
_STOP_RE = re.compile(
    r"(?:怎么办|怎么|什么|哪里|如何|一下|几点|在哪|请问|怎么办理|"
    r"的|了|吗|呢|吧|啊|呀|？|\?|。|，|,|！|!|、|\s)+"
)

_MAX_KEYWORDS = 4


def suggest_keywords(question: str) -> str:
    """从问句拆出预填关键词（逗号分隔，最多 4 个，可为空串）。"""
    parts = [p for p in _STOP_RE.split(question.strip()) if len(p) >= 2]
    seen: list[str] = []
    for part in parts:
        if part not in seen:
            seen.append(part)
    return ",".join(seen[:_MAX_KEYWORDS])
