# CampusDesk（校园服务台）— 校园智能服务台 Agent

校园报修/咨询/投诉的智能服务 Agent 平台：学生一句话提交问题 → 自动意图识别、分类定级、派单、跟踪、回访，服务闭环。

## 功能特性

- **4 个专职 Agent 编排**：Entry（意图分流）/ Repair（报修多轮对话）/ Consult（诊断式咨询）/ Quality（关闭 24h 后回访），LangGraph 显式状态图编排
- **9 个确定性工具**：报修侧 6 + 咨询侧 3，每工具独立单测、不依赖 LLM
- **工单状态机 6 态 8 边**：SUBMITTED→ASSIGNED→IN_PROGRESS→PENDING_VERIFY→CLOSED + CANCELLED，非法跳转在图结构上不存在；超时升级用字段而非状态，挂起 3 天自动关闭
- **用户长期记忆**：三层上下文（会话/画像/全局），报修画像随对话更新、回访记录沉淀
- **评测闭环**：76 条对话剧本全量评测，意图/报修/投诉 100% 达标
- **Langfuse 全链路可观测**：agent 步骤 / 工具调用 / 状态跳转 / LLM call 四类埋点
- **前端 5 页 + JWT/RBAC**：登录 / 对话提交 / 我的工单 / 管理列表 / 数据看板，四角色数据过滤 + 越权拦截
- **Docker Compose 一键起**：MySQL + 后端 + 前端（nginx 反代 8080），未配 LLM key 也能启动

## 技术栈

| 层 | 选型 |
|----|------|
| 编排 | LangGraph + LangChain（checkpointer: SQLite SqliteSaver）|
| LLM | DeepSeek（deepseek-v4-flash，OpenAI 兼容）|
| 后端 | FastAPI + Pydantic v2（SSE 流式对话）|
| 数据 | MySQL 8 + SQLAlchemy 2.0（alembic 迁移，禁手改表）|
| 前端 | Vue3 + Element Plus（最小闭环）|
| 可观测 | Langfuse（agent 步骤级 trace）|
| 语言 | Python 3.14 |
| 工程 | pytest + ruff · Docker Compose · GitHub Actions（CI）|

## 快速开始

### 方式一：本机开发

```bash
# 1. 创建虚拟环境并安装依赖（官方 PyPI 在国内可能卡死，统一用清华镜像）
py -3.14 -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2. 配置 .env：DEEPSEEK_API_KEY / DEEPSEEK_MODEL / LANGFUSE_PUBLIC_KEY /
#    LANGFUSE_SECRET_KEY / LANGFUSE_HOST / DATABASE_URL（格式见 CLAUDE.md §9）

# 3. 种子数据入库（账号 + FAQ + 15 张演示工单）
.venv/Scripts/python scripts/seed_db.py
.venv/Scripts/python scripts/seed_demo_data.py

# 4. 启动后端（--workers 1 硬约束：多 worker 会多图单例冲突）
.venv/Scripts/python -m uvicorn campus_desk.api.app:create_app --factory \
  --host 0.0.0.0 --port 8000 --workers 1

# 5. 启动前端
cd frontend && npm run dev    # 访问 http://localhost:5173
```

### 方式二：Docker 一键起

```bash
docker compose up --build
# 访问 http://localhost:8080（nginx 托管前端 + /api 反代后端）
```

- 编排三服务：MySQL 8（带 healthcheck）+ 后端（entrypoint 自动迁移/种子/demo 数据）+ 前端（nginx）
- 未传 `DEEPSEEK_API_KEY` 也能启动：配置用 `${VAR:-}` 插值兜底，无 key 时对话自动降级为规则回答

## 演示账号

| 账号 | 角色 | 权限 |
|------|------|------|
| student-001 | student | 提交报修/咨询、查看自己的工单 |
| staff-001 | staff | 工单处理、管理列表、数据看板 |
| it-001 | it_staff | 工单处理（IT 部门）、管理列表、数据看板 |
| admin-001 | admin | 全量权限（含管理）|

密码统一 `123456`。角色差异 = 菜单可见性 + 后端 RBAC 数据过滤双保险（越权访问拦截，已自动化验收）。

## 项目结构

