"""M11-ZJUT 结构解析器：从采集的 HTML/PDF 提取结构化条目。

两阶段（学校 CMS DOM 未见过，先探测后精调）：
1. probe：探测文档结构——输出候选章节/条款标题清单（正则编号 + HTML 标题标签），
   供确认结构规则后再进入 parse。
2. parse：按结构规则切分 → 条目（source/chapter/article/text）→ 章节过滤
   + 时效检测 → 审计报告 JSON。

零 LLM，全确定性规则（M11 grill 收敛：结构感知分块核心不依赖 LLM）。

用法：
    .venv/Scripts/python.exe scripts/parse_zjut_structure.py probe <html_or_pdf 路径>
    .venv/Scripts/python.exe scripts/parse_zjut_structure.py parse <data/zjut_raw 目录>
"""

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

RE_CHAPTER = re.compile(r"^第[一二三四五六七八九十百千\d]+[章篇]")
RE_ARTICLE = re.compile(r"^第[一二三四五六七八九十百千\d]+条")
RE_CLAUSE = re.compile(r"^[（(][一二三四五六七八九十\d]+[）)]")
# 目录页条目特征：标题带页码后缀（如 "第四章 学籍管理 ..... 12"）
RE_TOC = re.compile(r"\.{2,}\s*[\d\s]+$")
# 目录行特征（vision 提取/退化段落）："十二、标题……（120）" / "一、标题（5）"
RE_TOC_ENTRY = re.compile(r"^[一二三四五六七八九十百\d]+、.*[（(]\d+[）)]$")
# 时效：只认"具体日期/学期指示/时间安排"，裸"学年/学期"是常规词不误伤
RE_TIMELINESS = re.compile(
    r"\d{4}\s*年|\d{1,2}\s*月\s*\d{1,2}\s*日|本学期|下学期|每学期|作息时间|校历|夏令时|冬令时|"
    r"\d{4}\s*[-/]\s*\d{4}\s*学年"
)

# 章节级黑名单（M11 grill 收敛：封面/目录/前言/校训/思想教育等低问答价值章节丢弃）
BLACKLIST_CHAPTERS = [
    "目录", "前言", "序言", "引言", "写在前面", "校训", "校歌", "校徽", "校标",
    "思想教育", "思政", "爱国主义", "理想信念", "行为规范总则", "封面", "出版说明",
    "编委会", "后记",
]

# 时效处理：命中后由调用方决定丢弃或追加尾注
TIMELINESS_NOTE = "具体时间、地点与流程以学校最新官方通知为准。"


@dataclass
class Section:
    level: int  # 1=章 2=条 3=款
    title: str
    text: str
    source: str = ""
    chapter: str = ""
    article: str = ""
    timeliness: bool = False
    dropped: bool = False
    drop_reason: str = ""


@dataclass
class ParseResult:
    sections: list = field(default_factory=list)
    audit: dict = field(default_factory=dict)


def _norm_title(raw: str) -> str:
    return re.sub(r"\s+", " ", raw).strip()


def _is_toc_line(text: str) -> bool:
    """目录页条目：标题 + 点号页码后缀（如 '第四章 学籍管理 ..... 12'）。"""
    return bool(RE_TOC.search(text))


