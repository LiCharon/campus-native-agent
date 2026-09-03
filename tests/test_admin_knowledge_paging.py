"""知识列表分页/截断（BUG-003，方案 C）。

背景：M4 建「浏览」tab 时库里仅 36 条，未设计分页；M11 采集后涨到 834 条，
接口一次全量返回、前端一次渲染 834 行 → 页面明显卡顿。

方案 C（最小改动）：后端默认 `limit=200` 截断，返回 `total` / `truncated`
让前端能明确提示"结果已截断，请用筛选缩小范围"；显式传 `limit<=0` 表示不截断。

契约要点（勿回退）：
- `items` 语义不变（仍是过滤后的列表），新增字段只做**告知**，不静默吞数据
- `total` 必须是**过滤后**的总数，不是整表条数（否则前端提示会骗人）
- `truncated=True` 必须伴随 `total > len(items)`
- 不加 offset：管理页是浏览+筛选场景，截断 + 提示已够用，真上量再升级真分页

造数隔离：测试库自带 36 条通用种子，故所有造数统一用 `Q_PREFIX` 前缀，
查询时带 `q=Q_PREFIX` 精确圈定本测试的数据，避免种子干扰绝对条数断言。
"""

from sqlalchemy import func, select

from campus_desk.db.models import KnowledgeEntry

from test_admin import _login

Q_PREFIX = "分页测试问题"


def _seed_knowledge(db_session_factory, n: int, *, domain="教务", type_="info"):
    """造 n 条带 Q_PREFIX 的知识条目。"""
    with db_session_factory() as s:
        for i in range(n):
            s.add(
                KnowledgeEntry(
                    domain=domain,
                    keywords=f"{Q_PREFIX}kw{i}",
                    question=f"{Q_PREFIX}{i:03d}",
                    type=type_,
                    answer=f"答案{i}",
                )
            )
        s.commit()
    return n


def _list(client, headers, **params):
    return client.get("/api/admin/knowledge", headers=headers, params=params)


def _list_scoped(client, headers, **params):
    """只查本测试造的数据（q=Q_PREFIX）。"""
    return _list(client, headers, q=Q_PREFIX, **params).json()


def _table_count(db_session_factory) -> int:
    with db_session_factory() as s:
        return s.execute(select(func.count()).select_from(KnowledgeEntry)).scalar_one()


# ---------- 契约：新增字段 ----------


def test_list_response_has_total_and_truncated(api_client, db_session_factory):
    """响应必带 total / truncated；未超限时 truncated=False 且 total==len(items)。"""
    headers = _login(api_client, "admin-001")
    body = _list(api_client, headers).json()
    assert "total" in body and "truncated" in body
    assert body["truncated"] is False
    assert body["total"] == len(body["items"])


def test_total_equals_raw_table_count_when_no_filter(api_client, db_session_factory):
    """无筛选时 total 必须等于整表条数（种子 + 造数），证明 total 不是拍脑袋值。"""
    _seed_knowledge(db_session_factory, 7)
    headers = _login(api_client, "admin-001")
    body = _list(api_client, headers).json()
    assert body["total"] == _table_count(db_session_factory)


# ---------- 核心：默认截断 ----------


def test_default_limit_truncates_at_200(api_client, db_session_factory):
    """造 205 条 → 默认只回 200 条，truncated=True，total=205。"""
    _seed_knowledge(db_session_factory, 205)
    body = _list_scoped(api_client, _login(api_client, "admin-001"))
    assert body["total"] == 205
    assert len(body["items"]) == 200
    assert body["truncated"] is True


def test_limit_param_overrides_default(api_client, db_session_factory):
    """显式 limit=5 → 只回 5 条，truncated=True。"""
    _seed_knowledge(db_session_factory, 20)
    body = _list_scoped(api_client, _login(api_client, "admin-001"), limit=5)
    assert len(body["items"]) == 5
    assert body["total"] == 20
    assert body["truncated"] is True


def test_limit_zero_means_no_truncation(api_client, db_session_factory):
    """limit<=0 → 不截断（逃生阀，供脚本/导出场景用）。"""
    _seed_knowledge(db_session_factory, 20)
    body = _list_scoped(api_client, _login(api_client, "admin-001"), limit=0)
    assert len(body["items"]) == 20
    assert body["truncated"] is False


def test_no_truncation_when_under_limit(api_client, db_session_factory):
    """不足限时不截断：items 全回，truncated=False。"""
    _seed_knowledge(db_session_factory, 3)
    body = _list_scoped(api_client, _login(api_client, "admin-001"))
    assert len(body["items"]) == 3
    assert body["truncated"] is False


# ---------- 与筛选组合：total 必须是「过滤后」的总数 ----------


def test_total_counts_after_filter_not_raw_table(api_client, db_session_factory):
    """BUG 防线：total 统计过滤后条数，不是整表条数（否则前端提示会骗人）。"""
    _seed_knowledge(db_session_factory, 10, domain="图书馆", type_="process")
    _seed_knowledge(db_session_factory, 4, domain="奖助", type_="info")
    headers = _login(api_client, "admin-001")

    body = _list_scoped(api_client, headers, domain="奖助")
    assert body["total"] == 4, "total 应为 领域过滤后 的条数"
    assert len(body["items"]) == 4
    assert body["truncated"] is False

    body = _list_scoped(api_client, headers, type="process")
    assert body["total"] == 10, "total 应为 类型过滤后 的条数"


def test_truncation_respects_filter(api_client, db_session_factory):
    """筛选 + 截断同时生效：limit=3 且 domain=图书馆 → 只回 3 条图书馆，total=10。"""
    _seed_knowledge(db_session_factory, 10, domain="图书馆")
    _seed_knowledge(db_session_factory, 8, domain="奖助")
    body = _list_scoped(
        api_client, _login(api_client, "admin-001"), domain="图书馆", limit=3
    )
    assert body["total"] == 10
    assert len(body["items"]) == 3
    assert {i["domain"] for i in body["items"]} == {"图书馆"}
    assert body["truncated"] is True


# ---------- 排序与权限不变 ----------


def test_order_still_by_id_asc(api_client, db_session_factory):
    """截断不得破坏原有 id 升序契约。"""
    _seed_knowledge(db_session_factory, 10)
    body = _list_scoped(api_client, _login(api_client, "admin-001"), limit=6)
    ids = [i["id"] for i in body["items"]]
    assert ids == sorted(ids)


def test_paging_requires_kb_review_perm(api_client, db_session_factory):
    """权限不变：student 仍 403，未登录仍 401。"""
    _seed_knowledge(db_session_factory, 5)
    assert (
        _list(api_client, _login(api_client, "student-001"), limit=2).status_code == 403
    )
    assert api_client.get("/api/admin/knowledge").status_code == 401


def test_invalid_limit_422(api_client):
    """limit 非整数 → 422（FastAPI 类型校验），不静默吞成默认值。"""
    headers = _login(api_client, "admin-001")
    assert _list(api_client, headers, limit="abc").status_code == 422
