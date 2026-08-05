# CLAUDE.md — 校园智能服务台（CampusDesk）

> 项目唯一权威规范。开工必读，改动必更新。
> 完整需求规格 → docs/PROJECT_REQUIREMENTS.md｜选型原因+面试话术 → docs/TECH_DECISIONS.md｜迭代日志 → docs/DEV_JOURNAL.md

## 0. 定位
校园报修/咨询/投诉的智能服务 Agent 平台：学生一句话提交问题 → 自动意图识别、分类定级、派单、跟踪、回访，服务闭环。

### 0.1 项目事实（仅索引，不重复维护；权威源：pyproject.toml 管包名、README.md 管标题）
| 项 | 值 |
|----|----|
| 项目名 | CampusDesk（校园服务台） |
| 仓库名 | campus-desk |
| 包名 | `campus_desk`（M1 建 pyproject.toml 时确定） |
| README 标题 | `CampusDesk（校园服务台）— 校园智能服务台 Agent`（M7 写 README 时用） |

### 0.2 docs 地图（开工按需读，不必全读）
| 文档 | 什么时候读 |
|------|-----------|
| docs/PROJECT_REQUIREMENTS.md | 写需求细节/状态机/工具/评测前 |
| docs/TECH_DECISIONS.md | 面试备答、选型变更时 |
| docs/STATUS.md | 当前进度/下一步/基线（随里程碑更新；规范文件不含状态） |
| docs/DEV_JOURNAL.md | 新会话看最新迭代记录（做了什么/坑/面试点） |
| docs/eval_report_m2.md | M5 评测校准、面试展示基线数据（意图准确率 94.4%，本地私有管理） |
| docs/eval_report_m3.md | M3 评测：意图 95.8% + 报修链路成功率 94.4%（17/18），本地私有管理 |
| （已归档） | PLANNING_REVIEW 评析报告与 Qwen 需求分析原文已于 2026-08-05 删，历史在 docs 私有仓库 git 可恢复 |

## 1. 为什么做（面试叙事，防止跑偏）
- InterviewAI 教训：AI 占比有限，多 Agent 像多次 LLM 调用，无记忆/skill/FC/规划
- 本项目补齐：LangGraph 编排 + 工具调用 + 用户记忆 + Langfuse 可观测 + 量化评测
- **8:2 原则**：80% 预算在 Agent 构建，20% 场景壳；工程化贯穿不占预算、不可省略
- 目标岗位：Agent / LLM 应用开发（求职备考并行，约 5-6 周）

## 2. 技术栈（已拍板）
Python 3.14（M1 实测核心依赖全兼容，推翻 3.11 保守假设，见 DEV_JOURNAL M1）· LangGraph（checkpointer: SQLite 官方 SqliteSaver）· LangChain · FastAPI + Pydantic v2 · MySQL 8 + SQLAlchemy 2.0 · Langfuse（开发期 Cloud 免费额度，M6 再试自托管）· DeepSeek（deepseek-v4-flash）· Vue3 最小闭环 · pytest + ruff + sse-starlette + httpx · MCP（扩展期演示加分）
**MVP 不引入**：Redis（M6+ 加：热点缓存 FAQ/公告/排班——会话历史由 checkpointer 管，Redis 不背会话）、Celery（不引入，APScheduler 够用）
⚠️ LangGraph/LangChain API 变动快：**写框架代码前先 context7 查文档**，禁凭记忆写

