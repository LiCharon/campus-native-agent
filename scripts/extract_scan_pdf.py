"""M11 扫描件 OCR 轨道：扫描型 PDF 逐页用 DS 多模态提取文本。

输入：纯扫描 PDF（无文字层，如印刷版学生手册扫描件）。
输出：data/zjut_raw/ocr/pages/NNNN.txt（按页，断点续跑）+ 合并全文 full.txt + fail 报告。

用法：
    .venv/Scripts/python.exe scripts/extract_scan_pdf.py [--pdf data/zjut_raw/pdf/手册_2025级.pdf] [--start N] [--end N]
"""

import argparse
import base64
import io
import sys
import time
from pathlib import Path

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pdfplumber
from langchain_openai import ChatOpenAI

from campus_desk.config import settings

PROMPT = (
    "你是校园文档录入员。请完整提取这张扫描页的所有文字，"
    "保留原有章节结构（如第X章/第X条/（一）/1.），逐条输出原文，"
    "不要概括、不要遗漏、不要添加任何内容。若该页是封面/目录/空白页，"
    "如实输出页面上有的内容即可。"
)


def page_to_png(pdf_path: Path, page_idx: int, resolution: int = 120) -> bytes:
    with pdfplumber.open(str(pdf_path)) as pdf:
        img = pdf.pages[page_idx].to_image(resolution=resolution)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


def vision_extract(llm, png: bytes) -> str:
    b64 = base64.b64encode(png).decode()
    resp = llm.invoke(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }
        ]
    )
    return str(resp.content).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="扫描 PDF 逐页 DS vision 提取")
    parser.add_argument("--pdf", default=str(ROOT / "data" / "zjut_raw" / "pdf" / "手册_2025级.pdf"))
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--end", type=int, default=0, help="0=到最后一页")
    parser.add_argument("--delay", type=float, default=0.5, help="页间延迟秒，礼貌限速")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    out_dir = ROOT / "data" / "zjut_raw" / "ocr" / "pages"
    out_dir.mkdir(parents=True, exist_ok=True)

    with pdfplumber.open(str(pdf_path)) as pdf:
        total = len(pdf.pages)
        end = args.end or total
        end = min(end, total)
        print(f"[extract_scan_pdf] {pdf_path.name} 共 {total} 页，本次处理 {args.start+1}–{end} 页")

        llm = ChatOpenAI(
            model=settings.deepseek_model,
            base_url="https://api.deepseek.com",
            api_key=settings.deepseek_api_key,
            temperature=0,
            timeout=90,
        )

        fails: list[int] = []
        t0 = time.time()
        for i in range(args.start, end):
            out_file = out_dir / f"{i + 1:04d}.txt"
            if out_file.exists() and out_file.stat().st_size > 0:
                continue  # 断点续跑：已提取跳过
            text = ""
            for attempt in (1, 2, 3):
                try:
                    png = page_to_png(pdf_path, i)
                    text = vision_extract(llm, png)
                    if text:
                        break
                except Exception as exc:  # noqa: BLE001
                    print(f"  第{i + 1}页 第{attempt}次失败: {exc.__class__.__name__}")
                    time.sleep(2 * attempt)
            if text:
                out_file.write_text(text, encoding="utf-8")
                elapsed = time.time() - t0
                print(f"[{i + 1}/{end}] {len(text)} 字 ({elapsed:.0f}s)")
            else:
                fails.append(i + 1)
                print(f"[{i + 1}/{end}] 提取失败，记录")
            time.sleep(args.delay)

    # 合并全文（保留分页标记便于跨页条款合并）
    full_path = ROOT / "data" / "zjut_raw" / "ocr" / f"{pdf_path.stem}_full.txt"
    pages = sorted(out_dir.glob("*.txt"))
    with full_path.open("w", encoding="utf-8") as f:
        for p in pages:
            f.write(f"\n===== 第{p.stem.lstrip('0')}页 =====\n")
            f.write(p.read_text(encoding="utf-8"))
    print(f"[extract_scan_pdf] 全文合并: {full_path}（{len(pages)} 页）")
    if fails:
        print(f"[extract_scan_pdf] 失败页: {fails}")
    return 0 if not fails else 2


if __name__ == "__main__":
    raise SystemExit(main())
