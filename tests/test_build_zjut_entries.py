"""M11 build 抽取管道离线测试（TDD）。

验证 5 道工序：分类 / LLM 转 FAQ（mock）/ 原文直存 / 双关卡去重（mock 向量）/ 收尾标注。
LLM 与向量均不真实调用（离线可跑）。
"""

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_zjut_entries as b

SAMPLE_ITEMS = [
    {"source": "手册_2025级", "level": 2, "title": "第十二条 休学与复学",
     "text": "学生因故需要休学的，应当向所在学院提交休学申请，办理休学手续。休学以一学年为限，累计不得超过两学年。复学须在规定时间内向学院提出申请并办理相关手续。", "chapter": "第四章 学籍管理", "article": "第十二条"},
    {"source": "手册_2025级", "level": 2, "title": "第十三条 学生注册",
     "text": "学生应当按时办理注册手续。未按时注册且未办理暂缓注册手续的，按学校规定处理。", "chapter": "第四章 学籍管理", "article": "第十三条"},
    {"source": "资助管理办法", "level": 2, "title": "第五条 资助对象认定",
     "text": "学生资助对象认定应当坚持实事求是、公开透明的原则。家庭经济困难学生可向所在学院提交认定申请，经评议小组民主评议后报学校资助管理中心审核。", "chapter": "第二章 资助对象认定", "article": "第五条"},
]


class TestClassify:
    def test_process_text(self):
        assert b.classify("申请休学须提交材料并办理手续，流程如下") == "process"

    def test_rule_text(self):
        assert b.classify("学生应当遵守校规，不得违反规定，违者给予处分") == "info"

    def test_title_hint_boosts(self):
        assert b.classify("办理休学手续后按学籍规定处理", title="怎么申请休学") == "process"


class TestMapDomain:
    @pytest.mark.parametrize(
        "text,domain",
        [
            ("选课成绩课表考试学分", "教务"),
            ("图书馆借书还书续借馆藏", "图书馆"),
            ("校园网账号密码邮箱无线", "网络与IT"),
            ("校园卡一卡通饭卡挂失充值", "校园卡与证件"),
            ("宿舍住宿报修热水电费", "住宿后勤"),
            ("奖学金助学金资助困难贷款", "奖助"),
            ("医保医院医疗报销体检", "医疗健康"),
            ("社团百团第二课堂志愿工时", "社团与活动"),
            ("就业招聘简历面试签约档案", "就业与职业发展"),
            ("保卫报警户籍消防诈骗", "安全与保卫"),
            ("学费缴费计财周边交通", "生活服务"),
        ],
    )
    def test_domain_hits(self, text, domain):
        assert b.map_domain(text) == domain


class TestExtractKeywords:
    def test_strips_article_no(self):
        kws = b.extract_keywords("第十二条 休学与复学")
        assert "休学" in kws
        assert not any("条" in k for k in kws)

    def test_returns_list(self):
        assert isinstance(b.extract_keywords("学生注册"), list)


class TestFinalize:
    def test_source_prefix_and_channel(self):
        out = b.finalize_answer("休学以一学年为限。", "手册_2025级", "第十二条")
        assert out.startswith("依据《浙江工业大学2025级学生手册》第十二条。")
        assert "教务处官网" in out
        assert out.endswith("最新通知为准。")

    def test_no_double_append(self):
        out = b.finalize_answer("以教务处官网最新通知为准。", "手册_2025级", "第十二条")
        assert out.count("最新通知为准") == 1

    def test_default_channel(self):
        out = b.finalize_answer("规定内容。", "未知来源", "第一条")
        assert "学校官方最新通知" in out


class TestDedupeInternal:
    def test_auto_dedupe_keeps_first(self):
        records = [
            {"question": "怎么申请休学？", "domain": "教务"},
            {"question": "怎么申请休学", "domain": "教务"},  # 仅标点差异 → 自动合并
            {"question": "宿舍空调怎么租？", "domain": "住宿后勤"},
        ]
        out = b.dedupe_internal(records)
        assert len(out) == 2
        assert out[0]["question"] == "怎么申请休学？"  # 保留先出现者
        assert out[1]["question"] == "宿舍空调怎么租？"

    def test_template_clause_auto_deduped(self):
        # 多办法合集的模板条款（长文本仅条款号不同）→ 自动保留一条
        records = [
            {"question": "第三十七条 学校有关部门和各学院可以依据本办法制定符合本单位实际的实施细则，报学校备案；其他有关规定与本办法抵触者，以本办法为准", "domain": "教务"},
            {"question": "第二十三条 学校有关部门和各学院可以依据本办法制定符合本单位实际的实施细则,报学校备案;其他有关规定与本办法抵触者,以本办法为准", "domain": "教务"},
        ]
        out = b.dedupe_internal(records)
        assert len(out) == 1

    def test_semantic_dup_not_caught_by_difflib(self):
        # 词序颠倒的语义重复：difflib 只有 0.62（字符级测不出）→ 保留，
        # 这正是 dense 关卡（语义级）的职责范围。
        records = [
            {"question": "怎么申请休学？", "domain": "教务"},
            {"question": "休学怎么申请啊？", "domain": "教务"},
        ]
        assert b.dedupe_internal(records) == records

    def test_warn_mild_dup(self):
        records = [
            {"question": "图书馆座位怎么预约？", "domain": "图书馆"},
            {"question": "研讨室座位如何预约", "domain": "图书馆"},
        ]
        assert b.dedupe_internal(records) == records

    def test_ok_distinct(self):
        records = [
            {"question": "怎么申请休学？", "domain": "教务"},
            {"question": "宿舍空调怎么租？", "domain": "住宿后勤"},
        ]
        assert b.dedupe_internal(records) == records