## 3. 核心设计（细节 → docs/PROJECT_REQUIREMENTS.md）
- 入口分流：EntryAgent 意图识别（报修/咨询/投诉/其他）→ 置信度门控 → 低置信兜底转人工；多意图取主意图处理、次要问题提示继续问；**投诉 = 创建 P1 工单 + 通知管理员**（复用 Repair 管道；通知载体 = 管理列表标红/界面可见，不建通知模块，见 §14）
- 工单状态机 6 态：SUBMITTED→ASSIGNED→IN_PROGRESS→PENDING_VERIFY→CLOSED + CANCELLED（仅 SUBMITTED/ASSIGNED 可撤）；**超时升级=字段不是状态**（escalation_count+escalated_at+审计日志，P1 4h/P2 48h/P3 不适用）；**跳转=白名单**（完整边清单 8 条，测试照单锁定；M3 落地：纯函数白名单 machine.py 为权威 + apply_transition SAVEPOINT 原子写库（状态+审计日志，唯一写入口）+ TicketStateGraph 图渲染条件边——"跳转=图的边"叙事保留但 RepairGraph 不嵌套子图，状态变更全走 apply_transition）：验收不通过 PENDING_VERIFY→IN_PROGRESS 返工；挂起 3 天无响应自动 CLOSED（备注"超时自动关闭"，APScheduler 定时 M4 挂，同为事件非状态）
- 4 Agent：Entry（分流）/ Repair（报修主流程）/ Consult（**诊断式咨询**：追问≤8 轮、每轮≤2 问 → 工具排查 → 三态分支，转人工打包排查记录）/ Quality（关闭 24h 后回访）
- 9 确定性工具（每工具独立单测，不依赖 LLM）：报修侧 6（create_ticket / get_ticket / update_ticket_status / list_repairmen / query_dorm_info / urgent_followup）+ 咨询侧 3（query_account_status / query_announcement / search_faq 关键词匹配）
- 5 类上下文隔离：当前任务（LangGraph state）/ 会话（checkpointer SQLite）/ 用户长期画像（MySQL）/ 工具只读数据 / 全局 FAQ
- 边界声明：业务办理=工单类（报修+投诉+咨询）；咨询=IT 诊断工具 + FAQ 问答；外部接入/通知/多端 = **演示不实装**（requirements 12-14 节，仅面试备答）
- **待定项：5 个待拍板**（报修采集形态/意图识别实现/派单规则/FAQ 库/评测集细节）+ **2 项后置**（few-shot/MCP）→ 按里程碑时间表拍死（requirements 第 11 节），不边写边吵

## 4. 工程化标准（贯穿，不做就白做）
- Langfuse 全链路埋点（agent 步骤/工具调用/状态跳转/LLM call）
- 评测数据集（M2 落地 72 条 + M3 扩展 turns）：**对话剧本格式（scripted，预写学生每轮回复）**，报修/咨询/投诉/闲聊各 16 条 + 多意图 6 + 重复报修 2，**JSON 文件入 git + M3 已做入库脚本同步 MySQL**（scripts/ingest_eval_data.py）；报修 18 条剧本 turns 按真 LLM 口径设计（断言=tool:/status: 行为）
- 量化指标（9 项，细节见 requirements §10）：意图分类准确率/分类定级准确率/自助解决率/人工介入率/平均对话轮次/工单闭环率/工单响应时间/超时率/满意度——**目标值均为示例基线，M5 评测后按实测校准，不拍死**
- 评测脚本独立于业务代码；需外部环境的标 skip，不进 CI（InterviewAI CI 教训）
- CI（GitHub Actions）：**起步 ruff + pytest**；覆盖率门槛/gitleaks/pip-audit M6 后加（单人排期有限，先保核心质量门）
- 安全：.env gitignore + 密钥不入库；alembic 迁移脚本，禁手改表
- Docker Compose 一键起：MySQL + 后端 + 前端（Langfuse 开发期用 Cloud，自托管 M6 再试）

