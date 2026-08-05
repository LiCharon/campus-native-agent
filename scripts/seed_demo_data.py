"""Demo 演示数据脚本（M6）：重建有质量的工单演示数据。

用法：`PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/seed_demo_data.py`

语义：**先清空 tickets/ticket_logs 再插入**（demo 专用，测试/评测数据用内存库不受影响）。
验收发现 179 张冒烟测试工单（重复描述、category 全"其他"、dept 全 NULL）导致
员工端/看板无数据可看——重建 15 张覆盖 6 态/5 类/2 部门的真实感工单。

数据设计（可复现、幂等）：
- student-001 主责 10 张（学生端演示丰富），student-002/003 各 2-3 张（RBAC 过滤演示）
- 状态全覆盖：SUBMITTED/ASSIGNED/IN_PROGRESS/PENDING_VERIFY/CLOSED/CANCELLED
- 部门覆盖：后勤 12 张（staff-001 可见）+ 信息中心 3 张（it_staff 可见）
- CLOSED 单带完整回访数据（closed_at/rating/review_comment/reviewed_at）
- 每单配状态流转日志（详情抽屉时间线用）
"""

import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from campus_desk.db.models import Ticket, TicketLog
from campus_desk.db.session import default_session_factory

_NOW = datetime.now(UTC)
D = lambda days, hours=0: _NOW - timedelta(days=days, hours=hours)  # noqa: E731

# (id, user_id, type, description, contact, category, priority, status, building, location, dept, repairman_id, 创建偏移, 关闭偏移, rating, comment)
TICKETS = [
    (1, "student-001", "repair", "3号楼502宿舍的灯管一直闪烁，晚上写作业很刺眼", "李华", "水电", "P2", "CLOSED", "3号楼", None, "后勤", "rm-001", 5, 3, 5, "师傅来得很快，十几分钟就换好了"),
    (2, "student-001", "repair", "5号楼311水龙头一直漏水，地上都是水，很危险", "李华", "水电", "P1", "IN_PROGRESS", "5号楼", None, "后勤", "rm-002", 2, None, None, None),
    (3, "student-001", "repair", "3号楼608宿舍校园网完全连不上，网线WiFi都不行", "李华", "网络", "P2", "PENDING_VERIFY", "3号楼", None, "信息中心", "rm-005", 4, None, None, None),
    (4, "student-001", "repair", "2号楼415空调不制冷，出风全是热风", "李华", "设备", "P2", "ASSIGNED", "2号楼", None, "后勤", "rm-003", 1, None, None, None),
    (5, "student-001", "repair", "4号楼203宿舍门锁坏了，钥匙拧不动", "李华", "门窗", "P3", "SUBMITTED", "4号楼", None, "后勤", None, 0, None, None, None),
    (6, "student-001", "repair", "6号楼302卫生间下水道堵了，一直反味", "李华", "环境", "P2", "CLOSED", "6号楼", None, "后勤", "rm-003", 6, 4, 4, "清理得很干净，还帮忙擦了地面"),
    (7, "student-001", "repair", "1号楼110宿舍插座全部没电，充电器插上没反应", "李华", "水电", "P2", "CANCELLED", "1号楼", None, "后勤", None, 7, None, None, None),
    (8, "student-001", "complaint", "一食堂二楼窗口打饭时有人插队，工作人员也不管", "李华", "其他", "P1", "CLOSED", None, "一食堂二楼窗口", "后勤", None, 4, 3, 3, "反馈后食堂加强了排队管理，基本改善"),
    (9, "student-001", "repair", "校园网账号欠费停机了，请问怎么办理复机", "李华", "网络", "P2", "CLOSED", None, None, "信息中心", "rm-007", 8, 6, 5, "帮我查了欠费明细，指导线上缴费，已经恢复了"),
    (10, "student-001", "repair", "寝室的风扇声音特别大，晚上吵得睡不着", "李华", "设备", "P2", "ASSIGNED", "3号楼", None, "后勤", "rm-003", 3, None, None, None),
    (11, "student-002", "repair", "4号楼506晚上网速特别慢，看视频一直卡", "王芳", "网络", "P2", "IN_PROGRESS", "4号楼", None, "信息中心", "rm-006", 2, None, None, None),
    (12, "student-002", "repair", "7号楼218热水器不热，水温只有二十度", "王芳", "水电", "P2", "SUBMITTED", "7号楼", None, "后勤", None, 1, None, None, None),
    (13, "student-003", "repair", "2号楼512阳台玻璃裂了一道缝，怕掉下来砸到人", "张伟", "门窗", "P1", "ASSIGNED", "2号楼", None, "后勤", "rm-004", 2, None, None, None),
    (14, "student-003", "repair", "洗衣房3号洗衣机洗到一半就停了，衣服还在里面", "张伟", "设备", "P2", "PENDING_VERIFY", "1号楼", None, "后勤", "rm-003", 3, None, None, None),
    (15, "student-002", "complaint", "快递驿站取件时要额外收保管费，没有公示", "王芳", "其他", "P1", "CLOSED", None, "快递驿站", "后勤", None, 5, 4, 4, "驿站解释了原因并整改了收费公示"),
]

