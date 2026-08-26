# CLAUDE.md — Campus Native Agent（校园智能服务助手）

> 项目开工必读规范；设计契约权威在 docs/design/ZJUT_DESIGN.md，进度/pytest 数权威在 docs/journal/STATUS.md。改动必同步对应文档。

## 0. 定位
学生一句话问任何校园问题 → 意图分流 → 能答直接答（知识库检索）/ 缺信息追问澄清（≤3 轮）/ 仍答不上转人工（bad_cases 沉淀）；不代办事务（报修走后勤、资金操作不在范围）。

### 0.1 项目事实（权威源：pyproject.toml / README.md）
- 展示名 Campus Native Agent｜包名 `campus_desk`｜GitHub LiCharon/campus-native-agent

### 0.2 文档地图（开工按需读；本表外的文档 → docs/_INDEX.md 分类导航）
| 文档 | 何时读 |
|------|--------|
| docs/_INDEX.md | **分类导航**（代表索引，非全量；M6–M11 计划/报告以 STATUS.md / DEV_JOURNAL.md 为准）|
| docs/design/ZJUT_DESIGN.md | **设计契约（权威）**：入口/11 域/分型/追问/进化闭环/待定项 |
| docs/journal/STATUS.md | 当前进度/下一步/基线/pytest passed 数（随里程碑更新）|
| docs/journal/DEV_JOURNAL.md | 最新迭代记录（做了什么/坑/面试点）|
| docs/requirements/TECH_DECISIONS.md | 选型原因 + 面试话术 |
| docs/plans/ | 各里程碑历史实现计划（M1–M11，已交付仅作记录，勿据此开工）；含 RAG_ROADMAP.md 演进蓝图 |
| docs/specs/AI_CODING_SPEC.md | AI 编码规范细则（AGENTS.md 详版，含外部 AI 协作踩坑）|
| AGENTS.md | 通用 AI 编码纪律（轻量版）|

### 0.3 里程碑命名
新系列 `M{n}-ZJUT`（M1–M11），旧 M0–M7 无后缀为历史基线；文档引用新系列须带 `-ZJUT`。

## 1. 为什么做
- InterviewAI 教训 → 本项目补齐：LangGraph 编排 + 工具调用 + 用户记忆 + Langfuse 可观测 + 量化评测。
- 聚焦原则（原"8:2"）：以 Agent 能力本位为核心，工程与壳按需演进，**不提前过度建设**（Redis 即过度建设教训）。
- 目标岗位：Agent / LLM 应用开发。

## 2. 技术栈
Python 3.14 · LangGraph(SqliteSaver) · LangChain · FastAPI+Pydantic v2 · MySQL8+SQLAlchemy2.0 · Langfuse(localhost:3001) · DeepSeek(deepseek-v4-flash-vision-exp) · Vue3 · pytest+ruff+httpx · MCP(演示加分)
- 真 FC 可用（M1-T12）：bind_tools 返回真实 tool_calls；但 build_llm 写死 response_format=json_object，FC prompt 须含 "json" 否则 400（FC 用 build_tool_llm）。

## 3. 核心设计（拍板结论；细节 → ZJUT_DESIGN.md）
- 入口分流 4 类（knowledge/tool_query/multi_intent/other）+ 置信度 <0.7 转人工；三层防线（结构化→重试1次→关键词兜底）。
- 知识库 11 域零重叠 + type 分型（info/process/index）；关键词起步、向量检索演进预留。
- 追问 ≤3 轮（图结构硬约束）；转人工 + 进化闭环（bad_cases → 管理页审查补库）。
- 工具查询 13 工具 + 10 mock 表（M2+）；参数白名单动态派生、按域路由追问。
- 权限 RBAC 三表（M6）；运行时以 DB 为准——JWT claims 鉴权不查库，改权限需重登。

## 4. 工程化标准
- Langfuse 全链路埋点 + score_trace 自动评分（answer1.0/ask0.6/degraded0.3/handoff0.0）。
- 评测数据集入 git（意图 56 zjut_intent.json + 链路 49 zjut_chain.json）；真 LLM 跑分无 key SKIP；InMemorySaver 隔离可重跑。
- 安全：.env gitignore；alembic 迁移禁手改表；JWT claims 鉴权不查库。
- 前端 Vue3+Element Plus 最小闭环，品牌不出现本地地名/校名。

