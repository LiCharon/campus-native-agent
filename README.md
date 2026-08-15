# Campus Native Agent — 校园智能服务助手

校园信息聚合 + 问答 + 引导 + 索引的智能服务 Agent：学生一句话提问 → 意图分流（4 类）→ 能答的直接答（知识库检索，info/process/index 分型组装）→ 缺信息追问澄清（≤3 轮）→ 仍答不上转人工兜底（bad_cases 沉淀，进化闭环养料）。

## 功能特性

- **4 类意图分流**：knowledge（知识问答）/ tool_query（动态数据查询，M2 实装）/ multi_intent（多问题）/ other（闲聊与超范围兜底）；LLM 三层防线（结构化输出 → 重试 1 次 → 关键词规则兜底）+ 置信度门控（<0.7 转人工）
- **知识库检索组装**：关键词计分检索 + 条目 type 分型（info 直接答 / process 流程清单 / index 索引引导"去哪查"），检索层可替换（向量检索 RAG 演进预留，Agent 侧零改动）
- **追问澄清**：检索未命中 → LLM 决策追问（≤3 轮硬约束，图结构执行不靠 LLM 自觉）→ 补充后合并重检索 → 仍未命中转人工
- **转人工兜底**：未解决问题沉淀进 bad_cases（status=PENDING），M3 进化闭环（管理员审查 → 补入知识库，"越用越聪明"）
- **LangGraph 显式状态图**：entry 分流图 + knowledge 问答图双图编排，interrupt 收敛唯一 wait 节点，SqliteSaver 会话记忆（每用户独立实例 + 全局锁串行化）
- **Langfuse 全链路可观测**：agent 步骤 / LLM call 埋点，无 key 环境零开销零报错
- **意图评测基线**：24 条剧本真 LLM 评测，意图准确率 95.8%（含低置信门控审计）
- **前端最小闭环**：登录 + 对话（Vue3 + Element Plus），JWT 鉴权 + 三角色（student / cs_staff / admin）

## 技术栈

| 层 | 选型 |
|----|------|
| 编排 | LangGraph + LangChain（checkpointer: SQLite SqliteSaver）|
| LLM | DeepSeek（deepseek-v4-flash，OpenAI 兼容；真 FC 已探测可用）|
| 后端 | FastAPI + Pydantic v2 |
| 数据 | MySQL 8 + SQLAlchemy 2.0（alembic 迁移，禁手改表）|
| 前端 | Vue3 + Element Plus（最小闭环）|
| 可观测 | Langfuse（agent 步骤级 trace）|
| 语言 | Python 3.14 |
| 工程 | pytest + ruff |

## 快速开始

```bash
# 1. 创建虚拟环境并安装依赖（官方 PyPI 在国内可能卡死，统一用清华镜像）
py -3.14 -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2. 配置 .env：DEEPSEEK_API_KEY / DEEPSEEK_MODEL / LANGFUSE_PUBLIC_KEY /
#    LANGFUSE_SECRET_KEY / LANGFUSE_HOST / DATABASE_URL（格式见 .env.example）

# 3. 种子数据入库（10 个演示账号 + 36 条通用校园知识，幂等可重跑）
.venv/Scripts/python scripts/seed_db.py
#    本地化校园真实信息（可选，私有数据不进 git）：按 config/zjut_local_data.json
#    格式准备后运行
.venv/Scripts/python scripts/seed_zjut_local.py

# 4. 环境验证（LangGraph quickstart / DeepSeek 结构化输出 / 真 FC 探测）
PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/verify_env.py

# 5. 意图评测（24 条剧本，真 LLM 跑分；无 key 自动 SKIP）
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m campus_desk.eval.runner

# 6. 启动后端（--workers 1 硬约束：多 worker = 多图单例冲突）
.venv/Scripts/python -m uvicorn campus_desk.api.app:create_app --factory \
  --host 0.0.0.0 --port 8000 --workers 1

# 7. 启动前端
cd frontend && npm run dev    # 访问 http://localhost:5173
```

## 演示账号

