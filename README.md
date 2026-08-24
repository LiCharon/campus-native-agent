# Campus Native Agent — 校园智能服务助手

校园信息聚合 + 问答 + 引导 + 索引的智能服务 Agent：学生一句话提问 → 意图分流 → 能答的直接答（知识库检索）→ 缺信息追问澄清 → 仍答不上转人工兜底（问题回流知识库，形成"进化闭环"）。

## 功能特性

- **4 类意图分流**：knowledge / tool_query / multi_intent / other，LLM 意图识别 + 置信度门控 + 规则兜底
- **知识库检索组装**：条目 type 分型（info 直接答 / process 流程清单 / index 索引引导）
- **确定性工具查询**：Function Calling → 字段抽取 → 查表 → 模板组装，覆盖教室 / 课表 / 成绩 / 借阅 / 余额 / 失物 / 班车等场景
- **追问澄清 ≤3 轮**：缺信息时追问，轮次上限结构化约束
- **转人工兜底 + 进化闭环**：bad_cases 沉淀 → 管理员审查 → 补入知识库
- **知识库管理**：浏览 / 筛选 / 新建 / 编辑 / 删除知识条目，管理员改动即时生效（审查采纳与直接编辑双入口）
- **长期记忆画像**：记住学生常驻楼栋与常问领域，后续回答更贴合个人上下文，避免重复追问
- **可观测与权限**：Langfuse 全链路埋点；角色默认权限 ∪ 附加权限位
- **前端闭环**：对话 / 客服工作台 / 知识库审查 / 看板 / 用户管理 / 审计日志（Vue3 + Element Plus，JWT 鉴权）

## 技术栈

| 层   | 选型                                                      |
| --- | ------------------------------------------------------- |
| 编排  | LangGraph + LangChain（checkpointer: SQLite SqliteSaver） |
| LLM | DeepSeek                                                |
| 后端  | FastAPI + Pydantic v2                                   |
| 数据  | MySQL 8 + SQLAlchemy 2.0（alembic 迁移）                    |
| 前端  | Vue3 + Element Plus                                     |
| 可观测 | Langfuse（agent 步骤级 trace）                               |
| 语言  | Python 3.14                                             |

## 快速开始

> **前置**：本地已运行 MySQL 8；Python 3.14；step 4 需配置 DeepSeek API key（见 `.env`）。

```bash
# 1. 创建虚拟环境并安装依赖（国内统一用清华镜像）
py -3.14 -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2. 配置 .env（格式见 .env.example）

# 3. 种子数据入库（演示账号 + 36 条通用校园知识，幂等可重跑）
.venv/Scripts/python scripts/seed_db.py

# 4. 环境验证（LangGraph / DeepSeek 结构化输出 / Function Calling）
PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/verify_env.py

# 5. 启动后端
.venv/Scripts/python -m uvicorn campus_desk.api.app:create_app --factory \
  --host 0.0.0.0 --port 8000 --workers 1

# 6. 启动前端
cd frontend && npm run dev    # 访问 http://localhost:5173
```

## 演示账号

| 账号                      | 角色       | 权限                    |
| ----------------------- | -------- | --------------------- |
| student-001 / 002 / 003 | student  | 对话问答（学生）              |
| cs-001                  | cs_staff | 人工客服（工作台接待）           |
| admin-001               | admin    | 管理（用户管理 / 知识库审查 / 看板） |

密码统一 `123456`。JWT 鉴权，user_id 取自 token 绝不信任请求体。

## 项目结构

```
campus-desk/
├─ src/campus_desk/    后端主包
│  ├─ entry/           入口分流（意图识别 + 三层防线 + 置信度门控）
│  ├─ knowledge/       知识管道（检索 + type 组装 + 追问决策）
│  ├─ query/           工具查询管道（工具注册表 + 字段抽取 + 模板组装）
│  ├─ api/             FastAPI 路由（鉴权 / 对话 / 管理 / 客服 / 反馈）
│  ├─ db/              SQLAlchemy + alembic + 幂等种子
│  └─ telemetry.py     Langfuse 全链路埋点
├─ frontend/src/views/ Vue3 页面（Login / Chat / CsWorkbench / AdminReview / StatsDashboard / UserManage / LogViewer）
├─ scripts/            种子 / 环境验证 / 本地数据注入脚本
├─ tests/              pytest
└─ docs/               项目文档（独立私有文档仓，不随主仓公开）
```

## 关键设计亮点

1. **两层解耦**：入口分流答"怎么处理"（4 类意图），知识库分类答"是什么"（领域 + type），index 不设入口意图
2. **静态流程检索直答**：流程类问题是静态知识，检索直答即最优解，不做状态机
3. **构建期近重复自动检测**：相似问题在数据构建时自动拦截，保证知识库零冗余

## 文档说明

项目内部规范与设计文档（CLAUDE.md / docs/，含选型记录、迭代日志）为本地私有仓库，不随本仓库公开——本 README 与代码即公开门面。