## 5. AI Coding 顺序规范
- 开工：本文件 + AGENTS.md + 记忆索引 + ZJUT_DESIGN.md + 相关 docs；todo 先列清单。
- 想清楚再动：方案先确认；3+ 文件或动 DB/权限 → Plan 模式。
- 小步循环：一次一功能点，改完立即 pytest。
- 收尾三件套（缺一不可）：① DEV_JOURNAL 追加 ② STATUS 同步 ③ 涉及文档回填（ZJUT_DESIGN/_INDEX，以代码为准）；里程碑收尾顺手核对 _INDEX 的 M 系列行与 STATUS.md 对齐（防悄悄过期）。非里程碑实体改动同样触发。
- 防忘：工具独立单测・DB 走迁移・追问轮次有测试・Langfuse 有 trace・分支 feature/*・改完问"STATUS/DESIGN 还准吗？"

## 6. 里程碑索引（详细基线/DoD → STATUS.md；新系列 M{n}-ZJUT）
| 里程碑 | 一句话 |
|---|---|
| M1-ZJUT 最小闭环 ✅08-15 | 4 类分流 + 检索组装 + 追问 + 转人工 + 36 种子 + 意图评测 |
| M2-ZJUT 工具管道 ✅08-16 | 真 FC + 2 工具 + 四层失败链 + multi_intent + 链路评测 |
| M3-ZJUT 进化闭环 ✅08-16 | 双通道反馈 + 管理页审查 + 补库 |
| M4-ZJUT 前端重构+权限 ✅08-17 | 品牌通用化 + 角色权限 + 客服台 + 审计 |
| M2+-ZJUT 工具扩展 ✅08-19 | 2→13 工具 + 10 mock 表 + 时间上下文 + 评测 56/49 |
| M4.5-ZJUT 知识库重构 ✅08-19 | 11 域零重叠 + 本地 262 条 + 近重复关卡 |
| M5-ZJUT 会话服务端化 ✅08-20 | conversations/messages + /api/sessions + 落库归属校验 |
| M6-ZJUT 权限升级 ✅08-20 | RBAC 三表 + 查库化 JWT claims |
| M7-ZJUT 用户记忆 ✅08-20 | user_profiles 增量抽取 + 注入追问/FC |
| M8-ZJUT 数据洞察 ✅08-24 | 业务指标聚合 + 看板 |
| M9-ZJUT 知识闭环 ✅08-24 | 知识增改删 + 管理页 + Qdrant 增量同步 |
| M10-ZJUT 检索工具化 ✅08-24 | Qdrant 混合检索 + 三档降级 + retrieve 第14工具 |
| M11-ZJUT 真实数据采集 ✅08-25 | 双轨采集 + 构建管道 + 知识库 262→834 + S5 V2 |
| 以后再说 | 真·多意图拆解 / MCP 暴露 / 渠道扩展 / LLM 摘要画像 / SSE 流式 |

pytest 全绿，passed 数见 STATUS.md。

## 7. 别再犯清单（陷阱 → 对策；细节 → DEV_JOURNAL.md）
**外部协作**：评审默认吸收文档/契约；"审查对象"声明不可信 → 对照磁盘逐条核验。
**LLM 结构化**：DeepSeek 不支持 with_structured_output（全400）→ 自写 prompt(含"json")+json_object+pydantic；FC 须 build_tool_llm。
**LangGraph**：interrupt 重入不落盘（计数/问句 return 写 state）；终态 thread 再 invoke 残留旧 state（新会话新 thread_id）；带 checkpointer 的 invoke 必带 thread_id，resume 用 Command(resume=)。
**SQLAlchemy/MySQL**：写操作统一 `with factory() as s, s.begin():`；TEXT 无 DEFAULT、VARCHAR 按最长枚举留余量（"assistant">VARCHAR(8)，MySQL 1406 才暴露）；downgrade 先 drop FK 再 drop 索引。
**工具管道**：strict FC 无可选参数（date 服务端默认今天）；两图同 thread 串挂起 → 派生 `{thread_id}:query`；权限顺序按 permission_id 字母序。
**种子/测试**：36 条通用种子破坏检索假设（测试显式清空）；真 LLM 意图有方差（sources 断言放宽）；hits 是 int 列表（API 需回查 DB）。
**前端/环境(Windows)**：npm 端口占用须验证；localStorage 非响应式（computed 加 void route.path）；dev 须清 NODE_OPTIONS（WorkBuddy shim 拦 fs.rm 致 vite 崩）；git 含 `/` 分支名有 bug → 顶层 feature-xxx。
**检索(Qdrant)**：中文 BM25 须 jieba 注入（fastembed 按空白切召回≈0）；fastembed0.8 用 as_dict()；本地 upsert 只覆盖 → rebuild 前 rm -rf；HF 不可达走 specific_model_path。
**知识CRUD/采集**：写路径接 sync_entry/delete_entry_vector（否则语义检索隐形）；MySQL 截断 question≤250/keywords≤120；difflib≥0.95 自动去重；校外 IP 403.6（应用级白名单）→ gold 随数据重标。
**多 agent worktree**：baseRef 取 origin/main 须先 git log；.venv 可能指向主仓 src → 加 PYTHONPATH=src。
**M8 数据**：bad_cases 双通道；负反馈率须 reply!='' 过滤+thread 去重；无采集点不叫"解决率"。

## 8. 环境与运行
| 项 | 拍板 |
|----|------|
| Python | 3.14（py launcher）；依赖装 .venv；清华源 |
| .env | DEEPSEEK_API_KEY/MODEL · LANGFUSE_* · DATABASE_URL · JWT_SECRET/EXP_MIN |
| 命令 | 测试 `.venv/Scripts/python -m pytest`；lint `-m ruff`；种子 `scripts/seed_db.py`；本地数据 `scripts/seed_zjut_local.py`；评测 `-m campus_desk.eval.runner/chain_runner --out docs/eval/`；API `-m uvicorn campus_desk.api.app:create_app --factory --port 8000 --workers 1`；前端 `cd frontend && NODE_OPTIONS= npm run dev`(5173)；演示账号 student-001/cs-001/admin-001(123456) |
| 测试库 | 单测/图测试 SQLite 内存（conftest StaticPool）；集成冒烟连本机 MySQL 8.0.45 |
| 验证 | `scripts/verify_env.py`（LangGraph/DeepSeek 结构化/真 FC，无 key SKIP）|
