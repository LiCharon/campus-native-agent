"""用户画像测试（M4，需求 §7）：ProfileStore 读写 + RepairGraph 注入时机 + 重复报修关联。

注入设施：FakeFieldExtractor / FakeRepairClassifier（conftest，classifier 记录
profile_context 断言注入内容）+ InMemorySaver。
"""

from langgraph.checkpoint.memory import InMemorySaver

from campus_desk.db.models import UserProfile
from campus_desk.repair.classify import ClassificationResult
from campus_desk.repair.drafting import DraftExtract
from campus_desk.repair.graph import build_repair_graph
from campus_desk.repair.profile import ProfileStore, profile_text, same_category_as_before
from tests.conftest import FakeFieldExtractor, FakeRepairClassifier

CFG = {"configurable": {"thread_id": "profile-t1"}}


def _build(db_session_factory, classifier=None):
    return build_repair_graph(
        db_session_factory,
        extractor=FakeFieldExtractor(
            default=DraftExtract(description="", building="3号楼", room="502", contact="李华")
        ),
        classifier=classifier
        or FakeRepairClassifier(
            default=ClassificationResult(
                category="水电",
                priority="P2",
                confidence=0.9,
                needs_human_confirm=False,
                reason="规则/LLM 判定",
            )
        ),
        checkpointer=InMemorySaver(),
        user_id="student-001",
        actor="student-001",
        default_contact="李华",
    )


class TestProfileStore:
    def test_no_profile_returns_none(self, db_session_factory):
        assert ProfileStore(db_session_factory).get_profile("student-001") is None

    def test_update_then_read_back(self, db_session_factory):
        store = ProfileStore(db_session_factory)
        store.update_profile(
            "student-001", building="3号楼", category="网络", description="宿舍断网"
        )
        profile = store.get_profile("student-001")
        assert profile["building"] == "3号楼"
        assert profile["frequent_categories"] == "网络"
        assert "宿舍断网" in profile["last_ticket_summary"]

    def test_category_counting_sorted(self, db_session_factory):
        """常报类别按次数排序，保留前 3（重复报修关联数据源）。"""
        store = ProfileStore(db_session_factory)
        for cat in ("水电", "网络", "水电", "网络", "水电"):
            store.update_profile("student-001", category=cat, description="x")
        profile = store.get_profile("student-001")
        cats = profile["frequent_categories"].split(",")
        assert cats[0] == "水电"  # 3 次最多
        assert cats[1] == "网络"
        assert len(cats) <= 3

    def test_building_only_overwritten_when_given(self, db_session_factory):
        store = ProfileStore(db_session_factory)
        store.update_profile("student-001", building="3号楼", category="网络", description="x")
        # 没写楼栋的更新不覆盖已有楼栋
        store.update_profile("student-001", category="水电", description="x")
        assert store.get_profile("student-001")["building"] == "3号楼"

    def test_summary_truncated(self, db_session_factory):
        store = ProfileStore(db_session_factory)
        store.update_profile("student-001", category="水电", description="灯" * 200)
        summary = store.get_profile("student-001")["last_ticket_summary"]
        assert len(summary) < 120  # 截断到 80 + 后缀

    def test_update_profile_is_idempotent(self, db_session_factory):
        """同一信息重复更新不产生脏数据（同 upsert 语义）。"""
        store = ProfileStore(db_session_factory)
        store.update_profile("student-001", building="3号楼", category="网络", description="断网")
        store.update_profile("student-001", building="3号楼", category="网络", description="断网")
        profile = store.get_profile("student-001")
        assert profile["frequent_categories"] == "网络"  # 无重复项
        with db_session_factory() as session:
            assert session.query(UserProfile).count() == 1


class TestProfileText:
    def test_full_profile_format(self):
        text = profile_text(
            {
                "building": "3号楼",
                "frequent_categories": "水电,网络",
                "last_ticket_summary": "灯坏了（水电，08-01）",
            }
        )
        assert "3号楼" in text and "水电,网络" in text and "灯坏了" in text
        assert text.startswith("学生历史报修画像")

    def test_empty_profile_none(self):
        assert (
            profile_text({"building": None, "frequent_categories": "", "last_ticket_summary": None})
            is None
        )


