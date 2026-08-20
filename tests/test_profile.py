"""M7-ZJUT 用户长期记忆：画像抽取纯函数 / 落库 upsert / 注入 / api 集成测试。

覆盖：
- extract：building 正则、sources domain 解析、merge 累加（轮内去重/128 截断）、格式化
- upsert：独立事务写库、异常隔离
- 注入：ClarifyDecider system prompt 含画像段、query prompt 含画像段
- api：student 对话后画像行 upsert、同轮累加、cs_staff 不写（role 门控）
"""

from campus_desk.api.schemas import SourceItem
from campus_desk.profile.extract import (
    extract_building,
    extract_domains,
    format_profile_text,
    merge_profile,
)

# ---------- extract_building ----------

class TestExtractBuilding:
    def test_normal(self):
        assert extract_building("我住在1号楼，网络不好") == "1号楼"

    def test_spaced(self):
        assert extract_building("3 号 楼宿舍没电") == "3号楼"

    def test_none(self):
        assert extract_building("图书馆几点关门") is None

    def test_multi_keep_first(self):
        assert extract_building("从1号楼走到3号楼") == "1号楼"

    def test_empty(self):
        assert extract_building("") is None


# ---------- extract_domains ----------

class TestExtractDomains:
    def test_kb_and_tool(self):
        sources = [
            SourceItem(type="kb", label="知识库", ref_id="#K1", detail="info型 · 教务"),
            SourceItem(type="kb", label="知识库", ref_id="#K2", detail="process型 · 图书馆"),
            SourceItem(type="tool", label="工具查询", ref_id="", detail="query_empty_rooms"),
        ]
        assert extract_domains(sources) == ["教务", "图书馆", "工具查询"]

    def test_dirty_detail_skipped(self):
        sources = [SourceItem(type="kb", label="知识库", ref_id="#K1", detail="")]
        assert extract_domains(sources) == []

    def test_tool_only(self):
        sources = [SourceItem(type="tool", label="工具查询", ref_id="", detail="query_timetable")]
        assert extract_domains(sources) == ["工具查询"]

    def test_empty(self):
        assert extract_domains([]) == []


# ---------- merge_profile ----------

class TestMergeProfile:
    def test_new_profile(self):
        p = merge_profile(None, "1号楼", ["教务"])
        assert p["building"] == "1号楼"
        assert p["frequent_categories"] == "教务:1"

    def test_building_last_write_wins(self):
        p = merge_profile({"building": "2号楼", "frequent_categories": ""}, "1号楼", [])
        assert p["building"] == "1号楼"

    def test_dedup_within_round(self):
        p = merge_profile(None, None, ["教务", "教务"])
        assert p["frequent_categories"] == "教务:1"

    def test_cross_round_accumulate(self):
        existing = {"building": None, "frequent_categories": "教务:2,图书馆:1"}
        p = merge_profile(existing, None, ["图书馆"])
        # 次数平局按域名稳定序（Unicode 码位：图 < 教）
        assert p["frequent_categories"] == "图书馆:2,教务:2"

    def test_sort_desc_by_count(self):
        existing = {"building": None, "frequent_categories": "图书馆:2,教务:1"}
        p = merge_profile(existing, None, ["教务"])
        assert p["frequent_categories"] == "图书馆:2,教务:2"

    def test_truncate_128(self):
        existing = {"building": None, "frequent_categories": "教务:10,图书馆:9,网络与IT:8,校园卡与证件:7,住宿后勤:6,医疗健康:5,社团与活动:4,就业与职业发展:3,安全与保卫:2,生活服务:1,奖助:1,工具查询:1"}
        p = merge_profile(existing, None, ["教务"])
        assert len(p["frequent_categories"]) <= 128

    def test_no_change_empty_round(self):
        existing = {"building": "1号楼", "frequent_categories": "教务:1"}
        p = merge_profile(existing, "1号楼", [])
        assert p == existing


# ---------- format_profile_text ----------

class TestFormatProfile:
    def test_full(self):
        text = format_profile_text({"building": "1号楼", "frequent_categories": "教务:2,图书馆:1"})
        assert "1号楼" in text
        assert "教务" in text
        assert "2次" in text

    def test_building_only(self):
        text = format_profile_text({"building": "1号楼", "frequent_categories": ""})
        assert "1号楼" in text
        assert "常问领域" not in text

    def test_empty_profile(self):
        assert format_profile_text({"building": None, "frequent_categories": ""}) == ""
        assert format_profile_text(None) == ""


# ---------- upsert ----------

class TestUpdateProfileAfterTurn:
    def test_upsert_row(self, db_session_factory):
        from campus_desk.db.models import UserProfile
        from campus_desk.profile.upsert import update_profile_after_turn

        sources = [SourceItem(type="kb", label="知识库", ref_id="#K1", detail="info型 · 教务")]
        update_profile_after_turn(
            db_session_factory, user_id="student-001", msg="我1号楼网不好", sources=sources
        )
        with db_session_factory() as s:
            p = s.get(UserProfile, "student-001")
        assert p is not None
        assert p.building == "1号楼"
        assert p.frequent_categories == "教务:1"

    def test_accumulate_across_turns(self, db_session_factory):
        from campus_desk.db.models import UserProfile
        from campus_desk.profile.upsert import update_profile_after_turn

        sources = [SourceItem(type="kb", label="知识库", ref_id="#K1", detail="info型 · 教务")]
        update_profile_after_turn(db_session_factory, user_id="student-001", msg="a", sources=sources)
        update_profile_after_turn(db_session_factory, user_id="student-001", msg="b", sources=sources)
        with db_session_factory() as s:
            p = s.get(UserProfile, "student-001")
        assert p.frequent_categories == "教务:2"

    def test_error_isolated(self, db_session_factory):
        """抽取/落库异常不得阻断主对话流程（旁路）。"""
        from campus_desk.profile.upsert import update_profile_after_turn

        class _BoomFactory:
            def __call__(self):
                raise RuntimeError("boom")

        # 不抛异常即通过
        update_profile_after_turn(
            _BoomFactory(), user_id="student-001", msg="1号楼", sources=[]
        )