# 状态流转日志（按工单状态生成时间线）
def _logs(t):
    tid, uid, _, desc, contact, cat, pri, status, building, loc, dept, rid, d0, d1, rating, comment = t
    seq = [("SUBMITTED", "SUBMITTED", uid, "提交报修工单")]
    if status == "CANCELLED":
        seq.append(("SUBMITTED", "CANCELLED", uid, "学生申请撤回"))
    elif status != "SUBMITTED":
        seq.append(("SUBMITTED", "ASSIGNED", "system", "系统按类别自动派单"))
        if status in ("IN_PROGRESS", "PENDING_VERIFY", "CLOSED"):
            seq.append(("ASSIGNED", "IN_PROGRESS", rid or "system", "维修工接单"))
        if status in ("PENDING_VERIFY", "CLOSED"):
            seq.append(("IN_PROGRESS", "PENDING_VERIFY", rid or "system", "维修完成，待学生验收"))
        if status == "CLOSED":
            seq.append(("PENDING_VERIFY", "CLOSED", uid, "学生验收通过"))
    return [
        TicketLog(ticket_id=tid, from_status=a, to_status=b, actor=c,
                  note=note, created_at=D(d0, i * 3 + 1))
        for i, (a, b, c, note) in enumerate(seq)
    ]


def main() -> int:
    factory = default_session_factory()
    with factory() as session, session.begin():
        # 1. 清空（日志先删，外键顺序）+ 重置自增
        session.query(TicketLog).delete()
        session.query(Ticket).delete()
        session.execute(text("ALTER TABLE tickets AUTO_INCREMENT = 1"))

        # 2. 插入工单 + 日志
        for t in TICKETS:
            tid, uid, ttype, desc, contact, cat, pri, status, building, loc, dept, rid, d0, d1, rating, comment = t
            closed_at = D(d0 - d1) if d1 else None
            session.add(Ticket(
                id=tid, user_id=uid, ticket_type=ttype, description=desc, contact=contact,
                category=cat, priority=pri, status=status, building=building, location=loc,
                dept=dept, repairman_id=rid, created_at=D(d0, 2), updated_at=D(d0, 2),
                closed_at=closed_at,
                rating=rating,
                review_comment=comment if d1 else None,
                # 回访发生在关闭后（QualityAgent 关闭 24h 惰性触发语义）
                reviewed_at=D(d0 - d1, -26) if d1 else None,
            ))
            for log in _logs(t):
                session.add(log)

    # 3. 摘要
    with factory() as session:
        total = session.query(Ticket).count()
        by_status = {}
        by_dept = {}
        for s, in session.query(Ticket.status).all():
            by_status[s] = by_status.get(s, 0) + 1
        for d, in session.query(Ticket.dept).all():
            by_dept[d or "(空)"] = by_dept.get(d or "(空)", 0) + 1
        logs = session.query(TicketLog).count()
    print(f"Demo 工单重建完成：{total} 张 / 日志 {logs} 条")
    print(f"  状态分布: {by_status}")
    print(f"  部门分布: {by_dept}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