class TestDedupeTemplate:
    def test_template_deduped(self):
        items = [
            {"text": "第二条 本办法适用于在校研究生、本科生等通过注册取得浙江工业大学正式学籍的全日制各类学生（以下简称学生）。", "source": "a"},
            {"text": "第二条 本办法适用于在校研究生、本科生等通过注册取得浙江工业大学正式学籍的全日制各类学生（以下简称学生）。", "source": "b"},
            {"text": "第五条 各学院应根据本实施办法成立评审小组并报学生处备案。", "source": "a"},
        ]
        out = b.dedupe_template_clauses(items)
        assert len(out) == 2  # 模板条款保留一条 + 非模板一条

    def test_non_template_kept(self):
        items = [
            {"text": "第一条 学生应当按时注册。因故不能按期注册者须履行暂缓注册手续。", "source": "a"},
        ]
        assert b.dedupe_template_clauses(items) == items


class TestDedupeCorpus:
    def _fake_embed(self, texts):
        # 假向量：把文本 hash 到 512 维，相似文本（含相同词）向量更接近
        vecs = []
        for t in texts:
            v = np.zeros(512, dtype=np.float32)
            for i, ch in enumerate(t):
                v[hash(ch) % 512] += 1.0
            vecs.append(v)
        return np.asarray(vecs, dtype=np.float32)

    def _write_corpus(self, tmp_path, questions):
        path = tmp_path / "corpus.json"
        path.write_text(json.dumps([{"question": q, "answer": q} for q in questions], ensure_ascii=False), encoding="utf-8")
        return path

    def test_block_semantic_dup(self, tmp_path, monkeypatch):
        monkeypatch.setattr(b, "embed_dense", self._fake_embed)
        corpus = self._write_corpus(tmp_path, ["怎么申请休学？"])
        records = [{"question": "怎么申请休学？", "answer": "怎么申请休学？", "domain": "教务"}]
        with pytest.raises(SystemExit, match="去重阻断"):
            b.dedupe_vs_corpus(records, corpus)

    def test_distinct_passes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(b, "embed_dense", self._fake_embed)
        corpus = self._write_corpus(tmp_path, ["宿舍空调怎么租？"])
        records = [{"question": "图书馆座位怎么预约？", "answer": "图书馆座位怎么预约？", "domain": "图书馆"}]
        assert b.dedupe_vs_corpus(records, corpus) == records

    def test_missing_corpus_warns(self, tmp_path, monkeypatch):
        monkeypatch.setattr(b, "embed_dense", self._fake_embed)
        records = [{"question": "任意问题", "answer": "任意", "domain": "教务"}]
        assert b.dedupe_vs_corpus(records, tmp_path / "nope.json") == records


class TestBuildItems:
    def test_mock_llm_structure(self):
        records = b.build_items(SAMPLE_ITEMS, mock_llm=True)
        assert len(records) == len(SAMPLE_ITEMS)
        for r in records:
            assert set(r) == {"domain", "keywords", "question", "type", "answer"}
            assert r["domain"] in b._DOMAINS
            assert r["type"] in ("info", "process")
            assert r["answer"].startswith("依据")
            assert r["answer"].endswith("最新通知为准。")

    def test_process_detected(self):
        records = b.build_items(SAMPLE_ITEMS, mock_llm=True)
        # 含"申请/办理/手续"的条款应判为 process
        proc = [r for r in records if r["type"] == "process"]
        assert any("休学" in r["question"] for r in proc)


class TestLlmToFaqParsing:
    def test_json_fence_stripped(self, monkeypatch):
        class _Resp:
            content = '```json\n{"question": "Q", "keywords": "k1,k2", "answer": "A"}\n```'

        class _LLM:
            def invoke(self, prompt):
                assert "json" in prompt
                return _Resp()

        monkeypatch.setattr(b, "build_llm", lambda: _LLM())
        faq = b._llm_to_faq("正文", "手册_2025级", "第十二条 休学")
        assert faq == {"question": "Q", "keywords": "k1,k2", "answer": "A"}

    def test_bad_json_fallback(self, monkeypatch):
        class _Resp:
            content = "不是 json"

        class _LLM:
            def invoke(self, prompt):
                return _Resp()

        monkeypatch.setattr(b, "build_llm", lambda: _LLM())
        faq = b._llm_to_faq("休学正文", "手册_2025级", "第十二条 休学")
        assert faq["answer"] == "休学正文"  # 兜底原文


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
