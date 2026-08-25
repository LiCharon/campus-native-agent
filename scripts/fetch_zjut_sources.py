"""M11-ZJUT 探源脚本：下载浙江工业大学官方公开文档（Tier 1）。

只下载学校官方公开发布的内容（信息公开网 / 官网公开下载 / 图书馆读者手册），
静态页面 + 公开 PDF，无登录、无反爬。每个源独立容错，失败记入 manifest 不中断。

用法：
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/fetch_zjut_sources.py

产物：
    data/zjut_raw/                  （gitignored，见 .gitignore）
        html/<name>.html            网页源
        pdf/<name>.pdf              PDF 源
        manifest.json               每个源的状态记录（url/status/size/path/note）
"""

import json
import time
from datetime import datetime
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
TIMEOUT = 20
SLEEP = 0.8  # 限速，礼貌爬取

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "zjut_raw"

# (name, url, kind)  kind: html | pdf
# 全 https + verify=False（jwc 证书 hostname 不匹配，本机 schannel 问题）+ 跟随重定向
SOURCES = [
    # 教务处 · 学生手册 2025 级（网页版）
    ("手册_2025级", "https://www.jwc.zjut.edu.cn/2025/0827/c234a319039/page.htm", "html"),
    # 信息公开网 · 学籍管理细则（网页版）
    ("学籍管理细则", "https://www.jwc.zjut.edu.cn/2021/0917/c1832a104782/page.htm", "html"),
    # 信息公开网 · 本科生资助管理办法（PDF 直链）
    ("资助管理办法", "https://info.zjut.edu.cn/zjutinfo/UploadFile/2021/9/24/bkszz.pdf", "pdf"),
    # 信息公开网 · 学生申诉处理规定（PDF 直链）
    ("学生申诉处理规定", "https://info.zjut.edu.cn/zjutinfo/UploadFile/2021/9/24/xsss.pdf", "pdf"),
    # 信息公开网 · 学校章程（PDF 直链）
    ("学校章程", "https://info.zjut.edu.cn/zjutinfo/UploadFile/2021/9/24/2020zc.pdf", "pdf"),
    # 学工部 · 本科生奖励处罚办法（页面）
    ("奖励处罚办法", "https://www.xgb.zjut.edu.cn/xgbwz/IndexMotion!ListOneNews.do?oneNews.id=7", "html"),
    # 计划财务处 · 学费收费项目公示表
    ("学费收费公示", "https://www.jcc.zjut.edu.cn/424/list.htm", "html"),
    # 计划财务处 · 学生公寓收费标准
    ("公寓收费标准", "https://www.jcc.zjut.edu.cn/425/list.htm", "html"),
    # 图书馆 · 读者手册（入馆须知 / 借还规则）
    ("图书馆读者手册", "https://xszl.lib.zjut.edu.cn/introdzsc.php", "html"),
    # 就业信息网 · 主页（先探列表结构）
    ("就业信息网主页", "https://job.zjut.edu.cn", "html"),
]


def fetch_one(session: requests.Session, name: str, url: str, kind: str) -> dict:
    entry = {"name": name, "url": url, "kind": kind, "status": None, "size": 0, "path": None, "note": ""}
    try:
        resp = session.get(url, timeout=TIMEOUT, verify=False)
        entry["status"] = resp.status_code
        if resp.status_code != 200:
            entry["note"] = f"HTTP {resp.status_code}"
            return entry
        content = resp.content
        entry["size"] = len(content)
        if kind == "pdf":
            out = RAW_DIR / "pdf" / f"{name}.pdf"
        else:
            out = RAW_DIR / "html" / f"{name}.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(content)
        entry["path"] = str(out.relative_to(RAW_DIR.parent.parent))
        # 简易内容嗅探：PDF 看头，HTML 看编码
        if kind == "pdf" and not content.startswith(b"%PDF"):
            entry["note"] = "非 PDF 内容（可能被重定向到提示页）"
        if kind == "html":
            head = content[:512].decode("utf-8", errors="ignore")
            entry["note"] = f"charset 待定; 首部含 <title>: {'<title>' in head.lower()}"
    except requests.RequestException as exc:
        entry["note"] = f"请求异常: {exc.__class__.__name__}"
    return entry


def main() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = UA
    results = []
    for name, url, kind in SOURCES:
        entry = fetch_one(session, name, url, kind)
        results.append(entry)
        flag = "OK " if entry["status"] == 200 else "FAIL"
        print(f"[{flag}] {name:16s} {kind:4s} {entry['status']} {entry['size']:>9d}B {entry['note']}")
        time.sleep(SLEEP)
    manifest = {
        "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "total": len(results),
        "ok": sum(1 for r in results if r["status"] == 200),
        "sources": results,
    }
    (RAW_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nmanifest: {RAW_DIR / 'manifest.json'}")


if __name__ == "__main__":
    main()
