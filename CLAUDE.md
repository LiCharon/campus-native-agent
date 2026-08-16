# CLAUDE.md — Campus Native Agent（校园智能服务助手）

> 项目唯一权威规范。开工必读，改动必更新。
> 完整设计契约 → docs/ZJUT_DESIGN.md（演进方向，权威）｜实现计划 → docs/ZJUT_M1_PLAN.md｜迭代日志 → docs/DEV_JOURNAL.md

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
| docs/ZJUT_DESIGN.md | **设计契约（权威）**：定位/4 类入口/type 分型/追问/进化闭环/待定项 |
| docs/ZJUT_M1_PLAN.md | M1 实现计划（Task 拆解/退役总表/已知局限登记）|
| docs/STATUS.md | 当前进度/下一步/基线（随里程碑更新；规范文件不含状态）|
| docs/DEV_JOURNAL.md | 新会话看最新迭代记录（做了什么/坑/面试点）|
| docs/PROJECT_REQUIREMENTS.md | CampusDesk 历史需求（M1-ZJUT 前基线，报修/状态机章节已退役，仅参考演进脉络）|
| docs/TECH_DECISIONS.md | 选型原因 + 面试话术（DeepSeek 结构化输出铁律/FC 探测结论）|
| docs/eval_report_zjut_m1.md | M1-ZJUT 意图评测基线报告（95.8%，本地私有管理）|
| （历史） | eval_report_m2~m6.md = CampusDesk 旧里程碑评测，本地私有仓库可恢复 |

## 1. 为什么做（面试叙事，防止跑偏）
- InterviewAI 教训：AI 占比有限，多 Agent 像多次 LLM 调用，无记忆/skill/FC/规划
- 本项目补齐：LangGraph 编排 + 工具调用（M2 起）+ 用户记忆 + Langfuse 可观测 + 量化评测
- **8:2 原则**：80% 预算在 Agent 构建（分流→检索→组装→追问→索引引导），20% 场景壳
- 目标岗位：Agent / LLM 应用开发；本地化场景做差异化特色（信息聚合 + 办事引导 + 索引）

## 2. 技术栈（已拍板）
Python 3.14（M1 实测核心依赖全兼容）· LangGraph（checkpointer: SQLite 官方 SqliteSaver）· LangChain · FastAPI + Pydantic v2 · MySQL 8 + SQLAlchemy 2.0 · Langfuse（开发期 Cloud 免费额度）· DeepSeek（deepseek-v4-flash）· Vue3 最小闭环 · pytest + ruff + httpx · MCP（扩展期演示加分）
**真 FC 已探测可用（M1-T12）**：deepseek-v4-flash bind_tools 返回真实 tool_calls（FC_SUPPORTED=True）；但 build_llm 写死 response_format=json_object，工具调用 prompt 必须含 "json" 字样否则 400——M2 工具管道按此设计（详见 env_check.py 注释 + DEV_JOURNAL）
**以后再说**：Redis 缓存（当前 36 条知识遍历 <0.1ms 不是瓶颈，规模上去再评估 cache-aside）；向量检索 RAG（检索层已做成可替换模块）；MCP 暴露；自托管 Langfuse

## 3. 核心设计（拍板结论；细节 → docs/ZJUT_DESIGN.md）
- **入口分流 4 类意图**：knowledge / tool_query（M2 实装）/ multi_intent / other；三层防线（结构化输出 → 重试 1 次 → 关键词规则兜底）+ 置信度门控（<0.7 转人工）；index/ambiguous 不设入口意图（"能否命中"只有检索层知道）
- **知识库 type 分型**：条目 type ∈ {info 直接答 / process 流程清单 / index 索引引导"去哪查"}，一个检索管道按 type 组装；关键词计分起步，向量检索演进预留（Agent 侧零改动）
- **追问澄清 ≤3 轮**：检索未命中 → ClarifyDecider（LLM ask/handoff）→ 补充后合并全部 history 重检索 → 仍未命中转人工；轮次上限图结构硬约束（rounds 计数 + 条件边），不靠 LLM 自觉
- **转人工兜底 + 进化闭环**：knowledge 管道 handoff 写 bad_cases（status=PENDING）；M3 进化闭环（管理员审查 → 补入知识库，"越用越聪明"）；数据分层：个人数据只给索引引导不直连
- **工具查询（M2）**：真 FC 可用（M1 探测）→ 字段抽取 + 确定性工具 + mock 表；每工具独立单测不依赖 LLM（沿用铁律）

