# Campus Native Agent — 校园智能服务助手

校园信息聚合 + 问答 + 引导 + 索引的智能服务 Agent：学生一句话提问 → 意图分流（4 类）→ 能答的直接答（知识库检索，info/process/index 分型组装）→ 缺信息追问澄清（≤3 轮）→ 仍答不上转人工兜底（问题沉淀进 bad_cases，管理员审查后回流知识库，形成"进化闭环"）。

## 功能特性

- **4 类意图分流**：knowledge（知识问答）/ tool_query（动态数据查询）/ multi_intent（多问题）/ other（闲聊与超范围兜底）；LLM 三层防线（结构化输出 → 重试 1 次 → 关键词规则兜底）+ 置信度门控（<0.7 转人工）
- **知识库检索组装**：关键词计分检索 + 条目 type 分型（info 直接答 / process 流程清单 / index 索引引导"去哪查"），检索层可替换（向量检索 RAG 演进预留，Agent 侧零改动）
- **确定性工具查询**：真 Function Calling → 字段抽取 + 确定性查表 + 模板组装；覆盖教室 / 座位 / 课表 / 成绩 / 考试 / 借阅 / 余额 / 电量 / 失物 / 班车 / 日历 / 通知等场景；参数白名单由 schema 动态派生，缺参按领域路由追问；失败链降级（索引引导 → 连续熔断 → 转人工）
- **追问澄清**：检索未命中 → LLM 决策追问（≤3 轮硬约束，图结构执行不靠 LLM 自觉）→ 补充后合并重检索 → 仍未命中转人工
- **转人工兜底**：未解决问题沉淀进 bad_cases（status=PENDING），进化闭环（管理员审查 → 补入知识库）
- **LangGraph 显式状态图**：entry 分流图 + knowledge 问答图 + query 工具图，interrupt 收敛唯一 wait 节点，SqliteSaver 会话记忆（query 图派生独立 thread_id 防串挂起）
- **Langfuse 全链路可观测**：agent 步骤 / LLM call 埋点，无 key 环境零开销零报错
- **角色权限体系**：角色默认权限 ∪ 附加权限位（JWT claims 携带）；用户管理 / 客服工作台 / 数据看板 / 审计日志
- **前端闭环**：登录 + 对话 + 客服工作台 + 知识库审查 + 数据看板 + 用户管理 + 审计日志（Vue3 + Element Plus，JWT 鉴权）

## 技术栈

| 层 | 选型 |
|----|------|
| 编排 | LangGraph + LangChain（checkpointer: SQLite SqliteSaver）|
| LLM | DeepSeek（deepseek-v4-flash，OpenAI 兼容；真 FC 已探测可用）|
| 后端 | FastAPI + Pydantic v2 |
| 数据 | MySQL 8 + SQLAlchemy 2.0（alembic 迁移，禁手改表）|
| 前端 | Vue3 + Element Plus |
| 可观测 | Langfuse（agent 步骤级 trace）|
| 语言 | Python 3.14（需该版本，低版本可能不兼容）|
| 工程 | pytest + ruff |

## 快速开始

> **前置**：本地已运行 MySQL 8；Python 3.14（项目依赖该版本特性，低版本可能不兼容）；step 4 需联网并配置 DeepSeek API key（见 `.env`）。

```bash
# 1. 创建虚拟环境并安装依赖（官方 PyPI 在国内可能卡死，统一用清华镜像）
py -3.14 -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2. 配置 .env：DEEPSEEK_API_KEY / DEEPSEEK_MODEL / LANGFUSE_PUBLIC_KEY /
#    LANGFUSE_SECRET_KEY / LANGFUSE_HOST / DATABASE_URL（格式见 .env.example）

# 3. 种子数据入库（演示账号 + 36 条通用校园知识，幂等可重跑）
.venv/Scripts/python scripts/seed_db.py

# 4. 环境验证（LangGraph quickstart / DeepSeek 结构化输出 / 真 FC 探测）
PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/verify_env.py

# 5. 启动后端（--workers 1 硬约束：多 worker = 多图单例冲突）
.venv/Scripts/python -m uvicorn campus_desk.api.app:create_app --factory \
  --host 0.0.0.0 --port 8000 --workers 1

# 6. 启动前端
cd frontend && npm run dev    # 访问 http://localhost:5173
```