| 账号 | 角色 | 权限 |
|------|------|------|
| student-001 | student | 对话问答（学生）|
| cs-001 | cs_staff | 人工客服（工作台 M3 接入）|
| admin-001 | admin | 管理（进化闭环工作台 M3）|

密码统一 `123456`。JWT 鉴权，user_id 取自 token 绝不信任请求体（越权拦截已自动化验收）。

## 项目结构

```
campus-desk/
├─ src/campus_desk/            后端主包
│  ├─ entry/                   入口分流：4 类意图识别 + 三层防线 + 置信度门控 + 路由
│  │                           （intent.py / entry_graph.py / orchestrator.py）
│  ├─ knowledge/               知识管道：关键词检索 + type 组装 + 追问决策 + 双节点图
│  │                           （search.py / decide.py / graph.py）
│  ├─ api/                     FastAPI：登录 JWT + 对话 + 图注册表（auth.py / chat.py / graphs.py）
│  ├─ db/                      SQLAlchemy + 幂等种子（users / knowledge_entries / bad_cases / user_profiles）
│  ├─ eval/                    意图评测 runner + 24 条剧本（行为断言）
│  ├─ telemetry.py             Langfuse 惰性埋点（无 key 零开销）
│  ├─ security.py              JWT（HS256）+ pbkdf2 密码哈希
│  ├─ llm.py                   build_llm 统一构造（json_object 构造期声明）
│  ├─ env_check.py             环境验证 3 项（含真 FC 探测）
│  └─ config.py                pydantic-settings 配置加载
├─ frontend/src/views/         Vue3 2 页（Login / Chat）
├─ scripts/                    seed_db / seed_zjut_local / verify_env / ingest_eval_data
├─ tests/                      15 个测试文件（96 用例）
└─ docs/                       项目文档（本地私有仓库管理，不进主 git）
```

## 评测结果（M1 基线，2026-08-15）

24 条对话剧本意图评测（knowledge 18 + tool_query 2 + multi_intent 2 + other 2），真 LLM 跑分，可重复：

| 指标 | 结果 |
|------|------|
| 意图分类准确率 | **95.8%**（23/24）|
| 路由准确率 | **95.8%**（23/24）|
| 低置信门控 | zjut-intent-008（"座位怎么预约？"，置信 0.60）→ 转人工（参考信息随行）|
| 单测 | pytest 96 绿 + ruff 零告警 |

评测口径：行为断言（expected_route + 门控）而非对话字面；真 LLM 跑分（无 key 自动 SKIP，不进 CI）；评测与生产同代码、InMemorySaver 隔离可无限重跑。

## 关键设计亮点

**1. 两层解耦：入口分流 vs 知识库分类**
入口分流回答"怎么处理"（4 类意图，LLM 判定、稳定）；知识库分类回答"是什么"（领域 + type 分型，人工组织、渐进长出）。index 不设入口意图——"能否命中知识库"只有检索层知道。

**2. 静态流程检索直答，状态机退役**
补卡/缓考/调宿舍等流程是静态知识（材料/地点/时间不变），检索到直接答就是最优解；无动态生命周期实体，不做状态机。多轮能力保留给追问澄清。

**3. 追问澄清 ≤3 轮硬约束**
轮次上限由图结构执行（rounds 计数 + 条件边），不靠 LLM 自觉；interrupt 收敛唯一 wait 节点（LangGraph 重入不落盘教训：问句/计数由 return 写入 state）。

**4. 真 FC 探测先行，不写死实现**
M1 环境验证用 `bind_tools` 实测 DeepSeek function calling 可用性（**FC_SUPPORTED=True**）——结果决定 M2 工具管道走真 FC 还是伪 FC 兜底，探测结果出来前不写死任何一种。

**5. 评测驱动选型 + 进化闭环**
24 条剧本行为断言基线 95.8%；每次答不上都沉淀为 bad_cases 知识库养料（M3 管理员审查回流），"越用越聪明"是面试叙事核心。

## 文档说明

项目内部规范与设计文档（CLAUDE.md / docs/，含选型记录、迭代日志、评测报告）为本地私有仓库，不随本仓库公开——本 README 与代码即公开门面。