## 4. 工程化标准（贯穿，不做就白做）
- Langfuse 全链路埋点（agent 步骤 / LLM call；orchestrator.turn + build_llm 挂载）
- 评测数据集（M1 意图 24 条剧本 zjut_intent.json 入 git）：断言 expected_route + 门控行为，不查对话字面；真 LLM 跑分（无 key 自动 SKIP 不进 CI）；InMemorySaver 隔离可无限重跑；M2 起加链路剧本（行为断言 route:/outcome:）与答案正确性口径（期望命中条目 id + 答案关键词）
- 量化指标：意图准确率（M1 基线 95.8%）/ 检索命中率 / 自助解决率 / 平均轮次
- 评测脚本独立于业务代码；需外部环境的标 skip，不进 CI（InterviewAI CI 教训）
- 安全：.env gitignore + 密钥不入库；alembic 迁移脚本，禁手改表；JWT claims 鉴权不查库
- 前端：Vue3 + Element Plus 最小闭环（Login + Chat），Campus Native Agent 品牌（不出现本地化地名/学校名）

## 5. AI Coding 顺序规范（项目特有；通用纪律 → AGENTS.md）
- 开工：读本文件 + AGENTS.md（通用纪律）+ 记忆索引 + docs/ZJUT_DESIGN.md（设计契约）+ 相关 docs；todo 工具先列清单
- 想清楚再动：方案先给用户确认才写码；3+ 文件或动 DB/权限 → Plan 模式；大改动多 agent 协同
- 小步循环：一次一个功能点，改完立即 pytest 验证
- 收尾：更新 DEV_JOURNAL.md（做了什么/为什么/坑/量化/面试点）；里程碑跑完先 /neat 再存档、提议新会话
- 防忘清单（项目特有）：工具有独立单测 ・ DB 变更走迁移 ・ 追问轮次有测试锁定 ・ Langfuse 有 trace ・ 分支 feature/* from main

## 6. 里程碑（MVP 三分法，见 ZJUT_DESIGN §10）
**M1（已完成，2026-08-15）**：入口分流 4 类 + 知识库检索组装（type 分型）+ 追问澄清 + 转人工兜底 + 36 条种子知识 + 前端收敛 Login/Chat + 环境验证（含真 FC 探测）+ 意图评测 24 条基线 95.8%
**M2（已完成，2026-08-16）**：工具查询管道（真 FC + 空教室/图书馆座位 2 确定性工具 + 2 mock 表 + 四层失败链）+ multi_intent 实装（primary_intent 主意图路由）+ 链路评测 14 条（答案正确性口径）+ 意图 100% / 链路 92.9%
**M3（应该有）**：进化闭环（bad_cases/建议 + 管理页审查）+ 真实数据收集 + 画像可选
**以后再说**：真·多意图拆解 / 向量检索 RAG / MCP 暴露 / 渠道扩展
进度/下一步/基线 → docs/STATUS.md
**DoD（完成标准，模式：核心链路测试绿 + 环境验证 + 文档/DEV_JOURNAL 更新）**：M1 已按此收尾（96 passed + verify_env 3 项 PASS + 验收 5 路径 + 文档同步）

## 7. 当前状态
进度/下一步/基线数据 → docs/STATUS.md（随里程碑更新；本规范文件不含状态）

## 8. 别再犯清单（历史教训精简版，细节在 DEV_JOURNAL）
- 外部评审建议默认按**文档/契约**吸收（零成本面试弹药）；**代码/模块级**单独过"演示项目是否值得"关
- 同一评审倾向会反复出现，裁决保持一致；AI 评审工具的"审查对象"声明不可信，对照磁盘逐条核验
- 写框架代码前先查 API（LangGraph/LangChain API 变动快），禁凭记忆写
- **DeepSeek 不支持 langchain with_structured_output 三种 method**（实测 2026-08-04 全 400）——结构化输出统一**自写 prompt（含 "json" 字样）+ response_format=json_object + pydantic 校验**（intent.py 已沉淀模板）
- **LangGraph interrupt 重入不落盘**：interrupt 节点内"中断前的修改"不持久化——问句/计数必须由 return 写入 state（KnowledgeGraph 双节点 ping-pong：collect 纯逻辑 + wait 唯一 interrupt）
- **终态 thread 再 invoke = 旧 state 残留 + 重复中断**——新会话必须新 thread_id；评测 runner 必须 InMemorySaver 隔离
- LangGraph 普通边多出边 = 并行分支（同 step 更新同 key 报错）→ 分支必须用条件边
- SQLAlchemy 2.0：SELECT 隐式开事务，与 begin() 混用报 "already begun"；rollback() expire 所有实例——测试 helper 返回整数 id 不返回 ORM 实例
- alembic autogenerate 前验证表数（env.py 漏 import 业务 models 只生成部分表）；alembic.ini 只写英文注释；密码含 @ 需 %40 URL 编码
- 规则抽取与真 LLM 行为差异：turns 评测口径按真 LLM 设计，规则版只做机制验证
- **36 条种子会破坏测试检索假设（M1-ZJUT 新坑）**：种子含"图书馆几点开门/食堂几点开门"等常见问句，测试若断言"某问题无命中"会误命中——检索类测试必须显式清空知识库或断言具体命中条目（T4 quality review 登记）
- **LangGraph checkpointer 版 invoke 必须带 thread_id（M1-ZJUT 新坑）**：带 checkpointer 的图每次 invoke 都要 `config={"configurable": {"thread_id": ...}}`，挂起中 resume 用 `Command(resume=...)` 同款 cfg——orchestrator 是唯一正确范式，别绕过
- **工作台/前端残留进程（Windows）**：npm run dev 后台任务停了端口仍被占——改前端后验证端口归属 + fetch /src/App.vue 确认 serve 的是新代码
- **前端 localStorage 非响应式**：computed 无响应式依赖只求值一次（加 `void route.path` 依赖）；模块级单例只在页面首次加载执行，换账号必须显式 reload
- **两图 checkpoint 同 thread_id 串状态（M2 新坑）**：knowledge/query 两图共享 checkpointer 时同 thread_id 挂起状态互污染；langgraph 1.2.10 的 compile()/config 均无 checkpoint_ns → query 图内部派生 thread_id `{thread_id}:query`
- **strict FC 工具无可选参数（M2 新坑）**：strict 要求所有 property 必填，可选参数（如 date）不能进 schema → date 由服务端默认今天转周几
- **json_object 抑制 tool_calls（M2 新坑）**：build_llm 写死 json_object，FC 场景必须用 build_tool_llm（无 response_format），否则 prompt 要带 "json" 且模型可能直接答不调工具
- **ruff 0.16.1 默认启用 DTZ011（M2 新坑）**：`date.today()` 被 flag → 用 `datetime.now(UTC).date()`

## 9. 环境与运行（M1 已拍板，2026-08-04）
| 项 | 拍板结果 |
|----|---------|
| Python | **3.14.6**（本机 py launcher，M1 实测核心库 requires-python 全兼容） |
| venv 与依赖管理 | `py -3.14 -m venv .venv`；pyproject.toml 声明直接依赖（含 `[dev]` 组），requirements.txt = pip freeze 锁定快照 |
| 镜像 | ⚠️ 官方 PyPI 在国内卡死，统一加 `--index-url https://pypi.tuna.tsinghua.edu.cn/simple` |
| .env 变量 | `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL`（=deepseek-v4-flash）/ `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` / `DATABASE_URL` / `JWT_SECRET` / `JWT_EXPIRE_MINUTES`（config.py 用 pydantic-settings 加载） |
| 命令 | 测试 `.venv/Scripts/python -m pytest`；lint `.venv/Scripts/python -m ruff check/format`；环境验证 `PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/verify_env.py`（3 项 + FC_SUPPORTED 打印）；种子入库 `.venv/Scripts/python scripts/seed_db.py`；本地化校园真实信息注入 `.venv/Scripts/python scripts/seed_zjut_local.py`（config/zjut_local_data.json 私有文件）；评测集入库 `.venv/Scripts/python scripts/ingest_eval_data.py`；意图评测 `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m campus_desk.eval.runner --out docs/eval_report_zjut_m1.md`；链路评测 `PYTHONIOENCODING=utf-8 .venv/Scripts/python -m campus_desk.eval.chain_runner --out docs/eval_report_zjut_m2.md`；M2 验收 `PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/accept_m2.py`（5 路径）；**API 服务 `.venv/Scripts/python -m uvicorn campus_desk.api.app:create_app --factory --host 0.0.0.0 --port 8000 --workers 1`（--workers 1 硬约束：多 worker = 多图单例冲突）**；前端 dev `cd frontend && npm run dev`（5173）/ 构建 `npm run build`；演示账号 student-001 / cs-001 / admin-001（密码统一 123456）；⚠️ 依赖只装在 .venv（`py -3.14` 全局无 pytest/ruff）；Windows 控制台 GBK 需 `PYTHONIOENCODING=utf-8` |
| 测试数据库 | 业务单测/图测试 **SQLite 内存库**（conftest fixture：StaticPool 单连接，测试串行）；集成冒烟连 **本机 MySQL 8.0.45**（MySQL80 服务，root 密码在 .env DATABASE_URL，%40 编码） |
| 环境验证 | `scripts/verify_env.py` 3 项（LangGraph quickstart / DeepSeek 结构化输出 / 真 FC 探测），逻辑在包内 `campus_desk/env_check.py`，pytest 同源复用；无 key 项自动 SKIP（需外部环境项不进 CI）；FC 探测实测 **FC_SUPPORTED=True**（2026-08-15） |
