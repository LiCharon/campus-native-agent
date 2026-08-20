# CLAUDE.md — Campus Native Agent（校园智能服务助手）

> 项目唯一权威规范。开工必读，改动必更新。
> 完整设计契约 → docs/design/ZJUT_DESIGN.md（演进方向，权威）｜实现计划 → docs/plans/ZJUT_M1_PLAN.md｜迭代日志 → docs/journal/DEV_JOURNAL.md

## 0. 定位
**Campus Native Agent（ZJUT Native Agent 演进）——校园信息聚合 + 问答 + 引导 + 索引**：学生一句话问任何校园问题 → 意图分流 → 能答的直接答（知识库检索），缺信息的追问澄清（≤3 轮），仍答不上转人工（bad_cases 沉淀）；不代办任何事务（报修走后勤系统、资金操作不在范围）。

### 0.1 项目事实（仅索引，不重复维护；权威源：pyproject.toml 管包名、README.md 管标题）
| 项 | 值 |
|----|----|
| 项目名 | Campus Native Agent（公开展示名，用户拍板，非 ZJUT）|
| 仓库名 | campus-desk（GitHub 公开仓库已改名 LiCharon/campus-native-agent）|
| 包名 | `campus_desk`（演进迁移成本低，保留不改）|
| README 标题 | `Campus Native Agent — 校园智能服务助手`（M1-T12 重写）|

### 0.2 docs 地图（开工按需读，不必全读）
| 文档 | 什么时候读 |
|------|-----------|
| docs/design/ZJUT_DESIGN.md | **设计契约（权威）**：定位/4 类入口/type 分型/11 域/追问/进化闭环/待定项 |
| docs/plans/ZJUT_M1_PLAN.md / ZJUT_M2_PLAN.md | M1/M2 实现计划（Task 拆解/退役总表/已知局限登记）|
| docs/journal/STATUS.md | 当前进度/下一步/基线（**随里程碑和收尾更新**）|
| docs/journal/DEV_JOURNAL.md | 新会话看最新迭代记录（做了什么/坑/面试点）|
| docs/requirements/PROJECT_REQUIREMENTS.md | CampusDesk 历史需求（已退役，仅参考演进脉络）|
| docs/requirements/TECH_DECISIONS.md | 选型原因 + 面试话术（活弹药）|
| docs/plans/RAG_ROADMAP.md | RAG 实现蓝图（A 路线 + sqlite-vec，2026-08-18 拍板）|

### 0.3 里程碑命名规范（2026-08-20 拍板，防新旧混淆）
- 新系列统一 `M{n}-ZJUT` 后缀（重构后 2026-08-15 起：M1-ZJUT ~ M10-ZJUT）；旧系列 M0–M7 无后缀、为重构前历史基线。
- 所有文档（STATUS/CLAUDE/DEV_JOURNAL 新记录/计划）引用新系列必须带 `-ZJUT`；DEV_JOURNAL 历史记录带日期天然消歧，不改。
- 防冲突机制 = 文档即记忆（本文件在主仓库内、拷贝即见）；本地记忆与 docs/ 私有仓均不承载命名权威。

## 1. 为什么做（面试叙事，防止跑偏）
- InterviewAI 教训：AI 占比有限，多 Agent 像多次 LLM 调用，无记忆/skill/FC/规划
- 本项目补齐：LangGraph 编排 + 工具调用（M2 起）+ 用户记忆 + Langfuse 可观测 + 量化评测
- **8:2 原则**：80% 预算在 Agent 构建（分流→检索→组装→追问→索引引导），20% 场景壳
- 目标岗位：Agent / LLM 应用开发；本地化场景做差异化特色（信息聚合 + 办事引导 + 索引）