## 5. AI Coding 顺序规范
### 阶段 0 开工（每个新会话）
1. 读本文件 + **记忆索引（MEMORY.md）指向的记忆文件**（教训 2026-08-05：只读 CLAUDE.md 漏读记忆 → 基于过期状态操作）+ 相关 docs + mem-search
2. TodoWrite 列任务清单（先列再动）
### 阶段 1 想清楚再动
3. 方案先给（改哪些文件/目标/验收标准），**用户确认后才写码**
4. 3+ 文件或动 DB/权限 → Plan 模式；大改动按全局规范多 agent 协同
### 阶段 2 小步循环（每个功能点）
5. 一次一个功能点，不跨任务；写码前 context7 核实框架 API
6. **先写失败的测试**，测试定义"完成"的标准
7. 改完立即验证（pytest/冒烟），不靠"应该没问题"
8. 一个 commit 一件事；受保护文件（migrations/.env/锁文件）改动先报备
9. 报错给 AI 完整资料包（日志+源码+步骤+预期/实际），AI 答四问：出错位置/原因/修改点/验证方案
10. commit 前自查 Diff：文件清单/原因/冗余改动/逻辑误删/硬编码/安全隐患
### 阶段 3 收尾（任务完成/会话结束）
11. 全量测试 + 冒烟 + CI 绿；验收过异常/空数据/保存能力（不只走正常流程）
12. **更新 DEV_JOURNAL.md**（做了什么/为什么/坑/量化数据/面试可讲点）
13. **里程碑（Mx）跑完先跑 /neat 洁癖收尾**（全局规范 2026-08-05 确认）：核对代码/文档/规则/记忆/工作区一致，删除候选与推送项列清单交用户确认，未确认不动
14. 存档（记忆+本文件当前状态），提议新会话
### 防忘清单（每个 commit 前 + 会话收尾时各过一遍）
- [ ] .env gitignore，密钥不入库　[ ] 依赖锁版本　[ ] 改动有测试或冒烟验证
- [ ] 工具有独立单测　[ ] DB 变更走迁移　[ ] 状态机跳转有测试锁定
- [ ] Langfuse 有 trace（不靠猜排障）　[ ] 提交信息规范
- 分支策略：feature/* from main（多 agent worktree 用）
### 额外纪律（防 AI 翻车）
- AI 写坏代码 → 修环境（加测试/lint 规则），不修 prompt
- 上下文当预算：超 40% 在干净边界重置会话（/compact）
- 审查用对抗性视角："找出你能找到的每个问题，不要鼓励"
- 自主循环迭代上限 5 次，改进 <5% 提前停
- 禁危险命令：git push --force / git reset --hard
- AI 犯过的错 → 沉淀进本文件"别再犯"
- **外部评审吸收先过三问**（教训 2026-08-04：无条件吸收 hy3 通知/provider 抽象层 → 过度建设）：① 服务"演示能跑+面试能讲"？② 加的是文档契约还是代码模块？③ 与 8:2/演进式冲突吗？——本作是演示项目，生产级设计（真实接入/通知/适配器）一律不实装

## 6. 里程碑（生存线/加分线）
**生存线（做不完加分线，项目不成立）**：M1 骨架 → M2 入口分流+评测集 → M3 报修主链路+状态机+工具 → M4 记忆+咨询+回访 → M5 评测闭环+Langfuse
**加分线（演示层，可精简不影响项目成立）**：M6 前端+Compose+CI → M7 打磨+README+自托管尝试（弹性缓冲）
Redis 缓存（M6+ 加分项）；进度/下一步/基线 → docs/STATUS.md
**DoD（完成标准，模式：核心链路测试绿 + 环境验证 + 文档/DEV_JOURNAL 更新）**：
- M1：git init + 初始 commit 完成 / 环境验证 3 项跑通 / 骨架 pytest 绿 / 文档日志更新
- M2-M5：该里程碑核心链路测试绿 + 环境验证跑通 + 文档/日志更新

## 7. 当前状态
进度/下一步/基线数据 → docs/STATUS.md（随里程碑更新；本规范文件不含状态）

## 8. 别再犯清单（历史教训精简版，细节在 DEV_JOURNAL）
- 外部评审建议默认按**文档/契约**吸收（零成本面试弹药）；**代码/模块级**单独过"演示项目是否值得"关（三问：服务"演示能跑+面试能讲"？文档契约还是代码模块？与 8:2/演进式冲突吗？）
- 同一评审倾向会反复出现（如通知模块），裁决保持一致，不为"通知"加实体
- AI 评审工具的"审查对象"声明不可信（可能用旧快照），无论第几轮审查都对照当前磁盘文档逐条核验
- 写框架代码前先 context7 查 API（LangGraph/LangChain API 变动快），禁凭记忆写
- **DeepSeek（v4-flash thinking 模式）不支持 langchain with_structured_output 三种 method**（实测 2026-08-04：json_schema/json_mode/function_calling 全 400）——所有 LLM 结构化输出统一用**自写 prompt（含 "json" 字样）+ response_format=json_object + pydantic 校验**（intent.py 已沉淀模板，M3/M4 复用）
- **LangGraph interrupt 重入不落盘**：interrupt 节点内"中断前的修改"不持久化，恢复时节点从头重入——问句/计数必须由 return 写入 state（RepairGraph 双节点 ping-pong：collect 纯逻辑 + wait 唯一 interrupt）
- **终态 thread 再 invoke = 旧 state 残留 + 重复中断**（实测）——新会话必须新 thread_id；评测 runner 必须 InMemorySaver 隔离（文件 SqliteSaver 残留终态 → 重跑全失配）
- LangGraph 普通边多出边 = 并行分支（同 step 更新同 key 报错）→ 分支必须用条件边
- SQLAlchemy 2.0：SELECT 隐式开事务，与 begin()/begin_nested() 混用报 "already begun"；**session.rollback() expire 所有实例**，失败路径后访问实例属性会触发惰性加载 + autobegin——测试 helper 一律返回整数 id 不返回 ORM 实例
- alembic autogenerate 前验证表数（env.py 漏 import 业务 models → 只生成 2 张表的迁移）；alembic.ini 只写英文注释（configparser GBK 读取）；密码含 @ 需 %40 URL 编码
- 规则抽取与真 LLM 行为差异（规则恒抽不到 contact）：turns 评测口径按真 LLM 设计，规则版只做机制验证
- 编排层：报修挂起中 other 类输入（补充信息）要 resume 进 RepairGraph，不落到人工占位（真 LLM 评测抓出）

## 9. 环境与运行（M1 已拍板，2026-08-04）
| 项 | 拍板结果 |
|----|---------|
| Python | **3.14.6**（本机 py launcher，M1 用 pip dry-run 实测核心库 requires-python 全兼容后拍板） |
| venv 与依赖管理 | `py -3.14 -m venv .venv`；pyproject.toml 声明直接依赖（含 `[dev]` 组），requirements.txt = pip freeze 锁定快照（57 行） |
| 镜像 | ⚠️ 官方 PyPI 在国内卡死，统一加 `--index-url https://pypi.tuna.tsinghua.edu.cn/simple` |
| .env 变量 | `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL`（=deepseek-v4-flash）/ `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST`（惯例命名；config.py 用 pydantic-settings 加载） |
| 命令 | 测试 `.venv/Scripts/python -m pytest`；lint `.venv/Scripts/python -m ruff check/format`；环境验证 `.venv/Scripts/python scripts/verify_env.py`；种子入库 `.venv/Scripts/python scripts/seed_db.py`；评测集入库 `.venv/Scripts/python scripts/ingest_eval_data.py`；评测 `.venv/Scripts/python -m campus_desk.eval.runner --out docs/eval_report_m3.md`；⚠️ 依赖只装在 .venv（`py -3.14` 全局无 pytest/ruff）；Windows 控制台 GBK 需 `PYTHONIOENCODING=utf-8` |
| 测试数据库 | 业务单测/图测试 **SQLite 内存库**（conftest fixture：StaticPool 单连接，测试串行）；集成冒烟连 **本机 MySQL 8.0.45**（MySQL80 服务，root 密码在 .env DATABASE_URL，%40 编码）；docker-compose.yml 备着（供无本机 MySQL 环境，实际开发不用） |
| 环境验证 | `scripts/verify_env.py` 3 项（LangGraph quickstart / DeepSeek 调用 / SqliteSaver 中断恢复），逻辑在包内 `campus_desk/env_check.py`，pytest 同源复用；DeepSeek 项无 key 自动 SKIP（需外部环境项不进 CI） |
