"""咨询侧 3 个确定性工具（M3，需求 §5）：IT 诊断用（M4 ConsultAgent 挂接）。

数据说明：
- query_account_status 读 accounts mock 表（生产接校园网计费系统，见需求 §12 契约）
- query_announcement 读公告表（区域关键词匹配）
- search_faq 关键词匹配 FAQ 库（轻 RAG，不做向量；M3 先入 9 条，M4 补全 20-30）
"""

import json

from langchain.tools import BaseTool, tool

from campus_desk.db.models import Account, Announcement, Faq
from campus_desk.db.session import SessionFactory


def create_consult_tools(session_factory: SessionFactory) -> list[BaseTool]:
    """咨询侧 3 工具工厂。"""

    @tool("query_account_status", parse_docstring=True)
    def query_account_status(student_no: str) -> str:
        """查询学生网络账号状态（正常/欠费/过期）。

        Args:
            student_no: 学号
        """
        with session_factory() as session, session.begin():
            acct = session.query(Account).filter(Account.student_no == student_no).first()
        if acct is None:
            return f"错误: 未找到学号 {student_no} 的网络账号"
        state_cn = {"normal": "正常", "overdue": "欠费停机", "expired": "账号过期"}
        return f"学号 {student_no} 网络账号状态: {state_cn.get(acct.status, acct.status)}（{acct.note or ''}）"

    @tool("query_announcement", parse_docstring=True)
    def query_announcement(region: str) -> str:
        """查询区域故障公告（匹配区域关键词，如 3号楼/全校）。

        Args:
            region: 区域（楼栋名或"全校"）
        """
        with session_factory() as session, session.begin():
            rows = session.query(Announcement).filter(Announcement.region == region).all()
        if not rows:
            return f"未找到 {region} 的公告"
        items = [{"region": a.region, "content": a.content} for a in rows]
        return json.dumps(items, ensure_ascii=False)

    @tool("search_faq", parse_docstring=True)
    def search_faq(keyword: str) -> str:
        """检索常见问题（关键词匹配，返回命中 3 条内）。

        Args:
            keyword: 检索关键词（如 密码/网速/选课）
        """
        with session_factory() as session, session.begin():
            faqs = session.query(Faq).all()
        hits = []
        for faq in faqs:
            score = sum(1 for kw in faq.keywords.split(",") if kw and kw in keyword)
            if score > 0:
                hits.append((score, faq))
        hits.sort(key=lambda x: x[0], reverse=True)
        if not hits:
            return "未找到相关问题，请换个说法，或让我转人工为您服务。"
        items = [{"question": faq.question, "answer": faq.answer} for _, faq in hits[:3]]
        return json.dumps(items, ensure_ascii=False)

    return [query_account_status, query_announcement, search_faq]