## 2. 技术栈（已拍板）
Python 3.14 · LangGraph（checkpointer: SQLite 官方 SqliteSaver）· LangChain · FastAPI + Pydantic v2 · MySQL 8 + SQLAlchemy 2.0 · Langfuse（自托管 localhost:3001）· DeepSeek（deepseek-v4-flash）· Vue3 最小闭环 · pytest + ruff + httpx · MCP（扩展期演示加分）
**真 FC 已探测可用（M1-T12）**：deepseek-v4-flash bind_tools 返回真实 tool_calls（FC_SUPPORTED=True）；但 build_llm 写死 response_format=json_object，工具调用 prompt 必须含 "json" 字样否则 400——M2 工具管道按此设计（详见 env_check.py 注释 + docs/journal/DEV_JOURNAL.md）
**以后再说**：Redis 缓存（当前 36 条知识遍历 <0.1ms 不是瓶颈，规模上去再评估 cache-aside）；向量检索 RAG（检索层已做成可替换模块，路线见 RAG_ROADMAP.md）；MCP 暴露

## 3. 核心设计（拍板结论；细节 → docs/design/ZJUT_DESIGN.md）
- **入口分流 4 类意图**：knowledge / tool_query（M2 实装）/ multi_intent / other；三层防线（结构化输出 → 重试 1 次 → 关键词规则兜底）+ 置信度门控（<0.7 转人工）；index/ambiguous 不设入口意图
- **知识库 11 域 + type 分型**：领域 = 教务/图书馆/网络与IT/校园卡与证件/住宿后勤/奖助/医疗健康/社团与活动/就业与职业发展/安全与保卫/生活服务（DB 自由字符串，API/前端枚举同源）；条目 type ∈ {info 直接答 / process 流程清单 / index 索引引导"去哪查"}；关键词计分起步，向量检索演进预留（Agent 侧零改动）
- **追问澄清 ≤3 轮**：检索未命中 → ClarifyDecider（LLM ask/handoff）→ 补充后合并全部 history 重检索 → 仍未命中转人工；轮次上限图结构硬约束
- **转人工兜底 + 进化闭环**：knowledge 管道 handoff 写 bad_cases；对话页"没解决"按钮 + "问题没答案"提建议（suggestions）→ 管理页审查（keywords 预填建议可编辑）→ 补入知识库（人工把关，"越用越聪明"）
- **工具查询（M2 起）**：真 FC → 字段抽取 + 确定性工具 + mock 表；**M2+ 已扩至 13 工具 + 10 mock 表**；参数白名单 schema 动态派生 + 按领域路由追问；每工具独立单测不依赖 LLM
- **权限体系（M4 + M6-ZJUT RBAC 三表化）**：roles/permissions/role_permissions 三表（M6 起）；**运行时以 DB 为准**——login 查库算"角色默认权限 ∪ users.permissions 附加位"写 JWT claims，鉴权（require_perm/require_roles）不查库；perms.py 的 ROLE_PERMS/GRANTABLE_PERMS 降级为种子源+兜底（与 seed.py 三表种子、前端 constants/perms.js 同源）；用户管理页角色/权限下拉查 /api/admin/roles、/api/admin/permissions（require_perm user_mgmt）；改权限需重登；admin 不可禁用；student 不可带附加位；审计日志旁路不阻断

## 4. 工程化标准（贯穿，不做就白做）
- Langfuse 全链路埋点（orchestrator.turn + build_llm 挂载）+ score_trace 按 outcome 自动评分（answer 1.0/ask 0.6/degraded 0.3/handoff 0.0）
- 评测数据集入 git（意图 47 条 zjut_intent.json + 链路 44 条 zjut_chain.json）：断言 expected_route/outcome/tool_calls，不查对话字面；真 LLM 跑分（无 key 自动 SKIP 不进 CI）；InMemorySaver 隔离可无限重跑
- 量化指标：意图准确率（M2+ 100% 47/47）/ 检索命中率 / 自助解决率 / 平均轮次
- 安全：.env gitignore + 密钥不入库；alembic 迁移脚本，禁手改表；JWT claims 鉴权不查库
- 前端：Vue3 + Element Plus 最小闭环（Login + Chat），Campus Native Agent 品牌（不出现本地化地名/学校名）

