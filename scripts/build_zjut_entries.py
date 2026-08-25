"""M11-ZJUT build 抽取管道：解析器产物(items.json) → 标准知识条目 JSON。

5 道工序（2026-08-25 grill 收敛）：
① 分类：流程类(process)/条文类(info)——规则关键词计分，零 LLM
② 流程类 → DS API 转 FAQ（question/keywords/answer；domain 走规则映射）
③ 条文类 → 原文直存（question=条款标题，keywords 规则提炼）
④ 双关卡去重：difflib（采集内部，文本级）‖ dense 余弦（vs 全库 zjut_local_data.json，语义级）
⑤ 收尾：answer 首句来源标注 + 末尾核实指引

输出 config/zjut_m11_data.json（与 zjut_local_data.json 同构，gitignored）。
LLM/向量不可用时降级（--mock-llm / EmbeddingUnavailable 跳过 dense 关卡），不阻断主流程。

用法：
    .venv/Scripts/python.exe scripts/build_zjut_entries.py [--items data/zjut_raw/parsed/items.json]
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections import Counter
from pathlib import Path

import jieba

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from campus_desk.knowledge.embeddings import EmbeddingUnavailable, embed_dense
from campus_desk.llm import build_llm

_DOMAINS = [
    "教务", "图书馆", "网络与IT", "校园卡与证件", "住宿后勤", "奖助", "医疗健康",
    "社团与活动", "就业与职业发展", "安全与保卫", "生活服务",
]

# 流程类判定：流程词命中数 > 规定词命中数（且标题含流程词有加权）→ process
_PROCESS_WORDS = [
    "申请", "办理", "流程", "手续", "补办", "登记", "报名", "预约", "申报", "申领",
    "认定", "开通", "激活", "提交", "步骤", "怎么办", "如何", "缴费", "注册", "挂失",
    "签订", "转移", "换发", "材料", "审批", "签订",
]
_RULE_WORDS = [
    "应当", "不得", "禁止", "给予", "处以", "依照", "按照", "违反", "之日起",
    "以内", "以上", "以下", "实行", "负责", "严禁", "取消",
]

# domain 规则映射（M4.5 机制：关键词计分取最高，平手按 _DOMAINS 顺序）
_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "教务": ["选课", "成绩", "课表", "考试", "学分", "转专业", "缓考", "补考", "重修", "绩点",
             "毕业", "学位", "军训", "四六级", "校历", "学籍", "休学", "复学", "注册", "开题",
             "答辩", "查重", "送审", "交换生", "访学", "培养方案"],
    "图书馆": ["图书馆", "图书", "借书", "还书", "续借", "馆藏", "数据库", "文献", "阅览",
               "座位", "研讨室", "查新", "借阅", "馆际"],
    "网络与IT": ["校园网", "网络", "上网", "账号", "密码", "邮箱", "云盘", "软件", "无线",
                 "wifi", "vpn", "门户", "认证", "宽带", "客户端"],
    "校园卡与证件": ["校园卡", "一卡通", "饭卡", "学生证", "挂失", "充值", "火车票", "优惠卡",
                    "卡务", "写卡", "证件"],
    "住宿后勤": ["宿舍", "寝室", "住宿", "报修", "热水", "电费", "空调", "洗衣", "食堂",
                 "快递", "班车", "饮水", "门禁", "场馆", "体测", "体育馆"],
    "奖助": ["奖学金", "助学金", "资助", "贷款", "困难", "勤工", "补贴", "绿色通道", "励志",
             "补助", "学费补偿", "代偿", "无息借款", "精弘"],
    "医疗健康": ["医保", "医院", "医疗", "看病", "报销", "体检", "疫苗", "心理", "健康",
                 "急诊", "挂号", "转诊", "校医院", "门诊"],
    "社团与活动": ["社团", "百团", "第二课堂", "志愿", "工时", "社会实践", "学生会", "团委",
                   "活动", "积分", "劳育", "美育"],
    "就业与职业发展": ["就业", "招聘", "简历", "面试", "签约", "档案", "创业", "实习", "求职",
                       "报到证", "去向", "三方", "见习", "基层就业"],
    "安全与保卫": ["保卫", "报警", "户籍", "消防", "诈骗", "反诈", "监控", "失物", "安全",
                   "居住证", "电动车", "校门", "通行"],
    "生活服务": ["学费", "缴费", "计财", "周边", "交通", "超市", "地图", "电话", "常识",
                 "美食", "退费", "缓缴", "校历查询"],
}

# 来源 → 文档名标签（来源标注首句用）
_SOURCE_LABEL = {
    "手册_2025级": "浙江工业大学2025级学生手册",
    "学籍管理细则": "浙江工业大学本科学生学籍管理细则",
    "资助管理办法": "浙江工业大学本科生资助管理办法",
    "奖励处罚办法": "浙江工业大学本科生奖励处罚办法",
    "学生申诉处理规定": "浙江工业大学学生申诉处理规定",
    "学校章程": "浙江工业大学章程",
    "学费收费公示": "浙江工业大学学费收费项目公示表",
    "公寓收费标准": "浙江工业大学学生公寓收费标准",
    "图书馆读者手册": "浙江工业大学图书馆读者手册",
    "就业信息网主页": "浙江工业大学就业信息网",
}

# 来源 → 核实渠道（核实指引末尾用）
_SOURCE_CHANNEL = {
    "手册_2025级": "教务处官网 jwc.zjut.edu.cn",
    "学籍管理细则": "教务处官网 jwc.zjut.edu.cn",
    "资助管理办法": "学生工作部（学生资助管理中心）",
    "奖励处罚办法": "学生工作部（学工部）",
    "学生申诉处理规定": "教务处与校团委",
    "学校章程": "学校办公室",
    "学费收费公示": "计划财务处 jcc.zjut.edu.cn",
    "公寓收费标准": "计划财务处 jcc.zjut.edu.cn",
    "图书馆读者手册": "图书馆官网 lib.zjut.edu.cn",
    "就业信息网主页": "就业信息网 zjut.jysd.com",
}

_DEFAULT_CHANNEL = "学校官方最新通知"
# 0.95：模板条款（不同办法的"第二条 本办法适用于…"）0.94 仅告警；1.00 级真重复仍阻断
_NEAR_ERROR = 0.95   # difflib 采集内部：阻断
_NEAR_WARN = 0.55    # difflib 采集内部：告警
_DENSE_BLOCK = 0.92  # dense 余弦 vs 全库：阻断
_DENSE_WARN = 0.85   # dense 余弦 vs 全库：告警

_PUNCT_RE = re.compile(
    r"[\s，。？！、；：\"'（）()「」【】《》…—\-·.,?!;:\"'`()\[\]{}<>~`@#$%^&*_+=|\\/]"
)


def _norm(s: str) -> str:
    return _PUNCT_RE.sub("", s).lower()


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def classify(text: str, title: str = "") -> str:
    """① 分类：流程类 → "process"，条文类 → "info"（规则计分，零 LLM）。"""
    proc = sum(1 for w in _PROCESS_WORDS if w in text)
    rule = sum(1 for w in _RULE_WORDS if w in text)
    if any(w in title for w in ("怎么", "如何", "申请", "办理", "流程", "步骤")):
        proc += 2
    return "process" if proc > rule else "info"


def map_domain(text: str) -> str:
    """domain 规则映射：关键词计分取最高，平手按 _DOMAINS 顺序。"""
    best, best_score = _DOMAINS[0], -1
    for d in _DOMAINS:
        score = sum(1 for w in _DOMAIN_KEYWORDS[d] if w in text)
        if score > best_score:
            best, best_score = d, score
    return best


def extract_keywords(title: str) -> list[str]:
    """keywords 规则提炼：条款标题去编号 → jieba 分词取实词 top4。"""
    t = re.sub(r"^第[一二三四五六七八九十百千\d]+条", "", title).strip()
    t = re.sub(r"^[（(][一二三四五六七八九十\d]+[）)]", "", t).strip()
    words = [w for w in jieba.lcut(t) if len(w) > 1 and not _PUNCT_RE.match(w)]
    stop = {"以及", "或者", "按照", "根据", "有关", "规定", "办法", "条例", "细则"}
    words = [w for w in words if w not in stop]
    return words[:4] or ["办事"]


def _llm_to_faq(text: str, source: str, title: str) -> dict:
    """② 流程类 → DS API 转 FAQ（question/keywords/answer；domain 由调用方 map_domain）。"""
    llm = build_llm()
    label = _SOURCE_LABEL.get(source, source)
    prompt = (
        "你是校园智能服务台的运营编辑。请把下面这条校园制度条款改写为一条"
        "学生视角的 FAQ 问答，输出 JSON（必须含 json 字样，格式如下）：\n"
        '{"question": "学生视角的自然问句", "keywords": "逗号分隔的关键词 3-8 个", '
        '"answer": "忠实于条款的答案正文，口语化，不编造条款没有的信息"}\n'
        f"条款来源：{label}\n条款标题：{title}\n条款正文：\n{text}\n"
    )
    resp = llm.invoke(prompt)
    content = resp.content
    content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.DOTALL).strip()
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        # 兜底：从文本里抠出 question/keywords/answer 字段
        data = {
            "question": title or "相关事务咨询",
            "keywords": ",".join(extract_keywords(title)),
            "answer": text,
        }
    return {
        "question": str(data.get("question", "")).strip(),
        "keywords": str(data.get("keywords", "")).strip(),
        "answer": str(data.get("answer", "")).strip() or text,
    }


def finalize_answer(text: str, source: str, article: str, channel_override: str | None = None) -> str:
    """⑤ 收尾：首句来源标注 + 末尾核实指引（防重复追加）。"""
    label = _SOURCE_LABEL.get(source, source)
    src_note = f"依据《{label}》{article}。"
    if not text.startswith("依据"):
        text = src_note + text
    if "最新通知为准" not in text:
        channel = channel_override or _SOURCE_CHANNEL.get(source, _DEFAULT_CHANNEL)
        text = f"{text} 具体以{channel}最新通知为准。"
    return text


def dedupe_internal(records: list[dict]) -> list[dict]:
    """④a difflib 关卡：采集内部 question 近重复 → ≥0.95 自动去重（保留先出现者，
    丢弃的记入审计），0.55-0.95 告警。多办法合集模板条款（仅差条款号）因此正确合并。
    """
    warns: list[str] = []
    dropped: list[str] = []
    keep: list[dict] = []
    for r in records:
        dup_of = None
        for k in keep:
            score = _similar(k["question"], r["question"])
            if score >= _NEAR_ERROR:
                dup_of = (k["question"], score)
                break
        if dup_of is not None:
            dropped.append(f"  {dup_of[1]:.2f} 「{r['question'][:50]}」 ⇔ 保留「{dup_of[0][:50]}」")
            continue
        keep.append(r)
        for k in keep[:-1]:
            score = _similar(k["question"], r["question"])
            if _NEAR_WARN <= score < _NEAR_ERROR:
                warns.append(f"  {score:.2f} 「{k['question'][:50]}」 ⇔ 「{r['question'][:50]}」")
    if warns:
        print(f"[去重告警] 采集内部疑似相近 {len(warns)} 对（{_NEAR_WARN}-{_NEAR_ERROR}），人工复核：")
        print("\n".join(warns[:20]))
    if dropped:
        print(f"[去重自动] 采集内部重复 {len(dropped)} 条已合并（保留先出现者）：")
        print("\n".join(dropped[:20]))
    return keep


def dedupe_vs_corpus(records: list[dict], corpus_path: Path) -> list[dict]:
    """④b dense 关卡：新条目 vs 全库（zjut_local_data.json）语义余弦去重。

    EmbeddingUnavailable / 全库为空 → 降级跳过（仅告警），不阻断。
    """
    if not corpus_path.exists():
        print("[去重告警] 全库文件不存在，dense 语义去重跳过")
        return records
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    if not corpus:
        print("[去重告警] 全库为空，dense 语义去重跳过")
        return records
    try:
        corpus_vecs = embed_dense([f"{r['question']} {r['answer']}" for r in corpus])
        new_vecs = embed_dense([f"{r['question']} {r['answer']}" for r in records])
    except EmbeddingUnavailable as exc:
        print(f"[去重告警] 稠密模型不可用，dense 语义去重跳过: {exc}")
        return records
    cnorm = corpus_vecs / (np_norm(corpus_vecs) + 1e-9)
    nnorm = new_vecs / (np_norm(new_vecs) + 1e-9)
    sims = nnorm @ cnorm.T
    for i, rec in enumerate(records):
        top = sims[i].max()
        if top >= _DENSE_BLOCK:
            j = int(sims[i].argmax())
            raise SystemExit(
                f"[去重阻断] 与全库语义近重复（余弦 {top:.3f} ≥ {_DENSE_BLOCK}）：\n"
                f"  新条目「{rec['question']}」\n  ⇔ 全库「{corpus[j]['question']}」"
            )
        if top >= _DENSE_WARN:
            j = int(sims[i].argmax())
            print(f"[去重告警] 与全库语义接近（{top:.3f}）：「{rec['question']}」 ⇔ 「{corpus[j]['question']}」")
    return records


def np_norm(mat) -> list:
    import numpy as np

    return np.linalg.norm(mat, axis=1, keepdims=True)


def dedupe_template_clauses(items: list[dict]) -> list[dict]:
    """模板套话条款去重：多办法合集中的通用条款（适用范围/解释权/生效条款）按
    归一化文本（去条款号/标点）只保留一条，避免 '第三十七条…以本办法为准' ⇔
    '第二十三条…以本办法为准' 这类跨办法模板重复。
    """
    seen: set[str] = set()
    out: list[dict] = []
    for it in items:
        t = it.get("text", "")
        is_template = (
            "本办法" in t
            and len(t) < 200
            and any(k in t for k in ("以本办法为准", "本办法由", "本办法适用于", "自发布之日起", "负责解释"))
        )
        if not is_template:
            out.append(it)
            continue
        key = re.sub(r"第[一二三四五六七八九十百千\d]+条", "", t)
        key = re.sub(r"[\s，。、；：,.!?;:（）()「」“”\"'—-]", "", key)
        if key in seen:
            continue  # 同模板已保留一条
        seen.add(key)
        out.append(it)
    return out


def build_items(items: list[dict], mock_llm: bool = False) -> list[dict]:
    """工序 ①②③⑤ 主流程：条款 → 标准条目（先模板条款去重）。"""
    items = dedupe_template_clauses(items)
    records: list[dict] = []
    for item in items:
        text, source, title = item["text"], item.get("source", ""), item.get("title", "")
        article = re.sub(r"^(第[一二三四五六七八九十百千\d]+条).*", r"\1", title) or ""
        domain = map_domain(text)
        ktype = classify(text, title)
        if ktype == "process" and not mock_llm:
            faq = _llm_to_faq(text, source, title)
            question, keywords, answer = faq["question"], faq["keywords"], faq["answer"]
        else:
            question = title or text[:30]
            keywords = ",".join(extract_keywords(title))
            answer = text
        answer = finalize_answer(answer, source, article)
        records.append(
            {
                "domain": domain,
                # String(128)/String(256) 列上限留余量：keywords ≤120、question ≤250
                "keywords": keywords[:120],
                "question": question[:250],
                "type": ktype,
                "answer": answer,
            }
        )
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="M11 build 抽取管道")
    parser.add_argument("--items", default=str(ROOT / "data" / "zjut_raw" / "parsed" / "items.json"))
    parser.add_argument("--out", default=str(ROOT / "config" / "zjut_m11_data.json"))
    parser.add_argument("--corpus", default=str(ROOT / "config" / "zjut_local_data.json"))
    parser.add_argument("--mock-llm", action="store_true", help="不调 DS API，流程类也走直存（离线测试用）")
    parser.add_argument("--exclude", nargs="*", default=[], help="排除指定 source（空格分隔多个）")
    args = parser.parse_args()

    items_path = Path(args.items)
    if not items_path.exists():
        print(f"[build_zjut_entries] 无解析产物: {items_path}（先跑 parse_zjut_structure.py parse）")
        return 1
    items = json.loads(items_path.read_text(encoding="utf-8"))
    if args.exclude:
        before = len(items)
        excluded = set(args.exclude)
        items = [i for i in items if i.get("source") not in excluded]
        print(f"[build_zjut_entries] 排除 {sorted(excluded)}: {before} → {len(items)}")
    print(f"[build_zjut_entries] 输入条款 {len(items)} 条（mock_llm={args.mock_llm}）")

    records = build_items(items, mock_llm=args.mock_llm)
    print(f"[build_zjut_entries] ① 分类+②③ 生成完成：{len(records)} 条 "
          f"(process={sum(1 for r in records if r['type'] == 'process')})")

    records = dedupe_internal(records)
    records = dedupe_vs_corpus(records, Path(args.corpus))

    records.sort(key=lambda r: (_DOMAINS.index(r["domain"]), r["question"]))
    out_path = Path(args.out)
    out_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    counter = Counter(r["domain"] for r in records)
    print(f"[build_zjut_entries] 已写入 {out_path}，共 {len(records)} 条，覆盖 {len(counter)} 域：")
    for d in _DOMAINS:
        print(f"    {d}: {counter.get(d, 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
