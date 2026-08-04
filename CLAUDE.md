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
| docs/DEV_JOURNAL.md | 新会话看最新迭代记录（做了什么/坑/面试点） |
| docs/PLANNING_REVIEW.md | ⚠️ 已过期标注（头部有说明），仅参考历史评审 |
| docs/inputs/需求分析文档.md | Qwen 需求分析原文，仅溯源用 |

## 1. 为什么做（面试叙事，防止跑偏）
- InterviewAI 教训：AI 占比有限，多 Agent 像多次 LLM 调用，无记忆/skill/FC/规划
- 本项目补齐：LangGraph 编排 + 工具调用 + 用户记忆 + Langfuse 可观测 + 量化评测
- **8:2 原则**：80% 预算在 Agent 构建，20% 场景壳；工程化贯穿不占预算、不可省略
- 目标岗位：Agent / LLM 应用开发（求职备考并行，约 5-6 周）

## 2. 技术栈（已拍板）
Python 3.11 · LangGraph（checkpointer: SQLite 官方 SqliteSaver）· LangChain · FastAPI + Pydantic v2 · MySQL 8 + SQLAlchemy 2.0 · Langfuse（开发期 Cloud 免费额度，M6 再试自托管）· DeepSeek · Vue3 最小闭环 · pytest + ruff + sse-starlette + httpx · MCP（扩展期演示加分）
**MVP 不引入**：Redis（M6+ 加：热点缓存 FAQ/公告/排班——会话历史由 checkpointer 管，Redis 不背会话）、Celery（不引入，APScheduler 够用）
⚠️ LangGraph/LangChain API 变动快：**写框架代码前先 context7 查文档**，禁凭记忆写

## 3. 核心设计（细节 → docs/PROJECT_REQUIREMENTS.md）
- 入口分流：EntryAgent 意图识别（报修/咨询/投诉/其他）→ 置信度门控 → 低置信兜底转人工；多意图取主意图处理、次要问题提示继续问；**投诉 = 创建 P1 工单 + 通知管理员**（复用 Repair 管道；通知载体 = 管理列表标红/界面可见，不建通知模块，见 §14）
- 工单状态机 6 态：SUBMITTED→ASSIGNED→IN_PROGRESS→PENDING_VERIFY→CLOSED + CANCELLED（仅 SUBMITTED/ASSIGNED 可撤）；**超时升级=字段不是状态**（escalation_count+escalated_at+审计日志，P1 4h/P2 48h/P3 不适用）；**跳转=图的边=白名单**（完整边清单 8 条，测试照单锁定）：验收不通过 PENDING_VERIFY→IN_PROGRESS 返工；挂起 3 天无响应自动 CLOSED（备注"超时自动关闭"，APScheduler 定时，同为事件非状态）
- 4 Agent：Entry（分流）/ Repair（报修主流程）/ Consult（**诊断式咨询**：追问≤8 轮、每轮≤2 问 → 工具排查 → 三态分支，转人工打包排查记录）/ Quality（关闭 24h 后回访）
- 9 确定性工具（每工具独立单测，不依赖 LLM）：报修侧 6（create_ticket / get_ticket / update_ticket_status / list_repairmen / query_dorm_info / urgent_followup）+ 咨询侧 3（query_account_status / query_announcement / search_faq 关键词匹配）
- 5 类上下文隔离：当前任务（LangGraph state）/ 会话（checkpointer SQLite）/ 用户长期画像（MySQL）/ 工具只读数据 / 全局 FAQ
- 边界声明：业务办理=工单类（报修+投诉+咨询）；咨询=IT 诊断工具 + FAQ 问答；外部接入/通知/多端 = **演示不实装**（requirements 12-14 节，仅面试备答）
- **待定项：5 个待拍板**（报修采集形态/意图识别实现/派单规则/FAQ 库/评测集细节）+ **2 项后置**（few-shot/MCP）→ 按里程碑时间表拍死（requirements 第 11 节），不边写边吵

