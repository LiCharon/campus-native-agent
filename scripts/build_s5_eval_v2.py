"""S5 评测集 V2 生成：34 条旧用例重标（多 gold）+ 36 条新增（面向 834 库）→ 70 条。

用法：
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/build_s5_eval_v2.py
覆盖 scripts/s5_eval_set.json（旧版在 git 历史可查）。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import select

from campus_desk.db.models import KnowledgeEntry
from campus_desk.db.session import default_session_factory

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "scripts" / "s5_eval_set.json"

# 旧 34 条重标（多 gold）：基于 834 库检索候选 + 内容相关性判断
RELABELED = [
    ("寒假从几号开始放", "教务", "keyword", [4]),
    ("选课和退课在哪里操作", "教务", "keyword", [5, 79, 41]),
    ("宿舍物品损坏怎么报修", "住宿后勤", "keyword", [10, 60, 1196]),
    ("图书馆开放时间是几点", "图书馆", "keyword", [17, 50]),
    ("图书馆座位怎么提前预约", "图书馆", "keyword", [18]),
    ("校园网连接上网怎么弄", "网络与IT", "keyword", [22, 55]),
    ("一卡通丢失了如何补办", "校园卡与证件", "keyword", [28, 52, 31]),
    ("我的校园卡找不到了怎么挂失", "校园卡与证件", "keyword", [31, 52, 1140]),
    ("医保费用怎么报销", "医疗健康", "keyword", [35, 144, 133]),
    ("助学贷款申请条件是什么", "奖助", "keyword", [119, 1220, 1245]),
    ("就业信息网入口和联系方式", "就业与职业发展", "keyword", [160, 161]),
    ("保卫处报警电话是多少", "安全与保卫", "keyword", [172, 179]),
    ("在哪能看到我每学期的分数", "教务", "semantic", [6]),
    ("想暂停学业一段时间怎么办理", "教务", "semantic", [8, 924]),
    ("就餐区域早晨几点开放", "生活服务", "semantic", [12]),
    ("网购的包裹在学校哪个点位领取", "生活服务", "semantic", [13, 264]),
    ("怎么从阅览室把书带出去到期再送回", "图书馆", "semantic", [16, 1126, 1127]),
    ("去哪能获取到学术期刊文章", "图书馆", "semantic", [19, 86, 1130]),
    ("进系统的口令忘掉了怎么重新设", "网络与IT", "semantic", [23]),
    ("怎么给饭卡里充钱", "校园卡与证件", "semantic", [29, 51]),
    ("开学要交的那个在校读书的证明怎么开", "校园卡与证件", "semantic", [32, 280, 279]),
    ("学校有合作的精神科专家门诊吗", "医疗健康", "semantic", [125]),
    ("大学生创新创业项目怎么立项", "社团与活动", "semantic", [158, 1359, 1362]),
    ("怎么辨别冒充客服的骗局", "安全与保卫", "semantic", [175, 258]),
    ("图书馆座位预约和食堂营业时间分别是多少", "图书馆", "cross_domain", [18, 12]),
    ("一卡通和学生证都丢了先补办哪个", "校园卡与证件", "cross_domain", [28, 52]),
    ("校医院在哪开门医疗费怎么报销", "医疗健康", "cross_domain", [34, 35, 144]),
    ("图书馆和自习室分别几点能进", "图书馆", "cross_domain", [17, 21]),
    ("校园网连不上学生邮箱也登不进", "网络与IT", "cross_domain", [22, 24]),
    ("就业信息网入口和创业支持政策", "就业与职业发展", "cross_domain", [160, 159]),
    ("保卫处电话和防电信诈骗找谁", "安全与保卫", "cross_domain", [172, 175]),
    ("助学贷款和国家助学金怎么同时申请", "奖助", "cross_domain", [119, 1219, 1220]),
    ("快递点在哪宿舍东西坏了找谁", "生活服务", "cross_domain", [13, 10, 185]),
    ("心理中心在哪预约入学体检怎么安排", "医疗健康", "cross_domain", [63, 127, 132]),
]

# 新增 36 条：(query, domain, category, 目标 question 关键词片段 → 查 id)
NEW_CASES = [
    # 教务（8）
    ("辅修课程选课后需要缴纳哪些费用？中途退出学费能退吗", "教务", "keyword", "辅修课程选课后需要缴纳"),
    ("提前修完所有课程想早点毕业该怎么办", "教务", "semantic", "提前修完了所有课程"),
    ("免听申请怎么提交？需要什么条件", "教务", "semantic", "免听"),
    ("因临时急病不能参加考试需要办什么手续", "教务", "keyword", "因临时急病"),
    ("转专业能报几个志愿？有几次机会", "教务", "keyword", "转专业能报几个志愿"),
    ("研究生开题报告什么时候做", "教务", "keyword", "研究生开题报告"),
    ("考试违纪作弊有什么后果", "教务", "semantic", "考试违纪"),
    ("英语四六级什么时候报名考试", "教务", "keyword", "四六级"),
    # 图书馆（6）
    ("图书超期怎么还？有费用吗", "图书馆", "keyword", "超期"),
    ("没办借阅手续把书带出图书馆算违规吗", "图书馆", "semantic", "没办借阅手续就把书带出"),
    ("教职工怎么开通借书权限", "图书馆", "keyword", "教职工开通借书权限"),
    ("图书馆馆舍总面积多大？各校区多少", "图书馆", "keyword", "阅览座位"),
    ("图书到期能续借吗？有什么限制", "图书馆", "semantic", "续借"),
    ("图书馆信息共享空间怎么预约", "图书馆", "keyword", "信息共享空间"),
    # 奖助（6）
    ("国家励志奖学金能和国家奖学金同时拿吗", "奖助", "semantic", "励志奖学金"),
    ("勤工助学工资多少？每周最多几小时", "奖助", "keyword", "勤工助学工资"),
    ("校内无息借款能借多少？要还吗", "奖助", "keyword", "校内无息借款"),
    ("应征入伍学费怎么补偿", "奖助", "semantic", "应征入伍"),
    ("毕业生求职创业补贴有多少钱", "奖助", "keyword", "求职创业补贴"),
    ("到基层就业学费补偿怎么申请", "奖助", "keyword", "基层就业"),
    # 社团与活动（4）
    ("第二课堂要修满多少积分才能毕业", "社团与活动", "keyword", "第二课堂"),
    ("志愿工时怎么算？和劳育积分什么关系", "社团与活动", "semantic", "志愿工时"),
    ("学校社团有哪些类型", "社团与活动", "keyword", "社团有哪些类型"),
    ("错过百团大战还能加社团吗", "社团与活动", "semantic", "百团大战"),
    # 住宿后勤（4）
    ("宿舍空调怎么租", "住宿后勤", "keyword", "空调租赁"),
    ("宿舍洗衣机怎么用", "住宿后勤", "keyword", "洗衣机"),
    ("宿舍公寓维修找谁", "住宿后勤", "keyword", "学生公寓的零星维修"),
    ("宿舍门禁和访客有什么规定", "住宿后勤", "semantic", "门禁"),
    # 网络与IT（3）
    ("新生统一身份认证初始密码是多少", "网络与IT", "keyword", "初始密码"),
    ("学生公寓上网怎么办理", "网络与IT", "keyword", "公寓区上网"),
    ("宿舍网管理办法对用户有什么禁止要求", "网络与IT", "semantic", "不得随意将帐号"),
    # 医疗健康（2）
    ("大学生医保门诊起付线多少报多少", "医疗健康", "keyword", "医保门诊和住院"),
    ("校医院急诊电话是多少", "医疗健康", "keyword", "急诊电话"),
    # 安全与保卫（2）
    ("电动车能在宿舍充电吗", "安全与保卫", "keyword", "室内充电"),
    ("户籍业务怎么办理", "安全与保卫", "keyword", "户籍"),
    # 校园卡与证件（1）
    ("学生证火车票优惠卡写卡在哪里办", "校园卡与证件", "keyword", "优惠卡写卡"),
]


def resolve_id(factory, keyword: str) -> int | None:
    with factory() as s:
        rows = s.execute(
            select(KnowledgeEntry).where(KnowledgeEntry.question.like(f"%{keyword}%")).limit(5)
        ).scalars().all()
    for r in rows:
        return r.id
    return None


def main() -> None:
    factory = default_session_factory()
    cases = [{"query": q, "domain": d, "category": c, "gold_ids": g}
             for q, d, c, g in RELABELED]

    missing = []
    for q, d, c, kw in NEW_CASES:
        cid = resolve_id(factory, kw)
        if cid is None:
            missing.append((q, kw))
            continue
        cases.append({"query": q, "domain": d, "category": c, "gold_ids": [cid]})

    if missing:
        print("[build_s5_eval_v2] 未匹配到条目的新用例（需人工补 gold）：")
        for q, kw in missing:
            print(f"  {q}  <-  {kw}")
        # 仍继续写出（缺的用例跳过），保证 70 条目标可达的尽量达成

    meta = {
        "version": 2,
        "note": "70 条：34 旧用例重标多 gold + 36 新增（面向 834 库）；gold_ids 多值 1-3 个",
        "n": len(cases),
    }
    (OUT).write_text(json.dumps({"meta": meta, "cases": cases}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[build_s5_eval_v2] 已写入 {OUT}，共 {len(cases)} 条（目标 70，缺 {len(missing)}）")
    from collections import Counter

    print("category 分布:", dict(Counter(c["category"] for c in cases)))


if __name__ == "__main__":
    main()