> 本地校园真实信息为私有数据，不进 git，由独立的本地数据注入脚本维护（不随公开仓库分发）。

## 演示账号

| 账号 | 角色 | 权限 |
|------|------|------|
| student-001 / 002 / 003 | student | 对话问答（学生）|
| cs-001 | cs_staff | 人工客服（工作台接待）|
| admin-001 | admin | 管理（用户管理 / 知识库审查 / 看板）|

密码统一 `123456`。JWT 鉴权，user_id 取自 token 绝不信任请求体（越权拦截已自动化验收）。

## 项目结构

```
campus-desk/
├─ src/campus_desk/            后端主包
│  ├─ entry/                   入口分流：4 类意图识别 + 三层防线 + 置信度门控 + 路由
│  │                           （intent.py / entry_graph.py / orchestrator.py）
│  ├─ knowledge/               知识管道：关键词检索 + type 组装 + 追问决策 + 双节点图
│  │                           （search.py / decide.py / graph.py）
│  ├─ query/                   工具查询管道：工具注册表（strict FC schema）+ 规则兜底字段抽取
│  │                           + 模板组装 + QueryGraph（tools.py / field_extract.py / assemble.py / graph.py）
│  ├─ api/                     FastAPI：鉴权 / 对话 / 管理 / 客服 / 反馈（routes/ 分模块）
│  ├─ db/                      SQLAlchemy + alembic 迁移 + 幂等种子（users / knowledge_entries / bad_cases / suggestions / 工具数据表）
│  ├─ telemetry.py             Langfuse 惰性埋点（无 key 零开销）
│  ├─ security.py              JWT（HS256）+ pbkdf2 密码哈希
│  ├─ llm.py                   build_llm / build_tool_llm 统一构造（json_object 与 FC 分离）
│  ├─ env_check.py             环境验证 3 项（含真 FC 探测）
│  └─ config.py                pydantic-settings 配置加载
├─ frontend/src/views/         Vue3 页面（Login / Chat / CsWorkbench / AdminReview / StatsDashboard / UserManage / LogViewer）
├─ scripts/                    seed_db / verify_env / ingest_eval_data / 本地数据注入脚本
├─ tests/                      测试（pytest + ruff）
└─ docs/                       项目文档（独立私有文档仓，不随主仓公开，见文末说明）
```

## 关键设计亮点

**1. 两层解耦：入口分流 vs 知识库分类**
入口分流回答"怎么处理"（4 类意图，LLM 判定、稳定）；知识库分类回答"是什么"（领域 + type 分型，人工组织、渐进长出）。index 不设入口意图——"能否命中知识库"只有检索层知道。

**2. 静态流程检索直答，状态机退役**
补卡/缓考/调宿舍等流程是静态知识（材料/地点/时间不变），检索到直接答就是最优解；无动态生命周期实体，不做状态机。多轮能力保留给追问澄清。

**3. 追问澄清 ≤3 轮硬约束**
轮次上限由图结构执行（rounds 计数 + 条件边），不靠 LLM 自觉；interrupt 收敛唯一 wait 节点（LangGraph 重入不落盘教训：问句/计数由 return 写入 state）。

**4. 真 FC 探测先行，不写死实现**
环境验证用 `bind_tools` 实测 DeepSeek function calling 可用性（**FC_SUPPORTED=True**）——结果决定工具管道走真 FC 还是规则兜底，探测结果出来前不写死任何一种。

**5. 构建期近重复自动检测**
本地数据构建脚本内置相似度检测：同主题换说法的近重复直接阻断构建强制合并，模板撞车的不同主题仅告警——把"相近问题"判重从人工排查升级为自动化关卡。

## 文档说明

项目内部规范与设计文档（CLAUDE.md / docs/，含选型记录、迭代日志）为本地私有仓库，不随本仓库公开——本 README 与代码即公开门面。