def detect_html_structure(html_text: str, source: str = "") -> list:
    """探测 HTML 中的章节/条款结构：标题标签 + 编号正则。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_text, "html.parser")
    sections: list = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "p", "strong", "b"]):
        text = _norm_title(tag.get_text())
        if not text or len(text) > 120:
            continue
        level = None
        if tag.name in ("h1", "h2"):
            level = 1
        elif tag.name == "h3":
            level = 2 if RE_ARTICLE.search(text) else 3
        elif tag.name == "h4":
            level = 3
        elif tag.name in ("strong", "b"):
            if RE_CHAPTER.search(text):
                level = 1
            elif RE_ARTICLE.search(text):
                level = 2
        if level is None and (RE_CHAPTER.search(text) or RE_ARTICLE.search(text)):
            level = 2 if RE_ARTICLE.search(text) else 1
        if level and not _is_toc_line(text):
            sections.append(Section(level=level, title=text, text=text, source=source))
    return _dedupe_sections(sections)


def detect_text_structure(lines: list, source: str = "") -> list:
    """探测纯文本行（PDF 抽取后）的章节/条款结构：编号正则。"""
    sections: list = []
    for raw in lines:
        text = _norm_title(raw)
        if not text:
            continue
        if RE_ARTICLE.search(text):
            sections.append(Section(level=2, title=text[:60], text=text, source=source))
        elif RE_CHAPTER.search(text) and not _is_toc_line(text):
            sections.append(Section(level=1, title=text[:60], text=text, source=source))
    return _dedupe_sections(sections)


def _dedupe_sections(sections: list) -> list:
    seen, out = set(), []
    for s in sections:
        key = (s.level, s.title)
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def detect_pdf_structure(pdf_path: Path, source: str = "") -> list:
    """探测 PDF 结构：pdfplumber 抽取每行文本 + 字号，正则匹配编号。"""
    import pdfplumber

    lines: list = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            lines.extend((page.extract_text() or "").splitlines())
    return detect_text_structure(lines, source)


def filter_sections(sections: list) -> list:
    """章节过滤：黑名单标题 + 目录页特征（短行密集无编号正文）。"""
    for s in sections:
        if any(kw in s.title for kw in BLACKLIST_CHAPTERS):
            s.dropped = True
            s.drop_reason = f"黑名单标题: {s.title[:30]}"
        elif RE_CHAPTER.search(s.title) and not any(
            RE_ARTICLE.search(c.title) or RE_CLAUSE.search(c.title) for c in sections if c.level >= 2
        ):
            # 章下无任何条/款 → 疑似目录/无正文结构，标告警不丢弃（留给人工）
            s.drop_reason = "章下无条/款（疑似目录或空章节），人工确认"
    return sections


def mark_timeliness(sections: list) -> list:
    for s in sections:
        s.timeliness = bool(RE_TIMELINESS.search(s.text))
    return sections


def chunk_by_articles(lines: list, source: str = "") -> list:
    """按条款边界切块：'第X条'行开新块，后续行累积为该块正文（跨行/跨页连续）。

    返回 Section 列表（level=2，text=完整条款正文，chapter=所属章，article=条款号）。
    分页标记行（如 '===== 第N页 ====='）与空行跳过；'第X章'行记录章节归属。
    """
    page_marker = re.compile(r"^===== 第\d+页 =====$")
    blocks: list[Section] = []
    cur: Section | None = None
    chapter = ""
    for raw in lines:
        line = _norm_title(raw)
        if not line or page_marker.match(line) or RE_TOC_ENTRY.match(line):
            continue
        if RE_ARTICLE.match(line):
            if cur is not None and cur.text.strip():
                blocks.append(cur)
            cur = Section(level=2, title=line, text=line, source=source, chapter=chapter)
            m = RE_ARTICLE.match(line)
            cur.article = m.group(0) if m else ""
            continue
        if RE_CHAPTER.match(line):
            chapter = line[:40]
            if cur is not None and cur.text.strip():
                blocks.append(cur)
                cur = None
            continue
        if cur is not None:
            cur.text += line
    if cur is not None and cur.text.strip():
        blocks.append(cur)
    return blocks


def build_audit(sections: list) -> dict:
    total = len(sections)
    dropped = [s for s in sections if s.dropped]
    timely = [s for s in sections if s.timeliness]
    return {
        "total": total,
        "kept": total - len(dropped),
        "dropped": len(dropped),
        "dropped_items": [{"title": s.title[:40], "reason": s.drop_reason} for s in dropped],
        "timeliness_hit": len(timely),
        "timeliness_items": [s.title[:40] for s in timely][:50],
    }


def probe(path: Path) -> None:
    """探测模式：输出结构报告（章节/条款标题清单），不产出正式条目。"""
    if path.suffix.lower() == ".pdf":
        sections = detect_pdf_structure(path, path.stem)
    else:
        sections = detect_html_structure(path.read_text(encoding="utf-8", errors="ignore"), path.stem)
    sections = mark_timeliness(filter_sections(sections))
    print(f"文件: {path}")
    print(f"探测到结构标题: {len(sections)} 个")
    for s in sections[:80]:
        flag = "DROP" if s.dropped else "KEEP"
        tl = " [时效]" if s.timeliness else ""
        print(f"  L{s.level} {flag}{tl} {s.title[:50]}")
    print("\n审计摘要:", json.dumps(build_audit(sections), ensure_ascii=False, indent=2))


def _pdf_lines(pdf_path: Path) -> list:
    import pdfplumber

    lines: list = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            lines.extend((page.extract_text() or "").splitlines())
    return lines


def _html_lines(html_text: str) -> list:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    return [line for line in soup.get_text("\n").splitlines() if line.strip()]


def chunk_source(text_lines: list, source: str) -> list:
    """条款切块；无条款结构时退化为段落切块（每非空段一块）。"""
    blocks = chunk_by_articles(text_lines, source)
    if blocks:
        return blocks
    # 兜底：无"第X条"的普通网页 → 每段一块（排除目录行与过短行）
    return [
        Section(level=2, title=line[:30], text=line, source=source)
        for line in text_lines
        if len(line.strip()) >= 20 and not RE_TOC_ENTRY.match(line.strip())
    ]


def parse_dir(raw_dir: Path) -> None:
    """解析模式：遍历 data/zjut_raw/ 下 html/、pdf/、ocr/*_full.txt，产出条款块 + 审计。"""
    out_dir = raw_dir / "parsed"
    out_dir.mkdir(parents=True, exist_ok=True)
    html_dir, pdf_dir, ocr_dir = raw_dir / "html", raw_dir / "pdf", raw_dir / "ocr"
    all_items, all_audit = [], {}

    for f in sorted(html_dir.glob("*.html") if html_dir.exists() else []):
        lines = _html_lines(f.read_text(encoding="utf-8", errors="ignore"))
        if sum(len(l) for l in lines) < 50:
            all_audit[f.stem] = {"skipped": "内容过短（疑似 IP 限制页/空壳）"}
            continue
        blocks = chunk_source(lines, f.stem)
        _finalize(blocks, f.stem, all_items, all_audit)
    for f in sorted(pdf_dir.glob("*.pdf") if pdf_dir.exists() else []):
        blocks = chunk_source(_pdf_lines(f), f.stem)
        _finalize(blocks, f.stem, all_items, all_audit)
    for f in sorted(ocr_dir.glob("*_full.txt") if ocr_dir.exists() else []):
        blocks = chunk_source(f.read_text(encoding="utf-8").splitlines(), f.stem)
        _finalize(blocks, f.stem, all_items, all_audit)

    (out_dir / "items.json").write_text(json.dumps(all_items, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "audit.json").write_text(json.dumps(all_audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"解析完成: {len(all_items)} 条款块 → {out_dir/'items.json'}")
    print(f"审计报告: {out_dir/'audit.json'}")
    for src, audit in all_audit.items():
        if "skipped" in audit:
            print(f"  {src:20s} {audit['skipped']}")
        else:
            print(f"  {src:20s} 总数={audit['total']:4d} 保留={audit['kept']:4d} 丢弃={audit['dropped']:3d} 时效={audit['timeliness_hit']:3d}")


def _finalize(sections: list, source: str, all_items: list, all_audit: dict) -> None:
    sections = mark_timeliness(filter_sections(sections))
    all_audit[source] = build_audit(sections)
    for s in sections:
        if s.dropped or s.timeliness:
            continue  # 丢弃/纯时效条目不产出；流程类时效尾注在 build 阶段追加
        all_items.append(
            {
                "source": source,
                "level": s.level,
                "title": s.title,
                "text": s.text,
                "chapter": s.chapter,
                "article": s.article,
            }
        )


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    mode, target = sys.argv[1], Path(sys.argv[2])
    if mode == "probe":
        probe(target)
    elif mode == "parse":
        parse_dir(target)
    else:
        print(f"未知模式: {mode}")
        sys.exit(1)


if __name__ == "__main__":
    main()
