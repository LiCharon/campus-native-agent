"""BUG-005 回归护栏：来源 chip 的字段契约（后端蛇形 ref_id ↔ 前端按同名读取）。

背景：M4 上线以来，前端 `Chat.vue:srcDetail()` 读驼峰 `s.refId`、后端 `SourceItem` 序列化
蛇形 `ref_id` → 恒为 undefined，来源行渲染成"来源：undefined process型 · 住宿后勤"。
一行可修，但没护栏就会再犯（前后端命名风格不一是根因），故锁两端契约：
- 后端：SourceItem 序列化必须是 ref_id（不得引入 camelCase 别名把前端又坑一次）
- 前端：不得再出现 .refId 读取

为什么用源码断言而不是跑前端测试：仓库无前端测试基建（无 vitest/jest），
而 M13B 已有"grep 归零"式验收先例，成本最低且防得住。
"""

from pathlib import Path

from campus_desk.api.schemas import SourceItem

_CHAT_VUE = Path(__file__).resolve().parents[1] / "frontend" / "src" / "views" / "Chat.vue"


def test_source_item_serializes_snake_case_ref_id():
    """后端序列化保持蛇形：前端按 ref_id 取，别名改成驼峰会立刻回归 BUG-005。"""
    item = SourceItem(type="kb", label="知识库", ref_id="#K33", detail="process型 · 教务")
    dumped = item.model_dump()
    assert dumped["ref_id"] == "#K33"
    assert "refId" not in dumped


def test_frontend_reads_snake_case_ref_id():
    """前端不得再按驼峰 refId 取来源编号。"""
    src = _CHAT_VUE.read_text(encoding="utf-8")
    assert ".refId" not in src
    assert "ref_id" in src