class TestSameCategoryBefore:
    def test_hit(self):
        profile = {
            "building": "3号楼",
            "frequent_categories": "水电,网络",
            "last_ticket_summary": "x",
        }
        assert same_category_as_before(profile, "网络") is True

    def test_miss(self):
        profile = {"building": "3号楼", "frequent_categories": "水电", "last_ticket_summary": "x"}
        assert same_category_as_before(profile, "网络") is False

    def test_no_profile(self):
        assert same_category_as_before(None, "网络") is False


class TestGraphInjection:
    def test_no_profile_first_repair(self, db_session_factory):
        """首次报修（无画像）→ classifier 收到 context=None，正常建单。"""
        classifier = FakeRepairClassifier(
            default=ClassificationResult(
                category="水电",
                priority="P2",
                confidence=0.9,
                needs_human_confirm=False,
                reason="x",
            )
        )
        graph = _build(db_session_factory, classifier)
        out = graph.invoke({"user_input": "3号楼502灯坏了"}, CFG)
        assert out["finished"] is True
        desc, ctx = classifier.calls[0]
        assert desc == "3号楼502灯坏了"
        assert ctx is None
        assert out["reply"]  # 无"上次同类"提示

    def test_profile_injected_before_classify(self, db_session_factory):
        """有画像 → classify 前注入（context 含画像内容，原始描述不变）。"""
        store = ProfileStore(db_session_factory)
        store.update_profile(
            "student-001", building="3号楼", category="网络", description="上次宿舍断网"
        )
        classifier = FakeRepairClassifier(
            default=ClassificationResult(
                category="网络",
                priority="P2",
                confidence=0.9,
                needs_human_confirm=False,
                reason="x",
            )
        )
        graph = _build(db_session_factory, classifier)
        graph.invoke({"user_input": "又连不上网了"}, CFG)
        desc, ctx = classifier.calls[0]
        assert desc == "又连不上网了"  # 规则层输入不变
        assert "上次宿舍断网" in ctx  # 画像上下文注入（LLM 层）
        assert "3号楼" in ctx

    def test_profile_updated_after_create(self, db_session_factory):
        """建单成功后画像随工单提交更新（需求 §7 时机）。"""
        graph = _build(db_session_factory)
        graph.invoke({"user_input": "3号楼502灯坏了"}, CFG)
        profile = ProfileStore(db_session_factory).get_profile("student-001")
        assert profile["building"] == "3号楼"
        assert "水电" in profile["frequent_categories"]  # 分类器 fake 返回水电
        assert "灯坏了" in profile["last_ticket_summary"]

    def test_repeat_repair_same_category_hint(self, db_session_factory):
        """重复报修（"又坏了"）：上次同类 → finalize 带"已优先跟进"提示。"""
        store = ProfileStore(db_session_factory)
        store.update_profile(
            "student-001", building="3号楼", category="水电", description="上次灯坏了"
        )
        classifier = FakeRepairClassifier(
            default=ClassificationResult(
                category="水电",
                priority="P2",
                confidence=0.9,
                needs_human_confirm=False,
                reason="x",
            )
        )
        graph = _build(db_session_factory, classifier)
        out = graph.invoke({"user_input": "上次修的灯又坏了"}, CFG)
        assert "已优先跟进" in out["reply"]

    def test_new_category_no_hint(self, db_session_factory):
        """本次类别不在常报类别（上次网络、本次水电）→ 无提示。"""
        store = ProfileStore(db_session_factory)
        store.update_profile(
            "student-001", building="3号楼", category="网络", description="上次断网"
        )
        classifier = FakeRepairClassifier(
            default=ClassificationResult(
                category="水电",
                priority="P2",
                confidence=0.9,
                needs_human_confirm=False,
                reason="x",
            )
        )
        graph = _build(db_session_factory, classifier)
        out = graph.invoke({"user_input": "水龙头漏水"}, CFG)
        assert "已优先跟进" not in out["reply"]