```
campus-desk/
├─ src/campus_desk/            后端主包
│  ├─ entry/                   EntryAgent：意图识别 + 置信度门控 + 多意图分流
│  ├─ repair/                  RepairAgent：报修多轮对话 + RepairGraph（投诉复用此管道建 P1 单）
│  ├─ consult/                 ConsultAgent：诊断式咨询（≤8 轮追问 + 3 工具排查）
│  ├─ quality/                 QualityAgent：关闭 24h 后惰性回访
│  ├─ tools/                   9 确定性工具（报修 6 + 咨询 3）
│  ├─ state_machine/           6 态 8 边状态机（纯函数白名单 + 原子写库）
│  ├─ scheduler/               超时升级扫描（APScheduler，纯函数时钟注入）
│  ├─ api/                     FastAPI 9 接口（SSE 流式对话 + JWT 登录 + RBAC）
│  ├─ db/                      SQLAlchemy + MySQL（alembic 迁移）
│  ├─ eval/                    评测 runner（76 条剧本行为断言）
│  ├─ telemetry.py             Langfuse 惰性埋点（无 key 零开销）
│  ├─ security.py              JWT（HS256）+ pbkdf2 密码哈希
│  └─ config.py                pydantic-settings 配置加载
├─ frontend/src/views/         Vue3 5 页（Login / Chat / MyTickets / Management / Dashboard）
├─ scripts/                    seed_db / seed_demo_data / ingest_eval_data / verify_env / smoke_langfuse
├─ tests/                      30 个测试文件（298 用例）
└─ docs/                       项目文档（本地私有仓库管理，不进主 git）
```

## 评测结果（M6 基线，2026-08-06）

76 条对话剧本全量评测（报修 16 + 咨询 16 + 投诉 20 + 闲聊 16 + 多意图 6 + 重复报修 2），评测集 JSON 入 git 并由 scripts/ingest_eval_data.py 同步 MySQL，可重复跑：

| 指标 | 结果 |
|------|------|
| 意图分类准确率 | **100.0%**（76/76）|
| 报修链路成功率 | **100.0%**（18/18，平均轮次 1.3）|
| 投诉链路成功率 | **100.0%**（20/20，平均轮次 0.2）|
| 咨询自助解决率 | 68.8%（11/16；断言通过率 100%，介入率 25%）|
| 单测 | pytest 286 绿 + ruff 零告警 |

评测口径：行为断言（tool:/status:）而非对话字面；报修/投诉失配判失败、咨询失配不判失败（提前解答 = 合理自助解决）；评测与生产同代码、不同 checkpointer（InMemorySaver 隔离，可无限重跑）。

## 关键设计亮点

**1. LangGraph 编排 + 确定性工具，流程可断言**
显式状态图 = 确定性流程，非法跳转在图结构上就不存在。9 个工具全部确定性实现、独立单测（不依赖 LLM）——Agent 只做决策不碰数据库，业务规则可单测、评测才能稳定。

**2. 状态机 6 态 8 边 + SAVEPOINT 原子写库**
纯函数白名单 machine.py 为唯一权威，图只做渲染（双保险）。apply_transition 是全项目唯一写入口：状态 + 审计日志写在同一保存点，任一步失败整体回滚——日志永不与状态不一致。

**3. 多 Agent 编排，投诉不建独立流程**
投诉 = RepairGraph 参数化复用（ticket_type 构建参数），建 P1 工单、不自动派单待管理员；Quality 回访惰性触发（学生进对话时查，不装常驻调度）。四个 Agent 各有边界，避免平行流程蔓延。

**4. 评测闭环驱动迭代**
76 条剧本 JSON 入库，行为断言防"脚本学生答非所问"失真；意图准确率 M2 实测 94.4% → M5 校准至 100% 并维持到 M6（可配化回归零漂移）——"评测驱动选型"是面试叙事核心。

**5. 用户长期记忆 + 全链路可观测**
三层上下文（会话/画像/全局）超出常规单会话框架；画像只进 LLM prompt 不进规则层（防关键词干扰分类）。Langfuse 惰性埋点四类 span，无 key 环境零开销零报错。

## 文档地图

| 文档 | 内容 |
|------|------|
| CLAUDE.md | 项目唯一权威规范（技术栈 / 核心设计 / 运行命令 / 教训清单）|
| docs/PROJECT_REQUIREMENTS.md | 完整需求规格（状态机 / 工具表 / 评测目标）|
| docs/TECH_DECISIONS.md | 选型理由 + 面试备答（为什么选它、别的为什么不行）|
| docs/STATUS.md | 当前进度 / 下一步 / 实测基线数据 |
| docs/DEV_JOURNAL.md | 迭代日志（做了什么 / 坑 / 面试点）|
| docs/eval_report_m2~m6.md | 各里程碑评测报告（本地私有管理）|