## 5. AI Coding 顺序规范（项目特有；通用纪律 → AGENTS.md）
- 开工：读本文件 + AGENTS.md（通用纪律）+ 记忆索引 + docs/design/ZJUT_DESIGN.md（设计契约）+ 相关 docs；todo 工具先列清单
- 想清楚再动：方案先给用户确认才写码；3+ 文件或动 DB/权限 → Plan 模式；大改动多 agent 协同
- 小步循环：一次一个功能点，改完立即 pytest 验证
- **收尾必做（三件套，缺一不可）**：① DEV_JOURNAL.md 追加（做了什么/为什么/坑/量化/面试点）② STATUS.md 同步（进度/下一步/基线）③ 本次改动涉及的权威文档回填（ZJUT_DESIGN/_INDEX 等，以代码为准）。**非里程碑的实体改动（改数据/接口/架构）同样触发收尾**，不只在里程碑跑完
- 防忘清单（项目特有）：工具有独立单测 ・ DB 变更走迁移 ・ 追问轮次有测试锁定 ・ Langfuse 有 trace ・ 分支 feature/* from main ・ 数据/接口改完先问"STATUS 和 DESIGN 还准吗？"

## 6. 里程碑（细节/基线 → docs/journal/STATUS.md；新系列统一 M{n}-ZJUT 后缀，见 §0.3）
**M1-ZJUT 最小闭环（✅ 2026-08-15）**：入口分流 4 类 + 检索组装 + 追问 + 转人工 + 36 条种子 + 意图评测 24 条 95.8%
**M2-ZJUT 工具管道（✅ 2026-08-16）**：真 FC + 2 确定性工具 + 2 mock 表 + 四层失败链 + multi_intent + 链路评测 14 条 92.9%
**M3-ZJUT 进化闭环（✅ 2026-08-16）**：双通道反馈 + 管理页审查 + 补入知识库 + accept_m3 7 路径
**M4-ZJUT 前端重构 + 权限（✅ 2026-08-17）**：Kimi 工大蓝设计 + 角色权限体系 + 客服工作台 + 对话页重写 + 审计日志
**M2+-ZJUT 工具扩展（✅ 2026-08-19）**：2→13 工具 + 10 mock 表 + 时间上下文注入 + 评测意图 47/链路 44 + score_trace
**M4.5-ZJUT 知识库重构（✅ 2026-08-19）**：11 域零重叠 + 本地注入 262 条 + 近重复自动检测（构建期硬关卡）
**M5-ZJUT 会话服务端化（✅ 2026-08-20）**：conversations/messages 两表 + /api/sessions 增删改查 + /api/chat 归属校验与落库 + 自动标题后端化 + handoff 落库 + useChat.js 从 localStorage 迁 API
**M6-ZJUT 权限模型升级（✅ 2026-08-20）**：RBAC 三表（roles/permissions/role_permissions）+ perms.py 查库化（login 查库算并集写 JWT）+ 只读接口 /api/admin/roles、/api/admin/permissions + UserManage.vue 下拉查库
**以后再说**：真·多意图拆解 / 向量检索 RAG / MCP 暴露 / 渠道扩展 / 用户画像 / SSE 流式
**DoD（完成标准，模式：核心链路测试绿 + 环境验证 + 收尾三件套同步）**：M1-ZJUT 96 passed；M3-ZJUT 166 passed + accept_m3 7/7；M4-ZJUT 180 passed + 运行态 8/8；M5-ZJUT 261 passed + 真实链路冒烟 11/11；M6-ZJUT 273 passed + MySQL 冒烟（三表种子/login JWT 查库/只读接口 401-403-200）；当前 pytest 273 passed

## 7. 当前状态
进度/下一步/基线数据 → docs/journal/STATUS.md（随里程碑和收尾更新；本规范文件不含状态）

## 8. 别再犯清单（历史教训精简版，细节在 docs/journal/DEV_JOURNAL.md）
**外部评审**：默认按**文档/契约**吸收（零成本面试弹药）；**代码/模块级**单独过"演示项目是否值得"关；评审"审查对象"声明不可信，对照磁盘逐条核验；同一倾向反复出现时裁决一致
**DeepSeek 结构化**：不支持 langchain with_structured_output 三种 method（实测全 400）——统一**自写 prompt（含 "json" 字样）+ response_format=json_object + pydantic 校验**（intent.py 已沉淀模板）
**LangGraph**：interrupt 重入不落盘（问句/计数必须 return 写入 state，双节点 ping-pong）；终态 thread 再 invoke = 旧 state 残留（新会话新 thread_id，评测 InMemorySaver）；普通边多出边 = 并行分支（用条件边）；带 checkpointer 的 invoke 必须带 thread_id，挂起 resume 用 Command(resume=)
**SQLAlchemy/MySQL**：SELECT 隐式开事务与 begin() 混用报 already begun；rollback 后实例 expire（helper 返回整数 id）；写操作必须 `with factory() as s, s.begin():`；MySQL TEXT 列无 DEFAULT（nullable 无 server_default）；非事务 DDL 半应用先 DROP 再修迁移；alembic autogenerate 前验证表数（env.py 漏 import）；alembic.ini 只英文注释；密码含 @ 需 %40 URL 编码；**VARCHAR 长度按最长枚举值留余量（"assistant" 9 字符超 VARCHAR(8)，SQLite 测试库不校验长度、MySQL 严格模式 1406 才暴露——新表/新列务必冒烟连真实库）；downgrade 顺序：先 drop FK 约束再 drop 索引，否则 MySQL 1553**
**M2 工具管道**：json_object 抑制 tool_calls（FC 场景必须 build_tool_llm 无 response_format）；strict FC 无可选参数（date 不进 schema，服务端默认今天）；knowledge/query 两图同 thread_id 串挂起（query 图派生 `{thread_id}:query`）；ruff DTZ011 用 `datetime.now(UTC).date()`；require_perm 加 pyproject B008 豁免
**种子/测试**：36 条通用种子会破坏检索测试假设（测试显式清空或断言具体命中条目）；规则抽取与真 LLM 行为差异（turns 按真 LLM 设计，规则版只做机制验证）；orchestrator hits 是 int 列表（API 展示需回查 DB）；真 LLM 意图方差影响运行态验收（sources 断言放宽，由 pytest Fake 图稳定覆盖）；**权限顺序断言（M6）**：查库版按 permission_id 字母序（chat<cs_workbench<kb_review<user_mgmt<view_logs<view_stats），与硬编码 ROLE_PERMS 顺序不同——test_admin_m4.py 的 _ALL_PERMS 已按字母序更新，别改回硬编码序
**前端/进程（Windows）**：npm run dev 后台停端口仍被占（改前端后验证端口 + fetch App.vue 确认新代码）；localStorage 非响应式（computed 加 `void route.path` 依赖；换账号必须 reload）
**多 agent worktree**：worktree 创建时 baseRef 默认取 origin/main（落后本地）→ 子任务开工先 `git log` 核基线；worktree 的 .venv 可能指向主仓 src → 跑脚本加 PYTHONPATH=src

## 9. 环境与运行（M1 已拍板，2026-08-04）
| 项 | 拍板结果 |
|----|---------|
| Python | **3.14**（本机 py launcher，核心依赖全兼容）|
| venv 与依赖 | `py -3.14 -m venv .venv`；pyproject.toml 直接依赖（含 [dev]），requirements.txt = pip freeze 快照；镜像清华源 |
| .env 变量 | `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` / `DATABASE_URL` / `JWT_SECRET` / `JWT_EXPIRE_MINUTES` |
| 命令 | 测试 `.venv/Scripts/python -m pytest`；lint `-m ruff check/format`；环境验证 `scripts/verify_env.py`；种子 `scripts/seed_db.py`；本地真实数据 `scripts/seed_zjut_local.py`（config/zjut_local_data.json 私有）；评测 `-m campus_desk.eval.runner / chain_runner --out docs/eval/...`；验收 `scripts/accept_m2.py / accept_m3.py`；API `-m uvicorn campus_desk.api.app:create_app --factory --port 8000 --workers 1`（**--workers 1 硬约束**）；前端 `cd frontend && npm run dev`（5173）；演示账号 student-001/cs-001/admin-001（密码 123456）；依赖只装 .venv；Windows 控制台 GBK 需 `PYTHONIOENCODING=utf-8` |
| 测试数据库 | 业务单测/图测试 **SQLite 内存库**（conftest StaticPool）；集成冒烟连本机 MySQL 8.0.45（root 密码 .env DATABASE_URL，%40 编码）|
| 环境验证 | `scripts/verify_env.py` 3 项（LangGraph / DeepSeek 结构化 / 真 FC），无 key 自动 SKIP 不进 CI；FC_SUPPORTED=True（2026-08-15 实测）|