# ---------- 注入：ClarifyDecider / query prompt ----------

class _RecordingLLM:
    """记录 invoke messages 的 stub（ClarifyDecider 注入断言用）。"""

    def __init__(self, content='{"action": "handoff", "questions": [], "reply": "x", "summary": "s"}'):
        self.messages = None
        self.content = content

    def invoke(self, messages):
        self.messages = messages
        return type("FakeAIMessage", (), {"content": self.content})()


class TestClarifyProfileInjected:
    def test_profile_section_present(self):
        from campus_desk.knowledge.decide import ClarifyDecider

        llm = _RecordingLLM()
        decider = ClarifyDecider(llm=llm, profile="常驻楼栋 1号楼；常问领域：教务(2次)")
        decider.decide(history=[], user_text="网络问题", missed=True)
        system_text = llm.messages[0][1]
        assert "常驻楼栋 1号楼" in system_text
        assert "关于该学生" in system_text

    def test_no_profile_unchanged(self):
        from campus_desk.knowledge.decide import ClarifyDecider

        llm = _RecordingLLM()
        decider = ClarifyDecider(llm=llm)
        decider.decide(history=[], user_text="网络问题", missed=True)
        system_text = llm.messages[0][1]
        assert "关于该学生" not in system_text


class TestQueryPromptProfile:
    def test_profile_section_present(self, db_session_factory):
        from campus_desk.query.graph import _build_query_prompt, _Deps

        deps = _Deps(
            db_session_factory, llm=None, user_id="student-001",
            profile_text="用户画像：常驻楼栋 1号楼",
        )
        prompt = _build_query_prompt(deps)
        assert "用户画像" in prompt
        assert "常驻楼栋 1号楼" in prompt

    def test_no_profile_unchanged(self, db_session_factory):
        from campus_desk.query.graph import _build_query_prompt, _Deps

        deps = _Deps(db_session_factory, llm=None, user_id="student-001")
        prompt = _build_query_prompt(deps)
        assert "用户画像" not in prompt


class TestBundleProfileVersion:
    """画像 updated_at 版本跟踪（GraphRegistry bundle 重建依据）。"""

    def test_fetch_profile_none_before_write(self, db_session_factory):
        from campus_desk.api.graphs import _fetch_profile

        text, updated = _fetch_profile(db_session_factory, "student-001")
        assert text is None
        assert updated is None

    def test_fetch_profile_after_write(self, db_session_factory):
        from campus_desk.api.graphs import _fetch_profile
        from campus_desk.profile.upsert import update_profile_after_turn

        sources = [SourceItem(type="kb", label="知识库", ref_id="#K1", detail="info型 · 教务")]
        update_profile_after_turn(
            db_session_factory, user_id="student-001", msg="我1号楼网不好", sources=sources
        )
        text, updated = _fetch_profile(db_session_factory, "student-001")
        assert text is not None
        assert "1号楼" in text
        assert updated is not None


# ---------- api 集成 ----------

def _login(api_client, username="student-001"):
    login = api_client.post(
        "/api/auth/login", json={"username": username, "password": "123456"}
    )
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _new_thread(api_client, username="student-001"):
    headers = _login(api_client, username)
    conv = api_client.post("/api/sessions", headers=headers).json()
    return conv["thread_id"], headers


def _seed_knowledge(db_session_factory):
    from campus_desk.db.models import KnowledgeEntry

    with db_session_factory() as s, s.begin():
        s.add(
            KnowledgeEntry(
                domain="网络与IT",
                keywords="宿舍,网络,断网",
                question="宿舍网络不稳定怎么办？",
                type="info",
                answer="宿舍网络问题可先重启路由器，如仍异常请到网络中心报修。",
            )
        )


def test_api_chat_writes_profile(api_client, db_session_factory):
    from campus_desk.db.models import UserProfile

    _seed_knowledge(db_session_factory)
    thread_id, headers = _new_thread(api_client)
    resp = api_client.post(
        "/api/chat", json={"thread_id": thread_id, "msg": "1号楼宿舍网不好"}, headers=headers
    )
    assert resp.status_code == 200
    with db_session_factory() as s:
        p = s.get(UserProfile, "student-001")
    assert p is not None
    assert p.building == "1号楼"
    assert "网络与IT" in p.frequent_categories


def test_api_chat_accumulate_same_thread(api_client, db_session_factory):
    from campus_desk.db.models import UserProfile

    _seed_knowledge(db_session_factory)
    thread_id, headers = _new_thread(api_client)
    for _ in range(2):
        resp = api_client.post(
            "/api/chat", json={"thread_id": thread_id, "msg": "宿舍网又断了"}, headers=headers
        )
        assert resp.status_code == 200
    with db_session_factory() as s:
        p = s.get(UserProfile, "student-001")
    assert p.frequent_categories.endswith("网络与IT:2")


def test_api_chat_skip_non_student(api_client, db_session_factory):
    from campus_desk.db.models import UserProfile

    _seed_knowledge(db_session_factory)
    thread_id, headers = _new_thread(api_client, username="cs-001")
    resp = api_client.post(
        "/api/chat", json={"thread_id": thread_id, "msg": "1号楼宿舍网不好"}, headers=headers
    )
    assert resp.status_code == 200
    with db_session_factory() as s:
        p = s.get(UserProfile, "cs-001")
    assert p is None