## 4. 工程化标准（贯穿，不做就白做）
- Langfuse 全链路埋点（agent 步骤/工具调用/状态跳转/LLM call）
- 评测数据集开工第一周造：**对话剧本格式（scripted，预写学生每轮回复）**，报修/咨询/投诉/闲聊各 15-20 条 + 多意图 5-10 条 + 重复报修 2-3 条，存 MySQL
- 量化指标（9 项，细节见 requirements §10）：意图分类准确率/分类定级准确率/自助解决率/人工介入率/平均对话轮次/工单闭环率/工单响应时间/超时率/满意度——**目标值均为示例基线，M5 评测后按实测校准，不拍死**
- 评测脚本独立于业务代码；需外部环境的标 skip，不进 CI（InterviewAI CI 教训）
- CI（GitHub Actions）：**起步 ruff + pytest**；覆盖率门槛/gitleaks/pip-audit M6 后加（单人排期有限，先保核心质量门）
- 安全：.env gitignore + 密钥不入库；alembic 迁移脚本，禁手改表
- Docker Compose 一键起：MySQL + 后端 + 前端（Langfuse 开发期用 Cloud，自托管 M6 再试）

## 5. AI Coding 顺序规范
### 阶段 0 开工（每个新会话）
1. 读本文件 + 相关 docs + mem-search
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
13. 存档（记忆+本文件当前状态），提议新会话
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

## 6. 里程碑（生存线/加分线，2026-08-04 审查后调整）
**生存线（做不完加分线，项目不成立）**：M1 骨架(0.5周) → M2 入口分流+评测集(1周) → M3 报修主链路+状态机+工具(1.5周) → M4 记忆+咨询+回访(1周) → M5 评测闭环+Langfuse(0.5周)
**加分线（演示层，可精简不影响项目成立）**：M6 前端+Compose+CI(0.5周) → M7 打磨+README+自托管尝试(0.5周，弹性当缓冲)
Redis 缓存（M6+ 加分项）；M1 开工前先跑环境验证：LangGraph quickstart + DeepSeek 一次调用 + SqliteSaver 中断恢复
**DoD（完成标准，模式：核心链路测试绿 + 环境验证 + 文档/DEV_JOURNAL 更新）**：
- M1：git init + 初始 commit 完成 / 环境验证 3 项跑通 / 骨架 pytest 绿 / 文档日志更新
- M2-M5：该里程碑核心链路测试绿 + 环境验证跑通 + 文档/日志更新

## 7. 当前状态（2026-08-04）
- [x] 选题 / 8:2 / 技术栈 / MVP 策略确认
- [x] 工程化标准 + 安全检测补充
- [x] Qwen 对抗性审查吸收（状态机 6 态 / checkpointer SQLite / Langfuse Cloud 起步 / 鉴权 / 剧本评测集 / 待定项 11→7）
- [x] 需求待定项按里程碑时间表拍死（5 待拍板 + 2 后置）
- [x] Qwen 二轮审查吸收（状态机边清单补全 / 投诉楼栋可选 / login 接口 / 评测行为断言 / 回访触达；驳回 3 项误报）
- [x] CLAUDE.md 审查修复 + git 初始化（指标对齐 9 项 / 通知载体标注 / 别再犯清单 / DoD / §9 环境占位）
- [ ] M1 骨架（下一步；前置：git init + 环境待定项拍板，见 §9）

## 8. 别再犯清单（历史教训精简版，细节在 DEV_JOURNAL）
- 外部评审建议默认按**文档/契约**吸收（零成本面试弹药）；**代码/模块级**单独过"演示项目是否值得"关（三问：服务"演示能跑+面试能讲"？文档契约还是代码模块？与 8:2/演进式冲突吗？）
- 同一评审倾向会反复出现（如通知模块），裁决保持一致，不为"通知"加实体
- AI 评审工具的"审查对象"声明不可信（可能用旧快照），无论第几轮审查都对照当前磁盘文档逐条核验
- 写框架代码前先 context7 查 API（LangGraph/LangChain API 变动快），禁凭记忆写

## 9. 环境与运行（M1 拍板占位：开工时逐项与用户商量后回填，回填前保持待定）
| 待定项 | 说明 |
|--------|------|
| Python 版本与安装 | 技术栈已定 3.11，安装/管理方式待定 |
| venv 与依赖管理 | pip + requirements.txt 锁定？（待定，M1 商量） |
| .env 变量命名 | DeepSeek / Langfuse key 变量名（待定） |
| 启动与测试命令 | pytest / uvicorn 用法（待定） |
| 测试数据库策略 | SQLite 内存 or 本地 MySQL/Compose（待定，工程决策不进 requirements 待定项表） |
