"""M11 结构解析器离线测试（TDD：先样例后真实数据）。

构造迷你 HTML（章/条层级）与文本行，验证：
- HTML 结构探测（h2=章、h3=条、strong=条）
- 文本行结构探测（PDF 抽取后的正则路径）
- 章节过滤（黑名单）
- 时效检测
- 审计输出
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from parse_zjut_structure import (
    BLACKLIST_CHAPTERS,
    RE_ARTICLE,
    RE_CHAPTER,
    RE_CLAUSE,
    RE_TIMELINESS,
    build_audit,
    chunk_by_articles,
    chunk_source,
    detect_html_structure,
    detect_text_structure,
    filter_sections,
    mark_timeliness,
)

MINI_HTML = """
<html><body>
<h1>浙江工业大学2025级学生手册</h1>
<h2>第四章 学籍管理</h2>
<h3>第十二条 学生应当按时注册，因故不能按期注册者须履行暂缓注册手续。</h3>
<h3>第十三条 休学以一学年为限，累计不得超过两学年。</h3>
<h2>第五章 奖励与处分</h2>
<h3>第十四条 学校对品学兼优的学生给予表彰和奖励。</h3>
<h2>目录</h2>
<p>第四章 学籍管理 ..... 12</p>
<p>第五章 奖励与处分 ..... 20</p>
</body></html>
"""


class TestRegex:
    def test_chapter_re(self):
        assert RE_CHAPTER.search("第四章 学籍管理")
        assert RE_CHAPTER.search("第10章 附则")
        assert not RE_CHAPTER.search("第一条 总则")

    def test_article_re(self):
        assert RE_ARTICLE.search("第十二条 休学")
        assert RE_ARTICLE.search("第100条 附则")
        assert not RE_ARTICLE.search("第十二章 学籍")

    def test_clause_re(self):
        assert RE_CLAUSE.search("（一）申请休学")
        assert RE_CLAUSE.search("(2) 提交材料")
        assert not RE_CLAUSE.search("一、申请休学")

    def test_timeliness_re(self):
        assert RE_TIMELINESS.search("9月18日10:00开始")
        assert RE_TIMELINESS.search("2025-2026学年第一学期")
        assert RE_TIMELINESS.search("作息时间：夏令时7:00")
        assert not RE_TIMELINESS.search("学生应当按时注册")


class TestHtmlStructure:
    def test_detect_chapters_and_articles(self):
        sections = detect_html_structure(MINI_HTML, "mini")
        chapters = [s for s in sections if s.level == 1]
        articles = [s for s in sections if s.level == 2]
        assert any("第四章" in s.title for s in chapters)
        assert any("第五章" in s.title for s in chapters)
        assert len(articles) >= 3
        assert any("第十二条" in s.title for s in articles)

    def test_dedupe(self):
        html = MINI_HTML + "<h2>第四章 学籍管理</h2>"  # 重复标题
        sections = detect_html_structure(html, "mini")
        chapters = [s for s in sections if s.level == 1 and "第四章" in s.title]
        assert len(chapters) == 1


class TestTextStructure:
    def test_detect_from_pdf_like_lines(self):
        lines = [
            "第四章 学籍管理",
            "第十二条 学生应当按时注册。",
            "第十三条 休学以一学年为限。",
            "（一）办理流程见教务系统。",
        ]
        sections = detect_text_structure(lines, "pdf1")
        chapters = [s for s in sections if s.level == 1]
        articles = [s for s in sections if s.level == 2]
        assert len(chapters) == 1
        assert len(articles) == 2


class TestFilter:
    def test_blacklist_drops_toc(self):
        sections = detect_html_structure(MINI_HTML, "mini")
        sections = filter_sections(sections)
        toc = [s for s in sections if "目录" in s.title]
        assert toc and all(s.dropped for s in toc)
        kept = [s for s in sections if not s.dropped]
        assert any("第四章" in s.title for s in kept)

    def test_blacklist_constant_nonempty(self):
        assert BLACKLIST_CHAPTERS
        assert "目录" in BLACKLIST_CHAPTERS


class TestTimeliness:
    def test_mark(self):
        sections = detect_html_structure(MINI_HTML, "mini")
        sections[0].text = "9月18日10:00开始退补选"  # 注入时效文本
        sections = mark_timeliness(sections)
        assert sections[0].timeliness
        assert not any(s.timeliness for s in sections[1:])


class TestAudit:
    def test_audit_shape(self):
        sections = detect_html_structure(MINI_HTML, "mini")
        sections = mark_timeliness(filter_sections(sections))
        audit = build_audit(sections)
        assert audit["total"] == len(sections)
        assert audit["dropped"] == len([s for s in sections if s.dropped])
        assert audit["kept"] + audit["dropped"] == audit["total"]
        assert isinstance(audit["dropped_items"], list)


class TestChunkByArticles:
    def test_chunk_with_full_text(self):
        lines = [
            "第一章 总则",
            "第一条 学生应当按时注册。因故不能按期注册者须履行暂缓注册手续。",
            "第二条 休学以一学年为限。",
            "===== 第2页 =====",
            "复学须向学院申请。",  # 跨页续第二条正文
        ]
        blocks = chunk_by_articles(lines, "pdf1")
        assert len(blocks) == 2
        assert blocks[0].article == "第一条"
        assert "暂缓注册手续" in blocks[0].text
        assert blocks[1].article == "第二条"
        assert "复学须向学院申请" in blocks[1].text  # 跨页正文连续
        assert blocks[1].chapter == "第一章 总则"

    def test_page_marker_skipped(self):
        lines = ["===== 第1页 =====", "第一条 内容。", "===== 第2页 =====", "正文续。"]
        blocks = chunk_by_articles(lines, "s")
        assert len(blocks) == 1
        assert "正文续" in blocks[0].text

    def test_chunk_source_fallback_paragraph(self):
        # 无条款结构的普通网页 → 退化段落切块
        lines = ["这是图书馆入馆须知的第一个完整段落，内容足够长会被保留为一块。", "短行", "这是第二个完整段落，也超过二十字会被保留。"]
        blocks = chunk_source(lines, "lib")
        assert len(blocks) == 2

    def test_chunk_source_uses_articles(self):
        lines = ["第一条 内容一。", "正文。", "第二条 内容二。"]
        blocks = chunk_source(lines, "s")
        assert len(blocks) == 2
        assert blocks[0].article == "第一条"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
